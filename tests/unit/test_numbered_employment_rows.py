"""Real numbered fieldsets must never reuse a global employer/title answer across roles."""
from types import SimpleNamespace
from dataclasses import replace
import pytest
from playwright.sync_api import sync_playwright
from jobagent.tools import forms
from jobagent.filling.facts import Fact,compile_facts,scope_allows
from jobagent.filling.routing import needs_entity_binding,candidates
from jobagent.filling.employment import recent_employment_facts


def records():
    return compile_facts({'experience':[{'question':'Work history','answer':[
        {'company':'Current Lab','title':'Researcher','from':'January 2026','to':'Present'},
        {'company':'Research Institute','title':'Scholar','from':'May 2025','to':'May 2026'},
        {'company':'Old Controls','title':'Engineer','from':'August 2023','to':'May 2024'},
        {'company':'Recent Platform','title':'Platform Engineer','from':'May 2026','to':'August 2026'}]}]},'')


def view(heading='Please enter your last three (3) employers (if applicable).'):
    return SimpleNamespace(headings=[heading],fields=[forms.Field(i,'text','Employer',section=f'Work Experience {i}') for i in range(1,4)])


def test_recent_roles_are_selected_from_their_own_dates_without_rewriting_source_ids():
    original=records();facts=recent_employment_facts(original,view())
    selected=[(f.section,f.value,f.id) for f in facts if 'Employer' in f.aliases]
    assert [f[1] for f in selected]==['Current Lab','Research Institute','Recent Platform']
    assert dict((s,v) for s,v,_ in selected)=={'Work Experience 1':'Current Lab','Work Experience 2':'Recent Platform','Work Experience 3':'Research Institute'}
    assert next(i for _,v,i in selected if v=='Recent Platform').endswith('/3/company')
    assert next(f.section for f in original if f.value=='Recent Platform')=='Work Experience 4'
    poison=Fact('learned:company:Employer',['Employer'],'Wrong Shared Company','learned:company:Employer')
    for field in view().fields:
        allowed=[f.value for f in candidates(field,facts+[poison]) if 'Employer' in f.aliases]
        expected={'Work Experience 1':'Current Lab','Work Experience 2':'Recent Platform','Work Experience 3':'Research Institute'}
        assert allowed==[expected[field.section]]


@pytest.mark.parametrize('label',['Employer','Title','City','Start Date','End Date','School'])
@pytest.mark.parametrize('section',['Work Experience 2','Please enter your last three (3) employers (if applicable).'])
def test_entity_components_refuse_unscoped_learned_literals(label,section):
    field=forms.Field(1,'text',label,section=section)
    assert not scope_allows(field,Fact('learned:old',[label],'Wrong answer','learned:old'))
    assert needs_entity_binding(label,section,kind='text')


@pytest.mark.parametrize('bad',['tied','missing','contradictory_limit'])
def test_recent_history_does_not_guess_uncertain_ordering(bad):
    facts=records();page=view()
    if bad=='contradictory_limit':page.headings=['Please enter the last three (4) employers']
    else:
        facts=[replace(f,value='May 2026' if bad=='tied' else '2026') if f.id.endswith('/3/to') else f for f in facts]
    with pytest.raises(ValueError):recent_employment_facts(facts,page)


def test_nonlimited_forms_keep_the_resume_order():
    facts=records()
    assert recent_employment_facts(facts,view('Work history')) is facts


def test_numbered_legend_scopes_all_fields_including_nested_date_groups():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page()
            html='<h2>Please enter your last three (3) employers (if applicable).</h2>'
            for i in range(1,4):
                html+=f'<fieldset><legend>Professional Experience ({i})*<span>required.</span></legend><label>Employer<input></label><label>Title<input></label><fieldset><legend>Start Date</legend><label>Year<input></label></fieldset></fieldset>'
            page.set_content(html)
            result=forms.read_page(page)
            assert [(f.section,f.label) for f in result.fields]==[(f'Work Experience {i}',label) for i in range(1,4) for label in ('Employer','Title','Year')]
            facts=recent_employment_facts(records(),result)
            assert {f.section:f.value for f in facts if 'Employer' in f.aliases}=={'Work Experience 1':'Current Lab','Work Experience 2':'Recent Platform','Work Experience 3':'Research Institute'}
        finally:browser.close()
