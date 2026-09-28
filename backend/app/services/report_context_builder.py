"""
Report Context Builder Service for SmartVerify Report Generation System.
Deterministic service that collects and normalizes verified database facts
into a structured, machine-readable ReportContext according to the fixed report template.

Strict Guardrails:
- No LLMs, prompts, or narrative generation.
- No inference or fabrication of missing data.
- Does not select evidence or generate PDF output.
- Exposes traceable facts with provenance and detects missing required items.
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from app.models.application import (
    Application,
    JointApplicant,
    SiteVerification,
    PropertyDetails,
    GovVerification,
)
from app.models.document import Document
from app.models.finding import ApplicationFinding
from app.models.government_screenshot import GovernmentVerificationScreenshot
from app.report_templates import get_template
from app.report_templates.schema import ReportTemplate, ParticularItem
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

logger = logging.getLogger(__name__)


def _mask_aadhaar(aadhaar: Optional[str]) -> Optional[str]:
    """Return masked Aadhaar string showing only the last 4 digits."""
    if not aadhaar:
        return None
    cleaned = aadhaar.replace(" ", "").replace("-", "")
    if len(cleaned) < 4:
        return cleaned
    return "X" * (len(cleaned) - 4) + cleaned[-4:]


def _safe_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def build_report_context(
    application_id: int,
    db: Session,
    template_key: str = "standard_reverification",
    template_version: str = "v1.0",
) -> ReportContext:
    """
    Collects and normalizes all verified database facts for the given application.
    Maps facts deterministically to the specified report template particulars.
    Computes a deterministic SHA-256 input hash for staleness detection.
    """
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise ValueError(f"Application with ID {application_id} not found.")

    template: ReportTemplate = get_template(template_key, template_version)

    all_facts: List[FactProvenance] = []

    def record_fact(
        fact_id: str,
        value: Any,
        source: str,
        source_id: Optional[str],
        particular_ids: List[str],
        status: Optional[str] = None,
    ) -> FactProvenance:
        if status is None:
            if value is not None and value != "" and value != []:
                fact_status = "available"
            else:
                fact_status = "missing"
        else:
            fact_status = status

        fp = FactProvenance(
            fact_id=fact_id,
            value=value,
            status=fact_status,
            source=source,
            source_id=source_id,
            particular_ids=particular_ids,
        )
        all_facts.append(fp)
        return fp

    # ─────────────────────────────────────────────────────────────────
    # 1. Parties Normalization (Applicant + Guarantors / Co-Applicants)
    # ─────────────────────────────────────────────────────────────────
    app_source_id = f"app:{app.id}"

    # Traceable Applicant Facts
    record_fact("applicant.name", app.applicant_name, "applications", app_source_id, ["P1"])
    record_fact("applicant.father_name", app.father_name, "applications", app_source_id, ["P1"])
    record_fact("applicant.address", app.address, "applications", app_source_id, ["P1", "P2"])
    record_fact("applicant.mobile", app.applicant_mobile, "applications", app_source_id, ["P1"])
    record_fact("applicant.email", app.applicant_email, "applications", app_source_id, ["P1"])
    record_fact("applicant.dob", app.dob, "applications", app_source_id, ["P1"])
    record_fact("applicant.pan_number", app.pan_number, "applications", app_source_id, ["P3"])
    record_fact("applicant.aadhaar_number", app.aadhaar_number, "applications", app_source_id, ["P2E", "P3"])

    applicant_party = NormalizedParty(
        party_id="applicant",
        party_type="applicant",
        name=_safe_str(app.applicant_name),
        father_name=_safe_str(app.father_name),
        address=_safe_str(app.address),
        mobile=_safe_str(app.applicant_mobile),
        email=_safe_str(app.applicant_email),
        dob=_safe_str(app.dob),
        aadhaar_number=_safe_str(app.aadhaar_number),
        masked_aadhaar_number=_mask_aadhaar(app.aadhaar_number),
        pan_number=_safe_str(app.pan_number),
        occupation=None,
        years_in_occupation=None,
        income=None,
        income_period="monthly",
        relationship_to_applicant="Self",
    )

    # Co-Applicants / Guarantors
    guarantors_list: List[NormalizedParty] = []
    joint_applicants = db.query(JointApplicant).filter(
        JointApplicant.application_id == app.id
    ).order_by(JointApplicant.index).all()

    for ja in joint_applicants:
        ja_source_id = f"joint_applicants:{ja.id}"
        prefix = f"guarantor_{ja.index}"
        record_fact(f"{prefix}.name", ja.name, "joint_applicants", ja_source_id, ["P1"])
        record_fact(f"{prefix}.father_name", ja.father_name, "joint_applicants", ja_source_id, ["P1"])
        record_fact(f"{prefix}.address", ja.address, "joint_applicants", ja_source_id, ["P1", "P2"])
        record_fact(f"{prefix}.pan_number", ja.pan_number, "joint_applicants", ja_source_id, ["P3"])
        record_fact(f"{prefix}.aadhaar_number", ja.aadhaar_number, "joint_applicants", ja_source_id, ["P2E", "P3"])
        record_fact(f"{prefix}.income", ja.income, "joint_applicants", ja_source_id, ["P4", "P4D"])
        record_fact(f"{prefix}.occupation", ja.occupation, "joint_applicants", ja_source_id, ["P4D"])
        record_fact(f"{prefix}.years_in_occupation", ja.years_in_occupation, "joint_applicants", ja_source_id, ["P4D"])

        g_party = NormalizedParty(
            party_id=f"{ja.applicant_type}:{ja.index}",
            party_type=ja.applicant_type,
            name=_safe_str(ja.name),
            father_name=_safe_str(ja.father_name),
            address=_safe_str(ja.address),
            mobile=_safe_str(ja.mobile),
            email=_safe_str(ja.email),
            dob=_safe_str(ja.dob),
            aadhaar_number=_safe_str(ja.aadhaar_number),
            masked_aadhaar_number=_mask_aadhaar(ja.aadhaar_number),
            pan_number=_safe_str(ja.pan_number),
            occupation=_safe_str(ja.occupation),
            years_in_occupation=ja.years_in_occupation,
            income=ja.income,
            income_period="monthly",
            relationship_to_applicant=_safe_str(ja.relationship_type),
        )
        guarantors_list.append(g_party)

    # ─────────────────────────────────────────────────────────────────
    # 2. Documents Normalization
    # ─────────────────────────────────────────────────────────────────
    raw_docs = db.query(Document).filter(Document.application_id == app.id).all()
    normalized_docs: List[NormalizedDocumentMeta] = []
    income_doc_types = {"salary_slip", "income_cert", "form_16", "itr"}
    bank_doc_types = {"bank_statement"}
    vehicle_doc_types = {"vehicle_document", "invoice"}

    income_docs_found: List[str] = []
    bank_docs_found: List[str] = []
    vehicle_docs_found: List[str] = []

    for d in raw_docs:
        dtype = d.document_type.value if hasattr(d.document_type, "value") else str(d.document_type)
        p_id = "applicant" if d.joint_applicant_index is None else f"joint_applicant:{d.joint_applicant_index}"
        ocr_st = "ocr_done" if d.processed == 2 else ("preprocessed" if d.processed == 1 else "raw")

        doc_meta = NormalizedDocumentMeta(
            document_id=d.id,
            document_type=dtype,
            party_id=p_id,
            original_name=d.original_name,
            file_path=d.file_path,
            ocr_status=ocr_st,
            has_extracted_text=bool(d.extracted_text and len(d.extracted_text.strip()) > 0),
            has_structured_data=bool(d.structured_data and len(d.structured_data.strip()) > 0),
            created_at=d.created_at.isoformat() if d.created_at else None,
        )
        normalized_docs.append(doc_meta)

        if dtype in income_doc_types:
            income_docs_found.append(dtype)
        if dtype in bank_doc_types:
            bank_docs_found.append(dtype)
        if dtype in vehicle_doc_types:
            vehicle_docs_found.append(dtype)

    record_fact(
        "documents.income_docs_seen",
        sorted(list(set(income_docs_found))),
        "documents",
        app_source_id,
        ["P4", "P4A"],
    )
    record_fact(
        "documents.bank_docs_seen",
        sorted(list(set(bank_docs_found))),
        "documents",
        app_source_id,
        ["P5"],
    )

    # ─────────────────────────────────────────────────────────────────
    # 3. Government Verification Normalization
    # ─────────────────────────────────────────────────────────────────
    gov_ver = app.gov_verification
    raw_shots = db.query(GovernmentVerificationScreenshot).filter(
        GovernmentVerificationScreenshot.application_id == app.id
    ).all()

    norm_shots = [
        GovernmentScreenshotMeta(
            screenshot_id=gs.id,
            verification_type=gs.verification_type,
            government_portal=gs.government_portal,
            verification_reference=gs.verification_reference,
            verification_status=gs.verification_status,
            screenshot_path=gs.screenshot_path,
            captured_at=gs.captured_at.isoformat() if gs.captured_at else None,
            source_url=gs.source_url,
        )
        for gs in raw_shots
    ]

    gov_source_id = f"gov_verifications:{gov_ver.id}" if gov_ver else None
    norm_gov = NormalizedGovVerification(
        pan_aadhaar_link_status=_safe_str(gov_ver.pan_aadhaar_link_status) if gov_ver else None,
        aadhaar_validity_status=_safe_str(gov_ver.aadhaar_validity_status) if gov_ver else None,
        tax_receipt_status=_safe_str(gov_ver.tax_receipt_status) if gov_ver else None,
        officer_name=_safe_str(gov_ver.officer_name) if gov_ver else None,
        timestamp=_safe_str(gov_ver.timestamp) if gov_ver else None,
        remarks=_safe_str(gov_ver.remarks) if gov_ver else None,
        aadhaar_screenshot_filename=_safe_str(gov_ver.aadhaar_screenshot_path) if gov_ver else None,
        pan_screenshot_filename=_safe_str(gov_ver.screenshot_path) if gov_ver else None,
        screenshots=norm_shots,
    )

    record_fact("gov.aadhaar_validity_status", norm_gov.aadhaar_validity_status, "gov_verifications", gov_source_id, ["P2E", "P3"])
    record_fact("gov.pan_aadhaar_link_status", norm_gov.pan_aadhaar_link_status, "gov_verifications", gov_source_id, ["P3"])
    record_fact("gov.verification_timestamp", norm_gov.timestamp, "gov_verifications", gov_source_id, ["P3"])

    # ─────────────────────────────────────────────────────────────────
    # 4. Site Verification Normalization
    # ─────────────────────────────────────────────────────────────────
    site_ver = app.site_verification
    site_source_id = f"site_verifications:{site_ver.id}" if site_ver else None

    site_dict: Dict[str, Any] = {}
    if site_ver:
        site_dict = {
            "gps_coordinates": _safe_str(site_ver.gps_coordinates),
            "officer_name": _safe_str(site_ver.officer_name),
            "date": _safe_str(site_ver.date),
            "time": _safe_str(site_ver.time),
            "property_condition": _safe_str(site_ver.property_condition),
            "construction_quality": _safe_str(site_ver.construction_quality),
            "boundary_present": _safe_str(site_ver.boundary_present),
            "road_access": _safe_str(site_ver.road_access),
            "utilities_available": _safe_str(site_ver.utilities_available),
            "remarks": _safe_str(site_ver.remarks),
        }

    record_fact("site.visit_confirmed", bool(site_ver and site_ver.officer_name), "site_verifications", site_source_id, ["P2A"])
    record_fact("site.officer_name", site_dict.get("officer_name"), "site_verifications", site_source_id, ["P2B"])
    record_fact("site.visit_date", site_dict.get("date"), "site_verifications", site_source_id, ["P2B"])
    record_fact("site.property_condition", site_dict.get("property_condition"), "site_verifications", site_source_id, ["P2A"])
    record_fact("site.remarks", site_dict.get("remarks"), "site_verifications", site_source_id, ["P2A", "P2C"])

    # ─────────────────────────────────────────────────────────────────
    # 5. Property Details Normalization
    # ─────────────────────────────────────────────────────────────────
    prop = app.property_details
    prop_source_id = f"property_details:{prop.id}" if prop else None
    prop_dict: Dict[str, Any] = {}
    if prop:
        prop_dict = {
            "property_type": _safe_str(prop.property_type),
            "address": _safe_str(prop.address),
            "survey_number": _safe_str(prop.survey_number),
            "khata_number": _safe_str(prop.khata_number),
            "property_area": _safe_str(prop.property_area),
            "market_value": prop.market_value,
            "loan_security_value": prop.loan_security_value,
        }

    record_fact("property.property_type", prop_dict.get("property_type"), "property_details", prop_source_id, ["P2D"])
    record_fact("property.market_value", prop_dict.get("market_value"), "property_details", prop_source_id, [])
    record_fact("property.security_value", prop_dict.get("loan_security_value"), "property_details", prop_source_id, [])

    # ─────────────────────────────────────────────────────────────────
    # 6. Application Findings Normalization
    # ─────────────────────────────────────────────────────────────────
    raw_findings = db.query(ApplicationFinding).filter(
        ApplicationFinding.application_id == app.id
    ).all()

    norm_findings: List[NormalizedFinding] = []
    for f in raw_findings:
        f_source_id = f"application_findings:{f.id}"
        p_ids = [f.particular_id] if f.particular_id else []
        record_fact(
            f"finding.{f.id}.status",
            f.status,
            "application_findings",
            f_source_id,
            p_ids,
        )
        norm_findings.append(
            NormalizedFinding(
                finding_id=f.id,
                particular_id=f.particular_id,
                category=f.finding_type,
                question=f.question,
                answer=f.answer,
                status=f.status,
                confidence=f.confidence,
                source="application_findings",
                linked_document_ids=[d.id for d in f.documents],
                party_id=None,
            )
        )

    # ─────────────────────────────────────────────────────────────────
    # 7. Map Facts to Fixed Particulars & Detect Missing Fields
    # ─────────────────────────────────────────────────────────────────
    particular_facts_map: Dict[str, ParticularContextFact] = {}
    missing_info_list: List[MissingParticularInfo] = []

    for item in template.particulars:
        pid = item.id
        app_facts: Dict[str, Any] = {}
        guar_facts: List[Dict[str, Any]] = []
        shared: Dict[str, Any] = {}
        missing_fields: List[str] = []

        if pid == "P1":
            app_facts = {
                "name": applicant_party.name,
                "father_name": applicant_party.father_name,
                "address": applicant_party.address,
                "mobile": applicant_party.mobile,
                "email": applicant_party.email,
            }
            if not applicant_party.name:
                missing_fields.append("applicant_name")
            if not applicant_party.address:
                missing_fields.append("applicant_address")

            for g in guarantors_list:
                guar_facts.append({
                    "party_id": g.party_id,
                    "name": g.name,
                    "father_name": g.father_name,
                    "address": g.address,
                    "mobile": g.mobile,
                    "relationship": g.relationship_to_applicant,
                })

        elif pid == "P2":
            app_facts = {
                "residence_address": applicant_party.address,
                "residence_status": prop_dict.get("property_type"),
            }
            if not applicant_party.address:
                missing_fields.append("residence_address")

        elif pid == "P2A":
            app_facts = {
                "physical_visit_confirmed": bool(site_ver and site_ver.officer_name),
                "property_condition": site_dict.get("property_condition"),
                "construction_quality": site_dict.get("construction_quality"),
                "boundary_present": site_dict.get("boundary_present"),
                "road_access": site_dict.get("road_access"),
                "utilities": site_dict.get("utilities_available"),
                "field_observations": site_dict.get("remarks"),
                "gps_coordinates": site_dict.get("gps_coordinates"),
            }
            if not site_ver or not site_ver.officer_name:
                missing_fields.append("physical_visit_record")
            if not site_dict.get("property_condition"):
                missing_fields.append("property_condition_observation")

        elif pid == "P2B":
            app_facts = {
                "visiting_officer": site_dict.get("officer_name"),
                "visit_date": site_dict.get("date"),
                "visit_time": site_dict.get("time"),
            }
            if not site_dict.get("officer_name"):
                missing_fields.append("visiting_officer_name")
            if not site_dict.get("date"):
                missing_fields.append("visit_date")

        elif pid == "P2C":
            app_facts = {
                "reason_not_visited": site_dict.get("remarks") if not site_ver else None,
            }
            # Optional field, no mandatory missing flag unless visit was not done and reason missing

        elif pid == "P2D":
            app_facts = {
                "residence_tenure_status": prop_dict.get("property_type"),
            }
            if not prop_dict.get("property_type"):
                missing_fields.append("residence_status")

        elif pid == "P2E":
            app_facts = {
                "masked_aadhaar": applicant_party.masked_aadhaar_number,
                "aadhaar_present": bool(applicant_party.aadhaar_number),
            }
            if not applicant_party.aadhaar_number:
                missing_fields.append("applicant_aadhaar_number")

            for g in guarantors_list:
                guar_facts.append({
                    "party_id": g.party_id,
                    "masked_aadhaar": g.masked_aadhaar_number,
                    "aadhaar_present": bool(g.aadhaar_number),
                })

        elif pid == "P3":
            app_facts = {
                "masked_aadhaar": applicant_party.masked_aadhaar_number,
                "pan_number": applicant_party.pan_number,
                "aadhaar_validity_status": norm_gov.aadhaar_validity_status,
                "pan_aadhaar_link_status": norm_gov.pan_aadhaar_link_status,
                "government_verification_timestamp": norm_gov.timestamp,
            }
            if not applicant_party.pan_number:
                missing_fields.append("applicant_pan_number")
            if not norm_gov.aadhaar_validity_status:
                missing_fields.append("aadhaar_validity_status")
            if not norm_gov.pan_aadhaar_link_status:
                missing_fields.append("pan_aadhaar_link_status")

            for g in guarantors_list:
                guar_facts.append({
                    "party_id": g.party_id,
                    "pan_number": g.pan_number,
                    "masked_aadhaar": g.masked_aadhaar_number,
                })

        elif pid == "P4":
            shared = {
                "income_documents_seen": sorted(list(set(income_docs_found))),
            }
            if not income_docs_found:
                missing_fields.append("income_documents")

        elif pid == "P4A":
            shared = {
                "documents_seen": sorted(list(set(income_docs_found))),
            }
            if not income_docs_found:
                missing_fields.append("income_documents_list")

        elif pid == "P4B":
            shared = {
                "documents_count": len(income_docs_found),
                "income_documents": sorted(list(set(income_docs_found))),
            }

        elif pid == "P4C":
            app_facts = {
                "employer_name": None,  # normalized strictly from structured extraction if present
                "employer_address": None,
            }

        elif pid == "P4D":
            app_facts = {
                "occupation": applicant_party.occupation,
                "income": applicant_party.income,
                "years_in_occupation": applicant_party.years_in_occupation,
            }
            for g in guarantors_list:
                guar_facts.append({
                    "party_id": g.party_id,
                    "occupation": g.occupation,
                    "income": g.income,
                    "years_in_occupation": g.years_in_occupation,
                })

        elif pid == "P5":
            shared = {
                "bank_statements_present": bool(bank_docs_found),
                "bank_docs_count": len(bank_docs_found),
            }
            if not bank_docs_found:
                missing_fields.append("bank_statements")

        elif pid in ("P5A", "P5B", "P5C"):
            shared = {
                "bank_statement_count": len(bank_docs_found),
            }
            if not bank_docs_found:
                missing_fields.append("bank_records")

        elif pid == "P6":
            shared = {
                "findings_count": len(norm_findings),
                "has_findings": bool(norm_findings),
            }

        elif pid == "P7":
            shared = {
                "is_vehicle_machinery_loan": bool(
                    vehicle_docs_found or (app.loan_type and "vehicle" in app.loan_type.lower())
                ),
                "vehicle_machinery_docs": sorted(list(set(vehicle_docs_found))),
                "findings": [f.model_dump() for f in norm_findings if f.particular_id == "P7"],
            }

        elif pid == "P7A":
            invoice_docs = [d.original_name or d.file_path for d in normalized_docs if d.document_type == "invoice"]
            shared = {
                "for_new_vehicles_machineries": True,
                "invoice_documents": invoice_docs,
                "findings": [f.model_dump() for f in norm_findings if f.particular_id == "P7A"],
            }

        elif pid == "P7A1":
            p7a1_findings = [f for f in norm_findings if f.particular_id == "P7A1"]
            shared = {
                "dealer_subdealer_details": [f.answer for f in p7a1_findings] if p7a1_findings else [],
                "findings": [f.model_dump() for f in p7a1_findings],
            }

        elif pid == "P7A2":
            p7a2_findings = [f for f in norm_findings if f.particular_id == "P7A2"]
            invoice_docs = [d.original_name or d.file_path for d in normalized_docs if d.document_type == "invoice"]
            shared = {
                "invoice_documents_present": bool(invoice_docs),
                "advance_payment_receipt_findings": [f.answer for f in p7a2_findings] if p7a2_findings else [],
                "findings": [f.model_dump() for f in p7a2_findings],
            }

        elif pid == "P7B":
            rc_docs = [d.original_name or d.file_path for d in normalized_docs if d.document_type == "vehicle_document"]
            shared = {
                "for_old_used_vehicles_machineries": True,
                "rc_documents": rc_docs,
                "findings": [f.model_dump() for f in norm_findings if f.particular_id == "P7B"],
            }

        elif pid == "P7B1":
            p7b1_findings = [f for f in norm_findings if f.particular_id == "P7B1"]
            shared = {
                "rc_machinery_number_verification": [f.answer for f in p7b1_findings] if p7b1_findings else [],
                "findings": [f.model_dump() for f in p7b1_findings],
            }

        elif pid == "P7B2":
            p7b2_findings = [f for f in norm_findings if f.particular_id == "P7B2"]
            shared = {
                "charge_hypothecation_noted_in_rc": [f.answer for f in p7b2_findings] if p7b2_findings else [],
                "findings": [f.model_dump() for f in p7b2_findings],
            }

        elif pid == "P7B3":
            p7b3_findings = [f for f in norm_findings if f.particular_id == "P7B3"]
            shared = {
                "insurance_details_validity": [f.answer for f in p7b3_findings] if p7b3_findings else [],
                "findings": [f.model_dump() for f in p7b3_findings],
            }

        is_missing = bool(missing_fields)
        if is_missing:
            fact_status = "missing" if len(app_facts) == 0 and len(shared) == 0 else "partial"
        else:
            fact_status = "available"

        pcf = ParticularContextFact(
            particular_id=pid,
            section=item.section,
            title=item.title,
            parent_id=item.parent_id,
            applicant_supported=item.applicant_supported,
            guarantor_supported=item.guarantor_supported,
            data_type=item.data_type,
            required=item.required,
            narrative_allowed=item.narrative_allowed,
            evidence_category=item.evidence_category,
            display_order=item.display_order,
            applicant_facts=app_facts,
            guarantor_facts=guar_facts,
            shared_facts=shared,
            status=fact_status,
            missing_fields=missing_fields,
        )
        particular_facts_map[pid] = pcf

        if item.required and is_missing:
            missing_info_list.append(
                MissingParticularInfo(
                    particular_id=pid,
                    title=item.title,
                    required=item.required,
                    missing=True,
                    missing_fields=missing_fields,
                )
            )

    # ─────────────────────────────────────────────────────────────────
    # 8. Report Metadata
    # ─────────────────────────────────────────────────────────────────
    report_meta = ReportMetadata(
        application_id=app.id,
        application_number=f"APP-{app.id:06d}",
        template_key=template.template_key,
        template_version=template.template_version,
        loan_type=app.loan_type,
        loan_amount=app.loan_amount,
        branch=app.branch or "Main Branch",
        report_date=datetime.utcnow().strftime("%Y-%m-%d"),
        borrower_name=app.applicant_name,
        officer_name=app.user.name if app.user else None,
    )

    # ─────────────────────────────────────────────────────────────────
    # 9. Deterministic Input Hash (SHA-256)
    # ─────────────────────────────────────────────────────────────────
    # Build canonical payload of normalized facts and metadata
    hashable_payload = {
        "application_id": app.id,
        "applicant_name": app.applicant_name,
        "loan_type": app.loan_type,
        "loan_amount": float(app.loan_amount) if app.loan_amount is not None else None,
        "branch": app.branch,
        "applicant": applicant_party.model_dump(),
        "guarantors": [g.model_dump() for g in guarantors_list],
        "site_verification": site_dict,
        "property_details": prop_dict,
        "gov_verification": {
            "pan_aadhaar_link_status": norm_gov.pan_aadhaar_link_status,
            "aadhaar_validity_status": norm_gov.aadhaar_validity_status,
            "tax_receipt_status": norm_gov.tax_receipt_status,
        },
        "documents": [
            {"id": d.document_id, "type": d.document_type, "ocr": d.ocr_status}
            for d in normalized_docs
        ],
        "findings": [
            {"id": f.finding_id, "pid": f.particular_id, "status": f.status}
            for f in norm_findings
        ],
        "template_key": template.template_key,
        "template_version": template.template_version,
    }
    canonical_json = json.dumps(hashable_payload, sort_keys=True, separators=(",", ":"))
    input_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    return ReportContext(
        metadata=report_meta,
        applicant=applicant_party,
        guarantors=guarantors_list,
        particular_facts=particular_facts_map,
        all_facts=all_facts,
        documents=normalized_docs,
        government_verification=norm_gov,
        findings=norm_findings,
        site_verification=site_dict,
        property_details=prop_dict,
        missing_information=missing_info_list,
        input_hash=input_hash,
    )
