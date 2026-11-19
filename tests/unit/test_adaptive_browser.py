"""Real DOM mutations and workflow routing for the opt-in adaptive strategy."""
from types import SimpleNamespace
from dataclasses import replace
from jobagent.filling.adaptive import AdaptiveFiller
from jobagent.filling.adaptive_view import AdaptiveView
from jobagent.filling.facts import Fact
from jobagent.tools import forms
from jobagent.steps import apply as A
from test_filling_strategy import world, SourceModel, CONTACT
import pytest


@pytest.mark.parametrize('reply',[None,'Asian: South Asian'])
def test_unavailable_broad_race_asks_for_an_offered_choice_once_instead_of_repair_loop(world,tmp_path,reply):
    world.setup('''<label for=e>Email</label><input id=e required>
      <label for=r>Race</label><input id=r role=combobox required
       onclick="document.getElementById('menu').hidden=false">
      <div id=menu role=listbox hidden>
       <div role=option onmousedown="event.preventDefault()"
        onclick="document.getElementById('r').value=this.textContent;this.parentElement.hidden=true">Asian: East Asian</div>
       <div role=option onmousedown="event.preventDefault()"
        onclick="document.getElementById('r').value=this.textContent;this.parentElement.hidden=true">Asian: South Asian</div>
      </div>''')
    world.page.goto('https://fixture.test/apply');questions=[]
    def ask(items):
        questions.append(items)
        return {str(f['id']):reply for f in items}
    filler=AdaptiveFiller([Fact('email',['Email'],'one@example.test','owner'),
                           Fact('race',['Race'],'Asian','owner')],ask,checkpoint_path=tmp_path/'saved.json')
    result=filler.fill(world.page,advance_pages=False,max_rounds=8)
    assert len(questions)==1 and len(questions[0])==1
    assert questions[0][0]['options']==['Asian: East Asian','Asian: South Asian']
    assert result['state']==('page_complete' if reply else 'blocked'),result['blockers']
    assert not any('round budget' in reason for reason in result['blockers'])
    if reply:assert world.page.locator('#r').input_value()==reply
    else:assert not any(r.get('label')=='Race' and r.get('outcome')=='verified' for r in result['records'])
    assert world.page.locator('#e').input_value()=='one@example.test'
    assert len([r for r in result['records'] if r.get('outcome')=='failed'])<=2


def test_adaptive_uses_existing_workflow_without_any_submission(world):
    world.setup(CONTACT)
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='adaptive',log=lambda _:None)
    assert result['ready_to_submit'],result.get('reason')
    assert result['verified']==3 and result['filling_strategy']=='adaptive'
    assert not world.page.evaluate('!!window.sent')
    assert (world.job/'application/adaptive-v1.json').exists()


def test_a_refused_stale_dispatch_replans_even_without_another_successful_field(world):
    world.setup('<label for=x>Favourite colour</label><select id=x required><option value="">Choose</option><option>Red</option></select>')
    world.page.goto('https://fixture.test/apply');questions=[]
    class MutatesBeforeDispatch(AdaptiveFiller):
        def plan(self,*args,**kwargs):
            result=super().plan(*args,**kwargs)
            if not getattr(self,'mutated',False):
                self.mutated=True
                world.page.evaluate("document.getElementById('x').options[1].textContent='Blue'")
            return result
    def ask(items):
        questions.append(items)
        return {str(f['id']):'Blue' for f in items}
    filler=MutatesBeforeDispatch([Fact('colour',['Favourite colour'],'Red','owner')],ask)
    result=filler.fill(world.page,advance_pages=False,max_rounds=5)
    assert result['state']=='page_complete',result['blockers']
    assert len(questions)==1 and world.page.locator('#x').input_value()=='Blue'
    assert not any(r.get('answer')=='Red' and r.get('outcome')=='verified' for r in result['records'])


def test_accepted_other_school_is_not_refilled_when_its_placeholder_disappears(world):
    world.setup('''<label for=s>School</label><input id=s role=combobox required aria-describedby=hint
       onclick="document.getElementById('menu').hidden=false">
      <span id=hint>Select...</span><div id=menu role=listbox hidden>
       <div role=option onmousedown="event.preventDefault()" onclick="document.getElementById('s').value='Other';
        document.getElementById('hint').remove();this.parentElement.hidden=true">Other</div></div>
      <label for=c>Favourite colour</label><input id=c required>''')
    world.page.goto('https://fixture.test/apply');questions=[]
    def ask(items):questions.append(items);return {}
    filler=AdaptiveFiller([Fact('school',['School'],'Unlisted University','owner',variants=['Other'])],ask)
    result=filler.fill(world.page,advance_pages=False,max_rounds=6)
    assert result['state']=='blocked' and not any('round budget' in b for b in result['blockers'])
    assert len(questions)==1 and questions[0][0]['label']=='Favourite colour'
    assert world.page.locator('#s').input_value()=='Other'
    assert len([r for r in result['records'] if r.get('label')=='School' and r.get('outcome')=='verified'])==1
    assert not any(r.get('label')=='School' and r.get('outcome')=='failed' for r in result['records'])


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


def test_repair_stops_after_two_failed_attempts_and_keeps_private_diagnostic(world,tmp_path):
    world.setup('<label for=x>Email</label><input id=x required oninput="this.value=\'\'">')
    world.page.goto('https://fixture.test/apply')
    filler=AdaptiveFiller([Fact('email',['Email'],'one@example.test','owner')],lambda _: {},
                           checkpoint_path=tmp_path/'saved.json')
    result=filler.fill(world.page,advance_pages=False,max_rounds=8)
    assert result['state']=='blocked'
    assert len([r for r in result['records'] if r.get('outcome')=='failed'])==2
    assert 'repair budget exhausted' in result['blockers'][0]
    assert (tmp_path/'saved.png').stat().st_mode & 0o777 == 0o600


def test_schema_watcher_observes_recovered_closed_shadow_roots(world):
    world.setup('<div id=host></div>')
    world.page.goto('https://fixture.test/apply')
    world.page.evaluate('''() => {
      const root=document.getElementById('host').attachShadow({mode:'closed'});
      root.innerHTML='<label for=x>Email</label><input id=x aria-describedby=help><p id=help>Old help</p>';
      window.testRoot=root;
    }''')
    live=AdaptiveView(world.page,forms.read_page);live.capture()
    assert not live.changed()
    world.page.evaluate("window.testRoot.getElementById('help').textContent='New help'")
    assert live.changed()
    assert 'New help' in live.capture().fields[0].help


def test_unrelated_clock_text_does_not_force_a_full_form_read(world):
    world.setup('<label for=x>Email</label><input id=x><span id=clock>1</span>')
    world.page.goto('https://fixture.test/apply')
    live=AdaptiveView(world.page,forms.read_page);live.capture()
    world.page.evaluate("document.getElementById('clock').firstChild.textContent='2'")
    assert not live.changed()


def test_profile_file_change_reloads_evidence_and_updates_only_the_changed_answer(world):
    from jobagent.filling.adapter import fill_page
    from jobagent.config import get_settings
    world.setup(CONTACT);world.page.goto('https://fixture.test/apply')
    source=get_settings().data_dir/'applicant.yaml';source.write_text('revision: one')
    context=A.context(world.job,'fixture');context['_filling_strategy']='adaptive'
    out=world.job/'application';out.mkdir()
    env={'out_dir':out,'company':'fixture'}
    fill_page(world.page,context,{},SourceModel(),lambda _:None,[],1,env)
    before=context['_experimental_filler'].questions
    world.data['Contact'][1]['answer']='new@example.test';source.write_text('revision: two')
    fill_page(world.page,context,{},SourceModel(),lambda _:None,[],1,env)
    assert world.page.locator('#e').input_value()=='new@example.test'
    assert world.page.locator('#f').input_value()=='Casey'
    assert context['_experimental_filler'].questions==before


def test_contacts_csv_change_refreshes_the_referral_and_date_context(world,monkeypatch):
    from jobagent.filling.adapter import fill_page
    from jobagent.config import get_settings
    world.setup(CONTACT);world.page.goto('https://fixture.test/apply')
    path=get_settings().data_dir/'asu_contacts.csv';path.write_text('First contact')
    original=A.context
    def context(*args):
        fresh=original(*args)
        fresh.update(REFERRAL={'name':path.read_text(),'email':'employee@example.test'},
                     TODAY='2026-11-19',DATE_FACTS='Dates for '+path.read_text())
        return fresh
    monkeypatch.setattr(A,'context',context)
    ctx=context(world.job,'fixture');ctx['_filling_strategy']='adaptive'
    out=world.job/'application';out.mkdir();env={'out_dir':out,'company':'fixture'}
    fill_page(world.page,ctx,{},SourceModel(),lambda _:None,[],1,env)
    path.write_text('New contact')
    fill_page(world.page,ctx,{},SourceModel(),lambda _:None,[],1,env)
    filler=ctx['_experimental_filler']
    assert filler.referral_context['contact']['name']=='New contact'
    assert filler.narrative_sources['date_facts']=='Dates for New contact'
    assert filler.questions==0 and world.page.locator('#e').input_value()=='casey@example.test'
