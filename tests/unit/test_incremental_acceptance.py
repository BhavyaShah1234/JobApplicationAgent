"""Incremental verification must agree with full extraction and portal acceptance."""
import pytest
from jobagent.filling.facts import Fact
from jobagent.filling.planned import PlannedFiller
from test_filling_strategy import world


def no_questions(_):
    pytest.fail('The exact owner source and offered value are present')


def test_incremental_aria_radio_readback_uses_its_referenced_caption(world):
    world.setup('''<div role=radiogroup aria-label="Permanent authorization" aria-required=true>
      <span role=radio aria-checked=false aria-labelledby=yes onclick="this.setAttribute('aria-checked','true')"></span><label id=yes>Yes</label>
      <span role=radio aria-checked=false aria-labelledby=no onclick="this.setAttribute('aria-checked','true')"></span><label id=no>No</label></div>
      <style>[role=radio]{display:inline-block;width:20px;height:20px;background:#eee}</style>''')
    world.page.goto('https://fixture.test/apply')
    result=PlannedFiller([Fact('owner.permanent',['Permanent authorization'],'No','applicant/status')],no_questions).fill(
        world.page,advance_pages=False,max_rounds=3)
    assert result['state']=='page_complete',result['blockers']
    record=next(r for r in result['records'] if r.get('outcome')=='verified')
    assert (record['readback'],record['fact_id'])==('No','owner.permanent')


@pytest.mark.parametrize('accepted',[True,False])
def test_picker_must_blur_and_wait_for_its_own_validation_to_accept(world,accepted):
    world.setup('''<div class=form-field><label for=school>School</label>
      <input id=school role=combobox aria-invalid=true aria-required=true
       onclick="document.getElementById('menu').hidden=false"
       onblur="if(window.accepted)setTimeout(()=>{this.setAttribute('aria-invalid','false');document.getElementById('err').remove()},150)">
      <div id=menu role=listbox hidden><div role=option onmousedown="event.preventDefault()"
       onclick="document.getElementById('school').value='Example University';document.getElementById('menu').hidden=true">Example University</div></div>
      <div id=err role=alert>School is required</div></div>''')
    world.page.goto('https://fixture.test/apply');world.page.evaluate('(a)=>window.accepted=a',accepted)
    result=PlannedFiller([Fact('school',['School'],'Example University','applicant/education')],no_questions).fill(
        world.page,advance_pages=False,max_rounds=3)
    assert world.page.locator('#school').input_value()=='Example University'
    if accepted:
        assert result['state']=='page_complete',result['blockers']
        assert world.page.locator('#school').get_attribute('aria-invalid')=='false'
    else:
        assert result['state']=='blocked'
        assert not any(r.get('outcome')=='verified' for r in result['records'])


def test_dependent_global_education_fields_fill_from_one_degree_without_human_or_model(world):
    from jobagent.filling.facts import compile_facts
    from jobagent.filling.education import selection_context
    world.setup('''<label for=level>What is your highest level of education?</label>
      <select id=level required><option value="">No Selection</option><option>Masters</option><option>Bachelors</option></select>
      <label for=region>Where did you study – Region?</label>
      <select id=region required onchange="document.getElementById('country').disabled=false"><option value="">No Selection</option><option>Americas</option><option>APJ</option></select>
      <label for=country>Where did you study – Country?</label>
      <select id=country required disabled onchange="document.getElementById('school').disabled=false"><option value="">No Selection</option><option>USA</option><option>India</option></select>
      <label for=school>Where did you study – School?</label>
      <select id=school required disabled><option value="">No Selection</option><option>Example University</option><option>Other Institute</option></select>''')
    facts=compile_facts({'education':[{'question':'Degrees','answer':[
        {'school':['Example University - Campus','Example University'],'degree':'Master of Science',
         'country':'United States','region':'Americas'},
        {'school':'Other Institute','degree':'Bachelor of Technology','country':'India','region':'APJ'}]}]},'')
    world.page.goto('https://fixture.test/apply')
    filler=PlannedFiller(facts,no_questions)
    filler.prepare_sources=lambda _:selection_context(facts,filler._live.view)
    result=filler.fill(world.page,advance_pages=False,max_rounds=8)
    assert result['state']=='page_complete',result['blockers']
    assert [world.page.locator('#'+id).input_value() for id in ['level','region','country','school']]==[
        'Masters','Americas','USA','Example University']
    assert result['questions']==0 and result['model_calls']==0
