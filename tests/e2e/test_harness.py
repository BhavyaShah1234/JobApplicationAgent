"""Step 0 e2e: run inside the container (`docker compose exec agent pytest -m e2e tests/e2e/test_harness.py`)."""

import pytest

from jobagent.steps.harness import run_all

pytestmark = pytest.mark.e2e


def test_all_harness_checks_pass():
    results = run_all()
    failed = {r.name: r.detail for r in results if not r.ok}
    assert not failed, failed
