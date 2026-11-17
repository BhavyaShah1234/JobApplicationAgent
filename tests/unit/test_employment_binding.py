"""Generic short labels must use their own employment record without asking or borrowing another row."""
import pytest
from jobagent.tools.forms import Field
from jobagent.filling.facts import compile_facts,resolve
from jobagent.filling.routing import resolve_routed,route


@pytest.mark.parametrize('label',['Company *','Company Name','Employer','Employer Name'])
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
