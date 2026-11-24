"""Dialogs that gate a form (Dayforce: Cookie Preferences under an Amphenol Privacy Policy dialog)."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import notices

TWO_MODALS = """<html><body><h1>Application</h1><button onclick="window.nextClicked=true">Next</button>
<div role=dialog id=cookie style="position:fixed;inset:20px;background:#fff;z-index:10"><h2>Cookie Preferences</h2>
  <button onclick="window.cookieMode='enable'">Enable All</button><button onclick="window.cookieMode='disable'">Disable All</button>
  <button onclick="window.cookieSaved=true;document.getElementById('cookie').remove()">Save</button></div>
<div role=dialog id=privacy style="position:fixed;left:100px;top:100px;width:300px;background:#fff;z-index:20"><h2>Amphenol Privacy Policy</h2>
  <p>Please review the policy below.</p><label><input type=checkbox id=agree> I agree to the Privacy Statement</label>
  <button onclick="window.privacySaved=document.getElementById('agree').checked;if(window.privacySaved)document.getElementById('privacy').remove()">Save</button>
  <button onclick="document.getElementById('privacy').remove()">Cancel</button></div></body></html>"""

OTHER_AGREEMENT = """<html><body><div role=dialog style="position:fixed;inset:20px;background:#fff"><h2>Terms of Use</h2>
<label><input type=checkbox> I agree to the Terms of Use</label><button>Save</button></div></body></html>"""


def chrome():
    pw = sync_playwright().start()
    try:
        b = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        pw.stop()
        pytest.skip("no Chrome here")
    return pw, b, b.new_page()


def test_cookie_preferences_get_disable_all_then_save_and_a_privacy_agreement_is_agreed_only_with_the_owners_consent():
    pw, b, page = chrome()
    try:
        page.set_content(TWO_MODALS)
        assert notices.resolve_blocking_dialogs(page, privacy_ok=True) == 2
        assert page.evaluate("({m: window.cookieMode, s: window.cookieSaved, p: window.privacySaved})") == {"m": "disable", "s": True, "p": True}
        assert page.evaluate("!window.nextClicked")                                # never presses the form's own buttons
    finally:
        b.close(); pw.stop()


def test_without_the_owners_consent_a_privacy_agreement_is_left_alone_but_cookies_are_still_declined():
    pw, b, page = chrome()
    try:
        page.set_content(TWO_MODALS)
        messages = []
        assert notices.resolve_blocking_dialogs(page, privacy_ok=False, log=messages.append) == 1
        assert page.evaluate("window.privacySaved") is None and page.locator("#privacy").count() == 1
        assert any("left for the candidate" in m for m in messages)
    finally:
        b.close(); pw.stop()


def test_an_unrelated_agreement_dialog_is_never_touched():
    pw, b, page = chrome()
    try:
        page.set_content(OTHER_AGREEMENT)
        assert notices.resolve_blocking_dialogs(page, privacy_ok=True) == 0
        assert page.locator("input[type=checkbox]").is_checked() is False
    finally:
        b.close(); pw.stop()


SMS_DIALOG_HTML = """<html><body><h1>Application</h1>
<div role=dialog id=sms style="position:fixed;inset:20px;background:#fff;z-index:999"><h2>Short Message Service (SMS) Consent</h2>
<p>By selecting the "I consent to receive SMS messages" option and clicking "save", you consent to receive SMS messages.</p>
<label><input type=radio name=sms id=no value=no> I do not consent to receive SMS messages</label>
<label><input type=radio name=sms id=yes value=yes> I consent to receive SMS messages</label>
<button onclick="window.smsChoice=document.querySelector('input[name=sms]:checked')?.value;document.getElementById('sms').remove()">Save</button></div>
<input id=country></body></html>"""


@pytest.mark.parametrize("recorded,expected", [(True, "yes"), (False, "no")])
def test_an_sms_consent_dialog_gets_the_recorded_answer_and_covers_the_form_no_longer(recorded, expected):
    pw, b, page = chrome()
    try:
        page.set_content(SMS_DIALOG_HTML)
        logs=[]
        assert notices.resolve_blocking_dialogs(page, sms_ok=recorded, log=logs.append) == 1, logs
        assert page.evaluate("window.smsChoice") == expected
        page.locator("#country").click(timeout=2000)          # the form is reachable again
    finally:
        b.close(); pw.stop()


def test_an_sms_consent_with_no_recorded_answer_is_left_for_the_candidate():
    pw, b, page = chrome()
    try:
        page.set_content(SMS_DIALOG_HTML)
        assert notices.resolve_blocking_dialogs(page, sms_ok=None) == 0
        assert page.evaluate("window.smsChoice") is None
    finally:
        b.close(); pw.stop()
