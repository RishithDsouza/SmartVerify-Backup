"""
Report Context Schemas for SmartVerify Report Generation System.
Defines normalized, typed, machine-readable representations of verified
application facts, parties, documents, government verifications, and particulars.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class FactProvenance(BaseModel):
    fact_id: str = Field(..., description="Unique deterministic fact key, e.g. 'applicant.pan_number'")
    value: Any = Field(None, description="Raw normalized fact value")
    status: str = Field(..., description="Status: 'available', 'missing', 'not_applicable', 'unverified'")
    source: str = Field(..., description="Database source table/entity, e.g. 'applications', 'joint_applicants'")
    source_id: Optional[str] = Field(None, description="Database record identifier, e.g. 'app:10', 'ja:2'")
    particular_ids: List[str] = Field(default_factory=list, description="Associated report Particular IDs, e.g. ['P1', 'P3']")
    party_id: Optional[str] = Field(None, description="Associated party ID, e.g. 'applicant', 'guarantor:1'")


class NormalizedParty(BaseModel):
    party_id: str = Field(..., description="Unique identifier within application, e.g. 'applicant', 'guarantor:1'")
    party_type: str = Field(..., description="'applicant', 'co_applicant', 'guarantor'")
    name: Optional[str] = None
    father_name: Optional[str] = None
    address: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    dob: Optional[str] = None
    aadhaar_number: Optional[str] = None
    masked_aadhaar_number: Optional[str] = None
    pan_number: Optional[str] = None
    occupation: Optional[str] = None
    years_in_occupation: Optional[int] = None
    income: Optional[float] = None
    income_period: Optional[str] = None
    relationship_to_applicant: Optional[str] = None


class NormalizedDocumentMeta(BaseModel):
    document_id: int
    document_type: str
    party_id: Optional[str] = None
    original_name: Optional[str] = None
    file_path: str
    ocr_status: str = Field("raw", description="'raw', 'preprocessed', 'ocr_done'")
    has_extracted_text: bool = False
    has_structured_data: bool = False
    created_at: Optional[str] = None


class GovernmentScreenshotMeta(BaseModel):
    screenshot_id: int
    verification_type: str
    government_portal: str
    verification_reference: Optional[str] = None
    verification_status: Optional[str] = None
    screenshot_path: str
    captured_at: Optional[str] = None
    source_url: Optional[str] = None


class NormalizedGovVerification(BaseModel):
    pan_aadhaar_link_status: Optional[str] = None
    aadhaar_validity_status: Optional[str] = None
    tax_receipt_status: Optional[str] = None
    officer_name: Optional[str] = None
    timestamp: Optional[str] = None
    remarks: Optional[str] = None
    aadhaar_screenshot_filename: Optional[str] = None
    pan_screenshot_filename: Optional[str] = None
    screenshots: List[GovernmentScreenshotMeta] = Field(default_factory=list)


from app.schemas.fraud_remarks import DocumentFraudRemarksSection


class NormalizedFinding(BaseModel):
    finding_id: int
    particular_id: Optional[str] = None
    category: Optional[str] = None
    question: Optional[str] = None
    answer: Optional[str] = None
    status: str = "REVIEW"
    confidence: Optional[float] = None
    source: str = "application_findings"
    linked_document_ids: List[int] = Field(default_factory=list)
    party_id: Optional[str] = None
    evidence: Dict[str, Any] = Field(default_factory=dict)


class ParticularContextFact(BaseModel):
    particular_id: str
    section: str
    title: str
    parent_id: Optional[str] = None
    applicant_supported: bool
    guarantor_supported: bool
    data_type: str
    required: bool
    narrative_allowed: bool
    evidence_category: Optional[str] = None
    display_order: int
    applicant_facts: Dict[str, Any] = Field(default_factory=dict)
    guarantor_facts: List[Dict[str, Any]] = Field(default_factory=list)
    shared_facts: Dict[str, Any] = Field(default_factory=dict)
    status: str = Field("available", description="'available', 'partial', 'missing', 'not_applicable'")
    missing_fields: List[str] = Field(default_factory=list)


class MissingParticularInfo(BaseModel):
    particular_id: str
    title: str
    required: bool
    missing: bool = True
    missing_fields: List[str] = Field(default_factory=list)


class ReportMetadata(BaseModel):
    application_id: int
    application_number: str
    template_key: str
    template_version: str
    loan_type: Optional[str] = None
    loan_amount: float
    branch: Optional[str] = None
    report_date: str
    borrower_name: Optional[str] = None
    officer_name: Optional[str] = None


class ReportContext(BaseModel):
    metadata: ReportMetadata
    applicant: NormalizedParty
    guarantors: List[NormalizedParty] = Field(default_factory=list)
    particular_facts: Dict[str, ParticularContextFact] = Field(default_factory=dict)
    all_facts: List[FactProvenance] = Field(default_factory=list)
    documents: List[NormalizedDocumentMeta] = Field(default_factory=list)
    government_verification: NormalizedGovVerification
    findings: List[NormalizedFinding] = Field(default_factory=list)
    site_verification: Dict[str, Any] = Field(default_factory=dict)
    property_details: Dict[str, Any] = Field(default_factory=dict)
    missing_information: List[MissingParticularInfo] = Field(default_factory=list)
    fraud_remarks: DocumentFraudRemarksSection = Field(default_factory=DocumentFraudRemarksSection)
    input_hash: str = Field(..., description="Deterministic SHA-256 digest of normalized context facts")
