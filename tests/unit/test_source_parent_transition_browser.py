"""The live source selection changes after planning, before its Other child is filled."""
import pytest
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.adaptive import AdaptiveFiller
from test_filling_strategy import world


@pytest.mark.parametrize('engine',[PlannedFiller,AdaptiveFiller])
def test_native_source_change_clears_only_the_inactive_other_field(world,engine):
    world.setup('''<h1>Application</h1><form onsubmit="window.sent=true;return false">
        <label for=source>How did you hear about us?</label><select id=source required>
        <option>Other</option><option>Employee Referral</option></select>
        <label for=other>If Other, please specify</label><input id=other value="old Other explanation">
        <button type=submit>Submit Application</button></form><script>window.sent=false</script>''')
    world.page.goto('https://fixture.test/apply')
    agent=engine([],lambda *_:pytest.fail('No owner information is missing'),
                 referral_context={'company':'Example','contact':{}})
    result=agent.fill(world.page,advance_pages=False,max_rounds=6)
    assert result['state']=='page_complete',result['blockers']
    assert world.page.locator('#source').input_value()=='Employee Referral'
    assert world.page.locator('#other').input_value()==''
    assert not world.page.evaluate('window.sent')
    assert result['questions']==result['model_calls']==0


def test_original_opt_in_filler_rechecks_the_live_other_dependency(world,monkeypatch):
    from jobagent.steps import apply as A
    world.setup('''<h1>Application</h1><form onsubmit="window.sent=true;return false">
        <label for=source>How did you hear about us?</label><select id=source required>
        <option>Other</option><option>Employee Referral</option></select>
        <label for=other>If Other, please specify</label><input id=other value="old Other explanation">
        <button type=submit>Submit Application</button></form><script>window.sent=false</script>''')
    world.page.goto('https://fixture.test/apply')
    def plan(fields,*args,**kwargs):
        return {f.id:A.Answer(id=f.id,value='Employee Referral' if f.label=='How did you hear about us?' else 'old model prose',source='applicant') for f in fields},[]
    monkeypatch.setattr(A,'plan',plan)
    monkeypatch.setattr(A,'check',lambda *a,**k:None)
    monkeypatch.setattr(A,'consult_learned',lambda *a,**k:None)
    records=[]
    A.fill_page(world.page,{'_applicant':{},'_jd':{'company':'Example'},'REFERRAL':{'name':'Example Employee','email':'employee@example.test'}},{},object(),lambda _:None,records,1,{})
    assert world.page.locator('#source').input_value()=='Employee Referral'
    assert world.page.locator('#other').input_value()==''
    assert not world.page.evaluate('window.sent')
    other=[r for r in records if r['label']=='If Other, please specify']
    assert len(other)==1 and other[0]['verified'] and other[0]['clear']
