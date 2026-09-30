"""
Automated Test Suite for Document Upload and Processing.

Verifies:
1. Salary Slip upload creates a documents row.
2. File is actually persisted under uploads directory.
3. Correct application_id is stored.
4. Correct document_type is stored.
5. Form 16 upload works.
6. Aadhaar/PAN upload still works.
7. Document processing updates structured_data.
8. Application isolation: A document from Application A cannot appear for Application B.
9. Existing Government Verification Screenshot upload still does NOT create a documents row.
10. Report Context Builder can consume the persisted documents.
"""
import io
import os
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.database import Base, get_db
from app.core.security import get_current_user
from app.models.user import User, UserRole
from app.models.application import Application, GovVerification
from app.models.document import Document, DocumentType
from app.models.government_screenshot import GovernmentVerificationScreenshot
from app.core.config import settings
from app.services.report_context_builder import build_report_context


@pytest.fixture(scope="module")
def setup_env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()

    admin = User(
        id=1,
        name="Admin User",
        email="admin@test.com",
        password="hashedpassword",
        role=UserRole.admin,
    )
    officer = User(
        id=2,
        name="Loan Officer",
        email="officer@test.com",
        password="hashedpassword",
        role=UserRole.loan_officer,
    )
    db.add(admin)
    db.add(officer)

    # App A created by admin
    app_a = Application(
        id=101,
        user_id=1,
        applicant_name="Sunil Kumar",
        branch="Mangalore Main",
        loan_type="Vehicle Loan",
        loan_amount=500000.0,
        loan_tenure=36,
        interest_rate=8.5,
    )
    # App B created by admin
    app_b = Application(
        id=102,
        user_id=1,
        applicant_name="Other Applicant",
        branch="Bangalore Central",
        loan_type="Home Loan",
        loan_amount=2500000.0,
        loan_tenure=120,
        interest_rate=7.5,
    )
    db.add(app_a)
    db.add(app_b)
    db.commit()

    created_files = []

    def override_get_db():
        try:
            yield db
        finally:
            pass

    # Default auth as loan officer
    def override_get_current_user():
        return officer

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)

    yield {
        "client": client,
        "db": db,
        "app_a": app_a,
        "app_b": app_b,
        "officer": officer,
        "admin": admin,
        "created_files": created_files,
    }

    # Cleanup created test files
    for fp in created_files:
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError:
                pass

    app.dependency_overrides.clear()


def _dummy_png_bytes():
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?"
        b"\x00\x05\xfe\x02\xfe\xa74;\xa0\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_1_2_3_4_salary_slip_upload_and_persistence(setup_env):
    """
    1. Salary Slip upload creates a documents row.
    2. File is actually persisted.
    3. Correct application_id is stored.
    4. Correct document_type is stored.
    """
    client = setup_env["client"]
    db = setup_env["db"]

    file_bytes = _dummy_png_bytes()
    response = client.post(
        "/documents/upload",
        data={"application_id": 101, "document_type": "salary_slip"},
        files={"file": ("salary_slip_test.png", io.BytesIO(file_bytes), "image/png")},
    )

    assert response.status_code == 201, f"Failed upload: {response.text}"
    data = response.json()
    doc_id = data["id"]

    # 1. Row created in database
    doc = db.query(Document).filter(Document.id == doc_id).first()
    assert doc is not None, "Document row not found in DB"

    # 2. File physically persisted
    assert os.path.exists(doc.file_path), f"File was not saved at {doc.file_path}"
    setup_env["created_files"].append(doc.file_path)

    # 3. Correct application_id
    assert doc.application_id == 101
    assert data["application_id"] == 101

    # 4. Correct document_type
    assert doc.document_type == DocumentType.salary_slip
    assert data["document_type"] == "salary_slip"
    assert doc.original_name == "salary_slip_test.png"


def test_5_form16_upload_works(setup_env):
    """5. Form 16 upload works."""
    client = setup_env["client"]
    db = setup_env["db"]

    file_bytes = _dummy_png_bytes()
    response = client.post(
        "/documents/upload",
        data={"application_id": 101, "document_type": "form_16"},
        files={"file": ("form16_test.png", io.BytesIO(file_bytes), "image/png")},
    )

    assert response.status_code == 201, f"Form 16 upload failed: {response.text}"
    data = response.json()
    doc_id = data["id"]

    doc = db.query(Document).filter(Document.id == doc_id).first()
    assert doc is not None
    assert doc.document_type == DocumentType.form_16
    assert doc.application_id == 101
    assert os.path.exists(doc.file_path)
    setup_env["created_files"].append(doc.file_path)


def test_6_aadhaar_pan_upload_still_works(setup_env):
    """6. Aadhaar/PAN upload still works."""
    client = setup_env["client"]
    db = setup_env["db"]

    file_bytes = _dummy_png_bytes()
    for dt in ["aadhaar", "pan"]:
        response = client.post(
            "/documents/upload",
            data={"application_id": 101, "document_type": dt},
            files={"file": (f"{dt}_test.png", io.BytesIO(file_bytes), "image/png")},
        )
        assert response.status_code == 201, f"{dt} upload failed: {response.text}"
        data = response.json()
        doc = db.query(Document).filter(Document.id == data["id"]).first()
        assert doc is not None
        assert doc.document_type.value == dt
        setup_env["created_files"].append(doc.file_path)


def test_7_document_processing_updates_structured_data(setup_env):
    """7. Document processing updates structured_data."""
    client = setup_env["client"]
    db = setup_env["db"]

    # Use existing synthetic salary slip image if present, or dummy
    backend_dir = os.path.dirname(__file__)
    synth_path = os.path.join(backend_dir, "demo-evidence", "demo_salary_slip_aug_2026.png")
    if os.path.exists(synth_path):
        with open(synth_path, "rb") as f:
            file_bytes = f.read()
    else:
        file_bytes = _dummy_png_bytes()

    upload_res = client.post(
        "/documents/upload",
        data={"application_id": 101, "document_type": "salary_slip"},
        files={"file": ("salary_slip_proc.png", io.BytesIO(file_bytes), "image/png")},
    )
    assert upload_res.status_code == 201
    doc_id = upload_res.json()["id"]

    # Process document
    proc_res = client.post(f"/documents/process/{doc_id}")
    assert proc_res.status_code == 200, f"Processing failed: {proc_res.text}"
    proc_data = proc_res.json()

    assert proc_data["processed"] == 2
    assert proc_data["structured_data"] is not None

    db_doc = db.query(Document).filter(Document.id == doc_id).first()
    assert db_doc.processed == 2
    parsed_sd = json.loads(db_doc.structured_data)
    assert "document_type" in parsed_sd
    assert parsed_sd["document_type"] == "salary_slip"
    setup_env["created_files"].append(db_doc.file_path)


def test_8_application_isolation(setup_env):
    """8. A document from Application A cannot appear for Application B."""
    client = setup_env["client"]
    db = setup_env["db"]

    # Upload document to App A (101)
    file_bytes = _dummy_png_bytes()
    res_a = client.post(
        "/documents/upload",
        data={"application_id": 101, "document_type": "salary_slip"},
        files={"file": ("salary_slip_a.png", io.BytesIO(file_bytes), "image/png")},
    )
    doc_a_id = res_a.json()["id"]

    # Upload document to App B (102)
    res_b = client.post(
        "/documents/upload",
        data={"application_id": 102, "document_type": "salary_slip"},
        files={"file": ("salary_slip_b.png", io.BytesIO(file_bytes), "image/png")},
    )
    doc_b_id = res_b.json()["id"]

    # Query documents for App A
    docs_a_res = client.get("/documents/101")
    assert docs_a_res.status_code == 200
    docs_a_ids = [d["id"] for d in docs_a_res.json()]

    # Query documents for App B
    docs_b_res = client.get("/documents/102")
    assert docs_b_res.status_code == 200
    docs_b_ids = [d["id"] for d in docs_b_res.json()]

    assert doc_a_id in docs_a_ids
    assert doc_a_id not in docs_b_ids, "App A document leaked into App B!"

    assert doc_b_id in docs_b_ids
    assert doc_b_id not in docs_a_ids, "App B document leaked into App A!"


def test_9_gov_screenshot_does_not_create_documents_row(setup_env):
    """9. Existing Government Verification Screenshot upload still does NOT create a documents row."""
    client = setup_env["client"]
    db = setup_env["db"]
    admin = setup_env["admin"]

    # Temporarily override user to admin (owner of app 101)
    app.dependency_overrides[get_current_user] = lambda: admin

    initial_doc_count = db.query(Document).filter(Document.application_id == 101).count()

    file_bytes = _dummy_png_bytes()
    res = client.post(
        "/gov-screenshots/101/upload",
        data={"verification_type": "uidai_aadhaar"},
        files={"file": ("uidai_screenshot.png", io.BytesIO(file_bytes), "image/png")},
    )
    assert res.status_code == 201, f"Gov screenshot upload failed: {res.text}"

    # Verify no row was created in `documents`
    after_doc_count = db.query(Document).filter(Document.application_id == 101).count()
    assert after_doc_count == initial_doc_count, "Gov screenshot erroneously created a Document row!"

    # Verify row WAS created in `government_verification_screenshots`
    gs = db.query(GovernmentVerificationScreenshot).filter(
        GovernmentVerificationScreenshot.application_id == 101
    ).first()
    assert gs is not None
    assert gs.verification_type == "uidai_aadhaar"


def test_10_report_context_builder_consumes_persisted_documents(setup_env):
    """10. Report Context Builder can consume the persisted documents (Salary Slip and Form 16)."""
    db = setup_env["db"]

    # Verify Report Context Builder sees the salary_slip and form_16 for app 101
    context = build_report_context(application_id=101, db=db)

    doc_types = [d.document_type for d in context.documents]
    assert "salary_slip" in doc_types, f"salary_slip missing from report context documents: {doc_types}"
    assert "form_16" in doc_types, f"form_16 missing from report context documents: {doc_types}"

    # Income docs fact
    fact_map = {f.fact_id: f for f in context.all_facts}
    income_docs_fact = fact_map.get("documents.income_docs_seen")
    assert income_docs_fact is not None
    assert "salary_slip" in income_docs_fact.value
    assert "form_16" in income_docs_fact.value
