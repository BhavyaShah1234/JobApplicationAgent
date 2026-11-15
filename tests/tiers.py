"""How tests are assigned to tiers (step 30). Pure functions, so the rules themselves are tested (tests/unit/test_test_tiers.py)."""
import inspect
import re
from pathlib import Path

UNIT = Path(__file__).parent / "unit"
_cache: dict[str, bool] = {}


def module_starts_chrome(path: Path, depth: int = 3) -> bool:
    """True when the module drives Playwright, or imports (up to `depth` levels) a test module that does: a `world` fixture imported from
    another test file starts Chrome even though the importing file never mentions Playwright."""
    key = str(path)
    if key in _cache:
        return _cache[key]
    _cache[key] = False                                            # cycle guard
    try:
        text = path.read_text()
    except OSError:
        return False
    found = "sync_playwright" in text
    if not found and depth > 0:
        for name in re.findall(r"^\s*(?:from|import)\s+(test_\w+|page_fakes)\b", text, re.M):
            sibling = path.parent / f"{name}.py"
            if sibling.is_file() and module_starts_chrome(sibling, depth - 1):
                found = True
                break
    _cache[key] = found
    return found


def simulation_size(source: str) -> int:
    """The largest simulated population a test builds (`simulate(N, ...)`), 0 when it builds none."""
    return max((int(n) for n in re.findall(r"simulate\(\s*(\d+)", source)), default=0)


SIMULATION_MIN = 250                                              # applications; below this a simulated world is a fast unit test


def is_simulation(item) -> bool:
    try:
        source = inspect.getsource(item.function)
    except (OSError, TypeError):
        return False
    return simulation_size(source) >= SIMULATION_MIN or bool(re.search(r"for \w+ in range\(\d{2,}\):", source) and "simulate(" in source)
