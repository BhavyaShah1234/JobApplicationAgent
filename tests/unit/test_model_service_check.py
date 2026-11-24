"""The model service (Voyager, VPN-only) must be checked before a run: a disconnected VPN looked like a slow, stuck agent."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
import typer

from jobagent import llm, main


def serve(status):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a): pass
    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def settings(base):
    from pydantic import SecretStr
    return SimpleNamespace(openai_base_url=base, openai_api_key=SecretStr("k"))        # the real Settings holds a SecretStr


@pytest.mark.parametrize("status", [200, 401])
def test_a_service_that_answers_is_reachable_even_when_it_refuses_the_key(status):
    server, base = serve(status)
    try:
        r = llm.endpoint_status(settings(base))
        assert r["ok"] is True and r["seconds"] < 5
    finally:
        server.shutdown()


def test_nothing_listening_is_unreachable_and_says_what_failed():
    r = llm.endpoint_status(settings("http://127.0.0.1:1/v1"), timeout=2)
    assert r["ok"] is False and r["detail"]


def test_no_hosted_service_configured_needs_no_check():
    assert llm.endpoint_status(settings(""))["ok"] is True


def test_a_run_is_stopped_early_with_a_vpn_message_when_the_service_is_unreachable(monkeypatch, capsys):
    monkeypatch.setattr(llm, "endpoint_status", lambda *a, **k: {"ok": False, "seconds": 6.0, "detail": "URLError: timed out"})
    with pytest.raises(typer.Exit) as e:
        main._require_model_service("apply")
    assert e.value.exit_code == 4
    assert "VPN" in capsys.readouterr().out
    main._require_model_service("apply", skip=True)               # the explicit opt-out never checks


def test_a_reachable_service_lets_the_run_go_on(monkeypatch):
    monkeypatch.setattr(llm, "endpoint_status", lambda *a, **k: {"ok": True, "seconds": 0.3, "detail": "HTTP 200"})
    main._require_model_service("apply")
