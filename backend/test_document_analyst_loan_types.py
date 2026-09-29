import pytest
from unittest.mock import MagicMock
from app.agents.tasks import get_loan_document_rules, LOAN_TYPE_DOCUMENT_RULES, build_tasks
from app.agents.tools import OCRTool


def test_get_loan_document_rules_vehicle_loan():
    rules = get_loan_document_rules("Vehicle Loan")
    assert "vehicle_document" in rules["required_collateral"]
    assert "invoice" in rules["required_collateral"]
    assert "aadhaar" in rules["required_identity"]
    assert "pan" in rules["required_identity"]
    assert "RC" in rules["checklist_text"]
    assert "Vehicle Loan" in rules["checklist_text"]


def test_get_loan_document_rules_home_loan():
    rules = get_loan_document_rules("Home Loan")
    assert "sale_deed" in rules["required_collateral"]
    assert "aadhaar" in rules["required_identity"]
    assert "pan" in rules["required_identity"]
    assert "Home Loan" in rules["checklist_text"]


def test_get_loan_document_rules_personal_and_business_and_fallback():
    personal_rules = get_loan_document_rules("Personal Loan")
    assert "aadhaar" in personal_rules["required_identity"]
    assert "Personal Loan" in personal_rules["checklist_text"]

    business_rules = get_loan_document_rules("Business Loan")
    assert "pan" in business_rules["required_identity"]
    assert "Business Loan" in business_rules["checklist_text"]

    education_rules = get_loan_document_rules("Education Loan")
    assert "aadhaar" in education_rules["required_identity"]
    assert "Education Loan" in education_rules["checklist_text"]

    fallback_rules = get_loan_document_rules("Unknown Custom Loan")
    assert "aadhaar" in fallback_rules["required_identity"]
    assert "Unknown Custom Loan" in fallback_rules["checklist_text"]


def test_ocr_tool_unextracted_returns_informative_status():
    tool = OCRTool()
    result = tool._run("/non_existent/demo_document_path.pdf")
    assert "NO_TEXT_EXTRACTED" in result
    assert "NOT that the underlying document is unreadable" in result
    assert "Rely on authoritative structured_data" in result


def test_build_tasks_vehicle_loan_application_6_scenario():
    # Mock agents dict (CrewAI Task accepts None for agent)
    agents = {
        "document_analyst": None,
        "extraction_specialist": None,
        "verification_officer": None,
        "gov_verification_agent": None,
        "compliance_reporter": None,
    }

    # 9 demo documents for Application 6
    doc_specs = [
        {"id": 11, "type": "aadhaar", "file_name": "aadhaar.pdf", "structured": {"id_number": "123456789012"}},
        {"id": 12, "type": "pan", "file_name": "pan.pdf", "structured": {"pan": "ABCDE1234F"}},
        {"id": 13, "type": "salary_slip", "file_name": "salary.pdf", "structured": {"monthly_salary": 100000}},
        {"id": 14, "type": "form_16", "file_name": "form16.pdf", "structured": {"gross_salary": 1200000}},
        {"id": 15, "type": "bank_statement", "file_name": "bank.pdf", "structured": {"average_balance": 250000}},
        {"id": 16, "type": "itr", "file_name": "itr.pdf", "structured": {"total_income": 1200000}},
        {"id": 9, "type": "vehicle_document", "file_name": "rc.pdf", "structured": {"reg_number": "MH01AB1234"}},
        {"id": 10, "type": "invoice", "file_name": "invoice.pdf", "structured": {"invoice_amount": 950000}},
        {"id": 83, "type": "site_front_view", "file_name": "site_front.jpg", "structured": {"site_verified": True}},
    ]

    docs_list = []
    for spec in doc_specs:
        docs_list.append({
            "id": spec["id"],
            "document_type": spec["type"],
            "original_name": spec["file_name"],
            "file_path": f"/demo-evidence/{spec['file_name']}",
            "extracted_text": None,  # Raw OCR text not in DB or unextracted
            "structured_data": spec["structured"],
        })

    run_context = {
        "application_id": 6,
        "applicant_name": "Vikram Aditya Sharma",
        "loan_type": "Vehicle Loan",
        "loan_amount": 850000.0,
        "loan_tenure": 60,
        "documents": docs_list,
        "shared_context_summary": "Demo summary",
    }

    tasks = build_tasks(
        agents=agents,
        run_context=run_context,
    )

    assert len(tasks) == 5
    task1 = tasks[0]

    # Description checks
    desc = task1.description
    assert "Vehicle Loan" in desc
    assert "DISTINGUISH OCR UNAVAILABILITY FROM DOCUMENT UNREADABILITY" in desc
    assert "Do NOT mark it as unreadable or in unreadable_documents" in desc
    assert "site_front_view" in desc
    assert "visual evidence of the physical property/site inspection" in desc
    assert "vehicle_document" in desc
    assert "invoice" in desc

    # Verify doc metadata in description:
    # 1. No document should be marked as "quality=unreadable" when structured_data is present
    assert "quality=unreadable" not in desc
    # 2. site_front_view should be classified as visual_evidence
    assert "visual_evidence (photographic site/property evidence; text OCR not applicable)" in desc
    # 3. Documents with structured data should be classified as verified_structured_data_available
    assert "verified_structured_data_available" in desc
