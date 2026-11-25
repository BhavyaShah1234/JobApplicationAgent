"""Dedicated desktop Chrome sessions for application URLs, shared with the Docker agent."""

import ipaddress
import json
import os
import shlex
import shutil
import signal
import socketserver
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

PROTOCOL = 2
ROOT = 'desktop-browser'


def process_alive(pid):
    try:
        status = Path(f'/proc/{pid}/status').read_text()
        return not any(line.startswith('State:') and ('Z (zombie)' in line or 'X (dead)' in line)
                       for line in status.splitlines())
    except OSError:
        return False


def agent_profile_root(root, url):
    import hashlib
    return root / 'agent-session' / hashlib.sha256(url.encode()).hexdigest()[:16]


def process_arguments(raw):
    args = [arg for arg in raw.decode().split('\0') if arg]
    return shlex.split(args[0]) if len(args) == 1 else args


def allowed(url):
    """Public HTTPS destinations only; never open personal profiles or local/browser URLs."""
    try:
        p = urlsplit(url)
        host = (p.hostname or '').lower().rstrip('.')
        if p.scheme != 'https' or not host or p.username or p.password or p.port not in (None, 443):
            return False
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            # Chrome accepts abbreviated IPv4 such as 127.1; do not treat numeric endings as DNS names.
            return '.' in host and not host.rsplit('.', 1)[-1].isdigit()
    except (TypeError, ValueError):
        return False


def host_is_public(host, resolver=None):
    """The network boundary for 'public only', at the level of a host name (any scheme or port): not a local / internal name,
    an IP literal that is globally routable, or a name whose EVERY resolved address is. A name that cannot be resolved is
    refused (fail closed): a guard that lets unverifiable destinations through is not a boundary. (The browser resolves on
    the host machine; where that differs from this process's view, add the origin with --desktop-allow-origin.)"""
    import socket
    host = (host or '').lower().rstrip('.')
    if not host or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        return False
    try:
        return ipaddress.ip_address(host.strip('[]')).is_global
    except ValueError:
        pass
    if '.' not in host or host.rsplit('.', 1)[-1].isdigit():
        return False
    try:
        infos = (resolver or socket.getaddrinfo)(host, 443)
        return bool(infos) and all(ipaddress.ip_address(i[4][0].split('%')[0]).is_global for i in infos)
    except (OSError, ValueError):
        return False


def resolves_public(url, resolver=None):
    return host_is_public(urlsplit(url).hostname, resolver)


def public_destination(url, resolver=None):
    return allowed(url) and resolves_public(url, resolver)


class PublicProxy(socketserver.ThreadingTCPServer):
    """The public-only network boundary, enforced where it cannot be bypassed: the dedicated Chrome is started with
    `--proxy-server` pointing here (and `--proxy-bypass-list=<-loopback>` so not even loopback skips it). Every connection the
    browser makes (the first navigation, each hop of a redirect chain, frames, sub-resources, new tabs, WebSockets) arrives as
    an HTTP CONNECT carrying the HOST NAME. The proxy resolves it itself, refuses unless EVERY address is globally routable
    (a name that does not resolve is refused: fail closed), and connects to the address it vetted, so a name cannot resolve
    differently between the check and the connection. Plain HTTP (no CONNECT) and ports other than 443 are refused.
    `resolver`, `connector` and `ports` are injectable for tests."""
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, resolver=None, connector=None, ports=(443,), port=0):
        super().__init__(('127.0.0.1', port), _ProxyHandler)
        self.resolver, self.connector, self.ports = resolver, connector, tuple(ports)
        self.refused: list[str] = []

    @property
    def port(self):
        return self.server_address[1]

    def vetted_address(self, host, port):
        import socket
        host = host.lower().rstrip('.').strip('[]')
        if not host or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            raise PermissionError('local name')
        try:
            ipaddress.ip_address(host)
            addrs = [host]
        except ValueError:
            try:
                infos = (self.resolver or socket.getaddrinfo)(host, port)
            except OSError as exc:
                raise PermissionError('unresolvable name') from exc
            addrs = [i[4][0].split('%')[0] for i in infos]
        if not addrs or not all(ipaddress.ip_address(a).is_global for a in addrs):
            raise PermissionError('not a public address')
        return addrs[0]


def start_proxy(root):
    """The broker's proxy, on the SAME port as last time when that port is free, so that browsers started before a bridge
    restart keep pointing at a live guard. If the old port cannot be had, a new one is used and the old browsers are refused
    (see `open_request`) instead of being trusted with a retired endpoint."""
    root.mkdir(parents=True, exist_ok=True)
    store = root / 'proxy.json'
    try:
        wanted = int(json.loads(store.read_text()).get('port', 0))
    except (OSError, ValueError, TypeError):
        wanted = 0
    try:
        proxy = PublicProxy(port=wanted)
    except OSError:
        proxy = PublicProxy()
    store.write_text(json.dumps({'port': proxy.port}))
    os.chmod(store, 0o600)
    return proxy


def proxy_arguments_ok(args, port):
    """Is this the command line of a Chrome started through the proxy on `port` (loopback not exempt)?"""
    return f'--proxy-server=http://127.0.0.1:{port}' in args and '--proxy-bypass-list=<-loopback>' in args


def open_request(root, request, proxy, launcher=None, alive=None, validate=None):
    """The broker's reply to an `open` request. A browser that already runs is reused only if the proxy it was started with
    IS this broker's live proxy (same port, and its command line says so); a browser bound to a retired proxy is refused with
    the restart it needs, never reported as guarded. `launcher`, `alive`, `validate` are injectable for tests."""
    if request.get('protocol') != PROTOCOL or request.get('action') not in ('open','handoff') or not public_destination(request.get('url', '')):
        raise ValueError('Expected a desktop application open request with a public HTTPS URL')
    browser_root = agent_profile_root(root, request['url'])
    if request['action']=='handoff':
        from .desktop_handoff import prepare
        prepare(browser_root,request['url'],proxy.port)
        return {'ok':True,'protocol':PROTOCOL,'url':request['url'],'prepared':True}
    path = browser_root / 'session.json'
    manifest = json.loads(path.read_text()) if path.exists() else None
    if manifest and manifest.get('kind')=='consented':
        if manifest.get('url')!=request['url']:raise RuntimeError('Another task owns this handoff')
        from .desktop_handoff import connection_reply
        return connection_reply(browser_root,manifest,proxy.port)
    fresh = False
    if manifest and (alive or process_alive)(manifest['pid']):
        (validate or validate_process)(browser_root, manifest)
        if manifest['url'] != request['url']:
            raise RuntimeError('Another application owns this browser')
        if manifest.get('proxy_port') != proxy.port:
            raise RuntimeError(f"This application's browser uses a retired network guard (proxy port {manifest.get('proxy_port')}, "
                               f"the bridge now guards {proxy.port}); close that Chrome window and open the application again")
    else:
        manifest = (launcher or launch)(browser_root, request['url'], proxy.port)
        fresh = True
    return {'ok': True, 'protocol': PROTOCOL, 'port': manifest['port'], 'url': manifest['url'], 'fresh': fresh,
            'guarded': manifest.get('proxy_port') == proxy.port,'view_url':manifest.get('view_url','')}


class _ProxyHandler(socketserver.StreamRequestHandler):
    def _reply(self, status):
        self.wfile.write(f'HTTP/1.1 {status}\r\nConnection: close\r\n\r\n'.encode())

    def handle(self):
        import select
        import socket
        server = self.server
        try:
            line = self.rfile.readline(8192).decode('latin1').split()
            while (h := self.rfile.readline(8192)) not in (b'\r\n', b'\n', b''):
                pass
            if len(line) < 2 or line[0].upper() != 'CONNECT':
                return self._reply('405 Only CONNECT is allowed (HTTPS only)')
            host, _, port = line[1].rpartition(':')
            port = int(port)
            if port not in server.ports:
                server.refused.append(line[1])
                return self._reply('403 Port not allowed')
            ip = server.vetted_address(host, port)
        except (PermissionError, ValueError) as exc:
            server.refused.append(line[1] if len(line) > 1 else '?')
            return self._reply(f'403 Refused: {exc}')
        try:
            upstream = (server.connector or (lambda ip, port: socket.create_connection((ip, port), timeout=20)))(ip, port)
        except OSError:
            return self._reply('502 Could not connect')
        self.wfile.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
        with upstream:
            sockets = [self.connection, upstream]
            try:
                while True:
                    ready, _, _ = select.select(sockets, [], [], 120)
                    if not ready:
                        return
                    for s in ready:
                        data = s.recv(65536)
                        if not data:
                            return
                        (upstream if s is self.connection else self.connection).sendall(data)
            except OSError:
                return


def origin(url):
    if not allowed(url):
        raise ValueError('Desktop applications require a public HTTPS URL')
    host = urlsplit(url).hostname.rstrip('.').encode('idna').decode('ascii').lower()
    return 'https://' + (f'[{host}]' if ':' in host else host)


def validate_process(root, manifest):
    profile = str((root / 'profile').resolve())
    if manifest.get('profile') != profile or manifest.get('protocol') != PROTOCOL:
        raise RuntimeError('Desktop browser identity mismatch')
    proc = Path(f"/proc/{manifest['pid']}")
    args = process_arguments((proc / 'cmdline').read_bytes())
    lock = Path(profile) / 'SingletonLock'
    active = (Path(profile) / 'DevToolsActivePort').read_text().splitlines()
    if (proc / 'exe').resolve().name not in {'chrome', 'chromium', 'chromium-browser'} or \
            f'--user-data-dir={profile}' not in args or '--remote-debugging-port=0' not in args or \
            not proxy_arguments_ok(args, manifest.get('proxy_port')) or \
            not lock.is_symlink() or os.readlink(lock).rsplit('-', 1)[-1] != str(manifest['pid']) or \
            int(active[0]) != manifest.get('port') or active[1] != manifest.get('websocket_path'):
        raise RuntimeError('Dedicated Chrome process, profile or debugging endpoint does not match')


def launch(root, url, proxy_port):
    if not public_destination(url):
        raise ValueError('Desktop applications require a public HTTPS URL')
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / 'session.json'
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if previous and process_alive(previous['pid']):
        raise RuntimeError('Dedicated browser is already running')
    profile = (root / 'profile').resolve()
    if profile.exists() and previous is None:
        raise RuntimeError('Unrecognized profile directory; refusing to use it')
    chrome = shutil.which('google-chrome') or shutil.which('google-chrome-stable') or shutil.which('chromium')
    if not chrome:
        raise RuntimeError('Install desktop Chrome on the host')
    profile.mkdir(exist_ok=True)
    lock = profile / 'SingletonLock'
    if lock.is_symlink():
        owner = int(os.readlink(lock).rsplit('-', 1)[-1])
        if process_alive(owner) or not previous or owner != previous['pid']:
            raise RuntimeError('Dedicated profile is owned by another Chrome; close it before resuming')
        for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
            (profile / name).unlink(missing_ok=True)
    (profile / 'DevToolsActivePort').unlink(missing_ok=True)
    # Every connection of this browser goes through the public-only proxy; loopback is not exempt from it.
    command = [chrome, f'--user-data-dir={profile}', '--remote-debugging-address=127.0.0.1',
               '--remote-debugging-port=0', '--no-first-run', '--no-default-browser-check',
               f'--proxy-server=http://127.0.0.1:{proxy_port}', '--proxy-bypass-list=<-loopback>', url]
    with (root / 'chrome.log').open('ab') as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
    # Persist ownership even if startup fails, so a later run cannot mistake this for a personal profile.
    manifest = {'protocol': PROTOCOL, 'pid': process.pid, 'profile': str(profile), 'url': url, 'proxy_port': proxy_port,
                'view_url':os.environ.get('JOBAGENT_DESKTOP_VIEW_URL','')}
    manifest_path.write_text(json.dumps(manifest, indent=2))
    os.chmod(manifest_path, 0o600)
    for _ in range(60):
        try:
            active = (profile / 'DevToolsActivePort').read_text().splitlines()
            manifest.update(port=int(active[0]), websocket_path=active[1])
            validate_process(root, manifest)
            manifest_path.write_text(json.dumps(manifest, indent=2))
            return manifest
        except (OSError, ValueError, IndexError, RuntimeError):
            if not process_alive(process.pid):
                raise RuntimeError('Dedicated Chrome exited during startup; inspect chrome.log')
            time.sleep(.25)
    raise RuntimeError('Dedicated Chrome debugging endpoint did not become ready; inspect chrome.log')


class Scope:
    """One task's lease on one browser tab (F07).

    * Top-level navigation is permitted only to `origins`: the requested URL's origin, explicitly trusted login / application
      origins (`related_urls`), the origins of application links PUBLISHED on the top-level page, and destinations approved
      earlier for this same task (persisted in `store`, so a resumed session keeps them).
    * A FRESH session may land on a redirect target of the requested URL (the posting redirecting to its ATS): that is the one
      time the tab's current origin is approved without having been requested. A REUSED session gets no such grace: its tab
      must already be on an approved origin and, when a target id was recorded, be the very same browser target; an unrelated
      public site left in a reused tab is refused before anything is planned or filled.
    * Frames never grant navigation. An embedded form's origin is recorded as a frame origin only; it needs to be a public
      destination to be tolerated, but it does not become a place the tab may navigate to.
    * 'Public' is enforced on the resolved addresses as well as on the text of the URL."""
    def __init__(self, context, page, url, related_urls=(), *, fresh=True, store=None, target_id=None, resolver=None):
        from jobagent.tools.challenge import Blocked
        self.context, self.page, self.store, self.resolver = context, page, store, resolver
        self.requested = {origin(u) for u in (url, *related_urls) if public_destination(u, resolver)}
        if not self.requested:
            raise Blocked('The requested application URL is not a public HTTPS destination')
        lease = {}
        if store is not None and not fresh:
            try:
                lease = json.loads(Path(store).read_text())
            except (OSError, ValueError):
                lease = {}
            if lease.get('url') != url:
                lease = {}
        self.origins = set(self.requested) | set(lease.get('origins', []))
        self.frame_origins = set()
        if lease.get('target_id') and (not target_id or lease['target_id'] != target_id):
            # a recorded target needs a freshly verified current one: a failed lookup is not evidence of the same tab
            raise Blocked('This tab is not the browser target this application was opened in, or its identity could not '
                          'be verified; application stopped')
        current = origin(page.url) if public_destination(page.url, resolver) else None
        if current is None:
            raise Blocked('The application tab is not on a public HTTPS destination')
        if current not in self.origins:
            if not fresh:
                raise Blocked('The reused browser tab is on a site unrelated to this application '
                              f'({current}); open the application again or add the trusted origin with --desktop-allow-origin')
            self.origins.add(current)             # a fresh session follows the posting's ordinary initial redirects
        self.url, self.target_id = url, target_id or lease.get('target_id')
        self._save()

    def _save(self):
        if self.store is None:
            return
        try:
            path = Path(self.store)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'url': self.url, 'origins': sorted(self.origins), 'target_id': self.target_id}, indent=2))
            os.chmod(path, 0o600)
        except OSError:
            pass

    def __call__(self, page):
        from jobagent.tools.challenge import Blocked
        if page is not self.page or page.context is not self.context or \
                len(self.context.pages) != 1 or page not in self.context.pages:
            raise Blocked('Unexpected desktop tab or context; application stopped')
        if not public_destination(page.url, self.resolver) or origin(page.url) not in self.origins:
            raise Blocked('Navigation left this application’s known origins; add the trusted origin with '
                          '--desktop-allow-origin before resuming')
        main = getattr(page, 'main_frame', None) or (page.frames[0] if page.frames else None)
        before = set(self.origins)
        for frame in page.frames:
            if frame.url.startswith(('https://', 'http://')) and not public_destination(frame.url, self.resolver):
                raise Blocked('Application contains an unsupported frame destination')
            if allowed(frame.url):
                self.frame_origins.add(origin(frame.url))      # tolerated, never a place the tab may navigate to
            if frame is not main:
                continue                                       # only the top-level page publishes application links
            try:
                links = frame.locator('a[href], form[action]').evaluate_all('''es=>es.filter(e=>
                    e.tagName==='FORM'||/apply|application|candidate|sign.?in|log.?in|continue|next|register|create.{0,20}account/i.test(
                    e.innerText||e.getAttribute('aria-label')||'')).map(e=>e.href||e.action)''')
                self.origins.update(origin(u) for u in links if public_destination(u, self.resolver))
            except Exception:
                pass  # a transitioning frame is handled by the challenge/readiness controller
        if self.origins != before:
            self._save()


def serve(root):
    """Owner-only local broker. Each posting owns a separate profile, process and loopback port."""
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    def stop(*args):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    root.mkdir(parents=True, exist_ok=True)
    socket_path = root / 'bridge.sock'
    if socket_path.exists():
        raise RuntimeError('Desktop bridge socket already exists; use or stop the running bridge')

    proxy = start_proxy(root)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                reply = open_request(root, json.loads(self.rfile.readline(16384)), proxy)
            except Exception as exc:
                reply = {'ok': False, 'error': str(exc)}
            self.wfile.write((json.dumps(reply) + '\n').encode())

    try:
        with socketserver.UnixStreamServer(str(socket_path), Handler) as server:
            os.chmod(socket_path, 0o600)
            print(f'Desktop application bridge ready: {socket_path}', flush=True)
            server.serve_forever()
    finally:
        socket_path.unlink(missing_ok=True)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['serve','handoff'])
    parser.add_argument('url',nargs='?',help='Public posting URL for an ordinary-Chrome handoff')
    args=parser.parse_args()
    # The host bridge intentionally requires only Python's standard library.
    root=Path.cwd()/'state'/ROOT
    if args.stage=='serve':
        serve(root)
        return
    if not args.url or not public_destination(args.url):parser.error('handoff requires a public HTTPS posting URL')
    import socket
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(25);client.connect(str(root/'bridge.sock'))
        client.sendall((json.dumps({'protocol':PROTOCOL,'action':'handoff','url':args.url})+'\n').encode())
        reply=json.loads(client.makefile('rb').readline(8192))
    if not reply.get('ok') or reply.get('protocol')!=PROTOCOL or reply.get('url')!=args.url or not reply.get('prepared'):
        raise RuntimeError(reply.get('error','Handoff preparation failed; restart the updated host desktop bridge'))
    print('Ordinary agent-owned Chrome is open. Reach the application/profile form manually.\n'
          'In a new tab, open chrome://inspect/#remote-debugging and enable remote debugging; close that tab.\n'
          'Then run jobagent apply with --browser-mode desktop, and click Allow when Chrome asks.\n'
          'Do not submit during a filling-only test. Handoff never bypasses Chrome permission or human verification.')


if __name__ == '__main__':
    main()
