"""Source-routed form filling by default, with explicit strategy selection and resume continuity."""
import json
from pathlib import Path

STRATEGIES = ("original", "experimental", "adaptive")


def select_strategy(value=None, *, resume_job=None):
    if value is None and resume_job is not None:
        path=Path(resume_job)/'application'/'application.json'
        if path.exists():
            value=json.loads(path.read_text()).get('filling_strategy')
    if value is None:
        from jobagent.config import get_settings
        value=get_settings().filling_strategy
    if value not in STRATEGIES:
        raise ValueError('filling-strategy must be original, experimental or adaptive')
    return value
