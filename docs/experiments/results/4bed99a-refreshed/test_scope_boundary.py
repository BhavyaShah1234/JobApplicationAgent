"""Additional compiler boundary, outside the registered provider cohort. Synthetic facts only."""
from jobagent.experiments.resolution import compile_facts, resolve
from jobagent.tools.forms import Field


def test_employer_city_cannot_use_current_home_city_when_work_city_is_supplied():
    data={'Contact':[{'question':'Current address','answer':{'city':'Austin'}}],
          'Experience':[{'question':'Work history','answer':[{'company':'Example Systems','city':'Boston'}]}]}
    decision=resolve(Field(id=0,kind='text',label='City',section='Work Experience 1'),compile_facts(data,''))
    assert decision.value in (None,'Boston'),decision


def test_reference_email_cannot_use_the_applicants_email():
    data={'Contact':[{'question':'Email address','answer':'applicant@example.test'}]}
    decision=resolve(Field(id=0,kind='email',label='Email address',section='References 1'),compile_facts(data,''))
    assert decision.value is None,decision
