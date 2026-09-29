"""Government Verification Screenshots endpoints.

Provides dedicated endpoints for managing verification screenshots (UIDAI Aadhaar,
Income Tax PAN-Aadhaar link) completely decoupled from borrower identity documents.
Screenshots are stored in `government_verification_screenshots` and do NOT trigger OCR.
"""
import os
import uuid
import logging
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.application import Application
from app.models.government_screenshot import GovernmentVerificationScreenshot
from app.models.user import User
from app.core.security import get_current_user
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
MAX_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

VERIFICATION_TYPE_MAP = {
    "aadhaar": "uidai_aadhaar",
    "uidai_aadhaar": "uidai_aadhaar",
    "pan": "pan_aadhaar_link",
    "pan_aadhaar_link": "pan_aadhaar_link",
}

PORTAL_MAP = {
    "uidai_aadhaar": "UIDAI",
    "pan_aadhaar_link": "Income Tax e-Filing",
}


class GovScreenshotOut(BaseModel):
    id: int
    application_id: int
    verification_type: str
    original_name: str
    file_url: str
    created_at: datetime

    class Config:
        from_attributes = True


def _to_screenshot_out(gs: GovernmentVerificationScreenshot) -> GovScreenshotOut:
    filename = os.path.basename(gs.screenshot_path) if gs.screenshot_path else ""
    return GovScreenshotOut(
        id=gs.id,
        application_id=gs.application_id,
        verification_type=gs.verification_type,
        original_name=gs.notes or filename,
        file_url=f"/uploads/{filename}",
        created_at=gs.created_at or datetime.utcnow(),
    )


@router.post("/{application_id}/upload", response_model=GovScreenshotOut, status_code=201)
async def upload_gov_screenshot(
    application_id: int,
    verification_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a government verification screenshot for an application.
    Stored in government_verification_screenshots. No OCR or Document row creation.
    """
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if current_user.role != "admin" and app.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    norm_type_key = (verification_type or "").strip().lower()
    if norm_type_key not in VERIFICATION_TYPE_MAP:
        raise HTTPException(
            status_code=400,
            detail="Invalid verification_type. Must be 'aadhaar', 'pan', 'uidai_aadhaar', or 'pan_aadhaar_link'",
        )
    canonical_type = VERIFICATION_TYPE_MAP[norm_type_key]

    orig_name = file.filename or "screenshot.png"
    ext = os.path.splitext(orig_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File extension '{ext}' not allowed. Allowed extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(contents) > MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds maximum size of {settings.MAX_UPLOAD_SIZE_MB}MB",
        )

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    stored_filename = f"gov_{canonical_type}_{uuid.uuid4().hex[:12]}{ext}"
    disk_path = os.path.join(settings.UPLOAD_DIR, stored_filename)

    with open(disk_path, "wb") as f_out:
        f_out.write(contents)

    screenshot = GovernmentVerificationScreenshot(
        application_id=application_id,
        verification_type=canonical_type,
        government_portal=PORTAL_MAP.get(canonical_type, "Government Portal"),
        verification_reference=None,
        verification_status="VERIFIED",
        screenshot_path=disk_path,
        captured_at=datetime.utcnow(),
        source_url=None,
        notes=orig_name,
        created_at=datetime.utcnow(),
    )
    db.add(screenshot)
    db.commit()
    db.refresh(screenshot)

    logger.info(
        "Saved gov screenshot %d (type=%s) for application %d",
        screenshot.id,
        canonical_type,
        application_id,
    )
    return _to_screenshot_out(screenshot)


@router.get("/{application_id}", response_model=List[GovScreenshotOut])
def list_gov_screenshots(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieve all active verification screenshots for an application in deterministic order."""
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if current_user.role != "admin" and app.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    shots = (
        db.query(GovernmentVerificationScreenshot)
        .filter(GovernmentVerificationScreenshot.application_id == application_id)
        .order_by(GovernmentVerificationScreenshot.created_at.asc(), GovernmentVerificationScreenshot.id.asc())
        .all()
    )
    return [_to_screenshot_out(s) for s in shots]


@router.delete("/{screenshot_id}", status_code=200)
def delete_gov_screenshot(
    screenshot_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a government verification screenshot record and its file.
    Does NOT touch borrower documents or OCR data.
    """
    shot = (
        db.query(GovernmentVerificationScreenshot)
        .filter(GovernmentVerificationScreenshot.id == screenshot_id)
        .first()
    )
    if not shot:
        raise HTTPException(status_code=404, detail="Government screenshot not found")

    app = db.query(Application).filter(Application.id == shot.application_id).first()
    if app and current_user.role != "admin" and app.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if shot.screenshot_path and os.path.exists(shot.screenshot_path):
        try:
            os.remove(shot.screenshot_path)
        except OSError as e:
            logger.warning("Could not delete screenshot file %s: %s", shot.screenshot_path, e)

    db.delete(shot)
    db.commit()

    logger.info("Deleted gov screenshot %d", screenshot_id)
    return {"status": "success", "message": "Screenshot deleted successfully", "id": screenshot_id}
