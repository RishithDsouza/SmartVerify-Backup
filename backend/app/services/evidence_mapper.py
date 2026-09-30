"""
Deterministic Evidence Mapper Service for SmartVerify Report Generation System.
Maps verified facts, documents, government screenshots, and application findings
to report Particulars deterministically according to the Phase 2 ReportTemplate.

Strict Guardrails:
- ZERO AI, LLM, Gemini, prompts, embeddings, RAG, or semantic similarity.
- ZERO direct database queries (consumes only ReportContext and ReportTemplate).
- ZERO filesystem or file mutations (no upload, download, copy, move, delete).
- ZERO binary duplication.
- Strict Applicant vs. Guarantor party isolation.
- Complete duplicate suppression and deterministic ordering.
- Explicit tracking of missing evidence.
"""
import os
from typing import Dict, List, Optional, Set, Tuple, Any
from app.report_templates import get_template
from app.report_templates.schema import ReportTemplate, ParticularItem
from app.schemas.report_context import (
    ReportContext,
    NormalizedDocumentMeta,
    GovernmentScreenshotMeta,
    NormalizedFinding,
)
from app.schemas.evidence_map import (
    EvidenceReference,
    EvidenceMapping,
    EvidenceMap,
)


def _resolve_party_name_map(context: ReportContext) -> Dict[str, str]:
    """Builds a lookup mapping from normalized party_id to full legal name."""
    name_map: Dict[str, str] = {}
    if context.applicant and context.applicant.name:
        name_map["applicant"] = context.applicant.name

    for idx, g in enumerate(context.guarantors):
        if g.name:
            name_map[g.party_id] = g.name
            # Also support joint_applicant index alias if present
            name_map[f"joint_applicant:{idx}"] = g.name
            name_map[f"guarantor:{idx}"] = g.name
            name_map[f"co_applicant:{idx}"] = g.name
            if ":" in g.party_id:
                p_idx = g.party_id.split(":")[-1]
                name_map[f"joint_applicant:{p_idx}"] = g.name
                name_map[f"guarantor:{p_idx}"] = g.name
                name_map[f"co_applicant:{p_idx}"] = g.name

    return name_map


def _is_applicant_party(party_id: Optional[str]) -> bool:
    """Returns True if the party_id explicitly designates the primary applicant."""
    if not party_id:
        return False
    return party_id.strip().lower() == "applicant"


def _is_guarantor_party(party_id: Optional[str]) -> bool:
    """Returns True if the party_id designates a co-applicant or guarantor."""
    if not party_id:
        return False
    p = party_id.strip().lower()
    return p != "applicant" and ("guarantor" in p or "joint_applicant" in p or "co_applicant" in p)


def _build_doc_reference(
    doc: NormalizedDocumentMeta,
    particular_id: str,
    evidence_category: str,
    party_name_map: Dict[str, str],
) -> EvidenceReference:
    """Creates a strongly-typed EvidenceReference from a NormalizedDocumentMeta."""
    fname = doc.original_name or os.path.basename(doc.file_path)
    p_name = party_name_map.get(doc.party_id) if doc.party_id else None

    return EvidenceReference(
        evidence_id=f"doc:{doc.document_id}",
        evidence_type="document",
        source="documents",
        source_id=str(doc.document_id),
        filename=fname,
        document_type=doc.document_type,
        party_id=doc.party_id,
        party_name=p_name,
        particular_id=particular_id,
        evidence_category=evidence_category,
        status="available",
        metadata={
            "file_path": doc.file_path,
            "ocr_status": doc.ocr_status,
            "has_extracted_text": doc.has_extracted_text,
            "has_structured_data": doc.has_structured_data,
        },
    )


def _build_screenshot_reference(
    gs: GovernmentScreenshotMeta,
    particular_id: str,
    applicant_name: Optional[str],
) -> EvidenceReference:
    """Creates a strongly-typed EvidenceReference from a GovernmentScreenshotMeta."""
    fname = os.path.basename(gs.screenshot_path)

    return EvidenceReference(
        evidence_id=f"gov_shot:{gs.screenshot_id}",
        evidence_type="government_screenshot",
        source="government_verification_screenshots",
        source_id=str(gs.screenshot_id),
        filename=fname,
        document_type=gs.verification_type,
        party_id="applicant",
        party_name=applicant_name,
        particular_id=particular_id,
        evidence_category="government_verification",
        status=gs.verification_status or "VERIFIED",
        metadata={
            "government_portal": gs.government_portal,
            "verification_reference": gs.verification_reference,
            "verification_status": gs.verification_status,
            "screenshot_path": gs.screenshot_path,
            "captured_at": gs.captured_at,
            "source_url": gs.source_url,
        },
    )


def _build_finding_reference(
    finding: NormalizedFinding,
    particular_id: str,
    evidence_category: str,
    party_name_map: Dict[str, str],
) -> EvidenceReference:
    """Creates a strongly-typed EvidenceReference from a NormalizedFinding."""
    p_name = party_name_map.get(finding.party_id) if finding.party_id else None

    return EvidenceReference(
        evidence_id=f"finding:{finding.finding_id}",
        evidence_type="finding",
        source="application_findings",
        source_id=str(finding.finding_id),
        filename=None,
        document_type=finding.category,
        party_id=finding.party_id,
        party_name=p_name,
        particular_id=particular_id,
        evidence_category=evidence_category,
        status=finding.status or "REVIEW",
        metadata={
            "question": finding.question,
            "answer": finding.answer,
            "confidence": finding.confidence,
            "linked_document_ids": finding.linked_document_ids,
        },
    )


def _sort_key(ref: EvidenceReference) -> Tuple:
    """
    Deterministic sort key:
    1. Evidence category (alphabetical)
    2. Party hierarchy: Applicant (0), Guarantor (1), Shared (2)
    3. Party ID (alphabetical)
    4. Source type (alphabetical)
    5. Source ID (numerical if possible, else string)
    6. Evidence ID
    """
    party_rank = 2
    if ref.party_id == "applicant":
        party_rank = 0
    elif ref.party_id is not None:
        party_rank = 1

    try:
        numeric_source_id = int(ref.source_id)
    except (ValueError, TypeError):
        numeric_source_id = 999999999

    return (
        ref.evidence_category or "",
        party_rank,
        ref.party_id or "",
        ref.source,
        numeric_source_id,
        ref.source_id,
        ref.evidence_id,
    )


def map_evidence(
    context: ReportContext,
    template: Optional[ReportTemplate] = None,
) -> EvidenceMap:
    """
    Deterministically maps verified documents, government verification screenshots,
    and application findings from ReportContext to template Particulars.
    Zero direct database queries, zero AI/LLM calls, and zero filesystem mutations.
    """
    if template is None:
        template = get_template(context.metadata.template_key, context.metadata.template_version)

    party_name_map = _resolve_party_name_map(context)
    applicant_name = context.applicant.name if context.applicant else None

    particular_mappings: Dict[str, EvidenceMapping] = {}
    missing_evidence_particular_ids: List[str] = []
    total_evidence_count = 0

    # KYC Document Types
    kyc_doc_types = {"aadhaar", "pan", "driving_licence", "voter_id", "passport"}
    # Income Document Types
    income_doc_types = {"salary_slip", "income_cert", "form_16", "itr"}
    # Residence / Site Document Types
    residence_doc_types = {
        "residence_proof", "house_photograph", "site_front_view",
        "site_side_view", "site_interior", "site_entrance", "site_landmark",
        "property_image", "sale_deed", "tax_receipt", "encumbrance_cert",
        "building_plan", "ownership_proof",
        "geo_site_front_view", "geo_site_side_view", "geo_site_interior",
        "geo_site_entrance", "geo_site_landmark"
    }
    # Bank Document Types
    bank_doc_types = {"bank_statement"}

    for item in template.particulars:
        pid = item.id
        ev_category = item.evidence_category

        raw_candidates: List[EvidenceReference] = []
        seen_keys: Set[Tuple[str, str]] = set()

        def add_ref(ref: EvidenceReference):
            key = (ref.source, ref.source_id)
            if key not in seen_keys:
                seen_keys.add(key)
                raw_candidates.append(ref)

        # ─────────────────────────────────────────────────────────────
        # 1. Particular-specific deterministic mapping rules
        # ─────────────────────────────────────────────────────────────
        if pid == "P1":
            # Primary applicant and guarantor KYC proofs
            for d in context.documents:
                if d.document_type in kyc_doc_types:
                    category = "applicant_kyc" if _is_applicant_party(d.party_id) else "guarantor_kyc"
                    add_ref(_build_doc_reference(d, pid, category, party_name_map))

            # Identity findings
            for f in context.findings:
                if f.particular_id == "P1" or f.category in ["identity", "address"]:
                    add_ref(_build_finding_reference(f, pid, ev_category or "applicant_kyc", party_name_map))

        elif pid == "P2":
            # Residence verification documents & site images
            for d in context.documents:
                if d.document_type in residence_doc_types:
                    add_ref(_build_doc_reference(d, pid, "residence_evidence", party_name_map))

            for f in context.findings:
                if f.particular_id == "P2" or f.category in ["residence", "address_verification"]:
                    add_ref(_build_finding_reference(f, pid, "residence_evidence", party_name_map))

        elif pid == "P2A":
            # Physical visit confirmation & site visit photographs
            site_photo_types = {
                "site_front_view", "site_side_view", "site_interior",
                "site_entrance", "site_landmark", "house_photograph", "property_image", "residence_proof",
                "geo_site_front_view", "geo_site_side_view", "geo_site_interior",
                "geo_site_entrance", "geo_site_landmark"
            }
            for d in context.documents:
                if d.document_type in site_photo_types:
                    add_ref(_build_doc_reference(d, pid, "residence_evidence", party_name_map))

            for f in context.findings:
                if f.particular_id == "P2A":
                    add_ref(_build_finding_reference(f, pid, "residence_evidence", party_name_map))

        elif pid == "P2B":
            # Visiting official and date of visit evidence
            for d in context.documents:
                if d.document_type in {"site_front_view", "site_side_view", "house_photograph", "geo_site_front_view", "geo_site_side_view"}:
                    add_ref(_build_doc_reference(d, pid, "residence_evidence", party_name_map))

            for f in context.findings:
                if f.particular_id == "P2B":
                    add_ref(_build_finding_reference(f, pid, "residence_evidence", party_name_map))

        elif pid == "P2C":
            # Reason for not physically visiting (optional particular)
            for f in context.findings:
                if f.particular_id == "P2C":
                    add_ref(_build_finding_reference(f, pid, "other_relevant_evidence", party_name_map))

        elif pid == "P2D":
            # Status of residence / tenure proof
            tenure_doc_types = {
                "residence_proof", "sale_deed", "tax_receipt", "encumbrance_cert",
                "building_plan", "ownership_proof"
            }
            for d in context.documents:
                if d.document_type in tenure_doc_types:
                    add_ref(_build_doc_reference(d, pid, "residence_evidence", party_name_map))

            for f in context.findings:
                if f.particular_id == "P2D":
                    add_ref(_build_finding_reference(f, pid, "residence_evidence", party_name_map))

        elif pid == "P2E":
            # Aadhaar No. and portal verification
            for d in context.documents:
                if d.document_type == "aadhaar":
                    category = "applicant_kyc" if _is_applicant_party(d.party_id) else "guarantor_kyc"
                    add_ref(_build_doc_reference(d, pid, category, party_name_map))

            for gs in context.government_verification.screenshots:
                if gs.verification_type in ["uidai_aadhaar", "aadhaar"]:
                    add_ref(_build_screenshot_reference(gs, pid, applicant_name))

            for f in context.findings:
                if f.particular_id == "P2E":
                    add_ref(_build_finding_reference(f, pid, "government_verification", party_name_map))

        elif pid == "P3":
            # Comprehensive KYC verification (Aadhaar, PAN, DL + Govt portal screenshots)
            for d in context.documents:
                if d.document_type in kyc_doc_types:
                    category = "applicant_kyc" if _is_applicant_party(d.party_id) else "guarantor_kyc"
                    add_ref(_build_doc_reference(d, pid, category, party_name_map))

            for gs in context.government_verification.screenshots:
                add_ref(_build_screenshot_reference(gs, pid, applicant_name))

            for f in context.findings:
                if f.particular_id == "P3" or f.category in ["identity", "kyc"]:
                    add_ref(_build_finding_reference(f, pid, "government_verification", party_name_map))

        elif pid == "P4":
            # Income substantiation
            for d in context.documents:
                if d.document_type in income_doc_types:
                    add_ref(_build_doc_reference(d, pid, "income_documents", party_name_map))

            for f in context.findings:
                if f.particular_id == "P4" or f.category in ["income", "employment"]:
                    add_ref(_build_finding_reference(f, pid, "income_documents", party_name_map))

        elif pid == "P4A":
            # Income documents seen
            for d in context.documents:
                if d.document_type in income_doc_types:
                    add_ref(_build_doc_reference(d, pid, "income_documents", party_name_map))

            for f in context.findings:
                if f.particular_id == "P4A":
                    add_ref(_build_finding_reference(f, pid, "income_documents", party_name_map))

        elif pid == "P4B":
            # Pertains to year & remarks on genuineness
            for d in context.documents:
                if d.document_type in income_doc_types:
                    add_ref(_build_doc_reference(d, pid, "income_documents", party_name_map))

            for f in context.findings:
                if f.particular_id == "P4B":
                    add_ref(_build_finding_reference(f, pid, "income_documents", party_name_map))

        elif pid == "P4C":
            # Salaried employer name and address
            for d in context.documents:
                if d.document_type in {"salary_slip", "form_16"}:
                    add_ref(_build_doc_reference(d, pid, "income_documents", party_name_map))

            for f in context.findings:
                if f.particular_id == "P4C":
                    add_ref(_build_finding_reference(f, pid, "income_documents", party_name_map))

        elif pid == "P4D":
            # Designation / occupation / income details
            for d in context.documents:
                if d.document_type in income_doc_types:
                    add_ref(_build_doc_reference(d, pid, "income_documents", party_name_map))

            for f in context.findings:
                if f.particular_id == "P4D":
                    add_ref(_build_finding_reference(f, pid, "income_documents", party_name_map))

        elif pid in ["P5", "P5A", "P5B", "P5C"]:
            # Bank statement & operative banking conduct
            for d in context.documents:
                if d.document_type in bank_doc_types:
                    add_ref(_build_doc_reference(d, pid, "bank_documents", party_name_map))

            for f in context.findings:
                if f.particular_id == pid or (pid == "P5" and f.category in ["banking", "bank"]):
                    add_ref(_build_finding_reference(f, pid, "bank_documents", party_name_map))

        elif pid == "P6":
            # Track Record / Criminal Antecedents
            for f in context.findings:
                if f.particular_id == "P6" or f.category in [
                    "antecedents", "criminal", "legal", "track_record", "police", "court"
                ]:
                    add_ref(_build_finding_reference(f, pid, "other_relevant_evidence", party_name_map))

        elif pid == "P7":
            # Overall dealer & invoice / RC collateral verification
            for d in context.documents:
                if d.document_type in {"invoice", "vehicle_document"}:
                    cat = "vehicle_invoice" if d.document_type == "invoice" else "rc_evidence"
                    add_ref(_build_doc_reference(d, pid, cat, party_name_map))

            for f in context.findings:
                if f.particular_id == "P7" or f.category in ["collateral", "vehicle", "machinery"]:
                    add_ref(_build_finding_reference(f, pid, "vehicle_invoice", party_name_map))

        elif pid == "P7A":
            # For New Vehicles/Machineries
            for d in context.documents:
                if d.document_type == "invoice":
                    add_ref(_build_doc_reference(d, pid, "vehicle_invoice", party_name_map))

            for f in context.findings:
                if f.particular_id == "P7A":
                    add_ref(_build_finding_reference(f, pid, "vehicle_invoice", party_name_map))

        elif pid == "P7A1":
            # Dealer/Sub-dealer name/address
            for d in context.documents:
                fname_lower = (d.original_name or "").lower() + " " + (d.file_path or "").lower()
                if d.document_type in {"vehicle_document", "other", "invoice"} and "dealer" in fname_lower:
                    add_ref(_build_doc_reference(d, pid, "dealer_evidence", party_name_map))

            for f in context.findings:
                if f.particular_id == "P7A1" or f.category == "dealer":
                    add_ref(_build_finding_reference(f, pid, "dealer_evidence", party_name_map))

        elif pid == "P7A2":
            # Genuineness of Invoice/Advance Payment Receipts
            for d in context.documents:
                fname_lower = (d.original_name or "").lower() + " " + (d.file_path or "").lower()
                if d.document_type == "invoice" or "receipt" in fname_lower or "invoice" in fname_lower:
                    add_ref(_build_doc_reference(d, pid, "vehicle_invoice", party_name_map))

            for f in context.findings:
                if f.particular_id == "P7A2" or f.category in ["invoice", "margin_money"]:
                    add_ref(_build_finding_reference(f, pid, "vehicle_invoice", party_name_map))

        elif pid == "P7B":
            # For Old/Used Vehicles/Machineries
            for d in context.documents:
                if d.document_type == "vehicle_document":
                    add_ref(_build_doc_reference(d, pid, "rc_evidence", party_name_map))

            for f in context.findings:
                if f.particular_id == "P7B":
                    add_ref(_build_finding_reference(f, pid, "rc_evidence", party_name_map))

        elif pid == "P7B1":
            # Correctness of RC Book No./Machinery No.
            for d in context.documents:
                if d.document_type == "vehicle_document":
                    add_ref(_build_doc_reference(d, pid, "rc_evidence", party_name_map))

            for gs in context.government_verification.screenshots:
                if gs.verification_type in ["parivahan_vahan", "vahan", "rc_verification"]:
                    add_ref(_build_screenshot_reference(gs, pid, applicant_name))

            for f in context.findings:
                if f.particular_id == "P7B1" or f.category in ["rc_book", "vahan"]:
                    add_ref(_build_finding_reference(f, pid, "rc_evidence", party_name_map))

        elif pid == "P7B2":
            # Charge noted details in RC
            for d in context.documents:
                fname_lower = (d.original_name or "").lower() + " " + (d.file_path or "").lower()
                if d.document_type == "vehicle_document" and (
                    "charge" in fname_lower or "hypothecation" in fname_lower or "rc" in fname_lower
                ):
                    add_ref(_build_doc_reference(d, pid, "rc_evidence", party_name_map))

            for f in context.findings:
                if f.particular_id == "P7B2" or f.category in ["hypothecation", "charge"]:
                    add_ref(_build_finding_reference(f, pid, "rc_evidence", party_name_map))

        elif pid == "P7B3":
            # Insurance details & validity
            for d in context.documents:
                fname_lower = (d.original_name or "").lower() + " " + (d.file_path or "").lower()
                if d.document_type == "vehicle_document" and (
                    "insurance" in fname_lower or "policy" in fname_lower
                ):
                    add_ref(_build_doc_reference(d, pid, "rc_evidence", party_name_map))

            for f in context.findings:
                if f.particular_id == "P7B3" or f.category in ["insurance", "vehicle_insurance"]:
                    add_ref(_build_finding_reference(f, pid, "rc_evidence", party_name_map))

        # ─────────────────────────────────────────────────────────────
        # 2. Deterministic Ordering & Party Segregation
        # ─────────────────────────────────────────────────────────────
        sorted_evidence = sorted(raw_candidates, key=_sort_key)

        applicant_ev: List[EvidenceReference] = []
        guarantor_ev: List[EvidenceReference] = []
        shared_ev: List[EvidenceReference] = []

        for ref in sorted_evidence:
            if _is_applicant_party(ref.party_id):
                applicant_ev.append(ref)
            elif _is_guarantor_party(ref.party_id):
                guarantor_ev.append(ref)
            else:
                shared_ev.append(ref)

        # ─────────────────────────────────────────────────────────────
        # 3. Missing Evidence Evaluation
        # ─────────────────────────────────────────────────────────────
        evidence_missing = False
        missing_reason = None

        if len(sorted_evidence) == 0:
            if item.required or item.evidence_category is not None:
                # Except P2C which is conditional
                if pid != "P2C":
                    evidence_missing = True
                    missing_reason = f"No verified evidence found for category '{ev_category or 'required'}'"
                    missing_evidence_particular_ids.append(pid)

        mapping = EvidenceMapping(
            particular_id=pid,
            evidence_category=ev_category,
            evidence=sorted_evidence,
            applicant_evidence=applicant_ev,
            guarantor_evidence=guarantor_ev,
            shared_evidence=shared_ev,
            evidence_missing=evidence_missing,
            missing_reason=missing_reason,
        )

        particular_mappings[pid] = mapping
        total_evidence_count += len(sorted_evidence)

    return EvidenceMap(
        template_key=template.template_key,
        template_version=template.template_version,
        application_id=context.metadata.application_id,
        particular_mappings=particular_mappings,
        input_hash=context.input_hash,
        total_evidence_count=total_evidence_count,
        missing_evidence_particular_ids=missing_evidence_particular_ids,
    )
