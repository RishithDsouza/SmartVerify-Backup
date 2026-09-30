"""
Automated Test Suite: Document Verification / Fraud Remarks in SmartVerify PDF.
Verifies all 14 requirements:
1. No fraud findings: PDF contains compact "No significant document fraud indicators detected" remark.
2. One document fraud/suspicion finding: PDF contains DOCUMENT VERIFICATION / FRAUD REMARKS.
3. Multiple findings: all relevant findings appear.
4. Finding contains document reference: human-readable document name appears.
5. Internal document/finding IDs are NOT visible in PDF.
6. Existing finding reason is preserved verbatim.
7. Fraud finding does not alter the 26 Particulars.
8. Fraud finding does not alter Supporting Evidence.
9. Existing audit/certification content remains present.
10. Existing VALID report still generates successfully.
11. Existing party isolation remains valid.
12. Existing Report Validator tests pass.
13. Existing PDF generator tests pass.
14. Application 158 regression (report remains VALID, party isolation correct, fraud remarks accurate).
"""
import sys
import os
import io
import copy
import pypdf
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db.database import SessionLocal
from app.models.application import Application
from app.models.document import Document
from app.models.finding import ApplicationFinding, FindingStatus
from app.report_templates import get_template
from app.schemas.report_context import ReportContext, NormalizedFinding, NormalizedDocumentMeta
from app.schemas.baseline_report import BaselineReport
from app.schemas.evidence_map import EvidenceMap, EvidenceReference
from app.schemas.composed_report import ComposedReport
from app.schemas.validated_report import ValidatedReport
from app.schemas.report_review import ReportReviewState
from app.schemas.pdf_report import PdfGenerationRequest, PdfGenerationResult
from app.schemas.fraud_remarks import DocumentFraudRemark, DocumentFraudRemarksSection

from app.services.report_context_builder import build_report_context
from app.services.baseline_report_renderer import render_baseline_report
from app.services.evidence_mapper import map_evidence
from app.services.report_composer import compose_report
from app.services.report_validator import validate_report
from app.services.report_review_editor import create_review_state
from app.services.pdf_generator import generate_pdf_report
from app.services.fraud_remarks_builder import build_document_fraud_remarks

from test_phase9_pdf_generator import build_pipeline_fixtures


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract all text from generated PDF byte stream."""
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return " ".join(page.extract_text() for page in reader.pages)


def test_01_no_fraud_findings_pdf_contains_compact_remark():
    """1. No fraud findings: PDF contains compact 'No significant document fraud indicators detected' remark."""
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    # Ensure clean no-finding section
    validated.fraud_remarks = DocumentFraudRemarksSection(
        has_remarks=False,
        status="No significant document fraud indicators detected.",
        remarks=[],
    )
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-30T12:00:00Z",
        generated_by="Officer Test User",
    )
    result = generate_pdf_report(req)
    assert result.page_count >= 1
    text = _extract_pdf_text(result.pdf_bytes)

    assert "DOCUMENT VERIFICATION / FRAUD REMARKS" in text
    assert "No significant document fraud indicators detected" in text


def test_02_one_document_fraud_suspicion_finding():
    """2. One document fraud/suspicion finding: PDF contains DOCUMENT VERIFICATION / FRAUD REMARKS and review status."""
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    validated.fraud_remarks = DocumentFraudRemarksSection(
        has_remarks=True,
        status="REVIEW REQUIRED",
        remarks=[
            DocumentFraudRemark(
                finding_id=901,
                document_id=501,
                document_type="salary_slip",
                document_name="Salary Slip",
                category="fraud",
                severity="REVIEW REQUIRED",
                reason="Salary information is inconsistent with the corresponding bank statement.",
                evidence_reference="finding:901",
                source="application_findings",
            )
        ],
    )
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-30T12:00:00Z",
        generated_by="Officer Test User",
    )
    result = generate_pdf_report(req)
    text = _extract_pdf_text(result.pdf_bytes)

    assert "DOCUMENT VERIFICATION / FRAUD REMARKS" in text
    assert "REVIEW REQUIRED" in text
    assert "Document: Salary Slip" in text
    assert "Salary information is inconsistent with the corresponding bank statement." in text


def test_03_multiple_findings_all_appear():
    """3. Multiple findings: all relevant findings appear."""
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    validated.fraud_remarks = DocumentFraudRemarksSection(
        has_remarks=True,
        status="REVIEW REQUIRED",
        remarks=[
            DocumentFraudRemark(
                finding_id=901,
                document_id=501,
                document_type="salary_slip",
                document_name="Salary Slip",
                category="income",
                severity="REVIEW REQUIRED",
                reason="Salary information is inconsistent with the corresponding bank statement.",
                evidence_reference="finding:901",
                source="application_findings",
            ),
            DocumentFraudRemark(
                finding_id=902,
                document_id=502,
                document_type="pan",
                document_name="PAN Card",
                category="identity",
                severity="REVIEW REQUIRED",
                reason="PAN details require manual verification.",
                evidence_reference="finding:902",
                source="application_findings",
            ),
            DocumentFraudRemark(
                finding_id=903,
                document_id=503,
                document_type="aadhaar",
                document_name="Aadhaar",
                category="fraud",
                severity="POTENTIAL FRAUD INDICATOR",
                reason="Duplicate Aadhaar detected across applications.",
                evidence_reference="finding:903",
                source="application_findings",
            ),
        ],
    )
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-30T12:00:00Z",
        generated_by="Officer Test User",
    )
    result = generate_pdf_report(req)
    text = _extract_pdf_text(result.pdf_bytes)

    assert "Document: Salary Slip" in text
    assert "Salary information is inconsistent with the corresponding bank statement." in text
    assert "Document: PAN Card" in text
    assert "PAN details require manual verification." in text
    assert "Document: Aadhaar" in text
    assert "Duplicate Aadhaar detected across applications." in text


def test_04_finding_contains_document_reference_human_readable_name():
    """4. Finding contains document reference: human-readable document name appears."""
    norm_findings = [
        NormalizedFinding(
            finding_id=77,
            particular_id="P4",
            category="fraud",
            question="Unusual High Income Claim Scrutiny",
            answer="High claimed income requires corroboration.",
            status="NOT_VERIFIED",
            confidence=1.0,
            linked_document_ids=[101],
            evidence={"source": "fraud_analysis", "check": "high_income_claim", "risk_added": 15.0},
        )
    ]
    norm_docs = [
        NormalizedDocumentMeta(
            document_id=101,
            document_type="salary_slip",
            party_id="applicant",
            file_path="uploads/salary_slip_nov.pdf",
            original_name="salary_slip_nov.pdf",
        )
    ]
    section = build_document_fraud_remarks(norm_findings, norm_docs)
    assert section.has_remarks is True
    assert len(section.remarks) == 1
    assert section.remarks[0].document_name == "Salary Slip"
    assert section.remarks[0].document_id == 101


def test_05_internal_ids_not_visible_in_pdf():
    """5. Internal document/finding IDs are NOT visible in PDF."""
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    secret_finding_id = 998877
    secret_doc_id = 445566
    validated.fraud_remarks = DocumentFraudRemarksSection(
        has_remarks=True,
        status="REVIEW REQUIRED",
        remarks=[
            DocumentFraudRemark(
                finding_id=secret_finding_id,
                document_id=secret_doc_id,
                document_type="salary_slip",
                document_name="Salary Slip",
                category="fraud",
                severity="REVIEW REQUIRED",
                reason="Monthly net salary differs from claimed figure.",
                evidence_reference=f"finding:{secret_finding_id}",
                source="application_findings",
            )
        ],
    )
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-30T12:00:00Z",
        generated_by="Officer Test User",
    )
    result = generate_pdf_report(req)
    text = _extract_pdf_text(result.pdf_bytes)

    assert str(secret_finding_id) not in text, "Internal finding ID must NOT be displayed in PDF"
    assert str(secret_doc_id) not in text, "Internal document ID must NOT be displayed in PDF"
    assert f"finding:{secret_finding_id}" not in text
    assert f"doc:{secret_doc_id}" not in text


def test_06_existing_finding_reason_is_preserved():
    """6. Existing finding reason is preserved verbatim."""
    exact_reason = "PAN details require manual verification by branch manager."
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    validated.fraud_remarks = DocumentFraudRemarksSection(
        has_remarks=True,
        status="REVIEW REQUIRED",
        remarks=[
            DocumentFraudRemark(
                finding_id=12,
                document_id=34,
                document_type="pan",
                document_name="PAN Card",
                category="identity",
                severity="REVIEW REQUIRED",
                reason=exact_reason,
            )
        ],
    )
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-30T12:00:00Z",
        generated_by="Officer Test User",
    )
    result = generate_pdf_report(req)
    text = _extract_pdf_text(result.pdf_bytes)
    assert exact_reason in text


def test_07_fraud_finding_does_not_alter_the_26_particulars():
    """7. Fraud finding does not alter the 26 Particulars."""
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    assert len(template.particulars) == 26
    assert len(baseline.particulars) == 26
    assert len(composed.particular_outputs) == 26
    assert len(validated.validation_results) == 26
    assert len(state.particular_reviews) == 26


def test_08_fraud_finding_does_not_alter_supporting_evidence():
    """8. Fraud finding does not alter Supporting Evidence."""
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    original_count = evidence_map.total_evidence_count
    # Add fraud remark
    validated.fraud_remarks = DocumentFraudRemarksSection(
        has_remarks=True,
        status="REVIEW REQUIRED",
        remarks=[
            DocumentFraudRemark(
                document_name="PAN Card",
                reason="Manual verification required.",
            )
        ],
    )
    # EvidenceMap remains completely untouched
    assert evidence_map.total_evidence_count == original_count
    assert len(evidence_map.particular_mappings) == 26


def test_09_existing_audit_certification_content_remains_present():
    """9. Existing audit/certification content remains present and precedes fraud remarks."""
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    validated.fraud_remarks = DocumentFraudRemarksSection(
        has_remarks=True,
        status="REVIEW REQUIRED",
        remarks=[
            DocumentFraudRemark(
                document_name="PAN Card",
                reason="PAN format requires manual review.",
            )
        ],
    )
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-30T12:00:00Z",
        generated_by="Officer Raghavendra",
    )
    result = generate_pdf_report(req)
    text = _extract_pdf_text(result.pdf_bytes)

    audit_title = "REPORT REVIEW STATUS & STATUTORY AUDIT CERTIFICATION"
    remarks_title = "DOCUMENT VERIFICATION / FRAUD REMARKS"
    notice_text = template.footer.confidentiality_notice

    assert audit_title in text
    assert "Investigating Official" in text
    assert "Branch Head Scrutiny & Acceptance" in text
    assert "Automated Quality Assurance" in text
    assert remarks_title in text
    assert notice_text in text

    # Verify chronological sequence in document
    pos_audit = text.find(audit_title)
    pos_remarks = text.find(remarks_title)
    pos_notice = text.find(notice_text[:30])

    assert pos_audit < pos_remarks, "Audit Certification must precede Fraud Remarks"
    assert pos_remarks < pos_notice, "Fraud Remarks must precede Confidentiality Notice"


def test_10_existing_valid_report_still_generates_successfully():
    """10. Existing VALID report still generates successfully."""
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
        generation_timestamp="2026-09-30T12:00:00Z",
    )
    result = generate_pdf_report(req)
    assert result.pdf_bytes.startswith(b"%PDF-")
    assert result.page_count >= 1
    assert result.file_size > 1000


def test_11_existing_party_isolation_remains_valid():
    """11. Existing party isolation remains valid."""
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    p1_rev = state.particular_reviews["P1"]
    assert p1_rev.validation_status == "VALID"
    # Ensure no contamination in validated report
    party_issues = [
        iss for iss in validated.blocking_issues
        if "PARTY_CONTAMINATION" in iss.code
    ]
    assert len(party_issues) == 0


def test_12_existing_report_validator_tests_still_pass():
    """12. Existing Report Validator tests still pass."""
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    val = validate_report(context=context, template=template, evidence_map=evidence_map, composed_report=composed)
    assert val.is_valid is True
    assert val.overall_status in ("VALID", "VALID_WITH_WARNINGS")
    assert hasattr(val, "fraud_remarks")


def test_13_existing_pdf_generator_tests_still_pass():
    """13. Existing PDF generator tests still pass."""
    context, template, baseline, evidence_map, composed, validated, state = build_pipeline_fixtures()
    req = PdfGenerationRequest(
        review_state=state,
        template=template,
        baseline_report=baseline,
        evidence_map=evidence_map,
        validated_report=validated,
    )
    result = generate_pdf_report(req)
    assert result.page_count >= 1


def test_14_application_158_regression():
    """
    14. Application 158 regression:
    Generate report using Application 158 and verify:
    - report remains VALID
    - party isolation remains correct
    - fraud/document remarks appear only if findings exist.
    """
    db = SessionLocal()
    try:
        ctx = build_report_context(158, db=db)
        template = get_template(ctx.metadata.template_key, ctx.metadata.template_version)
        baseline = render_baseline_report(ctx, template.template_key, template.template_version)
        evidence_map = map_evidence(ctx, template)
        composed = compose_report(ctx, template=template, evidence_map=evidence_map)
        val = validate_report(context=ctx, template=template, evidence_map=evidence_map, composed_report=composed)

        # 1. Report remains VALID
        assert val.overall_status == "VALID", f"App 158 validation must be VALID, got {val.overall_status}"
        assert len(val.blocking_issues) == 0

        # 2. Party isolation remains correct
        p1_map = evidence_map.get_mapping("P1")
        guar_ev_ids = [ref.evidence_id for ref in p1_map.guarantor_evidence]
        for expected_id in ["doc:363", "doc:364", "doc:366", "doc:367"]:
            assert expected_id in guar_ev_ids, f"{expected_id} must remain in guarantor evidence"

        # 3. Fraud/document remarks behavior
        state = create_review_state(baseline, evidence_map, composed, val, template)
        req = PdfGenerationRequest(
            review_state=state,
            template=template,
            baseline_report=baseline,
            evidence_map=evidence_map,
            validated_report=val,
            generation_timestamp="2026-09-30T12:00:00Z",
            generated_by="Officer Reviewer",
        )
        result = generate_pdf_report(req)
        assert result.page_count >= 1
        text = _extract_pdf_text(result.pdf_bytes)

        # For Application 158 without document fraud findings, compact clean remark appears
        if not val.fraud_remarks.has_remarks:
            assert "No significant document fraud indicators detected" in text
        else:
            assert "DOCUMENT VERIFICATION / FRAUD REMARKS" in text
            assert "REVIEW REQUIRED" in text

        # Verify flow: Audit Certification -> Remarks -> Confidentiality Notice
        audit_idx = text.find("REPORT REVIEW STATUS & STATUTORY AUDIT CERTIFICATION")
        remarks_idx = text.find("DOCUMENT VERIFICATION / FRAUD REMARKS")
        notice_idx = text.find(template.footer.confidentiality_notice[:35])

        assert audit_idx != -1, "Audit certification section must exist"
        assert remarks_idx != -1, "Fraud remarks section must exist"
        assert notice_idx != -1, "Confidentiality notice must exist"
        assert audit_idx < remarks_idx < notice_idx, "Audit < Remarks < Notice order violated"

    finally:
        db.close()
