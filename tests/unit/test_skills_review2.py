"""Codex review of the skill graph (G01-G09) and the remaining R03/R05/R07/R13 cases."""
import json
import threading
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from jobagent import db, main
from jobagent import log as L
from jobagent.config import get_settings
from jobagent.tools import eligibility, multi_input
from jobagent.tools import jobqueue as Q
from jobagent.tools import skillgraph as SG
from jobagent.tools import tailor as T
from jobagent.tools.matching import Match
from jobagent.tools.skillgraph import DutyOut, SkillOut
from test_queue_claims import US


def duty(name, *skills):
    return DutyOut(duty=name, skills=[SkillOut(name=s, category="framework") for s in skills])


def jd(title, skills, url=None):
    return {"title": title, "company": "Acme", "source_url": url or f"https://x.test/{title}", "technical_skills": skills,
            "responsibilities": ["r"], "preferred_qualifications": []}


# --- G01: ownership is enforced wherever a resume is rendered -------------------------------------------------------------------

def tex_with_skills():
    return ("\\section{Summary}\nS\n\\section{Experience}\n\\resumeSubheading{T}{d}{C}{l}\n\\resumeItem{b}\n\\section{Education}\n"
            "\\section{Technical Skills}\n\\small{\\item{\n     \\textbf{Languages}{: Python}\n    }}\n")


def test_apply_fill_removes_unowned_skills_for_every_writer():
    from jobagent.tools.tailor import SkillLine, parse
    pytest.importorskip("jobagent.tools.tailor")
    r = SimpleNamespace(forbidden={"huggingface"}, skills=[("Languages", "Python")])
    f = SimpleNamespace(skills=[SkillLine(category="ML", terms=["HuggingFace"]), SkillLine(category="Lang", terms=["Python", "HuggingFace"])])
    kept, dropped = SG.enforce(f.skills, r.forbidden)
    assert [x.terms for x in kept] == [["Python"]] and dropped == ["HuggingFace", "HuggingFace"]


def test_the_final_gate_reports_an_unowned_skill_that_slipped_through(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SKILLS_USE_GRAPH", "true")
    get_settings.cache_clear()
    job = tmp_path / "job"
    job.mkdir()
    posting = jd("p", ["PyTorch"])
    (job / "jd.json").write_text(json.dumps(posting))
    g = SG.Graph()
    SG.ingest(g, jd("a", ["PyTorch", "JAX"]), "ml", ask=lambda *a: [duty("model training", "PyTorch", "JAX")])
    SG.save(g, "t", None)
    SG.job_suggestions(job, posting, "ml", ["Python"])
    f = SimpleNamespace(skills=[T.SkillLine(category="ML", terms=["PyTorch", "JAX"])])
    assert any("JAX" in v for v in SG.violations(job, f))
    assert SG.violations(job, SimpleNamespace(skills=[T.SkillLine(category="ML", terms=["PyTorch"])])) == []
    get_settings.cache_clear()


# --- G03: two pipelines adding postings at once lose nothing --------------------------------------------------------------------

def test_two_postings_added_concurrently_both_survive_and_versions_are_unique(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    s = get_settings()
    gate = threading.Barrier(2)

    def slow_ask(name, skill):
        def ask(j, known, skills):
            gate.wait(5)                                              # both have read the same snapshot before either writes
            return [duty("model training", skill)]
        return ask
    errs = []

    def worker(title, skill):
        try:
            SG.add_posting(jd(title, [skill]), "ml", s, ask=slow_ask(title, skill), timeout=10)
        except Exception as e:                                        # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=worker, args=a) for a in (("p1", "PyTorch"), ("p2", "JAX"))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    g = SG.load(s)
    assert not errs and {n["name"] for n in g.of_kind("skill")} == {"PyTorch", "JAX"} and g.version == 2
    versions = [json.loads(x)["version"] for x in (tmp_path / "skills" / "history.jsonl").read_text().splitlines()]
    assert versions == [1, 2]
    get_settings.cache_clear()


# --- G04: a posting read again with changed content is re-ingested --------------------------------------------------------------

def test_a_changed_posting_replaces_its_old_support():
    g = SG.Graph()
    plan = {"a": [duty("model training", "PyTorch")], "b": [duty("model training", "PyTorch", "JAX")]}
    first = jd("same", ["PyTorch"], url="https://x.test/1")
    SG.ingest(g, first, "ml", ask=lambda *a: plan["a"])
    h = g.content_hash()
    assert SG.ingest(g, first, "ml", ask=lambda *a: plan["a"])["skipped"] and g.content_hash() == h
    second = jd("same", ["PyTorch", "JAX"], url="https://x.test/1")
    res = SG.ingest(g, second, "ml", ask=lambda *a: plan["b"])
    assert not res["skipped"] and g.find("skill", "JAX")
    pt = g.find("skill", "PyTorch")
    assert [w for _, w in g.parents(pt)] == [1]                           # one posting still counts once
    third = jd("same", ["JAX"], url="https://x.test/1")
    SG.ingest(g, third, "ml", ask=lambda *a: [duty("model training", "JAX")])
    assert g.find("skill", "PyTorch") is None                              # nothing supports it any more


# --- G05: fallback vectors never meet primary vectors ---------------------------------------------------------------------------

def test_vectors_of_different_models_are_never_compared():
    assert SG.cosine([1, 0, 0, 0], [1, 0]) == 0.0


def test_a_fallback_batch_is_embedded_wholly_by_the_fallback_model(tmp_path, monkeypatch):
    from jobagent import llm
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()

    class Emb:
        def __init__(self, fallback): self.used_fallback = fallback
        def embed_documents(self, texts): return [[1.0, 0.0]] * len(texts) if self.used_fallback else [[1.0, 0.0, 0.0, 0.0]] * len(texts)
    embed = SG.default_embed()
    monkeypatch.setattr(llm, "embeddings", lambda: Emb(False))
    assert len(embed(["a"])[0]) == 4                                        # primary vector cached
    monkeypatch.setattr(llm, "embeddings", lambda: Emb(True))
    out = embed(["a", "b"])                                                 # 'b' is new: the primary is down
    assert len(out[0]) == len(out[1]) == 2                                  # both come from the fallback model; the cached 4-d vector is not mixed in
    get_settings.cache_clear()


# --- G06: unplaced skills create no neighbours ----------------------------------------------------------------------------------

def test_the_general_tooling_bucket_creates_no_neighbours():
    g = SG.Graph()
    SG.ingest(g, jd("a", ["PyTorch"]), "ml", ask=lambda *a: [])
    SG.ingest(g, jd("b", ["React"]), "swe", ask=lambda *a: [])
    assert SG.suggest(g, ["PyTorch"], "ml", owned={"react"}, on_resume=set()) == {"owned": [], "review": []}


# --- G07/G08: fresh artifacts, and the resume cache sees the graph ---------------------------------------------------------------

def test_an_empty_new_result_clears_the_old_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("SKILLS_USE_GRAPH", "true")
    get_settings.cache_clear()
    job = tmp_path / "job"
    job.mkdir()
    posting = jd("p", ["PyTorch"])
    (job / "jd.json").write_text(json.dumps(posting))
    g = SG.Graph()
    SG.ingest(g, jd("a", ["PyTorch", "JAX"]), "ml", ask=lambda *a: [duty("model training", "PyTorch", "JAX")])
    SG.save(g, "t", None)
    assert SG.job_suggestions(job, posting, "ml", ["Python"])["review"]
    assert SG.review_lines(job)
    SG.job_suggestions(job, jd("p", ["Unknown thing"]), "ml", ["Python"])
    assert json.loads((job / "skill_suggestions.json").read_text())["review"] == []
    get_settings.cache_clear()


def test_graph_approval_and_switch_change_the_resume_stage_input(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SKILLS_USE_GRAPH", "true")
    get_settings.cache_clear()
    from jobagent.state import JobDescription
    g = SG.Graph()
    SG.ingest(g, jd("a", ["PyTorch", "JAX"]), "ml", ask=lambda *a: [duty("model training", "PyTorch", "JAX")])
    SG.save(g, "t", None)
    r = SimpleNamespace(skills=[("Languages", "Python")])
    posting = JobDescription.model_validate({"title": "p", "company": "Acme", "technical_skills": ["PyTorch"], "source_url": "https://x.test/p"})
    before = SG.input_fingerprint(posting, r)
    assert SG.approve("JAX")
    assert SG.input_fingerprint(posting, r) != before
    monkeypatch.setenv("SKILLS_USE_GRAPH", "false")
    get_settings.cache_clear()
    assert SG.input_fingerprint(posting, r) == "off"
    get_settings.cache_clear()


# --- G09: sync reports failures -------------------------------------------------------------------------------------------------

def test_sync_exits_nonzero_when_a_scrape_fails(tmp_path, monkeypatch):
    import jobagent.tools.pipeline as P
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    conn = db.connect()
    Q.enqueue(conn, Q.Listing("Acme", "Robot", "https://e.test/r1", ["Austin, TX"], req_id="1"), Match("queued", "regex", "robotics", 0.9), US)
    monkeypatch.setattr("jobagent.tools.matching.load_roles", lambda *a, **k: {"roles": [{"id": "robotics", "title": "Robotics"}]})
    calls = []
    monkeypatch.setattr(P, "Pipeline", lambda *a, **k: SimpleNamespace(run=lambda url, **kw: calls.append((url, kw)) or
                                                                       SimpleNamespace(state="failed", note="x", code=1)))
    r = CliRunner().invoke(main.app, ["skills", "sync", "--no-discover"])
    assert calls and calls[0][1] == {"through": "scrape"} and r.exit_code == 1
    get_settings.cache_clear()


# --- R03 ------------------------------------------------------------------------------------------------------------------------

NONE = {"us_citizen": True, "authorized": True, "needs_sponsorship": False, "clearance": "none", "permanent_resident": False}


@pytest.mark.parametrize("text, facts, level", [
    ("An active Secret clearance is required and U.S. citizenship is required.", NONE, "blocked"),
    ("Must be a U.S. citizen with an active Secret clearance.", NONE, "blocked"),
    ("U.S. citizenship and an active Secret clearance are required.", {**NONE, "us_citizen": False, "clearance": "Secret"}, "blocked"),
    ("No active security clearance is required.", NONE, "ok"),
    ("Active Secret clearance is preferred, and Python experience is required.", NONE, "ok"),
])
def test_eligibility_more_clause_variants(text, facts, level):
    assert eligibility.screen(text, facts).level == level


# --- R05 ------------------------------------------------------------------------------------------------------------------------

def test_abbreviated_suffixes_and_missing_slugs_share_one_bucket(tmp_path):
    assert Q.employer_key("Acme L.L.C.") == Q.employer_key("Acme LLC") == "acme"
    conn = db.connect(tmp_path / "jobs.sqlite")
    for n in (1, 2):
        Q.enqueue(conn, Q.Listing("Acme", f"R{n}", f"https://e.test/{n}", ["Austin, TX"], req_id=str(n)), Match("queued", "regex", "swe", 0.9 - n / 100), US)
    with conn:
        conn.execute("UPDATE jobs SET company_slug = NULL, company = 'Acme Corporation'")             # as scrape.save leaves it
    assert Q.claim(conn, "w1", pace=(1, 7)) is not None
    assert Q.claim(conn, "w2", pace=(1, 7)) is None


# --- R07 ------------------------------------------------------------------------------------------------------------------------

def test_one_url_in_two_files_with_different_referrals_uses_neither(tmp_path):
    for name, who in (("a.csv", "Pat Lee"), ("b.csv", "Sam Roe")):
        (tmp_path / name).write_text(f"company,url,contact_name,contact_email,referral_consent\nAcme,https://jobs.acme.test/1,{who},x@acme.test,yes\n")
    p = multi_input.plan([str(tmp_path / "a.csv"), str(tmp_path / "b.csv")])
    key = next(iter(p.url_meta))
    assert p.url_meta[key]["referral"] is None and any("different referral contacts" in w for w in p.warnings)


# --- R13: the dispatch paths propagate the run context ---------------------------------------------------------------------------

def test_with_context_carries_the_run_into_pool_workers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import io
    from rich.console import Console
    log = L.RunLog("research", "job-r", console=Console(file=io.StringIO()), directory=tmp_path / "logs", quiet=False)
    log("research: start")
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(L.with_context(lambda _: L.default().warn("fallback in a worker")), [1, 2]))
    assert log.path.read_text().count("fallback in a worker") == 2
    log.close()


def test_the_real_render_of_both_writers_drops_a_skill_the_candidate_lacks():
    """G01 at the shared render: T.apply_fill is what the single writer, the multi-agent drafts and the ATS refresh all call."""
    import test_tailor as TT
    r = T.parse(TT.TEX)
    f = TT._filled("Built a thing", "Built another thing")
    f.skills = [T.SkillLine(category="ML", terms=["PyTorch", "HuggingFace"]), T.SkillLine(category="Only", terms=["HuggingFace"]),
                T.SkillLine(category="Lang", terms=["Python"]), T.SkillLine(category="Tools", terms=["Docker"])]
    untouched = f.model_copy(deep=True)
    r.forbidden = {"huggingface"}
    tex, _ = T.apply_fill(r, f)
    assert all("HuggingFace" not in x.terms for x in f.skills)                              # filled.json is written from the cleaned object
    assert "HuggingFace" not in tex and "PyTorch" in tex and "Only" not in tex
    r2 = T.parse(TT.TEX)
    tex2, _ = T.apply_fill(r2, untouched)                                                              # no guard attached: nothing is removed
    assert "HuggingFace" in tex2


def test_a_skill_the_posting_names_anywhere_is_not_a_suggestion(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("SKILLS_USE_GRAPH", "true")
    get_settings.cache_clear()
    job = tmp_path / "job"
    job.mkdir()
    posting = jd("p", ["PyTorch"])
    posting["basic_qualifications"] = ["Experience with SQL databases", "Willing to go the extra mile"]
    (job / "jd.json").write_text(json.dumps(posting))
    g = SG.Graph()
    SG.ingest(g, jd("a", ["PyTorch", "SQL", "Go"]), "ml", ask=lambda *a: [duty("model training", "PyTorch", "SQL", "Go")])
    SG.save(g, "t", None)
    res = SG.job_suggestions(job, posting, "ml", ["Python"])
    assert [x["skill"] for x in res["review"]] == ["Go"]              # SQL is in the posting's text; "go" the verb is not the language
    f = SimpleNamespace(skills=[T.SkillLine(category="Data", terms=["SQL", "PyTorch"])])
    assert SG.violations(job, f) == []
    get_settings.cache_clear()


def test_a_skill_the_candidates_own_writing_names_counts_as_owned(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    get_settings.cache_clear()
    (tmp_path / "d" / "templates").mkdir(parents=True)
    (tmp_path / "d" / "templates" / "resume.tex").write_text("\\resumeItem{Moved a monolith to microservices on Kubernetes, with data in Azure SQL.}\n"
                                                              "\\resumeItem{Go beyond the brief.}")
    g = SG.Graph()
    SG.ingest(g, jd("a", ["Kubernetes", "SQL", "Go", "Terraform"]), "ml", ask=lambda *a: [duty("cloud infrastructure", "Kubernetes", "SQL", "Go", "Terraform")])
    owned = SG.owned_with_evidence(g, ["Python"])
    assert {"kubernetes", "sql", "python"} <= owned and "terraform" not in owned and "go" not in owned          # "Go" in prose is not evidence
    assert SG._resume_terms(SimpleNamespace(skills=[("Cloud", "AWS (EC2, Kubernetes), Azure")])) == ["AWS", "EC2", "Kubernetes", "Azure"]
    get_settings.cache_clear()
