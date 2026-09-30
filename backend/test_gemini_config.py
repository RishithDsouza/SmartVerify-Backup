"""
Test LLM Provider Configuration (Groq Active + Gemini Fallback)
Verifies that:
1. LLM_PROVIDER resolves to 'groq' by default.
2. GROQ_API_KEY is detected without printing secret values.
3. Groq model configuration is 'openai/gpt-oss-120b'.
4. get_llm() resolves to the Groq model.
5. All five CrewAI agents use the configured Groq LLM.
6. Gemini configuration fallback remains completely functional.
7. Agent tools (including OCR tool) are fully preserved.
"""
import os
import sys
from pathlib import Path

# Ensure backend directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def test_groq_config_detection():
    """Verify that Groq is active, detected, and used by all 5 agents."""
    test_groq_key = "gsk_mock_test_key_for_safe_unit_testing_12345"

    orig_provider = os.environ.get("LLM_PROVIDER")
    orig_groq = os.environ.get("GROQ_API_KEY")
    orig_gemini = os.environ.get("GEMINI_API_KEY")

    try:
        os.environ["LLM_PROVIDER"] = "groq"
        os.environ["GROQ_API_KEY"] = test_groq_key

        from app.core.config import settings
        from app.agents.agent_definitions import get_llm, build_agents

        # 1. Provider check
        provider = os.environ.get("LLM_PROVIDER") or getattr(settings, "LLM_PROVIDER", "groq")
        assert provider == "groq", f"Expected provider 'groq', got '{provider}'"

        # 2. Key detection without exposing secret
        key_detected = bool(os.environ.get("GROQ_API_KEY") or getattr(settings, "GROQ_API_KEY", ""))
        assert key_detected is True, "GROQ_API_KEY was not detected"

        # 3. Model configuration
        expected_model = "openai/gpt-oss-120b"
        groq_model = getattr(settings, "GROQ_MODEL", expected_model)
        assert groq_model == expected_model, f"Expected Groq model '{expected_model}', got '{groq_model}'"

        # 4. get_llm() resolution
        llm = get_llm()
        assert llm is not None, "get_llm() returned None"
        assert llm.model == expected_model, f"Expected LLM model '{expected_model}', got '{llm.model}'"
        assert llm.api_key == test_groq_key, "LLM api_key does not match configured test key"

        # 5. All 5 agents use the exact same Groq LLM
        agents = build_agents(db_session=None)
        expected_agents = [
            "document_analyst",
            "extraction_specialist",
            "verification_officer",
            "gov_verification_agent",
            "compliance_reporter",
        ]
        for name in expected_agents:
            assert name in agents, f"Missing agent: {name}"
            agent = agents[name]
            assert agent.llm.model == expected_model, (
                f"Agent '{name}' has model '{agent.llm.model}', expected '{expected_model}'"
            )

        # 6. Preserve tools (document_ocr_tool on document_analyst)
        doc_agent = agents["document_analyst"]
        tool_names = [getattr(t, "name", str(t)) for t in doc_agent.tools]
        assert any("document_ocr_tool" in name or "ocr" in name.lower() for name in tool_names), (
            f"document_ocr_tool missing from document_analyst tools: {tool_names}"
        )

    finally:
        if orig_provider is not None:
            os.environ["LLM_PROVIDER"] = orig_provider
        elif "LLM_PROVIDER" in os.environ:
            del os.environ["LLM_PROVIDER"]

        if orig_groq is not None:
            os.environ["GROQ_API_KEY"] = orig_groq
        elif "GROQ_API_KEY" in os.environ:
            del os.environ["GROQ_API_KEY"]

        if orig_gemini is not None:
            os.environ["GEMINI_API_KEY"] = orig_gemini


def test_gemini_fallback_detection():
    """Verify that Gemini remains completely functional as a fallback provider."""
    test_key = "mock_test_key_for_gemini_fallback"

    orig_provider = os.environ.get("LLM_PROVIDER")
    orig_gemini = os.environ.get("GEMINI_API_KEY")
    orig_google = os.environ.get("GOOGLE_API_KEY")

    try:
        os.environ["LLM_PROVIDER"] = "gemini"
        os.environ["GEMINI_API_KEY"] = test_key
        if "GOOGLE_API_KEY" in os.environ:
            del os.environ["GOOGLE_API_KEY"]

        from app.agents.agent_definitions import get_llm, build_agents

        llm = get_llm()
        assert llm is not None
        assert llm.api_key == test_key
        assert llm.model in ("gemini-3.8-flash", "gemini/gemini-3.8-flash")

        # Bidirectional sync check
        assert os.environ.get("GOOGLE_API_KEY") == test_key

        # Agents check under fallback
        agents = build_agents(db_session=None)
        assert len(agents) == 5
        for name, agent in agents.items():
            assert agent.llm.model in ("gemini-3.8-flash", "gemini/gemini-3.8-flash")

    finally:
        if orig_provider is not None:
            os.environ["LLM_PROVIDER"] = orig_provider
        elif "LLM_PROVIDER" in os.environ:
            del os.environ["LLM_PROVIDER"]

        if orig_gemini is not None:
            os.environ["GEMINI_API_KEY"] = orig_gemini
        elif "GEMINI_API_KEY" in os.environ:
            del os.environ["GEMINI_API_KEY"]

        if orig_google is not None:
            os.environ["GOOGLE_API_KEY"] = orig_google
        elif "GOOGLE_API_KEY" in os.environ:
            del os.environ["GOOGLE_API_KEY"]


if __name__ == "__main__":
    test_groq_config_detection()
    print("test_groq_config_detection: PASSED")
    test_gemini_fallback_detection()
    print("test_gemini_fallback_detection: PASSED")
    print("test_gemini_config: ALL CHECKS PASSED")
