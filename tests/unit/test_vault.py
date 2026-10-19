import stat

import pytest
from cryptography.fernet import Fernet

from jobagent.tools.vault import Vault, VaultError


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


def test_round_trip_and_merge(tmp_path, key):
    path = tmp_path / "vault.enc"
    v = Vault(path, key)
    v.set("gmail", email="agent@example.com", password="s3cret")
    v.set("gmail", app_password="abcd")
    assert Vault(path, key).get("gmail") == {"email": "agent@example.com", "password": "s3cret", "app_password": "abcd"}
    assert b"s3cret" not in path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_wrong_key_is_rejected(tmp_path, key):
    path = tmp_path / "vault.enc"
    Vault(path, key).set("x", a=1)
    with pytest.raises(VaultError, match="wrong VAULT_KEY"):
        Vault(path, Fernet.generate_key().decode()).get("x")


def test_empty_or_invalid_key(tmp_path):
    with pytest.raises(VaultError, match="empty"):
        Vault(tmp_path / "v", "")
    with pytest.raises(VaultError, match="not a valid"):
        Vault(tmp_path / "v", "nope")


def test_delete_and_names(tmp_path, key):
    v = Vault(tmp_path / "vault.enc", key)
    v.set("b", x=1)
    v.set("a", x=2)
    assert v.names() == ["a", "b"]
    assert v.delete("a") and not v.delete("a")
    assert v.names() == ["b"]
