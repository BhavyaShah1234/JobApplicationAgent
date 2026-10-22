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

    def fake_scrape(page, url, hitl, log):
        time.sleep(1.0)
        jd = JobDescription(title="SWE", company="Acme", locations=["Remote"], basic_qualifications=["a", "b", "c"])
        return scrape_mod.ScrapeResult(jd, url[-1], tmp_path, "x", False, "main", 1000, False)

    monkeypatch.setattr(scrape_mod, "scrape", fake_scrape)
    cached = "https://example.com/jobs/cached"
    monkeypatch.setattr(scrape_mod, "already_scraped",
                        lambda url: JobDescription(title="Old", company="Acme") if url == cached else None)
    urls = [f"https://example.com/jobs/{i}" for i in range(4)] + [cached]
    t0 = time.monotonic()
    items = scrape_mod.scrape_many(urls, Hitl(tmp_path / "hitl", notify=lambda _: None), workers=4, log=lambda _: None)
    elapsed = time.monotonic() - t0
    get_settings.cache_clear()
    assert [bool(i.result) for i in items] == [True, True, True, True, False] and items[-1].cached
    assert not any(i.error for i in items)
    assert elapsed < 4.0 * 0.75 + 3  # parallel: ~1 s of work plus browser start-up, not 4 s sequential + overhead
