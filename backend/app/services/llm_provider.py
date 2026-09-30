"""
LLM Provider Abstraction for SmartVerify Report Generation System (Phase 6).
Provides a clean interface separating business logic from LLM APIs.
Supports Google Gemini via REST API and a Mock provider for 100% deterministic,
offline automated testing.
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Set
import json
import logging
import os

from app.core.config import settings

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Raised when an LLM provider fails (timeout, network error, invalid response)."""
    pass


class LLMProvider(ABC):
    """Abstract interface for LLM text and structured JSON generation."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Returns the model identifier string."""
        pass

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_mode: bool = True,
    ) -> str:
        """
        Executes generation and returns the raw response string (typically JSON).
        Raises ProviderError on failure.
        """
        pass


class GeminiRESTProvider(LLMProvider):
    """
    Direct Google Gemini REST API client.
    Reuses existing environment configuration without third-party LLM orchestrator overhead.
    Does not log or expose API keys.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ):
        raw_model = model or getattr(settings, "CREWAI_MODEL", "gemini-2.5-flash")
        # Strip provider prefix if present (e.g. 'gemini/gemini-2.5-flash' -> 'gemini-2.5-flash')
        if "/" in raw_model:
            raw_model = raw_model.split("/")[-1]
        self._model = raw_model

        self._api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or getattr(settings, "GEMINI_API_KEY", "")
            or getattr(settings, "GOOGLE_API_KEY", "")
            or getattr(settings, "gemini_api_key", "")
            or ""
        )
        self.timeout_seconds = timeout_seconds

    @property
    def model_name(self) -> str:
        return self._model

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_mode: bool = True,
    ) -> str:
        if not self._api_key:
            raise ProviderError("Gemini API key is not configured in settings or environment.")

        import requests

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        headers = {"Content-Type": "application/json"}
        params = {"key": self._api_key}

        contents = []
        if prompt:
            contents.append({"role": "user", "parts": [{"text": prompt}]})

        body: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.1,
            },
        }

        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"

        if system_prompt:
            body["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        try:
            resp = requests.post(
                url,
                headers=headers,
                params=params,
                json=body,
                timeout=self.timeout_seconds,
            )
            if resp.status_code != 200:
                raise ProviderError(f"Gemini API returned status {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ProviderError("Gemini API returned zero candidates.")

            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise ProviderError("Gemini candidate content has no text parts.")

            return parts[0].get("text", "")

        except requests.RequestException as e:
            raise ProviderError(f"Network error during Gemini API call: {e}") from e
        except Exception as e:
            raise ProviderError(f"Unexpected error during Gemini API call: {e}") from e


class MockLLMProvider(LLMProvider):
    """
    Deterministic Mock LLM Provider for unit and regression testing.
    Zero external network calls, zero API costs, and 100% predictable responses.
    Allows simulating edge cases: provider failures, malformed JSON, timeouts.
    """

    def __init__(
        self,
        model_name: str = "mock-gemini-2.5-flash",
        custom_responses: Optional[Dict[str, str]] = None,
        fail_particular_ids: Optional[Set[str]] = None,
        malformed_particular_ids: Optional[Set[str]] = None,
    ):
        self._model_name = model_name
        self.custom_responses: Dict[str, str] = custom_responses or {}
        self.fail_particular_ids: Set[str] = fail_particular_ids or set()
        self.malformed_particular_ids: Set[str] = malformed_particular_ids or set()
        self.call_history: list = []

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_mode: bool = True,
    ) -> str:
        self.call_history.append({"prompt": prompt, "system_prompt": system_prompt})

        # Check if a specific particular ID is in prompt and configured to fail
        for pid in self.fail_particular_ids:
            if f'"particular_id": "{pid}"' in prompt or f"Particular ID: {pid}" in prompt or f"'{pid}'" in prompt:
                raise ProviderError(f"Simulated provider failure for particular {pid}")

        for pid in self.malformed_particular_ids:
            if f'"particular_id": "{pid}"' in prompt or f"Particular ID: {pid}" in prompt or f"'{pid}'" in prompt:
                return "{ malformed json: not valid json at all ... "

        # Check custom responses
        for key, resp in self.custom_responses.items():
            if key in prompt:
                return resp

        # Default structured fallback matching ComposedParticular contract
        default_payload = {
            "text": "Verification observations confirmed from submitted documents and verified findings.",
            "fact_ids": [],
            "evidence_ids": [],
            "confidence": 0.95,
            "missing_information": [],
        }
        return json.dumps(default_payload)


def mask_secrets(text: Any) -> str:
    """
    Sanitize text by redacting API keys and sensitive tokens.
    Prevents leaking credentials in logs, console output, or error messages.
    """
    if text is None:
        return ""
    s = str(text)

    # 1. Redact exact known secrets from settings & env if configured
    known_keys = set()
    for attr in ("GROQ_API_KEY", "groq_api_key", "GEMINI_API_KEY", "gemini_api_key", "GOOGLE_API_KEY", "SECRET_KEY"):
        val = getattr(settings, attr, None) or os.environ.get(attr)
        if val and isinstance(val, str) and len(val.strip()) >= 8:
            known_keys.add(val.strip())

    for key in known_keys:
        if key in s:
            s = s.replace(key, "[REDACTED_API_KEY]")

    # 2. Redact patterns: Groq (gsk_...), Google (AIza... or AQ....), Bearer tokens
    import re
    s = re.sub(r"gsk_[A-Za-z0-9_-]{10,}", "[REDACTED_GROQ_KEY]", s)
    s = re.sub(r"AIza[0-9A-Za-z_-]{25,}", "[REDACTED_GEMINI_KEY]", s)
    s = re.sub(r"AQ\.[A-Za-z0-9_-]{20,}", "[REDACTED_GEMINI_KEY]", s)
    s = re.sub(r"(Bearer\s+)[A-Za-z0-9._-]{15,}", r"\1[REDACTED_TOKEN]", s)
    s = re.sub(r"([?&]key=)[^&\s]+", r"\1[REDACTED_KEY]", s)

    return s


def is_retryable_provider_error(exc: Exception) -> tuple[bool, str]:
    """
    Determine if an exception represents a transient provider failure:
    - HTTP 429 / rate limit / quota exceeded
    - HTTP 5xx temporary server errors (500, 502, 503, 504)
    - Timeout / network connection failure

    Returns:
        (is_retryable, sanitized_reason_str)

    Explicitly excludes:
    - Ordinary application errors (ValueError, KeyError, TypeError)
    - Invalid structured output / parsing errors (JSONDecodeError, ValidationError)
    """
    if exc is None:
        return False, "No exception"

    # 1. Check for non-retryable application/parsing errors first
    non_retryable_types = (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        json.JSONDecodeError,
    )
    try:
        from pydantic import ValidationError as PydanticValidationError
        non_retryable_types = non_retryable_types + (PydanticValidationError,)
    except ImportError:
        pass

    # Inspect status code if present directly or on response
    status_code = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status_code is None and hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        status_code = exc.response.status_code

    # Extract all text from exception, cause, and args
    err_str = f"{type(exc).__name__}: {str(exc)}"
    if getattr(exc, "__cause__", None):
        err_str += f" | Cause: {type(exc.__cause__).__name__}: {str(exc.__cause__)}"

    sanitized = mask_secrets(err_str)
    lower = sanitized.lower()

    # 2. Explicitly reject authentication/permission errors (HTTP 401, 403, or auth messages)
    if status_code in (401, 403):
        return False, f"authentication error (HTTP {status_code}): {sanitized}"
    if any(k in lower for k in (
        "api_key_invalid", "invalid_api_key", "invalid api key", "unauthorized",
        "authentication failed", "permission denied", "forbidden", "bad credentials",
        "unauthenticated", "api key not valid"
    )):
        return False, f"authentication error: {sanitized}"

    # 3. Explicitly reject malformed requests or invalid model errors (HTTP 400, 404, or model messages)
    if status_code == 400:
        return False, f"malformed request error (HTTP 400): {sanitized}"
    if status_code == 404:
        return False, f"invalid model error (HTTP 404): {sanitized}"
    if any(k in lower for k in (
        "model not found", "is no longer available", "does not exist",
        "unsupported model", "invalid_argument", "tool_use_failed"
    )):
        return False, f"client/model error: {sanitized}"

    # 4. If it is purely an application error without HTTP or provider failure markers
    if isinstance(exc, non_retryable_types) and not any(k in lower for k in ("429", "500", "502", "503", "504", "rate limit", "quota", "timeout")):
        return False, sanitized

    # 5. Check retryable HTTP status code
    if status_code == 429:
        return True, f"429 rate limit: {sanitized}"
    if status_code in (500, 502, 503, 504):
        return True, f"{status_code} provider error: {sanitized}"

    # 6. Check LiteLLM / HTTP exception class names
    exc_cls_name = type(exc).__name__
    if any(k in exc_cls_name for k in ("RateLimitError", "QuotaExceeded", "ResourceExhausted")):
        return True, f"429 rate limit: {sanitized}"
    if any(k in exc_cls_name for k in ("Timeout", "TimeOut", "ConnectTimeout", "ReadTimeout")):
        return True, f"timeout: {sanitized}"
    if any(k in exc_cls_name for k in ("APIConnectionError", "ConnectError", "NetworkError", "ConnectionRefusedError")):
        return True, f"connection failure: {sanitized}"
    if any(k in exc_cls_name for k in ("ServiceUnavailableError", "InternalServerError", "BadGatewayError")):
        return True, f"5xx provider error: {sanitized}"

    # 7. String inspection for retryable provider error keywords
    # Rate limit & Quota
    if any(k in lower for k in (
        "rate limit", "rate_limit", "quota exceeded", "quota", "429",
        "resource_exhausted", "too many requests", "tokens per minute", "tpm", "rpm"
    )):
        return True, f"429 rate limit: {sanitized}"

    # Temporary 5xx / Service unavailable / High demand / Overloaded
    if any(k in lower for k in (
        "500 internal server error", "502 bad gateway", "503 service unavailable",
        "503", "504 gateway timeout", "504", "overloaded", "high demand",
        "temporarily unavailable", "server is busy", "capacity", "unavailable"
    )):
        return True, f"5xx provider error: {sanitized}"

    # Timeout & Connection
    if any(k in lower for k in (
        "timeout", "timed out", "connection reset", "connection refused",
        "failed to connect", "network unreachable"
    )):
        return True, f"timeout / connection failure: {sanitized}"

    # Otherwise non-retryable
    return False, sanitized

