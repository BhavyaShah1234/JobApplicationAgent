"""Explicit, isolated experiment entrypoint. Never imports submit or modifies production source."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import time
import yaml
from playwright.sync_api import sync_playwright
from .engine import Candidate
from .resolution import Fact, compile_facts


def revision():
    try:return subprocess.check_output(['git','rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError):return os.environ.get('JOBAGENT_EXPERIMENT_COMMIT','unavailable; use source hashes')


def batch_ask(questions):
    print(json.dumps({'missing_answers':questions,'reply':'JSON object mapping field IDs to answers; null skips'},indent=2))
    answer=json.loads(input('Answers: '))
    if not isinstance(answer,dict):raise ValueError('answers must be a JSON object')
    return answer


def run(args):
    root=args.run_dir.resolve()
    if root.exists():raise ValueError('run-dir must be new; each trial needs independent state')
    root.mkdir(parents=True,mode=0o700);os.chmod(root,0o700)
    # Settings must be isolated before importing the existing engine or its settings cache.
    os.environ.update(STATE_DIR=str(root/'state'),OUTPUTS_DIR=str(root/'outputs'),DATA_DIR=str(root/'data'))
    (root/'data').mkdir();(root/'data'/'applicant.yaml').write_bytes(args.applicant.read_bytes())
    os.chmod(root/'data'/'applicant.yaml',0o600)
    from jobagent import llm
    from jobagent.config import get_settings
    get_settings.cache_clear()
    from jobagent.tools import forms,shadow,wizard
    from jobagent.steps import apply as A
    data=yaml.safe_load(args.applicant.read_text()) or {}
    jd=args.job_dir/'jd.txt';raw=jd.read_text();md=args.job_dir/'jd.md'
    explicit=[]
    facts=[]
    if args.facts:
        explicit=[Fact(**x) for x in json.loads(args.facts.read_text())]
        facts += explicit
        data['experiment_explicit_facts']=[{'question':x.aliases[0],'seen':x.aliases,'answer':x.value,
            'instructions':'Applies only to section '+x.section if x.section else '', 'source':x.source} for x in explicit]
    # Snapshot all job inputs so context enrichment cannot write to the source directory.
    job=root/'job';job.mkdir()
    for name in ('jd.txt','jd.md','jd.json','resume.pdf','resume.tex','cover_letter.pdf','cover_letter.tex'):
        source=args.job_dir/name
        if source.exists():shutil.copyfile(source,job/name)
    posting=job/'jd.json'
    contents=json.loads(posting.read_text()) if posting.exists() else {}
    contents.setdefault('locations_resolved',[])
    posting.write_text(json.dumps(contents))
    (root/'data'/'applicant.yaml').write_text(yaml.safe_dump(data))
    if args.contacts:
        shutil.copyfile(args.contacts,root/'data'/'asu_contacts.csv')
        os.chmod(root/'data'/'asu_contacts.csv',0o600)
    ctx_data=A.context(job,'experiment')
    candidate_data={k:v for k,v in ctx_data['_applicant'].items() if k!='experiment_explicit_facts'}
    raw=(job/'jd.txt').read_text()
    facts=compile_facts(candidate_data,raw)+explicit
    model=llm.writer(model=get_settings().model_writer,timeout=get_settings().llm_timeout_s) if args.model else None
    from .metrics import CallAudit
    audit=CallAudit()
    if model:model.callbacks=[*(model.callbacks or []),audit]
    manifest={'engine':args.engine,'model_enabled':bool(model),'source_commit':revision(),
              'source_sha256':{str(p.relative_to(Path(__file__).parents[3])):hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in Path(__file__).parents[2].joinpath('jobagent').rglob('*.py')},
              'inputs':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [args.applicant,jd,*([args.facts] if args.facts else []),*([args.contacts] if args.contacts else [])]},
              'frozen_job':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in job.iterdir()}}
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(str(root/'browser'),channel='chrome',headless=args.headless)
        shadow.install(pw,ctx);page=ctx.pages[0]
        if args.html:page.set_content(args.html.read_text())
        else:
            from jobagent.tools.desktop_browser import public_destination
            if not public_destination(args.url):raise ValueError('live drafts require a public HTTPS URL')
            page.goto(args.url,wait_until='domcontentloaded')
            from jobagent.tools.desktop_browser import Scope
            scope=Scope(ctx,page,args.url,related_urls=[contents.get('apply_url',''),contents.get('source_url','')],fresh=True)
            ctx.jobagent_policy=scope
            scope(page)
            if not args.headless:input('Sign in/navigate to the form in this isolated browser, then press Enter: ')
        if args.url:ctx.jobagent_policy(page)
        if any(f.kind=='password' for f in forms.read_page(page).fields):raise ValueError('Sign in before starting filling; authentication is measured separately')
        files={role:str((job/(role+'.pdf')).resolve()) for role in ('resume','cover_letter') if (job/(role+'.pdf')).exists()}
        resume=subprocess.run(['pdftotext',str(job/'resume.pdf'),'-'],capture_output=True,text=True).stdout
        if args.engine in ('candidate','experimental','adaptive'):
            if args.engine=='adaptive':
                from jobagent.filling.adaptive import AdaptiveFiller
                filler=AdaptiveFiller(facts,batch_ask,model,{'resume':resume,'jd':raw},
                    {'company':contents.get('company',''),'contact':ctx_data.get('REFERRAL',{})},
                    checkpoint_path=root/'adaptive-v1.json',source_version=hashlib.sha256(
                        json.dumps(manifest['inputs'],sort_keys=True).encode()).hexdigest())
            else:
                filler=Candidate(facts,batch_ask,model,{'resume':resume,'jd':raw},
                    {'company':contents.get('company',''),'contact':ctx_data.get('REFERRAL',{})})
                if args.engine=='experimental':filler.engine_name='experimental'
            result=filler.fill(page,files)
        else:
            if model is None:raise ValueError('current engine requires --model')
            class Hitl:
                questions=0;wait=0
                def __init__(self):self.events=[]
                def ask(self,prompt,**kwargs):
                    self.questions+=1;t=time.monotonic();print(prompt);value=input('Answer: ');self.wait+=time.monotonic()-t
                    self.events.append({'event':'human_question','prompt':prompt,'classification':'unclassified'})
                    return value
            h=Hitl();records=[];start=time.monotonic();result={}
            from jobagent.tools import challenge
            with challenge.session(page,h,policy=getattr(ctx,'jobagent_policy',None)):
                for n in range(1,16):
                    view,_=A.fill_page(page,ctx_data,files,model,print,records,n,env={'hitl':h,'company':'experiment','host':'experiment'})
                    if not view.coverage.complete or view.errors:break
                    before=wizard.state_of(page,view)
                    if not forms.click_button(page,view,forms.NEXT):break
                    progressed,_,_,_=wizard.wait_for_progress(page,before)
                    if progressed!='advanced':break
            blockers=A.blockers_of(records)+view.coverage.problems()+view.errors
            ready=not blockers and any(forms.SUBMIT.search(b) for b in view.buttons)
            result={'engine':'current','state':'review' if ready else 'blocked','ready':ready,'records':records,
                    'blockers':blockers,'elapsed_s':time.monotonic()-start,'human_wait_s':h.wait,
                    'active_s':time.monotonic()-start-h.wait,'questions':h.questions,'events':h.events}
        result['logical_model_calls']=result.get('model_calls')
        result['model_calls']=audit.calls
        result['reported_tokens']=audit.reported_tokens
        (root/'result.json').write_text(json.dumps(result,indent=2));os.chmod(root/'result.json',0o600)
        page.screenshot(path=str(root/'review.png'),full_page=True)
        print(json.dumps({k:v for k,v in result.items() if k not in ('records','events')},indent=2))
        if not args.headless:input('Stopped without submitting. Inspect the draft, then Enter closes this experiment browser: ')
        ctx.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--engine',choices=['current','candidate','experimental','adaptive'],required=True)
    p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--job-dir',type=Path,required=True)
    p.add_argument('--applicant',type=Path,required=True);p.add_argument('--facts',type=Path);p.add_argument('--contacts',type=Path)
    target=p.add_mutually_exclusive_group(required=True);target.add_argument('--url');target.add_argument('--html',type=Path)
    p.add_argument('--model',action='store_true',help='Enable configured planner on both compared arms')
    p.add_argument('--headless',action='store_true');run(p.parse_args())


if __name__=='__main__':main()
