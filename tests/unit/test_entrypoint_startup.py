"""Exercise container startup without a display server, browser or real dashboard."""
import subprocess
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize('allowed_hosts', [None, '', 'laptop.example,phone.example'])
def test_dashboard_boots_without_optional_hosts_and_passes_configured_hosts(tmp_path, allowed_hosts):
    root = Path(__file__).resolve().parents[2]
    state, data = (tmp_path / name for name in ('state', 'data'))
    for path in (state, data):
        path.mkdir()
    # Redirect only persistent paths; execute the complete actual entrypoint.
    script = (root / 'docker/entrypoint.sh').read_text().replace('/app/state', str(state)).replace('/app/data', str(data))
    entrypoint = tmp_path / 'entrypoint.sh'
    entrypoint.write_text(script)
    # Shell functions also work when the test container mounts /tmp noexec.
    setup = '\n'.join(f'{name}() {{ :; }}; export -f {name};' for name in
                      ('Xvfb', 'xdpyinfo', 'fluxbox', 'x11vnc', 'websockify'))
    setup += '\njobagent() { printf "%s\\n" "$@" > "$DASHBOARD_RECORD.tmp"; '
    setup += 'mv "$DASHBOARD_RECORD.tmp" "$DASHBOARD_RECORD"; }; export -f jobagent; exec bash "$@"'
    record = tmp_path / 'dashboard-arguments.txt'
    env = {'PATH': '/bin:/usr/bin', 'DASHBOARD_PORT': '8765',
           'DASHBOARD_RECORD': str(record), 'DISPLAY': ':jobagent-entrypoint-test'}
    if allowed_hosts is not None:
        env['DASHBOARD_ALLOW_HOSTS'] = allowed_hosts
    run = subprocess.run(['bash', '-c', setup, 'entrypoint-test', str(entrypoint), '/bin/true'],
                         env=env, capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stdout + run.stderr
    deadline = time.monotonic() + 2
    while not record.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert record.exists(), (state / 'logs/dashboard.log').read_text()
    arguments = record.read_text().splitlines()
    assert arguments[:5] == ['dashboard', '--host', '127.0.0.1', '--port', '8765']
    assert arguments[7:] == ([] if not allowed_hosts else ['--allow-host', 'laptop.example', '--allow-host', 'phone.example'])
