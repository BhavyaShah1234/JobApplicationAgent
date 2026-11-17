"""Step 17: the dashboard's data, its guards, and the two things it may do (answer a question, start a run up to the review screen)."""
import http.client
import json
import threading

import pytest

from jobagent.config import Settings
from jobagent.dashboard import data
from jobagent.dashboard.demo import seed
from jobagent.dashboard.page import PAGE
from jobagent.dashboard.server import load_token, make_server, start_run


@pytest.fixture
def env(tmp_path):
    paths = seed(tmp_path)
    settings = Settings(**paths)
    started = []

    class Proc:
        pid = 4242

    def popen(cmd, **kw):
        started.append((cmd, kw))
        return Proc()
    srv = make_server(settings, "127.0.0.1", 0, popen=popen)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield settings, srv, started
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, headers=None, cookie=True, host=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    h = dict(headers or {})
    if cookie:
        h["Cookie"] = f"jobagent_dashboard={srv.token}"
    if host:
        h["Host"] = host
    if body is not None:
        h.setdefault("Content-Type", "application/json")
        h.setdefault("X-Dashboard", "1")
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw.decode()


# --- data ---------------------------------------------------------------------------------------------------------------------------

def test_the_data_layer_reads_the_agents_own_files(env):
    s, _, _ = env
    o = data.overview(s)
    assert o["jobs"]["queued"] == 2 and o["applications"]["filled"] == 2 and o["waiting"] == 2 and o["ready"] == 1 and o["questions"] == 2
    assert [r["company"] for r in data.review(s)] == ["Sample Cloud Co", "Example Robotics"]                  # oldest first
    assert any(r["state"] == "confirmed" and r["latest_stage"] == "interview" for r in data.applications(s))
    assert {r["step"] for r in data.runs(s)} == {"apply", "tailor", "run"}
    tailor = next(r for r in data.runs(s) if r["step"] == "tailor")
    assert tailor["errors"] == 1 and tailor["warnings"] == 1 and tailor["state"] == "finished" and not tailor["running"]
    live = next(r for r in data.runs(s) if r["step"] == "run")
    assert live["running"] and live["state"] == "running"
    assert data.outcomes(s)["counts"] == {"confirmation": 1, "interview": 1}


def test_an_empty_state_folder_gives_empty_lists_not_errors(tmp_path):
    s = Settings(state_dir=tmp_path / "s", outputs_dir=tmp_path / "o", data_dir=tmp_path / "d")
    assert data.queue(s) == [] and data.applications(s) == [] and data.review(s) == [] and data.runs(s) == [] and data.hitl_pending(s) == []
    assert data.overview(s)["jobs"] == {} and data.outcomes(s) == {"counts": {}, "recent": []}


def test_a_log_name_cannot_leave_the_logs_folder(env):
    s, _, _ = env
    assert data.run_log(s, "../jobs") is None and data.run_log(s, "a/b") is None and data.run_log(s, "") is None and data.run_log(s, "nope") is None
    assert data.run_log(s, data.runs(s)[0]["id"])


# --- the guards ---------------------------------------------------------------------------------------------------------------------

def test_every_request_needs_the_token_and_a_local_host_name(env):
    _, srv, _ = env
    assert call(srv, "GET", "/api/overview", cookie=False)[0] == 401
    assert call(srv, "GET", "/api/overview", host="evil.example")[0] == 403                                   # DNS rebinding
    assert call(srv, "GET", "/api/overview")[0] == 200
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1])
    c.request("GET", f"/?t={srv.token}")
    r = c.getresponse()
    assert r.status == 302 and "HttpOnly" in r.getheader("Set-Cookie") and "SameSite=Strict" in r.getheader("Set-Cookie")
    c.request("GET", "/?t=wrong")
    assert c.getresponse().status == 401


def test_the_token_file_is_private_and_stable(tmp_path):
    s = Settings(state_dir=tmp_path, outputs_dir=tmp_path, data_dir=tmp_path)
    t = load_token(s)
    assert load_token(s) == t and len(t) >= 24 and oct((tmp_path / "dashboard" / "token").stat().st_mode & 0o777) == "0o600"


def test_posts_refuse_cross_site_and_malformed_requests(env):
    _, srv, started = env
    body = {"inputs": ["Example Robotics"], "through": "apply", "limit": 2}
    assert call(srv, "POST", "/api/run", body, cookie=False)[0] == 401
    assert call(srv, "POST", "/api/run", body, headers={"X-Dashboard": ""})[0] == 403
    assert call(srv, "POST", "/api/run", body, headers={"Origin": "http://evil.example"})[0] == 403
    assert call(srv, "POST", "/api/run", body, headers={"Content-Type": "text/plain"})[0] == 403
    assert not started


def test_there_is_no_route_that_approves_or_submits(env):
    _, srv, started = env
    for path in ("/api/approve", "/api/submit", "/api/review/approve", "/api/applications/1/approve"):
        assert call(srv, "POST", path, {"id": "1"})[0] == 404
    assert not started


# --- the two actions ----------------------------------------------------------------------------------------------------------------

def test_answering_a_question_writes_the_answer_but_a_secret_is_refused(env):
    s, srv, _ = env
    code, out = call(srv, "POST", "/api/hitl/answer", {"id": "dem0a1b2", "value": "Yes"})
    assert code == 200 and out == {"answered": "dem0a1b2"}
    assert json.loads((s.state_dir / "hitl" / "answers" / "dem0a1b2.json").read_text()) == {"value": "Yes"}
    code, out = call(srv, "POST", "/api/hitl/answer", {"id": "dem0c3d4", "value": "hunter2"})
    assert code == 400 and "terminal" in out["error"] and not (s.state_dir / "hitl" / "answers" / "dem0c3d4.json").exists()
    assert call(srv, "POST", "/api/hitl/answer", {"id": "gone", "value": "x"})[0] == 400
    assert call(srv, "POST", "/api/hitl/answer", {"id": "dem0a1b2", "value": 5})[0] == 400


def test_a_started_run_stops_at_the_review_screen(env):
    s, srv, started = env
    code, out = call(srv, "POST", "/api/run", {"inputs": ["Example Robotics", "https://jobs.example.test/1"], "through": "apply", "limit": 3})
    assert code == 200 and out["pid"] == 4242
    cmd, kw = started[0]

    assert "submit" not in cmd and cmd[cmd.index("--through") + 1] == "apply" and cmd[cmd.index("--limit") + 1] == "3" and kw["start_new_session"]
    for bad in ({"inputs": ["x"], "through": "submit"}, {"inputs": ["--force"], "through": "apply"}, {"inputs": [], "through": "apply"},
                {"inputs": ["a"] * 11, "through": "apply"}, {"inputs": ["a\nb"], "through": "apply"}, {"inputs": ["x"], "through": "apply", "limit": 99}):
        assert call(srv, "POST", "/api/run", bad)[0] == 400
    assert len(started) == 1


def test_start_run_builds_an_argument_list_not_a_shell_line(env):
    s, _, _ = env
    seen = []
    start_run(s, ["Acme; rm -rf /"], "scrape", 1, popen=lambda cmd, **kw: seen.append(cmd) or type("P", (), {"pid": 1})())
    assert isinstance(seen[0], list) and "Acme; rm -rf /" in seen[0]


# --- the page -----------------------------------------------------------------------------------------------------------------------

def test_the_page_never_puts_agent_data_in_as_html():
    assert "innerHTML" not in PAGE and "outerHTML" not in PAGE and "insertAdjacentHTML" not in PAGE and "document.write" not in PAGE


def test_the_page_and_api_are_served(env):
    _, srv, _ = env
    code, html = call(srv, "GET", "/")
    assert code == 200 and "<title>Job agent</title>" in html and "__NOVNC__" not in html
    for path in ("overview", "queue", "applications", "review", "hitl", "runs", "outcomes"):
        assert call(srv, "GET", f"/api/{path}")[0] == 200
    assert call(srv, "GET", "/api/run/does-not-exist")[0] == 404 and call(srv, "GET", "/api/run/..%2f..%2fjobs")[0] == 404


def test_every_table_cell_is_wrapped_in_a_td():
    """The row builder must wrap each value in <td>; unwrapped values ran together on the page (found in a screenshot)."""
    assert 'cell(r).map(c=>h("td",{},c))' in PAGE


def test_run_states_follow_the_closing_record_and_how_long_the_log_has_been_quiet(tmp_path):
    import os
    import time
    s = Settings(state_dir=tmp_path / "s", outputs_dir=tmp_path / "o", data_dir=tmp_path / "d")
    logs = s.state_dir / "logs"
    logs.mkdir(parents=True)

    def make(name, quiet_s, closed, cmd="run", tag="skills"):
        recs = [{"ts": "2026-11-17T09:00:00Z", "run": "r", "cmd": cmd, "job": "j", "level": "info", "step": tag, "msg": "x"}]
        if closed:
            recs.append({"ts": "2026-11-17T09:01:00Z", "run": "r", "cmd": cmd, "job": "j", "level": "end", "step": cmd, "msg": "finished"})
        p = logs / f"{name}.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in recs))
        os.utime(p, (time.time() - quiet_s,) * 2)
    make("a", 5, False)
    make("b", 400, False)
    make("c", 4000, False)
    make("d", 4000, True)
    got = {r["id"]: r for r in data.runs(s)}
    assert [got[k]["state"] for k in "abcd"] == ["running", "waiting", "stalled", "finished"]
    assert got["a"]["step"] == "run" and got["a"]["lines"] == 1                      # the command, not the first line's tag ("skills")


def test_the_run_log_records_its_command_and_a_closing_line(tmp_path):
    import io
    from rich.console import Console
    from jobagent import log as L
    lg = L.RunLog("tailor", "job-1", console=Console(file=io.StringIO()), directory=tmp_path / "logs", quiet=False)
    lg("skills: 3 new skill(s)")
    lg.close()
    recs = [json.loads(x) for x in lg.path.with_suffix(".jsonl").read_text().splitlines()]
    assert recs[0]["cmd"] == "tailor" and recs[0]["step"] == "skills" and recs[-1]["level"] == "end"


def test_the_page_does_not_rebuild_under_someone_typing():
    assert "function busy()" in PAGE and "busy()?later(ms):draw()" in PAGE


# --- reaching it: read-only view, sign-in page, token without a writable state folder, port clashes -------------------------------------

def test_a_read_only_dashboard_shows_everything_and_changes_nothing(tmp_path):
    s = Settings(**seed(tmp_path))
    srv = make_server(s, "127.0.0.1", 0, popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not start a run")), read_only=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert call(srv, "GET", "/api/overview")[1]["read_only"] is True and call(srv, "GET", "/api/review")[0] == 200
        code, out = call(srv, "POST", "/api/hitl/answer", {"id": "dem0a1b2", "value": "Yes"})
        assert code == 403 and "read-only" in out["error"] and not (s.state_dir / "hitl" / "answers" / "dem0a1b2.json").exists()
        assert call(srv, "POST", "/api/run", {"inputs": ["x"], "through": "apply"})[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()


def test_without_the_token_you_get_a_page_that_says_how_to_get_the_address(env):
    _, srv, _ = env
    code, html = call(srv, "GET", "/", cookie=False)
    assert code == 401 and "access link" in html and "--print-url" in html and "docker logs jobagent-dashboard" in html


def test_a_state_folder_that_cannot_be_written_still_gets_a_token(tmp_path, monkeypatch):
    import jobagent.dashboard.server as S
    s = Settings(state_dir=tmp_path / "s", outputs_dir=tmp_path / "o", data_dir=tmp_path / "d")
    monkeypatch.setattr(S.os, "open", lambda *a, **k: (_ for _ in ()).throw(PermissionError("read-only file system")))
    t = load_token(s)
    assert len(t) >= 24 and load_token(s) == t                     # stable within the process, nothing written


def test_a_busy_port_gives_a_plain_message(env):
    from jobagent.dashboard.server import serve
    s, srv, _ = env
    with pytest.raises(SystemExit) as e:
        serve(s, "127.0.0.1", srv.server_address[1], echo=lambda m: None)
    assert "cannot listen" in str(e.value) and "--port" in str(e.value)


def test_print_url_prints_the_address_with_the_token(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from jobagent import main
    from jobagent.config import get_settings
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    r = CliRunner().invoke(main.app, ["dashboard", "--print-url", "--port", "8765"])
    assert r.exit_code == 0 and r.output.strip() == f"http://127.0.0.1:8765/?t={(tmp_path / 'dashboard' / 'token').read_text().strip()}"
    get_settings.cache_clear()


def test_the_launcher_and_entrypoint_are_valid_shell():
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for f in ("scripts/dashboard.sh", "docker/entrypoint.sh"):
        assert subprocess.run(["bash", "-n", str(root / f)], capture_output=True).returncode == 0, f
    assert "--read-only" in (root / "scripts/dashboard.sh").read_text() and ":ro" in (root / "scripts/dashboard.sh").read_text()
