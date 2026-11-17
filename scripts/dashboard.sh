#!/usr/bin/env bash
# Control the dashboard INSIDE the production harness, sharing its Chrome,
# display, credentials, profile, writable learned answers and current code.
# Usage: scripts/dashboard.sh start|url|logs|stop
set -euo pipefail
cd "$(dirname "$0")/.."
AGENT="${JOBAGENT_CONTAINER:-jobagent}"
PORT="${DASHBOARD_PORT:-8765}"
MODE="${DASHBOARD_MODE:-interactive}"
case "$MODE" in readonly|interactive) ;; *) echo 'DASHBOARD_MODE must be readonly or interactive' >&2; exit 2 ;; esac
case "${1:-start}" in
  start)
    docker inspect --format '{{.State.Running}}' "$AGENT" | grep -qx true || { echo 'Start the jobagent harness first.' >&2; exit 1; }
    # Keep an already healthy dashboard; never interrupt its worker to restart a viewer.
    if ! docker exec "$AGENT" python -c 'import http.client,sys; from jobagent.config import get_settings; from jobagent.dashboard.server import load_token; c=http.client.HTTPConnection("127.0.0.1",int(sys.argv[1]),timeout=2); c.request("GET","/api/config",headers={"Cookie":"jobagent_dashboard="+load_token(get_settings())}); assert c.getresponse().status==200' "$PORT" >/dev/null 2>&1; then
      flags=(); [ "$MODE" = readonly ] && flags+=(--read-only)
      docker exec -d "$AGENT" sh -c 'mkdir -p /app/state/dashboard /app/state/logs; echo $$ >/app/state/dashboard/server.pid; exec jobagent dashboard "$@" >/app/state/logs/dashboard.log 2>&1' dashboard \
        --host 127.0.0.1 --port "$PORT" --novnc-url "${NOVNC_URL:-http://localhost:6080/vnc.html}" "${flags[@]}"
    fi
    "$0" url
    ;;
  url) docker exec "$AGENT" jobagent dashboard --print-url --port "$PORT" ;;
  logs) docker exec "$AGENT" tail -f /app/state/logs/dashboard.log ;;
  stop)
    docker exec "$AGENT" python -c 'import os,signal; from pathlib import Path; p=Path("/app/state/dashboard/server.pid"); pid=int(p.read_text()); args=Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0"); assert b"dashboard" in args and any(a.endswith(b"/jobagent") for a in args), "PID is not the owned dashboard"; os.kill(pid,signal.SIGTERM); p.unlink(missing_ok=True)'
    ;;
  *) echo 'Usage: scripts/dashboard.sh start|url|logs|stop' >&2; exit 2 ;;
esac
