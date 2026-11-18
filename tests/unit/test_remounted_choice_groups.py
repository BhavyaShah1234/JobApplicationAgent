"""Conditional renders may replace every radio group's generated IDs."""
import pytest
from jobagent.steps import apply as A
from jobagent.tools import forms
from test_filling_strategy import world

HTML='''<h1>Application</h1><h2>Eligibility</h2><div id=groups></div><script>
const values={};let generation=0;
function render(){generation++;
 for(const question of ['Authorization','Residence history',...(values.Authorization==='Yes'?['Petition history']:[])]){
  let fieldset=[...groups.children].find(e=>e.dataset.question===question);
  if(!fieldset){fieldset=document.createElement('fieldset');fieldset.dataset.question=question;
   const legend=document.createElement('legend');legend.textContent=question;fieldset.append(legend);
   for(const answer of ['Yes','No']){const label=document.createElement('label'),input=document.createElement('input');
    input.type='radio';input.required=true;input.value=answer;input.onchange=()=>{values[question]=answer;render()};label.append(input,answer);fieldset.append(label)}
   groups.append(fieldset)}
  for(const input of fieldset.querySelectorAll('input')){
   input.name=question+'-'+generation;input.id='choice-'+generation+'-'+question+'-'+input.value;input.checked=values[question]===input.value}
 }}render();</script>'''



def setup(world):
    world.setup(HTML);world.page.goto('https://fixture.test/apply')


def test_unique_group_keeps_planning_identity_but_not_a_stale_locator(world):
    setup(world);before=forms.read_page(world.page);old=before.fields[0]
    forms.fill_field(world.page,before,old,'Yes',{})
    world.page.locator('#groups').evaluate('e=>e.replaceWith(e.cloneNode(true))')
    after=forms.read_page(world.page)
    assert len(after.fields)==3
    assert A._key(old)==A._key(after.fields[0])
    assert old.locator_ref!=after.fields[0].locator_ref
    with pytest.raises(forms.FillError,match='captured control'):
        forms.fill_field(world.page,before,old,'No',{})


def test_native_fill_does_not_replan_questions_just_because_radio_ids_changed(world,monkeypatch):
    setup(world);batches=[]
    def plan(fields,*args,**kwargs):
        batches.append([f.label for f in fields])
        return {f.id:A.Answer(id=f.id,value='Yes' if f.label=='Authorization' else 'No',source='applicant') for f in fields},[]
    monkeypatch.setattr(A,'plan',plan);monkeypatch.setattr(A,'check',lambda *a:None)
    monkeypatch.setattr(A,'enforce',lambda *a:None);monkeypatch.setattr(A,'consult_learned',lambda *a:None)
    records=[]
    view,_=A.fill_page(world.page,{'_applicant':{},'_jd':{}},{},object(),lambda _:None,records,1,{})
    assert batches==[['Authorization','Residence history'],['Petition history']]
    assert len(records)==3 and all(e['verified'] for e in records),records
    assert [(f.label,f.value) for f in view.fields]==[('Authorization','Yes'),('Residence history','No'),('Petition history','No')]


@pytest.mark.parametrize('change', ['options','section','duplicate'])
def test_changed_meaning_or_ambiguous_groups_do_not_reuse_a_planning_identity(world,change):
    setup(world);old=forms.read_page(world.page).fields[0]
    if change=='options':world.page.locator('fieldset').first.locator('label').last.evaluate("e=>e.lastChild.textContent='Not applicable'")
    elif change=='section':world.page.locator('h2').evaluate("e=>e.textContent='Another section'")
    else:world.page.locator('#groups').evaluate("e=>e.append(e.firstElementChild.cloneNode(true))")
    fresh=forms.read_page(world.page).fields[0]
    assert A._key(old)!=A._key(fresh)
