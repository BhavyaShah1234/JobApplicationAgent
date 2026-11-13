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
    ctx_data=A.context(job,'experiment')
    facts=compile_facts(ctx_data['_applicant'],raw)+explicit
    model=llm.writer(model=get_settings().model_planner,timeout=120) if args.model else None
    manifest={'engine':args.engine,'model_enabled':bool(model),'source_commit':revision(),
              'source_sha256':{str(p.relative_to(Path(__file__).parents[3])):hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in Path(__file__).parents[2].joinpath('jobagent').rglob('*.py')},
              'inputs':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [args.applicant,jd,*([args.facts] if args.facts else [])]}}
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(str(root/'browser'),channel='chrome',headless=args.headless)
        shadow.install(pw,ctx);page=ctx.pages[0]
        if args.html:page.set_content(args.html.read_text())
        else:
            from jobagent.tools.desktop_browser import public_destination
            if not public_destination(args.url):raise ValueError('live drafts require a public HTTPS URL')
            page.goto(args.url,wait_until='domcontentloaded')
            if not args.headless:input('Sign in/navigate to the form in this isolated browser, then press Enter: ')
        if any(f.kind=='password' for f in forms.read_page(page).fields):raise ValueError('Sign in before starting filling; authentication is measured separately')
        files={role:str((args.job_dir/(role+'.pdf')).resolve()) for role in ('resume','cover_letter') if (args.job_dir/(role+'.pdf')).exists()}
        resume=subprocess.run(['pdftotext',str(args.job_dir/'resume.pdf'),'-'],capture_output=True,text=True).stdout
        if args.engine=='candidate':result=Candidate(facts,batch_ask,model,{'resume':resume,'jd':raw}).fill(page,files)
        else:
            if model is None:raise ValueError('current engine requires --model')
            class Hitl:
                questions=0;wait=0
                def ask(self,prompt,**kwargs):
                    self.questions+=1;t=time.monotonic();print(prompt);value=input('Answer: ');self.wait+=time.monotonic()-t;return value
            h=Hitl();records=[];start=time.monotonic();result={}
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
                    'active_s':time.monotonic()-start-h.wait,'questions':h.questions}
        (root/'result.json').write_text(json.dumps(result,indent=2));os.chmod(root/'result.json',0o600)
        page.screenshot(path=str(root/'review.png'),full_page=True)
        print(json.dumps({k:v for k,v in result.items() if k not in ('records','events')},indent=2))
        if not args.headless:input('Stopped without submitting. Inspect the draft, then Enter closes this experiment browser: ')
        ctx.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--engine',choices=['current','candidate'],required=True)
    p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--job-dir',type=Path,required=True)
    p.add_argument('--applicant',type=Path,required=True);p.add_argument('--facts',type=Path)
    target=p.add_mutually_exclusive_group(required=True);target.add_argument('--url');target.add_argument('--html',type=Path)
    p.add_argument('--model',action='store_true',help='Enable configured planner on both compared arms')
    p.add_argument('--headless',action='store_true');run(p.parse_args())


if __name__=='__main__':main()
