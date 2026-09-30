"""Test Complete Government Verification Flow for Application 6."""
import os
import io
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.db.database import SessionLocal
from app.models.user import User
from app.models.application import Application, GovVerification
from app.models.document import Document
from app.models.government_screenshot import GovernmentVerificationScreenshot
from app.services.report_context_builder import build_report_context
from app.report_templates import get_template
from app.services.evidence_mapper import map_evidence


def test_app6_complete_government_verification_flow():
    db = SessionLocal()
    try:
        # 1. Identify admin or application user
        admin = db.query(User).filter(User.role == "admin").first()
        if not admin:
            admin = db.query(User).first()
        assert admin is not None, "User required for auth token"
        token = create_access_token({"sub": str(admin.id), "role": admin.role})
        headers = {"Authorization": f"Bearer {token}"}

        client = TestClient(app)

        # 2. Check Application 6 basic details
        app_res = client.get("/applications/6", headers=headers)
        assert app_res.status_code == 200, f"Failed to get application 6: {app_res.text}"
        app_data = app_res.json()
        assert app_data["id"] == 6
        assert app_data["applicant_name"] == "Sunil Kumar"
        assert app_data["loan_type"] == "Vehicle Loan"
        assert float(app_data["loan_amount"]) == 850000.0

        # 3. Check borrower KYC documents are present in `documents`
        docs_res = client.get("/documents/6", headers=headers)
        assert docs_res.status_code == 200
        docs_data = docs_res.json()
        initial_doc_count = len(docs_data)
        doc_types = [d["document_type"] for d in docs_data]
        assert "aadhaar" in doc_types, "Aadhaar borrower document should be present in documents"

        # 4. Check existing government verification screenshots from `government_verification_screenshots`
        shots_res = client.get("/gov-screenshots/6", headers=headers)
        assert shots_res.status_code == 200
        shots_data = shots_res.json()
        assert len(shots_data) >= 2, "Expected at least 2 existing gov screenshots for App 6"
        shot_types = [s["verification_type"] for s in shots_data]
        assert "uidai_aadhaar" in shot_types
        assert "pan_aadhaar_link" in shot_types

        # Verify evidence URLs resolve with HTTP 200
        for s in shots_data:
            url = s["file_url"]
            file_res = client.get(url)
            assert file_res.status_code == 200, f"Evidence URL {url} must resolve with HTTP 200"

        # 5. Upload a test government screenshot via dedicated endpoint
        test_file_content = b"fake-png-content-for-test-screenshot"
        test_file = io.BytesIO(test_file_content)
        upload_res = client.post(
            "/gov-screenshots/6/upload",
            headers=headers,
            data={"verification_type": "aadhaar"},
            files={"file": ("test_uidai_shot.png", test_file, "image/png")},
        )
        assert upload_res.status_code == 201, f"Upload failed: {upload_res.text}"
        uploaded_shot = upload_res.json()
        uploaded_id = uploaded_shot["id"]
        assert uploaded_shot["verification_type"] == "uidai_aadhaar", "Type 'aadhaar' must be normalized to 'uidai_aadhaar'"

        # 6. Verify row is created in government_verification_screenshots
        gs_row = db.query(GovernmentVerificationScreenshot).filter(
            GovernmentVerificationScreenshot.id == uploaded_id
        ).first()
        assert gs_row is not None
        assert gs_row.application_id == 6

        # 7. CRITICAL: Verify NO row created in `documents`
        docs_after_res = client.get("/documents/6", headers=headers)
        assert docs_after_res.status_code == 200
        assert len(docs_after_res.json()) == initial_doc_count, "Uploading gov screenshot MUST NOT insert into documents!"

        # 8. CRITICAL: Delete the test screenshot and verify only screenshot is removed
        del_res = client.delete(f"/gov-screenshots/{uploaded_id}", headers=headers)
        assert del_res.status_code == 200

        # Confirm screenshot row is gone
        db.expire_all()
        gs_deleted = db.query(GovernmentVerificationScreenshot).filter(
            GovernmentVerificationScreenshot.id == uploaded_id
        ).first()
        assert gs_deleted is None, "Government screenshot record must be deleted"

        # Confirm documents table is completely untouched
        docs_after_del = client.get("/documents/6", headers=headers)
        assert len(docs_after_del.json()) == initial_doc_count, "Borrower documents must remain untouched"

        # 9. Submit Government Verification
        gov_payload = {
            "aadhaar_validity_status": "Valid",
            "aadhaar_screenshot_path": "aadhaar.png",
            "pan_aadhaar_link_status": "Linked",
            "tax_receipt_status": "VERIFIED",
            "officer_name": "Demo Government Verification Officer",
            "timestamp": "29-09-2026 03:30 PM",
            "remarks": "UIDAI validity checked and PAN-Aadhaar linking confirmed.",
            "screenshot_path": "pan-aadhaar.png",
        }
        submit_res = client.post("/applications/6/gov_verification", headers=headers, json=gov_payload)
        assert submit_res.status_code in (200, 201), f"Submit failed: {submit_res.text}"

        # 10. Refresh/Get application and verify saved result
        app_refresh_res = client.get("/applications/6", headers=headers)
        assert app_refresh_res.status_code == 200
        gv_data = app_refresh_res.json()["gov_verification"]
        assert gv_data["aadhaar_validity_status"] == "Valid"
        assert gv_data["pan_aadhaar_link_status"] == "Linked"
        assert gv_data["officer_name"] == "Demo Government Verification Officer"

        # 11. Downstream: verify Report Context Builder consumes the saved government verification
        ctx = build_report_context(6, db)
        assert ctx.government_verification is not None
        assert ctx.government_verification.aadhaar_validity_status == "Valid"
        assert ctx.government_verification.pan_aadhaar_link_status == "Linked"

        # 12. Downstream: verify Evidence Mapper includes government verification screenshots
        mapped_evidence = map_evidence(ctx, get_template())
        all_refs = [
            ref
            for mapping in mapped_evidence.particular_mappings.values()
            for ref in mapping.evidence
        ]
        evidence_types = [ref.evidence_type for ref in all_refs]
        assert "government_screenshot" in evidence_types, "Evidence map must include government screenshots"

    finally:
        db.close()
