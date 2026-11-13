import sqlite3
from types import SimpleNamespace

import pytest

from jobagent import db
from jobagent.tools import ats_accounts as accounts, ats_providers as providers, ats_sites


class ReportPage:
    def __init__(self, text, url="https://example.com/report", sections=None):
        self.text, self.url, self.sections = text, url, sections

    def locator(self, selector):
        assert selector == "body"
        return self

    def inner_text(self, **kwargs):
        return self.text

    def evaluate(self, script):
        return self.sections


def test_jobscan_reads_visible_skills_and_excludes_locked_names():
    page = ReportPage("Match Rate\n59\nResume Report", "https://app.jobscan.co/match-report/123", {
        "hardSkills": [
            {"name": "Upgrade to view", "locked": True, "missing": True},
            {"name": "SQL", "locked": False, "missing": True},
            {"name": "Python", "locked": False, "missing": False},
        ],
        "softSkills": [{"name": "Communication", "locked": False, "missing": True}],
    })
    result = providers.jobscan_result(page)
    assert result["score"] == 59
    assert result["missing"] == ["SQL"]
    assert result["breakdown"]["matched"] == ["Python"]
    assert result["breakdown"]["limited_feedback"]
    assert "Communication" in result["advice"][0]


def test_jobscan_does_not_read_an_old_dashboard_score():
    page = ReportPage("Match Rate\n41", "https://app.jobscan.co/dashboard")
    with pytest.raises(RuntimeError, match="not a completed match report"):
        providers.jobscan_result(page)


def test_jobscan_waits_for_the_animated_score_to_settle(monkeypatch):
    class AnimatedReport(ReportPage):
        clock = 0
        reads = 0

        def inner_text(self, **kwargs):
            score = [18, 27, 62][min(self.reads, 2)]
            self.reads += 1
            return f"Match Rate\n{score}\nResume Report\nSQL"

        def wait_for_timeout(self, milliseconds):
            self.clock += milliseconds / 1000

    page = AnimatedReport("", "https://app.jobscan.co/match-report/123")
    monkeypatch.setattr(providers.time, "monotonic", lambda: page.clock)
    providers._wait_jobscan_report(page)
    assert page.reads >= 6
    assert page.clock >= 3.4


def test_skillsyncer_distinguishes_missing_keywords_from_frequency_shortfalls():
    page = ReportPage("""Match Report
57
%
HARD SKILLS
3
Score
Resume
Job
Python
50%
1
2
SQL
0%
0
1
SOFT SKILLS
1
Score
Resume
Job
Communication
0%
0
1
OTHER KEYWORDS
1
Score
Resume
Job
Data
100%
3
2
Job Title Match
Great work
JOB DESCRIPTION
Match Report
100
%
""")
    result = providers.skillsyncer_result(page)
    assert result["score"] == 57
    assert result["missing"] == ["SQL"]
    assert result["breakdown"]["matched"] == ["Python", "Data"]
    assert result["breakdown"]["frequency_shortfalls"][0]["name"] == "Python"
    assert "Communication" in result["advice"][0]


def test_atsalign_uses_job_match_and_reports_partial_coverage():
    page = ReportPage("""Job Match
90
/100
Python: Critical, covered by your resume.
SQL: Important, not covered by your resume.
Machine Learning: Critical, partially covered by your resume.
View all skills
Could be stronger.
Built existing Python tooling
1 issue
No numbers yet
Edit
Rewrite with AI
""")
    result = providers.atsalign_result(page)
    assert result["score"] == 90
    assert result["missing"] == ["SQL", "Machine Learning"]
    assert result["breakdown"]["limited_feedback"]
    assert "Built existing Python tooling" in result["advice"][0]


@pytest.mark.parametrize("text,status", [
    ("Available scans: 0", "quota_exhausted"),
    ("No free scans left this week", "quota_exhausted"),
    ("You've used your free tries", "quota_exhausted"),
    ("2 of 2 credits used\nDepleted", "quota_exhausted"),
    ("Captcha verification failed. Please try again.", "human_required"),
])
def test_unavailable_scans_have_explicit_status(text, status):
    with pytest.raises(accounts.AutomationBlocked) as error:
        accounts.check_scan_blockers(text, "example")
    assert error.value.status == status


def test_empty_scan_history_is_not_an_exhausted_allowance():
    accounts.check_scan_blockers("No scans created\nScans:\n2\n0 scans in the last 6 months", "example")
    accounts.check_scan_blockers("1 of 2 credits used", "example")


def test_successful_last_scan_is_read_even_when_no_credits_remain():
    page = ReportPage("Available scans: 0\nMatch Rate\n59")
    result = providers._wait_report(page, "example", lambda text: "Match Rate" in text)
    assert "59" in result


@pytest.fixture
def score_store(tmp_path, monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    monkeypatch.setattr(db, "connect", lambda: conn)
    (tmp_path / "resume.pdf").write_bytes(b"PDF fixture")
    return tmp_path


def test_cached_score_requires_the_same_resume_jd_title_and_company(score_store):
    score = ats_sites.SiteScore("jobscan", 59, True, breakdown={
        "parser_version": 2,
        "job_fingerprint": ats_sites.job_fingerprint("JD one", "Engineer", "Acme"),
    })
    ats_sites.record(score_store, score.site, 0, score.score, score.missing, score.to_dict())
    pdf = score_store / "resume.pdf"
    assert ats_sites.cached(pdf, "jobscan", "JD one", "Engineer", "Acme").score == 59
    assert ats_sites.cached(pdf, "jobscan", "JD two", "Engineer", "Acme") is None
    assert ats_sites.cached(pdf, "jobscan", "JD one", "Scientist", "Acme") is None
    assert ats_sites.cached(pdf, "jobscan", "JD one", "Engineer", "Other") is None
    pdf.write_bytes(b"different PDF")
    assert ats_sites.cached(pdf, "jobscan", "JD one", "Engineer", "Acme") is None


def test_cache_hit_does_not_open_browser_or_spend_credit(score_store, monkeypatch):
    score = ats_sites.SiteScore("jobscan", 59, True, breakdown={
        "parser_version": 2,
        "job_fingerprint": ats_sites.job_fingerprint("JD", "Engineer", "Acme"),
    })
    ats_sites.record(score_store, score.site, 0, score.score, [], score.to_dict())

    def unexpected_browser():
        raise AssertionError("cached scans must not open a browser")

    monkeypatch.setattr(ats_sites, "persistent_context", unexpected_browser)
    result = ats_sites.score_all(
        score_store / "resume.pdf", "JD", sites=("jobscan",), title="Engineer", company="Acme",
    )
    assert result[0].score == 59


def test_quota_failure_is_not_a_zero_score(score_store, monkeypatch):
    class Context:
        def __enter__(self):
            return SimpleNamespace(pages=[None])

        def __exit__(self, *args):
            pass

    def blocked(*args):
        raise accounts.AutomationBlocked("no scan credits left", "quota_exhausted")

    monkeypatch.setattr(ats_sites, "persistent_context", Context)
    monkeypatch.setattr(ats_sites, "upload_name", lambda: "Test_Resume.pdf")
    monkeypatch.setitem(ats_sites.SITES, "jobscan", blocked)
    result = ats_sites.score_all(score_store / "resume.pdf", "JD", sites=("jobscan",))
    assert result[0].score is None
    assert result[0].job_specific
    assert result[0].breakdown["status"] == "quota_exhausted"


def test_cache_ignores_incomplete_legacy_rows(score_store):
    import hashlib

    pdf = score_store / "resume.pdf"
    score = ats_sites.SiteScore("jobscan", 59, True, breakdown={
        "parser_version": 2,
        "job_fingerprint": ats_sites.job_fingerprint("JD"),
    })
    ats_sites.record(score_store, "jobscan", 0, 59, [], score.to_dict())
    db.connect().execute(
        "INSERT INTO ats_runs (job_dir,site,round,score,raw,resume_sha) VALUES (?,?,?,?,?,?)",
        (str(score_store), "jobscan", 0, 80, None, hashlib.sha256(pdf.read_bytes()).hexdigest()),
    )
    assert ats_sites.cached(pdf, "jobscan", "JD").score == 59


def test_jobscan_cache_rejects_scores_read_before_animation_settled(score_store):
    score = ats_sites.SiteScore("jobscan", 18, True, breakdown={
        "job_fingerprint": ats_sites.job_fingerprint("JD"),
    })
    ats_sites.record(score_store, score.site, 0, score.score, [], score.to_dict())
    assert ats_sites.cached(score_store / "resume.pdf", "jobscan", "JD") is None


def test_unknown_provider_fails_before_opening_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(ats_sites, "persistent_context", lambda: pytest.fail("unexpected browser"))
    with pytest.raises(ValueError, match="unknown ATS sites"):
        ats_sites.score_all(tmp_path / "missing.pdf", "JD", sites=("unknown",))


def test_resumeatlas_uses_completed_report_and_only_skill_chips():
    from unittest.mock import Mock

    page, report = Mock(), Mock()
    page.url = "https://resumeatlas.io/check-resume-against-job-description"
    page.locator.side_effect = lambda selector: report if selector == ".ref-dash" else ReportPage("56% sample")
    report.locator.return_value.inner_text.return_value = "91%"
    report.inner_text.return_value = "Completed report; If you have this experience, add evidence."
    rows = {}
    for heading, chips in {
        "Missing from resume": ["SQL"],
        "Needs stronger evidence": ["RAG"],
        "Already demonstrated": ["Python"],
    }.items():
        row = Mock()
        row.locator.return_value.locator.return_value.all_inner_texts.return_value = chips
        rows[heading] = row
    report.get_by_text.side_effect = lambda label, exact: rows[label]
    result = providers.resumeatlas_result(page)
    assert result["score"] == 91
    assert result["missing"] == ["SQL", "RAG"]
    assert result["breakdown"]["matched"] == ["Python"]
    assert result["breakdown"]["parser_version"] == 2


def test_resumeatlas_cache_rejects_scores_from_sample_parser(score_store):
    fingerprint = ats_sites.job_fingerprint("JD")
    score = ats_sites.SiteScore("resumeatlas", 56, True, breakdown={"job_fingerprint": fingerprint})
    ats_sites.record(score_store, score.site, 0, score.score, [], score.to_dict())
    assert ats_sites.cached(score_store / "resume.pdf", "resumeatlas", "JD") is None
    score.score = 91
    score.breakdown["parser_version"] = 2
    ats_sites.record(score_store, score.site, 0, score.score, [], score.to_dict())
    assert ats_sites.cached(score_store / "resume.pdf", "resumeatlas", "JD").score == 91


def test_five_job_specific_scorers_are_the_default_average():
    assert ats_sites.AVERAGE_SITES == ("autoapplymax", "hireflow", "jobscan", "atsalign", "atsfriendly")
    assert ats_sites.numeric_sites(ats_sites.DEFAULT_SITES) == ats_sites.AVERAGE_SITES
    assert "skillsfirst" in ats_sites.DEFAULT_SITES
    assert "applycove" not in ats_sites.DEFAULT_SITES
    assert "applycove" in ats_sites.SITES
    assert "novoresume" in ats_sites.SITES


def test_novoresume_profile_uses_dated_roles_instead_of_summary_claim(tmp_path, monkeypatch):
    from jobagent.tools import applicant

    pdf = tmp_path / "resume.pdf"
    pdf.with_suffix(".tex").write_text("dated resume source")
    monkeypatch.setattr(applicant, "answer", lambda question: {
        "Current address": {"country": "United States"}, "Years of relevant experience": 5,
    }.get(question))
    monkeypatch.setattr(applicant, "resume_experience", lambda tex: {"roles": ["role"], "years": 2.4})
    assert providers._novoresume_profile(pdf) == ("United States of America", "Graduate")


def test_scoring_keeps_resume_source_for_matcher_profile(score_store, monkeypatch):
    source = score_store / "resume.tex"
    source.write_text("dated resume source")

    class Context:
        def __enter__(self):
            return SimpleNamespace(pages=[None])

        def __exit__(self, *args):
            pass

    def scorer(page, pdf, jd, title, company):
        assert Path(pdf).with_suffix(".tex").read_text() == source.read_text()
        return ats_sites.SiteScore("novoresume", 78, True, advice=["Show existing SQL work"])

    from pathlib import Path
    monkeypatch.setattr(ats_sites, "persistent_context", Context)
    monkeypatch.setattr(ats_sites, "upload_name", lambda: "Test_Resume.pdf")
    monkeypatch.setitem(ats_sites.SITES, "novoresume", scorer)
    result = ats_sites.score_all(score_store / "resume.pdf", "JD", sites=("novoresume",))
    assert result[0].score == 78
    assert result[0].advice == ["Show existing SQL work"]
