"""Publish a resume version as ONE coherent bundle, and lock a job against concurrent builds (F25).

A resume version is more than its PDF: `resume.tex`, `resume.pdf`, `projects.json` (which projects fit, which are shortened) and
`filled.json` (the structure the checkers and the claims review read) describe the same document. Writing them one by one lets a
crash, a rollback or a second process leave a MIXED version: the PDF of one attempt next to the layout metadata of another.

`publish` stages every file beside its destination, records the whole intended version in a journal, then moves the files into
place. A crash before the journal exists changes nothing; a crash after it is completed by `recover` (roll forward), so the
folder always holds one complete version once recovered. A file that the version does not have (no `projects.json`) is removed,
so a rollback to a version without projects does not keep a later attempt's file. `job_lock` stops two builds of one job from
running at once."""

import contextlib
import fcntl
import json
import os
from pathlib import Path

JOURNAL = ".publish.json"
BUNDLE_FILES = ("resume.tex", "resume.pdf", "resume.layout.json", "projects.json", "filled.json")


class JobBusy(RuntimeError):
    """Another process is already building this job's documents."""


def _write_synced(path: Path, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _apply(job_dir: Path) -> None:
    journal = job_dir / JOURNAL
    try:
        plan = json.loads(journal.read_text())
    except (OSError, ValueError):
        journal.unlink(missing_ok=True)          # a torn journal means nothing was moved yet: the old version stands
        return
    for name, tmp in plan.get("stage", {}).items():
        if (job_dir / tmp).exists():
            os.replace(job_dir / tmp, job_dir / name)
    for name in plan.get("remove", []):
        (job_dir / name).unlink(missing_ok=True)
    journal.unlink(missing_ok=True)


def recover(job_dir: Path | str) -> bool:
    """Finish an interrupted publish and clear staged leftovers. True if a publish was completed."""
    job_dir = Path(job_dir)
    done = (job_dir / JOURNAL).exists()
    if done:
        _apply(job_dir)
    for leftover in job_dir.glob(".*.new"):      # staged files of a publish that never reached its journal
        leftover.unlink(missing_ok=True)
    return done


def publish(job_dir: Path | str, files: dict[str, bytes | str | None]) -> None:
    """Make `files` (name -> content; None removes the file) the folder's version, all or nothing. See the module doc."""
    job_dir = Path(job_dir)
    recover(job_dir)
    stage, remove = {}, []
    for name, data in files.items():
        if data is None:
            remove.append(name)
            continue
        tmp = f".{name}.new"
        _write_synced(job_dir / tmp, data.encode() if isinstance(data, str) else data)
        stage[name] = tmp
    tmp_journal = job_dir / f"{JOURNAL}.tmp"
    _write_synced(tmp_journal, json.dumps({"stage": stage, "remove": remove}).encode())
    os.replace(tmp_journal, job_dir / JOURNAL)    # from here on the new version is the one that will be completed
    _apply(job_dir)


@contextlib.contextmanager
def job_lock(job_dir: Path | str, what: str = "build"):
    """Exclusive, non-blocking lock on a job folder for the duration of a build. The OS drops it if the process dies."""
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    with open(job_dir / ".build.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise JobBusy(f"another process is already running a {what} for {job_dir.name}; wait for it or stop it first") from e
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
