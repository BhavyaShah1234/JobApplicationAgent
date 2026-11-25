"""New-account credentials come from the vault, stay out of readers, and never send a form."""
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from jobagent.filling.live_view import LiveView
from jobagent.tools import forms, portal_auth


@pytest.fixture
def setup(monkeypatch):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page()
            page.route('https://recruit.test/**', lambda route: route.fulfill(body='<html></html>',content_type='text/html'))
            page.goto('https://recruit.test/profile')
            saved = {}
            vault = SimpleNamespace(get=lambda key: saved.get(key), set=lambda key, **value: saved.update({key: value}))
            monkeypatch.setattr(portal_auth, 'portal_password', lambda vault: 'Synthetic-only-pw-75!')
            yield page, vault, saved
        finally:
            browser.close()


def profile(page, first='Choose Password', second='Retype Password', heading='Candidate Profile'):
    page.set_content(f'<h1>{heading}</h1><label>{first}<input type=password id=pw required></label>'
                     f'<label>{second}<input type=password id=confirm required></label>'
                     '<button onclick="window.sent=true">Submit Application</button>')
    return forms.read_page(page)


def test_new_account_pair_is_saved_then_filled_without_clicking_submit_or_leaking_password(setup):
    page, vault, saved = setup
    view = profile(page)
    logs = []
    assert portal_auth.fill_new_account_passwords(page, view, vault, 'candidate@example.test', page.url, logs.append)
    assert page.locator('#pw').input_value() == saved['portal:recruit.test']['password']
    assert page.locator('#confirm').input_value() == page.locator('#pw').input_value()
    assert not page.evaluate('window.sent === true')
    assert saved['portal:recruit.test']['verified'] is False
    fresh = forms.read_page(page)
    assert all(f.value == '[credential present]' for f in fresh.fields if f.kind == 'password')
    live = LiveView(page, forms.read_page)
    field = next(f for f in live.capture().fields if f.kind == 'password')
    assert live.refresh(field).value == '[credential present]'
    assert 'Synthetic-only' not in json.dumps([asdict(f) for f in fresh.fields]) + str(logs)


@pytest.mark.parametrize('first,second,heading', [('Current Password','New Password','Change Password'),
                                                ('Password','Password','Candidate Profile'),
                                                ('New Password','Confirm Password','Reset your password')])
def test_unknown_or_recovery_fields_are_not_filled(setup, first, second, heading):
    page, vault, saved = setup
    view = profile(page, first, second, heading)
    assert not portal_auth.fill_new_account_passwords(page, view, vault, 'candidate@example.test', page.url)
    assert not page.locator('#pw').input_value() and not page.locator('#confirm').input_value()
    assert not saved and not page.evaluate('window.sent === true')


def test_a_prefilled_password_is_never_replaced_by_a_different_saved_credential(setup):
    page, vault, saved = setup
    profile(page)
    page.locator('#pw').fill('Owner-entered-unknown')
    assert not portal_auth.fill_new_account_passwords(page, forms.read_page(page), vault, 'candidate@example.test', page.url)
    assert page.locator('#pw').input_value() == 'Owner-entered-unknown'
    assert not page.locator('#confirm').input_value() and not saved


def test_an_existing_account_never_gets_a_new_password_through_registration(setup):
    page, vault, saved = setup
    view = profile(page)
    vault.set('portal:recruit.test', account=True, password='existing-password')
    assert not portal_auth.fill_new_account_passwords(page, view, vault, 'candidate@example.test', page.url)
    assert not page.locator('#pw').input_value() and not page.locator('#confirm').input_value()
    assert saved['portal:recruit.test']['password'] == 'existing-password'


def test_a_widget_that_rewrites_the_password_cannot_proceed_with_different_vault_credentials(setup):
    page, vault, saved = setup
    view = profile(page)
    page.locator('#pw').evaluate("e => e.oninput = () => {e.value = e.value.slice(0, 6)}")
    with pytest.raises(portal_auth.PortalError, match='did not retain the saved credential'):
        portal_auth.fill_new_account_passwords(page, view, vault, 'candidate@example.test', page.url)
    assert not page.evaluate('window.sent === true')
