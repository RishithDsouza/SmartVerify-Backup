"""
Tests for Automatic 3-Tier LLM Provider Cascade and Fallback in SmartVerify Multi-Agent Pipeline.
Covers:
1. Groq success -> Gemini not used
2. Groq 429 -> Gemini 3.8 success
3. Groq 429 -> Gemini 3.8 503 -> Gemini 3.5 Flash-Lite success
4. Groq 503 -> Gemini fallback
5. Gemini 429 retry with bounded backoff
6. Gemini 503 retry with bounded backoff
7. Invalid API key should NOT trigger endless retries (non-retryable auth error)
8. All providers unavailable -> clean structured failure
9. Secrets never appear in logs or errors
10. Provider/model selection remains isolated per run
11. Safe smoke test verifying provider and model resolution
"""

import logging
import pytest
from unittest.mock import MagicMock, patch

from app.agents.crew import LoanVerificationCrew
from app.agents.agent_definitions import get_llm, build_agents
from app.services.llm_provider import mask_secrets, is_retryable_provider_error


def _sample_crew_result(provider="groq", model="openai/gpt-oss-120b"):
    return {
        "final_status": "approved",
        "verification_score": 85.0,
        "risk_score": 15.0,
        "fraud_flag": False,
        "extracted_info": {"applicant_name": "Test User"},
        "verification_details": {},
        "fraud_analysis": {},
        "pdf_path": None,
        "summary": "Verified successfully.",
        "agent_findings": {},
        "recommendation": "Approve",
        "human_review": "Not required",
        "llm_provider": provider,
        "llm_model": model,
    }


# ── Test 1: Groq success ───────────────────────────────────────────────────

def test_groq_success():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, backoff_base=0.01)

    with patch.object(
        crew,
        "_execute_pipeline",
        return_value=_sample_crew_result("groq", "openai/gpt-oss-120b"),
    ) as mock_exec:
        result = crew.run(
            application_id=1,
            applicant_name="Test User",
            loan_amount=500000.0,
            documents=[],
        )

        assert mock_exec.call_count == 1
        call_kwargs = mock_exec.call_args.kwargs
        assert call_kwargs["provider_name"] == "groq"
        assert call_kwargs["model_name"] == "openai/gpt-oss-120b"
        assert result["llm_provider"] == "groq"
        assert result["llm_model"] == "openai/gpt-oss-120b"


# ── Test 2: Groq 429 → Gemini 3.8 success ──────────────────────────────────

def test_groq_429_gemini_38_success():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, backoff_base=0.01)

    groq_429 = Exception("Rate limit exceeded: 429 Too Many Requests. Organization token quota exhausted.")
    setattr(groq_429, "status_code", 429)

    gemini_38_res = _sample_crew_result("gemini", "gemini-3.8-flash")

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[groq_429, gemini_38_res],
    ) as mock_exec:
        result = crew.run(
            application_id=2,
            applicant_name="Test User 2",
            loan_amount=750000.0,
            documents=[],
        )

        assert mock_exec.call_count == 2
        assert mock_exec.call_args_list[0].kwargs["provider_name"] == "groq"
        assert mock_exec.call_args_list[0].kwargs["model_name"] == "openai/gpt-oss-120b"
        assert mock_exec.call_args_list[1].kwargs["provider_name"] == "gemini"
        assert mock_exec.call_args_list[1].kwargs["model_name"] == "gemini-3.8-flash"
        assert result["llm_provider"] == "gemini"
        assert result["llm_model"] == "gemini-3.8-flash"


# ── Test 3: Groq 429 → Gemini 3.8 503 → Gemini 3.5 Flash-Lite success ───────

def test_groq_429_gemini_38_503_gemini_35_success():
    mock_db = MagicMock()
    # gemini_max_retries=2: Gemini 3.8 attempts twice, then cascades to Gemini 3.5 Flash-Lite
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=2, backoff_base=0.001)

    groq_429 = Exception("429 Too Many Requests: TPM quota exhausted")
    setattr(groq_429, "status_code", 429)

    gemini_503_1 = Exception("503 Service Unavailable: Gemini 3.8 Flash model experiencing high demand")
    setattr(gemini_503_1, "status_code", 503)

    gemini_503_2 = Exception("503 Service Unavailable: Gemini 3.8 Flash overloaded")
    setattr(gemini_503_2, "status_code", 503)

    gemini_35_res = _sample_crew_result("gemini", "gemini-3.5-flash-lite")

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[groq_429, gemini_503_1, gemini_503_2, gemini_35_res],
    ) as mock_exec:
        result = crew.run(
            application_id=3,
            applicant_name="Test User 3",
            loan_amount=300000.0,
            documents=[],
        )

        assert mock_exec.call_count == 4
        # Call 1: Groq
        assert mock_exec.call_args_list[0].kwargs["provider_name"] == "groq"
        # Calls 2 & 3: Gemini 3.8 Flash attempts
        assert mock_exec.call_args_list[1].kwargs["model_name"] == "gemini-3.8-flash"
        assert mock_exec.call_args_list[2].kwargs["model_name"] == "gemini-3.8-flash"
        # Call 4: Gemini 3.5 Flash-Lite fallback
        assert mock_exec.call_args_list[3].kwargs["model_name"] == "gemini-3.5-flash-lite"
        assert result["llm_provider"] == "gemini"
        assert result["llm_model"] == "gemini-3.5-flash-lite"


# ── Test 4: Groq 503 → Gemini fallback ─────────────────────────────────────

def test_groq_503_gemini_fallback():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, backoff_base=0.01)

    groq_503 = Exception("503 Service Unavailable: Groq API gateway overloaded")
    setattr(groq_503, "status_code", 503)

    gemini_res = _sample_crew_result("gemini", "gemini-3.8-flash")

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[groq_503, gemini_res],
    ) as mock_exec:
        result = crew.run(
            application_id=4,
            applicant_name="Test User 4",
            loan_amount=200000.0,
            documents=[],
        )

        assert mock_exec.call_count == 2
        assert mock_exec.call_args_list[0].kwargs["provider_name"] == "groq"
        assert mock_exec.call_args_list[1].kwargs["provider_name"] == "gemini"
        assert result["llm_provider"] == "gemini"


# ── Test 5: Gemini 429 retry ───────────────────────────────────────────────

def test_gemini_429_retry():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=2, backoff_base=0.001)

    groq_429 = Exception("429 rate limit exceeded on Groq")
    setattr(groq_429, "status_code", 429)

    gemini_429_transient = Exception("429 ResourceExhausted: rate limit exceeded, please retry in 1s")
    setattr(gemini_429_transient, "status_code", 429)

    gemini_success = _sample_crew_result("gemini", "gemini-3.8-flash")

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[groq_429, gemini_429_transient, gemini_success],
    ) as mock_exec:
        result = crew.run(
            application_id=5,
            applicant_name="Test User 5",
            loan_amount=150000.0,
            documents=[],
        )

        # Call 1: Groq, Call 2: Gemini 3.8 (fails 429), Call 3: Gemini 3.8 retry (succeeds)
        assert mock_exec.call_count == 3
        assert mock_exec.call_args_list[1].kwargs["model_name"] == "gemini-3.8-flash"
        assert mock_exec.call_args_list[2].kwargs["model_name"] == "gemini-3.8-flash"
        assert result["llm_model"] == "gemini-3.8-flash"


# ── Test 6: Gemini 503 retry ───────────────────────────────────────────────

def test_gemini_503_retry():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=2, backoff_base=0.001)

    groq_timeout = TimeoutError("Groq connection timeout")

    gemini_503 = Exception("503 Model overloaded")
    setattr(gemini_503, "status_code", 503)

    gemini_success = _sample_crew_result("gemini", "gemini-3.8-flash")

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=[groq_timeout, gemini_503, gemini_success],
    ) as mock_exec:
        result = crew.run(
            application_id=6,
            applicant_name="Test User 6",
            loan_amount=100000.0,
            documents=[],
        )

        assert mock_exec.call_count == 3
        assert mock_exec.call_args_list[1].kwargs["model_name"] == "gemini-3.8-flash"
        assert mock_exec.call_args_list[2].kwargs["model_name"] == "gemini-3.8-flash"
        assert result["llm_model"] == "gemini-3.8-flash"


# ── Test 7: Invalid API key should NOT trigger endless retries ─────────────

def test_invalid_api_key_no_endless_retries():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=3, backoff_base=0.001)

    auth_error = Exception("HTTP 401 Unauthorized: API_KEY_INVALID. The provided API key is invalid.")
    setattr(auth_error, "status_code", 401)

    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=auth_error,
    ) as mock_exec:
        with pytest.raises(Exception) as exc_info:
            crew.run(
                application_id=7,
                applicant_name="Test User 7",
                loan_amount=50000.0,
                documents=[],
            )

        # Must fail immediately on attempt 1 without retry or cascade
        assert mock_exec.call_count == 1
        assert "API_KEY_INVALID" in str(exc_info.value)


# ── Test 8: All providers unavailable ──────────────────────────────────────

def test_all_providers_unavailable():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=2, backoff_base=0.001)

    err_503 = Exception("503 Service Unavailable")
    setattr(err_503, "status_code", 503)

    # 1 attempt Groq + 2 attempts Gemini 3.8 + 2 attempts Gemini 3.5 = 5 total
    with patch.object(
        crew,
        "_execute_pipeline",
        side_effect=err_503,
    ) as mock_exec:
        with pytest.raises(RuntimeError) as exc_info:
            crew.run(
                application_id=8,
                applicant_name="Test User 8",
                loan_amount=450000.0,
                documents=[],
            )

        err_msg = str(exc_info.value)
        assert "All configured providers in fallback cascade failed" in err_msg
        assert "Groq" in err_msg
        assert "gemini-3.8-flash" in err_msg
        assert "gemini-3.5-flash-lite" in err_msg
        assert mock_exec.call_count == 5


# ── Test 9: Secrets never appear in logs or errors ─────────────────────────

def test_secrets_never_appear_in_logs_or_errors(caplog):
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=1, backoff_base=0.001)

    mock_groq_key = "gsk_testdummysecretgroqkey1234567890abcdef"
    mock_gemini_key = "AQ.testdummysecretgeminikey1234567890abcdef"

    err = Exception(f"429 rate limit exceeded with {mock_groq_key} and fallback {mock_gemini_key}")
    setattr(err, "status_code", 429)

    with caplog.at_level(logging.INFO):
        with patch.object(
            crew,
            "_execute_pipeline",
            side_effect=[err, _sample_crew_result("gemini", "gemini-3.8-flash")],
        ):
            crew.run(
                application_id=9,
                applicant_name="Secret Applicant",
                loan_amount=50000.0,
                documents=[],
            )

    log_output = caplog.text
    assert mock_groq_key not in log_output
    assert mock_gemini_key not in log_output
    assert "[REDACTED_GROQ_KEY]" in log_output or "[REDACTED_API_KEY]" in log_output
    assert "[REDACTED_GEMINI_KEY]" in log_output or "[REDACTED_API_KEY]" in log_output


# ── Test 10: Provider/model selection remains isolated per run ─────────────

def test_provider_model_selection_isolated_per_run():
    mock_db = MagicMock()
    crew = LoanVerificationCrew(db_session=mock_db, gemini_max_retries=1, backoff_base=0.001)

    groq_429 = Exception("429 rate limit")
    setattr(groq_429, "status_code", 429)

    gemini_res = _sample_crew_result("gemini", "gemini-3.8-flash")
    groq_res = _sample_crew_result("groq", "openai/gpt-oss-120b")

    # Run 1: Groq fails -> Gemini 3.8 succeeds
    with patch.object(crew, "_execute_pipeline", side_effect=[groq_429, gemini_res]) as mock_exec1:
        res1 = crew.run(application_id=10, applicant_name="App 10", loan_amount=10000.0, documents=[])
        assert res1["llm_provider"] == "gemini"
        assert res1["llm_model"] == "gemini-3.8-flash"
        assert mock_exec1.call_args_list[0].kwargs["provider_name"] == "groq"

    # Run 2: Groq succeeds -> Groq must still be the primary attempt!
    with patch.object(crew, "_execute_pipeline", return_value=groq_res) as mock_exec2:
        res2 = crew.run(application_id=11, applicant_name="App 11", loan_amount=20000.0, documents=[])
        assert res2["llm_provider"] == "groq"
        assert res2["llm_model"] == "openai/gpt-oss-120b"
        assert mock_exec2.call_args_list[0].kwargs["provider_name"] == "groq"


# ── Test 11: Safe smoke test for provider and model resolution ─────────────

def test_safe_smoke_test_provider_resolution():
    # 1. Groq LLM instance
    groq_llm = get_llm(provider="groq", model="openai/gpt-oss-120b")
    assert groq_llm is not None
    assert "openai/gpt-oss-120b" in groq_llm.model or "groq" in groq_llm.model

    # 2. Gemini 3.8 Flash LLM instance
    gemini_38 = get_llm(provider="gemini", model="gemini-3.8-flash")
    assert gemini_38 is not None
    assert "3.8" in gemini_38.model or "gemini" in gemini_38.model.lower()

    # 3. Gemini 3.5 Flash-Lite LLM instance
    gemini_35 = get_llm(provider="gemini", model="gemini-3.5-flash-lite")
    assert gemini_35 is not None
    assert "3.5" in gemini_35.model or "lite" in gemini_35.model.lower()

    # 4. Agents construction
    mock_db = MagicMock()
    agents = build_agents(mock_db, provider="gemini", model="gemini-3.5-flash-lite")
    assert len(agents) == 5
    assert "document_analyst" in agents
    assert "extraction_specialist" in agents
    assert "verification_officer" in agents
    assert "gov_verification_agent" in agents
    assert "compliance_reporter" in agents
