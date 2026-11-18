"""Successful repairs reset consecutive failures; explicit optional skips stay skipped."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.filling.facts import Fact
from jobagent.filling.planned import PlannedFiller
from jobagent.tools import forms


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page()
        page.context.jobagent_policy=lambda _:None
        yield page
        browser.close()


def test_a_repaired_field_gets_its_full_retry_budget_after_a_new_validation_failure(page,monkeypatch):
    page.set_content('<label for=k>Known</label><input id=k required>')
    filler=PlannedFiller([Fact('known',['Known'],'Sourced value','applicant')],lambda _:pytest.fail('Known value'))
    original=forms.fill_field;state={'fail_next':True}
    def fail_once(*args,**kwargs):
        if state['fail_next']:
            state['fail_next']=False
            raise forms.FillError('Temporary widget rejection')
        return original(*args,**kwargs)
    monkeypatch.setattr(forms,'fill_field',fail_once)
    first=filler.fill(page,advance_pages=False,max_rounds=4)
    assert first['state']=='page_complete'
    page.locator('#k').fill('')
    state['fail_next']=True
    second=filler.fill(page,advance_pages=False,max_rounds=4,errors=['Known is required'])
    assert second['state']=='page_complete'
    assert page.locator('#k').input_value()=='Sourced value'


def test_a_skipped_optional_search_picker_is_not_reopened_after_other_fields_fill(page):
    page.set_content('''<label for=k>Known</label><input id=k required>
      <label for=o>Optional choice</label><div data-automation-id=multiSelectContainer>
      <input id=o role=combobox data-uxi-widget-type=selectinput onclick="opens++"></div>
      <script>window.opens=0</script>''')
    prompts=[]
    def skip(questions):prompts.append(questions);return {}
    filler=PlannedFiller([Fact('known',['Known'],'Sourced value','applicant')],skip)
    result=filler.fill(page,advance_pages=False,max_rounds=3)
    assert result['state']=='page_complete'
    assert len(prompts)==1 and page.evaluate('opens')==1
    assert page.locator('#o').input_value()==''
