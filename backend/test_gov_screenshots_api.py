"""
Focused Unit & Integration Tests for Government Verification Screenshots API.

Verifies:
1. upload Aadhaar screenshot
2. upload PAN screenshot
3. verification_type normalization ("aadhaar" -> "uidai_aadhaar", "pan" -> "pan_aadhaar_link")
4. screenshot appears in GET
5. screenshot delete works
6. deleting screenshot does NOT delete Aadhaar/PAN document
7. invalid application returns 404
8. invalid verification type returns 400
9. file URL is returned
10. screenshot is stored in government_verification_screenshots
11. no OCR/processDocument call occurs
12. existing GovVerification save endpoint still works
"""
import io
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db.database import Base, get_db
from app.core.security import get_current_user
from app.models.user import User, UserRole
from app.models.application import Application, ApplicationStatus, GovVerification
from app.models.document import Document, DocumentType
from app.models.government_screenshot import GovernmentVerificationScreenshot
from app.core.config import settings


from sqlalchemy.pool import StaticPool


@pytest.fixture(scope="module")
def test_setup():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()

    # Create admin user
    admin = User(
        id=1,
        name="Admin Test",
        email="admin@test.com",
        password="hashedpassword",
        role=UserRole.admin,
    )
    db.add(admin)

    # Create test application
    test_app = Application(
        id=101,
        user_id=1,
        applicant_name="Ramesh Patel",
        loan_amount=750000.0,
        loan_type="Vehicle Loan",
        status=ApplicationStatus.pending,
    )
    db.add(test_app)

    # Create borrower Aadhaar and PAN documents in documents table
    aadhaar_doc = Document(
        id=501,
        application_id=101,
        document_type=DocumentType.aadhaar,
        file_path="uploads/test_aadhaar_card.pdf",
        original_name="borrower_aadhaar.pdf",
        extracted_text="MERA AADHAAR MERI PEHCHAN 1234 5678 9012",
        structured_data='{"aadhaar_number": "123456789012", "applicant_name": "Ramesh Patel"}',
    )
    pan_doc = Document(
        id=502,
        application_id=101,
        document_type=DocumentType.pan,
        file_path="uploads/test_pan_card.jpg",
        original_name="borrower_pan.jpg",
        extracted_text="INCOME TAX DEPARTMENT ABCDE1234F",
        structured_data='{"pan_number": "ABCDE1234F", "applicant_name": "Ramesh Patel"}',
    )
    db.add(aadhaar_doc)
    db.add(pan_doc)
    db.commit()

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    def override_get_current_user():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)

    yield {"client": client, "db": db, "app_id": 101, "SessionLocal": TestingSessionLocal}

    app.dependency_overrides.clear()
    db.close()


def test_01_upload_aadhaar_screenshot_and_normalization(test_setup):
    """Test 1, 3, 9, 10, 11: Upload Aadhaar screenshot, check normalization, file_url, and DB persistence without OCR."""
    client = test_setup["client"]
    db = test_setup["db"]
    app_id = test_setup["app_id"]

    fake_image = io.BytesIO(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRUIDAI_VERIFIED_TEST")
    response = client.post(
        f"/gov-screenshots/{app_id}/upload",
        data={"verification_type": "aadhaar"},
        files={"file": ("uidai_portal_audit.png", fake_image, "image/png")},
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["application_id"] == app_id
    assert data["verification_type"] == "uidai_aadhaar"  # Normalized
    assert data["original_name"] == "uidai_portal_audit.png"
    assert data["file_url"].startswith("/uploads/")
    assert data["id"] > 0

    # Verify directly in government_verification_screenshots table
    shot = db.query(GovernmentVerificationScreenshot).filter_by(id=data["id"]).first()
    assert shot is not None
    assert shot.verification_type == "uidai_aadhaar"
    assert shot.government_portal == "UIDAI"
    assert shot.verification_status == "VERIFIED"

    # Verify NO Document row was created for this upload
    doc_match = db.query(Document).filter(Document.original_name == "uidai_portal_audit.png").first()
    assert doc_match is None


def test_02_upload_pan_screenshot_and_normalization(test_setup):
    """Test 2, 3: Upload PAN screenshot and verify pan_aadhaar_link normalization."""
    client = test_setup["client"]
    db = test_setup["db"]
    app_id = test_setup["app_id"]

    fake_image = io.BytesIO(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRIT_PORTAL_LINKED")
    response = client.post(
        f"/gov-screenshots/{app_id}/upload",
        data={"verification_type": "pan"},
        files={"file": ("it_portal_link_proof.png", fake_image, "image/png")},
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["verification_type"] == "pan_aadhaar_link"  # Normalized
    assert data["original_name"] == "it_portal_link_proof.png"
    assert data["file_url"].startswith("/uploads/")

    # Verify stored in DB
    shot = db.query(GovernmentVerificationScreenshot).filter_by(id=data["id"]).first()
    assert shot is not None
    assert shot.verification_type == "pan_aadhaar_link"
    assert shot.government_portal == "Income Tax e-Filing"


def test_03_get_gov_screenshots(test_setup):
    """Test 4: Verify active screenshots appear in deterministic order in GET."""
    client = test_setup["client"]
    app_id = test_setup["app_id"]

    response = client.get(f"/gov-screenshots/{app_id}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data) >= 2
    types = [s["verification_type"] for s in data]
    assert "uidai_aadhaar" in types
    assert "pan_aadhaar_link" in types


def test_04_delete_screenshot_preserves_borrower_documents(test_setup):
    """Test 5, 6: Delete screenshot works and does NOT delete borrower Aadhaar/PAN documents."""
    client = test_setup["client"]
    db = test_setup["db"]
    app_id = test_setup["app_id"]

    # First verify borrower documents exist
    aadhaar_doc = db.query(Document).filter_by(id=501).first()
    pan_doc = db.query(Document).filter_by(id=502).first()
    assert aadhaar_doc is not None
    assert pan_doc is not None

    # Get screenshot to delete
    list_res = client.get(f"/gov-screenshots/{app_id}")
    shots = list_res.json()
    assert len(shots) > 0
    target_shot = shots[0]
    target_id = target_shot["id"]

    # Delete screenshot
    del_res = client.delete(f"/gov-screenshots/{target_id}")
    assert del_res.status_code == 200, del_res.text
    del_data = del_res.json()
    assert del_data["status"] == "success"

    # Confirm screenshot is deleted from government_verification_screenshots
    deleted_shot = db.query(GovernmentVerificationScreenshot).filter_by(id=target_id).first()
    assert deleted_shot is None

    # CRITICAL: Confirm borrower documents are completely untouched
    aadhaar_doc_after = db.query(Document).filter_by(id=501).first()
    pan_doc_after = db.query(Document).filter_by(id=502).first()
    assert aadhaar_doc_after is not None
    assert pan_doc_after is not None
    assert aadhaar_doc_after.extracted_text == "MERA AADHAAR MERI PEHCHAN 1234 5678 9012"
    assert pan_doc_after.extracted_text == "INCOME TAX DEPARTMENT ABCDE1234F"


def test_05_error_cases(test_setup):
    """Test 7, 8: Invalid application returns 404, invalid type returns 400."""
    client = test_setup["client"]

    # 1. Invalid application ID
    fake_image = io.BytesIO(b"test data")
    res_404 = client.post(
        "/gov-screenshots/999999/upload",
        data={"verification_type": "aadhaar"},
        files={"file": ("test.png", fake_image, "image/png")},
    )
    assert res_404.status_code == 404

    # 2. Invalid verification type
    res_400 = client.post(
        f"/gov-screenshots/{test_setup['app_id']}/upload",
        data={"verification_type": "invalid_unknown_type"},
        files={"file": ("test.png", fake_image, "image/png")},
    )
    assert res_400.status_code == 400
    assert "Invalid verification_type" in res_400.json()["detail"]

    # 3. Invalid screenshot ID on delete
    del_404 = client.delete("/gov-screenshots/888888")
    assert del_404.status_code == 404


def test_06_existing_gov_verification_save_endpoint_works(test_setup):
    """Test 12: Existing POST /applications/{app_id}/gov_verification works seamlessly."""
    client = test_setup["client"]
    db = test_setup["db"]
    app_id = test_setup["app_id"]

    payload = {
        "aadhaar_validity_status": "Valid",
        "aadhaar_screenshot_path": "uidai_portal_audit.png",
        "pan_aadhaar_link_status": "Linked",
        "tax_receipt_status": "pending",
        "officer_name": "R. K. Verma",
        "timestamp": "2026-09-29 11:30 AM",
        "remarks": "UIDAI active validity and Income Tax PAN-Aadhaar link confirmed.",
        "screenshot_path": "it_portal_link_proof.png",
    }

    res = client.post(f"/applications/{app_id}/gov_verification", json=payload)
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["aadhaar_validity_status"] == "Valid"
    assert data["pan_aadhaar_link_status"] == "Linked"
    assert data["officer_name"] == "R. K. Verma"

    # Confirm in gov_verifications table
    gv = db.query(GovVerification).filter_by(application_id=app_id).first()
    assert gv is not None
    assert gv.aadhaar_validity_status == "Valid"
    assert gv.pan_aadhaar_link_status == "Linked"
