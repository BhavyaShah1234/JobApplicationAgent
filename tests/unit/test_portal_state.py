"""Forgetting one portal's local state: scoped to its own domain, never Google, never a substitute for waiting out a lockout."""
import json
from types import SimpleNamespace

from jobagent.tools import portal_state as ps


class Ctx:
    def __init__(self):
        self.cleared, self.cdp_calls = [], []
        self._cookies = [{"domain": ".ultipro.com"}, {"domain": "signin-us.ultipro.com"}, {"domain": ".google.com"}, {"domain": "example.org"}]

    def cookies(self):
        return self._cookies

    def clear_cookies(self, domain=None):
        self.cleared.append(domain.pattern)

    def new_cdp_session(self, page):
        calls = self.cdp_calls
        return SimpleNamespace(send=lambda method, params: calls.append((method, params["origin"])))


def test_the_portals_own_hosts_are_cleared_and_nothing_else():
    ctx = Ctx()
    page = SimpleNamespace(context=ctx)
    r = ps.forget_portal(page, "https://recruiting2.ultipro.com/DOW1001DOWL/JobBoard/x",
                         ["recruiting2.ultipro.com", "signin-us.ultipro.com", "www.google.com", "other.example.org"])
    assert r["domain"] == "ultipro.com" and r["cookies_before"] == 2                   # not google.com's, not example.org's
    assert sorted(h for _, h in ctx.cdp_calls) == ["https://recruiting2.ultipro.com", "https://signin-us.ultipro.com"]
    assert all(m == "Storage.clearDataForOrigin" for m, _ in ctx.cdp_calls)
    assert "ultipro" in ctx.cleared[0] and "google" not in ctx.cleared[0]


def test_google_and_the_agents_mail_sign_in_can_never_be_cleared():
    ctx = Ctx()
    for url in ("https://accounts.google.com/signin", "https://mail.google.com/x", "https://www.gstatic.com/a"):
        r = ps.forget_portal(SimpleNamespace(context=ctx), url, [])
        assert r.get("refused") and not ctx.cleared and not ctx.cdp_calls, url


def test_a_clear_failure_is_reported_not_raised():
    ctx = Ctx()
    ctx.new_cdp_session = lambda page: (_ for _ in ()).throw(RuntimeError("no session"))
    r = ps.forget_portal(SimpleNamespace(context=ctx), "https://signin-us.ultipro.com/u/login", [])
    assert r["errors"] and r["cleared"] == []


def test_the_attempt_count_is_only_cleared_when_asked_and_a_lockout_page_never_clears_state(tmp_path, monkeypatch):
    from jobagent.tools import portal_agent as pa
    monkeypatch.setattr(pa, "_rejections_file", lambda host: tmp_path / f"{host}.json")
    pa.record_rejection("signin-us.ultipro.com", now=1.0)
    assert (tmp_path / "signin-us.ultipro.com.json").exists()
    assert ps.clear_attempts("signin-us.ultipro.com") is True and ps.clear_attempts("signin-us.ultipro.com") is False
    import inspect
    # the lockout / restriction handling never calls the clearing code: forgetting is a deliberate command, not a way past a limit
    assert "portal_state" not in inspect.getsource(pa) and "forget_portal" not in inspect.getsource(pa)
