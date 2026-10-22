"""Graph wiring, idempotency bookkeeping and crash/resume with stub nodes (no network, no browser)."""

import pytest
from cryptography.fernet import Fernet

from jobagent.config import get_settings
from jobagent.graph import bootstrap


@pytest.fixture
def env(tmp_path, monkeypatch):
    for d in ("data", "state", "outputs"):
        (tmp_path / d).mkdir()
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("OUTPUTS_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("VAULT_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    calls: list[str] = []

    def stub(name):
        def node(_state):
            calls.append(name)
            return bootstrap._done(name, "ok")
        return node

    for name in ("gmail", "browser", "linkedin", "overleaf"):
        monkeypatch.setattr(bootstrap, name, stub(name))
    yield calls
    get_settings.cache_clear()


def test_first_run_creates_capabilities_file_then_is_idempotent(env):
    first = bootstrap.run(log=lambda _: None)
    assert first["ran_now"] == bootstrap.NODES and first["changed"] == ["created missing-agent-capabilities.txt"]
    second = bootstrap.run(log=lambda _: None)
    assert second["changed"] == [] and not second["resumed"]
    assert "harness: ok" in second["messages"][-1].content


def test_crash_resumes_from_the_unfinished_node(env, monkeypatch):
    bootstrap.run(log=lambda _: None)
    env.clear()
    monkeypatch.setenv(bootstrap.CRASH_ENV, "browser")
    with pytest.raises(bootstrap.BootstrapCrash):
        bootstrap.run(log=lambda _: None)
    assert env == ["gmail", "browser"]
    monkeypatch.delenv(bootstrap.CRASH_ENV)
    env.clear()
    resumed = bootstrap.run(log=lambda _: None)
    assert resumed["resumed"] and resumed["ran_now"] == ["browser", "linkedin", "overleaf"]
    assert env == ["browser", "linkedin", "overleaf"]  # harness and gmail were not repeated
    assert not bootstrap.run(log=lambda _: None)["resumed"]  # finished: next run starts fresh
