"""Generated project prose must stay inside its OWN card: adjectival compliance outcomes, technologies named in the paragraph, and recovery in the real loop.

The packet under test is the exact projects.json of the native Samsara run that both independent PDF reviewers rejected (CloudForge "ensuring deterministic
and compliant cloud setups"; COLLIDE "React for a dynamic frontend" after React was dropped from its printed stack).
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobagent.config import get_settings
from jobagent.steps import multi_agent as M
from jobagent.tools import documents as D, projects as P, tailor as T

SHORT_MIN = P.SHORT_FLOOR
PACKET = json.loads((Path(__file__).parent.parent / "fixtures" / "samsara_3b818f3_projects.json").read_text())
FILLED = T.Filled.model_construct(summary="Engineer with Python experience.", roles=[], skills=[], notes="")
CLOUD, COLLIDE = "ak-asu/cloudforge", "ak-asu/energyhackathon"


@pytest.fixture(autouse=True)
def isolate_settings_cache():
    """A temporary DATA_DIR must not remain cached after monkeypatch restores the environment."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def packet(**changes):
    """A private copy of the real packet, optionally with changed top-level keys."""
    return {**copy.deepcopy(PACKET), **changes}


def entry(st, repo):
    """The ranked entry of a repository (case-insensitive)."""
    return next(e for e in st["ranked"] if e["repo"].casefold() == repo.casefold())


def cards(st):
    """The packet's own saved evidence."""
    return P.saved_evidence(st)


def bound(st, keep=()):
    """Fixture contract (source-sentence binding): the captured packet's model-written prose predates it, so every entry not in `keep` carries whole
    sentences of its own saved card (`faithful_variants`) and a card-supported title; `keep` entries stay exactly as captured (the defects under test)."""
    st, skip = copy.deepcopy(st), {k.casefold() for k in keep}
    for e in st["ranked"]:
        if e["repo"].casefold() not in skip:
            full, short = P.faithful_variants(cards(st)[e["repo"].casefold()])
            e.update(paragraph=full, short=short or "", title=P.card_title(cards(st)[e["repo"].casefold()]))
    return st


# --- 1. adjectival outcomes ------------------------------------------------------------------------------------------------------------

CARD = {"full_name": "o/r", "summary": "Maps architectures to SOC 2, HIPAA and ISO 27001 controls with compliance mapping and validates Terraform with TFLint "
                                      "and Checkov architecture checks.", "tech_stack": ["Terraform"]}


def test_actual_cloudforge_adjective_outcome_is_found_in_its_own_saved_evidence():
    st = packet()
    claims = P.standard_outcome_claims(entry(st, CLOUD)["paragraph"], cards(st)[CLOUD])
    assert len(claims) == 1 and "compliant cloud setups" in claims[0]
    assert any("achieved standard or compliance" in f for f in P.content_findings(st, cards(st)))


@pytest.mark.parametrize("text", [
    "The pipeline generates Terraform, ensuring deterministic and compliant cloud setups.",
    "It produces compliant infrastructure for every request.",
    "Guarantees conformant deployments across accounts.",
    "Validates Terraform with Checkov, ensuring compliant setups.",           # the checking clause ended at the comma and "ensuring"
])
def test_a_compliant_result_the_card_only_maps_or_checks_is_an_unsupported_outcome(text):
    assert P.standard_outcome_claims(text, CARD)


@pytest.mark.parametrize("text", [
    "Flags non-compliant resources before deployment.",
    "Checks whether each plan is compliant with the mapped controls.",
    "Verifies that generated stacks are compliant, reporting any gap.",
    "Designed to be compliant with SOC 2 controls.",
    "Not yet compliant with HIPAA; mapping is in progress.",
    "Scans Terraform for compliant and non-compliant settings.",
    "Ensures reliable deployments, reducing manual intervention and improving deployment reliability.",         # ordinary qualitative benefits stay allowed
    "Cut review time by 40% and ensures consistent, repeatable runs.",                                         # numerics are not banned
])
def test_capability_negation_plans_and_ordinary_benefits_are_not_outcomes(text):
    assert not P.standard_outcome_claims(text, CARD)


@pytest.mark.parametrize("field, value", [
    ("summary", "Generates compliant infrastructure from plain-English requirements."),
    ("results", ["Ensures deterministic and compliant cloud setups in every run."]),
    ("readme", "Every stack the tool deploys is compliant with the configured baseline."),
    ("evidence_notes", "The generator achieves compliance for each deployment."),
])
def test_the_same_outcome_in_any_own_card_field_supports_the_claim(field, value):
    card = {**CARD, "summary": "Maps controls.", field: value}
    assert not P.standard_outcome_claims("The system generates stacks, ensuring compliant cloud setups.", card)


@pytest.mark.parametrize("support", ["It is not compliant yet.", "Flags non-compliant resources.", "Planned: generate compliant stacks (designed to be compliant).",
                                     "Checks whether stacks are compliant."])
def test_a_card_that_denies_plans_or_only_checks_the_outcome_does_not_support_it(support):
    assert P.standard_outcome_claims("Ensures compliant cloud setups.", {**CARD, "summary": support})


def test_the_selected_short_variant_is_the_one_checked():
    st = packet()
    cloud = entry(st, CLOUD)
    cloud["paragraph"], cloud["short"] = "CloudForge deploys AWS infrastructure from requirements using a knowledge graph.", "It ensures compliant cloud setups."
    assert not [f for f in P.content_findings(st, cards(st)) if "achieved standard" in f]          # the unused variant is not displayed
    st["short"] = [cloud["repo"]]
    assert [f for f in P.content_findings(st, cards(st)) if "achieved standard" in f]


# --- 2. technologies named in the selected prose ---------------------------------------------------------------------------------------

def test_actual_collide_react_is_found_although_only_another_projects_card_lists_it():
    st = packet()
    assert not any(t.casefold() == "react" for t in entry(st, COLLIDE)["stack"])                    # the printed stack had already dropped it
    finding = [f for f in P.content_findings(st, cards(st)) if "describes technologies" in f]
    assert len(finding) == 1 and "'React'" in finding[0] and finding[0].startswith("project 2 ")


def test_a_mirrored_repository_card_is_not_evidence_for_the_selected_repository():
    st = packet()
    mirror = {**cards(st)["forgeopus/visionforge"], "full_name": "BhavyaShah1234/EnergyHackathon", "tech_stack": ["React 18", "Vite", "FastAPI"]}
    st["evidence"]["bhavyashah1234/energyhackathon"] = mirror
    assert [f for f in P.content_findings(st, cards(st)) if "describes technologies" in f]


def test_the_cards_own_structured_stack_or_text_supports_the_technology():
    st = packet()
    own = cards(st)[COLLIDE]
    for support in ({"tech_stack": own["tech_stack"] + ["React 19", "Vite"]}, {"summary": own["summary"] + " The dashboard is a React single-page app."}):
        st["evidence"][COLLIDE] = {**own, **support}
        assert not [f for f in P.content_findings(st, cards(st)) if "describes technologies" in f]


def test_the_faithful_actual_packet_text_is_not_flagged():
    st = packet()
    findings = P.content_findings(st, cards(st))
    assert not any("project 3" in f or "project 4" in f for f in findings)              # Agentic Report Generation, VisionForge: supported by their own cards


def vocabulary(*stacks):
    """A vocabulary built from stacks only."""
    return P.tech_vocabulary([{"tech_stack": list(s)} for s in stacks])


OWN = {"full_name": "o/own", "tech_stack": ["Python 3.12", "FastAPI", "Next.js 15"], "summary": "A web client."}


@pytest.mark.parametrize("text, expected", [
    ("Uses React for the frontend.", ["React"]),
    ("Uses Next.js 14 with FastAPI on Python 3.11.", []),                      # versions differ; the technology is the card's
    ("Uses NextJS and fastapi.", []),                                         # case and punctuation variants of supported terms
    ("Built the mobile app in React Native.", ["React Native"]),               # a longer product name is judged whole, not as React plus Native
    ("Built with ReactNative.", ["ReactNative"]),
    ("Services react to events and a spark of insight drives the design.", []),   # ordinary words are not products
    ("Systems React to load by scaling.", []),                                 # sentence-initial verb use
    ("React to load by scaling.", []),
    ("Uses Go and Rust for services.", []),                                    # ambiguous short names are never read from prose
    ("A React-based UI with TypeScript/React widgets.", ["React", "TypeScript"]),   # joined by a hyphen or a slash: still whole-word mentions
    ("LangGraph's agent plus Vue.js widgets.", ["Vue"]),                       # a possessive and a `.js` suffix do not hide the name
    ("The UI is built with Vue.", ["Vue"]),
    ("The UI is built with Svelte.", []),                                         # not in any saved card's structured stack: outside the vocabulary (disclosed limit)
])
def test_prose_technology_detection_controls(text, expected):
    vocab = vocabulary(["React", "React Native", "TypeScript", "Vue", "Go", "Rust", "Spark"], OWN["tech_stack"])
    assert P.prose_tech_claims(text, OWN, vocab) == expected


def test_react_native_is_not_taken_for_react_or_the_reverse():
    web = {"tech_stack": ["React 19"], "summary": "A web client."}
    mobile = {"tech_stack": ["React Native"], "summary": "A mobile client."}
    vocab = vocabulary(["React", "React Native"])
    assert P.prose_tech_claims("Built a React Native app.", web, vocab) == ["React Native"]
    assert P.prose_tech_claims("Built a React app.", web, vocab) == []
    assert P.prose_tech_claims("Built a React Native app.", mobile, vocab) == []


def test_other_repository_vocabulary_cannot_be_reassembled_into_support():
    other = {"tech_stack": ["Terraform", "Checkov"], "summary": "Infrastructure."}
    own = {"tech_stack": ["Python"], "summary": "Plain Terraform-free scripts, and native browser inputs.", "readme": "React, and also native tooling"}
    assert P.prose_tech_claims("Uses Terraform.", own, P.tech_vocabulary([own, other])) == ["Terraform"]
    assert P.prose_tech_claims("Uses React Native.", own, vocabulary(["React Native"])) == ["React Native"]      # React + native in different phrases


def test_vocabulary_is_data_from_the_saved_cards_not_a_list_in_the_code():
    st = packet()
    vocab = P.tech_vocabulary(cards(st), st["ranked"])
    assert "React 19" in vocab and "Terraform" in vocab and "Vue" not in vocab
    assert P.tech_vocabulary([]) == [] and P.prose_tech_claims("Uses React for the frontend.", OWN, []) == []


def test_short_variant_selection_decides_which_prose_is_checked():
    st = packet()
    collide = entry(st, COLLIDE)
    collide["short"] = "COLLIDE scores data center sites using React dashboards."
    st["short"] = [collide["repo"]]
    shown = [f for f in P.content_findings(st, cards(st)) if "describes technologies" in f]
    assert len(shown) == 1                                                       # the displayed short form names React; the unused paragraph's React is not reported twice
    collide["short"] = "COLLIDE evaluates data center sites by integrating spatial, economic, and reliability constraints."
    assert not [f for f in P.content_findings(st, cards(st)) if "describes technologies" in f]


# --- 3. the normal validation path and the real repair loop ---------------------------------------------------------------------------

def normal_job(tmp_path, monkeypatch, state):
    """A job folder whose resume_findings runs the real hard_issues and projects.json checks; layout/TeX/LLM machinery is stood in."""
    d = tmp_path / "job"
    d.mkdir()
    (d / "resume.pdf").write_bytes(b"%PDF")
    (tmp_path / "data" / "templates").mkdir(parents=True)
    (tmp_path / "data" / "templates" / "resume.tex").write_text("%")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    get_settings.cache_clear()
    (d / "filled.json").write_text(FILLED.model_dump_json())
    (d / "jd.json").write_text('{"title":"Engineer","company":"Acme"}')
    (d / "projects.json").write_text(json.dumps(state))
    monkeypatch.setattr(T, "Filled", SimpleNamespace(model_validate_json=lambda s: FILLED))
    monkeypatch.setattr(T, "parse", lambda text: SimpleNamespace(roles=[], skills=[], tex=text))
    monkeypatch.setattr(M.layout, "check", lambda _: M.layout.Layout(pages=1, free_pt=0))
    for name, value in (("duplicates", lambda *a, **k: []), ("quality", lambda *a, **k: []), ("fill_checks", lambda *a, **k: ([], 0)),
                        ("declared_claims", lambda: {})):
        monkeypatch.setattr(T, name, value)
    monkeypatch.setattr(M, "summary_issues", lambda *a: [])
    monkeypatch.setattr(M, "_title_prefix", lambda *a: True)
    monkeypatch.setattr("jobagent.tools.skillgraph.violations", lambda *a: [])
    return d


def test_normal_resume_findings_rejects_the_actual_packet_for_both_defects_and_not_for_the_clean_ones(tmp_path, monkeypatch):
    found = D.resume_findings(normal_job(tmp_path, monkeypatch, bound(packet(), keep=(CLOUD, COLLIDE))))
    assert found["evidence"]
    text = "\n".join(found["hard"])
    assert "compliant cloud setups" in text and "'React'" in text
    source = [h for h in found["hard"] if h.startswith("project ") and "source card" not in h and "template title" not in h]
    assert len(source) == 2                                                      # the two source defects; the bound clean projects add nothing
    unbound = {h.split(" ")[1] for h in found["hard"] if "source card" in h}
    assert unbound == {"1", "2"}                                                 # and the two captured (freeform) projects are also not source-bound


def test_normal_resume_findings_accepts_the_faithful_variants(tmp_path, monkeypatch):
    st = bound(packet())          # fixture contract: faithful variants are whole sentences of their own card (the freeform wording this test patched is no longer a valid source)
    assert D.resume_findings(normal_job(tmp_path, monkeypatch, st))["hard"] == []


def test_a_legacy_packet_without_saved_evidence_still_fails_closed(tmp_path, monkeypatch):
    st = packet()
    del st["evidence"]
    hard = D.resume_findings(normal_job(tmp_path, monkeypatch, st))["hard"]
    assert hard and all("no saved source evidence" in h for h in hard)


def test_evidence_is_the_jobs_own_snapshot_not_the_changing_shared_cache(tmp_path, monkeypatch):
    d = normal_job(tmp_path, monkeypatch, packet())
    before = D.resume_findings(d)["hard"]
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "github").mkdir(exist_ok=True)
    assert D.resume_findings(d)["hard"] == before


def loop(tmp_path, monkeypatch, incremental, repair, st=None):
    """Run `edit_sections` for real, the projects agent answering `repair()`; returns what the findings were and where the state is."""
    d = tmp_path / ("inc" if incremental else "def")
    d.mkdir()
    st = st or packet()
    (d / "projects.json").write_text(json.dumps(st))
    ctx = {"job_dir": d, "log": lambda *_: None, "incremental": incremental, "r": SimpleNamespace(roles=[], tex=""), "jd": SimpleNamespace(title="E"), "models": {}}
    monkeypatch.setattr(T, "normalize_originals", lambda r, g: g)
    monkeypatch.setattr(M, "ensure_incremental_coverage", lambda c, g: g)
    monkeypatch.setattr(M, "_run_agent", lambda c, agent, ff, tasks, last: (agent, repair() if agent == "projects" else None))
    tasks = {"projects": P.content_findings(st, cards(st))}
    assert len(tasks["projects"]) == 2
    return ctx, tasks, d


def round_(ctx, tasks, d):
    """One edit round; returns the saved state and the loop result."""
    out = M.edit_sections({"ctx": ctx, "f": FILLED, "tasks": tasks, "last": ""})
    return json.loads((d / "projects.json").read_text()), out


@pytest.mark.parametrize("incremental", [True, False])
@pytest.mark.parametrize("kind", ["none", "unchanged"])
def test_none_or_unchanged_repair_reaches_faithful_recovery_for_both_source_findings(tmp_path, monkeypatch, incremental, kind):
    original = bound(packet(), keep=(CLOUD, COLLIDE))
    if kind == "none":
        repair = lambda: None
    else:
        repair = lambda: M.IncrementalProjectsOut(projects=[M.ProjectEdit(project=1, paragraph=entry(original, CLOUD)["paragraph"]),
                                                              M.ProjectEdit(project=2, paragraph=entry(original, COLLIDE)["paragraph"])])
    ctx, tasks, d = loop(tmp_path, monkeypatch, incremental, repair, original)
    st, out = round_(ctx, tasks, d)
    assert not P.content_findings(st, cards(st)) and not P.binding_findings(st)
    assert "compliant" not in entry(st, CLOUD)["paragraph"] and "React" not in entry(st, COLLIDE)["paragraph"]
    for repo in (CLOUD, COLLIDE):                                                   # the replacement is the card's own complete sentences, nothing new
        for part in entry(st, repo)["paragraph"].split(". "):
            assert any(part.rstrip(".") in v for v in P._card_strings(cards(st)[repo]))
    assert entry(st, "BhavyaShah1234/AgenticReportGeneration")["paragraph"] == entry(original, "BhavyaShah1234/AgenticReportGeneration")["paragraph"]
    if incremental:
        assert out.get("layout_pass") != M.LAYOUT_PASSES                            # recovery changed the content: not "no progress"


def test_a_changed_but_still_unsupported_repair_gets_one_more_try_before_recovery(tmp_path, monkeypatch):
    # The writer can no longer change a description with prose (a rewrite is refused), so "changed but still wrong" is a repair that re-selects
    # another variant by source sentence number while the defective full description stays.
    original = bound(packet(), keep=(CLOUD, COLLIDE))
    sentences = P.source_sentences(cards(original)[COLLIDE])
    other = next(i for i, s in enumerate(sentences, 1) if SHORT_MIN <= len(s) <= 240 and s != entry(original, COLLIDE)["short"])
    attempts = iter([M.ProjectEdit(project=2, short_sentences=[other]), M.ProjectEdit(project=2, short_sentences=[other])])
    repair = lambda: M.IncrementalProjectsOut(projects=[next(attempts)])
    ctx, tasks, d = loop(tmp_path, monkeypatch, False, repair, original)
    st, _ = round_(ctx, tasks, d)
    assert "React" in entry(st, COLLIDE)["paragraph"]                               # first failed repair: it changed something, the model gets another try
    assert entry(st, COLLIDE)["short"] == sentences[other - 1] != entry(original, COLLIDE)["short"]
    tasks = {"projects": P.content_findings(st, cards(st))}
    st, _ = round_(ctx, tasks, d)                                                   # the second repair changes nothing: recovery from the card's own sentences
    assert "React" not in entry(st, COLLIDE)["paragraph"] and not [f for f in P.content_findings(st, cards(st)) if "describes technologies" in f]


def test_a_card_with_no_faithful_complete_text_keeps_the_blocker_and_invents_nothing(tmp_path, monkeypatch):
    st = packet()
    st["evidence"][COLLIDE] = {**st["evidence"][COLLIDE], "summary": "Scores sites (e.g.", "results": ["fragment"], "evidence_notes": "", "readme": ""}
    ctx, tasks, d = loop(tmp_path, monkeypatch, True, lambda: None, st)
    before = entry(st, COLLIDE)["paragraph"]
    saved, out = round_(ctx, tasks, d)
    assert entry(saved, COLLIDE)["paragraph"] == before
    assert [f for f in P.content_findings(saved, cards(saved)) if "describes technologies" in f]


def test_fatal_configuration_errors_are_not_swallowed_by_recovery(tmp_path, monkeypatch):
    def boom():
        raise M.llm.LLMConfigError("no model configured")
    ctx, tasks, d = loop(tmp_path, monkeypatch, True, boom)
    with pytest.raises(M.llm.LLMConfigError):
        round_(ctx, tasks, d)
    assert json.loads((d / "projects.json").read_text()) == PACKET


def test_ordinary_benefits_and_supported_text_survive_a_recovery_round(tmp_path, monkeypatch):
    # Contract change, reported: an unsourced qualitative clause ("reducing manual intervention", absent from the card) is exactly the freeform
    # prose the source-sentence contract removes, so it no longer survives; what survives is every source-bound variant and whole source sentences.
    original = bound(packet(), keep=(CLOUD, COLLIDE))
    ctx, tasks, d = loop(tmp_path, monkeypatch, True, lambda: None, original)
    st, _ = round_(ctx, tasks, d)
    agentic = "BhavyaShah1234/AgenticReportGeneration"
    assert entry(st, agentic) == entry(original, agentic)
    assert "reducing manual intervention" not in entry(st, CLOUD)["short"] and P.bound_text(entry(st, CLOUD)["short"], cards(st)[CLOUD])
    assert not P.binding_findings({**st, "ranked": [entry(st, CLOUD), entry(st, COLLIDE)], "kept": 2})


def test_stack_terms_a_card_does_not_show_are_dropped_by_recovery():
    st = packet()
    collide = entry(st, COLLIDE)
    collide["stack"] = ["Python", "React", "FastAPI"]
    assert P.recover_stack(collide, cards(st)[COLLIDE])
    assert "React" not in collide["stack"] and "FastAPI" in collide["stack"] and not P.recover_stack(collide, cards(st)[COLLIDE])


def test_a_round_without_a_source_finding_never_counts_toward_recovery(tmp_path, monkeypatch):
    ctx, tasks, d = loop(tmp_path, monkeypatch, True, lambda: None)
    tasks = {"projects": ["Shorten project 1 to fit."]}
    st, _ = round_(ctx, tasks, d)
    assert entry(st, COLLIDE)["paragraph"] == entry(PACKET, COLLIDE)["paragraph"] and not ctx.get("scope_failures")


# --- source denial / plan and negated lists (follow-up to the independent controls) ------------------------------------------------------------
def _clause_findings(text, summary, stack=('Python', 'FastAPI')):
    entry = {'repo': 'o/s', 'title': 'S', 'stack': ['Python'], 'paragraph': text, 'short': text}
    cards = {'o/s': {'full_name': 'o/s', 'summary': summary, 'tech_stack': list(stack)},
             'o/x': {'full_name': 'o/x', 'summary': 'React TypeScript Vue app', 'tech_stack': ['React', 'TypeScript', 'Vue']}}
    return P.content_findings({'ranked': [entry], 'kept': 1, 'short': []}, cards)


def test_negated_technology_lists_are_not_claims_but_other_names_still_are():
    assert not _clause_findings('Built a Python service with no React, TypeScript or Vue.', 'Built a Python API.')
    assert not _clause_findings('Built a Python service, not React.', 'Built a Python API.')
    assert _clause_findings('Built a Python service without tests; React frontend.', 'Built a Python API.')
    assert _clause_findings('Built a Python API without Vue, plus a React frontend.', 'Built a Python API.')
    assert _clause_findings('Built a Python API, not only Vue but also React.', 'Built a Python API.')


def test_source_denials_and_plans_in_any_phrasing_do_not_support_the_claim():
    for summary in ('Built a Python API. No React is used.', 'Built a Python API, without React.',
                    'Built a Python API. Future work: React.', 'Built a Python API. We may add React later.'):
        assert _clause_findings('Built a React frontend.', summary), summary


def test_an_affirmed_clause_survives_a_denied_neighbour():
    assert not _clause_findings('Built a React frontend.', 'Built a React frontend, but no Vue. Planned: TypeScript.')


def test_project_rejection_cannot_cite_owner_policy_but_a_confirmation_may(tmp_path, monkeypatch):
    import pytest
    from test_generation_reliability import _project_world, _verdict
    from jobagent.steps import multi_agent as M
    R, ctx, value = _project_world(tmp_path, monkeypatch, 'Built a React frontend.')
    finding = M.Finding(agent='projects', severity='error', issue='React is not shown by this project card.')
    monkeypatch.setattr(R.llm, 'structured_invoke', lambda model, schema, messages, **kw:
                        schema.model_validate(_verdict(0, 'confirmed', 'React is absent from the card.', source_id='owner_policy')))
    assert R.adjudicate(ctx, value, [finding])
