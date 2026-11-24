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


MODALS = """<!doctype html><html><body><h1>Questions</h1>
<label for="yrs">Years</label><input id="yrs" required>
<div class="ai-preference-modal modal fade" style="display:none"><div class="modal-body"><fieldset>
 <input type="radio" id="consent-yes" name="c" required aria-label="Consent yes" style="opacity:0;position:absolute;width:0;height:0"></fieldset></div></div>
<div class="modal open-now" role="dialog" style="display:block;position:fixed;top:0;left:0;width:300px;height:200px;background:#fff">
 <label for="open-q">Open dialog question</label><input id="open-q" required></div>
</body></html>"""


def test_required_controls_in_a_closed_modal_are_not_missing_fields_of_the_page_but_an_open_dialog_still_counts():
    from jobagent.filling.discovery import INVENTORY
    def run(page):
        return [x["label"] for x in page.evaluate(INVENTORY)]
    labels = with_page(MODALS, run)
    assert "Years" in labels and "Open dialog question" in labels
    assert "Consent yes" not in labels


NATIVE_COMBO = """<!doctype html><html><body><label>Country <select role="combobox" name="country" required>
<option value="">— Make a Selection —</option><option value="1">United States</option><option value="2">India</option><option value="3">Åland Islands</option></select></label>
<label>I consent <select role="combobox" name="sms"><option value="">— Make a Selection —</option><option value="9">I consent to receive text messages</option><option value="8">No thanks</option></select></label></body></html>"""


def test_a_native_select_reported_as_a_combobox_is_chosen_with_select_option_from_its_live_options():
    from dataclasses import replace
    from jobagent.tools import forms
    def run(page):
        v = forms.read_page(page)
        out = {}
        for f in v.fields:
            f = replace(f, kind="combobox", options=[])          # the reader's option list can be empty or cut short: the live one is used
            ans = "India" if "Country" in f.label else "I consent to receive text messages"
            out[f.label] = forms.fill_field(page, v, f, ans, {})
        return out, page.evaluate("[...document.querySelectorAll('select')].map(s => s.options[s.selectedIndex].text)")
    out, chosen = with_page(NATIVE_COMBO, run)
    assert chosen == ["India", "I consent to receive text messages"] and len(out) == 2



ICIMS_DROPDOWN = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<label id="lbl" for="c">Country</label>
<select id="c" name="c" class="dropdown-hide" role="combobox" aria-controls="c_listbox" style="display:none"><option value="1" selected>United States</option></select>
<a id="c_icimsDropdown" class="dropdown-select" tabindex="0" role="combobox" aria-labelledby="lbl" aria-label="Country" aria-expanded="false"
   style="display:inline-block;border:1px solid #888;width:220px;height:28px"><span id="c_fake" class="dropdown-text">— Make a Selection —</span></a>
<div id="c_icimsDropdown_ctnr" class="dropdown-container" aria-owns="c_dropdown-results" style="display:none;border:1px solid #888;width:220px"><input type="text" class="dropdown-search" id="q" aria-label="Search">
<ul id="c_dropdown-results" role="listbox" style="margin:0;padding:0;list-style:none"></ul></div>
<script>
const ALL=["— Make a Selection —","United States","Canada","India","Åland Islands"], ul=document.getElementById('c_dropdown-results'), q=document.getElementById('q'),
 a=document.getElementById('c_icimsDropdown'), box=document.getElementById('c_icimsDropdown_ctnr');
function draw(){ul.innerHTML='';ALL.filter(x=>!q.value||x.toLowerCase().includes(q.value.toLowerCase())).forEach(x=>{const li=document.createElement('li');li.setAttribute('role','option');li.textContent=x;li.style.padding='4px';
  li.onclick=()=>{document.getElementById('c_fake').textContent=x;box.style.display='none'};ul.appendChild(li)})}
a.onclick=()=>{box.style.display='block';draw()}; q.onkeyup=draw;
</script></body></html>"""


def test_the_icims_dropdown_widget_is_opened_searched_and_read_back_from_its_own_value_span():
    from jobagent.tools import forms
    def run(page):
        v = forms.read_page(page)
        f = next(x for x in v.fields if x.kind == "combobox")
        before = f.value
        got = forms.fill_field(page, v, f, "India", {}, strict=True)
        after = next(x for x in forms.read_page(page).fields if x.kind == "combobox").value
        return before, got, after
    before, got, after = with_page(ICIMS_DROPDOWN, run)
    assert before == "" and got == "India" and after == "India"


def test_a_country_with_no_states_leaves_the_state_dropdown_as_it_is_and_an_address_list_with_only_enter_manually_takes_that_entry():
    from jobagent.tools import forms
    html_states = ICIMS_DROPDOWN.replace('— Make a Selection —</span>', 'No states available</span>')
    def run(page):
        v = forms.read_page(page)
        f = next(x for x in v.fields if x.kind == "combobox")
        return forms.fill_field(page, v, f, "Maharashtra", {}, strict=True)
    assert "No states available" in with_page(html_states, run)
    html_addr = ICIMS_DROPDOWN.replace('"United States","Canada","India","Åland Islands"', '"— Enter Manually —"').replace("!q.value||", "x.includes('Enter Manually')||!q.value||")   # an address list always offers it
    def run2(page):
        v = forms.read_page(page)
        f = next(x for x in v.fields if x.kind == "combobox")
        return forms.fill_field(page, v, f, "100 East Santa Fe Street", {}, strict=True)
    assert "Enter Manually" in with_page(html_addr, run2)


HIDDEN_FILE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div class="row"><label id="l" for="PortalProfileFields.Resume_File">Please upload your resume (max size: 5MB).</label><span class="req">* required</span>
<div class="iCIMS_FileUploadButtonBlock"><div tabindex="0" class="iCIMS_FileFieldButton" style="border:1px solid #888;width:140px;height:28px"
 onclick="document.getElementById('PortalProfileFields.Resume_File').click()">My Computer</div>
<div style="position:absolute;width:0;height:0;overflow:hidden;opacity:0"><input type="file" id="PortalProfileFields.Resume_File" name="resume"></div></div></div>
</body></html>"""


def test_a_file_input_hidden_in_a_zero_size_wrapper_behind_a_styled_button_is_an_upload_field():
    from jobagent.tools import forms
    def run(page):
        v = forms.read_page(page)
        return [(f.kind, f.label, f.required) for f in v.fields]
    fields = with_page(HIDDEN_FILE, run)
    files = [f for f in fields if f[0] == "file"]
    assert len(files) == 1 and "resume" in files[0][1].lower()


def test_the_inventory_ignores_the_native_select_behind_an_icims_dropdown_widget():
    from jobagent.filling.discovery import INVENTORY
    def run(page):
        return [x["label"] for x in page.evaluate(INVENTORY)]
    labels = with_page(ICIMS_DROPDOWN, run)
    assert [x for x in labels if x.strip().lower().startswith("country")] == ["Country"]      # the widget once, not also the select behind it
