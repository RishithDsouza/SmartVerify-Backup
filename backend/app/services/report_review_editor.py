"""
Report Review and Editor Service for SmartVerify (Phase 8).
Manages in-memory officer review sessions, controlled manual editing of
Verification Details, comprehensive audit logging, evidence inclusion preferences,
and deterministic revalidation gating.

Strict Guardrails:
- ZERO LLM/Gemini calls: Opening, editing, saving, or revalidating never calls Gemini.
- ZERO database queries or mutations: In-memory review state only.
- ZERO filesystem mutations: No evidence files copied, moved, or deleted.
- ZERO PDF generation: Gated via `can_export_pdf` boolean only.
- ZERO evidence reassignment across Particulars.
- Strict applicant vs guarantor party isolation.
- Complete immutability of original AI text, deterministic baseline text, and provenance.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime
import copy

from app.report_templates.schema import ReportTemplate
from app.schemas.report_context import ReportContext
from app.schemas.baseline_report import BaselineReport
from app.schemas.evidence_map import EvidenceMap, EvidenceMapping, EvidenceReference
from app.schemas.composed_report import ComposedReport, ComposedParticular
from app.schemas.validated_report import ValidatedReport, ValidationSeverity, ValidationIssue
from app.schemas.report_review import (
    ReportReviewState,
    ReportEdit,
    ParticularReview,
    ReviewEvidenceItem,
    EvidenceSelection,
    ParticularEditRequest,
)
from app.services.report_validator import validate_report


def create_review_state(
    baseline_report: BaselineReport,
    evidence_map: EvidenceMap,
    composed_report: ComposedReport,
    validated_report: ValidatedReport,
    template: ReportTemplate,
) -> ReportReviewState:
    """
    Initializes a new in-memory ReportReviewState from Phase 2–7 artifacts.
    Preserves original deterministic and AI wording, sets up evidence panels,
    and performs the initial export-gate check.
    """
    base_app_id = getattr(baseline_report, "application_id", None) or baseline_report.header.application_id
    if base_app_id != composed_report.application_id:
        raise ValueError(
            f"Application ID mismatch: Baseline ({base_app_id}) != Composed ({composed_report.application_id})"
        )

    application_id = base_app_id
    template_key = template.template_key
    template_version = template.template_version
    input_hash = composed_report.input_hash

    SECTION_TITLES = {
        "1": "Borrower & Guarantor Identification",
        "2": "Residence Verification",
        "3": "KYC & Identity Scrutiny",
        "4": "Income & Employment Verification",
        "5": "Banking & Account Scrutiny",
        "6": "Antecedents & General Track Record",
        "7": "Collateral & Asset Verification",
        "8": "Final Verification Opinion & Recommendation",
    }

    particular_reviews: Dict[str, ParticularReview] = {}
    evidence_selections: Dict[str, List[EvidenceSelection]] = {}

    for p in template.particulars:
        pid = p.id
        sec_id = p.section
        sec_title = SECTION_TITLES.get(p.section, f"Section {p.section}")
        p_title = p.title

        # Baseline text
        base_p = baseline_report.get_particular(pid)
        lines = []
        if base_p:
            if base_p.applicant_verification and base_p.applicant_verification.summary_lines:
                lines.extend(base_p.applicant_verification.summary_lines)
            if base_p.shared_verification and base_p.shared_verification.summary_lines:
                lines.extend(base_p.shared_verification.summary_lines)
            for g in base_p.guarantor_verifications:
                if g.summary_lines:
                    lines.extend(g.summary_lines)
        deterministic_text = " \n".join(lines) if lines else (base_p.missing_marker if (base_p and base_p.missing_marker) else "")

        # Composed text
        comp_part = composed_report.get_particular(pid)
        ai_text = comp_part.text if comp_part else ""
        current_text = ai_text if ai_text else deterministic_text

        # Provenance
        fact_ids = list(comp_part.fact_ids) if comp_part else []
        missing_info = list(comp_part.missing_information) if comp_part else []
        ai_composed = comp_part.status == "composed" if comp_part else False
        model_id = (comp_part.model if comp_part else None) or ("gemini-2.5-flash" if ai_composed else None)
        prompt_version = (comp_part.prompt_version if comp_part else None) or ("v1.0" if ai_composed else None)

        # Evidence mapping from Phase 5
        ev_mapping = evidence_map.get_mapping(pid)
        ev_ids: List[str] = []
        ev_items: List[ReviewEvidenceItem] = []
        selections: List[EvidenceSelection] = []

        if ev_mapping:
            ev_ids = [ref.evidence_id for ref in ev_mapping.evidence]
            for idx, ref in enumerate(ev_mapping.evidence):
                party_label = (
                    "APPLICANT"
                    if ref.party_id == "applicant"
                    else (
                        "GUARANTOR"
                        if (ref.party_id and "guarantor" in str(ref.party_id).lower())
                        else "SHARED"
                    )
                )
                source_label = ref.filename or ref.document_type or ref.source or ref.evidence_id
                item = ReviewEvidenceItem(
                    evidence_id=ref.evidence_id,
                    evidence_type=ref.evidence_type,
                    source=source_label,
                    filename=ref.filename,
                    party=party_label,
                    verification_status=ref.status or "VERIFIED",
                    metadata=dict(ref.metadata),
                    included=True,
                    display_order=idx,
                )
                ev_items.append(item)
                selections.append(
                    EvidenceSelection(
                        particular_id=pid,
                        evidence_id=ref.evidence_id,
                        included=True,
                        display_order=idx,
                    )
                )

        evidence_selections[pid] = selections

        # Validation from Phase 7
        val_res = validated_report.get_result(pid)
        val_status = val_res.status if val_res else "VALID"
        val_issues = list(val_res.issues) if val_res else []

        particular_reviews[pid] = ParticularReview(
            particular_id=pid,
            title=p_title,
            section_id=sec_id,
            section_title=sec_title,
            deterministic_text=deterministic_text,
            original_ai_text=ai_text,
            current_text=current_text,
            is_edited=False,
            edit_count=0,
            fact_ids=fact_ids,
            evidence_ids=ev_ids,
            evidence_items=ev_items,
            missing_information=missing_info,
            validation_status=val_status,
            issues=val_issues,
            ai_composed=ai_composed,
            model_id=model_id,
            prompt_version=prompt_version,
        )

    # Initial export gate check
    can_export = (
        validated_report.is_valid
        and len(validated_report.blocking_issues) == 0
        and len(particular_reviews) == len(template.particulars)
        and composed_report.input_hash == validated_report.input_hash
    )

    return ReportReviewState(
        application_id=application_id,
        template_key=template_key,
        template_version=template_version,
        input_hash=input_hash,
        review_status="PENDING_REVIEW",
        validation_status=validated_report.overall_status,
        revision=1,
        particular_reviews=particular_reviews,
        edited_particular_ids=[],
        evidence_selections=evidence_selections,
        edit_history=[],
        blocking_issues=list(validated_report.blocking_issues),
        warnings=list(validated_report.warnings),
        can_export_pdf=can_export,
    )


def apply_particular_edit(
    state: ReportReviewState,
    context: ReportContext,
    evidence_map: EvidenceMap,
    template: ReportTemplate,
    edit_req: ParticularEditRequest,
) -> ReportReviewState:
    """
    Applies a manual textual edit to a report Particular.
    - Preserves immutable provenance (fact IDs, evidence IDs, input hash, application ID).
    - Preserves original AI text and deterministic baseline text.
    - Records an audit log entry in edit_history.
    - Increments the revision counter.
    - Automatically executes Phase 7 revalidation on the current edited state.
    - Updates can_export_pdf according to the new validation result.
    """
    if edit_req.particular_id not in state.particular_reviews:
        raise ValueError(f"Particular '{edit_req.particular_id}' does not exist in review state.")

    prev_review = state.particular_reviews[edit_req.particular_id]
    previous_text = prev_review.current_text

    # Next revision number
    next_revision = state.revision + 1

    # Record edit in audit history
    edit_record = ReportEdit(
        particular_id=edit_req.particular_id,
        previous_text=previous_text,
        new_text=edit_req.new_text,
        edited_by=edit_req.edited_by,
        edited_at=datetime.utcnow().isoformat(),
        reason=edit_req.reason,
        revision=next_revision,
    )
    state.edit_history.append(edit_record)
    state.revision = next_revision

    # Update particular
    prev_review.current_text = edit_req.new_text
    prev_review.is_edited = True
    prev_review.edit_count += 1

    if edit_req.particular_id not in state.edited_particular_ids:
        state.edited_particular_ids.append(edit_req.particular_id)

    state.updated_at = datetime.utcnow().isoformat()

    # Revalidate using CURRENT edited text
    return revalidate_review_state(state, context, evidence_map, template)


def apply_evidence_selection(
    state: ReportReviewState,
    particular_id: str,
    evidence_id: str,
    included: bool,
    display_order: Optional[int] = None,
) -> ReportReviewState:
    """
    Updates the reviewer's inclusion preference for a mapped evidence item.
    Guards:
    - Does NOT modify Phase 5 EvidenceMap.
    - Strictly forbids assigning evidence to another Particular.
    - Strictly preserves party ownership (Applicant vs Guarantor).
    - Does NOT copy, move, or delete files on disk.
    """
    if particular_id not in state.particular_reviews:
        raise ValueError(f"Particular '{particular_id}' does not exist in review state.")

    p_review = state.particular_reviews[particular_id]

    target_item = None
    for item in p_review.evidence_items:
        if item.evidence_id == evidence_id:
            target_item = item
            break

    if not target_item:
        raise ValueError(
            f"Evidence '{evidence_id}' does not belong to Particular '{particular_id}' and cannot be reassigned."
        )

    # Update inclusion and display order
    target_item.included = included
    if display_order is not None:
        target_item.display_order = display_order

    # Update selection preference list
    selections = state.evidence_selections.get(particular_id, [])
    found = False
    for sel in selections:
        if sel.evidence_id == evidence_id:
            sel.included = included
            if display_order is not None:
                sel.display_order = display_order
            found = True
            break
    if not found:
        selections.append(
            EvidenceSelection(
                particular_id=particular_id,
                evidence_id=evidence_id,
                included=included,
                display_order=target_item.display_order,
            )
        )
    state.evidence_selections[particular_id] = selections
    state.updated_at = datetime.utcnow().isoformat()

    return state


def revalidate_review_state(
    state: ReportReviewState,
    context: ReportContext,
    evidence_map: EvidenceMap,
    template: ReportTemplate,
) -> ReportReviewState:
    """
    Executes Phase 7 deterministic revalidation on the CURRENT review state.
    Constructs an updated in-memory ComposedReport with current texts and immutable provenance,
    runs Phase 7 validate_report(), and re-evaluates the PDF export gate.
    """
    composed_parts: Dict[str, ComposedParticular] = {}
    for pid, p_rev in state.particular_reviews.items():
        # Preserve original provenance strictly
        composed_parts[pid] = ComposedParticular(
            particular_id=pid,
            status="composed" if p_rev.ai_composed else "deterministic",
            text=p_rev.current_text,
            fact_ids=list(p_rev.fact_ids),
            evidence_ids=list(p_rev.evidence_ids),
            missing_information=list(p_rev.missing_information),
            model=p_rev.model_id or ("gemini-2.5-flash" if p_rev.ai_composed else None),
            prompt_version=p_rev.prompt_version or ("v1.0" if p_rev.ai_composed else None),
        )

    current_composed = ComposedReport(
        application_id=state.application_id,
        template_key=state.template_key,
        template_version=state.template_version,
        particular_outputs=composed_parts,
        input_hash=state.input_hash,
        composer_version="v1.0.0",
        total_composed_count=sum(1 for p in composed_parts.values() if p.status == "composed"),
        total_deterministic_count=sum(1 for p in composed_parts.values() if p.status == "deterministic"),
    )

    # Pure deterministic Phase 7 validation
    val_report = validate_report(
        context=context,
        template=template,
        evidence_map=evidence_map,
        composed_report=current_composed,
    )

    # Update state validation metrics
    state.validation_status = val_report.overall_status
    state.blocking_issues = list(val_report.blocking_issues)
    state.warnings = list(val_report.warnings)

    for pid, p_rev in state.particular_reviews.items():
        p_res = val_report.get_result(pid)
        if p_res:
            p_rev.validation_status = p_res.status
            p_rev.issues = list(p_res.issues)
        else:
            p_rev.validation_status = "VALID"
            p_rev.issues = []

    # Strict PDF export gate evaluation
    has_zero_blocking = len(state.blocking_issues) == 0
    has_all_particulars = len(state.particular_reviews) == len(template.particulars)
    hashes_match = state.input_hash == context.input_hash
    templates_match = (
        state.template_key == template.template_key
        and state.template_version == template.template_version
    )
    overall_valid = state.validation_status in ("VALID", "VALID_WITH_WARNINGS")

    state.can_export_pdf = bool(
        overall_valid and has_zero_blocking and has_all_particulars and hashes_match and templates_match
    )

    state.updated_at = datetime.utcnow().isoformat()
    return state
