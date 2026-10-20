import threading
import time

import pytest

from jobagent.tools.hitl import SECRET_MARKER, Hitl, HitlTimeout


def _answer_when_pending(hitl: Hitl, value: str):
    def run():
        for _ in range(200):
            if hitl.list_pending():
                hitl.answer(None, value)
                return
            time.sleep(0.01)

    t = threading.Thread(target=run)
    t.start()
    return t


def test_ask_returns_user_answer(tmp_path):
    hitl = Hitl(tmp_path, notify=lambda _: None)
    t = _answer_when_pending(hitl, "bhavya.careers")
    assert hitl.ask("username?", poll_s=0.01) == "bhavya.careers"
    t.join()
    assert hitl.list_pending() == []


def test_empty_answer_uses_default(tmp_path):
    hitl = Hitl(tmp_path, notify=lambda _: None)
    t = _answer_when_pending(hitl, "")
    assert hitl.ask("gender?", default="Rather not say", poll_s=0.01) == "Rather not say"
    t.join()


def test_done_when_resolves_without_answer(tmp_path):
    hitl = Hitl(tmp_path, notify=lambda _: None)
    start = time.monotonic()
    assert hitl.ask("finish in noVNC", done_when=lambda: time.monotonic() - start > 0.05, poll_s=0.01) == ""
    assert hitl.list_pending() == []


def test_timeout(tmp_path):
    with pytest.raises(HitlTimeout):
        Hitl(tmp_path, notify=lambda _: None).ask("?", timeout_s=0.05, poll_s=0.01)


def test_secret_goes_to_vault_not_disk(tmp_path, monkeypatch):
    stored = {}

    class FakeVault:
        def set(self, name, **fields):
            stored[name] = fields

    monkeypatch.setattr("jobagent.tools.vault.open_vault", lambda: FakeVault())
    hitl = Hitl(tmp_path, notify=lambda _: None)
    t = _answer_when_pending(hitl, "abcd efgh ijkl mnop")
    assert hitl.ask("app password", secret=True, vault_target=["gmail", "app_password"], poll_s=0.01) == SECRET_MARKER
    t.join()
    assert stored == {"gmail": {"app_password": "abcd efgh ijkl mnop"}}
    assert not any("abcd" in p.read_text() for p in tmp_path.rglob("*.json"))
