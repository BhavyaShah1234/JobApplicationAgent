import json,hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path('/tmp/form-provider-refreshed-4bed99a');cases=json.loads((root/'cases.json').read_text())
case=next(c for c in cases if c['name']=='fresh-compiled-conditional-date')
trials=json.loads((root/'trials.json').read_text());out=[];seen=set()
with sync_playwright() as pw:
    browser=pw.chromium.launch(channel='chrome',headless=True)
    for row in trials:
        if row['fixture']!=case['name']:continue
        value=row['actual']['#start'];key=(row['engine'],value)
        if key in seen:continue
        seen.add(key);page=browser.new_page();page.set_content(case['html'])
        page.locator('#job').select_option(label='Full time');page.locator('#start').fill(value)
        evidence=page.locator('#start').evaluate('e=>({value:e.value,valid:e.validity.valid,patternMismatch:e.validity.patternMismatch,pattern:e.pattern,placeholder:e.placeholder})')
        out.append({'engine':row['engine'],'reported_ready':row['ready'],**evidence});page.close()
    browser.close()
Path('/tmp/form-date-native-validation.json').write_text(json.dumps({'purpose':'Replay only the observed values into the unchanged registered control to inspect native validity; no agent rerun or repair', 'html_sha256':hashlib.sha256(case['html'].encode()).hexdigest(),'evidence':out},indent=2))
print(json.dumps(out))
