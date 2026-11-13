"""Exercise the actual entrypoint, including private snapshots and shared explicit inputs."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_candidate_cli_uses_shared_explicit_facts_without_modifying_input(tmp_path):
    job=tmp_path/'source-job';job.mkdir()
    (job/'jd.txt').write_text('Synthetic job posting')
    (job/'jd.json').write_text('{"company":"Fixture","title":"Fixture"}')
    applicant=tmp_path/'applicant.yaml';applicant.write_text('profile: []\n')
    facts=tmp_path/'facts.json';facts.write_text(json.dumps([{'id':'email','aliases':['Email'],
        'value':'person@example.test','source':'user-confirmed'}]))
    html=tmp_path/'fixture.html';html.write_text('<label for=e>Email</label><input id=e type=email required><button type=button>Apply</button>')
    before={p.name:p.read_bytes() for p in job.iterdir()}
    root=tmp_path/'trial'
    result=subprocess.run([sys.executable,'-m','jobagent.experiments.filling','--engine','candidate',
        '--job-dir',str(job),'--applicant',str(applicant),'--facts',str(facts),'--html',str(html),
        '--run-dir',str(root),'--headless'],capture_output=True,text=True,timeout=45,input='')
    assert result.returncode==0,result.stdout+result.stderr
    record=json.loads((root/'result.json').read_text())
    assert record['ready'] and record['questions']==0
    assert {p.name:p.read_bytes() for p in job.iterdir()}==before
    assert applicant.read_text()=='profile: []\n'
    assert os.stat(root).st_mode & 0o077==0
    manifest=json.loads((root/'manifest.json').read_text())
    assert manifest['source_sha256']
