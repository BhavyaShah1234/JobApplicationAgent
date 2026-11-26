"""Independent review of presence concurrency and queue-attempt boundaries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime as RealDateTime
import threading

import pytest

from jobagent.tools import jobqueue as Q, presence, queue_runner as R
from jobagent.tools.pipeline import PipelineResult
from test_presence import seed, state


def test_away_runner_does_not_wait_for_another_companys_deferred_work(state):
    conn = seed(state / 'jobs.sqlite', 1)
    row = Q.claim(conn, 'earlier')
    assert Q.defer(conn, row['url'], 'earlier')
    presence.set_available(False, state_dir=state)
    result = R.run_queue(conn, through='apply', limit=1, company='Different Employer',
                         outputs_dir=state / 'outputs', slug_of=str.lower,
                         log=lambda _: None, pipeline=object(),
                         sleep=lambda _: pytest.fail('Unrelated company must not keep this runner waiting'))
    assert result == 0


def test_same_second_deferral_from_a_previous_attempt_cannot_requeue_a_new_failure(state, monkeypatch):
    import datetime
    fixed = RealDateTime(2026, 11, 26, 20, 0, 0, tzinfo=UTC)

    class FrozenDateTime(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(datetime, 'datetime', FrozenDateTime)
    monkeypatch.setattr(presence, '_now', lambda: fixed.isoformat(timespec='seconds'))
    conn = seed(state / 'jobs.sqlite', 1)
    url = 'https://e.test/jobs/1'
    presence.record_deferral({'url': url}, 'Earlier attempt needed the owner', state)
    presence.set_available(True, state_dir=state)

    class Broken:
        calls = 0

        def run(self, url, **kwargs):
            self.calls += 1
            if self.calls > 1:
                # BaseException exits the runner's broad Exception handler.
                raise SystemExit('A stale deferral caused an incorrect retry')
            return PipelineResult('blocked', code=4, note='Independent portal failure')

    pipeline = Broken()
    R.run_queue(conn, through='apply', limit=1, company=None, outputs_dir=state / 'outputs',
                slug_of=str.lower, log=lambda _: None, pipeline=pipeline)
    assert pipeline.calls == 1
    assert conn.execute('SELECT status, deferred_at FROM jobs').fetchone()[:] == ('blocked', None)


def test_away_runner_does_not_wait_for_deferred_work_excluded_by_pacing(state, monkeypatch):
    from jobagent.tools import pacing
    conn = seed(state / 'jobs.sqlite', 1)
    row = Q.claim(conn, 'earlier')
    assert Q.defer(conn, row['url'], 'earlier')
    presence.set_available(False, state_dir=state)
    monkeypatch.setattr(pacing, 'excluded', lambda *args, **kwargs: {'acme': 2})
    assert R.run_queue(conn, through='apply', limit=1, company='Acme', outputs_dir=state / 'outputs',
                       slug_of=str.lower, log=lambda _: None, pipeline=object(),
                       sleep=lambda _: pytest.fail('Paced-out work must not keep this runner waiting')) == 0


def test_simultaneous_dashboard_presence_writes_do_not_share_a_temporary_file(state):
    start = threading.Barrier(8)

    def writer(index):
        start.wait(timeout=5)
        errors = []
        for iteration in range(20):
            try:
                presence.set_available(bool((index + iteration) % 2), state_dir=state)
            except OSError as exc:
                errors.append(type(exc).__name__)
        return errors

    with ThreadPoolExecutor(max_workers=8) as pool:
        errors = [error for batch in pool.map(writer, range(8)) for error in batch]
    assert not errors, errors
    assert isinstance(presence.status(state)['available'], bool)
