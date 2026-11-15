"""Runtime settings, read from environment / .env (see .env.example)."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# Behaviour you may want to change without touching code lives in config/<concern>.yaml (step 21); precedence, highest first:
# environment variable > .env > config/*.yaml > the default in this class. Paths, secrets and the display are environment-only.
CONFIG_FILES = ("models", "forms", "ats", "browser", "queue", "learning", "logging", "skills")
CONCERN = {                      # which file each setting belongs to
    "models": ["openai_base_url", "ollama_base_url", "model_planner", "model_vision", "model_fast", "model_embed", "model_writer",
               "fallback_planner", "fallback_vision", "fallback_fast", "fallback_embed", "llm_max_retries", "llm_timeout_s",
               "llm_call_deadline_s", "llm_max_calls", "llm_rate_retries"],
    "forms": ["filling_strategy", "apply_max_pages"],
    "ats": ["ats_shortfall", "ats_target"],
    "browser": ["chrome_channel", "headless"],
    "queue": ["pacing_max_per_company", "pacing_window_days", "queue_lease_seconds"],
    "learning": ["learning_use_site_mix", "learning_use_tailoring_hints", "learning_use_lessons"],
    "logging": ["log_level"],
    "skills": ["skills_use_graph", "skills_max_neighbours", "skills_duty_merge", "skills_skill_merge"],
}


class _ConcernYaml(PydanticBaseSettingsSource):
    """config/<concern>.yaml, accepting only the keys listed for that concern in CONCERN: paths, credentials, the display and keys that
    belong to another file are not read from YAML at all (`describe` reports them as rejected)."""

    def get_field_value(self, field, field_name):                  # required by the base class; __call__ does the work
        return None, field_name, False

    def __call__(self) -> dict:
        import yaml
        out: dict = {}
        for name in CONFIG_FILES:
            f = config_dir() / f"{name}.yaml"
            if f.is_file():
                data = yaml.safe_load(f.read_text()) or {}
                if not isinstance(data, dict):
                    raise ValueError(f"{f}: must be a mapping of setting: value")
                out.update({k: v for k, v in data.items() if k in CONCERN[name]})
        return out


def config_dir() -> Path:
    """Where the config/*.yaml files are: $CONFIG_DIR, else /app/config in the container, else ./config."""
    if os.environ.get("CONFIG_DIR"):
        return Path(os.environ["CONFIG_DIR"])
    return Path("/app/config") if Path("/app/config").is_dir() else Path("config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return (init_settings, env_settings, dotenv_settings, _ConcernYaml(settings_cls), file_secret_settings)

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
    filling_strategy: Literal['original','experimental'] = 'experimental'
    # F05: what an ATS shortfall does to a valid resume. "warn" (default): the application may proceed, the review packet says the
    # average is below the target (or unscored); "block": no application is filled until the target is met. Invalid documents
    # (missing, not compiled, not one page, unresolved hard findings) always block, whatever this says.
    ats_shortfall: Literal["warn", "block"] = "warn"
    ats_target: float = Field(default=90.0, ge=0, le=100)

    # Step 15 learning is advisory: each lever stays off until the candidate switches it on (LEARNING_USE_SITE_MIX=true ...)
    learning_use_site_mix: bool = False          # drop checker sites whose scores have been shown to carry no signal
    learning_use_tailoring_hints: bool = False   # tell the tailor which skills went with replies in this role family
    learning_use_lessons: bool = False           # put the active lessons for this role family in the tailor's prompt
    fallback_planner: str = "qwen3:4b"
    fallback_vision: str = "qwen2.5vl:3b"
    fallback_fast: str = "qwen3:4b"
    fallback_embed: str = "nomic-embed-text"

    # Retry policy for Voyager (429 token budget / 503 capacity)
    llm_max_retries: int = Field(default=4, ge=0, le=20)
    llm_timeout_s: float = Field(default=600.0, gt=0)
    # F19: bounds on one `structured_invoke` (across all its methods and retries): wall-clock seconds, calls, and how many times
    # a rate limit / capacity error is waited out before the call gives up
    llm_call_deadline_s: float = Field(default=900.0, gt=0)
    llm_max_calls: int = Field(default=8, ge=1, le=100)
    llm_rate_retries: int = Field(default=3, ge=0, le=20)
    # Per-company pacing for the queue (tools/pacing.py): at most this many applications started or sent per company in the window; 0 = off
    pacing_max_per_company: int = Field(default=2, ge=0)
    pacing_window_days: int = Field(default=7, ge=1, le=365)
    queue_lease_seconds: int = Field(default=900, ge=30)               # how long a claimed queue job stays its runner's without a heartbeat (tools/jobqueue.py)
    apply_max_pages: int = Field(default=15, ge=1, le=60)                    # the most wizard pages `apply` walks through before it stops
    skills_use_graph: bool = True                          # step 4c: neighbouring skills the candidate owns may fill spare room on the resume
    skills_max_neighbours: int = Field(default=6, ge=1, le=20)
    skills_duty_merge: float = Field(default=0.85, gt=0, le=1)
    skills_skill_merge: float = Field(default=0.9, gt=0, le=1)
    log_level: Literal["normal", "quiet"] = "normal"       # quiet hides progress detail on screen (the log file keeps everything)

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


def describe(settings: Settings | None = None) -> list[dict]:
    """Every setting in config/*.yaml's scope with its effective value and where it came from (env | .env | yaml:<file> | default),
    plus any key in a yaml file that names no setting (`source` = 'unknown key')."""
    import yaml
    s = settings or get_settings()
    dotenv = {}
    if Path(".env").is_file():
        for line in Path(".env").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                dotenv[k.strip().upper()] = v.strip()
    in_yaml: dict[str, str] = {}
    unknown: list[dict] = []
    for name in CONFIG_FILES:
        f = config_dir() / f"{name}.yaml"
        if f.is_file():
            for key in (yaml.safe_load(f.read_text()) or {}):
                if key in CONCERN[name]:
                    in_yaml[key] = f"yaml:{name}.yaml"
                elif key in Settings.model_fields or key in {v.alias for v in Settings.model_fields.values() if v.alias}:
                    unknown.append({"concern": name, "key": key, "value": "", "source": "rejected: environment-only or belongs in another file"})
                else:
                    unknown.append({"concern": name, "key": key, "value": "", "source": "unknown key (ignored)"})
    rows = []
    for concern, keys in CONCERN.items():
        for key in keys:
            if key not in Settings.model_fields:
                continue
            up = key.upper()
            source = "env" if up in os.environ else ".env" if up in dotenv else in_yaml.get(key, "default")
            rows.append({"concern": concern, "key": key, "value": getattr(s, key), "source": source})
    return rows + unknown
