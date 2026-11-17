"""Step 17: the dashboard's data, its guards, and the two things it may do (answer a question, start a run up to the review screen)."""
import http.client
import json
import threading

import pytest

from jobagent.config import Settings
from jobagent.dashboard import data
from jobagent.dashboard.demo import seed
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
    tailor = next(r for r in data.runs(s) if r["id"].endswith("tailor-sample-cloud-bbbb2222"))
    assert tailor["errors"] == 1 and tailor["warnings"] == 1 and tailor["state"] == "finished" and not tailor["running"]
    live = next(r for r in data.runs(s) if r["id"].endswith("run-cccc3333"))
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


# --- the page (a built React app) ------------------------------------------------------------------------------------------------

def test_the_react_app_is_served_with_a_strict_content_policy(env):
    import re
    _, srv, _ = env
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    c.request("GET", "/", headers={"Cookie": f"jobagent_dashboard={srv.token}"})
    r = c.getresponse()
    html = r.read().decode()
    csp = r.getheader("Content-Security-Policy")
    assert r.status == 200 and '<div id="root">' in html and "<title>Job agent</title>" in html
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp and "frame-ancestors 'none'" in csp
    js = re.search(r'src="(/assets/[^"]+\.js)"', html).group(1)
    code, body = call(srv, "GET", js)
    assert code == 200 and "createRoot" in body or "react" in body.lower()
    css = re.search(r'href="(/assets/[^"]+\.css)"', html).group(1)
    assert call(srv, "GET", css)[0] == 200
    assert call(srv, "GET", js, cookie=False)[0] == 401                                  # the assets are behind the token as well


def test_assets_cannot_be_used_to_read_other_files(env):
    _, srv, _ = env
    for p in ("/assets/../server.py", "/assets/..%2fserver.py", "/assets/%2e%2e/data.py", "/assets/", "/assets/nope.js", "/static/index.html"):
        assert call(srv, "GET", p)[0] == 404, p


def test_the_front_end_reports_the_settings_it_needs(env):
    _, srv, _ = env
    assert call(srv, "GET", "/api/config") == (200, {"read_only": False, "novnc": ""})


def test_an_unbuilt_front_end_says_how_to_build_it(env, tmp_path, monkeypatch):
    import jobagent.dashboard.server as S
    _, srv, _ = env
    monkeypatch.setattr(S, "STATIC", tmp_path / "missing")
    code, html = call(srv, "GET", "/")
    assert code == 503 and "scripts/build_dashboard.sh" in html


def test_the_front_end_never_puts_agent_data_in_as_html():
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "frontend" / "src"
    banned = ("dangerouslySetInnerHTML", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function")
    hits = [f"{p.name}: {b}" for p in src.rglob("*.js*") if ".test." not in p.name for b in banned if b in p.read_text()]
    assert not hits, hits


def test_the_committed_build_matches_the_sources():
    """The static build is committed with the fingerprint of the sources it came from; a front-end change without a rebuild fails here."""
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("dsh", root / "scripts" / "dashboard_source_hash.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    recorded = (root / "src/jobagent/dashboard/static/source.sha256").read_text().strip()
    assert recorded == mod.fingerprint(root), "frontend/ changed after the last build: run scripts/build_dashboard.sh build"


# --- the pipeline view: where each job is, and which iteration ----------------------------------------------------------------------

def test_the_pipeline_view_shows_stage_iteration_and_progress_for_each_job(env):
    s, _, _ = env
    jobs = {j["job"]: j for j in data.pipeline(s)}
    assert set(jobs) == {"101", "102", "103", "104", "105"}
    a = jobs["101"]
    assert a["state"] == "running" and a["active"] and a["current"] == "documents" and a["title"] == "Robotics Engineer" and a["company"] == "Example Robotics"
    cur = next(x for x in a["stages"] if x["id"] == "documents")
    assert (cur["step"], cur["i"], cur["n"]) == ("ATS round", 2, 3) and [x["status"] for x in a["stages"]] == ["done", "done", "running", "pending"]
    assert 60 <= a["percent"] <= 75                                                    # 2 of 4 stages done plus two thirds of the third
    b = jobs["102"]
    assert b["current"] == "apply" and next(x for x in b["stages"] if x["id"] == "apply")["step"] == "Form page" and 70 <= b["percent"] <= 85
    assert jobs["103"]["state"] == "done" and jobs["103"]["percent"] == 100 and not jobs["103"]["active"]
    assert jobs["104"]["state"] == "failed" and jobs["104"]["current"] == "research"
    e = jobs["105"]
    assert e["state"] == "blocked" and "clearance" in next(x for x in e["stages"] if x["id"] == "research")["detail"]
    assert [j["job"] for j in data.pipeline(s)][:2] == ["102", "101"] or {j["job"] for j in data.pipeline(s)[:2]} == {"101", "102"}      # working jobs first


def test_a_stage_left_running_by_a_run_that_is_gone_shows_as_stalled(tmp_path):
    s = Settings(**seed(tmp_path))
    import os
    import time
    for p in (s.state_dir / "logs").glob("*aaaa0*.jsonl"):
        os.utime(p, (time.time() - 3600,) * 2)
    a = next(j for j in data.pipeline(s) if j["job"] == "101")
    assert a["state"] == "stalled" and not a["active"] and a["stages"][2]["status"] == "stalled"


def test_progress_records_carry_stage_iteration_and_job_and_plain_logs_ignore_them(tmp_path):
    import io
    from rich.console import Console
    from jobagent import log as L
    lg = L.RunLog("tailor", "job-1", console=Console(file=io.StringIO()), directory=tmp_path / "logs", quiet=False)
    L.progress(lg, "documents", "running", "ATS round", 2, 3, "average 84", url="https://x.test/1", job="job-9")
    L.progress(print, "documents", "running", "ignored")                              # a plain print or lambda is simply skipped
    L.progress(lambda m: None, "documents")
    lg.close()
    rec = json.loads(lg.path.with_suffix(".jsonl").read_text().splitlines()[0])
    assert rec["level"] == "progress" and rec["stage"] == "documents" and (rec["i"], rec["n"]) == (2, 3) and rec["job"] == "job-9" and rec["url"] == "https://x.test/1"
    assert "ATS round 2/3" in lg.path.read_text()


def test_the_pipeline_reports_each_stage_as_it_goes(tailor_world):
    from types import SimpleNamespace
    from jobagent import db
    from jobagent.config import get_settings
    from jobagent.tools import places
    from jobagent.tools.pipeline import Pipeline
    job, calls = tailor_world
    s = get_settings()
    c = db.connect(s.state_dir / "pipeline.sqlite")
    url = "https://e.test/progress"
    seen = []

    class Rec:
        def __call__(self, m): pass

        def progress(self, stage, status="running", label="", i=None, n=None, detail="", **extra):
            seen.append((stage, status, extra.get("plan"), extra.get("job")))

    def invoke(stage, **kw):
        if stage == "scrape":
            with c:
                c.execute("INSERT INTO jobs(url,company,job_id,status) VALUES(?,'Acme','1','scraped')", (url,))
        elif stage == "research":
            p = s.state_dir / "companies"; p.mkdir(exist_ok=True)
            (p / "acme.json").write_text('{"name":"Acme"}')
            (p / "acme.md").write_text("Acme profile")
        elif stage == "tailor":
            return Pipeline._invoke(stage, **kw)
        elif stage == "apply":
            out = job / "application"; out.mkdir(exist_ok=True)
            (out / "application.json").write_text(json.dumps({"state": "review", "ready_to_submit": True}))
        return 0
    pipeline = Pipeline(c, SimpleNamespace(state_dir=s.state_dir, outputs_dir=job.parents[1]), invoke=invoke, log=Rec())
    assert pipeline.run(url, no_ats=True).state == "filled"
    plan = ["scrape", "research", "documents", "apply"]
    assert [(a, b) for a, b, _, _ in seen] == [("scrape", "running"), ("scrape", "done"), ("research", "running"), ("research", "done"),
                                              ("documents", "running"), ("documents", "done"), ("apply", "running"), ("apply", "done")]
    assert all(p == plan for _, _, p, _ in seen) and seen[-1][3] == job.name


from test_workflow_resume import tailor_world  # noqa: E402,F401  (a fixture: a prepared job folder with a fake writer)


def test_the_token_can_live_outside_the_state_folder(tmp_path, monkeypatch):
    s = Settings(state_dir=tmp_path / "ro-state", outputs_dir=tmp_path, data_dir=tmp_path)
    f = tmp_path / "keep" / "token"
    monkeypatch.setenv("DASHBOARD_TOKEN_FILE", str(f))
    t = load_token(s)
    assert f.read_text().strip() == t and load_token(s) == t and not (tmp_path / "ro-state").exists()


def test_every_api_route_answers(env):
    _, srv, _ = env
    for path in ("overview", "queue", "applications", "review", "hitl", "runs", "outcomes", "pipeline", "config"):
        assert call(srv, "GET", f"/api/{path}")[0] == 200, path
    assert call(srv, "GET", "/api/run/does-not-exist")[0] == 404 and call(srv, "GET", "/api/run/..%2f..%2fjobs")[0] == 404


# --- the agent's browser (noVNC) inside the page --------------------------------------------------------------------------------------

def test_only_the_novnc_address_may_be_framed_and_its_reachability_is_reported(tmp_path):
    import socket
    s = Settings(**seed(tmp_path))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    srv = make_server(s, "127.0.0.1", 0, novnc_url=f"http://127.0.0.1:{port}/vnc.html")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("GET", "/api/vnc", headers={"Cookie": f"jobagent_dashboard={srv.token}"})
        r = c.getresponse()
        assert json.loads(r.read()) == {"url": f"http://127.0.0.1:{port}/vnc.html", "reachable": True}
        assert f"frame-src http://127.0.0.1:{port}" in r.getheader("Content-Security-Policy")
        listener.close()
        assert call(srv, "GET", "/api/vnc")[1]["reachable"] is False                         # nothing listens there any more
        assert call(srv, "GET", "/api/config")[1]["novnc"] == f"http://127.0.0.1:{port}/vnc.html"
    finally:
        srv.shutdown()
        srv.server_close()


def test_without_a_novnc_address_nothing_may_be_framed(env):
    _, srv, _ = env
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    c.request("GET", "/api/vnc", headers={"Cookie": f"jobagent_dashboard={srv.token}"})
    r = c.getresponse()
    assert json.loads(r.read()) == {"url": "", "reachable": False} and "frame-src 'none'" in r.getheader("Content-Security-Policy")
