"""Execution identity and lifecycle events shared by the CLI and dashboard."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import json
from pathlib import Path
import uuid

_context = ContextVar('jobagent_progress', default=None)


def metadata():
    return dict(_context.get() or {})


def update(**values):
    current = _context.get()
    if current is not None:
        current.update({k: v for k, v in values.items() if v is not None})


@contextmanager
def execution(*, new=False, **values):
    context = {} if new else metadata()
    context.setdefault('execution_id', uuid.uuid4().hex)
    context.update({k: v for k, v in values.items() if v is not None})
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)


def job_command(stage):
    """Report a command's actual return/exception, including standalone commands."""
    def decorate(fn):
        signature = inspect.signature(fn)

        @wraps(fn)
        def wrapped(*args, **kwargs):
            from jobagent.config import get_settings
            from jobagent.log import progress, runlog, _current
            values = signature.bind_partial(*args, **kwargs).arguments
            details = {'stage': stage}
            if not metadata().get('plan'):
                details['plan'] = [stage]
            job_dir = values.get('job_dir')
            if isinstance(job_dir, (str, Path)):
                d = Path(job_dir)
                if not d.is_absolute():
                    d = get_settings().outputs_dir.parent / d
                details.update(job_dir=str(d.resolve()), job=d.name)
                try:
                    jd = json.loads((d / 'jd.json').read_text())
                    details.update(title=jd.get('title', ''), company=jd.get('company', ''),
                                   url=jd.get('source_url') or jd.get('apply_url') or '')
                except (OSError, ValueError):
                    pass
            previous = _current.get()
            log = runlog(fn.__name__.removesuffix('_cmd').replace('_', '-'), details.get('job', ''))
            try:
                with execution(**details):
                    progress(log, stage, 'running')
                    try:
                        result = fn(*args, **kwargs)
                    except BaseException as exc:
                        code = getattr(exc, 'exit_code', getattr(exc, 'code', None))
                        status = 'done' if code == 0 else 'blocked' if code in (4, 5, 8) else 'failed'
                        progress(log, stage, status, detail=f'exit {code}' if code is not None else type(exc).__name__)
                        raise
                    progress(log, stage, 'done')
                    return result
            finally:
                log.close()
                _current.set(previous)
        return wrapped
    return decorate
