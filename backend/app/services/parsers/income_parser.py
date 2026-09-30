"""
Income Document Parser & Field Validator for SmartVerify
Extracts structured metadata (value, confidence, source, extraction_method, validation_status)
from raw OCR / PDF text for Salary Slips and Form 16 documents.
Strictly non-fabricating: returns None for fields that cannot be reliably extracted.
"""
import re
from typing import Dict, Any, Optional


def _clean_amount(amt_str: str) -> Optional[float]:
    """Parse cleaned monetary float value."""
    if not amt_str:
        return None
    try:
        clean = amt_str.replace(",", "").strip()
        # Handle OCR artifacts like extra dots: 4.200.00 -> 4200.00
        parts = clean.split(".")
        if len(parts) > 2:
            clean = "".join(parts[:-1]) + "." + parts[-1]
        val = float(clean)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def parse_salary_slip(text: str) -> Dict[str, Any]:
    """
    Parse key fields from salary slip text:
    - applicant_name / employee_name
    - employer_name
    - monthly_income (gross earnings or net pay)
    - net_salary
    - salary_period / pay_period
    - designation
    - pan_number
    """
    fields: Dict[str, Any] = {}
    if not text:
        return fields

    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # 1. Employer Name
    employer_val = None
    # Check explicit label first: e.g. "Fictional employer: Example Technologies Test Pvt. Ltd."
    m_emp = re.search(
        r"(?:Fictional\s+employer|Employer\s*(?:Name)?|Company\s*(?:Name)?)\s*[:\-\s]+([A-Za-z0-9\s\.\,\&]+(?:Pvt\.?\s*Ltd\.?|Private\s+Limited|Ltd\.?|Corp\.?|Inc\.?|LLC|Technologies|Solutions|Services))",
        text,
        re.IGNORECASE,
    )
    if m_emp:
        employer_val = m_emp.group(1).strip()
    else:
        # Search lines for corporate suffix
        for line in lines[:5]:
            if re.search(r"\b(Pvt\.?\s*Ltd\.?|Private\s+Limited|Limited|Technologies|Solutions)\b", line, re.I):
                clean_line = re.sub(r"[-–—]?\s*(?:MONTHLY\s+PAYSLIP|PAYSLIP|SALARY\s+SLIP).*", "", line, flags=re.I).strip()
                if clean_line and len(clean_line) >= 4:
                    employer_val = clean_line
                    break

    if employer_val:
        fields["employer_name"] = {
            "value": employer_val,
            "confidence": 0.90,
            "confidence_level": "high",
            "source_document": "salary_slip",
            "extraction_method": "employer_regex",
            "validation_status": "valid",
            "raw_match": employer_val,
        }

    # 2. Applicant / Employee Name
    name_val = None
    # Pattern A: "Employee Name:\n Sunil Kumar"
    for i, line in enumerate(lines):
        if re.search(r"^(?:Employee\s+Name|Name\s+of\s+Employee)\s*[:\-\s]*$", line, re.I) and i + 1 < len(lines):
            cand = lines[i + 1].strip()
            if re.match(r"^[A-Za-z\s\.\']{3,50}$", cand) and not re.search(r"(?:designation|department|employee|id|date)", cand, re.I):
                name_val = cand
                break
        m_name = re.search(r"(?:Employee\s+Name|Name\s+of\s+Employee)\s*[:\-\s]+([A-Za-z\s\.\']{3,50})", line, re.I)
        if m_name:
            cand = m_name.group(1).strip()
            if cand and not re.search(r"(?:designation|department|employee|id|date)", cand, re.I):
                name_val = cand
                break

    # Pattern B: Table format "Employee Name" | "Demo Renika"
    if not name_val:
        m_tbl = re.search(r"(?:Employee\s+Name|Applicant\s+Name)\s*[\n\|]+\s*([A-Za-z\s\.\']{3,50})", text, re.I)
        if m_tbl:
            cand = m_tbl.group(1).strip().split("\n")[0].strip()
            if cand and not re.search(r"(?:designation|department|employee|field|value)", cand, re.I):
                name_val = cand

    if name_val:
        fields["applicant_name"] = {
            "value": name_val,
            "confidence": 0.92,
            "confidence_level": "high",
            "source_document": "salary_slip",
            "extraction_method": "employee_name_regex",
            "validation_status": "valid",
            "raw_match": name_val,
        }

    # 3. Pay Period / Salary Month
    period_val = None
    m_period = re.search(
        r"(?:Pay\s+Period|Salary\s+(?:Month|Slip\s+for)|Period\s+Ending|Month)\s*[:\-\s]+([A-Za-z]+\s+\d{4}|\d{2}[/\-]\d{4})",
        text,
        re.IGNORECASE,
    )
    if m_period:
        period_val = m_period.group(1).strip()
    else:
        # Check first 5 lines for "Month Year" e.g. "July 2026", "August 2026"
        for line in lines[:5]:
            m_mo = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b", line, re.I)
            if m_mo:
                period_val = f"{m_mo.group(1).title()} {m_mo.group(2)}"
                break

    if period_val:
        fields["salary_period"] = {
            "value": period_val,
            "confidence": 0.92,
            "confidence_level": "high",
            "source_document": "salary_slip",
            "extraction_method": "period_regex",
            "validation_status": "valid",
            "raw_match": period_val,
        }

    # 4. Monthly Income / Gross Earnings / Net Pay
    # Prefer Gross Earnings; fall back to Net Pay
    gross_income = None
    gross_patterns = [
        r"(?:Gross\s+(?:Monthly\s+)?Earnings|Grccs\s+(?:Monthly\s+)?Earnings|Gross\s+(?:Salary|Pay)|Total\s+Earnings|Total\s+Gross)[\s:\n]+(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
        r"(?:Monthly\s+Gross|Gross\s+Salary)\s*[\n\|]+\s*(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
    ]
    for gp in gross_patterns:
        m_gr = re.search(gp, text, re.IGNORECASE)
        if m_gr:
            amt = _clean_amount(m_gr.group(1))
            if amt and amt > 1000:
                gross_income = amt
                break

    net_income = None
    net_patterns = [
        r"(?:Net\s+(?:Take\s+Home\s+)?Pay|Net\s+Salary|Take\s+Home\s+Pay)[\s:\n]+(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
        r"(?:Net\s+Pay|Net\s+Salary)\s*[\n\|]+\s*(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
    ]
    for np in net_patterns:
        m_net = re.search(np, text, re.IGNORECASE)
        if m_net:
            amt = _clean_amount(m_net.group(1))
            if amt and amt > 1000:
                net_income = amt
                break

    effective_income = gross_income or net_income
    if effective_income:
        fields["monthly_income"] = {
            "value": effective_income,
            "confidence": 0.95 if gross_income else 0.88,
            "confidence_level": "high",
            "source_document": "salary_slip",
            "extraction_method": "gross_earnings_regex" if gross_income else "net_pay_fallback",
            "validation_status": "valid",
            "raw_match": str(effective_income),
        }
    if net_income:
        fields["net_salary"] = {
            "value": net_income,
            "confidence": 0.92,
            "confidence_level": "high",
            "source_document": "salary_slip",
            "extraction_method": "net_pay_regex",
            "validation_status": "valid",
            "raw_match": str(net_income),
        }

    # 5. Designation / Department
    m_desig = re.search(
        r"(?:Designation\s*(?:/\s*Department)?|Role|Title)\s*[:\n]\s*([A-Za-z\s\,]{3,40})",
        text,
        re.IGNORECASE,
    )
    if m_desig:
        desig_val = m_desig.group(1).strip().split("\n")[0].strip(", ")
        if desig_val and len(desig_val) >= 3 and not re.search(r"(?:salary|date|earnings)", desig_val, re.I):
            fields["designation"] = {
                "value": desig_val,
                "confidence": 0.85,
                "confidence_level": "medium",
                "source_document": "salary_slip",
                "extraction_method": "designation_regex",
                "validation_status": "valid",
                "raw_match": desig_val,
            }

    # 6. PAN Number if present in salary slip
    m_pan = re.search(r"\b([A-Z]{5}\d{4}[A-Z])\b", text)
    if m_pan:
        fields["pan_number"] = {
            "value": m_pan.group(1),
            "confidence": 0.95,
            "confidence_level": "high",
            "source_document": "salary_slip",
            "extraction_method": "pan_regex",
            "validation_status": "valid",
            "raw_match": m_pan.group(1),
        }

    return fields


def parse_form_16(text: str) -> Dict[str, Any]:
    """
    Parse key fields from Form 16 text:
    - applicant_name / employee_name
    - employer_name
    - employer_tan
    - pan_number (employee PAN)
    - financial_year
    - assessment_year
    - annual_income / gross_salary
    - monthly_income (annual / 12)
    - tax_deducted
    """
    fields: Dict[str, Any] = {}
    if not text:
        return fields

    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # 1. Employer Name
    employer_val = None
    m_emp = re.search(
        r"(?:Employer\s+Name\s*(?:&|and)?\s*TAN|Fictional\s+employer)\s*[:\-\s\n]+([A-Za-z0-9\s\.\,\&]+(?:Pvt\.?\s*Ltd\.?|Private\s+Limited|Ltd\.?|Corp\.?|Inc\.?|Technologies|Solutions|Services))",
        text,
        re.IGNORECASE,
    )
    if m_emp:
        employer_val = m_emp.group(1).strip()
    else:
        for line in lines[:8]:
            if re.search(r"\b(Pvt\.?\s*Ltd\.?|Private\s+Limited|Limited|Technologies|Solutions)\b", line, re.I):
                clean_line = re.sub(r"^(?:Fictional\s+employer|Employer\s*Name)?[:\s]*", "", line, flags=re.I).strip()
                if clean_line and len(clean_line) >= 4:
                    employer_val = clean_line
                    break

    if employer_val:
        fields["employer_name"] = {
            "value": employer_val,
            "confidence": 0.92,
            "confidence_level": "high",
            "source_document": "form_16",
            "extraction_method": "employer_regex",
            "validation_status": "valid",
            "raw_match": employer_val,
        }

    # 2. Employer TAN
    m_tan = re.search(r"\b([A-Z]{4}\d{5}[A-Z])\b", text)
    if m_tan:
        fields["employer_tan"] = {
            "value": m_tan.group(1),
            "confidence": 0.95,
            "confidence_level": "high",
            "source_document": "form_16",
            "extraction_method": "tan_regex",
            "validation_status": "valid",
            "raw_match": m_tan.group(1),
        }

    # 3. Employee Name and PAN
    m_name_pan = re.search(
        r"(?:Employee\s+Name\s*(?:&|and)?\s*PAN)\s*[:\-\s\n]+([A-Za-z\s\.\']{3,50})(?:\s*[/|]\s*([A-Z]{5}\d{4}[A-Z]))?",
        text,
        re.IGNORECASE,
    )
    if m_name_pan:
        name_cand = m_name_pan.group(1).strip()
        pan_cand = m_name_pan.group(2)
        if name_cand and not re.search(r"(?:salary|date|pan|tan)", name_cand, re.I):
            fields["applicant_name"] = {
                "value": name_cand,
                "confidence": 0.92,
                "confidence_level": "high",
                "source_document": "form_16",
                "extraction_method": "employee_name_regex",
                "validation_status": "valid",
                "raw_match": name_cand,
            }
        if pan_cand:
            fields["pan_number"] = {
                "value": pan_cand,
                "confidence": 0.98,
                "confidence_level": "high",
                "source_document": "form_16",
                "extraction_method": "pan_regex",
                "validation_status": "valid",
                "raw_match": pan_cand,
            }

    # Fallback for employee PAN if not captured above
    if "pan_number" not in fields:
        all_pans = re.findall(r"\b([A-Z]{5}\d{4}[A-Z])\b", text)
        for pan in all_pans:
            # Skip if same as TAN (TAN is 4 letters, PAN is 5 letters, so regex handles this)
            fields["pan_number"] = {
                "value": pan,
                "confidence": 0.92,
                "confidence_level": "high",
                "source_document": "form_16",
                "extraction_method": "pan_regex",
                "validation_status": "valid",
                "raw_match": pan,
            }
            break

    # 4. Financial Year & Assessment Year
    m_fy = re.search(r"(?:Financial\s+Year|FY)\s*[:\-\s]*(\d{4}[-\s]?\d{2,4})", text, re.I)
    if m_fy:
        fields["financial_year"] = {
            "value": m_fy.group(1).strip().replace(" ", "-"),
            "confidence": 0.95,
            "confidence_level": "high",
            "source_document": "form_16",
            "extraction_method": "fy_regex",
            "validation_status": "valid",
            "raw_match": m_fy.group(1),
        }

    m_ay = re.search(r"(?:Assessment\s+Year|AY)\s*[:\-\s]*(\d{4}[-\s]?\d{2,4})", text, re.I)
    if m_ay:
        fields["assessment_year"] = {
            "value": m_ay.group(1).strip().replace(" ", "-"),
            "confidence": 0.95,
            "confidence_level": "high",
            "source_document": "form_16",
            "extraction_method": "ay_regex",
            "validation_status": "valid",
            "raw_match": m_ay.group(1),
        }

    # 5. Gross Salary / Total Income
    annual_income = None
    gross_patterns = [
        r"(?:Grccs\s+Salary|Gross\s+Salary|Total\s+(?:Gross\s+)?Salary|Gross\s+Total\s+Income)[^\d\n\r]*(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
        r"(?:Gross\s+Salary|Total\s+Income)\s*[\n\|]+\s*(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
    ]
    for gp in gross_patterns:
        m_gr = re.search(gp, text, re.IGNORECASE)
        if m_gr:
            amt = _clean_amount(m_gr.group(1))
            if amt and amt > 10000:
                annual_income = amt
                break

    if annual_income:
        monthly_computed = round(annual_income / 12.0, 2)
        fields["annual_income"] = {
            "value": annual_income,
            "confidence": 0.95,
            "confidence_level": "high",
            "source_document": "form_16",
            "extraction_method": "gross_salary_regex",
            "validation_status": "valid",
            "raw_match": str(annual_income),
        }
        fields["monthly_income"] = {
            "value": monthly_computed,
            "confidence": 0.90,
            "confidence_level": "high",
            "source_document": "form_16",
            "extraction_method": "annual_divided_by_12",
            "validation_status": "valid",
            "raw_match": f"{annual_income} / 12 = {monthly_computed}",
        }

    # 6. Tax Deducted
    m_tax = re.search(r"(?:Total\s+Income\s+Tax\s+Deducted|Tax\s+Deducted|TDS\s+Deducted)[^\d\n\r]*(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)", text, re.I)
    if m_tax:
        tax_amt = _clean_amount(m_tax.group(1))
        if tax_amt:
            fields["tax_deducted"] = {
                "value": tax_amt,
                "confidence": 0.90,
                "confidence_level": "high",
                "source_document": "form_16",
                "extraction_method": "tax_deducted_regex",
                "validation_status": "valid",
                "raw_match": str(tax_amt),
            }

    return fields
