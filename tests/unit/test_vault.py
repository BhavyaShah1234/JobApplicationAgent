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


def test_truncated_vault_is_reported_and_restorable(tmp_path, key):
    path = tmp_path / "vault.enc"
    v = Vault(path, key)
    v.set("gmail", email="a@gmail.com")
    v.set("gmail", status="ready")  # second save → backup holds the first good state
    path.write_bytes(b"")  # simulate an outside truncation
    with pytest.raises(VaultError, match="empty.*vault restore"):
        v.get("gmail")
    assert v.restore_backup() == ["gmail"]
    assert v.get("gmail") == {"email": "a@gmail.com"}
    assert (tmp_path / "vault.enc.unreadable").exists()


def test_backup_is_never_overwritten_by_an_unreadable_vault(tmp_path, key):
    path = tmp_path / "vault.enc"
    v = Vault(path, key)
    v.set("a", x=1)
    v.set("a", x=2)
    good_backup = v.backup_path.read_bytes()
    path.write_bytes(b"garbage")
    with pytest.raises(VaultError):
        v.set("a", x=3)  # load fails first, so nothing is written
    assert v.backup_path.read_bytes() == good_backup


def test_delete_and_names(tmp_path, key):
    v = Vault(tmp_path / "vault.enc", key)
    v.set("b", x=1)
    v.set("a", x=2)
    assert v.names() == ["a", "b"]
    assert v.delete("a") and not v.delete("a")
    assert v.names() == ["b"]
