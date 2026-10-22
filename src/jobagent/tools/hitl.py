"""Human-in-the-loop requests through a file queue in state/hitl/.

The agent writes a request and waits; the user answers from any terminal with
`jobagent hitl answer` (or `jobagent hitl done` after finishing something in noVNC).
Secret answers go straight into the vault; only a marker is written to the answer file.
"""

import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SECRET_MARKER = "<stored in vault>"


class HitlTimeout(TimeoutError):
    pass


@dataclass
class Request:
    id: str
    prompt: str
    secret: bool = False
    default: str | None = None
    # For secrets: where `jobagent hitl answer` stores the value, e.g. ["gmail", "app_password"].
    vault_target: list[str] | None = None
    created: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    # Process waiting for the answer; requests of dead processes (killed runs) are pruned.
    pid: int = field(default_factory=os.getpid)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Hitl:
    def __init__(self, root: Path, notify: Callable[[str], None] = print):
        self.pending, self.answers = root / "pending", root / "answers"
        self.pending.mkdir(parents=True, exist_ok=True)
        self.answers.mkdir(parents=True, exist_ok=True)
        self.notify = notify

    # --- agent side -------------------------------------------------------------
    def ask(
        self,
        prompt: str,
        *,
        secret: bool = False,
        default: str | None = None,
        vault_target: list[str] | None = None,
        done_when: Callable[[], bool] | None = None,
        timeout_s: float | None = None,
        poll_s: float = 2.0,
    ) -> str:
        """Block until the user answers (or `done_when()` turns true). Returns the answer ("" if auto-resolved)."""
        req = Request(id=uuid.uuid4().hex[:8], prompt=prompt, secret=secret, default=default, vault_target=vault_target)
        _write_atomic(self.pending / f"{req.id}.json", json.dumps(asdict(req)))
        hint = "jobagent hitl answer " + req.id + ("" if secret else " '<answer>'")
        self.notify(f"[HITL {req.id}] {prompt}" + (f" (default: {default})" if default else "") + f"\n  → answer with: {hint}")
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        try:
            while True:
                answer_file = self.answers / f"{req.id}.json"
                if answer_file.exists():
                    value = json.loads(answer_file.read_text())["value"]
                    answer_file.unlink()
                    return default if value == "" and default is not None else value
                if done_when is not None and done_when():
                    return ""
                if deadline is not None and time.monotonic() > deadline:
                    raise HitlTimeout(f"no answer to HITL {req.id} within {timeout_s}s: {prompt}")
                time.sleep(poll_s)
        finally:
            (self.pending / f"{req.id}.json").unlink(missing_ok=True)

    # --- user side --------------------------------------------------------------
    def list_pending(self) -> list[Request]:
        reqs = []
        for p in self.pending.glob("*.json"):
            try:
                req = Request(**json.loads(p.read_text()))
            except (json.JSONDecodeError, TypeError, FileNotFoundError):
                continue  # being written or removed right now; seen on the next poll
            if _alive(req.pid):
                reqs.append(req)
            else:  # the asking run was killed before it could clean up
                p.unlink(missing_ok=True)
        return sorted(reqs, key=lambda r: r.created)

    def answer(self, req_id: str | None, value: str) -> Request:
        reqs = self.list_pending()
        if not reqs:
            raise LookupError("no pending HITL requests")
        req = next((r for r in reqs if r.id == req_id), None) if req_id else reqs[0]
        if req is None:
            raise LookupError(f"no pending HITL request {req_id!r}")
        if req.secret and req.vault_target:
            from jobagent.tools.vault import open_vault

            name, fld = req.vault_target
            open_vault().set(name, **{fld: value})
            value = SECRET_MARKER
        _write_atomic(self.answers / f"{req.id}.json", json.dumps({"value": value}))
        return req


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file + rename so a concurrent reader never sees a half-written request/answer."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def open_hitl(notify: Callable[[str], None] = print) -> Hitl:
    from jobagent.config import get_settings

    return Hitl(get_settings().state_dir / "hitl", notify)
