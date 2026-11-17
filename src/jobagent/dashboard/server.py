"""The dashboard's web server: standard library only (no new dependency), bound to this machine, behind a token.

- Every request needs the token (first visit `/?t=<token>` sets an HttpOnly, SameSite=Strict cookie) and a Host header that names this machine,
  so another website cannot read it or drive it (DNS rebinding, cross-site requests).
- GET serves the page and read-only JSON (`/api/overview`, `queue`, `applications`, `review`, `hitl`, `runs`, `run/<id>`, `outcomes`).
- POST does two things only, and only with the cookie, a JSON body and the `X-Dashboard: 1` header: answer a question the agent is waiting on
  (never a secret: those are answered in a terminal) and start `jobagent run` up to `apply`. There is no route that approves or submits.
"""

import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jobagent.dashboard import data
from jobagent.dashboard.page import PAGE

COOKIE = "jobagent_dashboard"
THROUGH = ("scrape", "tailor", "apply")          # a started run never goes past the review screen
MAX_BODY = 64_000
SIGN_IN = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Job agent</title>
<body style="font:16px system-ui;max-width:640px;margin:48px auto;padding:0 16px;line-height:1.5">
<h1 style="font-size:20px">The dashboard is running, but this page needs its access link</h1>
<p>The address ends in <code>?t=</code> and a long token. It was printed when the dashboard started:</p>
<pre style="background:#eee;padding:10px;overflow:auto">docker logs jobagent-dashboard 2>&amp;1 | grep "dashboard: http"</pre>
<p>or, when it was started inside the agent container:</p>
<pre style="background:#eee;padding:10px;overflow:auto">docker exec jobagent jobagent dashboard --print-url</pre>
<p>Open that address once; your browser then remembers it.</p></body>"""


_EPHEMERAL: dict[str, str] = {}


def load_token(settings) -> str:
    """The saved token (state/dashboard/token, or $DASHBOARD_TOKEN_FILE; readable by you only), made on first use so a bookmarked URL keeps working. When the state
    folder cannot be written (a read-only view of live data) the token lives only for this process and is printed at start."""
    p = Path(os.environ["DASHBOARD_TOKEN_FILE"]) if os.environ.get("DASHBOARD_TOKEN_FILE") else settings.state_dir / "dashboard" / "token"
    d = p.parent
    try:
        t = p.read_text().strip()
        if len(t) >= 16:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(24)
    try:
        d.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as h:
            h.write(t)
        return t
    except OSError:
        return _EPHEMERAL.setdefault(str(p), t)


def start_run(settings, inputs, through: str, limit: int, popen=subprocess.Popen) -> dict:
    """`jobagent run INPUTS --through ... --limit ...` as a detached process. ValueError for anything not allowed."""
    if through not in THROUGH:
        raise ValueError(f"through must be one of {', '.join(THROUGH)} (the dashboard never submits)")
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 10:
        raise ValueError("give between 1 and 10 inputs (job URLs, company names or a file name)")
    clean = []
    for x in inputs:
        x = str(x).strip()
        if not x or len(x) > 500 or "\n" in x or "\r" in x or x.startswith("-"):
            raise ValueError(f"not an acceptable input: {x[:40]!r}")
        clean.append(x)
    limit = int(limit)
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    exe = shutil.which("jobagent")
    cmd = [exe] if exe else [sys.executable, "-m", "jobagent.main"]
    cmd += ["run", *clean, "--through", through, "--limit", str(limit)]
    proc = popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                 cwd=str(settings.state_dir.parent) if settings.state_dir.parent.is_dir() else None)
    return {"pid": getattr(proc, "pid", 0), "command": " ".join(cmd), "note": "watch it under Runs; it stops at the review screen"}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, settings, token: str, allowed_hosts: set[str], popen=subprocess.Popen, novnc_url: str = "", read_only: bool = False):
        super().__init__(addr, Handler)
        self.settings, self.token, self.allowed_hosts, self.popen, self.novnc_url = settings, token, allowed_hosts, popen, novnc_url
        self.read_only = read_only
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "jobagent-dashboard"

    def log_message(self, *_):                       # no access log on the terminal
        pass

    # --- guards ----------------------------------------------------------------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        name = host[1:].split("]")[0] if host.startswith("[") else host.rsplit(":", 1)[0]
        return name.lower() in self.server.allowed_hosts

    def _cookie_ok(self) -> bool:
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and hmac.compare_digest(v, self.server.token):
                return True
        return False

    def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    # --- GET -------------------------------------------------------------------------------------------------------------------
    def do_GET(self) -> None:
        if not self._host_ok():
            return self._send(403, b"forbidden host", "text/plain")
        url = urlparse(self.path)
        if url.path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        q = parse_qs(url.query)
        if url.path == "/" and "t" in q and hmac.compare_digest(q["t"][0], self.server.token):
            return self._send(302, b"", "text/plain", {"Location": "/", "Set-Cookie": f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Strict; Path=/"})
        if not self._cookie_ok():
            return self._send(401, SIGN_IN.encode(), "text/html; charset=utf-8")
        s = self.server.settings
        if url.path == "/":
            return self._send(200, PAGE.replace("__NOVNC__", json.dumps(self.server.novnc_url).replace("<", "\\u003c")).encode(), "text/html; charset=utf-8")
        routes = {"/api/overview": lambda: {**data.overview(s), "read_only": self.server.read_only}, "/api/queue": lambda: data.queue(s), "/api/applications": lambda: data.applications(s),
                  "/api/review": lambda: data.review(s), "/api/hitl": lambda: data.hitl_pending(s), "/api/runs": lambda: data.runs(s),
                  "/api/outcomes": lambda: data.outcomes(s), "/api/pipeline": lambda: data.pipeline(s)}
        if url.path in routes:
            return self._json(routes[url.path]())
        if url.path.startswith("/api/run/"):
            rows = data.run_log(s, url.path[len("/api/run/"):])
            return self._json(rows, 200) if rows is not None else self._json({"error": "no such run"}, 404)
        self._json({"error": "not found"}, 404)

    # --- POST ------------------------------------------------------------------------------------------------------------------
    def do_POST(self) -> None:
        if not self._host_ok():
            return self._send(403, b"forbidden host", "text/plain")
        if not self._cookie_ok():
            return self._json({"error": "not signed in"}, 401)
        if self.server.read_only:
            return self._json({"error": "this dashboard is read-only: answer questions and start runs from a terminal"}, 403)
        origin = self.headers.get("Origin")
        if self.headers.get("X-Dashboard") != "1" or not (self.headers.get("Content-Type") or "").startswith("application/json") or (
                origin and urlparse(origin).hostname not in self.server.allowed_hosts):
            return self._json({"error": "cross-site or malformed request refused"}, 403)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_BODY:
                return self._json({"error": "body too large or empty"}, 400)
            body = json.loads(self.rfile.read(n))
            if not isinstance(body, dict):
                raise ValueError("expected an object")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json({"error": str(exc)}, 400)
        s, path = self.server.settings, urlparse(self.path).path
        try:
            if path == "/api/hitl/answer":
                return self._json(self._answer(s, body))
            if path == "/api/run":
                with self.server.lock:
                    return self._json(start_run(s, body.get("inputs"), str(body.get("through", "apply")), body.get("limit", 5), self.server.popen))
        except (ValueError, LookupError) as exc:
            return self._json({"error": str(exc)}, 400)
        self._json({"error": "not found"}, 404)

    @staticmethod
    def _answer(settings, body: dict) -> dict:
        from jobagent.tools.hitl import Hitl
        rid, value = str(body.get("id", "")), body.get("value")
        if not rid or not isinstance(value, str) or len(value) > 5000:
            raise ValueError("give the question id and a text answer (at most 5000 characters)")
        h = Hitl(settings.state_dir / "hitl", lambda m: None)
        req = next((r for r in h.list_pending() if r.id == rid), None)
        if req is None:
            raise LookupError("that question is not waiting any more")
        if req.secret:
            raise ValueError("this question asks for a secret: answer it in a terminal (`jobagent hitl answer " + rid + "`), never in a web page")
        h.answer(rid, value)
        return {"answered": rid}


def make_server(settings, host: str = "127.0.0.1", port: int = 8765, token: str | None = None, popen=subprocess.Popen,
                extra_hosts: tuple[str, ...] = (), novnc_url: str = "", read_only: bool = False) -> Server:
    allowed = {"127.0.0.1", "localhost", "::1", host.lower(), *(h.lower() for h in extra_hosts)}
    return Server((host, port), settings, token or load_token(settings), allowed, popen, novnc_url, read_only)


def access_url(srv: Server) -> str:
    host = srv.server_address[0]
    return f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{srv.server_address[1]}/?t={srv.token}"


def serve(settings, host: str = "127.0.0.1", port: int = 8765, extra_hosts: tuple[str, ...] = (), novnc_url: str = "", echo=print,
          read_only: bool = False) -> None:
    try:
        srv = make_server(settings, host, port, extra_hosts=extra_hosts, novnc_url=novnc_url, read_only=read_only)
    except OSError as exc:
        raise SystemExit(f"dashboard: cannot listen on {host}:{port} ({exc.strerror or exc}); is another dashboard already running? try --port") from exc
    echo(f"dashboard: {access_url(srv)}")
    echo("dashboard: " + ("READ-ONLY view" if read_only else "read-only except answering questions and starting runs up to the review screen")
         + "; open the address above in your browser; Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
