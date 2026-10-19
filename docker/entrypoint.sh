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

echo "Harness ready: noVNC at http://localhost:${NOVNC_PORT}/vnc.html"
exec "$@"
