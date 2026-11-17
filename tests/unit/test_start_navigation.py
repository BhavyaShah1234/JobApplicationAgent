"""F01: startup may open an application, never send one. A bare "Apply" on a short form is the form's final button."""
import re
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from jobagent.steps import apply as A
from jobagent.tools import forms

FORM3 = ("<form><input name=a type=text placeholder=Name><input name=b type=email placeholder=Email>"
         "<input name=c type=tel placeholder=Phone><button type=submit>Submit application</button></form>")
OPEN_ON_CLICK = (f"window.opened = true; document.body.insertAdjacentHTML('beforeend', '{FORM3}');")


def start(html: str, routes: dict | None = None, allow_error: bool = False, url: str | None = None, expected_url: str | None = None):
    """Run the real `_start` on a page already open (no navigation). Returns (what the page recorded, the view).
    `routes`: {url: html} answered by the test, every other request is blocked (nothing leaves the machine)."""
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pytest.skip("no Chrome here")
        try:
            page = browser.new_page()
            served, requests = dict(routes or {}), []
            if url:
                served[url] = html

            def answer(r):
                requests.append(r.request.url)                  # what the page asked for, kept across navigations
                if r.request.url in served:
                    r.fulfill(status=200, content_type="text/html", body=served[r.request.url])
                else:
                    r.abort()
            page.route("**/*", answer)
            if url:
                page.goto(url)
            else:
                page.set_content(html)
            real = page.wait_for_timeout
            page.wait_for_timeout = lambda ms: real(min(ms, 200))     # the real loop waits 4 s per pass
            try:
                view = A._start(page, expected_url or url or "https://example.com/apply", lambda u: None, lambda m: None, already_open=True)
            except RuntimeError as e:
                if not allow_error:
                    raise
                view = type("NoView", (), {"fields": [], "error": str(e)})()
            state = page.evaluate("({sent: !!window.sent, opened: !!window.opened})")
            state["url"], state["requests"] = page.url, requests
            return state, view
        finally:
            browser.close()


# --- the final button of a short form is never clicked at startup --------------------------------------------------

def test_a_one_field_form_with_a_final_apply_button_is_not_submitted():
    state, view = start("<form onsubmit=\"window.sent = true; return false;\"><input name=e type=email placeholder=Email>"
                        "<button type=submit>Apply</button></form>")
    assert state["sent"] is False and len(view.fields) == 1       # the field goes to the planner, the button to the review path


def test_a_two_field_prefilled_form_with_a_final_apply_button_is_not_submitted():
    state, view = start("<form onsubmit=\"window.sent = true; return false;\"><input name=e type=email value=me@x.org>"
                        "<input name=p type=tel value=5551234><button type=submit>Apply</button></form>")
    assert state["sent"] is False and len(view.fields) == 2


def test_an_unlabelled_style_submit_input_named_apply_is_not_clicked_either():
    state, _ = start("<h1>Quick application</h1><form onsubmit=\"window.sent = true; return false;\">"
                     "<input name=e type=email><input type=submit value=\"Apply\"></form>")
    assert state["sent"] is False


def test_a_final_apply_button_in_a_form_with_a_select_and_textarea_is_not_clicked():
    state, _ = start("<form onsubmit=\"window.sent = true; return false;\"><select name=s><option>Yes</option></select>"
                     "<textarea name=t></textarea><button>Apply</button></form>")     # a button's default type is submit
    assert state["sent"] is False


# --- opening an application still works ----------------------------------------------------------------------------

def test_an_apply_link_opens_the_application():
    state, view = start(f"<a href=\"#\" onclick=\"{OPEN_ON_CLICK} return false;\">Apply</a>")
    assert state["opened"] is True and len(view.fields) >= 3


def test_an_apply_button_outside_any_form_opens_the_application():
    state, view = start(f"<button type=button onclick=\"{OPEN_ON_CLICK}\">Apply now</button>")
    assert state["opened"] is True and len(view.fields) >= 3


def test_an_apply_button_in_a_form_with_only_hidden_fields_opens_the_application():
    state, view = start(f"<form onsubmit=\"{OPEN_ON_CLICK} return false;\"><input type=hidden name=job value=7>"
                        "<button type=submit>Apply</button></form>")
    assert state["opened"] is True and len(view.fields) >= 3


def test_continue_application_still_resumes_a_started_application():
    state, view = start(f"<a href=\"#\" onclick=\"{OPEN_ON_CLICK} return false;\">Continue application</a>")
    assert state["opened"] is True and len(view.fields) >= 3


# --- the capability to send an application ---------------------------------------------------------------------------

def test_the_functions_that_send_an_application_need_an_approval_object():
    with pytest.raises(TypeError):
        forms.SubmitApproval("outputs/x/1", "req1")                      # nobody else can mint one
    with pytest.raises(TypeError):
        forms.click_final_submit(None, None, re.compile("submit"), None)
    with pytest.raises(TypeError):
        forms.click_submit_input(None, None)
    ok = forms.SubmitApproval("outputs/x/1", "req1", _issuer=forms._ISSUER)
    assert ok.job_dir == "outputs/x/1" and ok.request_id == "req1"


def test_only_the_reviewed_submission_path_calls_the_functions_that_send():
    root = Path(__file__).resolve().parents[2] / "src" / "jobagent"
    callers = {}
    for p in root.rglob("*.py"):
        text = p.read_text()
        for name in ("click_final_submit(", "click_submit_input(", "allow_submit"):
            if name in text:
                callers.setdefault(name, set()).add(str(p.relative_to(root)))
    allowed = {"steps/submit.py", "tools/forms.py"}                       # definitions live in forms; calls only in submit
    assert all(files <= allowed for files in callers.values()), callers
    assert "allow_submit" not in callers                                  # the permissive flag is gone


# --- F01 independent verification: submissions that don't come from a native submit button ----------------------------

def test_a_javascript_final_apply_button_in_a_form_with_a_field_is_not_clicked():          # Codex case 1
    state, view = start("<h1>Job application</h1><form onsubmit=\"window.sent = true; return false;\">"
                        "<input name=email value=me@example.org>"
                        "<button type=\"button\" onclick=\"this.form.requestSubmit(); this.remove();\">Apply</button></form>")
    assert state["sent"] is False and len(view.fields) == 1


def test_a_field_attached_to_the_form_from_outside_it_counts_as_a_field_of_that_form():     # Codex case 2
    state, view = start("<h1>Job application</h1><input name=email form=application value=me@example.org>"
                        "<form id=application onsubmit=\"window.sent = true; this.querySelector('button').remove(); return false;\">"
                        "<button type=\"submit\">Apply</button></form>")
    assert state["sent"] is False and len(view.fields) == 1


def test_a_javascript_link_or_a_button_outside_the_form_is_not_navigation_while_fields_are_on_the_page():
    handler = "document.forms[0].requestSubmit(); return false;"
    state, _ = start(f"<h1>Application</h1><form onsubmit=\"window.sent = true; return false;\"><input name=e value=x></form>"
                     f"<a href=\"#\" onclick=\"{handler}\">Apply</a>")
    assert state["sent"] is False
    state, _ = start(f"<h1>Application</h1><form onsubmit=\"window.sent = true; return false;\"><input name=e value=x></form>"
                     f"<button type=button onclick=\"{handler}\">Apply</button>")
    assert state["sent"] is False


def test_a_form_whose_only_visible_control_is_a_search_box_still_counts_as_a_form_with_fields():
    # the field reader skips search boxes, so there are "no fields" on the page; the form's own controls still count
    state, view = start("<h1>Application</h1><form onsubmit=\"window.sent = true; return false;\">"
                        "<input type=search name=q value=x>"
                        "<button type=button onclick=\"this.form.requestSubmit()\">Apply</button></form>", allow_error=True)
    assert state["sent"] is False
    assert "never clicked" in view.error        # nothing was sent, and the stop says why an Apply button was left alone


def test_a_real_link_to_another_page_is_followed_by_address_not_clicked_even_with_a_field_on_the_page():
    apply_page = "<h1>Apply</h1>" + FORM3
    state, view = start("<h1>Senior Engineer</h1><input name=news type=email placeholder=\"Email for job alerts\">"
                        "<a href=\"https://jobs.example.test/apply\" onclick=\"fetch('https://jobs.example.test/handler-ran')\">"
                        "Apply</a>", routes={"https://jobs.example.test/apply": apply_page})
    assert state["url"] == "https://jobs.example.test/apply" and len(view.fields) >= 3
    assert "https://jobs.example.test/handler-ran" not in state["requests"]     # its click handler never ran: the address was loaded


@pytest.mark.parametrize('glyph',['»','›','→'])
def test_decorated_apply_now_link_opens_application_instead_of_planning_an_alert_question(glyph):
    state,view=start('<label>How often to receive a job alert?<input type=number></label>'
                     '<a href="https://jobs.example.test/application" onclick="window.sent=true">Apply now '+glyph+'</a>',
                     routes={'https://jobs.example.test/application':FORM3})
    assert state['url']=='https://jobs.example.test/application' and len(view.fields)==3
    assert not state['sent']


def test_decorated_apply_now_does_not_send_a_short_form():
    state,view=start('<form onsubmit="window.sent=true;return false"><label>Email<input type=email></label>'
                     '<button>Apply now »</button></form>')
    assert len(view.fields)==1 and not state['sent']


SF_URL = "https://careers.example.test/career?career_ns=job_listing&career_job_req_id=123"
SF_ENTRY = "checkDpcs2AndProceed({isUserLoggedIn: false, jobReqId: 123, jobSecKey: '123', isApplyWithLinkedIn:false, elementId: 'applyButton_top', evtSrc:'Apply'}); return false;"


def sf_listing(extra=""):
    return (f'<label>Language<button role="combobox" aria-label="Language">English</button></label>'
            f'<form id="careerform">{extra}<button onclick="{SF_ENTRY}">Apply</button></form>'
            f'<script>function checkDpcs2AndProceed(params){{{OPEN_ON_CLICK}}}</script>')


def test_successfactors_listing_language_picker_does_not_hide_entry_navigation():
    state, view = start(sf_listing(), url=SF_URL)
    assert state["opened"] and not state["sent"] and len(view.fields) >= 3


@pytest.mark.parametrize("req,opened", [("123", True), ("456", False)])
def test_successfactors_postback_matches_hidden_listing_state_to_intended_job(req, opened):
    state_fields = f'<input type=hidden name=career_ns value=job_listing><input type=hidden name=career_job_req_id value={req}>'
    state, _ = start(sf_listing(state_fields), url="https://careers.example.test/career?_s.crb=session", expected_url=SF_URL)
    assert state["opened"] is opened and not state["sent"]


@pytest.mark.parametrize("url,extra", [
    (SF_URL.replace("job_listing", "job_application"), ""),
    (SF_URL.replace("id=123", "id=456"), ""),
    (SF_URL, '<input type="email" placeholder="Email">'),
])
def test_successfactors_exception_refuses_application_routes_other_jobs_and_candidate_controls(url, extra):
    state, _ = start(sf_listing(extra), url=url)
    assert not state["opened"] and not state["sent"]


def test_successfactors_exception_refuses_custom_candidate_fields_and_extra_handler_code():
    custom = '<button role="combobox" aria-label="Current location">Choose</button>'
    state, _ = start(sf_listing().replace('<form id="careerform">', custom + '<form id="careerform">'), url=SF_URL)
    assert not state["opened"]
    state, _ = start(sf_listing().replace(SF_ENTRY, SF_ENTRY + ' window.sent = true;'), url=SF_URL)
    assert not state["opened"] and not state["sent"]


@pytest.mark.parametrize("strategy", ["original", "experimental"])
def test_desktop_startup_runs_guarded_navigation_even_when_listing_has_a_utility_field(tmp_path, monkeypatch, strategy):
    class ReachedForm(Exception):
        pass
    monkeypatch.setattr(A, "context", lambda *a: {"_jd": {"apply_url": SF_URL}})
    monkeypatch.setattr(A, "recover_desktop_form", lambda *a: None)
    monkeypatch.setattr(A.challenge, "guard", lambda *a: None)
    monkeypatch.setattr(A.availability, "check_page", lambda *a: None)
    def filled(page, *a, **kw):
        assert page.evaluate("!!window.opened")
        raise ReachedForm()
    monkeypatch.setattr(A, "fill_page", filled)
    from jobagent.filling import adapter
    monkeypatch.setattr(adapter, "fill_page", filled)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=sf_listing()) if r.request.url == SF_URL else r.abort())
        page.goto(SF_URL)
        real_wait = page.wait_for_timeout
        page.wait_for_timeout = lambda ms: real_wait(min(ms, 50))
        with pytest.raises(ReachedForm):
            A._run(page, tmp_path, "fixture", model=object(), already_open=True, filling_strategy=strategy)
        assert not page.evaluate("!!window.sent")
        b.close()


def test_resumed_desktop_login_keeps_the_intended_posting_as_its_return_address(tmp_path, monkeypatch):
    class StopAtEntry(Exception):
        pass
    page = type("LoginPage", (), {"url": "https://careers.example.test/login"})()
    monkeypatch.setattr(A, "context", lambda *a: {"_jd": {"apply_url": SF_URL}})
    monkeypatch.setattr(A, "recover_desktop_form", lambda *a: None)
    monkeypatch.setattr(A.challenge, "guard", lambda *a: None)
    monkeypatch.setattr(A.availability, "check_page", lambda *a: None)
    def enter(page, url, *a, **kw):
        assert url == SF_URL and kw["already_open"]
        raise StopAtEntry()
    monkeypatch.setattr(A, "_start", enter)
    with pytest.raises(StopAtEntry):
        A._run(page, tmp_path, "fixture", model=object(), already_open=True)


def test_failed_portal_authentication_stops_before_treating_login_as_an_application():
    from types import SimpleNamespace
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        page.set_content('<label>Email<input type=email></label><label>Password<input type=password></label><button>Sign In</button>')
        real_wait = page.wait_for_timeout
        page.wait_for_timeout = lambda ms: real_wait(min(ms, 50))
        with pytest.raises(RuntimeError, match="sign-in did not complete"):
            A._start(page, SF_URL, lambda _: SimpleNamespace(signed_in=False), lambda _: None, already_open=True)
        b.close()


def test_signed_in_successfactors_masthead_picker_does_not_block_portalcareer_navigation():
    html = (f'<div id="headerNavBar" role="region"><div id="rcmCareerLangDropDown"><a role="combobox" aria-label="Language">English</a></div></div>'
            f'<h1>Career Opportunities: Engineer (123)</h1><form id="careerform"><button onclick="{SF_ENTRY.replace("false", "true", 1)}">Apply</button></form>'
            f'<script>function checkDpcs2AndProceed(params){{{OPEN_ON_CLICK}}}</script>')
    state, view = start(html, url="https://careers.example.test/portalcareer?_s.crb=session", expected_url=SF_URL)
    assert state['opened'] and not state['sent']
    assert len(view.fields) == 3 and all(f.label != 'Language' for f in view.fields)


def test_candidate_language_question_remains_a_field_and_final_apply_stays_protected():
    state, view = start('<form onsubmit="window.sent=true;return false"><label>Language<select><option>English</option></select></label><button>Apply</button></form>')
    assert not state['sent'] and len(view.fields) == 1 and view.fields[0].label.startswith('Language')
