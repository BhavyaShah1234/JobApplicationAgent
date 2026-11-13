"""F13: claims are atomic, leases protect live work, recovery is safe, and outcomes come from structured results."""
import json
import os
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobagent import db
from jobagent.tools import jobqueue as Q
from jobagent.tools import queue_runner as R
from jobagent.tools.locations import Decision
from jobagent.tools.matching import Match

US = Decision("us", "t", "Austin")


def seed(path, n=3, company="Acme"):
    conn = db.connect(path)
    for i in range(1, n + 1):
        Q.enqueue(conn, Q.Listing(company, f"Engineer {i}", f"https://e.test/jobs/{i}", ["Austin, TX"], req_id=str(i)),
                  Match("queued", "regex", "swe", 0.9 - i / 100), US)
        with conn:
            conn.execute("UPDATE jobs SET job_id = ?, company = ? WHERE req_id = ?", (str(i), company, str(i)))
    return conn


def status_of(conn, i):
    return conn.execute("SELECT status, notes, claimed_by FROM jobs WHERE req_id = ?", (str(i),)).fetchone()


# --- claiming ------------------------------------------------------------------------------------------------------------

def test_two_workers_never_claim_the_same_job(tmp_path):
    path = tmp_path / "jobs.sqlite"
    seed(path, n=12)
    got, lock = [], threading.Lock()

    def worker(name):
        conn = db.connect(path)                                   # its own connection, as a separate process would have
        while (row := Q.claim(conn, name)) is not None:
            with lock:
                got.append((row["url"], name))
    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(got) == 12 and len({u for u, _ in got}) == 12      # every job exactly once
    conn = db.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'running' AND claimed_by IS NOT NULL").fetchone()[0] == 12


def test_claim_takes_the_best_match_first_and_honours_the_company_filter(tmp_path):
    conn = seed(tmp_path / "jobs.sqlite", 3)
    Q.enqueue(conn, Q.Listing("Other", "Dev", "https://o.test/1", ["Austin, TX"], req_id="9"), Match("queued", "regex", "swe", 0.99), US)
    assert Q.claim(conn, "w", "Acme")["title"] == "Engineer 1"
    assert Q.claim(conn, "w")["company"] == "Other"
    assert Q.claim(conn, "w", "Nobody") is None


def test_only_the_owner_can_record_the_outcome_and_renewing_extends_the_lease(tmp_path):
    conn = seed(tmp_path / "jobs.sqlite", 1)
    row = Q.claim(conn, "me", lease_s=60)
    url = row["url"]
    assert Q.finish(conn, url, "someone-else", "failed") is False and status_of(conn, 1)["status"] == "running"
    before = conn.execute("SELECT lease_until FROM jobs").fetchone()[0]
    assert Q.renew(conn, url, "me", lease_s=3600) and conn.execute("SELECT lease_until FROM jobs").fetchone()[0] > before
    assert Q.renew(conn, url, "someone-else") is False
    assert Q.finish(conn, url, "me", "scraped", "ok")
    s = status_of(conn, 1)
    assert (s["status"], s["claimed_by"]) == ("scraped", None)


# --- recovery ---------------------------------------------------------------------------------------------------------

def test_a_second_runner_does_not_reset_live_work_but_recovers_a_killed_runners_job(tmp_path):
    conn = seed(tmp_path / "jobs.sqlite", 2)
    live = Q.claim(conn, Q.worker_id(), lease_s=600)                          # this very process: alive, lease current
    dead = Q.claim(conn, f"{socket.gethostname()}:999999:ab12cd", lease_s=600)    # a local pid that does not exist
    remote = seed(tmp_path / "jobs.sqlite", 3, "Remote Co")
    r = Q.claim(remote, "otherhost:1:zz", lease_s=600)                       # another machine, lease current: cannot be judged dead
    assert Q.requeue_stale(conn) == 1
    assert status_of(conn, 1)["status"] == "running"                          # the live runner's job is untouched
    assert conn.execute("SELECT status, notes FROM jobs WHERE url = ?", (dead["url"],)).fetchone()[0] == "queued"
    assert conn.execute("SELECT status FROM jobs WHERE url = ?", (r["url"],)).fetchone()[0] == "running"
    assert conn.execute("SELECT status FROM jobs WHERE url = ?", (live["url"],)).fetchone()[0] == "running"
    later = datetime.now(UTC) + timedelta(seconds=700)                        # the remote lease finally runs out
    assert Q.requeue_stale(conn, now=later) >= 2                              # (this process's own lease has run out in this fiction too)


def test_a_legacy_running_row_is_recovered_only_when_it_has_been_untouched_for_a_lease(tmp_path):
    conn = seed(tmp_path / "jobs.sqlite", 1)
    with conn:
        conn.execute("UPDATE jobs SET status = 'running'")                    # written before leases existed: no owner, no lease
    assert Q.requeue_stale(conn) == 0
    assert Q.requeue_stale(conn, now=datetime.now(UTC) + timedelta(seconds=Q.LEASE_S + 60)) == 1


def test_a_job_a_step_moved_past_running_loses_a_dead_claim_but_keeps_its_status(tmp_path):
    conn = seed(tmp_path / "jobs.sqlite", 1)
    Q.claim(conn, "w", lease_s=10)
    with conn:
        conn.execute("UPDATE jobs SET status = 'scraped'")                    # scrape recorded its own result
    Q.requeue_stale(conn, now=datetime.now(UTC) + timedelta(seconds=60))
    s = status_of(conn, 1)
    assert (s["status"], s["claimed_by"]) == ("scraped", None)


# --- the runner and structured results ---------------------------------------------------------------------------------

def make_step(tmp_path, apply_result=None, apply_code=0, scrape=None):
    """A fake `jobagent` command runner: scrape/research/tailor succeed; apply writes `apply_result` (if any) and returns apply_code."""
    calls = []

    def step(cmd):
        calls.append(cmd[0])
        if cmd[0] == "scrape" and scrape:
            return scrape(cmd)
        if cmd[0] == "apply":
            job = tmp_path / "outputs" / "acme" / "1" / "application"
            job.mkdir(parents=True, exist_ok=True)
            if apply_result is not None:
                (job / "application.json").write_text(json.dumps(apply_result))
            return apply_code
        return 0
    return step, calls


def run(conn, step, tmp_path, through="apply", limit=1, **kw):
    return R.run_queue(conn, step, through=through, limit=limit, company=None, outputs_dir=tmp_path / "outputs",
                       slug_of=lambda c: c.lower(), log=lambda m: None, **kw)


def apply_status(tmp_path, result, code):
    conn = seed(tmp_path / "jobs.sqlite", 1)
    step, _ = make_step(tmp_path, result, code)
    run(conn, step, tmp_path)
    return tuple(status_of(conn, 1)[k] for k in ("status", "notes"))


def test_a_blocked_form_is_never_reported_as_filled_even_if_the_exit_code_said_zero(tmp_path):
    status, note = apply_status(tmp_path, {"state": "blocked", "blocked_by": "challenge", "reason": "a cloudflare check"}, 0)
    assert status == "blocked" and "structured result was used" in note


def test_review_not_ready_is_needs_attention_and_review_ready_is_filled(tmp_path):
    (tmp_path / "a").mkdir()
    assert apply_status(tmp_path / "a", {"state": "review", "ready_to_submit": True}, 0)[0] == "filled"
    (tmp_path / "b").mkdir()
    status, note = apply_status(tmp_path / "b", {"state": "review", "ready_to_submit": False, "blockers": ["Email: required"]}, 5)
    assert status == "needs_attention" and "Email" in note


def test_a_planner_failure_stays_a_failure_and_a_stuck_form_fails(tmp_path):
    status, note = apply_status(tmp_path, {"state": "blocked", "blocked_by": "planner", "reason": "RuntimeError: down"}, 4)
    assert status == "failed" and "planner" in note
    (tmp_path / "s").mkdir()
    assert apply_status(tmp_path / "s", {"state": "stuck"}, 1)[0] == "failed"


def test_without_a_fresh_structured_result_the_exit_code_decides_and_says_so(tmp_path):
    status, note = apply_status(tmp_path, None, 4)
    assert status == "blocked" and "judged by the exit code" in note
    (tmp_path / "old").mkdir()
    conn = seed(tmp_path / "old" / "jobs.sqlite", 1)
    stale = tmp_path / "old" / "outputs" / "acme" / "1" / "application"
    stale.mkdir(parents=True)
    (stale / "application.json").write_text(json.dumps({"state": "review", "ready_to_submit": True}))
    os.utime(stale / "application.json", (time.time() - 3600, time.time() - 3600))      # a previous run's file is not this run's result
    step = lambda cmd: 4 if cmd[0] == "apply" else 0                                      # noqa: E731
    run(conn, step, tmp_path / "old")
    assert status_of(conn, 1)["status"] == "blocked"


def test_a_closed_posting_stays_closed_and_nothing_overwrites_it(tmp_path):
    from jobagent.tools import availability
    conn = seed(tmp_path / "jobs.sqlite", 1)

    def step(cmd):
        if cmd[0] == "apply":
            availability.mark("https://e.test/jobs/1", availability.Unavailable("closed", "no longer accepting", "u"), db.connect(tmp_path / "jobs.sqlite"))
            return 3
        return 0
    run(conn, step, tmp_path)
    s = status_of(conn, 1)
    assert s["status"] == "closed" and "no longer accepting" in s["notes"] and s["claimed_by"] is None


def test_a_scrape_that_finds_the_posting_closed_ends_that_job_and_the_next_starts(tmp_path):
    from jobagent.tools import availability
    conn = seed(tmp_path / "jobs.sqlite", 2)

    def scrape(cmd):
        if cmd[1].endswith("/1"):
            availability.mark(cmd[1], availability.Unavailable("closed", "gone", "u"), db.connect(tmp_path / "jobs.sqlite"))
            return 3
        return 0
    step, calls = make_step(tmp_path, {"state": "review", "ready_to_submit": True}, 0, scrape=scrape)
    n = run(conn, step, tmp_path, limit=2)
    assert status_of(conn, 1)["status"] == "closed" and n == 1


def test_a_runner_that_lost_its_claim_does_not_overwrite_the_new_owners_job(tmp_path):
    conn = seed(tmp_path / "jobs.sqlite", 1)

    def step(cmd):
        if cmd[0] == "apply":                                   # while this runner works, its lease ran out and another took the job
            with conn:
                conn.execute("UPDATE jobs SET claimed_by = 'other:1:x', status = 'running'")
        return 0
    logs = []
    R.run_queue(conn, step, through="apply", limit=1, company=None, outputs_dir=tmp_path / "outputs", slug_of=str.lower, log=logs.append)
    s = status_of(conn, 1)
    assert s["status"] == "running" and s["claimed_by"] == "other:1:x" and any("taken over" in m for m in logs)


def test_the_runner_recovers_a_killed_runners_job_then_runs_it(tmp_path):
    conn = seed(tmp_path / "jobs.sqlite", 1)
    Q.claim(conn, f"{socket.gethostname()}:999999:dead00", lease_s=600)       # a killed runner's claim
    step, calls = make_step(tmp_path, {"state": "review", "ready_to_submit": True}, 0)
    run(conn, step, tmp_path)
    assert status_of(conn, 1)["status"] == "filled" and "apply" in calls


def test_the_heartbeat_renews_the_lease_while_a_step_runs(tmp_path):
    path = tmp_path / "jobs.sqlite"
    conn = seed(path, 1)
    row = Q.claim(conn, "me", lease_s=2)
    first = conn.execute("SELECT lease_until FROM jobs").fetchone()[0]
    with R.Heartbeat(row["url"], "me", 3600, 0.05, lambda: db.connect(path)) as hb:
        time.sleep(0.4)
    assert not hb.lost and conn.execute("SELECT lease_until FROM jobs").fetchone()[0] > first
