"""Document ORM model."""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.db.database import Base

class DocumentType(str, enum.Enum):
    aadhaar          = "aadhaar"
    pan              = "pan"
    salary_slip      = "salary_slip"
    income_cert      = "income_cert"
    form_16          = "form_16"
    bank_statement   = "bank_statement"
    loan_application = "loan_application"
    
    # Property & Site Verification
    sale_deed        = "sale_deed"
    tax_receipt      = "tax_receipt"
    encumbrance_cert = "encumbrance_cert"
    building_plan    = "building_plan"
    ownership_proof  = "ownership_proof"
    property_image   = "property_image"
    
    site_front_view  = "site_front_view"
    site_side_view   = "site_side_view"
    site_interior    = "site_interior"
    site_entrance    = "site_entrance"
    site_landmark    = "site_landmark"

    geo_site_front_view = "geo_site_front_view"
    geo_site_side_view  = "geo_site_side_view"
    geo_site_interior   = "geo_site_interior"
    geo_site_entrance   = "geo_site_entrance"
    geo_site_landmark   = "geo_site_landmark"

    # Additional Identity, Income, Asset & Chatbot types
    driving_licence  = "driving_licence"
    itr              = "itr"
    residence_proof  = "residence_proof"
    house_photograph = "house_photograph"
    vehicle_document = "vehicle_document"
    invoice          = "invoice"
    chatbot_upload   = "chatbot_upload"
    other            = "other"

class Document(Base):
    __tablename__ = "documents"
    id             = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    document_type  = Column(SAEnum(DocumentType, native_enum=False), nullable=False)
    file_path      = Column(String(500), nullable=False)
    original_name  = Column(String(255))
    extracted_text = Column(Text)
    structured_data = Column(Text)  # JSON string containing NLP extracted fields
    processed      = Column(Integer, default=0)  # 0=raw,1=preprocessed,2=ocr-done
    joint_applicant_index = Column(Integer, nullable=True, default=None)
    created_at     = Column(DateTime, default=datetime.utcnow)
    application = relationship("Application", back_populates="documents")
    findings    = relationship("ApplicationFinding", secondary="finding_documents", back_populates="documents")
