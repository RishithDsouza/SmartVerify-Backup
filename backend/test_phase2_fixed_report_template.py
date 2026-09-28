"""
SmartVerify Phase 2: Fixed Report Template Automated Test Suite.
Validates that the machine-readable versioned report template (standard_reverification v1.0)
conforms strictly to the approved specification, hierarchy, row IDs, and deterministic ordering.
"""
import sys
import json
from pathlib import Path

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.report_templates import get_template, list_templates, TEMPLATE_REGISTRY
from app.report_templates.schema import ReportTemplate, ParticularItem


def test_phase2_report_template():
    print("\n" + "=" * 75)
    print("SMARTVERIFY PHASE 2: FIXED REPORT TEMPLATE TEST SUITE")
    print("=" * 75)

    # ─────────────────────────────────────────────────────────────────
    # Test 1: Template Loading & Registry Resolution
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 1] Testing Template Loading & Registry Resolution...")
    template = get_template("standard_reverification", "v1.0")
    assert template is not None
    assert template.template_key == "standard_reverification"
    assert template.template_version == "v1.0"
    print(f"  -> PASSED: Loaded template '{template.name}' [{template.template_key} {template.template_version}]")

    templates_list = list_templates()
    assert len(templates_list) >= 1
    assert any(t["template_key"] == "standard_reverification" and t["template_version"] == "v1.0" for t in templates_list)
    print("  -> PASSED: Registry lists standard_reverification v1.0 correctly.")

    # ─────────────────────────────────────────────────────────────────
    # Test 2: All Required Particular IDs Exist & Are Unique
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 2] Testing Presence of All Required Particular IDs...")
    EXPECTED_IDS = [
        "P1",
        "P2", "P2A", "P2B", "P2C", "P2D", "P2E",
        "P3",
        "P4", "P4A", "P4B", "P4C", "P4D",
        "P5", "P5A", "P5B", "P5C",
        "P6",
        "P7", "P7A", "P7A1", "P7A2", "P7B", "P7B1", "P7B2", "P7B3"
    ]

    actual_ids = [p.id for p in template.particulars]

    # Check uniqueness
    assert len(actual_ids) == len(set(actual_ids)), f"Duplicate row IDs found in template: {actual_ids}"
    print(f"  -> PASSED: All {len(actual_ids)} row IDs are strictly unique (no duplicates).")

    # Check exact membership
    missing_ids = [pid for pid in EXPECTED_IDS if pid not in actual_ids]
    assert len(missing_ids) == 0, f"Missing required Particular IDs: {missing_ids}"
    extra_ids = [pid for pid in actual_ids if pid not in EXPECTED_IDS]
    assert len(extra_ids) == 0, f"Unexpected extra Particular IDs: {extra_ids}"
    print(f"  -> PASSED: All {len(EXPECTED_IDS)} required Particular IDs exist with exact 1-to-1 match.")

    # ─────────────────────────────────────────────────────────────────
    # Test 3: Display Order Determinism
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 3] Testing Deterministic Display Ordering...")
    orders = [p.display_order for p in template.particulars]
    assert orders == list(range(1, len(EXPECTED_IDS) + 1)), f"Display orders not contiguous 1..{len(EXPECTED_IDS)}: {orders}"
    # Verify particulars list is pre-sorted by display_order
    sorted_particulars = sorted(template.particulars, key=lambda p: p.display_order)
    assert template.particulars == sorted_particulars
    print(f"  -> PASSED: Display order is strictly monotonic and contiguous (1..{len(EXPECTED_IDS)}).")

    # ─────────────────────────────────────────────────────────────────
    # Test 4: Parent / Child Relationship Integrity
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 4] Testing Parent/Child Hierarchy and Referential Integrity...")
    id_map = {p.id: p for p in template.particulars}

    expected_parents = {
        "P1": None,
        "P2": None,
        "P2A": "P2",
        "P2B": "P2",
        "P2C": "P2",
        "P2D": "P2",
        "P2E": "P2",
        "P3": None,
        "P4": None,
        "P4A": "P4",
        "P4B": "P4",
        "P4C": "P4",
        "P4D": "P4",
        "P5": None,
        "P5A": "P5",
        "P5B": "P5",
        "P5C": "P5",
        "P6": None,
        "P7": None,
        "P7A": "P7",
        "P7A1": "P7A",
        "P7A2": "P7A",
        "P7B": "P7",
        "P7B1": "P7B",
        "P7B2": "P7B",
        "P7B3": "P7B",
    }

    for item in template.particulars:
        exp_parent = expected_parents[item.id]
        assert item.parent_id == exp_parent, f"Particular {item.id} expected parent '{exp_parent}', got '{item.parent_id}'"
        if item.parent_id is not None:
            assert item.parent_id in id_map, f"Parent ID '{item.parent_id}' for item '{item.id}' does not exist in template!"

    # Verify children retrieval method
    p2_children = [c.id for c in template.get_children("P2")]
    assert p2_children == ["P2A", "P2B", "P2C", "P2D", "P2E"]
    p7a_children = [c.id for c in template.get_children("P7A")]
    assert p7a_children == ["P7A1", "P7A2"]
    p7b_children = [c.id for c in template.get_children("P7B")]
    assert p7b_children == ["P7B1", "P7B2", "P7B3"]
    print("  -> PASSED: All parent/child relationships and nested sub-items are structurally valid.")

    # ─────────────────────────────────────────────────────────────────
    # Test 5: Section Coverage (Sections 1 through 8)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 5] Testing Required Sections (1 through 7)...")
    sections_present = set(p.section for p in template.particulars)
    expected_sections = {"1", "2", "3", "4", "5", "6", "7"}
    assert sections_present == expected_sections, f"Sections mismatch: {sections_present} vs {expected_sections}"
    print(f"  -> PASSED: All 7 reference report sections exist in the template.")

    # ─────────────────────────────────────────────────────────────────
    # Test 6: Applicant & Guarantor Applicability Metadata
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 6] Testing Applicant / Guarantor Support Flags...")
    for p in template.particulars:
        assert isinstance(p.applicant_supported, bool)
        assert isinstance(p.guarantor_supported, bool)
        # Verify general sections (P1-P6) support both applicant and guarantor
        if p.section in ["1", "2", "3", "4", "5", "6"]:
            assert p.applicant_supported is True
            assert p.guarantor_supported is True
        # Verify vehicle/machinery collateral section (P7) applies to applicant/asset
        if p.section == "7":
            assert p.applicant_supported is True
            assert p.guarantor_supported is False
    print("  -> PASSED: Applicant and Guarantor support flags are properly mapped per banking domain.")

    # ─────────────────────────────────────────────────────────────────
    # Test 7: Controlled AI Narrative Flags
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 7] Testing Controlled Narrative Allowance Flags...")
    # Narrative allowed should only be True on specific qualitative / observation fields:
    # P2A (Observations), P2C (Reason if not visited), P4B (Remarks on genuineness),
    # P6 (Track Record / Criminal Antecedents), P7A2 (Genuineness of invoice)
    expected_narrative_ids = {"P2A", "P2C", "P4B", "P6", "P7A2"}
    actual_narrative_ids = set(p.id for p in template.particulars if p.narrative_allowed)
    assert actual_narrative_ids == expected_narrative_ids, (
        f"Narrative allowed mismatch: got {actual_narrative_ids}, expected {expected_narrative_ids}"
    )
    print(f"  -> PASSED: AI narrative wording is strictly restricted to designated observation fields ({len(actual_narrative_ids)} fields).")

    # ─────────────────────────────────────────────────────────────────
    # Test 8: Header and Footer Configuration
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 8] Testing Header & Footer Structural Specifications...")
    header_keys = [f.key for f in template.header.fields]
    expected_header_keys = [
        "organization_name", "report_title", "application_no",
        "borrower_name", "loan_type", "loan_amount", "branch", "report_date"
    ]
    for hk in expected_header_keys:
        assert hk in header_keys, f"Header missing key: {hk}"
    print("  -> PASSED: Header configuration contains all banking reference fields.")

    signatory_keys = [s.key for s in template.footer.signatories]
    assert "authorized_signatory" in signatory_keys
    assert "branch_head_scrutiny" in signatory_keys
    assert "report_prepared_by" in signatory_keys
    print("  -> PASSED: Footer signatories contain Investigating Official, Branch Head scrutiny, and Prepared By blocks.")

    # ─────────────────────────────────────────────────────────────────
    # Test 9: Evidence Category Specification
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 9] Testing Evidence Categories Specification...")
    cat_keys = [c.key for c in template.evidence_categories]
    expected_categories = [
        "applicant_kyc", "guarantor_kyc", "government_verification",
        "income_documents", "bank_documents", "residence_evidence",
        "vehicle_invoice", "dealer_evidence", "rc_evidence", "other_relevant_evidence"
    ]
    for ck in expected_categories:
        assert ck in cat_keys, f"Missing evidence category: {ck}"

    # Verify that particulars referencing evidence categories reference valid keys
    for p in template.particulars:
        if p.evidence_category:
            assert p.evidence_category in cat_keys, f"Particular {p.id} references invalid evidence category '{p.evidence_category}'"
    print(f"  -> PASSED: All {len(expected_categories)} evidence categories defined and referenced accurately.")

    # ─────────────────────────────────────────────────────────────────
    # Test 10: Serialization & Deserialization (Machine-Readable JSON)
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 10] Testing Full Template Serialization and Deserialization...")
    # Dump to dict and JSON string
    template_dict = template.model_dump()
    template_json = template.model_dump_json(indent=2)
    assert len(template_json) > 1000

    # Parse back
    reconstituted = ReportTemplate.model_validate_json(template_json)
    assert reconstituted.template_key == template.template_key
    assert reconstituted.template_version == template.template_version
    assert len(reconstituted.particulars) == len(template.particulars)
    assert [p.id for p in reconstituted.particulars] == actual_ids
    print("  -> PASSED: Template serializes to clean JSON and deserializes back losslessly.")

    print("\n" + "=" * 75)
    print("PHASE 2 REPORT TEMPLATE TESTS: ALL PASSED (10/10)")
    print("=" * 75)


if __name__ == "__main__":
    test_phase2_report_template()
