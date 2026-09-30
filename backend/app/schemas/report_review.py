"""
Report Review Schemas for SmartVerify Report Generation System (Phase 8).
Defines strongly-typed representations for in-memory officer review,
controlled manual text editing, audit trail tracking, evidence selection preferences,
and deterministic revalidation gating.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field

from app.schemas.validated_report import ValidationIssue, ValidationSeverity
from app.schemas.fraud_remarks import DocumentFraudRemarksSection


class ReportEdit(BaseModel):
    """
    Immutable audit record of a single manual edit to a report Particular.
    Tracks previous text, new text, officer identity, timestamp, reason, and revision.
    """
    particular_id: str = Field(..., description="Target Particular ID, e.g. 'P1', 'P4B'")
    previous_text: str = Field(..., description="Text before this edit was applied")
    new_text: str = Field(..., description="Text submitted by the reviewer")
    edited_by: str = Field(..., description="Username or ID of the reviewing officer")
    edited_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="ISO-8601 UTC timestamp when the edit occurred"
    )
    reason: Optional[str] = Field(None, description="Optional justification or note for the edit")
    revision: int = Field(..., description="Revision number created by this edit")


class EvidenceSelection(BaseModel):
    """
    Reviewer preference for mapped evidence display and inclusion.
    Does NOT alter Phase 5 EvidenceMap or move/copy/delete underlying files.
    """
    particular_id: str = Field(..., description="Target Particular ID")
    evidence_id: str = Field(..., description="Evidence reference ID")
    included: bool = Field(True, description="Whether this evidence is selected for the report")
    display_order: int = Field(0, description="Display order preference within the Particular")


class ReviewEvidenceItem(BaseModel):
    """
    Detailed evidence view item displayed in the officer review Evidence Panel.
    Derived strictly from Phase 5 EvidenceMap with immutable provenance.
    """
    evidence_id: str = Field(..., description="Stable EvidenceReference ID")
    evidence_type: str = Field(..., description="'document' or 'finding'")
    source: str = Field(..., description="Source document or finding title")
    filename: Optional[str] = Field(None, description="Filename or storage path if applicable")
    party: str = Field(..., description="'APPLICANT', 'GUARANTOR', or 'SHARED'")
    verification_status: str = Field(..., description="Status from finding/document (e.g. 'VERIFIED', 'REVIEW')")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional evidence metadata")
    included: bool = Field(True, description="Reviewer selection preference")
    display_order: int = Field(0, description="Display sequence number")


class ParticularReview(BaseModel):
    """
    Editable state representation of an individual report Particular.
    Preserves original AI text and deterministic text separately from current edited text.
    """
    particular_id: str = Field(..., description="Stable Particular identifier, e.g. 'P1', 'P4B'")
    title: str = Field(..., description="Display title from Phase 2 template")
    section_id: str = Field(..., description="Parent section ID, e.g. 'applicant_details'")
    section_title: str = Field(..., description="Parent section display title")
    deterministic_text: str = Field("", description="Phase 4 rendered baseline text (immutable)")
    original_ai_text: str = Field("", description="Phase 6 composed AI text (immutable)")
    current_text: str = Field("", description="Active Verification Details text (editable by officer)")
    is_edited: bool = Field(False, description="True if manually modified by reviewer")
    edit_count: int = Field(0, description="Number of manual edits applied to this Particular")
    fact_ids: List[str] = Field(default_factory=list, description="Immutable Fact IDs from Phase 3/6")
    evidence_ids: List[str] = Field(default_factory=list, description="Immutable Evidence IDs from Phase 5/6")
    evidence_items: List[ReviewEvidenceItem] = Field(default_factory=list, description="Evidence items for this Particular")
    missing_information: List[str] = Field(default_factory=list, description="Missing fields or evidence notes")
    validation_status: str = Field("VALID", description="'VALID', 'WARNING', or 'INVALID'")
    issues: List[ValidationIssue] = Field(default_factory=list, description="Current Phase 7 validation issues")
    ai_composed: bool = Field(False, description="True if originally drafted via AI composer")
    model_id: Optional[str] = Field(None, description="Original composing model identifier")
    prompt_version: Optional[str] = Field("v1.0", description="Original prompt version if composed")


class ReportReviewState(BaseModel):
    """
    Complete, strongly-typed in-memory review state for Phase 8.
    Encapsulates all 27 Particulars, edit history, revision tracking,
    evidence preferences, current validation status, and the PDF export gate.
    """
    application_id: int = Field(..., description="Application ID")
    template_key: str = Field(..., description="Phase 2 template key, e.g. 'standard_reverification'")
    template_version: str = Field(..., description="Phase 2 template version, e.g. 'v1.0'")
    input_hash: str = Field(..., description="SHA-256 fingerprint from Phase 3 ReportContext")
    review_status: str = Field("PENDING_REVIEW", description="'PENDING_REVIEW', 'UNDER_REVIEW', 'APPROVED', 'REJECTED'")
    validation_status: str = Field("VALID", description="'VALID', 'VALID_WITH_WARNINGS', 'INVALID'")
    revision: int = Field(1, description="Monotonically increasing revision counter (starts at 1)")
    particular_reviews: Dict[str, ParticularReview] = Field(
        default_factory=dict,
        description="Map of ParticularReview keyed by stable Particular ID"
    )
    edited_particular_ids: List[str] = Field(default_factory=list, description="Particular IDs that have been edited")
    evidence_selections: Dict[str, List[EvidenceSelection]] = Field(
        default_factory=dict,
        description="Map of evidence selection preferences keyed by Particular ID"
    )
    edit_history: List[ReportEdit] = Field(default_factory=list, description="Complete chronological audit trail of edits")
    blocking_issues: List[ValidationIssue] = Field(default_factory=list, description="Active BLOCKING validation issues")
    warnings: List[ValidationIssue] = Field(default_factory=list, description="Active WARNING validation issues")
    fraud_remarks: DocumentFraudRemarksSection = Field(default_factory=DocumentFraudRemarksSection)
    can_export_pdf: bool = Field(False, description="PDF export gate: True ONLY when validation passes with 0 blocking issues")
    created_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="ISO-8601 UTC timestamp of review initialization"
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="ISO-8601 UTC timestamp of last state modification"
    )

    def get_particular(self, particular_id: str) -> Optional[ParticularReview]:
        """Retrieve review state for a specific Particular ID."""
        return self.particular_reviews.get(particular_id)

    def get_current_text(self, particular_id: str) -> str:
        """Retrieve current active text for a Particular."""
        p = self.particular_reviews.get(particular_id)
        return p.current_text if p else ""


class ParticularEditRequest(BaseModel):
    """Request payload for updating Verification Details text for a Particular."""
    particular_id: str = Field(..., description="Target Particular ID")
    new_text: str = Field(..., description="Updated text content")
    edited_by: str = Field(..., description="Officer identifier")
    reason: Optional[str] = Field(None, description="Optional reason for the change")


class EvidenceToggleRequest(BaseModel):
    """Request payload for toggling inclusion/exclusion of mapped evidence."""
    particular_id: str = Field(..., description="Target Particular ID")
    evidence_id: str = Field(..., description="Target Evidence ID")
    included: bool = Field(..., description="Inclusion flag")
    display_order: Optional[int] = Field(None, description="Optional display order")
