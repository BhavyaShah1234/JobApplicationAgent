"""Durable stage results for one job's workflow (F15): research -> resume -> cover letter -> documents -> apply -> submit.

Each stage's outcome is recorded in `<job>/workflow.json` under a workflow id: when it started and finished, a fingerprint of
its INPUTS, and the hash of every OUTPUT file it produced. On the next run a finished stage is reused (not run again) only if
its inputs are unchanged and its outputs are still on disk, byte for byte; a changed input or a missing or edited output reruns
that stage and every stage after it (their inputs include the earlier stages' outputs).

**What resumes, and what does not.** Resumption is at STAGE level. A stage that was running when the process ended is marked
`interrupted` and restarts from its beginning: the tailoring graph's state holds live objects (callbacks, open models), so its
rounds are not checkpointed, and a half-finished resume is rebuilt, not continued. What survives is every stage that finished.

**External side effects are never blindly replayed.** A stage that changes something outside the job folder (sending an
application) is declared `side_effect=True` and given a `reconcile` function that looks at the outside world (for submit: the
submission attempts and the portal/mail evidence). An interrupted side-effect stage is reconciled first: `done` marks it done,
`not_done` lets it run, anything else stops with `NeedsReconciliation` for the candidate to settle. Browser filling is not a
side effect in this sense: it is re-done by reopening the page and reading it again (it never sends)."""

import contextlib
import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

FILE = "workflow.json"


class NeedsReconciliation(RuntimeError):
    """A stage with an outside effect was interrupted and the outside world does not say whether it happened."""


def sha_file(path: Path) -> str | None:
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def fingerprint(inputs: dict) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Workflow:
    def __init__(self, job_dir: Path | str):
        self.dir = Path(job_dir)
        self.path = self.dir / FILE
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {"id": uuid.uuid4().hex[:12], "created": _now(), "stages": {}}
        for st in self.data["stages"].values():          # whoever was running is not running now (callers hold the job lock)
            if st.get("status") == "running":
                st["status"] = "interrupted"
                st["note"] = "the process ended while this stage was running"

    @property
    def id(self) -> str:
        return self.data["id"]

    def stage(self, name: str) -> dict | None:
        return self.data["stages"].get(name)

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".workflow-")
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=1, sort_keys=True, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def forget_from(self, name: str, order: list[str]) -> None:
        """Drop `name` and every later stage of `order` (their inputs depended on it)."""
        drop = False
        for n in order:
            drop = drop or n == name
            if drop:
                self.data["stages"].pop(n, None)

    def reusable(self, name: str, inputs: dict, outputs: list[str]) -> bool:
        st = self.stage(name)
        if not st or st.get("status") != "done" or st.get("inputs_sha") != fingerprint(inputs):
            return False
        return all(sha_file(self.dir / o) is not None and sha_file(self.dir / o) == st.get("outputs", {}).get(o) for o in outputs)

    def run(self, name: str, fn: Callable[[], dict | None], *, inputs: dict, outputs: list[str] = (), force: bool = False,
            side_effect: bool = False, reconcile: Callable[[], str] | None = None, log: Callable[[str], None] = print,
            check: Callable[[dict | None], str | None] | None = None):
        """Run stage `name` unless a finished, still-valid result exists. Returns (result, "ran" | "reused" | "reconciled").
        `fn` returns a small JSON-able result that is stored and handed back when the stage is reused. `check(result)` returns a
        reason when the stage ran but did not succeed (e.g. no valid resume was produced): the result is returned to the caller
        to handle, the stage is recorded `failed`, and it is never reused."""
        st = self.stage(name)
        if not force and self.reusable(name, inputs, list(outputs)):
            log(f"workflow {self.id}: {name}: reused (finished {st['finished_at']}, inputs and outputs unchanged)")
            return st.get("result"), "reused"
        if st and st.get("status") in ("interrupted", "running") and side_effect:
            verdict = reconcile() if reconcile else "unknown"
            if verdict == "done":
                st.update(status="done", note="reconciled: the outside effect had happened", finished_at=_now())
                self.save()
                return st.get("result"), "reconciled"
            if verdict != "not_done":
                raise NeedsReconciliation(f"{name} was interrupted and it is not known whether its effect happened "
                                          f"({verdict or 'no way to check'}); settle it, then run again")
        elif st and st.get("status") == "interrupted":
            log(f"workflow {self.id}: {name}: was interrupted; starting it again from the beginning")
        self.data["stages"][name] = {"status": "running", "started_at": _now(), "inputs_sha": fingerprint(inputs), "inputs": inputs,
                                     "side_effect": side_effect}
        self.save()
        try:
            result = fn()
        except BaseException as e:
            self.data["stages"][name].update(status="failed" if isinstance(e, Exception) else "interrupted", error=f"{type(e).__name__}: {str(e)[:300]}",
                                             finished_at=_now())
            with contextlib.suppress(Exception):
                self.save()
            raise
        if check and (problem := check(result)):
            self.data["stages"][name].update(status="failed", error=problem, finished_at=_now(), result=result)
            self.save()
            return result, "ran"
        missing = [o for o in outputs if not (self.dir / o).exists()]
        if missing:       # the stage returned, but not with its files: it is recorded, and never reused
            self.data["stages"][name].update(status="unverified", note=f"finished without producing {', '.join(missing)}",
                                             finished_at=_now(), result=result)
            self.save()
            return result, "ran"
        self.data["stages"][name].update(status="done", finished_at=_now(), result=result,
                                         outputs={o: sha_file(self.dir / o) for o in outputs})
        self.save()
        return result, "ran"

    def mark(self, name: str, status: str, **fields) -> None:
        """Record a stage that is run outside `run()` (the browser stages): running -> done / failed, with a small result."""
        st = self.data["stages"].setdefault(name, {})
        st.update(status=status, **fields)
        st["started_at" if status == "running" else "finished_at"] = _now()
        self.save()

    def summary(self) -> list[dict]:
        return [{"stage": n, "status": s.get("status"), "finished_at": s.get("finished_at"), "error": s.get("error"), "note": s.get("note")}
                for n, s in self.data["stages"].items()]
