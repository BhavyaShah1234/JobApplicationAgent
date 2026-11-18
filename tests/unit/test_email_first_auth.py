import pytest

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
