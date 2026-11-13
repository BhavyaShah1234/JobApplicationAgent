from types import SimpleNamespace

import pytest

from jobagent.tools import desktop_browser as D, challenge


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    import socket
    real = socket.getaddrinfo
    # names resolve to a public address; IP literals and localhost (the tests' own sockets) are left to the real resolver
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, *a, **k:
                        real(host, port, *a, **k) if host in ('127.0.0.1', 'localhost') else [(2, 1, 6, '', ('93.184.216.34', 443))])


@pytest.mark.parametrize('url',[
    'https://new-employer.example.org/careers/posting/1',
    'https://tenant.myworkdayjobs.com/en-US/External/job/Engineering/JR123/apply',
    'https://jobs.unseen-ats.example.net/a-long-id?step=contact#resume',
])
def test_generic_desktop_accepts_application_urls_without_a_vendor_list(url):
    assert D.allowed(url)


def test_scope_origins_normalize_https_ports_and_international_domains():
    assert D.origin('https://JOBS.EXAMPLE.ORG:443/apply?step=1')=='https://jobs.example.org'
    assert D.origin('https://bücher.example.org/apply')=='https://xn--bcher-kva.example.org'


@pytest.mark.parametrize('url',[
    'javascript:alert(1)', 'chrome://settings', 'https://10.0.0.1/', 'https://[::1]/', 'https://127.1/',
    'https://machine.local/', 'https://example.org:9222/', 'https://example.org@127.0.0.1/',
])
def test_local_and_non_web_destinations_are_rejected(url):
    assert not D.allowed(url)


def scoped_page(url='https://careers.unseen-example.org/posting/1',links=(),frames=()):
    page=SimpleNamespace(url=url,frames=list(frames))
    page.frames.insert(0,SimpleNamespace(url=url,locator=lambda selector:SimpleNamespace(evaluate_all=lambda js:list(links))))
    ctx=SimpleNamespace(pages=[page]);page.context=ctx
    return page,ctx


def test_application_links_and_same_origin_steps_are_discovered_but_a_frame_grants_nothing():
    iframe=SimpleNamespace(url='https://forms.embedded-example.org/questions',
        locator=lambda selector:SimpleNamespace(evaluate_all=lambda js:['https://hidden.embedded-example.org/go']))
    page,ctx=scoped_page(links=['https://apply.new-vendor.example.net/start'],frames=[iframe])
    scope=D.Scope(ctx,page,page.url);scope(page)
    assert 'https://forms.embedded-example.org' in scope.frame_origins          # tolerated as embedded content ...
    page.url='https://apply.new-vendor.example.net/start?step=education#details';scope(page)   # a published application link
    page.url='https://forms.embedded-example.org/questions/next'                                # ... but not a navigation target
    with pytest.raises(challenge.Blocked,match='known origins'):scope(page)
    page.url='https://hidden.embedded-example.org/go'                                           # nor does a frame publish links
    with pytest.raises(challenge.Blocked,match='known origins'):scope(page)
    page.url='https://unrelated-example.com/';
    with pytest.raises(challenge.Blocked,match='known origins'):scope(page)


def test_initial_redirect_and_explicit_login_origin_are_supported():
    page,ctx=scoped_page('https://ats.new-vendor.example.net/job/1')
    scope=D.Scope(ctx,page,'https://employer.example.org/careers/1',
                  ['https://identity.example.net'])
    scope(page)
    page.url='https://identity.example.net/sign-in';scope(page)
    ctx.pages.append(SimpleNamespace(url='https://identity.example.net/unrelated'))
    with pytest.raises(challenge.Blocked,match='tab'):scope(page)


def test_auto_handoff_does_not_wait_for_docker_human_verification():
    hitl=SimpleNamespace(ask=lambda *a,**kw:pytest.fail('Should hand off immediately'))
    controller=challenge.Controller(hitl,desktop_fallback=True)
    page=SimpleNamespace(url='https://new-employer.example.org/job')
    with pytest.raises(challenge.DesktopFallback,match='opening the desktop'):
        controller.wait(page,challenge.Challenge('cloudflare',page.url,''))


def test_dynamic_port_identity_requires_the_dedicated_process(tmp_path):
    with pytest.raises(RuntimeError,match='identity'):
        D.validate_process(tmp_path,{'protocol':D.PROTOCOL,'profile':'/personal/chrome','port':45678})


def test_relaunch_does_not_remove_another_live_browser_lock(tmp_path,monkeypatch):
    import json
    profile=tmp_path/'profile';profile.mkdir();(profile/'SingletonLock').symlink_to('host-222')
    (tmp_path/'session.json').write_text(json.dumps({'protocol':2,'pid':111,'profile':str(profile)}))
    monkeypatch.setattr(D,'process_alive',lambda pid:pid==222)
    monkeypatch.setattr(D.shutil,'which',lambda name:'/opt/google/chrome/chrome')
    monkeypatch.setattr(D.subprocess,'Popen',lambda *a,**kw:pytest.fail('Must not launch another browser'))
    with pytest.raises(RuntimeError,match='owned by another Chrome'):D.launch(tmp_path,'https://jobs.example.org/1',45678)
    assert (profile/'SingletonLock').is_symlink()


# --- F07: leases are bound to the task; frames and resolved addresses are not trusted blindly ---------------------------------------

def test_a_reused_tab_on_an_unrelated_public_site_is_rejected_before_anything_runs(tmp_path):
    page,ctx=scoped_page('https://unrelated-news.example.com/story')
    with pytest.raises(challenge.Blocked,match='unrelated'):
        D.Scope(ctx,page,'https://careers.employer.example.org/jobs/1',fresh=False,store=tmp_path/'approved.json')


def test_a_fresh_session_follows_the_postings_initial_redirect_and_a_resume_remembers_it(tmp_path):
    store=tmp_path/'approved.json'
    page,ctx=scoped_page('https://ats.new-vendor.example.net/job/1')
    D.Scope(ctx,page,'https://careers.employer.example.org/jobs/1',fresh=True,store=store,target_id='T1')   # the redirect is approved
    again,ctx2=scoped_page('https://ats.new-vendor.example.net/job/1/apply')
    scope=D.Scope(ctx2,again,'https://careers.employer.example.org/jobs/1',fresh=False,store=store,target_id='T1')
    scope(again)                                                                                           # reused, still the same task
    other,ctx3=scoped_page('https://ats.new-vendor.example.net/job/1')
    with pytest.raises(challenge.Blocked,match='target'):
        D.Scope(ctx3,other,'https://careers.employer.example.org/jobs/1',fresh=False,store=store,target_id='T2')


def test_a_lease_for_another_task_does_not_approve_this_tasks_tab(tmp_path):
    store=tmp_path/'approved.json'
    page,ctx=scoped_page('https://ats.new-vendor.example.net/job/1')
    D.Scope(ctx,page,'https://careers.employer.example.org/jobs/1',fresh=True,store=store)
    other,ctx2=scoped_page('https://ats.new-vendor.example.net/job/1')
    with pytest.raises(challenge.Blocked,match='unrelated'):
        D.Scope(ctx2,other,'https://careers.another-employer.example.org/jobs/9',fresh=False,store=store)


def test_explicit_login_origins_and_unseen_vendors_still_work_when_reused(tmp_path):
    page,ctx=scoped_page('https://identity.example.net/sign-in')
    scope=D.Scope(ctx,page,'https://employer.example.org/careers/1',['https://identity.example.net'],fresh=False,
                  store=tmp_path/'a.json')
    scope(page)


def test_a_public_name_that_resolves_to_a_private_address_is_refused(monkeypatch):
    def resolver(host,port,*a,**k):
        return [(2,1,6,'',('10.1.2.3' if host=='sneaky.example.org' else '93.184.216.34',443))]
    assert D.public_destination('https://fine.example.org/x',resolver)
    assert not D.public_destination('https://sneaky.example.org/x',resolver)
    page,ctx=scoped_page('https://sneaky.example.org/x')
    with pytest.raises(challenge.Blocked,match='public'):
        D.Scope(ctx,page,'https://sneaky.example.org/x',resolver=resolver)
    good,ctx=scoped_page('https://employer.example.org/x',frames=[SimpleNamespace(url='https://sneaky.example.org/f',
        locator=lambda s:SimpleNamespace(evaluate_all=lambda js:[]))])
    scope=D.Scope(ctx,good,good.url,resolver=resolver)
    with pytest.raises(challenge.Blocked,match='frame'):scope(good)


def test_an_unresolvable_name_is_refused_fail_closed():
    def boom(*a,**k):raise OSError('no dns')
    assert not D.host_is_public('jobs.unseen.example.org',boom)               # a guard that waves through what it cannot verify is no boundary
    assert not D.public_destination('https://jobs.unseen.example.org/a',boom)
    assert not D.public_destination('https://10.0.0.1/a',boom)
    assert D.host_is_public('8.8.8.8',boom)                                   # an IP literal needs no lookup


def test_the_recorded_target_must_be_verified_again_on_resume(tmp_path):
    store=tmp_path/'approved.json'
    page,ctx=scoped_page('https://ats.new-vendor.example.net/job/1')
    D.Scope(ctx,page,'https://careers.employer.example.org/jobs/1',fresh=True,store=store,target_id='T1')
    for bad in (None,'','T2'):
        again,ctx2=scoped_page('https://ats.new-vendor.example.net/job/1')
        with pytest.raises(challenge.Blocked,match='target'):
            D.Scope(ctx2,again,'https://careers.employer.example.org/jobs/1',fresh=False,store=store,target_id=bad)
    ok,ctx3=scoped_page('https://ats.new-vendor.example.net/job/1')
    D.Scope(ctx3,ok,'https://careers.employer.example.org/jobs/1',fresh=False,store=store,target_id='T1')(ok)


def test_the_browser_is_launched_through_the_public_only_proxy(tmp_path,monkeypatch):
    seen={}
    class Stop(Exception):pass
    def popen(command,**kw):
        seen['command']=command;raise Stop()
    monkeypatch.setattr(D.shutil,'which',lambda name:'/opt/google/chrome/chrome')
    monkeypatch.setattr(D.subprocess,'Popen',popen)
    with pytest.raises(Stop):D.launch(tmp_path,'https://jobs.example.org/1',45678)
    assert '--proxy-server=http://127.0.0.1:45678' in seen['command'] and '--proxy-bypass-list=<-loopback>' in seen['command']
    private=lambda host,port,*a,**k:[(2,1,6,'',('10.9.9.9',443))]
    monkeypatch.setattr('socket.getaddrinfo',private)
    with pytest.raises(ValueError):D.launch(tmp_path/'x','https://jobs.example.org/1',45678)     # refused before any process starts


import socket
import threading


def start_proxy(resolver, upstream_port=None, ports=(443,)):
    proxy=D.PublicProxy(resolver=resolver,
                        connector=(lambda ip,port:socket.create_connection(('127.0.0.1',upstream_port),timeout=5)) if upstream_port else None,
                        ports=ports)
    threading.Thread(target=proxy.serve_forever,daemon=True).start()
    return proxy


def connect(proxy, target):
    """CONNECT, then read up to the blank line that ends the response header. Returns (socket, header, bytes already read past
    it): TCP preserves no read boundaries, so a payload that arrived with the header must not be lost."""
    s=socket.create_connection(('127.0.0.1',proxy.port),timeout=5)
    s.sendall(f'CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n'.encode())
    data=b''
    while b'\r\n\r\n' not in data:
        chunk=s.recv(4096)
        if not chunk:break
        data+=chunk
    head,_,rest=data.partition(b'\r\n\r\n')
    return s, head.decode('latin1'), rest


def test_the_proxy_tunnels_to_a_vetted_public_address_and_refuses_everything_else():
    echo=socket.socket();echo.bind(('127.0.0.1',0));echo.listen(5);connections=[]
    def serve():
        while True:
            try:c,_=echo.accept()
            except OSError:return
            connections.append(1);c.sendall(b'hello from upstream');c.close()
    threading.Thread(target=serve,daemon=True).start()
    table={'public.example':'93.184.216.34','private.example':'10.1.2.3','mixed.example':None}
    def resolver(host,port,*a,**k):
        if host=='mixed.example':return [(2,1,6,'',('93.184.216.34',port)),(2,1,6,'',('127.0.0.1',port))]    # one bad answer spoils the name
        if host not in table:raise OSError('no such name')
        return [(2,1,6,'',(table[host],port))]
    proxy=start_proxy(resolver,echo.getsockname()[1])
    try:
        s,reply,rest=connect(proxy,'public.example:443')
        payload=rest
        while len(payload)<len(b'hello from upstream'):
            chunk=s.recv(100)
            if not chunk:break
            payload+=chunk
        assert reply.startswith('HTTP/1.1 200') and payload==b'hello from upstream';s.close()
        for target in ('private.example:443','mixed.example:443','nonexistent.example:443','10.0.0.5:443','127.0.0.1:443','localhost:443',
                       'internal.service.internal:443','public.example:22'):
            before=len(connections)
            s,reply,_=connect(proxy,target);s.close()
            assert reply.startswith('HTTP/1.1 403'),(target,reply)             # unresolvable names and bad ports included: fail closed
            assert len(connections)==before                                   # nothing was connected to
        c=socket.create_connection(('127.0.0.1',proxy.port),timeout=5)
        c.sendall(b'GET http://public.example/ HTTP/1.1\r\nHost: public.example\r\n\r\n')
        assert c.recv(200).startswith(b'HTTP/1.1 405');c.close()               # plain HTTP is not proxied
    finally:
        proxy.shutdown();echo.close()


def test_a_name_that_resolves_differently_later_cannot_reach_a_private_address():
    """The proxy connects to the address it vetted, so a second lookup is never made by the connection."""
    calls=[]
    def rebinding(host,port,*a,**k):
        calls.append(host)
        return [(2,1,6,'',('93.184.216.34' if len(calls)==1 else '10.0.0.9',port))]
    proxy=start_proxy(rebinding,upstream_port=1)            # nothing listens on port 1: the connect fails after vetting
    try:
        s,reply,_=connect(proxy,'rebind.example:443');s.close()
        assert reply.startswith('HTTP/1.1 502') and len(calls)==1
    finally:
        proxy.shutdown()


def test_chrome_through_the_proxy_never_reaches_a_private_redirect_target(tmp_path):
    """Real Chrome, real TLS servers on loopback, the real PublicProxy: a public application link redirects to a host that
    resolves to a private address. The browser's request for it is stopped at the proxy; the server standing in for it is
    never contacted. A public redirect to another public host still works."""
    import http.server
    import shutil
    import ssl
    import subprocess
    from playwright.sync_api import sync_playwright
    if not shutil.which('openssl'):pytest.skip('no openssl')
    key,crt=tmp_path/'k.pem',tmp_path/'c.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(crt),'-days','1','-subj','/CN=synthetic',
                    '-addext','subjectAltName=DNS:*.synthetic.example'],check=True,capture_output=True)
    hits={'A':[],'B':[]};ports={}
    def make(name):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                hits[name].append((self.headers.get('Host','').split(':')[0],self.path))
                if self.path=='/':
                    self.send_response(200);self.send_header('content-type','text/html');self.end_headers()
                    self.wfile.write(b'<a id=a href="https://apply.synthetic.example/start">Apply</a>')
                elif self.path=='/start':
                    self.send_response(302);self.send_header('location','https://private.synthetic.example/private-landing');self.end_headers()
                elif self.path=='/start-ok':
                    self.send_response(302);self.send_header('location','https://ats.synthetic.example/landing');self.end_headers()
                else:
                    self.send_response(200);self.send_header('content-type','text/html');self.end_headers();self.wfile.write(b'<h1>ATS form</h1>')
        return H
    servers={}
    for n in 'AB':
        s=http.server.ThreadingHTTPServer(('127.0.0.1',0),make(n))
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(str(crt),str(key));s.socket=ctx.wrap_socket(s.socket,server_side=True)
        servers[n]=s;threading.Thread(target=s.serve_forever,daemon=True).start()
    # every public name resolves to one fake public address, the ATS host to another, the private name to a private one;
    # the connector sends each vetted address to the local server that stands in for it
    def resolver(host,port,*a,**k):
        ip='10.1.2.3' if host=='private.synthetic.example' else '93.184.216.35' if host=='ats.synthetic.example' else '93.184.216.34'
        return [(2,1,6,'',(ip,port))]
    def connector(ip,port):
        return socket.create_connection(('127.0.0.1',servers['B' if ip=='93.184.216.35' else 'A'].server_address[1]),timeout=5)
    proxy=D.PublicProxy(resolver=resolver,connector=connector)
    threading.Thread(target=proxy.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as pw:
            try:browser=pw.chromium.launch(channel='chrome',headless=True,args=[f'--proxy-server=http://127.0.0.1:{proxy.port}',
                                                '--proxy-bypass-list=<-loopback>','--ignore-certificate-errors'])
            except Exception:pytest.skip('no Chrome here')
            try:
                page=browser.new_page()
                page.goto('https://careers.synthetic.example/')
                page.click('#a')                                   # Apply -> 302 -> private.synthetic.example
                page.wait_for_timeout(1000)
                assert ('apply.synthetic.example','/start') in hits['A']          # the public application URL was fetched
                assert not any(h=='private.synthetic.example' for h,_ in hits['A']+hits['B'])     # the private host never was
                assert any('private.synthetic.example' in r for r in proxy.refused)                # stopped at the proxy
                with pytest.raises(Exception):page.goto('https://private.synthetic.example/')      # nor the initial navigation
                fresh=browser.new_page()                                                            # the errored tab is left behind
                fresh.goto('https://apply.synthetic.example/start-ok')                              # a public redirect still works
                assert fresh.url=='https://ats.synthetic.example/landing' and 'ATS form' in fresh.inner_text('body')
            finally:
                browser.close()
    finally:
        proxy.shutdown()
        for s in servers.values():s.shutdown()


# --- F07 verification: the browser is bound to the proxy that is live now -----------------------------------------------------------------

def session(root, url, proxy_port, pid=4242):
    import json as _json
    d=D.agent_profile_root(root,url);d.mkdir(parents=True)
    (d/'session.json').write_text(_json.dumps({'protocol':D.PROTOCOL,'pid':pid,'profile':str(d/'profile'),'url':url,'port':45555,
                                               'proxy_port':proxy_port}))


def test_a_running_browser_is_reused_only_with_the_proxy_that_guards_it_now(tmp_path):
    url='https://careers.unseen-example.org/jobs/1'
    request={'protocol':D.PROTOCOL,'action':'open','url':url}
    live=D.PublicProxy()
    try:
        session(tmp_path,url,live.port)
        ok=D.open_request(tmp_path,request,live,alive=lambda pid:True,validate=lambda root,manifest:None)
        assert ok['ok'] and ok['fresh'] is False and ok['guarded'] is True               # a current guard still allows normal reuse
    finally:
        live.server_close()


def test_a_browser_bound_to_a_retired_proxy_is_refused_not_reported_as_guarded(tmp_path):
    url='https://careers.unseen-example.org/jobs/1'
    request={'protocol':D.PROTOCOL,'action':'open','url':url}
    restarted=D.PublicProxy()                                  # the broker was restarted: a new proxy on a new port
    try:
        session(tmp_path,url,restarted.port+1)                 # the browser still points at the old one (nothing listens there)
        with pytest.raises(RuntimeError,match='retired network guard'):
            D.open_request(tmp_path,request,restarted,alive=lambda pid:True,validate=lambda root,manifest:None)
        legacy=tmp_path/'x';session(legacy,url,None)           # a browser started before the proxy existed
        with pytest.raises(RuntimeError,match='retired network guard'):
            D.open_request(legacy,request,restarted,alive=lambda pid:True,validate=lambda root,manifest:None)
    finally:
        restarted.server_close()


def test_a_bridge_restart_keeps_the_same_proxy_port_so_running_browsers_stay_guarded(tmp_path):
    url='https://careers.unseen-example.org/jobs/1'
    request={'protocol':D.PROTOCOL,'action':'open','url':url}
    first=D.start_proxy(tmp_path)
    port=first.port
    session(tmp_path,url,port)
    first.server_close()                                       # the bridge stops; its Chrome keeps running
    second=D.start_proxy(tmp_path)                             # ... and comes back
    try:
        assert second.port==port
        second.resolver=lambda host,port_,*a,**k:[(2,1,6,'',('10.1.2.3',port_))]       # every name is private to this guard
        threading.Thread(target=second.serve_forever,daemon=True).start()
        reply=D.open_request(tmp_path,request,second,alive=lambda pid:True,validate=lambda root,manifest:None)
        assert reply['guarded'] is True and reply['fresh'] is False
        s,header,_=connect(second,'private.synthetic.example:443');s.close()          # and the restarted guard still refuses
        assert header.startswith('HTTP/1.1 403') or header.startswith('HTTP/1.1 502')
    finally:
        second.shutdown();second.server_close()


def test_when_the_old_proxy_port_cannot_be_had_the_old_browsers_are_refused(tmp_path):
    url='https://careers.unseen-example.org/jobs/1'
    first=D.start_proxy(tmp_path);port=first.port;session(tmp_path,url,port)
    squatter=socket.socket();squatter.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,0)
    first.server_close()
    squatter.bind(('127.0.0.1',port));squatter.listen(1)       # someone else took the port while the bridge was down
    try:
        second=D.start_proxy(tmp_path)
        try:
            assert second.port!=port
            with pytest.raises(RuntimeError,match='retired network guard'):
                D.open_request(tmp_path,{'protocol':D.PROTOCOL,'action':'open','url':url},second,
                              alive=lambda pid:True,validate=lambda root,manifest:None)
        finally:
            second.server_close()
    finally:
        squatter.close()


def test_the_process_identity_includes_the_proxy_it_was_started_with():
    ok=['/opt/chrome','--user-data-dir=/p','--proxy-server=http://127.0.0.1:41000','--proxy-bypass-list=<-loopback>']
    assert D.proxy_arguments_ok(ok,41000)
    assert not D.proxy_arguments_ok(ok,41001)                                  # a different port
    assert not D.proxy_arguments_ok([a for a in ok if 'bypass' not in a],41000)    # loopback not forced through the proxy
    assert not D.proxy_arguments_ok(['/opt/chrome','--user-data-dir=/p'],None)     # a browser with no proxy at all
