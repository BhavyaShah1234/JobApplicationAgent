"""Month/day widgets render their own source date, never reuse a matching prefill."""
from dataclasses import replace
import pytest
from playwright.sync_api import sync_playwright
from jobagent.filling.facts import Fact,resolve,from_fact,FIRST_DAY_POLICY
from jobagent.tools import forms


@pytest.mark.parametrize('value,expected',[('August 2026','Aug'),('2026-08-12','Aug'),('2026',None)])
def test_month_comes_from_the_role_end_date(value,expected):
    field=forms.Field(0,'select','End Date (if currently employed, leave blank) Month',options=['Jan','May','Aug'],section='Work Experience 2')
    fact=Fact('role2.end',['End Date'],value,'resume/role2/end',section=field.section)
    assert resolve(field,[fact]).value==expected


@pytest.mark.parametrize('part',['Month','Day','Year'])
def test_current_role_clears_every_optional_end_date_component(part):
    field=forms.Field(0,'select' if part!='Year' else 'text',f'End Date (if currently employed, leave blank) {part}',section='Work Experience 1')
    fact=Fact('role1.end',['End Date'],'Present','resume/role1/end',section=field.section)
    assert resolve(field,[fact]).value==''
    assert resolve(replace(field,required=True),[fact]).value is None


def test_an_approximate_day_requires_the_recorded_owner_policy():
    field=forms.Field(0,'select','Start Date Day',options=['1','2'],section='Work Experience 1')
    fact=Fact('role1.start',['Start Date'],'January 2026','resume/role1/start',section=field.section)
    assert resolve(field,[fact]).value is None
    fact=replace(fact,date_policy=FIRST_DAY_POLICY,policy_fact_id='owner.month-policy')
    answer=resolve(field,[fact])
    assert answer.value=='1' and 'owner.month-policy' in answer.fact_id


def test_real_native_selects_clear_zero_value_placeholder_and_fix_stale_month():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page()
            html=''
            for row in (1,2):
                html+=f'<fieldset><legend>Professional Experience ({row})</legend><label>End Date (if currently employed, leave blank) Month<select><option value=0></option><option value=01>Jan</option><option selected value=05>May</option><option value=08>Aug</option></select></label></fieldset>'
            page.set_content(html)
            facts=[Fact('current.end',['End Date'],'Present','resume/current/end',section='Work Experience 1'),
                   Fact('previous.end',['End Date'],'August 2026','resume/previous/end',section='Work Experience 2')]
            for field in forms.read_page(page).fields:
                answer=resolve(field,facts)
                assert answer.value is not None
                forms.fill_field(page,forms.read_page(page),field,answer.value,{})
            assert [field.value for field in forms.read_page(page).fields]==['','Aug']
            assert page.locator('select').evaluate_all('(es)=>es.map(e=>e.value)')==['0','08']
        finally:browser.close()


def test_month_rendering_does_not_ask_a_writer_to_choose_from_a_stale_prefill():
    from jobagent.filling.planned import PlannedFiller
    class NoWriter:
        def with_structured_output(self,*args,**kwargs):
            pytest.fail('A fully evidenced split date must be resolved without a writer')
    field=forms.Field(0,'select','End Date (if currently employed, leave blank) Month',
                      value='May',options=['May','Aug'],section='Work Experience 2')
    fact=Fact('role2.end',['End Date'],'August 2026','resume/role2/end',section=field.section)
    assert PlannedFiller([fact],lambda _: {},NoWriter()).plan([field],allow_human=False)[0].value=='Aug'
