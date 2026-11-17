"""The dashboard must use the same exact resume/JD score identity as review."""
import json

from jobagent import db
from jobagent.config import Settings
from jobagent.dashboard import data
from jobagent.tools import ats_result, ats_sites


def test_dashboard_never_averages_scores_from_other_resume_versions(tmp_path):
    s = Settings(state_dir=tmp_path / 'state', outputs_dir=tmp_path / 'outputs', data_dir=tmp_path / 'data')
    s.state_dir.mkdir()
    d = s.outputs_dir / 'fixture/one'
    d.mkdir(parents=True)
    (d / 'jd.json').write_text(json.dumps({'company': 'Fixture', 'title': 'Engineer'}))
    (d / 'jd.txt').write_text('Fixture job description')
    (d / 'resume.pdf').write_bytes(b'fixture-current-resume')
    sha, fingerprint = ats_result.identity(d)
    with db.connect(s.state_dir / 'jobs.sqlite') as conn:
        for i, site in enumerate(ats_sites.AVERAGE_SITES):
            conn.execute('INSERT INTO ats_runs(job_dir,site,round,score,raw,resume_sha) VALUES(?,?,?,?,?,?)',
                         (str(d), site, 0, 60 + i, json.dumps({'job_fingerprint': fingerprint}), sha))
        # A larger round and later row do not make another PDF's score current.
        conn.execute('INSERT INTO ats_runs(job_dir,site,round,score,raw,resume_sha) VALUES(?,?,?,?,?,?)',
                     (str(d), ats_sites.AVERAGE_SITES[0], 99, 100, json.dumps({'job_fingerprint': fingerprint}), 'other-pdf'))
    report = data.analytics(s)['ats']
    assert report['jobs'] == 1 and report['incomplete'] == 0
    assert sum(b['n'] for b in report['hist']) == 1
    assert next(r for r in report['by_site'] if r['site'] == ats_sites.AVERAGE_SITES[0])['mean'] == 60
    (d / 'resume.pdf').write_bytes(b'fixture-next-resume-with-no-scores')
    report = data.analytics(s)['ats']
    assert report['jobs'] == 0 and report['incomplete'] == 1
    assert report['by_site'] == [] and sum(b['n'] for b in report['hist']) == 0
