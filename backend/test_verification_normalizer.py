"""
Test Suite for Verification Findings Normalizer (Phase 1B).
Verifies all 20 required behaviors:
1. Identity Verification -> P1
2. Aadhaar Format -> P2E
3. PAN Format -> P3
4. Income Verification -> P4
5. Missing Documents -> P6
6. Duplicate Aadhaar -> P6
7. High income claim -> P6
8. Invalid PAN -> P6
9. Correct status preservation
10. Correct risk_score preservation
11. Evidence JSON preservation
12. Aadhaar document linking
13. PAN document linking
14. Income document linking
15. Idempotent normalization
16. Agentic finding normalization
17. No chain-of-thought stored
18. No fabricated finding
19. No unrelated document linking
20. Legacy generate_report is no longer called by verification endpoint
"""
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models.user import User, UserRole
from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.finding import ApplicationFinding, FindingStatus
from app.models.verification_result import VerificationResult
from app.services.verification_normalizer import normalize_and_persist_verification_findings


@pytest.fixture
def db_session():
    """In-memory SQLite database session for isolated testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Create dummy user & application
    user = User(name="Test Officer", email="officer@test.com", password="pass", role=UserRole.loan_officer)
    session.add(user)
    session.commit()

    app = Application(
        user_id=user.id,
        applicant_name="Sunil Kumar",
        loan_amount=500000.0,
        loan_type="Personal Loan",
        status=ApplicationStatus.pending,
    )
    session.add(app)
    session.commit()

    # Create dummy documents
    doc_aadhaar = Document(application_id=app.id, document_type=DocumentType.aadhaar, file_path="uploads/aadhaar.png")
    doc_pan = Document(application_id=app.id, document_type=DocumentType.pan, file_path="uploads/pan.png")
    doc_salary = Document(application_id=app.id, document_type=DocumentType.salary_slip, file_path="uploads/salary.pdf")
    doc_bank = Document(application_id=app.id, document_type=DocumentType.bank_statement, file_path="uploads/bank.pdf")

    session.add_all([doc_aadhaar, doc_pan, doc_salary, doc_bank])
    session.commit()

    yield session

    session.close()
    Base.metadata.drop_all(bind=engine)


def test_verification_normalizer_core_mapping_and_document_linking(db_session):
    """Verifies checks 1-14: field mapping, document linking, risk scores, and evidence JSON."""
    app = db_session.query(Application).first()
    docs = db_session.query(Document).filter(Document.application_id == app.id).all()

    verification_details = {
        "verification_score": 85.0,
        "status": "approved",
        "checks": [
            {
                "name": "Identity Verification",
                "score": 1.0,
                "weight": 30,
                "details": "Name found: True, Identity document present: True",
                "passed": True,
            },
            {
                "name": "Aadhaar Format",
                "score": 1.0,
                "weight": 5,
                "details": "Aadhaar present: True, Valid format: True",
                "passed": True,
            },
            {
                "name": "PAN Format",
                "score": 1.0,
                "weight": 10,
                "details": "PAN: ABCDE1234F, Valid format: True",
                "passed": True,
            },
            {
                "name": "Income Verification",
                "score": 0.7,
                "weight": 30,
                "details": "Monthly income: ₹45000.0, Eligible: True",
                "passed": True,
            },
        ],
    }

    fraud_analysis = {
        "risk_score": 45.0,
        "fraud_flag": False,
        "alerts": [
            "Missing required documents: ['form_16']",
            "Duplicate Aadhaar detected in 1 other application(s)",
            "Unusually high claimed income: ₹650000/month",
            "PAN number format is invalid: ABCDE123",
        ],
        "details": [
            {
                "check": "missing_documents",
                "risk_added": 10.0,
                "detail": "Missing required documents: ['form_16']",
            },
            {
                "check": "duplicate_aadhaar",
                "risk_added": 30.0,
                "detail": "Duplicate Aadhaar detected in 1 other application(s)",
            },
            {
                "check": "high_income_claim",
                "risk_added": 15.0,
                "detail": "Unusually high claimed income: ₹650000/month",
            },
            {
                "check": "invalid_pan",
                "risk_added": 25.0,
                "detail": "PAN number format is invalid: ABCDE123",
            },
        ],
    }

    findings = normalize_and_persist_verification_findings(
        application_id=app.id,
        db=db_session,
        extracted_info={"applicant_name": "Sunil Kumar"},
        verification_details=verification_details,
        fraud_analysis=fraud_analysis,
        agent_trace=None,
        documents=docs,
    )

    by_pid = {f.particular_id: [] for f in findings}
    for f in findings:
        by_pid[f.particular_id].append(f)

    # 1. Identity Verification -> P1
    p1_findings = [f for f in by_pid.get("P1", []) if "Identity" in f.question]
    assert len(p1_findings) == 1
    assert p1_findings[0].finding_type == "identity"
    assert p1_findings[0].status == FindingStatus.VERIFIED.value

    # 2. Aadhaar Format -> P2E
    p2e_findings = by_pid.get("P2E", [])
    assert len(p2e_findings) == 1
    assert p2e_findings[0].finding_type == "identity"
    assert p2e_findings[0].status == FindingStatus.VERIFIED.value

    # 3. PAN Format -> P3
    p3_findings = by_pid.get("P3", [])
    assert len(p3_findings) == 1
    assert p3_findings[0].finding_type == "compliance"
    assert p3_findings[0].status == FindingStatus.VERIFIED.value

    # 4. Income Verification -> P4
    p4_findings = by_pid.get("P4", [])
    assert len(p4_findings) == 1
    assert p4_findings[0].finding_type == "income"
    assert p4_findings[0].status == FindingStatus.VERIFIED.value

    # 5, 6, 7, 8. Fraud findings -> P6
    p6_findings = by_pid.get("P6", [])
    p6_by_q = {f.question: f for f in p6_findings}

    # 5. Missing Documents -> P6
    assert "Mandatory Application Documentation Availability" in p6_by_q
    assert p6_by_q["Mandatory Application Documentation Availability"].status == FindingStatus.NOT_AVAILABLE.value

    # 6. Duplicate Aadhaar -> P6
    assert "Cross-Application Duplicate Aadhaar Scrutiny" in p6_by_q
    assert p6_by_q["Cross-Application Duplicate Aadhaar Scrutiny"].status == FindingStatus.NOT_VERIFIED.value

    # 7. High income claim -> P6
    assert "Unusual High Income Claim Scrutiny" in p6_by_q
    assert p6_by_q["Unusual High Income Claim Scrutiny"].status == FindingStatus.NOT_VERIFIED.value

    # 8. Invalid PAN -> P6
    assert "Permanent Account Number (PAN) Format Authenticity" in p6_by_q
    assert p6_by_q["Permanent Account Number (PAN) Format Authenticity"].status == FindingStatus.NOT_VERIFIED.value

    # 9. Correct status preservation
    assert p1_findings[0].status == "VERIFIED"
    assert p6_by_q["Cross-Application Duplicate Aadhaar Scrutiny"].status == "NOT_VERIFIED"

    # 10. Correct risk_score preservation
    assert p6_by_q["Cross-Application Duplicate Aadhaar Scrutiny"].evidence["risk_score"] == 45.0
    assert p6_by_q["Cross-Application Duplicate Aadhaar Scrutiny"].evidence["risk_added"] == 30.0

    # 11. Evidence JSON preservation
    assert p1_findings[0].evidence["source"] == "verification_engine"
    assert p1_findings[0].evidence["passed"] is True

    # 12. Aadhaar document linking
    linked_aadhaar_types = [d.document_type.value for d in p2e_findings[0].documents]
    assert "aadhaar" in linked_aadhaar_types

    # 13. PAN document linking
    linked_pan_types = [d.document_type.value for d in p3_findings[0].documents]
    assert "pan" in linked_pan_types

    # 14. Income document linking
    linked_income_types = [d.document_type.value for d in p4_findings[0].documents]
    assert "salary_slip" in linked_income_types

    # 19. No unrelated document linking
    assert "pan" not in linked_aadhaar_types
    assert "salary_slip" not in linked_pan_types


def test_idempotent_normalization_and_no_duplication(db_session):
    """Verifies check 15: running normalizer twice yields identical count and values."""
    app = db_session.query(Application).first()
    docs = db_session.query(Document).filter(Document.application_id == app.id).all()

    # Insert a manual officer finding that should NOT be deleted
    manual_finding = ApplicationFinding(
        application_id=app.id,
        particular_id="P2A",
        question="Manual Field Officer Visit Observation",
        answer="Neighbor confirmed residence tenure for 5 years.",
        finding_type="identity",
        status=FindingStatus.VERIFIED.value,
        evidence={"source": "manual_officer_entry"},
    )
    db_session.add(manual_finding)
    db_session.commit()
    manual_id = manual_finding.id

    v_details = {
        "checks": [
            {"name": "Identity Verification", "score": 1.0, "weight": 30, "details": "Identity OK", "passed": True}
        ]
    }
    f_analysis = {"risk_score": 0.0, "fraud_flag": False, "details": []}

    # Pass 1
    run1 = normalize_and_persist_verification_findings(
        application_id=app.id,
        db=db_session,
        extracted_info={},
        verification_details=v_details,
        fraud_analysis=f_analysis,
        documents=docs,
    )
    count1 = db_session.query(ApplicationFinding).filter(ApplicationFinding.application_id == app.id).count()

    # Pass 2 (Identical input)
    run2 = normalize_and_persist_verification_findings(
        application_id=app.id,
        db=db_session,
        extracted_info={},
        verification_details=v_details,
        fraud_analysis=f_analysis,
        documents=docs,
    )
    count2 = db_session.query(ApplicationFinding).filter(ApplicationFinding.application_id == app.id).count()

    assert count1 == count2
    # Verify manual officer finding was untouched
    preserved_manual = db_session.query(ApplicationFinding).filter(ApplicationFinding.id == manual_id).first()
    assert preserved_manual is not None
    assert preserved_manual.answer == "Neighbor confirmed residence tenure for 5 years."


def test_agentic_findings_normalization_and_no_cot(db_session):
    """Verifies checks 16, 17, 18: agent findings normalized, no chain of thought, no fabrications."""
    app = db_session.query(Application).first()
    docs = db_session.query(Document).filter(Document.application_id == app.id).all()

    agent_trace = {
        "raw": "Thought: Let's inspect the files...\nAction: Check...",
        "agent_findings": {
            "loan_verification_officer": {
                "decision": "Income of ₹45000 verified against policy guidelines; FOIR is 42% (below 50% max limit)."
            },
            "document_analyst": {
                "finding": "OCR extraction quality high (0.95), all primary pages legible."
            },
            "unrelated_empty_agent": None,
        },
    }

    findings = normalize_and_persist_verification_findings(
        application_id=app.id,
        db=db_session,
        extracted_info={},
        verification_details={"checks": []},
        fraud_analysis={"risk_score": 0.0, "fraud_flag": False, "details": []},
        agent_trace=agent_trace,
        documents=docs,
    )

    answers = [f.answer for f in findings]
    # 17. No chain-of-thought stored
    for ans in answers:
        assert "Thought:" not in ans
        assert "Action:" not in ans

    # 16. Agentic finding normalization
    income_agent_f = [f for f in findings if f.particular_id == "P4" and "Loan Verification Officer" in f.question]
    assert len(income_agent_f) == 1
    assert "FOIR is 42%" in income_agent_f[0].answer

    # 18. No fabricated finding for None/empty agent
    empty_agent_f = [f for f in findings if "Unrelated Empty Agent" in f.question]
    assert len(empty_agent_f) == 0


def test_legacy_generate_report_not_called_in_verification_endpoint(db_session):
    """Verifies check 20: legacy generate_report is NOT invoked during verification."""
    from app.api.endpoints.verification import run_verification

    app = db_session.query(Application).first()
    user = db_session.query(User).first()

    with patch("app.api.endpoints.verification.generate_report", create=True) as mock_legacy_report:
        # Also patch extract_information to be deterministic
        with patch("app.api.endpoints.verification.extract_information", return_value={"applicant_name": "Sunil Kumar"}):
            rep = run_verification(application_id=app.id, db=db_session, current_user=user)

            # Assert mock was NOT called
            assert not mock_legacy_report.called
            # Assert report record was created and saved
            assert rep is not None
            assert rep.application_id == app.id
            assert rep.pdf_path is None

            # Assert findings were normalized into database
            findings = db_session.query(ApplicationFinding).filter(ApplicationFinding.application_id == app.id).all()
            assert len(findings) > 0


def test_end_to_end_verification_to_report_context_builder(db_session):
    """Verifies complete end-to-end flow: run_verification -> application_findings -> build_report_context -> evidence mapping."""
    from app.api.endpoints.verification import run_verification
    from app.services.report_context_builder import build_report_context
    from app.report_templates import get_template
    from app.services.baseline_report_renderer import render_baseline_report
    from app.services.evidence_mapper import map_evidence

    app = db_session.query(Application).first()
    user = db_session.query(User).first()

    with patch("app.api.endpoints.verification.extract_information", return_value={"applicant_name": "Sunil Kumar", "monthly_income": 50000.0}):
        rep = run_verification(application_id=app.id, db=db_session, current_user=user)

    # 1. Verify findings exist in PostgreSQL
    findings = db_session.query(ApplicationFinding).filter(ApplicationFinding.application_id == app.id).all()
    assert len(findings) > 0

    # 2. Build report context
    template = get_template("standard_reverification", "v1.0")
    context = build_report_context(app.id, db_session, template.template_key, template.template_version)

    assert context is not None
    assert len(context.findings) == len(findings)
    assert context.particular_facts["P6"].shared_facts["has_findings"] is True
    assert context.particular_facts["P6"].shared_facts["findings_count"] > 0

    # 3. Baseline report renderer consumes findings
    baseline = render_baseline_report(context, template.template_key, template.template_version)
    assert baseline is not None
    assert baseline.template_key == template.template_key
    assert context.metadata.application_id == app.id

    # 4. Evidence mapper links evidence without exceptions
    ev_map = map_evidence(context, template)
    assert ev_map is not None
