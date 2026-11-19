"""Real-browser application-code continuation, with a private, synthetic inbox."""
import json
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import sync_playwright

from jobagent import db
from jobagent.steps import submit
from jobagent.tools import challenge, forms
from jobagent.tools.application_verification import EmailCodeVerifier, code_fields
from jobagent.tools.inbox import Inbox, Mail

URL = 'https://job-boards.greenhouse.io/acme/jobs/123'
JD = {'company': 'Acme', 'title': 'Engineer', 'apply_url': URL}
NOTICE = ('A verification code was sent to agent@example.test. To submit your application, '
          "enter the 8-character code to confirm you're a human.")


@pytest.fixture
def world(tmp_path):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        page.route('**/*', lambda r: r.fulfill(content_type='text/html', body=''))
        page.goto(URL)
        page.set_content('''<h1>Application</h1><form>
          <label>Email<input id=email value="agent@example.test" required></label>
          <label>Resume<input id=resume type=file></label>
          <button type=button id=send>Submit application</button></form>
          <script>window.clicks=0;document.querySelector('#send').onclick=()=>{
            window.clicks++;
            const boxes=[...document.querySelectorAll('.code')];
            if(boxes.length && boxes.map(e=>e.value).join('')==='AB12CD34'){
              document.querySelector('form').remove();document.body.insertAdjacentHTML('beforeend','<h2>Thank you for applying</h2>');
            }
          }</script>''')
        page.locator('#resume').set_input_files({'name': 'resume.pdf', 'mimeType': 'application/pdf', 'buffer': b'approved file'})
        job = tmp_path / 'outputs/acme/123'
        (job / 'application').mkdir(parents=True)
        conn = db.connect(tmp_path / 'jobs.sqlite')
        inbox = Inbox('agent@example.test', 'unused', conn)
        since = datetime.now(UTC)
        mail = Mail('1', '<code@example.test>', 'Acme <no-reply@us.greenhouse-mail.io>',
                    'Security code for your application to Acme', since,
                    'Copy this code into the security code field:\r\n \r\nAB12CD34\r\n', recipients=('agent@example.test',))
        state = {'page': page, 'job': job, 'inbox': inbox, 'since': since, 'mail': mail, 'messages': [mail], 'reads': 0, 'logs': []}

        def wait(predicate, criteria, timeout_s=0, header_filter=None):
            for message in reversed(state['messages']):
                if header_filter(message):
                    state['reads'] += 1
                    if predicate(message):
                        return message
            raise TimeoutError('no matching verification')

        inbox.wait_for = wait
        state['sha'] = submit.freeze_payload(page, forms.read_page(page), [], job)[0]
        page.locator('#send').click()  # the already approved initial dispatch
        yield state
        browser.close()
        conn.close()


def verifier(w):
    return EmailCodeVerifier(w['inbox'], JD, w['since'], w['job'], 1, w['sha'], [], w['logs'].append,
                            approved_url=URL, approved_frames={0: URL})


def add_code(w, split=False):
    boxes = ('<input class=code maxlength=1>' * 8 if split else
             '<input class=code maxlength=8 autocomplete=one-time-code>')
    w['page'].locator('form').evaluate('(form,html)=>form.insertAdjacentHTML("beforeend",html)',
                                     '<p>' + NOTICE + '</p><fieldset><legend>Security code</legend>' + boxes + '</fieldset>')


@pytest.mark.parametrize('split', [False, True])
def test_ordinary_email_code_completes_same_attempt_and_requires_receipt(world, split):
    w = world; add_code(w, split)
    v = verifier(w)
    assert v(w['page']), (v.audit.get('phase'), v.audit.get('error_type'), w['logs'], w['reads'])
    assert 'Thank you for applying' in w['page'].inner_text('body')
    assert w['page'].evaluate('window.clicks') == 2  # initial dispatch + code continuation
    assert not v(w['page'])  # one continuation only
    assert w['inbox'].conn.execute("select status from agent_actions").fetchone()[0] == 'open'
    v.accepted()  # only the caller's receipt observation grants ownership
    assert w['inbox'].conn.execute("select status from agent_actions").fetchone()[0] == 'consumed'
    evidence = (w['job'] / 'application/email-verification-1.json').read_text()
    assert json.loads(evidence)['state'] == 'receipt_observed'
    assert 'AB12CD34' not in evidence + '\n'.join(w['logs'])


@pytest.mark.parametrize('change', ['answer', 'upload', 'new_question', 'url'])
def test_changed_approved_application_never_gets_code_or_second_dispatch(world, change):
    w=world; add_code(w)
    if change == 'answer': w['page'].locator('#email').fill('changed@example.test')
    if change == 'upload': w['page'].locator('#resume').set_input_files({'name':'resume.pdf','mimeType':'application/pdf','buffer':b'different'})
    if change == 'new_question': w['page'].locator('form').evaluate('e=>e.insertAdjacentHTML("beforeend","<label>New answer<input value=yes></label>")')
    if change == 'url': w['page'].evaluate("history.pushState({},'', '/acme/jobs/456')")
    assert not verifier(w)(w['page'])
    assert w['page'].evaluate('window.clicks') == 1 and w['reads'] == 0
    assert w['page'].locator('.code').input_value() == ''


@pytest.mark.parametrize('bad', ['recipient', 'company', 'sender', 'stale'])
def test_unrelated_mail_body_is_not_used(world, bad):
    w=world;add_code(w);mail=w['mail']
    if bad == 'recipient': mail.recipients=('someone@example.test',)
    if bad == 'company': mail.sender='Greenhouse <no-reply@us.greenhouse-mail.io>';mail.subject='Security code for your application to OtherCo'
    if bad == 'sender': mail.sender='Acme <bad@unrelated.test>'
    if bad == 'stale': mail.date=w['since']-timedelta(seconds=10)
    assert not verifier(w)(w['page'])
    assert w['reads'] == 0 and w['page'].evaluate('window.clicks') == 1


def test_real_visible_captcha_is_not_an_email_code(world):
    w=world;add_code(w)
    w['page'].locator('form').evaluate('e=>e.insertAdjacentHTML("beforeend",\'<iframe title="reCAPTCHA" src="https://www.google.com/recaptcha/api2/anchor" style="width:300px;height:100px"></iframe>\')')
    assert challenge.detect(w['page']).kind == 'application-email-code'
    assert challenge.detect(w['page'], include_application_email=False).kind == 'recaptcha'
    assert not verifier(w)(w['page'])
    assert w['reads'] == 0 and w['page'].evaluate('window.clicks') == 1


def test_payload_changed_while_waiting_for_mail_is_not_dispatched(world):
    w=world;add_code(w);real=w['inbox'].wait_for

    def changed(*args, **kwargs):
        result=real(*args,**kwargs)
        w['page'].locator('#email').fill('changed@example.test')
        return result

    w['inbox'].wait_for=changed
    assert not verifier(w)(w['page'])
    assert w['page'].evaluate('window.clicks') == 1


def test_missing_or_ambiguous_code_controls_require_attention(world):
    w=world;add_code(w)
    w['page'].locator('form').evaluate('e=>e.insertAdjacentHTML("beforeend",\'<label>Verification code<input maxlength=8></label>\')')
    assert code_fields(forms.read_page(w['page'])) == []
    assert not verifier(w)(w['page'])
    assert w['reads'] == 0 and w['page'].evaluate('window.clicks') == 1


def test_large_maxlength_is_a_capacity_not_the_requested_code_length(world):
    w=world;add_code(w)
    w['page'].locator('.code').evaluate('e=>e.maxLength=64')
    v=verifier(w)
    assert v(w['page']), (v.audit, w['logs'], w['reads'])
    assert 'Thank you for applying' in w['page'].inner_text('body')


def test_wrong_length_code_is_not_entered(world):
    w=world;add_code(w)
    w['mail'].text='Your verification code is 123456.'
    assert not verifier(w)(w['page'])
    assert w['page'].locator('.code').input_value() == ''
    assert w['page'].evaluate('window.clicks') == 1


def test_a_new_frame_cannot_receive_the_application_code(world):
    w=world;add_code(w)
    v=verifier(w)
    # The frame URL was not part of the reviewed application, even if its
    # controls and the rest of the page imitate the original exactly.
    v.approved_frames={0: 'https://job-boards.greenhouse.io/acme/jobs/other'}
    assert not v(w['page'])
    assert w['reads'] == 0 and w['page'].locator('.code').input_value() == ''


def test_rejected_code_is_not_owned_or_dispatched_again(world):
    w=world;add_code(w)
    w['mail'].text='Your verification code is AA11BB22.'
    v=verifier(w)
    assert v(w['page']), (v.audit, w['logs'], w['reads'])  # dispatch, not acceptance
    assert not v(w['page'])
    assert w['page'].evaluate('window.clicks') == 2
    assert 'Thank you for applying' not in w['page'].inner_text('body')
    assert w['inbox'].conn.execute('select status from agent_actions').fetchone()[0] == 'open'
    v.human_handoff()
    v.accepted()
    assert w['inbox'].conn.execute('select status from agent_actions').fetchone()[0] == 'open'
    assert v.audit['state'] == 'receipt_after_human_verification'
