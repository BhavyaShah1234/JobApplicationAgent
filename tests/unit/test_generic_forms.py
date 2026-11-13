import re
import shutil

import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import forms


@pytest.fixture
def page():
    if not shutil.which('google-chrome'):pytest.skip('Chrome required')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page()
        page.context.jobagent_policy=lambda p:None
        yield page
        browser.close()


def test_unseen_aria_radios_and_contenteditable_are_filled_and_read_back(page):
    page.set_content('''<h1>Candidate details</h1><span id="q_opaque">Preferred workplace</span>
      <div role="radiogroup" aria-labelledby="q_opaque" aria-required="true">
       <button role="radio" aria-checked="false" onclick="this.setAttribute('aria-checked','true')">Remote</button>
       <button role="radio" aria-checked="false" onclick="this.setAttribute('aria-checked','true')">Onsite</button>
      </div><div role="textbox" contenteditable="true" aria-label="Professional profile link"></div>''')
    view=forms.read_page(page)
    radio=next(f for f in view.fields if f.kind=='radio')
    assert radio.label=='Preferred workplace' and radio.required
    forms.fill_field(page,view,radio,'Remote',{})
    text=next(f for f in view.fields if f.kind=='text')
    forms.fill_field(page,view,text,'https://profile.example.org/candidate',{})
    values={f.label:f.value for f in forms.read_page(page).fields}
    assert values=={'Preferred workplace':'Remote','Professional profile link':'https://profile.example.org/candidate'}


@pytest.mark.parametrize('button,caption',[('Attach your CV','Candidate CV *'),('Browse files','Additional document')])
def test_lazy_upload_needs_no_known_caption_or_input_id(page,tmp_path,button,caption):
    from jobagent.steps.apply import _key
    pdf=tmp_path/'test.pdf';pdf.write_bytes(b'unseen upload test')
    page.set_content(f'''<section class="form-field"><label>{caption}</label><button onclick="
      const node=document.createElement('input');node.type='file';node.id='opaque-u91';node.hidden=true;
      this.parentElement.append(node)">{button}</button></section>''')
    view=forms.read_page(page);upload=next(f for f in view.fields if f.kind=='file')
    forms.fill_field(page,view,upload,'resume',{'resume':str(pdf)})
    assert page.locator('#opaque-u91').evaluate('e=>e.files[0].size')==pdf.stat().st_size
    mounted=forms.read_page(page).fields
    assert len(mounted)==1 and _key(mounted[0])==_key(upload)


def test_native_upload_does_not_require_filename_in_body_text(page,tmp_path):
    pdf=tmp_path/'native.pdf';pdf.write_bytes(b'native upload test')
    page.set_content('<label for="opaque-u92">Supporting document</label><input id="opaque-u92" type="file">')
    view=forms.read_page(page)
    forms.fill_field(page,view,view.fields[0],str(pdf),{})
    assert forms.read_page(page).fields[0].value=='native.pdf'


def test_desktop_application_links_stay_in_the_dedicated_tab(page):
    page.route('https://apply.unseen-example.org/**',lambda route:route.fulfill(body='<h1>Application</h1>'))
    page.set_content('<a href="https://apply.unseen-example.org/start" target="_blank">Begin Application</a>')
    forms.click_button(page,forms.read_page(page),re.compile('begin application',re.I))
    page.wait_for_url('https://apply.unseen-example.org/start')
    assert len(page.context.pages)==1
