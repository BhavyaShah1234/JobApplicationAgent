import pytest
from jobagent.tools import forms
from jobagent.filling.facts import Fact
from jobagent.filling.planned import PlannedFiller
from test_filling_strategy import world


@pytest.mark.parametrize('label',['Skip & continue','Skip and continue'])
def test_optional_step_can_continue_without_enrolling_or_submitting(world,label):
    world.setup('''<h1>Application</h1><h2>Optional updates</h2><form>
      <button type=submit onclick="event.preventDefault();window.enrolled=true">Enable SMS updates</button></form>
      <button type=button id=skip>SKIP</button><div hidden><form><button type=submit>Submit application</button></form></div>
      <script>window.enrolled=false;window.sent=false;skip.onclick=()=>{document.body.innerHTML=
      '<h2>Application review</h2><label for=name>First Name</label><input id=name required>'+
      '<button type=button onclick="window.sent=true">Submit application</button>'};</script>'''.replace('SKIP',label))
    world.page.goto('https://fixture.test/apply')
    assert not forms.has_submit_input(world.page)
    filler=PlannedFiller([Fact('name',['First Name'],'Casey','applicant/identity')],lambda q:pytest.fail(str(q)))
    result=filler.fill(world.page)
    assert result['state']=='review' and world.page.locator('#name').input_value()=='Casey'
    assert not world.page.evaluate('enrolled') and not world.page.evaluate('sent')


@pytest.mark.parametrize('visible,label,expected',[(False,'Submit application',False),(True,'Submit application',True),
    (True,'Enable SMS updates',False),(True,'',True)])
def test_submit_type_is_not_enough_to_identify_application_review(world,visible,label,expected):
    world.setup('<form'+(' hidden' if not visible else '')+'><input type=submit value="'+label+'"></form>')
    world.page.goto('https://fixture.test/apply')
    assert forms.has_submit_input(world.page) is expected


@pytest.mark.parametrize('label',['Skip this application','Skip & submit','Enable SMS updates'])
def test_unrelated_skip_or_optin_actions_are_not_continuation(label):
    assert not forms.NEXT.search(label)
