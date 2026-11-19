"""A secret that only improves a step (the GitHub token) must never stop it: tailoring ran into `VaultError: VAULT_KEY is empty` on a machine without a vault."""
import pytest

from jobagent.config import get_settings
from jobagent.tools import vault as V


def settings(monkeypatch, tmp_path, key=None):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    if key is None:
        monkeypatch.setenv("VAULT_KEY", "")
    else:
        monkeypatch.setenv("VAULT_KEY", key)
    get_settings.cache_clear()


def test_no_vault_key_gives_none_instead_of_an_error(monkeypatch, tmp_path):
    settings(monkeypatch, tmp_path)
    with pytest.raises(V.VaultError):
        V.open_vault().get("github")                       # the strict form still refuses
    assert V.optional_secret("github", "token") is None
    get_settings.cache_clear()


def test_a_stored_secret_is_returned_and_a_missing_one_is_none(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    settings(monkeypatch, tmp_path, Fernet.generate_key().decode())
    assert V.optional_secret("github", "token") is None    # empty vault
    V.open_vault().set("github", token="ghp_example")
    assert V.optional_secret("github", "token") == "ghp_example"
    assert V.optional_secret("github", "nope") is None and V.optional_secret("other", "token") is None
    get_settings.cache_clear()


def test_a_wrong_key_does_not_stop_a_step_either(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    settings(monkeypatch, tmp_path, Fernet.generate_key().decode())
    V.open_vault().set("github", token="ghp_example")
    monkeypatch.setenv("VAULT_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    assert V.optional_secret("github", "token") is None
    get_settings.cache_clear()


def test_tailoring_code_no_longer_calls_the_strict_vault_for_the_github_token():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "src" / "jobagent"
    for f in ("steps/tailor.py", "steps/multi_agent.py", "tools/repo_cards.py"):
        assert 'open_vault().get("github")' not in (root / f).read_text(), f
