"""A confirmed empty 429 is temporary transport failure, not a CAPTCHA to solve."""
import json
from types import SimpleNamespace
import pytest
from jobagent.steps import apply as A
from jobagent.tools import challenge,forms
from page_fakes import EventPage


class EmptyPage(EventPage):
    url='https://careers.example.com/role?token=secret'
    context=SimpleNamespace()

    def goto(self,*args,**kwargs):
        return SimpleNamespace(status=429,headers={'retry-after':'120'})

    def wait_for_timeout(self,*args):
        pass

    def evaluate(self,*args):
        return 429


def no_human(*args,**kwargs):
    """A rate-limit response without a visible challenge needs no human answer."""
    pytest.fail('Must not ask someone to solve an invisible challenge')


@pytest.mark.parametrize('already_open',[False,True])
def test_empty_429_stops_before_challenge_handoff(tmp_path,monkeypatch,already_open):
    page=EmptyPage()
    monkeypatch.setattr(A.forms,'read_page',lambda _:forms.PageView(fields=[],buttons=[],errors=[],frames=[],headings=[]))
    monkeypatch.setattr(A.challenge,'detect',lambda p:challenge.Challenge('unknown',p.url,''))
    with pytest.raises(A.RateLimited) as caught:
        A._start(page,page.url,no_human,lambda _:None,hitl=SimpleNamespace(ask=no_human),already_open=already_open)
    assert 'secret' not in str(caught.value)
    assert caught.value.retry_after==('' if already_open else '120')


def test_rate_limited_desktop_stops_before_its_initial_challenge_guard(monkeypatch):
    page=EmptyPage()
    monkeypatch.setattr(A.forms,'read_page',lambda _:forms.PageView(fields=[],buttons=[],errors=[],frames=[],headings=[]))
    monkeypatch.setattr(A.challenge,'detect',lambda p:challenge.Challenge('unknown',p.url,''))
    with pytest.raises(A.RateLimited):A.recover_desktop_form(page)


def test_visible_captcha_retains_the_human_verification_flow(monkeypatch):
    page=EmptyPage();asked=[]
    monkeypatch.setattr(A.forms,'read_page',lambda _:forms.PageView(fields=[],buttons=[],errors=[],frames=[],headings=[]))
    monkeypatch.setattr(A.challenge,'detect',lambda p:challenge.Challenge('hcaptcha',p.url,''))
    def human(*args,**kwargs):
        """Stop at a visible challenge rather than exercising a CAPTCHA control."""
        asked.append(1);raise challenge.Blocked('manual verification needed')
    monkeypatch.setattr(A.challenge,'wait_for_user',human)
    with pytest.raises(challenge.Blocked):A._start(page,page.url,no_human,lambda _:None,hitl=object())
    assert asked==[1]


@pytest.mark.parametrize('timing',[0,None,200,'429',False,{'responseStatus':429}])
def test_rate_limit_is_not_inferred_from_unknown_or_untyped_timing(timing):
    page=EmptyPage();page.evaluate=lambda *args:timing
    A._check_start_rate_limit(page,None)


def test_rate_limit_preserves_checkpoint_without_requesting_desktop_fallback(tmp_path,monkeypatch):
    out=tmp_path/'application';out.mkdir();fields=[{'label':'Name','verified':True}]
    checkpoint=json.dumps({'fields':fields,'pages':[{'title':'Earlier page'}]})
    (out/'checkpoint.json').write_text(checkpoint)
    def limited(*args,**kwargs):
        """Stand in for startup's confirmed HTTP status failure."""
        raise A.RateLimited(EmptyPage.url,'120')
    monkeypatch.setattr(A,'_run',limited)
    result=A.run(EmptyPage(),tmp_path,'example',filling_strategy='adaptive',desktop_fallback=True)
    assert result['state']=='blocked' and result['blocked_by']=='rate_limit'
    assert result['resumable'] and not result['ready_to_submit'] and not result['desktop_fallback_requested']
    assert result['retry_after']=='120' and result['fields']==fields and result['verified']==1
    assert (out/'checkpoint.json').read_text()==checkpoint
    assert 'secret' not in (out/'application.json').read_text()
    assert A.exit_code(result)==A.EXIT_BLOCKED

