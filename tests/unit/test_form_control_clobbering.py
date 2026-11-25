"""Generated review forms can have controls named elements, action or method."""
import pytest
from playwright.sync_api import sync_playwright
from jobagent.tools import forms
from jobagent.filling.discovery import INVENTORY


@pytest.mark.parametrize('embedded',[False,True])
@pytest.mark.parametrize('sdk_form',[False,True])
def test_named_elements_does_not_hide_fields_or_externally_associated_controls(embedded,sdk_form):
    html='''<h1>Application Review</h1><form id=review method=post>
      <input type=hidden name=elements value=metadata>
      <input type=hidden name=action value=metadata>
      <label>Name<input name=name value="Example Applicant"></label>
      <button>Submit Application</button></form>
      <label>Email<input form=review name=email type=email value=owner@example.test></label>'''
    if sdk_form:html+='''<script>document.querySelectorAll('input,button').forEach(e=>
      Object.defineProperty(e,'form',{value:{elements:{metadata:true}},configurable:true}));</script>'''
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page()
        try:
            def serve(r):
                body='<iframe style="width:900px;height:600px" src="https://forms.example.test/forms"></iframe>' if r.request.url.endswith('/shell') else html
                r.fulfill(content_type='text/html',body=body)
            page.route('**/*',serve);page.goto('https://portal.example.test/shell' if embedded else 'https://forms.example.test/forms')
            frame=page.frames[1] if embedded else page.main_frame
            assert frame.evaluate("() => document.getElementById('review').elements.tagName")=='INPUT'
            view=forms.read_page(page)
            assert view.coverage.complete,view.coverage.summary()
            assert {f.label for f in view.fields}=={'Name','Email'}
            assert {f['label'] for f in frame.evaluate(INVENTORY) if f['visible']} >= {'Name','Email'}
            assert frame.locator('button').evaluate(forms.SENDS_FORM_JS)['sends']
        finally:browser.close()


def test_generated_iform_captions_dates_and_repeating_entity_rows():
    html='''<form><table><tr><td><strong>Position Details</strong></td></tr>
    <tr><td>Position Title<br><span><input class=iCIMS_Forms_Global id=icims_f_TITLE aria-label=TITLE value="Engineer"></span></td>
    <td>Application Date<div class="iCIMS_Forms_Global iCIMS_Forms_DateOnlyField">
      <label for=icims_f_APPLICATION_DATE_Month>Month</label><select class=iCIMS_Forms_Global id=icims_f_APPLICATION_DATE_Month><option>Nov</option></select>
      <label for=icims_f_APPLICATION_DATE_Year>Year</label><input class=iCIMS_Forms_Global id=icims_f_APPLICATION_DATE_Year value=2026>
    </div></td></tr>
    <tr><td><strong>Education</strong></td></tr>
    <tr><td><span>City<br><input class=iCIMS_Forms_Global id=icims_0_EducationCity aria-label=EducationCity value=Tempe></span>
      <span>State/Province<br><input class=iCIMS_Forms_Global id=icims_0_EducationState aria-label=EducationState value=Arizona></span></td></tr>
    <tr><td>Name of Employer<br><input class=iCIMS_Forms_Global id=icims_1_EmpName aria-label=EmpName value="Second Employer"></td></tr>
    <tr><td><strong>Applicant Statement</strong></td></tr>
    <tr><td><span><input class=iCIMS_Forms_Global id=icims_f_signature1 type=checkbox aria-label=Signature1 required>Signature</span>
      <br>By checking the box above you are applying your signature and you agree to this Applicant Statement.</td></tr>
    <tr><td><label for=ordinary>Unrelated field</label><input id=ordinary aria-label="Correct accessible label"></td></tr>
    </table><button>Submit Application</button></form>'''
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page()
        try:
            page.set_content(html);view=forms.read_page(page)
            fields={forms.locate(view,f).get_attribute('id'):f for f in view.fields}
            assert fields['icims_f_TITLE'].label=='Position Title'
            assert fields['icims_f_TITLE'].section=='Position Details'
            assert fields['icims_f_APPLICATION_DATE_Month'].label=='Application Date Month'
            assert fields['icims_f_APPLICATION_DATE_Year'].label=='Application Date Year'
            assert fields['icims_0_EducationCity'].label=='City'
            assert fields['icims_0_EducationCity'].section=='Education 1'
            assert fields['icims_0_EducationState'].label=='State/Province'
            assert fields['icims_1_EmpName'].section=='Work Experience 2'
            assert 'agree to this Applicant Statement' in fields['icims_f_signature1'].label
            assert fields['icims_f_signature1'].section=='Applicant Statement'
            assert fields['ordinary'].label=='Unrelated field'
        finally:browser.close()
