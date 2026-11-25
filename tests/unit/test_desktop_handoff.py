"""The consented transport must keep process, task and public-network boundaries."""
import json
import os
import socket
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobagent.tools import desktop_browser as D, desktop_handoff as H


@pytest.mark.parametrize('raw',['0\n/devtools/browser/id','65536\n/devtools/browser/id',
    '9222\nhttp://other.test/x','9222\n/devtools/page/id','9222\n/devtools/browser/../id',
    '9222\n/devtools/browser/id\nextra'])
def test_endpoint_rejects_untrusted_ports_paths_and_extra_lines(raw):
    with pytest.raises(RuntimeError):H.endpoint(raw)


def test_endpoint_is_only_a_loopback_port_and_browser_path():
    assert H.endpoint('9222\n/devtools/browser/abc-123\n')==(9222,'/devtools/browser/abc-123')


@pytest.mark.parametrize('current,expected',[
    ('https://careers.example.org/jobs/42/new-role/candidate?private=opaque',True),
    ('https://careers.example.org/jobs/42/new-role/login',True),
    ('https://careers.example.org/jobs/420/new-role/candidate',False),
    ('https://another.example.org/jobs/42/new-role/candidate',False),
    ('https://careers.example.org/settings',False),
])
def test_job_namespace_remains_bound_without_employer_hardcoding(current,expected):
    assert H.same_posting('https://careers.example.org/jobs/42/new-role/job',current) is expected


def test_non_namespace_urls_cannot_switch_the_job_query():
    assert H.same_posting('https://jobs.example.org/apply?id=42','https://jobs.example.org/apply?id=42')
    assert not H.same_posting('https://jobs.example.org/apply?id=42','https://jobs.example.org/apply?id=99')


def test_listener_has_to_belong_to_the_exact_process_and_be_loopback():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen()
        port=listener.getsockname()[1]
        assert H.owned_loopback_listener(Path('/proc')/str(os.getpid()),port)
        assert not H.owned_loopback_listener(Path('/proc/1'),port)


@pytest.fixture
def owned(tmp_path,monkeypatch):
    profile=tmp_path/'profile';profile.mkdir()
    (profile/'SingletonLock').symlink_to('host-12345')
    manifest={'protocol':D.PROTOCOL,'kind':'consented','pid':12345,'profile':str(profile),
              'url':'https://jobs.example.org/jobs/42/job','proxy_port':34567,'started_ns':time.time_ns()-1_000_000_000}
    real_read=Path.read_bytes;real_resolve=Path.resolve
    args=['/opt/google/chrome/chrome',f'--user-data-dir={profile}',
          '--proxy-server=http://127.0.0.1:34567','--proxy-bypass-list=<-loopback>']
    monkeypatch.setattr(Path,'read_bytes',lambda p: ('\0'.join(args)+'\0').encode() if str(p)=='/proc/12345/cmdline' else real_read(p))
    monkeypatch.setattr(Path,'resolve',lambda p,*a,**k: Path('/opt/google/chrome/chrome') if str(p)=='/proc/12345/exe' else real_resolve(p,*a,**k))
    monkeypatch.setattr(D,'process_alive',lambda p:p==12345)
    return tmp_path,profile,manifest,args


@pytest.mark.parametrize('change',['profile','proxy','pid','managed_flags','lock','symlink','protocol'])
def test_identity_rejects_personal_profiles_process_changes_or_unguarded_browsers(owned,change):
    root,profile,m,args=owned
    if change=='profile':m['profile']='/personal/chrome'
    if change=='proxy':args.remove('--proxy-bypass-list=<-loopback>')
    if change=='pid':m['pid']=99999
    if change=='managed_flags':args.append('--remote-debugging-port=0')
    if change=='lock':(profile/'SingletonLock').unlink();(profile/'SingletonLock').symlink_to('host-99999')
    if change=='symlink':profile.rename(root/'elsewhere');profile.symlink_to(root/'elsewhere',target_is_directory=True)
    if change=='protocol':m['protocol']=-1
    with pytest.raises(RuntimeError):H.validate_identity(root,m,34567)


def test_connection_reply_requires_a_fresh_owned_endpoint(owned,monkeypatch):
    root,profile,m,args=owned
    with pytest.raises(RuntimeError,match='Enable remote debugging'):H.connection_reply(root,m,34567)
    active=profile/'DevToolsActivePort';active.write_text('9222\n/devtools/browser/abc-123\n')
    monkeypatch.setattr(H,'owned_loopback_listener',lambda proc,port:port==9222)
    reply=H.connection_reply(root,m,34567)
    assert reply['guarded'] and reply['handoff'] and reply['fresh'] is False
    assert time.time()<reply['expires_at']<=time.time()+300
    os.utime(active,ns=(1,1))
    with pytest.raises(RuntimeError,match='Stale'):H.connection_reply(root,m,34567)


def test_an_unowned_listener_is_never_returned(owned,monkeypatch):
    root,profile,m,args=owned
    (profile/'DevToolsActivePort').write_text('9222\n/devtools/browser/abc-123\n')
    monkeypatch.setattr(H,'owned_loopback_listener',lambda *a:False)
    with pytest.raises(RuntimeError,match='not owned'):H.connection_reply(root,m,34567)


def test_prepare_launches_ordinary_chrome_with_the_same_public_only_guard(tmp_path,monkeypatch):
    seen=[]
    monkeypatch.setattr(D,'public_destination',lambda u:True)
    monkeypatch.setattr(D.shutil,'which',lambda name:'/opt/google/chrome/chrome')
    monkeypatch.setattr(H.subprocess,'Popen',lambda args,**k:seen.extend(args) or SimpleNamespace(pid=12345))
    manifest=H.prepare(tmp_path,'https://jobs.example.org/jobs/42/job',34567)
    assert manifest['kind']=='consented' and manifest['pid']==12345
    assert not any('remote-debugging' in a for a in seen)
    assert D.proxy_arguments_ok(seen,34567)
    assert (tmp_path/'session.json').stat().st_mode&0o777==0o600
    assert not (tmp_path/'profile/DevToolsActivePort').exists()


def test_prepare_does_not_touch_a_live_or_unrecognized_profile(tmp_path,monkeypatch):
    (tmp_path/'profile').mkdir()
    monkeypatch.setattr(D,'public_destination',lambda u:True)
    monkeypatch.setattr(H.subprocess,'Popen',lambda *a,**k:pytest.fail('No browser launch'))
    with pytest.raises(RuntimeError,match='Unrecognized'):H.prepare(tmp_path,'https://jobs.example.org/1',34567)


def test_broker_uses_the_consent_endpoint_without_a_managed_restart(tmp_path,monkeypatch):
    url='https://jobs.example.org/jobs/42/job';root=D.agent_profile_root(tmp_path,url);root.mkdir(parents=True)
    (root/'session.json').write_text(json.dumps({'kind':'consented','url':url}))
    monkeypatch.setattr(D,'public_destination',lambda u:True)
    monkeypatch.setattr(H,'connection_reply',lambda r,m,p:{'ok':True,'handoff':True})
    assert D.open_request(tmp_path,{'action':'open','protocol':D.PROTOCOL,'url':url},SimpleNamespace(port=34567),
                          launcher=lambda *a:pytest.fail('No restart'))=={'ok':True,'handoff':True}


def test_connection_failure_does_not_leak_private_browser_endpoint(tmp_path,monkeypatch):
    import io
    import traceback
    from contextlib import nullcontext
    from jobagent.tools import browser

    url='https://jobs.example.org/jobs/42/job'
    directory=tmp_path/'desktop-browser';directory.mkdir();(directory/'bridge.sock').touch()
    reply={'ok':True,'protocol':D.PROTOCOL,'url':url,'port':9222,'guarded':True,
           'handoff':True,'fresh':False,'websocket_path':'/devtools/browser/private-token','expires_at':time.time()+60}
    class Client:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def settimeout(self,*a):pass
        def connect(self,*a):pass
        def sendall(self,*a):pass
        def makefile(self,*a):return io.BytesIO((json.dumps(reply)+'\n').encode())
    def failed(endpoint,**kwargs):raise RuntimeError('Connection failed: '+endpoint)
    monkeypatch.setattr(socket,'socket',lambda *a:Client())
    monkeypatch.setattr(browser,'sync_playwright',lambda:nullcontext(SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=failed))))
    with pytest.raises(RuntimeError,match='Chrome handoff') as caught:
        with browser.application_context('desktop',url,SimpleNamespace(state_dir=tmp_path)):pytest.fail('No session')
    assert 'private-token' not in ''.join(traceback.format_exception(caught.value))


@pytest.mark.parametrize('background',[False,True])
def test_screenshot_respects_the_desktop_background_policy(background):
    from jobagent.tools.browser import screenshot
    seen=[]
    page=SimpleNamespace(context=SimpleNamespace(jobagent_background=background),
                         bring_to_front=lambda:seen.append('foreground'),
                         screenshot=lambda **k:seen.append('screenshot'))
    screenshot(page,'capture.png')
    assert seen==(['screenshot'] if background else ['foreground','screenshot'])
