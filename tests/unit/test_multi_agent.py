"""Multi-agent validator: the Summary's own checks."""

from jobagent.steps import multi_agent as M


def _review_fixture():
    return M.T.Filled.model_construct(summary="Developer with cloud experience.", roles=[], notes="metadata",
        skills=[M.T.SkillLine(category="Databases", terms=["SAP BTP"])])


def test_semantic_review_is_bound_to_visible_content_and_retains_unchanged_findings(tmp_path, monkeypatch):
    import json
    f = _review_fixture(); calls = []
    st = {"kept": 1, "short": [], "ranked": [{"repo": "owner/project", "title": "Project", "stack": ["Python"],
          "paragraph": "Built an automation system.", "short": "Unused short description."}]}
    path = tmp_path / "projects.json"; path.write_text(json.dumps(st))

    def review(ctx, current):
        calls.append(current.skills[0].category)
        return [M.Finding(agent="skills", severity="error", issue="SAP BTP belongs under Platforms, not Databases")] if current.skills[0].category == "Databases" else []

    monkeypatch.setattr(M, "content_review", review)
    ctx = {"job_dir": tmp_path}
    first = M._reviewed_content(ctx, f, {})
    assert first["semantic_hard"]
    again = M._reviewed_content(ctx, f, first)
    assert again["semantic_hard"] == first["semantic_hard"] and len(calls) == 1
    f.notes = "Changed nonvisible metadata"
    st["ranked"][0]["short"] = "Another unused variant."; path.write_text(json.dumps(st))
    assert M._reviewed_content(ctx, f, again)["content_hash"] == first["content_hash"]
    assert len(calls) == 1
    f.skills[0].category = "Platforms"
    repaired = M._reviewed_content(ctx, f, again)
    assert repaired["semantic_hard"] == [] and len(calls) == 2
    st["ranked"][0]["paragraph"] = "Changed the visible description."; path.write_text(json.dumps(st))
    M._reviewed_content(ctx, f, repaired)
    assert len(calls) == 3


def test_unavailable_content_review_blocks_without_rewriting_or_spending_scans(tmp_path, monkeypatch):
    def unavailable(*args):
        raise M.llm.StructuredError("provider unavailable", "transient")
    monkeypatch.setattr(M, "content_review", unavailable)
    review = M._reviewed_content({"job_dir": tmp_path}, _review_fixture(), {})
    assert review["content_error"] and review["semantic_hard"]
    assert M.after_layout({**review, "tasks": {"skills": ["layout advice"]}, "layout_pass": 1}) == "score"
    assert M.after_score({**review, "ctx": {"sites": ("hireflow",)}}) == "done"


def test_category_refinements_are_advisory_and_padding_cannot_undo_an_error_repair(tmp_path, monkeypatch):
    suggestion = M.Finding(agent="skills", severity="suggestion", issue="A narrower AI heading could be used")
    monkeypatch.setattr(M, "content_review", lambda *a: [suggestion])
    review = M._reviewed_content({"job_dir": tmp_path}, _review_fixture(), {})
    assert review["semantic_hard"] == []
    assert M._skill_review_tasks(review, None, False)[0] == []
    assert M._skill_review_tasks(review, "Optional padding", False) == (["Optional padding"], True)
    error = M.Finding(agent="skills", severity="error", issue="Move a language out of Databases")
    tasks, attempted = M._skill_review_tasks({"content_findings": [error]}, "Optional padding", False)
    assert tasks == [error.issue] and attempted
    assert M._skill_review_tasks({"content_findings": []}, "Optional padding", attempted) == ([], True)


def test_review_requires_explicit_severity_and_never_pads_invalid_or_unreviewed_skills():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        M.Finding(agent="skills", issue="Unclassified finding")
    assert M._skill_review_tasks({}, "Optional padding", False, invalid=True) == ([], True)
    assert M._skill_review_tasks({"content_error": "Reviewer unavailable"}, "Optional padding", False)[0] == []


def test_review_context_and_fingerprint_agree_on_displayed_variants(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    f = _review_fixture()
    f.skills.append(M.T.SkillLine(category="Omitted empty category", terms=["Omitted empty category"]))
    st = {"kept": 1, "short": ["owner/project"], "ranked": [
        {"repo": "owner/project", "title": "Project", "stack": ["Python"],
         "paragraph": "Hidden full variant.", "short": "Displayed first sentence. Displayed second sentence."},
        {"repo": "owner/omitted", "title": "Omitted project", "paragraph": "Not selected."}]}
    (tmp_path / "projects.json").write_text(json.dumps(st))
    monkeypatch.setattr(M.T, "facts_block", lambda: "Owner facts")
    ctx = {"r": SimpleNamespace(skills=[], roles=[]), "job_dir": tmp_path}
    prompt = M._context(ctx, f, json.dumps(st), visible_only=True)
    assert "Displayed second sentence." in prompt and "short, two sentences" in prompt
    assert "Hidden full variant" not in prompt and "Omitted project" not in prompt
    assert "Omitted empty category" not in prompt
    fingerprint = M._content_hash(ctx, f)
    st["ranked"][0]["paragraph"] = "Another hidden variant."
    (tmp_path / "projects.json").write_text(json.dumps(st))
    f.skills[-1].category = "Another omitted category"
    f.skills[-1].terms = ["Another omitted category"]
    assert M._content_hash(ctx, f) == fingerprint
    st["ranked"][0]["short"] = "Changed visible text. Still two sentences."
    (tmp_path / "projects.json").write_text(json.dumps(st))
    assert M._content_hash(ctx, f) != fingerprint


def test_optional_non_skill_findings_do_not_trigger_automatic_rewrites(tmp_path, monkeypatch):
    from types import SimpleNamespace

    f = _review_fixture()
    ctx = {"r": SimpleNamespace(roles=[], skills=[]), "jd": None, "job_dir": tmp_path, "log": lambda _: None}
    monkeypatch.setattr(M, "render", lambda _: (f, "tex", [], 1, ""))
    monkeypatch.setattr(M.T, "fill_checks", lambda *a: ([], []))
    monkeypatch.setattr(M.T, "duplicates", lambda *a: [])
    monkeypatch.setattr(M.T, "quality", lambda *a: [])
    monkeypatch.setattr(M, "summary_issues", lambda *a: [])
    monkeypatch.setattr(M, "hard_issues", lambda *a: [])
    monkeypatch.setattr(M.layout, "check", lambda _: SimpleNamespace(issues=[], short_skills=False, free_pt=0))
    findings = [M.Finding(agent="experience", severity="suggestion", issue="Optional wording refinement"),
                M.Finding(agent="projects", severity="error", issue="Description contradicts repository evidence")]
    monkeypatch.setattr(M, "_reviewed_content", lambda *a: {"content_findings": findings, "semantic_hard": []})
    result = M.check_layout({"ctx": ctx, "f": f, "reviewed": True})
    assert result["tasks"] == {"projects": [findings[1].issue]}


def test_project_source_mismatch_blocks_and_each_edit_keeps_its_own_repository_context(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    f = _review_fixture()
    st = {"kept": 2, "short": [], "ranked": [
        {"repo": "owner/sandbox", "title": "Sandbox", "paragraph": "Provides report archetypes.", "stack": ["Python"]},
        {"repo": "owner/reports", "title": "Reports", "paragraph": "Provides report archetypes.", "stack": ["Python"]}]}
    (tmp_path / "projects.json").write_text(json.dumps(st))
    ctx = {"r": SimpleNamespace(skills=[], roles=[]), "job_dir": tmp_path,
           "cards": {"owner/sandbox": {"summary": "Isolated code execution in containers."},
                     "owner/reports": {"summary": "Report generation using archetypes."}}}
    monkeypatch.setattr(M.T, "facts_block", lambda: "Owner facts")
    prompt = M._context(ctx, f, json.dumps(st))
    assert 'repo=owner/sandbox; title=Sandbox' in prompt
    assert 'SOURCE FOR THIS REPO ONLY: Isolated code execution in containers.' in prompt
    assert 'SOURCE FOR THIS REPO ONLY: Report generation using archetypes.' in prompt
    monkeypatch.setattr(M, 'content_review', lambda *a: [M.Finding(agent='projects', severity='error',
        issue='Sandbox paragraph borrowed the report project features')])
    review = M._reviewed_content(ctx, f, {})
    assert review['semantic_hard'] == ['projects: Sandbox paragraph borrowed the report project features']


def test_known_semantic_defects_and_unreviewed_edits_never_consume_ats_scans(tmp_path, monkeypatch):
    import pytest
    f = _review_fixture();ctx = {"job_dir": tmp_path, "jd": None, "jd_text": "JD", "sites": ("hireflow",), "log": lambda _: None}
    monkeypatch.setattr(M, "render", lambda state: (f, "tex", [], 1, ""))
    monkeypatch.setattr(M, "hard_issues", lambda *args: [])
    monkeypatch.setattr(M.A, "evaluate", lambda *a: pytest.fail("An invalid document spent an ATS scan"))
    state = {"ctx": ctx, "semantic_hard": ["skills: wrong category"]}
    failed = M.score(state)
    assert failed["hard"] == ["skills: wrong category"] and "best" not in failed
    assert list(M.route({**state, **failed})["tasks"]) == ["skills"]
    assert M.after_score({**state, **failed, "ctx": {**ctx, "sites": ()}}) == "done"
    prior_hash = M._content_hash(ctx, f)
    f.skills[0].category = "Changed after validation"
    changed = M.score({"ctx": ctx, "content_hash": prior_hash})
    assert any("changed after" in problem for problem in changed["hard"])


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
