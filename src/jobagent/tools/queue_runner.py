"""Take queued roles through the existing steps (`jobagent queue run`), safely when more than one runner exists (F13).

A job is CLAIMED atomically with this runner's identity and a lease that a heartbeat keeps alive while a step runs; only the
claim holder records the outcome; recovery of a killed runner's job waits for its lease (or its death) - see
`jobqueue.claim` / `renew` / `finish` / `requeue_stale`.

What a step achieved is read from its STRUCTURED RESULT, not guessed from the process exit code: `apply` writes
`application/application.json` (state, ready_to_submit, reason...); `scrape` records the posting's state on the `jobs` row.
The exit code is only the fallback when no fresh structured result exists, and the two are compared: when they disagree the
structured result wins and the note says so.
"""

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

from jobagent.tools import jobqueue as Q

# queue statuses written by the steps themselves when they find a posting unavailable; the runner never overwrites them
FINAL_BY_STEP = {"closed", "already_applied", "blocked", "needs_attention", "failed", "filled", "applied"}


def apply_outcome(code: int) -> tuple[str, str] | None:
    """The queue status and note for an `apply` exit code alone (the fallback). None for 3: the posting is unavailable and
    the stop already recorded its own state, which must not be overwritten."""
    if code == 0:
        return "filled", ""
    if code == 3:
        return None
    if code == 4:
        return "blocked", "apply: blocked (a bot check, the planner, or a page that keeps refusing Next); see application.md"
    if code == 5:
        return "needs_attention", "on the review screen but not ready to submit; see application.md"
    return "failed", f"apply exited {code}"


def fresh_result(job_dir: Path, since: float) -> dict | None:
    """The `application.json` the apply step just wrote (modified at or after `since`); None if absent, stale or unreadable."""
    p = job_dir / "application" / "application.json"
    try:
        if p.stat().st_mtime < since - 1:
            return None
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def outcome_of_apply(result: dict | None, code: int) -> tuple[str, str] | None:
    """(status, note) for the queue from apply's structured result and exit code; None when the posting is unavailable and
    the step recorded that itself."""
    if result is None:
        base = apply_outcome(code)
        return base if base is None else (base[0], (base[1] + " (no structured result was written; judged by the exit code)").strip())
    state = result.get("state")
    if state == "review":
        got = ("filled", "") if result.get("ready_to_submit") else (
            "needs_attention", "; ".join((result.get("blockers") or ["on the review screen but not ready to submit"])[:3]))
    elif state == "blocked" and result.get("blocked_by") == "planner":
        got = ("failed", f"the answer planner failed: {result.get('reason', '')}".strip())      # a model outage is a failure, not a bot check
    elif state == "blocked":
        got = ("blocked", f"{result.get('blocked_by', '')}: {result.get('reason', '')}".strip(": ") or "blocked")
    elif state in ("closed", "already_applied", "expired", "removed", "unavailable") or code == 3:
        return None
    else:
        got = ("failed", str(result.get("reason") or f"the form ended '{state}'"))
    by_code = apply_outcome(code)
    if by_code is not None and by_code[0] != got[0]:
        got = (got[0], f"{got[1]} (exit code {code} said {by_code[0]}; the structured result was used)".strip())
    return got


class Heartbeat:
    """Keeps a claim's lease alive while a long step runs, from a thread with its own connection."""

    def __init__(self, url: str, worker: str, lease_s: int, every_s: float, connect: Callable):
        self.url, self.worker, self.lease_s, self.every_s, self.connect = url, worker, lease_s, every_s, connect
        self.lost = False
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        conn = self.connect()
        try:
            while not self._stop.wait(self.every_s):
                if not Q.renew(conn, self.url, self.worker, self.lease_s):
                    self.lost = True
                    return
        finally:
            conn.close()

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=5)


def run_queue(conn, step: Callable[[list[str]], int] | None = None, *, through: str, limit: int, company: str | None, outputs_dir: Path,
              slug_of: Callable[[str], str], log: Callable[[str], None], worker: str | None = None, lease_s: int = Q.LEASE_S,
              connect: Callable | None = None, browser_mode: str = "docker", pipeline=None, companies: tuple[str, ...] | None = None,
              pipeline_options: dict | None = None, results: list | None = None, away_poll_s: float = 15.0,
              sleep: Callable[[float], None] = time.sleep) -> int:
    """Claim and run up to `limit` queued jobs; returns how many were taken through `through` WITHOUT failing. `companies` (slugs)
    restricts the claims to those companies; `pipeline_options` (force, no_ats, filling_strategy) reach `Pipeline.run`; when `results`
    is a list, one dict per job (url, company, state, code, note) is appended to it. `step(cmd)` runs one
    `jobagent` command and returns its exit code. `browser_mode` (docker | desktop | auto) is passed to `apply` (F14); the queue
    never submits, so there is no submit mode here.

    Candidate presence (dashboard toggle, tools/presence.py): a job that stopped on a question while the candidate was away is
    put back at the END of the queue (`Q.defer`), does not count against `limit`, and the runner goes on with the next job.
    Deferred jobs are not taken while the candidate is away; when only they are left, the runner waits for the candidate to
    come back (checking every `away_poll_s` seconds) and then resumes them, oldest deferral first."""
    from jobagent import db

    worker = worker or Q.worker_id()
    connect = connect or db.connect
    if step is None and pipeline is None:
        from jobagent.tools.pipeline import Pipeline
        pipeline = Pipeline(conn=conn, log=log)
    back = Q.requeue_stale(conn, lease_s=lease_s)
    if back:
        log(f"queue: {back} job(s) whose runner is gone put back in the queue")
    done = taken = 0
    from jobagent.config import get_settings
    from jobagent.tools import pacing
    s = get_settings()
    announced: set[str] = set()
    from datetime import UTC, datetime
    from jobagent.tools import presence
    said_waiting = False
    while taken < limit:
        paced = pacing.excluded(conn, s.pacing_max_per_company, s.pacing_window_days)
        for slug_, n in paced.items():
            if slug_ not in announced:
                announced.add(slug_)
                log(f"queue: pacing: {slug_} already has {n} application(s) in the last {s.pacing_window_days} days "
                    f"(limit {s.pacing_max_per_company}); its other jobs wait")
        here = presence.available(s.state_dir)
        row = Q.claim(conn, worker, company, lease_s, exclude=tuple(paced), companies=companies,
                      pace=(s.pacing_max_per_company, s.pacing_window_days), include_deferred=here)
        if row is None:
            if not here and Q.deferred_count(conn):
                if not said_waiting:
                    log(f"queue: {Q.deferred_count(conn)} job(s) are waiting for you; resuming them when the dashboard shows you available")
                    said_waiting = True
                sleep(away_poll_s)
                continue
            break
        said_waiting = False
        taken += 1
        url = row["url"]
        job_started = datetime.now(UTC).isoformat(timespec="seconds")

        def deferred_now(job_dir: str = "") -> dict | None:
            """The deferral recorded for this job since it was claimed (the candidate was away when it needed them), or None."""
            now = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
            dirs = [job_dir]
            if now is not None and now["job_id"]:
                dirs.append(str(outputs_dir / slug_of(now["company"] or row["company"]) / now["job_id"]))
            return presence.deferred_since({url}, dirs, job_started, s.state_dir)

        def requeue_for_candidate(why: dict) -> None:
            """Back to the end of the queue: it needs the candidate, who is away. Not counted against the run's limit."""
            nonlocal taken
            note = f"waiting for you (you were away): {why.get('question', '')[:160]}"
            if Q.defer(conn, url, worker, note):
                log(f"[yellow]queue: {row['company']} - {row['title']}: needs you and you are away; moved to the end of the queue[/]")
            else:
                log(f"[yellow]queue: {url} was taken over by another runner; it was not deferred[/]")
            taken -= 1
            if results is not None:
                results.append({"url": url, "company": row["company"], "state": "deferred", "code": 0, "note": note})
        log(f"queue: {row['company']} - {row['title']}")

        def fin(status: str, note: str = "") -> None:
            """Record this job's outcome if this worker still holds its claim (a job taken over by another runner keeps its new owner's outcome)."""
            if not Q.finish(conn, url, worker, status, note):
                log(f"[yellow]queue: {url} was taken over by another runner; this runner's outcome ({status}) was not recorded[/]")

        def release() -> None:                       # a step recorded its own final state: only drop the claim
            """Drop this worker's claim without changing the status (the step already recorded its own final state)."""
            conn.execute("UPDATE jobs SET claimed_by = NULL, lease_until = NULL WHERE url = ? AND claimed_by = ?", (url, worker))
            conn.commit()

        with Heartbeat(url, worker, lease_s, max(lease_s / 3, 1), connect):
            if pipeline is not None:
                opts = {k: v for k, v in (pipeline_options or {}).items() if k in ("force", "no_ats", "filling_strategy", "document_strategy", "ats_refine")}
                try:
                    result = pipeline.run(url, through=through, browser_mode=browser_mode, **opts)
                except Exception as exc:
                    why = deferred_now()
                    if why is not None:
                        requeue_for_candidate(why)
                        continue
                    fin("failed", f"pipeline: {type(exc).__name__}: {str(exc)[:200]}")
                    if results is not None:
                        results.append({"url": url, "company": row["company"], "state": "failed", "code": 1,
                                        "note": f"{type(exc).__name__}: {str(exc)[:120]}"})
                    continue
                unfinished = bool(result.code) or result.state in ("failed", "blocked", "needs_review", "needs_attention", "unavailable")
                why = deferred_now(getattr(result, "job_dir", "") or "") if unfinished else None
                if why is not None:
                    requeue_for_candidate(why)
                    continue
                fin(result.state, result.note)
                if results is not None:
                    results.append({"url": url, "company": row["company"], "state": result.state, "code": result.code or 0, "note": result.note or ""})
                if not (result.code or result.state in ("failed", "blocked", "needs_review", "unavailable")):
                    done += 1
                continue
            code = step(["scrape", url])
            now = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
            if now["status"] != "running" and now["status"] in FINAL_BY_STEP:      # scrape found it closed / blocked and said so
                log(f"[yellow]queue: {now['status']}: {now['notes']}[/]")
                release()
                continue
            if code == 3:
                log(f"[yellow]queue: unavailable: {now['notes']}[/]")
                release()
                continue
            if code != 0:
                why = deferred_now()
                if why is not None:
                    requeue_for_candidate(why)
                    continue
                fin("failed", "scrape failed")
                continue
            job_dir = outputs_dir / slug_of(now["company"] or row["company"]) / (now["job_id"] or "")
            rel = f"outputs/{job_dir.parent.name}/{job_dir.name}"
            co = Q.slug(now["company"] or row["company"])
            if through == "scrape":
                fin("scraped")
                done += 1
                continue
            if step(["research", now["company"] or row["company"]]) != 0 or step(["tailor", rel, co]) != 0:
                why = deferred_now(str(job_dir))
                if why is not None:
                    requeue_for_candidate(why)
                    continue
                fin("failed", "research or tailor failed")
                continue
            if through == "tailor":
                fin("tailored")
                done += 1
                continue
            started = time.time()
            code = step(["apply", rel, co, "--browser-mode", browser_mode])
            outcome = outcome_of_apply(fresh_result(job_dir, started), code)
        if outcome is not None and outcome[0] in ("blocked", "needs_attention", "failed"):
            why = deferred_now()
            if why is not None:
                requeue_for_candidate(why)
                continue
        if outcome is None:
            release()                                # the stop recorded closed / already applied itself
        else:
            fin(*outcome)
        done += 1
    log(f"queue: {done} job(s) taken through `{through}`")
    return done
