"""Manual review regression for the source constructor's title proof."""
import json
import pytest
from jobagent.config import get_settings

from jobagent.tools import documents as D, projects as P


@pytest.fixture(autouse=True)
def isolate_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_negative_source_title_cannot_be_shortened_into_a_certification_claim(tmp_path, monkeypatch):
    from test_independent_project_sentence_controls import card
    from test_scope_evidence_flow import normal_job
    own = card()
    own["title"] = "Not HIPAA Certified Sensor Service"
    entry = P.from_card(own)
    assert entry is not None
    state = {"ranked": [entry.model_dump()], "kept": 1, "short": [],
             "evidence": {own["full_name"]: P.evidence_snapshot(own)}}
    job = normal_job(tmp_path, monkeypatch, state)
    chosen = P.Chosen(entries=[entry], candidates=[(1.0, own)],
                      years={own["full_name"]: "January 2026"}, notes="Title qualifier control.")
    P.save_state(job, [entry], chosen)
    assert D.resume_findings(job)["hard"] == []
    path = job / "projects.json"
    stored = json.loads(path.read_text())
    stored["ranked"][0]["title"] = "HIPAA Certified Sensor Service"
    path.write_text(json.dumps(stored))
    assert D.resume_findings(job)["hard"]
