import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner

from jobagent.config import get_settings
from jobagent.main import app
from jobagent.tools.vault import Vault


@pytest.fixture
def env(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VAULT_KEY", key)
    get_settings.cache_clear()
    yield tmp_path, key
    get_settings.cache_clear()


def test_import_plaintext_written_over_vault_file(env):
    state, key = env
    (state / "vault.enc").write_text("hunter2xy\n")  # user typed the password straight into vault.enc
    result = CliRunner().invoke(app, ["vault", "import-file", "gmail", "password", str(state / "vault.enc")])
    assert result.exit_code == 0, result.output
    assert "hunter2xy" not in result.output
    assert Vault(state / "vault.enc", key).get("gmail") == {"password": "hunter2xy"}
    assert not (state / "vault.enc.plaintext").exists()  # shredded


def test_import_from_separate_file_keeps_existing_entries(env):
    state, key = env
    Vault(state / "vault.enc", key).set("gmail", email="a@gmail.com")
    secret = state / "pw.txt"
    secret.write_text("s3cret")
    result = CliRunner().invoke(app, ["vault", "import-file", "gmail", "password", str(secret)])
    assert result.exit_code == 0, result.output
    assert Vault(state / "vault.enc", key).get("gmail") == {"email": "a@gmail.com", "password": "s3cret"}
    assert not secret.exists()
