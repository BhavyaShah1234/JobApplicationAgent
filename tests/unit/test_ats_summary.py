import hashlib
import json

import pytest
from typer.testing import CliRunner

from jobagent.main import app
from jobagent.tools import ats_sites


SCORES = {"autoapplymax": 76, "hireflow": 53.5, "skillsyncer": 81, "atsalign": 85, "atsfriendly": 47}


def test_five_site_mean_excludes_other_sites():
    summary = ats_sites.score_summary({**SCORES, "jobalytics": 100, "resumeatlas": 100, "jobscan": 100, "skillsfirst": None})
    assert summary["average"] == 68.5
    assert summary["complete"]
    assert summary["scores"] == SCORES


@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf"), True, -1, 101])
def test_incomplete_scores_do_not_change_the_denominator(invalid):
    summary = ats_sites.score_summary({**SCORES, "skillsyncer": invalid})
    assert summary["average"] is None
    assert not summary["complete"]
    assert summary["missing"] == ["skillsyncer"]


def test_zero_is_a_valid_score_and_duplicate_sites_do_not_count_twice():
    summary = ats_sites.score_summary({"skillsyncer": 0}, ("skillsyncer", "skillsyncer", "skillsfirst"))
    assert summary["average"] == 0
    assert summary["sites"] == ["skillsyncer"]
    assert summary["complete"]


@pytest.fixture
def job(tmp_path):
    (tmp_path / "resume.pdf").write_bytes(b"generated PDF fixture")
    (tmp_path / "jd.txt").write_text("Original job description")
    (tmp_path / "jd.json").write_text(json.dumps({"title": "Engineer", "company": "Acme"}))
    return tmp_path


@pytest.mark.parametrize("failed", [None, "skillsyncer"])
def test_cli_reports_and_persists_complete_or_incomplete_mean(job, monkeypatch, failed):
    requested = []

    def score_all(pdf, jd_text, **kwargs):
        requested.extend(kwargs["sites"])
        return [ats_sites.SiteScore(name, value if name != failed else None, True)
                for name, value in SCORES.items()] + [ats_sites.SiteScore(
                    "skillsfirst", None, True, breakdown={"feedback_only": True}, advice=["Explain existing impact"],
                )]

    monkeypatch.setattr(ats_sites, "score_all", score_all)
    monkeypatch.setattr(ats_sites, "record", lambda *args: None)
    result = CliRunner().invoke(app, ["ats", str(job)])
    assert result.exit_code == 0, result.output
    assert tuple(requested) == ats_sites.DEFAULT_SITES
    summary = json.loads((job / "ats_summary.json").read_text())
    assert summary["resume_sha256"] == hashlib.sha256((job / "resume.pdf").read_bytes()).hexdigest()
    assert summary["job_fingerprint"] == ats_sites.job_fingerprint("Original job description", "Engineer", "Acme")
    if failed:
        assert summary["average"] is None
        assert "ATS average incomplete (4/5 sites)" in result.output
        assert summary["missing"] == [failed]
    else:
        assert summary["average"] == 68.5
        assert "ATS average (5/5 sites): 68.5/100" in result.output
    assert "written feedback" in result.output


def test_cli_discloses_partial_posting_scope_with_the_average(job, monkeypatch):
    monkeypatch.setattr(ats_sites, 'score_all', lambda *a, **kw: [])
    monkeypatch.setattr(ats_sites, 'write_summary', lambda *a, **kw: {
        'scores': SCORES, 'sites': list(SCORES), 'average': 68.5, 'complete': True,
        'truncated_posting': {'skillsyncer': {'word_limit': 650, 'jd_words_sent': 650, 'jd_words_total': 948}},
    })
    result = CliRunner().invoke(app, ['ats', str(job)])
    assert result.exit_code == 0
    assert 'skillsyncer 650/948 words' in result.output


@pytest.mark.parametrize("failed", [None, "skillsyncer"])
def test_single_writer_final_average_requires_every_selected_site(job, monkeypatch, failed):
    from types import SimpleNamespace
    from jobagent.steps import ats

    (job / "templates").mkdir()
    (job / "templates/resume.tex").write_text("template")
    (job / "filled.json").write_text(json.dumps({
        "summary": "Engineer", "notes": "Fixture",
        "roles": [{"role": 0, "bullets": [{"text": f"Built project {i}", "duties": [0]} for i in range(4)]}],
        "skills": [{"category": category, "terms": [term]}
                   for category, term in (("Languages", "Python"), ("Data", "SQL"), ("Cloud", "AWS"))],
    }))
    monkeypatch.setattr(ats, "get_settings", lambda: SimpleNamespace(data_dir=job))
    monkeypatch.setattr(ats.T, "parse", lambda text: None)
    monkeypatch.setattr(ats.llm, "writer", lambda *args, **kwargs: None)
    monkeypatch.setattr(ats.layout, "check", lambda path: SimpleNamespace(issues=[]))
    monkeypatch.setattr(ats, "present", lambda *args: 0)
    monkeypatch.setattr(ats_sites, "manual_scores", lambda path: {"jobalytics": 100})
    scores = {name: 99 for name in ats_sites.AVERAGE_SITES if name != failed}
    monkeypatch.setattr(ats, "evaluate", lambda *args: {
        "scores": scores, "missing": {}, "advice": [], "advice_by_site": {}, "template": [], "keep": [],
    })
    result = ats.run(job, "acme", rounds=0, rewrite_letter=False, log=lambda message: None)
    assert result["average"] == (99 if failed is None else None)
    assert result["reached"] == (failed is None)
    assert "jobalytics" not in result["scores"]
    assert result["average_missing"] == ([] if failed is None else [failed])


@pytest.mark.parametrize("failed", [None, "skillsyncer"])
def test_multi_agent_final_average_drops_a_stale_limited_score(job, monkeypatch, failed):
    from types import SimpleNamespace
    from jobagent.steps import multi_agent as multi

    (job / "companies").mkdir()
    (job / "companies/acme.md").write_text("Company research")
    (job / "templates").mkdir()
    (job / "templates/resume.tex").write_text("template")
    monkeypatch.setattr(multi, "get_settings", lambda: SimpleNamespace(data_dir=job, state_dir=job))
    # This graph stub exercises score aggregation, not TeX compilation. Real
    # template preflight failures are covered in test_template_preflight.py.
    from jobagent.tools import documents
    monkeypatch.setattr(documents, "validate_template", lambda *args, **kwargs: None)
    monkeypatch.setattr(multi.T, "parse", lambda text: None)
    monkeypatch.setattr(multi.layout, "check", lambda path: SimpleNamespace(issues=[]))
    monkeypatch.setattr(multi.layout, "page_count", lambda path: 1)
    best = {"tex": "final resume", "pdf": b"final PDF", "f": SimpleNamespace(model_dump_json=lambda **kwargs: "{}"),
            "scores": {name: 99 for name in ats_sites.AVERAGE_SITES}, "hard": []}
    monkeypatch.setattr(multi, "build_graph", lambda: SimpleNamespace(invoke=lambda *args: {
        "best": best, "history": ["ats 99"], "ats_round": 1,
    }))
    monkeypatch.setattr(multi.A, "evaluate", lambda *args: {
        "scores": {name: 99 for name in ats_sites.AVERAGE_SITES
                   if name in ats_sites.BUDGET_SITES and name != failed},
    })
    result = multi.run_job(job, "acme", {}, log=lambda message: None)
    assert result["average"] == (99 if failed is None else None)
    assert result["reached"] == (failed is None)
    assert result["average_missing"] == ([] if failed is None else [failed])
    assert json.loads((job / "ats_summary.json").read_text())["resume_sha256"] == hashlib.sha256(b"final PDF").hexdigest()
