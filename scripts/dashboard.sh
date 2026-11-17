#!/usr/bin/env bash
# The dashboard in its own container on the HOST network (so http://127.0.0.1:8765 works, like the harness's noVNC), without touching the
# running agent container.
#
#   scripts/dashboard.sh start      build the image (first time) and start the dashboard   [DASHBOARD_MODE=readonly|interactive]
#   scripts/dashboard.sh url        print the address to open (it carries the access token)
#   scripts/dashboard.sh logs       follow its log
#   scripts/dashboard.sh stop       stop and remove it
#
# readonly (default): data/, state/, outputs/ and config/ are mounted read-only; you can look at everything, nothing can be changed.
# interactive: state/ and outputs/ are writable, so a question can be answered from the page and a run can be started from it (it reads
#   .env for the keys); approving or submitting is still never done from the page.
# Settings: JOBAGENT_HOME (folder with data/ state/ outputs/, default this checkout), DASHBOARD_PORT (8765), DASHBOARD_CONTAINER.
set -euo pipefail
cd "$(dirname "$0")/.."
HOME_DIR="$(cd "${JOBAGENT_HOME:-$PWD}" && pwd)"
PORT="${DASHBOARD_PORT:-8765}"
NAME="${DASHBOARD_CONTAINER:-jobagent-dashboard}"
MODE="${DASHBOARD_MODE:-readonly}"
TAG="jobagent-harness:dashboard-$(git rev-parse --short HEAD)"

case "${1:-start}" in
  start)
    docker image inspect "$TAG" >/dev/null 2>&1 || docker build --network host --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" -t "$TAG" .
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    if [ "$MODE" = "interactive" ]; then
      RW=rw; FLAGS=(); ENVF=(); [ -f "$HOME_DIR/.env" ] && ENVF=(--env-file "$HOME_DIR/.env")
      EXTRA=(-e OPENAI_API_KEY)
    else
      RW=ro; FLAGS=(--read-only); ENVF=(); EXTRA=()
    fi
    # The access token lives outside your data (so the read-only view can keep it across restarts and your bookmark keeps working).
    TOK_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/jobagent-dashboard"; mkdir -p "$TOK_DIR"; chmod 700 "$TOK_DIR"
    docker run -d --name "$NAME" --restart unless-stopped --network host --user "$(id -u):$(id -g)" -e HOME=/tmp -e DASHBOARD_TOKEN_FILE=/dash/token \
      -v "$TOK_DIR:/dash" "${ENVF[@]}" "${EXTRA[@]}" \
      -v "$HOME_DIR/data:/app/data:ro" -v "$HOME_DIR/state:/app/state:$RW" -v "$HOME_DIR/outputs:/app/outputs:$RW" -v "$HOME_DIR/config:/app/config:ro" \
      --entrypoint jobagent "$TAG" dashboard --host 127.0.0.1 --port "$PORT" "${FLAGS[@]}" >/dev/null
    sleep 3
    docker ps --filter "name=^${NAME}$" --format '{{.Names}} {{.Status}}'
    "$0" url
    ;;
  url)
    for _ in $(seq 20); do u="$(docker logs "$NAME" 2>&1 | grep -m1 -o 'http://[^ ]*?t=[^ ]*' || true)"; [ -n "$u" ] && break; sleep 1; done
    [ -n "${u:-}" ] && echo "$u" || { echo "dashboard is not up; see: docker logs $NAME" >&2; exit 1; }
    ;;
  logs) docker logs -f "$NAME" ;;
  stop) docker rm -f "$NAME" ;;
  *) sed -n 2,13p "$0"; exit 2 ;;
esac
