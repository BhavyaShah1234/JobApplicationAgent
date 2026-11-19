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


def test_close_technologies_stay_distinct_skills_and_never_share_ownership():
    PLAN["a"] = [duty("model training", "TensorFlow")]
    PLAN["b"] = [duty("model training", "PyTorch")]
    close = {"TensorFlow": [1, 0.1], "PyTorch": [1, 0.12]}
    embed = lambda texts: [close.get(t, [0, 1]) for t in texts]   # noqa: E731
    g = SG.Graph()
    SG.ingest(g, jd("a", ["TensorFlow"]), "ml", ask=ask, embed=embed)
    SG.ingest(g, jd("b", ["PyTorch"]), "ml", ask=ask, embed=embed)
    assert len(g.of_kind("skill")) == 2 and not g.nodes[g.find("skill", "TensorFlow")]["aliases"]
    res = SG.suggest(g, ["PyTorch"], "ml", owned={"pytorch"}, on_resume=set())
    assert [x["skill"] for x in res["review"]] == ["TensorFlow"] and res["owned"] == []                    # G02


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
    res = {"owned": [{"skill": "JAX", "via": "model training", "score": 2}],
           "review": [{"skill": "HuggingFace", "via": "model training", "score": 1, "aliases": ["hf"]}]}
    text = SG.hint_text(res)
    assert "JAX (via model training)" in text and "do NOT add these: HuggingFace" in text
    from jobagent.tools.tailor import SkillLine, ToolsFor
    lines = [SkillLine(category="ML", terms=["PyTorch", "HuggingFace", "JAX"]), SkillLine(category="Only", terms=["HF"])]
    kept, dropped = SG.enforce(lines, SG.forbidden_set(res))
    assert [x.terms for x in kept] == [["PyTorch", "JAX"]] and dropped == ["HuggingFace", "HF"]              # an emptied line is removed (G01)
    tools = [SkillLine(category="ML", terms=["Deep Learning (PyTorch, HuggingFace)", "NLP"], tools_for=[ToolsFor(term="NLP", tools=["HuggingFace", "spaCy"])])]
    kept, dropped = SG.enforce(tools, SG.forbidden_set(res))
    assert kept[0].terms == ["Deep Learning (PyTorch)", "NLP"] and kept[0].tools_for[0].tools == ["spaCy"] and dropped.count("HuggingFace") == 2


def test_suggestions_are_written_for_the_review_screen_and_switched_off_means_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    SG.save(built(), "test", None)
    job = tmp_path / "job"
    job.mkdir()
    posting = jd("x", ["PyTorch"])
    (job / "jd.json").write_text(json.dumps(posting))
    monkeypatch.setenv("SKILLS_USE_GRAPH", "true")
    get_settings.cache_clear()
    res = SG.job_suggestions(job, posting, "ml", ["Python"])
    assert res["review"] and (job / "skill_suggestions.json").exists()
    assert any("HuggingFace" in line or "JAX" in line for line in SG.review_lines(job))
    (job / "jd.json").write_text(json.dumps(jd("x", ["PyTorch", "Rust"])))                       # the posting changed: old suggestions are stale (G07)
    assert SG.review_lines(job) == []
    monkeypatch.setenv("SKILLS_USE_GRAPH", "false")
    get_settings.cache_clear()
    assert SG.job_suggestions(job, posting, "ml", ["Python"]) == {} and not (job / "skill_suggestions.json").exists()
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


def test_duties_are_short_kinds_of_work_and_sentences_are_refused():
    assert SG.valid_duty("model training") and SG.valid_duty("model deployment and hosting")
    assert not SG.valid_duty("develop and implement scalable and efficient modeling solutions for products")
    g = SG.Graph()
    SG.ingest(g, jd("sent", ["PyTorch"]), "ml", ask=lambda *a: [duty("architect, develop, and maintain high-performance scalable services", "PyTorch")])
    assert [n["name"] for n in g.of_kind("duty")] == [SG.GENERAL_DUTY]                      # the sentence is not a duty; the skill is kept


def test_generic_phrases_are_not_skills():
    got = SG.clean_skills(["Python", "Different backend languages", "optimization techniques", "predictive models", "Kubernetes", "machine learning",
                           "Spring Boot", "relational databases", "object-oriented programming"])
    assert got == ["Python", "Kubernetes", "machine learning", "Spring Boot"]


def test_a_posting_with_no_recorded_role_is_placed_by_title_then_by_similarity():
    roles = [{"id": "ml", "title": "Machine Learning Engineer", "patterns": ["(?i)machine learning"], "description": "models"},
             {"id": "swe", "title": "Software Engineer", "patterns": [], "description": "backend services and apis"}]
    assert SG.role_for({"title": "Staff Machine Learning Engineer"}, roles) == "ml"
    vec = {"Cloud Solutions Engineer. ": [0, 1], "Machine Learning Engineer. models": [1, 0], "Software Engineer. backend services and apis": [0, 0.95]}
    embed = lambda texts: [next((v for k, v in vec.items() if t.startswith(k)), [0, 0]) for t in texts]   # noqa: E731
    assert SG.role_for({"title": "Cloud Solutions Engineer", "role_summary": ""}, roles, embed) == "swe"
    assert SG.role_for({"title": "Pastry Chef"}, roles, lambda texts: [[1, 1]] + [[-1, 0], [0, -1]]) == ""


def test_catch_all_and_soft_skill_duties_are_refused():
    for bad in ("software development", "team collaboration", "debugging and troubleshooting", "algorithm innovation", "software engineering"):
        assert not SG.valid_duty(bad), bad
    assert SG.valid_duty("model deployment and hosting") and SG.valid_duty("performance optimization")


def test_a_skill_under_many_duties_is_a_weaker_neighbour_than_a_specific_one():
    g = SG.Graph()
    PLAN["w1"] = [duty("model training", "PyTorch", "Python", "JAX"), duty("backend services", "Python", "FastAPI")]
    PLAN["w2"] = [duty("model training", "PyTorch", "Python", "JAX"), duty("data pipelines", "Python", "Spark")]
    for t, s in (("w1", ["PyTorch", "Python", "JAX", "FastAPI"]), ("w2", ["PyTorch", "Python", "JAX", "Spark"])):
        SG.ingest(g, jd(t, s), "ml", ask=ask)
    res = SG.suggest(g, ["PyTorch"], "ml", owned=set(), on_resume=set())
    assert [x["skill"] for x in res["review"]][0] == "JAX"


def test_languages_do_not_steer_the_neighbours():
    g = SG.Graph()
    PLAN["l1"] = [duty("model training", "PyTorch", "JAX", "Python"), duty("frontend development", "Python", "React")]
    ingest_langs = jd("l1", ["PyTorch", "JAX", "Python", "React"])
    SG.ingest(g, ingest_langs, "ml", ask=ask)
    g.nodes[g.find("skill", "Python")]["category"] = "language"
    res = SG.suggest(g, ["Python", "PyTorch"], "ml", owned=set(), on_resume=set())
    assert [x["skill"] for x in res["review"]] == ["JAX"]                       # React sits under a duty only Python (a language) points at


def test_place_names_are_not_skills():
    got = SG.clean_skills(["Python", "NY", "WA", "Washington", "Remote", "Seattle", "Go", "R", "C", "TypeScript"], {"seattle"})
    assert got == ["Python", "Go", "R", "C", "TypeScript"]                                       # "Go", "R", "C" are technologies; state codes are not
    assert SG.is_place("CA") and not SG.is_place("ca-style") and SG.is_place("New York")
    assert {"seattle", "foster city"} <= SG.posting_places({"locations": ["Seattle, WA; Foster City, CA"], "locations_resolved": []})


def test_a_posting_s_own_locations_are_dropped_from_its_skills():
    g = SG.Graph()
    jd_ = {"title": "x", "source_url": "https://x.test/1", "technical_skills": ["Go", "Kubernetes", "Foster City"], "locations": ["Foster City, CA"],
           "responsibilities": ["r"], "preferred_qualifications": []}
    SG.ingest(g, jd_, "swe", ask=lambda *a: [duty("cloud infrastructure", "Go", "Kubernetes")])
    assert {n["name"] for n in g.of_kind("skill")} == {"Go", "Kubernetes"}


def test_prune_removes_place_names_older_builds_stored():
    g = SG.Graph()
    SG.ingest(g, jd("p", ["Kubernetes"]), "swe", ask=lambda *a: [duty("cloud infrastructure", "Kubernetes")])
    nid = g.add("skill", "WA", "tool", "old")
    g.link(g.find("duty", "cloud infrastructure"), nid, "old")
    assert g.prune_noise() == ["WA"] and g.find("skill", "WA") is None and g.find("skill", "Kubernetes")
