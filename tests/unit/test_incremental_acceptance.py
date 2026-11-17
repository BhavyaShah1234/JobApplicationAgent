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
