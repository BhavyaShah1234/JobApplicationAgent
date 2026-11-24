"""Explicit global qualifications can use their degree; a row needs its own evidence."""
from types import SimpleNamespace
import pytest
from jobagent.filling.facts import compile_facts
from jobagent.filling.planned import PlannedFiller,Mapping,Mappings,Supported
from jobagent.tools.forms import Field


class Model:
    model_name='fixture'
    def __init__(self,mapping):self.mapping=mapping
    def with_structured_output(self,schema,**kwargs):
        return SimpleNamespace(invoke=lambda _:Mappings(mappings=[self.mapping]) if schema is Mappings else Supported(supported_ids=[0]))


@pytest.mark.parametrize('row,expected',[(1,'Yes'),(0,None)])
def test_explicit_bachelor_qualification_uses_only_the_named_degree_record(row,expected):
    facts=compile_facts({'education':[{'question':'Degrees','answer':[
        {'school':'Graduate University','degree':'Masters','field':'Robotics'},
        {'school':'Undergraduate Institute','degree':'Bachelors','field':'Computer Science'}]}]},'')
    question=Field(id=0,kind='radio',label="Do you have a Bachelor's degree in Computer Science or a related field?",options=['Yes','No'])
    model=Model(Mapping(field_id=0,fact_id=f'applicant/education/Degrees/answer/{row}',value='Yes'))
    d=PlannedFiller(facts,lambda _: {},model).plan([question],allow_human=False)[0]
    assert d.value==expected
    if expected:assert d.fact_id.endswith('/1')


def test_a_quote_from_another_resume_role_cannot_bypass_row_binding():
    field=Field(id=0,kind='text',label='Internal position title',section='Work Experience 2')
    model=Model(Mapping(field_id=0,value='Director',source='resume',quote='Director at Another Company'))
    d=PlannedFiller([],lambda _: {},model,{'resume':'Director at Another Company'}).plan([field],allow_human=False)[0]
    assert d.value is None


@pytest.mark.parametrize('heading,expected',[('Education History',('education',None)),('Education Details',('education',None)),
                                             ('Education 2',('education',2)),('Employment History',('employment',None)),
                                             ('References',('reference',None)),('Personal Information *',None),
                                             ('Please enter the highest level of education you have attained',None)])
def test_section_headings_are_scoped_to_their_fact_rows_including_education_history(heading,expected):
    """Dayforce: 'Education History' was unknown, so no education fact could bind and every school field was asked."""
    from jobagent.filling.facts import entity_scope
    assert entity_scope(heading)==expected
