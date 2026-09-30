"""Application configuration via environment variables."""
from pathlib import Path
from typing import List
import os
from dotenv import load_dotenv
from pydantic import model_validator
from pydantic_settings import BaseSettings

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_PATH = _BACKEND_DIR / ".env"
if _ENV_PATH.is_file():
    load_dotenv(dotenv_path=_ENV_PATH, override=False)

class Settings(BaseSettings):
    APP_NAME: str = "SmartVerify"
    DEBUG: bool = False
    SECRET_KEY: str = "changeme-use-strong-secret-in-production"

    # Active LLM Provider: "groq" (default) or "gemini" (fallback)
    LLM_PROVIDER: str = "groq"
    GROQ_API_KEY: str = ""
    groq_api_key: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-120b"

    # Gemini settings (preserved as fallback)
    gemini_api_key: str = ""
    GEMINI_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini/gemini-flash-latest"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    DATABASE_URL: str = "postgresql://postgres:admin@db:5432/smartverify"
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
        "http://localhost",
    ]

    UPLOAD_DIR: str = "/app/uploads"
    REPORT_DIR: str = "/app/reports"
    MAX_UPLOAD_SIZE_MB: int = 20

    OCR_LANGUAGE: str = "en"
    google_vision_api_key: str = ""
    SPACY_MODEL: str = "en_core_web_sm"
    MIN_INCOME_FOR_LOAN: float = 25000.0
    MIN_VERIFICATION_SCORE: float = 60.0
    FRAUD_RISK_THRESHOLD: float = 70.0

    CHROMADB_DIR: str = "/app/chroma_db"


    # ── CrewAI Multi-Agent System ─────────────────────────────────────────
    # Toggle between the deterministic pipeline (/verify) and the
    # multi-agent CrewAI pipeline (/verify-agentic).
    CREWAI_ENABLED: bool = True
    # Active model string passed to CrewAI LLM wrapper via LiteLLM:
    CREWAI_MODEL: str = "openai/gpt-oss-120b"
    CREWAI_TEMPERATURE: float = 0.2
    # API keys for the chosen LLM provider are read directly from the
    # environment by CrewAI/LiteLLM (e.g. GROQ_API_KEY, GEMINI_API_KEY).

    @model_validator(mode="after")
    def resolve_relative_paths(self):
        if self.DATABASE_URL.startswith("sqlite:///./"):
            rel_path = self.DATABASE_URL[len("sqlite:///./"):]
            abs_db = (_BACKEND_DIR / rel_path).resolve()
            self.DATABASE_URL = f"sqlite:///{abs_db.as_posix()}"
        elif self.DATABASE_URL.startswith("sqlite:///") and not self.DATABASE_URL.startswith("sqlite:////"):
            rel_path = self.DATABASE_URL[len("sqlite:///"):]
            if not os.path.isabs(rel_path):
                abs_db = (_BACKEND_DIR / rel_path).resolve()
                self.DATABASE_URL = f"sqlite:///{abs_db.as_posix()}"

        if not os.path.isabs(self.UPLOAD_DIR):
            self.UPLOAD_DIR = str((_BACKEND_DIR / self.UPLOAD_DIR).resolve())
        if not os.path.isabs(self.REPORT_DIR):
            self.REPORT_DIR = str((_BACKEND_DIR / self.REPORT_DIR).resolve())
        if not os.path.isabs(self.CHROMADB_DIR):
            self.CHROMADB_DIR = str((_BACKEND_DIR / self.CHROMADB_DIR).resolve())

        # Synchronize Groq API key to os.environ for CrewAI / LiteLLM
        resolved_groq_key = (
            self.GROQ_API_KEY
            or self.groq_api_key
            or os.environ.get("GROQ_API_KEY")
            or ""
        )
        if resolved_groq_key:
            if not os.environ.get("GROQ_API_KEY"):
                os.environ["GROQ_API_KEY"] = resolved_groq_key

        # Synchronize Gemini / Google API keys to os.environ for CrewAI / LiteLLM / Google GenAI SDK
        resolved_key = (
            self.GEMINI_API_KEY
            or self.GOOGLE_API_KEY
            or self.gemini_api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or ""
        )
        if resolved_key:
            if not os.environ.get("GEMINI_API_KEY"):
                os.environ["GEMINI_API_KEY"] = resolved_key
            if not os.environ.get("GOOGLE_API_KEY"):
                os.environ["GOOGLE_API_KEY"] = resolved_key

        return self

    class Config:
        env_file = str(_ENV_PATH) if _ENV_PATH.is_file() else ".env"
        extra = "ignore"

settings = Settings()
