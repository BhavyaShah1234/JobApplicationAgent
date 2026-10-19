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

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self._fernet.decrypt(self.path.read_bytes()))
        except InvalidToken as e:
            raise VaultError(f"cannot decrypt {self.path}: wrong VAULT_KEY or corrupted file") from e

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = self._fernet.encrypt(json.dumps(data, sort_keys=True).encode())
        # Atomic replace so a crash never leaves a half-written vault.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".vault-")
        with os.fdopen(fd, "wb") as f:
            f.write(token)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

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
