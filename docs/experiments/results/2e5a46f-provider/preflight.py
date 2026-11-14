import json, time, re
from pathlib import Path
from playwright.sync_api import sync_playwright
from jobagent.tools import forms, shadow
ROOT=Path('/tmp/form-experiment-live-preflight')
candidates=json.loads((ROOT/'candidates.json').read_text())
rows=[]
with sync_playwright() as pw:
    browser=pw.chromium.launch(channel='chrome',headless=True)
    for c in candidates:
        if c['company'] in ('Stripe','Palantir Technologies'):
            rows.append({**c,'status':'excluded_non_US_from_saved_JD'});continue
        row=dict(c);start=time.monotonic();ctx=browser.new_context();shadow.install(pw,ctx);page=ctx.new_page()
        try:
            response=page.goto(c['url'],wait_until='domcontentloaded',timeout=45000)
            page.wait_for_timeout(2500)
            text=page.locator('body').inner_text(timeout=5000)
            row.update(http_status=response.status if response else None,final_url=page.url,title=page.title(),public_text=text[:20000])
            row['status']='challenge' if re.search(r'verify you are human|checking your browser|just a moment|access denied|request blocked',text,re.I) else 'loaded'
            view=forms.read_page(page)
            row.update(fields=[{'label':f.label,'kind':f.kind,'required':f.required,'section':getattr(f,'section','')} for f in view.fields],buttons=view.buttons,coverage=view.coverage.problems())
            row['frame_urls']=[f.url for f in page.frames]
            row['apply_links']=page.locator('a').evaluate_all("els=>els.filter(e=>/apply|application/i.test(e.innerText)).slice(0,15).map(e=>({text:e.innerText.slice(0,150),href:e.href}))")
            page.screenshot(path=str(ROOT/(re.sub('[^a-z0-9]+','-',c['company'].lower())+'-'+c['job_dir'].split('/')[-1]+'.png')),full_page=True)
        except Exception as exc:
            row.update(status='navigation_or_read_error',error=type(exc).__name__)
        finally:
            ctx.close();row['setup_seconds']=time.monotonic()-start;rows.append(row)
            (ROOT/'results.json').write_text(json.dumps(rows,indent=2))
            print(c['company'],c['job_dir'].split('/')[-1],row['status'],len(row.get('fields',[])),flush=True)
    browser.close()
