"""Step 4c: the skill graph (Role -> Duty -> Skill), its growth from postings, neighbour suggestions and the tailoring hooks."""
import json
from types import SimpleNamespace

from jobagent.config import get_settings
from jobagent.tools import skillgraph as SG
from jobagent.tools.skillgraph import DutyOut, SkillOut


def jd(title, skills, url=None, company="Acme"):
    return {"title": title, "company": company, "source_url": url or f"https://x.test/{title}", "technical_skills": skills,
            "responsibilities": ["r"], "preferred_qualifications": []}


def duty(name, *skills):
    return DutyOut(duty=name, skills=[SkillOut(name=s, category="framework") for s in skills])


PLAN = {  # what the fake planner model answers per posting title
    "ml1": [duty("model training", "PyTorch", "TensorFlow", "HuggingFace"), duty("model deployment and hosting", "Triton Inference Server", "vLLM")],
    "ml2": [duty("model training", "PyTorch", "JAX"), duty("databases", "PostgreSQL")],
    "swe1": [duty("databases", "PostgreSQL", "MongoDB"), duty("backend services", "FastAPI")],
}


def ask(j, known, skills):
    return PLAN[j["title"]]


ML1 = ["PyTorch", "TensorFlow", "HuggingFace", "Triton Inference Server", "vLLM"]
ML2 = ["PyTorch", "JAX", "PostgreSQL"]
SWE1 = ["PostgreSQL", "MongoDB", "FastAPI"]


def built():
    g = SG.Graph()
    SG.ingest(g, jd("ml1", ML1), "ml", ask=ask)
    SG.ingest(g, jd("ml2", ML2), "ml", ask=ask)
    SG.ingest(g, jd("swe1", SWE1), "swe", ask=ask)
    return g


def test_names_are_compared_by_one_spelling():
    assert SG.norm("PyTorch 2.1") == "pytorch" and SG.norm("torch") == "pytorch" and SG.norm("Postgres (v15)") == "postgresql"
    assert SG.clean_skills(["Python, Go", "go", "a very long sentence that is not a skill at all really"]) == ["Python", "Go"]


def test_a_posting_builds_role_duty_skill_and_a_skill_sits_under_several_duties():
    g = built()
    kinds = {k: len(g.of_kind(k)) for k in ("role", "duty", "skill")}
    assert kinds == {"role": 2, "duty": 4, "skill": 9}
    training = g.find("duty", "model training")
    names = {g.nodes[s]["name"] for s, _ in g.children(training)}
    assert {"PyTorch", "TensorFlow", "HuggingFace", "JAX"} == names
    assert g.edges[f"{training}>{g.find('skill', 'PyTorch')}"]["weight"] == 2            # two postings support it
    pg = g.find("skill", "PostgreSQL")
    assert {g.nodes[d]["name"] for d, _ in g.parents(pg)} == {"databases"} and {g.nodes[r]["name"] for r, _ in g.parents(g.find("duty", "databases"))} == {"ml", "swe"}


def test_ingesting_a_posting_twice_changes_nothing():
    g = built()
    h = g.content_hash()
    assert SG.ingest(g, jd("ml1", ML1), "ml", ask=ask)["skipped"] is True
    assert g.content_hash() == h


def test_skills_the_model_invents_are_ignored_and_unplaced_ones_go_to_general_tooling():
    g = SG.Graph()
    SG.ingest(g, jd("ml2", ML2 + ["Docker"]), "ml", ask=ask)                  # the plan names JAX etc.; Docker is listed but unplaced
    assert g.find("skill", "Docker") and {g.nodes[d]["name"] for d, _ in g.parents(g.find("skill", "Docker"))} == {SG.GENERAL_DUTY}
    PLAN["bad"] = [duty("model training", "PyTorch", "NotInThePosting")]
    SG.ingest(g, jd("bad", ["PyTorch"]), "ml", ask=ask)
    assert g.find("skill", "NotInThePosting") is None


def test_similar_names_merge_and_the_spelling_is_kept_as_an_alias():
    vec = {"PyTorch Lightning": [1, 0.1, 0], "Lightning for PyTorch": [1, 0.12, 0], "Kubernetes": [0, 0, 1]}
    embed = lambda texts: [vec.get(t, [0, 1, 0]) for t in texts]   # noqa: E731
    g = SG.Graph()
    PLAN["a"] = [duty("model training", "PyTorch Lightning")]
    PLAN["b"] = [duty("model training", "Lightning for PyTorch", "Kubernetes")]
    SG.ingest(g, jd("a", ["PyTorch Lightning"]), "ml", ask=ask, embed=embed)
    SG.ingest(g, jd("b", ["Lightning for PyTorch", "Kubernetes"]), "ml", ask=ask, embed=embed)
    assert len(g.of_kind("skill")) == 2 and "Lightning for PyTorch" in g.nodes[g.find("skill", "PyTorch Lightning")]["aliases"]


def test_suggestions_are_the_neighbours_under_shared_duties_split_by_ownership():
    g = built()
    res = SG.suggest(g, ["PyTorch"], "ml", owned={"jax", "vllm"}, on_resume={"tensorflow"})
    got = {x["skill"]: x for x in res["owned"] + res["review"]}
    assert "TensorFlow" not in got and "PyTorch" not in got                        # on the resume / named by the posting
    assert [x["skill"] for x in res["owned"]] == ["JAX"] and "HuggingFace" in [x["skill"] for x in res["review"]]
    assert got["JAX"]["via"] == "model training" and "vLLM" not in got            # deployment is a different duty than PyTorch's
    assert SG.suggest(g, ["Unknown tool"], "ml", set(), set()) == {"owned": [], "review": []}


def test_a_role_without_postings_is_reported_and_one_failed_posting_does_not_stop_the_rest():
    g = SG.Graph()
    def flaky(j, known, skills):
        if j["title"] == "ml2":
            raise RuntimeError("model down")
        return PLAN[j["title"]]
    logs = []
    stats = SG.build(g, [{"id": "ml", "title": "ML Engineer"}, {"id": "robotics", "title": "Robotics"}],
                     [(jd("ml1", ML1), "ml"), (jd("ml2", ML2), "ml")], ask=flaky, merge=None, log=logs.append)
    assert stats["postings"] == 1 and stats["failed"] == 1 and stats["roles_without_postings"] == ["robotics"]
    assert g.find("skill", "TensorFlow") and any("model down" in m for m in logs)
    assert SG.jd_key(jd("ml2", ML2)) not in g.postings                              # a rerun retries it


def test_duties_that_mean_the_same_work_are_consolidated():
    g = SG.Graph()
    PLAN["m1"] = [duty("model training", "PyTorch")]
    PLAN["m2"] = [duty("training machine learning models", "TensorFlow")]
    SG.ingest(g, jd("m1", ["PyTorch"]), "ml", ask=ask)
    SG.ingest(g, jd("m2", ["TensorFlow"]), "ml", ask=ask)
    n = SG.consolidate(g, lambda names: [SG.MergeGroup(keep="model training", merge=["training machine learning models", "nonsense"])])
    assert n == 1 and len(g.of_kind("duty")) == 1
    assert {g.nodes[s]["name"] for s, _ in g.children(g.find("duty", "model training"))} == {"PyTorch", "TensorFlow"}


def test_the_graph_is_versioned_and_saved_only_when_it_changed(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    g = built()
    assert SG.save(g, "first", None) and g.version == 1
    again = SG.load()
    before = again.content_hash()
    assert again.version == 1 and not SG.save(again, "nothing", before) and again.version == 1
    SG.ingest(again, jd("m1", ["Rust"]), "swe", ask=lambda *a: [duty("systems programming", "Rust")])
    assert SG.save(again, "grew", before) and SG.load().version == 2
    assert len((tmp_path / "skills" / "history.jsonl").read_text().splitlines()) == 2
    get_settings.cache_clear()


def test_the_diagram_export_follows_role_duty_skill():
    text = SG.mermaid(built(), role="swe")
    assert text.startswith("graph LR") and '"swe"' in text and '"databases"' in text and '"FastAPI"' in text and '"JAX"' not in text
    assert text.count("-->") == 5                                              # swe -> 2 duties, databases -> 2 skills, backend -> 1


def test_owned_skills_come_from_the_candidates_own_files_and_approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "master_profile.yaml").write_text("skills:\n  tools: [Docker, {name: Terraform, years: 2}]\n")
    cards = tmp_path / "state" / "github" / "cards"
    cards.mkdir(parents=True)
    (cards / "a.json").write_text(json.dumps({"tech_stack": ["Rust", "Postgres"]}))
    assert SG.approve("Kubernetes") and not SG.approve("kubernetes")
    owned = SG.owned_skills(["Python"])
    assert {"python", "docker", "terraform", "rust", "postgresql", "kubernetes"} <= owned and "java" not in owned
    get_settings.cache_clear()


def test_related_skills_the_candidate_lacks_never_reach_the_resume():
    res = {"owned": [{"skill": "JAX", "via": "model training", "score": 2}], "review": [{"skill": "HuggingFace", "via": "model training", "score": 1}]}
    text = SG.hint_text(res)
    assert "JAX (via model training)" in text and "do NOT add these: HuggingFace" in text
    from jobagent.tools.tailor import SkillLine
    lines = [SkillLine(category="ML", terms=["PyTorch", "HuggingFace", "JAX"]), SkillLine(category="Only", terms=["HuggingFace"])]
    kept, dropped = SG.drop_forbidden(lines, res)
    assert kept[0].terms == ["PyTorch", "JAX"] and kept[1].terms == ["HuggingFace"] and dropped == ["HuggingFace"]    # a line is never emptied


def test_suggestions_are_written_for_the_review_screen_and_switched_off_means_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    SG.save(built(), "test", None)
    job = tmp_path / "job"
    job.mkdir()
    monkeypatch.setenv("SKILLS_USE_GRAPH", "true")
    get_settings.cache_clear()
    res = SG.job_suggestions(job, jd("x", ["PyTorch"]), "ml", ["Python"])
    assert res["review"] and (job / "skill_suggestions.json").exists()
    assert any("HuggingFace" in line or "JAX" in line for line in SG.review_lines(job))
    monkeypatch.setenv("SKILLS_USE_GRAPH", "false")
    get_settings.cache_clear()
    assert SG.job_suggestions(job, jd("x", ["PyTorch"]), "ml", ["Python"]) == {}
    get_settings.cache_clear()


def test_a_graph_failure_never_stops_the_pipeline(tmp_path):
    from jobagent.tools.pipeline import Pipeline
    logs = []
    me = SimpleNamespace(settings=SimpleNamespace(skills_use_graph=True), log=logs.append)
    Pipeline._skill_graph(me, tmp_path, {"role_id": "ml"})                       # no jd.json in the folder
    assert logs and "graph not updated" in logs[0]
    off = SimpleNamespace(settings=SimpleNamespace(skills_use_graph=False), log=logs.append)
    Pipeline._skill_graph(off, tmp_path, {"role_id": "ml"})
    assert len(logs) == 1


def test_the_writers_context_carries_the_related_skills():
    from jobagent.steps import multi_agent as M
    r = SimpleNamespace(skills=[("Languages", "Python")], roles=[])
    ctx = {"r": r, "graph": {"owned": [{"skill": "JAX", "via": "model training", "score": 1}], "review": []}}
    assert "RELATED SKILLS" in M._context(ctx, None, None) and "JAX" in M._context(ctx, None, None)
    assert "RELATED SKILLS" not in M._context({"r": r}, None, None)
