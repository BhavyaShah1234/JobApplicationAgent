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


def _lines(*pairs):
    from jobagent.tools.tailor import SkillLine
    return [SkillLine(category=c, terms=list(ts)) for c, ts in pairs]


def _typed_graph():
    g = SG.Graph()
    for name, cat in [("PostgreSQL", "database"), ("MongoDB", "database"), ("SQL", "database"), ("Redis", "database"), ("Go", "language"), ("Python", "language"),
                      ("Kubernetes", "cloud"), ("Terraform", "tool"), ("SQS", "cloud"), ("NoSQL", "database")]:
        sid = g.node_id("skill", name)
        g.nodes[sid] = {"id": sid, "kind": "skill", "name": name, "aliases": [], "category": cat, "sources": []}
    return g


def item(name, aliases=(), via="platform design"):
    return {"skill": name, "via": via, "score": 1.0, "aliases": list(aliases)}


def test_sql_is_not_present_because_nosql_is_and_go_is_not_in_mongodb():
    lines = _lines(("Databases", ["PostgreSQL", "NoSQL (MongoDB)"]), ("Languages", ["Python"]))
    added, omitted = SG.place_skills(lines, [item("SQL"), item("Go")], _typed_graph(), {"databases": 40, "languages": 40})
    assert ("Databases", "SQL") in added and ("Languages", "Go") in added, (added, omitted)


def test_a_true_alias_or_an_exact_term_is_not_added_twice():
    lines = _lines(("Databases", ["PostgreSQL", "Redis"]))
    added, omitted = SG.place_skills(lines, [item("Postgres", ["PostgreSQL"]), item("Redis")], _typed_graph(), {"databases": 60})
    assert not added and len(omitted) == 2 and all("already" in o["reason"] for o in omitted)


def test_a_candidate_goes_to_the_line_its_label_says_and_a_generic_tool_is_never_forced():
    lines = _lines(("Cloud & DevOps", ["Kubernetes", "SQS"]), ("Databases", ["Relational databases", "ORMs"]))
    added, omitted = SG.place_skills(lines, [item("Redis"), item("Terraform")], _typed_graph(), {"cloud & devops": 60, "databases": 60})
    assert added == [("Databases", "Redis")]                                      # Terraform is a generic tool: no line is reliably its category
    assert "arbitrary" in omitted[0]["reason"]


def test_the_real_cvs_shape_descriptive_database_terms_take_redis_and_sql_but_not_go_or_terraform():
    g = _typed_graph()
    for n in ("SQL", "Go", "Terraform"):
        g.nodes[g.find("skill", n)]["category"] = "tool"                          # the saved graph files them all under `tool`
    duty = "duty:databases"
    g.nodes[duty] = {"id": duty, "kind": "duty", "name": "databases", "aliases": [], "category": "", "sources": []}
    g.edges[f"{duty}>{g.find('skill', 'SQL')}"] = {"src": duty, "dst": g.find("skill", "SQL"), "weight": 1, "postings": []}
    lines = _lines(("Databases", ["Relational databases", "NoSQL databases", "ORMs"]), ("Scripting and Automation", ["Python", "Shell"]),
                   ("DevOps and Cloud", ["Docker", "AWS"]))
    owned = [item("Redis", via="distributed systems"), item("SQL", via="databases"), item("Go"), item("Terraform")]
    added, omitted = SG.place_skills(lines, owned, g, {"databases": 64, "scripting and automation": 67, "devops and cloud": 18})
    assert added == [("Databases", "Redis"), ("Databases", "SQL")], (added, omitted)
    assert {o["skill"] for o in omitted} == {"Go", "Terraform"}


def test_the_owners_own_template_grouping_is_the_strongest_evidence():
    g = _typed_graph()
    lines = _lines(("Infrastructure", ["Docker"]), ("Backend", ["FastAPI"]))
    added, _ = SG.place_skills(lines, [item("Terraform")], g, {"infrastructure": 40, "backend": 40}, [("Infrastructure", r"\\textbf{x} Docker, Terraform")])
    assert added == [("Infrastructure", "Terraform")]


def test_a_full_best_line_does_not_block_a_compatible_second_line():
    lines = _lines(("Databases", ["PostgreSQL"]), ("Data Storage", ["S3"]))
    added, _ = SG.place_skills(lines, [item("Redis")], _typed_graph(), {"databases": 3, "data storage": 40})
    assert added == [("Data Storage", "Redis")]


def test_a_candidate_without_a_category_line_or_room_is_omitted_with_a_reason():
    lines = _lines(("Databases", ["PostgreSQL"]))
    added, omitted = SG.place_skills(lines, [item("Terraform"), item("Redis"), item("MongoDB")], _typed_graph(), {"databases": 6})
    assert not added
    reasons = {o["skill"]: o["reason"] for o in omitted}
    assert "category" in reasons["Terraform"] and "no room" in reasons["Redis"] and "no room" in reasons["MongoDB"]


def test_room_is_shared_between_candidates_on_one_line():
    lines = _lines(("Databases", ["PostgreSQL"]))
    added, omitted = SG.place_skills(lines, [item("Redis"), item("MongoDB"), item("SQL")], _typed_graph(), {"databases": 17})
    assert [n for _, n in added] == ["Redis", "MongoDB"][:len(added)] and len(added) == 2 and omitted and omitted[0]["skill"] == "SQL"


def test_the_allocation_step_keeps_the_lines_that_fit_and_drops_only_the_one_that_does_not(tmp_path, monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools.tailor import Filled, SkillLine
    f = Filled.model_construct(summary="s", roles=[], skills=[SkillLine(category="Databases", terms=["Relational databases"]),
                                                              SkillLine(category="DevOps and Cloud", terms=["Docker"])], notes="")
    lay = SimpleNamespace(skill_fill={"Databases": 0.2, "DevOps and Cloud": 0.2}, short_skills=["Databases", "DevOps and Cloud"], issues=[], free_pt=0.0)
    monkeypatch.setattr(M.SG, "load", lambda *a, **k: _typed_graph())
    monkeypatch.setattr(M.SG, "place_skills", lambda lines, owned, g, room, source=None: ([("Databases", "Redis"), ("DevOps and Cloud", "Terraform")], []))

    def render(state):
        return state["f"], "tex", [], 1, ""
    monkeypatch.setattr(M, "render", render)
    monkeypatch.setattr(M.layout, "check", lambda path: SimpleNamespace(
        issues=[] if all(len(ln.terms) < 3 and "Terraform" not in ln.terms for ln in monkeypatch_state["f"].skills) else ["Technical Skills wrap"], free_pt=0.0,
        skill_fill=lay.skill_fill, short_skills=lay.short_skills))
    monkeypatch_state = {}
    seen = []

    def render2(state):
        monkeypatch_state["f"] = state["f"]
        seen.append([t for ln in state["f"].skills for t in ln.terms])
        return state["f"], "tex", [], 1, ""
    monkeypatch.setattr(M, "render", render2)
    (tmp_path / "resume.pdf").write_bytes(b"x")
    state = {"ctx": {"job_dir": tmp_path, "log": lambda *a, **k: None, "graph": {"owned": [item("Redis"), item("Terraform")]}, "r": SimpleNamespace(skills=[])}, "f": f}
    f2, tex, problems, pages, err, lay2 = M._allocate_graph_skills(state, f, "tex", [], 1, "", lay, 1)
    terms = {ln.category: ln.terms for ln in f2.skills}
    assert terms["Databases"] == ["Relational databases", "Redis"] and terms["DevOps and Cloud"] == ["Docker"]
    record = json.loads((tmp_path / "skill_allocation.json").read_text())
    assert record["kept"] and record["added"] == [{"skill": "Redis", "line": "Databases"}] and any(o["skill"] == "Terraform" for o in record["omitted"])
    assert len(seen) <= 4                                                           # bounded: one all-at-once render, one per line, one to restore the kept state
    assert not (tmp_path / "skill_suggestions.json").exists()                       # the input fingerprint file is untouched


def _alloc_state(tmp_path, monkeypatch, render):
    from jobagent.steps import multi_agent as M
    from jobagent.tools.tailor import Filled, SkillLine
    f = Filled.model_construct(summary="s", roles=[], skills=[SkillLine(category="Databases", terms=["Relational databases"])], notes="")
    lay = SimpleNamespace(skill_fill={"Databases": 0.2}, short_skills=["Databases"], issues=[], free_pt=0.0)
    monkeypatch.setattr(M.SG, "load", lambda *a, **k: _typed_graph())
    monkeypatch.setattr(M.SG, "place_skills", lambda *a, **k: ([("Databases", "Redis")], []))
    monkeypatch.setattr(M.layout, "check", lambda path: SimpleNamespace(issues=[], free_pt=0.0, skill_fill=lay.skill_fill, short_skills=lay.short_skills))
    monkeypatch.setattr(M, "render", render)
    (tmp_path / "resume.pdf").write_bytes(b"original")
    state = {"ctx": {"job_dir": tmp_path, "log": lambda *a, **k: None, "graph": {"owned": [item("Redis")]}, "r": SimpleNamespace(skills=[])}, "f": f}
    return M, f, lay, state


def test_a_programmer_error_during_a_trial_restores_the_files_and_is_raised(tmp_path, monkeypatch):
    import pytest

    def render(state):
        (tmp_path / "resume.pdf").write_bytes(b"half written")
        raise RuntimeError("source bug")
    M, f, lay, state = _alloc_state(tmp_path, monkeypatch, render)
    with pytest.raises(RuntimeError, match="source bug"):
        M._allocate_graph_skills(state, f, "tex", [], 1, "", lay, 1)
    assert (tmp_path / "resume.pdf").read_bytes() == b"original"


def test_an_unreadable_graph_is_optional_and_reported_but_nothing_else_is(tmp_path, monkeypatch):
    M, f, lay, state = _alloc_state(tmp_path, monkeypatch, lambda s: (s["f"], "tex", [], 1, ""))

    def broken(*a, **k):
        raise ValueError("bad graph file")
    monkeypatch.setattr(M.SG, "load", broken)
    out = M._allocate_graph_skills(state, f, "tex", [], 1, "", lay, 1)
    assert out[0] is f and "graph unavailable" in json.loads((tmp_path / "skill_allocation.json").read_text())["omitted"][0]["reason"]


def test_the_allocation_record_accumulates_names_the_pdf_and_keeps_earlier_additions(tmp_path, monkeypatch):
    import hashlib
    M, f, lay, state = _alloc_state(tmp_path, monkeypatch, lambda s: (s["f"], "tex", [], 1, ""))
    out = M._allocate_graph_skills(state, f, "tex", [], 1, "", lay, 1)
    assert [x["skill"] for x in json.loads((tmp_path / "skill_allocation.json").read_text())["added"]] == ["Redis"]
    monkeypatch.setattr(M.SG, "place_skills", lambda *a, **k: ([], [{"skill": "SQL", "reason": "no room"}]))
    M._allocate_graph_skills({**state, "f": out[0]}, out[0], "tex", [], 1, "", lay, 2)               # a later attempt that adds nothing
    rec = json.loads((tmp_path / "skill_allocation.json").read_text())
    assert [x["skill"] for x in rec["added"]] == ["Redis"] and len(rec["attempts"]) == 2 and rec["omitted"][0]["skill"] == "SQL"
    assert rec["resume_sha256"] == hashlib.sha256((tmp_path / "resume.pdf").read_bytes()).hexdigest()


def test_the_allocation_record_is_part_of_the_accepted_version_bundle():
    from jobagent.steps import multi_agent as M
    files = M._version_files({"tex": "t", "pdf": b"p", "projects": None, "layout_meta": None, "allocation": '{"added": []}', "f": None})
    assert files["skill_allocation.json"] == '{"added": []}'
    assert M._version_files({"tex": "t", "pdf": b"p", "allocation": None, "f": None})["skill_allocation.json"] is None    # no record in that version: no such file
