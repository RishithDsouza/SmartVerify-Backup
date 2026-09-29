"""
SmartVerify Multi-Agent Task Definitions — Milestone 2
───────────────────────────────────────────────────────
Defines the strictly sequential tasks for the loan verification crew.

Each task feeds its output to the next agent via CrewAI's `context` mechanism.
No agent repeats OCR or NLP — all agents consume the Structured JSON already
stored by the Milestone 1 pipeline.

Pipeline:
    analyse_documents         (Document Analyst)
        → extract_profile     (Data Extraction Specialist)
            → verify_loan     (Loan Verification Officer)
                → verify_gov  (Government Verification Agent)
                    → compile_report (Compliance Reporter)
"""

import json
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from crewai import Task


class ComplianceReportOutput(BaseModel):
    application_id: int
    final_status: str
    verification_score: float = 0.0
    risk_score: float = 0.0
    fraud_flag: bool = False
    overall_ai_confidence: Optional[float] = 0.0
    extracted_info: Dict[str, Any] = Field(default_factory=dict)
    verification_details: Dict[str, Any] = Field(default_factory=dict)
    fraud_analysis: Dict[str, Any] = Field(default_factory=dict)
    agent_findings: Dict[str, Any] = Field(default_factory=dict)
    recommendation: str = ""
    human_review: str = ""
    summary: str = ""
    pdf_path: Optional[str] = None


LOAN_TYPE_DOCUMENT_RULES: Dict[str, Dict[str, Any]] = {
    "vehicle loan": {
        "required_identity": ["aadhaar", "pan"],
        "required_income": ["salary_slip", "income_cert", "form_16", "itr", "bank_statement"],
        "required_collateral": ["vehicle_document", "invoice"],
        "supporting": ["residence_proof", "site_front_view", "house_photograph"],
        "checklist_text": (
            "Required document categories for Vehicle Loan:\n"
            "  1. Identity proof: Aadhaar or PAN (or Driving Licence)\n"
            "  2. Income/financial proof: At least one of salary_slip, income_cert, form_16, ITR, or bank_statement\n"
            "  3. Vehicle / collateral documents: vehicle_document (RC) or vehicle purchase invoice\n"
            "  4. Supporting / site evidence: residence_proof and site_front_view (treated as supporting site/residence photographic evidence, not generic text documents)"
        ),
    },
    "home loan": {
        "required_identity": ["aadhaar", "pan"],
        "required_income": ["salary_slip", "income_cert", "form_16", "itr", "bank_statement"],
        "required_collateral": ["sale_deed", "tax_receipt", "encumbrance_cert", "property_image"],
        "supporting": ["residence_proof", "site_front_view", "house_photograph"],
        "checklist_text": (
            "Required document categories for Home Loan:\n"
            "  1. Identity proof: Aadhaar and PAN\n"
            "  2. Income proof: At least one of salary_slip, income_cert, form_16, ITR, or bank_statement\n"
            "  3. Property / collateral documents: sale_deed, tax_receipt, encumbrance_cert, or property_image\n"
            "  4. Supporting evidence: residence_proof and site_front_view (supporting site photographic evidence)"
        ),
    },
    "personal loan": {
        "required_identity": ["aadhaar", "pan"],
        "required_income": ["salary_slip", "income_cert", "form_16", "itr", "bank_statement"],
        "required_collateral": [],
        "supporting": ["residence_proof"],
        "checklist_text": (
            "Required document categories for Personal Loan:\n"
            "  1. Identity proof: Aadhaar and PAN\n"
            "  2. Income proof: At least one of salary_slip, income_cert, form_16, ITR, or bank_statement\n"
            "  3. Supporting evidence: residence_proof"
        ),
    },
    "business loan": {
        "required_identity": ["pan", "aadhaar"],
        "required_income": ["bank_statement", "itr", "form_16", "financial_statement"],
        "required_collateral": ["invoice", "tax_receipt", "property_image"],
        "supporting": ["residence_proof", "site_front_view"],
        "checklist_text": (
            "Required document categories for Business Loan:\n"
            "  1. Identity proof: PAN and Aadhaar\n"
            "  2. Financial/income proof: At least one of bank_statement, ITR, or form_16\n"
            "  3. Business/collateral documents: Registration, invoice, or tax receipt\n"
            "  4. Supporting evidence: residence_proof or site_front_view"
        ),
    },
    "education loan": {
        "required_identity": ["aadhaar", "pan"],
        "required_income": ["salary_slip", "form_16", "itr", "bank_statement"],
        "required_collateral": [],
        "supporting": ["residence_proof"],
        "checklist_text": (
            "Required document categories for Education Loan:\n"
            "  1. Identity proof: Aadhaar and PAN\n"
            "  2. Income proof (co-borrower / parent): At least one of salary_slip, form_16, ITR, or bank_statement\n"
            "  3. Supporting evidence: residence_proof"
        ),
    },
}


def get_loan_document_rules(loan_type: str) -> Dict[str, Any]:
    """Retrieve loan-type-aware document requirement rules and checklist description."""
    normalized = (loan_type or "").strip().lower()
    for key, rules in LOAN_TYPE_DOCUMENT_RULES.items():
        if key in normalized or normalized in key:
            return rules
    # Default fallback for unlisted loan types
    return {
        "required_identity": ["aadhaar", "pan"],
        "required_income": ["salary_slip", "income_cert", "form_16", "itr", "bank_statement"],
        "required_collateral": [],
        "supporting": ["residence_proof", "site_front_view"],
        "checklist_text": (
            f"Required document categories for {loan_type or 'General Loan'}:\n"
            "  1. Identity proof: At least one of aadhaar or pan\n"
            "  2. Income proof: At least one of salary_slip, income_cert, form_16, ITR, or bank_statement\n"
            "  3. Collateral / asset documents: Any uploaded collateral or asset documents\n"
            "  4. Supporting evidence: residence_proof or site inspection evidence"
        ),
    }


def build_tasks(agents: dict, run_context: dict) -> list:
    """
    Build the ordered list of CrewAI tasks for a verification run.

    `run_context` is a dict with keys:
        application_id    (int)
        applicant_name    (str)
        loan_amount       (float)
        loan_type         (str, optional)
        loan_tenure       (int, optional)
        gov_verification_record (dict, optional)
        documents         (list[dict]) — each:
                            {id, document_type, file_path,
                             original_name, structured_data, extracted_text}
        shared_context_summary (str, optional)
    """
    application_id = run_context["application_id"]
    applicant_name = run_context.get("applicant_name") or "Unknown Applicant"
    loan_amount    = run_context["loan_amount"]
    loan_type      = run_context.get("loan_type") or "Home Loan"
    loan_tenure    = run_context.get("loan_tenure") or 60
    gov_record     = run_context.get("gov_verification_record") or {}
    documents      = run_context["documents"]

    # ── Build a compact metadata string for Task 1 ───────────────────────
    doc_list_str = "\n".join(
        f"  - doc_id={d['id']}, type={d['document_type']}, "
        f"file={d.get('original_name', '')}"
        for d in documents
    ) or "  (no documents uploaded)"

    # ── Build the merged Structured JSON for Task 1 ──────────────────────
    # Merge all per-document structured_data JSONs into a single dict.
    # Later fields overwrite earlier ones; applicant_name/loan_amount
    # fall back to the application record values.
    merged_structured: dict = {}
    doc_structured_details = []
    for d in documents:
        raw_json = d.get("structured_data") or "{}"
        try:
            parsed = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        except Exception:
            parsed = {}
        # Merge (prefer non-null values)
        for k, v in parsed.items():
            if v is not None:
                merged_structured[k] = v
        doc_structured_details.append({
            "doc_id": d["id"],
            "document_type": d["document_type"],
            "file_path": d.get("file_path", ""),
            "has_structured_data": bool(parsed),
            "structured_data": parsed,
        })

    # Fallback applicant profile fields from application record
    if not merged_structured.get("applicant_name"):
        merged_structured["applicant_name"] = applicant_name
    if not merged_structured.get("loan_amount"):
        merged_structured["loan_amount"] = loan_amount

    merged_json_str = json.dumps(merged_structured, indent=2)
    doc_structured_str = json.dumps(doc_structured_details, indent=2)
    doc_types_json = json.dumps(
        [{"document_type": d["document_type"]} for d in documents]
    )

    # ── Document verification & metadata assessment ───────────────────────
    VISUAL_EVIDENCE_TYPES = {
        "site_front_view", "site_side_view", "site_interior", "site_entrance",
        "property_image", "house_photograph"
    }

    doc_meta_notes = []
    for d in documents:
        doc_type = d.get("document_type") or ""
        text = d.get("extracted_text") or ""
        raw_json = d.get("structured_data") or "{}"
        try:
            parsed_sd = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        except Exception:
            parsed_sd = {}
        has_struct = bool(parsed_sd)
        is_visual = doc_type in VISUAL_EVIDENCE_TYPES

        if is_visual:
            doc_quality = "good"
            ocr_state = "visual_evidence (photographic site/property evidence; text OCR not applicable)"
        elif has_struct:
            doc_quality = "good"
            ocr_state = "verified_structured_data_available (authoritative metadata present in database)"
        elif len(text) > 50:
            doc_quality = "good"
            ocr_state = f"ocr_text_available ({len(text)} chars)"
        elif len(text) > 5:
            doc_quality = "partial"
            ocr_state = f"ocr_partial_text ({len(text)} chars)"
        else:
            doc_quality = "partial"
            ocr_state = "ocr_text_unavailable"

        doc_meta_notes.append(
            f"  - doc_id={d['id']}, type={doc_type}, file={d.get('original_name', '')}, "
            f"has_structured_data={has_struct}, ocr_status={ocr_state}, quality={doc_quality}"
        )
    doc_meta_str = "\n".join(doc_meta_notes) or "  (no documents uploaded)"

    # ── Loan type document rules ──────────────────────────────────────────
    rules = get_loan_document_rules(loan_type)
    loan_rules_checklist = rules["checklist_text"]

    # ─────────────────────────────────────────────────────────────────────
    # TASK 1 — Document Analyst
    # ─────────────────────────────────────────────────────────────────────
    analyse_documents = Task(
        description=(
            f"You are reviewing loan application #{application_id} for applicant: "
            f"{applicant_name} (loan amount: ₹{loan_amount}, type: {loan_type}).\n\n"
            "UPLOADED DOCUMENTS:\n"
            f"{doc_list_str}\n\n"
            "DOCUMENT METADATA & OCR STATUS (authoritative pre-extracted information):\n"
            f"{doc_meta_str}\n\n"
            "PER-DOCUMENT STRUCTURED DATA (already extracted and verified by OCR+NLP pipeline):\n"
            f"{doc_structured_str}\n\n"
            "LOAN TYPE DOCUMENT REQUIREMENTS:\n"
            f"{loan_rules_checklist}\n\n"
            "ANALYSIS RULES & INSTRUCTIONS:\n"
            "1. DISTINGUISH OCR UNAVAILABILITY FROM DOCUMENT UNREADABILITY:\n"
            "   - If document_ocr_tool returns NO_TEXT_EXTRACTED or OCR text is unavailable, but authoritative structured_data / verified metadata is present in the database, the document is READABLE and VERIFIED. Do NOT mark it as unreadable or in unreadable_documents.\n"
            "   - Do NOT fabricate OCR text.\n"
            "   - A document is ONLY 'unreadable' if the file is genuinely corrupted, blank, or completely illegible with NO verified structured data.\n"
            "2. PHOTOGRAPHIC / SITE EVIDENCE:\n"
            "   - site_front_view (and any site/property photos) are visual evidence of the physical property/site inspection, NOT generic text documents. They do not require OCR text and must NOT be marked as unreadable or poor quality.\n"
            "3. LOAN-TYPE DOCUMENT COMPLETENESS:\n"
            f"   - Evaluate completeness against the Loan Type Document Requirements for {loan_type}.\n"
            f"   - For {loan_type}, verify that identity, income, vehicle/collateral, and supporting documents in UPLOADED DOCUMENTS are recognized.\n"
            "   - Do NOT treat Aadhaar, PAN, income documents (salary_slip, form_16), bank statement, or vehicle documents (vehicle_document/RC, invoice) as missing if they are listed in UPLOADED DOCUMENTS.\n"
            "   - If all required categories for the loan type are met, set document_status = 'complete'.\n\n"
            "Your task is to analyse the above data and produce a JSON object with this exact shape:\n"
            "{\n"
            '  "document_status": "complete" | "incomplete" | "unreadable",\n'
            '  "document_quality": "good" | "partial" | "poor",\n'
            '  "missing_documents": [<list of missing required document types>],\n'
            '  "unreadable_documents": [<doc_ids of unreadable documents>],\n'
            '  "validated_document_types": [<list of confirmed document types>],\n'
            '  "document_summary": "<2-3 sentence summary of document quality and completeness>",\n'
            '  "ocr_confidence": <number 0-100>,\n'
            '  "explainability": {\n'
            '    "input": "<summary of input data>",\n'
            '    "reasoning": "<step-by-step reasoning for document quality and completeness>",\n'
            '    "evidence_used": ["<evidence 1>", "<evidence 2>"],\n'
            '    "tools_invoked": ["document_ocr_tool"],\n'
            '    "confidence": <number 0-100>,\n'
            '    "decision": "<final analytical decision>"\n'
            '  }\n'
            "}\n\n"
            "Return ONLY this JSON object, with no extra commentary."
        ),
        expected_output=(
            "A single JSON object with document_status, document_quality, "
            "missing_documents, unreadable_documents, validated_document_types, "
            "document_summary, ocr_confidence, and explainability fields."
        ),
        agent=agents["document_analyst"],
    )

    # ─────────────────────────────────────────────────────────────────────
    # TASK 2 — Data Extraction Specialist
    # ─────────────────────────────────────────────────────────────────────
    extract_profile = Task(
        description=(
            "You have received the Document Analyst's assessment from the previous task.\n\n"
            "Now consume the following Structured JSON that was produced by the earlier pipeline:\n"
            f"{merged_json_str}\n\n"
            "CRITICAL: If OCR text is available, you may use information_extraction_tool. If raw OCR text is unavailable, rely directly on the authoritative Structured JSON provided above without fabricating OCR text.\n\n"
            "Application defaults (use if field is missing):\n"
            f"  - applicant_name: {applicant_name}\n"
            f"  - loan_amount: {loan_amount}\n\n"
            "Your task:\n"
            "1. Validate each extracted value (e.g., PAN format ABCDE1234F, Aadhaar 12 digits).\n"
            "2. Normalize names to Title Case.\n"
            "3. Normalize dates to DD-MM-YYYY format if possible.\n"
            "4. Identify any missing mandatory fields "
            "(applicant_name, aadhaar_number, pan_number, dob).\n"
            "5. Produce a cleaned applicant profile.\n\n"
            "Return ONLY a JSON object with this exact shape:\n"
            "{\n"
            '  "applicant_profile": {\n'
            '    "applicant_name": "<str>",\n'
            '    "aadhaar_number": "<str>",\n'
            '    "pan_number": "<str>",\n'
            '    "dob": "<str>",\n'
            '    "gender": "<str>",\n'
            '    "address": "<str>",\n'
            '    "phone": "<str>",\n'
            '    "employer_name": "<str>",\n'
            '    "monthly_income": <number or null>,\n'
            '    "loan_amount": <number>,\n'
            '    "bank_account": "<str>"\n'
            "  },\n"
            '  "validation_errors": [<list of validation error strings>],\n'
            '  "missing_fields": [<list of missing mandatory field names>],\n'
            '  "normalized_data": {<same as applicant_profile but with all normalizations noted>},\n'
            '  "extraction_confidence": <number 0-100>,\n'
            '  "explainability": {\n'
            '    "input": "<summary of input data>",\n'
            '    "reasoning": "<step-by-step reasoning for extraction>",\n'
            '    "evidence_used": ["<evidence 1>", "<evidence 2>"],\n'
            '    "tools_invoked": ["information_extraction_tool"],\n'
            '    "confidence": <number 0-100>,\n'
            '    "decision": "<final extraction decision>"\n'
            '  }\n'
            "}\n"
            "Return ONLY this JSON object, with no extra commentary."
        ),
        expected_output=(
            "A single JSON object with applicant_profile, validation_errors, "
            "missing_fields, normalized_data, extraction_confidence, and explainability."
        ),
        agent=agents["extraction_specialist"],
        context=[analyse_documents],
    )

    # ─────────────────────────────────────────────────────────────────────
    # TASK 3 — Loan Verification Officer
    # ─────────────────────────────────────────────────────────────────────
    verify_loan = Task(
        description=(
            "You have received the cleaned Applicant Profile from the Data Extraction Specialist.\n\n"
            "Loan Application Details:\n"
            f"  - Application ID: {application_id}\n"
            f"  - Loan Amount: ₹{loan_amount}\n"
            f"  - Loan Type: {loan_type}\n"
            f"  - Loan Tenure: {loan_tenure} months\n\n"
            "Submitted Document Types:\n"
            f"  {doc_types_json}\n\n"
            "Eligibility Rules to apply:\n"
            "1. Applicant must have a valid Aadhaar AND PAN number.\n"
            "2. Monthly income must be present and ≥ ₹15,000.\n"
            "3. EMI affordability: loan_amount / tenure ≤ 50% of monthly_income.\n"
            "4. At least one income document (salary_slip, income_cert, or form_16) must be present.\n"
            "5. Identity documents (aadhaar, pan) must be present.\n"
            "6. For vehicle loans, vehicle documents (vehicle_document or invoice) should be acknowledged and considered.\n\n"
            "CRITICAL: BEFORE making any recommendation, you MUST use the policy_retrieval_tool to retrieve:\n"
            "  - Relevant Bank Policies\n"
            "  - Loan Eligibility Policies\n"
            "  - Income Policies\n"
            "  - Loan Type Policies\n"
            "You MUST reference the retrieved policies in your reasoning and include them in your output.\n\n"
            "Return ONLY a JSON object with this exact shape:\n"
            "{\n"
            '  "eligibility": "eligible" | "ineligible" | "review_required",\n'
            '  "verification_score": <0-100>,\n'
            '  "checks": [\n'
            '    {"check": "<name>", "passed": <bool>, "detail": "<str>"}\n'
            "  ],\n"
            '  "issues": [<list of issue strings>],\n'
            '  "recommendation": "approve" | "manual_review" | "reject",\n'
            '  "policies_retrieved": <number of policies retrieved>,\n'
            '  "rbi_guidelines": "<mention any RBI guidelines retrieved>",\n'
            '  "policy_references": ["<policy string 1>", "<policy string 2>"],\n'
            '  "verification_confidence": <number 0-100>,\n'
            '  "explainability": {\n'
            '    "input": "<summary of input data>",\n'
            '    "reasoning": "<step-by-step reasoning for eligibility>",\n'
            '    "evidence_used": ["<evidence 1>"],\n'
            '    "tools_invoked": ["policy_retrieval_tool"],\n'
            '    "confidence": <number 0-100>,\n'
            '    "decision": "<final eligibility decision>"\n'
            '  }\n'
            "}\n"
            "Return ONLY this JSON object, with no extra commentary."
        ),
        expected_output=(
            "A single JSON object with eligibility, verification_score, checks, "
            "issues, recommendation, policies_retrieved, policy_references, and explainability."
        ),
        agent=agents["verification_officer"],
        context=[extract_profile],
    )

    # ─────────────────────────────────────────────────────────────────────
    # TASK 4 — Government Verification Agent
    # ─────────────────────────────────────────────────────────────────────
    gov_record_json = json.dumps(gov_record, indent=2)

    verify_gov = Task(
        description=(
            "You have received the Applicant Profile from the Data Extraction Specialist.\n\n"
            "You have also received the following manual verification record entered by the bank officer "
            "using the Government Verification UI (Aadhaar/PAN/Tax portals):\n"
            f"{gov_record_json}\n\n"
            "Your task is to analyze this manual record against the applicant profile.\n"
            "CRITICAL: BEFORE performing verification, you MUST use the similarity_search_tool to retrieve:\n"
            "  - Similar historical applications\n"
            "  - Previous fraud cases\n"
            "  - Similar Aadhaar/PAN records\n"
            "  - Previous loan decisions\n"
            "CRITICAL: You MUST also use the fraud_detection_tool with the applicant profile to check for duplicate Aadhaar, PAN, and other historical fraud patterns.\n"
            "You MUST use this retrieved historical context and fraud tool output in your reasoning.\n\n"
            "Rules:\n"
            "  1. Verify the OCR-extracted aadhaar_number and pan_number from the profile match the officer's record.\n"
            "  2. Verify aadhaar_validity_status in the officer's record is 'Valid'.\n"
            "  3. Verify pan_aadhaar_link_status in the officer's record is 'Linked'.\n"
            "  4. If Aadhaar Valid AND PAN-Aadhaar Linked, confidence should be increased.\n"
            "  5. If Aadhaar Invalid OR PAN-Aadhaar Not Linked, flag application immediately and increase fraud risk score.\n"
            "  6. If either status is 'Pending' or 'Manual Review Required', flag application, mark 'requires_manual_review' = true and add to 'issues'.\n"
            "  7. Check for missing screenshots, missing timestamps, or missing officer names. If any are missing, set verification_status to 'Verification Incomplete' and 'requires_manual_review' = true.\n"
            "  8. If all checks pass and both statuses are positive, set verification_status to 'Government Verification Passed' and 'requires_manual_review' = false.\n\n"
            "Return ONLY a JSON object with this exact shape:\n"
            "{\n"
            '  "government_verification": {\n'
            '    "aadhaar_validity_status": "<status>",\n'
            '    "pan_aadhaar_link_status": "<status>",\n'
            '    "tax_receipt": "<status>",\n'
            '    "verification_status": "Government Verification Passed" | "Verification Incomplete" | "Manual Review",\n'
            '    "issues": ["<issue 1>", "<issue 2>"],\n'
            '    "remarks": "<synthesis of officer remarks and your findings>",\n'
            '    "requires_manual_review": <true/false>,\n'
            '    "similar_cases": <number of similar cases retrieved>,\n'
            '    "similarity_score": "<highest similarity percentage, e.g., 85%>",\n'
            '    "historical_context": ["<case 1 details>", "<case 2 details>"],\n'
            '    "fraud_score": <0-100>,\n'
            '    "fraud_flag": <true/false>,\n'
            '    "fraud_confidence": <number 0-100>,\n'
            '    "explainability": {\n'
            '      "input": "<summary of input data>",\n'
            '      "reasoning": "<step-by-step reasoning for gov verification and fraud detection>",\n'
            '      "evidence_used": ["<evidence 1>"],\n'
            '      "tools_invoked": ["similarity_search_tool", "fraud_detection_tool"],\n'
            '      "confidence": <number 0-100>,\n'
            '      "decision": "<final verification and fraud decision>"\n'
            '    }\n'
            "  }\n"
            "}\n"
            "Return ONLY this JSON object, with no extra commentary."
        ),
        expected_output=(
            "A single JSON object containing a 'government_verification' key, which holds "
            "aadhaar_validity_status, pan_aadhaar_link_status, tax_receipt, verification_status, issues, remarks, requires_manual_review, "
            "fraud fields, and explainability."
        ),
        agent=agents["gov_verification_agent"],
        context=[extract_profile, verify_loan],
    )

    # ─────────────────────────────────────────────────────────────────────
    # TASK 5 — Compliance Reporter
    # ─────────────────────────────────────────────────────────────────────
    compile_report = Task(
        description=(
            "You have the full context from all previous agents:\n"
            "  1. Document Analyst's findings\n"
            "  2. Data Extraction Specialist's cleaned applicant profile\n"
            "  3. Loan Verification Officer's eligibility assessment\n"
            "  4. Government Verification Agent's ID check results\n\n"
            "Synthesise all findings and produce the Final AI Output JSON.\n\n"
            "Decision rules:\n"
            "  - If government verification requires_manual_review == true → final_status = 'manual_review'\n"
            "  - If government verification_status == 'Manual Review' or 'Verification Incomplete' → final_status = 'manual_review'\n"
            "  - If Loan Verification Officer recommendation == 'reject' → "
            "final_status = 'rejected'\n"
            "  - If document_status == 'unreadable' → final_status = 'rejected'\n"
            "  - If recommendation == 'manual_review' OR any issues exist → "
            "final_status = 'manual_review'\n"
            "  - Otherwise → final_status = 'approved'\n\n"
            "CRITICAL: Synthesise all structured findings. Authoritative PDF report publication is managed downstream by the report review pipeline (do NOT invoke pdf_report_generation_tool).\n"
            "CRITICAL: Do NOT execute any tool calls or function calls. Do NOT call a tool named 'json' or any other tool. Your final answer must be plain text and NOT a tool call.\n\n"
            "Return ONLY a single JSON object with this exact shape "
            "(no markdown fences, no commentary):\n"
            "{\n"
            f'  "application_id": {application_id},\n'
            '  "final_status": "approved" | "manual_review" | "rejected",\n'
            '  "verification_score": <number 0-100 from Loan Verification Officer>,\n'
            '  "risk_score": <number 0-100, inverse of verification_score>,\n'
            '  "fraud_flag": <bool from Government Verification Agent>,\n'
            '  "overall_ai_confidence": <number 0-100 (average of all agent confidences)>,\n'
            '  "extracted_info": <applicant_profile object from Data Extraction Specialist>,\n'
            '  "verification_details": <full JSON from Loan Verification Officer>,\n'
            '  "fraud_analysis": <gov verification JSON from Government Verification Agent>,\n'
            '  "agent_findings": {\n'
            '    "document_analyst": <document analysis JSON>,\n'
            '    "extraction_specialist": <extraction JSON>,\n'
            '    "verification_officer": <verification JSON>,\n'
            '    "gov_verification_agent": <gov verification JSON>\n'
            '  },\n'
            '  "recommendation": "<Final recommendation text>",\n'
            '  "human_review": "<What a human officer should focus on, if any>",\n'
            '  "summary": "<3-5 sentence plain-English summary for the loan officer>",\n'
            '  "pdf_path": null\n'
            "}"
        ),
        expected_output=(
            "A single JSON object with application_id, final_status, verification_score, "
            "risk_score, fraud_flag, overall_ai_confidence, extracted_info, verification_details, fraud_analysis, "
            "agent_findings, recommendation, human_review, summary, and pdf_path."
        ),
        agent=agents["compliance_reporter"],
        context=[analyse_documents, extract_profile, verify_loan, verify_gov],
        output_json=ComplianceReportOutput,
    )

    return [
        analyse_documents,
        extract_profile,
        verify_loan,
        verify_gov,
        compile_report,
    ]
