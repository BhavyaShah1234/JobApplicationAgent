"""Explicit human-verification interstitials must not be mistaken for empty forms."""
from types import SimpleNamespace

import pytest

from jobagent.tools import challenge


def page(body, title="Careers"):
    return SimpleNamespace(url="https://jobs.example.com/apply", frames=[], title=lambda: title,
                           locator=lambda selector: SimpleNamespace(inner_text=lambda **kwargs: body))


@pytest.mark.parametrize("body", [
    "Verification Required\nSlide right to secure your access\nWhy is this step needed?",
    "Verification Required\nWe detected unusual activity from your device or network.",
    "VERIFICATION REQUIRED\nWe detected unusual activity from your network.",
])
def test_verification_interstitial_goes_to_human(body):
    result = challenge.detect(page(body))
    assert result and result.kind == "human-verification"


@pytest.mark.parametrize("body", [
    "Verification required for employment eligibility. Upload your documents.",
    "Verification required. Slide right to see the salary range.",
    "We detected unusual activity from your network.",
    "Software Engineer: develop device and network verification systems.",
])
def test_related_employment_and_network_wording_is_not_a_challenge(body):
    assert challenge.detect(page(body)) is None
