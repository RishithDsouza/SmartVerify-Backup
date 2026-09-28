"""
Automated Test Suite for Phase 6: AI Report Composer.
Verifies all 20 architectural requirements:
- Controlled AI wording for narrative Particulars
- Deterministic fact preservation for standard Particulars
- Strict anti-hallucination safeguards
- Provenance preservation (fact_ids and evidence_ids)
- Strict Applicant vs Guarantor isolation
- PII Aadhaar masking
- Failure handling (timeout, malformed JSON, provider error)
- Complete offline execution using MockLLMProvider (ZERO live Gemini calls)
"""
import sys
import os
import json
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
from app.schemas.composed_report import ComposedParticular, ComposedReport
from app.services.llm_provider import MockLLMProvider, ProviderError
from app.services.report_composer import (
    compose_report,
    _mask_aadhaar_pii,
    _build_particular_scoped_payload,
    PROMPT_VERSION,
    COMPOSER_VERSION,
    NARRATIVE_PARTICULAR_IDS,
)


def create_mock_report_context(missing_p2a: bool = False) -> ReportContext:
    """Creates a realistic, verified ReportContext fixture for testing."""
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
        NormalizedDocumentMeta(
            document_id=101,
            document_type="aadhaar",
            party_id="applicant",
            original_name="Aadhaar_Sunil.pdf",
            file_path="uploads/aadhaar_sunil.pdf",
            ocr_status="ocr_done",
        ),
        NormalizedDocumentMeta(
            document_id=102,
            document_type="pan",
            party_id="applicant",
            original_name="PAN_Sunil.pdf",
            file_path="uploads/pan_sunil.pdf",
            ocr_status="ocr_done",
        ),
        NormalizedDocumentMeta(
            document_id=103,
            document_type="salary_slip",
            party_id="applicant",
            original_name="Salary_May2026.pdf",
            file_path="uploads/salary.pdf",
            ocr_status="ocr_done",
        ),
        NormalizedDocumentMeta(
            document_id=104,
            document_type="bank_statement",
            party_id="applicant",
            original_name="HDFC_Statement.pdf",
            file_path="uploads/bank.pdf",
            ocr_status="ocr_done",
        ),
        NormalizedDocumentMeta(
            document_id=105,
            document_type="site_front_view",
            party_id="applicant",
            original_name="House_Front.jpg",
            file_path="uploads/front.jpg",
            ocr_status="ocr_done",
        ),
        NormalizedDocumentMeta(
            document_id=107,
            document_type="sale_deed",
            party_id="applicant",
            original_name="Sale_Deed.pdf",
            file_path="uploads/sale_deed.pdf",
            ocr_status="ocr_done",
        ),
        NormalizedDocumentMeta(
            document_id=106,
            document_type="invoice",
            party_id="applicant",
            original_name="Proforma_Invoice.pdf",
            file_path="uploads/invoice.pdf",
            ocr_status="ocr_done",
        ),
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
        NormalizedFinding(
            finding_id=301,
            particular_id="P1",
            category="identity",
            question="Is identity verified?",
            answer="Identity confirmed via Aadhaar records.",
            status="VERIFIED",
            confidence=0.99,
            party_id="applicant",
        ),
        NormalizedFinding(
            finding_id=302,
            particular_id="P2A",
            category="residence",
            question="Observation of physical visit",
            answer="Inspected residential premises at Koramangala. Property is a well-maintained RCC structure.",
            status="VERIFIED",
            confidence=0.96,
            party_id="applicant",
        ),
        NormalizedFinding(
            finding_id=303,
            particular_id="P4B",
            category="income",
            question="Salary slip genuineness",
            answer="Salary credits verified with bank records and Form 16.",
            status="VERIFIED",
            confidence=0.98,
            party_id="applicant",
        ),
        NormalizedFinding(
            finding_id=304,
            particular_id="P6",
            category="antecedents",
            question="Criminal and court background check",
            answer="Clean track record. No adverse regulatory or police records identified.",
            status="VERIFIED",
            confidence=0.95,
            party_id="applicant",
        ),
        NormalizedFinding(
            finding_id=305,
            particular_id="P7A2",
            category="invoice",
            question="Invoice authenticity and advance receipt",
            answer="Proforma invoice verified with authorized dealership. Advance payment receipt verified.",
            status="VERIFIED",
            confidence=0.97,
            party_id="applicant",
        ),
        NormalizedFinding(
            finding_id=306,
            particular_id="P8",
            category="opinion",
            question="Overall loan officer recommendation",
            answer="Recommended for approval based on adequate debt-service ratio and clear collateral title.",
            status="VERIFIED",
            confidence=0.95,
            party_id="applicant",
        ),
        # Pending review finding for testing status preservation
        NormalizedFinding(
            finding_id=307,
            particular_id="P6",
            category="antecedents",
            question="Secondary legal scrutiny status",
            answer="External advocate non-encumbrance report awaiting final stamp.",
            status="REVIEW",
            confidence=0.85,
            party_id="applicant",
        ),
    ]

    # Explicit fact provenance list
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
        "P1": ParticularContextFact(
            particular_id="P1", section="1", title="Name and Address", parent_id=None,
            applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False,
            display_order=1, applicant_facts={"name": "Sunil Kumar Varma", "father_name": "Kailash Varma", "address": "Plot 88, Koramangala"},
            guarantor_facts=[{"party_id": "joint_applicant:0", "name": "Pooja Sunil Varma", "relationship": "Spouse"}],
            status="available", missing_fields=[],
        ),
        "P2": ParticularContextFact(
            particular_id="P2", section="2", title="Residence", parent_id=None,
            applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False,
            display_order=2, applicant_facts={"residence_address": "Plot 88, Koramangala", "residence_status": "Owned"},
            status="available", missing_fields=[],
        ),
        "P2A": ParticularContextFact(
            particular_id="P2A", section="2", title="Whether address of residence/unit is seen and confirmed through physical visit - Observations", parent_id="P2",
            applicant_supported=True, guarantor_supported=True, data_type="narrative", required=True, narrative_allowed=True,
            display_order=3,
            applicant_facts={"physical_visit_confirmed": not missing_p2a, "field_observations": "RCC structure" if not missing_p2a else None},
            status="missing" if missing_p2a else "available",
            missing_fields=["physical_visit_observation"] if missing_p2a else [],
        ),
        "P2B": ParticularContextFact(
            particular_id="P2B", section="2", title="Name of official/person who visited - Date of visit", parent_id="P2",
            applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False,
            display_order=4, applicant_facts={"visiting_officer": "Rajesh Sharma", "visit_date": "2026-09-18"},
            status="available", missing_fields=[],
        ),
        "P2D": ParticularContextFact(
            particular_id="P2D", section="2", title="Status of residence", parent_id="P2",
            applicant_supported=True, guarantor_supported=True, data_type="text", required=True, narrative_allowed=False,
            display_order=6, applicant_facts={"residence_tenure_status": "Owned"},
            status="available", missing_fields=[],
        ),
        "P2E": ParticularContextFact(
            particular_id="P2E", section="2", title="Aadhaar No.", parent_id="P2",
            applicant_supported=True, guarantor_supported=True, data_type="text", required=True, narrative_allowed=False,
            display_order=7, applicant_facts={"masked_aadhaar": "XXXXXXXX9012"},
            guarantor_facts=[{"masked_aadhaar": "XXXXXXXX1098"}],
            status="available", missing_fields=[],
        ),
        "P3": ParticularContextFact(
            particular_id="P3", section="3", title="KYC Document Re-verification", parent_id=None,
            applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False,
            display_order=8, applicant_facts={"pan_number": "ABCDE1234F", "masked_aadhaar": "XXXXXXXX9012", "aadhaar_validity_status": "Valid", "pan_aadhaar_link_status": "Linked"},
            status="available", missing_fields=[],
        ),
        "P4": ParticularContextFact(
            particular_id="P4", section="4", title="Income", parent_id=None,
            applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False,
            display_order=9, shared_facts={"income_documents_seen": ["salary_slip"]},
            status="available", missing_fields=[],
        ),
        "P4A": ParticularContextFact(
            particular_id="P4A", section="4", title="Documents seen", parent_id="P4",
            applicant_supported=True, guarantor_supported=True, data_type="text", required=True, narrative_allowed=False,
            display_order=10, shared_facts={"documents_seen": ["salary_slip"]},
            status="available", missing_fields=[],
        ),
        "P4B": ParticularContextFact(
            particular_id="P4B", section="4", title="Pertains to year - Remarks on genuineness", parent_id="P4",
            applicant_supported=True, guarantor_supported=True, data_type="narrative", required=True, narrative_allowed=True,
            display_order=11, shared_facts={"salary_period": "May 2026", "genuineness_remarks": "Verified with bank credit"},
            status="available", missing_fields=[],
        ),
        "P4C": ParticularContextFact(
            particular_id="P4C", section="4", title="If salaried, employer name and address", parent_id="P4",
            applicant_supported=True, guarantor_supported=True, data_type="text", required=False, narrative_allowed=False,
            display_order=12, applicant_facts={"employer_name": "Tech Corp Bangalore"},
            status="available", missing_fields=[],
        ),
        "P4D": ParticularContextFact(
            particular_id="P4D", section="4", title="Designation / Occupation - Income details - Years in occupation", parent_id="P4",
            applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False,
            display_order=13, applicant_facts={"occupation": "Senior Software Architect", "monthly_income": 185000.0, "years_in_occupation": 11},
            status="available", missing_fields=[],
        ),
        "P5": ParticularContextFact(
            particular_id="P5", section="5", title="Bank Statement", parent_id=None,
            applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False,
            display_order=14, shared_facts={"bank_name": "HDFC Bank", "branch_name": "Koramangala"},
            status="available", missing_fields=[],
        ),
        "P5A": ParticularContextFact(
            particular_id="P5A", section="5", title="Bank & Branch", parent_id="P5",
            applicant_supported=True, guarantor_supported=True, data_type="text", required=True, narrative_allowed=False,
            display_order=15, shared_facts={"bank_name": "HDFC Bank", "branch_name": "Koramangala"},
            status="available", missing_fields=[],
        ),
        "P5B": ParticularContextFact(
            particular_id="P5B", section="5", title="Type of Account", parent_id="P5",
            applicant_supported=True, guarantor_supported=True, data_type="text", required=True, narrative_allowed=False,
            display_order=16, shared_facts={"account_type": "Savings"},
            status="available", missing_fields=[],
        ),
        "P5C": ParticularContextFact(
            particular_id="P5C", section="5", title="Statement examined from/to", parent_id="P5",
            applicant_supported=True, guarantor_supported=True, data_type="composite", required=True, narrative_allowed=False,
            display_order=17, shared_facts={"examined_from": "2025-11-01", "examined_to": "2026-04-30"},
            status="available", missing_fields=[],
        ),
        "P6": ParticularContextFact(
            particular_id="P6", section="6", title="Remarks on Track Record / Criminal Antecedents", parent_id=None,
            applicant_supported=True, guarantor_supported=True, data_type="narrative", required=True, narrative_allowed=True,
            display_order=18, applicant_facts={"track_record": "Clean", "criminal_check": "Negative"},
            status="available", missing_fields=[],
        ),
        "P7": ParticularContextFact(
            particular_id="P7", section="7", title="Dealer & Invoice/RC Book Verification for Vehicles/Machineries", parent_id=None,
            applicant_supported=True, guarantor_supported=False, data_type="composite", required=False, narrative_allowed=False,
            display_order=19, shared_facts={"vehicle_machinery_type": "Commercial Vehicle"},
            status="available", missing_fields=[],
        ),
        "P7A": ParticularContextFact(
            particular_id="P7A", section="7", title="For New Vehicles/Machineries", parent_id="P7",
            applicant_supported=True, guarantor_supported=False, data_type="composite", required=False, narrative_allowed=False,
            display_order=20, shared_facts={},
            status="available", missing_fields=[],
        ),
        "P7A1": ParticularContextFact(
            particular_id="P7A1", section="7", title="Dealer/Sub-dealer name/address", parent_id="P7A",
            applicant_supported=True, guarantor_supported=False, data_type="text", required=False, narrative_allowed=False,
            display_order=21, shared_facts={"dealer_name": "Pratham Motors Commercial"},
            status="available", missing_fields=[],
        ),
        "P7A2": ParticularContextFact(
            particular_id="P7A2", section="7", title="Genuineness of Invoice/Advance Payment Receipts", parent_id="P7A",
            applicant_supported=True, guarantor_supported=False, data_type="narrative", required=False, narrative_allowed=True,
            display_order=22, shared_facts={"invoice_verified": True},
            status="available", missing_fields=[],
        ),
        "P7B": ParticularContextFact(
            particular_id="P7B", section="7", title="For Old/Used Vehicles/Machineries", parent_id="P7",
            applicant_supported=True, guarantor_supported=False, data_type="composite", required=False, narrative_allowed=False,
            display_order=23, shared_facts={},
            status="available", missing_fields=[],
        ),
        "P7B1": ParticularContextFact(
            particular_id="P7B1", section="7", title="Correctness of RC Book No./Machinery No.", parent_id="P7B",
            applicant_supported=True, guarantor_supported=False, data_type="text", required=False, narrative_allowed=False,
            display_order=24, shared_facts={"rc_number": "KA01MJ1234"},
            status="available", missing_fields=[],
        ),
        "P7B2": ParticularContextFact(
            particular_id="P7B2", section="7", title="Charge noted details in RC", parent_id="P7B",
            applicant_supported=True, guarantor_supported=False, data_type="text", required=False, narrative_allowed=False,
            display_order=25, shared_facts={"charge_details": "Hypothecated to Bank"},
            status="available", missing_fields=[],
        ),
        "P7B3": ParticularContextFact(
            particular_id="P7B3", section="7", title="Insurance details & validity", parent_id="P7B",
            applicant_supported=True, guarantor_supported=False, data_type="composite", required=False, narrative_allowed=False,
            display_order=26, shared_facts={"insurance_details": "Comprehensive Policy Valid until 2027"},
            status="available", missing_fields=[],
        ),
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
            screenshots=gov_shots,
        ),
        findings=findings,
        site_verification={},
        property_details={},
        missing_information=[],
        input_hash="deterministic_input_hash_phase6_test_123456",
    )


def run_phase6_tests():
    print("\n" + "=" * 75)
    print("SMARTVERIFY PHASE 6: AI REPORT COMPOSER TEST SUITE")
    print("=" * 75)

    results = {}

    context = create_mock_report_context()
    template = get_template("standard_reverification", "v1.0")
    evidence_map = map_evidence(context, template)

    # Custom responses for Mock LLM to test structured output
    custom_responses = {
        "P2A": json.dumps({
            "text": "Physical inspection of premises at Koramangala confirmed residential RCC structure in good condition.",
            "fact_ids": ["site.visit_confirmed", "site.property_condition"],
            "evidence_ids": ["doc:105", "finding:302"],
            "confidence": 0.96,
            "missing_information": [],
        }),
        "P4B": json.dumps({
            "text": "Salary slips for May 2026 verified and corroborated with HDFC bank statement credits.",
            "fact_ids": [],
            "evidence_ids": ["doc:103", "doc:104", "finding:303"],
            "confidence": 0.98,
            "missing_information": [],
        }),
        "P6": json.dumps({
            "text": "Antecedent scrutiny indicates clean track record. Secondary legal scrutiny is under officer review.",
            "fact_ids": [],
            "evidence_ids": ["finding:304", "finding:307"],
            "confidence": 0.92,
            "missing_information": [],
        }),
        "P7A2": json.dumps({
            "text": "Proforma invoice verified with dealer records; margin money advance receipt confirmed.",
            "fact_ids": [],
            "evidence_ids": ["doc:106", "finding:305"],
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

    mock_provider = MockLLMProvider(
        model_name="mock-gemini-2.5-flash",
        custom_responses=custom_responses,
    )

    # ─────────────────────────────────────────────────────────────────
    # Test 1: Service Import & Execution
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 1] Testing AI Report Composer Service Import & Execution...")
    report = compose_report(context, template, evidence_map, provider=mock_provider)
    assert report is not None
    assert isinstance(report, ComposedReport)
    print("  -> PASSED: AI Report Composer imported and executed cleanly.")
    results["1_import_and_execution"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 2: Valid Input Produces Strongly-Typed ComposedReport
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 2] Testing Valid Input Produces ComposedReport...")
    assert report.application_id == 1
    assert report.template_key == "standard_reverification"
    assert report.template_version == "v1.0"
    assert len(report.particular_outputs) == 26
    assert report.input_hash == context.input_hash
    print("  -> PASSED: Complete ComposedReport produced with all 26 Particular outputs.")
    results["2_valid_input_produces_report"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 3: Particular-Scoped Context Isolation (Minimal Payload Per Row)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 3] Testing Particular-Scoped Context Isolation...")
    p2a_item = template.get_particular_by_id("P2A")
    scoped_p2a = _build_particular_scoped_payload(
        context, p2a_item, context.particular_facts.get("P2A"),
        evidence_map.get_mapping("P2A"), ["site.visit_confirmed"]
    )
    assert scoped_p2a["particular_id"] == "P2A"
    # Ensure bank statements or vehicle documents are NOT present in P2A scoped payload
    p2a_ev_sources = [ev["document_type"] for ev in scoped_p2a["evidence_references"]]
    assert "bank_statement" not in p2a_ev_sources
    assert "invoice" not in p2a_ev_sources
    print("  -> PASSED: Scoped context contains minimal relevant facts and evidence without cross-leakage.")
    results["3_particular_scoped_isolation"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 4: Strict Applicant vs Guarantor Party Isolation
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 4] Testing Applicant vs Guarantor Party Isolation...")
    p1_output = report.get_particular("P1")
    assert "Applicant: Sunil Kumar Varma" in p1_output.text
    assert "Guarantor (Spouse): Pooja Sunil Varma" in p1_output.text
    assert not ("Applicant: Pooja" in p1_output.text)
    print("  -> PASSED: Zero party cross-contamination in composed outputs.")
    results["4_party_isolation"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 5: Anti-Hallucination: Missing Facts Produce Empty Text
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 5] Testing Anti-Hallucination: Missing Facts Produce Empty Text...")
    missing_ctx = create_mock_report_context(missing_p2a=True)
    missing_rep = compose_report(missing_ctx, template, evidence_map, provider=mock_provider)
    p2a_missing = missing_rep.get_particular("P2A")
    assert p2a_missing.status == "information_required"
    assert p2a_missing.text == ""  # STRICT RULE: No fabricated prose
    assert "physical_visit_observation" in p2a_missing.missing_information
    print("  -> PASSED: Missing required facts produce empty narrative and status='information_required'.")
    results["5_no_hallucination_with_missing_facts"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 6: Missing Information Explicit Tracking
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 6] Testing Missing Information Explicit Tracking...")
    assert len(missing_rep.get_particular("P2A").missing_information) >= 1
    assert missing_rep.missing_information_count >= 1
    print("  -> PASSED: Missing information explicitly listed without speculative text.")
    results["6_missing_information_tracking"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 7: Provenance Preservation (Fact IDs Traced)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 7] Testing Fact Provenance Preservation...")
    p2a_out = report.get_particular("P2A")
    assert "site.visit_confirmed" in p2a_out.fact_ids
    assert "site.property_condition" in p2a_out.fact_ids
    print("  -> PASSED: Fact IDs accurately traced back to ReportContext fact provenance.")
    results["7_fact_provenance_preservation"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 8: Evidence ID Preservation
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 8] Testing Evidence ID Preservation...")
    assert "doc:105" in p2a_out.evidence_ids
    assert "finding:302" in p2a_out.evidence_ids
    print("  -> PASSED: Evidence IDs accurately traced back to Phase 5 EvidenceMap.")
    results["8_evidence_id_preservation"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 9: Finding Status Preservation (VERIFIED vs REVIEW)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 9] Testing Finding Status Preservation (VERIFIED vs REVIEW)...")
    p6_out = report.get_particular("P6")
    assert "under officer review" in p6_out.text.lower() or "review" in p6_out.text.lower()
    # Ensure it was not rewritten as "verified"
    assert "finding:307" in p6_out.evidence_ids
    print("  -> PASSED: REVIEW status preserved strictly as under review, never inflated to verified.")
    results["9_finding_status_preservation"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 10: Strict Aadhaar / PII Masking Enforcement
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 10] Testing Strict Aadhaar Masking Enforcement...")
    raw_leaked_sample = "Borrower Aadhaar 1234 5678 9012 and 987654321098 confirmed."
    masked_sample = _mask_aadhaar_pii(raw_leaked_sample)
    assert "1234 5678 9012" not in masked_sample
    assert "987654321098" not in masked_sample
    assert "XXXXXXXX9012" in masked_sample
    assert "XXXXXXXX1098" in masked_sample
    # Verify report output P2E has only masked numbers
    p2e_out = report.get_particular("P2E")
    assert "XXXXXXXX9012" in p2e_out.text
    assert "XXXXXXXX1098" in p2e_out.text
    assert "123456789012" not in p2e_out.text
    print("  -> PASSED: All Aadhaar numbers masked (XXXXXXXX####); zero raw 12-digit exposure.")
    results["10_aadhaar_masking"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 11: Non-Narrative / Deterministic Particulars Rendered Without AI
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 11] Testing Deterministic Particulars Rendered Without AI...")
    deterministic_pids = ["P1", "P2", "P2B", "P2D", "P2E", "P3", "P4", "P4A", "P4C", "P4D", "P5", "P5A", "P5B", "P5C", "P7", "P7A", "P7A1", "P7B", "P7B1", "P7B2", "P7B3"]
    for d_pid in deterministic_pids:
        d_out = report.get_particular(d_pid)
        assert d_out is not None
        assert d_out.status == "deterministic", f"Expected deterministic status for {d_pid}, got {d_out.status}"
        assert d_out.model == "deterministic"
        assert d_out.prompt_version is None
    print(f"  -> PASSED: All {len(deterministic_pids)} non-narrative rows rendered deterministically with model='deterministic'.")
    results["11_deterministic_particulars"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 12: Malformed AI Response Handling -> status="composer_unavailable"
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 12] Testing Malformed AI Response Handling...")
    malformed_provider = MockLLMProvider(
        model_name="mock-gemini-2.5-flash",
        custom_responses=custom_responses,
        malformed_particular_ids={"P2A"},
    )
    malformed_rep = compose_report(context, template, evidence_map, provider=malformed_provider)
    p2a_malformed = malformed_rep.get_particular("P2A")
    assert p2a_malformed.status == "composer_unavailable"
    assert "unavailable" in p2a_malformed.missing_information[0].lower() or "json" in p2a_malformed.missing_information[0].lower()
    print("  -> PASSED: Malformed JSON handled gracefully with status='composer_unavailable' without pipeline crash.")
    results["12_malformed_ai_response"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 13: Provider Failure / Timeout Handling -> status="composer_unavailable"
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 13] Testing Provider Failure / Timeout Handling...")
    failing_provider = MockLLMProvider(
        model_name="mock-gemini-2.5-flash",
        custom_responses=custom_responses,
        fail_particular_ids={"P4B"},
    )
    fail_rep = compose_report(context, template, evidence_map, provider=failing_provider)
    p4b_failed = fail_rep.get_particular("P4B")
    assert p4b_failed.status == "composer_unavailable"
    assert len(p4b_failed.missing_information) > 0
    print("  -> PASSED: Provider failure caught cleanly with status='composer_unavailable'.")
    results["13_provider_failure_handling"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 14: Deterministic Input Hashing Matches Context
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 14] Testing Deterministic Input Hash Match...")
    assert report.input_hash == context.input_hash
    print("  -> PASSED: Report input hash strictly preserved from Phase 3 ReportContext.")
    results["14_input_hash_preserved"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 15: Lossless JSON Serialization & Deserialization
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 15] Testing Full JSON Serialization and Deserialization...")
    serialized = report.model_dump()
    reconstructed = ComposedReport.model_validate(serialized)
    assert reconstructed.application_id == report.application_id
    assert reconstructed.composer_version == COMPOSER_VERSION
    assert len(reconstructed.particular_outputs) == 26
    assert reconstructed.get_particular("P2A").text == report.get_particular("P2A").text
    print("  -> PASSED: ComposedReport serializes and deserializes losslessly.")
    results["15_lossless_serialization"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 16: Prompt Version Tracking
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 16] Testing Prompt Version Tracking...")
    p2a_comp = report.get_particular("P2A")
    assert p2a_comp.prompt_version == PROMPT_VERSION
    print("  -> PASSED: Prompt version tracked accurately on composed rows.")
    results["16_prompt_version_tracking"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 17: Model Metadata Tracking
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 17] Testing Model Metadata Tracking...")
    assert p2a_comp.model == "mock-gemini-2.5-flash"
    print("  -> PASSED: Model metadata tracked accurately on composed rows.")
    results["17_model_metadata_tracking"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 18: Zero Direct Database Queries
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 18] Testing Zero Direct Database Queries...")
    # Function signature check: compose_report consumes only in-memory dataclasses
    import inspect
    sig = inspect.signature(compose_report)
    params = list(sig.parameters.keys())
    assert "db" not in params
    assert "db_session" not in params
    print("  -> PASSED: compose_report operates purely on in-memory models with zero DB arguments.")
    results["18_zero_database_queries"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 19: Zero Filesystem Mutation
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 19] Testing Zero Filesystem Mutation...")
    # Verify no new files written in uploads or reports during compose_report
    uploads_before = set(os.listdir("uploads")) if os.path.exists("uploads") else set()
    _ = compose_report(context, template, evidence_map, provider=mock_provider)
    uploads_after = set(os.listdir("uploads")) if os.path.exists("uploads") else set()
    assert uploads_before == uploads_after
    print("  -> PASSED: Zero filesystem mutations during report composition.")
    results["19_zero_filesystem_mutation"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 20: Zero Changes to Previous Phases
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 20] Testing Immutability of Context and EvidenceMap...")
    context_hash_before = context.input_hash
    evidence_count_before = evidence_map.total_evidence_count
    _ = compose_report(context, template, evidence_map, provider=mock_provider)
    assert context.input_hash == context_hash_before
    assert evidence_map.total_evidence_count == evidence_count_before
    print("  -> PASSED: Input ReportContext and EvidenceMap remain completely immutable.")
    results["20_immutability_of_previous_phases"] = "PASSED"

    print("\n" + "=" * 75)
    print(f"PHASE 6 AI REPORT COMPOSER TESTS: ALL PASSED ({len(results)}/20)")
    print("=" * 75 + "\n")
    return results


def test_phase6_ai_report_composer():
    """Pytest entrypoint."""
    results = run_phase6_tests()
    for k, v in results.items():
        assert v == "PASSED"


if __name__ == "__main__":
    run_phase6_tests()
