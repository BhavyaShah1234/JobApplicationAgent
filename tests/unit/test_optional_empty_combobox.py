"""An explicitly blank optional picker needs no invented empty menu option."""
from dataclasses import replace

import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import forms


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        yield browser.new_page()
        browser.close()


def setup(page, *, value='', required=False):
    page.set_content('''<label for=second>Citizenship 2:</label>
        <input id=second role=combobox aria-controls=menu placeholder="No Selection">
        <div id=menu role=listbox hidden><div role=option>No Selection</div><div role=option>India</div></div>
        <script>window.actions=0;second.onclick=()=>{window.actions++;menu.hidden=false;};
        second.oninput=second.onchange=()=>window.actions++;</script>''')
    page.locator('#second').evaluate('(e,v)=>{e.value=v.value;e.required=v.required}',
                                    {'value':value,'required':required})
    view = forms.read_page(page)
    return view, view.fields[0]


def test_optional_already_empty_picker_is_a_verified_no_action(page):
    view, field = setup(page)
    assert forms.fill_field(page, view, field, '', {}, strict=True) == ''
    assert forms.read_page(page).fields[0].value == ''
    assert page.evaluate('window.actions') == 0


def test_stale_empty_snapshot_does_not_hide_a_nonempty_current_picker(page):
    view, field = setup(page, value='India')
    with pytest.raises(forms.FillError):
        forms.fill_field(page, view, replace(field, value=''), '', {}, strict=True)
    assert page.locator('#second').input_value() == 'India'


def test_required_empty_picker_cannot_take_the_optional_noop(page):
    view, field = setup(page, required=True)
    with pytest.raises(forms.FillError):
        forms.fill_field(page, view, field, '', {}, strict=True)


def test_populated_optional_picker_is_not_reported_as_cleared_without_a_clear_action(page):
    view, field = setup(page, value='India')
    with pytest.raises(forms.FillError):
        forms.fill_field(page, view, field, '', {}, strict=True)
    assert page.locator('#second').input_value() == 'India'


def test_invalid_empty_picker_is_not_silently_accepted(page):
    view, field = setup(page)
    page.locator('#second').evaluate("e=>e.setAttribute('aria-invalid','true')")
    with pytest.raises(forms.FillError):
        forms.fill_field(page, view, field, '', {}, strict=True)
    assert page.evaluate('window.actions') == 0


def test_newly_required_empty_picker_is_not_silently_accepted(page):
    view, field = setup(page)
    page.locator('#second').evaluate("e=>e.required=true")
    with pytest.raises(forms.FillError):
        forms.fill_field(page, view, field, '', {}, strict=True)
    assert page.evaluate('window.actions') == 0
