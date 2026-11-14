"""Preserve the existing policy: a selected empty Address Line 2 clears a stale prefill."""
import pytest
from playwright.sync_api import sync_playwright
from jobagent.experiments.engine import Candidate
from jobagent.experiments.resolution import compile_facts,resolve
from jobagent.tools.forms import Field


def test_an_explicit_blank_address_component_is_distinct_from_an_unknown_one():
    field=Field(id=0,kind='text',label='Address Line 2',section='Contact information')
    known={'Contact':[{'question':'Current address','answer':{'line2':''}}]}
    missing={'Contact':[{'question':'Current address','answer':{}}]}
    assert resolve(field,compile_facts(known,'')).value==''
    assert resolve(field,compile_facts(missing,'')).value is None


def test_selected_blank_line2_is_cleared_without_asking_or_submitting():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page();page.set_content('<h2>Contact information</h2>'
                '<label for=line2>Address Line 2</label><input id=line2 value="Old Unit 4">'
                '<button type=button onclick="window.sent=true">Apply</button>')
            data={'Contact':[{'question':'Current address','answer':{'line2':''}}]}
            result=Candidate(compile_facts(data,''),lambda q:pytest.fail(str(q))).fill(page)
            assert result['ready'] and page.locator('#line2').input_value()==''
            assert not page.evaluate('!!window.sent')
        finally:browser.close()
