"""
OCR Service
Robust multi-tier OCR engine with automatic orientation correction:
1. Google Cloud Vision REST API (if GOOGLE_VISION_API_KEY is configured)
2. PaddleOCR (if installed)
3. EasyOCR (primary local deep-learning engine)
4. Tesseract OCR (fallback)
"""
import base64
import logging
import os
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import httpx
import cv2
import numpy as np
from PIL import Image, ImageOps

from app.core.config import settings

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# EasyOCR (primary local engine)
# -------------------------------------------------------------------------
_easyocr_reader = None

def _get_easyocr():
    """Lazy-load EasyOCR reader."""
    global _easyocr_reader
    if _easyocr_reader is None:
        try:
            import easyocr
            lang = (settings.OCR_LANGUAGE or "en").lower()
            langs = [lang] if lang in ["en", "hi"] else ["en"]
            _easyocr_reader = easyocr.Reader(langs, gpu=False, verbose=False)
            logger.info("EasyOCR reader initialised successfully")
        except Exception as e:
            logger.warning(f"EasyOCR initialization failed: {e}")
    return _easyocr_reader


def _extract_with_easyocr(image_input) -> str:
    """Run EasyOCR with line preservation."""
    reader = _get_easyocr()
    if not reader:
        return ""
    try:
        results = reader.readtext(image_input, detail=0, paragraph=False)
        return "\n".join(results).strip()
    except Exception as e:
        logger.warning(f"EasyOCR extraction failed: {e}")
        return ""


def _extract_detailed_easyocr(image_input):
    """
    Run EasyOCR with full details: bounding boxes, recognized text, and token confidences.
    Returns (full_text, detections_list, avg_confidence).
    """
    reader = _get_easyocr()
    if not reader:
        return "", [], 0.0
    try:
        raw_results = reader.readtext(image_input, detail=1, paragraph=False)
        detections = []
        texts = []
        confs = []
        for item in raw_results:
            if not item or len(item) < 3:
                continue
            bbox, text, prob = item[0], item[1], item[2]
            clean_t = str(text).strip()
            if clean_t:
                texts.append(clean_t)
                confs.append(float(prob))
                bbox_list = [[float(pt[0]), float(pt[1])] for pt in bbox] if bbox is not None else []
                detections.append({
                    "bbox": bbox_list,
                    "text": clean_t,
                    "confidence": float(prob)
                })
        full_text = "\n".join(texts).strip()
        avg_conf = float(sum(confs) / len(confs)) if confs else 0.0
        return full_text, detections, avg_conf
    except Exception as e:
        logger.warning(f"Detailed EasyOCR extraction failed: {e}")
        return "", [], 0.0


# -------------------------------------------------------------------------
# PaddleOCR (optional engine)
# -------------------------------------------------------------------------
_paddle_ocr = None
_PADDLE_LANG_MAP = {
    "en": "en", "hi": "hi", "ch": "ch", "zh": "ch",
    "fr": "french", "de": "german", "ko": "korean", "ja": "japan",
}

def _get_paddle_ocr():
    global _paddle_ocr
    if _paddle_ocr is None:
        try:
            from paddleocr import PaddleOCR
            lang = _PADDLE_LANG_MAP.get((settings.OCR_LANGUAGE or "en").lower(), "en")
            _paddle_ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
            logger.info("PaddleOCR engine initialised")
        except Exception as e:
            logger.debug(f"PaddleOCR unavailable: {e}")
    return _paddle_ocr


def _extract_with_paddle(image_path: str) -> str:
    ocr = _get_paddle_ocr()
    if not ocr:
        return ""
    try:
        result = ocr.ocr(image_path, cls=True)
        lines = []
        for page in result or []:
            for detection in page or []:
                try:
                    text = detection[1][0]
                    if text:
                        lines.append(text)
                except (IndexError, TypeError):
                    continue
        return "\n".join(lines).strip()
    except Exception as e:
        logger.warning(f"PaddleOCR failed: {e}")
        return ""


# -------------------------------------------------------------------------
# Google Cloud Vision OCR (cloud engine)
# -------------------------------------------------------------------------
_GOOGLE_VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"

def _extract_with_google_vision(image_path: str) -> str:
    api_key = os.getenv("GOOGLE_VISION_API_KEY") or getattr(settings, "google_vision_api_key", "")
    if not api_key:
        return ""

    try:
        with open(image_path, "rb") as f:
            image_content = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "requests": [
                {
                    "image": {"content": image_content},
                    "features": [{"type": "TEXT_DETECTION"}],
                }
            ]
        }

        resp = httpx.post(
            _GOOGLE_VISION_ENDPOINT,
            params={"key": api_key},
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

        response_obj = (data.get("responses") or [{}])[0]
        if "error" in response_obj:
            logger.error(f"Google Vision API error: {response_obj['error']}")
            return ""

        text = response_obj.get("fullTextAnnotation", {}).get("text", "")
        if not text:
            text_annotations = response_obj.get("textAnnotations", [])
            text = text_annotations[0]["description"] if text_annotations else ""

        return text.strip()
    except Exception as e:
        logger.error(f"Google Vision OCR failed: {e}")
        return ""


# -------------------------------------------------------------------------
# Tesseract OCR (local fallback)
# -------------------------------------------------------------------------
def _extract_with_tesseract(image_path: str) -> str:
    try:
        import pytesseract
        img = Image.open(image_path)
        return pytesseract.image_to_string(img, lang="eng").strip()
    except Exception as e:
        logger.debug(f"Tesseract unavailable: {e}")
        return ""


# -------------------------------------------------------------------------
# Orientation Detection & Quality Scoring
# -------------------------------------------------------------------------
ID_KEYWORDS = {
    "government", "india", "aadhaar", "income", "tax", "department",
    "dob", "date", "birth", "male", "female", "name", "father", "address",
    "permanent", "account", "republic", "election", "commission", "signature"
}

def _score_text(text: str) -> int:
    """Score text based on word count and common document keywords."""
    if not text:
        return 0
    words = [w.lower() for w in text.split() if len(w) >= 3]
    kw_matches = sum(5 for w in words if any(kw in w for kw in ID_KEYWORDS))
    return len(words) + kw_matches


def _run_with_orientation_correction(image_path: str) -> str:
    """Try OCR with automatic orientation detection if image is sideways."""
    # 1. First attempt with direct path
    text = _extract_with_easyocr(image_path)
    score = _score_text(text)
    if score >= 15:
        logger.info(f"Direct EasyOCR succeeded with score={score} ({len(text)} chars)")
        return text

    # 2. Check if image needs rotation (common for mobile camera captures)
    logger.info(f"Direct OCR low score ({score}), checking image rotations...")
    try:
        img = cv2.imread(image_path)
        if img is None:
            return text

        best_text = text
        best_score = score

        rotations = [
            (90, cv2.ROTATE_90_CLOCKWISE),
            (180, cv2.ROTATE_180),
            (270, cv2.ROTATE_90_COUNTERCLOCKWISE),
        ]
        for angle, rot_flag in rotations:
            rot_img = cv2.rotate(img, rot_flag)
            candidate_text = _extract_with_easyocr(rot_img)
            c_score = _score_text(candidate_text)
            logger.debug(f"Rotation {angle} deg: score={c_score}")
            if c_score > best_score:
                best_score = c_score
                best_text = candidate_text

        if best_score > score:
            logger.info(f"Rotation improved score from {score} to {best_score}")
        return best_text
    except Exception as e:
        logger.warning(f"Orientation correction failed: {e}")
        return text


# -------------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------------
def extract_text_from_image(image_path: str) -> str:
    """
    Extract text using available OCR engines in priority order:
    1. Google Cloud Vision (if configured)
    2. PaddleOCR (if installed)
    3. EasyOCR (with auto-orientation)
    4. Tesseract
    """
    text = ""

    # 1. Google Cloud Vision
    text = _extract_with_google_vision(image_path)
    if text:
        logger.info(f"Google Vision extracted {len(text)} chars from {image_path}")
        return text

    # 2. PaddleOCR
    text = _extract_with_paddle(image_path)
    if text:
        logger.info(f"PaddleOCR extracted {len(text)} chars from {image_path}")
        return text

    # 3. EasyOCR with auto-orientation
    text = _run_with_orientation_correction(image_path)
    if text:
        logger.info(f"EasyOCR extracted {len(text)} chars from {image_path}")
        return text

    # 4. Tesseract fallback
    text = _extract_with_tesseract(image_path)
    if text:
        logger.info(f"Tesseract extracted {len(text)} chars from {image_path}")
        return text

    logger.warning(f"No OCR engine could extract text from {image_path}")
    return ""


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from PDF pages using pypdf, pdfplumber, or pdf2image."""
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        pages_text = [p.extract_text() or "" for p in reader.pages]
        text = "\n".join(pages_text).strip()
        if text:
            return text
    except Exception as e:
        logger.warning(f"pypdf extraction failed for {pdf_path}: {e}")

    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages).strip()
            if text:
                return text
    except Exception as e2:
        logger.warning(f"pdfplumber failed: {e2}")

    try:
        import pdf2image
        pages = pdf2image.convert_from_path(pdf_path, dpi=200)
        texts = []
        for i, page in enumerate(pages):
            tmp_path = pdf_path + f"_page_{i}.png"
            page.save(tmp_path, "PNG")
            texts.append(extract_text_from_image(tmp_path))
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return "\n".join(texts)
    except Exception as e3:
        logger.warning(f"pdf2image failed: {e3}")
        return ""



def extract_text(file_path: str) -> str:
    """Route file to appropriate extractor."""
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    else:
        return extract_text_from_image(file_path)


def run_multipass_ocr(image_path: str, doc_type: str = "generic") -> Dict[str, Any]:
    """
    Execute multi-pass OCR workflow:
    1. Image quality assessment (blur, resolution, lighting, noise, tilt)
    2. Adaptive preprocessing variants generation
    3. Multi-pass OCR execution with spatial and confidence details
    4. Return full passes report for consensus parsing
    """
    from app.services.preprocessing import assess_image_quality, generate_preprocessing_variants

    # If PDF, extract first page to image or fallback to single text
    ext = Path(image_path).suffix.lower()
    if ext == ".pdf":
        text = extract_text_from_pdf(image_path)
        return {
            "quality": {"quality_score": 90, "issues": [], "warning": None},
            "passes": [{"variant": "pdf_direct", "text": text, "detections": [], "confidence": 0.95, "score": _score_text(text)}],
            "primary_text": text,
            "all_texts": [text] if text else []
        }

    quality_report = assess_image_quality(image_path)
    issues = quality_report.get("issues", [])
    variants = generate_preprocessing_variants(image_path, quality_report)

    if not variants:
        raw_text = extract_text_from_image(image_path)
        return {
            "quality": quality_report,
            "passes": [{"variant": "fallback", "text": raw_text, "detections": [], "confidence": 0.7, "score": _score_text(raw_text)}],
            "primary_text": raw_text,
            "all_texts": [raw_text] if raw_text else []
        }

    # Select variants according to detected issues
    pass_keys = []
    if "enhanced" in variants:
        pass_keys.append("enhanced")

    if "excessive_blur" in issues or "slight_blur" in issues:
        if "sharpened" in variants and "sharpened" not in pass_keys:
            pass_keys.append("sharpened")

    if "very_low_resolution" in issues or "low_resolution" in issues:
        if "upscaled" in variants and "upscaled" not in pass_keys:
            pass_keys.append("upscaled")

    if any(k in issues for k in ["underexposed_dark", "overexposed_bright", "uneven_lighting", "poor_contrast"]):
        if "thresholded" in variants and "thresholded" not in pass_keys:
            pass_keys.append("thresholded")

    if "document_tilt" in issues:
        if "deskewed" in variants and "deskewed" not in pass_keys:
            pass_keys.append("deskewed")

    # Ensure at least 2 distinct passes for consensus
    fallback_variants = ["original", "sharpened", "thresholded", "upscaled"]
    for fb in fallback_variants:
        if len(pass_keys) >= 3:
            break
        if fb in variants and fb not in pass_keys:
            pass_keys.append(fb)

    # Maximum 3 passes for optimal accuracy vs interactive speed
    pass_keys = pass_keys[:3]
    logger.info(f"Running multi-pass OCR on {Path(image_path).name} with variants: {pass_keys}")

    passes = []
    for key in pass_keys:
        img_var = variants[key]
        p_text, p_dets, p_conf = _extract_detailed_easyocr(img_var)

        # Orientation check on first pass if score is poor
        if len(passes) == 0 and _score_text(p_text) < 15:
            best_rot_text = p_text
            best_rot_dets = p_dets
            best_rot_conf = p_conf
            best_score = _score_text(p_text)
            rotations = [
                (90, cv2.ROTATE_90_CLOCKWISE),
                (180, cv2.ROTATE_180),
                (270, cv2.ROTATE_90_COUNTERCLOCKWISE)
            ]
            for angle, rot_flag in rotations:
                try:
                    rot_img = cv2.rotate(img_var, rot_flag)
                    r_text, r_dets, r_conf = _extract_detailed_easyocr(rot_img)
                    r_score = _score_text(r_text)
                    if r_score > best_score:
                        best_score = r_score
                        best_rot_text = r_text
                        best_rot_dets = r_dets
                        best_rot_conf = r_conf
                except Exception:
                    pass
            p_text = best_rot_text
            p_dets = best_rot_dets
            p_conf = best_rot_conf

        passes.append({
            "variant": key,
            "text": p_text,
            "detections": p_dets,
            "confidence": p_conf,
            "score": _score_text(p_text)
        })

    # Sort passes by information score & confidence
    passes.sort(key=lambda x: (x["score"], x["confidence"]), reverse=True)
    primary_text = passes[0]["text"] if passes else ""

    return {
        "quality": quality_report,
        "passes": passes,
        "primary_text": primary_text,
        "all_texts": [p["text"] for p in passes if p["text"]]
    }