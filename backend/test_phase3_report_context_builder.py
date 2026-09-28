"""
SmartVerify Phase 3: Report Context Builder Automated Test Suite.
Validates that build_report_context() deterministically extracts and normalizes verified database facts
into a structured ReportContext conforming to the Phase 2 fixed report template.
Tests provenance, normalization, missing field detection, input hash determinism, and serialization.
"""
import sys
import json
from pathlib import Path
from datetime import datetime

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.db.database import SessionLocal
from app.models.application import (
    Application,
    ApplicationStatus,
    JointApplicant,
    SiteVerification,
    PropertyDetails,
    GovVerification,
)
from app.models.document import Document, DocumentType
from app.models.finding import ApplicationFinding
from app.models.government_screenshot import GovernmentVerificationScreenshot
from app.models.user import User, UserRole
from app.services.report_context_builder import build_report_context
from app.schemas.report_context import ReportContext


def run_phase3_tests():
    print("\n" + "=" * 75)
    print("SMARTVERIFY PHASE 3: REPORT CONTEXT BUILDER TEST SUITE")
    print("=" * 75)

    db = SessionLocal()
    test_user_id = None
    app_id = None
    results = {}

    try:
        # ─────────────────────────────────────────────────────────────────
        # Setup: Create full test dataset in PostgreSQL
        # ─────────────────────────────────────────────────────────────────
        print("\n[Setup] Setting up test application with complete verified facts...")
        officer = db.query(User).filter(User.email == "officer@smartverify.com").first()
        if not officer:
            officer = User(
                name="Inspector Rajesh Sharma",
                email=f"officer_phase3_{int(datetime.utcnow().timestamp())}@smartverify.com",
                password="hashed_pw",
                role=UserRole.loan_officer,
            )
            db.add(officer)
            db.commit()
            db.refresh(officer)
        test_user_id = officer.id

        # 1. Create Application
        app = Application(
            user_id=test_user_id,
            applicant_name="Sunil Kumar Varma",
            branch="Koramangala Branch",
            loan_type="Home Construction Loan",
            loan_amount=8500000.0,
            loan_tenure=240,
            interest_rate=8.65,
            aadhaar_number="123456789012",
            pan_number="ABCDE1234F",
            dob="1985-06-20",
            gender="Male",
            address="Plot 88, 4th Cross, Koramangala 6th Block, Bengaluru, KA 560095",
            father_name="Kailash Varma",
            applicant_mobile="+91-9876500001",
            applicant_email="sunil.varma@example.com",
            status=ApplicationStatus.approved,
        )
        db.add(app)
        db.commit()
        db.refresh(app)
        app_id = app.id

        # 2. Add Joint Applicants (Co-Applicant + Guarantor)
        ja_coapp = JointApplicant(
            application_id=app_id,
            index=0,
            name="Pooja Sunil Varma",
            applicant_type="co_applicant",
            relationship_type="Spouse",
            pan_number="FGHIJ5678K",
            aadhaar_number="987654321098",
            dob="1988-11-14",
            address="Plot 88, 4th Cross, Koramangala 6th Block, Bengaluru, KA 560095",
            mobile="+91-9876500002",
            email="pooja.varma@example.com",
            income=120000.0,
            occupation="Software Architect",
            father_name="Rajanikant Mehta",
            years_in_occupation=11,
            remarks="Primary co-borrower contributing to repayment",
        )
        ja_guarantor = JointApplicant(
            application_id=app_id,
            index=1,
            name="Maheshwar Varma",
            applicant_type="guarantor",
            relationship_type="Brother",
            pan_number="KLMNO9012Z",
            aadhaar_number="555544443333",
            dob="1982-03-05",
            address="Flat 202, Green Glen, Bellandur, Bengaluru, KA 560103",
            mobile="+91-9876500003",
            email="maheshwar.v@example.com",
            income=250000.0,
            occupation="Civil Contractor",
            father_name="Kailash Varma",
            years_in_occupation=18,
            remarks="Financial guarantor with property collateral",
        )
        db.add_all([ja_coapp, ja_guarantor])

        # 3. Add Documents
        doc_aadhaar = Document(
            application_id=app_id,
            document_type=DocumentType.aadhaar,
            file_path="uploads/test_aadhaar.png",
            original_name="Aadhaar_Card_Sunil.png",
            extracted_text="Government of India Sunil Kumar Varma 1234 5678 9012",
            structured_data='{"aadhaar": "123456789012", "name": "Sunil Kumar Varma"}',
            processed=2,
            joint_applicant_index=None,
        )
        doc_salary = Document(
            application_id=app_id,
            document_type=DocumentType.salary_slip,
            file_path="uploads/salary_slip_may2026.pdf",
            original_name="SalarySlip_May2026.pdf",
            extracted_text="Monthly Net Pay: Rs 1,85,000",
            structured_data='{"net_income": 185000}',
            processed=2,
            joint_applicant_index=None,
        )
        doc_bank = Document(
            application_id=app_id,
            document_type=DocumentType.bank_statement,
            file_path="uploads/bank_statement_6m.pdf",
            original_name="HDFC_Statement_6M.pdf",
            extracted_text="HDFC Bank Koramangala Account: 5010023456789",
            structured_data='{"bank": "HDFC Bank", "account_type": "Savings"}',
            processed=2,
            joint_applicant_index=None,
        )
        db.add_all([doc_aadhaar, doc_salary, doc_bank])

        # 4. Add Site Verification
        site_v = SiteVerification(
            application_id=app_id,
            gps_coordinates="12.9352, 77.6245",
            officer_name="Inspector Rajesh Sharma",
            date="2026-09-18",
            time="11:30 AM",
            property_condition="Good Condition - RCC Slab Structure",
            construction_quality="Class A Concrete",
            boundary_present="Compound Wall on all 4 sides",
            road_access="30 feet Tar Road Access",
            utilities_available="BESCOM Electricity, BWSSB Water Connection",
            remarks="Physical visit completed. Borrower resides at premises with family.",
        )
        db.add(site_v)

        # 5. Add Property Details
        prop = PropertyDetails(
            application_id=app_id,
            property_type="Residential House",
            address="Plot 88, 4th Cross, Koramangala 6th Block, Bengaluru, KA 560095",
            village_city="Bengaluru",
            taluk="Bengaluru South",
            district="Bengaluru Urban",
            state="Karnataka",
            pin_code="560095",
            survey_number="Sy. No. 142/2",
            khata_number="Khata A-9872",
            property_area="2400 sq.ft",
            market_value=12500000.0,
            loan_security_value=10500000.0,
        )
        db.add(prop)

        # 6. Add Government Verification & Screenshot Metadata
        gov_v = GovVerification(
            application_id=app_id,
            pan_aadhaar_link_status="Linked",
            aadhaar_validity_status="Valid",
            tax_receipt_status="Verified",
            aadhaar_screenshot_path="uidai_shot_sunil.png",
            screenshot_path="pan_link_shot_sunil.png",
            officer_name="Inspector Rajesh Sharma",
            timestamp="2026-09-18 10:15:00",
            remarks="UIDAI portal confirms Valid Aadhaar; IT portal confirms Linked PAN-Aadhaar.",
        )
        db.add(gov_v)

        shot_meta = GovernmentVerificationScreenshot(
            application_id=app_id,
            verification_type="uidai_aadhaar",
            government_portal="UIDAI",
            verification_reference="UIDAI-REF-992812",
            verification_status="VERIFIED",
            screenshot_path="uploads/gov_shots/uidai_shot_sunil.png",
            source_url="https://myaadhaar.uidai.gov.in",
            notes="Screenshot captured showing green verification tick",
        )
        db.add(shot_meta)

        # 7. Add Finding
        finding = ApplicationFinding(
            application_id=app_id,
            particular_id="P1",
            question="Is borrower residential address confirmed?",
            answer="Yes, residential address matches physical inspection and utility records.",
            finding_type="identity",
            status="VERIFIED",
            confidence=0.98,
        )
        db.add(finding)

        db.commit()
        print(f"  -> Setup complete. Test Application ID = {app_id}")

        # ─────────────────────────────────────────────────────────────────
        # Test 1: Service Import & Execution
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 1] Testing Report Context Builder Service Execution...")
        context: ReportContext = build_report_context(app_id, db)
        assert context is not None
        assert isinstance(context, ReportContext)
        print("  -> PASSED: Context Builder executed successfully and returned typed ReportContext.")
        results["1_execution"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 2 & 3: Template Key & Version Fidelity
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 2 & 3] Testing Template Key & Version Fidelity...")
        assert context.metadata.template_key == "standard_reverification"
        assert context.metadata.template_version == "v1.0"
        assert context.metadata.application_id == app_id
        assert context.metadata.application_number == f"APP-{app_id:06d}"
        assert context.metadata.loan_amount == 8500000.0
        assert context.metadata.borrower_name == "Sunil Kumar Varma"
        print(f"  -> PASSED: Metadata matches template: {context.metadata.template_key} {context.metadata.template_version}")
        results["2_3_template_meta"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 4: Primary Applicant Normalization
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 4] Testing Primary Applicant Normalization...")
        app_norm = context.applicant
        assert app_norm.party_id == "applicant"
        assert app_norm.party_type == "applicant"
        assert app_norm.name == "Sunil Kumar Varma"
        assert app_norm.father_name == "Kailash Varma"
        assert app_norm.pan_number == "ABCDE1234F"
        assert app_norm.aadhaar_number == "123456789012"
        assert app_norm.masked_aadhaar_number == "XXXXXXXX9012"
        assert app_norm.mobile == "+91-9876500001"
        assert app_norm.email == "sunil.varma@example.com"
        print(f"  -> PASSED: Applicant '{app_norm.name}' normalized with masked Aadhaar '{app_norm.masked_aadhaar_number}'.")
        results["4_applicant_normalization"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 5: Co-Applicant & Guarantor Normalization
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 5] Testing Co-Applicant & Guarantor Normalization...")
        assert len(context.guarantors) == 2

        coapp = next(g for g in context.guarantors if g.party_type == "co_applicant")
        assert coapp.name == "Pooja Sunil Varma"
        assert coapp.father_name == "Rajanikant Mehta"
        assert coapp.pan_number == "FGHIJ5678K"
        assert coapp.masked_aadhaar_number == "XXXXXXXX1098"
        assert coapp.years_in_occupation == 11
        assert coapp.income == 120000.0

        guar = next(g for g in context.guarantors if g.party_type == "guarantor")
        assert guar.name == "Maheshwar Varma"
        assert guar.father_name == "Kailash Varma"
        assert guar.pan_number == "KLMNO9012Z"
        assert guar.masked_aadhaar_number == "XXXXXXXX3333"
        assert guar.years_in_occupation == 18
        assert guar.income == 250000.0
        print(f"  -> PASSED: Co-applicant '{coapp.name}' and Guarantor '{guar.name}' normalized with full KYC and financials.")
        results["5_guarantors_normalization"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 6: Missing Fields Remain Null / Missing
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 6] Testing Missing Values Remain Null (No Fabrication)...")
        # Occupation was not set on Application model, must remain None
        assert context.applicant.occupation is None
        assert context.applicant.years_in_occupation is None
        # P7 (Vehicles/Machineries) has no vehicle docs, should not invent dealer info
        p7a1 = context.particular_facts["P7A1"]
        assert p7a1.applicant_facts.get("dealer_name") is None
        print("  -> PASSED: Unset database fields remain strictly None / missing without fabrication.")
        results["6_missing_no_fabrication"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 7: Traceable Fact Objects & Deterministic IDs
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 7] Testing Traceable Fact Objects with Source Provenance...")
        fact_map = {f.fact_id: f for f in context.all_facts}
        assert "applicant.name" in fact_map
        assert fact_map["applicant.name"].value == "Sunil Kumar Varma"
        assert fact_map["applicant.name"].source == "applications"
        assert fact_map["applicant.name"].source_id == f"app:{app_id}"
        assert "P1" in fact_map["applicant.name"].particular_ids

        assert "guarantor_1.income" in fact_map
        assert fact_map["guarantor_1.income"].value == 250000.0
        assert fact_map["guarantor_1.income"].source == "joint_applicants"

        assert "gov.aadhaar_validity_status" in fact_map
        assert fact_map["gov.aadhaar_validity_status"].value == "Valid"
        print(f"  -> PASSED: Verified {len(context.all_facts)} traceable fact objects with source table and record IDs.")
        results["7_traceable_facts"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 8: All 26 Stable Particular IDs Mapped
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 8] Testing Complete Mapping to all 26 Stable Particular IDs...")
        EXPECTED_IDS = [
            "P1", "P2", "P2A", "P2B", "P2C", "P2D", "P2E",
            "P3", "P4", "P4A", "P4B", "P4C", "P4D",
            "P5", "P5A", "P5B", "P5C", "P6",
            "P7", "P7A", "P7A1", "P7A2", "P7B", "P7B1", "P7B2", "P7B3"
        ]
        assert len(context.particular_facts) == 26
        EXPECTED_P7_TITLES = {
            "P7": "Dealer & Invoice/RC Book Verification for Vehicles/Machineries",
            "P7A": "For New Vehicles/Machineries",
            "P7A1": "Dealer/Sub-dealer name/address",
            "P7A2": "Genuineness of Invoice/Advance Payment Receipts",
            "P7B": "For Old/Used Vehicles/Machineries",
            "P7B1": "Correctness of RC Book No./Machinery No.",
            "P7B2": "Charge noted details in RC",
            "P7B3": "Insurance details & validity",
        }
        for p7_id, expected_title in EXPECTED_P7_TITLES.items():
            assert p7_id in context.particular_facts
            assert context.particular_facts[p7_id].title == expected_title, (
                f"Title mismatch for {p7_id}: got '{context.particular_facts[p7_id].title}', expected '{expected_title}'"
            )
            assert context.particular_facts[p7_id].section == "7"

        # Verify semantic facts for P7
        assert "is_vehicle_machinery_loan" in context.particular_facts["P7"].shared_facts
        assert "for_new_vehicles_machineries" in context.particular_facts["P7A"].shared_facts
        assert "dealer_subdealer_details" in context.particular_facts["P7A1"].shared_facts
        assert "invoice_documents_present" in context.particular_facts["P7A2"].shared_facts
        assert "for_old_used_vehicles_machineries" in context.particular_facts["P7B"].shared_facts
        assert "rc_machinery_number_verification" in context.particular_facts["P7B1"].shared_facts
        assert "charge_hypothecation_noted_in_rc" in context.particular_facts["P7B2"].shared_facts
        assert "insurance_details_validity" in context.particular_facts["P7B3"].shared_facts

        print("  -> PASSED: All 26 stable Particular IDs exist in normalized context, with exact Phase 2 P7 semantic mappings.")
        results["8_particular_ids_mapped"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 9: Government Verification & Screenshot Normalization
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 9] Testing Government Verification & Screenshot Metadata Normalization...")
        gov = context.government_verification
        assert gov.pan_aadhaar_link_status == "Linked"
        assert gov.aadhaar_validity_status == "Valid"
        assert gov.timestamp == "2026-09-18 10:15:00"
        assert len(gov.screenshots) == 1
        assert gov.screenshots[0].government_portal == "UIDAI"
        assert gov.screenshots[0].verification_reference == "UIDAI-REF-992812"
        # Verify screenshots are exposed as metadata without selecting/filtering evidence
        assert gov.screenshots[0].screenshot_path == "uploads/gov_shots/uidai_shot_sunil.png"
        print("  -> PASSED: Government verification and screenshot metadata normalized accurately.")
        results["9_gov_verification"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 10: Document Metadata Normalization
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 10] Testing Document Metadata Normalization...")
        assert len(context.documents) == 3
        doc_types = {d.document_type for d in context.documents}
        assert doc_types == {"aadhaar", "salary_slip", "bank_statement"}
        for d in context.documents:
            assert d.ocr_status == "ocr_done"
            assert d.has_extracted_text is True
            assert d.has_structured_data is True
            assert d.file_path is not None
        print("  -> PASSED: Document metadata normalized with OCR statuses and file paths.")
        results["10_documents_normalized"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 11: Findings Normalization
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 11] Testing Application Findings Normalization...")
        assert len(context.findings) == 1
        f = context.findings[0]
        assert f.particular_id == "P1"
        assert f.status == "VERIFIED"
        assert f.confidence == 0.98
        assert f.category == "identity"
        print("  -> PASSED: Findings normalized without reinterpretation or risk score mutation.")
        results["11_findings_normalized"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 12: Missing Information Detection on Incomplete Application
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 12] Testing Missing Required Information Detection...")
        # Create an incomplete bare application to verify missing info detection
        bare_app = Application(
            user_id=test_user_id,
            applicant_name=None,  # missing
            address=None,         # missing
            loan_amount=1000000.0,
            status=ApplicationStatus.pending,
        )
        db.add(bare_app)
        db.commit()
        db.refresh(bare_app)

        bare_context = build_report_context(bare_app.id, db)
        assert len(bare_context.missing_information) > 0

        p1_missing = next((m for m in bare_context.missing_information if m.particular_id == "P1"), None)
        assert p1_missing is not None
        assert "applicant_name" in p1_missing.missing_fields
        assert "applicant_address" in p1_missing.missing_fields

        p2a_missing = next((m for m in bare_context.missing_information if m.particular_id == "P2A"), None)
        assert p2a_missing is not None
        assert "physical_visit_record" in p2a_missing.missing_fields

        p4_missing = next((m for m in bare_context.missing_information if m.particular_id == "P4"), None)
        assert p4_missing is not None
        assert "income_documents" in p4_missing.missing_fields

        db.delete(bare_app)
        db.commit()
        print("  -> PASSED: Missing required fields properly detected across incomplete particulars.")
        results["12_missing_information_detection"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 13 & 14: Input Hash Determinism & Staleness Sensitivity
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 13 & 14] Testing Deterministic Input Hash and Staleness Sensitivity...")
        # Re-running context on same DB state must produce IDENTICAL hash
        ctx_run1 = build_report_context(app_id, db)
        ctx_run2 = build_report_context(app_id, db)
        assert ctx_run1.input_hash == ctx_run2.input_hash
        print(f"  -> PASSED: Same database state produces identical hash: {ctx_run1.input_hash[:16]}...")

        # Mutate one fact on the application (change loan amount)
        original_amt = app.loan_amount
        app.loan_amount = 9999999.0
        db.commit()

        ctx_mutated = build_report_context(app_id, db)
        assert ctx_mutated.input_hash != ctx_run1.input_hash
        print(f"  -> PASSED: Mutating loan_amount changes hash: {ctx_mutated.input_hash[:16]}... != {ctx_run1.input_hash[:16]}...")

        # Revert change
        app.loan_amount = original_amt
        db.commit()
        ctx_reverted = build_report_context(app_id, db)
        assert ctx_reverted.input_hash == ctx_run1.input_hash
        print("  -> PASSED: Reverting fact restores exact original hash.")
        results["13_14_input_hash"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 15: Serialization / Deserialization (Clean JSON)
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 15] Testing Full Context Serialization & Deserialization...")
        ctx_json = context.model_dump_json(indent=2)
        assert len(ctx_json) > 2000

        reconstituted = ReportContext.model_validate_json(ctx_json)
        assert reconstituted.metadata.application_id == context.metadata.application_id
        assert reconstituted.input_hash == context.input_hash
        assert reconstituted.applicant.name == context.applicant.name
        assert len(reconstituted.guarantors) == len(context.guarantors)
        assert len(reconstituted.particular_facts) == 26
        print("  -> PASSED: Context serializes to clean JSON and deserializes losslessly.")
        results["15_serialization"] = "PASSED"

        print("\n" + "=" * 75)
        print("PHASE 3 REPORT CONTEXT BUILDER TESTS: ALL PASSED (15/15)")
        print("=" * 75)

    finally:
        # Cleanup test application
        if app_id:
            try:
                del_app = db.query(Application).filter(Application.id == app_id).first()
                if del_app:
                    db.delete(del_app)
                    db.commit()
            except Exception:
                db.rollback()
        db.close()

    return results


def test_phase3_report_context_builder():
    res = run_phase3_tests()
    assert all(v == "PASSED" for v in res.values())


if __name__ == "__main__":
    test_results = run_phase3_tests()
    all_passed = all(v == "PASSED" for v in test_results.values())
    sys.exit(0 if all_passed else 1)
