"""F08: a verification mail is the agent's, and may be trashed, only when the site is SEEN to accept it: tested through the real
callers (portal_auth._confirm_email and the generic portal agent loop) on real pages with a real Inbox over a strict fake IMAP."""
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from jobagent import db
from jobagent.tools import portal_agent as PA
from jobagent.tools import portal_auth
from test_inbox_privacy import StrictImap, make, vendor_mail


def now():
    return datetime.now(UTC)


@pytest.fixture
def page():
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        pw.stop()
        pytest.skip("no Chrome here")
    pg = browser.new_page()
    real = pg.wait_for_timeout
    pg.wait_for_timeout = lambda ms: real(min(ms, 120))
    yield pg
    browser.close()
    pw.stop()


FORM = ("<h1>Enter the verification code</h1><p>We have sent a code to your email.</p>"
        "<form id=f><input id=c type=text name=code autocomplete=one-time-code><button id=b type=submit>Verify</button></form><p id=msg></p>"
        "<script>document.getElementById('f').onsubmit = (e) => {{ e.preventDefault(); {handler} }};</script>")
ACCEPT = "if (document.getElementById('c').value === '222222') document.body.innerHTML = '<h1>Your email is verified. Welcome agent@gmail.com</h1>'; else document.getElementById('msg').textContent = 'That code is invalid.';"
CONTRADICTED_ACCOUNT = ("<h1>Email not verified</h1><p>agent@gmail.com <a href=#>My account</a> <a href=#>Sign out</a></p>")
BEHAVIOURS = {
    "stalled": "",                                                    # nothing happens, the form stays
    "pending": "setTimeout(() => { document.body.innerHTML = '<h1>Welcome agent@gmail.com</h1>' }, 20000);",   # slower than we wait
    "challenge": "document.body.innerHTML = '<h1>Just a moment...</h1><p>Verify you are human</p>';",
    "other_wording": "document.getElementById('msg').textContent = 'Hmm, that does not look right. Please check your email again.';",
    "replaced_pending": "document.body.innerHTML = '<h1>Processing your request</h1><p>Please wait.</p>';",       # the form is GONE, nothing else
    "replaced_error": "document.body.innerHTML = '<h1>We could not confirm your identity</h1><p>Please contact support.</p>';",
    "negated": "document.body.innerHTML = '<h1>Email not verified</h1><p>Please contact support.</p>';",
    "welcome_only": "document.body.innerHTML = '<h1>Welcome!</h1>';",
    "unknown_screen": "document.body.innerHTML = '<h1>Thanks</h1><p>We will be in touch.</p>';",
    "contradicted_account": f"document.body.innerHTML = '{CONTRADICTED_ACCOUNT}';",
    "accepted": ACCEPT,
}


def run_confirm(tmp_path, page, behaviour, code="222222"):
    inbox, conn = make(tmp_path, [vendor_mail("1", "agent@gmail.com", now(), code=code)])
    page.set_content(FORM.format(handler=BEHAVIOURS[behaviour]))
    logs = []
    done = portal_auth._confirm_email(page, inbox, "vendor.com", now() - timedelta(minutes=1), logs.append, "otp", timeout_s=2, settle_s=2)
    audit = conn.execute("SELECT status, consumed_message_id, evidence FROM agent_actions").fetchone()
    return done, inbox, dict(audit), logs


@pytest.mark.parametrize("behaviour", ["stalled", "pending", "challenge", "other_wording", "replaced_pending", "replaced_error",
                                       "negated", "welcome_only", "unknown_screen"])
def test_an_unproven_or_rejected_code_leaves_the_mail_unowned_and_in_place(tmp_path, page, behaviour):
    done, inbox, audit, logs = run_confirm(tmp_path, page, behaviour)
    assert done is False and inbox._imap.moved == []
    assert audit["status"] == "open" and audit["consumed_message_id"] is None and audit["evidence"] is None
    assert not inbox.is_agent_mail(inbox.fetch("1")) and any("left alone" in l for l in logs)


def test_an_accepted_code_makes_the_mail_the_agents_and_it_is_trashed(tmp_path, page):
    done, inbox, audit, logs = run_confirm(tmp_path, page, "accepted")
    assert done is True and inbox._imap.moved == ["1"]
    assert audit["status"] == "consumed" and audit["consumed_message_id"] == "<m1@x>" and "accepted" in audit["evidence"]
    assert any("cleanup: completed" in l for l in logs)                 # the cleanup outcome is logged apart from the verification


def test_the_cleanup_outcome_is_reported_separately_from_the_verification(tmp_path, page):
    inbox, conn = make(tmp_path, [vendor_mail("1", "agent@gmail.com", now(), code="222222")])
    inbox._imap.uid_real = inbox._imap.uid
    def failing(cmd, *args):
        if cmd == "MOVE":
            return "NO", [b"refused"]
        return inbox._imap.uid_real(cmd, *args)
    inbox._imap.uid = failing
    inbox._imap.capabilities = ("IMAP4REV1",)                           # no UIDPLUS: no fallback either
    page.set_content(FORM.format(handler=ACCEPT))
    logs = []
    assert portal_auth._confirm_email(page, inbox, "vendor.com", now() - timedelta(minutes=1), logs.append, "otp", timeout_s=2, settle_s=2) is True
    assert any("cleanup: " in l and "completed" not in l.split("cleanup: ")[1] for l in logs)     # verified, cleanup did not complete


# --- the generic portal agent: only the candidate the site accepted becomes the agent's ------------------------------------------------

def code_index(page):
    """Use the current observation; taking another snapshot invalidates its targets."""
    ref=page.locator('input[autocomplete=one-time-code]').get_attribute('data-agent-auth-ref')
    assert ref, 'The native loop must have observed the code control before asking the model'
    return int(ref.rsplit(':',1)[1])


def run_agent(tmp_path, monkeypatch, page, script, html, extra_mail=None):
    """The real loop on a real page; the model is a script of (action, value) answers, evaluated step by step."""
    inbox, conn = make(tmp_path, [vendor_mail("1", "agent@gmail.com", now() + timedelta(seconds=10), code="111111")])
    steps = iter(script)
    monkeypatch.setattr(PA.llm, "writer", lambda *a, **k: object())

    def model(agent, schema, msgs, **k):
        action, value = next(steps)
        if action == "deliver_second":                           # a newer mail arrives, then the loop asks for the code again
            inbox._imap.msgs["2"] = vendor_mail("2", "agent@gmail.com", now() + timedelta(seconds=20), code="222222")
            action = "code_sent"
        element = code_index(page) if action == "fill" else None
        return PA.AuthAction(action=action, frame=0, element=element, value=value, thought="scripted")
    monkeypatch.setattr(PA.llm, "structured_invoke", model)
    page.set_content(html)
    out = PA.run(page, "https://vendor.com/login", {"EMAIL": "agent@gmail.com", "PASSWORD": "pw"}, inbox,
                 SimpleNamespace(ask=lambda *a, **k: "done"), "vendor.com", log=lambda m: None)
    return out, inbox, conn


def consumed(conn):
    return [r[0] for r in conn.execute("SELECT consumed_message_id FROM agent_actions WHERE status = 'consumed'")]


def test_only_the_code_the_site_accepted_is_trashed_not_the_one_retrieved_earlier(tmp_path, monkeypatch, page):
    html = FORM.format(handler=ACCEPT)
    out, inbox, conn = run_agent(tmp_path, monkeypatch, page, [("code_sent", ""), ("deliver_second", ""), ("fill", "{CODE}"),
                                                              ("signed_in", "")], html)
    assert out["state"] == "signed_in"
    assert inbox._imap.moved == ["2"] and "1" in inbox._imap.msgs                 # the personal code (UID 1) was never used
    assert consumed(conn) == ["<m2@x>"]


def test_a_rejected_first_code_then_a_correct_second_one_trashes_only_the_second(tmp_path, monkeypatch, page):
    html = FORM.format(handler=ACCEPT)
    out, inbox, conn = run_agent(tmp_path, monkeypatch, page, [("code_sent", ""), ("fill", "{CODE}"), ("deliver_second", ""),
                                                              ("fill", "{CODE}"), ("signed_in", "")], html)
    assert out["state"] == "signed_in" and inbox._imap.moved == ["2"] and consumed(conn) == ["<m2@x>"]


def test_signing_in_some_other_way_does_not_claim_a_selected_candidate(tmp_path, monkeypatch, page):
    out, inbox, conn = run_agent(tmp_path, monkeypatch, page, [("code_sent", ""), ("signed_in", "")],
                                 "<h1>Welcome agent@gmail.com</h1><p>Your account</p>")      # already signed in without using the mail
    assert out["state"] == "signed_in" and inbox._imap.moved == [] and consumed(conn) == []


# --- the helper's decision rules ------------------------------------------------------------------------------------------------------------------

from jobagent.tools import verification


@pytest.mark.parametrize("html,expected", [
    ("<h1>Processing your request</h1><p>Please wait.</p>", "unproven"),
    ("<h1>Email not verified</h1><p>Please contact support.</p>", "unproven"),
    ("<h1>Verification not complete</h1>", "unproven"),
    ("<h1>Welcome</h1>", "unproven"),
    ("<h1>Your email address has been successfully verified.</h1>", "accepted"),
    ("<h1>Verification complete</h1><p>You are all set.</p>", "accepted"),
    ("<p>Signed in as agent@gmail.com</p><a href=#>Sign out</a>", "accepted"),
    ("<p>We sent a code to agent@gmail.com</p><p>Enter the verification code</p>", "unproven"),     # the address alone is not a signed-in view
    ("<p>Processing... agent@gmail.com</p><a href=#>Sign out</a>", "unproven"),
    ("<h1>Your email is verified</h1><p>Verification required for your phone number.</p>", "unproven"),   # contradicted on the same page
    ("<p>That code is incorrect.</p>", "rejected"),
])
def test_acceptance_needs_an_affirmative_uncontradicted_statement(page, html, expected):
    page.set_content(html)
    assert verification.outcome(page, None, settle_s=0.2, poll_s=0.05, email="agent@gmail.com") == expected


def test_a_failed_field_inspection_is_unknown_not_gone(page):
    page.set_content("<h1>Processing your request</h1><input id=c autocomplete=one-time-code><p>Please wait.</p>")
    def broken():
        raise RuntimeError("frame detached")
    assert verification.outcome(page, broken, settle_s=0.2, poll_s=0.05) == "unproven"
    page.set_content("<h1>Your email is verified.</h1>")                       # even with a success statement ...
    assert verification.outcome(page, broken, settle_s=0.2, poll_s=0.05) == "unproven"      # ... an unknown field state proves nothing
    assert verification.outcome(page, lambda: False, settle_s=0.2, poll_s=0.05) == "accepted"   # a confirmed gone field does
    assert verification.outcome(page, lambda: True, settle_s=0.2, poll_s=0.05) == "unproven"    # a field still showing does not


def test_a_pending_screen_that_later_confirms_within_the_wait_is_accepted(page):
    page.set_content("<h1 id=h>Processing your request</h1><p>Please wait.</p>"
                     "<script>setTimeout(() => { document.body.innerHTML = '<h1>Your email is verified.</h1>' }, 300)</script>")
    assert verification.outcome(page, None, settle_s=3, poll_s=0.1) == "accepted"


def test_a_pending_or_error_transition_followed_by_another_sign_in_does_not_own_the_code(tmp_path, monkeypatch, page):
    # the code is entered, the form is replaced by a pending screen, and the run later signs in another way (the account shows)
    html = FORM.format(handler="document.body.innerHTML = '<h1>Processing your request</h1><p>Please wait.</p>"
                               "<p>agent@gmail.com <a href=#>Sign out</a></p>';")
    out, inbox, conn = run_agent(tmp_path, monkeypatch, page, [("code_sent", ""), ("fill", "{CODE}"), ("signed_in", "")], html)
    assert out["state"] == "signed_in" and inbox._imap.moved == [] and consumed(conn) == []
    assert conn.execute("SELECT status FROM agent_actions").fetchone()[0] == "open"


# --- F08: a contradiction overrides every positive sign, including an account view ----------------------------------------------



@pytest.mark.parametrize("html,expected", [
    (CONTRADICTED_ACCOUNT, "unproven"),
    ("<h1>Verification pending</h1><p>agent@gmail.com <a href=#>My account</a></p>", "unproven"),
    ("<h1>We could not confirm your email</h1><p>Signed in as agent@gmail.com <a href=#>Sign out</a></p>", "unproven"),
    ("<p>Signed in as agent@gmail.com</p><a href=#>Sign out</a>", "accepted"),                         # consistent: nothing contradicts it
    ("<h1>Your email is verified.</h1><p>agent@gmail.com <a href=#>My account</a></p>", "accepted"),
])
def test_an_account_view_does_not_override_a_statement_that_verification_failed(page, html, expected):
    page.set_content(html)
    assert verification.outcome(page, None, settle_s=0.2, poll_s=0.05, email="agent@gmail.com") == expected


def test_the_real_generic_loop_does_not_own_a_code_the_page_says_failed_even_with_an_account_view(tmp_path, monkeypatch, page):
    html = FORM.format(handler=f"document.body.innerHTML = '{CONTRADICTED_ACCOUNT}';")
    out, inbox, conn = run_agent(tmp_path, monkeypatch, page, [("code_sent", ""), ("fill", "{CODE}"), ("signed_in", "")], html)
    assert out["state"] == "signed_in" and inbox._imap.moved == [] and consumed(conn) == []         # signed in, but the mail is not claimed
    row = conn.execute("SELECT status, consumed_message_id, evidence FROM agent_actions").fetchone()
    assert tuple(row) == ("open", None, None)


def test_the_dedicated_caller_does_not_trash_on_a_contradicted_account_view(tmp_path, page):
    done, inbox, audit, logs = run_confirm(tmp_path, page, "contradicted_account")
    assert done is False and inbox._imap.moved == [] and audit["status"] == "open" and audit["consumed_message_id"] is None
