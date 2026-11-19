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


def test_collapsed_required_controls_do_not_make_final_apply_a_startup_action():
    html='''<form onsubmit="window.sent=true;return false"><section>
      <h2><button aria-expanded=false>Profile</button></h2><div hidden><input type=email required></div>
      </section><button type=submit>Apply</button></form>'''
    state,view=start(html,allow_error=True)
    assert not state['sent'] and 'remain collapsed' in view.error


@pytest.mark.parametrize('entry', [
    '<a href="https://jobs.example.test/application" onclick="window.sent=true">Apply now</a>',
    '<button aria-haspopup="true" href="https://jobs.example.test/application" '
    'onclick="window.sent=true">Apply now</button>',
])
def test_published_application_address_precedes_unrelated_collapsed_search_filters(entry):
    html = '''<form role=search onsubmit="window.sent=true;return false">
      <button aria-expanded=false aria-controls=filters>Show More Options</button>
      <div id=filters hidden><label>Location<input name=location></label></div></form>
      <label>Job alert frequency<input type=number value=7></label>''' + entry
    state, view = start(html, routes={'https://jobs.example.test/application': FORM3})
    assert state['url'] == 'https://jobs.example.test/application' and len(view.fields) == 3
    assert not state['sent'] and view.coverage.complete


def test_a_published_button_address_inside_candidate_form_cannot_bypass_submission_guard():
    html = '''<form onsubmit="window.sent=true;return false"><label>Email<input type=email></label>
      <button aria-expanded=false aria-controls=details>More information</button>
      <div id=details hidden><label>Phone<input type=tel></label></div>
      <button aria-haspopup=true href="https://jobs.example.test/send"
      onclick="window.sent=true">Apply now</button></form>'''
    state, view = start(html, allow_error=True)
    assert 'remain collapsed' in view.error and not state['sent']
    assert 'https://jobs.example.test/send' not in state['requests']


@pytest.mark.parametrize('href', ['javascript:window.sent=true', '#apply'])
def test_collapsed_controls_do_not_allow_script_or_fragment_menu_apply_handlers(href):
    html = '''<form><button aria-expanded=false aria-controls=details>More information</button>
      <div id=details hidden><label>Email<input type=email></label></div></form>'''
    html += f'<button aria-haspopup=true href="{href}" onclick="window.sent=true">Apply now</button>'
    state, view = start(html, allow_error=True)
    assert 'remain collapsed' in view.error and not state['sent']


def test_startup_opens_sections_through_expand_all_and_leaves_final_apply_alone():
    html='''<form onsubmit="window.sent=true;return false">
      <a role=button onclick="panel.hidden=false;heading.setAttribute('aria-expanded','true')">Expand all sections</a>
      <section><h2><button id=heading aria-expanded=false>Profile</button></h2>
      <div id=panel hidden><label>Email<input type=email></label></div></section>
      <button type=submit>Apply</button></form>'''
    state,view=start(html)
    assert not state['sent'] and len(view.fields)==1 and view.coverage.complete


def test_a_final_apply_button_in_a_form_with_a_select_and_textarea_is_not_clicked():
    state, _ = start("<form onsubmit=\"window.sent = true; return false;\"><select name=s><option>Yes</option></select>"
                     "<textarea name=t></textarea><button>Apply</button></form>")     # a button's default type is submit
    assert state["sent"] is False


# --- opening an application still works ----------------------------------------------------------------------------

def test_an_apply_link_opens_the_application():
    state, view = start(f"<a href=\"#\" onclick=\"{OPEN_ON_CLICK} return false;\">Apply</a>")
    assert state["opened"] is True and len(view.fields) >= 3


def test_fieldless_careers_shell_has_a_typed_recoverable_startup_failure():
    with pytest.raises(A.FormNotReady, match="no application form and no Apply button"):
        start('<h1>Careers</h1><p>Loading application</p>', url='https://example.com/careers')


@pytest.mark.parametrize('heading',['Apply','Job application'])
def test_resumed_fieldless_application_step_is_handed_off_without_clicking(heading):
    state,view=start('<h1>'+heading+'</h1><h2>Optional updates</h2>'
        '<button type=button onclick="window.sent=true">Skip &amp; continue</button>')
    assert not state['sent'] and not view.fields and 'Skip & continue' in view.buttons


def test_fieldless_careers_pagination_is_not_an_application_resume():
    state,view=start('<h1>Search jobs</h1><button onclick="window.sent=true">Next</button>',allow_error=True)
    assert not state['sent'] and 'no application form' in view.error


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


def test_careers_search_filters_never_reach_application_planning():
    filters = """<h1>Find your next role</h1><form>
      <label>Work area<select><option>All</option><option>Engineering</option></select></label>
      <label>Career status<select><option>All</option><option>Graduate</option></select></label>
      <label>Country / Region<select><option>All</option><option>United States</option></select></label>
      <button type=submit onclick="window.sent=true">Search jobs</button></form>"""
    state, view = start(filters, allow_error=True)
    assert "careers search page" in view.error
    assert not state["sent"]


def test_search_sidebar_does_not_hide_actual_candidate_controls():
    html = ("<h1>Find your next role</h1><label>Work area<select><option>All</option></select></label>"
            "<label>Career status<select><option>All</option></select></label>" + FORM3)
    state, view = start(html)
    assert len(view.fields) == 5 and not state["sent"]


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


@pytest.mark.parametrize("signed_in", [True, False])
def test_email_first_login_is_handed_to_account_agent_before_application_planning(signed_in):
    from types import SimpleNamespace

    posting = "https://careers.example.test/jobs/123"
    login = "https://accounts.example.test/login"
    email_screen = ('<h1>Log in or create account</h1><form><label>Email *<input type=email required></label>'
                    '<button>Continue</button></form>')
    calls = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        try:
            page = b.new_page()

            def serve(route):
                if route.request.url == posting:
                    if calls and signed_in:
                        route.fulfill(status=200, content_type="text/html", body="<h1>Application</h1>" + FORM3)
                    else:
                        route.abort()
                elif route.request.url == login:
                    route.fulfill(status=200, content_type="text/html", body=email_screen)
                else:
                    route.abort()

            page.route("**/*", serve)
            # Begin after Apply has opened the login page. All traffic is
            # fulfilled locally, including startup's post-auth return to the job.
            page.goto(login)
            real_wait = page.wait_for_timeout
            page.wait_for_timeout = lambda ms: real_wait(min(ms, 50))

            def authenticate(url):
                calls.append(url)
                assert page.locator('input[type=email]').count() == 1
                return SimpleNamespace(signed_in=signed_in)

            if signed_in:
                view = A._start(page, posting, authenticate, lambda _: None, already_open=True)
                assert len(view.fields) == 3 and page.url == posting
                assert not page.evaluate("!!window.sent")
            else:
                with pytest.raises(RuntimeError, match="sign-in did not complete"):
                    A._start(page, posting, authenticate, lambda _: None, already_open=True)
                assert page.url == login
            assert calls == [login]
        finally:
            b.close()


@pytest.mark.parametrize("button", ["Apply", "Apply now", "Submit application"])
def test_ambiguous_short_application_cannot_be_sent_by_the_account_agent(button):
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        try:
            page = b.new_page()
            page.set_content('<h1>Sign in</h1><form onsubmit="window.sent=true;return false;">'
                             '<label>Email<input type=email></label><button>' + button + '</button></form>')
            real_wait = page.wait_for_timeout
            page.wait_for_timeout = lambda ms: real_wait(min(ms, 50))
            calls = []
            with pytest.raises(A.FormNotReady, match="mixes sign-in"):
                A._start(page, "https://careers.example.test/apply", calls.append, lambda _: None, already_open=True)
            assert not calls and not page.evaluate("!!window.sent")
        finally:
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


WD_LISTING = 'https://example.wd1.myworkdayjobs.com/Careers/job/Remote/Engineer_R123'
WD_HTML = ('<h2 data-automation-id="jobPostingHeader">Engineer</h2>'
           '<div data-automation-id="jobPostingDescription">Job description</div>'
           '<a data-automation-id="adventureButton" href="'+WD_LISTING+'/apply" '
           'onclick="event.preventDefault(); '+OPEN_ON_CLICK+'">Apply</a>')


def test_workday_listing_runs_the_native_chooser_instead_of_loading_an_empty_parent_route():
    # Loading /apply has no form: only the real click handler opens this SPA chooser.
    state, view = start(WD_HTML, url=WD_LISTING, routes={WD_LISTING+'/apply': '<h1>Careers</h1>'})
    assert state['opened'] and not state['sent'] and len(view.fields) == 3
    assert WD_LISTING+'/apply' not in state['requests']


@pytest.mark.parametrize('change', ['other_host', 'wrong_component', 'cross_origin', 'other_job', 'review', 'native_form'])
def test_workday_native_entry_exception_does_not_apply_to_arbitrary_links_or_forms(change):
    url = WD_LISTING;html = WD_HTML
    if change == 'other_host':
        html=html.replace('example.wd1.myworkdayjobs.com','jobs.example.test');url=url.replace('example.wd1.myworkdayjobs.com','jobs.example.test')
    elif change == 'wrong_component':html=html.replace('adventureButton','finalApply')
    elif change == 'cross_origin':html=html.replace('href="'+WD_LISTING,'href="https://other.wd1.myworkdayjobs.com/Careers/job/Remote/Engineer_R123')
    elif change == 'other_job':html=html.replace('href="'+WD_LISTING,'href="'+WD_LISTING.replace('R123','R999'))
    elif change == 'review':
        html=html.replace(WD_LISTING,WD_LISTING+'/apply/review');url+='/apply/review'
    elif change == 'native_form':html='<form>'+html+'</form>'
    with sync_playwright() as pw:
        b=pw.chromium.launch(channel='chrome',headless=True);page=b.new_page()
        page.route('**/*',lambda r:r.fulfill(status=200,content_type='text/html',body=html));page.goto(url)
        view=forms.read_page(page);link=page.get_by_role('link',name='Apply',exact=True)
        assert not forms._workday_listing_entry(view,link)
        assert not page.evaluate('!!window.opened')
        b.close()


def test_workday_native_entry_is_forbidden_when_candidate_fields_are_present():
    state,view=start(WD_HTML+'<input placeholder="Candidate email" type=email>',url=WD_LISTING,
                     routes={WD_LISTING+'/apply':FORM3})
    assert not state['opened'] and not state['sent']
    assert WD_LISTING+'/apply' in state['requests']


def test_a_detached_iframe_is_not_reused_for_startup_buttons():
    with sync_playwright() as pw:
        b=pw.chromium.launch(channel='chrome',headless=True);page=b.new_page()
        page.set_content('<h1>Careers</h1><iframe srcdoc="<button>Apply</button>"></iframe>')
        page.frame_locator('iframe').get_by_role('button',name='Apply').wait_for()
        view=forms.read_page(page);page.locator('iframe').evaluate('(e)=>e.remove()')
        assert forms.click_open_application(page,view,re.compile('^Apply$')) is None
        b.close()
