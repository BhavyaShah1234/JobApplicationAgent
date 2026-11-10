import typer
from rich.console import Console
from typer.testing import CliRunner

from jobagent import cli_discovery, db
from jobagent.config import get_settings
from jobagent.tools import jobqueue as Q
from jobagent.tools.locations import Decision
from jobagent.tools.matching import Match

US = Decision("us", "text", "Austin, TX")


def make(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    app = typer.Typer()
    cli_discovery.register(app, Console(width=200, force_terminal=False))
    conn = db.connect()
    for i, (title, m) in enumerate([("Software Engineer I", Match("queued", "regex", "swe", 0.9)),
                                    ("Data Analyst", Match("needs_review", "semantic", "data", 0.62, "semantic match")),
                                    ("Barista", Match("skipped", "none", reason="no target role fits"))], 1):
        Q.enqueue(conn, Q.Listing("Acme", title, f"https://acme.com/jobs/{i}", ["Austin, TX"], req_id=str(i)), m, US)
    return app, conn


def test_queue_lists_by_status_and_review(tmp_path, monkeypatch):
    app, conn = make(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["queue"])
    assert r.exit_code == 0 and "Software Engineer I" in r.output and "Data Analyst" not in r.output
    assert "1 needs_review" in r.output and "1 queued" in r.output and "1 skipped" in r.output
    assert "Data Analyst" in CliRunner().invoke(app, ["queue", "--review"]).output
    assert "Barista" in CliRunner().invoke(app, ["queue", "--status", "skipped"]).output
    get_settings.cache_clear()


def test_approve_and_reject_only_touch_open_jobs(tmp_path, monkeypatch):
    app, conn = make(tmp_path, monkeypatch)
    ids = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM jobs")}
    out = CliRunner().invoke(app, ["queue", "approve", str(ids["Data Analyst"])]).output
    assert f"job {ids['Data Analyst']} -> queued" in out
    Q.mark(conn, "https://acme.com/jobs/1", "running")
    out = CliRunner().invoke(app, ["queue", "reject", str(ids["Software Engineer I"]), "999"]).output
    assert "already running" in out and "no job 999" in out
    assert {r["title"] for r in Q.pending(conn)} == {"Data Analyst"}
    get_settings.cache_clear()


def test_discover_refuses_job_urls_and_needs_a_company(tmp_path, monkeypatch):
    app, _ = make(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["discover", "https://boards.greenhouse.io/acme/jobs/1"])
    assert r.exit_code == 1 and "jobagent scrape" in r.output
    get_settings.cache_clear()
