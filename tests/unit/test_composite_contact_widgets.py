"""Regression markup observed in live Select2 and intl-tel-input contact forms."""
import pytest

from jobagent.tools import forms
from jobagent.filling.facts import compile_facts
from jobagent.filling.live_view import LiveView
from jobagent.filling.planned import PlannedFiller, read_view
from test_filling_strategy import world

HTML = '''<style>.select2-hidden-accessible{position:absolute;width:1px;height:1px;clip:rect(0,0,0,0)}
.select2-selection{display:inline-block;width:220px;height:32px}.iti__selected-flag{display:inline-block;width:80px;height:32px}.required::after{content:'*'}</style>
<h1>Application</h1><h2>Contact information</h2>
<label id=email-label>Email address</label><input aria-labelledby=email-label id=email type=text readonly required value=casey@example.test>
<div class=iti><div class=iti__selected-flag role=combobox tabindex=0 aria-label="Country for phone number" title="United States: +1"></div></div>
<div class=form-group><label id=country-label class=required>Country/Region</label>
<select id=country class=select2-hidden-accessible aria-hidden=true tabindex=-1><option value=US>United States</option><option value=CA>Canada</option></select>
<span class=select2-container><span id=country-combo class="select2-selection select2-selection--single" role=combobox tabindex=0 aria-required=true aria-invalid=true aria-labelledby="country-label country-rendered country-error">
<span id=country-rendered role=textbox aria-readonly=true class=select2-selection__rendered title="United States"><span class=select2-selection__placeholder>Select a country/region</span></span></span></span>
<div id=country-error role=alert hidden>Select a country</div></div>
<div class=form-group><label id=state-label>Province/State</label>
<select id=state disabled class=select2-hidden-accessible aria-hidden=true tabindex=-1></select>
<span id=state-wrapper class="select2-container select2-container--disabled"><span id=state-combo class="select2-selection select2-selection--single" role=combobox tabindex=-1 aria-required=true aria-labelledby="state-label state-rendered">
<span id=state-rendered role=textbox aria-readonly=true class=select2-selection__rendered><span class=select2-selection__placeholder>No states/provinces available</span></span></span></span></div>
<div id=options hidden role=listbox></div><script>
window.selectionWrites=0;window.emailWrites=0;window.phoneWrites=0;
email.addEventListener('input',()=>emailWrites++);
function openMenu(combo, entries, chosen){options.innerHTML='';options.hidden=false;combo.setAttribute('aria-expanded','true');
 for(const [value,text] of entries){const li=document.createElement('div');li.setAttribute('role','option');li.textContent=text;
 li.onclick=()=>{chosen(value,text);selectionWrites++;combo.setAttribute('aria-expanded','false');options.hidden=true};options.append(li)}}
const countryCombo=document.getElementById('country-combo'),stateCombo=document.getElementById('state-combo');
countryCombo.removeAttribute('id');stateCombo.removeAttribute('id');
countryCombo.onclick=()=>openMenu(countryCombo,[['US','United States'],['CA','Canada']],(value,text)=>{
 country.value=value;country.dispatchEvent(new Event('change',{bubbles:true}));document.getElementById('country-rendered').textContent=text;
 countryCombo.setAttribute('aria-invalid','false');state.disabled=false;document.getElementById('state-wrapper').classList.remove('select2-container--disabled');
 state.innerHTML=(value==='CA'?'<option value=ON>Ontario</option>':'<option value=TX>Texas</option><option value=AZ>Arizona</option>');
 document.getElementById('state-rendered').innerHTML='<span class=select2-selection__placeholder>Select a province/state</span>'});
stateCombo.onclick=()=>openMenu(stateCombo,[...state.options].map(o=>[o.value,o.text]),(value,text)=>{
 state.value=value;state.dispatchEvent(new Event('change',{bubbles:true}));document.getElementById('state-rendered').textContent=text;stateCombo.setAttribute('aria-invalid','false');stateCombo.setAttribute('aria-labelledby','state-rendered')});
const phone=document.querySelector('.iti__selected-flag');
phone.onclick=()=>openMenu(phone,[['US','United States (+1)'],['CA','Canada (+1)']],(_,text)=>{phone.title=text;phoneWrites++});
document.addEventListener('keydown',e=>{if(e.key==='Escape')options.hidden=true});
</script>'''


def setup(world):
    world.setup(HTML)
    world.page.goto('https://fixture.test/apply')


def no_questions(questions):
    pytest.fail('All contact answers have independent supplied sources: '+repr(questions))


def facts(country='United States',state='Texas'):
    return compile_facts({'contact':[{'question':'Email','answer':'casey@example.test'},
        {'question':'Phone','answer':{'country':'United States','country_code':'+1','number':'2025550100'}}],
        'address':[{'question':'Current address','answer':{'country':country,'state':state}}]},'')


def test_select2_has_one_question_not_a_second_textbox_or_a_disabled_child(world):
    setup(world)
    view=forms.read_page(world.page)
    assert [(f.kind,f.label) for f in view.fields]==[
        ('text','Email address'),('combobox','Country for phone number'),('combobox','Country/Region')]
    country=view.fields[-1]
    assert country.required and country.value=='' and country.invalid
    assert view.fields[1].value=='United States (+1)'


@pytest.mark.parametrize('native_label_for', [False, True])
def test_select2_selected_values_and_labels_agree_in_snapshot_and_targeted_readback(world,native_label_for):
    setup(world)
    if native_label_for:world.page.locator('#state-label').evaluate("e=>e.htmlFor='state'")
    live=LiveView(world.page,read_view);view=live.capture()
    country=next(f for f in view.fields if f.label=='Country/Region')
    forms.fill_field(world.page,view,country,'United States',{},strict=True)
    snapshot=forms.read_page(world.page)
    assert [f.label for f in snapshot.fields][-2:]==['Country/Region','Province/State']
    fresh_country=next(f for f in snapshot.fields if f.label=='Country/Region')
    assert fresh_country.value=='United States' and not fresh_country.invalid
    assert live.refresh(country).value==fresh_country.value
    state=next(f for f in snapshot.fields if f.label=='Province/State')
    assert state.value=='' and state.required
    forms.fill_field(world.page,snapshot,state,'Texas',{},strict=True)
    assert forms.read_page(world.page).fields[-1].label=='Province/State'
    assert live.refresh(state).value=='Texas'


@pytest.mark.parametrize('country,state,country_code,state_code',[
    ('United States','Texas','US','TX'),('Canada','Ontario','CA','ON'),
])
def test_contact_filler_finishes_conditional_widgets_without_model_or_hitl(world,country,state,country_code,state_code):
    setup(world);filler=PlannedFiller(facts(country,state),no_questions)
    result=filler.fill(world.page,advance_pages=False)
    assert result['state']=='page_complete',result['blockers']
    assert result['model_calls']==result['questions']==0
    assert world.page.locator('#country').input_value()==country_code
    assert world.page.locator('#state').input_value()==state_code
    assert world.page.locator('#email').input_value()=='casey@example.test'
    assert world.page.evaluate('emailWrites')==world.page.evaluate('phoneWrites')==0
    assert result['records'] and all(r['outcome']=='verified' for r in result['records'])


@pytest.mark.parametrize('value,invalid,allowed',[
    ('casey@example.test',False,True),('different@example.test',False,False),('casey@example.test',True,False),
])
def test_readonly_email_requires_exact_sourced_value_and_a_valid_control(world,value,invalid,allowed):
    setup(world);world.page.locator('#email').evaluate('(e,x)=>{e.value=x[0];e.setAttribute("aria-invalid",String(x[1]))}',[value,invalid])
    view=forms.read_page(world.page);field=view.fields[0]
    if allowed:assert forms.fill_field(world.page,view,field,'casey@example.test',{})=='casey@example.test'
    else:
        with pytest.raises(forms.FillError,match='read-only'):
            forms.fill_field(world.page,view,field,'casey@example.test',{})
    assert world.page.evaluate('emailWrites')==0


@pytest.mark.parametrize('already_open', [False,True])
def test_select2_menu_is_closed_before_the_next_control_even_when_selection_remounts(world,already_open):
    html='''<style>.select2-selection{display:inline-block;width:200px;height:40px}
    #choices:not([hidden]){position:fixed;inset:0;background:white;z-index:100}</style>
    <h1>Application</h1><label for=a>Linux experience</label>
    <select id=a hidden><option>No</option><option>Yes</option></select>
    <span class=select2-container><span class="select2-selection select2-selection--single" role=combobox tabindex=0 aria-expanded=false>
    <span class=select2-selection__rendered>No</span></span></span>
    <label for=b>Pipeline experience</label><select id=b hidden><option>No</option><option>Yes</option></select>
    <span class=select2-container><span class="select2-selection select2-selection--single" role=combobox tabindex=0 aria-expanded=false>
    <span class=select2-selection__rendered>No</span></span></span><div id=choices hidden role=listbox></div>
    <button onclick="window.sent=true">Submit application</button><script>
    window.sent=false;
    function wire(combo){combo.onclick=()=>{if(combo.getAttribute('aria-expanded')==='true'){closeMenu();return}
      choices.innerHTML='';choices.hidden=false;combo.setAttribute('aria-expanded','true');
      for(const text of ['No','Yes']){let li=document.createElement('div');li.setAttribute('role','option');li.textContent=text;
        li.onclick=()=>{combo.parentElement.previousElementSibling.value=text;
          let replacement=combo.cloneNode(true);replacement.querySelector('.select2-selection__rendered').textContent=text;
          combo.replaceWith(replacement);wire(replacement)};choices.append(li)}}}
    function closeMenu(){choices.hidden=true;document.querySelectorAll('[aria-expanded]').forEach(e=>e.setAttribute('aria-expanded','false'))}
    document.querySelectorAll('[role=combobox]').forEach(wire);
    document.addEventListener('keydown',e=>{if(e.key==='Escape')closeMenu()});</script>'''
    world.setup(html);world.page.goto('https://fixture.test/apply')
    view=forms.read_page(world.page)
    assert len(view.fields)==2
    if already_open:world.page.get_by_role('combobox').first.click()
    for control in view.fields:
        assert forms.fill_field(world.page,view,control,'Yes',{},strict=True)=='Yes'
        assert world.page.locator('#choices').is_hidden()
    assert [world.page.locator('#'+key).input_value() for key in ['a','b']]==['Yes','Yes']
    assert world.page.evaluate('sent') is False
