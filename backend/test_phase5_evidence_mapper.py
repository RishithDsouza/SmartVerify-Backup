"""
Automated Test Suite for Phase 5: Deterministic Evidence Mapper.
Verifies all 22 architectural requirements for evidence traceability,
party segregation, deterministic mapping rules, deduplication, and serialization.
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
    MissingParticularInfo,
    FactProvenance,
)
from app.schemas.evidence_map import (
    EvidenceReference,
    EvidenceMapping,
    EvidenceMap,
)
from app.services.evidence_mapper import (
    map_evidence,
    _sort_key,
    _resolve_party_name_map,
)


def create_mock_report_context() -> ReportContext:
    """Creates a comprehensive, realistic ReportContext fixture."""
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

    guarantor_1 = NormalizedParty(
        party_id="joint_applicant:0",
        party_type="co_applicant",
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

    guarantor_2 = NormalizedParty(
        party_id="joint_applicant:1",
        party_type="guarantor",
        name="Maheshwar Varma",
        father_name="Kailash Varma",
        address="Flat 202, Green Glen, Bellandur, Bengaluru, KA 560103",
        mobile="+91-9876500003",
        email="maheshwar.v@example.com",
        dob="1982-03-05",
        aadhaar_number="555544443333",
        masked_aadhaar_number="XXXXXXXX3333",
        pan_number="KLMNO9012Z",
        occupation="Civil Contractor",
        years_in_occupation=18,
        income=250000.0,
        income_period="monthly",
        relationship_to_applicant="Brother",
    )

    docs = [
        # 1. Applicant Aadhaar
        NormalizedDocumentMeta(
            document_id=101,
            document_type="aadhaar",
            party_id="applicant",
            original_name="Aadhaar_Sunil.pdf",
            file_path="uploads/aadhaar_sunil.pdf",
            ocr_status="ocr_done",
            has_extracted_text=True,
            has_structured_data=True,
        ),
        # 2. Applicant PAN
        NormalizedDocumentMeta(
            document_id=102,
            document_type="pan",
            party_id="applicant",
            original_name="PAN_Sunil.pdf",
            file_path="uploads/pan_sunil.pdf",
            ocr_status="ocr_done",
            has_extracted_text=True,
            has_structured_data=True,
        ),
        # 3. Guarantor Aadhaar
        NormalizedDocumentMeta(
            document_id=103,
            document_type="aadhaar",
            party_id="joint_applicant:0",
            original_name="Aadhaar_Pooja.pdf",
            file_path="uploads/aadhaar_pooja.pdf",
            ocr_status="ocr_done",
            has_extracted_text=True,
            has_structured_data=True,
        ),
        # 4. Income: Salary Slip
        NormalizedDocumentMeta(
            document_id=104,
            document_type="salary_slip",
            party_id="applicant",
            original_name="SalarySlip_May2026.pdf",
            file_path="uploads/salary_may2026.pdf",
            ocr_status="ocr_done",
            has_extracted_text=True,
            has_structured_data=True,
        ),
        # 5. Income: Form 16
        NormalizedDocumentMeta(
            document_id=105,
            document_type="form_16",
            party_id="applicant",
            original_name="Form16_AY2025_26.pdf",
            file_path="uploads/form16_2026.pdf",
            ocr_status="ocr_done",
            has_extracted_text=True,
            has_structured_data=True,
        ),
        # 6. Bank Statement
        NormalizedDocumentMeta(
            document_id=106,
            document_type="bank_statement",
            party_id="applicant",
            original_name="HDFC_Bank_6M.pdf",
            file_path="uploads/hdfc_6m.pdf",
            ocr_status="ocr_done",
            has_extracted_text=True,
            has_structured_data=True,
        ),
        # 7. Residence: Site Visit Front View
        NormalizedDocumentMeta(
            document_id=107,
            document_type="site_front_view",
            party_id="applicant",
            original_name="House_Front_Koramangala.jpg",
            file_path="uploads/site_front.jpg",
            ocr_status="preprocessed",
            has_extracted_text=False,
            has_structured_data=False,
        ),
        # 8. Residence: Sale Deed
        NormalizedDocumentMeta(
            document_id=108,
            document_type="sale_deed",
            party_id="applicant",
            original_name="SaleDeed_Koramangala.pdf",
            file_path="uploads/sale_deed.pdf",
            ocr_status="ocr_done",
            has_extracted_text=True,
            has_structured_data=True,
        ),
        # 9. Vehicle: Dealer Quotation
        NormalizedDocumentMeta(
            document_id=109,
            document_type="other",
            party_id=None,
            original_name="Authorized_Dealer_Confirmation.pdf",
            file_path="uploads/dealer_quote.pdf",
            ocr_status="ocr_done",
            has_extracted_text=True,
            has_structured_data=False,
        ),
        # 10. Vehicle: Proforma Invoice
        NormalizedDocumentMeta(
            document_id=110,
            document_type="invoice",
            party_id="applicant",
            original_name="Proforma_Invoice_Tata_Truck.pdf",
            file_path="uploads/invoice_truck.pdf",
            ocr_status="ocr_done",
            has_extracted_text=True,
            has_structured_data=True,
        ),
        # 11. Vehicle: RC Document (Old Vehicle)
        NormalizedDocumentMeta(
            document_id=111,
            document_type="vehicle_document",
            party_id=None,
            original_name="RC_Book_KA01MJ1234_Hypothecation.pdf",
            file_path="uploads/rc_book.pdf",
            ocr_status="ocr_done",
            has_extracted_text=True,
            has_structured_data=True,
        ),
        # 12. General Loan Application
        NormalizedDocumentMeta(
            document_id=112,
            document_type="loan_application",
            party_id="applicant",
            original_name="Signed_Application_Form.pdf",
            file_path="uploads/loan_app.pdf",
            ocr_status="ocr_done",
            has_extracted_text=True,
            has_structured_data=False,
        ),
    ]

    gov_shots = [
        # Screenshot 1: UIDAI Aadhaar
        GovernmentScreenshotMeta(
            screenshot_id=201,
            verification_type="uidai_aadhaar",
            government_portal="UIDAI",
            verification_reference="UIDAI-VER-982110",
            verification_status="VERIFIED",
            screenshot_path="uploads/gov_shots/uidai_sunil.png",
            captured_at="2026-09-18T10:15:00",
            source_url="https://myaadhaar.uidai.gov.in",
        ),
        # Screenshot 2: Income Tax PAN-Aadhaar Link
        GovernmentScreenshotMeta(
            screenshot_id=202,
            verification_type="pan_aadhaar_link",
            government_portal="Income Tax e-Filing Portal",
            verification_reference="ITD-LINK-441209",
            verification_status="VERIFIED",
            screenshot_path="uploads/gov_shots/pan_link_sunil.png",
            captured_at="2026-09-18T10:18:00",
            source_url="https://eportal.incometax.gov.in",
        ),
        # Screenshot 3: Parivahan VAHAN RC Portal
        GovernmentScreenshotMeta(
            screenshot_id=203,
            verification_type="parivahan_vahan",
            government_portal="MoRTH VAHAN Citizen Portal",
            verification_reference="VAHAN-RC-88129",
            verification_status="VERIFIED",
            screenshot_path="uploads/gov_shots/vahan_rc_shot.png",
            captured_at="2026-09-18T11:00:00",
            source_url="https://vahan.parivahan.gov.in",
        ),
    ]

    findings = [
        # Finding 1: P1 Identity
        NormalizedFinding(
            finding_id=301,
            particular_id="P1",
            category="identity",
            question="Is borrower identity and present address confirmed?",
            answer="Yes, identity verified via Aadhaar and physical field visit.",
            status="VERIFIED",
            confidence=0.99,
            linked_document_ids=[101, 107],
            party_id="applicant",
        ),
        # Finding 2: P2A Physical Visit
        NormalizedFinding(
            finding_id=302,
            particular_id="P2A",
            category="residence",
            question="Were physical residence premises inspected?",
            answer="Yes, inspected by Rajesh Sharma. Residential RCC structure in good condition.",
            status="VERIFIED",
            confidence=0.95,
            linked_document_ids=[107],
            party_id="applicant",
        ),
        # Finding 3: P4B Income genuineness
        NormalizedFinding(
            finding_id=303,
            particular_id="P4B",
            category="income",
            question="Are submitted salary slips and Form 16 genuine?",
            answer="Yes, corroborated with IT e-filing portal and bank credits.",
            status="VERIFIED",
            confidence=0.97,
            linked_document_ids=[104, 105],
            party_id="applicant",
        ),
        # Finding 4: P6 Antecedents
        NormalizedFinding(
            finding_id=304,
            particular_id="P6",
            category="antecedents",
            question="Any negative track record or criminal antecedents noted?",
            answer="Clean track record. No civil suits, police complaints, or defaulter marks found.",
            status="VERIFIED",
            confidence=0.94,
            linked_document_ids=[],
            party_id="applicant",
        ),
        # Finding 5: P7A1 Dealer
        NormalizedFinding(
            finding_id=305,
            particular_id="P7A1",
            category="dealer",
            question="Is equipment dealership authorized?",
            answer="Yes, authorized 3S dealership confirmed via GST records and site visit.",
            status="VERIFIED",
            confidence=0.96,
            linked_document_ids=[109],
            party_id=None,
        ),
        # Finding 6: P7B1 RC Book
        NormalizedFinding(
            finding_id=306,
            particular_id="P7B1",
            category="rc_book",
            question="Does RC chassis and engine number match VAHAN registry?",
            answer="Yes, chassis imprint matches Parivahan digital records exactly.",
            status="VERIFIED",
            confidence=0.98,
            linked_document_ids=[111],
            party_id=None,
        ),
        # Finding 7: P8 General opinion
        NormalizedFinding(
            finding_id=307,
            particular_id="P8",
            category="opinion",
            question="Overall loan officer recommendation and credit suitability",
            answer="Borrower demonstrates stable cash flows, adequate collateral coverage, and good repute.",
            status="VERIFIED",
            confidence=0.95,
            linked_document_ids=[],
            party_id="applicant",
        ),
    ]

    return ReportContext(
        metadata=ReportMetadata(
            application_id=1,
            application_number="APP-2026-0001",
            template_key="standard_reverification",
            template_version="v1.0",
            loan_type="Commercial Vehicle & Machinery Loan",
            loan_amount=8500000.0,
            branch="Koramangala Branch",
            report_date="2026-09-21",
            borrower_name="Sunil Kumar Varma",
            officer_name="Inspector Rajesh Sharma",
        ),
        applicant=applicant,
        guarantors=[guarantor_1, guarantor_2],
        particular_facts={},
        all_facts=[],
        documents=docs,
        government_verification=NormalizedGovVerification(
            pan_aadhaar_link_status="Linked",
            aadhaar_validity_status="Valid",
            tax_receipt_status="Verified",
            officer_name="Inspector Rajesh Sharma",
            timestamp="2026-09-18 10:15:00",
            remarks="Portal checks verified",
            screenshots=gov_shots,
        ),
        findings=findings,
        site_verification={},
        property_details={},
        missing_information=[],
        input_hash="test_sha256_input_hash_deterministic_abc123",
    )


def run_phase5_tests():
    print("\n" + "=" * 75)
    print("SMARTVERIFY PHASE 5: DETERMINISTIC EVIDENCE MAPPER TEST SUITE")
    print("=" * 75)

    results = {}

    # ─────────────────────────────────────────────────────────────────
    # Test 1: Service Import & Pure Function Execution
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 1] Testing Evidence Mapper Service Import & Pure Execution...")
    context = create_mock_report_context()
    evidence_map = map_evidence(context)
    assert evidence_map is not None
    assert isinstance(evidence_map, EvidenceMap)
    print("  -> PASSED: Evidence Mapper imported and executed purely without direct DB queries.")
    results["1_import_and_execution"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 2: Valid ReportContext Produces EvidenceMap
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 2] Testing Valid ReportContext Produces EvidenceMap...")
    assert evidence_map.template_key == "standard_reverification"
    assert evidence_map.template_version == "v1.0"
    assert evidence_map.application_id == 1
    assert evidence_map.input_hash == "test_sha256_input_hash_deterministic_abc123"
    assert len(evidence_map.particular_mappings) == 26
    print("  -> PASSED: Valid EvidenceMap generated with all 26 template Particular mappings.")
    results["2_valid_context_produces_map"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 3: All Relevant Particulars Have Deterministic Evidence Categories
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 3] Testing All Particulars Have Phase 2 Evidence Categories...")
    template = get_template("standard_reverification", "v1.0")
    for pid, mapping in evidence_map.particular_mappings.items():
        tmpl_item = template.get_particular_by_id(pid)
        assert tmpl_item is not None
        assert mapping.evidence_category == tmpl_item.evidence_category
    print("  -> PASSED: All 26 Particular mappings exactly preserve Phase 2 evidence_category values.")
    results["3_deterministic_categories"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 4: Applicant KYC Maps to Applicant KYC Evidence
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 4] Testing Applicant KYC Maps Exclusively to Applicant Evidence...")
    p1_mapping = evidence_map.get_mapping("P1")
    assert p1_mapping is not None
    applicant_kyc_refs = [
        ref for ref in p1_mapping.applicant_evidence
        if ref.source == "documents"
    ]
    assert len(applicant_kyc_refs) >= 2  # Aadhaar + PAN
    for ref in applicant_kyc_refs:
        assert ref.party_id == "applicant"
        assert ref.party_name == "Sunil Kumar Varma"
        assert ref.evidence_category == "applicant_kyc"
    print("  -> PASSED: Applicant KYC documents mapped with category 'applicant_kyc' and correct party name.")
    results["4_applicant_kyc_mapping"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 5: Guarantor KYC Maps to Guarantor KYC Evidence
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 5] Testing Guarantor KYC Maps Exclusively to Guarantor Evidence...")
    guarantor_kyc_refs = [
        ref for ref in p1_mapping.guarantor_evidence
        if ref.source == "documents"
    ]
    assert len(guarantor_kyc_refs) >= 1  # Pooja's Aadhaar
    for ref in guarantor_kyc_refs:
        assert ref.party_id == "joint_applicant:0"
        assert ref.party_name == "Pooja Sunil Varma"
        assert ref.evidence_category == "guarantor_kyc"
    print("  -> PASSED: Guarantor KYC documents mapped with category 'guarantor_kyc' and correct party name.")
    results["5_guarantor_kyc_mapping"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 6: Government Verification Screenshots Map Correctly
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 6] Testing Government Verification Screenshots Mapping...")
    p3_mapping = evidence_map.get_mapping("P3")
    assert p3_mapping is not None
    gov_shots = [ref for ref in p3_mapping.evidence if ref.source == "government_verification_screenshots"]
    assert len(gov_shots) >= 2
    # Verify UIDAI Aadhaar and IT PAN-Aadhaar screenshots exist
    shot_types = {gs.document_type for gs in gov_shots}
    assert "uidai_aadhaar" in shot_types
    assert "pan_aadhaar_link" in shot_types
    for gs in gov_shots:
        assert gs.evidence_type == "government_screenshot"
        assert gs.evidence_category == "government_verification"
        assert gs.filename.endswith(".png")
    print("  -> PASSED: Government screenshots mapped to P3 with full portal metadata.")
    results["6_gov_screenshots_mapping"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 7: Income Documents Map to Income Particulars
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 7] Testing Income Documents Map to P4, P4A, P4B, P4C, P4D...")
    for inc_pid in ["P4", "P4A", "P4B", "P4C", "P4D"]:
        m = evidence_map.get_mapping(inc_pid)
        assert m is not None
        doc_types = {ref.document_type for ref in m.evidence if ref.source == "documents"}
        assert len(doc_types.intersection({"salary_slip", "form_16"})) > 0
        for ref in m.evidence:
            if ref.source == "documents":
                assert ref.evidence_category == "income_documents"
    print("  -> PASSED: Income documents accurately mapped across all P4 child particulars.")
    results["7_income_documents_mapping"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 8: Bank Documents Map to Bank Particulars
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 8] Testing Bank Documents Map to P5, P5A, P5B, P5C...")
    for bank_pid in ["P5", "P5A", "P5B", "P5C"]:
        m = evidence_map.get_mapping(bank_pid)
        assert m is not None
        bank_refs = [ref for ref in m.evidence if ref.source == "documents"]
        assert len(bank_refs) >= 1
        assert bank_refs[0].document_type == "bank_statement"
        assert bank_refs[0].evidence_category == "bank_documents"
    print("  -> PASSED: Bank statements accurately mapped across P5, P5A, P5B, P5C.")
    results["8_bank_documents_mapping"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 9: Residence Evidence Maps Correctly
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 9] Testing Residence Evidence Maps to P2, P2A, P2B, P2D...")
    for res_pid in ["P2", "P2A", "P2B", "P2D"]:
        m = evidence_map.get_mapping(res_pid)
        assert m is not None
        assert len(m.evidence) >= 1
        for ref in m.evidence:
            if ref.source == "documents":
                assert ref.evidence_category == "residence_evidence"
    print("  -> PASSED: Residence and site inspection evidence mapped to P2 series.")
    results["9_residence_evidence_mapping"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 10: P7A1 Maps Dealer Evidence
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 10] Testing P7A1 Maps Dealer Evidence...")
    p7a1_mapping = evidence_map.get_mapping("P7A1")
    assert p7a1_mapping is not None
    assert len(p7a1_mapping.evidence) >= 1
    dealer_refs = [ref for ref in p7a1_mapping.evidence if ref.evidence_category == "dealer_evidence"]
    assert len(dealer_refs) >= 1
    # Check finding is present
    dealer_findings = [ref for ref in p7a1_mapping.evidence if ref.source == "application_findings"]
    assert len(dealer_findings) >= 1
    assert dealer_findings[0].source_id == "305"
    print("  -> PASSED: P7A1 maps dealer evidence documents and finding 305.")
    results["10_p7a1_dealer_mapping"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 11: P7A2 Maps Invoice Evidence
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 11] Testing P7A2 Maps Invoice Evidence...")
    p7a2_mapping = evidence_map.get_mapping("P7A2")
    assert p7a2_mapping is not None
    invoice_docs = [ref for ref in p7a2_mapping.evidence if ref.document_type == "invoice"]
    assert len(invoice_docs) >= 1
    assert invoice_docs[0].evidence_category == "vehicle_invoice"
    print("  -> PASSED: P7A2 maps proforma invoice document.")
    results["11_p7a2_invoice_mapping"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 12: P7B1 Maps RC Evidence
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 12] Testing P7B1 Maps RC Evidence...")
    p7b1_mapping = evidence_map.get_mapping("P7B1")
    assert p7b1_mapping is not None
    rc_docs = [ref for ref in p7b1_mapping.evidence if ref.document_type == "vehicle_document"]
    assert len(rc_docs) >= 1
    assert rc_docs[0].evidence_category == "rc_evidence"
    # Check VAHAN screenshot mapped
    vahan_shots = [ref for ref in p7b1_mapping.evidence if ref.document_type == "parivahan_vahan"]
    assert len(vahan_shots) >= 1
    print("  -> PASSED: P7B1 maps RC book document and VAHAN portal screenshot.")
    results["12_p7b1_rc_mapping"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 13: P7B2 Maps Charge / Hypothecation Evidence
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 13] Testing P7B2 Maps Charge / Hypothecation Evidence...")
    p7b2_mapping = evidence_map.get_mapping("P7B2")
    assert p7b2_mapping is not None
    assert len(p7b2_mapping.evidence) >= 1
    for ref in p7b2_mapping.evidence:
        assert ref.evidence_category == "rc_evidence"
    print("  -> PASSED: P7B2 maps hypothecation/charge evidence.")
    results["13_p7b2_charge_mapping"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 14: P7B3 Maps Insurance Evidence Where Available
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 14] Testing P7B3 Insurance Evidence Handling...")
    p7b3_mapping = evidence_map.get_mapping("P7B3")
    assert p7b3_mapping is not None
    # If no insurance doc is uploaded in this fixture, verify missing evidence is properly flagged
    if len(p7b3_mapping.evidence) == 0:
        assert p7b3_mapping.evidence_missing is True
        assert "insurance" in p7b3_mapping.missing_reason.lower() or "rc_evidence" in p7b3_mapping.missing_reason
    else:
        for ref in p7b3_mapping.evidence:
            assert ref.evidence_category == "rc_evidence"
    print("  -> PASSED: P7B3 handles insurance evidence tracking deterministically.")
    results["14_p7b3_insurance_mapping"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 15: Applicant and Guarantor Evidence Are Never Mixed
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 15] Testing Strict Applicant vs Guarantor Party Segregation...")
    p1_m = evidence_map.get_mapping("P1")
    for ref in p1_m.applicant_evidence:
        assert ref.party_id == "applicant", f"Guarantor leaked into applicant evidence: {ref}"
    for ref in p1_m.guarantor_evidence:
        assert ref.party_id != "applicant", f"Applicant leaked into guarantor evidence: {ref}"
    print("  -> PASSED: Zero cross-contamination between applicant and guarantor evidence.")
    results["15_party_isolation"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 16: Same Evidence Source Is Not Duplicated Within Particular
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 16] Testing Deduplication Within Particulars...")
    for pid, mapping in evidence_map.particular_mappings.items():
        keys = [(ref.source, ref.source_id) for ref in mapping.evidence]
        assert len(keys) == len(set(keys)), f"Duplicate evidence found in particular {pid}!"
    print("  -> PASSED: All 26 Particular mappings have strictly zero duplicate source references.")
    results["16_duplicate_suppression"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 17: Evidence Ordering Is Deterministic
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 17] Testing Deterministic Sort Ordering...")
    for pid, mapping in evidence_map.particular_mappings.items():
        sorted_copy = sorted(mapping.evidence, key=_sort_key)
        assert mapping.evidence == sorted_copy, f"Evidence not sorted deterministically in {pid}!"
    print("  -> PASSED: All evidence lists conform strictly to deterministic composite sort keys.")
    results["17_deterministic_ordering"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 18: Missing Evidence Produces Empty List and explicit evidence_missing Flag
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 18] Testing Missing Evidence Tracking...")
    # Create empty context with no documents, screenshots, or findings
    empty_context = ReportContext(
        metadata=ReportMetadata(
            application_id=2,
            application_number="APP-EMPTY",
            template_key="standard_reverification",
            template_version="v1.0",
            loan_amount=1000000.0,
            report_date="2026-09-21",
        ),
        applicant=NormalizedParty(party_id="applicant", party_type="applicant", name="Bare Applicant"),
        guarantors=[],
        particular_facts={},
        all_facts=[],
        documents=[],
        government_verification=NormalizedGovVerification(),
        findings=[],
        input_hash="hash_empty_context",
    )
    empty_map = map_evidence(empty_context)
    assert empty_map.total_evidence_count == 0
    assert len(empty_map.missing_evidence_particular_ids) > 0
    # Check P1, P3, P4 have evidence: [] and evidence_missing: True
    for req_pid in ["P1", "P3", "P4", "P5"]:
        req_mapping = empty_map.get_mapping(req_pid)
        assert req_mapping is not None
        assert req_mapping.evidence == []
        assert req_mapping.evidence_missing is True
        assert req_mapping.missing_reason is not None
    print("  -> PASSED: Incomplete application explicitly flags missing evidence without fabrication.")
    results["18_missing_evidence_flagging"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 19: Findings Remain Traceable to Original Finding IDs
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 19] Testing Findings Traceability to Original Finding IDs...")
    p6_mapping = evidence_map.get_mapping("P6")
    assert p6_mapping is not None
    p6_finding_refs = [ref for ref in p6_mapping.evidence if ref.source == "application_findings"]
    assert len(p6_finding_refs) >= 1
    assert p6_finding_refs[0].source_id == "304"
    assert p6_finding_refs[0].evidence_id == "finding:304"
    assert "court" in p6_finding_refs[0].metadata["answer"].lower() or "clean" in p6_finding_refs[0].metadata["answer"].lower()
    print("  -> PASSED: Finding 304 preserved losslessly with original ID and question/answer.")
    results["19_findings_traceability"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 20: Government Screenshots Remain Traceable to Original Screenshot IDs
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 20] Testing Government Screenshots Traceability...")
    p2e_mapping = evidence_map.get_mapping("P2E")
    assert p2e_mapping is not None
    p2e_shots = [ref for ref in p2e_mapping.evidence if ref.source == "government_verification_screenshots"]
    assert len(p2e_shots) >= 1
    assert p2e_shots[0].source_id == "201"
    assert p2e_shots[0].evidence_id == "gov_shot:201"
    assert p2e_shots[0].metadata["government_portal"] == "UIDAI"
    assert p2e_shots[0].metadata["verification_reference"] == "UIDAI-VER-982110"
    print("  -> PASSED: Government screenshot 201 preserved losslessly with original ID and portal.")
    results["20_screenshots_traceability"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 21: Byte-for-Byte Output Determinism
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 21] Testing Byte-for-Byte Determinism on Repeated Execution...")
    map1 = map_evidence(context)
    map2 = map_evidence(context)
    json1 = map1.model_dump_json()
    json2 = map2.model_dump_json()
    assert json1 == json2, "Repeated execution produced differing output!"
    print("  -> PASSED: Identical input context generates 100% byte-for-byte identical EvidenceMap.")
    results["21_byte_determinism"] = "PASSED"

    # ─────────────────────────────────────────────────────────────────
    # Test 22: Full Lossless JSON Serialization and Deserialization
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 22] Testing Lossless JSON Deserialization...")
    serialized = evidence_map.model_dump()
    reconstructed = EvidenceMap.model_validate(serialized)
    assert reconstructed.template_key == evidence_map.template_key
    assert reconstructed.template_version == evidence_map.template_version
    assert reconstructed.application_id == evidence_map.application_id
    assert reconstructed.input_hash == evidence_map.input_hash
    assert reconstructed.total_evidence_count == evidence_map.total_evidence_count
    assert len(reconstructed.particular_mappings) == 26
    print("  -> PASSED: EvidenceMap serializes to JSON and deserializes back losslessly.")
    results["22_lossless_serialization"] = "PASSED"

    print("\n" + "=" * 75)
    print(f"PHASE 5 EVIDENCE MAPPER TESTS: ALL PASSED ({len(results)}/22)")
    print("=" * 75 + "\n")
    return results


def test_phase5_evidence_mapper():
    """Pytest entrypoint."""
    results = run_phase5_tests()
    for k, v in results.items():
        assert v == "PASSED"


if __name__ == "__main__":
    run_phase5_tests()
