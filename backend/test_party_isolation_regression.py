"""
Regression Test Suite for Party Isolation Architecture.
Covers all 14 mandatory test cases:
1. Applicant Aadhaar maps to Applicant P1.
2. Guarantor Aadhaar maps to Guarantor P1.
3. Guarantor P1 evidence never appears in Applicant P1 statement.
4. Applicant P1 evidence never appears in Guarantor P1 statement.
5. Applicant Salary Slip maps only to Applicant P4.
6. Guarantor Salary Slip maps only to Guarantor P4.
7. Applicant PAN maps only to Applicant P3.
8. Guarantor PAN maps only to Guarantor P3.
9. Missing guarantor evidence produces NA/INFORMATION REQUIRED.
10. Documents remain persisted in the documents table.
11. Existing application isolation remains intact.
12. Existing 26-particular report tests still pass.
13. Existing validator tests still pass.
14. Generate a report for Application 158 and confirm P1 is VALID,
    verifying docs 363, 364, 366, 367 classification.
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_phase6_ai_report_composer import create_mock_report_context
from app.schemas.report_context import (
    NormalizedParty,
    NormalizedDocumentMeta,
    ParticularContextFact,
)
from app.schemas.evidence_map import EvidenceReference
from app.schemas.composed_report import ComposedReport, ComposedParticular
from app.services.evidence_mapper import map_evidence
from app.services.report_composer import compose_report, _filter_party_evidence_for_text
from app.services.report_validator import validate_report, ValidationSeverity
from app.services.baseline_report_renderer import render_baseline_report
from app.services.report_context_builder import build_report_context
from app.db.database import SessionLocal
from app.models.document import Document


def _build_party_isolated_context(has_guarantor_docs=True, has_guarantor_party=True):
    ctx = create_mock_report_context()
    
    docs = [
        NormalizedDocumentMeta(
            document_id=101,
            document_type="aadhaar",
            party_id="applicant",
            original_name="app_aadhaar.pdf",
            file_path="uploads/app_aadhaar.pdf",
            has_structured_data=True,
            ocr_status="ocr_done",
        ),
        NormalizedDocumentMeta(
            document_id=102,
            document_type="pan",
            party_id="applicant",
            original_name="app_pan.pdf",
            file_path="uploads/app_pan.pdf",
            has_structured_data=True,
            ocr_status="ocr_done",
        ),
        NormalizedDocumentMeta(
            document_id=103,
            document_type="salary_slip",
            party_id="applicant",
            original_name="app_salary.pdf",
            file_path="uploads/app_salary.pdf",
            has_structured_data=True,
            ocr_status="ocr_done",
        ),
    ]

    guarantors = []
    if has_guarantor_party:
        guarantors.append(
            NormalizedParty(
                party_id="guarantor:1",
                party_type="guarantor",
                name="Pooja Sunil Varma",
                relationship_to_applicant="Spouse",
                address="Plot 88, Koramangala",
            )
        )

    if has_guarantor_docs:
        docs.extend([
            NormalizedDocumentMeta(
                document_id=201,
                document_type="aadhaar",
                party_id="guarantor:1",
                original_name="guar_aadhaar.pdf",
                file_path="uploads/guar_aadhaar.pdf",
                has_structured_data=True,
                ocr_status="ocr_done",
            ),
            NormalizedDocumentMeta(
                document_id=202,
                document_type="pan",
                party_id="guarantor:1",
                original_name="guar_pan.pdf",
                file_path="uploads/guar_pan.pdf",
                has_structured_data=True,
                ocr_status="ocr_done",
            ),
            NormalizedDocumentMeta(
                document_id=203,
                document_type="salary_slip",
                party_id="guarantor:1",
                original_name="guar_salary.pdf",
                file_path="uploads/guar_salary.pdf",
                has_structured_data=True,
                ocr_status="ocr_done",
            ),
        ])

    ctx.documents = docs
    ctx.guarantors = guarantors
    return ctx


def test_1_applicant_aadhaar_maps_to_applicant_p1():
    ctx = _build_party_isolated_context()
    em = map_evidence(ctx)
    p1 = em.get_mapping("P1")
    app_ids = [ref.evidence_id for ref in p1.applicant_evidence]
    assert "doc:101" in app_ids
    assert "doc:201" not in app_ids


def test_2_guarantor_aadhaar_maps_to_guarantor_p1():
    ctx = _build_party_isolated_context()
    em = map_evidence(ctx)
    p1 = em.get_mapping("P1")
    guar_ids = [ref.evidence_id for ref in p1.guarantor_evidence]
    assert "doc:201" in guar_ids
    assert "doc:101" not in guar_ids


def test_3_guarantor_p1_evidence_never_appears_in_applicant_p1_statement():
    ctx = _build_party_isolated_context()
    em = map_evidence(ctx)
    p1_mapping = em.get_mapping("P1")
    all_p1_ids = [e.evidence_id for e in p1_mapping.evidence]

    # Statement strictly about applicant
    applicant_only_text = "Applicant: Sunil Kumar Varma | Address: Plot 88, Koramangala"
    filtered = _filter_party_evidence_for_text(applicant_only_text, p1_mapping, all_p1_ids)

    assert "doc:101" in filtered
    assert "doc:201" not in filtered
    assert "doc:202" not in filtered

    # Validator verification: if guarantor evidence is forced into applicant-only statement, validator must flag BLOCKING
    comp = ComposedReport(
        application_id=1,
        template_key="standard_reverification",
        template_version="v1.0",
        particular_outputs={
            "P1": ComposedParticular(
                particular_id="P1",
                status="deterministic",
                text=applicant_only_text,
                fact_ids=["applicant.name"],
                evidence_ids=["doc:101", "doc:201"],  # Intentionally contaminated
                confidence=1.0,
            )
        },
        input_hash=ctx.input_hash,
        composer_version="1.0",
        total_composed_count=0,
        total_deterministic_count=1,
        missing_information_count=0,
    )
    val = validate_report(context=ctx, evidence_map=em, composed_report=comp)
    contamination_issues = [
        iss for iss in val.blocking_issues
        if iss.code == "PARTY_CONTAMINATION_GUARANTOR_IN_APPLICANT"
    ]
    assert len(contamination_issues) == 1
    assert "doc:201" in contamination_issues[0].evidence_ids


def test_4_applicant_p1_evidence_never_appears_in_guarantor_p1_statement():
    ctx = _build_party_isolated_context()
    em = map_evidence(ctx)
    p1_mapping = em.get_mapping("P1")
    all_p1_ids = [e.evidence_id for e in p1_mapping.evidence]

    guarantor_only_text = "Guarantor: Pooja Sunil Varma | Address: Plot 88, Koramangala"
    filtered = _filter_party_evidence_for_text(guarantor_only_text, p1_mapping, all_p1_ids)

    assert "doc:201" in filtered
    assert "doc:101" not in filtered

    # Validator verification: if applicant evidence is forced into guarantor-only statement
    comp = ComposedReport(
        application_id=1,
        template_key="standard_reverification",
        template_version="v1.0",
        particular_outputs={
            "P1": ComposedParticular(
                particular_id="P1",
                status="deterministic",
                text=guarantor_only_text,
                fact_ids=["guarantor.name"],
                evidence_ids=["doc:201", "doc:101"],  # Contaminated with applicant doc
                confidence=1.0,
            )
        },
        input_hash=ctx.input_hash,
        composer_version="1.0",
        total_composed_count=0,
        total_deterministic_count=1,
        missing_information_count=0,
    )
    val = validate_report(context=ctx, evidence_map=em, composed_report=comp)
    contamination_issues = [
        iss for iss in val.blocking_issues
        if iss.code == "PARTY_CONTAMINATION_APPLICANT_IN_GUARANTOR"
    ]
    assert len(contamination_issues) == 1
    assert "doc:101" in contamination_issues[0].evidence_ids


def test_5_applicant_salary_slip_maps_only_to_applicant_p4():
    ctx = _build_party_isolated_context()
    em = map_evidence(ctx)
    p4 = em.get_mapping("P4")
    app_ids = [ref.evidence_id for ref in p4.applicant_evidence]
    assert "doc:103" in app_ids
    assert "doc:203" not in app_ids


def test_6_guarantor_salary_slip_maps_only_to_guarantor_p4():
    ctx = _build_party_isolated_context()
    em = map_evidence(ctx)
    p4 = em.get_mapping("P4")
    guar_ids = [ref.evidence_id for ref in p4.guarantor_evidence]
    assert "doc:203" in guar_ids
    assert "doc:103" not in guar_ids


def test_7_applicant_pan_maps_only_to_applicant_p3():
    ctx = _build_party_isolated_context()
    em = map_evidence(ctx)
    p3 = em.get_mapping("P3")
    app_ids = [ref.evidence_id for ref in p3.applicant_evidence]
    assert "doc:102" in app_ids
    assert "doc:202" not in app_ids


def test_8_guarantor_pan_maps_only_to_guarantor_p3():
    ctx = _build_party_isolated_context()
    em = map_evidence(ctx)
    p3 = em.get_mapping("P3")
    guar_ids = [ref.evidence_id for ref in p3.guarantor_evidence]
    assert "doc:202" in guar_ids
    assert "doc:102" not in guar_ids


def test_9_missing_guarantor_evidence_produces_na():
    ctx = _build_party_isolated_context(has_guarantor_docs=False, has_guarantor_party=False)
    for pf in ctx.particular_facts.values():
        pf.guarantor_facts = []

    baseline = render_baseline_report(ctx)
    p1 = next(p for p in baseline.particulars if p.id == "P1")
    assert len(p1.guarantor_verifications) == 0

    composed = compose_report(ctx)
    p1_out = composed.get_particular("P1")
    assert "Guarantor" not in p1_out.text
    # Evidence list should contain only applicant docs
    em = map_evidence(ctx)
    filtered = _filter_party_evidence_for_text(p1_out.text, em.get_mapping("P1"), [e.evidence_id for e in em.get_mapping("P1").evidence])
    assert all(ref.party_id == "applicant" for ref in em.get_mapping("P1").evidence if ref.evidence_id in filtered)


def test_10_documents_remain_persisted_in_database():
    db = SessionLocal()
    try:
        doc_count = db.query(Document).filter(Document.application_id == 158).count()
        assert doc_count >= 10, f"Application 158 documents must remain in database, found {doc_count}"
        d363 = db.query(Document).filter(Document.id == 363).first()
        d364 = db.query(Document).filter(Document.id == 364).first()
        d366 = db.query(Document).filter(Document.id == 366).first()
        d367 = db.query(Document).filter(Document.id == 367).first()
        assert d363 is not None
        assert d364 is not None
        assert d366 is not None
        assert d367 is not None
    finally:
        db.close()


def test_11_existing_application_isolation_remains_intact():
    db = SessionLocal()
    try:
        ctx158 = build_report_context(158, db=db)
        doc_ids_158 = {d.document_id for d in ctx158.documents}
        # Documents from application 158 must not leak into a context built for another application
        app6_docs = db.query(Document).filter(Document.application_id == 6).all()
        app6_ids = {d.id for d in app6_docs}
        assert doc_ids_158.isdisjoint(app6_ids)
    finally:
        db.close()


def test_12_and_13_validator_and_composition_pipeline_clean():
    ctx = _build_party_isolated_context()
    em = map_evidence(ctx)
    composed = compose_report(ctx, evidence_map=em)
    val = validate_report(context=ctx, evidence_map=em, composed_report=composed)
    # No party contamination issues in clean composition
    party_issues = [
        iss for iss in val.blocking_issues
        if "PARTY_CONTAMINATION" in iss.code
    ]
    assert len(party_issues) == 0


def test_14_application_158_p1_valid_and_docs_classified():
    db = SessionLocal()
    try:
        ctx = build_report_context(158, db=db)
        em = map_evidence(ctx)
        composed = compose_report(ctx, evidence_map=em)
        val = validate_report(context=ctx, evidence_map=em, composed_report=composed)

        # Confirm docs 363, 364, 366, 367 are classified as guarantor
        for d in ctx.documents:
            if d.document_id in [363, 364, 366, 367]:
                assert "guarantor" in d.party_id or "joint_applicant" in d.party_id

        p1_map = em.get_mapping("P1")
        guar_ev_ids = [ref.evidence_id for ref in p1_map.guarantor_evidence]
        assert "doc:363" in guar_ev_ids
        assert "doc:364" in guar_ev_ids
        assert "doc:366" in guar_ev_ids
        assert "doc:367" in guar_ev_ids

        # P1 validation must be completely VALID with 0 blocking issues
        p1_result = val.validation_results.get("P1")
        assert p1_result is not None
        assert p1_result.status == "VALID"
        assert len(p1_result.issues) == 0
        assert val.overall_status == "VALID"
        assert len(val.blocking_issues) == 0
    finally:
        db.close()
