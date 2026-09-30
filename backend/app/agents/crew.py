"""
SmartVerify Loan Verification Crew
─────────────────────────────────────
Orchestrates the multi-agent pipeline:

    Document Analyst → Extraction Specialist → Verification Officer
                                              ↘ Fraud Investigator ↗
                                                       ↓
                                              Compliance Reporter

Produces a single structured result dict that mirrors the shape of the
previous (non-agentic) verification pipeline, so the FastAPI endpoint and
VerificationReport model don't need to change shape.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List

from crewai import Crew, Process

from app.agents.agent_definitions import build_agents
from app.agents.tasks import build_tasks
from app.core.config import settings
from app.services.llm_provider import is_retryable_provider_error

logger = logging.getLogger(__name__)


class LoanVerificationCrew:
    """Builds and runs the multi-agent loan verification crew for one application."""

    def __init__(self, db_session, gemini_max_retries: int = 2, backoff_base: float = 1.0):
        self.db = db_session
        self.gemini_max_retries = gemini_max_retries
        self.backoff_base = backoff_base

    def run(
        self,
        application_id: int,
        applicant_name: str,
        loan_amount: float,
        documents: List[Dict[str, Any]],
        loan_type: str = "Home Loan",
        loan_tenure: int = 60,
        gov_verification_record: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Execute the crew with automatic 3-tier provider cascade:
        1. Primary: Groq (openai/gpt-oss-120b)
        2. Fallback 1: Gemini (gemini-3.8-flash)
        3. Fallback 2: Gemini (gemini-3.5-flash-lite)

        Applies bounded exponential backoff for transient 429/5xx errors.
        Immediately re-raises non-retryable errors (auth failures, malformed requests, application errors).
        If all providers fail, returns a clean structured verification failure.
        """
        cascade_tiers = [
            {
                "provider": "groq",
                "model": "openai/gpt-oss-120b",
                "display_name": "Groq",
                "model_display": "openai/gpt-oss-120b",
                "is_fallback": False,
                "max_retries": 1,
            },
            {
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "display_name": "Gemini",
                "model_display": "gemini-3.8-flash",
                "is_fallback": True,
                "max_retries": self.gemini_max_retries,
            },
            {
                "provider": "gemini",
                "model": "gemini-3.5-flash-lite",
                "display_name": "Gemini",
                "model_display": "gemini-3.5-flash-lite",
                "is_fallback": True,
                "max_retries": self.gemini_max_retries,
            },
        ]

        tier_failures = []

        for tier in cascade_tiers:
            tier_name = tier["display_name"]
            model_name = tier["model"]
            model_display = tier["model_display"]
            max_attempts = tier["max_retries"]

            if not tier["is_fallback"]:
                logger.info(f"[LLM] Primary provider: {tier_name}")
            else:
                logger.warning(f"[LLM] Falling back to {tier_name} ({model_display})")
                logger.info(f"[LLM] Fallback provider: {tier_name}")
                logger.info(f"[LLM] {tier_name} verification started (model: {model_display})")

            for attempt in range(1, max_attempts + 1):
                try:
                    return self._execute_pipeline(
                        provider_name=tier["provider"],
                        model_name=tier["model"],
                        application_id=application_id,
                        applicant_name=applicant_name,
                        loan_amount=loan_amount,
                        documents=documents,
                        loan_type=loan_type,
                        loan_tenure=loan_tenure,
                        gov_verification_record=gov_verification_record,
                    )
                except Exception as exc:
                    is_retryable, sanitized_reason = is_retryable_provider_error(exc)
                    if not is_retryable:
                        logger.error(
                            f"[LLM] {tier_name} ({model_display}) failed with non-retryable error: {sanitized_reason}"
                        )
                        raise

                    logger.warning(f"[LLM] {tier_name} ({model_display}) request failed: {sanitized_reason}")

                    if attempt < max_attempts:
                        delay = self.backoff_base * (2 ** (attempt - 1))
                        logger.info(
                            f"[LLM] Retrying {tier_name} ({model_display}) in {delay:.2f}s "
                            f"(bounded exponential backoff, attempt {attempt + 1}/{max_attempts})..."
                        )
                        import time
                        time.sleep(delay)
                        continue
                    else:
                        tier_failures.append(f"{tier_name} ({model_display}): {sanitized_reason}")
                        break

        failure_details = " -> ".join(tier_failures)
        logger.error(f"[LLM] All configured providers in fallback cascade failed: {failure_details}")
        raise RuntimeError(
            f"Multi-Agent verification failed: All configured providers in fallback cascade failed. "
            f"Details: {failure_details}"
        )

    def _execute_pipeline(
        self,
        provider_name: str,
        model_name: str,
        application_id: int,
        applicant_name: str,
        loan_amount: float,
        documents: List[Dict[str, Any]],
        loan_type: str = "Home Loan",
        loan_tenure: int = 60,
        gov_verification_record: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Execute the 5-agent CrewAI pipeline with the specified LLM provider and model.
        """
        import time
        from app.agents.context import SharedAgentContext

        # Phase 4: Shared Context
        shared_context = SharedAgentContext(
            application_id=application_id,
            applicant_name=applicant_name,
            loan_amount=loan_amount
        )

        start_time = time.time()

        agents = build_agents(
            self.db,
            shared_context=shared_context,
            provider=provider_name,
            model=model_name,
        )
        tasks = build_tasks(
            agents,
            run_context={
                "application_id": application_id,
                "applicant_name": applicant_name,
                "loan_amount": loan_amount,
                "loan_type": loan_type,
                "loan_tenure": loan_tenure,
                "documents": documents,
                "gov_verification_record": gov_verification_record or {},
                "shared_context_summary": shared_context.get_summary_context()
            },
        )

        crew = Crew(
            agents=list(agents.values()),
            tasks=tasks,
            process=Process.sequential,
            verbose=True,
        )

        # Crew AI execution (Phase 8 Timeline tracking)
        shared_context.record_timing("crewai_start", time.time())
        crew_output = crew.kickoff()
        shared_context.record_timing("crewai", time.time() - start_time)

        raw_output = self._extract_raw(crew_output)
        if hasattr(crew_output, "json_dict") and isinstance(crew_output.json_dict, dict) and crew_output.json_dict:
            result = dict(crew_output.json_dict)
        elif hasattr(crew_output, "pydantic") and crew_output.pydantic:
            result = crew_output.pydantic.model_dump()
        else:
            result = self._parse_final_json(raw_output)
        result["agent_trace"] = raw_output

        # Defensive defaults in case the LLM omitted a field
        result.setdefault("final_status", "manual_review")
        result.setdefault("verification_score", 0.0)
        result.setdefault("risk_score", 0.0)
        result.setdefault("fraud_flag", False)
        result.setdefault("extracted_info", {})
        result.setdefault("verification_details", {})
        result.setdefault("fraud_analysis", {})
        result.setdefault("pdf_path", None)
        result.setdefault("summary", "")
        result.setdefault("agent_findings", {})
        result.setdefault("recommendation", "")
        result.setdefault("human_review", "")

        # New Phase 6 & 7 & 8 fields
        result.setdefault("explainable_ai", {})
        result.setdefault("confidence_score", 0.0)
        result["agent_memory"] = shared_context.agent_memory

        shared_context.record_timing("total", time.time() - start_time)
        result["execution_timeline"] = shared_context.execution_timeline
        result["llm_provider"] = provider_name
        result["llm_model"] = model_name

        # Safety net: fraud flag always forces rejection, even if the
        # compliance reporter agent got this wrong.
        if result.get("fraud_flag") and result["final_status"] != "rejected":
            logger.warning(
                "Crew set final_status=%s despite fraud_flag=True; forcing 'rejected'.",
                result["final_status"],
            )
            result["final_status"] = "rejected"

        return result

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_raw(crew_output: Any) -> str:
        """Normalise CrewOutput (or plain string) into a raw string."""
        if hasattr(crew_output, "raw"):
            return crew_output.raw
        return str(crew_output)

    @staticmethod
    def _parse_final_json(raw_output: str) -> Dict[str, Any]:
        """
        Parse the compliance reporter's final JSON object out of the raw
        crew output, tolerating markdown code fences or surrounding prose.
        """
        if not raw_output:
            return {}

        text = raw_output.strip()

        # Strip ```json ... ``` or ``` ... ``` fences if present
        fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1)

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Fallback: find the last top-level-looking {...} block
        brace_match = re.search(r"(\{.*\})", text, re.DOTALL)
        if brace_match:
            candidate = brace_match.group(1)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse crew JSON output: {e}\nRaw: {text[:1000]}")

        logger.error(f"Could not extract JSON from crew output: {text[:1000]}")
        return {}
