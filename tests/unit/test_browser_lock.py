import multiprocessing as mp
import time

from jobagent.config import Settings
from jobagent.tools.browser import browser_lock


def _hold(state_dir: str, started, seconds: float):
    with browser_lock(Settings(state_dir=state_dir), log=lambda _: None):
        started.set()
        time.sleep(seconds)


def test_second_run_waits_for_the_first(tmp_path):
    started = mp.Event()
    holder = mp.Process(target=_hold, args=(str(tmp_path), started, 1.5))
    holder.start()
    assert started.wait(10)
    messages = []
    t0 = time.monotonic()
    with browser_lock(Settings(state_dir=tmp_path), log=messages.append, poll_s=0.2):
        waited = time.monotonic() - t0
    holder.join()
    assert waited >= 1.0  # it queued instead of colliding
    assert messages and "browser busy" in messages[0] and "pid" in messages[0]


def test_lock_is_released_when_the_holder_dies(tmp_path):
    started = mp.Event()
    holder = mp.Process(target=_hold, args=(str(tmp_path), started, 60))
    holder.start()
    assert started.wait(10)
    holder.kill()
    holder.join()
    t0 = time.monotonic()
    with browser_lock(Settings(state_dir=tmp_path), log=lambda _: None, poll_s=0.2):
        assert time.monotonic() - t0 < 2
