from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from jobagent.tools import temp_mail, challenge


def setup(monkeypatch, *, blocked_docker=False, address='mailbox@example.test', block_desktop=False):
    events = []
    clock = [0.0]

    class Field:
        def count(self): return 1
        def input_value(self, **kw): return address

    class Page:
        url = temp_mail.URL
        def locator(self, selector):
            assert selector == 'input#mail:visible'
            return Field()
        def goto(self, url, **kw): events.append(('goto', self.mode, url))
        def wait_for_timeout(self, ms): clock[0] += ms / 1000

    @contextmanager
    def context(mode, url, settings):
        assert url == temp_mail.URL
        events.append(('open', mode))
        page = Page(); page.mode = mode
        ctx = SimpleNamespace(pages=[page])
        if mode == 'desktop':
            ctx.jobagent_page = page
            ctx.jobagent_policy = lambda p: events.append(('policy', p.mode))
        yield ctx
        events.append(('disconnect', mode))

    monkeypatch.setattr(temp_mail.browser, 'application_context', context)
    monkeypatch.setattr(temp_mail.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(temp_mail.challenge, 'detect', lambda p:
        challenge.Challenge('cloudflare', temp_mail.URL, 'blocked')
        if (p.mode == 'docker' and blocked_docker) or (p.mode == 'desktop' and block_desktop) else None)
    return events


def test_auto_retries_a_real_docker_challenge_through_existing_desktop_scope(monkeypatch):
    events = setup(monkeypatch, blocked_docker=True)
    result = temp_mail.open_mailbox(wait_s=2, log=lambda _: None)
    assert result == {'email': 'mailbox@example.test', 'browser_mode': 'desktop', 'desktop_window_left_open': True}
    assert [e for e in events if e[0] == 'open'] == [('open', 'docker'), ('open', 'desktop')]
    assert ('disconnect', 'docker') in events and ('policy', 'desktop') in events
    assert not any(e[:2] == ('goto', 'desktop') for e in events)  # preserve an existing inbox/message


def test_docker_success_does_not_open_desktop(monkeypatch):
    events = setup(monkeypatch)
    assert temp_mail.open_mailbox(wait_s=2)['browser_mode'] == 'docker'
    assert ('open', 'desktop') not in events


def test_loading_address_does_not_trigger_desktop_or_get_returned(monkeypatch):
    events = setup(monkeypatch, address='Loading..')
    with pytest.raises(challenge.Blocked, match='not ready'):
        temp_mail.open_mailbox(wait_s=2)
    assert ('open', 'desktop') not in events


def test_desktop_challenge_is_never_clicked_and_expires(monkeypatch):
    events = setup(monkeypatch, block_desktop=True)
    messages = []
    with pytest.raises(challenge.Blocked, match='window remains open'):
        temp_mail.open_mailbox('desktop', wait_s=2, log=messages.append)
    assert len(messages) == 1 and 'human verification' in messages[0]
    assert ('open', 'docker') not in events


def test_address_is_not_read_from_a_redirect_or_premium_signup_field():
    page = SimpleNamespace(url='https://example.test/', locator=lambda _: pytest.fail('Wrong origin'))
    with pytest.raises(challenge.Blocked): temp_mail._address(page)


@pytest.mark.parametrize('mode,wait', [('other', 1), ('auto', 0), ('desktop', 3601)])
def test_bad_parameters_are_rejected_without_opening_a_browser(mode, wait):
    with pytest.raises(ValueError): temp_mail.open_mailbox(mode, wait_s=wait)
