import json
import pytest
from playwright.sync_api import sync_playwright
from jobagent.experiments.live_compare import VisualObserver,TrialStopped
from jobagent.filling.metrics import FillingMetrics


@pytest.mark.parametrize('action',['continue','stop'])
def test_observation_preserves_application_values_and_reader_tags(tmp_path,action):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page()
        page.set_content('<label for=x>Name</label><input id=x data-jf=17 value="Owner supplied">'
                         '<button type=button onclick="window.sent=true">Submit</button>')
        control=tmp_path/'control';control.mkdir()
        (control/'inspection-1.json').write_text(json.dumps({'action':action,'reason':'test audit'}))
        metrics=FillingMetrics();metrics.begin();observer=VisualObserver(tmp_path,[metrics],True)
        if action=='stop':
            with pytest.raises(TrialStopped):observer.snapshot(page,'page-filled',gate=True)
        else:observer.snapshot(page,'page-filled',gate=True)
        assert page.locator('#x').input_value()=='Owner supplied'
        assert page.locator('#x').get_attribute('data-jf')=='17'
        assert not page.evaluate('!!window.sent')
        assert observer.overhead_s>0 and (tmp_path/'visual/0001.png').exists()
        assert json.loads((tmp_path/'visual-events.json').read_text())[0]['inspection']['action']==action
        assert not (control/'inspection-pending.json').exists()
        browser.close()
