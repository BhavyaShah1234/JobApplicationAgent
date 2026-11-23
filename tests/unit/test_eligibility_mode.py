"""eligibility_mode: off | warn | stop. The default is warn: the finding is logged, recorded and shown at review, and nothing is ever stopped or blocked by it."""
import json
from types import SimpleNamespace

import pytest

from jobagent import config as C
from jobagent import db
from jobagent.tools import eligibility, jobqueue
from jobagent.tools.eligibility import Verdict
from jobagent.tools.pipeline import Pipeline

BLOCKED = Verdict(level="blocked", reasons=[{"requirement": "U.S. citizenship", "quote": "- US Citizenship required.", "why": "your file says you are not a U.S. citizen"}])


def settings(monkeypatch, tmp_path, yaml_text="", env=None):
    (tmp_path / "queue.yaml").write_text(yaml_text)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    return C.Settings()


def test_the_default_is_warn_and_yaml_and_environment_can_change_it(monkeypatch, tmp_path):
    assert settings(monkeypatch, tmp_path).eligibility_mode == "warn"
    assert settings(monkeypatch, tmp_path, "eligibility_mode: stop\n").eligibility_mode == "stop"
    assert settings(monkeypatch, tmp_path, "eligibility_mode: stop\n", {"ELIGIBILITY_MODE": "off"}).eligibility_mode == "off"


def test_a_bad_value_is_refused(monkeypatch, tmp_path):
    with pytest.raises(Exception):
        settings(monkeypatch, tmp_path, "eligibility_mode: sometimes\n")


@pytest.fixture
def job(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "jobs.sqlite")
    url = "https://jobs.example.test/1"
    conn.execute("INSERT INTO jobs (url, company, title, status) VALUES (?, 'Acme', 'Engineer', 'queued')", (url,))
    conn.commit()
    d = tmp_path / "acme" / "1"
    d.mkdir(parents=True)
    (d / "jd.txt").write_text("- US Citizenship required.\nBuild things.")
    logged = []
    monkeypatch.setattr(eligibility, "assess_job", lambda _d: BLOCKED)
    return SimpleNamespace(log=logged.append, conn=conn), d, {"url": url}, logged


def screen(job, mode, monkeypatch, ignored=False):
    me, d, row, logged = job
    monkeypatch.setattr(eligibility, "mode", lambda: mode)
    return Pipeline._eligibility(me, d, row, ignored), me, d, row, logged


def test_warn_mode_goes_on_logs_a_warning_and_records_the_finding(job, monkeypatch):
    stop, me, d, row, logged = screen(job, "warn", monkeypatch)
    assert stop is None
    assert any("WARNING" in x and "going on" in x for x in logged)
    rec = json.loads((d / "eligibility.json").read_text())
    assert rec["level"] == "blocked" and rec["mode"] == "warn" and rec["reasons"]
    assert me.conn.execute("SELECT status FROM jobs WHERE url = ?", (row["url"],)).fetchone()["status"] == "queued"      # not set aside


def test_stop_mode_still_stops_before_any_work_and_the_override_still_works(job, monkeypatch):
    stop, me, d, row, logged = screen(job, "stop", monkeypatch)
    assert stop is not None and stop.status == "needs_review" and stop.exit_code == 4 if hasattr(stop, "exit_code") else stop is not None
    assert me.conn.execute("SELECT status FROM jobs WHERE url = ?", (row["url"],)).fetchone()["status"] == "needs_review"
    stop2, *_ = screen(job, "stop", monkeypatch, ignored=True)
    assert stop2 is None


def test_off_mode_does_not_look_at_the_posting_at_all(job, monkeypatch):
    stop, me, d, row, logged = screen(job, "off", monkeypatch)
    assert stop is None and not (d / "eligibility.json").exists() and not logged


@pytest.mark.parametrize("mode,expected_blockers", [("warn", False), ("off", False), ("stop", True)])
def test_only_stop_mode_blocks_review_and_approval(tmp_path, monkeypatch, mode, expected_blockers):
    (tmp_path / "jd.txt").write_text("- US Citizenship required.")
    monkeypatch.setattr(eligibility, "assess_job", lambda _d: BLOCKED)
    monkeypatch.setattr(eligibility, "mode", lambda: mode)
    assert bool(eligibility.review_blockers(tmp_path)) is expected_blockers


def test_the_report_labels_a_warn_finding_as_a_warning_and_not_an_override():
    from jobagent.steps import apply as A
    result = {"url": "https://x", "state": "review", "ready_to_submit": True, "blockers": [], "fields": [], "verified": 0, "flags": [], "outcomes": {},
              "eligibility": {"level": "blocked", "mode": "warn", "override_applied": False, "reasons": BLOCKED.reasons}}
    text = A.report(result, {"company": "Acme", "title": "Engineer"})
    assert "warning only" in text and "explicit override" not in text and "U.S. citizenship" in text
