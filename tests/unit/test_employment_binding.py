"""Generic short labels must use their own employment record without asking or borrowing another row."""
import pytest
from jobagent.tools.forms import Field
from jobagent.filling.facts import compile_facts,resolve
from jobagent.filling.routing import resolve_routed,route


@pytest.mark.parametrize('label',['Company *','Company Name','Employer','Employer Name','Name of Employer'])
@pytest.mark.parametrize('row',[1,2])
def test_company_label_uses_only_the_corresponding_employment_row(label,row):
    facts=compile_facts({'Experience':[{'question':'Work history','answer':[
        {'company':'First Systems'},{'company':'Second Software'}]}]},'')
    field=Field(id=0,kind='text',label=label,section=f'Work Experience {row}')
    decision=resolve_routed(field,facts) or resolve(field,facts)
    assert decision.value==['First Systems','Second Software'][row-1]
    assert route(field,facts).fact_ids==[f'applicant/Experience/Work history/answer/{row-1}/company']


def test_missing_employment_row_and_unbound_company_label_do_not_borrow_other_companies():
    facts=compile_facts({'Experience':[{'question':'Work history','answer':[{'company':'First Systems'}]}]},'')
    for section in ['Work Experience 2','Contact','References 1']:
        field=Field(id=0,kind='text',label='Company',section=section)
        assert (resolve_routed(field,facts) or resolve(field,facts)).value is None


def test_review_rows_keep_their_source_identity_when_source_list_order_differs():
    from jobagent.filling.employment import recent_employment_facts
    from jobagent.tools.forms import PageView
    facts=compile_facts({'Experience':[{'question':'Current company','answer':'Current Global'},
        {'question':'Work history','answer':[{'company':'First Co','title':'First Title','end':'May 2026'},
        {'company':'Second Co','title':'Second Title','end':'August 2026'},
        {'company':'Older Co','title':'Older Title','end':'August 2025'}]}]},'')
    fields=[Field(id=0,kind='text',label='Name of Employer',section='Work Experience 1',value='Second Co'),
            Field(id=1,kind='text',label='Your Job Title',section='Work Experience 1',value='Second Title'),
            Field(id=2,kind='text',label='Name of Employer',section='Work Experience 2',value='First Co'),
            Field(id=3,kind='text',label='Your Job Title',section='Work Experience 2',value='First Title')]
    view=PageView(fields,[],[],[],{})
    projected=recent_employment_facts(facts,view)
    assert (resolve_routed(fields[0],projected) or resolve(fields[0],projected)).value=='Second Co'
    assert (resolve_routed(fields[2],projected) or resolve(fields[2],projected)).value=='First Co'
    assert next(f for f in projected if f.id.endswith('/1/company')).section=='Work Experience 1'
    assert next(f for f in projected if f.id.endswith('/2/company')).value=='Older Co'
    fields[3].value='Second Title'  # inconsistent company/title: no partial remapping
    assert recent_employment_facts(facts,view)==facts


def test_education_state_province_cannot_borrow_the_applicants_job_city_state():
    facts=compile_facts({'Education':[{'question':'Degrees','answer':[{'school':'Example University','state':'Arizona'}]}],
                         'Address':[{'question':'Current address','answer':{'state':'Kansas'}}]},'')
    field=Field(id=1,kind='select',label='State/Province',section='Education 1',options=['Arizona','Kansas'])
    assert (resolve_routed(field,facts) or resolve(field,facts)).value=='Arizona'


def test_unnumbered_employment_sections_do_not_trigger_row_projection():
    from jobagent.filling.employment import recent_employment_facts
    from jobagent.tools.forms import PageView
    facts=compile_facts({'Experience':[{'question':'Work history','answer':[{'company':'Example Co','title':'Engineer'}]}]},'')
    fields=[Field(id=0,kind='text',label='Employer',section='Work Experience',value='Example Co'),
            Field(id=1,kind='text',label='Job Title',section='Work Experience',value='Engineer')]
    assert recent_employment_facts(facts,PageView(fields,[],[],[],{}))==facts
