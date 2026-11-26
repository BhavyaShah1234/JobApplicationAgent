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


@pytest.mark.parametrize('attributes', ['type=password', 'type=password role=button', 'type=text role=button autocomplete=current-password'])
def test_unlabelled_password_value_never_becomes_the_auth_field_label(page, attributes):
    page.set_content('<input ' + attributes + '><button>Continue</button>')
    page.locator('input').fill('Synthetic-secret-not-a-label')
    view, frames = agent.snapshot(page)
    assert 'Synthetic-secret' not in view
    assert frames.items_by_frame[0][0]['label'] == 'Password'


@pytest.mark.parametrize('html,expected', [
    ('<button name="fbclc_dataPrivacyAcceptButton">Accept</button>', 'Accept'),
    ('<input type=button name="fbclc_acceptButton" value="Accept">', 'Accept'),
    ('<label for=terms>Terms of Use:*</label><a id=terms role=button aria-label="Read and accept the required privacy statement">Read statement</a>',
     'Read and accept the required privacy statement'),
    ('<span id=caption>Accept privacy terms</span><button name=internal aria-labelledby=caption>Continue</button>',
     'Accept privacy terms'),
])
def test_action_caption_precedes_internal_machine_names_and_generic_labels(page, html, expected):
    page.set_content(html)
    view, frames = agent.snapshot(page)
    items = frames.items_by_frame[0]
    index = next(i for i, item in enumerate(items) if item['label'] == expected)
    target = agent._target(frames, agent.AuthAction(thought='Use the captured action', action='click', element=index))
    assert target is not None
    assert expected in view


def test_privacy_dialog_controls_and_text_are_not_drowned_by_background_registration(page):
    page.set_content('<h1>Create an account</h1>' + ''.join(f'<button>Background {i}</button>' for i in range(90)) +
                     '<div role=dialog aria-modal=true style="position:fixed;inset:10px;background:white;z-index:10">'
                     '<h2>Data Privacy Consent Statement</h2><p>Required privacy terms for account creation.</p>'
                     '<button name=accept onclick="window.accepted=true;this.parentElement.remove()">Accept</button>'
                     '<button>Decline</button><button aria-label=Close>×</button></div>')
    view, frames = agent.snapshot(page)
    assert [x['label'] for x in frames.items_by_frame[0]] == ['Accept', 'Decline', 'Close']
    assert 'ACTIVE_DIALOG:' in view and 'Required privacy terms' in view
    assert 'Background 0' not in view and 'Create an account' not in view
    target = agent._target(frames, agent.AuthAction(thought='Accept the required account terms', action='click', element=0))
    target.click()
    assert page.evaluate('window.accepted === true')
    assert 'Background 0' in agent.snapshot(page)[0]


def test_dialog_appearing_during_model_call_invalidates_background_action(page):
    page.set_content('<label>First name<input id=first></label>')
    _, frames = agent.snapshot(page)
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div role=dialog><h2>Privacy terms</h2><button>Accept</button></div>')")
    assert agent._target(frames, action()) is None
    assert page.locator('#first').input_value() == ''


@pytest.mark.parametrize('hidden', ['hidden', 'style="display:none"', 'aria-hidden=true'])
def test_inactive_dialog_does_not_hide_registration_controls(page, hidden):
    page.set_content('<label>First name<input></label><button>Create Account</button>'
                     '<div role=dialog ' + hidden + '><button>Accept</button></div>')
    view, frames = agent.snapshot(page)
    assert 'ACTIVE_DIALOG:' not in view
    assert any(x['label'] == 'First name' for x in frames.items_by_frame[0])


def test_parent_dialog_hides_and_invalidates_actions_in_a_background_frame(page):
    page.route('https://auth.example.test/**', lambda r: r.fulfill(content_type='text/html', body='<label>First name<input id=first></label>'))
    page.set_content('<iframe src="https://auth.example.test/form"></iframe>')
    page.frame_locator('iframe').locator('#first').wait_for()
    _, old = agent.snapshot(page)
    assert 1 in old
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div role=dialog><button>Accept</button></div>')")
    view, frames = agent.snapshot(page)
    assert 'First name' not in view and 1 not in frames
    assert agent._target(old, agent.AuthAction(thought='Old background answer', action='fill', frame=1, element=0)) is None


def test_frame_inside_the_active_dialog_remains_available(page):
    page.route('https://auth.example.test/**', lambda r: r.fulfill(content_type='text/html', body='<button>Accept required terms</button>'))
    page.set_content('<div role=dialog><iframe src="https://auth.example.test/privacy"></iframe></div>')
    page.frame_locator('iframe').get_by_role('button').wait_for()
    view, frames = agent.snapshot(page)
    assert 'Accept required terms' in view and 1 in frames
    target = agent._target(frames, agent.AuthAction(thought='Use the visible dialog frame', action='click', frame=1, element=0))
    assert target is not None
