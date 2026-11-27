"""A named school search must precede its owner's allowed Other fallback."""
from dataclasses import replace

import pytest
from playwright.sync_api import sync_playwright

from jobagent.filling.facts import choice
from jobagent.tools import forms


@pytest.fixture
def school_page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        yield page
        browser.close()


def setup(page, has_match):
    """Mirror the initial Other entry and the results delivered on Enter."""
    page.set_content('''<label for=school>School or University</label>
        <input id=school role=combobox data-uxi-widget-type=selectinput aria-controls=menu aria-expanded=true>
        <div id=menu role=listbox></div><script>
        const input=document.getElementById('school'), menu=document.getElementById('menu');
        window.probes=[];window.otherClicks=0;
        function render(searched){
            menu.replaceChildren();
            for(const name of (searched && HAS_MATCH ? ['Example Institute (EI)','Other'] : ['Other'])){
                const item=document.createElement('div');item.setAttribute('role','option');item.textContent=name;
                item.onclick=()=>{input.value=name;window.otherClicks+=name==='Other';menu.replaceChildren();};
                menu.append(item);
            }
        }
        input.onkeydown=event=>{if(event.key==='Enter'){
            event.preventDefault();window.probes.push(input.value);render(true);
        }};
        render(false);
        </script>'''.replace('HAS_MATCH', 'true' if has_match else 'false'))
    view = forms.read_page(page)
    return view, view.fields[0]


@pytest.mark.parametrize('has_match', [True, False])
def test_searches_the_named_school_before_using_an_approved_other(school_page, has_match):
    page = school_page
    view, field = setup(page, has_match)
    chosen = forms.fill_field(page, view, field, 'Example Institute', {}, strict=True,
        alternatives=['Other'], option_resolver=lambda options:
        choice(replace(field, options=options, options_state='complete'), ['Example Institute', 'Other']))
    expected = 'Example Institute (EI)' if has_match else 'Other'
    assert chosen == page.locator('#school').input_value() == expected
    assert page.evaluate('window.probes'), 'Other must not short-circuit the named search'
    assert page.evaluate('window.otherClicks') == (0 if has_match else 1)


def test_an_explicit_other_request_remains_direct(school_page):
    view, field = setup(school_page, True)
    assert forms.fill_field(school_page, view, field, 'Other', {}, strict=True) == 'Other'
    assert school_page.evaluate('window.probes') == []
