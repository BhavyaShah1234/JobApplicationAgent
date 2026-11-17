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


def selected_search_picker(page, label, prefill, options, multiple=False):
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
      function chip(text){if(!MULTIPLE)chips.innerHTML='';if(!text)return;
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
      </script>'''.replace('LABEL', label).replace('OPTIONS', json.dumps(options)).replace('PREFILL', json.dumps(prefill)).replace('MULTIPLE',json.dumps(multiple)))


def test_skills_are_separate_choices_and_replace_old_chips_only_once(page):
    from jobagent.filling.facts import choice
    selected_search_picker(page,'Type to Add Skills','Old skill',['Python','Go','Kubernetes'],multiple=True)
    page.locator('[data-automation-id=multiSelectContainer]').evaluate("e=>e.dataset.uxiWidgetType='multiselect'")
    view=forms.read_page(page);f=view.fields[0]
    assert f.multiple
    answer=choice(f,['Python, Go'])
    assert answer=='Python; Go'
    forms.fill_field(page,view,f,answer,{},strict=True)
    got=forms.read_page(page).fields[0].value
    assert set(got.split('; '))=={'Python','Go'}
    assert page.evaluate('deleteWrites')==1 and page.evaluate('selectionWrites')==2
    assert forms.same_value('Python; Go',got,'combobox',f.label,multiple=True)
    assert not forms.same_value('Python; Go','Python, Go','combobox',f.label,multiple=True)


def test_workday_field_boundary_can_acknowledge_dispatched_bytes_after_clearing_input(page,tmp_path):
    from jobagent.filling.uploads import UploadProof
    pdf=tmp_path/'resume.pdf';pdf.write_bytes(b'%PDF-1.4 source-bound resume fixture')
    page.set_content('''<div data-automation-id=formField-resume><label>Resume</label>
      <div><input type=file aria-label=Resume onchange="ack.textContent=this.files[0].name;this.value=''">
      <button type=button>Select files</button></div><div id=ack></div></div>''')
    view=forms.read_page(page);f=view.fields[0];proof=UploadProof(pdf,forms.locate(view,f))
    try:
        forms.fill_field(page,view,f,'resume',{'resume':str(pdf)},before_upload=proof.bind,upload_confirmation=proof.verify)
        assert page.locator('input').evaluate('e=>e.files.length')==0
        assert proof.verify() and proof.method=='dispatched bytes and widget acknowledgement'
        assert proof.widget.get_attribute('data-automation-id')=='formField-resume'
    finally:proof.close()


def test_cleared_upload_accepts_acknowledgement_with_hidden_live_announcements(page,tmp_path):
    from jobagent.filling.uploads import UploadProof
    pdf=tmp_path/'resume.pdf';pdf.write_bytes(b'%PDF-1.4 source-bound resume fixture')
    page.set_content('''<div data-automation-id=formField-resume><label>Resume</label>
      <input type=file onchange="ack.textContent=this.files[0].name;this.value=''">
      <div id=live hidden style="display:none"><div role=alert>resume.pdf successfully uploaded</div></div>
      <div id=ack></div></div>''')
    native=page.locator('input');proof=UploadProof(pdf,native)
    try:
        proof.bind(native);native.set_input_files(str(pdf))
        assert native.evaluate('e=>e.files.length')==0
        assert proof.verify() and proof.method=='dispatched bytes and widget acknowledgement'
        # A real, visible error still invalidates those exact dispatched bytes.
        page.locator('[role=alert]').evaluate("e=>e.textContent='Upload rejected'")
        page.locator('#live').evaluate("e=>{e.hidden=false;e.style.display='block'}")
        assert not proof.verify()
    finally:proof.close()


def test_resumeatlas_sample_and_personal_report_must_have_different_owners(page):
    from jobagent.tools.ats_providers import resumeatlas_result
    sample = '''<div class=ref-dash><div class=ref-dash-verdict-pct>56%</div>
      <div><div><h3>Missing from resume</h3></div><ul><li>Sample missing</li></ul></div>
      <div><div><h3>Needs stronger evidence</h3></div><ul><li>Sample weak</li></ul></div>
      <div><div><h3>Already demonstrated</h3></div><ul><li>Sample shown</li></ul></div></div>'''
    page.set_content('<section aria-label="Sample analysis report">'+sample+'</section>')
    with pytest.raises(RuntimeError, match='sample previews are excluded'):
        resumeatlas_result(page)
    # The same classes remain on screen alongside the completed personal pane.
    page.locator('body').evaluate('(e,html)=>e.insertAdjacentHTML("beforeend",html)',
        '<section aria-label="Your analysis report">'+sample.replace('56%', '81%').replace('Sample ', 'Actual ')+'</section>')
    result = resumeatlas_result(page)
    assert result['score'] == 81 and result['missing'] == ['Actual missing', 'Actual weak']
    assert result['breakdown']['matched'] == ['Actual shown']
    assert 'Sample ' not in result['breakdown']['report']


def test_one_school_portal_instruction_is_read_before_any_extra_row_is_created(page):
    from jobagent.filling.adapter import _portal_education_facts
    from jobagent.filling.facts import Fact
    from jobagent.filling.planned import PlannedFiller
    page.set_content('''<h1>Application</h1><section><div class=education--header><p>Education</p></div>
      <p>Please ensure you add only your most recent college/university attended.</p>
      <label for=school--0>School</label><input id=school--0 required>
      <button type=button onclick="window.extra=true">Add another</button></section>''')
    facts=[Fact('old',['School'],'Older University','applicant/education/0/school',section='Education 1'),
           Fact('old-date',['End Date'],'2023','applicant/education/0/to',section='Education 1'),
           Fact('new',['School'],'Latest University','applicant/education/1/school',section='Education 2'),
           Fact('new-date',['End Date'],'2026','applicant/education/1/to',section='Education 2')]
    filler=PlannedFiller(facts,lambda _:pytest.fail('Known school must not require assistance'))
    filler.prepare_sources=lambda _: _portal_education_facts(facts,filler._live.view)
    result=filler.fill(page,advance_pages=False)
    assert result['state']=='page_complete' and page.locator('input').input_value()=='Latest University'
    assert not page.evaluate('!!window.extra')
    assert result['records'][0]['source']=='applicant/education/1/school'


def test_delayed_school_row_is_not_added_repeatedly_before_it_renders(page):
    from jobagent.filling.adapter import _portal_education_facts
    from jobagent.filling.facts import Fact
    from jobagent.filling.planned import PlannedFiller
    page.set_content('''<h1>Application</h1><section><h2>Education</h2>
      <p>Please ensure you add only your most recent college/university attended.</p>
      <div id=rows></div><button type=button onclick="window.addClicks++;
        setTimeout(()=>{const i=window.rowsAdded++;rows.insertAdjacentHTML('beforeend',
        `<label for=school--${i}>School</label><input id=school--${i} required>`)},900)">Add</button>
      </section><script>window.addClicks=0;window.rowsAdded=0;</script>''')
    facts=[Fact('old',['School'],'Older University','applicant/education/0/school',section='Education 1'),
           Fact('old-date',['End Date'],'2023','applicant/education/0/to',section='Education 1'),
           Fact('new',['School'],'Latest University','applicant/education/1/school',section='Education 2'),
           Fact('new-date',['End Date'],'2026','applicant/education/1/to',section='Education 2')]
    filler=PlannedFiller(facts,lambda _:pytest.fail('Known school must not require assistance'))
    filler.prepare_sources=lambda _: _portal_education_facts(facts,filler._live.view)
    result=filler.fill(page,advance_pages=False)
    page.wait_for_timeout(1000)  # expose any extra clicks whose delayed rows are still queued
    assert page.evaluate('addClicks') == 1
    assert page.locator('input').count() == 1
    assert result['state']=='page_complete' and page.locator('input').input_value()=='Latest University'


def test_an_add_control_without_a_new_row_blocks_after_one_click(page,monkeypatch):
    from jobagent.filling.facts import Fact
    from jobagent.filling.planned import PlannedFiller
    page.set_content('''<h1>Application</h1><section><h2>Education</h2>
      <button type=button onclick="window.clicks=(window.clicks||0)+1">Add</button></section>''')
    monkeypatch.setattr(PlannedFiller,'ROW_ADD_TIMEOUT_S',0.25)
    filler=PlannedFiller([Fact('school',['School'],'University','applicant/education',section='Education 1')],
        lambda _:pytest.fail('A failed Add is a technical blocker, not a missing owner fact'))
    result=filler.fill(page,advance_pages=False)
    assert page.evaluate('clicks')==1 and result['state']=='blocked'
    assert 'stopped before clicking again' in result['blockers'][0]


@pytest.mark.parametrize('unrelated', [False, True])
def test_hidden_date_parts_are_covered_only_by_their_own_composite_question(page,unrelated):
    from jobagent.filling.facts import Fact
    from jobagent.filling.planned import PlannedFiller
    extra='<input aria-label="Unrelated question" required style="width:0;height:0;border:0;padding:0">' if unrelated else ''
    page.set_content('''<h1>Application</h1><h2>Work Experience 1</h2>
      <div data-automation-id=formField-start><label>From*</label>
      <div data-automation-id=dateInputWrapper id=start>
        <span data-automation-id=dateSectionMonth-display>01</span>
        <input data-automation-id=dateSectionMonth-input aria-label=Month required value=01 style="width:0;height:0;border:0;padding:0">
        <span data-automation-id=dateSectionYear-display>2026</span>
        <input data-automation-id=dateSectionYear-input aria-label=Year required value=2026 style="width:0;height:0;border:0;padding:0">
        EXTRA</div></div>'''.replace('EXTRA',extra))
    filler=PlannedFiller([Fact('start',['From* (MM/YYYY)','From','Start date'],'January 2026','applicant/work/0/from',section='Work Experience 1')],
        lambda _:pytest.fail('Known date parts are one supported question, not extra owner questions'))
    result=filler.fill(page,advance_pages=False)
    if unrelated:
        assert result['state']=='blocked' and result['blockers']==['unresolved potential field: Unrelated question']
    else:
        assert result['state']=='page_complete'
        assert any(r.get('outcome')=='verified' and r.get('kind')=='date_parts' for r in result['records'])


def test_a_required_year_makes_its_composite_required_even_if_the_month_is_optional(page):
    page.set_content('''<div><label>From</label><div data-automation-id=dateInputWrapper>
      <span data-automation-id=dateSectionMonth-display>MM</span>
      <input data-automation-id=dateSectionMonth-input aria-label=Month>
      <span data-automation-id=dateSectionYear-display>YYYY</span>
      <input data-automation-id=dateSectionYear-input aria-label=Year required></div></div>''')
    fields=forms.read_page(page).fields
    assert len(fields)==1 and fields[0].kind=='date_parts' and fields[0].required


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
