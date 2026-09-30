"""
Deterministic Report Validator Service for SmartVerify Report Generation System (Phase 7).
Validates ComposedReport against ReportContext, ReportTemplate, and EvidenceMap.

Strict Guardrails:
- Purely deterministic in-memory validation: ZERO AI/LLM/Gemini calls, ZERO RAG.
- ZERO database access and ZERO database mutations.
- ZERO filesystem mutations and ZERO PDF generation.
- Complete input immutability.
- 17 distinct validation checks covering template coverage, fact provenance,
  evidence provenance, party isolation, status consistency, numeric ground-truth,
  entity names, dates, PII/Aadhaar masking, and cryptographic input hash verification.
"""
import re
from typing import Dict, List, Optional, Set, Any, Tuple
from datetime import datetime

from app.report_templates import get_template
from app.report_templates.schema import ReportTemplate, ParticularItem
from app.schemas.report_context import ReportContext, FactProvenance
from app.schemas.evidence_map import EvidenceMap, EvidenceMapping, EvidenceReference
from app.schemas.composed_report import ComposedReport, ComposedParticular
from app.schemas.validated_report import (
    ValidationSeverity,
    ValidationIssue,
    ValidationResult,
    ValidatedReport,
)

# Unmasked Aadhaar pattern: 12 consecutive digits or 4-4-4 grouped digits
UNMASKED_AADHAAR_REGEX = re.compile(r"\b(\d{12}|\d{4}[\s-]\d{4}[\s-]\d{4})\b")

# Meaningful currency/financial numeric patterns (e.g. ₹ 85,00,000, Rs. 1,85,000, Rs 185000, INR 500000)
CURRENCY_REGEX = re.compile(
    r"(?:₹|Rs\.?|INR)\s*([0-9]{1,3}(?:,[0-9]{2,3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)"
)

# Standard date pattern: YYYY-MM-DD or DD/MM/YYYY
DATE_REGEX = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})\b")


def _parse_currency_to_float(val_str: str) -> Optional[float]:
    """Parses a cleaned currency string into a float value."""
    try:
        clean = val_str.replace(",", "").strip()
        return float(clean)
    except (ValueError, TypeError):
        return None


def _extract_approved_entities(context: ReportContext) -> Set[str]:
    """
    Builds an explicit approved set of entity/party names from ReportContext.
    Only explicit person, institution, and organizational names are included.
    """
    approved = set()
    # Applicant & Father
    if context.applicant:
        if context.applicant.name:
            approved.add(context.applicant.name.strip().lower())
        if context.applicant.father_name:
            approved.add(context.applicant.father_name.strip().lower())

    # Guarantors
    for g in context.guarantors:
        if g.name:
            approved.add(g.name.strip().lower())
        if g.father_name:
            approved.add(g.father_name.strip().lower())

    # Officers
    if context.metadata.officer_name:
        approved.add(context.metadata.officer_name.strip().lower())
    if context.government_verification and context.government_verification.officer_name:
        approved.add(context.government_verification.officer_name.strip().lower())
    if context.site_verification and context.site_verification.get("officer_name"):
        approved.add(str(context.site_verification["officer_name"]).strip().lower())

    # Branch & Client
    if context.metadata.branch:
        approved.add(context.metadata.branch.strip().lower())

    # Employer & Banks & Dealers from facts
    for fp in context.all_facts:
        if isinstance(fp.value, str) and len(fp.value.strip()) > 2:
            val_lower = fp.value.strip().lower()
            if any(k in fp.fact_id for k in ["employer", "bank", "branch", "dealer", "name", "officer"]):
                approved.add(val_lower)

    # From structured particular_facts (e.g. employer_name, dealer_name, bank_name, branch_name)
    for p_fact in context.particular_facts.values():
        for sub_dict in [p_fact.applicant_facts, p_fact.shared_facts]:
            if isinstance(sub_dict, dict):
                for k, v in sub_dict.items():
                    if isinstance(v, str) and len(v.strip()) > 2:
                        if any(term in k for term in ["employer", "bank", "branch", "dealer", "name", "officer"]):
                            approved.add(v.strip().lower())
        for g_dict in p_fact.guarantor_facts:
            if isinstance(g_dict, dict):
                for k, v in g_dict.items():
                    if isinstance(v, str) and len(v.strip()) > 2:
                        if any(term in k for term in ["employer", "bank", "branch", "dealer", "name", "officer"]):
                            approved.add(v.strip().lower())

    return approved


def _extract_allowed_numbers_for_particular(
    context: ReportContext,
    pid: str,
    fact_provenance_map: Dict[str, FactProvenance],
    avail_fact_ids: List[str],
) -> Set[float]:
    """Collects all known factual numeric values relevant to a Particular."""
    allowed_numbers = set()

    # Application-level general amounts
    if context.metadata.loan_amount:
        allowed_numbers.add(float(context.metadata.loan_amount))

    # Numbers from scoped fact provenance
    for fid in avail_fact_ids:
        fp = fact_provenance_map.get(fid)
        if fp and fp.value is not None:
            if isinstance(fp.value, (int, float)):
                allowed_numbers.add(float(fp.value))
            elif isinstance(fp.value, str):
                parsed = _parse_currency_to_float(fp.value)
                if parsed is not None:
                    allowed_numbers.add(parsed)

    # Numbers from structured particular facts
    fact_item = context.particular_facts.get(pid)
    if fact_item:
        for sub_dict in [fact_item.applicant_facts, fact_item.shared_facts]:
            if isinstance(sub_dict, dict):
                for v in sub_dict.values():
                    if isinstance(v, (int, float)):
                        allowed_numbers.add(float(v))
                    elif isinstance(v, str):
                        parsed = _parse_currency_to_float(v)
                        if parsed is not None:
                            allowed_numbers.add(parsed)

        for g_dict in fact_item.guarantor_facts:
            if isinstance(g_dict, dict):
                for v in g_dict.values():
                    if isinstance(v, (int, float)):
                        allowed_numbers.add(float(v))

    return allowed_numbers


def _extract_allowed_dates_for_particular(
    context: ReportContext,
    pid: str,
) -> Set[str]:
    """Collects all known date strings relevant to the application."""
    allowed_dates = set()
    if context.metadata.report_date:
        allowed_dates.add(context.metadata.report_date.strip())
    if context.applicant and context.applicant.dob:
        allowed_dates.add(context.applicant.dob.strip())

    for g in context.guarantors:
        if g.dob:
            allowed_dates.add(g.dob.strip())

    fact_item = context.particular_facts.get(pid)
    if fact_item:
        for d in [fact_item.applicant_facts, fact_item.shared_facts]:
            if isinstance(d, dict):
                for k, v in d.items():
                    if "date" in k or "from" in k or "to" in k:
                        if isinstance(v, str):
                            allowed_dates.add(v.strip())

    return allowed_dates


def validate_report(
    context: ReportContext,
    template: Optional[ReportTemplate] = None,
    evidence_map: Optional[EvidenceMap] = None,
    composed_report: Optional[ComposedReport] = None,
) -> ValidatedReport:
    """
    Deterministically validates a ComposedReport against ReportContext,
    ReportTemplate, and EvidenceMap.
    Zero direct database queries, zero AI/LLM calls, and zero filesystem mutations.
    """
    if template is None:
        template = get_template(context.metadata.template_key, context.metadata.template_version)

    if composed_report is None:
        raise ValueError("composed_report is required for validation.")

    blocking_issues: List[ValidationIssue] = []
    warnings: List[ValidationIssue] = []
    validation_results: Dict[str, ValidationResult] = {}

    # ─────────────────────────────────────────────────────────────────
    # 1. Header, Application ID, Template, and Input Hash Alignment
    # ─────────────────────────────────────────────────────────────────
    if composed_report.application_id != context.metadata.application_id:
        issue = ValidationIssue(
            code="APPLICATION_ID_MISMATCH",
            severity=ValidationSeverity.BLOCKING,
            message=f"Application ID mismatch: ComposedReport ({composed_report.application_id}) != Context ({context.metadata.application_id})",
        )
        blocking_issues.append(issue)

    if composed_report.template_key != template.template_key or composed_report.template_version != template.template_version:
        issue = ValidationIssue(
            code="TEMPLATE_MISMATCH",
            severity=ValidationSeverity.BLOCKING,
            message=f"Template mismatch: ComposedReport ({composed_report.template_key} {composed_report.template_version}) != Template ({template.template_key} {template.template_version})",
        )
        blocking_issues.append(issue)

    if composed_report.input_hash != context.input_hash:
        issue = ValidationIssue(
            code="INPUT_HASH_MISMATCH",
            severity=ValidationSeverity.BLOCKING,
            message=f"Input hash mismatch: ComposedReport hash '{composed_report.input_hash}' does not match ReportContext hash '{context.input_hash}' (stale composition).",
        )
        blocking_issues.append(issue)

    # Index all facts by ID and by particular ID
    fact_provenance_map: Dict[str, FactProvenance] = {fp.fact_id: fp for fp in context.all_facts}
    facts_by_pid: Dict[str, List[str]] = {}
    for fp in context.all_facts:
        for p in fp.particular_ids:
            if p not in facts_by_pid:
                facts_by_pid[p] = []
            facts_by_pid[p].append(fp.fact_id)

    # Approved entities
    approved_entities = _extract_approved_entities(context)

    # Template particulars set
    template_pids = {item.id for item in template.particulars}
    composed_pids = set(composed_report.particular_outputs.keys())

    # ─────────────────────────────────────────────────────────────────
    # 2. Template Coverage & Particular Consistency
    # ─────────────────────────────────────────────────────────────────
    missing_pids = template_pids - composed_pids
    for m_pid in missing_pids:
        issue = ValidationIssue(
            code="PARTICULAR_MISSING",
            severity=ValidationSeverity.BLOCKING,
            particular_id=m_pid,
            message=f"Required template Particular '{m_pid}' is missing from ComposedReport.",
        )
        blocking_issues.append(issue)

    unknown_pids = composed_pids - template_pids
    for u_pid in unknown_pids:
        issue = ValidationIssue(
            code="PARTICULAR_UNKNOWN",
            severity=ValidationSeverity.BLOCKING,
            particular_id=u_pid,
            message=f"Unknown Particular '{u_pid}' in ComposedReport not defined in ReportTemplate.",
        )
        blocking_issues.append(issue)

    # ─────────────────────────────────────────────────────────────────
    # 3. Per-Particular Comprehensive Deterministic Checks
    # ─────────────────────────────────────────────────────────────────
    for item in template.particulars:
        pid = item.id
        part_issues: List[ValidationIssue] = []
        checked_facts: List[str] = []
        checked_evidences: List[str] = []

        comp = composed_report.particular_outputs.get(pid)
        if not comp:
            # Already flagged above as missing
            validation_results[pid] = ValidationResult(
                particular_id=pid,
                status="INVALID",
                issues=[ValidationIssue(
                    code="PARTICULAR_MISSING",
                    severity=ValidationSeverity.BLOCKING,
                    particular_id=pid,
                    message=f"Particular '{pid}' is absent from report output.",
                )],
                validated=False,
            )
            continue

        text = comp.text or ""
        status = comp.status
        checked_facts.extend(comp.fact_ids)
        checked_evidences.extend(comp.evidence_ids)

        allowed_facts_for_pid = set(facts_by_pid.get(pid, []))
        ev_mapping: Optional[EvidenceMapping] = evidence_map.get_mapping(pid) if evidence_map else None
        allowed_ev_ids = {ref.evidence_id: ref for ref in ev_mapping.evidence} if ev_mapping else {}

        # 3A. PII / Aadhaar Safety
        unmasked_match = UNMASKED_AADHAAR_REGEX.search(text)
        if unmasked_match:
            issue = ValidationIssue(
                code="PII_UNMASKED_AADHAAR",
                severity=ValidationSeverity.BLOCKING,
                particular_id=pid,
                message=f"Unmasked 12-digit Aadhaar number detected in text: '{unmasked_match.group(0)}'. Must be masked (XXXXXXXX####).",
            )
            part_issues.append(issue)

        # 3B. Missing Information Gate
        if status == "information_required":
            if text.strip() != "":
                issue = ValidationIssue(
                    code="INFORMATION_REQUIRED_WITH_TEXT",
                    severity=ValidationSeverity.BLOCKING,
                    particular_id=pid,
                    message=f"Particular '{pid}' marked 'information_required' must have empty text, but contains narrative.",
                )
                part_issues.append(issue)
            if not comp.missing_information:
                issue = ValidationIssue(
                    code="INFORMATION_REQUIRED_EMPTY_REASON",
                    severity=ValidationSeverity.BLOCKING,
                    particular_id=pid,
                    message=f"Particular '{pid}' marked 'information_required' has an empty missing_information list.",
                )
                part_issues.append(issue)

        # 3C. Composer Failure Handling
        elif status == "composer_unavailable":
            issue = ValidationIssue(
                code="COMPOSER_UNAVAILABLE",
                severity=ValidationSeverity.WARNING,
                particular_id=pid,
                message=f"AI Composer was unavailable for Particular '{pid}'. Preserved deterministic fallback.",
            )
            part_issues.append(issue)

        # 3D. Deterministic Row Verification
        elif status == "deterministic":
            if comp.model != "deterministic":
                issue = ValidationIssue(
                    code="DETERMINISTIC_MODEL_MISMATCH",
                    severity=ValidationSeverity.WARNING,
                    particular_id=pid,
                    message=f"Particular '{pid}' marked deterministic but reports model '{comp.model}'.",
                )
                part_issues.append(issue)

        # 3E. AI-Composed Row Requirement
        elif status == "composed":
            if not comp.model or not comp.prompt_version:
                issue = ValidationIssue(
                    code="AI_PROVENANCE_MISSING",
                    severity=ValidationSeverity.BLOCKING,
                    particular_id=pid,
                    message=f"AI-composed Particular '{pid}' is missing model metadata or prompt_version.",
                )
                part_issues.append(issue)

        # 3F. Fact Provenance Verification
        for fid in comp.fact_ids:
            if fid not in fact_provenance_map:
                issue = ValidationIssue(
                    code="PROVENANCE_FACT_UNKNOWN",
                    severity=ValidationSeverity.BLOCKING,
                    particular_id=pid,
                    message=f"Cited fact_id '{fid}' does not exist in ReportContext.all_facts.",
                    fact_ids=[fid],
                )
                part_issues.append(issue)
            elif fid not in allowed_facts_for_pid:
                issue = ValidationIssue(
                    code="PROVENANCE_FACT_UNRELATED",
                    severity=ValidationSeverity.BLOCKING,
                    particular_id=pid,
                    message=f"Cited fact_id '{fid}' exists but is not associated with Particular '{pid}'.",
                    fact_ids=[fid],
                )
                part_issues.append(issue)

        # 3G. Evidence Provenance & Scope Verification
        for eid in comp.evidence_ids:
            if eid not in allowed_ev_ids:
                # Check if it exists in any other particular mapping in EvidenceMap
                found_elsewhere = False
                if evidence_map:
                    for other_pid, other_m in evidence_map.particular_mappings.items():
                        if other_pid != pid and any(r.evidence_id == eid for r in other_m.evidence):
                            found_elsewhere = True
                            issue = ValidationIssue(
                                code="PROVENANCE_EVIDENCE_WRONG_PARTICULAR",
                                severity=ValidationSeverity.BLOCKING,
                                particular_id=pid,
                                message=f"Cited evidence_id '{eid}' belongs to Particular '{other_pid}', not '{pid}'.",
                                evidence_ids=[eid],
                            )
                            part_issues.append(issue)
                            break
                if not found_elsewhere:
                    issue = ValidationIssue(
                        code="PROVENANCE_EVIDENCE_UNKNOWN",
                        severity=ValidationSeverity.BLOCKING,
                        particular_id=pid,
                        message=f"Cited evidence_id '{eid}' does not exist in EvidenceMap for Particular '{pid}'.",
                        evidence_ids=[eid],
                    )
                    part_issues.append(issue)

        # 3H. Party Isolation Verification
        for eid in comp.evidence_ids:
            ref = allowed_ev_ids.get(eid)
            if ref and ref.party_id:
                has_guarantor_statement = ("Guarantor:" in text) or ("Guarantor (" in text)
                has_applicant_statement = ("Applicant:" in text)

                # If text is strictly about applicant, guarantor evidence cannot be cited
                if has_applicant_statement and not has_guarantor_statement and ref.party_id != "applicant":
                    issue = ValidationIssue(
                        code="PARTY_CONTAMINATION_GUARANTOR_IN_APPLICANT",
                        severity=ValidationSeverity.BLOCKING,
                        particular_id=pid,
                        message=f"Guarantor evidence '{eid}' cited in applicant-specific statement.",
                        evidence_ids=[eid],
                    )
                    part_issues.append(issue)
                # Symmetrically: If text is strictly about guarantor, applicant evidence cannot be cited
                if has_guarantor_statement and not has_applicant_statement and ref.party_id == "applicant":
                    issue = ValidationIssue(
                        code="PARTY_CONTAMINATION_APPLICANT_IN_GUARANTOR",
                        severity=ValidationSeverity.BLOCKING,
                        particular_id=pid,
                        message=f"Applicant evidence '{eid}' cited in guarantor-specific statement.",
                        evidence_ids=[eid],
                    )
                    part_issues.append(issue)

        # 3I. Finding Status Consistency Verification
        scoped_findings = [f for f in context.findings if f.particular_id == pid]
        for f in scoped_findings:
            if f.status == "REVIEW":
                # If finding is REVIEW, narrative must NOT claim verified or confirmed
                if re.search(r"\b(is verified|are verified|confirmed as genuine|verification complete)\b", text, re.I):
                    issue = ValidationIssue(
                        code="STATUS_CONTRADICTION_REVIEW_CLAIMED_VERIFIED",
                        severity=ValidationSeverity.BLOCKING,
                        particular_id=pid,
                        message=f"Finding {f.finding_id} has status 'REVIEW', but generated text claims verified/confirmed.",
                    )
                    part_issues.append(issue)
            elif f.status == "NOT_AVAILABLE":
                # If finding is NOT_AVAILABLE, narrative must not claim negative result (e.g. rejected, fraudulent)
                if re.search(r"\b(rejected|fraudulent|failed verification|adverse finding)\b", text, re.I):
                    issue = ValidationIssue(
                        code="STATUS_CONTRADICTION_NOT_AVAILABLE_NEGATIVE",
                        severity=ValidationSeverity.BLOCKING,
                        particular_id=pid,
                        message=f"Finding {f.finding_id} has status 'NOT_AVAILABLE', but text infers negative/rejected conclusion.",
                    )
                    part_issues.append(issue)

        # 3J. Numeric Ground-Truth Consistency (Correction 1)
        allowed_nums = _extract_allowed_numbers_for_particular(
            context, pid, fact_provenance_map, list(allowed_facts_for_pid)
        )
        # Find meaningful factual currency expressions (e.g. ₹ 85,00,000 or Rs. 1,85,000)
        for cur_match in CURRENCY_REGEX.finditer(text):
            val_flt = _parse_currency_to_float(cur_match.group(1))
            if val_flt is not None:
                if val_flt not in allowed_nums:
                    issue = ValidationIssue(
                        code="NUMERIC_MISMATCH",
                        severity=ValidationSeverity.BLOCKING,
                        particular_id=pid,
                        message=f"Extracted currency claim '{cur_match.group(0)}' ({val_flt}) cannot be traced to any allowed fact for Particular '{pid}'.",
                    )
                    part_issues.append(issue)

        # 3K. Entity / Name Consistency (Correction 2)
        # Check explicitly introduced entity labels in text e.g. "Applicant: ...", "Guarantor: ...", "Dealer: ..."
        for ent_match in re.finditer(r"\b(?:Applicant|Guarantor|Employer|Dealer|Visited By):\s*([A-Za-z0-9\s,&.-]+?)(?:;|\.|$|\|)", text):
            ent_candidate = ent_match.group(1).strip()
            # If it's a substantive name phrase and not in approved set
            if len(ent_candidate) > 3 and not any(w in ent_candidate.lower() for w in ["not recorded", "none", "n/a", "unconfirmed", "pending"]):
                ent_lower = ent_candidate.lower()
                # Check if it matches any approved entity
                if not any(app_ent in ent_lower or ent_lower in app_ent for app_ent in approved_entities):
                    issue = ValidationIssue(
                        code="ENTITY_MISMATCH",
                        severity=ValidationSeverity.BLOCKING,
                        particular_id=pid,
                        message=f"Explicit entity '{ent_candidate}' introduced in '{pid}' does not match any approved entity in ReportContext.",
                    )
                    part_issues.append(issue)

        # 3L. Date Consistency
        allowed_dates = _extract_allowed_dates_for_particular(context, pid)
        for dt_match in DATE_REGEX.finditer(text):
            dt_str = dt_match.group(0).strip()
            if allowed_dates and dt_str not in allowed_dates:
                # Add WARNING rather than immediate BLOCKING to allow manual officer review if date is formatting variation
                issue = ValidationIssue(
                    code="DATE_UNVERIFIED",
                    severity=ValidationSeverity.WARNING,
                    particular_id=pid,
                    message=f"Date '{dt_str}' in '{pid}' requires manual officer review against primary records.",
                )
                part_issues.append(issue)

        # 3M. Evidence Missing Safety
        if ev_mapping and ev_mapping.evidence_missing:
            # Ensure text does not declare a negative/fraudulent result
            if re.search(r"\b(fraudulent|fake|rejected because of missing)\b", text, re.I):
                issue = ValidationIssue(
                    code="EVIDENCE_MISSING_FALSE_NEGATIVE",
                    severity=ValidationSeverity.BLOCKING,
                    particular_id=pid,
                    message=f"Particular '{pid}' has missing evidence, but text claims fraudulent/rejected conclusion.",
                )
                part_issues.append(issue)

        # Aggregate issues for this Particular
        has_blocking = any(i.severity == ValidationSeverity.BLOCKING for i in part_issues)
        has_warning = any(i.severity == ValidationSeverity.WARNING for i in part_issues)
        row_status = "INVALID" if has_blocking else ("WARNING" if has_warning else "VALID")

        for i in part_issues:
            if i.severity == ValidationSeverity.BLOCKING:
                blocking_issues.append(i)
            elif i.severity == ValidationSeverity.WARNING:
                warnings.append(i)

        validation_results[pid] = ValidationResult(
            particular_id=pid,
            status=row_status,
            issues=part_issues,
            validated=(not has_blocking),
            checked_fact_ids=checked_facts,
            checked_evidence_ids=checked_evidences,
        )

    # Determine overall status
    if len(blocking_issues) > 0:
        overall_status = "INVALID"
    elif len(warnings) > 0:
        overall_status = "VALID_WITH_WARNINGS"
    else:
        overall_status = "VALID"

    from app.schemas.fraud_remarks import DocumentFraudRemarksSection
    fraud_rem = getattr(context, "fraud_remarks", None) or DocumentFraudRemarksSection()

    return ValidatedReport(
        application_id=composed_report.application_id,
        template_key=composed_report.template_key,
        template_version=composed_report.template_version,
        input_hash=composed_report.input_hash,
        composer_version=composed_report.composer_version,
        overall_status=overall_status,
        validation_results=validation_results,
        blocking_issues=blocking_issues,
        warnings=warnings,
        fraud_remarks=fraud_rem,
    )
