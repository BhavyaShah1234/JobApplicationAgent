from unittest.mock import Mock

import pytest
from playwright.sync_api import Error as PlaywrightError, sync_playwright

from jobagent.tools import ats_providers as providers


@pytest.mark.parametrize('clipboard', [True, False])
def test_rich_editor_receives_real_input_transactions_and_blur(clipboard, monkeypatch):
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        context=browser.new_context()
        context.grant_permissions(['clipboard-read','clipboard-write'],origin='https://fixture.test')
        page=context.new_page()
        page.route('**/*',lambda r:r.fulfill(content_type='text/html',body='''
          <div id=editor contenteditable=true>Old posting</div><button>Next</button>
          <script>window.pastes=0;window.blurred=false;window.model='';
          editor.addEventListener('paste',()=>window.pastes++);
          editor.addEventListener('input',()=>window.model=editor.innerText);
          editor.addEventListener('blur',()=>window.blurred=true);</script>'''))
        page.goto('https://fixture.test/')
        if not clipboard:
            evaluate=page.evaluate
            def no_clipboard(expression,*args,**kwargs):
                if 'navigator.clipboard' in expression:raise PlaywrightError('Clipboard unavailable')
                return evaluate(expression,*args,**kwargs)
            monkeypatch.setattr(page,'evaluate',no_clipboard)
        text='Build Python services.\nDeploy with Kubernetes.'
        actual=providers._paste_rich_text(page,page.locator('#editor'),text)
        assert actual.split()==text.split()
        assert page.evaluate('model').split()==text.split()
        assert page.evaluate('blurred')
        assert page.evaluate('pastes') == int(clipboard)
        browser.close()


def test_skillsyncer_uploads_before_committing_the_jd_editor_and_rejects_validation(monkeypatch,tmp_path):
    page=Mock();events=[]
    page.locator.return_value.last.set_input_files.side_effect=lambda _:events.append('upload')
    def paste(*args):events.append('jd');return 'Complete posting'
    monkeypatch.setattr(providers,'_paste_rich_text',paste)
    monkeypatch.setattr(providers,'google_login',lambda *a,**kw:None)
    text=iter(['Scans: 2','Scans: 2','This is too short to be a Job Posting.'])
    monkeypatch.setattr(providers,'_text',lambda _:next(text))
    with pytest.raises(RuntimeError,match='job-description editor'):
        providers.skillsyncer(page,tmp_path/'resume.pdf','Complete posting','Engineer','Acme')
    assert events==['upload','jd']
    page.get_by_role.return_value.wait_for.assert_not_called()
