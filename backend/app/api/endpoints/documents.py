"""Document upload and processing endpoints."""
import os, uuid, logging, json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List

from app.db.database import get_db
from app.models.document import Document, DocumentType
from app.models.application import Application, GovVerification
from app.models.user import User
from app.schemas.document import DocumentOut
from app.core.security import get_current_user
from app.core.config import settings
from app.services.preprocessing import preprocess_image
from app.services.ocr_service import extract_text, run_multipass_ocr
from app.services.nlp_service import extract_information
from app.services.classification_service import classify_document
from app.services.gemini_vision_service import (
    should_use_vision_fallback,
    extract_aadhaar_address_via_gemini,
)

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


from typing import List, Optional

@router.post("/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    application_id: int = Form(...),
    document_type: str = Form(...),
    joint_applicant_index: Optional[int] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a single document for an application."""
    # Validate application ownership
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    # Validate application ownership/access (admin and loan_officer can upload documents)
    if current_user.role not in ("admin", "loan_officer"):
        raise HTTPException(status_code=403, detail="Access denied")

    # Validate file extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type {ext} not allowed")

    # Read and size-check
    contents = await file.read()
    if len(contents) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large")

    # Persist file
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, filename)
    with open(file_path, "wb") as f:
        f.write(contents)

    # Validate document_type enum
    try:
        doc_type = DocumentType(document_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid document_type: {document_type}")

    doc = Document(
        application_id=application_id,
        document_type=doc_type,
        joint_applicant_index=joint_applicant_index,
        file_path=file_path,
        original_name=file.filename,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    logger.info(f"Document {doc.id} uploaded for application {application_id}")
    return doc


@router.post("/process/{document_id}", response_model=DocumentOut)
def process_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Preprocess image + run OCR on a document."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Preprocess (skip for PDFs handled by pdf2image inside ocr_service)
    if not doc.file_path.endswith(".pdf"):
        try:
            preprocessed_path = preprocess_image(doc.file_path)
        except Exception as e:
            logger.warning(f"Preprocessing failed: {e}")
            preprocessed_path = doc.file_path
    else:
        preprocessed_path = doc.file_path

    doc.processed = 1
    db.commit()

    # OCR and NLP Extraction via Multi-Pass Pipeline
    try:
        # 1. Run multi-pass OCR workflow with image quality analysis
        multipass_result = run_multipass_ocr(doc.file_path, doc_type=doc.document_type.value)
        doc.extracted_text = multipass_result.get("primary_text", "")

        # 2. Multi-pass consensus and field-level confidence extraction
        structured_data = extract_information(multipass_result, doc_type=doc.document_type.value)

        # 3. Aadhaar address quality gate – Gemini Vision fallback
        #    Triggered only when the OCR address is missing or garbled.
        #    Does NOT affect any other fields.
        if doc.document_type.value == "aadhaar":
            ocr_address = structured_data.get("address")
            if should_use_vision_fallback(ocr_address, doc.file_path):
                logger.info(
                    f"Document {document_id}: OCR address quality insufficient – "
                    f"invoking Gemini Vision address fallback"
                )
                vision_address = extract_aadhaar_address_via_gemini(
                    doc.file_path,
                    applicant_name=structured_data.get("applicant_name") or structured_data.get("name"),
                    aadhaar_number=structured_data.get("aadhaar_number"),
                )
                if vision_address:
                    structured_data["address"] = vision_address
                    if "fields" not in structured_data:
                        structured_data["fields"] = {}
                    structured_data["fields"]["address"] = {
                        "value": vision_address,
                        "confidence": 0.88,
                        "confidence_level": "high",
                        "source_document": "aadhaar",
                        "extraction_method": "gemini_vision_fallback",
                        "validation_status": "valid",
                        "raw_match": vision_address,
                        "warning": None,
                    }
                    logger.info(
                        f"Document {document_id}: Gemini Vision address set "
                        f"({len(vision_address)} chars)"
                    )
                else:
                    logger.warning(
                        f"Document {document_id}: Gemini Vision address fallback "
                        f"returned nothing – retaining OCR result with warning"
                    )
                    if "fields" not in structured_data:
                        structured_data["fields"] = {}
                    if "address" not in structured_data["fields"]:
                        structured_data["fields"]["address"] = {"value": ocr_address or ""}
                    structured_data["fields"]["address"]["warning"] = (
                        "⚠ Address could not be confidently extracted — enter manually"
                    )
                    structured_data["fields"]["address"]["confidence_level"] = "low"
                    structured_data["fields"]["address"]["extraction_method"] = "ocr_low_confidence"

            # Preserve all original fields and ensure canonical & alias compatibility:
            # 1. Name: ensure both 'applicant_name' and 'name' are present and synchronized
            name_val = structured_data.get("applicant_name") or structured_data.get("name")
            if name_val:
                structured_data["applicant_name"] = name_val
                structured_data["name"] = name_val
                if "fields" in structured_data:
                    name_meta = structured_data["fields"].get("applicant_name") or structured_data["fields"].get("name")
                    if name_meta:
                        structured_data["fields"]["applicant_name"] = name_meta
                        structured_data["fields"]["name"] = name_meta

            # 2. Aadhaar Number: preserve and ensure all alias keys are populated
            aadhaar_val = (
                structured_data.get("aadhaar_number")
                or structured_data.get("aadhaar")
                or structured_data.get("aadhaar_no")
                or structured_data.get("uid")
            )
            if aadhaar_val:
                structured_data["aadhaar_number"] = aadhaar_val
                structured_data["aadhaar"] = aadhaar_val
                structured_data["aadhaar_no"] = aadhaar_val
                structured_data["uid"] = aadhaar_val
                if "fields" in structured_data:
                    aadhaar_meta = (
                        structured_data["fields"].get("aadhaar_number")
                        or structured_data["fields"].get("aadhaar")
                        or structured_data["fields"].get("aadhaar_no")
                        or structured_data["fields"].get("uid")
                    )
                    if aadhaar_meta:
                        for k_alias in ["aadhaar_number", "aadhaar", "aadhaar_no", "uid"]:
                            structured_data["fields"][k_alias] = aadhaar_meta

            # 3. Father's Name: ensure both 'father_name' and 'father\'s_name' are populated
            father_val = structured_data.get("father_name") or structured_data.get("father's_name")
            if father_val:
                structured_data["father_name"] = father_val
                structured_data["father's_name"] = father_val
                if "fields" in structured_data:
                    father_meta = structured_data["fields"].get("father_name") or structured_data["fields"].get("father's_name")
                    if father_meta:
                        structured_data["fields"]["father_name"] = father_meta
                        structured_data["fields"]["father's_name"] = father_meta

        doc.structured_data = json.dumps(structured_data)
        doc.processed = 2
        db.commit()
        logger.info(f"Multi-pass OCR complete for document {document_id}: {len(doc.extracted_text)} chars")
    except Exception as e:
        logger.warning(f"Multi-pass OCR pipeline exception for {document_id}: {e}. Falling back to standard OCR.")
        try:
            text = extract_text(preprocessed_path)
            doc.extracted_text = text
            structured_data = extract_information(text, doc_type=doc.document_type.value)
            doc.structured_data = json.dumps(structured_data)
            doc.processed = 2
            db.commit()
        except Exception as e2:
            logger.error(f"Fallback OCR also failed for {document_id}: {e2}")
            doc.processed = 3  # Processing failed / requires review
            if not doc.structured_data:
                doc.structured_data = json.dumps({"status": "processing_failed", "error": str(e2)})
            db.commit()
            raise HTTPException(status_code=500, detail="Document processing failed")

    db.refresh(doc)
    return doc


@router.get("/{application_id}", response_model=List[DocumentOut])
def list_documents(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(Document).filter(Document.application_id == application_id).all()


@router.delete("/by-type/{application_id}/{doc_type}", status_code=200)
def delete_documents_by_type(
    application_id: int,
    doc_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete all documents of a given type for an application, clean up stored files, and reset gov verification status."""
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if current_user.role not in ("admin", "loan_officer"):
        raise HTTPException(status_code=403, detail="Access denied")

    docs = db.query(Document).filter(
        Document.application_id == application_id,
        Document.document_type == doc_type
    ).all()

    for doc in docs:
        if doc.file_path and os.path.exists(doc.file_path):
            try:
                os.remove(doc.file_path)
            except Exception as e:
                logger.warning(f"Could not remove file {doc.file_path}: {e}")
        if doc.file_path:
            base, _ = os.path.splitext(doc.file_path)
            prep_file = f"{base}_prep.png"
            if os.path.exists(prep_file):
                try:
                    os.remove(prep_file)
                except Exception:
                    pass
        db.delete(doc)

    gv = db.query(GovVerification).filter(GovVerification.application_id == application_id).first()
    if gv:
        if doc_type == "aadhaar":
            gv.aadhaar_validity_status = "Pending"
            gv.aadhaar_screenshot_path = None
        elif doc_type == "pan":
            gv.pan_aadhaar_link_status = "Pending"
            gv.screenshot_path = None

    if doc_type == "aadhaar":
        app.aadhaar_number = None
    elif doc_type == "pan":
        app.pan_number = None

    db.commit()
    logger.info(f"Deleted {len(docs)} documents of type {doc_type} for application {application_id}")
    return {"status": "deleted", "application_id": application_id, "document_type": doc_type, "deleted_count": len(docs)}


@router.delete("/{document_id}", status_code=200)
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a document by id, clean up stored files and preprocessed variants, and reset associated gov verification status."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if current_user.role not in ("admin", "loan_officer"):
        raise HTTPException(status_code=403, detail="Access denied")

    app_id = doc.application_id
    doc_type_val = doc.document_type.value if hasattr(doc.document_type, "value") else str(doc.document_type)

    # Clean up target file and prep variant
    files_to_remove = [doc.file_path]
    if doc.file_path:
        base, _ = os.path.splitext(doc.file_path)
        files_to_remove.append(f"{base}_prep.png")

    for fpath in files_to_remove:
        if fpath and os.path.exists(fpath):
            try:
                os.remove(fpath)
            except Exception as e:
                logger.warning(f"Could not remove file {fpath}: {e}")

    db.delete(doc)

    # Also clean up any other documents of this type for this application
    other_docs = db.query(Document).filter(
        Document.application_id == app_id,
        Document.document_type == doc.document_type
    ).all()
    for od in other_docs:
        if od.file_path and os.path.exists(od.file_path):
            try:
                os.remove(od.file_path)
            except Exception:
                pass
        if od.file_path:
            base, _ = os.path.splitext(od.file_path)
            p = f"{base}_prep.png"
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        db.delete(od)

    # Reset gov_verifications audit status and screenshot paths
    gv = db.query(GovVerification).filter(GovVerification.application_id == app_id).first()
    if gv:
        if doc_type_val == "aadhaar":
            gv.aadhaar_validity_status = "Pending"
            gv.aadhaar_screenshot_path = None
        elif doc_type_val == "pan":
            gv.pan_aadhaar_link_status = "Pending"
            gv.screenshot_path = None

    app_record = db.query(Application).filter(Application.id == app_id).first()
    if app_record:
        if doc_type_val == "aadhaar":
            app_record.aadhaar_number = None
        elif doc_type_val == "pan":
            app_record.pan_number = None

    db.commit()
    logger.info(f"Document {document_id} and all {doc_type_val} documents for app {app_id} deleted successfully")
    return {"status": "deleted", "id": document_id, "application_id": app_id, "document_type": doc_type_val}
