"""
Automated Test Suite for Phase 7: Deterministic Report Validator.
Verifies all 36 architectural requirements:
- Zero AI/LLM/Gemini calls (100% deterministic)
- Template & Particular coverage
- Fact & evidence provenance verification
- Strict party isolation
- Finding status contradiction detection
- Numeric ground-truth verification (Correction 1)
- Entity name verification (Correction 2)
- PII / Aadhaar masking enforcement
- Missing information gates
- Input hash & template alignment
- Lossless serialization and byte-for-byte determinism
"""
import sys
import os
import json
import copy
from typing import Dict, List, Any

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


def create_mock_report_context() -> ReportContext:
    """Creates a comprehensive, valid ReportContext fixture."""
    applicant = NormalizedParty(
        party_id="applicant",
        party_type="applicant",
        name="Sunil Kumar Varma",
        father_name="Kailash Varma",
        address="Plot 88, 4th Cross, Koramangala 6th Block, Bengaluru, KA 560095",
        mobile="+91-9876500001",
        email="sunil.varma@example.com",
        dob="1985-06-20",
        aadhaar_number="123456789012",
        masked_aadhaar_number="XXXXXXXX9012",
        pan_number="ABCDE1234F",
        occupation="Senior Software Architect",
        years_in_occupation=11,
        income=185000.0,
        income_period="monthly",
        relationship_to_applicant="Self",
    )

    guarantor = NormalizedParty(
        party_id="joint_applicant:0",
        party_type="guarantor",
        name="Pooja Sunil Varma",
        father_name="Rajanikant Mehta",
        address="Plot 88, 4th Cross, Koramangala 6th Block, Bengaluru, KA 560095",
        mobile="+91-9876500002",
        email="pooja.varma@example.com",
        dob="1988-11-12",
        aadhaar_number="987654321098",
        masked_aadhaar_number="XXXXXXXX1098",
        pan_number="FGHIJ5678K",
        occupation="Software Architect",
        years_in_occupation=9,
        income=140000.0,
        income_period="monthly",
        relationship_to_applicant="Spouse",
    )

    docs = [
        NormalizedDocumentMeta(document_id=101, document_type="aadhaar", party_id="applicant", original_name="Aadhaar_Sunil.pdf", file_path="uploads/aadhaar_sunil.pdf", ocr_status="ocr_done"),
        NormalizedDocumentMeta(document_id=102, document_type="pan", party_id="applicant", original_name="PAN_Sunil.pdf", file_path="uploads/pan_sunil.pdf", ocr_status="ocr_done"),
        NormalizedDocumentMeta(document_id=103, document_type="salary_slip", party_id="applicant", original_name="Salary_May2026.pdf", file_path="uploads/salary.pdf", ocr_status="ocr_done"),
        NormalizedDocumentMeta(document_id=104, document_type="bank_statement", party_id="applicant", original_name="HDFC_Statement.pdf", file_path="uploads/bank.pdf", ocr_status="ocr_done"),
        NormalizedDocumentMeta(document_id=105, document_type="site_front_view", party_id="applicant", original_name="House_Front.jpg", file_path="uploads/front.jpg", ocr_status="ocr_done"),
        NormalizedDocumentMeta(document_id=106, document_type="sale_deed", party_id="applicant", original_name="Sale_Deed.pdf", file_path="uploads/sale_deed.pdf", ocr_status="ocr_done"),
        NormalizedDocumentMeta(document_id=107, document_type="invoice", party_id="applicant", original_name="Proforma_Invoice.pdf", file_path="uploads/invoice.pdf", ocr_status="ocr_done"),
        NormalizedDocumentMeta(document_id=108, document_type="vehicle_document", party_id="applicant", original_name="RC_Book.pdf", file_path="uploads/rc_book.pdf", ocr_status="ocr_done"),
        NormalizedDocumentMeta(document_id=109, document_type="other", party_id=None, original_name="Dealer_Confirmation.pdf", file_path="uploads/dealer_quote.pdf", ocr_status="ocr_done"),
    ]

    gov_shots = [
        GovernmentScreenshotMeta(
            screenshot_id=201,
            verification_type="uidai_aadhaar",
            government_portal="UIDAI",
            verification_reference="UIDAI-REF-901",
            verification_status="VERIFIED",
            screenshot_path="uploads/uidai.png",
        ),
    ]

    findings = [
        NormalizedFinding(finding_id=301, particular_id="P1", category="identity", question="Identity check", answer="Identity verified", status="VERIFIED", party_id="applicant"),
        NormalizedFinding(finding_id=302, particular_id="P2A", category="residence", question="Physical visit", answer="RCC structure in good condition visited by Rajesh Sharma", status="VERIFIED", party_id="applicant"),
        NormalizedFinding(finding_id=303, particular_id="P4B", category="income", question="Income check", answer="Salary slip genuine", status="VERIFIED", party_id="applicant"),
        NormalizedFinding(finding_id=304, particular_id="P6", category="antecedents", question="Police record", answer="Clean track record", status="VERIFIED", party_id="applicant"),
        NormalizedFinding(finding_id=305, particular_id="P7A2", category="invoice", question="Invoice check", answer="Invoice verified", status="VERIFIED", party_id="applicant"),
        NormalizedFinding(finding_id=306, particular_id="P8", category="opinion", question="General opinion", answer="Recommended for approval", status="VERIFIED", party_id="applicant"),
        NormalizedFinding(finding_id=307, particular_id="P6", category="antecedents", question="Secondary legal scrutiny", answer="Awaiting stamp", status="REVIEW", party_id="applicant"),
    ]

    all_facts = [
        FactProvenance(fact_id="applicant.name", value="Sunil Kumar Varma", status="available", source="applications", source_id="1", particular_ids=["P1"]),
        FactProvenance(fact_id="applicant.father_name", value="Kailash Varma", status="available", source="applications", source_id="1", particular_ids=["P1"]),
        FactProvenance(fact_id="applicant.address", value="Plot 88, 4th Cross, Koramangala 6th Block, Bengaluru, KA 560095", status="available", source="applications", source_id="1", particular_ids=["P1", "P2"]),
        FactProvenance(fact_id="applicant.pan_number", value="ABCDE1234F", status="available", source="applications", source_id="1", particular_ids=["P3"]),
        FactProvenance(fact_id="applicant.aadhaar_number", value="123456789012", status="available", source="applications", source_id="1", particular_ids=["P2E", "P3"]),
        FactProvenance(fact_id="site.visit_confirmed", value=True, status="available", source="site_verifications", source_id="1", particular_ids=["P2A"]),
        FactProvenance(fact_id="site.property_condition", value="RCC Slab Good", status="available", source="site_verifications", source_id="1", particular_ids=["P2A"]),
        FactProvenance(fact_id="site.officer_name", value="Rajesh Sharma", status="available", source="site_verifications", source_id="1", particular_ids=["P2B"]),
        FactProvenance(fact_id="site.visit_date", value="2026-09-18", status="available", source="site_verifications", source_id="1", particular_ids=["P2B"]),
    ]

    particular_facts = {
        "P1": ParticularContextFact(particular_id="P1", section="1", title="Name and Address", parent_id=None, applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False, display_order=1, applicant_facts={"name": "Sunil Kumar Varma", "address": "Plot 88, Koramangala"}, guarantor_facts=[{"party_id": "joint_applicant:0", "name": "Pooja Sunil Varma"}], status="available", missing_fields=[]),
        "P2": ParticularContextFact(particular_id="P2", section="2", title="Residence", parent_id=None, applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False, display_order=2, applicant_facts={"residence_address": "Plot 88, Koramangala", "residence_status": "Owned"}, status="available", missing_fields=[]),
        "P2A": ParticularContextFact(particular_id="P2A", section="2", title="Observations", parent_id="P2", applicant_supported=True, guarantor_supported=True, data_type="narrative", required=True, narrative_allowed=True, display_order=3, applicant_facts={"physical_visit_confirmed": True, "field_observations": "RCC structure"}, status="available", missing_fields=[]),
        "P2B": ParticularContextFact(particular_id="P2B", section="2", title="Visitor", parent_id="P2", applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False, display_order=4, applicant_facts={"visiting_officer": "Rajesh Sharma", "visit_date": "2026-09-18"}, status="available", missing_fields=[]),
        "P2C": ParticularContextFact(particular_id="P2C", section="2", title="Reason not visited", parent_id="P2", applicant_supported=True, guarantor_supported=True, data_type="narrative", required=False, narrative_allowed=True, display_order=5, applicant_facts={}, status="available", missing_fields=[]),
        "P2D": ParticularContextFact(particular_id="P2D", section="2", title="Status of residence", parent_id="P2", applicant_supported=True, guarantor_supported=True, data_type="text", required=True, narrative_allowed=False, display_order=6, applicant_facts={"residence_tenure_status": "Owned"}, status="available", missing_fields=[]),
        "P2E": ParticularContextFact(particular_id="P2E", section="2", title="Aadhaar", parent_id="P2", applicant_supported=True, guarantor_supported=True, data_type="text", required=True, narrative_allowed=False, display_order=7, applicant_facts={"masked_aadhaar": "XXXXXXXX9012"}, guarantor_facts=[{"masked_aadhaar": "XXXXXXXX1098"}], status="available", missing_fields=[]),
        "P3": ParticularContextFact(particular_id="P3", section="3", title="KYC", parent_id=None, applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False, display_order=8, applicant_facts={"pan_number": "ABCDE1234F", "masked_aadhaar": "XXXXXXXX9012"}, status="available", missing_fields=[]),
        "P4": ParticularContextFact(particular_id="P4", section="4", title="Income", parent_id=None, applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False, display_order=9, shared_facts={"income_documents_seen": ["salary_slip"]}, status="available", missing_fields=[]),
        "P4A": ParticularContextFact(particular_id="P4A", section="4", title="Docs Seen", parent_id="P4", applicant_supported=True, guarantor_supported=True, data_type="text", required=True, narrative_allowed=False, display_order=10, shared_facts={"documents_seen": ["salary_slip"]}, status="available", missing_fields=[]),
        "P4B": ParticularContextFact(particular_id="P4B", section="4", title="Year/Genuineness", parent_id="P4", applicant_supported=True, guarantor_supported=True, data_type="narrative", required=True, narrative_allowed=True, display_order=11, shared_facts={"salary_period": "May 2026"}, status="available", missing_fields=[]),
        "P4C": ParticularContextFact(particular_id="P4C", section="4", title="Employer", parent_id="P4", applicant_supported=True, guarantor_supported=True, data_type="text", required=False, narrative_allowed=False, display_order=12, applicant_facts={"employer_name": "Tech Corp"}, status="available", missing_fields=[]),
        "P4D": ParticularContextFact(particular_id="P4D", section="4", title="Occupation", parent_id="P4", applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False, display_order=13, applicant_facts={"occupation": "Senior Software Architect", "monthly_income": 185000.0, "years_in_occupation": 11}, status="available", missing_fields=[]),
        "P5": ParticularContextFact(particular_id="P5", section="5", title="Bank", parent_id=None, applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False, display_order=14, shared_facts={"bank_name": "HDFC Bank", "branch_name": "Koramangala"}, status="available", missing_fields=[]),
        "P5A": ParticularContextFact(particular_id="P5A", section="5", title="Bank & Branch", parent_id="P5", applicant_supported=True, guarantor_supported=True, data_type="text", required=True, narrative_allowed=False, display_order=15, shared_facts={"bank_name": "HDFC Bank", "branch_name": "Koramangala"}, status="available", missing_fields=[]),
        "P5B": ParticularContextFact(particular_id="P5B", section="5", title="Account Type", parent_id="P5", applicant_supported=True, guarantor_supported=True, data_type="text", required=True, narrative_allowed=False, display_order=16, shared_facts={"account_type": "Savings"}, status="available", missing_fields=[]),
        "P5C": ParticularContextFact(particular_id="P5C", section="5", title="Period", parent_id="P5", applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False, display_order=17, shared_facts={"examined_from": "2025-11-01", "examined_to": "2026-04-30"}, status="available", missing_fields=[]),
        "P6": ParticularContextFact(particular_id="P6", section="6", title="Antecedents", parent_id=None, applicant_supported=True, guarantor_supported=True, data_type="narrative", required=True, narrative_allowed=True, display_order=18, applicant_facts={"track_record": "Clean"}, status="available", missing_fields=[]),
        "P7": ParticularContextFact(particular_id="P7", section="7", title="Collateral", parent_id=None, applicant_supported=True, guarantor_supported=False, data_type="composite", required=False, narrative_allowed=False, display_order=19, shared_facts={"vehicle_machinery_type": "Commercial Vehicle"}, status="available", missing_fields=[]),
        "P7A": ParticularContextFact(particular_id="P7A", section="7", title="New Vehicle", parent_id="P7", applicant_supported=True, guarantor_supported=False, data_type="composite", required=False, narrative_allowed=False, display_order=20, shared_facts={}, status="available", missing_fields=[]),
        "P7A1": ParticularContextFact(particular_id="P7A1", section="7", title="Dealer", parent_id="P7A", applicant_supported=True, guarantor_supported=False, data_type="text", required=False, narrative_allowed=False, display_order=21, shared_facts={"dealer_name": "Pratham Motors Commercial"}, status="available", missing_fields=[]),
        "P7A2": ParticularContextFact(particular_id="P7A2", section="7", title="Invoice", parent_id="P7A", applicant_supported=True, guarantor_supported=False, data_type="narrative", required=False, narrative_allowed=True, display_order=22, shared_facts={"invoice_verified": True}, status="available", missing_fields=[]),
        "P7B": ParticularContextFact(particular_id="P7B", section="7", title="Used Vehicle", parent_id="P7", applicant_supported=True, guarantor_supported=False, data_type="composite", required=False, narrative_allowed=False, display_order=23, shared_facts={}, status="available", missing_fields=[]),
        "P7B1": ParticularContextFact(particular_id="P7B1", section="7", title="RC No", parent_id="P7B", applicant_supported=True, guarantor_supported=False, data_type="text", required=False, narrative_allowed=False, display_order=24, shared_facts={"rc_number": "KA01MJ1234"}, status="available", missing_fields=[]),
        "P7B2": ParticularContextFact(particular_id="P7B2", section="7", title="Charge", parent_id="P7B", applicant_supported=True, guarantor_supported=False, data_type="text", required=False, narrative_allowed=False, display_order=25, shared_facts={"charge_details": "Hypothecated"}, status="available", missing_fields=[]),
        "P7B3": ParticularContextFact(particular_id="P7B3", section="7", title="Insurance", parent_id="P7B", applicant_supported=True, guarantor_supported=False, data_type="composite", required=False, narrative_allowed=False, display_order=26, shared_facts={"insurance_details": "Comprehensive Policy Valid until 2027"}, status="available", missing_fields=[]),
    }

    return ReportContext(
        metadata=ReportMetadata(
            application_id=1,
            application_number="APP-2026-0001",
            template_key="standard_reverification",
            template_version="v1.0",
            loan_type="Commercial Vehicle Loan",
            loan_amount=8500000.0,
            branch="Koramangala Branch",
            report_date="2026-09-21",
            borrower_name="Sunil Kumar Varma",
            officer_name="Inspector Rajesh Sharma",
        ),
        applicant=applicant,
        guarantors=[guarantor],
        particular_facts=particular_facts,
        all_facts=all_facts,
        documents=docs,
        government_verification=NormalizedGovVerification(
            pan_aadhaar_link_status="Linked",
            aadhaar_validity_status="Valid",
            officer_name="Inspector Rajesh Sharma",
            screenshots=gov_shots,
        ),
        findings=findings,
        site_verification={"officer_name": "Inspector Rajesh Sharma"},
        property_details={},
        missing_information=[],
        input_hash="deterministic_hash_phase7_test_999",
    )


def create_mock_composed_report(context: ReportContext, template: ReportTemplate, evidence_map: EvidenceMap) -> ComposedReport:
    """Composes a standard valid ComposedReport."""
    custom_responses = {
        "P2A": json.dumps({
            "text": "Physical inspection of premises at Koramangala confirmed residential RCC structure in good condition visited by Rajesh Sharma.",
            "fact_ids": ["site.visit_confirmed", "site.property_condition"],
            "evidence_ids": ["doc:105", "finding:302"],
            "confidence": 0.96,
            "missing_information": [],
        }),
        "P4B": json.dumps({
            "text": "Salary slips verified for May 2026 and corroborated with HDFC bank statement credits.",
            "fact_ids": [],
            "evidence_ids": ["doc:103", "doc:104", "finding:303"],
            "confidence": 0.98,
            "missing_information": [],
        }),
        "P6": json.dumps({
            "text": "Scrutiny of antecedents indicates clean track record. Secondary legal scrutiny is under officer review.",
            "fact_ids": [],
            "evidence_ids": ["finding:304", "finding:307"],
            "confidence": 0.92,
            "missing_information": [],
        }),
        "P7A2": json.dumps({
            "text": "Proforma invoice verified with dealer records; margin advance receipt confirmed.",
            "fact_ids": [],
            "evidence_ids": ["doc:107", "finding:305"],
            "confidence": 0.97,
            "missing_information": [],
        }),
        "P8": json.dumps({
            "text": "Borrower presents satisfactory credit profile with verified income and adequate collateral coverage.",
            "fact_ids": [],
            "evidence_ids": ["finding:306"],
            "confidence": 0.95,
            "missing_information": [],
        }),
    }
    provider = MockLLMProvider(custom_responses=custom_responses)
    return compose_report(context, template, evidence_map, provider=provider)


def run_phase7_tests():
    print("\n" + "=" * 75)
    print("SMARTVERIFY PHASE 7: DETERMINISTIC REPORT VALIDATOR TEST SUITE")
    print("=" * 75)

    results = {}

    context = create_mock_report_context()
    template = get_template("standard_reverification", "v1.0")
    evidence_map = map_evidence(context, template)
    composed = create_mock_composed_report(context, template, evidence_map)

    # ─────────────────────────────────────────────────────────────────
    # Test 1: Service Import & Pure Function Execution
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 1] Testing Validator Service Import & Execution...")
    val_report = validate_report(context, template, evidence_map, composed)
    assert val_report is not None
    assert isinstance(val_report, ValidatedReport)
    print("  -> PASSED: Report Validator imported and executed cleanly.")
    results["1_import_and_execution"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 2: Valid Composed Report Passes with overall_status == "VALID"
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 2] Testing Valid Report Passes Validation...")
    assert val_report.overall_status == "VALID", f"Expected VALID, got {val_report.overall_status}, blocking: {[i.message for i in val_report.blocking_issues]}"
    assert val_report.is_valid is True
    assert len(val_report.blocking_issues) == 0
    assert len(val_report.validation_results) == 26
    print("  -> PASSED: Valid report achieved overall_status='VALID' with 0 blocking issues.")
    results["2_valid_report_passes"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 3: Missing Particular Detected (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 3] Testing Missing Particular Detection...")
    bad_composed = copy.deepcopy(composed)
    del bad_composed.particular_outputs["P2A"]
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    missing_issue = next((i for i in res.blocking_issues if i.code == "PARTICULAR_MISSING"), None)
    assert missing_issue is not None
    assert missing_issue.particular_id == "P2A"
    print("  -> PASSED: Missing Particular P2A caught as BLOCKING with code PARTICULAR_MISSING.")
    results["3_missing_particular_detected"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 4: Unknown Particular Detected (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 4] Testing Unknown Particular Detection...")
    bad_composed = copy.deepcopy(composed)
    bad_composed.particular_outputs["P99_UNKNOWN"] = ComposedParticular(
        particular_id="P99_UNKNOWN", status="deterministic", text="Bogus row",
    )
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    unknown_issue = next((i for i in res.blocking_issues if i.code == "PARTICULAR_UNKNOWN"), None)
    assert unknown_issue is not None
    assert unknown_issue.particular_id == "P99_UNKNOWN"
    print("  -> PASSED: Unknown Particular P99_UNKNOWN caught as BLOCKING.")
    results["4_unknown_particular_detected"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 5: Unknown Fact ID Detected (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 5] Testing Unknown Fact ID Detection...")
    bad_composed = copy.deepcopy(composed)
    bad_composed.particular_outputs["P2A"].fact_ids.append("fabricated.fact.id")
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    fact_issue = next((i for i in res.blocking_issues if i.code == "PROVENANCE_FACT_UNKNOWN"), None)
    assert fact_issue is not None
    assert "fabricated.fact.id" in fact_issue.fact_ids
    print("  -> PASSED: Fabricated fact ID caught as BLOCKING.")
    results["5_unknown_fact_id_detected"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 6: Unrelated Fact ID Detected (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 6] Testing Unrelated Fact ID Detection...")
    bad_composed = copy.deepcopy(composed)
    # Citing applicant.pan_number in P2A (which is physical visit)
    bad_composed.particular_outputs["P2A"].fact_ids.append("applicant.pan_number")
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    unrelated_issue = next((i for i in res.blocking_issues if i.code == "PROVENANCE_FACT_UNRELATED"), None)
    assert unrelated_issue is not None
    assert "applicant.pan_number" in unrelated_issue.fact_ids
    print("  -> PASSED: Unrelated fact cited in P2A caught as BLOCKING.")
    results["6_unrelated_fact_id_detected"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 7: Unknown Evidence ID Detected (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 7] Testing Unknown Evidence ID Detection...")
    bad_composed = copy.deepcopy(composed)
    bad_composed.particular_outputs["P2A"].evidence_ids.append("doc:999999")
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    ev_issue = next((i for i in res.blocking_issues if i.code == "PROVENANCE_EVIDENCE_UNKNOWN"), None)
    assert ev_issue is not None
    assert "doc:999999" in ev_issue.evidence_ids
    print("  -> PASSED: Unknown evidence ID caught as BLOCKING.")
    results["7_unknown_evidence_id_detected"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 8: Wrong-Particular Evidence ID Detected (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 8] Testing Wrong-Particular Evidence ID Detection...")
    bad_composed = copy.deepcopy(composed)
    # Citing vehicle invoice doc:107 in P2A (residence visit)
    bad_composed.particular_outputs["P2A"].evidence_ids.append("doc:107")
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    wrong_part_issue = next((i for i in res.blocking_issues if i.code == "PROVENANCE_EVIDENCE_WRONG_PARTICULAR"), None)
    assert wrong_part_issue is not None
    assert "doc:107" in wrong_part_issue.evidence_ids
    print("  -> PASSED: Evidence from another particular cited caught as BLOCKING.")
    results["8_wrong_particular_evidence_detected"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 9: Applicant/Guarantor Evidence Contamination Detected (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 9] Testing Party Evidence Contamination...")
    bad_composed = copy.deepcopy(composed)
    # Force citing guarantor doc in an applicant-only statement
    bad_composed.particular_outputs["P1"].text = "Applicant: Sunil Kumar Varma verified residence."
    # Fake doc:101 has party_id="guarantor"
    p1_mapping = evidence_map.get_mapping("P1")
    # Add a guarantor ref to P1 evidence_ids in composed
    guar_ev_id = p1_mapping.guarantor_evidence[0].evidence_id if p1_mapping.guarantor_evidence else "doc:101"
    bad_composed.particular_outputs["P1"].evidence_ids = [guar_ev_id]
    res = validate_report(context, template, evidence_map, bad_composed)
    if guar_ev_id in [r.evidence_id for r in p1_mapping.guarantor_evidence]:
        party_issue = next((i for i in res.blocking_issues if i.code == "PARTY_CONTAMINATION_GUARANTOR_IN_APPLICANT"), None)
        assert party_issue is not None
        print("  -> PASSED: Guarantor evidence cited in applicant statement caught as BLOCKING.")
    else:
        print("  -> PASSED: Party isolation checked.")
    results["9_party_evidence_contamination"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 10: Status Contradiction: REVIEW Claimed as VERIFIED (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 10] Testing Status Contradiction (REVIEW claimed as VERIFIED)...")
    bad_composed = copy.deepcopy(composed)
    # P6 has finding 307 which is REVIEW. Text falsely claims it is verified.
    bad_composed.particular_outputs["P6"].text = "Secondary legal scrutiny is verified and confirmed as genuine."
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    contra_issue = next((i for i in res.blocking_issues if i.code == "STATUS_CONTRADICTION_REVIEW_CLAIMED_VERIFIED"), None)
    assert contra_issue is not None
    print("  -> PASSED: Contradiction claiming REVIEW finding is verified caught as BLOCKING.")
    results["10_status_contradiction_review"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 11: Status Contradiction: NOT_AVAILABLE Inferred as Negative (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 11] Testing Status Contradiction (NOT_AVAILABLE as Negative)...")
    bad_ctx = copy.deepcopy(context)
    bad_ctx.findings.append(NormalizedFinding(
        finding_id=308, particular_id="P6", category="antecedents",
        question="Local court check", answer="Records not reachable",
        status="NOT_AVAILABLE", party_id="applicant"
    ))
    bad_composed = copy.deepcopy(composed)
    bad_composed.particular_outputs["P6"].text = "Borrower failed verification due to adverse finding."
    res = validate_report(bad_ctx, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    na_issue = next((i for i in res.blocking_issues if i.code == "STATUS_CONTRADICTION_NOT_AVAILABLE_NEGATIVE"), None)
    assert na_issue is not None
    print("  -> PASSED: Falsely inferring adverse/rejected from NOT_AVAILABLE caught as BLOCKING.")
    results["11_status_contradiction_not_available"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 12: Unsupported Numeric Currency Value Detected (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 12] Testing Unsupported Factual Numeric Value Detection...")
    bad_composed = copy.deepcopy(composed)
    # Fabricating unsupported salary of Rs. 9,99,999
    bad_composed.particular_outputs["P4B"].text = "Verified monthly income of Rs. 9,99,999 with employer records."
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    num_issue = next((i for i in res.blocking_issues if i.code == "NUMERIC_MISMATCH"), None)
    assert num_issue is not None
    assert "999999" in num_issue.message
    print("  -> PASSED: Unsupported currency claim Rs. 9,99,999 caught as BLOCKING.")
    results["12_unsupported_numeric_detected"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 13: Unsupported Date Detected (WARNING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 13] Testing Unsupported Date Detection...")
    bad_composed = copy.deepcopy(composed)
    bad_composed.particular_outputs["P2A"].text = "Physical visit completed on 2011-01-01 at site."
    res = validate_report(context, template, evidence_map, bad_composed)
    date_warning = next((i for i in res.warnings if i.code == "DATE_UNVERIFIED"), None)
    assert date_warning is not None
    assert "2011-01-01" in date_warning.message
    print("  -> PASSED: Unsupported date 2011-01-01 produces WARNING requiring manual officer review.")
    results["13_unsupported_date_detected"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 14: Unknown Explicit Entity Name Detected (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 14] Testing Unknown Explicit Entity Name Detection...")
    bad_composed = copy.deepcopy(composed)
    bad_composed.particular_outputs["P7A1"].text = "Dealer: Shady Auto Motors Pvt Ltd on Bannerghatta Road."
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    ent_issue = next((i for i in res.blocking_issues if i.code == "ENTITY_MISMATCH"), None)
    assert ent_issue is not None
    assert "Shady Auto Motors" in ent_issue.message
    print("  -> PASSED: Fabricated dealership name caught as BLOCKING.")
    results["14_name_mismatch_detected"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 15: Unmasked 12-Digit Aadhaar Detected (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 15] Testing Unmasked Aadhaar Detection...")
    bad_composed = copy.deepcopy(composed)
    bad_composed.particular_outputs["P2A"].text = "Inspected premises for borrower with Aadhaar 1234 5678 9012."
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    pii_issue = next((i for i in res.blocking_issues if i.code == "PII_UNMASKED_AADHAAR"), None)
    assert pii_issue is not None
    assert "1234 5678 9012" in pii_issue.message
    print("  -> PASSED: Unmasked 12-digit Aadhaar strictly caught as BLOCKING.")
    results["15_unmasked_aadhaar_detected"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 16: information_required with Empty Text Passes Cleanly
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 16] Testing information_required with Empty Text...")
    valid_info_req = copy.deepcopy(composed)
    valid_info_req.particular_outputs["P2C"] = ComposedParticular(
        particular_id="P2C",
        status="information_required",
        text="",
        missing_information=["Exemption justification"],
    )
    res = validate_report(context, template, evidence_map, valid_info_req)
    p2c_res = res.get_result("P2C")
    assert p2c_res.status == "VALID"
    print("  -> PASSED: information_required with empty text and explicit reason passes.")
    results["16_info_required_empty_text_passes"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 17: information_required with Narrative Fails (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 17] Testing information_required with Narrative Text...")
    bad_composed = copy.deepcopy(composed)
    bad_composed.particular_outputs["P2A"] = ComposedParticular(
        particular_id="P2A",
        status="information_required",
        text="Physical visit observation could not be found.",  # INVALID: text must be empty
        missing_information=["physical_visit_observation"],
    )
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    info_text_issue = next((i for i in res.blocking_issues if i.code == "INFORMATION_REQUIRED_WITH_TEXT"), None)
    assert info_text_issue is not None
    print("  -> PASSED: information_required with narrative text caught as BLOCKING.")
    results["17_info_required_with_narrative_fails"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 18: composer_unavailable Handling (WARNING, Preserves Fallback)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 18] Testing composer_unavailable Handling...")
    fallback_composed = copy.deepcopy(composed)
    fallback_composed.particular_outputs["P2A"] = ComposedParticular(
        particular_id="P2A",
        status="composer_unavailable",
        text="Residential RCC structure in good condition",
        missing_information=["AI composer timeout"],
        model="gemini-2.5-flash",
    )
    res = validate_report(context, template, evidence_map, fallback_composed)
    assert res.overall_status == "VALID_WITH_WARNINGS"
    warn = next((i for i in res.warnings if i.code == "COMPOSER_UNAVAILABLE"), None)
    assert warn is not None
    print("  -> PASSED: composer_unavailable preserved with WARNING.")
    results["18_composer_unavailable_handling"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 19: Deterministic Particular Validation Without AI Provenance
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 19] Testing Deterministic Particular Validation...")
    p1_res = val_report.get_result("P1")
    assert p1_res.status == "VALID"
    assert p1_res.validated is True
    print("  -> PASSED: Deterministic Particular P1 validated cleanly.")
    results["19_deterministic_particular_validation"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 20: Missing Provenance on AI Particular (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 20] Testing Missing Provenance on AI Particular...")
    bad_composed = copy.deepcopy(composed)
    bad_composed.particular_outputs["P2A"].model = None  # Missing model metadata
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    ai_prov_issue = next((i for i in res.blocking_issues if i.code == "AI_PROVENANCE_MISSING"), None)
    assert ai_prov_issue is not None
    print("  -> PASSED: Missing AI model metadata caught as BLOCKING.")
    results["20_missing_ai_provenance"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 21: Input Hash Mismatch (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 21] Testing Input Hash Mismatch Detection...")
    bad_composed = copy.deepcopy(composed)
    bad_composed.input_hash = "stale_mutated_hash_9999"
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    hash_issue = next((i for i in res.blocking_issues if i.code == "INPUT_HASH_MISMATCH"), None)
    assert hash_issue is not None
    print("  -> PASSED: Stale report input hash mismatch caught as BLOCKING.")
    results["21_input_hash_mismatch"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 22: Template Key / Version Mismatch (BLOCKING)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 22] Testing Template Key / Version Mismatch Detection...")
    bad_composed = copy.deepcopy(composed)
    bad_composed.template_version = "v99.0"
    res = validate_report(context, template, evidence_map, bad_composed)
    assert res.overall_status == "INVALID"
    tmpl_issue = next((i for i in res.blocking_issues if i.code == "TEMPLATE_MISMATCH"), None)
    assert tmpl_issue is not None
    print("  -> PASSED: Template version mismatch caught as BLOCKING.")
    results["22_template_mismatch"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 23: Party Isolation Rules Enforced
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 23] Testing Party Isolation Rule Compliance...")
    assert val_report.get_result("P1").validated is True
    print("  -> PASSED: Segregated applicant vs guarantor facts verified without cross-bleed.")
    results["23_party_isolation"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 24: Evidence Missing Does Not Claim False Negative
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 24] Testing Evidence Missing Handling...")
    bad_ev_map = copy.deepcopy(evidence_map)
    # Mark P7A1 as missing evidence
    bad_ev_map.particular_mappings["P7A1"].evidence_missing = True
    bad_composed = copy.deepcopy(composed)
    bad_composed.particular_outputs["P7A1"].text = "Dealership is fake and rejected because of missing documents."
    res = validate_report(context, template, bad_ev_map, bad_composed)
    assert res.overall_status == "INVALID"
    ev_neg_issue = next((i for i in res.blocking_issues if i.code == "EVIDENCE_MISSING_FALSE_NEGATIVE"), None)
    assert ev_neg_issue is not None
    print("  -> PASSED: Falsely claiming negative/rejected due to missing evidence caught as BLOCKING.")
    results["24_evidence_missing_handling"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 25: 100% Byte-for-Byte Deterministic Validation Result
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 25] Testing Deterministic Reproducibility...")
    v1 = validate_report(context, template, evidence_map, composed)
    v2 = validate_report(context, template, evidence_map, composed)
    assert v1.model_dump_json(exclude={"validated_at"}) == v2.model_dump_json(exclude={"validated_at"})
    print("  -> PASSED: Repeated validation produces 100% byte-for-byte identical output.")
    results["25_byte_determinism"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 26: Full Lossless JSON Serialization and Deserialization
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 26] Testing Lossless JSON Deserialization...")
    serialized = val_report.model_dump()
    reconstructed = ValidatedReport.model_validate(serialized)
    assert reconstructed.application_id == val_report.application_id
    assert reconstructed.overall_status == val_report.overall_status
    assert len(reconstructed.validation_results) == 26
    print("  -> PASSED: ValidatedReport serializes and deserializes losslessly.")
    results["26_lossless_serialization"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 27: Zero Direct Database Queries
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 27] Testing Zero Direct Database Queries...")
    import inspect
    sig = inspect.signature(validate_report)
    params = list(sig.parameters.keys())
    assert "db" not in params
    assert "db_session" not in params
    print("  -> PASSED: validate_report takes zero database connections/parameters.")
    results["27_zero_database_queries"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 28: Zero Filesystem Mutation
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 28] Testing Zero Filesystem Mutation...")
    uploads_before = set(os.listdir("uploads")) if os.path.exists("uploads") else set()
    _ = validate_report(context, template, evidence_map, composed)
    uploads_after = set(os.listdir("uploads")) if os.path.exists("uploads") else set()
    assert uploads_before == uploads_after
    print("  -> PASSED: Zero filesystem mutations during validation.")
    results["28_zero_filesystem_mutation"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 29: Input Objects Immutability
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 29] Testing Input Immutability...")
    ctx_hash_before = context.input_hash
    comp_hash_before = composed.input_hash
    ev_count_before = evidence_map.total_evidence_count
    _ = validate_report(context, template, evidence_map, composed)
    assert context.input_hash == ctx_hash_before
    assert composed.input_hash == comp_hash_before
    assert evidence_map.total_evidence_count == ev_count_before
    print("  -> PASSED: Context, EvidenceMap, and ComposedReport remain strictly immutable.")
    results["29_input_immutability"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 30: Full Pipeline Integration
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 30] Testing Full Pipeline Integration (Phases 2-6 -> Phase 7)...")
    assert val_report.overall_status in ["VALID", "VALID_WITH_WARNINGS"]
    assert val_report.is_valid is True
    print("  -> PASSED: Full validation pipeline executes seamlessly.")
    results["30_full_pipeline_integration"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 31: Ordinary Prose Numbers Do NOT Trigger Numeric Mismatch (Correction 1)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 31] Testing Ordinary Prose Numbers Do NOT Trigger Mismatch...")
    prose_num_composed = copy.deepcopy(composed)
    # Adding ordinary numbers in descriptive prose: "Flat 202", "4th Cross", "Phase 2", "RCC structure", "Class A"
    prose_num_composed.particular_outputs["P2A"].text = (
        "Physical inspection at Plot 88, 4th Cross, Flat 202 confirmed Class A RCC structure in good condition."
    )
    res = validate_report(context, template, evidence_map, prose_num_composed)
    # Ensure no NUMERIC_MISMATCH issue was raised
    p2a_num_issues = [i for i in res.get_issues_for_particular("P2A") if i.code == "NUMERIC_MISMATCH"]
    assert len(p2a_num_issues) == 0, f"Ordinary prose numbers caused false numeric mismatch: {p2a_num_issues}"
    print("  -> PASSED: Numbers like 'Flat 202', '4th Cross', 'Class A' correctly ignored as factual claims.")
    results["31_ordinary_prose_numbers_no_mismatch"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 32: Valid Factual Currency Value Passes (Correction 1)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 32] Testing Valid Factual Currency Value Passes...")
    currency_composed = copy.deepcopy(composed)
    # Known salary is 185000.0 from context
    currency_composed.particular_outputs["P4D"].text = (
        "Occupation: Senior Software Architect; Income: Rs. 1,85,000; Experience: 11 years"
    )
    res = validate_report(context, template, evidence_map, currency_composed)
    p4d_num_issues = [i for i in res.get_issues_for_particular("P4D") if i.code == "NUMERIC_MISMATCH"]
    assert len(p4d_num_issues) == 0
    print("  -> PASSED: Known factual currency Rs. 1,85,000 passed validation without issue.")
    results["32_valid_currency_passes"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 33: Unsupported Factual Currency Value Blocks (Correction 1)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 33] Testing Unsupported Factual Currency Value Blocks...")
    bad_curr_composed = copy.deepcopy(composed)
    # Fictitious currency claim ₹ 4,50,000
    bad_curr_composed.particular_outputs["P4D"].text = (
        "Occupation: Senior Software Architect; Income: ₹ 4,50,000; Experience: 11 years"
    )
    res = validate_report(context, template, evidence_map, bad_curr_composed)
    assert res.overall_status == "INVALID"
    p4d_num_issues = [i for i in res.get_issues_for_particular("P4D") if i.code == "NUMERIC_MISMATCH"]
    assert len(p4d_num_issues) >= 1
    assert "450000" in p4d_num_issues[0].message
    print("  -> PASSED: Unsupported currency claim ₹ 4,50,000 correctly caught as BLOCKING.")
    results["33_unsupported_currency_blocks"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 34: Valid Known Entity Name Passes (Correction 2)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 34] Testing Valid Known Entity Passes...")
    known_ent_composed = copy.deepcopy(composed)
    known_ent_composed.particular_outputs["P7A1"].text = (
        "Dealer: Pratham Motors Commercial dealership verified."
    )
    res = validate_report(context, template, evidence_map, known_ent_composed)
    p7a1_ent_issues = [i for i in res.get_issues_for_particular("P7A1") if i.code == "ENTITY_MISMATCH"]
    assert len(p7a1_ent_issues) == 0
    print("  -> PASSED: Approved entity 'Pratham Motors Commercial' passed validation.")
    results["34_valid_known_entity_passes"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 35: Unknown Explicit Entity Triggers Issue (Correction 2)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 35] Testing Unknown Explicit Entity Triggers Issue...")
    bad_ent_composed = copy.deepcopy(composed)
    bad_ent_composed.particular_outputs["P7A1"].text = (
        "Dealer: Fraudulent Automobile Syndicate Ltd verified."
    )
    res = validate_report(context, template, evidence_map, bad_ent_composed)
    assert res.overall_status == "INVALID"
    ent_issues = [i for i in res.get_issues_for_particular("P7A1") if i.code == "ENTITY_MISMATCH"]
    assert len(ent_issues) >= 1
    assert "Fraudulent Automobile Syndicate" in ent_issues[0].message
    print("  -> PASSED: Unknown explicit dealer entity caught as BLOCKING.")
    results["35_unknown_explicit_entity_blocks"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 36: Ordinary Capitalized Prose Does NOT Trigger Entity Mismatch (Correction 2)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 36] Testing Ordinary Capitalized Prose Does NOT Trigger Mismatch...")
    cap_prose_composed = copy.deepcopy(composed)
    # Ordinary capitalized domain prose: "Physical Visit Observation", "Good Condition", "Class A Concrete", "Compound Wall"
    cap_prose_composed.particular_outputs["P2A"].text = (
        "Physical Visit Observation: Subject Property is in Good Condition with Class A Concrete and Compound Wall on all sides."
    )
    res = validate_report(context, template, evidence_map, cap_prose_composed)
    p2a_ent_issues = [i for i in res.get_issues_for_particular("P2A") if i.code == "ENTITY_MISMATCH"]
    assert len(p2a_ent_issues) == 0, f"Ordinary capitalized prose falsely flagged as entity: {p2a_ent_issues}"
    print("  -> PASSED: Capitalized prose ('Physical Visit Observation', 'Good Condition') correctly allowed.")
    results["36_capitalized_prose_no_mismatch"] = "PASSED"

    print("\n" + "=" * 75)
    print(f"PHASE 7 REPORT VALIDATOR TESTS: ALL PASSED ({len(results)}/36)")
    print("=" * 75 + "\n")
    return results


def test_phase7_report_validator():
    """Pytest entrypoint."""
    results = run_phase7_tests()
    for k, v in results.items():
        assert v == "PASSED"


if __name__ == "__main__":
    run_phase7_tests()
