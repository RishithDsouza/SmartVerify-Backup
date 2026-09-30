"""
Validated Report Schemas for SmartVerify Report Generation System (Phase 7).
Defines strongly-typed, machine-readable representations of deterministic validation
results, issue severities, and audit traceability across the report pipeline.
"""
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field


class ValidationSeverity(str, Enum):
    """Deterministic validation issue severity levels."""
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"
    INFO = "INFO"


class ValidationIssue(BaseModel):
    """
    A single deterministic validation finding or inconsistency.
    """
    code: str = Field(..., description="Stable issue identifier, e.g. 'PROVENANCE_FACT_UNKNOWN', 'PII_UNMASKED_AADHAAR'")
    severity: ValidationSeverity = Field(..., description="BLOCKING, WARNING, or INFO")
    particular_id: Optional[str] = Field(None, description="Associated report Particular ID, e.g. 'P1', 'P2A'")
    message: str = Field(..., description="Human-readable explanation of the validation discrepancy")
    fact_ids: List[str] = Field(default_factory=list, description="Related FactProvenance IDs")
    evidence_ids: List[str] = Field(default_factory=list, description="Related EvidenceReference IDs")


class ValidationResult(BaseModel):
    """
    Validation results container for a single report Particular.
    """
    particular_id: str = Field(..., description="Target Particular ID, e.g. 'P1', 'P7A1'")
    status: str = Field(..., description="'VALID', 'WARNING', or 'INVALID'")
    issues: List[ValidationIssue] = Field(default_factory=list, description="List of issues identified for this Particular")
    validated: bool = Field(..., description="True if no BLOCKING issues exist for this row")
    checked_fact_ids: List[str] = Field(default_factory=list, description="All FactProvenance IDs checked")
    checked_evidence_ids: List[str] = Field(default_factory=list, description="All EvidenceReference IDs checked")


from app.schemas.fraud_remarks import DocumentFraudRemarksSection


class ValidatedReport(BaseModel):
    """
    Complete, strongly-typed Validated Report Package (Phase 7).
    Purely deterministic evaluation of ComposedReport against ReportContext,
    ReportTemplate, and EvidenceMap.
    Zero subjective scoring and zero LLM calls.
    """
    application_id: int = Field(..., description="Unique application identifier")
    template_key: str = Field(..., description="Template key from Phase 2, e.g. 'standard_reverification'")
    template_version: str = Field(..., description="Template version, e.g. 'v1.0'")
    input_hash: str = Field(..., description="Cryptographic SHA-256 fingerprint from Phase 3 ReportContext")
    composer_version: str = Field(..., description="Composer version from Phase 6 ComposedReport")
    overall_status: str = Field(..., description="'VALID', 'VALID_WITH_WARNINGS', or 'INVALID'")
    validation_results: Dict[str, ValidationResult] = Field(
        default_factory=dict,
        description="Dictionary of ValidationResult keyed by stable Particular ID"
    )
    blocking_issues: List[ValidationIssue] = Field(default_factory=list, description="All aggregated BLOCKING issues")
    warnings: List[ValidationIssue] = Field(default_factory=list, description="All aggregated WARNING issues")
    fraud_remarks: DocumentFraudRemarksSection = Field(default_factory=DocumentFraudRemarksSection)
    validated_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="ISO-8601 UTC timestamp of validation"
    )

    @property
    def is_valid(self) -> bool:
        """Returns True if there are zero BLOCKING issues."""
        return self.overall_status != "INVALID"

    def get_result(self, particular_id: str) -> Optional[ValidationResult]:
        """Retrieve validation result for a specific Particular ID."""
        return self.validation_results.get(particular_id)

    def get_issues_for_particular(self, particular_id: str) -> List[ValidationIssue]:
        """Retrieve issues list for a specific Particular ID."""
        res = self.validation_results.get(particular_id)
        return res.issues if res else []
