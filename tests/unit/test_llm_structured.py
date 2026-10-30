"""llm.structured_invoke: falls back across structured-output methods and parses plain JSON answers."""

import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from jobagent import llm


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


def test_timeout_is_not_retried():
    m = FakeModel({"json_schema": [openai.APITimeoutError(request=None)], "function_calling": [], "json_mode": []})
    with pytest.raises(llm.StructuredError, match="timeout"):
        llm.structured_invoke(m, Out, [HumanMessage("x")])
    assert m.calls == ["json_schema"]
