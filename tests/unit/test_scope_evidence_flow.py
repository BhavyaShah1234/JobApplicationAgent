"""The scope checks run on the ACTUAL normal validation path (a job's saved evidence), and recovery is reached in the real repair loop."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobagent.config import get_settings
from jobagent.steps import multi_agent as M
from jobagent.tools import documents as D, projects as P, tailor as T

@pytest.fixture(autouse=True)
def isolate_settings_cache():
    """A temporary DATA_DIR must not remain cached after monkeypatch restores the environment."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


SOLBOT = json.loads((Path(__file__).parent.parent / "fixtures" / "solbot_scope_card.json").read_text())
FAILED = {"repo": "BhavyaShah1234/SolBot", "title": "SolBot", "stack": ["Python", "Ollama", "Chroma", "LangChain", "SQLite"],
          "paragraph": "SolBot is a fully local, self-auditing RAG chatbot designed to answer questions about ASU Research Computing using only official documentation. "
                       "The system decomposes compound questions into sub-questions, researches each independently, and audits findings for groundedness before "
                       "streaming cited replies. It ensures robustness through input sanitization, adversarial fuzz testing, and a self-auditing answer loop.",
          # fixture contract (source-sentence binding): the title is the card's own ("Multi-Agent" is not in it) and the short variant is the card's results[1]
          "short": SOLBOT["results"][1]}
SENTENCES = P.source_sentences(SOLBOT)
# a faithful packet: whole sentences of the card (its summary's first sentence and its results[0]), nothing else
CLEAN = {**FAILED, "paragraph": f"{SENTENCES[0]} {SENTENCES[4]}"}
SENTINEL = "Built the platform, a fully local RAG chatbot."


def evidence_state(entry, evidence=True, card=SOLBOT):
    """A saved projects.json; `evidence` False is a file written before evidence snapshots."""
    st = {"ranked": [dict(entry)], "kept": 1, "short": [], "years": {}, "macro": False,
          "cards": {entry["repo"].lower(): {"url": "u", "full_name": entry["repo"]}}}
    if evidence:
        st["evidence"] = {entry["repo"].lower(): P.evidence_snapshot(card)}
    return st


FILLED = T.Filled.model_construct(summary="Engineer with Python experience.", roles=[], skills=[], notes="")


def normal_job(tmp_path, monkeypatch, state, d=None):
    """A job folder whose resume_findings runs the real hard_issues and projects.json check; only layout/TeX/LLM machinery is stood in."""
    if d is None:
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
    monkeypatch.setattr(T, "duplicates", lambda *a, **k: [])
    monkeypatch.setattr(T, "quality", lambda *a, **k: [])
    monkeypatch.setattr(T, "fill_checks", lambda *a, **k: ([], 0))
    monkeypatch.setattr(M, "summary_issues", lambda *a: [])
    monkeypatch.setattr(M, "_title_prefix", lambda *a: True)
    monkeypatch.setattr(T, "declared_claims", lambda: {})
    monkeypatch.setattr("jobagent.tools.skillgraph.violations", lambda *a: [])
    return d


def test_normal_resume_findings_rejects_the_exact_failed_packet_from_its_own_saved_evidence(tmp_path, monkeypatch):
    d = normal_job(tmp_path, monkeypatch, evidence_state(FAILED))
    found = D.resume_findings(d)
    assert found["evidence"] and any("claims a restriction" in h and "only official documentation" in h for h in found["hard"])
    assert not (tmp_path / "data" / "repo_cards").exists()              # no shared card cache was needed or read


def test_normal_resume_findings_accepts_a_packet_whose_own_evidence_supports_it(tmp_path, monkeypatch):
    found = D.resume_findings(normal_job(tmp_path, monkeypatch, evidence_state(CLEAN)))
    assert found["evidence"] and found["hard"] == []


def test_missing_saved_evidence_is_a_blocker_never_a_silent_pass(tmp_path, monkeypatch):
    found = D.resume_findings(normal_job(tmp_path, monkeypatch, evidence_state(FAILED, evidence=False)))
    assert found["evidence"] and any("no saved source evidence" in h for h in found["hard"])


def test_evidence_saved_under_another_repository_cannot_stand_in(tmp_path, monkeypatch):
    state = evidence_state(FAILED, evidence=False)
    state["evidence"] = {"bhavyashah1234/solbot": P.evidence_snapshot({**SOLBOT, "full_name": "someone/else"})}
    found = D.resume_findings(normal_job(tmp_path, monkeypatch, state))
    assert any("no saved source evidence" in h for h in found["hard"])


@pytest.mark.parametrize("evidence, expected", [(True, "claims a restriction"), (False, "no saved source evidence")])
def test_refresh_cannot_leave_an_old_bad_packet_marked_usable(tmp_path, monkeypatch, evidence, expected):
    from test_documents import GOOD_RESUME, assess, job
    d = job(tmp_path)
    D.save(d, assess(d, resume=GOOD_RESUME))                                  # the build recorded it as valid
    assert D.load(d)["valid"] is True
    normal_job(tmp_path, monkeypatch, evidence_state(FAILED, evidence=evidence), d)
    new = D.refresh(d, "warn", 90.0)
    assert new["valid"] is False and any(expected in h for h in new["resume"]["hard_issues"]) and D.verify(d)["ok"] is False


def test_a_freshly_saved_state_carries_the_evidence_it_was_written_from(tmp_path):
    card = {**SOLBOT, "embedding": [0.1] * 8}
    entry = P.ProjectEntry(**{k: v for k, v in FAILED.items()})
    chosen = SimpleNamespace(candidates=[(1.0, {**card, "url": "u", "full_name": "BhavyaShah1234/SolBot"})], entries=[entry], short=frozenset(), years={}, macro=False)
    P.save_state(tmp_path, [entry], chosen)
    saved = json.loads((tmp_path / "projects.json").read_text())
    assert "embedding" not in saved["evidence"]["bhavyashah1234/solbot"] and saved["evidence"]["bhavyashah1234/solbot"]["summary"] == SOLBOT["summary"]
    ctx = {"job_dir": tmp_path, "log": lambda *_: None}                  # no ctx cards: the saved evidence alone
    assert any("only official documentation" in f for f in P.content_findings(saved, M._cards_for(ctx, saved)))
    assert not P.missing_evidence(saved, M._cards_for(ctx, saved))


def test_run_supplied_cards_win_over_the_saved_snapshot():
    saved = evidence_state(FAILED, card={**SOLBOT, "summary": "x"})
    fresh = {"bhavyashah1234/solbot": SOLBOT}
    assert M._cards_for({"cards": fresh}, saved)["bhavyashah1234/solbot"]["summary"] == SOLBOT["summary"]


# --- recovery inside the real repair loop -------------------------------------------------------------------------------------------

def loop(tmp_path, monkeypatch, incremental, repair, card=SOLBOT, entry=FAILED):
    """Run `edit_sections` for real with the projects agent answering `repair(ctx_state)`."""
    d = tmp_path / ("inc" if incremental else "def")
    d.mkdir()
    st = evidence_state(entry, card=card)
    (d / "projects.json").write_text(json.dumps(st))
    ctx = {"job_dir": d, "log": lambda *_: None, "incremental": incremental, "r": SimpleNamespace(roles=[], tex=""), "jd": SimpleNamespace(title="E"), "models": {}}
    f = T.Filled.model_construct(summary="Engineer with Python experience.", roles=[], skills=[], notes="")
    monkeypatch.setattr(T, "normalize_originals", lambda r, g: g)
    monkeypatch.setattr(M, "ensure_incremental_coverage", lambda c, g: g)
    monkeypatch.setattr(M, "_run_agent", lambda c, agent, ff, tasks, last: (agent, repair() if agent == "projects" else None))
    tasks = {"projects": P.content_findings(json.loads((d / "projects.json").read_text()), {entry["repo"].lower(): card})}
    assert tasks["projects"], "the starting packet must carry the scope finding"
    return ctx, f, tasks, d


def run_round(ctx, f, tasks, d):
    """One edit round; returns the saved entry and the loop's result."""
    out = M.edit_sections({"ctx": ctx, "f": f, "tasks": tasks, "last": ""})
    return json.loads((d / "projects.json").read_text())["ranked"][0], out


@pytest.mark.parametrize("incremental", [True, False])
def test_unavailable_project_repair_reaches_faithful_recovery_in_the_real_loop(tmp_path, monkeypatch, incremental):
    ctx, f, tasks, d = loop(tmp_path, monkeypatch, incremental, lambda: None)      # malformed/unavailable: the agent returns nothing
    entry, out = run_round(ctx, f, tasks, d)
    assert " only " not in entry["paragraph"] and entry["paragraph"] != FAILED["paragraph"] and entry["short"] == FAILED["short"]
    assert not P.content_findings(json.loads((d / "projects.json").read_text()), {"bhavyashah1234/solbot": SOLBOT})
    if incremental:
        assert out.get("layout_pass") != M.LAYOUT_PASSES                            # recovery changed the content: not "no progress"


@pytest.mark.parametrize("incremental", [True, False])
def test_unchanged_project_repair_reaches_faithful_recovery_in_the_real_loop(tmp_path, monkeypatch, incremental):
    unchanged = lambda: M.IncrementalProjectsOut(projects=[M.ProjectEdit(project=1, paragraph=FAILED["paragraph"])])
    ctx, f, tasks, d = loop(tmp_path, monkeypatch, incremental, unchanged)
    entry, _ = run_round(ctx, f, tasks, d)
    assert " only " not in entry["paragraph"] and entry["paragraph"] != FAILED["paragraph"]


def test_a_changed_but_still_wrong_repair_gets_one_more_try_then_recovers(tmp_path, monkeypatch):
    # The writer cannot change a description with prose any more (refused), so a "changed" repair re-selects the short variant by source sentence
    # number while the defective full description stays; an identical second repair changes nothing and goes to recovery.
    other = next(i for i, s in enumerate(SENTENCES, 1) if 100 <= len(s) <= 240 and s != FAILED["short"])
    attempts = iter([other, other])
    repair = lambda: M.IncrementalProjectsOut(projects=[M.ProjectEdit(project=1, short_sentences=[next(attempts)])])
    ctx, f, tasks, d = loop(tmp_path, monkeypatch, False, repair)
    entry, _ = run_round(ctx, f, tasks, d)
    assert "only official" in entry["paragraph"] and entry["short"] == SENTENCES[other - 1]       # first failed repair: another model try
    tasks = {"projects": P.content_findings(json.loads((d / "projects.json").read_text()), {"bhavyashah1234/solbot": SOLBOT})}
    entry, _ = run_round(ctx, f, tasks, d)
    assert " only " not in entry["paragraph"] and P.bound_text(entry["paragraph"], SOLBOT)


def test_no_faithful_card_text_keeps_the_blocker_and_invents_nothing(tmp_path, monkeypatch):
    card = {**SOLBOT, "summary": "Answers using only official documentation (e.g.", "results": ["fragment"], "readme": ""}
    ctx, f, tasks, d = loop(tmp_path, monkeypatch, True, lambda: None, card={**card, "summary": "Retrieves official documentation (e.g."})
    entry, out = run_round(ctx, f, tasks, d)
    assert entry["paragraph"] == FAILED["paragraph"]
    assert out.get("layout_pass") == M.LAYOUT_PASSES                                  # nothing improved: the loop stops and hard_issues still rejects
    assert P.content_findings(json.loads((d / "projects.json").read_text()), {"bhavyashah1234/solbot": {**card, "summary": "Retrieves official documentation (e.g."}})


def test_fatal_configuration_errors_are_not_swallowed_by_recovery(tmp_path, monkeypatch):
    def boom():
        raise M.llm.LLMConfigError("no model configured")
    ctx, f, tasks, d = loop(tmp_path, monkeypatch, True, boom)
    with pytest.raises(M.llm.LLMConfigError):
        run_round(ctx, f, tasks, d)
    assert json.loads((d / "projects.json").read_text())["ranked"][0]["paragraph"] == FAILED["paragraph"]


def test_rounds_without_a_scope_finding_never_count_toward_recovery(tmp_path, monkeypatch):
    ctx, f, tasks, d = loop(tmp_path, monkeypatch, True, lambda: None)
    tasks = {"projects": ["Shorten project 1 to fit."]}
    entry, _ = run_round(ctx, f, tasks, d)
    assert entry["paragraph"] == FAILED["paragraph"] and not ctx.get("scope_failures")


# --- a complete source sentence with a normal possessive is kept --------------------------------------------------------------------

def test_a_complete_source_sentence_with_a_possessive_is_preserved():
    card = {"summary": "The platform connects to a company's warehouse and returns validated summaries. Customers' reports stay in their own tenant.", "results": []}
    text = P.faithful_description(card, 400, 100)
    assert text and "company's warehouse" in text and "Customers' reports" in text


@pytest.mark.parametrize("sentence", ["It said 'ready to ship and more.", "The tool answers (see 'setup guide.", 'It reads "the config file and loads it.'])
def test_an_unfinished_quote_is_still_not_a_complete_sentence(sentence):
    assert P.faithful_description({"summary": sentence, "results": []}, 400, 40) is None


@pytest.mark.parametrize("stored", [[{"full_name": "owner/a"}], "invalid", True, 42])
def test_malformed_snapshot_is_a_normal_validation_blocker_not_a_crash(tmp_path, monkeypatch, stored):
    state = evidence_state(CLEAN, evidence=False)
    state["evidence"] = stored
    assert P.saved_evidence(state) == {}
    found = D.resume_findings(normal_job(tmp_path, monkeypatch, state))
    assert found["evidence"] and any("no saved source evidence" in h for h in found["hard"])
