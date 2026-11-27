"""Native browser integration for the retained filler's source-policy guard."""
import pytest
from jobagent.steps import apply
from test_filling_strategy import world,SourceModel
from test_legacy_source_policies import context


@pytest.mark.browser
def test_original_native_workflow_enforces_sources_after_the_verifier(world,monkeypatch):
    owner=context()['_applicant']
    owner['education'][0]['answer']=owner['education'][0]['answer'][:1]
    world.data.update(owner)
    get_context=apply.context
    def prepared(*args):
        ctx=get_context(*args)
        ctx['_application_address']={'address':{'line1':'Test Street','city':'Sunnyvale','state':'California',
            'postal_code':'94086','country':'United States'},'state_code':'CA'}
        ctx['ANSWERS_FOR_THIS_APPLICATION']=context()['ANSWERS_FOR_THIS_APPLICATION']
        return ctx
    monkeypatch.setattr(apply,'context',prepared)
    world.setup('''<h1>Application</h1><form onsubmit="window.sent++;return false">
        <label>Location<input id=loc required></label>
        <fieldset><legend>Race</legend>
          <label><input type=radio name=race value=asian>Asian (Not Hispanic or Latino)</label>
          <label><input type=radio name=race value=decline>Decline to self-identify</label></fieldset>
        <h2>Education 1</h2><label>Discipline<select id=major><option>Choose</option>
          <option>Computer Science</option><option>Other</option></select></label>
        <h2>Additional Questions</h2><label>If yes, what versions?<input id=versions></label>
        <button type=submit>Submit application</button></form><script>window.sent=0</script>''')
    wrong={'Location':'Sunnyvale, California, United States','Race':'Asian','Discipline':'Computer Science',
           'If yes, what versions?':'.NET 8'}
    monkeypatch.setattr(apply,'plan',lambda fields,*a,**k:({f.id:apply.Answer(
        id=f.id,value=wrong[f.label],source='applicant') for f in fields},[]))
    def rejected_sheet(fields,answers,*args):
        for f in fields:
            if f.label=='If yes, what versions?':
                answers[f.id]=answers[f.id].model_copy(update={'ask_human':True,'flag':'planned .NET 8'})
    monkeypatch.setattr(apply,'check',rejected_sheet)
    class NoHelp:
        def ask(self,*args,**kwargs):raise AssertionError('Known source facts must not ask again')
    result=apply.run(world.page,world.job,'fixture',model=SourceModel(),hitl=NoHelp(),
                     filling_strategy='original',log=lambda _:None)
    assert result['state']=='review',result.get('blockers')
    assert world.page.locator('#loc').input_value()=='Tempe, Arizona, United States'
    assert world.page.locator('[value=decline]').is_checked()
    assert world.page.locator('#major').input_value()=='Other'
    assert world.page.locator('#versions').input_value()=='.NET 8'
    assert world.page.evaluate('window.sent')==0
