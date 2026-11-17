import re
import shutil

import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import forms


@pytest.fixture
def page():
    if not shutil.which('google-chrome'):pytest.skip('Chrome required')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page()
        page.context.jobagent_policy=lambda p:None
        yield page
        browser.close()


def selected_search_picker(page, label, prefill, options):
    """A Workday-like single-select chip, including its real Delete behavior."""
    import json
    page.set_content('''<label for=pick>LABEL</label>
      <div data-automation-id=multiSelectContainer>
        <ul id=chips data-automation-id=selectedItemList></ul>
        <input id=pick role=combobox data-uxi-widget-type=selectinput>
        <div id=menu hidden></div>
      </div><script>
      window.selectionWrites=0;window.deleteWrites=0;window.opens=0;
      const labels=OPTIONS;
      function chip(text){chips.innerHTML='';if(!text)return;
        const li=document.createElement('li');const item=document.createElement('div');
        item.dataset.automationId='selectedItem';item.tabIndex=0;item.textContent=text;
        const del=document.createElement('button');del.dataset.automationId='DELETE_charm';
        del.setAttribute('aria-label','Delete '+text);del.onclick=()=>{li.remove();deleteWrites++};
        item.onkeydown=e=>{if(e.key==='Delete'){li.remove();deleteWrites++}};
        item.append(del);li.append(item);chips.append(li)}
      for(const text of labels){const opt=document.createElement('div');opt.setAttribute('role','option');
        opt.dataset.automationId='promptOption';opt.textContent=text;
        opt.onclick=()=>{chip(text);selectionWrites++;menu.hidden=true};menu.append(opt)}
      pick.onclick=()=>{menu.hidden=false;opens++};
      document.addEventListener('keydown',e=>{if(e.key==='Escape')menu.hidden=true});
      chip(PREFILL);
      </script>'''.replace('LABEL', label).replace('OPTIONS', json.dumps(options)).replace('PREFILL', json.dumps(prefill)))


@pytest.mark.parametrize('prefill', ['United States of America (+1)', 'India (+91)', ''])
def test_search_picker_preserves_correct_phone_prefix_and_can_replace_wrong_or_empty_one(page, prefill):
    from jobagent.filling.facts import compile_facts, resolve
    options = ['United States of America (+1)', 'Canada (+1)', 'India (+91)']
    selected_search_picker(page, 'Country Phone Code', prefill, options)
    view = forms.read_page(page); field = view.fields[0]
    forms.discover_options(page, view)
    assert forms.read_page(page).fields[0].value == prefill
    if prefill:
        assert page.evaluate('selectionWrites+deleteWrites+opens') == 0
    facts = compile_facts({'contact': [{'question': 'Phone', 'answer': {
        'country': 'United States', 'country_code': '+1',
    }}]}, '')
    answer = resolve(field, facts).value
    chosen = forms.fill_field(page, view, field, answer, {}, strict=True)
    actual = forms.read_page(page).fields[0].value
    assert chosen == actual == options[0]
    if prefill == options[0]:
        assert page.evaluate('selectionWrites+deleteWrites+opens') == 0


def test_discovering_prefilled_source_does_not_select_or_clear_its_other_options(page):
    selected_search_picker(page, 'How Did You Hear About Us?', 'Internet', ['Internet', 'Employee Referral'])
    view = forms.read_page(page); forms.discover_options(page, view)
    assert view.fields[0].options == ['Internet', 'Employee Referral']
    assert forms.read_page(page).fields[0].value == 'Internet'
    assert page.evaluate('selectionWrites+deleteWrites') == 0
    # A preserved prefill still has to change when the actual source says so.
    assert forms.fill_field(page, view, view.fields[0], 'Employee Referral', {}, strict=True) == 'Employee Referral'
    assert forms.read_page(page).fields[0].value == 'Employee Referral'


def test_an_invalid_prefilled_picker_is_reselected_before_it_can_be_verified(page):
    option = 'United States of America (+1)'
    selected_search_picker(page, 'Country Phone Code', option, [option, 'India (+91)'])
    page.locator('#pick').evaluate("e=>e.setAttribute('aria-invalid','true')")
    page.locator('[role=option]').evaluate_all("es=>es.forEach(e=>e.addEventListener('click',()=>pick.removeAttribute('aria-invalid')))")
    view = forms.read_page(page)
    assert view.fields[0].invalid
    assert forms.fill_field(page, view, view.fields[0], option, {}, strict=True) == option
    assert not forms.read_page(page).fields[0].invalid
    assert page.evaluate('selectionWrites') == 1


def test_unseen_aria_radios_and_contenteditable_are_filled_and_read_back(page):
    page.set_content('''<h1>Candidate details</h1><span id="q_opaque">Preferred workplace</span>
      <div role="radiogroup" aria-labelledby="q_opaque" aria-required="true">
       <button role="radio" aria-checked="false" onclick="this.setAttribute('aria-checked','true')">Remote</button>
       <button role="radio" aria-checked="false" onclick="this.setAttribute('aria-checked','true')">Onsite</button>
      </div><div role="textbox" contenteditable="true" aria-label="Professional profile link"></div>''')
    view=forms.read_page(page)
    radio=next(f for f in view.fields if f.kind=='radio')
    assert radio.label=='Preferred workplace' and radio.required
    forms.fill_field(page,view,radio,'Remote',{})
    text=next(f for f in view.fields if f.kind=='text')
    forms.fill_field(page,view,text,'https://profile.example.org/candidate',{})
    values={f.label:f.value for f in forms.read_page(page).fields}
    assert values=={'Preferred workplace':'Remote','Professional profile link':'https://profile.example.org/candidate'}


@pytest.mark.parametrize('button,caption',[('Attach your CV','Candidate CV *'),('Browse files','Additional document')])
def test_lazy_upload_needs_no_known_caption_or_input_id(page,tmp_path,button,caption):
    from jobagent.steps.apply import _key
    pdf=tmp_path/'test.pdf';pdf.write_bytes(b'unseen upload test')
    page.set_content(f'''<section class="form-field"><label>{caption}</label><button onclick="
      const node=document.createElement('input');node.type='file';node.id='opaque-u91';node.hidden=true;
      this.parentElement.append(node)">{button}</button></section>''')
    view=forms.read_page(page);upload=next(f for f in view.fields if f.kind=='file')
    forms.fill_field(page,view,upload,'resume',{'resume':str(pdf)})
    assert page.locator('#opaque-u91').evaluate('e=>e.files[0].size')==pdf.stat().st_size
    mounted=forms.read_page(page).fields
    assert len(mounted)==1 and _key(mounted[0])==_key(upload)


def test_native_upload_does_not_require_filename_in_body_text(page,tmp_path):
    pdf=tmp_path/'native.pdf';pdf.write_bytes(b'native upload test')
    page.set_content('<label for="opaque-u92">Supporting document</label><input id="opaque-u92" type="file">')
    view=forms.read_page(page)
    forms.fill_field(page,view,view.fields[0],str(pdf),{})
    assert forms.read_page(page).fields[0].value=='native.pdf'


def test_desktop_application_links_stay_in_the_dedicated_tab(page):
    page.route('https://apply.unseen-example.org/**',lambda route:route.fulfill(body='<h1>Application</h1>'))
    page.set_content('<a href="https://apply.unseen-example.org/start" target="_blank">Begin Application</a>')
    forms.click_button(page,forms.read_page(page),re.compile('begin application',re.I))
    page.wait_for_url('https://apply.unseen-example.org/start')
    assert len(page.context.pages)==1
