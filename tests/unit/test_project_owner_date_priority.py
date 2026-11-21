"""Manual code-review regression: the owner's project date is not the repository creation date."""
from jobagent.tools import projects as P


def test_an_explicit_owner_project_date_takes_priority_over_repository_timestamps():
    source = {"full_name": "owner/project", "title": "Project", "created_at": "2026-01-01T00:00:00Z",
              "pushed_at": "2026-02-01T00:00:00Z", "summary": "Built a Python event service for reviewing timestamped sensor observations.",
              "tech_stack": ["Python"]}
    entry = {"repo": source["full_name"], "title": "Project", "stack": ["Python"],
             "paragraph": source["summary"], "short": source["summary"]}
    state = {"ranked": [entry], "kept": 1, "short": [], "years": {source["full_name"]: "January 2026"},
             "evidence": {source["full_name"]: P.evidence_snapshot(source)}}
    owner = {source["full_name"]: ("Project", "May 2024")}
    assert any("date" in issue for issue in P.binding_findings(state, owner))
    P.recover_years(state, owner)
    assert state["years"][source["full_name"]] == "May 2024"
