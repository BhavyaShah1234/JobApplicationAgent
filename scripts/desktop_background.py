"""Start the host browser bridge on an isolated, locally viewable desktop.

Run from the canonical checkout after stopping the previous host bridge.
Only the GUI runs in the auxiliary container; ordinary Chrome runs on the host.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

PROJECT=Path(__file__).resolve().parents[1]
NAME='jobagent-desktop-display'
VIEW='http://127.0.0.1:6083/vnc.html'


def write_private(path, text):
    """Write owner-only display metadata and assets."""
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:f.write(text)
    path.chmod(0o600)


def display_environment(directory, original=None):
    """Keep display and desktop-session integration separate from the user's GUI."""
    env=dict(os.environ if original is None else original)
    for key in ('WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS','DBUS_SESSION_BUS_PID'):
        env.pop(key,None)
    runtime=directory/'runtime';runtime.mkdir(exist_ok=True,mode=0o700)
    env.update(DISPLAY='127.0.0.1:102',XAUTHORITY=str(directory/'Xauthority'),
               XDG_RUNTIME_DIR=str(runtime),JOBAGENT_DESKTOP_VIEW_URL=VIEW)
    return env


def owned(container, record, directory):
    """Require the recorded service ID, UID and exact isolated-display mount."""
    return bool(record.get('id')==container.get('Id') and record.get('project')==str(PROJECT) and
                container.get('HostConfig',{}).get('NetworkMode')=='host' and
                container.get('Config',{}).get('User')==f'{os.getuid()}:{os.getgid()}' and
                any(m.get('Destination')=='/display' and m.get('Source')==str(directory) and not m.get('RW')
                    for m in container.get('Mounts',[])))


def vnc_ready():
    """Require the desktop's RFB server, not just its static noVNC web page."""
    try:
        with socket.create_connection(('127.0.0.1',5903),timeout=2) as client:
            return client.recv(12).startswith(b'RFB ')
    except OSError:
        return False


def ensure_display(directory):
    """Reuse only our running GUI service; recreate our stopped service with current assets."""
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    auth=directory/'Xauthority'
    if auth.is_symlink():raise RuntimeError('Refusing a redirected Xauthority file')
    if not auth.exists():
        write_private(auth,'')
        subprocess.run(['xauth','-f',str(auth),'source','-'],
            input=('add 127.0.0.1:102 MIT-MAGIC-COOKIE-1 '+secrets.token_hex(16)+'\n').encode(),
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True)
        records=subprocess.check_output(['xauth','-f',str(auth),'nlist'])
        wildcard=b'\n'.join(b'ffff'+line[4:] for line in records.splitlines())+b'\n'
        subprocess.run(['xauth','-f',str(auth),'nmerge','-'],input=wildcard,
                       stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True)
    if auth.stat().st_uid!=os.getuid() or auth.stat().st_mode&0o777!=0o600:
        raise RuntimeError('Display authentication must be owned by this user with mode0600')
    for source,target in [('desktop-display.sh','start.sh'),('x11relay.py','x11relay.py')]:
        write_private(directory/target,(PROJECT/'docker'/source).read_text())
    record_path=directory/'owner.json'
    record=json.loads(record_path.read_text()) if record_path.exists() else {}
    found=subprocess.run(['docker','inspect',NAME],capture_output=True,text=True)
    container=json.loads(found.stdout)[0] if found.returncode==0 else None
    if container and not owned(container,record,directory):
        raise RuntimeError('An unrecognized container owns the display name; it was left untouched')
    if container and not container['State']['Running']:
        subprocess.run(['docker','rm',NAME],check=True,stdout=subprocess.DEVNULL);container=None
    if container is None:
        for port in (5903,6083,6102):
            with socket.socket() as probe:probe.bind(('127.0.0.1',port))
        subprocess.run(['docker','image','inspect','jobagent-harness:latest'],check=True,stdout=subprocess.DEVNULL)
        ident=subprocess.check_output(['docker','run','-d','--name',NAME,'--init','--network','host',
            '--user',f'{os.getuid()}:{os.getgid()}','--shm-size','256m',
            '--mount',f'type=bind,src={directory},dst=/display,readonly',
            '--label','jobagent.role=isolated-desktop','--entrypoint','bash',
            'jobagent-harness:latest','/display/start.sh'],text=True).strip()
        write_private(record_path,json.dumps({'id':ident,'project':str(PROJECT)},indent=2))
    env=display_environment(directory)
    for attempt in range(20):
        try:
            subprocess.run(['xdpyinfo'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2,check=True)
            client=http.client.HTTPConnection('127.0.0.1',6083,timeout=2)
            try:
                client.request('HEAD','/vnc.html')
                if client.getresponse().status==200 and vnc_ready():return env
            finally:client.close()
        except (OSError,subprocess.SubprocessError):pass
        time.sleep(.25)
    raise RuntimeError('Isolated desktop did not become ready; inspect its container logs')


def main():
    """Prepare the isolated GUI and optionally replace this process with the host bridge."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only',action='store_true',help='Start/check the display without opening the host bridge')
    args=parser.parse_args()
    root=PROJECT/'state/desktop-browser'
    if not args.prepare_only and (root/'bridge.sock').exists():
        raise RuntimeError('A host bridge already exists. Stop it before starting the background bridge; no process was terminated.')
    env=ensure_display(root/'isolated-display')
    print(f'Isolated browser desktop: {VIEW}',flush=True)
    if args.prepare_only:return
    env['PYTHONPATH']=str(PROJECT/'src');os.chdir(PROJECT)
    os.execvpe(sys.executable,[sys.executable,'-m','jobagent.tools.desktop_browser','serve'],env)


if __name__=='__main__':main()
