"""Codex review R01-R07: a batch works only its own companies, keeps its options and exit codes, reserves pacing slots atomically."""
import sqlite3
from types import SimpleNamespace

from typer.testing import CliRunner

from jobagent import db, main
from jobagent.config import get_settings
from jobagent.tools import jobqueue as Q
from jobagent.tools import multi_input, pacing
from jobagent.tools import queue_runner as R
from jobagent.tools.matching import Match
from test_queue_claims import US


def add(conn, company, title, n, score):
    Q.enqueue(conn, Q.Listing(company, title, f"https://e.test/{company}/{n}", ["Austin, TX"], req_id=f"{company}{n}"),
              Match("queued", "regex", "swe", score), US)
    with conn:
        conn.execute("UPDATE jobs SET company = ? WHERE req_id = ?", (company, f"{company}{n}"))


class Recorder:
    def __init__(self, code=0, state="review"):
        self.calls, self.code, self.state = [], code, state

    def run(self, url, **kw):
        self.calls.append((url, kw))
        return SimpleNamespace(state=self.state, note="n", code=self.code)


def drain(conn, **kw):
    pipe = kw.pop("pipeline", Recorder())
    results: list = []
    n = R.run_queue(conn, None, through="apply", limit=5, company=None, outputs_dir=None, slug_of=str, log=lambda m: None,
                    pipeline=pipe, results=results, **kw)
    return n, pipe, results


def test_a_batch_claims_only_the_requested_companies(tmp_path):                                    # R01
    conn = db.connect(tmp_path / "jobs.sqlite")
    add(conn, "Other", "Best role", 1, 0.99)
    add(conn, "Acme", "Okay role", 1, 0.5)
    n, pipe, results = drain(conn, companies=(Q.slug("Acme"),))
    assert [c[0] for c in pipe.calls] == ["https://e.test/Acme/1"] and n == 1
    assert drain(conn, companies=("nobody",))[1].calls == []                                        # nothing found for them: nothing claimed
    assert conn.execute("SELECT status FROM jobs WHERE company = 'Other'").fetchone()[0] == "queued"


def test_execution_options_reach_the_pipeline_but_eligibility_override_does_not(tmp_path):         # R02
    conn = db.connect(tmp_path / "jobs.sqlite")
    add(conn, "Acme", "Role", 1, 0.9)
    _, pipe, _ = drain(conn, companies=("acme",), pipeline_options={"no_ats": True, "force": True, "filling_strategy": "original",
                                                                   "ignore_eligibility": True})
    kw = pipe.calls[0][1]
    assert kw["no_ats"] is True and kw["force"] is True and kw["filling_strategy"] == "original" and "ignore_eligibility" not in kw


def test_the_default_company_path_passes_options_and_companies_to_the_queue():                    # R02 via run_inputs
    seen = {}

    def work(t, n, *, companies, options, results):
        seen.update(companies=companies, options=options)
        return 0
    multi_input.run_inputs(["Acme"], through="apply", limit=2, log=lambda m: None, discover=lambda n: [], work_queue=work,
                           no_ats=True, force=True, filling_strategy="original", ignore_eligibility=True, browser_mode="docker")
    assert seen["companies"] == ("acme",)
    assert seen["options"] == {"no_ats": True, "force": True, "filling_strategy": "original", "browser_mode": "docker"}


def test_failed_jobs_are_not_counted_as_reaching_the_stage_and_keep_their_codes(tmp_path):         # R04
    conn = db.connect(tmp_path / "jobs.sqlite")
    add(conn, "Acme", "Role", 1, 0.9)
    n, _, results = drain(conn, companies=("acme",), pipeline=Recorder(code=4, state="needs_review"))
    assert n == 0 and results[0]["code"] == 4 and results[0]["state"] == "needs_review"


def test_the_exit_status_reports_stopped_work_and_discovery_failures():                            # R04
    ok = multi_input.Row("u", "url", "review")
    assert multi_input.exit_code([ok]) == 0
    assert multi_input.exit_code([ok, multi_input.Row("u2", "url", "needs_review", "", 4)]) == 4
    assert multi_input.exit_code([ok, multi_input.Row("u3", "url", "failed")]) == 1
    found = [SimpleNamespace(company="Acme", counts={}, listings=0, note="blocked by bot protection")]
    rows = multi_input.run_inputs(["Acme"], log=lambda m: None, discover=lambda n: found, work_queue=lambda t, n, **k: 0)
    assert rows[0].state == "failed" and "bot protection" in rows[0].note and multi_input.exit_code(rows) == 1


def test_the_run_command_exits_with_the_pipelines_code(monkeypatch, tmp_path):                      # R04
    import jobagent.tools.pipeline as P
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(P, "Pipeline", lambda *a, **k: Recorder(code=4, state="needs_review"))
    r = CliRunner().invoke(main.app, ["run", "https://ok.test/2", "--through", "tailor"])
    assert r.exit_code == 4
    get_settings.cache_clear()


def test_pacing_reserves_the_last_slot_for_one_of_two_workers(tmp_path):                           # R05
    path = tmp_path / "jobs.sqlite"
    a = db.connect(path)
    add(a, "Acme", "One", 1, 0.9)
    add(a, "Acme", "Two", 2, 0.8)
    b = db.connect(path)
    first = Q.claim(a, "wa", pace=(1, 7))
    second = Q.claim(b, "wb", pace=(1, 7))
    assert first is not None and second is None
    assert pacing.active_counts(a) == {"acme": 1}
    Q.finish(a, first["url"], "wa", "failed")                                                         # a failed job gives its slot back
    assert Q.claim(b, "wb", pace=(1, 7)) is not None


def test_a_running_job_with_a_counted_application_is_not_counted_twice(tmp_path):                 # R05
    conn = db.connect(tmp_path / "jobs.sqlite")
    add(conn, "Acme", "One", 1, 0.9)
    row = Q.claim(conn, "w")
    with conn:
        conn.execute("INSERT INTO applications (job_id, job_dir, company, state, filled_at) VALUES (?, 'x', 'Acme', 'filled', "
                     "strftime('%Y-%m-%dT%H:%M:%SZ','now'))", (row["id"],))
    assert pacing.active_counts(conn) == {} and pacing.excluded(conn, 1, 7, active=True) == {"acme": 1}


def test_one_unreadable_file_does_not_stop_the_other_inputs(tmp_path):                             # R06
    bad = tmp_path / "targets.xlsx"
    bad.write_bytes(b"not a zip")
    pipe = Recorder()
    rows = multi_input.run_inputs(["https://ok.test/1", str(bad)], through="tailor", log=lambda m: None, pipeline=pipe)
    assert [c[0] for c in pipe.calls] == ["https://ok.test/1"]
    assert any(r.kind == "input" and r.state == "failed" and str(bad) == r.input for r in rows) and multi_input.exit_code(rows) == 1


def test_a_file_rows_source_and_referral_survive_planning_and_conflicts_are_explicit(tmp_path):    # R07
    f = tmp_path / "t.csv"
    f.write_text("company,contact_name,contact_email,referral_consent\nAcme,Pat Lee,pat@acme.test,yes\nBeta,Sam Roe,sam@beta.test,no\n"
                 "Acme,Other Person,other@acme.test,yes\n")
    p = multi_input.plan([str(f)])
    assert p.companies == ["Acme", "Beta"] and "beta" not in p.referrals
    assert "acme" not in p.referrals and any("conflicts with" in w for w in p.warnings)   # two different contacts for one company: neither is used
    assert "row" in p.sources["acme"] and "row" in p.sources["beta"]
    two = multi_input.plan(["Acme", str(f)])                                  # a bare name and a file row for one company: still one company
    assert two.companies == ["Acme", "Beta"]
