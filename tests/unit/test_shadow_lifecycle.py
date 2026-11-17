"""Real browser lifecycle regressions: late cached attachShadow and CDP reconnect."""
import subprocess
import time
from playwright.sync_api import sync_playwright
from jobagent.tools import forms,shadow


def test_a_late_closed_root_created_through_a_cached_native_method_is_recovered():
    with sync_playwright() as pw:
        b=pw.chromium.launch(channel='chrome',headless=True);ctx=b.new_context();p=ctx.new_page()
        p.set_content('<script>window.cachedAttach=Element.prototype.attachShadow</script>')
        shadow.install(pw,ctx)
        assert not forms.read_page(p).fields
        p.evaluate('''()=>{const h=document.body.appendChild(document.createElement('div'));h.id='late';
          window.cachedAttach.call(h,{mode:'closed'}).innerHTML='<label>Late date<input placeholder="MM/DD/YYYY"></label>'}''')
        view=forms.read_page(p)
        assert len(view.fields)==1 and view.fields[0].label=='Late date'
        forms.fill_field(p,view,view.fields[0],'01/01/2026',{})
        assert forms.read_page(p).fields[0].value=='01/01/2026'
        assert p.evaluate("document.getElementById('late').shadowRoot===null")
        b.close()


def test_reconnecting_to_an_existing_desktop_chrome_preserves_native_dom_interactions(tmp_path):
    profile=tmp_path/'chrome';profile.mkdir()
    proc=subprocess.Popen(['google-chrome','--headless=new','--no-sandbox','--remote-debugging-port=0',
        '--user-data-dir='+str(profile),'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        portfile=profile/'DevToolsActivePort';deadline=time.monotonic()+15
        while not portfile.exists():
            assert time.monotonic()<deadline and proc.poll() is None
            time.sleep(.1)
        endpoint='http://127.0.0.1:'+portfile.read_text().splitlines()[0]
        for index in range(2):
            with sync_playwright() as pw:
                b=pw.chromium.connect_over_cdp(endpoint);ctx=b.contexts[0]
                shadow.install(pw,ctx);p=ctx.pages[0]
                if index==0:p.set_content('<button id=target onclick="this.textContent=\'clicked\'">Click</button>')
                forms.read_page(p)
                shadow.locator(p.main_frame,'#target').click(timeout=3000)
                assert p.locator('#target').inner_text()=='clicked'
                # Disconnect the client, leaving the dedicated browser alive.
    finally:
        proc.terminate()
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)


def test_open_roots_use_native_selectors_even_when_the_custom_registry_is_unavailable(monkeypatch):
    with sync_playwright() as pw:
        b=pw.chromium.launch(channel='chrome',headless=True);ctx=b.new_context();shadow.install(pw,ctx);p=ctx.new_page()
        p.set_content('''<div id=host></div><script>document.getElementById('host').attachShadow({mode:'open'}).
          innerHTML='<label>Given Name<input></label>'</script>''')
        view=forms.read_page(p);native=p.main_frame.locator
        def guarded(selector,*a,**kw):
            assert not selector.startswith('jobagent='),'an open root does not require a custom engine'
            return native(selector,*a,**kw)
        monkeypatch.setattr(p.main_frame,'locator',guarded)
        forms.fill_field(p,view,view.fields[0],'Example',{})
        assert forms.read_page(p).fields[0].value=='Example'
        b.close()
