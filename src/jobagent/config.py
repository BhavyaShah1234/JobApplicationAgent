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
    # Writes the resume, projects, ATS advice pass and cover letter (MODEL_WRITER; the model experiment sets it).
    # devstral2-123b (the user's "fast model for now", 2026-11-08): glm-5-3 was tried that day as a larger model and
    # wrote more specific bullets, but scored 73.3 vs 76.7 on General Mills and took ~140 min vs 44.
    model_writer: str = "devstral2-123b"
    # F05: what an ATS shortfall does to a valid resume. "warn" (default): the application may proceed, the review packet says the
    # average is below the target (or unscored); "block": no application is filled until the target is met. Invalid documents
    # (missing, not compiled, not one page, unresolved hard findings) always block, whatever this says.
    ats_shortfall: str = "warn"
    ats_target: float = 90.0

    # Step 15 learning is advisory: each lever stays off until the candidate switches it on (LEARNING_USE_SITE_MIX=true ...)
    learning_use_site_mix: bool = False          # drop checker sites whose scores have been shown to carry no signal
    learning_use_tailoring_hints: bool = False   # tell the tailor which skills went with replies in this role family
    learning_use_lessons: bool = False           # put the active lessons for this role family in the tailor's prompt
    fallback_planner: str = "qwen3:4b"
    fallback_vision: str = "qwen2.5vl:3b"
    fallback_fast: str = "qwen3:4b"
    fallback_embed: str = "nomic-embed-text"

    # Retry policy for Voyager (429 token budget / 503 capacity)
    llm_max_retries: int = 4
    llm_timeout_s: float = 600.0
    # F19: bounds on one `structured_invoke` (across all its methods and retries): wall-clock seconds, calls, and how many times
    # a rate limit / capacity error is waited out before the call gives up
    llm_call_deadline_s: float = 900.0
    llm_max_calls: int = 8
    llm_rate_retries: int = 3

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
