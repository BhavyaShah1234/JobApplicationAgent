"""Reported usage must not be fabricated or counted twice."""
from types import SimpleNamespace
from jobagent.tools.model_metrics import ModelAudit


def response(output,usage=None):
    return SimpleNamespace(llm_output=output,generations=[[SimpleNamespace(message=SimpleNamespace(usage_metadata=usage))]])


def test_aggregate_and_message_metadata_are_not_double_counted():
    audit=ModelAudit();audit.on_chat_model_start()
    audit.on_llm_end(response({'token_usage':{'total_tokens':20}},{'total_tokens':20}))
    assert audit.starts==1 and audit.tokens==20


def test_unknown_usage_is_not_zero_and_message_usage_is_supported():
    audit=ModelAudit();audit.on_llm_end(response({}))
    assert audit.tokens is None
    audit.on_llm_end(response({}, {'total_tokens':12}))
    assert audit.tokens==12
