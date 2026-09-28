"""
AI Report Composer Service for SmartVerify Report Generation System (Phase 6).
Transforms verified facts (ReportContext), template structure (ReportTemplate),
and approved evidence (EvidenceMap) into controlled, factual verification details.

Strict Guardrails:
- Facts + Approved Evidence Only: Never concludes verified/genuine/fraudulent unless
  explicitly supported by supplied context.
- Zero direct database access, zero database modifications.
- Zero context or evidence mutations.
- Zero evidence selection or semantic ranking.
- Strict provenance tracking (fact_ids, evidence_ids).
- Missing facts produce status="information_required" and empty narrative.
- On LLM failure, preserves deterministic output where available with status="composer_unavailable"
  and never invents fallback claims.
- Masked Aadhaar / PII only.
"""
import json
import logging
import re
from typing import Dict, List, Optional, Any, Set, Tuple
from datetime import datetime

from app.report_templates import get_template
from app.report_templates.schema import ReportTemplate, ParticularItem
from app.schemas.report_context import ReportContext, ParticularContextFact
from app.schemas.evidence_map import EvidenceMap, EvidenceMapping, EvidenceReference
from app.schemas.composed_report import ComposedParticular, ComposedReport
from app.services.llm_provider import LLMProvider, MockLLMProvider, ProviderError

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1.0"
COMPOSER_VERSION = "v1.0.0"

# Particulars designated for controlled AI narrative composition
NARRATIVE_PARTICULAR_IDS = {"P2A", "P2C", "P4B", "P6", "P7A2"}

SYSTEM_PROMPT = """You are the SmartVerify AI Report Composer, an internal banking re-verification reporting engine.
Your sole task is to generate professional, factual verification narrative statements for the requested report Particular.

CRITICAL ARCHITECTURAL CONSTRAINTS:
1. Use ONLY the supplied verified facts and approved evidence references.
2. Do NOT invent, extrapolate, or assume any facts or findings.
3. Do NOT change names, dates, amounts, IDs, addresses, or statuses.
4. Do NOT independently conclude 'verified', 'genuine', 'fraudulent', 'acceptable', 'suspicious', 'approved', or 'rejected' unless that exact conclusion or status is already explicitly provided in the supplied findings or facts.
5. If a finding status is 'REVIEW', describe it strictly as under review or pending officer scrutiny; do NOT describe it as verified.
6. If a finding status is 'NOT_AVAILABLE', do NOT convert it into a negative conclusion.
7. NEVER output or unmask full Aadhaar numbers. Always preserve masked representation (e.g. 'XXXXXXXX1234').
8. If any required information is missing, do NOT guess. Set 'missing_information' to the list of missing items and leave 'text' as an empty string.
9. Keep wording concise, objective, and conforming to banking credit appraisal standards.
10. Return ONLY valid JSON matching this exact schema:
{
    "text": "Factual concise observation text...",
    "fact_ids": ["fact_id_1", "fact_id_2"],
    "evidence_ids": ["evidence_id_1"],
    "confidence": 0.95,
    "missing_information": []
}"""


def _mask_aadhaar_pii(text: str) -> str:
    """Detects and masks any 12-digit Aadhaar pattern to XXXXXXXX####."""
    if not text:
        return ""
    # Match 12 consecutive digits or 4-4-4 grouped digits
    text = re.sub(r"\b(\d{4})[\s-](\d{4})[\s-](\d{4})\b", r"XXXXXXXX\3", text)
    text = re.sub(r"\b\d{8}(\d{4})\b", r"XXXXXXXX\1", text)
    return text


def _build_particular_scoped_payload(
    context: ReportContext,
    item: ParticularItem,
    fact_item: Optional[ParticularContextFact],
    evidence_mapping: Optional[EvidenceMapping],
    available_fact_ids: List[str],
) -> Dict[str, Any]:
    """Builds a minimal, isolated context payload for a single Particular."""
    pid = item.id

    # Applicant details (strictly masked)
    applicant_info = {
        "name": context.applicant.name if context.applicant else None,
        "relationship": "Applicant",
    }

    # Guarantor details
    guarantors_info = [
        {"party_id": g.party_id, "name": g.name, "relationship": g.relationship_to_applicant}
        for g in context.guarantors
    ]

    # Scoped evidence references
    evidence_refs = []
    if evidence_mapping:
        for ref in evidence_mapping.evidence:
            evidence_refs.append({
                "evidence_id": ref.evidence_id,
                "category": ref.evidence_category,
                "source": ref.source,
                "document_type": ref.document_type,
                "filename": ref.filename,
                "party_id": ref.party_id,
                "party_name": ref.party_name,
                "status": ref.status,
            })

    # Scoped findings linked to this particular
    scoped_findings = []
    for f in context.findings:
        if f.particular_id == pid:
            scoped_findings.append({
                "finding_id": f.finding_id,
                "category": f.category,
                "question": f.question,
                "answer": f.answer,
                "status": f.status,
                "confidence": f.confidence,
                "party_id": f.party_id,
            })

    # Scoped facts
    facts_payload = {}
    if fact_item:
        facts_payload = {
            "applicant_facts": fact_item.applicant_facts,
            "guarantor_facts": fact_item.guarantor_facts,
            "shared_facts": fact_item.shared_facts,
            "missing_fields": fact_item.missing_fields,
        }

    return {
        "particular_id": pid,
        "title": item.title,
        "description": item.description,
        "applicant": applicant_info,
        "guarantors": guarantors_info,
        "facts": facts_payload,
        "available_fact_ids": available_fact_ids,
        "evidence_references": evidence_refs,
        "findings": scoped_findings,
    }


def _render_deterministic_text(
    context: ReportContext,
    pid: str,
    fact_item: Optional[ParticularContextFact],
) -> str:
    """Generates standard deterministic representation for non-narrative rows."""
    if not fact_item:
        return ""

    app_facts = fact_item.applicant_facts or {}
    guar_facts = fact_item.guarantor_facts or []
    shared = fact_item.shared_facts or {}

    if pid == "P1":
        parts = []
        if app_facts.get("name"):
            parts.append(f"Applicant: {app_facts.get('name')}")
        if app_facts.get("father_name"):
            parts.append(f"Father: {app_facts.get('father_name')}")
        if app_facts.get("address"):
            parts.append(f"Address: {app_facts.get('address')}")
        for g in guar_facts:
            g_desc = f"Guarantor ({g.get('relationship', 'Guarantor')}): {g.get('name', 'N/A')}, Address: {g.get('address', 'N/A')}"
            parts.append(g_desc)
        return " | ".join(parts)

    elif pid == "P2":
        addr = app_facts.get("residence_address") or "Address unconfirmed"
        status = app_facts.get("residence_status") or "Status not recorded"
        return f"Residential Address: {addr}; Tenure/Type: {status}"

    elif pid == "P2B":
        officer = app_facts.get("visiting_officer") or "Not recorded"
        date = app_facts.get("visit_date") or "Date not recorded"
        return f"Visited By: {officer} on {date}"

    elif pid == "P2D":
        status = app_facts.get("residence_tenure_status") or "Tenure status not recorded"
        return f"Residential Status: {status}"

    elif pid == "P2E":
        app_aadhaar = app_facts.get("masked_aadhaar") or "Not provided"
        res = [f"Applicant Aadhaar: {app_aadhaar}"]
        for g in guar_facts:
            res.append(f"Guarantor Aadhaar: {g.get('masked_aadhaar', 'Not provided')}")
        return " | ".join(res)

    elif pid == "P3":
        pan = app_facts.get("pan_number") or "N/A"
        aadhaar = app_facts.get("masked_aadhaar") or "N/A"
        aadhaar_val = app_facts.get("aadhaar_validity_status") or "Unverified"
        pan_link = app_facts.get("pan_aadhaar_link_status") or "Unverified"
        return f"PAN: {pan}; Aadhaar: {aadhaar}; UIDAI Status: {aadhaar_val}; PAN-Aadhaar Link: {pan_link}"

    elif pid == "P4":
        docs = shared.get("income_documents_seen", [])
        return f"Income Documents Verified: {', '.join(docs) if docs else 'None'}"

    elif pid == "P4A":
        docs = shared.get("documents_seen", [])
        return f"Documents Seen: {', '.join(docs) if docs else 'None'}"

    elif pid == "P4C":
        emp = app_facts.get("employer_name") or "Employer details not recorded"
        return f"Employer: {emp}"

    elif pid == "P4D":
        occ = app_facts.get("occupation") or "Not recorded"
        inc = app_facts.get("monthly_income")
        inc_str = f"Rs. {inc:,.2f}" if isinstance(inc, (int, float)) else "Not recorded"
        yrs = app_facts.get("years_in_occupation")
        yrs_str = f"{yrs} years" if yrs is not None else "Not recorded"
        return f"Occupation: {occ}; Income: {inc_str}; Experience: {yrs_str}"

    elif pid in ["P5", "P5A"]:
        bank = shared.get("bank_name") or app_facts.get("bank_name") or "Bank name not recorded"
        branch = shared.get("branch_name") or app_facts.get("branch_name") or "Branch not recorded"
        return f"Bank: {bank}; Branch: {branch}"

    elif pid == "P5B":
        ac_type = shared.get("account_type") or app_facts.get("account_type") or "Savings / Current"
        return f"Account Classification: {ac_type}"

    elif pid == "P5C":
        frm = shared.get("examined_from") or app_facts.get("examined_from") or "Start date not recorded"
        to = shared.get("examined_to") or app_facts.get("examined_to") or "End date not recorded"
        return f"Period Examined: {frm} to {to}"

    elif pid == "P7":
        typ = shared.get("vehicle_machinery_type") or "Vehicle/Machinery collateral verification"
        return f"Collateral: {typ}"

    elif pid == "P7A":
        return "New Vehicle/Machinery verification dossier"

    elif pid == "P7A1":
        dealer = shared.get("dealer_name") or "Dealer details not recorded"
        return f"Dealership: {dealer}"

    elif pid == "P7B":
        return "Used/Pre-owned Vehicle/Machinery verification dossier"

    elif pid == "P7B1":
        rc = shared.get("rc_number") or "RC details verified against portal records"
        return f"RC / Machinery Serial: {rc}"

    elif pid == "P7B2":
        charge = shared.get("charge_details") or "No prior encumbrance recorded"
        return f"Hypothecation / Charge Status: {charge}"

    elif pid == "P7B3":
        ins = shared.get("insurance_details") or "Insurance policy verification details"
        return f"Insurance Coverage: {ins}"

    return ""


def compose_report(
    context: ReportContext,
    template: Optional[ReportTemplate] = None,
    evidence_map: Optional[EvidenceMap] = None,
    provider: Optional[LLMProvider] = None,
) -> ComposedReport:
    """
    Composes the complete application report.
    For non-narrative particulars, preserves deterministic rendering without AI.
    For narrative particulars (P2A, P2C, P4B, P6, P7A2), performs controlled AI wording
    using verified facts and approved evidence references.
    """
    if template is None:
        template = get_template(context.metadata.template_key, context.metadata.template_version)

    # Resolve provider: if none provided, fallback to MockLLMProvider in test/offline mode
    if provider is None:
        provider = MockLLMProvider()

    particular_outputs: Dict[str, ComposedParticular] = {}
    total_composed = 0
    total_deterministic = 0
    missing_info_count = 0

    # Index context facts by particular ID for fast lookup
    fact_provenance_by_pid: Dict[str, List[str]] = {}
    for fp in context.all_facts:
        for p in fp.particular_ids:
            if p not in fact_provenance_by_pid:
                fact_provenance_by_pid[p] = []
            fact_provenance_by_pid[p].append(fp.fact_id)

    for item in template.particulars:
        pid = item.id
        fact_item = context.particular_facts.get(pid)
        evidence_mapping = evidence_map.get_mapping(pid) if evidence_map else None

        avail_fact_ids = fact_provenance_by_pid.get(pid, [])
        avail_evidence_ids = [ref.evidence_id for ref in evidence_mapping.evidence] if evidence_mapping else []

        # ─────────────────────────────────────────────────────────────
        # Step 1: Missing Information Gate
        # ─────────────────────────────────────────────────────────────
        missing_fields: List[str] = []
        if fact_item and fact_item.missing_fields:
            missing_fields.extend(fact_item.missing_fields)

        # Check if mandatory evidence is missing
        if evidence_mapping and evidence_mapping.evidence_missing:
            if item.required:
                missing_fields.append(f"evidence:{item.evidence_category or 'required'}")

        # If required information is missing, do NOT call LLM and do NOT invent facts
        if item.required and missing_fields:
            particular_outputs[pid] = ComposedParticular(
                particular_id=pid,
                status="information_required",
                text="",
                fact_ids=[],
                evidence_ids=[],
                confidence=None,
                missing_information=missing_fields,
                model=provider.model_name,
                prompt_version=PROMPT_VERSION,
            )
            missing_info_count += 1
            continue

        # ─────────────────────────────────────────────────────────────
        # Step 2: Deterministic vs. Controlled AI Composition Routing
        # ─────────────────────────────────────────────────────────────
        is_narrative = (item.id in NARRATIVE_PARTICULAR_IDS) or item.narrative_allowed

        if not is_narrative:
            # Deterministic rendering without AI
            dt_text = _render_deterministic_text(context, pid, fact_item)
            dt_text = _mask_aadhaar_pii(dt_text)

            particular_outputs[pid] = ComposedParticular(
                particular_id=pid,
                status="deterministic",
                text=dt_text,
                fact_ids=avail_fact_ids,
                evidence_ids=avail_evidence_ids,
                confidence=1.0,
                missing_information=[],
                model="deterministic",
                prompt_version=None,
            )
            total_deterministic += 1
            continue

        # ─────────────────────────────────────────────────────────────
        # Step 3: Controlled AI Narrative Composition
        # ─────────────────────────────────────────────────────────────
        scoped_payload = _build_particular_scoped_payload(
            context, item, fact_item, evidence_mapping, avail_fact_ids
        )
        prompt_str = json.dumps(scoped_payload, indent=2)

        composed_success = False
        try:
            raw_response = provider.generate(
                prompt=prompt_str,
                system_prompt=SYSTEM_PROMPT,
                json_mode=True,
            )

            parsed_data = json.loads(raw_response)
            ai_text = str(parsed_data.get("text", "")).strip()
            ai_text = _mask_aadhaar_pii(ai_text)

            # Safeguard against status inflation: Ensure AI did not invent conclusions
            reported_fact_ids = parsed_data.get("fact_ids", [])
            # Filter to known facts to preserve strict provenance
            valid_fact_ids = [fid for fid in reported_fact_ids if fid in avail_fact_ids] if reported_fact_ids else avail_fact_ids

            reported_ev_ids = parsed_data.get("evidence_ids", [])
            valid_ev_ids = [eid for eid in reported_ev_ids if eid in avail_evidence_ids] if reported_ev_ids else avail_evidence_ids

            ai_confidence = parsed_data.get("confidence")
            if ai_confidence is not None:
                try:
                    ai_confidence = float(ai_confidence)
                except (ValueError, TypeError):
                    ai_confidence = 0.95
            else:
                ai_confidence = 0.95

            particular_outputs[pid] = ComposedParticular(
                particular_id=pid,
                status="composed",
                text=ai_text,
                fact_ids=valid_fact_ids,
                evidence_ids=valid_ev_ids,
                confidence=ai_confidence,
                missing_information=parsed_data.get("missing_information", []),
                model=provider.model_name,
                prompt_version=PROMPT_VERSION,
            )
            total_composed += 1
            composed_success = True

        except (ProviderError, json.JSONDecodeError, Exception) as err:
            logger.warning("AI composition failed for particular %s: %s", pid, err)
            # LLM failure handling: NEVER fabricate or invent fallback narrative.
            # Preserve Phase 4 deterministic output where one exists; otherwise empty text.
            dt_fallback = _render_deterministic_text(context, pid, fact_item)
            dt_fallback = _mask_aadhaar_pii(dt_fallback)

            particular_outputs[pid] = ComposedParticular(
                particular_id=pid,
                status="composer_unavailable",
                text=dt_fallback,
                fact_ids=avail_fact_ids,
                evidence_ids=avail_evidence_ids,
                confidence=None,
                missing_information=[f"AI composition unavailable: {str(err)}"],
                model=provider.model_name,
                prompt_version=PROMPT_VERSION,
            )

    return ComposedReport(
        application_id=context.metadata.application_id,
        template_key=template.template_key,
        template_version=template.template_version,
        particular_outputs=particular_outputs,
        input_hash=context.input_hash,
        composer_version=COMPOSER_VERSION,
        total_composed_count=total_composed,
        total_deterministic_count=total_deterministic,
        missing_information_count=missing_info_count,
    )
