"""Approved recovery preserves its tenant/session and commits credentials only after verified sign-in."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from typer.testing import CliRunner
from jobagent.tools import portal_auth,portal_agent

HOST='careers-company.icims.com'
SOURCE=f'https://{HOST}/jobs/123/job'
LOGIN='https://login.icims.com/u/login/password?state=fixture'


@pytest.mark.parametrize('success,already_open',[(True,True),(False,True),(True,False),(False,False)])
def test_recovery_is_tenant_scoped_and_failure_is_not_completed(monkeypatch,success,already_open):
    events=[];store={f'portal:{HOST}':{'password':'old-fixture','account':False}}
    class Vault:
        def get(self,key):return store.get(key)
        def set(self,key,**fields):store[key]={**store.get(key,{}),**fields}
    page=SimpleNamespace(url=LOGIN)
    monkeypatch.setattr(portal_auth.db,'record_agent_domain',lambda *a:None)
    monkeypatch.setattr(portal_auth,'portal_password',lambda vault:'approved-fixture')
    monkeypatch.setattr(portal_auth,'candidate_values',lambda email,password:{'EMAIL':email,'PASSWORD':password})
    monkeypatch.setattr(portal_auth.challenge,'guard',lambda page:events.append('guard'))
    monkeypatch.setattr(portal_auth.challenge,'goto',lambda *a,**kw:events.append('goto'))
    def run(page,url,values,*args,**kwargs):
        events.append('agent')
        assert kwargs['goal']=='reset_password' and args[2]==HOST
        assert store[f'portal:{HOST}']['password']=='old-fixture'
        assert store[f'portal:{HOST}']['reset_candidate_password']=='approved-fixture'
        return {'state':'signed_in' if success else 'failed','verified_mail':success,'steps':[]}
    monkeypatch.setattr(portal_agent,'run',run)
    result=portal_auth.reset_once(page,LOGIN,Vault(),SimpleNamespace(conn=object()),None,'fixture@example.test',
        log=lambda msg:None,already_open=already_open,account_url=SOURCE)
    record=store[f'portal:{HOST}']
    assert result.reset==success and result.signed_in==success
    assert result.host==HOST and set(store)=={f'portal:{HOST}'}
    assert events==['guard' if already_open else 'goto','agent']
    if success:
        assert record['password']=='approved-fixture' and record['reset_done'] and record['verified']
        with pytest.raises(portal_auth.PortalError,match='already done'):
            portal_auth.reset_once(page,LOGIN,Vault(),SimpleNamespace(conn=object()),None,'fixture@example.test',account_url=SOURCE)
    else:
        assert record['password']=='old-fixture' and not record.get('reset_done') and not record['account']
        assert record['reset_unconfirmed'] and record['reset_candidate_password']=='approved-fixture'


@pytest.mark.parametrize('success',[True,False])
def test_reset_cli_preserves_desktop_session_and_propagates_failure(monkeypatch,success):
    from jobagent import main
    from jobagent.tools import inbox,vault,session,challenge
    events=[];page=object();policy=object()
    @contextmanager
    def opened(mode,url,origins):
        assert mode=='desktop' and url==SOURCE and origins==['https://login.icims.com']
        yield SimpleNamespace(page=page,policy=policy,already_open=True)
    @contextmanager
    def mail():yield object()
    @contextmanager
    def guarded(p,hitl,**kwargs):
        assert p is page and kwargs['policy'] is policy
        yield
    def reset(p,url,*args,**kwargs):
        assert p is page and url==SOURCE and kwargs['already_open'] and kwargs['account_url']==SOURCE
        events.append('reset')
        return portal_auth.PortalLogin(HOST,'icims',signed_in=success,reset=success)
    monkeypatch.setattr(session,'open_session',opened)
    monkeypatch.setattr(inbox,'open_inbox',mail)
    monkeypatch.setattr(vault,'open_vault',lambda:SimpleNamespace(get=lambda key:{'email':'fixture@example.test'}))
    monkeypatch.setattr(challenge,'session',guarded)
    monkeypatch.setattr(portal_auth,'reset_once',reset)
    monkeypatch.setattr(main,'_require_model_service',lambda *a:None)
    monkeypatch.setattr(main,'runlog',lambda *a:lambda msg:None)
    result=CliRunner().invoke(main.app,['portal-reset',SOURCE,'--browser-mode','desktop','--desktop-allow-origin','https://login.icims.com'])
    assert result.exit_code==(0 if success else 1),result.output
    assert events==['reset']
