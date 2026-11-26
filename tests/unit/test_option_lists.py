"""A combobox's options are the list it owns; the page's own menus are never options (Dayforce: 'Prefix' offered 'Search Jobs, Careers')."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import forms

PAGE = """<html><body>
<header><nav><a role=menuitem href=#>Search Jobs</a><a role=menuitem href=#>Careers</a></nav></header>
<input id=c role=combobox aria-controls=owned aria-expanded=false readonly placeholder="Prefix">
<div id=owned role=listbox style="display:none"><div role=option>Mr</div><div role=option>Mr</div><div role=option>Mrs</div><div role=option>Dr</div></div>
<input id=other role=combobox aria-controls=other_list readonly>
<div id=other_list role=listbox><div role=option>UNRELATED OPEN LIST</div></div>
<script>document.getElementById('c').addEventListener('click',()=>{document.getElementById('owned').style.display='block'})</script>
</body></html>"""

NO_OWNED = """<html><body><header><nav><a role=menuitem href=#>Search Jobs</a></nav></header>
<input id=c role=combobox readonly><ul id=popup style="display:none"><li role=option>Yes</li><li role=option>No</li></ul>
<script>document.getElementById('c').addEventListener('click',()=>{document.getElementById('popup').style.display='block'})</script></body></html>"""


def chrome():
    pw = sync_playwright().start()
    try:
        b = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        pw.stop()
        pytest.skip("no Chrome here")
    return pw, b, b.new_page()


def test_a_combobox_reads_only_the_list_it_owns_and_never_the_headers_links():
    pw, b, page = chrome()
    try:
        page.set_content(PAGE)
        assert forms._open_options(page.main_frame, page.locator("#c"), page) == ["Mr", "Mrs", "Dr"]
    finally:
        b.close(); pw.stop()


def test_without_an_owned_list_the_page_wide_reader_still_works_but_skips_the_header_menu():
    pw, b, page = chrome()
    try:
        page.set_content(NO_OWNED)
        assert forms._open_options(page.main_frame, page.locator("#c"), page) == ["Yes", "No"]
    finally:
        b.close(); pw.stop()


def test_a_declared_menu_that_is_not_inserted_yet_never_uses_another_fields_choices():
    pw, b, page = chrome()
    try:
        page.set_content('''<input id=country role=combobox aria-controls="368:_listSelect" readonly>
          <div role=listbox><div role=option>Yes</div><div role=option>No</div></div>
          <script>country.onclick=()=>setTimeout(()=>{
            const menu=document.createElement('div');menu.id='368:_listSelect';menu.setAttribute('role','listbox');
            menu.innerHTML='<div role=option>United States</div><div role=option>India</div>';document.body.append(menu);
          },100)</script>''')
        control = page.locator('#country')
        assert forms._options_for_control(page.main_frame, control) == []
        assert forms._open_options(page.main_frame, control, page) == ['United States', 'India']
    finally:
        b.close(); pw.stop()


def test_consecutive_lazy_menus_remain_bound_to_their_own_question_during_discovery():
    pw, b, page = chrome()
    try:
        page.set_content('''<label>Work Country<input id=country role=combobox aria-controls=countries readonly></label>
          <label>Industry<input id=industry role=combobox aria-controls=industries readonly></label>
          <label>Current Role<input id=current role=combobox aria-controls=roles readonly></label>
          <div role=listbox><div role=option>UNRELATED OPEN LIST</div></div>
          <script>
          for(const [id,menuId,choices] of [['country','countries',['United States','India']],
              ['industry','industries',['Engineering','Information Technology']],['current','roles',['No','Yes']]]){
            document.getElementById(id).onclick=()=>setTimeout(()=>{
              if(document.getElementById(menuId))return;
              const menu=document.createElement('div');menu.id=menuId;menu.setAttribute('role','listbox');
              for(const value of choices){const option=document.createElement('div');option.setAttribute('role','option');
                option.textContent=value;menu.append(option)}document.body.append(menu);
            },100);
          }</script>''')
        view = forms.read_page(page)
        forms.discover_options(page, view)
        assert {f.label: f.options for f in view.fields} == {
            'Work Country': ['United States', 'India'],
            'Industry': ['Engineering', 'Information Technology'],
            'Current Role': ['No', 'Yes'],
        }
        assert page.locator('input').evaluate_all('es=>es.every(e=>e.value === "")')
    finally:
        b.close(); pw.stop()


def test_disappearing_owned_menu_cannot_fall_back_to_a_foreign_same_text_choice(monkeypatch):
    pw, b, page = chrome()
    try:
        page.set_content('''<label>Race/Ethnicity<input id=race role=combobox aria-controls=owned readonly></label>
          <div id=owned role=listbox><div role=option>Asian</div></div>
          <div role=listbox><div role=option onclick="window.wrongChoice=true">Asian</div></div>''')
        view = forms.read_page(page)
        def vanished(control, text):
            page.locator('#owned').evaluate('e=>e.remove()')
            return None
        monkeypatch.setattr(forms, '_owned_option', vanished)
        with pytest.raises(forms.FillError, match="own menu"):
            forms.fill_field(page, view, view.fields[0], 'Asian', {}, strict=True)
        assert page.evaluate('window.wrongChoice !== true')
    finally:
        b.close(); pw.stop()
