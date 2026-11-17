"""Multi-agent validator: the Summary's own checks."""

from jobagent.steps import multi_agent as M


def test_long_copied_summary_is_flagged():
    s = ("Software Engineer I, Entry-Level, as a recent graduate with a degree in Computer Science or related "
         "discipline, with 1 year of experience, having completed two internships, possessing skills in languages "
         "such as Python, Java, or Kotlin. " * 2)
    issues = M.summary_issues(s, overflow=False)
    assert any("characters" in i for i in issues) and any("copies" in i for i in issues)


def test_good_summary_passes():
    s = ("Software engineer with 5+ years building Python and Kafka backends for real-time detection. Shipped "
         "LLM tooling with LangGraph and RAG that cut review time by 35%.")
    assert M.summary_issues(s, overflow=False) == []


def test_stalled_project_repair_uses_a_short_call_budget_and_keeps_the_previous_version(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from jobagent import llm

    (tmp_path / "projects.json").write_text("{}")
    monkeypatch.setattr(M, "_context", lambda *a: "Existing project descriptions")
    fake = SimpleNamespace(model_name="fixture", with_structured_output=lambda *a, **kw: SimpleNamespace(invoke=lambda _: None))
    monkeypatch.setattr(llm, "writer", lambda *a, **kw: fake)
    monkeypatch.setattr(llm, "get_settings", lambda: SimpleNamespace(
        llm_structured_method="function_calling", llm_call_deadline_s=900, llm_max_calls=8, llm_rate_retries=3,
    ))
    budgets = []

    def stalled_request(fn, remaining):
        budgets.append(remaining)
        raise llm.StructuredError("simulated provider exceeded the caller budget", "deadline")

    monkeypatch.setattr(llm, "_bounded", stalled_request)
    result = M._run_agent({"job_dir": tmp_path, "models": {"projects": "fixture"}, "log": lambda _: None},
                          "projects", None, ["Repair an unfinished description"], "")
    assert result == ("projects", None)  # _merge keeps the previous content, whose hard gate still applies
    assert len(budgets) == 1 and 0 < budgets[0] <= 180


def test_scoring_retries_only_failed_site_and_keeps_written_feedback(tmp_path, monkeypatch):
    from types import SimpleNamespace

    filled = SimpleNamespace(model_copy=lambda **kwargs: "filled snapshot")
    monkeypatch.setattr(M, "render", lambda state: (filled, "", [], 1, ""))
    monkeypatch.setattr(M.layout, "check", lambda path: SimpleNamespace(issues=[]))
    monkeypatch.setattr(M, "hard_issues", lambda ctx, fields: [])
    (tmp_path / "resume.tex").write_text("resume fixture")
    (tmp_path / "resume.pdf").write_bytes(b"resume fixture")
    calls = []

    def evaluate(job_dir, jd, jd_text, rnd, sites, log):
        calls.append(tuple(sites))
        feedback = {
            "scores": {}, "missing": {}, "advice": [],
            "template": [], "keep": [], "completed": [],
        }
        if len(calls) == 1:
            feedback["completed"] = ["skillsfirst"]
            feedback["advice"] = ["Explain existing projects' measurable impact"]
        else:
            feedback["completed"] = ["hireflow"]
            feedback["scores"] = {"hireflow": 81.0}
        return feedback

    monkeypatch.setattr(M.A, "evaluate", evaluate)
    state = {"ctx": {
        "job_dir": tmp_path, "jd": None, "jd_text": "job description",
        "sites": ("skillsfirst", "hireflow"), "log": lambda message: None,
    }}

    result = M.score(state)

    assert calls == [("skillsfirst", "hireflow"), ("hireflow",)]
    assert result["scores"] == {"hireflow": 81.0}
    assert result["best"]["avg"] == 81.0
    assert result["best"]["ev"]["advice"] == [
        "Explain existing projects' measurable impact",
    ]


def test_limited_scorers_run_initially_and_their_feedback_survives_refinement(tmp_path, monkeypatch):
    from types import SimpleNamespace

    filled = SimpleNamespace(model_copy=lambda **kwargs: "filled snapshot")
    monkeypatch.setattr(M, "render", lambda state: (filled, "", [], 1, ""))
    monkeypatch.setattr(M.layout, "check", lambda path: SimpleNamespace(issues=[]))
    monkeypatch.setattr(M, "hard_issues", lambda ctx, fields: [])
    (tmp_path / "resume.tex").write_text("resume fixture")
    (tmp_path / "resume.pdf").write_bytes(b"resume fixture")
    calls = []

    def evaluate(job_dir, jd, jd_text, rnd, sites, log):
        calls.append(tuple(sites))
        feedback = {
            "scores": {"hireflow": 70.0 + rnd}, "missing": {}, "advice": [],
            "advice_by_site": {}, "template": [], "keep": [], "completed": list(sites),
        }
        if "jobscan" in sites:
            feedback["scores"]["jobscan"] = 99.0
            feedback["missing"]["jobscan"] = ["SQL"]
            feedback["advice"] = ["Explain existing impact"]
            feedback["advice_by_site"] = {"jobscan": ["Explain existing impact"]}
        return feedback

    monkeypatch.setattr(M.A, "evaluate", evaluate)
    ctx = {
        "job_dir": tmp_path, "jd": None, "jd_text": "job description",
        "sites": ("hireflow", "jobscan"), "log": lambda message: None,
    }
    first = M.score({"ctx": ctx, "ats_round": 0})
    assert first["best"]["avg"] == 70.0  # never compare a new PDF against a stale budget-site average
    second = M.score({"ctx": ctx, **first})
    assert calls == [("hireflow", "jobscan"), ("hireflow",)]
    assert second["best"]["avg"] == 71.0
    assert second["best"]["ev"]["missing"]["jobscan"] == ["SQL"]
    assert "Explain existing impact" in second["best"]["ev"]["advice"]


def test_optional_scorer_needing_login_does_not_reject_good_resume(tmp_path, monkeypatch):
    from types import SimpleNamespace

    filled = SimpleNamespace(model_copy=lambda **kwargs: "filled snapshot")
    monkeypatch.setattr(M, "render", lambda state: (filled, "", [], 1, ""))
    monkeypatch.setattr(M.layout, "check", lambda path: SimpleNamespace(issues=[]))
    monkeypatch.setattr(M, "hard_issues", lambda ctx, fields: [])
    (tmp_path / "resume.tex").write_text("resume fixture")
    (tmp_path / "resume.pdf").write_bytes(b"resume fixture")
    calls = []

    def evaluate(*args):
        calls.append(args[4])
        return {
            "scores": {"hireflow": 70.0}, "missing": {}, "advice": [],
            "template": [], "keep": [], "completed": ["hireflow"],
        }

    monkeypatch.setattr(M.A, "evaluate", evaluate)
    result = M.score({"ctx": {
        "job_dir": tmp_path, "jd": None, "jd_text": "JD",
        "sites": ("hireflow", "jobscan"), "log": lambda message: None,
    }})
    assert len(calls) == 1
    assert result["best"]["avg"] == 70.0
