"""Step 29: eligibility screen, per-company pacing, the review queue and several inputs in one run."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from jobagent import db, main
from jobagent.config import get_settings
from jobagent.tools import applicant, eligibility, multi_input, pacing, review_queue
from jobagent.tools import jobqueue as Q
from jobagent.tools import queue_runner as R
from jobagent.tools.locations import Decision
from jobagent.tools.matching import Match
from test_queue_claims import make_step, run as run_queue_test, seed

NOT_CITIZEN = {"us_citizen": False, "authorized": True, "needs_sponsorship": False, "clearance": None, "permanent_resident": False}
CITIZEN = {**NOT_CITIZEN, "us_citizen": True}
UNKNOWN = {"us_citizen": None, "authorized": None, "needs_sponsorship": None, "clearance": None, "permanent_resident": False}
PANTEX = ("Cloud Solutions Engineer. Travel - 30% or less. This position requires U.S. citizenship. "
          "Pantex is a DOE facility. Duties: administer cloud infrastructure for the plant.")


# --- eligibility ----------------------------------------------------------------------------------------------------------------

def test_a_citizenship_requirement_blocks_a_candidate_whose_file_says_they_are_not_a_citizen():
    v = eligibility.screen(PANTEX, NOT_CITIZEN)
    assert v.level == "blocked" and v.reasons[0]["requirement"] == "U.S. citizenship"
    assert "requires U.S. citizenship" in v.reasons[0]["quote"] and "not a U.S. citizen" in v.reasons[0]["why"]


def test_an_unknown_fact_asks_instead_of_blocking_and_a_citizen_passes():
    assert eligibility.screen(PANTEX, UNKNOWN).level == "review"
    assert eligibility.screen(PANTEX, CITIZEN).level == "ok"


@pytest.mark.parametrize("text", ["U.S. citizenship is required.", "Applicants must be a U.S. citizen.", "Only U.S. citizens will be considered.",
                                  "US citizens only.", "Candidates must be a citizen of the United States."])
def test_citizenship_wordings(text):
    assert eligibility.screen(text, NOT_CITIZEN).level == "blocked"


def test_an_either_or_requirement_is_a_review_not_a_block():
    text = "Must be a U.S. citizen or permanent resident (green card holder)."
    assert eligibility.screen(text, NOT_CITIZEN).level == "review"
    assert eligibility.screen(text, {**NOT_CITIZEN, "permanent_resident": True}).level == "ok"
    assert eligibility.screen("Must be a U.S. citizen or authorized to work in the United States.", NOT_CITIZEN).level == "ok"   # the posting offers it


def test_clearance_and_sponsorship_requirements():
    none = {**NOT_CITIZEN, "clearance": "None"}                                       # the file says so; absent (None) means unknown
    assert eligibility.screen("Active TS/SCI clearance required.", none).level == "blocked"
    assert eligibility.screen("Active TS/SCI clearance required.", NOT_CITIZEN).level == "review"
    assert eligibility.screen("Must be able to obtain and maintain a security clearance.", none).level == "review"
    assert eligibility.screen("No security clearance is required.", none).level == "ok"
    assert eligibility.screen("Security clearance is a plus.", none).level == "ok"
    assert eligibility.screen("Active TS/SCI clearance required.", {**NOT_CITIZEN, "clearance": "Secret"}).level == "review"
    assert eligibility.screen("Active Secret clearance required.", {**NOT_CITIZEN, "clearance": "Top Secret"}).level == "ok"
    assert eligibility.screen("Must be a U.S. citizen and have work authorization.", NOT_CITIZEN).level == "blocked"   # "and" is no alternative
    assert eligibility.screen("We will not provide visa sponsorship.", {**NOT_CITIZEN, "needs_sponsorship": True}).level == "blocked"
    assert eligibility.screen("We are unable to sponsor work visas.", {**NOT_CITIZEN, "needs_sponsorship": False}).level == "ok"
    assert eligibility.screen("No sponsorship is available.", UNKNOWN).level == "review"
    assert eligibility.screen("This role is subject to ITAR export controls; U.S. persons only.", NOT_CITIZEN).level == "review"


def test_ordinary_postings_are_not_flagged():
    text = ("We are a good corporate citizen. You will work on citizen science datasets and security reviews. Clearance of tickets is part "
            "of the job. We sponsor meetups and offer relocation support.")
    assert eligibility.screen(text, NOT_CITIZEN).level == "ok"


def test_the_candidates_facts_come_only_from_their_file():
    data = {"Work authorization": [{"question": "Are you legally authorized to work in the United States?", "answer": "Yes"},
                                   {"question": "Will you now require sponsorship?", "answer": "No"},
                                   {"question": "Will you in the future require sponsorship for employment visa status?", "answer": "Yes"},
                                   {"question": "Visa status / citizenship", "answer": {"visa_status": "F-1 OPT", "citizenship": "India"}}]}
    f = applicant.eligibility_facts(data)
    assert f["us_citizen"] is False and f["authorized"] is True and f["needs_sponsorship"] is True and f["clearance"] is None
    assert applicant.eligibility_facts({})["us_citizen"] is None                       # nothing declared: unknown, never assumed
    assert applicant.eligibility_facts({"x": [{"question": "Visa status / citizenship", "answer": {"citizenship": "USA"}}]})["us_citizen"] is True


def make_pipeline(tmp_path, monkeypatch, text, facts):
    from jobagent.tools.pipeline import Pipeline
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    conn = db.connect()
    conn.execute("INSERT INTO jobs (url, company, status) VALUES ('https://p.test/1', 'Pantex', 'running')")
    conn.commit()
    monkeypatch.setattr(applicant, "eligibility_facts", lambda *a, **k: facts)
    d = tmp_path / "outputs" / "pantex" / "1"
    d.mkdir(parents=True)
    (d / "jd.txt").write_text(text)
    logs = []
    return Pipeline(conn=conn, log=logs.append), conn, d, logs


def test_the_pipeline_stops_a_blocked_posting_before_any_work_and_records_why(tmp_path, monkeypatch):
    pipe, conn, d, logs = make_pipeline(tmp_path, monkeypatch, PANTEX, NOT_CITIZEN)
    row = conn.execute("SELECT * FROM jobs").fetchone()
    res = pipe._eligibility(d, row, ignored=False)
    assert res.state == "needs_review" and res.code == 4 and "requires U.S. citizenship" in res.note
    assert conn.execute("SELECT status, notes FROM jobs").fetchone()[0] == "needs_review"
    rec = json.loads((d / "eligibility.json").read_text())
    assert rec["level"] == "blocked" and not rec["ignored"]
    assert any("--ignore-eligibility" in m for m in logs)
    get_settings.cache_clear()


def test_a_review_level_continues_and_the_override_is_recorded(tmp_path, monkeypatch):
    pipe, conn, d, logs = make_pipeline(tmp_path, monkeypatch, PANTEX, UNKNOWN)
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert pipe._eligibility(d, row, ignored=False) is None and any("eligibility review" in m for m in logs)
    pipe2, conn2, d2, _ = make_pipeline(tmp_path / "b", monkeypatch, PANTEX, NOT_CITIZEN)
    assert pipe2._eligibility(d2, conn2.execute("SELECT * FROM jobs").fetchone(), ignored=True) is None
    assert json.loads((d2 / "eligibility.json").read_text())["ignored"] is True
    get_settings.cache_clear()


# --- pacing ---------------------------------------------------------------------------------------------------------------------

def stamp(days_ago):
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_application(conn, company, state, days_ago, n=[0]):
    n[0] += 1
    conn.execute("INSERT INTO applications (job_dir, company, title, url, state, submitted_at, filled_at) VALUES (?,?,?,?,?,?,?)",
                 (f"outputs/{company}/{n[0]}", company, "T", f"https://x.test/{company}/{n[0]}", state, stamp(days_ago), stamp(days_ago)))
    conn.commit()


def test_pacing_counts_recent_started_or_sent_applications_per_company(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    add_application(conn, "Acme", "submitted", 1)
    add_application(conn, "Acme", "filled", 2)
    add_application(conn, "Acme", "submitted", 30)                    # outside the window
    add_application(conn, "Beta Corp", "skipped", 1)                  # never counted
    add_application(conn, "Beta Corp", "submitted", 3)
    assert pacing.recent_counts(conn, 7) == {"acme": 2, "beta-corp": 1}
    assert pacing.excluded(conn, 2, 7) == {"acme": 2} and pacing.excluded(conn, 0, 7) == {}


def test_a_company_at_its_limit_waits_while_other_companies_go_on(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    conn = seed(tmp_path / "jobs.sqlite", n=2, company="Acme")
    Q.enqueue(conn, Q.Listing("Beta", "Dev", "https://b.test/1", ["Austin, TX"], req_id="b1"), Match("queued", "regex", "swe", 0.5),
              Decision("us", "t", "Austin"))
    add_application(conn, "Acme", "submitted", 1)
    add_application(conn, "Acme", "filled", 2)
    row = Q.claim(conn, "w", exclude=tuple(pacing.excluded(conn, 2, 7)))
    assert row["company"] == "Beta"                                                       # Acme's higher-scored jobs are skipped
    assert Q.claim(conn, "w", exclude=("acme", "beta")) is None
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE company_slug = 'acme' AND status = 'queued'").fetchone()[0] == 2
    get_settings.cache_clear()


def test_the_queue_runner_announces_pacing_and_leaves_the_paced_jobs_queued(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    conn = seed(tmp_path / "jobs.sqlite", n=2, company="Acme")
    add_application(conn, "Acme", "submitted", 1)
    add_application(conn, "Acme", "submitted", 2)
    logs = []
    step, calls = make_step(tmp_path, {"state": "review", "ready_to_submit": True}, 0)
    n = R.run_queue(conn, step, through="apply", limit=3, company=None, outputs_dir=tmp_path / "outputs", slug_of=str.lower, log=logs.append)
    assert n == 0 and calls == [] and any("pacing: acme already has 2 application(s)" in m for m in logs)
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued'").fetchone()[0] == 2
    monkeypatch.setenv("PACING_MAX_PER_COMPANY", "0")                                     # pacing off: the same jobs run
    get_settings.cache_clear()
    n = R.run_queue(conn, step, through="apply", limit=1, company=None, outputs_dir=tmp_path / "outputs", slug_of=str.lower, log=logs.append)
    assert n == 1
    get_settings.cache_clear()


# --- the review queue -----------------------------------------------------------------------------------------------------------

def test_the_review_queue_lists_filled_applications_with_readiness_and_the_command(tmp_path, monkeypatch):
    from jobagent import log
    monkeypatch.setattr(log, "in_container", lambda: False)
    conn = db.connect(tmp_path / "jobs.sqlite")
    ready = tmp_path / "outputs" / "acme" / "1"
    stuck = tmp_path / "outputs" / "beta" / "2"
    for d, res in ((ready, {"ready_to_submit": True, "blockers": []}), (stuck, {"ready_to_submit": False, "blockers": ["Email: required, left empty"]})):
        (d / "application").mkdir(parents=True)
        (d / "application" / "application.json").write_text(json.dumps(res))
    conn.execute("INSERT INTO applications (job_dir, company, title, state, filled_at) VALUES (?, 'Acme', 'Engineer', 'filled', ?)", (str(ready), stamp(0.5)))
    conn.execute("INSERT INTO applications (job_dir, company, title, state, filled_at) VALUES (?, 'Beta Corp', 'Dev', 'filled', ?)", (str(stuck), stamp(3)))
    conn.execute("INSERT INTO applications (job_dir, company, title, state, filled_at) VALUES ('outputs/c/3', 'C', 'T', 'submitted', ?)", (stamp(1),))
    conn.execute("INSERT INTO applications (job_dir, company, title, state, filled_at) VALUES ('outputs/d/4', 'D', 'T', 'filled', ?)", (stamp(2),))
    conn.commit()
    rows = review_queue.waiting(conn, tmp_path)
    assert [r["company"] for r in rows] == ["Beta Corp", "D", "Acme"]                     # oldest first; a sent one is not waiting
    by = {r["company"]: r for r in rows}
    assert by["Acme"]["ready"] and by["Acme"]["blockers"] == []
    assert not by["Beta Corp"]["ready"] and by["Beta Corp"]["blockers"] == ["Email: required, left empty"]
    assert by["D"]["blockers"] == ["no result recorded for this application"] and not by["D"]["ready"]
    assert by["Acme"]["review_command"].startswith("jobagent submit ") and by["Acme"]["review_command"].endswith("acme/1 acme")
    assert by["Beta Corp"]["review_command"].endswith(" beta-corp")


def test_the_review_queue_command_never_approves_anything(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    conn = db.connect()
    r = CliRunner().invoke(main.app, ["review-queue"])
    assert r.exit_code == 0 and "nothing is waiting" in r.output
    conn.execute("INSERT INTO applications (job_dir, company, title, state, filled_at) VALUES ('outputs/a/1', 'Acme', 'Engineer', 'filled', ?)", (stamp(1),))
    conn.commit()
    r = CliRunner().invoke(main.app, ["review-queue"])
    assert r.exit_code == 0 and "Acme" in r.output and "submit" in r.output
    assert conn.execute("SELECT state FROM applications").fetchone()[0] == "filled"       # listing changed nothing
    get_settings.cache_clear()


# --- several inputs in one run ----------------------------------------------------------------------------------------------------

class FakePipeline:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def run(self, url, **kw):
        self.calls.append((url, kw))
        if url in self.fail:
            raise RuntimeError("boom")
        return SimpleNamespace(state="review", note="", code=0)


def test_inputs_are_routed_and_deduplicated():
    p = multi_input.plan(["https://jobs.acme.test/1?utm_source=x", "https://jobs.acme.test/1", "Acme, Beta Corp", "acme"])
    assert p.urls == ["https://jobs.acme.test/1?utm_source=x"] and p.companies == ["Acme", "Beta Corp"]
    assert any("duplicate input" in w for w in p.warnings)


def test_urls_and_companies_run_together_and_one_failure_does_not_stop_the_rest():
    pipe, found, worked = FakePipeline(fail=["https://bad.test/1"]), [], []
    discover = lambda names: found.append(names) or [SimpleNamespace(company=n, counts={"queued": 2, "needs_review": 1}, listings=9) for n in names]   # noqa: E731
    rows = multi_input.run_inputs(["https://bad.test/1", "https://ok.test/2", "Acme, Beta"], through="apply", limit=3, log=lambda m: None,
                                  pipeline=pipe, discover=discover, work_queue=lambda t, n, **k: worked.append((t, n)) or 2, browser_mode="docker")
    assert [(r.kind, r.state) for r in rows] == [("url", "failed"), ("url", "review"), ("company", "discovered"), ("company", "discovered"), ("queue", "worked")]
    assert [c[0] for c in pipe.calls] == ["https://bad.test/1", "https://ok.test/2"] and pipe.calls[0][1]["through"] == "apply"
    assert found == [["Acme", "Beta"]] and worked == [("apply", 3)]


def test_a_batch_never_submits_a_companys_jobs_and_empty_input_is_reported():
    worked = []
    rows = multi_input.run_inputs(["Acme"], through="submit", limit=2, log=lambda m: None, pipeline=FakePipeline(),
                                  discover=lambda n: [], work_queue=lambda t, n, **k: worked.append(t) or 0)
    assert worked == ["apply"]                                                              # submit is downgraded for company inputs
    pipe = FakePipeline()
    rows = multi_input.run_inputs(["https://ok.test/2"], through="submit", log=lambda m: None, pipeline=pipe)
    assert pipe.calls[0][1]["through"] == "submit"                                          # a URL still stops at the typed approval downstream
    assert multi_input.run_inputs([""], log=lambda m: None)[0].state == "failed"


def test_the_run_command_takes_several_inputs_and_exits_nonzero_when_one_failed(monkeypatch, tmp_path):
    import jobagent.tools.pipeline as P
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    pipe = FakePipeline(fail=["https://bad.test/1"])
    monkeypatch.setattr(P, "Pipeline", lambda *a, **k: pipe)
    r = CliRunner().invoke(main.app, ["run", "https://ok.test/2", "https://ok.test/3", "--through", "tailor", "--no-ats"])
    assert r.exit_code == 0, r.output
    assert [c[0] for c in pipe.calls] == ["https://ok.test/2", "https://ok.test/3"] and pipe.calls[0][1]["no_ats"] is True
    r = CliRunner().invoke(main.app, ["run", "https://bad.test/1", "https://ok.test/2", "--ignore-eligibility"])
    assert r.exit_code == 1 and "failed" in r.output and pipe.calls[-1][1]["ignore_eligibility"] is True
    get_settings.cache_clear()
