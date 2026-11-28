"""An identifier left behind by a disappearing puzzle is not a usable destination."""
from types import SimpleNamespace

import pytest

from jobagent.steps import apply
from jobagent.tools import challenge, forms


def observed_ready(monkeypatch, tmp_path, fields, buttons, *, temporary_error=False, enabled=True):
    view = forms.PageView(fields, buttons, [], [], {})
    monkeypatch.setattr(apply.forms, 'read_page', lambda p: view)
    monkeypatch.setattr(apply.forms, '_visible_buttons',
        lambda view, label: [SimpleNamespace(is_enabled=lambda: enabled)])
    monkeypatch.setattr(apply, '_temporary_form_error', lambda *a: temporary_error)

    def probe(p, *args, **kwargs):
        return {'ready': challenge._controllers[p].ready(p)}

    class Page:
        pass
    page = Page()
    monkeypatch.setattr(apply, '_run', probe)
    return apply._guarded_run(page, tmp_path, 'fixture', filling_strategy='original')['ready']


@pytest.mark.parametrize('kind,label', [('email', 'Email'), ('text', 'Username')])
def test_completed_identifier_and_footer_links_do_not_clear_human_handoff(monkeypatch, tmp_path, kind, label):
    assert not observed_ready(monkeypatch, tmp_path,
        [forms.Field(0, kind, label, value='candidate@example.test')],
        ['Welcome page', 'Privacy', 'Terms'])


@pytest.mark.parametrize('button', ['Next', 'Continue', 'Sign in', 'Log in', 'Submit application'])
def test_completed_identifier_with_usable_continuation_can_resume(monkeypatch, tmp_path, button):
    assert observed_ready(monkeypatch, tmp_path,
        [forms.Field(0, 'email', 'Email', value='candidate@example.test')], [button])


def test_empty_or_invalid_identifier_can_still_be_filled_or_corrected(monkeypatch, tmp_path):
    assert observed_ready(monkeypatch, tmp_path, [forms.Field(0, 'email', 'Email')], [])
    assert observed_ready(monkeypatch, tmp_path,
        [forms.Field(0, 'email', 'Email', value='wrong', invalid=True)], [])


def test_a_disabled_continuation_does_not_clear_human_handoff(monkeypatch, tmp_path):
    assert not observed_ready(monkeypatch, tmp_path,
        [forms.Field(0, 'email', 'Email', value='candidate@example.test')], ['Continue'], enabled=False)


def test_a_new_password_or_application_form_is_a_destination(monkeypatch, tmp_path):
    email = forms.Field(0, 'email', 'Email', value='candidate@example.test')
    assert observed_ready(monkeypatch, tmp_path, [email, forms.Field(1, 'password', 'Password')], [])
    assert observed_ready(monkeypatch, tmp_path, [email, forms.Field(1, 'text', 'First name')], [])


def test_fieldless_posting_and_temporary_error_handling_remain_available(monkeypatch, tmp_path):
    assert observed_ready(monkeypatch, tmp_path, [], ['Apply for this Job'])
    assert not observed_ready(monkeypatch, tmp_path, [], ['Privacy', 'Terms'])
    assert observed_ready(monkeypatch, tmp_path, [], ['Reload'], temporary_error=True)
