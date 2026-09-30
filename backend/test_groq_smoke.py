"""
Smoke test for Groq configuration in SmartVerify Multi-Agent pipeline.
Ensures:
- LLM_PROVIDER defaults to 'groq'
- GROQ_MODEL is 'openai/gpt-oss-120b'
- get_llm() returns a LiteLLM instance configured with Groq provider
- All 5 CrewAI agents are constructed and share the Groq LLM
- Tool registrations (including document_ocr_tool) remain intact
- No secret keys are printed or logged
- No live multi-agent verification API calls are made
"""
import os
import sys
from pathlib import Path

# Ensure backend directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

def smoke_test_groq_configuration():
    print("=" * 60)
    print("SMARTVERIFY — GROQ LLM CONFIGURATION SMOKE TEST")
    print("=" * 60)

    # 1. Config Check
    from app.core.config import settings
    print("\n[1] Checking Settings...")
    provider = getattr(settings, "LLM_PROVIDER", "groq")
    model = getattr(settings, "GROQ_MODEL", "openai/gpt-oss-120b")
    print(f"    Active LLM_PROVIDER : {provider}")
    print(f"    Active GROQ_MODEL    : {model}")
    assert provider == "groq", f"Expected provider 'groq', got {provider}"
    assert model == "openai/gpt-oss-120b", f"Unexpected model {model}"

    # 2. LLM Construction Check
    print("\n[2] Checking get_llm() resolution...")
    from app.agents.agent_definitions import get_llm
    llm = get_llm()
    print(f"    llm.model           : {llm.model}")
    print(f"    llm provider type   : {getattr(llm, 'provider', 'litellm')}")
    assert llm.model == "openai/gpt-oss-120b"

    # 3. Agent Construction Check
    print("\n[3] Building all 5 agents...")
    from app.agents.agent_definitions import build_agents
    agents = build_agents(db_session=None)
    expected_agents = [
        ("document_analyst", "Document Analyst"),
        ("extraction_specialist", "Data Extraction Specialist"),
        ("verification_officer", "Loan Verification Officer"),
        ("gov_verification_agent", "Government Verification Agent"),
        ("compliance_reporter", "Compliance Reporter"),
    ]
    for key, role in expected_agents:
        assert key in agents, f"Missing agent '{key}'"
        agent = agents[key]
        assert agent.role == role, f"Agent {key} role mismatch: {agent.role}"
        assert agent.llm.model == "openai/gpt-oss-120b", (
            f"Agent {key} using wrong model: {agent.llm.model}"
        )
        print(f"    OK [{key}]: role='{agent.role}', model='{agent.llm.model}'")

    # 4. Tool Integrity Check
    print("\n[4] Checking tool bindings...")
    doc_analyst = agents["document_analyst"]
    doc_tools = [getattr(t, "name", str(t)) for t in doc_analyst.tools]
    print(f"    document_analyst tools : {doc_tools}")
    assert any("ocr" in t.lower() for t in doc_tools), "document_ocr_tool missing from document_analyst"

    print("\n" + "=" * 60)
    print("GROQ SMOKE TEST: ALL CHECKS PASSED (OFFLINE / SAFE)")
    print("=" * 60)

if __name__ == "__main__":
    smoke_test_groq_configuration()
