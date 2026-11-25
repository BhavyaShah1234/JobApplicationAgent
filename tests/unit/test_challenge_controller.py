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


def test_a_done_answered_just_before_the_deadline_still_gets_its_readiness_checks(monkeypatch, tmp_path):
    holder = {}

    def ask(*a, **kw):
        holder["page"].clock = 899          # the person answered 'done' one second before the 15-minute deadline
        return 'done'

    page, controller = setup(monkeypatch, tmp_path, lambda p: None, SimpleNamespace(ask=ask))
    holder["page"] = page
    controller.wait(page, challenge.Challenge('unknown', page.url, ''))     # must not raise Blocked
    assert page.clock >= 899


def test_repeated_done_cannot_extend_an_unresolved_challenge_indefinitely(monkeypatch, tmp_path):
    calls = []
    def ask(*args, **kwargs):
        calls.append(1)
        if len(calls) > 20:
            raise HitlTimeout('test safety bound: readiness never became true')
        return 'done'
    page, controller = setup(monkeypatch, tmp_path, lambda page: challenge.Challenge('unknown', page.url, ''),
                             SimpleNamespace(ask=ask), ready=lambda page: False)
    with pytest.raises(challenge.Blocked, match='Verification deadline expired'):
        controller.wait(page, challenge.Challenge('unknown', page.url, ''), timeout_s=5)
    assert page.clock <= 101
    assert len(calls) <= 20


class _Frame:
    def __init__(self, url):
        self.url = url


class _BlankPage:
    """A page whose top-level body is empty and whose only content is a provider's frame (SmartRecruiters + DataDome)."""
    url = "https://jobs.smartrecruiters.com/oneclick-ui/company/X/publication/1"

    def __init__(self, frame_urls):
        self.main_frame = _Frame(self.url)
        self.frames = [self.main_frame, *[_Frame(u) for u in frame_urls]]

    def title(self):
        return "smartrecruiters.com"

    def locator(self, selector):
        return SimpleNamespace(inner_text=lambda timeout=None: "")


def test_a_blank_page_with_a_datadome_frame_is_that_challenge_for_the_human_not_unknown():
    block = challenge.detect(_BlankPage(["https://geo.captcha-delivery.com/captcha/?initialCid=AHrlqAA"]))
    assert block is not None and block.kind == "datadome"


def test_a_blank_page_without_a_verification_frame_stays_unknown_and_a_tracking_frame_is_not_a_challenge():
    assert challenge.detect(_BlankPage([])).kind == "unknown"
    assert challenge.detect(_BlankPage(["about:blank", "https://www.googletagmanager.com/ns.html"])).kind == "unknown"


def test_access_temporarily_restricted_is_a_stop_not_a_puzzle_and_is_never_waited_on(monkeypatch, tmp_path):
    class RestrictedPage(Page):
        pass
    asked = []
    page, controller = setup(monkeypatch, tmp_path, lambda p: None, SimpleNamespace(ask=lambda *a, **kw: asked.append(1)))
    block = challenge.Challenge("access-restricted", page.url, "Verification detected")
    with pytest.raises(challenge.Blocked) as e:
        controller.wait(page, block)
    assert "Do not retry now" in str(e.value) and not asked          # no human request is raised for it


def test_the_restriction_page_text_is_recognised():
    text = ("Access is temporarily restricted\\nWe detected unusual activity from your device or network.\\nReasons may include:\\n"
            "Rapid taps or clicks\\nAutomated (bot) activity on your network")
    assert any(kind == "access-restricted" and pat.search(text) for kind, pat in challenge._TEXT_SIGNS)
    assert not any(kind == "access-restricted" and pat.search("Apply now. Access to benefits is restricted to employees.") for kind, pat in challenge._TEXT_SIGNS)


def test_a_turnstile_widget_in_an_iframe_is_detected_even_when_it_reports_verification_failed():
    """UKG's sign-up: a Turnstile in an iframe ('Verification failed / Troubleshoot') beside a greyed-out Create account button."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pytest.skip("no Chrome here")
        try:
            page = browser.new_page()
            frame_url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/av0/rcv/x/0xAAA/light/fbE/new/normal/auto/"
            page.context.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html",
                body=(f'<html><body><h2>Almost there!</h2><input placeholder="First name"><iframe src="{frame_url}" style="width:300px;height:65px"></iframe>'
                      '<button disabled>Create account</button></body></html>') if "example.com" in r.request.url
                else "<html><body>Verification failed. Troubleshoot</body></html>"))
            page.goto("https://example.com/signup")
            page.wait_for_timeout(800)
            block = challenge.detect(page)
            assert block is not None and block.kind == "turnstile"
        finally:
            browser.close()


@pytest.mark.parametrize('hidden', [False, True])
def test_datadome_frame_with_posting_footer_needs_human_only_when_visible(hidden):
    """A real posting body/footer does not make its visible verification iframe disappear."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page()
            source = 'https://jobs.example.test/application'
            frame = 'https://geo.captcha-delivery.com/captcha/?redacted=fixture'
            style = 'display:none' if hidden else 'width:360px;height:260px'
            page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html',
                body=(f'<h1>Developer</h1><button>Reload</button><button>Cookie Settings</button>'
                      '<textarea name=g-recaptcha-response hidden>unrelated completed widget</textarea>'
                      f'<iframe title="Verification system" src="{frame}" style="{style}"></iframe>')
                if route.request.url == source else '<h1>Human verification fixture</h1>'))
            page.goto(source)
            result = challenge.detect(page)
            assert (result is not None and result.kind == 'datadome') if not hidden else result is None
        finally:
            browser.close()
