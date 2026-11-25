"""User-approved connection to an ordinary, dedicated Chrome without restarting it."""
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit


def endpoint(raw):
    """Parse only a valid loopback debugging port and browser WebSocket path."""
    lines=raw.splitlines()
    if (len(lines)!=2 or not lines[0].isdigit() or not 1024<=int(lines[0])<=65535 or
            not re.fullmatch(r'/devtools/browser/[A-Za-z0-9-]+',lines[1])):
        raise RuntimeError('Invalid dedicated Chrome endpoint')
    return int(lines[0]),lines[1]


def validate_identity(root, manifest, proxy_port):
    """Verify the dedicated ordinary Chrome process, profile lock and public-only proxy."""
    from . import desktop_browser as D
    profile=root/'profile'
    if (manifest.get('protocol')!=D.PROTOCOL or manifest.get('kind')!='consented' or
            profile.is_symlink() or manifest.get('profile')!=str(profile.resolve()) or
            profile.resolve().parent!=root.resolve() or manifest.get('proxy_port')!=proxy_port or
            type(manifest.get('pid')) is not int or type(manifest.get('started_ns')) is not int):
        raise RuntimeError('Dedicated handoff profile identity mismatch')
    proc=Path('/proc')/str(manifest['pid'])
    try:
        args=D.process_arguments((proc/'cmdline').read_bytes())
        valid=(D.process_alive(manifest['pid']) and
               (proc/'exe').resolve().name in {'chrome','chromium','chromium-browser'} and
               f'--user-data-dir={profile.resolve()}' in args and
               not any('remote-debugging' in arg for arg in args) and
               D.proxy_arguments_ok(args,proxy_port) and
               os.readlink(profile/'SingletonLock').rsplit('-',1)[-1]==str(manifest['pid']))
    except (OSError,ValueError):valid=False
    if not valid:raise RuntimeError('The ordinary agent-owned Chrome process or network guard changed')
    return proc,profile


def owned_loopback_listener(proc, port):
    """Check that the exact browser process owns the loopback listening socket."""
    inodes=set()
    try:fds=list((proc/'fd').iterdir())
    except OSError:return False
    for fd in fds:
        try:target=os.readlink(fd)
        except FileNotFoundError:continue
        if target.startswith('socket:['):inodes.add(target[8:-1])
    for name in ('tcp','tcp6'):
        for row in (Path('/proc/net')/name).read_text().splitlines()[1:]:
            bits=row.split();address,raw_port=bits[1].split(':')
            if (int(raw_port,16)==port and bits[3]=='0A' and bits[9] in inodes and
                    address in ('0100007F','00000000000000000000000001000000')):return True
    return False


def connection_reply(root, manifest, proxy_port):
    """Host-side checks happen before exporting a short-lived endpoint over the private socket."""
    proc,profile=validate_identity(root,manifest,proxy_port)
    active=profile/'DevToolsActivePort'
    try:
        info=active.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mtime_ns<manifest['started_ns']:
            raise RuntimeError('Stale or unowned Chrome endpoint')
        port,path=endpoint(active.read_text())
    except FileNotFoundError:
        raise RuntimeError('Enable remote debugging in this Chrome at chrome://inspect/#remote-debugging, '
                           'close the inspect tab, then retry apply') from None
    if not owned_loopback_listener(proc,port):
        raise RuntimeError('The loopback debugging listener is not owned by this Chrome process')
    return {'ok':True,'protocol':manifest['protocol'],'url':manifest['url'],'guarded':True,
            'fresh':False,'handoff':True,'port':port,'websocket_path':path,'expires_at':time.time()+300,
            'view_url':manifest.get('view_url','')}


def prepare(root, url, proxy_port):
    """Open only an agent-owned profile, with no automation/debugging startup flags."""
    from . import desktop_browser as D
    if not D.public_destination(url):raise ValueError('Handoff requires a public HTTPS posting URL')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    store=root/'session.json';old=json.loads(store.read_text()) if store.exists() else None
    if old and old.get('url')!=url:raise RuntimeError('Another task owns this browser profile')
    if old and D.process_alive(old['pid']):
        validate_identity(root,old,proxy_port)
        return old
    profile=root/'profile'
    if profile.exists() and old is None:raise RuntimeError('Unrecognized profile; refusing to attach')
    if profile.is_symlink():raise RuntimeError('Refusing a redirected browser profile')
    chrome=D.shutil.which('google-chrome') or D.shutil.which('google-chrome-stable') or D.shutil.which('chromium')
    if not chrome:raise RuntimeError('Install desktop Chrome on the host')
    profile.mkdir(exist_ok=True,mode=0o700)
    lock=profile/'SingletonLock'
    if lock.is_symlink():
        owner=int(os.readlink(lock).rsplit('-',1)[-1])
        if D.process_alive(owner) or not old or owner!=old['pid']:
            raise RuntimeError('Another live browser owns this profile; close it before preparing a handoff')
        for name in ('SingletonLock','SingletonSocket','SingletonCookie'):(profile/name).unlink(missing_ok=True)
    (profile/'DevToolsActivePort').unlink(missing_ok=True)
    started=time.time_ns()
    command=[chrome,f'--user-data-dir={profile.resolve()}','--no-first-run','--no-default-browser-check',
             f'--proxy-server=http://127.0.0.1:{proxy_port}','--proxy-bypass-list=<-loopback>',url]
    with (root/'chrome.log').open('ab') as log:
        process=subprocess.Popen(command,stdout=log,stderr=log,start_new_session=True)
    manifest={'protocol':D.PROTOCOL,'kind':'consented','url':url,'pid':process.pid,
              'profile':str(profile.resolve()),'proxy_port':proxy_port,'started_ns':started,
              'view_url':os.environ.get('JOBAGENT_DESKTOP_VIEW_URL','')}
    store.write_text(json.dumps(manifest,indent=2));os.chmod(store,0o600)
    # A new process must not inherit the old process's tab approval. Site storage stays intact.
    (root/'approved.json').unlink(missing_ok=True)
    return manifest


def same_posting(requested, current):
    """Bind iCIMS job namespaces across /job, /login and /candidate routes."""
    from . import desktop_browser as D
    if D.origin(requested)!=D.origin(current):return False
    wanted=re.match(r'^/jobs/(\d+)(?:/|$)',unquote(urlsplit(requested).path))
    actual=re.match(r'^/jobs/(\d+)(?:/|$)',unquote(urlsplit(current).path))
    if wanted:return bool(actual and actual[1]==wanted[1])
    # Other URL schemes require an identical path and query for the first attachment.
    a,b=urlsplit(requested),urlsplit(current)
    return unquote(a.path).rstrip('/')==unquote(b.path).rstrip('/') and a.query==b.query
