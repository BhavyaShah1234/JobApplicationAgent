"""Separate sensitivity check after observing a minimal-context fixture failure; not held-out evidence."""
import hashlib,json,time,os
from dataclasses import asdict
from pathlib import Path
from playwright.sync_api import sync_playwright
from jobagent.experiments.benchmark import fixtures, execute
from jobagent.experiments.resolution import Fact
from jobagent.config import get_settings
from jobagent import llm
root=Path('/tmp/form-contact-context-sensitivity')
root.mkdir(mode=0o700)
case=next(c for c in fixtures() if c['name']=='heldout-contact-alias')
case['prepared_address']=True
case['heldout']=False
for label,value in [('Street Address','123 Example Street'),('City','Houston'),('State','Texas'),('Postal Code','77002'),('Country','United States')]:
    case['facts'].append(asdict(Fact('prepared-address/'+label,[label],value,'synthetic prepared-address source')))
(root/'case.json').write_text(json.dumps(case,indent=2))
(root/'source-manifest.json').write_text(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('src/jobagent').rglob('*.py')},indent=2))
rows=[]
with sync_playwright() as pw:
    for rep in range(3):
        for engine in (['candidate','current'] if rep%2==0 else ['current','candidate']):
            model=llm.writer(model=get_settings().model_writer,timeout=get_settings().llm_timeout_s)
            r=execute(case,engine,pw,root/f'{rep}-{engine}',model)
            rows.append(r);(root/'trials.json').write_text(json.dumps(rows,indent=2))
            print(rep,engine,r['graded_correct'],r.get('question_labels'),flush=True)
