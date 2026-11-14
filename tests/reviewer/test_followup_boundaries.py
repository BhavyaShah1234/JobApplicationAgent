"""Independent boundaries for the production experiment repairs; synthetic pages/state only."""
import pytest
from jobagent.steps import apply as A
from jobagent.tools import forms
from playwright.sync_api import sync_playwright


@pytest.mark.parametrize("placeholder_value", ["", "placeholder"])
def test_question_specific_empty_select_prompt_is_not_a_verified_prefill(placeholder_value):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page()
            page.set_content('<label for=p>Preferred contact method</label><select id=p required>'
                             f'<option value="{placeholder_value}" disabled selected>Select your preferred contact method</option>'
                             '<option>Email</option><option>Phone</option></select><button type=button>Apply</button>')
            f=next(f for f in forms.read_page(page).fields if f.kind=='select')
            assert page.locator('#p').evaluate('e=>e.selectedOptions[0].disabled')
            if not placeholder_value:assert page.locator('#p').evaluate('e=>!e.validity.valid')
            e={'page':1,'label':f.label,'required':True,'filled':'','answer':'','verified':False,'answered':True}
            e['outcome']=A.outcome_of(e,f)
            assert A.blockers_of([e]),{'readback':f.value,'outcome':e['outcome'],'has_value':forms.has_value(f)}
            assert not forms.has_value(f),f.value
        finally:browser.close()


def test_prepared_zip_code_still_overrides_a_tel_typed_postal_control():
    # Sites use type=tel for numeric keyboards; the explicit question still asks for a postal code.
    address={'line1':'1 Example Street','city':'Austin','state':'Texas','postal_code':'78701','country':'United States'}
    ctx={'_jd':{'company':'Fixture'},'REFERRAL':{},
         '_applicant':{'Contact':[{'question':'Current address','seen':['ZIP Code'],'answer':address}]},
         '_application_address':{'address':address,'state_code':'TX'}}
    f=forms.Field(id=0,kind='tel',label='ZIP Code',required=True)
    answers={0:A.Answer(id=0,value='10001',source='applicant')}
    A.enforce([f],answers,ctx,lambda _:None)
    assert answers[0].value=='78701',answers[0]
