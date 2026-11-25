"""Step 11 pure logic: generated passwords, platform detection, Workday site root."""

import re
from types import SimpleNamespace

import pytest

from jobagent.tools import portal_auth as PA


@pytest.fixture(autouse=True)
def isolate_server_lockouts(tmp_path, monkeypatch):
    from jobagent.tools import portal_agent
    monkeypatch.setattr(portal_agent, '_lockout_file', lambda host: tmp_path / (host + '-lockout.json'))


def test_password_meets_usual_portal_rules():
    for _ in range(50):
        p = PA.new_password()
        assert len(p) == 18
        assert re.search(r"[A-Z]", p) and re.search(r"[a-z]", p) and re.search(r"\d", p) and re.search(r"[!@#$%^*]", p)
    assert PA.new_password() != PA.new_password()


def test_platform_and_workday_site_root():
    url = ("https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced/job/San-Jose/"
           "XMLNAME-2027-University-Graduate---Machine-Learning-Engineer_R172085")
    assert PA.platform_of(url) == "workday"
    assert PA._wd_site(url) == "https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced"
    assert PA.platform_of("https://careers-acme.icims.com/jobs/123/x/job") == "icims"
    assert PA.platform_of("https://job-boards.greenhouse.io/doordashusa/jobs/1") == "unknown"


@pytest.fixture
def live_login(monkeypatch):
    from jobagent.tools import portal_agent
    url = "https://careers.example.test/career?_s.crb=post-backed-session"
    page = SimpleNamespace(url=url, form="live POST state")
    events, stored = [], {}
    vault = SimpleNamespace(get=lambda key: stored.get(key), set=lambda key, **v: stored.update({key: v}))
    memory = SimpleNamespace(data={"notes": []}, note=lambda *a: None, record_visit=lambda *a, **kw: None)
    monkeypatch.setattr(PA, "PortalMemory", lambda *a: memory)
    monkeypatch.setattr(PA.db, "record_agent_domain", lambda *a: None)
    monkeypatch.setattr(PA, "candidate_values", lambda email, password: {"EMAIL": email, "PASSWORD": password})
    monkeypatch.setattr(PA, "_clear_site_cookies", lambda *a: None)
    monkeypatch.setattr(PA.challenge, "goto", lambda *a, **kw: events.append("goto"))
    monkeypatch.setattr(PA.challenge, "guard", lambda *a: events.append("guard"))
    def run(*a, **kw):
        events.append("agent")
        assert page.form == "live POST state"
        return {"state": "signed_in", "registered": False, "verified_mail": False, "steps": []}
    monkeypatch.setattr(portal_agent, "run", run)
    return page, url, vault, SimpleNamespace(conn=object()), events


@pytest.mark.parametrize("already_open,fresh,first", [(False, False, "goto"), (True, False, "guard"), (True, True, "goto")])
def test_general_portal_handoff_preserves_live_forms_and_keeps_explicit_navigation_modes(live_login, already_open, fresh, first):
    page, url, vault, inbox, events = live_login
    result = PA.ensure_account(page, url, vault, inbox, None, "fixture@example.test", log=lambda _: None,
                               already_open=already_open, fresh=fresh)
    assert result.signed_in and events == [first, "agent"]


def test_live_login_handoff_refuses_an_unrelated_current_page(live_login):
    page, url, vault, inbox, events = live_login
    page.url = "https://unrelated.example.test/login"
    with pytest.raises(PA.PortalError, match="no longer matches"):
        PA.ensure_account(page, url, vault, inbox, None, "fixture@example.test", already_open=True)
    assert events == []


def test_rejected_registration_is_not_recorded_as_account_creation(live_login,monkeypatch):
    from jobagent.tools import portal_agent
    page,url,vault,inbox,events=live_login
    monkeypatch.setattr(portal_agent,'run',lambda *a,**k: {
        'state':'failed','registered':True,'verified_mail':False,'steps':[]})
    result=PA.ensure_account(page,url,vault,inbox,None,'fixture@example.test',log=lambda _:None)
    assert not result.created and not result.signed_in
    saved=vault.get('portal:careers.example.test')
    assert 'created' not in saved and not saved['account']


@pytest.mark.parametrize('tenant,auth_host', [('careers-peraton.icims.com','login.icims.com'),
                                           ('careers.garmin.com','login.icims.com'),
                                           ('jobs.ericsson.com','career2.successfactors.eu')])
def test_universal_login_uses_the_employer_credentials_and_rejection_scope(live_login, monkeypatch, tenant, auth_host):
    from jobagent.tools import portal_agent
    page, _, vault, inbox, events = live_login
    page.url = 'https://' + auth_host + '/u/login/password?state=live-session'
    vault.set('portal:' + auth_host, email='different@example.com', password='unrelated-global-password')
    vault.set('portal:' + tenant, email='tenant@example.com', password='tenant-specific-password')
    def run(page, url, values, inbox, hitl, host, **kwargs):
        assert host == tenant
        assert values['EMAIL'] == 'tenant@example.com' and values['PASSWORD'] == 'tenant-specific-password'
        return {'state': 'signed_in', 'registered': False, 'verified_mail': False, 'steps': []}
    monkeypatch.setattr(portal_agent, 'run', run)
    result = PA.ensure_account(page, page.url, vault, inbox, None, 'agent@example.com', log=lambda msg: None,
                               already_open=True, account_url='https://' + tenant + '/jobs/123')
    assert result.host == tenant and result.signed_in
    assert vault.get('portal:' + auth_host)['password'] == 'unrelated-global-password'
    assert vault.get('portal:' + tenant)['verified']


@pytest.mark.parametrize("label", ["Continue with Google", "Continue with Microsoft", "Continue with Linkedin", "Continue with Indeed",
                                   "Continue with Corporate Login", "Sign in with Google", "Log in with Facebook"])
def test_a_social_login_button_is_refused_on_icims_so_the_agent_cannot_loop_on_it(label):
    from jobagent.tools.portal_agent import social_login_refusal
    why = social_login_refusal("https://login.icims.com/u/login/identifier?state=x", label)
    assert why and why.startswith("refused") and "already linked" in why


@pytest.mark.parametrize("url,label", [("https://login.icims.com/u/login", "Continue"), ("https://login.icims.com/u/login", "Next"),
                                       ("https://login.icims.com/u/login", "Forgot password"), ("https://accounts.example.com/", "Continue with Google"),
                                       ("https://jobs.example.com/login", "Sign in with Google")])
def test_other_buttons_and_other_sites_are_not_affected(url, label):
    from jobagent.tools.portal_agent import social_login_refusal
    assert social_login_refusal(url, label) is None


def test_the_sign_in_agent_may_type_only_approved_placeholders_never_a_value_it_composed():
    from jobagent.tools.portal_agent import literal_fill_refusal as r
    for ok in ("{EMAIL}", "{PASSWORD}", "{FIRST_NAME}", "{LAST_NAME}", "{PHONE}", "{CODE}", "{CODE[3]}", "{LEGAL_FIRST_NAME}", ""):
        assert r(ok, "x") is None, ok
    for bad in ("New York", "10001", "United States", "India", "1990-01-01"):
        assert "never invent" in (r(bad, "Citizenship 1") or ""), bad


def test_registration_extras_are_lazy_filtered_and_never_raise():
    from jobagent.tools import portal_agent as pa
    pa.set_registration_profile(None)
    assert pa.registration_extras() == {}
    pa.set_registration_profile(lambda: {"CITIZENSHIP": "India", "CITY": "Austin", "EMAIL": "x", "STATE": ""})
    assert pa.registration_extras() == {"CITIZENSHIP": "India", "CITY": "Austin"}     # only the approved keys, none empty
    pa.set_registration_profile(lambda: 1 / 0)
    assert pa.registration_extras() == {}
    pa.set_registration_profile(None)


def test_the_registration_profile_gives_the_applicants_citizenship_and_the_jobs_address_from_the_existing_logic(tmp_path, monkeypatch):
    import json
    from jobagent.steps import apply as A
    from jobagent.tools import applicant, application_address
    job = tmp_path / "job"
    job.mkdir()
    (job / "jd.json").write_text(json.dumps({"company": "Acme", "title": "Eng", "locations": ["Austin, TX"], "locations_resolved": []}))

    def fake_prepare(ctx, out_dir, model, log=print):          # what the real job-city logic does: override the application's address
        for _, e in applicant.entries(ctx["_applicant"]):
            if e["question"] == "Current address":
                e["answer"] = {"line1": "100 Congress Ave", "city": "Austin", "state": "Texas", "postal_code": "78701",
                               "county": "Travis", "country": "United States"}
        return {}
    monkeypatch.setattr(application_address, "prepare", fake_prepare)
    got = A.registration_profile(job, "acme", None)()
    assert got["CITIZENSHIP"] == "India" and got["CITY"] == "Austin" and got["POSTAL_CODE"] == "78701"
    assert got["COUNTRY_OF_RESIDENCE"] == "United States" and got["ADDRESS_LINE1"] == "100 Congress Ave"


def test_a_posting_with_no_us_location_gives_citizenship_only_and_no_address(tmp_path, monkeypatch):
    import json
    from jobagent.steps import apply as A
    from jobagent.tools import application_address
    job = tmp_path / "job"
    job.mkdir()
    (job / "jd.json").write_text(json.dumps({"company": "Acme", "title": "Eng", "locations": ["Remote"], "locations_resolved": []}))
    monkeypatch.setattr(application_address, "prepare", lambda *a, **k: (_ for _ in ()).throw(application_address.NoUsLocation("no")))
    got = A.registration_profile(job, "acme", None)()
    assert got == {"CITIZENSHIP": "India"}


def test_the_registration_placeholder_substitution_lives_in_the_function_that_initialises_its_cache():
    """A scoping slip (cache set in run, used in _run) crashed the live Ericsson registration with UnboundLocalError."""
    import inspect
    from jobagent.tools import portal_agent as pa
    src = inspect.getsource(pa._run)
    assert "_extras = None" in src and "registration_extras(log)" in src


def test_the_registration_profile_turns_a_model_name_into_the_model_object(tmp_path, monkeypatch):
    """Live Ericsson run: the CLI passes a model NAME (or None); address preparation needs the model object ('NoneType has no model_name')."""
    import json
    from jobagent import llm
    from jobagent.steps import apply as A
    from jobagent.tools import application_address
    job = tmp_path / "job"
    job.mkdir()
    (job / "jd.json").write_text(json.dumps({"company": "Acme", "title": "Eng", "locations": ["Austin, TX"], "locations_resolved": []}))
    made, seen = object(), {}
    class Writer:
        model_name = "writer"
    monkeypatch.setattr(llm, "writer", lambda *a, **k: Writer())
    monkeypatch.setattr(application_address, "prepare", lambda ctx, out, model, log=print: seen.update(model=model) or {})
    A.registration_profile(job, "acme", None)()
    assert getattr(seen["model"], "model_name", None) == "writer"


PICKLIST = """<html><body>
<input id="c" role="combobox" aria-controls="lst" aria-expanded="false" type="text" placeholder="No Selection">
<ul id="lst" role="listbox"></ul><input id="hidden" type="hidden" value="">
<script>
const DATA=["British Indian Ocean Territory","India","Indonesia","Iceland"];
const c=document.getElementById('c'), l=document.getElementById('lst');
c.addEventListener('keyup',()=>{                      // filters on key events only; a programmatic fill() fires none
  const q=c.value.toLowerCase(); l.innerHTML='';
  DATA.filter(x=>x.toLowerCase().includes(q)).forEach(x=>{const li=document.createElement('li');li.setAttribute('role','option');li.textContent=x;
    li.onclick=()=>{c.value=x;document.getElementById('hidden').value=x;l.innerHTML='';};l.appendChild(li);});});
</script></body></html>"""


def _chrome_page():
    import pytest
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        pw.stop()
        pytest.skip("no Chrome here")
    return pw, browser, browser.new_page()


def test_a_search_and_click_picklist_takes_the_exact_entry_not_a_longer_one_that_contains_it():
    from jobagent.tools.portal_agent import pick_combobox_option
    pw, browser, page = _chrome_page()
    try:
        page.set_content(PICKLIST)
        assert pick_combobox_option(page, page.locator("#c"), "India") == "India"
        assert page.evaluate("document.getElementById('hidden').value") == "India"      # not 'British Indian Ocean Territory'
        page.set_content(PICKLIST)
        assert pick_combobox_option(page, page.locator("#c"), "Narnia") is None
        assert page.evaluate("document.getElementById('hidden').value") == ""
    finally:
        browser.close()
        pw.stop()


def test_a_nationality_list_takes_the_single_entry_that_is_the_country_plus_a_short_suffix():
    from jobagent.tools.portal_agent import pick_combobox_option
    pw, browser, page = _chrome_page()
    try:
        page.set_content(PICKLIST.replace('["British Indian Ocean Territory","India","Indonesia","Iceland"]',
                                          '["Indian","Indonesian","Icelandic"]'))
        assert pick_combobox_option(page, page.locator("#c"), "India") == "Indian"
        assert page.evaluate("document.getElementById('hidden').value") == "Indian"
        # two candidates, or a long suffix, is never guessed
        page.set_content(PICKLIST.replace('["British Indian Ocean Territory","India","Indonesia","Iceland"]', '["Indiana","Indian"]'))
        assert pick_combobox_option(page, page.locator("#c"), "India") is None
        page.set_content(PICKLIST.replace('["British Indian Ocean Territory","India","Indonesia","Iceland"]', '["Indiana"]'))
        assert pick_combobox_option(page, page.locator("#c"), "India") is None
    finally:
        browser.close()
        pw.stop()


def test_continue_with_google_is_refused_on_ukg_ultipro_as_on_icims_and_allowed_elsewhere():
    from jobagent.tools.portal_agent import social_login_refusal as r
    assert r("https://signin-us.ultipro.com/u/login?state=x", "Continue with Google") is not None
    assert r("https://careers-peraton.icims.com/x", "Continue with Google") is not None
    assert r("https://www.amazon.jobs/x", "Continue with Google") is None
    assert r("https://signin-us.ultipro.com/u/login", "Sign up") is None


def test_the_agent_prompt_tells_it_to_use_sign_up_when_invalid_credentials_is_all_the_site_says():
    from jobagent.tools import portal_agent as pa
    import inspect
    src = inspect.getsource(pa)
    assert "Invalid credentials" in src and "use that route once" in src and "Never use \"Continue with Google\" on ultipro.com" in src


def test_a_click_that_lands_on_google_from_a_refusing_portal_is_sent_back():
    from jobagent.tools.portal_agent import social_login_refusal as r
    # the loop asks exactly this question about the page it came from when it finds itself on accounts.google.com
    assert r("https://signin-us.ultipro.com/u/login", "Continue with Google")
    assert r("https://www.amazon.jobs/en/x", "Continue with Google") is None     # elsewhere the Google route is still allowed


def test_the_sign_up_link_is_found_by_name_with_its_index_after_a_rejected_password():
    from jobagent.tools.portal_agent import signup_link_hint
    items = [{"label": "Email address *"}, {"label": "Password *"}, {"label": "Show password"}, {"label": "Create or reset your password"},
             {"label": "Sign in"}, {"label": "Sign up"}, {"label": "Continue with Google"}]
    frame = type("F", (), {"evaluate": lambda self, js: {"items": items}})()
    hint = signup_link_hint({0: frame})
    assert hint and "F0[5]" in hint and "never Continue with Google" in hint
    no_signup = type("F", (), {"evaluate": lambda self, js: {"items": [{"label": "Email"}, {"label": "Sign in"}]}})()
    assert signup_link_hint({0: no_signup}) is None
    assert signup_link_hint({0: type("F", (), {"evaluate": lambda self, js: 1 / 0})()}) is None


def test_a_create_your_account_page_is_recognised_even_with_only_email_and_password():
    from jobagent.tools.portal_agent import is_create_account_page as c, is_registration
    ukg = "\n".join(["FRAME 0 https://signin-us.ultipro.com/u/signup",
                     "TEXT: Create your account | Enter the credentials you will use to sign in | Already have an account? Log in",
                     "ELEMENTS:", '  [0] input/text "Email address *"', '  [1] input/password "Password *"'])
    login = "\n".join(["FRAME 0 https://signin-us.ultipro.com/u/login",
                       "TEXT: Welcome, come on in! | Create or reset your password | Don't have an account? Sign up",
                       "ELEMENTS:", '  [0] input/text "Email address *"', '  [1] input/password "Password *"'])
    assert c(ukg) and not is_registration(ukg)          # the older detector missed it: no confirm field, no name fields
    assert not c(login)                                  # a sign-in page that merely links to Sign up is not one


def test_password_rejections_are_counted_across_runs_and_the_third_attempt_is_refused(tmp_path, monkeypatch):
    from jobagent.tools import portal_agent as pa
    monkeypatch.setattr(pa, "_rejections_file", lambda host: tmp_path / f"{host}.json")
    host = "signin-us.ultipro.com"
    assert pa.too_many_recent_rejections(host) is None
    pa.record_rejection(host, now=1000.0)
    assert pa.too_many_recent_rejections(host) is None                      # one rejection: a second try is still allowed
    pa.record_rejection(host, now=1100.0)
    import time
    monkeypatch.setattr(time, "time", lambda: 1200.0)
    why = pa.too_many_recent_rejections(host)
    assert why and "vault set" in why and "locking the account" in why
    monkeypatch.setattr(time, "time", lambda: 1100.0 + pa.REJECTION_WINDOW_S + 1)
    assert pa.too_many_recent_rejections(host) is None                      # old rejections expire


def test_other_hosts_are_not_affected_by_one_hosts_rejections(tmp_path, monkeypatch):
    from jobagent.tools import portal_agent as pa
    monkeypatch.setattr(pa, "_rejections_file", lambda host: tmp_path / f"{host}.json")
    pa.record_rejection("a.example.com", now=1.0)
    pa.record_rejection("a.example.com", now=2.0)
    assert pa.recent_rejections("b.example.com", now=3.0) == []


def test_register_goal_has_its_own_prompt_that_never_tries_a_login_and_stops_if_the_account_exists():
    from jobagent.tools import portal_agent as pa
    assert "CREATE it now" in pa.REGISTER_PROMPT and "Never try to sign in" in pa.REGISTER_PROMPT
    assert "the account already exists, answer failed" in pa.REGISTER_PROMPT and "never reset" in pa.REGISTER_PROMPT
    assert pa.REGISTER_PROMPT.startswith(pa.PROMPT)                      # all the sign-in agent's safety rules still apply


def test_register_only_goes_to_the_general_agent_even_for_a_platform_with_an_adapter(monkeypatch):
    from jobagent.tools import portal_auth as pa
    calls = []
    monkeypatch.setattr(pa, "ensure_with_agent", lambda *a, **k: calls.append(k.get("register_only")) or "agent")
    monkeypatch.setattr(pa, "_ensure_with_adapter", lambda *a, **k: "adapter")
    url = "https://example.wd5.myworkdayjobs.com/en-US/x/job/y"
    assert pa.platform_of(url) in pa.ADAPTERS
    assert pa.ensure_account(None, url, None, None, None, "e@example.com", register_only=True) == "agent" and calls == [True]
    assert pa.ensure_account(None, url, None, None, None, "e@example.com") == "adapter"


@pytest.mark.parametrize('message', ['Execution context was destroyed, most likely because of a navigation',
                                   'Frame was detached'])
def test_auth_target_discards_a_navigation_race(message):
    from playwright.sync_api import Error
    from jobagent.tools import portal_agent as agent
    def evaluate(*args):
        raise Error(message)
    frames = {0: SimpleNamespace(evaluate=evaluate)}
    assert agent._target(frames, agent.AuthAction(thought='Old page', action='click', element=0)) is None


def test_auth_target_does_not_hide_unrelated_script_errors():
    from playwright.sync_api import Error
    from jobagent.tools import portal_agent as agent
    def evaluate(*args):
        raise Error('ReferenceError: broken script')
    with pytest.raises(Error, match='broken script'):
        agent._target({0: SimpleNamespace(evaluate=evaluate)},
                      agent.AuthAction(thought='Old page', action='click', element=0))


def test_auth_hard_restriction_stops_without_asking_for_a_puzzle(monkeypatch):
    from jobagent.tools import portal_agent as agent, notices, challenge
    page = type('Page', (), {'url': 'https://signin.example.test/signup',
                            'wait_for_timeout': lambda self, ms: None})()
    monkeypatch.setattr(agent.llm, 'writer', lambda *a, **kw: object())
    monkeypatch.setattr(notices, 'dismiss_cookie_overlay', lambda page: False)
    monkeypatch.setattr(agent, '_settle_unknown', lambda page:
                        challenge.Challenge('access-restricted', page.url, 'Access is temporarily restricted'))
    hitl = SimpleNamespace(ask=lambda *a, **kw: pytest.fail('A restriction is not a solvable puzzle'))
    with pytest.raises(challenge.Blocked, match='temporarily restricted'):
        agent._run(page, page.url, {}, None, hitl, 'signin.example.test', lambda msg: None,
                   None, [], 'register', [])


def test_auth_challenge_uses_the_shared_verified_handoff(monkeypatch):
    from jobagent.tools import portal_agent as agent, notices, challenge
    page = type('Page', (), {'url': 'https://signin.example.test/signup',
                            'wait_for_timeout': lambda self, ms: None})()
    block = challenge.Challenge('hcaptcha', page.url, 'Visible verification widget')
    monkeypatch.setattr(agent.llm, 'writer', lambda *a, **kw: object())
    monkeypatch.setattr(notices, 'dismiss_cookie_overlay', lambda page: False)
    monkeypatch.setattr(agent, '_settle_unknown', lambda page: block)
    hitl = object()
    def handoff(actual_page, actual_block, actual_hitl, **kwargs):
        assert actual_page is page and actual_block is block and actual_hitl is hitl
        raise challenge.Blocked('Verification is still unresolved')
    monkeypatch.setattr(challenge, 'wait_for_user', handoff)
    with pytest.raises(challenge.Blocked, match='still unresolved'):
        agent._run(page, page.url, {}, None, hitl, 'signin.example.test', lambda msg: None,
                   None, [], 'register', [])


@pytest.mark.parametrize('label,url', [
    ('Reset your password', ''), ('Forgot password?', ''), ('Create or reset your password', ''),
    ('Continue', 'https://login.icims.com/u/reset-password/request/tenant'),
    ('', 'https://portal.test/password_reset/token'),
    ('Password Reset Instructions', 'https://portal.test/verification-link'),
])
def test_only_the_explicit_reset_goal_can_enter_recovery(label, url):
    from jobagent.tools import portal_agent as agent
    assert agent.recovery_refusal('register', label, url)
    assert agent.recovery_refusal('sign_in', label, url)
    assert agent.recovery_refusal('reset_password', label, url) is None


@pytest.mark.parametrize('label,url', [('Sign up', 'https://portal.test/u/signup'),
                                     ('Create your account', ''), ('Show password', ''), ('Continue', '')])
def test_normal_registration_controls_are_not_password_recovery(label, url):
    from jobagent.tools import portal_agent as agent
    assert agent.recovery_refusal('register', label, url) is None


@pytest.mark.parametrize('text', ['An account with this email already exists.', 'The user already exists.',
                                'Your email address is already registered.', 'This email is already in use.'])
def test_existing_account_error_does_not_depend_on_a_previous_rejected_password(text):
    from jobagent.tools import portal_agent as agent
    assert agent.EXISTING_ACCOUNT_ERROR.search(text)
    assert not agent.EXISTING_ACCOUNT_ERROR.search('Already have an account? Log in')


def test_registration_stops_on_existing_account_before_the_model_can_retry(monkeypatch):
    from jobagent.tools import portal_agent as agent, notices
    page = SimpleNamespace(url='https://signup.example.test/signup', wait_for_timeout=lambda ms: None)
    monkeypatch.setattr(agent.llm, 'writer', lambda *a, **kw: object())
    monkeypatch.setattr(agent.llm, 'structured_invoke', lambda *a, **kw: pytest.fail('No more model action after existing-account error'))
    monkeypatch.setattr(notices, 'dismiss_cookie_overlay', lambda page: False)
    monkeypatch.setattr(agent, '_settle_unknown', lambda page: None)
    monkeypatch.setattr(agent, 'snapshot', lambda page: ('FRAME 0 x\nTEXT: An account with this email already exists.\nELEMENTS:', {}))
    result = agent._run(page, page.url, {}, None, None, 'signup.example.test', lambda msg: None,
                        None, [], 'register', [])
    assert result['state'] == 'failed' and 'existing account' in result['reason']
    assert not result['registered']


def test_explicit_server_lockout_is_bounded_and_scoped_to_its_host():
    from jobagent.tools import portal_agent as agent
    agent.record_lockout('locked.example.test', now=100, seconds=60)
    assert agent.recent_lockout('locked.example.test', now=159)
    assert agent.recent_lockout('locked.example.test', now=160) is None
    assert agent.recent_lockout('other.example.test', now=101) is None


@pytest.mark.parametrize('goal', ['sign_in', 'register'])
def test_registration_flag_cannot_bypass_an_explicit_server_lockout(monkeypatch, goal):
    from jobagent.tools import portal_agent as agent
    agent.record_lockout('signin.example.test')
    page = SimpleNamespace(url='https://signin.example.test/signup')
    monkeypatch.setattr(agent.llm, 'writer', lambda *a, **kw: pytest.fail('Do not call a model while the portal is locked'))
    result = agent._run(page, page.url, {}, None, None, 'signin.example.test', lambda msg: None,
                        None, [], goal, [])
    assert result['state'] == 'failed' and 'no sign-in or registration retry before' in result['reason']


@pytest.mark.parametrize('value,kind,allowed', [('{PASSWORD}', 'password', True), ('{ALT_PASSWORD}', 'password', True),
                                              ('{PASSWORD}!', 'password', False), ('{FIRST_NAME}', 'password', False),
                                              ('{EMAIL}', 'password', False), ('{PASSWORD}', 'text', False),
                                              ('{FIRST_NAME}', 'text', True)])
def test_auth_cannot_set_a_password_different_from_the_saved_credential(value, kind, allowed):
    from jobagent.tools import portal_agent as agent
    assert (agent.credential_fill_refusal(value, kind) is None) is allowed


def test_a_frame_that_detaches_while_the_page_navigates_does_not_stop_the_cookie_notice_scan():
    from jobagent.tools.notices import dismiss_cookie_overlay

    class Detached:
        def locator(self, selector):
            raise RuntimeError("Locator.all: Frame was detached")

    class Page:
        frames = [Detached(), Detached()]

    assert dismiss_cookie_overlay(Page()) is False


def test_a_too_many_attempts_or_locked_page_is_recognised_and_a_plain_login_page_is_not():
    from jobagent.tools.portal_agent import lockout_in_view as lk
    assert lk("FRAME 0 x\nTEXT: Too many attempts were detected from this network. Please try again later.\nELEMENTS:\n")
    assert lk("FRAME 0 x\nTEXT: The account is locked due to too many incorrect login attempts\nELEMENTS:\n")
    assert not lk("FRAME 0 x\nTEXT: Welcome, come on in! | Create or reset your password | Sign up\nELEMENTS:\n  [0] input/text \"Email\"")


def test_failed_google_visits_are_hidden_where_google_is_a_dead_end_but_rules_always_show():
    from jobagent.tools.portal_agent import notes_for_prompt as n
    notes = ["2026-11-24: failed: chose x@y in Google > chose x@y in Google", "2026-11-23: job description lives in `main`",
             "2026-11-24: RULE: use Sign up, never Google"]
    ukg = n(notes, "https://signin-us.ultipro.com/u/login")
    assert all("chose x@y" not in s for s in ukg) and any("RULE:" in s for s in ukg)
    elsewhere = n(notes, "https://www.amazon.jobs/x")
    assert any("chose x@y" in s for s in elsewhere)                      # where Google can work the history is kept
