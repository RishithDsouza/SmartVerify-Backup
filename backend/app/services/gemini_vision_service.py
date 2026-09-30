"""
Gemini Vision Service – Aadhaar Address Fallback
=================================================
Provides a targeted Gemini Vision call to extract *only* the address block
from an Aadhaar card image when the local OCR pipeline produces a missing or
low-quality result.

Design constraints
------------------
- NEVER logs the complete Aadhaar number or raw image contents.
- Does NOT replace the main OCR pipeline – called only as an address fallback.
- Does NOT affect Name, DOB, Aadhaar Number, Gender, or PAN extraction.
- API key is read exclusively from the environment – never hard-coded.
- Returns None on any error so callers can gracefully degrade.
"""
import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Address quality helpers
# ---------------------------------------------------------------------------

_ADDRESS_GARBAGE_CHARS = re.compile(r"[^\w\s,./\-#()ÀàÁáÂâÃãÈèÉéÊêËëÌìÍíÎîÏïÒòÓóÔôÕõÙùÚúÛûÜüÝýÿ]")

_KNOWN_ARTIFACT_TOKENS = re.compile(
    r"\b(?:aadhaar|aadhar|government|india|uidai|unique|identification|authority|"
    r"republic|enrollment|enrolment|resident|electronic|letter|card|"
    r"helpdesk|download|www|gov\.in|toll|free|email|update|mera|ridjzi)\b",
    re.I,
)

# Positive address landmark patterns that should appear in a real Indian address
_ADDRESS_LANDMARKS = re.compile(
    r"\b(?:road|rd|street|st|lane|nagar|colony|block|sector|phase|layout|village|"
    r"post|po|vtc|taluk|tehsil|district|dist|city|town|state|flat|house|door|plot|building|"
    r"near|opp|opposite|floor|room|wing|cross|main|gali|ward|area|"
    r"S[/.]?O|D[/.]?O|W[/.]?O|C[/.]?O|care\s+of|father|mother|"
    r"bangalore|bengaluru|mysore|mysuru|hassan|mangalore|hubli|dharwad|arkalgud|"
    r"karnataka|andhra|pradesh|telangana|maharashtra|tamil|nadu|kerala|"
    r"hyderabad|chennai|mumbai|kolkata|delhi|pune|ahmedabad)\b",
    re.I,
)

# Case-sensitive check for internal casing corruption (e.g. eodeeaV)
_CAMEL_CORRUPTION = re.compile(r"\b[a-z]{2,}[A-Z]+\b")

# Transliteration noise clusters & known OCR corruptions
_GARBLED_NOISE_TOKENS = re.compile(
    r"\b(?:\w*(?:dzf|rudz|eode|sdrf|mopt|zxi|qxz|vcz|bkj)\w*|eodeeav|rudzf|stale|karataka)\b",
    re.I,
)

_PINCODE = re.compile(r"\b[1-9]\d{5}\b")


def _is_address_garbled(address: Optional[str]) -> bool:
    """
    Return True when the extracted address is considered garbled or insufficient.
    Triggers the Gemini Vision fallback.
    """
    if not address:
        return True

    addr = address.strip()
    if len(addr) < 15:
        return True

    # Immediately garbled if contains known OCR/UIDAI artifact tokens
    if _KNOWN_ARTIFACT_TOKENS.search(addr):
        return True

    # Case corruption from OCR scanning artifacts (e.g. eodeeaV)
    if _CAMEL_CORRUPTION.search(addr):
        return True

    # Transliteration noise tokens (e.g. rudzf, sdrft, stale, karataka)
    if _GARBLED_NOISE_TOKENS.search(addr):
        return True

    # Positive evidence: valid address has landmarks OR pincode
    has_landmark = bool(_ADDRESS_LANDMARKS.search(addr))
    has_pincode = bool(_PINCODE.search(addr))

    if has_landmark:
        return False  # Landmark present → real address

    if has_pincode:
        alpha_tokens = [
            t for t in re.findall(r"[A-Za-z]{3,}", addr)
            if t.lower() not in {"the", "and", "for", "from", "with", "into", "pin", "code"}
        ]
        # Pincode present: must have at least one recognized title-case city/landmark name
        proper = [w for w in alpha_tokens if w[0].isupper() and _ADDRESS_LANDMARKS.search(w)]
        if len(proper) >= 1:
            return False

    # No landmark and (no pincode or garbled-pincode-only) → garbled
    return True


def _normalise_address(raw: Optional[str]) -> Optional[str]:
    """
    Deterministic post-processing of Gemini-returned address:
    - Strip leading/trailing whitespace and newline pairs
    - Collapse multiple spaces / blank lines
    - Remove residual Aadhaar card header tokens
    - Capitalise first letter of each line
    Returns None if nothing meaningful remains.
    """
    if not raw:
        return None

    # Remove known artifact tokens
    cleaned = _KNOWN_ARTIFACT_TOKENS.sub("", raw)

    # Remove non-address punctuation artefacts
    cleaned = re.sub(r"[|\\~`@$%^&*=<>{}[\]]", "", cleaned)

    # Collapse multiple blank lines, normalise line endings
    lines = [ln.strip() for ln in cleaned.replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in lines if ln]

    if not lines:
        return None

    # Minimal capitalisation – preserve mixed-case city names
    formatted_lines = []
    for ln in lines:
        if ln and ln[0].islower():
            ln = ln[0].upper() + ln[1:]
        formatted_lines.append(ln)

    result = "\n".join(formatted_lines).strip()

    # Final sanity check – must contain at least one digit (house#/pin) or
    # a couple of real words to be considered a valid address
    has_digit = bool(re.search(r"\d", result))
    real_words = [w for w in result.split() if len(w) >= 3]
    if not has_digit and len(real_words) < 2:
        return None

    return result


def _is_valid_vision_address(
    address: Optional[str],
    applicant_name: Optional[str] = None,
    aadhaar_number: Optional[str] = None,
) -> bool:
    """
    Validate that Gemini Vision returned an actual address and not garbage/hallucination:
    - Must not be empty or too short (<15 chars)
    - Must not contain full 12-digit Aadhaar number
    - Must not be solely the applicant's name
    - Must not be solely a PIN code
    - Must not contain known artifact tokens
    - Must not contain known OCR garble/camel corruption
    """
    if not address or len(address.strip()) < 15:
        return False

    addr_clean = address.strip()

    # Reject if contains full 12-digit Aadhaar number
    if re.search(r"\b\d{4}\s*\d{4}\s*\d{4}\b", addr_clean):
        return False
    if aadhaar_number and len(aadhaar_number) >= 12:
        clean_num = aadhaar_number.replace(" ", "").replace("-", "")
        if clean_num in addr_clean.replace(" ", ""):
            return False

    # Reject if it's just the applicant name
    if applicant_name and len(applicant_name) > 3:
        if addr_clean.lower() == applicant_name.strip().lower():
            return False

    # Reject if solely a PIN code
    if re.match(r"^\s*(?:pin|pincode|pin\s*code)?\s*:?\s*\d{6}\s*$", addr_clean, re.I):
        return False

    # Reject if it contains known artifact tokens or garbled noise
    if _KNOWN_ARTIFACT_TOKENS.search(addr_clean):
        return False
    if _CAMEL_CORRUPTION.search(addr_clean):
        return False
    if _GARBLED_NOISE_TOKENS.search(addr_clean):
        return False

    return True


# ---------------------------------------------------------------------------
# Gemini Vision call
# ---------------------------------------------------------------------------

from app.core.config import settings

_GEMINI_VISION_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

_raw_vision_model = getattr(settings, "GEMINI_MODEL", "gemini-3.5-flash-lite")
if "/" in _raw_vision_model:
    _raw_vision_model = _raw_vision_model.split("/")[-1]
_VISION_MODEL = _raw_vision_model or "gemini-3.5-flash-lite"

_EXTRACTION_PROMPT = (
    "You are extracting the postal address printed on an Aadhaar card.\n\n"
    "Read ONLY the address block of the supplied Aadhaar image.\n\n"
    "Return ONLY the address text that is visibly present on the document.\n\n"
    "Do not invent, infer, autocomplete, or replace any address components.\n\n"
    "Preserve:\n"
    "- house/building number\n"
    "- house/building name\n"
    "- street\n"
    "- cross\n"
    "- road\n"
    "- locality\n"
    "- village/town/city\n"
    "- taluk/sub-district\n"
    "- district\n"
    "- state\n"
    "- PIN code\n\n"
    "If multiple languages are present, prefer the clearly readable English address where available.\n\n"
    "Combine multiline address lines into a clean multiline address or comma-separated address.\n\n"
    "Do not include:\n"
    "- Aadhaar number\n"
    "- name\n"
    "- father's name\n"
    "- mobile number\n"
    "- enrollment number\n"
    "- barcode/QR text\n"
    "- unrelated OCR text.\n\n"
    "If the address cannot be confidently read, return an empty result instead of guessing."
)


def extract_aadhaar_address_via_gemini(
    image_path: str,
    applicant_name: Optional[str] = None,
    aadhaar_number: Optional[str] = None,
) -> Optional[str]:
    """
    Use Gemini Vision to extract the address from an Aadhaar card image.
    Uses model cascade (primary model -> gemini-3.5-flash-lite -> gemini-flash-lite-latest)
    to handle transient 503/429 load spikes gracefully.
    """
    api_key = (
        getattr(settings, "GEMINI_API_KEY", "")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or ""
    )
    if not api_key:
        logger.debug("Gemini Vision address fallback skipped – GEMINI_API_KEY not set")
        return None

    ext = Path(image_path).suffix.lower()
    if ext == ".pdf":
        logger.debug("Gemini Vision address fallback skipped – PDF input not supported")
        return None

    try:
        with open(image_path, "rb") as fh:
            image_bytes = fh.read()
    except OSError as e:
        logger.warning(f"Gemini Vision: cannot read image file: {e}")
        return None

    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    mime_type = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"

    # Candidate models in order of priority:
    # 1. gemini-3.5-flash-lite (primary)
    # 2. gemini-flash-lite-latest (fallback 1)
    # 3. gemini-3.8-flash (fallback 2)
    models_to_try = []
    preferred_order = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-3.8-flash"]
    if _VISION_MODEL and _VISION_MODEL not in preferred_order:
        models_to_try.append(_VISION_MODEL)
    for m in preferred_order:
        if m not in models_to_try:
            models_to_try.append(m)

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": _EXTRACTION_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_image,
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 256,
        },
    }

    for model_name in models_to_try:
        url = _GEMINI_VISION_ENDPOINT.format(model=model_name)
        try:
            resp = httpx.post(
                url,
                params={"key": api_key},
                json=payload,
                timeout=25.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    logger.warning(f"Gemini Vision ({model_name}): no candidates returned")
                    continue

                raw_text: str = (
                    candidates[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )

                if not raw_text or raw_text.upper() == "NOT_FOUND":
                    logger.info(f"Gemini Vision ({model_name}): address not found on card")
                    return None

                normalised = _normalise_address(raw_text)
                if normalised and _is_valid_vision_address(normalised, applicant_name, aadhaar_number):
                    logger.info(
                        f"Gemini Vision ({model_name}): address successfully extracted "
                        f"({len(normalised)} chars)"
                    )
                    return normalised
                else:
                    logger.warning(
                        f"Gemini Vision ({model_name}): returned address failed validation: {raw_text[:60]!r}"
                    )
                    continue
            elif resp.status_code in (429, 503, 500, 404):
                logger.warning(
                    f"Gemini Vision ({model_name}) returned HTTP {resp.status_code} "
                    f"– trying next candidate model in cascade"
                )
                continue
            else:
                logger.warning(
                    f"Gemini Vision ({model_name}) returned HTTP {resp.status_code}: {resp.text[:120]}"
                )
                continue

        except httpx.TimeoutException:
            logger.warning(f"Gemini Vision ({model_name}) timed out – trying next model")
            continue
        except Exception as e:
            logger.warning(f"Gemini Vision ({model_name}) error: {e}")
            continue

    logger.warning("Gemini Vision: all candidate models exhausted or failed validation")
    return None


def should_use_vision_fallback(ocr_address: Optional[str], image_path: str) -> bool:
    """
    Decide whether to invoke Gemini Vision for address extraction.

    Only returns True when:
    1. The GEMINI_API_KEY environment variable or config setting is set.
    2. The file exists and is a supported image format (not PDF).
    3. The OCR-extracted address is missing or garbled.
    """
    api_key = (
        getattr(settings, "GEMINI_API_KEY", "")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or ""
    )
    if not api_key:
        return False

    ext = Path(image_path).suffix.lower() if image_path else ""
    if ext == ".pdf":
        return False

    if not image_path or not os.path.exists(image_path):
        return False

    return _is_address_garbled(ocr_address)

