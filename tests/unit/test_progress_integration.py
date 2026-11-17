"""Execution boundaries and real file-queue waits, independent of portal fixtures."""
import json
import threading
import time

import pytest

from jobagent.config import Settings
from jobagent.dashboard import data
from jobagent.log import RunLog, progress
from jobagent.progress import execution, job_command
from jobagent.tools.hitl import Hitl


def settings(tmp_path):
    return Settings(state_dir=tmp_path / 'state', outputs_dir=tmp_path / 'outputs', data_dir=tmp_path / 'data')


def test_identical_requisition_numbers_at_different_companies_are_distinct(tmp_path):
    s = settings(tmp_path)
    for company in ['First', 'Second']:
        with execution(new=True, job='123', job_dir=str(s.outputs_dir / company / '123'), company=company, plan=['apply']):
            log = RunLog('apply', '123', directory=s.state_dir / 'logs', quiet=True)
            progress(log, 'apply', 'done')
            log.close()
    jobs = data.pipeline(s)
    assert len(jobs) == 2 and {j['company'] for j in jobs} == {'First', 'Second'}
    assert len({j['identity'] for j in jobs}) == 2


def test_same_second_retry_cannot_inherit_the_previous_failure(tmp_path):
    s = settings(tmp_path)
    for status in ['blocked', 'done']:
        with execution(new=True, job='123', job_dir=str(s.outputs_dir / 'one/123'), plan=['apply']):
            log = RunLog('apply', '123', directory=s.state_dir / 'logs', quiet=True)
            progress(log, 'apply', status)
            log.close()
    jobs = data.pipeline(s)
    assert len(jobs) == 1 and jobs[0]['state'] == 'done' and jobs[0]['percent'] == 100
    assert not jobs[0]['active']


@pytest.mark.parametrize('fails', [True, False])
def test_standalone_command_has_a_real_final_status(tmp_path, monkeypatch, fails):
    s = settings(tmp_path)
    monkeypatch.setattr('jobagent.config.get_settings', lambda: s)

    @job_command('documents')
    def operation(job_dir):
        if fails:
            raise RuntimeError('fixture failure')

    if fails:
        with pytest.raises(RuntimeError):
            operation(str(s.outputs_dir / 'company/123'))
    else:
        operation(str(s.outputs_dir / 'company/123'))
    job = data.pipeline(s)[0]
    assert job['state'] == ('failed' if fails else 'done') and not job['active']


def test_real_file_queue_wait_is_linked_to_its_job_and_then_resumes(tmp_path):
    s = settings(tmp_path)
    result = []
    queue = Hitl(s.state_dir / 'hitl', lambda _: None)

    def worker():
        with execution(new=True, stage='apply', job='123', job_dir=str(s.outputs_dir / 'company/123'), plan=['apply']):
            log = RunLog('apply', '123', directory=s.state_dir / 'logs', quiet=True)
            progress(log, 'apply', 'running')
            try:
                result.append(Hitl(s.state_dir / 'hitl', log).ask('Fixture: enter test code', timeout_s=5, poll_s=.01))
                progress(log, 'apply', 'done')
            finally:
                log.close()

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.monotonic() + 3
    pending = []
    while time.monotonic() < deadline:
        pending = data.hitl_pending(s)
        if pending and data.pipeline(s)[0]['state'] == 'waiting':
            break
        time.sleep(.01)
    try:
        job = data.pipeline(s)[0]
        assert job['state'] == 'waiting' and job['active']
        assert job['question_ids'] == [pending[0]['id']]
        queue.answer(pending[0]['id'], 'fixture-code')
    finally:
        thread.join(timeout=6)
    assert result == ['fixture-code'] and not data.hitl_pending(s)
    assert data.pipeline(s)[0]['state'] == 'done'
