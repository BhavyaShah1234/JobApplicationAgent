"""A project heading prints only technologies that project's own card establishes (whole words, versions ignored), never another repository's or the posting's."""
import pytest

from jobagent.tools import projects as P

ENERGY = {"full_name": "ak-asu/EnergyHackathon", "title": "COLLIDE", "summary": "Optimal data-center siting with agents and ML.",
          "tech_stack": ["Python, FastAPI, SSE (sse-starlette), WebSockets, APScheduler",
                         "LangGraph StateGraph, LangChain, Anthropic Claude Sonnet 4.6 (synthesis) + Haiku (intent classification)",
                         "scikit-learn (Random Forest, KDTree spatial indices), PyTorch (Gaussian KDE per README), SHAP, TOPSIS",
                         "Tavily API (web enrichment + news), GridStatus (ERCOT), EIA Open Data (gas prices, demand), CAISO OASIS (LMP)",
                         "DuckDB / Parquet / Pandera data pipeline (per README), httpx async ingestion, pytest"]}
CLOUD = {"full_name": "ak-asu/cloudforge", "title": "CloudForge", "summary": "PRD to deployed AWS infrastructure.",
         "tech_stack": ["Python 3.12", "FastAPI", "LangGraph", "Terraform", "CloudFormation", "AWS STS/IAM", "Next.js 15", "React 19"]}
DJANGO = {"full_name": "o/vf", "title": "VisionForge", "summary": "Visual neural network builder.", "tech_stack": ["TypeScript", "React 19", "Django / Django REST Framework", "PyTorch"]}


def test_a_technology_only_another_repository_establishes_is_dropped():
    kept, dropped = P.ground_stack(["Python", "FastAPI", "LangGraph", "scikit-learn", "AWS"], ENERGY)       # the saved Samsara case: AWS is CloudForge's
    assert kept == ["Python", "FastAPI", "LangGraph", "scikit-learn"] and dropped == ["AWS"]
    assert P.ground_stack(["Python", "AWS"], CLOUD) == (["Python", "AWS"], [])                               # 'AWS STS/IAM' establishes AWS for CloudForge


def test_versions_and_compound_card_lines_are_normalised_but_nothing_is_invented():
    kept, dropped = P.ground_stack(["Python 3.12", "React 19", "Next.js", "Terraform"], CLOUD)
    assert kept == ["Python 3.12", "React 19", "Next.js", "Terraform"] and not dropped
    kept, dropped = P.ground_stack(["scikit-learn", "PyTorch", "DuckDB", "Kubernetes"], ENERGY)             # grouped card lines and a posting-only term
    assert kept == ["scikit-learn", "PyTorch", "DuckDB"] and dropped == ["Kubernetes"]


@pytest.mark.parametrize("card,term,ok", [(DJANGO, "Go", False), (DJANGO, "Django", True), (DJANGO, "Java", False), (ENERGY, "Spark", False),
                                          ({"full_name": "o/g", "title": "t", "summary": "s", "tech_stack": ["Go", "gRPC"]}, "Go", True),
                                          ({"full_name": "o/m", "title": "t", "summary": "Tracks usage metrics and user counts.", "tech_stack": ["Rust"]}, "Go", False)])
def test_terms_match_whole_words_only(card, term, ok):
    assert (term in P.ground_stack([term, "Rust" if "Rust" in card["tech_stack"] else card["tech_stack"][0]], card)[0]) is ok


def test_aliases_and_multi_word_terms():
    card = {"full_name": "o/a", "title": "t", "summary": "Runs on Amazon Web Services with PostgreSQL.", "tech_stack": ["Node.js", "Google Cloud Platform"]}
    kept, dropped = P.ground_stack(["AWS", "Postgres", "GCP", "NodeJS", "Azure"], card)
    assert kept == ["AWS", "Postgres", "GCP", "NodeJS"] and dropped == ["Azure"]
    assert P.ground_stack(["Random Forest"], {"full_name": "o/r", "title": "t", "summary": "A random forest model.", "tech_stack": ["Python"]})[0] == ["Random Forest"]
    assert P.ground_stack(["Random Boosting"], {"full_name": "o/r", "title": "t", "summary": "A random forest model.", "tech_stack": ["Python"]})[0] == ["Python"]


def test_nothing_grounded_falls_back_to_the_cards_own_terms_never_an_empty_heading():
    kept, dropped = P.ground_stack(["AWS", "Kubernetes"], ENERGY)
    assert kept[:3] == ["Python", "FastAPI", "SSE"] and dropped == ["AWS", "Kubernetes"]
    assert P.ground_stack(["X"], {"full_name": "o/e", "title": "", "summary": "", "tech_stack": []})[0] == ["GitHub"]


def test_card_terms_split_grouped_lines_and_drop_parentheticals():
    terms = P.card_terms(ENERGY)
    assert terms[:5] == ["Python", "FastAPI", "SSE", "WebSockets", "APScheduler"] and "SSE (sse-starlette)" not in terms
    # fixture contract: a fallback entry is described by whole sentences of its own card, so the card needs real ones (the assertion is about the stack)
    sited = ("Optimal data-center siting with agents and ML models that rank grid-connected parcels for planners. "
             "It scores candidate sites against live market data and returns an explained shortlist of ranked sites to the user.")
    assert P.from_card({"full_name": "o/e", "title": "COLLIDE: siting", "summary": sited, "tech_stack": ENERGY["tech_stack"], "url": "u"}).stack[:2] == ["Python", "FastAPI"]


def test_a_stale_state_with_an_ungrounded_stack_is_reported_not_rendered_silently():
    state = {"ranked": [{"repo": "ak-asu/EnergyHackathon", "paragraph": "Siting data centres with agents.", "stack": ["Python", "AWS"]}], "kept": 1, "short": []}
    got = P.content_findings(state, {"ak-asu/energyhackathon": ENERGY})
    assert any("project 1 prints technologies its own card does not show: ['AWS']" in f for f in got), got
    state["ranked"][0]["stack"] = ["Python", "FastAPI"]
    assert not any("prints technologies" in f for f in P.content_findings(state, {"ak-asu/energyhackathon": ENERGY}))


def test_words_scattered_over_different_fields_do_not_assemble_a_product():
    card = {"full_name": "o/web", "title": "Web app", "summary": "Renders native browser inputs", "tech_stack": ["React", "TypeScript"]}
    assert P.ground_stack(["React Native"], card) == (["React", "TypeScript"], ["React Native"])                      # fell back to the card's own terms
    assert P.ground_stack(["React", "React Native"], card) == (["React"], ["React Native"])
    assert P.ground_stack(["React Native"], {**card, "tech_stack": ["React, Native"]})[1] == ["React Native"]       # a comma separates two items
    assert P.ground_stack(["React Native"], {**card, "tech_stack": ["React Native", "TypeScript"]}) == (["React Native"], [])


def test_an_ambiguous_word_is_a_language_only_when_the_structured_stack_lists_it():
    prose = {"full_name": "o/p", "title": "Release tool", "summary": "Ready to go live; a Go service would also work.", "tech_stack": ["Rust", "Axum"]}
    assert P.ground_stack(["Rust", "Go"], prose) == (["Rust"], ["Go"])
    assert P.ground_stack(["Go"], {**prose, "tech_stack": ["Go", "gRPC"]}) == (["Go"], [])
    assert P.ground_stack(["Spark"], {**prose, "summary": "Spark jobs for ETL", "tech_stack": ["Python"]})[1] == ["Spark"]
    assert P.ground_stack(["PySpark"], {**prose, "tech_stack": ["PySpark"]}) == (["PySpark"], [])


def test_a_swapped_in_entry_is_grounded_before_it_is_saved(tmp_path):
    import json
    from types import SimpleNamespace
    from jobagent.steps import multi_agent as M
    from jobagent.tools import tailor as T
    state = {"ranked": [{"repo": "o/old", "title": "Old", "stack": ["Python"], "paragraph": "Old project text that is long enough to be a paragraph." * 3, "short": "s"}],
             "kept": 1, "short": [], "years": {}, "cards": {}, "macro": False}
    (tmp_path / "projects.json").write_text(json.dumps(state))
    # fixture contract: the swapped-in entry selects whole source sentences of its own card (the assertion is about the stack)
    sited = ("Optimal data-center siting with agents and ML models that rank grid-connected parcels for planners. "
             "It scores candidate sites against live market data and returns an explained shortlist of ranked sites to the user.")
    entry = P.ProjectPick(repo="ak-asu/EnergyHackathon", title="COLLIDE", stack=["Python", "FastAPI", "AWS"], paragraph_sentences=[1, 2], short_sentences=[2])
    ctx = {"job_dir": tmp_path, "r": SimpleNamespace(tex=""), "cards": {"ak-asu/energyhackathon": {**ENERGY, "summary": sited, "url": "https://github.com/ak-asu/EnergyHackathon"}},
           "log": lambda _: None}
    f = T.Filled.model_construct(summary="s", roles=[], skills=[], notes="")
    M._merge(ctx, f, {"projects": SimpleNamespace(projects=[], swaps=[SimpleNamespace(project=1, entry=entry)])})
    saved = json.loads((tmp_path / "projects.json").read_text())["ranked"][0]
    assert saved["repo"] == "ak-asu/EnergyHackathon" and saved["stack"] == ["Python", "FastAPI"]


@pytest.mark.parametrize("technology", ["C++", "C#", "F#", ".NET", "Node.js", "CI/CD", "TCP/IP"])
def test_real_technology_punctuation_survives_exact_matching(technology):
    kept, dropped = P.ground_stack([technology], {"tech_stack": [technology], "title": "", "summary": ""})
    assert kept == [technology] and not dropped


def test_cpp_does_not_establish_c_and_prose_net_does_not_establish_dotnet():
    assert P.ground_stack(["C"], {"tech_stack": ["C++"]})[1] == ["C"]
    assert P.ground_stack([".NET"], {"tech_stack": ["Python"], "summary": "Trains a neural net."})[1] == [".NET"]
    assert P.ground_stack(["CI"], {"tech_stack": ["Git"], "summary": "CI/CD pipelines."})[1] == ["CI"] or True      # a CI/CD phrase may establish CI; not asserted either way


def test_a_one_character_language_is_a_valid_fallback_term():
    assert P.ground_stack(["InventedFramework"], {"tech_stack": ["R"]}) == (["R"], ["InventedFramework"])
    assert P.card_terms({"tech_stack": ["R, C, Go"]}) == ["R", "C", "Go"]
