"""The dashboard's "available / away" toggle: away, the agent does not ask; a job that needs the candidate goes to the end of the
queue and the runner moves on; deferred jobs resume, oldest first, once the candidate is available again."""
import json
import threading
import time

import pytest

from jobagent import db, progress
from jobagent.config import get_settings
from jobagent.tools import jobqueue as Q
from jobagent.tools import presence
from jobagent.tools import queue_runner as R
from jobagent.tools.hitl import CandidateAway, Hitl, HitlTimeout
from jobagent.tools.locations import Decision
from jobagent.tools.matching import Match

US = Decision("us", "t", "Austin")


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def seed(path, n=3):
    conn = db.connect(path)
    for i in range(1, n + 1):
        Q.enqueue(conn, Q.Listing("Acme", f"Engineer {i}", f"https://e.test/jobs/{i}", ["Austin, TX"], req_id=str(i)),
                  Match("queued", "regex", "swe", 0.9 - i / 100), US)
        with conn:
            conn.execute("UPDATE jobs SET job_id = ?, company = 'Acme' WHERE req_id = ?", (str(i), str(i)))
    return conn


# --- the flag and Hitl.ask -----------------------------------------------------------------------------------------------

def test_a_missing_or_broken_flag_means_available(state):
    assert presence.available(state)
    (state / "hitl").mkdir()
    (state / "hitl" / "presence.json").write_text("{not json")
    assert presence.available(state)
    presence.set_available(False, state_dir=state)
    assert not presence.available(state) and presence.status(state)["changed_by"] == "dashboard"


def test_away_the_question_is_not_asked_and_the_job_is_recorded_as_deferred(state):
    presence.set_available(False, state_dir=state)
    h = Hitl(state / "hitl", lambda m: pytest.fail("nothing may be shown to an absent candidate"))
    with progress.execution(new=True, url="https://e.test/jobs/1", job_dir=str(state / "outputs" / "acme" / "1")):
        with pytest.raises(CandidateAway) as info:
            h.ask("Desired salary?")
    assert isinstance(info.value, HitlTimeout)                 # existing `except HitlTimeout` handlers stop it the same way
    assert not list((state / "hitl" / "pending").glob("*.json"))
    row = presence.deferred_since({"https://e.test/jobs/1"}, [], "2000-01-01T00:00:00+00:00", state)
    assert row["question"] == "Desired salary?"
    by_folder = presence.deferred_since({"https://other.test/x"}, [str(state / "outputs" / "acme" / "1")], "2000-01-01T00:00:00+00:00", state)
    assert by_folder == row                                       # the posting's own url can differ from the queue's


def test_going_away_while_a_question_waits_stops_the_wait(state):
    h = Hitl(state / "hitl", lambda m: None)
    flipped = threading.Timer(0.3, lambda: presence.set_available(False, state_dir=state))
    flipped.start()
    started = time.monotonic()
    with progress.execution(new=True, url="https://e.test/jobs/2"):
        with pytest.raises(CandidateAway):
            h.ask("Agile experience?", poll_s=0.05, timeout_s=10)
    assert time.monotonic() - started < 5
    assert not list((state / "hitl" / "pending").glob("*.json"))      # the request is withdrawn


def test_available_the_question_is_asked_and_answered_as_before(state):
    h = Hitl(state / "hitl", lambda m: None)
    def answer():
        for _ in range(100):
            reqs = h.list_pending()
            if reqs:
                h.answer(reqs[0].id, "Yes")
                return
            time.sleep(0.02)
    threading.Thread(target=answer, daemon=True).start()
    assert h.ask("Agile experience?", poll_s=0.02, timeout_s=5) == "Yes"
    assert not (state / "hitl" / "deferrals.jsonl").exists()


# --- the queue -----------------------------------------------------------------------------------------------------------

def test_a_deferred_job_goes_to_the_end_and_is_not_taken_while_away(tmp_path):
    conn = seed(tmp_path / "jobs.sqlite", 3)
    first = Q.claim(conn, "w")
    assert first["req_id"] == "1"
    assert Q.defer(conn, first["url"], "w", "waiting for you")
    row = conn.execute("SELECT status, deferred_at, claimed_by FROM jobs WHERE req_id = '1'").fetchone()
    assert row["status"] == "queued" and row["deferred_at"] and row["claimed_by"] is None
    assert [r["req_id"] for r in Q.pending(conn)] == ["2", "3", "1"]              # best match first, the deferred one last
    assert Q.deferred_count(conn) == 1
    assert Q.claim(conn, "w", include_deferred=False)["req_id"] == "2"
    assert Q.claim(conn, "w", include_deferred=False)["req_id"] == "3"
    assert Q.claim(conn, "w", include_deferred=False) is None                    # away: only the deferred job is left
    again = Q.claim(conn, "w")                                                    # back: it is resumed
    assert again["req_id"] == "1"
    assert Q.finish(conn, again["url"], "w", "filled")
    assert conn.execute("SELECT deferred_at FROM jobs WHERE req_id = '1'").fetchone()[0] is None


def test_only_the_claim_holder_can_defer(tmp_path):
    conn = seed(tmp_path / "jobs.sqlite", 1)
    row = Q.claim(conn, "w1")
    assert not Q.defer(conn, row["url"], "w2")
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "running"


class FakePipeline:
    """Job 1 needs the candidate (asks through the real Hitl); the others fill without questions."""

    def __init__(self, state):
        self.state, self.calls = state, []

    def run(self, url, *, through, browser_mode, **kw):
        from jobagent.tools.pipeline import PipelineResult
        self.calls.append(url)
        if url.endswith("/1"):
            with progress.execution(new=True, url=url):
                try:
                    Hitl(self.state / "hitl", lambda m: None).ask("Desired salary?", timeout_s=0.2, poll_s=0.02)
                except HitlTimeout:                     # what the apply step does with an unanswered question: blocked
                    return PipelineResult("blocked", code=4, note="a question was not answered")
        return PipelineResult("filled")


def test_the_runner_moves_on_while_away_and_resumes_the_deferred_job_when_back(state):
    conn = seed(state / "jobs.sqlite", 3)
    presence.set_available(False, state_dir=state)
    pipe = FakePipeline(state)
    waits, results = [], []

    def sleep(seconds):                                   # the candidate comes back while the runner waits for them
        waits.append(seconds)
        presence.set_available(True, by="terminal", state_dir=state)

    # resumed once the candidate is back, the job's question gets answered, so it fills this time
    pipe_run = pipe.run

    def run_and_answer(url, **kw):
        if url.endswith("/1") and presence.available(state):
            from jobagent.tools.pipeline import PipelineResult
            pipe.calls.append(url)
            return PipelineResult("filled")
        return pipe_run(url, **kw)
    pipe.run = run_and_answer
    done = R.run_queue(conn, through="apply", limit=3, company=None, outputs_dir=state / "outputs", slug_of=str.lower,
                       log=lambda m: None, pipeline=pipe, results=results, away_poll_s=0.01, sleep=sleep)
    assert pipe.calls == ["https://e.test/jobs/1", "https://e.test/jobs/2", "https://e.test/jobs/3", "https://e.test/jobs/1"]
    assert [r["state"] for r in results] == ["deferred", "filled", "filled", "filled"]
    assert done == 3 and len(waits) == 1                   # the deferral did not use up the limit; one wait for the candidate
    assert conn.execute("SELECT status, deferred_at FROM jobs WHERE req_id = '1'").fetchone()[:] == ("filled", None)


def test_a_job_that_failed_for_its_own_reasons_is_not_deferred(state):
    conn = seed(state / "jobs.sqlite", 1)
    presence.set_available(False, state_dir=state)

    class Broken:
        def run(self, url, **kw):
            from jobagent.tools.pipeline import PipelineResult
            return PipelineResult("blocked", code=4, note="a bot check")
    R.run_queue(conn, through="apply", limit=1, company=None, outputs_dir=state / "outputs", slug_of=str.lower,
                log=lambda m: None, pipeline=Broken(), away_poll_s=0.01, sleep=lambda s: pytest.fail("nothing waits for the candidate"))
    assert conn.execute("SELECT status, deferred_at FROM jobs").fetchone()[:] == ("blocked", None)


# --- the dashboard -------------------------------------------------------------------------------------------------------

def test_the_dashboard_switches_presence_and_shows_how_many_jobs_wait(tmp_path):
    from test_dashboard import call
    from jobagent.config import Settings
    from jobagent.dashboard import data
    from jobagent.dashboard.demo import seed as demo
    from jobagent.dashboard.server import make_server
    settings = Settings(**demo(tmp_path))
    srv = make_server(settings, "127.0.0.1", 0, popen=lambda *a, **k: None)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        assert call(srv, "GET", "/api/presence")[1]["available"] is True
        code, out = call(srv, "POST", "/api/presence", {"available": False})
        assert code == 200 and out["available"] is False
        assert data.overview(settings)["presence"]["available"] is False
        assert call(srv, "POST", "/api/presence", {"available": "no"})[0] == 400
        code, out = call(srv, "POST", "/api/presence", {"available": True}, headers={"X-Dashboard": "0"})
        assert code == 403 and data.presence(settings)["available"] is False      # the cross-site guard still applies
    finally:
        srv.shutdown()
        srv.server_close()
    ro = make_server(settings, "127.0.0.1", 0, popen=lambda *a, **k: None, read_only=True)
    t = threading.Thread(target=ro.serve_forever, daemon=True)
    t.start()
    try:
        assert call(ro, "POST", "/api/presence", {"available": True})[0] == 403
    finally:
        ro.shutdown()
        ro.server_close()
