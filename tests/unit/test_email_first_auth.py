import pytest
from types import SimpleNamespace

from jobagent.steps import apply
from jobagent.tools import forms


@pytest.mark.parametrize("heading", ["Log in or create account", "Login", "Sign in", "Sign-in to your account",
                                     "Sign in with email", "Create an account", "Create your account"])
def test_email_first_auth_is_recognized(heading):
    view = forms.PageView([forms.Field(0, "email", "Email *")], ["Continue"], [], [heading], {})
    assert apply._authentication_page(view)


@pytest.mark.parametrize("heading", ["Application", "Contact information", "Enter your email", "Sign-in bonus questionnaire",
                                     "How did you hear about us?", "Sign up for job alerts", "Register"])
def test_contact_email_is_not_sufficient_to_infer_authentication(heading):
    view = forms.PageView([forms.Field(0, "email", "Email *")], ["Apply"], [], [heading], {})
    assert not apply._authentication_page(view)


def test_auth_heading_does_not_take_over_a_real_application():
    fields = [forms.Field(0, "email", "Email"), forms.Field(1, "text", "Name"), forms.Field(2, "file", "Resume")]
    assert not apply._authentication_page(forms.PageView(fields, ["Submit"], [], ["Sign in"], {}))


def test_password_only_auth_is_still_recognized():
    assert apply._authentication_page(forms.PageView([forms.Field(0, "password", "Password")], [], [], [], {}))


def test_a_username_text_control_can_start_auth_but_an_unrelated_control_cannot():
    for label, expected in [("Username", True), ("Email address", True), ("Employee ID", False)]:
        assert apply._authentication_page(forms.PageView([forms.Field(0, "text", label)], [], [], ["Log in"], {})) is expected


def late_login_view():
    return forms.PageView([forms.Field(0, 'email', 'Email'), forms.Field(1, 'password', 'Password')],
                          ['Continue'], [], ['Enter Your Password'], {})


def test_credentials_revealed_after_an_application_page_go_to_the_account_callback():
    page = SimpleNamespace(url='https://login.icims.com/u/login/password?state=one-time')
    calls = []
    def account(url):
        calls.append(url)
        return SimpleNamespace(signed_in=True)
    assert apply._resume_authentication(page, late_login_view(), account, lambda msg: None, set())
    assert calls == [page.url]  # preserve the live POST/session; do not navigate back to the posting


def test_unresolved_later_authentication_stops_before_planning_or_another_credential_attempt():
    page = SimpleNamespace(url='https://login.icims.com/u/login/password?state=first')
    calls, attempted = [], set()
    def account(url):
        calls.append(url)
        return SimpleNamespace(signed_in=False)
    with pytest.raises(apply.FormNotReady, match='did not complete'):
        apply._resume_authentication(page, late_login_view(), account, lambda msg: None, attempted)
    page.url = 'https://login.icims.com/u/login/password?state=different'
    with pytest.raises(apply.FormNotReady, match='no further credential attempt'):
        apply._resume_authentication(page, late_login_view(), account, lambda msg: None, attempted)
    assert len(calls) == 1


def test_combined_account_application_stays_with_the_filler_and_submit_guard():
    view = forms.PageView([forms.Field(0, 'password', 'Choose Password'),
                          *[forms.Field(i+1, 'text', f'Qualification {i}') for i in range(8)]],
                         ['Submit Application'], [], ['Candidate Profile'], {})
    assert not apply._resume_authentication(SimpleNamespace(url='https://example.test/application'), view,
                   lambda url: pytest.fail('A combined application is not a login form'), lambda msg: None, set())


def test_ordinary_application_page_does_not_trigger_account_navigation():
    view = forms.PageView([forms.Field(0, 'email', 'Email'), forms.Field(1, 'file', 'Resume')],
                         ['Submit'], [], ['Apply'], {})
    assert not apply._resume_authentication(SimpleNamespace(url='https://example.test/application'), view,
                   lambda url: pytest.fail('This is an application'), lambda msg: None, set())
