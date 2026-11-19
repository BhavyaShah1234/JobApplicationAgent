#!/usr/bin/env bash
# Start the virtual display + VNC/noVNC (localhost only), seed user data files, then run CMD.
set -euo pipefail

: "${DISPLAY:=:99}"
: "${SCREEN_GEOMETRY:=1920x1080x24}"
: "${VNC_PORT:=5900}"
: "${NOVNC_PORT:=6080}"
LOG_DIR=/app/state/logs
mkdir -p "$LOG_DIR"

# A container restart leaves stale X locks and Chrome singleton files behind.
rm -f "/tmp/.X${DISPLAY#:}-lock" "/tmp/.X11-unix/X${DISPLAY#:}"
rm -f /app/state/chrome-profile/Singleton{Lock,Socket,Cookie}
# No agent run survives a restart, so pending HITL requests are stale. (PID-based pruning alone
# can't tell: PIDs restart from 1 in every new container and get reused.)
rm -f /app/state/hitl/pending/*.json /app/state/hitl/answers/*.json

Xvfb "$DISPLAY" -screen 0 "$SCREEN_GEOMETRY" -nolisten tcp >"$LOG_DIR/xvfb.log" 2>&1 &
for _ in $(seq 50); do xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break; sleep 0.1; done

fluxbox >"$LOG_DIR/fluxbox.log" 2>&1 &
# -localhost: VNC is reachable only from this host (host networking), never the LAN.
x11vnc -display "$DISPLAY" -rfbport "$VNC_PORT" -localhost -forever -shared -nopw -quiet \
    >"$LOG_DIR/x11vnc.log" 2>&1 &
websockify --web /usr/share/novnc "127.0.0.1:${NOVNC_PORT}" "localhost:${VNC_PORT}" \
    >"$LOG_DIR/novnc.log" 2>&1 &

# Seed user-editable data files from the committed examples, never overwriting.
for example in /app/data/*.example.* /app/data/templates/*.example.*; do
    [ -e "$example" ] || continue
    target="${example/.example/}"
    [ -e "$target" ] || cp "$example" "$target"
done

# Optional scheduled outcome sync (step 15 / 27): SYNC_INBOX_EVERY_HOURS=6 reads the mailbox for replies to submitted applications every
# 6 hours. Off by default (unset or 0). It is read-only (nothing is trashed, labelled or marked read), is skipped while another sync is
# running (the sync takes its own lock), and a failure is logged and retried at the next interval, never fatal to the container.
if [ "${SYNC_INBOX_EVERY_HOURS:-0}" != "0" ]; then
    (
        sleep 120
        while true; do
            flock -n /app/state/sync-inbox.lock jobagent sync-inbox >>"$LOG_DIR/sync-inbox.log" 2>&1 || echo "$(date -u +%FT%TZ) sync-inbox failed (exit $?)" >>"$LOG_DIR/sync-inbox.log"
            sleep "$(python -c "print(int(float('${SYNC_INBOX_EVERY_HOURS}') * 3600))")"
        done
    ) &
    echo "Outcome sync scheduled every ${SYNC_INBOX_EVERY_HOURS} h (log: $LOG_DIR/sync-inbox.log)"
fi

# Optional dashboard (step 17): DASHBOARD_PORT=8765 serves it on 127.0.0.1 with the harness (host networking), read-write for answering questions and
# starting runs; DASHBOARD_READ_ONLY=1 for a view only. The address with its access token is in $LOG_DIR/dashboard.log.
if [ -n "${DASHBOARD_PORT:-}" ]; then
    dashboard_flags=()
    case "${DASHBOARD_READ_ONLY:-0}" in 1|true|yes) dashboard_flags+=(--read-only) ;; esac
    for h in ${DASHBOARD_ALLOW_HOSTS//,/ }; do dashboard_flags+=(--allow-host "$h"); done     # e.g. a Tailscale name; empty = this machine only
    mkdir -p /app/state/dashboard
    jobagent dashboard --host 127.0.0.1 --port "$DASHBOARD_PORT" --novnc-url "http://localhost:${NOVNC_PORT}/vnc.html" "${dashboard_flags[@]}" >"$LOG_DIR/dashboard.log" 2>&1 &
    echo "$!" >/app/state/dashboard/server.pid
    echo "Dashboard: http://127.0.0.1:${DASHBOARD_PORT}/ (access address: jobagent dashboard --print-url --port ${DASHBOARD_PORT})"
fi

echo "Harness ready: noVNC at http://localhost:${NOVNC_PORT}/vnc.html"
exec "$@"
