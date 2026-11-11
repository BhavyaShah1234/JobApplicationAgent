import typer
from rich.console import Console
from typer.testing import CliRunner

from jobagent import cli_outcomes, db
from jobagent.config import get_settings


def make(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    app = typer.Typer()
    cli_outcomes.register(app, Console(width=220, force_terminal=False))
    conn = db.connect()
    conn.execute("INSERT INTO applications (job_dir, company, title, state, submitted_at) VALUES "
                 "('o/a', 'Stripe', 'Backend Engineer', 'submitted', '2026-11-01T12:00:00Z')")
    for i, (stage, review) in enumerate([("confirmation", 0), ("interview", 1)], 1):
        conn.execute("INSERT INTO outcome_events (application_id, stage, message_id, subject, excerpt, received_at, classifier, "
                     "confidence, needs_review) VALUES (1, ?, ?, ?, 'x', ?, 'model', 0.6, ?)",
                     (stage, f"<m{i}>", f"subject {i}", f"2026-11-0{i + 2}T10:00:00Z", review))
    conn.commit()  # the commands open their own connections
    return app, conn


def test_outcomes_lists_status_and_the_review_queue(tmp_path, monkeypatch):
    app, conn = make(tmp_path, monkeypatch)
    out = CliRunner().invoke(app, ["outcomes"]).output
    assert "Stripe" in out and "confirmation" in out and "interview" not in out     # the unapproved one isn't counted
    review = CliRunner().invoke(app, ["outcomes", "--review"]).output
    assert "interview" in review and "subject 2" in review
    get_settings.cache_clear()


def test_approve_reject_and_label(tmp_path, monkeypatch):
    app, conn = make(tmp_path, monkeypatch)
    r = CliRunner()
    assert "approved" in r.invoke(app, ["outcomes", "approve", "2"]).output
    assert "is not waiting" in r.invoke(app, ["outcomes", "approve", "2"]).output
    assert "interview" in r.invoke(app, ["outcomes"]).output
    assert "labelled" in r.invoke(app, ["outcomes", "label", "<m2>", "offer"]).output
    assert conn.execute("SELECT stage, classifier FROM outcome_events WHERE message_id = '<m2>'").fetchone()[:] == ("offer", "human")
    bad = r.invoke(app, ["outcomes", "label", "<m2>", "banana"])
    assert bad.exit_code != 0
    get_settings.cache_clear()


def test_sync_inbox_dry_run_reports_without_writing(tmp_path, monkeypatch):
    app, conn = make(tmp_path, monkeypatch)
    from jobagent.tools import inbox as inbox_mod

    class Box:
        def search(self, criteria, folder="INBOX"):
            return []
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(inbox_mod, "open_inbox", lambda: Box())
    out = CliRunner().invoke(app, ["sync-inbox", "--dry-run", "--no-model"]).output
    assert "1 submitted application(s), 0 message(s) read, 0 outcome(s) would store" in out
    assert conn.execute("SELECT COUNT(*) FROM kv WHERE key = 'outcome_last_sync'").fetchone()[0] == 0
    get_settings.cache_clear()


def test_stats_and_reflect_on_an_empty_and_a_small_history(tmp_path, monkeypatch):
    app, conn = make(tmp_path, monkeypatch)
    r = CliRunner()
    out = r.invoke(app, ["stats"]).output
    assert "1 submitted application(s)" in out and "too few" in out and "portal" in out
    csv = tmp_path / "s.csv"
    assert r.invoke(app, ["stats", "--csv", str(csv)]).exit_code == 0 and csv.read_text().startswith("dimension,")
    ref = r.invoke(app, ["reflect", "--dry-run"]).output
    assert "not enough data yet" in ref and "15 positive" in ref and "switches" in ref
    get_settings.cache_clear()
    conn2 = db.connect(tmp_path / "empty" / "jobs.sqlite") if (tmp_path / "empty").mkdir() is None else None
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "empty"))
    get_settings.cache_clear()
    assert "no submitted applications yet" in r.invoke(app, ["stats"]).output
    get_settings.cache_clear()
