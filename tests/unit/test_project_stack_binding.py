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
    assert P.from_card({"full_name": "o/e", "title": "COLLIDE: siting", "summary": "Siting.", "tech_stack": ENERGY["tech_stack"], "url": "u"}).stack[:2] == ["Python", "FastAPI"]


def test_a_stale_state_with_an_ungrounded_stack_is_reported_not_rendered_silently():
    state = {"ranked": [{"repo": "ak-asu/EnergyHackathon", "paragraph": "Siting data centres with agents.", "stack": ["Python", "AWS"]}], "kept": 1, "short": []}
    got = P.content_findings(state, {"ak-asu/energyhackathon": ENERGY})
    assert any("project 1 prints technologies its own card does not show: ['AWS']" in f for f in got), got
    state["ranked"][0]["stack"] = ["Python", "FastAPI"]
    assert not any("prints technologies" in f for f in P.content_findings(state, {"ak-asu/energyhackathon": ENERGY}))
