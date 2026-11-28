"""An SPA review that reuses row IDs must not reuse a different employer's city."""
import json
import pytest

from jobagent.steps import apply
from jobagent.tools import wizard
from test_filling_strategy import world, SourceModel


@pytest.mark.parametrize('strategy', ['experimental', 'adaptive'])
def test_reordered_employment_pages_preserve_employer_city_pairs(world, monkeypatch, strategy):
    world.data['Experience'] = [{'question': 'Work history', 'answer': [
        {'company': 'First Co', 'title': 'First Title', 'end': 'May 2026'},
        {'company': 'Second Co', 'title': 'Second Title', 'end': 'August 2026'}]}]
    world.setup('''<h1 id=heading>Candidate Profile</h1><div id=rows></div>
      <button type=button id=next>Next</button><script>
      window.stage=1;window.sent=0;
      function render(){
        const jobs=stage===1?[['First Co','First Title',''],['Second Co','Second Title','']]:
                             [['Second Co','Second Title','Phoenix'],['First Co','First Title','Tempe']];
        document.getElementById('rows').innerHTML=jobs.map((job,i)=>'<section><h2>Work Experience '+(i+1)+
          '</h2><label>Name of Employer<input id="company'+i+'" value="'+job[0]+'"></label>'+
          '<label>Your Job Title<input id="title'+i+'" value="'+job[1]+'"></label>'+
          '<label>City<input required id="city'+i+'" value="'+job[2]+'"></label></section>').join('');
      }
      render();document.getElementById('next').onclick=()=>{
        stage=2;document.getElementById('heading').textContent='Application Review';render();
        const b=document.getElementById('next');b.textContent='Submit application';b.onclick=()=>window.sent++;
      };</script>''')
    wait=wizard.wait_for_progress
    monkeypatch.setattr(wizard, 'wait_for_progress', lambda page, before:
        wait(page, before, attempts=1, wait_ms=30, settle_ms=30))

    class Hitl:
        calls=[]
        def ask(self, prompt, **kwargs):
            questions=json.loads(prompt.split('\n',1)[1]);self.calls.append(questions)
            second=world.page.evaluate('window.stage')==2
            values={'Work Experience 1':'Phoenix' if second else 'Tempe',
                    'Work Experience 2':'Tempe' if second else 'Phoenix'}
            assert all(q['label']=='City' for q in questions)
            return json.dumps({str(q['id']):values[q['section']] for q in questions})

    helper=Hitl()
    result=apply.run(world.page, world.job, 'fixture', model=SourceModel(), hitl=helper,
                     filling_strategy=strategy, log=lambda _:None)
    assert result['state']=='review', result.get('blockers')
    assert world.page.locator('#company0').input_value()=='Second Co'
    assert world.page.locator('#city0').input_value()=='Phoenix'
    assert world.page.locator('#company1').input_value()=='First Co'
    assert world.page.locator('#city1').input_value()=='Tempe'
    assert world.page.evaluate('window.sent')==0
