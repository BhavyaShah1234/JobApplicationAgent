"""Run one real draft arm through production apply; never submit.

Run in the configured jobagent environment. Poll control/pending.json and write
control/reply-N.json as {"answer": "...", "classification": "necessary|avoidable|challenge|technical",
"evidence": "..."}. Answers are supplied by the owner or an explicit source audit,
not invented by this harness. Never check private artifacts into Git.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import threading
import time


# Defense in depth; production apply already stops before submission. Wizard
# continuation and account login are allowed, final application controls are not.
GUARD = r"""(() => {
 const final = e => /^(submit|send|finish|complete\b|apply\b)/i.test((e?.innerText||e?.value||e?.getAttribute('aria-label')||'').trim());
 const auth = form => !!form?.querySelector('input[type=password]');
 const mark = reason => { window.__trialSubmissionBlocks ||= []; window.__trialSubmissionBlocks.push(reason); };
 document.addEventListener('click', e => {
   const b=e.composedPath().find(x => x?.matches?.('button,input[type=submit],[role=button]'));
   if (b && final(b) && !auth(b.form) && document.querySelector('input[type=email],input[type=file],textarea')) {
     e.preventDefault(); e.stopImmediatePropagation(); mark('final application click');
   }
 },true);
 document.addEventListener('submit',e=>{
   if(auth(e.target))return;
   if(e.submitter && /^(next|continue|save (and|&) continue|proceed|review)\b/i.test(e.submitter.innerText||e.submitter.value||''))return;
   e.preventDefault();e.stopImmediatePropagation();mark('final form submit');
 },true);
 const submit=HTMLFormElement.prototype.submit;
 HTMLFormElement.prototype.submit=function(){if(auth(this))return submit.call(this);mark('programmatic form submit');};
})();"""


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2,default=str));temporary.replace(path)


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


class TrialHitl:
    def __init__(self,root):self.root=root;self.events=[]
    def ask(self,prompt,timeout_s=1800,**kwargs):
        n=len(self.events)+1;started=time.monotonic()
        event={'id':n,'prompt':prompt,'classification':'unreviewed'}
        write(self.root/'pending.json',event)
        print(f'TRIAL_QUESTION {n}: {prompt}',flush=True)
        path=self.root/f'reply-{n}.json'
        while not path.exists():
            if time.monotonic()-started>timeout_s:
                value={'answer':'skip','classification':'unanswered','evidence':'timed out'};break
            time.sleep(1)
        else:value=json.loads(path.read_text())
        event.update(value,wait_s=time.monotonic()-started);self.events.append(event)
        write(self.root/'events.json',self.events);(self.root/'pending.json').unlink(missing_ok=True)
        return value['answer']


class TrialStopped(BaseException):pass


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job',type=Path,required=True);p.add_argument('--company',required=True)
    p.add_argument('--strategy',choices=['original','experimental'],required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--url')
    p.add_argument('--storage-state',type=Path);p.add_argument('--headless',action='store_true')
    p.add_argument('--active-budget-minutes',type=float,default=30)
    args=p.parse_args();os.umask(0o077)
    root=args.out.resolve();root.mkdir(parents=True,exist_ok=False)
    from jobagent.config import get_settings
    original=get_settings();data=root/'data';data.mkdir();state=root/'state';state.mkdir()
    # The real CLI supplies this account callback. Keep credentials in the normal
    # encrypted vault, not in the copied candidate inputs or result artifacts.
    portal_vault=portal_inbox=None
    if args.storage_state:
        from jobagent.tools.vault import open_vault
        from jobagent.tools.inbox import Inbox
        from jobagent import db
        portal_vault=open_vault();mail=portal_vault.get('gmail') or {}
        portal_inbox=Inbox(mail['email'],mail['app_password'],db.connect())
    for name in ('applicant.yaml','learned.yaml','asu_contacts.csv'):
        src=original.data_dir/name
        if not src.exists() and name=='asu_contacts.csv':src=Path('/app/asu_contacts.csv')
        if src.exists():shutil.copy2(src,data/name)
    cache=original.state_dir/'learned_vectors.json'
    if cache.exists():shutil.copy2(cache,state/cache.name)
    job=root/'job';job.mkdir()
    for name in ('jd.json','jd.txt','resume.pdf','resume.tex','cover_letter.pdf','cover_letter.tex','context-snapshot.json'):
        src=args.job/name
        if src.exists():shutil.copy2(src,job/name)
    for name in ('address.json','answers.md'):
        src=args.job/'application'/name
        if src.exists():
            (job/'application').mkdir(exist_ok=True);shutil.copy2(src,job/'application'/name)
    if args.url:
        jd=json.loads((job/'jd.json').read_text());jd['apply_url']=args.url
        (job/'jd.json').write_text(json.dumps(jd,indent=2))
    os.environ.update(DATA_DIR=str(data),STATE_DIR=str(state));get_settings.cache_clear()
    from playwright.sync_api import sync_playwright
    from jobagent.steps import apply as A
    from jobagent.tools import forms,shadow
    from jobagent.filling import metrics as M
    captured=[];base_metrics=M.FillingMetrics
    class ObservedMetrics(base_metrics):
        def __init__(self):super().__init__();captured.append(self)
    M.FillingMetrics=ObservedMetrics
    def stop(*_):raise TrialStopped('trial active-time budget or operator stop')
    signal.signal(signal.SIGTERM,stop)
    def budget():
        while True:
            time.sleep(2)
            if captured and captured[0].snapshot()['active_filling_s']>args.active_budget_minutes*60:
                os.kill(os.getpid(),signal.SIGTERM);return
    threading.Thread(target=budget,daemon=True).start()
    if (job/'context-snapshot.json').exists():
        company_source=json.loads((job/'context-snapshot.json').read_text()).get('COMPANY','')
        build_context=A.context
        def frozen_company(*a,**kw):
            ctx=build_context(*a,**kw);ctx['COMPANY']=company_source;return ctx
        A.context=frozen_company
    hitl=TrialHitl(root/'control');views=[]
    inputs={str(f.relative_to(root)):digest(f) for folder in (data,job) for f in folder.rglob('*') if f.is_file()}
    source=Path(A.__file__).parents[1]
    write(root/'manifest.json',{'strategy':args.strategy,'inputs':inputs,'source':{
        str(f.relative_to(source)):digest(f) for f in source.rglob('*.py')}})
    # Observe the same filling boundary in either engine without altering answers,
    # controls, loop budgets or model calls.
    def instrument(fn):
        def observed(page,ctx,files,model,log,record,page_no,*a,**kw):
            view=forms.read_page(page)
            item={'page':page_no,'url':page.url,'fields':[asdict(f) for f in view.fields]}
            views.append(item);write(root/'initial-views.json',views)
            page.screenshot(path=str(root/f'before-{len(views)}.png'),full_page=True)
            return fn(page,ctx,files,model,log,record,page_no,*a,**kw)
        return observed
    if args.strategy=='original':A.fill_page=instrument(A.fill_page)
    else:
        from jobagent.filling import adapter
        adapter.fill_page=instrument(adapter.fill_page)
    blocked=[]
    def route(r):
        request=r.request;url=request.url.lower()
        body=(request.post_data_buffer or b'').decode('utf-8',errors='replace')
        is_final=request.method=='POST' and (
            any(x in url for x in ('submitapplication','submit-application','application/submit','jobapplication/submit')) or
            ('greenhouse.io' in url and '/jobs/' in url and not any(x in url for x in ('upload','parse','validate'))) or
            ('triumph.tech' in url and '/apply' in url) or
            ('ashbyhq.com' in url and any(x in body for x in ('ApiSubmitApplication','SubmitApplication','submitApplication'))))
        if is_final:
            blocked.append({'url':url,'reason':'final submission request'});write(root/'submission-blocks.json',blocked);r.abort()
        else:r.continue_()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=args.headless)
        context=browser.new_context(**({'storage_state':str(args.storage_state)} if args.storage_state else {}))
        shadow.install(pw,context);context.add_init_script(GUARD);context.route('**/*',route)
        page=context.new_page();start=time.monotonic()
        try:
            def sign_in(url):
                if portal_vault is None:return
                from jobagent.tools.portal_auth import ensure_account
                ensure_account(page,url,portal_vault,portal_inbox,hitl,portal_inbox.address,log=print)
            result=A.run(page,job,args.company,hitl=hitl,ask_s=900,filling_strategy=args.strategy,
                         ensure_signed_in=sign_in,log=lambda m:print(m,flush=True))
            write(root/'outcome.json',{'state':result.get('state'),'ready':result.get('ready_to_submit'),
                  'metrics':result.get('filling_metrics'),'elapsed_total_s':time.monotonic()-start})
        except BaseException as exc:
            if captured:captured[0].finish()
            write(root/'exception.json',{'type':type(exc).__name__,'message':str(exc),'elapsed_total_s':time.monotonic()-start,
                  'metrics':captured[0].snapshot() if captured else None,'ready':False})
            raise
        finally:
            try:
                page.screenshot(path=str(root/'final.png'),full_page=True)
                write(root/'final-view.json',{'url':page.url,'fields':[asdict(f) for f in forms.read_page(page).fields],
                      'submission_blocks':page.evaluate('window.__trialSubmissionBlocks||[]'),
                      'native_controls':[{'frame':i,'controls':frame.locator('input:not([type=password]),select,textarea').evaluate_all(
                          "es=>es.filter(e=>e.type!=='hidden').map(e=>({id:e.id,name:e.name,type:e.type,required:e.required,"
                          "aria_required:e.getAttribute('aria-required'),will_validate:e.willValidate,valid:e.validity?.valid,"
                          "validation_message:e.validationMessage,labels:[...(e.labels||[])].map(x=>x.innerText)}))")}
                          for i,frame in enumerate(page.frames)]})
            finally:
                browser.close()
                if portal_inbox:portal_inbox.close()


if __name__=='__main__':main()
