"""Real Chrome against local pages: a field-less intermediate screen is moved on with its safe button, an opt-in is never pressed, and the next step's fields appear."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import forms, wizard

STEPPER = """<!doctype html><html><body>
<h1>Apply</h1><h2>My progress</h2>
<ol><li>Contact information</li><li>SMS Notifications Optional</li><li>General questions</li></ol>
<h2>Get SMS updates on your job applications</h2>
<button id="optin" onclick="document.title='OPTED-IN'">Enable SMS updates</button>
<button id="skip" onclick="document.body.innerHTML='<h1>General questions</h1><label>Years of experience <input name=years></label><button>Continue</button>'">Skip &amp; continue</button>
</body></html>"""
ONLY_OPT_IN = """<!doctype html><html><body><h1>Stay in touch</h1><button onclick="document.title='OPTED-IN'">Allow notifications</button></body></html>"""
NOT_NOW = """<!doctype html><html><body><h1>Welcome</h1><button onclick="document.title='OPTED-IN'">Turn on alerts</button>
<button onclick="document.body.innerHTML='<h1>Your details</h1><label>Email <input name=email></label>'">Not now</button></body></html>"""


def with_page(html, fn):
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pytest.skip("no Chrome here")
        try:
            page = browser.new_page()
            page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html) if r.request.resource_type == "document" else r.abort())
            page.goto("https://portal.test/apply")
            return fn(page)
        finally:
            browser.close()


def test_the_stepper_page_is_moved_on_with_skip_and_the_sms_opt_in_is_never_pressed():
    def run(page):
        v = forms.read_page(page)
        assert not v.fields and forms.interstitial_button(v) == "Skip & continue"
        before = wizard.state_of(page, v)
        clicked = forms.advance(page, v)
        verdict, after, _, _ = wizard.wait_for_progress(page, before)
        return clicked, verdict, [f.label for f in after.fields], page.title()
    clicked, verdict, labels, title = with_page(STEPPER, run)
    assert clicked == "Skip & continue" and verdict == "advanced" and any("Years of experience" in x for x in labels)
    assert title != "OPTED-IN"


def test_a_page_whose_only_button_is_an_opt_in_is_not_advanced():
    def run(page):
        v = forms.read_page(page)
        return forms.interstitial_button(v), forms.advance(page, v), page.title()
    step, clicked, title = with_page(ONLY_OPT_IN, run)
    assert step is None and clicked is None and title != "OPTED-IN"


def test_not_now_is_chosen_over_a_turn_on_button():
    def run(page):
        v = forms.read_page(page)
        clicked = forms.advance(page, v)
        return clicked, [f.label for f in forms.read_page(page).fields], page.title()
    clicked, labels, title = with_page(NOT_NOW, run)
    assert clicked == "Not now" and any("Email" in x for x in labels) and title != "OPTED-IN"


# Amazon's consent step: radios of size 0 with an EMPTY decorative label, and the card text in a second label for the same input.
CONSENT_CARDS = """<!doctype html><html><body><h1>Consent</h1>
<div role="group"><p>Do you consent to personalised job recommendations?</p>
<div class="card" style="min-height:60px"><div class="custom-control custom-radio"><input type="radio" id="consent-yes" name="consent-choice" required
 style="position:absolute;opacity:0;width:0;height:0"><label class="custom-control-label" for="consent-yes"></label></div>
 <label class="card-body" for="consent-yes" style="display:block;width:0;height:0;overflow:visible"><div style="width:300px;height:40px;position:relative"><b>Yes</b> Provide personalised job recommendations and transcribe my interviews</div></label></div>
<div class="card" style="min-height:60px"><div class="custom-control custom-radio"><input type="radio" id="consent-no" name="consent-choice" required
 style="position:absolute;opacity:0;width:0;height:0"><label class="custom-control-label" for="consent-no"></label></div>
 <label class="card-body" for="consent-no" style="display:block;width:0;height:0;overflow:visible"><div style="width:300px;height:40px;position:relative"><b>No</b> Continue without personalised job recommendations</div></label></div></div>
<button>Continue</button></body></html>"""


def test_a_zero_size_radio_with_an_empty_label_and_card_text_in_a_second_label_is_a_field_and_can_be_chosen():
    from jobagent.tools import forms
    def run(page):
        v = forms.read_page(page)
        radios = [f for f in v.fields if f.kind == "radio"]
        assert len(radios) == 1, [f.label for f in v.fields]
        f = radios[0]
        opts = f.options
        forms.fill_field(page, v, f, "Yes", {})
        return opts, page.evaluate("[document.getElementById('consent-yes').checked, document.getElementById('consent-no').checked]")
    opts, checked = with_page(CONSENT_CARDS, run)
    assert len(opts) == 2 and opts[0].startswith("Yes") and opts[1].startswith("No")
    assert checked == [True, False]
