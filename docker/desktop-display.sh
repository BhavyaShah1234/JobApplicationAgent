#!/bin/bash
# A separate GUI only; Chrome and the public-only browser broker stay on the host.
set -euo pipefail
export DISPLAY=:102 XAUTHORITY=/display/Xauthority
# Chrome connects from the host through the TCP relay, outside this IPC
# namespace. Shared-memory pixmaps cannot be exchanged over that connection.
Xvfb :102 -screen 0 1440x1000x24 -auth "$XAUTHORITY" -nolisten tcp -extension MIT-SHM > /tmp/xvfb.log 2>&1 &
for step in {1..100}; do
  if xdpyinfo -display :102 >/dev/null 2>&1; then break; fi
  sleep 0.1
done
xdpyinfo -display :102 >/dev/null
cat > /tmp/fluxbox-init <<'CFG'
session.screen0.rootCommand: true
session.screen0.workspaces: 1
session.screen0.toolbar.visible: false
CFG
fluxbox -display :102 -rc /tmp/fluxbox-init > /tmp/fluxbox.log 2>&1 &
python3 /display/x11relay.py > /tmp/xrelay.log 2>&1 &
x11vnc -display :102 -auth "$XAUTHORITY" -nopw -forever -shared \
  -rfbport 5903 -listen 127.0.0.1 -no6 > /tmp/x11vnc.log 2>&1 &
exec websockify --web=/usr/share/novnc 127.0.0.1:6083 127.0.0.1:5903
