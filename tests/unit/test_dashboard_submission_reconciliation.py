"""Current job status follows exact posting identity and durable receipts, not stale attempts."""
import json
from datetime import datetime, UTC

import pytest

from jobagent import db
from jobagent.config import Settings
from jobagent.dashboard import data
from jobagent.log import RunLog, progress
from jobagent.progress import execution


def emit(settings, company, folder, url, status, *, legacy=False):
    values = {} if legacy else dict(new=True, job='123', job_dir=str(folder), company=company,
                                    title='Engineer', url=url, plan=['documents'])
    with execution(**values):
        log=RunLog('apply' if legacy else 'tailor','123',directory=settings.state_dir/'logs',quiet=True)
        try:progress(log,'apply' if legacy else 'documents',status)
        finally:log.close()
    return log.path.with_suffix('.jsonl')


def receipt(settings, folder, url, company, state):
    conn=db.connect(settings.state_dir/'jobs.sqlite')
    stamp=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    with conn:
        job=conn.execute('INSERT INTO jobs(url,company,title,status) VALUES (?,?,?,?)',
                         (url,company,'Engineer','applied' if state in ('submitted','confirmed') else 'queued')).lastrowid
        conn.execute('INSERT INTO applications(job_id,job_dir,company,title,url,state,submitted_at,confirmed_at) VALUES (?,?,?,?,?,?,?,?)',
                     (job,str(folder.relative_to(settings.outputs_dir.parent)),company,'Engineer',url,state,
                      stamp if state in ('submitted','confirmed') else None,stamp if state=='confirmed' else None))
    conn.close()


@pytest.mark.parametrize('state',['submitted','confirmed','submission_unknown'])
def test_superseded_folders_and_legacy_progress_do_not_override_actual_receipt(tmp_path,state):
    s=Settings(state_dir=tmp_path/'state',outputs_dir=tmp_path/'outputs',data_dir=tmp_path/'data')
    old=s.outputs_dir/'parent/123';current=s.outputs_dir/'subsidiary/123';url='https://portal.test/jobs/123'
    old_log=emit(s,'Parent',old,url,'failed');original=old_log.read_bytes()
    emit(s,'Subsidiary',current,url,'done')
    emit(s,'',None,'','running',legacy=True)
    receipt(s,current,url,'Subsidiary',state)
    jobs=data.pipeline(s)
    assert len(jobs)==1 and jobs[0]['identity']==str(current.resolve())
    assert old_log.read_bytes()==original
    if state=='submission_unknown':
        assert jobs[0]['state']!='done' and 'submission_state' not in jobs[0]
    else:
        job=jobs[0]
        assert job['state']=='done' and not job['active'] and job['submission_state']==state
        assert job['company']=='Subsidiary' and job['url']==url
        assert job['stages'][0]['id']=='submit' and job['stages'][0]['status']=='done'


def test_same_number_at_other_company_does_not_inherit_a_submitted_receipt(tmp_path):
    s=Settings(state_dir=tmp_path/'state',outputs_dir=tmp_path/'outputs',data_dir=tmp_path/'data')
    first=s.outputs_dir/'first/123';second=s.outputs_dir/'second/123'
    emit(s,'First',first,'https://one.test/jobs/123','done')
    emit(s,'Second',second,'https://two.test/jobs/123','failed')
    emit(s,'',None,'','running',legacy=True)
    receipt(s,first,'https://one.test/jobs/123','First','submitted')
    rows=data.pipeline(s)
    assert len(rows)==3  # ambiguous bare legacy number stays separate
    assert next(r for r in rows if r['company']=='First')['submission_state']=='submitted'
    second_job=next(r for r in rows if r['company']=='Second')
    assert second_job['state']=='failed' and 'submission_state' not in second_job


def test_real_submit_cli_reports_its_canonical_job_lifecycle_before_guarded_return(tmp_path,monkeypatch):
    from typer.testing import CliRunner
    from jobagent.main import app
    from jobagent.steps import submit
    s=Settings(state_dir=tmp_path/'state',outputs_dir=tmp_path/'outputs',data_dir=tmp_path/'data')
    folder=s.outputs_dir/'first/123';folder.mkdir(parents=True)
    (folder/'jd.json').write_text(json.dumps({'company':'First','title':'Engineer','source_url':'https://one.test/jobs/123'}))
    monkeypatch.setattr('jobagent.config.get_settings',lambda:s)
    monkeypatch.setattr(db,'connect',lambda:object())
    monkeypatch.setattr(submit,'guard',lambda *a:{'state':'already_submitted','message':'Already received'})
    result=CliRunner().invoke(app,['submit',str(folder),'first','--filling-strategy','original'])
    assert result.exit_code==0,result.output
    rows=data.pipeline(s)
    assert len(rows)==1 and rows[0]['identity']==str(folder.resolve())
    assert rows[0]['company']=='First' and rows[0]['state']=='done' and not rows[0]['active']
