"""LLM access by role: Voyager (OpenAI-compatible) first, local Ollama as fallback.

The OpenAI client already retries 429 (token budget) and 503 (capacity) with
exponential backoff; once those retries are exhausted the call falls over to Ollama.
"""

import sys
from contextvars import ContextVar
from typing import Literal

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel

from jobagent.config import Settings, get_settings

Role = Literal["planner", "vision", "fast"]


def _primary(role: Role, s: Settings, temperature: float | None = None, timeout: float | None = None,
             max_tokens: int | None = None) -> BaseChatModel:
    return ChatOpenAI(
        model=getattr(s, f"model_{role}"),
        base_url=s.openai_base_url,
        api_key=s.openai_api_key,
        max_retries=s.llm_max_retries if timeout is None else 1,
        timeout=timeout or s.llm_timeout_s,
        temperature=temperature,
        max_tokens=max_tokens,
    )


class _FallbackNotice(BaseCallbackHandler):
    """Say so whenever a call falls back from Voyager to local Ollama (otherwise it is silent)."""

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        name = (serialized or {}).get("kwargs", {}).get("model", "?")
        print(f"[llm] Voyager unavailable; falling back to local Ollama model {name}", file=sys.stderr, flush=True)


def _fallback(role: Role, s: Settings, temperature: float | None = None) -> BaseChatModel:
    return ChatOllama(model=getattr(s, f"fallback_{role}"), base_url=s.ollama_base_url, temperature=temperature,
                      callbacks=[_FallbackNotice()])


def chat(role: Role = "planner", settings: Settings | None = None) -> Runnable:
    """Chat model for a role, with automatic fallback to Ollama."""
    s = settings or get_settings()
    return _primary(role, s).with_fallbacks([_fallback(role, s)])


def structured(schema: type[BaseModel], role: Role = "planner", settings: Settings | None = None,
               method: str = "function_calling", timeout: float | None = None,
               max_tokens: int | None = None) -> Runnable:
    """Chat model that returns `schema` instances, with fallback (structured output is bound per model).

    function_calling is the default: on Voyager all three methods work, and it was the fastest (~4x).
    `timeout` (seconds, one retry) and `max_tokens` keep a stuck or rambling call from blocking a run.
    """
    s = settings or get_settings()
    # Extraction/classification must be repeatable: temperature 0.
    return _primary(role, s, 0, timeout, max_tokens).with_structured_output(schema, method=method).with_fallbacks(
        [_fallback(role, s, 0).with_structured_output(schema, method=method)]
    )


# Per-thread override of the writing model (the model experiment runs several models in one process)
WRITER_MODEL: ContextVar[str | None] = ContextVar("writer_model", default=None)


def writer(max_tokens: int = 16000, timeout: float = 600, settings: Settings | None = None,
           model: str | None = None) -> ChatOpenAI:
    """The writing model (`model`, else this thread's WRITER_MODEL, else settings.model_writer): temperature 0,
    one retry."""
    s = settings or get_settings()
    return ChatOpenAI(model=model or WRITER_MODEL.get() or s.model_writer, base_url=s.openai_base_url, api_key=s.openai_api_key,
                      max_retries=1, timeout=timeout, temperature=0, max_tokens=max_tokens)


class StructuredError(RuntimeError):
    pass


_METHODS = ("json_schema", "function_calling", "json_mode")


def _parse_json(text: str, schema: type[BaseModel]) -> BaseModel:
    """`schema` from a free-text answer: drops <think> blocks and code fences, takes the outermost JSON object."""
    import re
    text = re.sub(r"(?s)<think>.*?</think>", "", text or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    return schema.model_validate_json(text[start:end + 1])


def structured_invoke(model: ChatOpenAI, schema: type[BaseModel], messages: list, first: str = "json_schema",
                      log=None) -> BaseModel:
    """Call `model` for a `schema` instance, robust to models that lack a structured-output method or answer
    empty: tries `first`, then the other methods (json_schema, function_calling, plain JSON with the schema in the
    prompt), each retried once on an empty or invalid answer. A timeout is not retried with another method (the
    model is just slow). Raises StructuredError if nothing works."""
    import json

    import openai
    from langchain_core.messages import SystemMessage
    errors = []
    for method in (first, *[m for m in _METHODS if m != first]):
        for _ in range(2):
            try:
                if method == "json_mode":
                    hint = SystemMessage("Answer with ONE JSON object only (no prose, no code fence) that matches "
                                         "this JSON schema:\n" + json.dumps(schema.model_json_schema()))
                    out = _parse_json(model.invoke([*messages, hint]).content, schema)
                else:
                    out = model.with_structured_output(schema, method=method).invoke(messages)
                if out is not None:
                    return out
                errors.append(f"{method}: empty answer")
            except (openai.APITimeoutError, TimeoutError) as e:
                raise StructuredError(f"timeout ({type(e).__name__})") from e
            except openai.BadRequestError as e:  # the method itself is unsupported: next method
                errors.append(f"{method}: {str(e)[:120]}")
                break
            except Exception as e:  # invalid / truncated answer: retry once, then next method
                errors.append(f"{method}: {type(e).__name__}: {str(e)[:120]}")
        if log:
            log(f"[llm] {model.model_name}: {method} failed ({errors[-1][:100]}); trying the next method")
    raise StructuredError("; ".join(errors)[-600:])


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
            print("[llm] Voyager embeddings unavailable; falling back to local Ollama", file=sys.stderr, flush=True)
            return self.fallback.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        try:
            return self.primary.embed_query(text)
        except Exception:
            self.used_fallback = True
            print("[llm] Voyager embeddings unavailable; falling back to local Ollama", file=sys.stderr, flush=True)
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
