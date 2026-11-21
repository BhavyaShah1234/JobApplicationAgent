"""Independent existing-API controls for source construction, authored before reviewing the sentence implementation."""
import json
import re

import pytest
from jobagent.config import get_settings
from jobagent.tools import documents as D, projects as P

FIRST = "Built a Python event service that validates incoming sensor readings and stores timestamped observations for authorized operators to review in a dashboard."
SECOND = "The prototype exposes read-only status pages, and automated tests cover malformed input, bounded report exports, authentication and scoped access checks."


@pytest.fixture(autouse=True)
def isolate_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def card(repo="owner/sensor", summary=None):
    return {"full_name": repo, "url": "https://github.com/" + repo, "title": "Sensor Event Service",
            "summary": summary or FIRST + " " + SECOND, "results": [], "tech_stack": ["Python"],
            "head_sha": "a" * 40, "owned": True, "kind": "project", "created_at": "2026-01-01T00:00:00Z"}


def complete_source_text(text, own):
    allowed = {re.sub(r"\s+", " ", s).strip()
               for block in [own["summary"], *own.get("results", [])]
               for s in re.split(r"(?<=[.!?])\s+", block) if s.strip()}
    return bool(text) and all(re.sub(r"\s+", " ", s).strip() in allowed
                              for s in re.split(r"(?<=[.!?])\s+", text) if s.strip())


def test_factory_returns_only_complete_own_sentences_when_facts_fit():
    own = card()
    entry = P.from_card(own)
    assert entry is not None
    assert complete_source_text(entry.paragraph, own)
    if entry.short:
        assert complete_source_text(entry.short, own)


def test_factory_cannot_turn_a_cut_sentence_into_a_fake_complete_fact():
    own = card(summary="The prototype " + "stores bounded sensor observations for later review, " * 15
                       + "and its robot controls are simulated rather than connected to hardware.")
    try:
        entry = P.from_card(own)
    except ValueError:
        return  # no whole facts fit: rejecting this candidate is valid, fabricating a terminator is not
    if entry is None:
        return
    assert complete_source_text(entry.paragraph, own)
    if entry.short:
        assert complete_source_text(entry.short, own)


def fresh_normal_job(tmp_path, monkeypatch):
    from test_scope_evidence_flow import normal_job
    own = card()
    entry = P.from_card(own)
    assert entry is not None
    state = {"ranked": [entry.model_dump()], "kept": 1, "short": [],
             "evidence": {own["full_name"]: P.evidence_snapshot(own)}}
    job = normal_job(tmp_path, monkeypatch, state)
    chosen = P.Chosen(entries=[entry], candidates=[(1.0, own)], years={own["full_name"]: "2026"},
                      notes="Independent same-source fixture.")
    P.save_state(job, chosen, [entry])  # real production serializer supplies any new source-binding metadata
    return job, own


def test_a_fresh_serialized_source_bound_project_passes_normal_validation(tmp_path, monkeypatch):
    job, _ = fresh_normal_job(tmp_path, monkeypatch)
    assert D.resume_findings(job)["hard"] == []


@pytest.mark.parametrize("invented", [
    "The platform enables real-time control of physical robotic systems.",
    "The platform automatically reconciles cross-border financial settlements.",
])
def test_normal_validation_rejects_new_arbitrary_features_not_just_known_bad_words(tmp_path, monkeypatch, invented):
    job, _ = fresh_normal_job(tmp_path, monkeypatch)
    p = job / "projects.json"
    state = json.loads(p.read_text())
    state["ranked"][0]["paragraph"] = invented
    p.write_text(json.dumps(state))
    assert D.resume_findings(job)["hard"]


def test_a_copied_body_from_another_project_cannot_reuse_the_original_binding(tmp_path, monkeypatch):
    job, _ = fresh_normal_job(tmp_path, monkeypatch)
    p = job / "projects.json"
    state = json.loads(p.read_text())
    other = card("owner/other", summary="Built a Python image service that labels uploaded photographs for later review.")
    state.setdefault("evidence", {})["owner/other"] = P.evidence_snapshot(other)
    state["ranked"][0]["paragraph"] = other["summary"]
    p.write_text(json.dumps(state))
    assert D.resume_findings(job)["hard"]


def test_an_unbound_legacy_freeform_packet_cannot_silently_pass_normal_validation(tmp_path, monkeypatch):
    from test_scope_evidence_flow import normal_job
    own = card()
    text = "The platform enables real-time control of physical robotic systems."
    entry = {"repo": own["full_name"], "title": own["title"], "stack": ["Python"],
             "paragraph": text, "short": text}
    state = {"ranked": [entry], "kept": 1, "short": [],
             "evidence": {own["full_name"]: P.evidence_snapshot(own)}}
    # Deliberately no modern P.save_state call: this is a genuine prior freeform packet with an own-card snapshot.
    job = normal_job(tmp_path, monkeypatch, state)
    assert D.resume_findings(job)["hard"]
