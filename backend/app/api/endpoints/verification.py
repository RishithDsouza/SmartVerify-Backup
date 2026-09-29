"""Verification pipeline endpoints.

Exposes two verification strategies sharing the same underlying
services and the same VerificationReport storage:

    POST /verify/{application_id}           – deterministic rule-based pipeline
    POST /verify/{application_id}/agentic    – CrewAI multi-agent pipeline
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.application import Application
from app.models.document import Document
from app.models.verification_report import VerificationReport
from app.models.user import User
from app.schemas.report import ReportOut
from app.core.security import get_current_user
from app.core.config import settings
from app.services.nlp_service import extract_information
from app.services.verification_engine import VerificationEngine
from app.services.fraud_detection import FraudDetector
from app.services.verification_normalizer import normalize_and_persist_verification_findings

router = APIRouter()
logger = logging.getLogger(__name__)


def _load_application_and_documents(application_id: int, db: Session):
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    docs = db.query(Document).filter(Document.application_id == application_id).all()
    if not docs:
        raise HTTPException(status_code=400, detail="No documents uploaded for this application")

    return app, docs


def _get_or_create_report(application_id: int, db: Session) -> VerificationReport:
    report = db.query(VerificationReport).filter(
        VerificationReport.application_id == application_id
    ).first()
    if not report:
        report = VerificationReport(application_id=application_id)
        db.add(report)
    return report


@router.post("/{application_id}", response_model=ReportOut)
def run_verification(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Deterministic rule-based verification pipeline:
    1. Aggregate OCR text from all documents
    2. NLP extraction
    3. Verification engine
    4. Fraud detection
    5. PDF report generation
    """
    app, docs = _load_application_and_documents(application_id, db)

    # ── 1. Aggregate all OCR text ─────────────────────────────────────
    combined_text = " ".join(d.extracted_text or "" for d in docs)

    # ── 2. NLP extraction ─────────────────────────────────────────────
    extracted_info = extract_information(combined_text)
    if not extracted_info.get("loan_amount"):
        extracted_info["loan_amount"] = app.loan_amount
    if not extracted_info.get("applicant_name") and app.applicant_name:
        extracted_info["applicant_name"] = app.applicant_name

    # ── 3. Verification engine ────────────────────────────────────────
    doc_dicts = [{"document_type": d.document_type, "id": d.id} for d in docs]
    engine = VerificationEngine()
    verification_result = engine.verify(extracted_info, doc_dicts)

    # ── 4. Fraud detection ────────────────────────────────────────────
    detector = FraudDetector()
    fraud_result = detector.analyse(extracted_info, doc_dicts, application_id, db)

    # ── 5. Determine final status ─────────────────────────────────────
    final_status = verification_result["status"]
    if fraud_result["fraud_flag"]:
        final_status = "rejected"

    # ── 6. Update application status ─────────────────────────────────
    app.status = final_status
    if extracted_info.get("applicant_name") and not app.applicant_name:
        app.applicant_name = extracted_info["applicant_name"]
    db.commit()

    # ── 7. Normalize findings bridge ──────────────────────────────────
    normalize_and_persist_verification_findings(
        application_id=application_id,
        db=db,
        extracted_info=extracted_info,
        verification_details=verification_result,
        fraud_analysis=fraud_result,
        agent_trace=None,
        documents=docs,
    )
    pdf_path = None

    # ── 8. Save / update report record ───────────────────────────────
    report = _get_or_create_report(application_id, db)
    report.verification_score   = verification_result["verification_score"]
    report.risk_score           = fraud_result["risk_score"]
    report.fraud_flag           = fraud_result["fraud_flag"]
    report.status               = final_status
    report.extracted_info       = extracted_info
    report.fraud_analysis       = fraud_result
    report.verification_details = verification_result
    report.pdf_path             = pdf_path
    report.verification_mode    = "rule_based"
    report.agent_summary        = None
    report.agent_trace          = None
    db.commit()
    db.refresh(report)

    logger.info(f"[rule_based] Verification complete for app {application_id}: {final_status}")
    return report


@router.post("/{application_id}/agentic", response_model=ReportOut)
def run_agentic_verification(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Multi-agent (CrewAI) verification pipeline.

    A crew of five specialised agents collaborates to read documents,
    extract applicant information, apply verification rules, assess
    fraud risk, and compile the final PDF report:

        Document Analyst → Data Extraction Specialist
            → Loan Verification Officer
            → Fraud Investigator
                → Compliance Reporter

    The underlying tools call the exact same deterministic services as
    POST /verify/{application_id}, so results are explainable and
    consistent — the agents add reasoning, cross-checking, and a
    natural-language summary on top.
    """
    if not settings.CREWAI_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Agentic verification is disabled (CREWAI_ENABLED=false).",
        )

    app, docs = _load_application_and_documents(application_id, db)

    doc_payload = [
        {
            "id": d.id,
            "document_type": d.document_type.value if hasattr(d.document_type, "value") else d.document_type,
            "file_path": d.file_path,
            "original_name": d.original_name,
            "structured_data": d.structured_data,
            "extracted_text": d.extracted_text,
        }
        for d in docs
    ]

    # Import here to avoid CrewAI/LLM provider startup cost on every
    # request to the non-agentic endpoint.
    from app.agents.crew import LoanVerificationCrew

    from app.models.application import GovVerification
    gov_ver = db.query(GovVerification).filter(GovVerification.application_id == application_id).first()
    gov_record = {}
    if gov_ver:
        gov_record = {
            # Canonical model fields
            "pan_aadhaar_link_status": gov_ver.pan_aadhaar_link_status,
            "aadhaar_validity_status": gov_ver.aadhaar_validity_status,
            "tax_receipt_status": gov_ver.tax_receipt_status,
            "aadhaar_screenshot_path": gov_ver.aadhaar_screenshot_path,
            "screenshot_path": gov_ver.screenshot_path,
            "verification_screenshots": gov_ver.verification_screenshots,
            "officer_name": gov_ver.officer_name,
            "timestamp": gov_ver.timestamp,
            "remarks": gov_ver.remarks,
            # Backward compatibility aliases mapped to current model fields
            "aadhaar_status": gov_ver.aadhaar_validity_status,
            "pan_status": gov_ver.pan_aadhaar_link_status,
            "pan_aadhaar_status": gov_ver.pan_aadhaar_link_status,
            "tax_status": gov_ver.tax_receipt_status,
            "tax_receipt": gov_ver.tax_receipt_status,
            "screenshot_uploaded": bool(
                gov_ver.aadhaar_screenshot_path
                or gov_ver.screenshot_path
                or gov_ver.verification_screenshots
            ),
        }

    try:
        crew = LoanVerificationCrew(db_session=db)
        result = crew.run(
            application_id=application_id,
            applicant_name=app.applicant_name or "",
            loan_amount=app.loan_amount,
            loan_type=app.loan_type or "Home Loan",
            loan_tenure=app.loan_tenure or 60,
            documents=doc_payload,
            gov_verification_record=gov_record,
        )
    except Exception as e:
        logger.error(f"Agentic verification crew failed for app {application_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Agentic verification failed: {e}")

    final_status        = result["final_status"]
    extracted_info       = result.get("extracted_info") or {}
    verification_result = result.get("verification_details") or {}
    fraud_result         = result.get("fraud_analysis") or {}
    pdf_path             = result.get("pdf_path")

    # ── Update application status ─────────────────────────────────────
    app.status = final_status
    if extracted_info.get("applicant_name") and not app.applicant_name:
        app.applicant_name = extracted_info["applicant_name"]
    db.commit()

    # ── Decouple from legacy PDF generator ────────────────────────────
    # Downstream report review and publication PDF are generated on-demand
    pdf_path = None

    # ── Save / update report record ───────────────────────────────────
    report = _get_or_create_report(application_id, db)
    report.verification_score   = result.get("verification_score", 0.0)
    report.risk_score           = result.get("risk_score", 0.0)
    report.fraud_flag           = result.get("fraud_flag", False)
    report.status               = final_status
    report.extracted_info       = extracted_info
    report.fraud_analysis       = fraud_result
    report.verification_details = verification_result
    report.pdf_path             = pdf_path
    report.verification_mode    = "agentic"
    report.agent_summary        = result.get("summary")
    report.agent_trace          = {
        "raw": result.get("agent_trace"),
        "agent_findings": result.get("agent_findings", {}),
        "recommendation": result.get("recommendation", ""),
        "human_review": result.get("human_review", ""),
    }
    
    # New Multi-Agent Fields (Phases 6, 7, 8)
    report.confidence_score     = result.get("confidence_score", 0.0)
    report.explainable_ai       = result.get("explainable_ai", {})
    report.agent_memory         = result.get("agent_memory", [])
    report.execution_timeline   = result.get("execution_timeline", {})
    
    db.commit()
    db.refresh(report)

    # ── Normalize findings bridge ─────────────────────────────────────
    normalize_and_persist_verification_findings(
        application_id=application_id,
        db=db,
        extracted_info=extracted_info,
        verification_details=verification_result,
        fraud_analysis=fraud_result,
        agent_trace=report.agent_trace,
        documents=docs,
    )

    # ── Store Application in Vector Database for Similarity Search (Phase 3) ──
    try:
        from app.services.vector_service import VectorService
        VectorService().store_application_vector(
            app_id=application_id,
            extracted_info=extracted_info,
            fraud_result=fraud_result,
            verification_score=report.verification_score,
            status=final_status
        )
    except Exception as e:
        logger.error(f"Failed to index application for similarity search: {e}")

    logger.info(f"[agentic] Verification complete for app {application_id}: {final_status}")
    return report
