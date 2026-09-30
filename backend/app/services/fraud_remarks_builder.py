"""
Fraud & Document Remarks Builder Service.
Deterministically resolves upstream verification and fraud findings into a
normalized, traceable DocumentFraudRemarksSection.

Architectural Constraints:
- ZERO database queries
- ZERO Gemini / LLM calls
- ZERO RAG / vector retrieval
- ZERO mutation of upstream facts or findings
- Deterministic document name resolution and internal ID preservation
"""
from typing import List, Optional, Dict, Any, Union
import logging

from app.schemas.fraud_remarks import DocumentFraudRemark, DocumentFraudRemarksSection
from app.schemas.report_context import NormalizedFinding, NormalizedDocumentMeta

logger = logging.getLogger(__name__)

DOCUMENT_DISPLAY_NAMES: Dict[str, str] = {
    "salary_slip": "Salary Slip",
    "pan": "PAN Card",
    "aadhaar": "Aadhaar",
    "bank_statement": "Bank Statement",
    "form_16": "Form 16",
    "income_cert": "Income Certificate",
    "itr": "ITR",
    "sale_deed": "Sale Deed",
    "tax_receipt": "Tax Receipt",
    "encumbrance_cert": "Encumbrance Certificate",
    "building_plan": "Building Plan",
    "ownership_proof": "Ownership Proof",
    "property_image": "Property Document",
    "vehicle_document": "Vehicle Document",
    "driving_licence": "Driving Licence",
    "residence_proof": "Residence Proof",
    "loan_application": "Loan Application",
    "chatbot_upload": "Uploaded Document",
    "other": "Supporting Document",
}


def build_document_fraud_remarks(
    findings: List[Any],
    documents: Optional[List[Any]] = None,
) -> DocumentFraudRemarksSection:
    """
    Deterministically normalizes upstream findings into DocumentFraudRemarksSection.
    Only includes document-related fraud, suspicious, or review-required findings.
    Preserves verbatim upstream finding text and internal traceability IDs.
    """
    documents = documents or []
    doc_by_id = {}
    doc_by_type = {}
    for d in documents:
        d_id = getattr(d, "document_id", None) or getattr(d, "id", None)
        d_type = getattr(d, "document_type", None)
        if hasattr(d_type, "value"):
            d_type = str(d_type.value)
        elif d_type is not None:
            d_type = str(d_type)

        if d_id is not None:
            doc_by_id[d_id] = (d_id, d_type)
        if d_type:
            doc_by_type.setdefault(d_type.lower(), []).append(d_id)

    remarks: List[DocumentFraudRemark] = []
    seen_keys = set()

    for f in findings:
        status = (getattr(f, "status", None) or "REVIEW").upper().strip()
        evidence = getattr(f, "evidence", None) or {}
        if not isinstance(evidence, dict):
            evidence = {}

        # 1. Skip if cleanly verified with no risk
        has_fraud_flag = bool(evidence.get("fraud_flag", False))
        risk_added = float(evidence.get("risk_added", 0.0) or 0.0)
        is_clean_verified = (status == "VERIFIED" and not has_fraud_flag and risk_added <= 0)
        if is_clean_verified:
            continue

        q = str(getattr(f, "question", "") or "")
        ans = str(getattr(f, "answer", "") or "")
        cat = str(getattr(f, "category", "") or getattr(f, "finding_type", "") or "").lower()
        linked_doc_ids = getattr(f, "linked_document_ids", None) or []
        if not linked_doc_ids and hasattr(f, "documents"):
            linked_doc_ids = [getattr(d, "id", None) for d in f.documents if getattr(d, "id", None)]

        combined_text = f"{q} {ans}".lower()

        # 2. Check if this finding is document-related or fraud-related
        is_fraud_cat = cat in ["fraud", "compliance"]
        has_fraud_source = evidence.get("source") in ["fraud_analysis", "agent_trace"]
        has_doc_keywords = any(
            k in combined_text
            for k in [
                "salary", "pay slip", "payslip", "pan", "aadhaar", "uidai",
                "bank statement", "statement", "tax receipt", "tax_receipt",
                "form 16", "form_16", "itr", "income tax", "sale deed",
                "document", "forgery", "tamper", "inconsisten", "duplicate",
                "authenticity", "suspicious", "mismatch", "invalid"
            ]
        )
        is_doc_finding = bool(linked_doc_ids) or is_fraud_cat or has_fraud_source or has_doc_keywords

        # If not document or fraud related, skip
        if not is_doc_finding:
            continue

        # If the finding text explicitly says no anomalies detected, skip
        if "no anomalies" in combined_text or "no significant document fraud" in combined_text:
            continue

        # 3. Determine document type, name, and ID
        target_doc_id = None
        target_doc_type = None
        target_doc_name = None

        if linked_doc_ids:
            first_id = linked_doc_ids[0]
            if first_id in doc_by_id:
                target_doc_id, target_doc_type = doc_by_id[first_id]

        if not target_doc_type:
            # Deduce from text or check name
            check_name = str(evidence.get("check", "") or "").lower()
            ref_text = f"{check_name} {combined_text}"
            if "salary" in ref_text or "payslip" in ref_text or "pay slip" in ref_text:
                target_doc_type = "salary_slip"
            elif "pan" in ref_text:
                target_doc_type = "pan"
            elif "aadhaar" in ref_text or "uidai" in ref_text:
                target_doc_type = "aadhaar"
            elif "bank statement" in ref_text or "bank" in ref_text:
                target_doc_type = "bank_statement"
            elif "form 16" in ref_text or "form_16" in ref_text:
                target_doc_type = "form_16"
            elif "tax" in ref_text:
                target_doc_type = "tax_receipt"
            elif "deed" in ref_text:
                target_doc_type = "sale_deed"
            elif "itr" in ref_text or "income tax" in ref_text:
                target_doc_type = "itr"
            elif "vehicle" in ref_text or "rc" in ref_text:
                target_doc_type = "vehicle_document"
            elif "document" in ref_text:
                target_doc_type = "other"

        if target_doc_type and not target_doc_id:
            available_ids = doc_by_type.get(target_doc_type.lower(), [])
            if available_ids:
                target_doc_id = available_ids[0]

        if target_doc_type:
            target_doc_name = DOCUMENT_DISPLAY_NAMES.get(
                target_doc_type.lower(),
                target_doc_type.replace("_", " ").title()
            )
        else:
            # Fallback based on category
            if cat == "identity":
                target_doc_name = "Identity Document"
                target_doc_type = "identity"
            elif cat == "income":
                target_doc_name = "Salary Slip"
                target_doc_type = "salary_slip"
            else:
                target_doc_name = "Application Documents"
                target_doc_type = "document"

        # 4. Reason (preserve verbatim)
        reason = ans.strip() if ans.strip() else q.strip()
        if not reason:
            reason = "Document verification requires manual scrutiny."

        # 5. Severity
        # Prefer: REVIEW REQUIRED, POTENTIAL FRAUD INDICATOR, DOCUMENT INCONSISTENCY DETECTED, VERIFICATION REQUIRED
        if has_fraud_flag or "fraud" in combined_text:
            severity = "POTENTIAL FRAUD INDICATOR"
        elif status in ["NOT_VERIFIED", "FAIL"]:
            severity = "DOCUMENT INCONSISTENCY DETECTED"
        elif status == "NOT_AVAILABLE":
            severity = "VERIFICATION REQUIRED"
        else:
            severity = "REVIEW REQUIRED"

        # Deduplication
        dedup_key = (target_doc_name, reason)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        f_id = getattr(f, "finding_id", None) or getattr(f, "id", None)
        remark_item = DocumentFraudRemark(
            finding_id=f_id,
            document_id=target_doc_id,
            document_type=target_doc_type,
            document_name=target_doc_name,
            category=cat if cat else None,
            severity=severity,
            reason=reason,
            evidence_reference=f"finding:{f_id}" if f_id else None,
            source=str(getattr(f, "source", "") or "application_findings"),
        )
        remarks.append(remark_item)

    if remarks:
        return DocumentFraudRemarksSection(
            has_remarks=True,
            status="REVIEW REQUIRED",
            remarks=remarks,
        )

    return DocumentFraudRemarksSection(
        has_remarks=False,
        status="No significant document fraud indicators detected.",
        remarks=[],
    )
