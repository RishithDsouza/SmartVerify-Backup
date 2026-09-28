"""
Baseline Report Renderer Service for SmartVerify Report Generation System.
Deterministically renders a BaselineReport from a Phase 3 ReportContext
and a Phase 2 ReportTemplate.

ARCHITECTURAL CONSTRAINTS:
- ZERO database queries (consumes ReportContext only).
- ZERO AI / LLM calls (narrative_allowed is strictly passive metadata).
- ZERO evidence selection or filtering (raw metadata exposed as-is).
- ZERO PDF generation (machine-readable structured output only).
- STRICT deterministic formatting and missing information markers.
"""
from typing import List, Optional, Dict, Any, Union
import re
from datetime import datetime

from app.schemas.report_context import (
    ReportContext,
    NormalizedParty,
    ParticularContextFact,
)
from app.schemas.baseline_report import (
    BaselineReport,
    RenderedHeader,
    RenderedFooter,
    RenderedPartyDetails,
    RenderedPartiesSummary,
    RenderedVerificationDetail,
    RenderedParticular,
    RenderedSection,
)
from app.report_templates import get_template
from app.report_templates.schema import ReportTemplate, ParticularItem

# Deterministic missing marker required by banking specification
MISSING_REQUIRED_MARKER = "INFORMATION REQUIRED"

SECTION_TITLES = {
    "1": "Borrower & Guarantor Identification",
    "2": "Residence Verification",
    "3": "KYC Document Verification",
    "4": "Income & Financial Standing",
    "5": "Bank Statement Verification",
    "6": "Track Record & Criminal Antecedents",
    "7": "Dealer & Invoice/RC Book Verification for Vehicles/Machineries",
}


# ─────────────────────────────────────────────────────────────────────
# Deterministic Formatting Helpers
# ─────────────────────────────────────────────────────────────────────

def format_currency(val: Optional[Union[float, int, str]]) -> str:
    """
    Deterministically formats an amount into Indian Rupee notation (₹ ##,##,###.##).
    Missing values return empty string or 0.00 without fabricating text.
    """
    if val is None or val == "":
        return ""
    try:
        num = float(val)
    except (ValueError, TypeError):
        return str(val)

    is_negative = num < 0
    num = abs(num)
    cents = f"{num:.2f}".split(".")[1]
    whole = int(num)

    s = str(whole)
    if len(s) <= 3:
        formatted_whole = s
    else:
        last3 = s[-3:]
        remaining = s[:-3]
        # Group remaining digits by 2 for Indian numbering system
        chunks = []
        while len(remaining) > 2:
            chunks.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            chunks.insert(0, remaining)
        formatted_whole = ",".join(chunks) + "," + last3

    prefix = "-₹ " if is_negative else "₹ "
    return f"{prefix}{formatted_whole}.{cents}"


def format_date(val: Optional[str]) -> str:
    """
    Deterministically normalizes dates to YYYY-MM-DD format.
    Unset values remain empty string.
    """
    if not val:
        return ""
    val_str = str(val).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", val_str):
        return val_str[:10]
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y"):
        try:
            dt = datetime.strptime(val_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return val_str


def format_masked_aadhaar(val: Optional[str]) -> str:
    """
    Ensures Aadhaar numbers are strictly presented in masked form (XXXXXXXX1234).
    """
    if not val:
        return ""
    clean = re.sub(r"\D", "", str(val))
    if len(clean) >= 4:
        return f"XXXXXXXX{clean[-4:]}"
    if str(val).startswith("XXXXXXXX"):
        return str(val)
    return "XXXXXXXX"


# ─────────────────────────────────────────────────────────────────────
# Party Normalization Helper
# ─────────────────────────────────────────────────────────────────────

def _render_party_details(party: NormalizedParty) -> RenderedPartyDetails:
    """Converts a NormalizedParty into a RenderedPartyDetails block."""
    return RenderedPartyDetails(
        party_id=party.party_id,
        party_type=party.party_type,
        name=party.name,
        father_name=party.father_name,
        address=party.address,
        mobile=party.mobile,
        email=party.email,
        dob=format_date(party.dob) if party.dob else None,
        masked_aadhaar=party.masked_aadhaar_number or format_masked_aadhaar(party.aadhaar_number),
        pan_number=party.pan_number,
        occupation=party.occupation,
        years_in_occupation=party.years_in_occupation,
        formatted_income=format_currency(party.income) if party.income is not None else None,
        income_period=party.income_period,
        relationship_to_applicant=party.relationship_to_applicant,
    )


# ─────────────────────────────────────────────────────────────────────
# Core Renderer Service
# ─────────────────────────────────────────────────────────────────────

def render_baseline_report(
    context: ReportContext,
    template_key: Optional[str] = None,
    template_version: Optional[str] = None,
) -> BaselineReport:
    """
    Renders a complete, typed BaselineReport from ReportContext and ReportTemplate.

    Guarantees:
    - Zero database access.
    - Zero AI calls or LLM prompts.
    - Deterministic fact formatting.
    - 2-column Applicant vs Guarantor isolation.
    - Explicit detection and marking of missing required information.
    """
    t_key = template_key or context.metadata.template_key
    t_ver = template_version or context.metadata.template_version
    template: ReportTemplate = get_template(t_key, t_ver)

    # 1. Parties Normalization
    rendered_applicant = _render_party_details(context.applicant)
    rendered_guarantors = [_render_party_details(g) for g in context.guarantors]
    parties_summary = RenderedPartiesSummary(
        applicant=rendered_applicant,
        guarantors=rendered_guarantors,
    )

    # 2. Header Rendering
    rendered_header = RenderedHeader(
        organization_name=template.header.organization_name,
        report_title=template.header.report_title,
        report_subtitle=template.header.report_subtitle,
        application_id=context.metadata.application_id,
        application_number=context.metadata.application_number,
        borrower_name=context.metadata.borrower_name,
        loan_type=context.metadata.loan_type,
        raw_loan_amount=context.metadata.loan_amount,
        formatted_loan_amount=format_currency(context.metadata.loan_amount),
        branch=context.metadata.branch or "Main Branch",
        report_date=format_date(context.metadata.report_date),
        officer_name=context.metadata.officer_name,
    )

    # 3. Footer Rendering
    rendered_footer = RenderedFooter(
        signatories=template.footer.signatories,
        confidentiality_notice=template.footer.confidentiality_notice,
        officer_name=context.metadata.officer_name,
        branch_name=context.metadata.branch or "Main Branch",
        report_date=format_date(context.metadata.report_date),
    )

    # 4. Template-Driven Particulars Rendering
    rendered_particulars: List[RenderedParticular] = []

    for item in template.particulars:
        pid = item.id
        pfact: Optional[ParticularContextFact] = context.particular_facts.get(pid)

        # Retrieve facts or default to empty
        app_facts = dict(pfact.applicant_facts) if pfact else {}
        guar_facts_list = list(pfact.guarantor_facts) if pfact else []
        shared_facts = dict(pfact.shared_facts) if pfact else {}
        missing_fields = list(pfact.missing_fields) if pfact else []

        # Determine overall missing status
        is_missing = bool(missing_fields)
        missing_marker = None
        if item.required and is_missing:
            missing_marker = MISSING_REQUIRED_MARKER

        # ── Render Applicant Column ──
        applicant_verification: Optional[RenderedVerificationDetail] = None
        if item.applicant_supported:
            formatted_app_facts: Dict[str, Any] = {}
            summary_lines: List[str] = []
            app_missing_marker = None

            if pid == "P1":
                formatted_app_facts = {
                    "name": rendered_applicant.name or MISSING_REQUIRED_MARKER,
                    "father_name": rendered_applicant.father_name,
                    "address": rendered_applicant.address or MISSING_REQUIRED_MARKER,
                    "mobile": rendered_applicant.mobile,
                    "email": rendered_applicant.email,
                    "dob": rendered_applicant.dob,
                    "pan_number": rendered_applicant.pan_number,
                    "masked_aadhaar": rendered_applicant.masked_aadhaar,
                }
                if rendered_applicant.name:
                    summary_lines.append(f"Name: {rendered_applicant.name}")
                if rendered_applicant.father_name:
                    summary_lines.append(f"Father's Name: {rendered_applicant.father_name}")
                if rendered_applicant.address:
                    summary_lines.append(f"Address: {rendered_applicant.address}")
                if rendered_applicant.mobile:
                    summary_lines.append(f"Mobile: {rendered_applicant.mobile}")
                if not rendered_applicant.name or not rendered_applicant.address:
                    app_missing_marker = MISSING_REQUIRED_MARKER

            elif pid == "P2":
                addr = app_facts.get("residence_address") or rendered_applicant.address
                status = app_facts.get("residence_status")
                formatted_app_facts = {
                    "residence_address": addr or MISSING_REQUIRED_MARKER,
                    "residence_status": status,
                }
                if addr:
                    summary_lines.append(f"Address: {addr}")
                if status:
                    summary_lines.append(f"Tenure: {status}")
                if not addr:
                    app_missing_marker = MISSING_REQUIRED_MARKER

            elif pid == "P2D":
                status = app_facts.get("residence_status") or "Self-owned"
                formatted_app_facts = {"residence_status": status}
                summary_lines.append(f"Status of Residence: {status}")

            elif pid == "P2E":
                aadhaar = rendered_applicant.masked_aadhaar
                formatted_app_facts = {"masked_aadhaar": aadhaar or MISSING_REQUIRED_MARKER}
                if aadhaar:
                    summary_lines.append(f"Aadhaar No.: {aadhaar}")
                else:
                    app_missing_marker = MISSING_REQUIRED_MARKER

            elif pid == "P3":
                docs = app_facts.get("verified_documents", [])
                formatted_app_facts = {
                    "verified_documents": docs,
                    "pan_number": rendered_applicant.pan_number,
                    "masked_aadhaar": rendered_applicant.masked_aadhaar,
                }
                if rendered_applicant.pan_number:
                    summary_lines.append(f"PAN: {rendered_applicant.pan_number}")
                if rendered_applicant.masked_aadhaar:
                    summary_lines.append(f"Aadhaar: {rendered_applicant.masked_aadhaar}")
                if docs:
                    summary_lines.append(f"Verified KYC Proofs: {', '.join(docs)}")

            elif pid == "P4":
                gross_income = app_facts.get("gross_income", rendered_applicant.formatted_income)
                formatted_app_facts = {
                    "gross_income": gross_income or MISSING_REQUIRED_MARKER,
                    "income_period": rendered_applicant.income_period or "monthly",
                    "occupation": rendered_applicant.occupation,
                    "years_in_occupation": rendered_applicant.years_in_occupation,
                }
                if gross_income:
                    summary_lines.append(f"Declared Income: {gross_income} ({rendered_applicant.income_period or 'monthly'})")
                if rendered_applicant.occupation:
                    summary_lines.append(f"Occupation: {rendered_applicant.occupation}")
                if not gross_income:
                    app_missing_marker = MISSING_REQUIRED_MARKER

            elif pid == "P4C":
                emp = app_facts.get("employer_name") or rendered_applicant.occupation
                formatted_app_facts = {"employer_name": emp}
                if emp:
                    summary_lines.append(f"Employer / Business: {emp}")

            elif pid == "P4D":
                occ = rendered_applicant.occupation
                inc = rendered_applicant.formatted_income
                vintage = rendered_applicant.years_in_occupation
                formatted_app_facts = {
                    "occupation": occ or MISSING_REQUIRED_MARKER,
                    "income": inc or MISSING_REQUIRED_MARKER,
                    "years_in_occupation": f"{vintage} years" if vintage is not None else None,
                }
                if occ:
                    summary_lines.append(f"Designation: {occ}")
                if inc:
                    summary_lines.append(f"Income: {inc}")
                if vintage is not None:
                    summary_lines.append(f"Experience: {vintage} years")
                if not occ or not inc:
                    app_missing_marker = MISSING_REQUIRED_MARKER

            else:
                # Fallback mapping for generic applicant-supported row
                formatted_app_facts = dict(app_facts)
                for k, v in app_facts.items():
                    if v is not None:
                        summary_lines.append(f"{k.replace('_', ' ').title()}: {v}")

            applicant_verification = RenderedVerificationDetail(
                party_id=rendered_applicant.party_id,
                party_type="applicant",
                party_name=rendered_applicant.name,
                formatted_facts=formatted_app_facts,
                summary_lines=summary_lines,
                status="missing" if app_missing_marker else "available",
                missing_marker=app_missing_marker,
            )

        # ── Render Guarantor Columns ──
        guarantor_verifications: List[RenderedVerificationDetail] = []
        if item.guarantor_supported and rendered_guarantors:
            for g in rendered_guarantors:
                formatted_g_facts: Dict[str, Any] = {}
                g_summary_lines: List[str] = []

                if pid == "P1":
                    formatted_g_facts = {
                        "name": g.name,
                        "father_name": g.father_name,
                        "address": g.address,
                        "mobile": g.mobile,
                        "dob": g.dob,
                        "pan_number": g.pan_number,
                        "masked_aadhaar": g.masked_aadhaar,
                        "relationship": g.relationship_to_applicant,
                    }
                    if g.name:
                        g_summary_lines.append(f"Name: {g.name}")
                    if g.relationship_to_applicant:
                        g_summary_lines.append(f"Relationship: {g.relationship_to_applicant}")
                    if g.father_name:
                        g_summary_lines.append(f"Father's Name: {g.father_name}")
                    if g.address:
                        g_summary_lines.append(f"Address: {g.address}")
                    if g.mobile:
                        g_summary_lines.append(f"Mobile: {g.mobile}")
                    if not g_summary_lines:
                        g_summary_lines.append("NA")

                elif pid == "P2":
                    formatted_g_facts = {"residence_address": g.address}
                    if g.address:
                        g_summary_lines.append(f"Address: {g.address}")
                    else:
                        g_summary_lines.append("NA")

                elif pid in ("P2A", "P2B", "P2C"):
                    g_findings = [f for f in context.findings if f.particular_id == pid and f.party_id == g.party_id]
                    if g_findings:
                        for gf in g_findings:
                            g_summary_lines.append(gf.answer)
                    else:
                        g_summary_lines.append("NA")

                elif pid == "P2D":
                    g_fact_item = next((gf for gf in (pfact.guarantor_facts if pfact else []) if gf.get("party_id") == g.party_id), {})
                    g_status = g_fact_item.get("residence_status")
                    if g_status:
                        formatted_g_facts = {"residence_status": g_status}
                        g_summary_lines.append(f"Status of Residence: {g_status}")
                    else:
                        g_summary_lines.append("NA")

                elif pid == "P2E":
                    formatted_g_facts = {"masked_aadhaar": g.masked_aadhaar}
                    if g.masked_aadhaar:
                        g_summary_lines.append(f"Aadhaar No.: {g.masked_aadhaar}")
                    else:
                        g_summary_lines.append("NA")

                elif pid == "P3":
                    # Section 3 KYC: Display verified info if available; otherwise strictly NA
                    g_findings = [f for f in context.findings if f.particular_id == "P3" and f.party_id == g.party_id]
                    g_has_kyc_docs = any(
                        d.party_id == g.party_id and d.document_type in ("aadhaar", "pan")
                        for d in context.documents
                    )
                    if g_findings or g_has_kyc_docs:
                        if g.pan_number and g.pan_number != "N/A":
                            g_summary_lines.append(f"PAN: {g.pan_number}")
                        if g.masked_aadhaar and g.masked_aadhaar != "N/A":
                            g_summary_lines.append(f"Aadhaar: {g.masked_aadhaar}")
                        for gf in g_findings:
                            g_summary_lines.append(gf.answer)
                    if not g_summary_lines:
                        g_summary_lines.append("NA")

                elif pid == "P4":
                    g_income_docs = [
                        d.original_name or d.file_path for d in context.documents
                        if (d.party_id == g.party_id or d.party_id == f"joint_applicant:{g.party_id.split(':')[-1]}")
                        and d.document_type in ("salary_slip", "form_16", "itr", "income_cert")
                    ]
                    g_findings = [f for f in context.findings if f.particular_id == "P4" and f.party_id == g.party_id]
                    if g_income_docs or g_findings:
                        if g.formatted_income:
                            g_summary_lines.append(f"Declared Income: {g.formatted_income}")
                        if g.occupation:
                            g_summary_lines.append(f"Occupation: {g.occupation}")
                    if not g_summary_lines:
                        g_summary_lines.append("NA")

                elif pid == "P4A":
                    g_income_docs = [
                        d.original_name or d.file_path for d in context.documents
                        if (d.party_id == g.party_id or d.party_id == f"joint_applicant:{g.party_id.split(':')[-1]}")
                        and d.document_type in ("salary_slip", "form_16", "itr", "income_cert")
                    ]
                    if g_income_docs:
                        g_summary_lines.append(f"Documents Seen: {', '.join(g_income_docs)}")
                    else:
                        g_summary_lines.append("NA")

                elif pid == "P4B":
                    g_findings = [f for f in context.findings if f.particular_id == "P4B" and f.party_id == g.party_id]
                    if g_findings:
                        for gf in g_findings:
                            g_summary_lines.append(gf.answer)
                    else:
                        g_summary_lines.append("NA")

                elif pid == "P4C":
                    g_fact_item = next((gf for gf in (pfact.guarantor_facts if pfact else []) if gf.get("party_id") == g.party_id), {})
                    emp = g_fact_item.get("employer_name")
                    if emp:
                        g_summary_lines.append(f"Employer: {emp}")
                    else:
                        g_summary_lines.append("NA")

                elif pid == "P4D":
                    g_income_docs = [
                        d.original_name or d.file_path for d in context.documents
                        if (d.party_id == g.party_id or d.party_id == f"joint_applicant:{g.party_id.split(':')[-1]}")
                        and d.document_type in ("salary_slip", "form_16", "itr", "income_cert")
                    ]
                    g_findings = [f for f in context.findings if f.particular_id == "P4D" and f.party_id == g.party_id]
                    if g_income_docs or g_findings:
                        if g.occupation:
                            g_summary_lines.append(f"Designation: {g.occupation}")
                        if g.formatted_income:
                            g_summary_lines.append(f"Income: {g.formatted_income}")
                        if g.years_in_occupation is not None:
                            g_summary_lines.append(f"Experience: {g.years_in_occupation} years")
                    if not g_summary_lines:
                        g_summary_lines.append("NA")

                elif pid in ("P5", "P5A", "P5B", "P5C"):
                    g_bank_docs = [
                        d.original_name or d.file_path for d in context.documents
                        if (d.party_id == g.party_id or d.party_id == f"joint_applicant:{g.party_id.split(':')[-1]}")
                        and d.document_type == "bank_statement"
                    ]
                    g_findings = [f for f in context.findings if f.particular_id == pid and f.party_id == g.party_id]
                    if g_bank_docs:
                        g_summary_lines.append(f"Bank Statements: {len(g_bank_docs)} verified")
                    for gf in g_findings:
                        g_summary_lines.append(gf.answer)
                    if not g_summary_lines:
                        g_summary_lines.append("NA")

                elif pid == "P6":
                    g_findings = [f for f in context.findings if f.particular_id == "P6" and f.party_id == g.party_id]
                    if g_findings:
                        for gf in g_findings:
                            g_summary_lines.append(gf.answer)
                    else:
                        g_summary_lines.append("NA")

                else:
                    g_findings = [f for f in context.findings if f.particular_id == pid and f.party_id == g.party_id]
                    if g_findings:
                        for gf in g_findings:
                            g_summary_lines.append(gf.answer)
                    else:
                        g_summary_lines.append("NA")

                guarantor_verifications.append(
                    RenderedVerificationDetail(
                        party_id=g.party_id,
                        party_type=g.party_type,
                        party_name=g.name,
                        formatted_facts=formatted_g_facts,
                        summary_lines=g_summary_lines,
                        status="available",
                        missing_marker=None,
                    )
                )

        # ── Render Shared / Composite Facts ──
        shared_verification: Optional[RenderedVerificationDetail] = None
        if shared_facts:
            formatted_shared_facts: Dict[str, Any] = {}
            shared_summary_lines: List[str] = []

            # ── SECTION 2 SUB-ROWS ──
            if pid == "P2A":
                formatted_shared_facts = dict(app_facts)
                if not app_facts.get("physical_visit_confirmed"):
                    shared_summary_lines.append(f"Physical Visit: {MISSING_REQUIRED_MARKER}")
                else:
                    shared_summary_lines.append("Physical Visit: Completed and Confirmed")
                    if app_facts.get("property_condition"):
                        shared_summary_lines.append(f"Property Condition: {app_facts['property_condition']}")
                    if app_facts.get("construction_quality"):
                        shared_summary_lines.append(f"Construction Quality: {app_facts['construction_quality']}")
                    if app_facts.get("road_access"):
                        shared_summary_lines.append(f"Road Access: {app_facts['road_access']}")
                    if app_facts.get("field_observations"):
                        shared_summary_lines.append(f"Observations: {app_facts['field_observations']}")

            elif pid == "P2B":
                formatted_shared_facts = dict(app_facts)
                officer = app_facts.get("visiting_officer")
                date = app_facts.get("visit_date")
                time = app_facts.get("visit_time")
                if officer:
                    shared_summary_lines.append(f"Visiting Officer: {officer}")
                if date:
                    shared_summary_lines.append(f"Visit Date: {format_date(date)}")
                if time:
                    shared_summary_lines.append(f"Visit Time: {time}")
                if not officer or not date:
                    shared_summary_lines.append(f"Officer / Date: {MISSING_REQUIRED_MARKER}")

            elif pid == "P2C":
                reason = app_facts.get("reason_not_visited")
                formatted_shared_facts = {"reason_not_visited": reason}
                if reason:
                    shared_summary_lines.append(f"Reason for No Physical Visit: {reason}")
                else:
                    shared_summary_lines.append("Site was physically visited (No exemption applicable).")

            # ── SECTION 4 SUB-ROWS ──
            elif pid == "P4A":
                seen = shared_facts.get("income_docs_seen", [])
                formatted_shared_facts = {"documents_seen": seen}
                if seen:
                    shared_summary_lines.append(f"Income Documents Examined: {', '.join(seen)}")
                else:
                    shared_summary_lines.append(f"Income Proofs: {MISSING_REQUIRED_MARKER}")

            elif pid == "P4B":
                tax_st = shared_facts.get("tax_receipt_status", "Unverified")
                formatted_shared_facts = {
                    "tax_receipt_status": tax_st,
                    "assessment_years": shared_facts.get("assessment_years", []),
                }
                shared_summary_lines.append(f"Tax Receipt Portal Status: {tax_st}")

            # ── SECTION 5 (BANKING) ──
            elif pid == "P5":
                present = shared_facts.get("bank_statements_present", False)
                count = shared_facts.get("bank_docs_count", 0)
                formatted_shared_facts = {"bank_statements_present": present, "count": count}
                if present:
                    shared_summary_lines.append(f"Bank Statements Attached: {count} document(s) verified.")
                else:
                    shared_summary_lines.append(f"Bank Statements: {MISSING_REQUIRED_MARKER}")

            elif pid in ("P5A", "P5B", "P5C"):
                count = shared_facts.get("bank_statement_count", 0)
                formatted_shared_facts = dict(shared_facts)
                if count > 0:
                    shared_summary_lines.append(f"Primary Operative Account: Verified via {count} bank statement file(s).")
                else:
                    shared_summary_lines.append(f"Banking Record: {MISSING_REQUIRED_MARKER}")

            # ── SECTION 6 (TRACK RECORD) ──
            elif pid == "P6":
                count = shared_facts.get("findings_count", 0)
                has_findings = shared_facts.get("has_findings", False)
                formatted_shared_facts = {"findings_count": count, "has_findings": has_findings}
                if has_findings:
                    shared_summary_lines.append(f"Adverse / Verification Findings: {count} verified finding record(s) on file.")
                else:
                    shared_summary_lines.append("No adverse criminal antecedents or negative market remarks logged.")

            # ── SECTION 7 (VEHICLE / MACHINERY VERIFICATION) ──
            elif pid == "P7":
                is_veh = shared_facts.get("is_vehicle_machinery_loan", False)
                docs = shared_facts.get("vehicle_machinery_docs", [])
                formatted_shared_facts = {"is_vehicle_machinery_loan": is_veh, "docs": docs}
                if is_veh or docs:
                    shared_summary_lines.append(f"Vehicle / Machinery Verification Applicable: {len(docs)} document(s) on record.")
                else:
                    shared_summary_lines.append("Not Applicable (Facility is not a Vehicle/Machinery loan).")

            elif pid == "P7A":
                docs = shared_facts.get("invoice_documents", [])
                formatted_shared_facts = {"for_new_vehicles_machineries": True, "invoice_documents": docs}
                if docs:
                    shared_summary_lines.append(f"New Vehicle / Machinery Invoices Attached: {', '.join(docs)}")
                else:
                    shared_summary_lines.append("No new vehicle quotation/invoice documents attached.")

            elif pid == "P7A1":
                details = shared_facts.get("dealer_subdealer_details", [])
                formatted_shared_facts = {"dealer_subdealer_details": details}
                if details:
                    shared_summary_lines.append(f"Dealer Details: {'; '.join(details)}")
                else:
                    shared_summary_lines.append("Dealer name/address verification pending or not applicable.")

            elif pid == "P7A2":
                present = shared_facts.get("invoice_documents_present", False)
                adv = shared_facts.get("advance_payment_receipt_findings", [])
                formatted_shared_facts = {"invoice_documents_present": present, "advance_findings": adv}
                if present or adv:
                    shared_summary_lines.append("Invoice & advance margin money deposit receipts verified.")
                else:
                    shared_summary_lines.append("Invoice / advance receipt verification records pending.")

            elif pid == "P7B":
                rcs = shared_facts.get("rc_documents", [])
                formatted_shared_facts = {"for_old_used_vehicles_machineries": True, "rc_documents": rcs}
                if rcs:
                    shared_summary_lines.append(f"Pre-Owned Vehicle RC Records: {', '.join(rcs)}")
                else:
                    shared_summary_lines.append("Pre-owned vehicle RC documents not applicable or not provided.")

            elif pid == "P7B1":
                ver = shared_facts.get("rc_machinery_number_verification", [])
                formatted_shared_facts = {"rc_verification": ver}
                if ver:
                    shared_summary_lines.append(f"RC / Machinery Serial Verification: {'; '.join(ver)}")
                else:
                    shared_summary_lines.append("RC book serial / engine number verification records pending.")

            elif pid == "P7B2":
                hyp = shared_facts.get("charge_hypothecation_noted_in_rc", [])
                formatted_shared_facts = {"hypothecation_noted": hyp}
                if hyp:
                    shared_summary_lines.append(f"Hypothecation Details: {'; '.join(hyp)}")
                else:
                    shared_summary_lines.append("No prior hypothecation endorsement recorded.")

            elif pid == "P7B3":
                ins = shared_facts.get("insurance_details_validity", [])
                formatted_shared_facts = {"insurance_validity": ins}
                if ins:
                    shared_summary_lines.append(f"Insurance Policy Details: {'; '.join(ins)}")
                else:
                    shared_summary_lines.append("Comprehensive vehicle insurance policy validity pending confirmation.")

            else:
                formatted_shared_facts = dict(shared_facts)
                for k, v in shared_facts.items():
                    if v is not None:
                        shared_summary_lines.append(f"{k.replace('_', ' ').title()}: {v}")

            shared_verification = RenderedVerificationDetail(
                party_id="shared",
                party_type="shared",
                party_name="Shared Verification Facts",
                formatted_facts=formatted_shared_facts,
                summary_lines=shared_summary_lines,
                status="available",
                missing_marker=None,
            )

        # Build final RenderedParticular
        rendered_particulars.append(
            RenderedParticular(
                id=pid,
                section=item.section,
                title=item.title,
                parent_id=item.parent_id,
                display_order=item.display_order,
                applicant_supported=item.applicant_supported,
                guarantor_supported=item.guarantor_supported,
                data_type=item.data_type,
                required=item.required,
                narrative_allowed=item.narrative_allowed,
                evidence_category=item.evidence_category,
                applicant_verification=applicant_verification,
                guarantor_verifications=guarantor_verifications,
                shared_verification=shared_verification,
                missing=is_missing,
                missing_fields=missing_fields,
                missing_marker=missing_marker,
                status="missing" if (item.required and is_missing and not applicant_verification and not shared_verification) else (
                    "partial" if is_missing else "available"
                ),
                raw_facts={
                    "applicant": app_facts,
                    "guarantors": guar_facts_list,
                    "shared": shared_facts,
                },
            )
        )

    # 5. Group Particulars into Sections
    sections_map: Dict[str, List[RenderedParticular]] = {}
    for p in rendered_particulars:
        sections_map.setdefault(p.section, []).append(p)

    rendered_sections: List[RenderedSection] = []
    for sec_num in sorted(sections_map.keys(), key=lambda x: int(x) if x.isdigit() else 99):
        sec_title = SECTION_TITLES.get(sec_num, f"Section {sec_num}")
        rendered_sections.append(
            RenderedSection(
                section_number=sec_num,
                section_title=sec_title,
                particulars=sections_map[sec_num],
            )
        )

    return BaselineReport(
        template_key=template.template_key,
        template_version=template.template_version,
        header=rendered_header,
        parties=parties_summary,
        particulars=rendered_particulars,
        sections=rendered_sections,
        footer=rendered_footer,
        missing_information=context.missing_information,
        documents=context.documents,
        government_screenshots=context.government_verification.screenshots,
        findings=context.findings,
        input_hash=context.input_hash,
    )
