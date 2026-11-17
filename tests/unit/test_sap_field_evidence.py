"""Observed SuccessFactors ARIA choices and ordinary global profile questions."""
import pytest
from playwright.sync_api import sync_playwright
from jobagent.filling.facts import compile_facts
from jobagent.filling.routing import resolve_routed,route
from jobagent.tools import forms


def profile():
    return {'education': [
        {'question':'Degrees','answer':[
            {'school':['ASU'],'degree':['Master of Science','MS'], 'field':['Robotics and AI']},
            {'school':['VIT'],'degree':['Bachelor of Technology','B.Tech'],'field':['Computer Science and Engineering']}]},
        {'question':'Current education status','answer':'Completed a graduate degree'}],
        'identity':[{'question':'Legal name','answer':{'first':'Example','middle':'M','last':'Candidate'}}]}


@pytest.mark.parametrize('label,options,value,suffix',[
    ('What is your highest level of education?',['No Selection','Bachelors','Masters','Doctorate'],'Masters','/0/degree'),
    ('What is your level of completion?',['No Selection','Currently in progress','Graduated'],'Graduated','/answer'),
    ("3. Please specify your Bachelor's Degree field of study.",[],'Computer Science and Engineering','/1/field'),
    ("6. Please specify your Master's Degree field of study.",[],'Robotics and AI','/0/field'),
    ('Typed Signature:',[],'Example M Candidate','/full'),
])
def test_global_queries_resolve_from_their_own_profile_facts(label,options,value,suffix):
    f=forms.Field(id=0,kind='combobox' if options else 'text',label=label,options=options,section='Developer (123)')
    result=resolve_routed(f,compile_facts(profile(),''))
    assert result is not None and result.value==value and result.fact_id.endswith(suffix)


def test_degree_subject_is_never_borrowed_from_another_degree():
    data=profile();data['education'][0]['answer']=data['education'][0]['answer'][:1]
    f=forms.Field(id=0,kind='text',label="Please specify your Bachelor's Degree field of study.")
    assert route(f,compile_facts(data,'')).fact_ids==[]


def test_highest_level_does_not_override_a_specific_education_row():
    f=forms.Field(id=0,kind='select',label='Highest degree',section='Education 2',options=['Masters','Bachelors'])
    result=resolve_routed(f,compile_facts(profile(),''))
    assert result is not None and result.value=='Bachelors'


def test_an_explicit_degree_cannot_borrow_the_subject_of_a_different_ui_row():
    f=forms.Field(id=0,kind='text',label="Please specify your Bachelor's Degree field of study.",section='Education 1')
    assert route(f,compile_facts(profile(),'')).fact_ids==[]


def test_unknown_degree_does_not_make_another_known_one_the_highest():
    data=profile();data['education'][0]['answer'].append({'degree':['Unspecified foreign qualification']})
    f=forms.Field(id=0,kind='select',label='Highest level of education',options=['Masters','Bachelors'])
    result=resolve_routed(f,compile_facts(data,''))
    assert result is None or result.value is None


def test_sibling_aria_labels_supply_radio_options_and_readback_in_the_same_question():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True);p=browser.new_page()
        p.set_content("""<div role=radiogroup aria-label='Permanent authorization' aria-required=true>
        <span role=radio aria-checked=false aria-labelledby=yes_label onclick="this.setAttribute('aria-checked','true')"></span><label id=yes_label>Yes</label>
        <span role=radio aria-checked=false aria-labelledby=no_label onclick="this.setAttribute('aria-checked','true')"></span><label id=no_label>No</label></div>
        <style>[role=radio]{display:inline-block;width:20px;height:20px;background:#eee}</style>""")
        view=forms.read_page(p);f=next(f for f in view.fields if f.kind=='radio')
        assert f.options==['Yes','No']
        assert forms.fill_field(p,view,f,'No',{})=='No'
        assert next(f for f in forms.read_page(p).fields if f.kind=='radio').value=='No'
        assert p.locator('[aria-labelledby=yes_label]').get_attribute('aria-checked')=='false'
        browser.close()
