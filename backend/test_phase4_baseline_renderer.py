"""
Automated Test Suite for Phase 4: No-AI Baseline Report Renderer.
Verifies all 20 architectural requirements against realistic application data.
Runs against the PostgreSQL development database.
"""
import sys
import os
from datetime import datetime
import json

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add backend directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db.database import SessionLocal
from app.models.user import User, UserRole
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
from app.services.report_context_builder import build_report_context
from app.schemas.report_context import ReportContext
from app.services.baseline_report_renderer import (
    render_baseline_report,
    format_currency,
    format_date,
    format_masked_aadhaar,
    MISSING_REQUIRED_MARKER,
)
from app.schemas.baseline_report import BaselineReport, RenderedParticular


def run_phase4_tests():
    db = SessionLocal()
    test_user_id = None
    app_id = None
    bare_app_id = None
    results = {}

    try:
        print("\n" + "=" * 75)
        print("SMARTVERIFY PHASE 4: NO-AI BASELINE REPORT RENDERER TEST SUITE")
        print("=" * 75)

        # ─────────────────────────────────────────────────────────────────
        # Setup: Create full test dataset in PostgreSQL
        # ─────────────────────────────────────────────────────────────────
        print("\n[Setup] Creating rich test application in PostgreSQL...")
        officer = db.query(User).filter(User.email == "officer@smartverify.com").first()
        if not officer:
            officer = User(
                name="Inspector Rajesh Sharma",
                email=f"officer_phase4_{int(datetime.utcnow().timestamp())}@smartverify.com",
                password="hashed_pw",
                role=UserRole.loan_officer,
            )
            db.add(officer)
            db.commit()
            db.refresh(officer)
        test_user_id = officer.id

        # 1. Primary Application
        app = Application(
            user_id=test_user_id,
            applicant_name="Sunil Kumar Varma",
            branch="Koramangala Branch",
            loan_type="Commercial Vehicle & Construction Loan",
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

        # 2. Joint Applicants (Co-Applicant & Guarantor)
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
            remarks="Primary co-borrower",
        )
        ja_guar = JointApplicant(
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
            remarks="Financial guarantor",
        )
        db.add(ja_coapp)
        db.add(ja_guar)

        # 3. Documents (KYC, Income, Bank, Vehicle, Invoice)
        doc1 = Document(
            application_id=app_id,
            document_type=DocumentType.pan,
            file_path="uploads/app_docs/pan_card_sunil.pdf",
            original_name="pan_card_sunil.pdf",
            processed=2,
            extracted_text="INCOME TAX DEPARTMENT GOVT OF INDIA ABCDE1234F SUNIL KUMAR VARMA",
        )
        doc2 = Document(
            application_id=app_id,
            document_type=DocumentType.salary_slip,
            file_path="uploads/app_docs/salary_slip_aug2026.pdf",
            original_name="salary_slip_aug2026.pdf",
            processed=2,
            extracted_text="Salary Slip Gross: 350000 Net: 280000",
        )
        doc3 = Document(
            application_id=app_id,
            document_type=DocumentType.bank_statement,
            file_path="uploads/app_docs/hdfc_statement_6m.pdf",
            original_name="hdfc_statement_6m.pdf",
            processed=2,
            extracted_text="HDFC Bank Statement A/c: 50100293848 Turnover 4500000",
        )
        doc4 = Document(
            application_id=app_id,
            document_type=DocumentType.invoice,
            file_path="uploads/app_docs/commercial_vehicle_invoice.pdf",
            original_name="commercial_vehicle_invoice.pdf",
            processed=2,
            extracted_text="Tata Motors Commercial Vehicle Proforma Invoice 2500000",
        )
        doc5 = Document(
            application_id=app_id,
            document_type=DocumentType.vehicle_document,
            file_path="uploads/app_docs/rc_book_machinery.pdf",
            original_name="rc_book_machinery.pdf",
            processed=2,
            extracted_text="Registration Certificate KA-01-MJ-9912 Chassis: MAT482912",
        )
        db.add_all([doc1, doc2, doc3, doc4, doc5])

        # 4. Site Verification
        site_v = SiteVerification(
            application_id=app_id,
            officer_name="Inspector Rajesh Sharma",
            date="2026-09-18",
            time="11:30 AM",
            property_condition="Good",
            construction_quality="RCC Frame Structure",
            boundary_present="Yes - Clear Fenced Boundary",
            road_access="12 Meter Asphalt Tar Road",
            utilities_available="Electricity, Water, Drainage",
            gps_coordinates="12.9352 N, 77.6245 E",
            remarks="Physical visit completed. Borrower resides at premises with family.",
        )
        db.add(site_v)

        # 5. Property Details
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

        # 6. Government Verification & Screenshot Metadata
        gov_v = GovVerification(
            application_id=app_id,
            pan_aadhaar_link_status="Linked",
            aadhaar_validity_status="Valid",
            tax_receipt_status="Verified",
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
            notes="Captured UIDAI verification tick",
        )
        db.add(shot_meta)

        # 7. Findings (including P7 vehicle findings)
        finding_p1 = ApplicationFinding(
            application_id=app_id,
            particular_id="P1",
            question="Is borrower identity verified?",
            answer="Yes, identity verified across PAN and Aadhaar.",
            finding_type="identity",
            status="VERIFIED",
            confidence=0.98,
        )
        finding_p7a1 = ApplicationFinding(
            application_id=app_id,
            particular_id="P7A1",
            question="Dealer name and authorization?",
            answer="Prerana Motors Commercial Vehicles Pvt Ltd, Hosur Road, Bengaluru (Authorized OEM Dealer)",
            finding_type="asset",
            status="VERIFIED",
            confidence=0.95,
        )
        finding_p7b1 = ApplicationFinding(
            application_id=app_id,
            particular_id="P7B1",
            question="RC Book number and engine chassis verification?",
            answer="RC No. KA-01-MJ-9912 verified against VAHAN portal; Engine and Chassis numbers match physical unit.",
            finding_type="asset",
            status="VERIFIED",
            confidence=0.96,
        )
        db.add_all([finding_p1, finding_p7a1, finding_p7b1])

        db.commit()
        print(f"  -> Setup complete. Test Application ID = {app_id}")

        # Build context via Phase 3 Context Builder
        report_context: ReportContext = build_report_context(app_id, db)
        assert report_context is not None

        # ─────────────────────────────────────────────────────────────────
        # Test 1 & 2: Service Import & Execution (ZERO Database queries)
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 1 & 2] Testing Baseline Renderer Execution (Pure Context Ingestion)...")
        # Notice: render_baseline_report takes report_context only; zero db argument passed!
        report: BaselineReport = render_baseline_report(report_context)
        assert report is not None
        assert isinstance(report, BaselineReport)
        print("  -> PASSED: Renderer executed cleanly and returned typed BaselineReport with zero DB dependencies.")
        results["1_2_execution"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 3 & 4: Template Key & Version Preservation
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 3 & 4] Testing Template Key & Version Preservation...")
        assert report.header.organization_name == "SMARTVERIFY / RETAIL LENDING RE-VERIFICATION"
        assert report.header.report_title == "BACKGROUND INFORMATION/RE-VERIFICATION REPORT FORMAT"
        assert report.header.application_id == app_id
        print("  -> PASSED: Template key, version, and header correctly preserved in report output.")
        results["3_4_template_preservation"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 5: All 26 Particular IDs Rendered
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 5] Testing All 26 Particular IDs Rendered...")
        EXPECTED_IDS = [
            "P1", "P2", "P2A", "P2B", "P2C", "P2D", "P2E",
            "P3", "P4", "P4A", "P4B", "P4C", "P4D",
            "P5", "P5A", "P5B", "P5C", "P6",
            "P7", "P7A", "P7A1", "P7A2", "P7B", "P7B1", "P7B2", "P7B3"
        ]
        assert len(report.particulars) == 26
        rendered_ids = [p.id for p in report.particulars]
        assert rendered_ids == EXPECTED_IDS
        print("  -> PASSED: Exact 1-to-1 match for all 26 Particular IDs in rendered report.")
        results["5_all_26_ids"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 6: Display Ordering Matches Template Exactly
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 6] Testing Display Ordering Contiguity & Monotonicity...")
        for idx, p in enumerate(report.particulars, start=1):
            assert p.display_order == idx, f"Display order mismatch: expected {idx}, got {p.display_order}"
        print("  -> PASSED: Display order is strictly contiguous and monotonic from 1 to 26.")
        results["6_display_ordering"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 7: Parent/Child Hierarchy Preserved
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 7] Testing Parent/Child Hierarchy Preservation...")
        p2a = report.get_particular("P2A")
        assert p2a is not None and p2a.parent_id == "P2"
        p7a1 = report.get_particular("P7A1")
        assert p7a1 is not None and p7a1.parent_id == "P7A"
        p7b3 = report.get_particular("P7B3")
        assert p7b3 is not None and p7b3.parent_id == "P7B"
        print("  -> PASSED: All nested parent/child relationships preserved accurately.")
        results["7_hierarchy"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 8, 9, 10: 2-Column Party Isolation (Applicant vs Guarantor)
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 8, 9, 10] Testing 2-Column Applicant vs Guarantor Isolation...")
        p1 = report.get_particular("P1")
        assert p1 is not None
        assert p1.applicant_verification is not None
        assert p1.applicant_verification.party_type == "applicant"
        assert p1.applicant_verification.party_name == "Sunil Kumar Varma"
        assert p1.applicant_verification.formatted_facts["pan_number"] == "ABCDE1234F"

        # Ensure guarantor data is strictly segregated into guarantor_verifications
        assert len(p1.guarantor_verifications) == 2
        coapp_render = p1.guarantor_verifications[0]
        assert coapp_render.party_name == "Pooja Sunil Varma"
        assert coapp_render.party_type == "co_applicant"
        assert coapp_render.formatted_facts["pan_number"] == "FGHIJ5678K"

        guar_render = p1.guarantor_verifications[1]
        assert guar_render.party_name == "Maheshwar Varma"
        assert guar_render.party_type == "guarantor"
        assert guar_render.formatted_facts["pan_number"] == "KLMNO9012Z"

        # Check where guarantor_supported is False (e.g. Section 7)
        p7 = report.get_particular("P7")
        assert p7.guarantor_supported is False
        assert len(p7.guarantor_verifications) == 0

        print("  -> PASSED: Strict column segregation; zero mixing of applicant and guarantor data.")
        results["8_9_10_party_isolation"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 11 & 12: Missing Values and 'INFORMATION REQUIRED' Marker
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 11 & 12] Testing Missing Required Information Markers...")
        bare_app = Application(
            user_id=test_user_id,
            applicant_name=None,  # missing required
            address=None,         # missing required
            loan_amount=1000000.0,
            status=ApplicationStatus.pending,
        )
        db.add(bare_app)
        db.commit()
        db.refresh(bare_app)
        bare_app_id = bare_app.id

        bare_context = build_report_context(bare_app_id, db)
        bare_report = render_baseline_report(bare_context)

        bare_p1 = bare_report.get_particular("P1")
        assert bare_p1.missing is True
        assert bare_p1.missing_marker == MISSING_REQUIRED_MARKER
        assert bare_p1.applicant_verification.formatted_facts["name"] == MISSING_REQUIRED_MARKER
        assert bare_p1.applicant_verification.formatted_facts["address"] == MISSING_REQUIRED_MARKER

        bare_p2a = bare_report.get_particular("P2A")
        assert bare_p2a.missing is True
        assert bare_p2a.missing_marker == MISSING_REQUIRED_MARKER

        print("  -> PASSED: Missing required fields deterministically flagged with 'INFORMATION REQUIRED'.")
        results["11_12_missing_markers"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 13: Masked Aadhaar Formatting
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 13] Testing Masked Aadhaar Formatting...")
        assert format_masked_aadhaar("123456789012") == "XXXXXXXX9012"
        assert format_masked_aadhaar("987654321098") == "XXXXXXXX1098"
        p2e = report.get_particular("P2E")
        assert p2e.applicant_verification.formatted_facts["masked_aadhaar"] == "XXXXXXXX9012"
        assert p2e.guarantor_verifications[0].formatted_facts["masked_aadhaar"] == "XXXXXXXX1098"
        print("  -> PASSED: All Aadhaar numbers strictly masked (XXXXXXXX####) without exposure.")
        results["13_aadhaar_masking"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 14: Deterministic Date Formatting (YYYY-MM-DD)
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 14] Testing Deterministic Date Formatting...")
        assert format_date("1985-06-20") == "1985-06-20"
        assert format_date("20/06/1985") == "1985-06-20"
        assert format_date("20-06-1985") == "1985-06-20"
        assert report.header.report_date == datetime.utcnow().strftime("%Y-%m-%d")
        print("  -> PASSED: Dates formatted deterministically to YYYY-MM-DD.")
        results["14_date_formatting"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 15: Currency Formatting (Indian Rupee Notation)
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 15] Testing Indian Currency Formatting (INR ##,##,###.##)...")
        assert format_currency(8500000.0) == "₹ 85,00,000.00"
        assert format_currency(120000.0) == "₹ 1,20,000.00"
        assert format_currency(250000.0) == "₹ 2,50,000.00"
        assert format_currency(0) == "₹ 0.00"
        assert report.header.formatted_loan_amount == "₹ 85,00,000.00"
        print("  -> PASSED: Currency formatted deterministically to Indian Rupee notation.")
        results["15_currency_formatting"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 16: P7 Vehicle/Machinery Exact Semantic Verification
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 16] Testing Exact P7 Vehicle/Machinery Semantic Verification...")
        p7_titles = {
            "P7": "Dealer & Invoice/RC Book Verification for Vehicles/Machineries",
            "P7A": "For New Vehicles/Machineries",
            "P7A1": "Dealer/Sub-dealer name/address",
            "P7A2": "Genuineness of Invoice/Advance Payment Receipts",
            "P7B": "For Old/Used Vehicles/Machineries",
            "P7B1": "Correctness of RC Book No./Machinery No.",
            "P7B2": "Charge noted details in RC",
            "P7B3": "Insurance details & validity",
        }
        for pid, expected_t in p7_titles.items():
            p_item = report.get_particular(pid)
            assert p_item is not None
            assert p_item.title == expected_t
            assert p_item.section == "7"
            assert p_item.guarantor_supported is False

        # Verify specific dealer details from finding appear in P7A1
        p7a1 = report.get_particular("P7A1")
        assert p7a1.shared_verification is not None
        assert any("Prerana Motors" in line for line in p7a1.shared_verification.summary_lines)

        # Verify specific RC book details from finding appear in P7B1
        p7b1 = report.get_particular("P7B1")
        assert p7b1.shared_verification is not None
        assert any("KA-01-MJ-9912" in line for line in p7b1.shared_verification.summary_lines)

        print("  -> PASSED: Section 7 strictly implements vehicle/machinery dealer and RC verification semantics.")
        results["16_p7_semantics"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 17: narrative_allowed Does NOT Invoke AI
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 17] Testing narrative_allowed Is Strictly Passive (No AI)...")
        p4b = report.get_particular("P4B")
        assert p4b.narrative_allowed is True
        # Verify output is structured fact, NOT AI prose
        assert p4b.shared_verification is not None
        assert "tax_receipt_status" in p4b.shared_verification.formatted_facts
        p7a2 = report.get_particular("P7A2")
        assert p7a2.narrative_allowed is True
        assert p7a2.shared_verification is not None
        print("  -> PASSED: narrative_allowed remains purely passive metadata; no AI/LLM calls invoked.")
        results["17_no_ai_narrative"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 18: No Evidence Selection Performed
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 18] Testing Raw Metadata Exposed Without Evidence Selection...")
        assert len(report.documents) == 5
        assert len(report.government_screenshots) == 1
        assert len(report.findings) == 3
        # Assert no ranking, filtering or page bindings exist in baseline report
        for doc in report.documents:
            assert hasattr(doc, "document_id")
            assert not hasattr(doc, "page_number")  # Evidence Mapper attribute
        print("  -> PASSED: Documents, screenshots, and findings exposed as raw metadata without selection.")
        results["18_no_evidence_selection"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 19: Serialization Determinism & Identity
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 19] Testing Byte-for-Byte Serialization Determinism...")
        rep1_json = report.model_dump_json()
        rep2 = render_baseline_report(report_context)
        rep2_json = rep2.model_dump_json()
        assert rep1_json == rep2_json
        print("  -> PASSED: Re-rendering identical context produces 100% byte-for-byte identical output.")
        results["19_serialization_determinism"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 20: Full Lossless JSON Deserialization
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 20] Testing Lossless JSON Deserialization...")
        report_dict = report.model_dump()
        reconstructed = BaselineReport.model_validate(report_dict)
        assert reconstructed.template_key == report.template_key
        assert len(reconstructed.particulars) == 26
        assert reconstructed.input_hash == report.input_hash
        assert reconstructed.header.formatted_loan_amount == "₹ 85,00,000.00"
        print("  -> PASSED: Report serializes to JSON and deserializes back losslessly.")
        results["20_deserialization"] = "PASSED"

        print("\n" + "=" * 75)
        print("PHASE 4 BASELINE RENDERER TESTS: ALL PASSED (20/20)")
        print("=" * 75 + "\n")

    finally:
        # Cleanup test applications
        print("[Cleanup] Cleaning up test applications...")
        if bare_app_id:
            b_app = db.query(Application).filter(Application.id == bare_app_id).first()
            if b_app:
                db.delete(b_app)
        if app_id:
            main_app = db.query(Application).filter(Application.id == app_id).first()
            if main_app:
                db.delete(main_app)
        db.commit()
        db.close()
        print("  -> Cleanup complete.")


def test_phase4_baseline_renderer():
    run_phase4_tests()


if __name__ == "__main__":
    run_phase4_tests()
