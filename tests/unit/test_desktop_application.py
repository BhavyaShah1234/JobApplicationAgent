import io
import json
import socket
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from jobagent.tools import browser, desktop_probe, desktop_browser, challenge
from page_fakes import EventPage


@pytest.mark.parametrize('valid', [True, False])
@pytest.mark.parametrize('url',['https://www.carvana.com/careers/apply?gh_jid=8188615',
                              'https://careers.unseen-example.com/openings/opaque-job'])
def test_desktop_lease_is_validated_and_context_is_not_closed(tmp_path, monkeypatch, valid,url):
    page = SimpleNamespace(url=url, frames=[])
    ctx = SimpleNamespace(pages=[page], new_cdp_session=lambda p: SimpleNamespace(
        send=lambda *a, **k: {'targetInfo': {'targetId': 'T1'}}))
    page.context = ctx
    remote = SimpleNamespace(contexts=[ctx])
    requests = []
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, *a, **k: [(2, 1, 6, '', ('93.184.216.34', 443))])
    directory=tmp_path/'desktop-browser';directory.mkdir();(directory/'bridge.sock').touch()

    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, timeout): pass
        def connect(self, path): assert path.endswith('desktop-browser/bridge.sock')
        def sendall(self, data): requests.append(json.loads(data))
        def makefile(self, *args):
            return io.BytesIO(json.dumps({'ok':True,'protocol':2,'port':42115 if valid else 0,'url':url,'fresh':True,'guarded':True}).encode()+b'\n')

    monkeypatch.setattr(socket, 'socket', lambda *a: Client())
    monkeypatch.setattr(browser, 'sync_playwright', lambda: nullcontext(SimpleNamespace(
        chromium=SimpleNamespace(connect_over_cdp=lambda endpoint: remote))))
    settings = SimpleNamespace(state_dir=tmp_path)
    if valid:
        with browser.application_context('desktop',url,settings) as connected:
            assert connected is ctx
            connected.jobagent_policy(page)
            page.url = 'https://unrelated.example.org/settings'
            with pytest.raises(challenge.Blocked): connected.jobagent_policy(page)
    else:
        with pytest.raises(RuntimeError,match='mismatch'):
            with browser.application_context('desktop',url,settings): pytest.fail('Must not yield')
    assert requests == [{'action':'open','protocol':2,'url':url}]


@pytest.mark.parametrize('url',['file:///etc/passwd','http://example.com/apply','https://localhost/apply',
                              'https://127.0.0.1/apply','https://user:password@example.com/apply'])
def test_desktop_rejects_non_application_destinations(url):
    with pytest.raises(ValueError,match='public HTTPS'):
        with browser.application_context('desktop',url): pass


def test_each_posting_gets_an_isolated_profile(tmp_path):
    first=desktop_probe.agent_profile_root(tmp_path,'https://www.carvana.com/careers/apply?gh_jid=8188615')
    second=desktop_probe.agent_profile_root(tmp_path,'https://www.carvana.com/careers/apply?gh_jid=8063598')
    assert first != second
    assert first.parent == second.parent == tmp_path/'agent-session'
    assert first==desktop_probe.agent_profile_root(tmp_path,'https://www.carvana.com/careers/apply?gh_jid=8188615')


def test_exited_zombie_browser_does_not_own_a_new_session(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path,'read_text',lambda self:'Name:\tchrome\nState:\tZ (zombie)\n')
    assert not desktop_probe.process_alive(123)


@pytest.mark.parametrize('desktop',[False,True])
def test_lazy_upload_is_discovered_and_filled(tmp_path,desktop):
    import shutil
    from playwright.sync_api import sync_playwright
    from jobagent.tools import forms
    if not shutil.which('google-chrome'): pytest.skip('Chrome required')
    pdf = tmp_path / 'resume.pdf'
    pdf.write_bytes(b'PDF fixture')
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel='chrome',headless=True)
        p = b.new_page()
        if desktop:
            p.context.jobagent_policy = lambda page: None
        p.set_content('''<h1>Application</h1><div><label for="Resume/CV">Resume/CV*</label>
        <button type="button" onclick="const e=document.createElement('input');e.type='file';
        e.id='Resume/CV_fileInput';e.style.display='none';e.onchange=()=>{
        const s=document.createElement('span');s.textContent=e.files[0].name;document.body.append(s)};
        this.parentElement.append(e)">Choose File</button></div>''')
        view = forms.read_page(p)
        upload = next(f for f in view.fields if f.kind=='file')
        assert upload.required
        forms.fill_field(p,view,upload,'resume',{'resume':str(pdf)})
        assert len([f for f in forms.read_page(p).fields if f.kind=='file'])==1
        assert 'resume.pdf' in p.locator('body').inner_text()
        assert p.locator('input[type=file]').evaluate('e=>e.files[0].size') == len(b'PDF fixture')
        b.close()


@pytest.mark.parametrize('resumable,expected', [(True,['docker','desktop']), (False,['docker'])])
def test_apply_auto_falls_back_only_for_a_challenge_block(tmp_path,monkeypatch,resumable,expected):
    from typer.testing import CliRunner
    from jobagent import main
    from jobagent.steps import apply
    from jobagent.tools import inbox,vault
    url='https://www.carvana.com/careers/apply?gh_jid=8188615'
    (tmp_path/'jd.json').write_text(json.dumps({'source_url':url,'company':'Carvana'}))
    modes=[];runs=[]
    def context(mode,url,**kwargs):
        modes.append(mode)
        return nullcontext(SimpleNamespace(pages=[object()]))
    def run(*args,**kwargs):
        runs.append(kwargs)
        return {'state':'blocked' if len(runs)==1 else 'review','resumable':resumable,
                'verified':0,'fields':[],'flags':[],'ready_to_submit':True,'blockers':[]}
    monkeypatch.setattr(main,'_stop_if_unavailable',lambda *a:None)
    monkeypatch.setattr(main,'_stop_if_documents_invalid',lambda *a:None)   # F05 gate: not what this test is about
    monkeypatch.setattr(browser,'application_context',context)
    monkeypatch.setattr(inbox,'open_inbox',lambda:nullcontext(None))
    monkeypatch.setattr(vault,'open_vault',lambda:SimpleNamespace(get=lambda key:{'email':'candidate@example.com'}))
    monkeypatch.setattr(apply,'run',run)
    result=CliRunner().invoke(main.app,['apply',str(tmp_path),'carvana','--browser-mode','auto'])
    # F03: a run that ends blocked is a nonzero exit (4) for shell chains and the queue; the desktop retry that ends on
    # a ready review screen is 0
    assert result.exit_code==(0 if resumable else 4),result.output
    assert modes==expected
    if resumable:
        assert runs[-1]['already_open']
        assert runs[-1]['observe_s']==30
        assert runs[0]['desktop_fallback'] and not runs[-1]['desktop_fallback']
        assert 'at_review' not in runs[-1]


def test_unusable_desktop_form_blocks_before_lookups_or_planning(tmp_path,monkeypatch):
    from jobagent.steps import apply
    class Page(EventPage):
        url='https://www.carvana.com/careers/apply?gh_jid=8188615'
    monkeypatch.setattr(apply,'context',lambda *a:{'_jd':{'apply_url':Page.url}})
    monkeypatch.setattr(apply.llm,'writer',lambda *a,**kw:object())
    monkeypatch.setattr(apply.forms,'read_page',lambda page:SimpleNamespace(fields=[],buttons=[]))
    monkeypatch.setattr(apply,'recover_desktop_form',lambda *a:None)
    monkeypatch.setattr(apply,'_start',lambda *a,**kw:None)   # guarded entry still leaves this stub page empty
    monkeypatch.setattr(challenge,'guard',lambda page:False)
    monkeypatch.setattr(challenge,'wait_for_user',lambda *a,**kw:(_ for _ in ()).throw(challenge.Blocked('not ready')))
    monkeypatch.setattr(apply.application_address,'prepare',lambda *a:pytest.fail('Must wait for form first'))
    result=apply.run(Page(),tmp_path,'carvana',already_open=True)
    assert result['state']=='blocked'
    assert result['verified']==0


def test_planner_audit_records_calls_without_prompt_content(tmp_path):
    from jobagent.steps.apply import PlannerAudit
    p=tmp_path/'calls.jsonl';audit=PlannerAudit(p,'voyager-model')
    audit.on_chat_model_start({},['private prompt'],run_id='test-id')
    audit.on_llm_end(object(),run_id='test-id')
    data=[json.loads(line) for line in p.read_text().splitlines()]
    assert [r['event'] for r in data]==['start','complete']
    assert 'private prompt' not in p.read_text()


def test_removed_desktop_posting_stops_before_address_lookup_and_records_closed(tmp_path,monkeypatch):
    from jobagent.steps import apply
    from jobagent.tools import availability
    class Page(EventPage):
        url='https://www.carvana.com/careers/apply?gh_jid=8063598'
    page=Page()
    monkeypatch.setattr(apply,'context',lambda *a:{'_jd':{'apply_url':page.url}})
    monkeypatch.setattr(apply.llm,'writer',lambda *a,**kw:object())
    monkeypatch.setattr(apply,'recover_desktop_form',lambda *a:None)
    monkeypatch.setattr(challenge,'guard',lambda page:False)
    gone=availability.Unavailable('not_found','Greenhouse job 8063598 not found',page.url)
    monkeypatch.setattr(availability,'check_page',lambda page:gone)
    monkeypatch.setattr(apply.application_address,'prepare',lambda *a:pytest.fail('Closed job must stop first'))
    with pytest.raises(availability.Unavailable):apply.run(page,tmp_path,'carvana',already_open=True)
    result=json.loads((tmp_path/'application'/'application.json').read_text())
    assert result['state']=='closed' and result['verified']==0 and not result['resumable']
    assert 'Nothing was submitted' in (tmp_path/'application'/'application.md').read_text()


@pytest.mark.parametrize('challenged',[True,False])
@pytest.mark.parametrize('text',['Page Not Found','Apply for this Job\nSUBMIT APPLICATION','Unrelated page'])
def test_missing_page_recovery_reloads_once_but_never_reloads_a_challenge(monkeypatch,challenged,text):
    from jobagent.steps import apply
    actions=[]
    connection=SimpleNamespace(send=lambda method,args:actions.append((method,args)),detach=lambda:None)
    page=SimpleNamespace(context=SimpleNamespace(new_cdp_session=lambda p:connection),
        locator=lambda selector:SimpleNamespace(inner_text=lambda:text),wait_for_timeout=lambda ms:None)
    monkeypatch.setattr(apply.forms,'read_page',lambda p:SimpleNamespace(fields=[] if not actions else ['field']))
    monkeypatch.setattr(challenge,'detect',lambda p:challenge.Challenge('cloudflare','','') if challenged else None)
    apply.recover_desktop_form(page,lambda message:None)
    assert actions==([] if challenged or text=='Unrelated page' else [('Page.reload',{'ignoreCache':True})])


def test_planner_answers_are_bound_to_schema_slots_not_returned_ids(monkeypatch):
    from jobagent.steps import apply
    from jobagent.tools.forms import Field
    fields=[Field(0,'text','First Name'),Field(7,'combobox','Visa sponsorship',options=['Yes','No'])]
    monkeypatch.setattr(apply,'hints',lambda *a,**kw:{})
    monkeypatch.setattr(apply,'sheet_hints',lambda *a:{})
    def invoke(model,schema,*args,**kwargs):
        return schema.model_validate({'field_0':{'id':7,'value':'Candidate','source':'applicant'},
                                      'field_7':{'id':0,'value':'No','source':'applicant'},'add':[]})
    monkeypatch.setattr(apply.llm,'structured_invoke',invoke)
    answers,_=apply.plan(fields,{},None,lambda m:None)
    assert answers[0].id==0 and answers[0].value=='Candidate'
    assert answers[7].id==7 and answers[7].value=='No'


def test_verifier_corrections_are_bound_to_their_question(monkeypatch):
    from jobagent.steps import apply
    from jobagent.tools.forms import Field
    # Slot binding is independent of the owner-evidence gate for sensitive facts.
    fields=[Field(0,'text','First Name'),Field(7,'combobox','Document format',options=['Yes','No'])]
    answers={0:apply.Answer(id=0,value='wrong',source='written'),7:apply.Answer(id=7,value='Yes',source='written')}
    monkeypatch.setattr(apply,'hints',lambda *a,**kw:{})
    def invoke(model,schema,*args,**kwargs):
        return schema.model_validate({'field_0':{'id':7,'supported':False,'value':'Candidate'},
                                      'field_7':{'id':0,'supported':False,'value':'No'}})
    monkeypatch.setattr(apply.llm,'structured_invoke',invoke)
    apply.check(fields,answers,{},None,lambda m:None)
    assert answers[0].value=='Candidate'
    assert answers[7].value=='No'


def test_verifier_cannot_supply_sponsorship_without_owner_evidence(monkeypatch):
    from jobagent.steps import apply
    from jobagent.tools.forms import Field
    fields=[Field(7,'combobox','Visa sponsorship',options=['Yes','No'])]
    answers={7:apply.Answer(id=7,value='Yes',source='written')}
    monkeypatch.setattr(apply.llm,'structured_invoke',lambda *a,**kw:pytest.fail('No eligible answers to check'))
    apply.check(fields,answers,{},None,lambda m:None)
    assert answers[7].value=='' and answers[7].ask_human
    assert 'owner' in answers[7].flag


def _lease_world(tmp_path, monkeypatch, url, reply_extra=None, cdp=None):
    page = SimpleNamespace(url=url, frames=[])
    ctx = SimpleNamespace(pages=[page], new_cdp_session=cdp or (lambda p: SimpleNamespace(
        send=lambda *a, **k: {'targetInfo': {'targetId': 'T1'}})))
    page.context = ctx
    directory = tmp_path / 'desktop-browser'; directory.mkdir(); (directory / 'bridge.sock').touch()
    reply = {'ok': True, 'protocol': 2, 'port': 42115, 'url': url, 'fresh': True, 'guarded': True, **(reply_extra or {})}

    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, timeout): pass
        def connect(self, path): pass
        def sendall(self, data): pass
        def makefile(self, *args): return io.BytesIO(json.dumps(reply).encode() + b'\n')

    monkeypatch.setattr(socket, 'socket', lambda *a: Client())
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, *a, **k: [(2, 1, 6, '', ('93.184.216.34', 443))])
    monkeypatch.setattr(browser, 'sync_playwright', lambda: nullcontext(SimpleNamespace(
        chromium=SimpleNamespace(connect_over_cdp=lambda endpoint: SimpleNamespace(contexts=[ctx])))))
    return SimpleNamespace(state_dir=tmp_path)


def test_a_browser_started_without_the_public_only_proxy_is_not_used(tmp_path, monkeypatch):
    url = 'https://careers.unseen-example.com/openings/1'
    settings = _lease_world(tmp_path, monkeypatch, url, {'guarded': False})
    with pytest.raises(challenge.Blocked, match='public-only network proxy'):
        with browser.application_context('desktop', url, settings): pytest.fail('Must not yield')


def test_a_failed_target_lookup_stops_the_application(tmp_path, monkeypatch):
    url = 'https://careers.unseen-example.com/openings/1'
    def broken(p): raise RuntimeError('CDP session failed')
    settings = _lease_world(tmp_path, monkeypatch, url, cdp=broken)
    with pytest.raises(challenge.Blocked, match='identity'):
        with browser.application_context('desktop', url, settings): pytest.fail('Must not yield')
