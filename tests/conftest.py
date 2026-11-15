"""Test tiers (step 30). The full unit suite is long because many tests drive a real headless Chrome and some run Monte-Carlo
simulations; most changes need only the fast tier.

    pytest -m "not browser and not simulation" tests/unit      # fast tier: logic only, no Chrome, no simulations
    pytest -m browser tests/unit                                # tests that drive a real headless Chrome
    pytest -m simulation tests/unit                             # statistical calibration / null-stream simulations
    pytest tests/unit                                           # everything (what a release is checked with)

A test is `browser` when its module (or a test module it imports) uses Playwright, and `simulation` when it builds a simulated
population of 250+ applications or loops over many simulated worlds; `@pytest.mark.browser` / `@pytest.mark.simulation` mark one by
hand. No test is skipped by this: the markers only let you choose, and tests/unit/test_test_tiers.py checks the rules.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tiers  # noqa: E402


def pytest_collection_modifyitems(config, items):
    for item in items:
        if tiers.module_starts_chrome(Path(str(item.path))):
            item.add_marker(pytest.mark.browser)
        if tiers.is_simulation(item):
            item.add_marker(pytest.mark.simulation)
