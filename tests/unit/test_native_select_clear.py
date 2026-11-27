"""Optional blank native selects remain blank even when exposed as comboboxes."""
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


@pytest.mark.parametrize('populated', [False, True])
def test_optional_combobox_on_native_select_can_keep_or_choose_its_empty_option(page, populated):
    page.set_content('''<label for=second>Citizenship 2</label><select id=second>
        <option value=""></option><option value=IN>India</option></select>''')
    if populated:
        page.locator('#second').select_option('IN')
    view = forms.read_page(page)
    field = replace(view.fields[0], kind='combobox')
    assert forms.fill_field(page, view, field, '', {}, strict=True) == ''
    assert page.locator('#second').input_value() == ''
    assert forms.read_page(page).fields[0].value == ''


def test_an_already_empty_disabled_placeholder_needs_no_selection_action(page):
    page.set_content('''<label for=second>Citizenship 2</label><select id=second>
        <option value="" disabled selected></option><option value=IN>India</option></select>''')
    view = forms.read_page(page)
    assert forms.fill_field(page, view, replace(view.fields[0], kind='combobox'), '', {}, strict=True) == ''
    assert page.locator('#second').input_value() == ''


@pytest.mark.parametrize('kind', ['select', 'combobox'])
def test_required_select_cannot_be_cleared_as_an_optional_blank(page, kind):
    page.set_content('''<label for=country>Country *</label><select id=country required>
        <option value=""></option><option value=IN selected>India</option></select>''')
    view = forms.read_page(page)
    with pytest.raises(forms.UnavailableOption):
        forms.fill_field(page, view, replace(view.fields[0], kind=kind), '', {}, strict=True)
    assert page.locator('#country').input_value() == 'IN'


def test_missing_empty_option_does_not_clear_to_a_real_country(page):
    page.set_content('''<label for=second>Citizenship 2</label><select id=second>
        <option value=IN selected>India</option><option value=US>United States</option></select>''')
    view = forms.read_page(page)
    with pytest.raises(forms.UnavailableOption):
        forms.fill_field(page, view, replace(view.fields[0], kind='combobox'), '', {}, strict=True)
    assert page.locator('#second').input_value() == 'IN'
