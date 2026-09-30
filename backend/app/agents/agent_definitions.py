"""
SmartVerify Multi-Agent Definitions
─────────────────────────────────────
Defines the specialised CrewAI agents for Milestone 2:

    1. Document Analyst
    2. Data Extraction Specialist
    3. Loan Verification Officer
    4. Government Verification Agent
    5. Compliance Reporter
"""

import logging
import os
from crewai import Agent, LLM

from app.core.config import settings
from app.agents.tools import (
    MemoryTool, PolicyRetrievalTool, SimilaritySearchTool,
    OCRTool, InformationExtractionTool, FraudDetectionTool, ReportGenerationTool
)

logger = logging.getLogger(__name__)


def get_llm(provider: str = None, model: str = None) -> LLM:
    """
    Build an LLM instance for CrewAI agents.
    Supports Groq (active default via LLM_PROVIDER="groq") and Gemini (fallback via LLM_PROVIDER="gemini").
    """
    active_provider = (
        provider
        or os.environ.get("LLM_PROVIDER")
        or getattr(settings, "LLM_PROVIDER", "groq")
        or "groq"
    ).strip().lower()

    temperature = getattr(settings, "CREWAI_TEMPERATURE", 0.2)

    if active_provider == "groq":
        chosen_model = (
            model
            or os.environ.get("GROQ_MODEL")
            or getattr(settings, "GROQ_MODEL", "")
            or getattr(settings, "CREWAI_MODEL", "")
            or "openai/gpt-oss-120b"
        )

        api_key = (
            os.environ.get("GROQ_API_KEY")
            or getattr(settings, "GROQ_API_KEY", "")
            or getattr(settings, "groq_api_key", "")
            or None
        )
        if api_key:
            if not os.environ.get("GROQ_API_KEY"):
                os.environ["GROQ_API_KEY"] = api_key

        if chosen_model.startswith("openai/"):
            llm = LLM(
                model=chosen_model,
                temperature=temperature,
                api_key=api_key if api_key else None,
                base_url="https://api.groq.com/openai/v1",
            )
            llm.model = chosen_model
            return llm
        elif chosen_model.startswith("groq/"):
            return LLM(
                model=chosen_model,
                temperature=temperature,
                api_key=api_key if api_key else None,
            )
        else:
            return LLM(
                model=f"groq/{chosen_model}",
                temperature=temperature,
                api_key=api_key if api_key else None,
            )

    # Gemini fallback
    api_key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or getattr(settings, "GEMINI_API_KEY", "")
        or getattr(settings, "GOOGLE_API_KEY", "")
        or getattr(settings, "gemini_api_key", "")
        or None
    )

    # Ensure environment variables are populated for underlying Google GenAI SDK
    if api_key:
        if not os.environ.get("GEMINI_API_KEY"):
            os.environ["GEMINI_API_KEY"] = api_key
        if not os.environ.get("GOOGLE_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = api_key

    configured_model = (
        model
        or os.environ.get("GEMINI_MODEL")
        or getattr(settings, "GEMINI_MODEL", "")
        or "gemini-3.8-flash"
    ).strip()

    # Avoid deprecated gemini-2.5-flash (returns 404 in Google API)
    if "gemini-2.5" in configured_model:
        gemini_model = "gemini/gemini-3.8-flash"
    elif configured_model.startswith("gemini/"):
        gemini_model = configured_model
    else:
        gemini_model = f"gemini/{configured_model}"

    return LLM(
        model=gemini_model,
        temperature=temperature,
        api_key=api_key if api_key else None,
    )


def build_agents(db_session, shared_context=None, provider: str = None, model: str = None, llm: LLM = None) -> dict:
    if llm is None:
        llm = get_llm(provider=provider, model=model)
    memory_tool = MemoryTool(shared_context=shared_context)
    policy_tool = PolicyRetrievalTool()
    similarity_tool = SimilaritySearchTool()
    ocr_tool = OCRTool()
    extraction_tool = InformationExtractionTool()
    fraud_tool = FraudDetectionTool(db_session=db_session)
    report_tool = ReportGenerationTool()

    document_analyst = Agent(
        role="Document Analyst",
        goal=(
            "Review the provided OCR text and Structured JSON to verify document quality, "
            "detect missing documents, validate document types, and flag unreadable documents."
        ),
        backstory=(
            "You are a meticulous document processing specialist. You use the document_ocr_tool "
            "to extract text from submitted documents, then assess the quality, completeness, "
            "and readability of those documents."
        ),
        tools=[ocr_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    extraction_specialist = Agent(
        role="Data Extraction Specialist",
        goal=(
            "Consume ONLY the Structured JSON provided. Validate extracted values, "
            "normalize names, addresses, and dates, detect missing mandatory fields, "
            "and produce a cleaned applicant profile."
        ),
        backstory=(
            "You are an expert in data normalization. You use the information_extraction_tool "
            "to pull structured data from raw OCR text, normalize names, addresses, and dates, "
            "and flag any missing required fields to create a cleaned applicant profile."
        ),
        tools=[extraction_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    verification_officer = Agent(
        role="Loan Verification Officer",
        goal=(
            "Check eligibility, validate income and loan amount against the cleaned applicant "
            "profile and loan details, and produce a preliminary recommendation."
        ),
        backstory=(
            "You are a senior loan verification officer. You strictly use the provided "
            "Applicant Profile and Loan Details to determine eligibility. You must use "
            "the PolicyRetrievalTool to retrieve Bank Policies, Loan Eligibility Policies, "
            "and Income Policies from the vector database before making any recommendation."
        ),
        tools=[memory_tool, policy_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    gov_verification_agent = Agent(
        role="Government Verification Agent",
        goal=(
            "Consume Aadhaar and PAN numbers, read the simulated/existing verification status, "
            "compare OCR values with verification status, flag mismatches or missing verifications, "
            "and produce a verification summary."
        ),
        backstory=(
            "You are a government ID verification specialist. You check Aadhaar and PAN "
            "validity statuses using the manual UI record. You must use the "
            "SimilaritySearchTool to retrieve similar historical applications, and the "
            "fraud_detection_tool to calculate a risk score based on duplicates and inconsistencies."
        ),
        tools=[memory_tool, similarity_tool, fraud_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    compliance_reporter = Agent(
        role="Compliance Reporter",
        goal=(
            "Consume outputs from all previous agents and generate the Final AI Output JSON, "
            "including the Final Recommendation, Human-in-the-loop Recommendation, and "
            "Consolidated AI Summary."
        ),
        backstory=(
            "You are responsible for the final audit and synthesis. You review the findings "
            "of all agents and compile them into a final decision, handing off structured "
            "findings to the downstream compliance and report generation pipeline."
        ),
        tools=[memory_tool, report_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    return {
        "document_analyst": document_analyst,
        "extraction_specialist": extraction_specialist,
        "verification_officer": verification_officer,
        "gov_verification_agent": gov_verification_agent,
        "compliance_reporter": compliance_reporter,
    }
