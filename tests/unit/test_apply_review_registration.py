"""Successful apply must feed the real review queue and durable pacing count."""

import json

import pytest
from typer.testing import CliRunner

from jobagent import db, main
from jobagent.steps import apply, submit
from jobagent.tools import jobqueue, pacing, review_queue
from test_apply_exit_codes import BASE, fake_world


def review_world(monkeypatch, tmp_path, *, ready=True):
    result = {**BASE, "state": "review", "ready_to_submit": ready,
              "blockers": [] if ready else ["Required field remains unanswered"]}
    job = fake_world(monkeypatch, tmp_path, result)

    def fill(*args, **kwargs):
        (job / "application").mkdir(exist_ok=True)
        (job / "application/application.json").write_text(json.dumps(result))
        return result

    monkeypatch.setattr(apply, "run", fill)
    return job


@pytest.mark.parametrize("ready", [True, False])
def test_apply_registers_one_reviewable_draft_without_submission(monkeypatch, tmp_path, ready):
    job = review_world(monkeypatch, tmp_path, ready=ready)
    for _ in range(2):
        result = CliRunner().invoke(main.app, ["apply", str(job), "acme"])
        assert result.exit_code == (0 if ready else 5), result.output
    conn = db.connect()
    rows = review_queue.waiting(conn, root=tmp_path)
    assert len(rows) == 1
    assert rows[0]["ready"] is ready
    assert rows[0]["blockers"] == ([] if ready else ["Required field remains unanswered"])
    draft = conn.execute("SELECT * FROM applications").fetchone()
    assert draft["state"] == "filled" and draft["job_id"] is not None
    assert draft["filled_at"] and draft["submitted_at"] is None
    assert conn.execute("SELECT count(*) FROM submission_attempts").fetchone()[0] == 0
    conn.close()


def test_blocked_apply_does_not_create_a_review_draft(monkeypatch, tmp_path):
    result = {**BASE, "state": "blocked", "ready_to_submit": False, "blockers": ["challenge"]}
    job = fake_world(monkeypatch, tmp_path, result)
    outcome = CliRunner().invoke(main.app, ["apply", str(job), "acme"])
    assert outcome.exit_code == 4, outcome.output
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM applications").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("state", submit.SENT)
def test_review_registration_cannot_regress_a_sent_or_uncertain_application(monkeypatch, tmp_path, state):
    job = review_world(monkeypatch, tmp_path)
    conn = db.connect()
    submit.record(conn, job, json.loads((job / "jd.json").read_text()),
                  state=state, filled_at="2026-01-01T00:00:00Z")
    outcome = CliRunner().invoke(main.app, ["apply", str(job), "acme"])
    assert outcome.exit_code == 3, outcome.output
    row = conn.execute("SELECT state, filled_at FROM applications").fetchone()
    assert tuple(row) == (state, "2026-01-01T00:00:00Z")
    conn.close()


def test_apply_hands_claim_reservation_to_the_durable_pacing_count(monkeypatch, tmp_path):
    job = review_world(monkeypatch, tmp_path)
    conn = db.connect()
    jd = json.loads((job / "jd.json").read_text())
    first_id = db.ensure_job(conn, jd, db.job_key(job))
    with conn:
        conn.execute("UPDATE jobs SET status='queued', company_slug='acme', match_score=1 WHERE id=?", (first_id,))
        conn.execute("INSERT INTO jobs(url,company,company_slug,status,match_score) VALUES "
                     "('https://e.test/b','Acme Inc.','acme-inc','queued',0.9)")
    first = jobqueue.claim(conn, "worker-a", pace=(1, 7))
    assert first["id"] == first_id
    outcome = CliRunner().invoke(main.app, ["apply", str(job), "acme"])
    assert outcome.exit_code == 0, outcome.output
    assert pacing.active_counts(conn) == {}
    assert pacing.recent_counts(conn, 7) == {"acme": 1}
    assert jobqueue.finish(conn, first["url"], "worker-a", "review")
    assert jobqueue.claim(conn, "worker-b", pace=(1, 7)) is None
    conn.close()
