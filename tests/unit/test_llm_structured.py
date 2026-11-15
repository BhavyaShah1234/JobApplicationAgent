"""llm.structured_invoke: falls back across structured-output methods and parses plain JSON answers."""

import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from jobagent import llm


@pytest.fixture(autouse=True)
def fresh_memory():
    llm._LOOPS.clear()  # which methods hung or looped is remembered per model name across calls


class Out(BaseModel):
    name: str


class FakeModel:
    """Scripted answers per method: a value, None (empty answer) or an exception to raise."""
    model_name = "fake"

    def __init__(self, script):
        self.script, self.calls = script, []

    def with_structured_output(self, schema, method):
        model = self

        class Bound:
            def invoke(self, messages):
                model.calls.append(method)
                ans = model.script[method].pop(0)
                if isinstance(ans, Exception):
                    raise ans
                return ans
        return Bound()

    def invoke(self, messages):
        self.calls.append("json_mode")
        return AIMessage(self.script["json_mode"].pop(0))


def bad_request():
    return openai.BadRequestError("unsupported", response=type("R", (), {"status_code": 400, "headers": {},
                                  "request": None})(), body=None)


def test_parse_json_strips_thinking_and_fences():
    assert llm._parse_json('<think>hmm {"x"}</think>```json\n{"name": "a"}\n```', Out).name == "a"


def test_empty_answer_retried_then_next_method():
    m = FakeModel({"json_schema": [None, None], "function_calling": [Out(name="ok")], "json_mode": []})
    assert llm.structured_invoke(m, Out, [HumanMessage("x")]).name == "ok"
    assert m.calls == ["json_schema", "json_schema", "function_calling"]


def test_unsupported_method_falls_through_to_plain_json():
    m = FakeModel({"json_schema": [bad_request()], "function_calling": [bad_request()],
                   "json_mode": ['Sure! {"name": "plain"}']})
    assert llm.structured_invoke(m, Out, [HumanMessage("x")]).name == "plain"


def test_a_timeout_gets_one_other_method_then_gives_up():
    t = lambda: openai.APITimeoutError(request=None)
    m = FakeModel({"json_schema": [t()], "function_calling": [Out(name="ok")], "json_mode": []})
    assert llm.structured_invoke(m, Out, [HumanMessage("x")]).name == "ok"
    m = FakeModel({"json_schema": [t()], "function_calling": [t()], "json_mode": ['{"name": "x"}']})
    m.model_name = "slow"  # a different model: nothing remembered about it yet
    with pytest.raises(llm.StructuredError, match="twice"):
        llm.structured_invoke(m, Out, [HumanMessage("x")])
    assert m.calls == ["json_schema", "function_calling"]


def test_a_method_that_ran_out_of_tokens_is_tried_last_next_time():
    class LengthFinishReasonError(Exception):
        pass
    m = FakeModel({"json_schema": [LengthFinishReasonError("cap")], "function_calling": [Out(name="a"), Out(name="b")],
                   "json_mode": []})
    m.model_name = "looper"
    assert llm.structured_invoke(m, Out, [HumanMessage("x")]).name == "a"
    assert llm.structured_invoke(m, Out, [HumanMessage("x")]).name == "b"
    assert m.calls == ["json_schema", "function_calling", "function_calling"]


def test_a_method_that_hung_is_tried_last_next_time():
    t = lambda: openai.APITimeoutError(request=None)
    m = FakeModel({"json_schema": [t()], "function_calling": [Out(name="a"), Out(name="b")], "json_mode": []})
    m.model_name = "hanger"
    assert llm.structured_invoke(m, Out, [HumanMessage("x")]).name == "a"
    assert llm.structured_invoke(m, Out, [HumanMessage("x")]).name == "b"
    assert m.calls == ["json_schema", "function_calling", "function_calling"]


# --- F19: classified failures, one budget, accurate metadata ------------------------------------------------------------

import httpx  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402
from pydantic import Field, ValidationError  # noqa: E402


def api_error(cls, status, headers=None, code=None, body=None):
    req = httpx.Request("POST", "http://voyager.test/v1/chat")
    err = cls("failure", response=httpx.Response(status, request=req, headers=headers or {}), body=body)
    if code:
        err.code = code
    return err


@pytest.fixture
def napping(monkeypatch):
    """Record the waits instead of sleeping them."""
    waits = []
    monkeypatch.setattr(llm, "_sleep", waits.append)
    return waits


def test_an_authentication_failure_is_one_call_not_one_per_output_method():
    m = FakeModel({"json_schema": [api_error(openai.AuthenticationError, 401)], "function_calling": [Out(name="no")], "json_mode": []})
    with pytest.raises(llm.LLMConfigError) as e:
        llm.structured_invoke(m, Out, [HumanMessage("x")])
    assert e.value.kind == "config" and m.calls == ["json_schema"]
    assert llm.last_call()["errors"] == ["config"] and llm.last_call()["calls"] == 1


def test_an_unknown_model_and_an_exhausted_quota_also_fail_at_once():
    for err in (api_error(openai.NotFoundError, 404), api_error(openai.RateLimitError, 429, code="insufficient_quota")):
        m = FakeModel({"json_schema": [err], "function_calling": [Out(name="no")], "json_mode": []})
        with pytest.raises(llm.LLMConfigError):
            llm.structured_invoke(m, Out, [HumanMessage("x")])
        assert m.calls == ["json_schema"]


def test_a_rate_limit_is_waited_out_on_the_same_method_honouring_retry_after(napping):
    m = FakeModel({"json_schema": [api_error(openai.RateLimitError, 429, {"retry-after": "7"}), Out(name="ok")],
                   "function_calling": [], "json_mode": []})
    assert llm.structured_invoke(m, Out, [HumanMessage("x")]).name == "ok"
    assert m.calls == ["json_schema", "json_schema"] and napping == [7.0]
    assert llm.last_call()["waited_s"] == 7.0 and llm.last_call()["ok"] and llm.last_call()["method"] == "json_schema"


def test_waiting_on_a_busy_service_is_bounded(napping):
    err = lambda: api_error(openai.InternalServerError, 503)                   # noqa: E731
    m = FakeModel({"json_schema": [err() for _ in range(10)], "function_calling": [Out(name="no")], "json_mode": []})
    with pytest.raises(llm.StructuredError) as e:
        llm.structured_invoke(m, Out, [HumanMessage("x")])
    assert e.value.kind == "transient" and len(napping) == 3 and m.calls == ["json_schema"] * 4     # 3 waits, then it stops
    assert all(w <= 30 for w in napping)                                       # the backoff is capped


def test_an_unsupported_output_mode_still_falls_through_to_the_next(napping):
    m = FakeModel({"json_schema": [bad_request()], "function_calling": [Out(name="ok")], "json_mode": []})
    assert llm.structured_invoke(m, Out, [HumanMessage("x")]).name == "ok" and napping == []


def test_an_invalid_answer_is_repaired_with_the_actual_reason():
    class Budgeted(BaseModel):
        text: str = Field(max_length=10)
    try:
        Budgeted(text="x" * 30)
    except ValidationError as ve:
        refusal = ve

    seen = []

    class Rec(FakeModel):
        def with_structured_output(self, schema, method):
            outer = super().with_structured_output(schema, method)

            class Bound:
                def invoke(self, messages):
                    seen.append([getattr(x, "content", "") for x in messages])
                    return outer.invoke(messages)
            return Bound()
    m = Rec({"json_schema": [refusal, Budgeted(text="short")], "function_calling": [], "json_mode": []})
    assert llm.structured_invoke(m, Budgeted, [HumanMessage("write")]).text == "short"
    assert m.calls == ["json_schema", "json_schema"]
    assert len(seen[0]) == 1 and "text" in seen[1][-1] and "at most 10 characters" in seen[1][-1]    # the second try was told why


def test_the_call_budget_and_the_deadline_stop_a_call_that_keeps_failing(monkeypatch):
    m = FakeModel({k: [None] * 20 for k in ("json_schema", "function_calling", "json_mode")})
    with pytest.raises(llm.StructuredError) as e:
        llm.structured_invoke(m, Out, [HumanMessage("x")])
    assert e.value.kind == "content" and len(m.calls) == 6                    # three methods, asked twice each, then it ends
    small = llm.get_settings().model_copy(update={"llm_max_calls": 4})
    monkeypatch.setattr(llm, "get_settings", lambda: small)
    m = FakeModel({k: [None] * 20 for k in ("json_schema", "function_calling", "json_mode")})
    with pytest.raises(llm.StructuredError) as e:
        llm.structured_invoke(m, Out, [HumanMessage("x")])
    assert e.value.kind == "budget" and len(m.calls) == 4
    m = FakeModel({k: [Out(name="never")] for k in ("json_schema", "function_calling", "json_mode")})
    with pytest.raises(llm.StructuredError) as e:
        llm.structured_invoke(m, Out, [HumanMessage("x")], deadline_s=0)
    assert e.value.kind == "deadline" and m.calls == []
    with llm.deadline(0):                                                    # an outer caller's deadline binds nested calls too
        with pytest.raises(llm.StructuredError, match="time budget"):
            llm.structured_invoke(FakeModel({"json_schema": [Out(name="x")]}), Out, [HumanMessage("x")])
    assert llm.structured_invoke(FakeModel({"json_schema": [Out(name="fine")]}), Out, [HumanMessage("x")]).name == "fine"   # and it ended


def test_only_an_unavailable_voyager_falls_back_to_ollama(monkeypatch):
    def primary(role, s, *a, **k):
        def go(x):
            raise outcome[0]
        return RunnableLambda(go)
    outcome = [api_error(openai.AuthenticationError, 401)]
    monkeypatch.setattr(llm, "_primary", primary)
    monkeypatch.setattr(llm, "_fallback", lambda *a, **k: RunnableLambda(lambda x: "ollama answered"))
    model = llm.chat("planner")
    with pytest.raises(openai.AuthenticationError):                           # a wrong key is not an outage: no silent small model
        model.invoke("hi")
    for err in (api_error(openai.RateLimitError, 429), api_error(openai.InternalServerError, 503),
                openai.APIConnectionError(request=httpx.Request("POST", "http://x"))):
        outcome[0] = err
        assert model.invoke("hi") == "ollama answered"


def test_the_writers_are_voyager_only_and_say_so():
    assert "Voyager only" in llm.writer.__doc__ and llm.writer(10).max_retries == 0
    m = FakeModel({"json_schema": [Out(name="ok")]})
    llm.structured_invoke(m, Out, [HumanMessage("x")])
    info = llm.last_call()
    assert info["provider"] == "voyager" and info["model"] == "fake" and info["calls"] == 1 and info["ok"]


def test_a_slow_call_cannot_outlast_the_total_budget_and_its_late_answer_is_discarded():
    import time as _t

    class Slow(FakeModel):
        def with_structured_output(self, schema, method):
            class Bound:
                def invoke(self, messages):
                    _t.sleep(0.4)
                    return Out(name="late")
            return Bound()
    started = _t.monotonic()
    with pytest.raises(llm.StructuredError) as e:
        llm.structured_invoke(Slow({}), Out, [HumanMessage("x")], deadline_s=0.05)
    assert e.value.kind == "deadline" and _t.monotonic() - started < 0.3               # returned at the budget, not after the call
    started = _t.monotonic()
    with llm.deadline(0.05):                                                            # an outer budget bounds it too
        with pytest.raises(llm.StructuredError):
            llm.structured_invoke(Slow({}), Out, [HumanMessage("x")])
    assert _t.monotonic() - started < 0.3


def test_a_call_that_finishes_inside_the_budget_is_unaffected():
    m = FakeModel({"json_schema": [Out(name="ok")]})
    assert llm.structured_invoke(m, Out, [HumanMessage("x")], deadline_s=5).name == "ok"


def test_an_abandoned_call_does_not_delay_the_process_exit():
    import subprocess
    import sys
    script = """from jobagent import llm
import atexit, time
start = time.monotonic()
atexit.register(lambda: print(round(time.monotonic() - start, 3), flush=True))
try:
    llm._bounded(lambda: time.sleep(.8), .01)
except TimeoutError:
    pass
"""
    p = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0 and float(p.stdout.strip()) < 0.3          # exit does not wait for the abandoned provider call


def test_repeated_timeouts_cannot_pile_up_abandoned_workers():
    import time as _t
    llm._ABANDONED.clear()
    for _ in range(llm.MAX_ABANDONED):
        with pytest.raises(TimeoutError):
            llm._bounded(lambda: _t.sleep(0.5), 0.01)
    started = _t.monotonic()
    with pytest.raises(llm.StructuredError) as e:
        llm._bounded(lambda: "never started", 5)
    assert e.value.kind == "budget" and _t.monotonic() - started < 0.2
    _t.sleep(0.7)                                                       # the abandoned calls finish; capacity returns
    assert llm._bounded(lambda: "ok", 5) == "ok"


def test_connection_failure_suggests_checking_vpn_without_changing_retry_policy(napping):
    error=openai.APIConnectionError(request=httpx.Request('POST','https://voyager.test/v1/chat/completions'))
    model=FakeModel({'json_schema':[error,Out(name='ok')],'function_calling':[],'json_mode':[]})
    logs=[]
    assert llm.structured_invoke(model,Out,[HumanMessage('x')],log=logs.append).name=='ok'
    assert model.calls==['json_schema','json_schema']
    assert any('VPN' in message and 'connection failed' in message for message in logs)
    assert llm.last_call()['connectivity_check_suggested']


def test_output_protocol_error_does_not_claim_a_connectivity_problem():
    model=FakeModel({'json_schema':[bad_request()],'function_calling':[Out(name='ok')],'json_mode':[]})
    logs=[]
    assert llm.structured_invoke(model,Out,[HumanMessage('x')],log=logs.append).name=='ok'
    assert not any('VPN' in message for message in logs)
