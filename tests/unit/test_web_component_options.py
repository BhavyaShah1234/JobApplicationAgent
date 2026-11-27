"""SmartRecruiters City returns visible spl-select-option hosts, not DOM role=option nodes."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import forms


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


def test_city_web_component_options_are_discovered_and_selected_in_the_owned_menu(page):
    page.set_content('''<nav><div role=option>San Ramon, CA, US</div></nav>
      <div id=foreign role=listbox><spl-select-option onclick="window.wrongChoice=true">San Ramon, CA, US</spl-select-option></div>
      <x-city></x-city><script>
      customElements.define('x-city', class extends HTMLElement {
        constructor(){super();const root=this.attachShadow({mode:'open'});
          root.innerHTML=`<style>spl-select-option{display:block;padding:8px;cursor:pointer}</style>
            <label for=city>City *</label><input id=city role=combobox aria-controls=menu aria-required=true>
            <div id=menu role=listbox></div>`;
          const city=root.querySelector('input'),menu=root.querySelector('#menu');
          city.addEventListener('input',()=>{menu.innerHTML='';if(!city.value)return;
            setTimeout(()=>{for(const name of ['San Francisco, CA, US','San Ramon, CA, US']){
              const option=document.createElement('spl-select-option');option.textContent=name;
              option.onclick=()=>{city.value=name;city.setAttribute('aria-invalid','false');menu.hidden=true;window.rightChoice=true};
              menu.append(option)}},60)});
        }
      });
      </script>''')
    view = forms.read_page(page)
    field = next(f for f in view.fields if f.label.startswith('City'))
    chosen = forms.fill_field(page, view, field, 'San Ramon', {}, strict=True)
    assert chosen == 'San Ramon, CA, US'
    assert page.locator('x-city input').input_value() == chosen
    assert page.evaluate('window.rightChoice === true && window.wrongChoice !== true')


def test_owned_options_deduplicate_custom_hosts_and_skip_hidden_entries(page):
    page.set_content('''<input id=city role=combobox aria-controls=menu>
      <div id=menu role=listbox>
        <spl-select-option style="display:block">San Ramon, CA, US</spl-select-option>
        <div role=option>San Ramon, CA, US</div>
        <spl-select-option hidden>Wrong hidden city</spl-select-option>
      </div>''')
    assert forms._owned_options(page.locator('#city')) == ['San Ramon, CA, US']


def test_lazy_options_apply_the_same_source_choice_rules_as_upfront_options(page):
    from jobagent.filling.facts import Fact
    from jobagent.filling.planned import PlannedFiller
    page.set_content('''<label for=veteran>Protected Veteran</label><input id=veteran role=combobox aria-controls=menu required>
      <div id=menu role=listbox></div><script>
      veteran.oninput=()=>{menu.innerHTML='';for(const label of ['I am not a protected veteran','I choose not to self-identify']){
        const item=document.createElement('div');item.setAttribute('role','option');item.textContent=label;
        item.onclick=()=>{veteran.value=label;menu.innerHTML=''};menu.append(item)}};
      </script>''')
    filler = PlannedFiller([Fact('veteran', ['Protected Veteran'], 'Not a veteran', 'applicant')],
                           lambda questions: pytest.fail('The saved non-veteran answer is sufficient'))
    result = filler.fill(page, advance_pages=False)
    assert result['state'] == 'page_complete', result['blockers']
    assert page.locator('#veteran').input_value() == 'I am not a protected veteran'


@pytest.mark.parametrize('asian_option', ['Asian', 'Asian (United States of America)'])
def test_known_demographic_fact_searches_a_lazy_picker_without_reasking_the_owner(page, asian_option):
    import json
    from jobagent.filling.facts import Fact
    from jobagent.filling.planned import PlannedFiller
    page.set_content('''<label for=race>Race/Ethnicity</label><input id=race role=combobox aria-controls=menu required>
      <div id=menu role=listbox></div><script>
      race.oninput=()=>{menu.innerHTML='';for(const label of OPTIONS){
        const item=document.createElement('div');item.setAttribute('role','option');item.textContent=label;
        item.onclick=()=>{race.value=label;menu.innerHTML=''};menu.append(item)}};
      </script>'''.replace('OPTIONS', json.dumps([asian_option, 'I choose not to self-identify'])))
    filler = PlannedFiller([Fact('race', ['Race/Ethnicity'], 'Asian', 'applicant')],
                           lambda questions: pytest.fail('Race is already supplied by the owner'))
    result = filler.fill(page, advance_pages=False)
    assert result['state'] == 'page_complete', result['blockers']
    assert page.locator('#race').input_value() == asian_option
