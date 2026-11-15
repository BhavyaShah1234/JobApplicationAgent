"""F24: dry runs change nothing, and the candidate can repair an accepted false positive with event ids alone."""
import json
from datetime import timedelta

from typer.testing import CliRunner

from jobagent import db
from jobagent.tools import outcomes as O
from test_outcome_integrity import SENDER, mail, run_sync
from test_outcomes import T0, FakeInbox, make_db, verified_company_domains  # noqa: F401  (autouse fixture is reused)

CONFIRM = mail(7001, "Thank you for applying to Stripe", "Thank you for applying. We have received your application.", thread="700")
INTERVIEW = mail(7002, "Re: Thank you for applying to Stripe", "We would like to schedule an interview with the team. Please pick a time.",
                 T0 + timedelta(days=4), sender="Stripe Recruiting <recruiter@stripe.com>", thread="700")


def dump(conn) -> dict:
    """Every row of every table, to compare a database with itself."""
    out = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        out[name] = [tuple(r) for r in conn.execute(f"SELECT * FROM {name} ORDER BY 1")]
    return out


# --- dry runs ----------------------------------------------------------------------------------------------------------

def test_a_dry_run_sync_leaves_every_table_exactly_as_it_found_it(tmp_path):
    conn = make_db(tmp_path)
    before = dump(conn)
    assert "agent_domains" in before
    res = O.sync(FakeInbox([CONFIRM, INTERVIEW]), conn, as_of=T0 + timedelta(days=5), model=None, dry_run=True, log=lambda m: None)
    assert res.stored                                                     # it did look at the mail and would have stored it
    assert dump(conn) == before                                           # ... and recorded nothing: no events, ledger, cursor, domain


def test_building_a_matcher_without_registering_does_not_touch_agent_domains(tmp_path):
    from jobagent.steps.submit import application_match
    conn = make_db(tmp_path)
    jd = {"apply_url": "https://job-boards.greenhouse.io/stripe/jobs/1", "company": "Stripe", "title": "Backend Engineer"}
    before = dump(conn)["agent_domains"]
    quiet = application_match(conn, jd, register=False)
    assert dump(conn)["agent_domains"] == before
    loud = application_match(conn, jd)
    assert dump(conn)["agent_domains"] != before                          # the submit step still records the portal
    assert quiet.allowed == loud.allowed                                  # the same matcher either way


# --- repairing an accepted false positive -------------------------------------------------------------------------------

def counted(conn):
    return {e["mailbox_uid"]: e for e in map(dict, conn.execute("SELECT * FROM outcome_events ORDER BY id"))}


def test_an_accepted_event_can_be_discarded_without_sql_and_everything_derived_follows(tmp_path):
    conn = make_db(tmp_path)
    box = FakeInbox([CONFIRM, INTERVIEW])
    run_sync(box, conn, days=5)
    es = counted(conn)
    assert es["7001"]["needs_review"] == 0 and conn.execute("SELECT state FROM applications").fetchone()[0] == "confirmed"
    assert conn.execute("SELECT current_stage FROM application_status").fetchone()[0] == "interview"
    assert O.discard(conn, es["7001"]["id"]) and O.discard(conn, es["7002"]["id"])
    assert O.discard(conn, es["7001"]["id"]) is False                     # already gone
    app = dict(conn.execute("SELECT * FROM applications").fetchone())
    assert app["state"] == "submitted" and app["confirmed_at"] is None and app["confirmation_id"] is None
    assert conn.execute("SELECT current_stage FROM application_status").fetchone()[0] is None      # no longer in the analysis
    run_sync(box, conn, days=6)                                            # the same messages are never added again
    assert {e["needs_review"] for e in counted(conn).values()} == {2}
    assert [r["action"] for r in conn.execute("SELECT action FROM outcome_audit ORDER BY id")].count("discard") == 2
    from jobagent.tools import outcome_stats as S
    assert S.load(conn)[0].events == []                                    # the statistics see nothing from them


def test_relabelling_by_event_id_moves_the_confirmation_state_both_ways(tmp_path):
    conn = make_db(tmp_path)
    run_sync(FakeInbox([CONFIRM]), conn)
    cid = counted(conn)["7001"]["id"]
    assert O.relabel(conn, cid, "interview")                               # it was not a confirmation after all
    assert conn.execute("SELECT stage, classifier FROM outcome_events").fetchone()[:] == ("interview", "human")
    assert conn.execute("SELECT state, confirmation_id FROM applications").fetchone()[:] == ("submitted", None)
    assert O.relabel(conn, cid, "confirmation")                            # and back
    assert conn.execute("SELECT state FROM applications").fetchone()[0] == "confirmed"
    assert [r["detail"] for r in conn.execute("SELECT detail FROM outcome_audit WHERE action = 'relabel' ORDER BY id")] == [
        "confirmation -> interview: set by the candidate", "interview -> confirmation: set by the candidate"]
    assert O.label(conn, str(cid), "offer") and O.label(conn, "nobody", "offer") is False
    try:
        O.relabel(conn, cid, "nonsense")
        raise AssertionError("an unknown stage must be refused")
    except ValueError:
        pass


def test_discarding_an_event_sends_the_replies_that_followed_it_back_to_review(tmp_path):
    conn = make_db(tmp_path)
    seed = mail(7101, "Next steps for Backend Engineer", "We would like to schedule an interview with you.", T0 + timedelta(days=3),
                sender="Stripe Recruiting <recruiter@stripe.com>", thread="710")
    reply = mail(7102, "Re: Next steps for Backend Engineer", "Unfortunately, we will not be moving forward.", T0 + timedelta(days=5),
                 sender="Jamie <jamie.lee.recruiter@gmail.com>", thread="710")
    box = FakeInbox([seed, reply])
    run_sync(box, conn, days=6)
    run_sync(box, conn, days=7)                                            # the reply attaches through the settled seed
    es = counted(conn)
    assert es["7102"]["inherited_from"] == es["7101"]["id"] and es["7102"]["needs_review"] == 0, es
    assert O.discard(conn, es["7101"]["id"])
    after = counted(conn)
    assert after["7101"]["needs_review"] == 2 and after["7102"]["needs_review"] == 1 and after["7102"]["inherited_from"] is None


def test_review_and_events_listings_show_the_ids_the_commands_take(tmp_path, monkeypatch):
    from jobagent.cli_outcomes import register
    import typer
    from rich.console import Console

    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    from jobagent.config import get_settings
    get_settings.cache_clear()
    conn = db.connect()
    conn.execute("INSERT INTO applications (job_dir, company, title, url, state, submitted_at) VALUES ('outputs/stripe/1', 'Stripe', 'Backend Engineer', "
                 "'https://job-boards.greenhouse.io/stripe/jobs/1', 'submitted', ?)", (T0.strftime("%Y-%m-%dT%H:%M:%SZ"),))
    conn.execute("INSERT INTO outcome_events (application_id, stage, message_id, sender, subject, excerpt, received_at, classifier, confidence, needs_review) "
                 "VALUES (1, 'interview', 'uv:9', 'r@stripe.com', 'Interview', 'x', ?, 'model', 0.5, 1)", (T0.strftime("%Y-%m-%dT%H:%M:%SZ"),))
    conn.commit()
    console = Console(record=True, width=200)
    app = typer.Typer()
    register(app, console)
    r = CliRunner().invoke(app, ["outcomes", "events"])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(app, ["outcomes", "discard", "1"])
    assert r.exit_code == 0 and "discarded" in r.output.lower() + console.export_text().lower()
    assert conn.execute("SELECT needs_review FROM outcome_events").fetchone()[0] == 2
    get_settings.cache_clear()
