"""F20: what each checker was sent, the raw repeated observations, and the version of the measurement."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from jobagent import db
from jobagent.tools import ats_accounts as accounts
from jobagent.tools import ats_result, ats_sites


@pytest.fixture
def store(tmp_path, monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    monkeypatch.setattr(db, "connect", lambda: conn)
    (tmp_path / "resume.pdf").write_bytes(b"PDF fixture")
    (tmp_path / "jd.txt").write_text("posting")
    (tmp_path / "jd.json").write_text(json.dumps({"title": "Engineer", "company": "Acme"}))
    monkeypatch.setattr(ats_sites, "upload_name", lambda: "Test_Resume.pdf")

    class Context:
        def __enter__(self):
            return SimpleNamespace(pages=[None])

        def __exit__(self, *a):
            pass
    monkeypatch.setattr(ats_sites, "persistent_context", Context)
    return tmp_path


def fake_site(monkeypatch, name, outcomes):
    """A provider that returns/raises the scripted outcomes in order and records the JD it was asked about."""
    calls = []

    def adapter(pg, pdf, jd_text, title="", company=""):
        calls.append(jd_text)
        out = outcomes[len(calls) - 1]
        if isinstance(out, Exception):
            raise out
        ats_sites.jd_payload(name, jd_text)                       # what a real adapter does when it types the posting
        return ats_sites.SiteScore(name, out, True, ["kw"], {"parsed": "ok"}, ["advice"])
    monkeypatch.setitem(ats_sites.SITES, name, adapter)
    return calls


# --- what was sent --------------------------------------------------------------------------------------------------------

def test_a_long_posting_is_cut_for_a_provider_with_a_limit_and_the_cut_is_recorded():
    long = "x" * 12_000
    sent = ats_sites.jd_payload("autoapplymax", long)
    rec = ats_sites._payload.last
    assert len(sent) == 8000 and rec["truncated"] and rec["jd_chars_total"] == 12_000 and rec["jd_chars_sent"] == 8000
    assert rec["limit"] == 8000 and rec["jd_sha256"] != rec["sent_sha256"]
    short = ats_sites.jd_payload("autoapplymax", "short posting")
    assert short == "short posting" and not ats_sites._payload.last["truncated"]
    assert ats_sites.jd_payload("jobscan", long) == long and ats_sites._payload.last["limit"] is None   # no limit: all of it


def test_the_payload_is_stored_with_the_score_and_surfaced_in_the_summary(store, monkeypatch):
    fake_site(monkeypatch, "autoapplymax", [71.0])
    res, = ats_sites.score_all(store / "resume.pdf", "y" * 9000, sites=("autoapplymax",), title="Engineer", company="Acme")
    p = res.breakdown["payload"]
    assert p["truncated"] and p["jd_chars_sent"] == 8000 and res.breakdown["provider_version"] == 1
    ats_sites.record(store, res.site, 0, res.score, res.missing, res.to_dict())
    g = ats_result.gather(store, ("autoapplymax",))
    assert g["truncated_posting"]["autoapplymax"]["jd_chars_total"] == 9000
    assert g["provenance"]["autoapplymax"]["payload"]["truncated"]


# --- repeated observations ----------------------------------------------------------------------------------------------

def test_a_real_written_report_survives_observation_and_reaches_the_writer(store, monkeypatch):
    from jobagent.steps import ats
    from jobagent.state import JobDescription
    report = "Skills Gaps:\n1. Explain the existing project's measurable impact."
    recommendation = "Describe existing project impact without inventing results."
    monkeypatch.setitem(ats_sites.SITES, "skillsfirst", lambda *a: ats_sites.SiteScore(
        "skillsfirst", None, True, breakdown={"feedback_only": True, "report": report}, advice=[recommendation],
    ))
    feedback = ats.evaluate(store, JobDescription(title="Engineer", company="Acme"), "JD", 0, ("skillsfirst",))
    assert feedback["completed"] == ["skillsfirst"]
    assert feedback["scores"] == {}  # written feedback never becomes a numeric score
    assert recommendation in feedback["advice"]
    raw = json.loads(db.connect().execute("SELECT raw FROM ats_runs").fetchone()[0])
    assert raw["breakdown"]["report"] == report
    assert raw["breakdown"]["observations"][0]["ok"] is True


@pytest.mark.parametrize("report", [None, "", "   "])
def test_an_empty_written_report_does_not_count_as_completed(store, monkeypatch, report):
    monkeypatch.setitem(ats_sites.SITES, "skillsfirst", lambda *a: ats_sites.SiteScore(
        "skillsfirst", None, True, breakdown={"feedback_only": True, "report": report},
    ))
    result, = ats_sites.score_all(store / "resume.pdf", "JD", sites=("skillsfirst",))
    assert not result.breakdown.get("feedback_only")
    assert result.breakdown["status"] == "incomplete"
    assert result.breakdown["observations"][0]["ok"] is False

def test_repeated_scores_are_kept_unaltered_next_to_their_average(store, monkeypatch):
    monkeypatch.setitem(ats_sites.REPEATS, "hireflow", 2)
    fake_site(monkeypatch, "hireflow", [60.0, 80.0])
    res, = ats_sites.score_all(store / "resume.pdf", "JD", sites=("hireflow",), title="Engineer", company="Acme")
    assert res.score == 70.0 and res.breakdown["runs"] == [60.0, 80.0]
    obs = res.breakdown["observations"]
    assert [o["result"]["score"] for o in obs] == [60.0, 80.0]               # the raw scores, not 70 and 80
    assert res.breakdown["aggregate"] == {"method": "mean", "runs": 2}
    ats_sites.record(store, res.site, 0, res.score, res.missing, res.to_dict())
    raw = json.loads(db.connect().execute("SELECT raw FROM ats_runs").fetchone()[0])
    assert [o["result"]["score"] for o in raw["breakdown"]["observations"]] == [60.0, 80.0]


def test_a_failed_repeat_leaves_the_site_without_a_score_and_keeps_what_succeeded(store, monkeypatch):
    monkeypatch.setitem(ats_sites.REPEATS, "hireflow", 2)
    fake_site(monkeypatch, "hireflow", [60.0, RuntimeError("page changed")])
    res, = ats_sites.score_all(store / "resume.pdf", "JD", sites=("hireflow",))
    assert res.score is None and "1 of 2" in res.breakdown["incomplete"]        # not "60": a one-run mean is another measurement
    assert [o["ok"] for o in res.breakdown["observations"]] == [True, False]
    assert res.breakdown["observations"][0]["result"]["score"] == 60.0 and "page changed" in res.breakdown["error"]
    # and the required five-site average stays incomplete (the denominator is never reduced)
    s = ats_sites.score_summary({n: 90 for n in ats_sites.AVERAGE_SITES if n != "hireflow"})
    assert s["average"] is None and s["missing"] == ["hireflow"]


def test_an_exhausted_allowance_stops_at_once_and_is_a_clear_state(store, monkeypatch):
    monkeypatch.setitem(ats_sites.REPEATS, "hireflow", 3)
    calls = fake_site(monkeypatch, "hireflow", [accounts.AutomationBlocked("no credits", "quota_exhausted")] * 3)
    res, = ats_sites.score_all(store / "resume.pdf", "JD", sites=("hireflow",))
    assert len(calls) == 1 and res.score is None and res.breakdown["status"] == "quota_exhausted"


# --- versions -------------------------------------------------------------------------------------------------------------

def record_row(store, site, score, **breakdown):
    sc = ats_sites.SiteScore(site, score, True, breakdown={"job_fingerprint": ats_sites.job_fingerprint("JD", "Engineer", "Acme"),
                                                           **breakdown})
    ats_sites.record(store, site, 0, score, [], sc.to_dict())


def test_a_score_made_by_another_provider_version_is_not_reused(store):
    pdf = store / "resume.pdf"
    record_row(store, "jobscan", 59, provider_version=1)                         # an older parser
    assert ats_sites.cached(pdf, "jobscan", "JD", "Engineer", "Acme") is None
    record_row(store, "jobscan", 61, provider_version=ats_sites.provider_version("jobscan"))
    assert ats_sites.cached(pdf, "jobscan", "JD", "Engineer", "Acme").score == 61


def test_legacy_rows_are_reused_only_by_providers_still_at_their_first_version(store):
    pdf = store / "resume.pdf"
    record_row(store, "atsfriendly", 77)                                          # no version recorded, provider unchanged since
    assert ats_sites.cached(pdf, "atsfriendly", "JD", "Engineer", "Acme").score == 77
    record_row(store, "jobscan", 59)                                              # no version, but jobscan has moved to version 2
    assert ats_sites.cached(pdf, "jobscan", "JD", "Engineer", "Acme") is None
    record_row(store, "jobscan", 58, parser_version=2)                            # the old key for the same thing still works
    assert ats_sites.cached(pdf, "jobscan", "JD", "Engineer", "Acme").score == 58


def test_a_version_bump_invalidates_cached_results(store, monkeypatch):
    pdf = store / "resume.pdf"
    record_row(store, "atsfriendly", 77, provider_version=1)
    assert ats_sites.cached(pdf, "atsfriendly", "JD", "Engineer", "Acme")
    monkeypatch.setitem(ats_sites.PROVIDER_VERSIONS, "atsfriendly", 2)           # the adapter changed
    assert ats_sites.cached(pdf, "atsfriendly", "JD", "Engineer", "Acme") is None
