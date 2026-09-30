"""Test Government Verification PAN KYC and Screenshot Isolation for Application 6."""
import os
import io
import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.db.database import SessionLocal
from app.models.user import User
from app.models.document import Document
from app.models.government_screenshot import GovernmentVerificationScreenshot


def test_app6_pan_kyc_and_screenshot_isolation():
    db = SessionLocal()
    client = TestClient(app)
    try:
        admin = db.query(User).filter(User.role == "admin").first() or db.query(User).first()
        assert admin is not None
        token = create_access_token({"sub": str(admin.id), "role": admin.role})
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Base counts
        docs_before = client.get("/documents/6", headers=headers).json()
        doc_count_before = len(docs_before)
        shots_before = client.get("/gov-screenshots/6", headers=headers).json()
        shot_count_before = len(shots_before)

        # 2. FLOW 1: BORROWER PAN CARD UPLOAD + OCR
        # Simulate user uploading PAN card via Gov Verification dropzone
        synthetic_pan_image = b"fake-pan-card-image-bytes"
        pan_upload_res = client.post(
            "/documents/upload",
            headers=headers,
            data={"application_id": "6", "document_type": "pan"},
            files={"file": ("test_pan_borrower_card.png", io.BytesIO(synthetic_pan_image), "image/png")}
        )
        assert pan_upload_res.status_code in (200, 201), f"PAN upload failed: {pan_upload_res.text}"
        pan_doc_data = pan_upload_res.json()
        pan_doc_id = pan_doc_data["id"]

        # Confirm documents table has new row
        doc_row = db.query(Document).filter(Document.id == pan_doc_id).first()
        assert doc_row is not None
        assert doc_row.document_type == "pan"
        assert doc_row.application_id == 6

        # Confirm NO row created in government_verification_screenshots
        shots_after_pan = client.get("/gov-screenshots/6", headers=headers).json()
        assert len(shots_after_pan) == shot_count_before, "Borrower PAN card must NOT create a government screenshot!"

        # Process document (OCR)
        proc_res = client.post(f"/documents/process/{pan_doc_id}", headers=headers)
        assert proc_res.status_code == 200, f"Process document failed: {proc_res.text}"
        proc_data = proc_res.json()
        assert proc_data["id"] == pan_doc_id

        # Check structured data / extracted PAN
        db.refresh(doc_row)
        assert doc_row.extracted_text is not None

        # Confirm refreshing application 6 documents returns this PAN document
        docs_refresh = client.get("/documents/6", headers=headers).json()
        matching_pan = next((d for d in docs_refresh if d["id"] == pan_doc_id), None)
        assert matching_pan is not None, "Uploaded PAN document must be present on refresh"

        # 3. FLOW 2: PAN-AADHAAR VERIFICATION SCREENSHOT
        # Simulate user uploading Income Tax portal screenshot
        screenshot_bytes = b"fake-portal-screenshot-bytes"
        shot_upload_res = client.post(
            "/gov-screenshots/6/upload",
            headers=headers,
            data={"verification_type": "pan"},
            files={"file": ("incometax_pan_link_screenshot.png", io.BytesIO(screenshot_bytes), "image/png")}
        )
        assert shot_upload_res.status_code == 201, f"Screenshot upload failed: {shot_upload_res.text}"
        shot_data = shot_upload_res.json()
        shot_id = shot_data["id"]
        assert shot_data["verification_type"] == "pan_aadhaar_link"

        # Confirm row created in government_verification_screenshots
        gs_row = db.query(GovernmentVerificationScreenshot).filter(
            GovernmentVerificationScreenshot.id == shot_id
        ).first()
        assert gs_row is not None
        assert gs_row.application_id == 6

        # CRITICAL: Confirm NO new row in documents
        docs_after_shot = client.get("/documents/6", headers=headers).json()
        assert len(docs_after_shot) == doc_count_before + 1, "Uploading gov screenshot must NOT create a documents row!"

        # Confirm OCR was NOT triggered for government screenshot (no document_ocr endpoint called, no Document model row)
        doc_with_shot_name = db.query(Document).filter(
            Document.file_path.ilike("%incometax_pan_link_screenshot%")
        ).first()
        assert doc_with_shot_name is None, "Government screenshot must NEVER create a Document row"

        # 4. DELETE ISOLATION
        # Deleting screenshot only deletes government screenshot
        del_shot_res = client.delete(f"/gov-screenshots/{shot_id}", headers=headers)
        assert del_shot_res.status_code == 200

        db.expire_all()
        assert db.query(GovernmentVerificationScreenshot).filter(
            GovernmentVerificationScreenshot.id == shot_id
        ).first() is None, "Screenshot must be deleted"

        # Confirm borrower PAN card in documents is still intact!
        pan_still_exists = db.query(Document).filter(Document.id == pan_doc_id).first()
        assert pan_still_exists is not None, "Borrower PAN document must remain intact when screenshot is deleted!"

        # 5. Clean up the test borrower PAN document
        del_doc_res = client.delete(f"/documents/{pan_doc_id}", headers=headers)
        assert del_doc_res.status_code == 200

        # Confirm document is deleted
        db.expire_all()
        assert db.query(Document).filter(Document.id == pan_doc_id).first() is None

    finally:
        db.close()
