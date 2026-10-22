"""Fernet-encrypted credential store, keyed by entry name (e.g. "gmail", a portal domain)."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class VaultError(RuntimeError):
    pass


class Vault:
    def __init__(self, path: Path, key: str):
        if not key:
            raise VaultError("VAULT_KEY is empty; run scripts/setup_env.sh to generate one")
        try:
            self._fernet = Fernet(key.encode())
        except ValueError as e:
            raise VaultError(f"VAULT_KEY is not a valid Fernet key: {e}") from e
        self.path = path

    @property
    def backup_path(self) -> Path:
        return self.path.with_name(self.path.name + ".bak")

    def _decrypt(self, path: Path) -> dict[str, dict[str, Any]]:
        raw = path.read_bytes()
        if not raw:
            raise VaultError(f"{path} is empty (0 bytes): it was truncated outside the agent")
        try:
            return json.loads(self._fernet.decrypt(raw))
        except InvalidToken as e:
            raise VaultError(f"cannot decrypt {path}: wrong VAULT_KEY or corrupted file") from e

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return self._decrypt(self.path)
        except VaultError as e:
            # Never silently start over empty: that would lose credentials. Point at the backup instead.
            hint = (f" A backup from the last good save exists: run `jobagent vault restore`."
                    if self.backup_path.exists() else " No backup exists.")
            raise VaultError(f"{e}.{hint} Never edit vault files directly; use `jobagent vault set`.") from e

    def _write_atomic(self, path: Path, data: bytes) -> None:
        # Write a temp file, fsync, then rename: a crash or full disk never leaves a half-written file.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".vault-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = self._fernet.encrypt(json.dumps(data, sort_keys=True).encode())
        if self.path.exists():
            # Keep the previous good version (only if it is readable) as the backup.
            try:
                self._decrypt(self.path)
                self._write_atomic(self.backup_path, self.path.read_bytes())
            except VaultError:
                pass
        self._write_atomic(self.path, token)

    def restore_backup(self) -> list[str]:
        """Replace an unreadable vault with the last good backup; the bad file is kept alongside."""
        if not self.backup_path.exists():
            raise VaultError("no vault backup to restore")
        names = sorted(self._decrypt(self.backup_path))
        if self.path.exists():
            os.replace(self.path, self.path.with_name(self.path.name + ".unreadable"))
        self._write_atomic(self.path, self.backup_path.read_bytes())
        return names

    def get(self, name: str) -> dict[str, Any] | None:
        return self._load().get(name)

    def set(self, name: str, **fields: Any) -> None:
        data = self._load()
        data[name] = {**data.get(name, {}), **fields}
        self._save(data)

    def delete(self, name: str) -> bool:
        data = self._load()
        if data.pop(name, None) is None:
            return False
        self._save(data)
        return True

    def names(self) -> list[str]:
        return sorted(self._load())


def open_vault() -> Vault:
    from jobagent.config import get_settings

    s = get_settings()
    return Vault(s.vault_path, s.vault_key.get_secret_value())
