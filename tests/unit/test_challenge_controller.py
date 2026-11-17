from types import SimpleNamespace

import pytest

from jobagent.tools import challenge, desktop_probe
from jobagent.tools.hitl import HitlTimeout
from page_fakes import EventPage


class Page(EventPage):
    url = 'https://www.carvana.com/careers/apply?token=secret'
    clock = 0

    def bring_to_front(self):
        pass

    def wait_for_timeout(self, milliseconds):
        self.clock += milliseconds / 1000


def setup(monkeypatch, tmp_path, detect, hitl, ready=lambda page: True):
    page = Page()
    monkeypatch.setattr(challenge, 'detect', detect)
    monkeypatch.setattr(challenge, 'screenshot', lambda *args: None)
    monkeypatch.setattr(challenge.time, 'monotonic', lambda: page.clock)
    controller = challenge.Controller(hitl, ready, tmp_path, lambda message: None)
    return page, controller


def test_done_does_not_clear_a_challenge(monkeypatch, tmp_path):
    calls = []

    def ask(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return 'done'
        raise HitlTimeout('not solved')

    page, controller = setup(monkeypatch, tmp_path, lambda p: challenge.Challenge('unknown', p.url, ''),
                             SimpleNamespace(ask=lambda *a, **kw: ask(**kw)))
    with pytest.raises(challenge.Blocked):
        controller.wait(page, challenge.Challenge('unknown', page.url, ''))
    assert len(calls) == 2
    assert 'secret' not in next(tmp_path.glob('*.json')).read_text()


def test_readiness_requires_three_spaced_checks(monkeypatch, tmp_path):
    page, controller = setup(monkeypatch, tmp_path, lambda p: None,
                             SimpleNamespace(ask=lambda *a, **kw: 'done'))
    controller.wait(page, challenge.Challenge('unknown', page.url, ''))
    assert page.clock >= 4


def test_transition_resets_readiness_streak(monkeypatch, tmp_path):
    page, controller = setup(monkeypatch, tmp_path, lambda p: None,
                             SimpleNamespace(ask=lambda *a, **kw: 'done'), ready=lambda p: p.clock != 4)
    controller.wait(page, challenge.Challenge('unknown', page.url, ''))
    assert page.clock >= 10


def test_desktop_field_stage_requires_verified_read_stage(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(desktop_probe, 'validate_process', lambda *args: None)
    (tmp_path / 'session.json').write_text(json.dumps({'stage': 'manual'}))
    monkeypatch.setattr(desktop_probe, 'sync_playwright', lambda: pytest.fail('Must not connect'))
    with pytest.raises(RuntimeError, match='read-only stage'):
        desktop_probe.probe(tmp_path, 'field', 'name', 'value')


def test_cycle_limit_and_skip_are_blocked(monkeypatch, tmp_path):
    page, controller = setup(monkeypatch, tmp_path, lambda p: None,
                             SimpleNamespace(ask=lambda *a, **kw: 'skip'))
    for _ in range(2):
        with pytest.raises(challenge.Blocked, match='skipped'):
            controller.wait(page, challenge.Challenge('unknown', page.url, ''))
    with pytest.raises(challenge.Blocked, match='limit'):
        controller.wait(page, challenge.Challenge('unknown', page.url, ''))


def test_detect_timeout_and_blank_are_unknown():
    page = SimpleNamespace(url='about:blank', title=lambda: '', locator=lambda selector: SimpleNamespace(
        inner_text=lambda **kwargs: ''))
    assert challenge.detect(page).kind == 'unknown'
    page.title = lambda: (_ for _ in ()).throw(RuntimeError('navigating'))
    assert challenge.detect(page).kind == 'unknown'


@pytest.mark.parametrize('url', ['https://evil.com/careers/', 'https://carvana.com.evil.com/careers/',
                               'http://www.carvana.com/careers/', 'https://www.carvana.com/settings'])
def test_desktop_rejects_unapproved_domains(url):
    assert not desktop_probe.allowed(url)


def test_desktop_rejects_another_profile(tmp_path):
    with pytest.raises(RuntimeError, match='identity'):
        desktop_probe.validate_process(tmp_path, {'profile': '/personal/chrome', 'port': 9223})


@pytest.mark.parametrize('raw', [
    b'/opt/google/chrome/chrome\0--user-data-dir=/dedicated/profile\0--remote-debugging-port=9223\0',
    b'/opt/google/chrome/chrome --user-data-dir=/dedicated/profile --remote-debugging-port=9223\0',
])
def test_desktop_process_identity_handles_chrome_process_titles(raw):
    assert desktop_probe.process_arguments(raw) == [
        '/opt/google/chrome/chrome', '--user-data-dir=/dedicated/profile', '--remote-debugging-port=9223',
    ]


def test_unexpected_tab_stops_probe():
    target = SimpleNamespace(url='https://www.carvana.com/careers/apply')
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[target, SimpleNamespace(url='https://mail.google.com')])])
    with pytest.raises(RuntimeError, match='Unexpected'):
        desktop_probe.check_pages(browser, target)


@pytest.mark.parametrize('stage,visible', [('read', True), ('field', True), ('field', False)])
def test_desktop_exposes_only_a_single_agreed_field(tmp_path, monkeypatch, stage, visible):
    import json
    from contextlib import nullcontext

    class Input:
        writes = []
        def get_attribute(self, key):
            return {'id': 'first_name', 'type': 'text'}.get(key)
        def is_visible(self): return visible
        def is_enabled(self): return True
        def is_editable(self): return True
        def fill(self, value): self.writes.append(value)

    field = Input()
    class DesktopPage(Page):
        url = 'https://www.carvana.com/careers/apply'
        frames = []
        def screenshot(self, **kwargs): pass
        def locator(self, selector):
            return SimpleNamespace(all=lambda: [field], evaluate_all=lambda js: [{'id': 'first_name'}])

    page = DesktopPage()
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])])
    pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda endpoint: browser))
    monkeypatch.setattr(desktop_probe, 'sync_playwright', lambda: nullcontext(pw))
    monkeypatch.setattr(desktop_probe, 'validate_process', lambda *a: None)
    monkeypatch.setattr(challenge, 'detect', lambda p: None)
    monkeypatch.setattr(desktop_probe.time, 'monotonic', lambda: page.clock)
    manifest = {'port': 9223, 'url': page.url, 'stage': 'read_verified'}
    (tmp_path / 'session.json').write_text(json.dumps(manifest))
    if not visible and stage == 'field':
        with pytest.raises(RuntimeError, match='visible'):
            desktop_probe.probe(tmp_path, stage, 'first_name', 'Agreed value')
        assert field.writes == []
    else:
        result = desktop_probe.probe(tmp_path, stage, 'first_name', 'Agreed value')
        assert result['state'] == ('read_verified' if stage == 'read' else 'field_verified')
        assert field.writes == ([] if stage == 'read' else ['Agreed value'])
        assert page.clock >= 30


def test_blocked_application_preserves_checkpoint(tmp_path, monkeypatch):
    import json
    from jobagent.steps import apply

    out = tmp_path / 'application'
    out.mkdir()
    fields = [{'label': 'First name', 'answer': 'Candidate', 'verified': True}]
    (out / 'checkpoint.json').write_text(json.dumps({'fields': fields, 'pages': []}))
    monkeypatch.setattr(apply, '_run', lambda *args, **kwargs: (_ for _ in ()).throw(challenge.Blocked('timeout')))
    result = apply.run(Page(), tmp_path, 'carvana')
    assert result['state'] == 'blocked'
    assert result['resumable']
    assert result['fields'] == fields
    assert result['verified'] == 1


def test_fieldless_startup_preserves_checkpoint_without_inventing_a_bot_challenge(tmp_path, monkeypatch):
    import json
    from jobagent.steps import apply

    out = tmp_path / 'application'
    out.mkdir()
    fields = [{'label': 'First name', 'answer': 'Candidate', 'verified': True}]
    checkpoint = json.dumps({'fields': fields, 'pages': [{'title': 'Earlier page'}]})
    (out / 'checkpoint.json').write_text(checkpoint)
    monkeypatch.setattr(apply, '_run', lambda *args, **kwargs: (_ for _ in ()).throw(
        apply.FormNotReady('no application form and no Apply button')))
    result = apply.run(Page(), tmp_path, 'example')
    assert result['state'] == 'blocked' and result['blocked_by'] == 'page_start'
    assert result['resumable'] and not result['ready_to_submit']
    assert result['desktop_fallback_requested'] is False
    assert result['fields'] == fields and result['verified'] == 1
    assert result['pages'] == [{'title': 'Earlier page'}]
    assert (out / 'checkpoint.json').read_text() == checkpoint
    assert apply.exit_code(result) == apply.EXIT_BLOCKED
    assert 'secret' not in (out / 'application.json').read_text()
    assert 'Nothing was submitted' in (out / 'application.md').read_text()
