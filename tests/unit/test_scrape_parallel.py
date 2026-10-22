import shutil
import threading
import time

import pytest

from jobagent.tools.memory import PortalMemory


def test_parallel_memory_updates_lose_nothing(tmp_path):
    def worker(i):
        m = PortalMemory("lever", tmp_path)
        m.note(f"note {i}")
        m.record_visit(f"https://jobs.lever.co/a/{i}", ok=True, content_selector=".posting-page")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    data = PortalMemory("lever", tmp_path).data
    assert data["visits"] == 8 and data["successes"] == 8
    assert sorted(n.split(": ", 1)[1] for n in data["notes"]) == sorted(f"note {i}" for i in range(8))


@pytest.mark.skipif(shutil.which("google-chrome") is None, reason="needs Chrome (run in the container)")
def test_scrape_many_runs_in_parallel_and_skips_cached(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    from jobagent.config import get_settings
    from jobagent.state import JobDescription
    from jobagent.tools import scrape as scrape_mod
    from jobagent.tools.hitl import Hitl

    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("OUTPUTS_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("VAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("HEADLESS", "true")
    get_settings.cache_clear()

    spans = []

    def fake_scrape(page, url, hitl, log):
        start = time.monotonic()
        time.sleep(1.0)
        spans.append((start, time.monotonic()))
        jd = JobDescription(title="SWE", company="Acme", locations=["Remote"], basic_qualifications=["a", "b", "c"])
        return scrape_mod.ScrapeResult(jd, url[-1], tmp_path, "x", False, "main", 1000, False)

    monkeypatch.setattr(scrape_mod, "scrape", fake_scrape)
    cached = "https://example.com/jobs/cached"
    monkeypatch.setattr(scrape_mod, "already_scraped",
                        lambda url: JobDescription(title="Old", company="Acme") if url == cached else None)
    urls = [f"https://example.com/jobs/{i}" for i in range(4)] + [cached]
    items = scrape_mod.scrape_many(urls, Hitl(tmp_path / "hitl", notify=lambda _: None), workers=4, log=lambda _: None)
    get_settings.cache_clear()
    assert [bool(i.result) for i in items] == [True, True, True, True, False] and items[-1].cached
    assert not any(i.error for i in items)
    # parallel: the four 1-second reads overlap (a timing-free check; Chrome start-up time varies with load)
    events = sorted([(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans])
    running, peak = 0, 0
    for _, delta in events:
        running += delta
        peak = max(peak, running)
    assert len(spans) == 4 and peak >= 2
