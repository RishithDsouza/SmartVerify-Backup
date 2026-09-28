"""
Generate authentic institutional sample demo evidence images for SmartVerify.
Generates:
- demo-evidence/gov/aadhaar.png (UIDAI Portal Verification)
- demo-evidence/gov/pan-aadhaar.png (Income Tax Department e-Filing Portal)
- demo-evidence/demo_aadhaar_sunil.png (Applicant Masked Aadhaar Card)
- demo-evidence/demo_pan_sunil.png (Applicant PAN Card)
- demo-evidence/demo_salary_slip_aug_2026.png (Salary Slip)
- demo-evidence/demo_form16_2025_26.png (Form 16 Tax Certificate)
- demo-evidence/demo_bank_statement.png (Bank Statement)
- demo-evidence/demo_residence_proof.png (Physical Residence Verification Photo)
- demo-evidence/demo_vehicle_rc.png (Registration Certificate)
- demo-evidence/demo_vehicle_invoice.png (Vehicle Proforma Invoice)
"""
import os
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.join(BASE_DIR, "demo-evidence")
GOV_DIR = os.path.join(DEMO_DIR, "gov")

os.makedirs(GOV_DIR, exist_ok=True)

def create_card_image(filename, title, subtitle, details, badge_text="VERIFIED", width=800, height=480, primary_color=(30, 58, 138), border_color=(203, 213, 225)):
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Top banner
    draw.rectangle([(0, 0), (width, 60)], fill=primary_color)
    draw.text((24, 16), title, fill=(255, 255, 255))
    draw.text((24, 38), subtitle, fill=(226, 232, 240))

    # Badge
    draw.rectangle([(width - 160, 16), (width - 24, 46)], fill=(5, 150, 105))
    draw.text((width - 130, 24), badge_text, fill=(255, 255, 255))

    # Outer border
    draw.rectangle([(0, 0), (width - 1, height - 1)], outline=border_color, width=2)

    # Content box
    draw.rectangle([(20, 80), (width - 20, height - 20)], fill=(248, 250, 252), outline=border_color, width=1)

    y = 100
    for label, val in details:
        draw.text((40, y), label, fill=(100, 116, 139))
        draw.text((260, y), val, fill=(15, 23, 42))
        y += 38

    # Watermark / footer
    draw.line([(40, height - 40), (width - 40, height - 40)], fill=(226, 232, 240), width=1)
    draw.text((40, height - 32), "CONFIDENTIAL - SMARTVERIFY AUDITED VERIFICATION DOSSIER", fill=(148, 163, 184))

    img.save(filename, "PNG")
    print(f"Created {filename}")

# 1. UIDAI Aadhaar Screenshot
create_card_image(
    os.path.join(GOV_DIR, "aadhaar.png"),
    "UNIQUE IDENTIFICATION AUTHORITY OF INDIA (UIDAI)",
    "Resident Portal - Online Aadhaar Verification Service",
    [
        ("Verification Ref:", "DEMO-UIDAI-001"),
        ("Aadhaar Number:", "XXXXXXXX9999 (Masked)"),
        ("Citizen Name:", "Sunil Kumar"),
        ("Gender / Year of Birth:", "Male / 1999"),
        ("State / Pincode:", "Karnataka / 575001"),
        ("Mobile Linked:", "Yes (Verified via OTP)"),
        ("Verification Status:", "RECORD ACTIVE & VALID"),
        ("Portal Timestamp:", "20-Sep-2026 12:15:22 IST"),
    ],
    primary_color=(24, 66, 138),
)

# 2. PAN-Aadhaar Link Screenshot
create_card_image(
    os.path.join(GOV_DIR, "pan-aadhaar.png"),
    "INCOME TAX DEPARTMENT - GOVERNMENT OF INDIA",
    "e-Filing Portal - PAN-Aadhaar Link Scrutiny Status",
    [
        ("Verification Ref:", "DEMO-PAN-001"),
        ("Permanent Account Number:", "ABCDE1234F"),
        ("Taxpayer Name:", "SUNIL KUMAR"),
        ("Aadhaar Number:", "XXXXXXXX9999 (Linked)"),
        ("Linkage Status:", "PAN is operative and linked to Aadhaar"),
        ("Jurisdiction / Category:", "ITO Ward 1(1), Mangalore / Individual"),
        ("Authentication Status:", "CONFIRMED & VALIDATED"),
        ("Timestamp:", "20-Sep-2026 12:16:04 IST"),
    ],
    primary_color=(15, 76, 129),
)

# 3. Aadhaar Document Card
create_card_image(
    os.path.join(DEMO_DIR, "demo_aadhaar_sunil.png"),
    "GOVERNMENT OF INDIA - AADHAAR IDENTITY CARD",
    "Applicant KYC Document - Formal Address & Identity Proof",
    [
        ("Cardholder Name:", "Sunil Kumar"),
        ("Father's Name:", "Kumar Kumar"),
        ("Date of Birth:", "15/08/1999"),
        ("Aadhaar Number:", "XXXXXXXX9999"),
        ("Residential Address:", "24 MG Road, Hampankatta, Mangalore - 575001"),
        ("Document Status:", "Submitted & OCR Authenticated"),
    ],
    primary_color=(180, 83, 9),
)

# 4. PAN Document Card
create_card_image(
    os.path.join(DEMO_DIR, "demo_pan_sunil.png"),
    "INCOME TAX DEPARTMENT - PERMANENT ACCOUNT NUMBER CARD",
    "Applicant KYC Document - Taxpayer Identification",
    [
        ("Name:", "Sunil Kumar"),
        ("Father's Name:", "Kumar Kumar"),
        ("PAN:", "ABCDE1234F"),
        ("Date of Birth:", "15/08/1999"),
        ("Document Type:", "e-PAN Verified Card"),
    ],
    primary_color=(14, 116, 144),
)

# 5. Salary Slip
create_card_image(
    os.path.join(DEMO_DIR, "demo_salary_slip_aug_2026.png"),
    "TECH SOLUTIONS INDIA PVT LTD - MONTHLY PAYSLIP",
    "Pay Period: August 2026 | Employee ID: TS-8842",
    [
        ("Employee Name:", "Sunil Kumar"),
        ("Designation / Department:", "Senior Software Engineer / R&D"),
        ("Basic Salary + HRA:", "Rs. 50,000.00"),
        ("Special Allowances:", "Rs. 25,000.00"),
        ("Gross Monthly Earnings:", "Rs. 75,000.00"),
        ("Total Deductions (PF/PT):", "Rs. 4,200.00"),
        ("Net Take Home Pay:", "Rs. 70,800.00"),
    ],
    primary_color=(4, 120, 87),
)

# 6. Form 16
create_card_image(
    os.path.join(DEMO_DIR, "demo_form16_2025_26.png"),
    "FORM NO. 16 - CERTIFICATE UNDER SECTION 203 OF INCOME TAX ACT",
    "Assessment Year: 2026-27 | Financial Year: 2025-26",
    [
        ("Employer Name & TAN:", "Tech Solutions India Pvt Ltd / BLRT12345C"),
        ("Employee Name & PAN:", "Sunil Kumar / ABCDE1234F"),
        ("Gross Salary u/s 17(1):", "Rs. 9,00,000.00"),
        ("Standard Deduction u/s 16:", "Rs. 50,000.00"),
        ("Total Income Tax Deducted:", "Rs. 48,500.00"),
        ("TDS Deposited Status:", "Quarterly TRACES Challans Matched & Clear"),
    ],
    primary_color=(124, 58, 237),
)

# 7. Bank Statement
create_card_image(
    os.path.join(DEMO_DIR, "demo_bank_statement.png"),
    "STATE BANK OF INDIA - SAVINGS BANK ACCOUNT STATEMENT",
    "Account No: XXXXXXXX4512 | Branch: Mangalore Main",
    [
        ("Account Holder:", "Sunil Kumar"),
        ("Period Examined:", "01-Apr-2026 to 20-Sep-2026 (6 Months)"),
        ("Average Monthly Balance:", "Rs. 1,45,280.00"),
        ("Salary Credits:", "Regular Monthly Credits of Rs. 70,800.00"),
        ("Inward Return / Cheque Bounce:", "Zero (0) Adverse Instances"),
        ("Financial Track Record:", "Classified Grade A - Satisfactory"),
    ],
    primary_color=(30, 64, 175),
)

# 8. Vehicle RC
create_card_image(
    os.path.join(DEMO_DIR, "demo_vehicle_rc.png"),
    "GOVERNMENT OF KARNATAKA - MOTOR VEHICLES DEPARTMENT",
    "Certificate of Registration (Form 23) - VAHAN Verified",
    [
        ("Registration Number:", "KA-19-MB-2026"),
        ("Owner Name:", "Sunil Kumar"),
        ("Vehicle Class / Maker:", "Motor Car / Hyundai Creta SX"),
        ("Engine / Chassis No.:", "G4FL-109283 / MALC8110992384"),
        ("Hypothecation / Charge:", "Noted in favor of SmartVerify Lending Bank"),
        ("Fitness & Tax Validity:", "Valid until 15-Sep-2041 (LTT Paid)"),
    ],
    primary_color=(131, 24, 67),
)

# 9. Vehicle Invoice
create_card_image(
    os.path.join(DEMO_DIR, "demo_vehicle_invoice.png"),
    "DEMO MOTORS PRIVATE LIMITED - PROFORMA TAX INVOICE",
    "Authorized Dealership - New Passenger Vehicle Quotation",
    [
        ("Customer Name:", "Sunil Kumar"),
        ("Vehicle Model / Variant:", "Hyundai Creta 1.5L Petrol SX"),
        ("Ex-Showroom Price:", "Rs. 14,80,000.00"),
        ("Road Tax + Registration:", "Rs. 2,10,000.00"),
        ("Comprehensive Insurance:", "Rs. 55,000.00"),
        ("Total On-Road Cost:", "Rs. 17,45,000.00"),
        ("Margin Money Paid:", "Rs. 3,50,000.00 (Receipt #MM-2026-04)"),
    ],
    primary_color=(190, 24, 93),
)

# 10. Residence Proof
create_card_image(
    os.path.join(DEMO_DIR, "demo_residence_proof.png"),
    "SMARTVERIFY PHYSICAL FIELD VERIFICATION DOSSIER",
    "Field Officer Residence Inspection Record & Landmark Photograph",
    [
        ("Inspected Address:", "24 MG Road, Hampankatta, Mangalore - 575001"),
        ("Date & Time of Visit:", "20-Sep-2026 at 11:30 AM IST"),
        ("Investigating Officer:", "Demo Verification Officer"),
        ("Property Condition:", "Good / RCC Construction Quality"),
        ("Occupancy Confirmation:", "Self-occupied / Family residing for 12 years"),
        ("Locality Feedback:", "Positive reference confirmed by neighborhood"),
    ],
    primary_color=(71, 85, 105),
)
