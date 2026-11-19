"""F04: submission attempts. A sent (or possibly sent) application is never filled or clicked again; an attempt is written
before the click; receipt that is not proven stays uncertain; the approved payload is frozen and checked before the click."""
import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from jobagent import db, main
from jobagent.config import get_settings
from jobagent.steps import submit as S
from jobagent.tools import availability, forms
from test_apply_exit_codes import BASE, fake_world
from test_approval_uploads import CODEX, WIDGET, Env, JD, env_for, real_records, upload_record  # noqa: F401  (env_for is a fixture)

PLAIN = ("<h1>Job application</h1><form><label>Email <input type=\"email\" name=\"email\" value=\"me@example.org\" required></label>"
         "<button type=\"button\" onclick=\"window.sent = (window.sent || 0) + 1\">Submit application</button></form>")


def app_row(e):
    return dict(e.conn.execute("SELECT * FROM applications WHERE job_dir = ?", (db.job_key(e.job),)).fetchone())


def attempts(e):
    return [dict(r) for r in e.conn.execute("SELECT * FROM submission_attempts WHERE job_dir = ? ORDER BY attempt_no", (db.job_key(e.job),))]


def seed_job(e, status="queued"):
    e.conn.execute("INSERT OR REPLACE INTO jobs (url, status) VALUES (?, ?)", (JD["source_url"], status))
    e.conn.commit()
    (e.job / "jd.json").write_text(json.dumps(JD))


def clicks(e):
    return e.page.evaluate("window.sent || 0")


def run_review(e, thank_you=True, errors=()):
    S.submitted_page = lambda page, before, shot, timeout_s=60, **kwargs: (thank_you, "thanks" if thank_you else "", list(errors))
    return e.review([], ready=True, blockers=())


@pytest.fixture(autouse=True)
def restore_submitted_page():
    real = S.submitted_page
    yield
    S.submitted_page = real


# --- a sent application is never filled or clicked again -------------------------------------------------------------------

@pytest.mark.parametrize("state", ["submitted", "confirmed", "submission_in_progress", "submission_unknown"])
def test_rerunning_a_sent_or_possibly_sent_application_does_not_click_or_rewrite_it(env_for, state):
    e = env_for(PLAIN)
    S.record(e.conn, e.job, JD, state=state, submitted_at="2026-11-01T10:00:00Z", notes="original")
    before = app_row(e)
    out, _ = e.review([], ready=True, blockers=())
    assert out["state"] in ("already_submitted", "submission_uncertain") and clicks(e) == 0
    assert e.prompts == []                                    # not even asked
    assert app_row(e) == before                               # state, timestamps and notes exactly as they were
    assert attempts(e) == []


@pytest.mark.parametrize("state", ["submitted", "confirmed", "submission_in_progress", "submission_unknown"])
def test_record_cannot_move_a_sent_application_backwards(tmp_path, state):
    conn = db.connect(tmp_path / "j.sqlite")
    S.record(conn, tmp_path / "a", JD, state=state, submitted_at="t")
    for back in ("filled", "approved", "skipped", "failed"):
        S.record(conn, tmp_path / "a", JD, state=back, decided_at="x", notes="late write")
    S.record(conn, tmp_path / "a", JD, filled_at="x")
    row = conn.execute("SELECT state, notes, filled_at FROM applications").fetchone()
    assert row["state"] == state and row["notes"] != "late write" and row["filled_at"] is None


def test_availability_rejects_a_posting_we_already_applied_to(env_for):
    e = env_for(PLAIN)
    url = JD["source_url"]
    assert availability.known(url, e.conn) is None
    e.conn.execute("INSERT INTO jobs (url, status) VALUES (?, 'applied')", (url,))
    e.conn.commit()
    assert availability.known(url, e.conn).kind == "already_applied"
    e.conn.execute("UPDATE jobs SET status = 'queued'")
    for state in ("submitted", "confirmed", "submission_unknown", "submission_in_progress"):
        S.record(e.conn, e.job, JD, state=state, submitted_at="t")
        assert availability.known(url, e.conn).kind == "already_applied", state
        e.conn.execute("DELETE FROM applications")


# --- the attempt: written before the click, settled by evidence ---------------------------------------------------------------

def test_the_attempt_is_committed_before_the_click(env_for):
    e = env_for(PLAIN)
    seen = {}
    real = S.click_submit

    def spy(page, view, approval):
        other = sqlite3.connect(str(e.conn.execute("PRAGMA database_list").fetchone()[2]))     # a second connection: committed data only
        seen["rows"] = other.execute("SELECT state FROM submission_attempts").fetchall()
        seen["app"] = other.execute("SELECT state FROM applications").fetchall()
        other.close()
        return real(page, view, approval)
    S.click_submit = spy
    try:
        run_review(e)
    finally:
        S.click_submit = real
    assert seen["rows"] == [("in_progress",)] and seen["app"] == [("submission_in_progress",)]


def test_email_action_window_starts_before_a_slow_submit_click_returns(env_for, monkeypatch):
    from datetime import timedelta
    e = env_for(PLAIN)
    current = [datetime(2026, 11, 18, 12, tzinfo=UTC)]

    class MailClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz) if tz else current[0].replace(tzinfo=None)

    monkeypatch.setattr(S, 'datetime', MailClock)
    real_click = S.click_submit

    def delayed_click(*args, **kwargs):
        result = real_click(*args, **kwargs)
        current[0] += timedelta(seconds=20)
        return result

    def receipt(page, before, shot, timeout_s=60, *, email_verifier):
        assert email_verifier.since == datetime(2026, 11, 18, 12, tzinfo=UTC)
        assert email_verifier.since < current[0]
        return True, 'thanks', []

    monkeypatch.setattr(S, 'click_submit', delayed_click)
    monkeypatch.setattr(S, 'submitted_page', receipt)
    assert e.review([], ready=True, blockers=())[0]['state'] == 'submitted'


def test_a_thank_you_page_is_submitted_with_the_page_as_evidence_and_a_frozen_payload(env_for):
    e = env_for(PLAIN)
    seed_job(e)
    out, _ = run_review(e)
    assert out["state"] == "submitted" and clicks(e) == 1
    a, = attempts(e)
    assert a["state"] == "submitted" and "thanks" in a["evidence"] and a["clicked_at"] and a["payload_sha"]
    frozen = json.loads(open(a["payload_path"]).read())
    assert frozen["payload_sha"] == a["payload_sha"] and any(f["value"] == "me@example.org" for f in frozen["screen"])
    row = app_row(e)
    assert row["state"] == "submitted" and row["submitted_at"]
    assert e.conn.execute("SELECT status FROM jobs").fetchone()["status"] == "applied"


def test_no_thank_you_page_is_not_recorded_as_submitted(env_for):
    e = env_for(PLAIN)
    seed_job(e)
    out, _ = run_review(e, thank_you=False)
    assert out["state"] == "submission_unknown" and clicks(e) == 1
    row = app_row(e)
    assert row["state"] == "submission_unknown" and row["submitted_at"] is None and row["confirmed_at"] is None
    assert attempts(e)[0]["state"] == "unknown"
    assert e.conn.execute("SELECT status FROM jobs").fetchone()["status"] == "needs_attention"      # never 'applied'
    # and a second run cannot send it again
    out2, _ = e.review([], ready=True, blockers=())
    assert out2["state"] == "submission_uncertain" and clicks(e) == 1


def test_a_confirmation_mail_after_an_unproven_click_settles_it_as_confirmed(env_for):
    e = env_for(PLAIN)
    seed_job(e)
    mail = SimpleNamespace(message_id="<m1>", sender="no-reply@x", subject="Thanks for applying")
    S.wait_for_confirmation = lambda *a, **k: mail
    out, _ = run_review(e, thank_you=False)
    assert out["state"] == "confirmed"
    row = app_row(e)
    assert row["state"] == "confirmed" and row["submitted_at"] and row["confirmation_id"] == "<m1>"
    assert attempts(e)[0]["state"] == "confirmed"


def test_the_site_refusing_the_form_is_a_clear_failure_and_a_new_attempt_is_allowed(env_for):
    e = env_for(PLAIN)
    out, _ = run_review(e, thank_you=False, errors=["Please enter a valid phone number"])
    assert out["state"] == "failed" and attempts(e)[0]["state"] == "refused"
    assert S.guard(e.conn, e.job) is None
    out2, _ = run_review(e)
    assert out2["state"] == "submitted" and [a["attempt_no"] for a in attempts(e)] == [1, 2]
    assert app_row(e)["state"] == "submitted"


def test_a_click_that_raises_leaves_the_attempt_unknown_and_blocks_a_retry(env_for):
    e = env_for(PLAIN)
    seed_job(e)
    real = S.click_submit
    S.click_submit = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("page crashed during the click"))
    try:
        out, _ = run_review(e)
    finally:
        S.click_submit = real
    assert out["state"] == "submission_unknown"
    assert app_row(e)["state"] == "submission_unknown" and attempts(e)[0]["state"] == "unknown"
    out, _ = e.review([], ready=True, blockers=())
    assert out["state"] == "submission_uncertain" and clicks(e) == 0


def test_a_crash_right_after_the_attempt_was_written_resumes_through_reconciliation(env_for, tmp_path):
    e = env_for(PLAIN)
    seed_job(e)
    S.record(e.conn, e.job, JD, state="approved")
    view = forms.read_page(e.page)
    sha, payload = S.freeze_payload(e.page, view)
    S.begin_attempt(e.conn, e.job, sha, payload, "2026-11-11T10:00:00Z")           # ... and the process died here
    assert app_row(e)["state"] == "submission_in_progress"
    assert S.guard(e.conn, e.job)["state"] == "submission_uncertain"
    out, _ = e.review([], ready=True, blockers=())
    assert out["state"] == "submission_uncertain" and clicks(e) == 0
    # no mail yet: it stays uncertain, saying how to settle it
    S.wait_for_confirmation = lambda *a, **k: None
    msg = S.confirm(e.job, None, e.conn, timeout_s=0)
    assert "still unproven" in msg and app_row(e)["state"] == "submission_in_progress"
    # the mail arrives: confirmed
    S.wait_for_confirmation = lambda *a, **k: SimpleNamespace(message_id="<m>", sender="a@x", subject="Application received")
    assert S.confirm(e.job, None, e.conn, timeout_s=0).startswith("confirmed")
    assert app_row(e)["state"] == "confirmed" and attempts(e)[0]["state"] == "confirmed"


def test_the_candidate_can_settle_an_unknown_attempt_by_hand(env_for):
    e = env_for(PLAIN)
    seed_job(e)
    run_review(e, thank_you=False)
    S.wait_for_confirmation = lambda *a, **k: None
    assert "not sent" in S.confirm(e.job, None, e.conn, attest="not-sent")
    assert app_row(e)["state"] == "failed" and S.guard(e.conn, e.job) is None and attempts(e)[0]["state"] == "not_sent"
    out, _ = run_review(e)                                                   # the second attempt is a new row
    assert out["state"] == "submitted" and [a["attempt_no"] for a in attempts(e)] == [1, 2]
    row = app_row(e)
    assert row["state"] == "submitted" and row["notes"].startswith("page said")


def test_attesting_that_it_was_sent_marks_it_submitted_and_never_clicks(env_for):
    e = env_for(PLAIN)
    seed_job(e)
    run_review(e, thank_you=False)
    assert "settled as submitted" in S.confirm(e.job, None, e.conn, attest="sent")
    assert app_row(e)["state"] == "submitted" and app_row(e)["submitted_at"] and clicks(e) == 1
    assert e.conn.execute("SELECT status FROM jobs").fetchone()["status"] == "applied"


def test_a_second_attempt_does_not_inherit_the_first_attempts_snapshot(env_for):
    e = env_for(PLAIN)
    run_review(e, thank_you=False, errors=["fix this"])
    e.conn.execute("UPDATE applications SET resume_sha = 'old', ats_average = 99, keywords = '[\"x\"]'")
    e.conn.commit()
    run_review(e)
    assert app_row(e)["resume_sha"] != "old" and app_row(e)["ats_average"] != 99


# --- the approval covers exactly the page that was approved -------------------------------------------------------------------

def test_a_page_that_changes_after_approval_is_not_sent_and_writes_no_attempt(env_for):
    e = env_for(PLAIN)
    real = S.freeze_payload
    calls = {"n": 0}

    def changing(page, view, records=(), job_dir=None):
        calls["n"] += 1
        if calls["n"] == 2:                                   # between the approval's snapshot and the one before the click
            e.page.evaluate("document.querySelector('input').value = 'someone-else@example.org'")
            view = forms.read_page(e.page)
        return real(page, view, records, job_dir)
    S.freeze_payload = changing
    try:
        out, _ = run_review(e)
    finally:
        S.freeze_payload = real
    assert out["state"] == "not_ready" and clicks(e) == 0 and attempts(e) == []
    assert app_row(e)["state"] == "filled"


# --- the command line ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("state,code", [("submitted", 0), ("confirmed", 0), ("submission_unknown", 6), ("submission_in_progress", 6)])
def test_submit_command_refuses_to_start_on_a_sent_application_and_opens_no_browser(monkeypatch, tmp_path, state, code):
    job = fake_world(monkeypatch, tmp_path, {**BASE, "state": "review"})
    import jobagent.steps.apply as A

    def boom(*a, **k):
        raise AssertionError("the form was filled / a browser was used")
    monkeypatch.setattr(A, "run", boom)
    S.record(db.connect(), job, {"company": "Acme", "title": "Engineer", "source_url": "https://e.test/a"}, state=state,
             submitted_at="2026-11-01T10:00:00Z")
    r = CliRunner().invoke(main.app, ["submit", str(job), "acme"])
    assert r.exit_code == code, r.output
    assert "nothing was clicked" in " ".join(r.output.split()).lower()
    get_settings.cache_clear()


def test_submit_exits_6_when_the_run_ends_with_an_unproven_submission(monkeypatch, tmp_path):
    job = fake_world(monkeypatch, tmp_path, {**BASE, "state": "review", "ready_to_submit": True,
                                            "submission": {"state": "submission_unknown"}})
    r = CliRunner().invoke(main.app, ["submit", str(job), "acme"])
    assert r.exit_code == 6, r.output
    get_settings.cache_clear()


# --- F04 independent verification --------------------------------------------------------------------------------------------

UNLABELLED = ("<h1>Job application</h1><form onsubmit=\"window.sent = (window.sent || 0) + 1; return false;\">"
              "<label>Email <input type=\"email\" value=\"me@example.org\" required></label><input type=\"submit\" value=\"\"></form>")


def deliver_then_time_out(monkeypatch):
    from playwright.sync_api import Locator, TimeoutError as PWTimeout
    real = Locator.click

    def click(self, *a, **k):
        real(self, *a, **k)                                   # the click is delivered ...
        raise PWTimeout("Timeout 10000ms exceeded while waiting for the click to be acknowledged")     # ... the acknowledgement is lost
    monkeypatch.setattr(Locator, "click", click)


def test_a_delivered_click_whose_acknowledgement_times_out_is_not_no_button(env_for, monkeypatch):     # verification 1
    e = env_for(UNLABELLED)
    seed_job(e)
    deliver_then_time_out(monkeypatch)
    out, _ = run_review(e)
    assert clicks(e) == 1 and out["state"] == "submission_unknown"
    assert app_row(e)["state"] == "submission_unknown" and attempts(e)[0]["state"] == "unknown" and attempts(e)[0]["action_at"]
    monkeypatch.undo()
    assert S.guard(e.conn, e.job)["state"] == "submission_uncertain"
    out2, _ = e.review([], ready=True, blockers=())
    assert out2["state"] == "submission_uncertain" and clicks(e) == 1             # nothing clicked again
    assert e.conn.execute("SELECT status FROM jobs").fetchone()["status"] == "needs_attention"


def test_the_helper_raises_after_a_click_and_never_tries_another_control(env_for, monkeypatch):
    e = env_for(UNLABELLED.replace("</form>", "</form><form onsubmit=\"window.sent2 = 1; return false;\"><input type=\"submit\" value=\"\"></form>"))
    deliver_then_time_out(monkeypatch)
    approval = forms.SubmitApproval(str(e.job), "r", _issuer=forms._ISSUER)
    with pytest.raises(forms.ClickOutcomeUnknown):
        forms.click_submit_input(e.page, approval)
    assert clicks(e) == 1 and e.page.evaluate("!!window.sent2") is False


def test_a_genuine_no_button_page_still_settles_as_not_sent(env_for):
    e = env_for("<h1>Job application</h1><form><label>Email <input type=\"email\" value=\"me@example.org\" required></label></form>")
    out, _ = run_review(e)
    assert out["state"] == "failed" and attempts(e)[0]["state"] == "not_sent" and S.guard(e.conn, e.job) is None


@pytest.mark.parametrize("first_thank_you", [True, False])
def test_an_approval_that_was_waiting_cannot_send_after_another_review_finished(env_for, first_thank_you):   # verification 2
    e = env_for(PLAIN)
    seed_job(e)
    S.submitted_page = lambda page, before, shot, timeout_s=60, **kwargs: (first_thank_you, "thanks" if first_thank_you else "", [])
    inner = {}

    def other_review_completes():
        e.before_answer = None
        view = forms.read_page(e.page)
        inner["out"] = S.make_at_review(e.job, JD, e.hitl, None, e.conn, lambda m: None, mail_s=0)(
            e.page, view, {"state": "review", "fields": [], "verified": 1, "pages": [], "url": "u", "ready_to_submit": True, "blockers": []})
    e.before_answer = other_review_completes
    out, _ = e.review([], ready=True, blockers=())
    assert inner["out"]["state"] in ("submitted", "submission_unknown")
    assert out["state"] in ("already_submitted", "submission_uncertain") and clicks(e) == 1
    assert len(attempts(e)) == 1 and app_row(e)["state"] == ("submitted" if first_thank_you else "submission_unknown")


def test_two_connections_cannot_both_reserve_an_attempt(tmp_path):
    path = tmp_path / "j.sqlite"
    a, b = db.connect(path), db.connect(path)
    S.record(a, tmp_path / "app", JD, state="approved")
    n = S.begin_attempt(a, tmp_path / "app", "sha1", {"fields": [], "attachments": []}, "t")
    assert n == 1
    with pytest.raises(S.AttemptRefused):
        S.begin_attempt(b, tmp_path / "app", "sha2", {"fields": [], "attachments": []}, "t")
    assert b.execute("SELECT COUNT(*) FROM submission_attempts").fetchone()[0] == 1
    assert json.loads(open(tmp_path / "app" / "application" / "submission-1.json").read())["payload_sha"] == "sha1"
    # the database itself refuses a second open attempt, whatever the code does
    with pytest.raises(sqlite3.IntegrityError):
        b.execute("INSERT INTO submission_attempts (job_dir, attempt_no, state, started_at) VALUES (?, 9, 'in_progress', 't')", (str(tmp_path / "app"),))
    b.rollback()
    S.settle_attempt(a, tmp_path / "app", 1, "submitted")
    S.record(a, tmp_path / "app", JD, state="submitted", submitted_at="t")
    with pytest.raises(S.AttemptRefused):                       # a sent application takes no new attempt either
        S.begin_attempt(b, tmp_path / "app", "sha3", {"fields": [], "attachments": []}, "t")
    assert a.execute("SELECT state FROM applications").fetchone()["state"] == "submitted"


FILE_FORM = ("<h1>Job application</h1><form onsubmit=\"window.sent = (window.sent || 0) + 1; return false;\">"
             "<label>Email <input type=\"email\" value=\"me@example.org\" required></label>"
             "<label>Resume <input type=\"file\" name=\"resume\" required></label>"
             "<button type=\"submit\">Submit application</button></form>")


def sha(b):
    import hashlib
    return hashlib.sha256(b).hexdigest()


def attach_bytes(e, tmp_path, data, name="resume.pdf", sub="manual"):
    d = tmp_path / sub
    d.mkdir(exist_ok=True)
    (d / name).write_bytes(data)
    e.page.locator("input[type=file]").first.set_input_files(str(d / name))


def test_the_frozen_hash_describes_the_attached_bytes_not_a_same_named_job_folder_file(env_for, tmp_path):   # verification 3
    e = env_for(FILE_FORM)
    (e.job / "resume.pdf").write_bytes(b"%PDF job folder B")
    attach_bytes(e, tmp_path, b"%PDF manual A")
    _, payload = S.freeze_payload(e.page, forms.read_page(e.page))
    held, = payload["attachments"]
    assert held["files"] == [{"name": "resume.pdf", "size": len(b"%PDF manual A"), "sha256": sha(b"%PDF manual A")}]
    assert sha(b"%PDF job folder B") not in json.dumps(payload)


def test_swapping_the_attachment_or_changing_its_bytes_changes_the_frozen_hash(env_for, tmp_path):
    e = env_for(FILE_FORM)
    attach_bytes(e, tmp_path, b"%PDF one")
    h1 = S.freeze_payload(e.page, forms.read_page(e.page))[0]
    assert S.freeze_payload(e.page, forms.read_page(e.page))[0] == h1
    attach_bytes(e, tmp_path, b"%PDF two", sub="again")                       # same file name, different bytes
    h2 = S.freeze_payload(e.page, forms.read_page(e.page))[0]
    attach_bytes(e, tmp_path, b"%PDF two", name="other.pdf", sub="third")      # same bytes, different name
    h3 = S.freeze_payload(e.page, forms.read_page(e.page))[0]
    assert len({h1, h2, h3}) == 3


def test_a_manual_replacement_between_the_snapshots_stops_the_send_and_the_saved_payload_names_the_real_file(env_for, tmp_path):
    e = env_for(FILE_FORM)
    attach_bytes(e, tmp_path, b"%PDF first")
    real = S.freeze_payload
    calls = {"n": 0}

    def swapping(page, view, records=(), job_dir=None):
        calls["n"] += 1
        if calls["n"] == 2:
            attach_bytes(e, tmp_path, b"%PDF replaced", sub="swap")
            view = forms.read_page(e.page)
        return real(page, view, records, job_dir)
    S.freeze_payload = swapping
    try:
        out, _ = run_review(e)
    finally:
        S.freeze_payload = real
    assert out["state"] == "not_ready" and clicks(e) == 0 and attempts(e) == []
    out2, _ = run_review(e)                                                    # approved again: the new file is what is frozen
    saved = json.loads(open(attempts(e)[0]["payload_path"]).read())
    assert saved["attachments"][0]["files"][0]["sha256"] == sha(b"%PDF replaced") and out2["state"] == "submitted"


def test_a_widget_that_shows_only_a_file_name_is_recorded_as_unverified_not_hashed(env_for):
    e = env_for("<h1>Job application</h1><form><label>Email <input type=\"email\" value=\"me@example.org\" required></label>"
                "<label>Resume <input type=\"text\" value=\"my_resume.pdf\" readonly></label>"
                "<button type=\"button\" onclick=\"window.sent = 1\">Submit application</button></form>")
    (e.job / "my_resume.pdf").write_bytes(b"%PDF somebody else")
    _, payload = S.freeze_payload(e.page, forms.read_page(e.page))
    item, = payload["attachments"]
    assert item["files"] is None and "my_resume.pdf" in item["unverified"]
    assert sha(b"%PDF somebody else") not in json.dumps(payload)


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)

    def now(self):
        return self.t.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_the_click_time_is_captured_at_the_click_not_at_settlement(env_for, monkeypatch):               # verification 4
    from datetime import timedelta
    e = env_for(PLAIN)
    seed_job(e)
    clock = Clock()
    monkeypatch.setattr(S, "_now", clock.now)
    real_click = S.click_submit

    def click(page, view, approval):
        r = real_click(page, view, approval)
        clock.t += timedelta(seconds=0)                                   # the click happens at 12:00:00
        return r
    S.click_submit = click

    def slow_receipt(page, before, shot, timeout_s=60, **kwargs):
        clock.t += timedelta(seconds=120)                                 # the receipt check takes two minutes
        return True, "thanks", []
    S.submitted_page = slow_receipt
    try:
        e.review([], ready=True, blockers=())
    finally:
        S.click_submit = real_click
    a = attempts(e)[0]
    assert a["clicked_at"] == "2026-11-11T12:00:00Z" and a["finished_at"] == "2026-11-11T12:02:00Z"
    assert a["action_at"] == "2026-11-11T12:00:00Z" and app_row(e)["submitted_at"] == "2026-11-11T12:00:00Z"


def test_reconciliation_keeps_the_original_action_time(env_for, monkeypatch):
    from datetime import timedelta
    e = env_for(PLAIN)
    seed_job(e)
    clock = Clock()
    monkeypatch.setattr(S, "_now", clock.now)
    run_review(e, thank_you=False)                                            # clicked at 12:00:00, unproven
    clock.t += timedelta(hours=3)
    S.wait_for_confirmation = lambda *a, **k: SimpleNamespace(message_id="<m>", sender="a@x", subject="Application received")
    assert S.confirm(e.job, None, e.conn, timeout_s=0).startswith("confirmed")
    row, a = app_row(e), attempts(e)[0]
    assert row["submitted_at"] == "2026-11-11T12:00:00Z" and a["clicked_at"] == "2026-11-11T12:00:00Z"
    assert row["confirmed_at"] == "2026-11-11T15:00:00Z" and a["finished_at"] == "2026-11-11T15:00:00Z"


def test_a_crash_with_no_known_click_time_falls_back_to_the_attempted_action_time_not_to_settlement(env_for, monkeypatch):
    from datetime import timedelta
    e = env_for(PLAIN)
    seed_job(e)
    clock = Clock()
    monkeypatch.setattr(S, "_now", clock.now)
    S.record(e.conn, e.job, JD, state="approved")
    sha_, payload = S.freeze_payload(e.page, forms.read_page(e.page))
    n = S.begin_attempt(e.conn, e.job, sha_, payload, clock.now())
    clock.t += timedelta(seconds=5)
    S.mark_action(e.conn, e.job, n)                                           # the process died after this, before the click returned
    clock.t += timedelta(hours=2)
    assert "settled as submitted" in S.confirm(e.job, None, e.conn, attest="sent")
    assert app_row(e)["submitted_at"] == "2026-11-11T12:00:05Z"


# --- F04 second independent verification ----------------------------------------------------------------------------------------

def widget_records(e):
    return [upload_record("failed", label="Resume / CV * / f", required=True, error="x") | {"frame": 0, "slot": 0}]


def attach_to_widget(e, tmp_path, name="reviewed_resume.pdf"):
    d = tmp_path / "w"
    d.mkdir(exist_ok=True)
    (d / name).write_bytes(b"%PDF widget")
    e.page.locator("input[type=file]").first.set_input_files(str(d / name))          # the widget replaces its input with the name


def rename_in_widget(e, new):
    e.page.evaluate("(n) => { for (const s of document.querySelectorAll('#up span')) if (/\\.pdf$/.test(s.textContent)) s.textContent = n; }", new)


def test_a_replacement_widget_stays_in_the_inventory_as_unverified_and_its_name_is_fingerprinted(env_for, tmp_path):     # verification 1
    e = env_for(WIDGET)
    recs = widget_records(e)
    attach_to_widget(e, tmp_path)
    assert e.page.locator("input[type=file]").count() == 0                                # no native input is left
    h1, payload = S.freeze_payload(e.page, forms.read_page(e.page), recs)
    item, = payload["attachments"]
    assert item["files"] is None and "reviewed_resume.pdf" in item["unverified"] and item["label"].startswith("Resume")
    rename_in_widget(e, "different_resume.pdf")
    h2, payload2 = S.freeze_payload(e.page, forms.read_page(e.page), recs)
    assert h1 != h2 and "different_resume.pdf" in payload2["attachments"][0]["unverified"]


def test_a_filename_change_between_the_snapshots_is_not_ready_with_no_click_and_no_attempt(env_for, tmp_path):
    e = env_for(WIDGET)
    attach_to_widget(e, tmp_path)
    real = S.freeze_payload
    calls = {"n": 0}

    def changing(page, view, records=(), job_dir=None):
        calls["n"] += 1
        if calls["n"] == 2:
            rename_in_widget(e, "different_resume.pdf")
            view = forms.read_page(page)
        return real(page, view, records, job_dir)
    S.freeze_payload = changing
    try:
        out, _ = e.review(widget_records(e), ready=True, blockers=())
    finally:
        S.freeze_payload = real
    assert out["state"] == "not_ready" and e.sent() is False and attempts(e) == []


def test_a_chooser_with_no_native_input_stays_in_the_inventory_and_a_chosen_name_changes_the_fingerprint(env_for):
    html = ("<h1>Job application</h1><form><label>Email <input type=\"email\" value=\"me@example.org\" required></label>"
            "<div id=up><span>Resume / CV *</span> <button type=\"button\">Choose file</button></div>"
            "<button type=\"button\" onclick=\"window.sent = true\">Submit application</button></form>")
    e = env_for(html)
    recs = real_records(e)                                  # the chooser is read as an upload control with no native input
    h1, payload = S.freeze_payload(e.page, forms.read_page(e.page), recs)
    item, = payload["attachments"]
    assert item["files"] is None and item["unverified"] == "bytes unreadable" and "Choose file" in item["label"]
    e.page.evaluate("document.querySelector('#up').insertAdjacentHTML('beforeend', '<span>chosen.pdf</span>')")
    h2, payload2 = S.freeze_payload(e.page, forms.read_page(e.page), recs)
    assert h1 != h2 and "chosen.pdf" in json.dumps(payload2["attachments"])
    # and when no control for it can be found any more, the record itself is listed, with nothing observed
    e.page.evaluate("document.querySelector('#up button').remove()")
    _, payload3 = S.freeze_payload(e.page, forms.read_page(e.page), [{**recs[0], "label": "Resume / CV * / f"}])
    assert any(a["unverified"].startswith("only the name is shown: chosen.pdf") or a["unverified"] == "no attachment shown"
               for a in payload3["attachments"])


def test_the_widget_filename_changing_after_the_reservation_is_caught_at_dispatch(env_for, tmp_path):
    e = env_for(WIDGET)
    attach_to_widget(e, tmp_path)
    real = S.begin_attempt

    def begin(*a, **k):
        n = real(*a, **k)
        rename_in_widget(e, "different_resume.pdf")
        return n
    S.begin_attempt = begin
    try:
        out, _ = e.review(widget_records(e), ready=True, blockers=())
    finally:
        S.begin_attempt = real
    assert out["state"] == "not_ready" and e.sent() is False
    assert attempts(e)[0]["state"] == "not_sent" and attempts(e)[0]["action_at"] is None


# --- verification 2: the approval is enforced at the final dispatch -----------------------------------------------------------------

def test_a_field_changed_after_the_reservation_is_not_sent_and_the_attempt_is_not_sent(env_for):
    e = env_for(PLAIN)
    seed_job(e)
    real = S.begin_attempt

    def begin(*a, **k):
        n = real(*a, **k)
        e.page.evaluate("document.querySelector('input').value = 'changed-after-freeze@example.org'")
        return n
    S.begin_attempt = begin
    try:
        out, _ = run_review(e)
    finally:
        S.begin_attempt = real
    assert out["state"] == "not_ready" and clicks(e) == 0
    a, = attempts(e)
    assert a["state"] == "not_sent" and "invalidated" in a["notes"]
    assert app_row(e)["state"] == "failed" and app_row(e)["submitted_at"] is None
    assert e.conn.execute("SELECT status FROM jobs").fetchone()["status"] != "applied"
    # a fresh approval is needed and works
    out2, _ = run_review(e)
    assert out2["state"] == "submitted" and clicks(e) == 1 and [x["attempt_no"] for x in attempts(e)] == [1, 2]
    assert any(f["value"] == "changed-after-freeze@example.org" for f in json.loads(open(attempts(e)[1]["payload_path"]).read())["screen"])


def test_a_native_attachment_changed_after_the_reservation_is_not_sent(env_for, tmp_path):
    e = env_for(FILE_FORM)
    attach_bytes(e, tmp_path, b"%PDF reviewed")
    real = S.begin_attempt

    def begin(*a, **k):
        n = real(*a, **k)
        attach_bytes(e, tmp_path, b"%PDF swapped in", sub="swap")
        return n
    S.begin_attempt = begin
    try:
        out, _ = run_review(e)
    finally:
        S.begin_attempt = real
    assert out["state"] == "not_ready" and clicks(e) == 0 and attempts(e)[0]["state"] == "not_sent"


@pytest.mark.parametrize("which", ["button", "input"])
def test_a_change_during_the_challenge_check_inside_the_sending_path_is_caught(env_for, monkeypatch, which):
    from jobagent.tools import challenge
    e = env_for(PLAIN if which == "button" else UNLABELLED)
    seed_job(e)
    seen = {"n": 0}

    def guard(page, *a, **k):
        seen["n"] += 1
        if seen["n"] == 1:                                          # the first guard inside the sending path (a human wait)
            page.evaluate("document.querySelector('input').value = 'edited-during-the-wait@example.org'")
        return False
    monkeypatch.setattr(challenge, "guard", guard)
    out, _ = run_review(e)
    assert out["state"] == "not_ready" and clicks(e) == 0
    assert attempts(e)[0]["state"] == "not_sent" and app_row(e)["state"] == "failed"


# --- F04 third independent verification: the whole reviewed application is frozen, not just the visible controls ------------------

WIZARD = """<h1 id=h>Step 1</h1>
<form onsubmit="return false">
<div id=p1><label>Full name <input name=full_name type=text required></label>
  <label>Phone <input name=phone1 type=tel required></label>
  <button type=button onclick="go(2)">Next</button></div>
<div id=p2 style="display:none"><label>Phone <input name=phone2 type=tel required></label>
  <label>Email <input name=email type=email required></label>
  <button type=button onclick="go(3)">Next</button></div>
<div id=p3 style="display:none"><h2>Your answers are on file</h2><p>Nothing on this screen can be edited.</p>
  <button type=button onclick="window.sent = (window.sent || 0) + 1; window.payload = JSON.stringify(
      [...document.querySelectorAll('input')].map(i => [i.name, i.value]))">Submit application</button></div>
</form>
<script>function go(n){ for (const i of [1,2,3]) document.getElementById('p'+i).style.display = (i===n ? '' : 'none');
  document.getElementById('h').textContent = 'Step ' + n; }</script>"""


def wizard_planner(monkeypatch):
    from test_planner_failure import quiet_pipeline
    from jobagent.steps import apply as A
    phones = iter(["111-1111", "222-2222"])

    def plan(todo, ctx, model, log, feedback="", adds=(), filled=()):
        out = {}
        for f in todo:
            lab = f.label.lower()
            value = ("Reviewed Candidate" if "full name" in lab else "me@example.org" if "email" in lab
                     else next(phones) if "phone" in lab else "")
            if value:
                out[f.id] = A.Answer(id=f.id, why="stub", value=value, source="applicant")
        return out, []
    quiet_pipeline(monkeypatch, plan)


def run_wizard(tmp_path, monkeypatch, mutate=None):
    from test_planner_failure import browser_page, run_review as run_apply
    wizard_planner(monkeypatch)
    conn = db.connect(tmp_path / "jobs.sqlite")
    prompts = []

    class Hitl:
        def ask(self, prompt, timeout_s=0):
            prompts.append(prompt)
            return "approve"
    monkeypatch.setattr(S, "submitted_page", lambda page, before, shot, timeout_s=60, **kwargs: (True, "thanks", []))
    monkeypatch.setattr(S, "wait_for_confirmation", lambda *a, **k: None)
    pw, browser, page = browser_page(WIZARD)
    try:
        job = tmp_path / "outputs" / "acme" / "1"
        hook = S.make_at_review(job, JD, Hitl(), None, conn, lambda m: None, mail_s=0)
        res, job = run_apply(page, tmp_path, monkeypatch, at_review=hook)
        page_state = page.evaluate("({sent: window.sent || 0, payload: window.payload || ''})")
        return res, job, conn, page_state
    finally:
        browser.close()
        pw.stop()


def test_every_answer_from_every_page_is_in_the_saved_attempt_and_the_fingerprint(tmp_path, monkeypatch):
    res, job, conn, state = run_wizard(tmp_path, monkeypatch)
    assert res["state"] == "review" and res["submission"]["state"] == "submitted" and state["sent"] == 1
    sent = dict(json.loads(state["payload"]))                          # what the final handler really consumed
    assert sent["full_name"] == "Reviewed Candidate" and sent["email"] == "me@example.org"
    att = dict(conn.execute("SELECT payload_path, payload_sha FROM submission_attempts").fetchone())
    frozen = json.loads(open(att["payload_path"]).read())
    assert frozen["screen"] == []                                      # the final screen is read-only: no editable control
    by_page = [(e["page"], e["label"], e["readback"]) for e in frozen["reviewed"]]
    assert (1, "Full name", "Reviewed Candidate") in by_page and any(l == "Email" and r == "me@example.org" for _, l, r in by_page)
    phones = [(p, r) for p, l, r in by_page if l == "Phone"]
    assert phones == [(1, "111-1111"), (2, "222-2222")]                # repeated labels on different pages stay separate
    assert all("page" in e["provenance"] and "not on the final screen" in e["provenance"] for e in frozen["reviewed"])
    assert "Reviewed Candidate" in json.dumps(frozen)


def test_a_changed_historical_answer_changes_the_fingerprint_and_unchanged_ones_do_not(tmp_path, monkeypatch):
    res, job, conn, _ = run_wizard(tmp_path, monkeypatch)
    from test_planner_failure import browser_page
    pw, browser, page = browser_page("<h1>Final</h1><p>read only</p>")
    try:
        view = forms.read_page(page)
        records = res["fields"]
        h0, _ = S.freeze_payload(page, view, records)
        assert S.freeze_payload(page, view, [dict(r) for r in records])[0] == h0
        changed = [dict(r) for r in records]
        next(r for r in changed if r["label"] == "Full name")["readback"] = "Somebody Else"
        assert S.freeze_payload(page, view, changed)[0] != h0
        dropped = [r for r in records if r["label"] != "Full name"]
        assert S.freeze_payload(page, view, dropped)[0] != h0                       # an earlier answer cannot just disappear
        swapped_pages = [dict(r) for r in records]
        phones = [r for r in swapped_pages if r["label"] == "Phone"]
        phones[0]["readback"], phones[1]["readback"] = phones[1]["readback"], phones[0]["readback"]
        assert S.freeze_payload(page, view, swapped_pages)[0] != h0                 # repeated labels are not merged
    finally:
        browser.close()
        pw.stop()


def test_a_password_answer_is_never_written_into_the_attempt(tmp_path):
    from test_planner_failure import browser_page
    pw, browser, page = browser_page("<h1>Final</h1>")
    try:
        _, payload = S.freeze_payload(page, forms.read_page(page), [{"page": 1, "kind": "password", "label": "Password", "answer": "hunter2",
                                                                  "filled": "hunter2", "readback": "hunter2"}])
    finally:
        browser.close()
        pw.stop()
    assert "hunter2" not in json.dumps(payload) and payload["reviewed"][0]["answer"] == "[redacted]"
