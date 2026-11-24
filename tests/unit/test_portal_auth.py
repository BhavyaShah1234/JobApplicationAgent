"""Step 11 pure logic: generated passwords, platform detection, Workday site root."""

import re
from types import SimpleNamespace

import pytest

from jobagent.tools import portal_auth as PA


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
