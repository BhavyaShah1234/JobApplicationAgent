"""Codex re-review of ab4a718: R03 eligibility, R05 pacing buckets, R04/R07 inputs, R13/R14 logging."""
import io
import threading

import pytest
from rich.console import Console

from jobagent import db
from jobagent import log as L
from jobagent.tools import applicant, eligibility, multi_input, pacing
from jobagent.tools import jobqueue as Q
from jobagent.tools.matching import Match
from test_queue_claims import US

NONE = {"us_citizen": True, "authorized": True, "needs_sponsorship": False, "clearance": "none", "permanent_resident": False}
NOT_CITIZEN = {**NONE, "us_citizen": False}


@pytest.mark.parametrize("text, facts, level", [
    ("U.S. citizenship is required and an active Secret clearance is required.", NONE, "blocked"),
    ("Must be able to obtain an active Secret clearance.", NONE, "review"),
    ("Candidates without a security clearance are not eligible.", NONE, "blocked"),
    ("Active Secret clearance preferred; Python experience required.", NONE, "ok"),
    ("Active TS/SCI clearance required.", {**NONE, "clearance": "Expired TS/SCI"}, "review"),
    ("We are unable to sponsor work visas.", {**NONE, "needs_sponsorship": True}, "blocked"),
    ("Must be a U.S. citizen and show a passport or work authorization.", NOT_CITIZEN, "blocked"),
    ("Must be a U.S. citizen or authorized to work in the United States.", NOT_CITIZEN, "ok"),
    ("No security clearance is required.", NONE, "ok"),
])
def test_eligibility_independent_counterexamples(text, facts, level):                                       # R03
    assert eligibility.screen(text, facts).level == level


def test_unknown_owner_facts_stay_unknown_and_an_explicit_no_is_a_no():                                     # R03
    unk = {"Work": [{"question": "Visa status / citizenship", "answer": {"citizenship": "unknown"}}]}
    assert applicant.eligibility_facts(unk)["us_citizen"] is None
    assert eligibility.screen("U.S. citizenship is required.", applicant.eligibility_facts(unk)).level == "review"
    no = {"Work": [{"question": "Do you currently hold a security clearance?", "answer": False}]}
    assert applicant.eligibility_facts(no)["clearance"] == "None"
    assert eligibility.screen("Active Secret clearance required.", {**NONE, **applicant.eligibility_facts(no)}).level == "blocked"
    yes = {"Work": [{"question": "Do you currently hold a security clearance?", "answer": "Yes"}]}
    assert eligibility.screen("Active Secret clearance required.", {**NONE, **applicant.eligibility_facts(yes)}).level == "review"


def add(conn, company, n):
    Q.enqueue(conn, Q.Listing(company, f"Role {n}", f"https://e.test/{company}/{n}", ["Austin, TX"], req_id=f"{company}{n}"), Match("queued", "regex", "swe", 0.9 - n / 100), US)


def test_an_old_linked_application_does_not_release_a_running_jobs_slot(tmp_path):                         # R05
    conn = db.connect(tmp_path / "jobs.sqlite")
    add(conn, "Acme", 1)
    add(conn, "Acme", 2)
    first = Q.claim(conn, "w1")
    with conn:
        conn.execute("INSERT INTO applications (job_id, job_dir, company, state, filled_at) VALUES (?, 'old', 'Acme', 'filled', '2026-10-01T00:00:00Z')", (first["id"],))
    assert pacing.active_counts(conn, 7) == {"acme": 1}                                                       # 30-day-old: still reserved
    assert Q.claim(conn, "w2", pace=(1, 7)) is None


def test_a_company_name_written_differently_later_is_still_one_bucket(tmp_path):                           # R05
    conn = db.connect(tmp_path / "jobs.sqlite")
    add(conn, "Acme", 1)
    add(conn, "Acme", 2)
    first = Q.claim(conn, "w1")
    with conn:
        conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (first["id"],))
        conn.execute("INSERT INTO applications (job_id, job_dir, company, state, filled_at) VALUES (?, 'x', 'Acme Corporation', 'filled', strftime('%Y-%m-%dT%H:%M:%SZ','now'))", (first["id"],))
    assert pacing.employer_key("Acme Corporation") == pacing.employer_key("Acme, Inc.") == "acme"
    assert Q.claim(conn, "w2", pace=(1, 7)) is None


def test_a_missing_input_file_is_a_failed_row_and_the_other_inputs_still_run(tmp_path):                      # R04
    class P:
        calls = []
        def run(self, url, **kw):
            self.calls.append(url)
            return type("R", (), {"state": "review", "note": "", "code": 0})()
    pipe = P()
    rows = multi_input.run_inputs(["https://ok.test/1", str(tmp_path / "missing.csv")], through="tailor", log=lambda m: None, pipeline=pipe)
    assert pipe.calls == ["https://ok.test/1"] and any(r.kind == "input" and r.state == "failed" for r in rows)
    assert multi_input.exit_code(rows) == 1


def test_url_rows_keep_their_source_and_say_when_a_referral_is_not_attached(tmp_path):                         # R07
    f = tmp_path / "t.csv"
    f.write_text("company,url,contact_name,contact_email,referral_consent\nAcme,https://jobs.acme.test/1,Pat Lee,pat@acme.test,yes\n")
    class P:
        def run(self, url, **kw):
            return type("R", (), {"state": "review", "note": "", "code": 0})()
    rows = multi_input.run_inputs([str(f)], through="tailor", log=lambda m: None, pipeline=P())
    assert "t.csv row 2" in rows[0].note and "Pat Lee not attached" in rows[0].note


def test_conflicting_referrals_in_one_file_attach_none_and_say_so(tmp_path):                                    # R07
    f = tmp_path / "t.csv"
    f.write_text("company,contact_name,contact_email,referral_consent\nAcme,Pat Lee,pat@acme.test,yes\nAcme,Sam Roe,sam@acme.test,yes\n")
    p = multi_input.plan([str(f)])
    assert p.companies == ["Acme"] and not p.referrals and any("conflicts with" in w for w in p.warnings)


def make(tmp_path, job):
    return L.RunLog("apply", job, console=Console(file=io.StringIO(), width=120), directory=tmp_path / "logs", quiet=False)


def test_a_fallback_warning_goes_to_the_run_of_its_own_thread(tmp_path):                                           # R13
    a_ready, b_ready, done = threading.Event(), threading.Event(), threading.Event()
    seen = {}

    def thread_a():
        a = make(tmp_path, "job-a")
        a("apply: start")
        a_ready.set()
        b_ready.wait(5)
        L.default().warn("fallback in A")
        seen["a"] = a
        done.set()

    def thread_b():
        a_ready.wait(5)
        b = make(tmp_path, "job-b")
        b("apply: start")
        seen["b"] = b
        b_ready.set()
        done.wait(5)
    ta, tb = threading.Thread(target=thread_a), threading.Thread(target=thread_b)
    ta.start(); tb.start(); ta.join(); tb.join()
    assert "fallback in A" in seen["a"].path.read_text() and "fallback in A" not in seen["b"].path.read_text()


def test_a_failing_cleanup_of_a_half_opened_pair_never_escapes(tmp_path, monkeypatch):                             # R14
    real = open

    class BadClose:
        def __init__(self, f): self.f = f
        def close(self): raise OSError("close failed")
        def __getattr__(self, n): return getattr(self.f, n)

    def fake(path, *a, **k):
        if str(path).endswith(".jsonl"):
            raise OSError("no space")
        return BadClose(real(path, *a, **k))
    monkeypatch.setattr("builtins.open", fake)
    log = make(tmp_path, "x")
    log("apply: still works")                                                                                       # must not raise
