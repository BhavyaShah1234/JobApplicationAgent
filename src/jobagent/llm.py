"""LLM access by role: Voyager (OpenAI-compatible) first, local Ollama as fallback.

The OpenAI client already retries 429 (token budget) and 503 (capacity) with
exponential backoff; once those retries are exhausted the call falls over to Ollama.
"""

import sys
import threading
import time
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


# Errors that mean "Voyager is unavailable right now", the only ones that send a call to local Ollama. A wrong key, a wrong
# model name or a request the server rejects would fail on the fallback too, or worse, succeed there with a much smaller model
# and no one the wiser (F19).
def _transient_errors() -> tuple[type[BaseException], ...]:
    import openai
    return (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError)


class _FallbackNotice(BaseCallbackHandler):
    """Say so whenever a call falls back from Voyager to local Ollama (otherwise it is silent), and record it."""

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        name = (serialized or {}).get("kwargs", {}).get("model", "?")
        FALLBACKS.append({"provider": "ollama", "model": name})
        print(f"[llm] Voyager unavailable; falling back to local Ollama model {name}", file=sys.stderr, flush=True)


# every fallback taken in this process (provider, model): `structured(...)` / `chat(...)` records here; the writers never fall back
FALLBACKS: list[dict] = []


def _fallback(role: Role, s: Settings, temperature: float | None = None) -> BaseChatModel:
    return ChatOllama(model=getattr(s, f"fallback_{role}"), base_url=s.ollama_base_url, temperature=temperature,
                      callbacks=[_FallbackNotice()])


def chat(role: Role = "planner", settings: Settings | None = None) -> Runnable:
    """Chat model for a role, with automatic fallback to Ollama."""
    s = settings or get_settings()
    return _primary(role, s).with_fallbacks([_fallback(role, s)], exceptions_to_handle=_transient_errors())


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
        [_fallback(role, s, 0).with_structured_output(schema, method=method)], exceptions_to_handle=_transient_errors()
    )


# Per-thread override of the writing model (the model experiment runs several models in one process)
WRITER_MODEL: ContextVar[str | None] = ContextVar("writer_model", default=None)


def writer(max_tokens: int = 16000, timeout: float = 600, settings: Settings | None = None,
           model: str | None = None) -> ChatOpenAI:
    """The writing model (`model`, else this thread's WRITER_MODEL, else settings.model_writer): temperature 0.

    **Voyager only, by design (F19).** A resume, cover letter or form answer written by a small local model is worse than a
    delayed one, so the writers do not fall back to Ollama; `structured_invoke` waits out rate limits and capacity errors
    within a bounded budget instead, and `last_call()` says which model actually answered. Client-side retries are off
    (`max_retries=0`) because `structured_invoke` owns the retry policy: retrying inside the client too would multiply the
    waits."""
    s = settings or get_settings()
    return ChatOpenAI(model=model or WRITER_MODEL.get() or s.model_writer, base_url=s.openai_base_url, api_key=s.openai_api_key,
                      max_retries=0, timeout=timeout, temperature=0, max_tokens=max_tokens)  # structured_invoke retries


class StructuredError(RuntimeError):
    """A structured call that did not produce an answer. `kind`: config | rate_limit | transient | timeout | deadline |
    budget | unsupported | content | length | unknown (what finally stopped it)."""

    def __init__(self, message: str, kind: str = "unknown"):
        super().__init__(message)
        self.kind = kind


class LLMConfigError(StructuredError):
    """The call can never succeed as configured (bad or expired key, no permission, unknown model, exhausted quota): it fails at
    once, with no retries and no other output method tried."""


_METHODS = ("json_schema", "function_calling", "json_mode")
# model -> methods it ran out of tokens in (some models loop in json_schema mode until the cap): tried last from then on
_LOOPS: dict[str, set[str]] = {}

_calls = threading.local()


def last_call() -> dict:
    """What the most recent `structured_invoke` on this thread did: {provider, model, method, calls, waited_s, errors (kinds),
    seconds, ok}. The writers are Voyager-only, so `provider` is always "voyager"; a fallback taken by `structured()` / `chat()`
    is listed in `FALLBACKS`."""
    return dict(getattr(_calls, "last", {}))


def classify(exc: BaseException) -> str:
    """What kind of failure this is, which decides what is done about it:
    config (never retry), rate_limit / transient (wait and retry the SAME method), timeout, unsupported (the method is not
    available: try another), length (the answer hit the token cap: try another), content (the answer was unusable: ask again
    with the reason), unknown (treated as content once)."""
    import json

    import openai
    from pydantic import ValidationError

    if isinstance(exc, openai.RateLimitError):
        return "config" if getattr(exc, "code", None) == "insufficient_quota" else "rate_limit"
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError)):
        return "config"
    if isinstance(exc, (openai.APITimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(exc, (openai.APIConnectionError, openai.InternalServerError)):
        return "transient"
    if isinstance(exc, (openai.BadRequestError, openai.UnprocessableEntityError)):
        return "unsupported"
    if "LengthFinishReason" in type(exc).__name__:
        return "length"
    if isinstance(exc, (ValidationError, json.JSONDecodeError, ValueError)) or type(exc).__name__ == "OutputParserException":
        return "content"
    return "unknown"


def _retry_after(exc: BaseException, attempt: int) -> float:
    """Seconds to wait before the next try: the server's Retry-After if it sent one, else exponential with jitter; capped."""
    import random
    try:
        header = exc.response.headers.get("retry-after")  # type: ignore[attr-defined]
        if header is not None:
            return min(float(header), 60.0)
    except (AttributeError, ValueError):
        pass
    return min(2.0 ** attempt + random.uniform(0, 1), 30.0)


_sleep = time.sleep          # replaced in tests


def _feedback(exc: BaseException) -> str:
    """Why an answer was refused, in words a model can act on (a pydantic ValidationError carries the field and the rule)."""
    from pydantic import ValidationError

    cause = exc if isinstance(exc, ValidationError) else exc.__cause__ if isinstance(exc.__cause__, ValidationError) else None
    if cause is not None:
        parts = []
        for e in cause.errors()[:6]:
            where = ".".join(str(x) for x in e["loc"]) or "answer"
            parts.append(f"{where}: {str(e['msg']).removeprefix('Value error, ')}")
        return "; ".join(parts)[:900]
    return f"{type(exc).__name__}: {str(exc)[:400]}"


def _parse_json(text: str, schema: type[BaseModel]) -> BaseModel:
    """`schema` from a free-text answer: drops <think> blocks and code fences, takes the outermost JSON object."""
    import re
    text = re.sub(r"(?s)<think>.*?</think>", "", text or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    return schema.model_validate_json(text[start:end + 1])


_ABANDONED: list = []         # worker threads whose call outlasted its budget and is still running
MAX_ABANDONED = 6             # beyond this many, no new call is started: a provider that hangs must not pile up threads


def _bounded(fn, seconds: float):
    """Run `fn()` and wait at most `seconds` for it (F19). A provider call cannot be cancelled from outside, so one that outlasts
    the budget is ABANDONED in its worker and whatever it returns later is discarded - the caller gets a timeout now, not a late
    answer. The worker is a DAEMON thread, so an abandoned call never delays the interpreter's exit (a non-daemon executor worker
    is joined at shutdown, which made a failed CLI wait for the provider); the number of abandoned workers still running is
    bounded (`MAX_ABANDONED`). The caller's context variables (the outer deadline) are carried into the thread."""
    import contextvars
    import threading

    _ABANDONED[:] = [w for w in _ABANDONED if w.is_alive()]
    if len(_ABANDONED) >= MAX_ABANDONED:
        raise StructuredError(f"{len(_ABANDONED)} earlier calls are still running after their budgets ran out; not starting "
                              "another until some finish", "budget")
    ctx, box = contextvars.copy_context(), {}

    def work():
        try:
            box["value"] = ctx.run(fn)
        except BaseException as e:           # handed to the caller, which classifies it
            box["error"] = e

    worker = threading.Thread(target=work, name="llm-call", daemon=True)
    worker.start()
    worker.join(max(seconds, 0.0))
    if worker.is_alive():
        _ABANDONED.append(worker)
        raise TimeoutError(f"the call outlasted the remaining {max(seconds, 0):.1f}s of its budget")
    if "error" in box:
        raise box["error"]
    return box["value"]


# an outer deadline (monotonic seconds) set by a caller that makes several calls: they all stop at the same moment
DEADLINE: ContextVar[float | None] = ContextVar("llm_deadline", default=None)


class deadline:
    """`with llm.deadline(300):` every structured call inside (however nested) stops starting new attempts after 300 s in total."""

    def __init__(self, seconds: float):
        self.seconds = seconds

    def __enter__(self):
        self._tok = DEADLINE.set(min(time.monotonic() + self.seconds, DEADLINE.get() or float("inf")))
        return self

    def __exit__(self, *exc):
        DEADLINE.reset(self._tok)


def structured_invoke(model: ChatOpenAI, schema: type[BaseModel], messages: list, first: str = "json_schema",
                      log=None, deadline_s: float | None = None) -> BaseModel:
    """Call `model` for a `schema` instance, robust to models that lack a structured-output method or answer empty.

    Each failure is classified (`classify`) and handled by its kind, within ONE budget (a wall-clock deadline, a maximum
    number of calls, a limited number of waits on rate limits):
    - config (bad key, unknown model, no quota): raise `LLMConfigError` at once - no retry, no other method;
    - rate_limit / transient: wait (Retry-After, else exponential with jitter) and retry the same method, a few times;
    - unsupported (the server rejects this output method) or length (it ran to the token cap): the next method;
    - content (the answer did not satisfy the schema): ask again WITH the reason, once, then the next method;
    - timeout: one other method, then give up (the model is just slow).
    Raises `StructuredError` (with `kind`) when nothing worked or the budget ran out."""
    import json

    from langchain_core.messages import HumanMessage

    s = get_settings()
    started = time.monotonic()
    own = started + (deadline_s if deadline_s is not None else s.llm_call_deadline_s)
    stop_at = min(own, DEADLINE.get() or float("inf"))
    info = {"provider": "voyager", "model": model.model_name, "method": None, "calls": 0, "waited_s": 0.0, "errors": [], "ok": False}
    _calls.last = info
    errors, timeouts, waits = [], 0, 0

    def fail(kind: str, why: str, cls=StructuredError):
        info["seconds"] = round(time.monotonic() - started, 1)
        return cls("; ".join([*errors, why])[-600:], kind)

    order = (first, *[m for m in _METHODS if m != first])
    bad = _LOOPS.get(model.model_name, set())
    for method in [m for m in order if m not in bad] + [m for m in order if m in bad]:
        feedback, content_tries = "", 0
        while content_tries < 2:
            if time.monotonic() >= stop_at:
                raise fail("deadline", "the time budget for this call ran out")
            if info["calls"] >= s.llm_max_calls:
                raise fail("budget", f"the limit of {s.llm_max_calls} calls was reached")
            info["calls"] += 1
            msgs = messages + ([HumanMessage("Your previous answer was refused: " + feedback + ". Answer again, correcting exactly "
                                             "this.")] if feedback else [])
            try:
                if method == "json_mode":
                    # as a user turn: some servers reject a system message that isn't the first one
                    hint = HumanMessage("Answer with ONE JSON object only (no prose, no code fence) that matches "
                                        "this JSON schema:\n" + json.dumps(schema.model_json_schema()))
                    out = _parse_json(_bounded(lambda: model.invoke([*msgs, hint]), stop_at - time.monotonic()).content, schema)
                else:
                    bound = model.with_structured_output(schema, method=method)
                    out = _bounded(lambda: bound.invoke(msgs), stop_at - time.monotonic())
                if out is not None:
                    info.update(method=method, ok=True, seconds=round(time.monotonic() - started, 1))
                    return out
                errors.append(f"{method}: empty answer")
                info["errors"].append("empty")
                content_tries += 1
                continue
            except StructuredError:
                raise                                          # already a final answer (e.g. too many abandoned calls)
            except Exception as e:
                kind = classify(e)
                from openai import APIConnectionError
                if kind=='timeout' or isinstance(e,APIConnectionError):
                    info['connectivity_check_suggested']=True
                    if log:
                        detail='request timed out' if kind=='timeout' else 'connection failed'
                        log(f'[llm] Voyager {detail}; check the VPN connection and provider availability.')
                info["errors"].append(kind)
                errors.append(f"{method}: {kind}: {str(e)[:100]}")
                if kind == "config":
                    raise fail("config", f"{type(e).__name__}: {str(e)[:160]} (fix the key, model name or quota; retrying cannot help)",
                               LLMConfigError) from e
                if kind in ("rate_limit", "transient"):
                    waits += 1
                    wait = _retry_after(e, waits)
                    if waits > s.llm_rate_retries or time.monotonic() + wait >= stop_at:
                        raise fail(kind, f"still unavailable after {waits - 1} wait(s)") from e
                    if log:
                        log(f"[llm] {model.model_name}: {kind.replace('_', ' ')}; waiting {wait:.0f}s ({waits}/{s.llm_rate_retries})")
                    _sleep(wait)
                    info["waited_s"] += wait
                    continue                              # the SAME method again: the service was busy, the method is fine
                if time.monotonic() >= stop_at:                # out of time: no more methods or waits, whatever the failure was
                    raise fail("deadline", "the time budget for this call ran out during an attempt") from e
                if kind == "timeout":
                    # A hang in one mode (e.g. a model looping in json_schema) gets one other method; two hangs = slow.
                    timeouts += 1
                    _LOOPS.setdefault(model.model_name, set()).add(method)
                    if timeouts >= 2:
                        raise fail("timeout", f"timed out twice ({type(e).__name__})") from e
                    break
                if kind in ("unsupported", "length"):
                    if kind == "length":
                        _LOOPS.setdefault(model.model_name, set()).add(method)
                    break
                feedback = _feedback(e)                   # content / unknown: say why, ask again, once
                content_tries += 1
        if log and errors:
            log(f"[llm] {model.model_name}: {method} failed ({errors[-1][:100]}); trying the next method")
    raise fail("content" if any(k in ("content", "empty", "unknown") for k in info["errors"]) else "unsupported",
               "no output method produced a valid answer")


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
