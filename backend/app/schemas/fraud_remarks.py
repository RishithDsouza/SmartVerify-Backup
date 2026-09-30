"""
Document Verification & Fraud Remarks Schema.
Provides strongly-typed, normalized representations of document-related
fraud or suspicious findings for PDF reporting and review consistency.

Architectural Constraints:
- Preserves full internal traceability (finding ID, doc ID, doc type, severity, evidence ref).
- Hides internal IDs from public/visible display.
- Consumed by ReportContext, ValidatedReport, ReportReviewState, and PdfGenerator.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class DocumentFraudRemark(BaseModel):
    """
    Traceable representation of a single document-related fraud or suspicious finding.
    Internal IDs are preserved for auditability and must NOT be shown in visible PDF text.
    """
    finding_id: Optional[int] = Field(None, description="Internal finding identifier for traceability (hidden in PDF)")
    document_id: Optional[int] = Field(None, description="Internal document identifier for traceability (hidden in PDF)")
    document_type: Optional[str] = Field(None, description="Internal machine-readable document type (e.g. 'salary_slip')")
    document_name: str = Field(..., description="Human-readable document name (e.g. 'Salary Slip', 'PAN Card')")
    category: Optional[str] = Field(None, description="Finding category (e.g. 'fraud', 'income', 'identity')")
    severity: str = Field("REVIEW REQUIRED", description="Severity label (e.g. 'REVIEW REQUIRED', 'POTENTIAL FRAUD INDICATOR')")
    reason: str = Field(..., description="Verbatim upstream finding reason/answer")
    evidence_reference: Optional[str] = Field(None, description="Evidence reference or source identifier")
    source: str = Field("application_findings", description="Upstream source system/table")


class DocumentFraudRemarksSection(BaseModel):
    """
    Container for the Document Verification / Fraud Remarks section.
    """
    has_remarks: bool = Field(False, description="True if any suspicious/fraud document findings exist")
    status: str = Field(
        "No significant document fraud indicators detected.",
        description="Section status: 'No significant document fraud indicators detected.' or 'REVIEW REQUIRED'"
    )
    remarks: List[DocumentFraudRemark] = Field(default_factory=list, description="List of document fraud remarks")
