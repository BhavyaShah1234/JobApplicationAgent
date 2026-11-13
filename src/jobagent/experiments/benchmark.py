"""Paired fixture benchmark. Replay is a smoke test, never a real model latency estimate."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import time
from types import SimpleNamespace
from playwright.sync_api import sync_playwright
from .resolution import Fact
from .engine import Candidate


def revision():
    try:return subprocess.check_output(['git','rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError):return os.environ.get('JOBAGENT_EXPERIMENT_COMMIT','unavailable; use source hashes')


def fixtures():
    def case(name,html,facts,expected,heldout=False):
        return dict(name=name,html=html,facts=facts,expected=expected,heldout=heldout)
    def fact(label,value,section=''):return asdict(Fact(label+section,[label],value,'fixture source',section))
    email='<label for=e>Email</label><input id=e type=email required>'
    apply='<button type=button onclick="window.sent=true">Apply</button>'
    cases=[case('text',email,[fact('Email','person@example.test')],{'#e':'person@example.test'}),
      case('travel','<label for=t>Willingness to travel</label><select id=t required><option>Choose</option><option>Travel - 30% or less</option><option>Travel - 50% or more</option></select>',[fact('Willingness to travel','Travel - 30% or less')],{'#t':'Travel - 30% or less'}),
      case('identity',"<input id=s role=combobox aria-label=School oninput=render() onclick=render()><div id=opts></div><script>function render(){let input=document.getElementById('s');let name=input.value.includes('Arizona')?'Arizona State University':'Adams State University';document.getElementById('opts').innerHTML='<div role=option onclick=pick(this)>'+name+'</div>'}function pick(e){document.getElementById('s').value=e.innerText;document.getElementById('opts').innerHTML='';}</script>",[fact('School','Arizona State University')],{'#s':'Arizona State University'}),
      case('date','<label for=d>Start</label><input id=d required pattern="[0-9]{2}/[0-9]{2}/[0-9]{4}" placeholder="MM/DD/YYYY">',[fact('Start','08/01/2024')],{'#d':'08/01/2024'}),
      case('hidden',email+'<label style="display:none">Unrelated branch<input></label>',[fact('Email','person@example.test')],{'#e':'person@example.test'}),
      case('schema',email+'<script type=application/json>{"questions":[{"label":"Email","type":"email","required":true}]}</script>',[fact('Email','person@example.test')],{'#e':'person@example.test'}),
      case('uploads','<label for=r>Resume</label><input type=file id=r required><label for=l>Cover Letter</label><input type=file id=l required>',[],{'file:#r':'resume.pdf','file:#l':'cover_letter.pdf'}),
      case('conditional','<label for=c>Country</label><select id=c onchange="document.getElementById(\'extra\').innerHTML=\'<label for=st>State</label><input id=st required>\'"><option>Choose</option><option>United States</option></select><div id=extra></div>',[fact('Country','United States'),fact('State','Texas')],{'#c':'United States','#st':'Texas'}),
      case('repeating','<section><h2>Education 1</h2><label for=s1>School</label><input id=s1 required></section><section><h2>Education 2</h2><label for=s2>School</label><input id=s2 required></section>',[fact('School','ASU','Education 1'),fact('School','VIT','Education 2')],{'#s1':'ASU','#s2':'VIT'},True),
      case('closed-shadow','<custom-form></custom-form><script>document.querySelector("custom-form").attachShadow({mode:"closed"}).innerHTML=\'<label for=e>Email</label><input id=e type=email required>\'</script>',[fact('Email','person@example.test')],{'closed:#e':'person@example.test'},True),
      case('iframe','<iframe srcdoc="<label for=e>Email</label><input id=e required>"></iframe>',[fact('Email','person@example.test')],{'frame:#e':'person@example.test'},True),
      case('wizard','<div id=step>'+email+'</div><button type=button id=next onclick="document.getElementById(\'step\').innerHTML=\'<label for=n>Name</label><input id=n required>\';this.remove()">Next</button>',[fact('Email','person@example.test'),fact('Name','Example Person')],{'#n':'Example Person'},True)]
    for c in cases:c['html']+=apply
    return cases


class Replay:
    model_name='fixture-replay';calls=0
    def __init__(self,facts):self.facts=facts
    def with_structured_output(self,schema,**kwargs):
        def invoke(messages):
            self.calls+=1;out={}
            for name,field in schema.model_fields.items():
                if name=='add':out[name]=[];continue
                if not name.startswith('field_'):continue
                fid=int(name.split('_')[1])
                if schema.__name__=='ApplicationFieldChecks':out[name]={'id':fid,'supported':True};continue
                label=(field.description or '').split(']: ',1)[-1].split('; kind=',1)[0]
                prompt='\n'.join(str(m.content) for m in messages)
                section=re.search(r'\['+str(fid)+r'\][^\n]*section="([^"]+)"',prompt)
                matches=[x for x in self.facts if label in x.aliases and (not x.section or section and section[1]==x.section)]
                out[name]={'id':fid,'value':matches[0].value if len(matches)==1 else '',
                           'source':'applicant' if len(matches)==1 else 'none','ask_human':len(matches)!=1}
            return schema.model_validate(out)
        return SimpleNamespace(invoke=invoke)


def execute(case,engine,pw,root,real_model):
    from jobagent.tools import forms,shadow,wizard
    from jobagent.steps import apply as A
    from jobagent.config import get_settings
    os.environ.update(DATA_DIR=str(root/'data'),STATE_DIR=str(root/'state'),OUTPUTS_DIR=str(root/'outputs'))
    (root/'data').mkdir(parents=True);get_settings.cache_clear()
    ctx=pw.chromium.launch(channel='chrome',headless=True).new_context();shadow.install(pw,ctx)
    browser=ctx.browser;page=ctx.new_page();page.set_content(case['html']);facts=[Fact(**f) for f in case['facts']]
    model=real_model or Replay(facts)
    def ask(q):return {}  # unanswered source gaps remain gaps, never repaired using the grader
    files={}
    for role in ('resume','cover_letter'):
        path=root/(role+'.pdf');path.write_bytes(('%PDF-1.4\n% Synthetic '+role+' fixture\n%%EOF').encode());files[role]=str(path)
    start=time.monotonic()
    try:
        if engine=='candidate':r=Candidate(facts,ask,real_model).fill(page,files)
        else:
            data={'fixture':[{'question':x.aliases[0],'answer':x.value} for x in facts]}
            import yaml
            ctx_data={'APPLICANT':yaml.safe_dump(data),'RESUME':'','JD':'','_applicant':data,'_jd':{}}
            class Hitl:
                count=0
                def ask(self,*args,**kwargs):self.count+=1;return 'skip'
            h=Hitl();records=[]
            for n in range(1,16):
                view,_=A.fill_page(page,ctx_data,files,model,lambda _:None,records,n,env={'hitl':h,'company':'fixture','host':'fixture'})
                if view.errors or not view.coverage.complete:break
                before=wizard.state_of(page,view)
                if not forms.click_button(page,view,forms.NEXT):break
                verdict,_,_,_=wizard.wait_for_progress(page,before)
                if verdict!='advanced':break
            blockers=A.blockers_of(records)+view.coverage.problems()+view.errors
            r={'engine':'current','ready':not blockers,'records':records,'blockers':blockers,
               'active_s':time.monotonic()-start,'questions':h.count,'model_calls':getattr(model,'calls',None)}
        actual={}
        for selector in case['expected']:
            if selector.startswith('file:'):
                actual[selector]=page.locator(selector[5:]).evaluate('e=>e.files?.[0]?.name')
            elif selector.startswith('closed:'):
                actual[selector]=page.evaluate('(s)=>window.__jobagentAll(s)[0]?.value',selector[7:])
            elif selector.startswith('frame:'):actual[selector]=page.frames[1].locator(selector[6:]).input_value()
            else:actual[selector]=page.locator(selector).input_value()
        r.update(fixture=case['name'],heldout=case['heldout'],graded_correct=actual==case['expected'],
                 submitted=page.evaluate('!!window.sent'),actual=actual)
        return r
    except Exception as e:
        return {'engine':engine,'fixture':case['name'],'ready':False,'graded_correct':False,
                'active_s':time.monotonic()-start,'error':type(e).__name__+': '+str(e)[:200]}
    finally:browser.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--repetitions',type=int,default=3);p.add_argument('--model',action='store_true')
    p.add_argument('--smoke',action='store_true');args=p.parse_args()
    if args.output.exists():raise ValueError('output must be new')
    args.output.mkdir(parents=True,mode=0o700)
    model=None
    if args.model:
        from jobagent import llm
        from jobagent.config import get_settings
        model=llm.writer(model=get_settings().model_planner,timeout=120)
    rows=[];cases=fixtures()[:2] if args.smoke else fixtures()
    import hashlib
    source_hashes={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in Path('src/jobagent').rglob('*.py')}
    (args.output/'source-manifest.json').write_text(json.dumps(source_hashes,indent=2))
    with sync_playwright() as pw:
        for rep in range(args.repetitions):
            for case in cases:
                for engine in (['current','candidate'] if rep%2==0 else ['candidate','current']):
                    r=execute(case,engine,pw,args.output/f'{rep}-{case["name"]}-{engine}',model)
                    rows.append(r);print(case['name'],engine,r['graded_correct'],r.get('error',''),flush=True)
                    (args.output/'trials.json').write_text(json.dumps(rows,indent=2))
    summary={'classification':'exploratory-real-model' if args.model else 'replay-smoke-not-a-latency-or-promotion-result',
             'source_commit':revision(),
             'input_sha256':__import__('hashlib').sha256(json.dumps(cases,sort_keys=True).encode()).hexdigest(),
             'source_sha256':{str(f):__import__('hashlib').sha256(f.read_bytes()).hexdigest() for f in Path('src/jobagent').rglob('*.py')},
             'sources_changed_during_run':any(hashlib.sha256(Path(f).read_bytes()).hexdigest()!=h for f,h in source_hashes.items()),
             'formal_checkpoint_integrated':False,'promotion_allowed':False,'trials':len(rows)}
    for engine in ('current','candidate'):
        arm=[r for r in rows if r['engine']==engine]
        summary[engine]={'correct':sum(r['graded_correct'] for r in arm),'completed':sum(bool(r.get('ready')) for r in arm),
                         'median_active_s':statistics.median(r['active_s'] for r in arm),'questions':sum(r.get('questions',0) for r in arm),
                         'false_ready':sum(bool(r.get('ready')) and not r['graded_correct'] for r in arm),
                         'errors':sum('error' in r for r in arm)}
    import csv
    with (args.output/'trials.csv').open('w') as handle:
        cols=['engine','fixture','heldout','active_s','questions','model_calls','ready','graded_correct','submitted','error']
        writer=csv.DictWriter(handle,fieldnames=cols,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
