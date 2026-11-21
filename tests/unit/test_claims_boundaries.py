"""Claims boundaries: future interest is not employment, the declared profile is validated, technical horizons are not personal tenure."""
import json
import math
from types import SimpleNamespace

import pytest

from jobagent.steps import multi_agent as M
from jobagent.tools import tailor as T

pytest_plugins = []


# --- the declared profile ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def profile(tmp_path, monkeypatch):
    from jobagent.config import get_settings
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path, raising=False)

    def write(text):
        (tmp_path / "master_profile.yaml").write_text(text)
    write.dir = tmp_path
    return write


@pytest.mark.parametrize("text", ["claims: 5", "claims: [a, b]", "claims:\n  years_experience: five", "claims:\n  years_experience: .nan", "claims:\n  years_experience: .inf",
                                  "claims:\n  years_experience: -1", "claims:\n  years_experience: true", "- a\n- b", "claims:\n  years_experience: [5]"])
def test_an_invalid_declared_profile_is_an_actionable_error_not_a_dropped_cap(profile, text):
    profile(text)
    with pytest.raises(ValueError, match="master_profile.yaml"):
        T.declared_claims()


def test_valid_and_optional_profiles_keep_working(profile):
    assert T.declared_claims() == {}                                                    # no file
    profile("")
    assert T.declared_claims() == {}                                                    # empty file
    profile("claims:\n")
    assert T.declared_claims() == {}                                                    # no claims
    profile("claims:\n  years_experience: 5\n  other: x")
    assert T.declared_claims() == {"years_experience": 5, "other": "x"}
    profile("claims:\n  years_experience: 4.5")
    assert T.declared_claims()["years_experience"] == 4.5
    profile("claims:\n  years_experience: 0")
    assert T.declared_claims()["years_experience"] == 0


def test_a_profile_path_that_is_a_directory_is_reported_not_treated_as_missing(profile):
    (profile.dir / "master_profile.yaml").mkdir()
    with pytest.raises(ValueError, match="master_profile.yaml"):
        T.declared_claims()


# --- technical horizons are not personal tenure --------------------------------------------------------------------------------------

@pytest.mark.parametrize("summary", ["Engineer who built 20-year NPV forecasting models using Python.", "Engineer who built twenty-year forecasting tools.",
                                      "Built a 30-year loan amortization model for lenders.", "Forecasts demand 10 years ahead with Python.",
                                      "Built a 15-year retention archive for audit logs.", "Analyzed 25 years of historical market data with Python."])
def test_a_technical_horizon_is_not_a_claim_of_personal_experience(monkeypatch, summary):
    monkeypatch.setattr(T, "declared_claims", lambda: {"years_experience": 5})
    assert not any("declared" in i for i in M.summary_issues(summary, False)), summary


@pytest.mark.parametrize("summary", ["Engineer with a 12-year track record in ML.", "Engineer with twelve years of experience.", "Engineer with two decades in data.",
                                      "Engineer with ten-plus years building forecasting models.", "ML engineer with 20 years of hands-on forecasting experience."])
def test_real_tenure_claims_are_still_caught(monkeypatch, summary):
    monkeypatch.setattr(T, "declared_claims", lambda: {"years_experience": 5})
    assert any("declared 5" in i for i in M.summary_issues(summary, False)), summary
