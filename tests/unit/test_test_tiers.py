"""The fast tier must not select a test that starts Chrome or builds a large simulated population (Codex review R08)."""
from pathlib import Path

import tiers

UNIT = Path(__file__).parent


def test_a_module_that_imports_a_chrome_world_is_a_browser_module():
    assert tiers.module_starts_chrome(UNIT / "test_filling_strategy.py")
    assert tiers.module_starts_chrome(UNIT / "test_filling_pipeline.py")          # imports `world` from the module above
    assert not tiers.module_starts_chrome(UNIT / "test_test_tiers.py")


def test_simulation_size_is_read_from_the_source():
    assert tiers.simulation_size("run(simulate(1200, 31, kw_odds=3.5))") == 1200
    assert tiers.simulation_size("apps = simulate(40, 1)") == 40 and tiers.simulation_size("nothing") == 0


def test_the_known_statistical_workloads_are_in_the_simulation_tier(pytestconfig):
    import _pytest.python as P
    import test_learning as T
    fn = T.test_an_effect_that_reverses_in_the_latest_applications_is_not_a_recommendation
    assert tiers.simulation_size(__import__("inspect").getsource(fn)) >= tiers.SIMULATION_MIN
