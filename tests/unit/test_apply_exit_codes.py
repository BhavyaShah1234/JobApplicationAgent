"""F03: the exit status of `jobagent apply` / `submit` and what the queue records for each."""
import contextlib
import json
import subprocess
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from jobagent import db, main
from jobagent.config import get_settings
from jobagent.tools import jobqueue as Q
from jobagent.tools.locations import Decision
from jobagent.tools.matching import Match


def fake_world(monkeypatch, tmp_path, result):
    """`apply`/`submit` with the browser, mailbox, vault and the run itself replaced; the command code is real."""
    import jobagent.steps.apply as A
    import jobagent.steps.submit as S
    import jobagent.tools.browser as B
    import jobagent.tools.hitl as H
    import jobagent.tools.inbox as I
    import jobagent.tools.vault as V
    job = tmp_path / "outputs" / "acme" / "1"
    job.mkdir(parents=True)
    (job / "jd.json").write_text(json.dumps({"company": "Acme", "title": "Engineer", "apply_url": "https://e.test/a",
                                             "source_url": "https://e.test/a"}))
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    from jobagent.tools import documents as _D
    from test_documents import PDF as _PDF
    (job / "resume.pdf").write_bytes(_PDF)
    (job / "cover_letter.pdf").write_bytes(_PDF)
    _D.save(job, _D.assess(job, resume={"produced": True, "hard_issues": [], "claims_issues": []},
                           letter={"issues": [], "words": 300}, ats={"average": 93.0, "complete": True, "missing": [], "scored": True},
                           policy="warn", target=90.0))

    @contextlib.contextmanager
    def ctx_manager(*a, **k):
        yield SimpleNamespace(pages=[object()], jobagent_page=object())

    @contextlib.contextmanager
    def inbox_manager(*a, **k):
        yield object()

    monkeypatch.setattr(A, "run", lambda *a, **k: dict(result))
    monkeypatch.setattr(B, "application_context", ctx_manager)
    monkeypatch.setattr(B, "persistent_context", ctx_manager)
    monkeypatch.setattr(I, "open_inbox", inbox_manager)
    monkeypatch.setattr(H, "open_hitl", lambda log=print: object())
    monkeypatch.setattr(V, "open_vault", lambda: SimpleNamespace(get=lambda k: {"email": "agent@x.org"}))
    monkeypatch.setattr(S, "make_at_review", lambda *a, **k: None)
    return job


BASE = {"verified": 0, "fields": [], "flags": [], "url": "u"}


@pytest.mark.parametrize("result,code,words", [
    ({**BASE, "state": "review", "ready_to_submit": True, "blockers": []}, 0, ""),
    ({**BASE, "state": "review", "ready_to_submit": False, "blockers": ["Email (page 1): required, no answer was planned"]},
     5, "not ready: Email"),
    ({**BASE, "state": "blocked", "blocked_by": "planner", "reason": "the answer planner failed on page 1: RuntimeError: down",
      "ready_to_submit": False, "blockers": ["x"]}, 4, "blocked (planner)"),
    ({**BASE, "state": "blocked", "blocked_by": "challenge", "reason": "a cloudflare check blocks u"}, 4, "blocked (challenge)"),
    ({**BASE, "state": "stuck"}, 1, "not ready"),
    ({**BASE, "state": "failed"}, 1, ""),
])
def test_apply_exits_with_the_state_it_ended_in(monkeypatch, tmp_path, result, code, words):
    job = fake_world(monkeypatch, tmp_path, result)
    r = CliRunner().invoke(main.app, ["apply", str(job), "acme"])
    assert r.exit_code == code, r.output
    assert words in r.output
    get_settings.cache_clear()


@pytest.mark.parametrize("res,submission,code", [
    ({**BASE, "state": "review", "ready_to_submit": True, "submission": {"state": "submitted"}}, "submitted", 0),
    ({**BASE, "state": "review", "ready_to_submit": True, "submission": {"state": "confirmed"}}, "confirmed", 0),
    ({**BASE, "state": "review", "ready_to_submit": True, "submission": {"state": "skipped"}}, "skipped", 0),
    ({**BASE, "state": "review", "ready_to_submit": False, "submission": {"state": "skipped"}}, "skipped", 5),
    ({**BASE, "state": "review", "ready_to_submit": False, "submission": {"state": "not_ready"}}, "not_ready", 5),
    ({**BASE, "state": "review", "ready_to_submit": True, "submission": {"state": "failed"}}, "failed", 1),
    ({**BASE, "state": "blocked", "blocked_by": "planner", "reason": "down"}, None, 4),
    ({**BASE, "state": "stuck"}, None, 1),
])
def test_submit_exits_nonzero_unless_the_application_really_went_or_was_declined_while_ready(monkeypatch, tmp_path, res, submission, code):
    job = fake_world(monkeypatch, tmp_path, res)
    r = CliRunner().invoke(main.app, ["submit", str(job), "acme"])
    assert r.exit_code == code, r.output
    get_settings.cache_clear()


# --- the queue records the right state for each exit code -------------------------------------------------------------

@pytest.mark.parametrize("code,status", [(0, "filled"), (4, "blocked"), (5, "needs_attention"), (1, "failed"), (2, "failed"),
                                         (0, "blocked"), (1, "filled")])
def test_queue_run_records_the_real_state(monkeypatch, tmp_path, code, status):
    from jobagent import cli_discovery as C
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("OUTPUTS_DIR", str(tmp_path / "outputs"))
    get_settings.cache_clear()
    conn = db.connect()
    Q.enqueue(conn, Q.Listing("Acme", "Engineer", "https://e.test/jobs/1", ["Austin, TX"], req_id="1"),
              Match("queued", "regex", "swe", 0.9), Decision("us", "t", "Austin"))
    with conn:
        conn.execute("UPDATE jobs SET job_id = '1', company = 'Acme' WHERE req_id = '1'")

    from jobagent.tools.pipeline import Pipeline, PipelineResult
    monkeypatch.setattr(Pipeline, "run", lambda self, *a, **k: PipelineResult(status, code=code))
    import typer
    from rich.console import Console
    app = typer.Typer()
    C.register(app, Console(width=200))
    r = CliRunner().invoke(app, ["queue", "run", "--through", "apply", "--limit", "1"])
    assert r.exit_code == 0, r.output
    assert conn.execute("SELECT status FROM jobs WHERE req_id = '1'").fetchone()[0] == status
    get_settings.cache_clear()


def test_queue_run_leaves_an_unavailable_posting_as_the_stop_recorded_it(monkeypatch, tmp_path):
    from jobagent import cli_discovery as C
    from jobagent.tools import availability
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    conn = db.connect()
    Q.enqueue(conn, Q.Listing("Acme", "Engineer", "https://e.test/jobs/2", ["Austin, TX"], req_id="2"),
              Match("queued", "regex", "swe", 0.9), Decision("us", "t", "Austin"))
    with conn:
        conn.execute("UPDATE jobs SET job_id = '2', company = 'Acme' WHERE req_id = '2'")

    from jobagent.tools.pipeline import Pipeline, PipelineResult
    def fake_run(self, *a, **k):
        availability.mark("https://e.test/jobs/2", availability.Unavailable("closed", "no longer accepting", "u"), db.connect())
        return PipelineResult("closed", code=3)
    monkeypatch.setattr(Pipeline, "run", fake_run)
    import typer
    from rich.console import Console
    app = typer.Typer()
    C.register(app, Console(width=200))
    CliRunner().invoke(app, ["queue", "run", "--through", "apply", "--limit", "1"])
    assert conn.execute("SELECT status FROM jobs WHERE req_id = '2'").fetchone()[0] == "closed"   # not overwritten as "blocked"
    get_settings.cache_clear()
