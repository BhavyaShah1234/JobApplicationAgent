"""Real DOM mutations and workflow routing for the opt-in adaptive strategy."""
from types import SimpleNamespace
from dataclasses import replace
from jobagent.filling.adaptive import AdaptiveFiller
from jobagent.filling.adaptive_view import AdaptiveView
from jobagent.filling.facts import Fact
from jobagent.tools import forms
from jobagent.steps import apply as A
from test_filling_strategy import world, SourceModel, CONTACT


def test_adaptive_uses_existing_workflow_without_any_submission(world):
    world.setup(CONTACT)
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='adaptive',log=lambda _:None)
    assert result['ready_to_submit'],result.get('reason')
    assert result['verified']==3 and result['filling_strategy']=='adaptive'
    assert not world.page.evaluate('!!window.sent')
    assert (world.job/'application/adaptive-v1.json').exists()


def test_native_options_and_help_mutations_invalidate_live_inventory(world):
    world.setup('<label for=x>Support</label><select id=x aria-describedby=help><option>No</option></select><p id=help>Visa sponsorship only</p>')
    world.page.goto('https://fixture.test/apply')
    live=AdaptiveView(world.page,forms.read_page)
    live.capture()
    assert not live.changed()
    world.page.evaluate("document.getElementById('help').firstChild.textContent='Includes OPT documentation'")
    assert live.changed()
    assert 'OPT' in live.capture().fields[0].help
    world.page.evaluate("document.getElementById('x').add(new Option('Yes'))")
    assert live.changed() and 'Yes' in live.capture().fields[0].options


def test_reopened_prefilled_form_is_read_back_instead_of_assumed_verified(world,tmp_path):
    world.setup('<label for=x>Email</label><input id=x type=email required>')
    world.page.goto('https://fixture.test/apply')
    facts=[Fact('email',['Email'],'one@example.test','owner')]
    def filler():return AdaptiveFiller(facts,lambda _: {},checkpoint_path=tmp_path/'saved.json')
    first=filler().fill(world.page,advance_pages=False)
    assert first['state']=='page_complete'
    resumed=filler().fill(world.page,advance_pages=False)
    assert resumed['state']=='page_complete'
    assert any(r.get('outcome')=='verified' for r in resumed['records'])
    assert resumed['adaptive']['restored_decisions']==1


def test_changed_choices_are_replanned_before_dispatch(world,tmp_path):
    world.setup('''<label for=a>Email</label><input id=a onchange="document.getElementById('x').options[1].text='Blue';document.getElementById('x').options[1].value='Blue'">
      <label for=x>Favourite colour</label><select id=x required><option value="">Choose</option><option>Red</option></select>''')
    world.page.goto('https://fixture.test/apply')
    questions=[]
    def ask(items):
        questions.append(items)
        return {str(f['id']):'Blue' for f in items if 'Blue' in f['options']}
    filler=AdaptiveFiller([Fact('email',['Email'],'one@example.test','owner'),
                           Fact('colour',['Favourite colour'],'Red','owner')],ask,checkpoint_path=tmp_path/'checkpoint.json')
    result=filler.fill(world.page,advance_pages=False,max_rounds=6)
    assert result['state']=='page_complete',result['blockers']
    assert world.page.locator('#x').input_value()=='Blue'
    assert not any(r.get('answer')=='Red' and r.get('outcome')=='verified' for r in result['records'])
