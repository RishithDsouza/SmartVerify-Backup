"""
Verification Findings Normalizer Service
Deterministically converts upstream verification results (rule-based checks,
fraud analysis, and structured agent findings) into normalized ApplicationFinding
and VerificationResult records.

Architectural Guardrails:
1. Deterministic and idempotent: Repeated execution yields identical state.
2. Does NOT delete unrelated officer-created findings.
3. Preserves upstream facts, risk scores, and check results without fabrication.
4. Stores structured metadata in finding.evidence.
5. Links source documents via finding.documents (finding_documents association).
6. Zero database schema modifications.
"""
import logging
from typing import Dict, Any, List, Optional, Set
from sqlalchemy.orm import Session

from app.models.finding import ApplicationFinding, FindingStatus
from app.models.verification_result import VerificationResult
from app.models.document import Document

logger = logging.getLogger(__name__)

# Automated question keys used by this normalizer to enable safe, targeted idempotency
AUTOMATED_CHECK_QUESTIONS = {
    "Borrower / Applicant Identity Verification",
    "Aadhaar Number Format and Structure Validation",
    "Permanent Account Number (PAN) Format Validation",
    "Applicant Income & Repayment Capacity Eligibility",
    "Mandatory Document Submission Completeness",
    "Cross-Application Duplicate Aadhaar Scrutiny",
    "Mandatory Application Documentation Availability",
    "Unusual High Income Claim Scrutiny",
    "Loan-To-Income (LTI) Exposure Scrutiny",
    "Applicant Name Extraction and Consistency",
    "Permanent Account Number (PAN) Format Authenticity",
    "Fraud Detection & Duplicate Record Scrutiny",
}


def _get_document_type_str(doc: Document) -> str:
    """Extract string document type whether enum or str."""
    if hasattr(doc.document_type, "value"):
        return str(doc.document_type.value).lower()
    return str(doc.document_type).lower()


def _link_documents_by_types(
    finding: ApplicationFinding,
    target_types: Set[str],
    docs_by_type: Dict[str, List[Document]],
) -> None:
    """Link matching documents to finding without duplicating."""
    for t in target_types:
        for doc in docs_by_type.get(t, []):
            if doc not in finding.documents:
                finding.documents.append(doc)


def normalize_and_persist_verification_findings(
    application_id: int,
    db: Session,
    extracted_info: Dict[str, Any],
    verification_details: Dict[str, Any],
    fraud_analysis: Dict[str, Any],
    agent_trace: Optional[Dict[str, Any]] = None,
    documents: Optional[List[Document]] = None,
) -> List[ApplicationFinding]:
    """
    Deterministically normalize upstream rule-based and agentic verification
    results into ApplicationFinding and VerificationResult records.
    """
    if documents is None:
        documents = db.query(Document).filter(Document.application_id == application_id).all()

    # Index documents by type for precise linking
    docs_by_type: Dict[str, List[Document]] = {}
    for d in documents:
        dtype = _get_document_type_str(d)
        docs_by_type.setdefault(dtype, []).append(d)

    # 1. Query existing records upfront to prevent session/autoflush warnings
    existing_findings = db.query(ApplicationFinding).filter(
        ApplicationFinding.application_id == application_id
    ).all()
    existing_finding_lookup: Dict[tuple, ApplicationFinding] = {
        (f.particular_id, f.question): f for f in existing_findings
    }

    existing_results = db.query(VerificationResult).filter(
        VerificationResult.application_id == application_id
    ).all()
    existing_result_lookup = {r.check_type: r for r in existing_results}

    persisted_findings: List[ApplicationFinding] = []
    incoming_keys: Set[tuple] = set()

    def get_or_create_finding(
        particular_id: str,
        question: str,
        answer: str,
        finding_type: str,
        status: str,
        confidence: Optional[float],
        evidence: Dict[str, Any],
        doc_types: Optional[Set[str]] = None,
    ) -> ApplicationFinding:
        key = (particular_id, question)
        incoming_keys.add(key)
        if key in existing_finding_lookup:
            f = existing_finding_lookup[key]
            f.answer = answer
            f.finding_type = finding_type
            f.status = status
            f.confidence = confidence
            f.evidence = evidence
        else:
            f = ApplicationFinding(
                application_id=application_id,
                particular_id=particular_id,
                question=question,
                answer=answer,
                finding_type=finding_type,
                status=status,
                confidence=confidence,
                evidence=evidence,
            )
            db.add(f)
            existing_finding_lookup[key] = f

        if doc_types:
            _link_documents_by_types(f, doc_types, docs_by_type)

        persisted_findings.append(f)
        return f

    def upsert_verification_result(
        check_type: str,
        status: str,
        confidence_score: float,
        details: Dict[str, Any],
    ) -> VerificationResult:
        if check_type in existing_result_lookup:
            vr = existing_result_lookup[check_type]
            vr.status = status
            vr.confidence_score = confidence_score
            vr.details = details
        else:
            vr = VerificationResult(
                application_id=application_id,
                check_type=check_type,
                status=status,
                confidence_score=confidence_score,
                details=details,
            )
            db.add(vr)
            existing_result_lookup[check_type] = vr
        return vr

    # ─────────────────────────────────────────────────────────────────
    # 2. NORMALIZE verification_details["checks"]
    # ─────────────────────────────────────────────────────────────────
    checks = verification_details.get("checks", [])
    if isinstance(checks, list):
        for check in checks:
            if not isinstance(check, dict):
                continue

            name = check.get("name", "")
            score = float(check.get("score", 0.0) or 0.0)
            passed = bool(check.get("passed", False))
            weight = check.get("weight", 0)
            details = str(check.get("details", "") or "")

            status_val = FindingStatus.VERIFIED.value if passed else (
                FindingStatus.REVIEW.value if score > 0 else FindingStatus.NOT_VERIFIED.value
            )
            vr_status = "PASS" if passed else ("WARNING" if score > 0 else "FAIL")

            if name == "Identity Verification":
                get_or_create_finding(
                    particular_id="P1",
                    question="Borrower / Applicant Identity Verification",
                    answer=details or f"Identity verification score: {score} (Passed: {passed})",
                    finding_type="identity",
                    status=status_val,
                    confidence=round(score, 3),
                    evidence={
                        "source": "verification_engine",
                        "check": name,
                        "score": score,
                        "weight": weight,
                        "passed": passed,
                        "details": details,
                    },
                    doc_types={"aadhaar", "pan"},
                )
                upsert_verification_result(
                    check_type="identity",
                    status=vr_status,
                    confidence_score=round(score, 3),
                    details={"check": name, "details": details, "weight": weight, "passed": passed},
                )

            elif name == "Aadhaar Format":
                get_or_create_finding(
                    particular_id="P2E",
                    question="Aadhaar Number Format and Structure Validation",
                    answer=details or f"Aadhaar format validation score: {score} (Passed: {passed})",
                    finding_type="identity",
                    status=status_val,
                    confidence=round(score, 3),
                    evidence={
                        "source": "verification_engine",
                        "check": name,
                        "score": score,
                        "weight": weight,
                        "passed": passed,
                        "details": details,
                    },
                    doc_types={"aadhaar"},
                )
                upsert_verification_result(
                    check_type="aadhaar",
                    status=vr_status,
                    confidence_score=round(score, 3),
                    details={"check": name, "details": details, "weight": weight, "passed": passed},
                )

            elif name == "PAN Format":
                get_or_create_finding(
                    particular_id="P3",
                    question="Permanent Account Number (PAN) Format Validation",
                    answer=details or f"PAN format validation score: {score} (Passed: {passed})",
                    finding_type="compliance",
                    status=status_val,
                    confidence=round(score, 3),
                    evidence={
                        "source": "verification_engine",
                        "check": name,
                        "score": score,
                        "weight": weight,
                        "passed": passed,
                        "details": details,
                    },
                    doc_types={"pan"},
                )
                upsert_verification_result(
                    check_type="pan",
                    status=vr_status,
                    confidence_score=round(score, 3),
                    details={"check": name, "details": details, "weight": weight, "passed": passed},
                )

            elif name == "Income Verification":
                get_or_create_finding(
                    particular_id="P4",
                    question="Applicant Income & Repayment Capacity Eligibility",
                    answer=details or f"Income eligibility check score: {score} (Passed: {passed})",
                    finding_type="income",
                    status=status_val,
                    confidence=round(score, 3),
                    evidence={
                        "source": "verification_engine",
                        "check": name,
                        "score": score,
                        "weight": weight,
                        "passed": passed,
                        "details": details,
                    },
                    doc_types={"salary_slip", "income_cert", "form_16", "itr"},
                )
                upsert_verification_result(
                    check_type="income",
                    status=vr_status,
                    confidence_score=round(score, 3),
                    details={"check": name, "details": details, "weight": weight, "passed": passed},
                )

            elif name == "Required Documents":
                get_or_create_finding(
                    particular_id="P6",
                    question="Mandatory Document Submission Completeness",
                    answer=details or f"Required documents score: {score} (Passed: {passed})",
                    finding_type="compliance",
                    status=status_val,
                    confidence=round(score, 3),
                    evidence={
                        "source": "verification_engine",
                        "check": name,
                        "score": score,
                        "weight": weight,
                        "passed": passed,
                        "details": details,
                    },
                    doc_types={"aadhaar", "pan", "salary_slip", "form_16", "bank_statement"},
                )
                upsert_verification_result(
                    check_type="required_docs",
                    status=vr_status,
                    confidence_score=round(score, 3),
                    details={"check": name, "details": details, "weight": weight, "passed": passed},
                )

    # ─────────────────────────────────────────────────────────────────
    # 3. NORMALIZE fraud_analysis["details"]
    # ─────────────────────────────────────────────────────────────────
    fraud_details = fraud_analysis.get("details", [])
    overall_fraud_flag = bool(fraud_analysis.get("fraud_flag", False))
    overall_risk_score = float(fraud_analysis.get("risk_score", 0.0) or 0.0)

    # Record overall fraud verification result
    upsert_verification_result(
        check_type="fraud",
        status="FAIL" if overall_fraud_flag else ("WARNING" if overall_risk_score > 25.0 else "PASS"),
        confidence_score=round(max(0.0, 1.0 - (overall_risk_score / 100.0)), 3),
        details={
            "risk_score": overall_risk_score,
            "fraud_flag": overall_fraud_flag,
            "alerts": fraud_analysis.get("alerts", []),
        },
    )

    if isinstance(fraud_details, list) and len(fraud_details) > 0:
        for fd in fraud_details:
            if not isinstance(fd, dict):
                continue

            check_name = fd.get("check", "")
            risk_added = float(fd.get("risk_added", 0.0) or 0.0)
            detail_msg = str(fd.get("detail", "") or "")

            base_evidence = {
                "source": "fraud_analysis",
                "check": check_name,
                "risk_added": risk_added,
                "risk_score": overall_risk_score,
                "fraud_flag": overall_fraud_flag,
            }

            if check_name == "duplicate_aadhaar":
                is_anomaly = risk_added > 0 or "duplicate" in detail_msg.lower()
                get_or_create_finding(
                    particular_id="P6",
                    question="Cross-Application Duplicate Aadhaar Scrutiny",
                    answer=detail_msg or "Duplicate Aadhaar detected across applications.",
                    finding_type="fraud",
                    status=FindingStatus.NOT_VERIFIED.value if is_anomaly else FindingStatus.VERIFIED.value,
                    confidence=1.0,
                    evidence=base_evidence,
                    doc_types={"aadhaar"},
                )

            elif check_name == "missing_documents":
                is_missing = risk_added > 0 or "missing" in detail_msg.lower()
                get_or_create_finding(
                    particular_id="P6",
                    question="Mandatory Application Documentation Availability",
                    answer=detail_msg or "Critical application documents are missing.",
                    finding_type="fraud",
                    status=FindingStatus.NOT_AVAILABLE.value if is_missing else FindingStatus.VERIFIED.value,
                    confidence=1.0,
                    evidence=base_evidence,
                )

            elif check_name == "high_income_claim":
                is_anomaly = risk_added > 0 or "high" in detail_msg.lower()
                get_or_create_finding(
                    particular_id="P6",
                    question="Unusual High Income Claim Scrutiny",
                    answer=detail_msg or "High claimed income requires corroboration.",
                    finding_type="fraud",
                    status=FindingStatus.NOT_VERIFIED.value if is_anomaly else FindingStatus.VERIFIED.value,
                    confidence=1.0,
                    evidence=base_evidence,
                    doc_types={"salary_slip", "income_cert", "form_16", "itr"},
                )

            elif check_name == "high_lti_ratio":
                is_anomaly = risk_added > 0 or "high" in detail_msg.lower()
                get_or_create_finding(
                    particular_id="P6",
                    question="Loan-To-Income (LTI) Exposure Scrutiny",
                    answer=detail_msg or "High loan-to-annual-income ratio detected.",
                    finding_type="fraud",
                    status=FindingStatus.NOT_VERIFIED.value if is_anomaly else FindingStatus.VERIFIED.value,
                    confidence=1.0,
                    evidence=base_evidence,
                    doc_types={"salary_slip", "income_cert", "form_16"},
                )

            elif check_name == "missing_name":
                is_missing = risk_added > 0 or "missing" in detail_msg.lower()
                get_or_create_finding(
                    particular_id="P6",
                    question="Applicant Name Extraction and Consistency",
                    answer=detail_msg or "Applicant name could not be verified from documents.",
                    finding_type="fraud",
                    status=FindingStatus.NOT_VERIFIED.value if is_missing else FindingStatus.VERIFIED.value,
                    confidence=1.0,
                    evidence=base_evidence,
                    doc_types={"aadhaar", "pan"},
                )

            elif check_name == "invalid_pan":
                is_invalid = risk_added > 0 or "invalid" in detail_msg.lower()
                get_or_create_finding(
                    particular_id="P6",
                    question="Permanent Account Number (PAN) Format Authenticity",
                    answer=detail_msg or "PAN format does not conform to income tax regulations.",
                    finding_type="fraud",
                    status=FindingStatus.NOT_VERIFIED.value if is_invalid else FindingStatus.VERIFIED.value,
                    confidence=1.0,
                    evidence=base_evidence,
                    doc_types={"pan"},
                )

            else:
                is_anomaly = risk_added > 0
                clean_q = f"Fraud Scrutiny: {check_name.replace('_', ' ').title()}"
                AUTOMATED_CHECK_QUESTIONS.add(clean_q)
                get_or_create_finding(
                    particular_id="P6",
                    question=clean_q,
                    answer=detail_msg or f"Fraud evaluation check: {check_name}",
                    finding_type="fraud",
                    status=FindingStatus.NOT_VERIFIED.value if is_anomaly else FindingStatus.VERIFIED.value,
                    confidence=1.0 if is_anomaly else None,
                    evidence=base_evidence,
                )

    elif overall_risk_score == 0.0 and not overall_fraud_flag:
        get_or_create_finding(
            particular_id="P6",
            question="Fraud Detection & Duplicate Record Scrutiny",
            answer="No anomalies, duplicate records, or high-risk fraud indicators detected.",
            finding_type="fraud",
            status=FindingStatus.VERIFIED.value,
            confidence=1.0,
            evidence={
                "source": "fraud_analysis",
                "risk_score": 0.0,
                "fraud_flag": False,
                "alerts": [],
            },
        )

    # ─────────────────────────────────────────────────────────────────
    # 4. NORMALIZE agent_trace["agent_findings"] (AGENTIC MODE ONLY)
    # ─────────────────────────────────────────────────────────────────
    if agent_trace and isinstance(agent_trace, dict):
        agent_findings = agent_trace.get("agent_findings", {})
        if isinstance(agent_findings, dict):
            for agent_key, af in agent_findings.items():
                if not af:
                    continue

                decision_text = ""
                status_val = FindingStatus.REVIEW.value
                conf_val = None

                if isinstance(af, dict):
                    decision_text = str(af.get("decision") or af.get("finding") or af.get("recommendation") or "").strip()
                    if not decision_text and "observation" in af:
                        decision_text = str(af["observation"]).strip()
                elif isinstance(af, str):
                    decision_text = af.strip()

                if not decision_text or len(decision_text) < 5:
                    continue

                if decision_text.startswith("```"):
                    continue

                target_pid = "P6"
                target_type = "compliance"
                q_text = f"Agent Finding: {agent_key.replace('_', ' ').title()}"
                k_lower = agent_key.lower()

                if any(k in k_lower for k in ("income", "financial", "verification_officer", "loan_officer", "loan_verification")):
                    target_pid = "P4"
                    target_type = "income"
                elif any(k in k_lower for k in ("document", "ocr", "extraction")):
                    target_pid = "P1"
                    target_type = "identity"
                elif any(k in k_lower for k in ("collateral", "property", "asset", "site")):
                    target_pid = "P7"
                    target_type = "collateral"
                elif any(k in k_lower for k in ("fraud", "risk")):
                    target_pid = "P6"
                    target_type = "fraud"

                AUTOMATED_CHECK_QUESTIONS.add(q_text)
                get_or_create_finding(
                    particular_id=target_pid,
                    question=q_text,
                    answer=decision_text,
                    finding_type=target_type,
                    status=status_val,
                    confidence=conf_val,
                    evidence={
                        "source": "agent_trace",
                        "agent_key": agent_key,
                    },
                )

    # ─────────────────────────────────────────────────────────────────
    # 5. REMOVE ONLY STALE AUTOMATED FINDINGS
    # ─────────────────────────────────────────────────────────────────
    for f in existing_findings:
        key = (f.particular_id, f.question)
        is_automated = (
            f.question in AUTOMATED_CHECK_QUESTIONS
            or (isinstance(f.evidence, dict) and f.evidence.get("source") in ("verification_engine", "fraud_analysis", "agent_trace"))
        )
        if is_automated and key not in incoming_keys:
            db.delete(f)

    db.commit()

    for f in persisted_findings:
        db.refresh(f)

    logger.info(
        "Successfully normalized %d findings and %d verification results for application %d",
        len(persisted_findings),
        len(existing_result_lookup),
        application_id,
    )
    return persisted_findings
