"""F17: extraction completeness is reported separately from answer verification; open shadow roots and srcdoc frames are read;
unreadable frames and unreadable regions are never reported as a clean form."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.steps import apply as A
from jobagent.tools import forms

FORM = "<form><label>Email address <input type=email name=e></label><label>Phone <input type=tel name=p></label></form>"


def read_view(html, routes=None, wrap_js=None):
    saved = forms.FIELDS_JS
    try:
        if wrap_js:
            forms.FIELDS_JS = wrap_js(saved)
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(channel="chrome", headless=True)
            except Exception:
                pytest.skip("no Chrome here")
            try:
                page = browser.new_page()
                served = {"https://app.test/": html, **(routes or {})}
                page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=served[r.request.url])
                           if r.request.url in served else r.abort())
                page.goto("https://app.test/")
                page.wait_for_timeout(300)
                view = forms.read_page(page)
                # fill the first field through the real locator, to prove what was read can also be written
                filled = None
                if view.fields:
                    f = view.fields[0]
                    forms.locate(view, f).fill("me@example.org")
                    filled = forms.read_page(page).fields[0].value
                return view, filled
            finally:
                browser.close()
    finally:
        forms.FIELDS_JS = saved


# --- what is read ------------------------------------------------------------------------------------------------------

def test_fields_inside_an_open_shadow_root_are_read_and_can_be_filled():
    html = ("<h1>Apply</h1><div id=host></div><script>const r = document.getElementById('host').attachShadow({mode:'open'});"
            f"r.innerHTML = {FORM!r};</script>")
    view, filled = read_view(html)
    assert sorted(f.label for f in view.fields) == ["Email address", "Phone"]
    assert view.coverage.complete and view.coverage.shadow_roots == 1 and view.coverage.shadow_fields == 2
    assert filled == "me@example.org"


def test_a_form_in_a_srcdoc_iframe_is_read():
    html = f'<h1>Apply</h1><iframe srcdoc="{FORM.replace(chr(34), chr(39))}" width=500 height=200></iframe>'
    view, filled = read_view(html)
    assert sorted(f.label for f in view.fields) == ["Email address", "Phone"]
    assert view.coverage.complete and view.coverage.frames_read == 2 and filled == "me@example.org"


def test_an_ordinary_embedded_ats_form_still_works_and_a_challenge_frame_is_left_out_on_purpose():
    html = ('<h1>Apply</h1><iframe src="https://ats.test/form" width=500 height=200></iframe>'
            '<iframe src="https://www.google.com/recaptcha/api2/anchor" width=300 height=80></iframe>')
    view, _ = read_view(html, {"https://ats.test/form": FORM, "https://www.google.com/recaptcha/api2/anchor": "<p>I am not a robot</p>"})
    assert len(view.fields) == 2 and view.coverage.complete
    assert view.coverage.challenge_frames == ["https://www.google.com/recaptcha/api2/anchor"]


# --- what could not be read is never reported as a clean form ------------------------------------------------------------

def test_a_frame_that_cannot_be_read_is_reported_not_skipped_silently():
    html = '<h1>Apply</h1><iframe src="https://ats.test/form" width=500 height=200></iframe><input name=top placeholder=Name>'
    broken = lambda js: "(start) => { if (window.top !== window) throw new Error('frame unreadable'); return (" + js + ")(start); }"  # noqa: E731
    view, _ = read_view(html, {"https://ats.test/form": FORM}, wrap_js=broken)
    assert [f.label for f in view.fields] == ["Name"]                       # what WAS readable is still returned
    assert not view.coverage.complete
    assert view.coverage.unreadable and "ats.test/form" in view.coverage.unreadable[0]["url"]
    blockers = A.coverage_blockers([{"page": 1, "extraction": view.coverage.summary()}])
    assert blockers and "not fully read" in blockers[0] and "frame could not be read" in blockers[0]


def test_a_closed_shadow_root_is_recovered_and_read():
    html = ("<h1>Apply</h1><x-widget style='display:block;width:320px;height:48px'></x-widget><script>"
            "customElements.define('x-widget', class extends HTMLElement { constructor() { super(); "
            "this.attachShadow({mode:'closed'}).innerHTML = '<input name=z>'; } });</script>")
    view, filled = read_view(html)
    assert len(view.fields) == 1 and view.coverage.complete and view.coverage.opaque == []
    assert filled == "me@example.org"


def test_small_custom_elements_such_as_icons_are_not_flagged():
    html = ("<h1>Apply</h1><my-icon style='display:inline-block;width:16px;height:16px'></my-icon>" + FORM +
            "<script>customElements.define('my-icon', class extends HTMLElement {});</script>")
    view, _ = read_view(html)
    assert len(view.fields) == 2 and view.coverage.complete


def test_a_clean_page_has_no_problems_and_no_blockers():
    view, _ = read_view("<h1>Apply</h1>" + FORM)
    assert view.coverage.complete and view.coverage.problems() == []
    assert A.coverage_blockers([{"page": 1, "extraction": view.coverage.summary()}]) == []
    assert A.coverage_blockers([{"page": 1}]) == []                            # a page record from before this field existed
