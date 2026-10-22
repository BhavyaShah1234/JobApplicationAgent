"""LLM access by role: Voyager (OpenAI-compatible) first, local Ollama as fallback.

The OpenAI client already retries 429 (token budget) and 503 (capacity) with
exponential backoff; once those retries are exhausted the call falls over to Ollama.
"""

from typing import Literal

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel

from jobagent.config import Settings, get_settings

Role = Literal["planner", "vision", "fast"]


def _primary(role: Role, s: Settings, temperature: float | None = None) -> BaseChatModel:
    return ChatOpenAI(
        model=getattr(s, f"model_{role}"),
        base_url=s.openai_base_url,
        api_key=s.openai_api_key,
        max_retries=s.llm_max_retries,
        timeout=s.llm_timeout_s,
        temperature=temperature,
    )


def _fallback(role: Role, s: Settings, temperature: float | None = None) -> BaseChatModel:
    return ChatOllama(model=getattr(s, f"fallback_{role}"), base_url=s.ollama_base_url, temperature=temperature)


def chat(role: Role = "planner", settings: Settings | None = None) -> Runnable:
    """Chat model for a role, with automatic fallback to Ollama."""
    s = settings or get_settings()
    return _primary(role, s).with_fallbacks([_fallback(role, s)])


def structured(schema: type[BaseModel], role: Role = "planner", settings: Settings | None = None,
               method: str = "function_calling") -> Runnable:
    """Chat model that returns `schema` instances, with fallback (structured output is bound per model).

    function_calling is the default: on Voyager all three methods work, and it was the fastest (~4x).
    """
    s = settings or get_settings()
    # Extraction/classification must be repeatable: temperature 0.
    return _primary(role, s, 0).with_structured_output(schema, method=method).with_fallbacks(
        [_fallback(role, s, 0).with_structured_output(schema, method=method)]
    )


class FallbackEmbeddings(Embeddings):
    """Embeddings don't support `.with_fallbacks`, so fall over manually."""

    def __init__(self, primary: Embeddings, fallback: Embeddings):
        self.primary, self.fallback = primary, fallback
        self.used_fallback = False

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            return self.primary.embed_documents(texts)
        except Exception:
            self.used_fallback = True
            return self.fallback.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        try:
            return self.primary.embed_query(text)
        except Exception:
            self.used_fallback = True
            return self.fallback.embed_query(text)


def embeddings(settings: Settings | None = None) -> FallbackEmbeddings:
    s = settings or get_settings()
    primary = OpenAIEmbeddings(
        model=s.model_embed,
        base_url=s.openai_base_url,
        api_key=s.openai_api_key,
        max_retries=s.llm_max_retries,
        # Voyager models aren't tiktoken-tokenized OpenAI models; send raw strings.
        check_embedding_ctx_length=False,
    )
    return FallbackEmbeddings(primary, OllamaEmbeddings(model=s.fallback_embed, base_url=s.ollama_base_url))
