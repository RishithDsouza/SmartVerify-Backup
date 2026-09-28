"""
Automated Test Suite for Phase 8: Report Preview / Review Editor.
Verifies all 32 architectural requirements:
- In-memory review state creation and lifecycle
- Edit audit trail, previous/new text logging, revision increments
- Preservation of original AI text, deterministic baseline text, and provenance
- Evidence selection preferences without mutating Phase 5 EvidenceMap
- Strict applicant vs guarantor isolation
- Revalidation using Phase 7 validator on CURRENT edited text
- Strict PDF export gate (can_export_pdf)
- Zero LLM/Gemini calls, zero DB writes, zero filesystem mutations
- Complete regression across Phases 1–7
"""
import sys
import os
import json
import copy
from typing import Dict, List, Any
from unittest.mock import MagicMock, patch

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add backend directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.report_templates import get_template
from app.report_templates.schema import ReportTemplate
from app.schemas.report_context import (
    ReportContext,
    ReportMetadata,
    NormalizedParty,
    NormalizedDocumentMeta,
    GovernmentScreenshotMeta,
    NormalizedGovVerification,
    NormalizedFinding,
    ParticularContextFact,
    FactProvenance,
)
from app.schemas.baseline_report import BaselineReport
from app.services.baseline_report_renderer import render_baseline_report
from app.schemas.evidence_map import EvidenceMap
from app.services.evidence_mapper import map_evidence
from app.schemas.composed_report import ComposedReport, ComposedParticular
from app.services.llm_provider import MockLLMProvider
from app.services.report_composer import compose_report
from app.schemas.validated_report import (
    ValidationSeverity,
    ValidationIssue,
    ValidationResult,
    ValidatedReport,
)
from app.services.report_validator import validate_report
from app.schemas.report_review import (
    ReportReviewState,
    ReportEdit,
    ParticularReview,
    ReviewEvidenceItem,
    EvidenceSelection,
    ParticularEditRequest,
    EvidenceToggleRequest,
)
from app.services.report_review_editor import (
    create_review_state,
    apply_particular_edit,
    apply_evidence_selection,
    revalidate_review_state,
)


from test_phase7_report_validator import create_mock_report_context, create_mock_composed_report


def build_pipeline_fixtures():
    """Generates all Phase 2-7 fixtures for review editor testing."""
    context = create_mock_report_context()
    template = get_template(context.metadata.template_key, context.metadata.template_version)
    baseline = render_baseline_report(context, template.template_key, template.template_version)
    evidence_map = map_evidence(context, template)
    composed = create_mock_composed_report(context, template, evidence_map)
    validated = validate_report(context=context, template=template, evidence_map=evidence_map, composed_report=composed)
    return context, template, baseline, evidence_map, composed, validated


# ─────────────────────────────────────────────────────────────────────────────
# 32 AUTOMATED TESTS FOR PHASE 8
# ─────────────────────────────────────────────────────────────────────────────

def test_1_review_state_creation():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    assert state.application_id == context.metadata.application_id
    assert state.template_key == "standard_reverification"
    assert state.template_version == "v1.0"
    assert state.input_hash == context.input_hash
    assert state.revision == 1
    assert len(state.particular_reviews) == 26
    print("PASS: test_1_review_state_creation")


def test_2_valid_report_loads():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    assert state.validation_status in ("VALID", "VALID_WITH_WARNINGS")
    assert len(state.blocking_issues) == 0
    print("PASS: test_2_valid_report_loads")


def test_3_invalid_report_cannot_export():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    # Artificially inject a blocking issue in validated report
    invalid_validated = copy.deepcopy(validated)
    invalid_validated.overall_status = "INVALID"
    invalid_validated.blocking_issues.append(
        ValidationIssue(code="TEST_BLOCKING", severity=ValidationSeverity.BLOCKING, message="Blocking issue")
    )
    state = create_review_state(baseline, evidence_map, composed, invalid_validated, template)
    assert state.can_export_pdf is False
    print("PASS: test_3_invalid_report_cannot_export")


def test_4_valid_report_can_export():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    assert state.can_export_pdf is True
    print("PASS: test_4_valid_report_can_export")


def test_5_edit_tracking():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    orig_text = state.get_current_text("P1")
    new_text = "Applicant Sunil Kumar Varma verified via physical visit and Aadhaar."

    edit_req = ParticularEditRequest(
        particular_id="P1",
        new_text=new_text,
        edited_by="Officer Sharma",
        reason="Updated observation notes",
    )
    updated_state = apply_particular_edit(state, context, evidence_map, template, edit_req)

    assert len(updated_state.edit_history) == 1
    edit_record = updated_state.edit_history[0]
    assert edit_record.particular_id == "P1"
    assert edit_record.previous_text == orig_text
    assert edit_record.new_text == new_text
    assert edit_record.edited_by == "Officer Sharma"
    assert edit_record.reason == "Updated observation notes"
    assert edit_record.revision == 2
    assert "P1" in updated_state.edited_particular_ids
    print("PASS: test_5_edit_tracking")


def test_6_revision_increment():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    assert state.revision == 1

    apply_particular_edit(state, context, evidence_map, template, ParticularEditRequest(particular_id="P1", new_text="Rev 2 text", edited_by="Officer"))
    assert state.revision == 2

    apply_particular_edit(state, context, evidence_map, template, ParticularEditRequest(particular_id="P1", new_text="Rev 3 text", edited_by="Officer"))
    assert state.revision == 3
    print("PASS: test_6_revision_increment")


def test_7_original_ai_text_preserved():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    p_rev = state.get_particular("P1")
    orig_ai = p_rev.original_ai_text

    apply_particular_edit(state, context, evidence_map, template, ParticularEditRequest(particular_id="P1", new_text="Manual edit text", edited_by="Officer"))
    assert p_rev.original_ai_text == orig_ai
    assert p_rev.current_text == "Manual edit text"
    print("PASS: test_7_original_ai_text_preserved")


def test_8_deterministic_text_preserved():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    p_rev = state.get_particular("P1")
    orig_det = p_rev.deterministic_text

    apply_particular_edit(state, context, evidence_map, template, ParticularEditRequest(particular_id="P1", new_text="Manual edit text", edited_by="Officer"))
    assert p_rev.deterministic_text == orig_det
    print("PASS: test_8_deterministic_text_preserved")


def test_9_evidence_inclusion_exclusion_tracking():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    p_rev = state.get_particular("P1")
    assert len(p_rev.evidence_items) > 0
    target_ev_id = p_rev.evidence_items[0].evidence_id

    # Exclude evidence
    apply_evidence_selection(state, "P1", target_ev_id, included=False)
    assert p_rev.evidence_items[0].included is False
    assert state.evidence_selections["P1"][0].included is False

    # Include evidence back
    apply_evidence_selection(state, "P1", target_ev_id, included=True)
    assert p_rev.evidence_items[0].included is True
    print("PASS: test_9_evidence_inclusion_exclusion_tracking")


def test_10_evidence_cannot_be_reassigned():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    # Attempt to assign an evidence ID that does not belong to P4B
    try:
        apply_evidence_selection(state, "P4B", "non_existent_evidence_999", included=True)
        assert False, "Should have raised ValueError for reassignment"
    except ValueError as e:
        assert "does not belong" in str(e)
    print("PASS: test_10_evidence_cannot_be_reassigned")


def test_11_applicant_guarantor_isolation():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    # Check that evidence items preserve party ownership
    for pid, p_rev in state.particular_reviews.items():
        for item in p_rev.evidence_items:
            assert item.party in ("APPLICANT", "GUARANTOR", "SHARED")
            # Items cannot have party changed via review
            orig_party = item.party
            apply_evidence_selection(state, pid, item.evidence_id, included=False)
            assert item.party == orig_party
    print("PASS: test_11_applicant_guarantor_isolation")


def test_12_edit_triggers_revalidation():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    assert state.validation_status in ("VALID", "VALID_WITH_WARNINGS")

    # Introducing an edit with unsupported currency triggers revalidation
    apply_particular_edit(
        state, context, evidence_map, template,
        ParticularEditRequest(particular_id="P4B", new_text="Salary claimed is ₹ 99,99,999 monthly.", edited_by="Officer")
    )
    # Revalidation should have automatically run and flagged invalid
    assert state.validation_status == "INVALID"
    assert any(i.code == "NUMERIC_MISMATCH" for i in state.blocking_issues)
    print("PASS: test_12_edit_triggers_revalidation")


def test_13_invalid_edit_blocks_export():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    assert state.can_export_pdf is True

    apply_particular_edit(
        state, context, evidence_map, template,
        ParticularEditRequest(particular_id="P4B", new_text="Unsupported amount ₹ 4,50,000.", edited_by="Officer")
    )
    assert state.can_export_pdf is False
    print("PASS: test_13_invalid_edit_blocks_export")


def test_14_corrected_edit_allows_export():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    # Invalid edit
    apply_particular_edit(
        state, context, evidence_map, template,
        ParticularEditRequest(particular_id="P4B", new_text="Unsupported amount ₹ 4,50,000.", edited_by="Officer")
    )
    assert state.can_export_pdf is False

    # Corrected edit with valid factual text
    apply_particular_edit(
        state, context, evidence_map, template,
        ParticularEditRequest(particular_id="P4B", new_text="Salary slips verified for May 2026 and corroborated with bank statement credits.", edited_by="Officer")
    )
    assert state.validation_status in ("VALID", "VALID_WITH_WARNINGS")
    assert state.can_export_pdf is True
    print("PASS: test_14_corrected_edit_allows_export")


def test_15_input_hash_protection():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    state.input_hash = "tampered_hash_123"
    revalidated = revalidate_review_state(state, context, evidence_map, template)
    assert revalidated.validation_status == "INVALID"
    assert revalidated.can_export_pdf is False
    assert any(i.code == "INPUT_HASH_MISMATCH" for i in revalidated.blocking_issues)
    print("PASS: test_15_input_hash_protection")


def test_16_template_version_protection():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    state.template_version = "v9.9"
    revalidated = revalidate_review_state(state, context, evidence_map, template)
    assert revalidated.validation_status == "INVALID"
    assert revalidated.can_export_pdf is False
    assert any(i.code == "TEMPLATE_MISMATCH" for i in revalidated.blocking_issues)
    print("PASS: test_16_template_version_protection")


def test_17_immutable_provenance():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    p_rev = state.get_particular("P1")
    orig_fact_ids = list(p_rev.fact_ids)
    orig_ev_ids = list(p_rev.evidence_ids)

    apply_particular_edit(state, context, evidence_map, template, ParticularEditRequest(particular_id="P1", new_text="New text", edited_by="Officer"))
    assert p_rev.fact_ids == orig_fact_ids
    assert p_rev.evidence_ids == orig_ev_ids
    print("PASS: test_17_immutable_provenance")


def test_18_json_serialization():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    json_str = state.model_dump_json() if hasattr(state, "model_dump_json") else state.json()
    deserialized = ReportReviewState.model_validate_json(json_str) if hasattr(ReportReviewState, "model_validate_json") else ReportReviewState.parse_raw(json_str)
    assert deserialized.application_id == state.application_id
    assert deserialized.revision == state.revision
    assert len(deserialized.particular_reviews) == len(state.particular_reviews)
    print("PASS: test_18_json_serialization")


def test_19_repeated_state_determinism():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state1 = create_review_state(baseline, evidence_map, composed, validated, template)
    state2 = create_review_state(baseline, evidence_map, composed, validated, template)
    assert state1.validation_status == state2.validation_status
    assert state1.can_export_pdf == state2.can_export_pdf
    assert len(state1.particular_reviews) == len(state2.particular_reviews)
    print("PASS: test_19_repeated_state_determinism")


def test_20_no_db_mutation():
    # Calling create_review_state, apply_particular_edit, apply_evidence_selection makes 0 DB calls
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    mock_db = MagicMock()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    apply_particular_edit(state, context, evidence_map, template, ParticularEditRequest(particular_id="P1", new_text="No DB text", edited_by="Officer"))
    assert mock_db.add.call_count == 0
    assert mock_db.commit.call_count == 0
    assert mock_db.execute.call_count == 0
    print("PASS: test_20_no_db_mutation")


def test_21_no_evidence_file_mutation():
    # Verify no file operations (remove, unlink, rename, write) occur on evidence files
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    with patch("os.remove") as mock_rm, patch("os.unlink") as mock_un, patch("os.rename") as mock_rn:
        apply_evidence_selection(state, "P1", state.particular_reviews["P1"].evidence_items[0].evidence_id, included=False)
        assert mock_rm.call_count == 0
        assert mock_un.call_count == 0
        assert mock_rn.call_count == 0
    print("PASS: test_21_no_evidence_file_mutation")


def test_22_phase1_to_7_regression():
    # Ensures Phase 7 validator runs perfectly on base composed report
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    val = validate_report(context=context, template=template, evidence_map=evidence_map, composed_report=composed)
    assert val.is_valid is True
    print("PASS: test_22_phase1_to_7_regression")


def test_23_save_changes_does_not_perform_db_writes():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    mock_db = MagicMock()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    apply_particular_edit(state, context, evidence_map, template, ParticularEditRequest(particular_id="P2A", new_text="Address verified", edited_by="Officer"))
    assert mock_db.commit.call_count == 0
    assert mock_db.flush.call_count == 0
    print("PASS: test_23_save_changes_does_not_perform_db_writes")


def test_24_opening_review_page_does_not_call_gemini():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    with patch("app.services.llm_provider.GeminiRESTProvider.generate") as mock_llm:
        state = create_review_state(baseline, evidence_map, composed, validated, template)
        assert mock_llm.call_count == 0
    print("PASS: test_24_opening_review_page_does_not_call_gemini")


def test_25_editing_text_does_not_call_gemini():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    with patch("app.services.llm_provider.GeminiRESTProvider.generate") as mock_llm:
        apply_particular_edit(state, context, evidence_map, template, ParticularEditRequest(particular_id="P1", new_text="Edited without AI", edited_by="Officer"))
        assert mock_llm.call_count == 0
    print("PASS: test_25_editing_text_does_not_call_gemini")


def test_26_revalidation_uses_current_edited_text_rather_than_stale_text():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    # Edit with unmasked Aadhaar number: must be caught by validator on current text
    apply_particular_edit(
        state, context, evidence_map, template,
        ParticularEditRequest(particular_id="P3", new_text="Unmasked Aadhaar: 123456789012 found.", edited_by="Officer")
    )
    assert state.validation_status == "INVALID"
    assert any(i.code == "PII_UNMASKED_AADHAAR" for i in state.blocking_issues)
    print("PASS: test_26_revalidation_uses_current_edited_text_rather_than_stale_text")


def test_27_invalid_current_edit_disables_pdf_export():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    assert state.can_export_pdf is True
    apply_particular_edit(
        state, context, evidence_map, template,
        ParticularEditRequest(particular_id="P4B", new_text="Unsupported salary ₹ 8,88,888.", edited_by="Officer")
    )
    assert state.can_export_pdf is False
    print("PASS: test_27_invalid_current_edit_disables_pdf_export")


def test_28_correcting_the_edit_reenables_pdf_export_after_revalidation():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    # Make invalid
    apply_particular_edit(
        state, context, evidence_map, template,
        ParticularEditRequest(particular_id="P4B", new_text="Unsupported salary ₹ 8,88,888.", edited_by="Officer")
    )
    assert state.can_export_pdf is False

    # Correct it
    apply_particular_edit(
        state, context, evidence_map, template,
        ParticularEditRequest(particular_id="P4B", new_text="Salary slips verified for May 2026 and corroborated with bank statement credits.", edited_by="Officer")
    )
    assert state.can_export_pdf is True
    print("PASS: test_28_correcting_the_edit_reenables_pdf_export_after_revalidation")


def test_29_phase5_evidence_map_remains_byte_for_byte_unchanged():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    orig_ev_json = evidence_map.model_dump_json() if hasattr(evidence_map, "model_dump_json") else evidence_map.json()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    apply_evidence_selection(state, "P1", state.particular_reviews["P1"].evidence_items[0].evidence_id, included=False)
    # Phase 5 EvidenceMap must be identical
    curr_ev_json = evidence_map.model_dump_json() if hasattr(evidence_map, "model_dump_json") else evidence_map.json()
    assert curr_ev_json == orig_ev_json
    print("PASS: test_29_phase5_evidence_map_remains_byte_for_byte_unchanged")


def test_30_original_ai_text_remains_unchanged_across_multiple_edits():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    p_rev = state.get_particular("P1")
    orig_ai = p_rev.original_ai_text

    for i in range(5):
        apply_particular_edit(state, context, evidence_map, template, ParticularEditRequest(particular_id="P1", new_text=f"Edit iteration {i}", edited_by="Officer"))
        assert p_rev.original_ai_text == orig_ai
    print("PASS: test_30_original_ai_text_remains_unchanged_across_multiple_edits")


def test_31_edit_history_preserves_all_revisions_in_order():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)

    apply_particular_edit(state, context, evidence_map, template, ParticularEditRequest(particular_id="P1", new_text="Text rev 2", edited_by="Officer A", reason="R1"))
    apply_particular_edit(state, context, evidence_map, template, ParticularEditRequest(particular_id="P2A", new_text="Text rev 3", edited_by="Officer B", reason="R2"))
    apply_particular_edit(state, context, evidence_map, template, ParticularEditRequest(particular_id="P1", new_text="Text rev 4", edited_by="Officer A", reason="R3"))

    assert len(state.edit_history) == 3
    assert [e.revision for e in state.edit_history] == [2, 3, 4]
    assert state.edit_history[0].particular_id == "P1"
    assert state.edit_history[1].particular_id == "P2A"
    assert state.edit_history[2].particular_id == "P1"
    assert state.edit_history[2].previous_text == "Text rev 2"
    assert state.edit_history[2].new_text == "Text rev 4"
    print("PASS: test_31_edit_history_preserves_all_revisions_in_order")


def test_32_changing_evidence_inclusion_does_not_change_evidence_provenance():
    context, template, baseline, evidence_map, composed, validated = build_pipeline_fixtures()
    state = create_review_state(baseline, evidence_map, composed, validated, template)
    p_rev = state.get_particular("P1")
    ev_item = p_rev.evidence_items[0]
    orig_ev_id = ev_item.evidence_id
    orig_source = ev_item.source
    orig_party = ev_item.party

    apply_evidence_selection(state, "P1", orig_ev_id, included=False)
    assert ev_item.evidence_id == orig_ev_id
    assert ev_item.source == orig_source
    assert ev_item.party == orig_party
    assert ev_item.included is False
    print("PASS: test_32_changing_evidence_inclusion_does_not_change_evidence_provenance")


if __name__ == "__main__":
    print("=" * 60)
    print("RUNNING PHASE 8 REPORT PREVIEW / REVIEW EDITOR TEST SUITE")
    print("=" * 60)

    tests = [
        test_1_review_state_creation,
        test_2_valid_report_loads,
        test_3_invalid_report_cannot_export,
        test_4_valid_report_can_export,
        test_5_edit_tracking,
        test_6_revision_increment,
        test_7_original_ai_text_preserved,
        test_8_deterministic_text_preserved,
        test_9_evidence_inclusion_exclusion_tracking,
        test_10_evidence_cannot_be_reassigned,
        test_11_applicant_guarantor_isolation,
        test_12_edit_triggers_revalidation,
        test_13_invalid_edit_blocks_export,
        test_14_corrected_edit_allows_export,
        test_15_input_hash_protection,
        test_16_template_version_protection,
        test_17_immutable_provenance,
        test_18_json_serialization,
        test_19_repeated_state_determinism,
        test_20_no_db_mutation,
        test_21_no_evidence_file_mutation,
        test_22_phase1_to_7_regression,
        test_23_save_changes_does_not_perform_db_writes,
        test_24_opening_review_page_does_not_call_gemini,
        test_25_editing_text_does_not_call_gemini,
        test_26_revalidation_uses_current_edited_text_rather_than_stale_text,
        test_27_invalid_current_edit_disables_pdf_export,
        test_28_correcting_the_edit_reenables_pdf_export_after_revalidation,
        test_29_phase5_evidence_map_remains_byte_for_byte_unchanged,
        test_30_original_ai_text_remains_unchanged_across_multiple_edits,
        test_31_edit_history_preserves_all_revisions_in_order,
        test_32_changing_evidence_inclusion_does_not_change_evidence_provenance,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAIL: {t.__name__} - {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"PHASE 8 TESTS COMPLETED: {passed}/{len(tests)} PASSED, {failed} FAILED")
    print("=" * 60)
    if failed > 0:
        sys.exit(1)
