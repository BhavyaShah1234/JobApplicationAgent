"""Readiness must follow real enabled controls after a human-verification handoff."""
import pytest

from jobagent.steps import apply
from jobagent.tools import challenge
from test_filling_strategy import world


@pytest.mark.parametrize('embedded', [False, True])
def test_expired_puzzle_cannot_leave_a_completed_identifier_as_a_ready_page(world, monkeypatch, embedded):
    html = '''<h1>Enter Your Information</h1><form onsubmit="window.sent++;return false">
        <label>Email<input type=email value="candidate@example.test"></label>
        <button id=next disabled></button></form>
        <a href="#privacy">Privacy</a><a href="#terms">Terms</a>
        <script>window.sent=0</script>'''
    if embedded:
        import html as escaped
        html = '<iframe title="Application" srcdoc="'+escaped.escape(html, quote=True)+'"></iframe>'
    world.setup(html)
    world.page.goto('https://fixture.test/apply')
    frame = world.page.frames[-1]

    def probe(page, *args, **kwargs):
        ready = challenge._controllers[page].ready
        assert not ready(page)  # blank disabled native continuation, as observed live
        frame.locator('#next').evaluate("e=>e.textContent='Next'")
        assert not ready(page)  # a caption alone does not enable the control
        frame.locator('#next').evaluate("e=>{e.disabled=false;e.setAttribute('aria-disabled','true')}")
        assert not ready(page)  # custom/ARIA disabling also blocks readiness
        frame.locator('#next').evaluate("e=>e.removeAttribute('aria-disabled')")
        assert ready(page)
        assert frame.evaluate('window.sent') == 0
        return {'ready': True}

    monkeypatch.setattr(apply, '_run', probe)
    assert apply._guarded_run(world.page, world.job, 'fixture', filling_strategy='original')['ready']
