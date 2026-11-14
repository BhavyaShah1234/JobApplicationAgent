"""Post-measurement correctness guards; not part of the registered provider cohort."""
from types import SimpleNamespace
from jobagent.experiments.resolution import compile_facts,resolve,Fact
from jobagent.experiments.engine import Candidate,Mappings,Supported
from jobagent.tools.forms import Field


def profile():
    return {'Contact':[{'question':'Current address','answer':{'city':'Austin'}},
                       {'question':'Email address','answer':'applicant@example.test'}],
            'Experience':[{'question':'Work history','answer':[{'company':'Example Systems','city':'Boston'}]}]}


def test_employer_city_uses_the_employment_row_not_the_home_address():
    facts=compile_facts(profile(),'')
    field=Field(id=0,kind='text',label='City',section='Work Experience 1')
    assert resolve(field,facts).value=='Boston'


def test_missing_employer_city_cannot_fall_back_to_residence():
    data=profile();del data['Experience'][0]['answer'][0]['city']
    assert resolve(Field(id=0,kind='text',label='City',section='Work Experience 1'),compile_facts(data,'')).value is None


def test_reference_email_cannot_use_the_applicants_email():
    assert resolve(Field(id=0,kind='email',label='Email address',section='References 1'),compile_facts(profile(),'')).value is None


def test_contact_and_global_policy_answers_remain_available():
    facts=compile_facts(profile(),'')
    assert resolve(Field(id=0,kind='text',label='City',section='Contact information'),facts).value=='Austin'
    facts.append(Fact('work-auth',['Authorized to work?'],'Yes','confirmed applicant policy'))
    assert resolve(Field(id=1,kind='select',label='Authorized to work?',section='Employment 1',options=['Yes','No']),facts).value=='Yes'


def test_equivalent_entity_headings_keep_row_numbers_distinct():
    facts=compile_facts(profile(),'')
    assert resolve(Field(id=0,kind='text',label='City',section='Employment entry 1'),facts).value=='Boston'
    assert resolve(Field(id=0,kind='text',label='City',section='Employment entry 2'),facts).value is None


def test_model_mapping_cannot_bypass_the_entity_boundary():
    facts=compile_facts({'Contact':profile()['Contact']},'')
    city=next(f for f in facts if f.value=='Austin')
    class Model:
        model_name='synthetic-model'
        def with_structured_output(self,schema,**kwargs):
            return SimpleNamespace(invoke=lambda _:Mappings(mappings=[{'field_id':0,'fact_id':city.id}]) if schema is Mappings else Supported(supported_ids=[0]))
    asked=[]
    engine=Candidate(facts,lambda q:asked.extend(q) or {},Model())
    assert engine.plan([Field(id=0,kind='text',label='City',section='Work Experience 1')])[0].value is None
    assert len(asked)==1 and asked[0]['section']=='Work Experience 1'
