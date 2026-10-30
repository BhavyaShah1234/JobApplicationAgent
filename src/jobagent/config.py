"""Runtime settings, read from environment / .env (see .env.example)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The user's local time: "today", "present" and "latest" are judged in this zone (America/Phoenix = MST, no DST)
    timezone: str = "America/Phoenix"

    # Paths (bind-mounted volumes inside the container)
    data_dir: Path = Path("/app/data")
    state_dir: Path = Path("/app/state")
    outputs_dir: Path = Path("/app/outputs")

    # Voyager (ASU RC) — OpenAI-compatible
    openai_base_url: str = "https://openai.rc.asu.edu/v1"
    openai_api_key: SecretStr = SecretStr("")

    # Ollama fallback
    ollama_base_url: str = "http://localhost:11434"

    # Model roles: Voyager primary / Ollama fallback
    model_planner: str = "qwen35-397b-a17b"
    model_vision: str = "qwen38-27b"
    model_fast: str = "qwen3-30b-a3b"
    model_embed: str = "qwen3-embedding-8b"
    # Writes the resume, projects, ATS advice pass and cover letter (MODEL_WRITER; the model experiment sets it)
    model_writer: str = "glm-5-3"
    fallback_planner: str = "qwen3:4b"
    fallback_vision: str = "qwen2.5vl:3b"
    fallback_fast: str = "qwen3:4b"
    fallback_embed: str = "nomic-embed-text"

    # Retry policy for Voyager (429 token budget / 503 capacity)
    llm_max_retries: int = 4
    llm_timeout_s: float = 600.0

    # Credential vault (Fernet key, urlsafe base64 of 32 bytes)
    vault_key: SecretStr = SecretStr("")

    # Browser
    chrome_channel: str = "chrome"
    headless: bool = False
    display: str = Field(default=":99", alias="DISPLAY")

    @property
    def vault_path(self) -> Path:
        return self.state_dir / "vault.enc"

    @property
    def chrome_profile_dir(self) -> Path:
        return self.state_dir / "chrome-profile"

    @property
    def missing_capabilities_path(self) -> Path:
        return self.state_dir / "missing-agent-capabilities.txt"


@lru_cache
def get_settings() -> Settings:
    return Settings()
