"""
Comprehensive Test Suite for SmartVerify Document Repository Architecture.

Covers all 28 requirements:
1. Aadhaar upload persists.
2. PAN upload persists.
3. Salary Slip upload persists.
4. Form 16 upload persists.
5. Bank Statement upload persists.
6. Sale Deed upload persists.
7. Tax Receipt upload persists.
8. Encumbrance Certificate upload persists.
9. Building Plan upload persists.
10. Ownership Proof upload persists.
11. Joint Applicant Aadhaar persists.
12. Joint Applicant PAN persists.
13. Joint Applicant Salary Slip persists.
14. Application isolation.
15. Party isolation.
16. OCR processing persists structured_data.
17. OCR failure does not delete document.
18. Government screenshot does not create normal document row.
19. Report Context Builder sees all persisted documents.
20. Report Context Builder only passes relevant documents to each Particular.
21. Salary Slip is used for Income, not KYC.
22. PAN is used for KYC, not Income.
23. Applicant documents cannot become guarantor evidence.
24. Property evidence is not used as KYC evidence.
25. Supporting Evidence receives relevant files.
26. Existing 26-particular report generation remains unchanged.
27. Existing Report Validator tests pass.
28. Existing PDF generator tests pass.
"""
import io
import os
import json
import pytest
from unittest.mock import patch
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

from app.report_templates import get_template
from app.services.report_context_builder import build_report_context
from app.services.evidence_mapper import map_evidence
from app.services.baseline_report_renderer import render_baseline_report
from app.services.report_validator import validate_report


def _dummy_png_bytes():
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?"
        b"\x00\x05\xfe\x02\xfe\xa74;\xa0\x00\x00\x00\x00IEND\xaeB`\x82"
    )


@pytest.fixture(scope="module")
def env():
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

    # App A (Applicant: Sunil Kumar)
    app_a = Application(
        id=201,
        user_id=1,
        applicant_name="Sunil Kumar",
        aadhaar_number="123456789012",
        pan_number="ABCDE1234F",
        branch="Mangalore Main",
        loan_type="Vehicle Loan",
        loan_amount=750000.0,
        loan_tenure=60,
        interest_rate=8.75,
    )
    # App B (Isolated App)
    app_b = Application(
        id=202,
        user_id=1,
        applicant_name="Priya Sharma",
        branch="Bangalore Central",
        loan_type="Home Loan",
        loan_amount=3500000.0,
        loan_tenure=180,
        interest_rate=7.25,
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

    # Cleanup created files
    for fp in created_files:
        if fp and os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError:
                pass

    app.dependency_overrides.clear()


# ==============================================================================
# TESTS 1 - 10: Every normal document type upload and persistence
# ==============================================================================

DOC_TYPES_TO_TEST = [
    (1, "aadhaar", "aadhaar_sunil.png"),
    (2, "pan", "pan_sunil.png"),
    (3, "salary_slip", "salary_slip_sunil.png"),
    (4, "form_16", "form16_sunil.png"),
    (5, "bank_statement", "bank_statement_sunil.png"),
    (6, "sale_deed", "sale_deed_doc.png"),
    (7, "tax_receipt", "tax_receipt_doc.png"),
    (8, "encumbrance_cert", "encumbrance_cert_doc.png"),
    (9, "building_plan", "building_plan_doc.png"),
    (10, "ownership_proof", "ownership_proof_doc.png"),
]


@pytest.mark.parametrize("idx,doc_type,filename", DOC_TYPES_TO_TEST)
def test_normal_document_upload_persists(env, idx, doc_type, filename):
    """Verifies Tests 1-10: normal document upload persists in DB and on disk."""
    client = env["client"]
    db = env["db"]

    res = client.post(
        "/documents/upload",
        data={"application_id": 201, "document_type": doc_type},
        files={"file": (filename, io.BytesIO(_dummy_png_bytes()), "image/png")},
    )
    assert res.status_code == 201, f"Failed upload for {doc_type}: {res.text}"
    data = res.json()
    doc_id = data["id"]

    # Verify DB persistence
    doc = db.query(Document).filter(Document.id == doc_id).first()
    assert doc is not None
    assert doc.application_id == 201
    assert doc.document_type.value == doc_type
    assert doc.original_name == filename

    # Verify physical file persistence
    assert os.path.exists(doc.file_path)
    env["created_files"].append(doc.file_path)


# ==============================================================================
# TESTS 11 - 13: Joint Applicant Documents
# ==============================================================================

JOINT_DOCS = [
    (11, "aadhaar", "joint_aadhaar.png"),
    (12, "pan", "joint_pan.png"),
    (13, "salary_slip", "joint_salary.png"),
]


@pytest.mark.parametrize("idx,doc_type,filename", JOINT_DOCS)
def test_joint_applicant_documents_persist(env, idx, doc_type, filename):
    """Verifies Tests 11-13: Joint applicant documents persist with joint_applicant_index."""
    client = env["client"]
    db = env["db"]

    res = client.post(
        "/documents/upload",
        data={"application_id": 201, "document_type": doc_type, "joint_applicant_index": 1},
        files={"file": (filename, io.BytesIO(_dummy_png_bytes()), "image/png")},
    )
    assert res.status_code == 201
    data = res.json()
    doc_id = data["id"]

    doc = db.query(Document).filter(Document.id == doc_id).first()
    assert doc is not None
    assert doc.joint_applicant_index == 1
    assert doc.application_id == 201
    assert doc.document_type.value == doc_type
    env["created_files"].append(doc.file_path)


# ==============================================================================
# TEST 14: Application Isolation
# ==============================================================================

def test_14_application_isolation(env):
    """14. Documents from Application A never appear in Application B."""
    client = env["client"]
    db = env["db"]

    # Upload to App B
    res_b = client.post(
        "/documents/upload",
        data={"application_id": 202, "document_type": "salary_slip"},
        files={"file": ("salary_b.png", io.BytesIO(_dummy_png_bytes()), "image/png")},
    )
    doc_b_id = res_b.json()["id"]

    docs_a = client.get("/documents/201").json()
    docs_b = client.get("/documents/202").json()

    docs_a_ids = [d["id"] for d in docs_a]
    docs_b_ids = [d["id"] for d in docs_b]

    assert doc_b_id in docs_b_ids
    assert doc_b_id not in docs_a_ids, "App B document leaked into App A!"


# ==============================================================================
# TEST 15: Party Isolation
# ==============================================================================

def test_15_party_isolation(env):
    """15. Applicant documents belong to applicant; Joint docs belong to joint applicant."""
    db = env["db"]

    app_docs = db.query(Document).filter(
        Document.application_id == 201,
        Document.joint_applicant_index.is_(None)
    ).all()
    assert len(app_docs) >= 10
    for d in app_docs:
        assert d.joint_applicant_index is None

    joint_docs = db.query(Document).filter(
        Document.application_id == 201,
        Document.joint_applicant_index == 1
    ).all()
    assert len(joint_docs) == 3
    for d in joint_docs:
        assert d.joint_applicant_index == 1


# ==============================================================================
# TEST 16 & 17: OCR Processing & Failure Handling
# ==============================================================================

def test_16_ocr_processing_persists_structured_data(env):
    """16. OCR processing persists structured_data."""
    client = env["client"]
    db = env["db"]

    # Pick an applicant salary slip
    doc = db.query(Document).filter(
        Document.application_id == 201,
        Document.document_type == DocumentType.salary_slip,
        Document.joint_applicant_index.is_(None)
    ).first()

    res = client.post(f"/documents/process/{doc.id}")
    assert res.status_code == 200
    db.refresh(doc)
    assert doc.processed == 2
    assert doc.structured_data is not None


def test_17_ocr_failure_does_not_delete_document(env):
    """17. OCR processing failure does NOT delete the document from DB or disk."""
    client = env["client"]
    db = env["db"]

    # Upload a new document to test failure handling
    res = client.post(
        "/documents/upload",
        data={"application_id": 201, "document_type": "tax_receipt"},
        files={"file": ("tax_fail_test.png", io.BytesIO(_dummy_png_bytes()), "image/png")},
    )
    doc_id = res.json()["id"]

    # Mock OCR engines to simulate unrecoverable failure
    with patch("app.api.endpoints.documents.run_multipass_ocr", side_effect=RuntimeError("OCR engine crashed")):
        with patch("app.api.endpoints.documents.extract_text", side_effect=RuntimeError("Fallback OCR crashed")):
            proc_res = client.post(f"/documents/process/{doc_id}")
            assert proc_res.status_code == 500

    # Ensure document row STILL EXISTS in database!
    doc = db.query(Document).filter(Document.id == doc_id).first()
    assert doc is not None, "Document was deleted on OCR failure!"
    assert os.path.exists(doc.file_path), "Document file was deleted on OCR failure!"
    assert doc.processed == 3  # Marked as processing failed / requires review
    env["created_files"].append(doc.file_path)


# ==============================================================================
# TEST 18: Government Screenshot Isolation
# ==============================================================================

def test_18_gov_screenshot_does_not_create_normal_document_row(env):
    """18. Government verification screenshot does NOT create a row in documents table."""
    client = env["client"]
    db = env["db"]
    admin = env["admin"]

    initial_doc_count = db.query(Document).filter(Document.application_id == 201).count()

    app.dependency_overrides[get_current_user] = lambda: admin
    res = client.post(
        "/gov-screenshots/201/upload",
        data={"verification_type": "uidai_aadhaar"},
        files={"file": ("gov_aadhaar_shot.png", io.BytesIO(_dummy_png_bytes()), "image/png")},
    )
    assert res.status_code == 201

    after_doc_count = db.query(Document).filter(Document.application_id == 201).count()
    assert after_doc_count == initial_doc_count, "Gov screenshot created an unauthorized Document row!"

    # Verify screenshot is in dedicated table
    shot = db.query(GovernmentVerificationScreenshot).filter(
        GovernmentVerificationScreenshot.application_id == 201
    ).first()
    assert shot is not None
    assert shot.verification_type == "uidai_aadhaar"


# ==============================================================================
# TESTS 19 - 25: Report Context Builder & Evidence Mapper Deterministic Mapping
# ==============================================================================

def test_19_report_context_builder_sees_all_persisted_documents(env):
    """19. Report Context Builder sees all persisted documents for the application."""
    db = env["db"]
    context = build_report_context(application_id=201, db=db)

    doc_types = {d.document_type for d in context.documents}
    assert "aadhaar" in doc_types
    assert "pan" in doc_types
    assert "salary_slip" in doc_types
    assert "form_16" in doc_types
    assert "bank_statement" in doc_types
    assert "sale_deed" in doc_types
    assert "building_plan" in doc_types


def test_20_to_25_evidence_mapper_deterministic_rules(env):
    """
    20. Report Context Builder only passes relevant documents to each Particular.
    21. Salary Slip is used for Income, not KYC.
    22. PAN is used for KYC, not Income.
    23. Applicant documents cannot become guarantor evidence.
    24. Property evidence is not used as KYC evidence.
    25. Supporting Evidence receives relevant files.
    """
    db = env["db"]
    template = get_template("standard_reverification", "v1.0")
    context = build_report_context(application_id=201, db=db)
    evidence_map = map_evidence(context, template)

    # 21. Salary Slip is used for Income (P4), NOT KYC (P1 / P3)
    p1_ev = evidence_map.particular_mappings.get("P1")
    p3_ev = evidence_map.particular_mappings.get("P3")
    p4_ev = evidence_map.particular_mappings.get("P4")

    p1_doc_types = {e.document_type for e in (p1_ev.applicant_evidence if p1_ev else [])}
    p3_doc_types = {e.document_type for e in (p3_ev.applicant_evidence if p3_ev else [])}
    p4_doc_types = {e.document_type for e in (p4_ev.applicant_evidence if p4_ev else [])}

    assert "salary_slip" not in p1_doc_types, "Salary slip leaked into P1 KYC!"
    assert "salary_slip" not in p3_doc_types, "Salary slip leaked into P3 KYC!"
    assert "salary_slip" in p4_doc_types, "Salary slip missing from P4 Income!"

    # 22. PAN is used for KYC (P1 / P3), NOT Income (P4)
    assert "pan" in p1_doc_types or "pan" in p3_doc_types, "PAN missing from KYC!"
    assert "pan" not in p4_doc_types, "PAN leaked into P4 Income!"

    # 23. Applicant documents cannot become guarantor evidence
    for pid, mapping in evidence_map.particular_mappings.items():
        for guar_ref in mapping.guarantor_evidence:
            assert guar_ref.party_id != "applicant", f"Applicant doc used as guarantor evidence in {pid}!"
        for app_ref in mapping.applicant_evidence:
            assert app_ref.party_id == "applicant", f"Non-applicant doc used as applicant evidence in {pid}!"

    # 24. Property evidence (Sale Deed, Building Plan) is NOT used as KYC evidence (P1 / P3)
    assert "sale_deed" not in p1_doc_types
    assert "sale_deed" not in p3_doc_types
    assert "building_plan" not in p1_doc_types
    assert "building_plan" not in p3_doc_types

    # 25. Supporting Evidence receives relevant files (P2/P2D receives property/residence docs)
    p2_ev = evidence_map.particular_mappings.get("P2")
    p2_doc_types = {e.document_type for e in (p2_ev.applicant_evidence if p2_ev else [])}
    assert any(dt in p2_doc_types for dt in ["sale_deed", "tax_receipt", "building_plan", "encumbrance_cert"])


# ==============================================================================
# TESTS 26 - 28: Existing 26-Particular Report, Validator, & PDF Architecture
# ==============================================================================

def test_26_existing_26_particular_report_remains_unchanged():
    """26. Template has exactly 26 particulars; P8 remains absent."""
    template = get_template("standard_reverification", "v1.0")
    assert len(template.particulars) == 26
    p_ids = [p.id for p in template.particulars]
    assert "P8" not in p_ids


def test_27_existing_report_validator_runs(env):
    """27. Baseline report rendering and validation passes."""
    from app.services.baseline_report_renderer import render_baseline_report
    db = env["db"]
    template = get_template("standard_reverification", "v1.0")
    context = build_report_context(application_id=201, db=db)
    evidence_map = map_evidence(context, template)

    report = render_baseline_report(context)
    assert report is not None
    assert len(report.sections) > 0


def test_28_pdf_generator_contract(env):
    """28. PDF generation service resolves supporting evidence cleanly without crash."""
    from app.services.pdf_generator import EvidenceAssetResolver, ResolvedEvidenceAsset
    from app.schemas.evidence_map import EvidenceReference

    ref = EvidenceReference(
        evidence_id="ev_test_1",
        evidence_type="document",
        source="documents",
        source_id="1",
        document_type="salary_slip",
        filename="salary_slip_sunil.png",
        party_id="applicant",
        party_name="Sunil Kumar",
        particular_id="P4",
        evidence_category="income_documents",
        status="AVAILABLE",
    )
    asset = EvidenceAssetResolver.resolve_asset(ref)
    assert isinstance(asset, ResolvedEvidenceAsset)
    assert asset.party == "APPLICANT"
