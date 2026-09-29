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
