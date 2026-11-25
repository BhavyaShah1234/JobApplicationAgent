"""A model decision cannot follow an old numeric index into a replacement signup page."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import portal_agent as agent


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


def action(index=0):
    return agent.AuthAction(thought='Fill the observed first name', action='fill', element=index,
                            value='{FIRST_NAME}')


def test_render_during_model_call_invalidates_target_instead_of_filling_replacement(page):
    page.set_content('<label>First name<input id="first"></label>')
    _, frames = agent.snapshot(page)
    # The redirect / SPA render happens AFTER discovery but BEFORE the model returns.
    page.evaluate("document.body.innerHTML = '<label>Verification code<input id=code></label>'")
    assert agent._target(frames, action()) is None
    assert agent._label(frames, action()) == 'First name'
    assert page.locator('#code').input_value() == ''


def test_inserted_control_does_not_shift_action_to_another_field(page):
    page.set_content('<label>First name<input id="first"></label>')
    _, frames = agent.snapshot(page)
    page.evaluate("document.body.insertAdjacentHTML('afterbegin', '<button>Cancel</button>')")
    target = agent._target(frames, action())
    assert target.get_attribute('id') == 'first'
    target.fill('Jamie')
    assert page.locator('#first').input_value() == 'Jamie'


def test_navigation_to_an_identical_document_still_requires_fresh_discovery(page):
    html = '<label>First name<input id="first"></label>'
    page.set_content(html)
    _, old_frames = agent.snapshot(page)
    page.goto('about:blank')
    page.set_content(html)
    assert agent._target(old_frames, action()) is None
    _, fresh_frames = agent.snapshot(page)
    assert agent._target(fresh_frames, action()).get_attribute('id') == 'first'


def test_read_only_label_probe_does_not_invalidate_observed_target(page):
    page.set_content('<label>First name<input id="first"></label>')
    _, frames = agent.snapshot(page)
    page.evaluate(agent.SNAPSHOT_JS)
    assert agent._label(frames, action()) == 'First name'
    assert agent._target(frames, action()).get_attribute('id') == 'first'


def test_action_cannot_target_an_element_outside_the_presented_inventory(page):
    page.set_content(''.join(f'<button>Choice {i}</button>' for i in range(81)))
    _, frames = agent.snapshot(page)
    assert len(frames.items_by_frame[0]) == 80
    assert agent._target(frames, action(80)) is None
    assert agent._target(frames, action(-1)) is None


@pytest.mark.parametrize('mutation', [
    "document.querySelector('label').firstChild.textContent = 'Phone number'",
    "document.querySelector('input').type = 'password'",
    "document.querySelector('input').name = 'verification-code'",
])
def test_reused_node_with_changed_question_or_type_is_not_the_observed_control(page, mutation):
    page.set_content('<label>First name<input id="first"></label>')
    _, frames = agent.snapshot(page)
    page.evaluate(mutation)
    assert agent._target(frames, action()) is None


def test_unlabelled_password_value_never_becomes_the_auth_field_label(page):
    page.set_content('<input type=password><button>Continue</button>')
    page.locator('input').fill('Synthetic-secret-not-a-label')
    view, frames = agent.snapshot(page)
    assert 'Synthetic-secret' not in view
    assert frames.items_by_frame[0][0]['label'] == 'Password'
