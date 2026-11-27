"""Explicit global qualifications can use their degree; a row needs its own evidence."""
from types import SimpleNamespace
import pytest
from jobagent.filling.facts import compile_facts
from jobagent.filling.planned import PlannedFiller,Mapping,Mappings,Supported
from jobagent.tools.forms import Field


@pytest.mark.parametrize('row,school,degree', [
    (1, 'Graduate University', 'Masters'),
    (2, 'Undergraduate Institute', 'Bachelors'),
])
@pytest.mark.parametrize('label,component', [
    ('School or University *', 'school'),
    ('Overall Result (GPA)', 'gpa'),
])
def test_workday_education_captions_keep_school_and_gpa_with_the_profile_degree(row, school, degree, label, component):
    """Opposite-order prefills and unrelated global answers cannot split a degree."""
    facts = compile_facts({
        'education': [
            {'question': 'Degrees', 'answer': [
                {'school': 'Graduate University', 'degree': 'Masters', 'gpa': '4.0'},
                {'school': 'Undergraduate Institute', 'degree': 'Bachelors', 'gpa': '3.8'},
            ]},
            {'question': 'GPA', 'answer': '2.0'},
        ],
        'employment': [{'question': 'Would you require CPT authorization from your school?', 'answer': 'No'}],
    }, '')
    expected = school if component == 'school' else '4.0' if row == 1 else '3.8'
    other_school = 'Undergraduate Institute' if row == 1 else 'Graduate University'
    field = Field(id=0, kind='combobox' if component == 'school' else 'text', label=label,
                  section=f'Education {row}', value=other_school if component == 'school' else '2.0',
                  options=[other_school] if component == 'school' else [], options_state='partial')
    degree_field = Field(id=1, kind='select', label='Degree *', section=f'Education {row}',
                         options=['Masters', 'Bachelors'])
    decisions = PlannedFiller(facts, lambda _: pytest.fail('Known education facts must not ask'), None).plan(
        [field, degree_field], allow_human=False)
    assert decisions[0].value == expected
    assert decisions[0].fact_id.endswith(f'/{row - 1}/{component}')
    assert decisions[1].value == degree


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


class _NoModel:
    model_name='fixture'
    def with_structured_output(self,schema,**kwargs):
        return SimpleNamespace(invoke=lambda _:schema.model_validate({'mappings':[]}) if hasattr(schema,'model_validate') else None)


REFS={'references':[{'question':'References','answer':[
    {'first_name':'Nakul','last_name':'Gopalan','prefix':'Dr.','title':'Assistant Professor','company':'Logos Robotics Lab',
     'email':'nakul.gopalan@asu.edu','phone':'6233329509'},
    {'first_name':'Apurva','last_name':'Patel','title':'VP','company':'TGen','email':'apatel@tgen.org','phone':'6233329509'}]}],
    'education':[{'question':'Degrees','answer':[{'school':['Arizona State University'],'degree':['Master of Science'],'gpa':'4.0'}]}]}


@pytest.mark.parametrize('label,expected',[('First Name','Nakul'),('Last Name','Gopalan'),('Position Title','Assistant Professor'),
                                           ('Email','nakul.gopalan@asu.edu'),('Business Phone','6233329509'),('Mobile Phone','6233329509'),
                                           ('Company','Logos Robotics Lab')])
def test_a_single_reference_row_takes_the_first_reference_from_the_profile(label,expected):
    """Dayforce shows one 'References' row (no number): its fields are the first reference's."""
    facts=compile_facts(REFS,'')
    field=Field(id=1,kind='text',label=label,section='References')
    from jobagent.filling.routing import candidates
    found=candidates(field,facts)
    values={x.value for x in found}
    assert expected in values, (label,[(x.id,x.value,x.section) for x in found])
    assert 'Apurva' not in values and 'Patel' not in values            # never the second reference's details in the first row


def test_the_gpa_label_with_dots_binds_to_the_education_gpa():
    facts=compile_facts(REFS,'')
    from jobagent.filling.routing import candidates
    found=candidates(Field(id=2,kind='spin',label='G.P.A.',section='Education History'),facts)
    assert '4.0' in {x.value for x in found}


def test_only_explicit_portal_decomposition_can_split_a_multiword_legal_first_name():
    """Dayforce splits 'Bhavya Minesh' itself: First Name read back 'Bhavya' while the fact said 'Bhavya Minesh'."""
    from jobagent.filling.adapter import _split_name_for_middle_field
    from jobagent.filling.routing import candidates
    data={'identity':[{'question':'Legal name','answer':{'first':'Ada Marie','middle':'','last':'Lovelace'}}]}
    facts=compile_facts(data,'')
    with_middle=[Field(id=1,kind='text',label='First Name'),Field(id=2,kind='text',label='Middle Name'),Field(id=3,kind='text',label='Last Name')]
    # A middle-name control by itself must not alter a multiword legal first name.
    assert _split_name_for_middle_field(facts,with_middle)==facts
    split=_split_name_for_middle_field(facts,with_middle,split_first_name=True)
    vals=lambda label:{x.value for x in candidates(Field(id=9,kind='text',label=label),split)}
    assert vals('First Name')=={'Ada'} and vals('Middle Name')=={'Marie'} and vals('Last Name')=={'Lovelace'}
    # no Middle Name field on the form: the first name stays whole; a recorded middle name is never overridden
    assert _split_name_for_middle_field(facts,[Field(id=1,kind='text',label='First Name')])==facts
    recorded=compile_facts({'identity':[{'question':'Legal name','answer':{'first':'Ada Marie','middle':'Q','last':'L'}}]},'')
    assert _split_name_for_middle_field(recorded,with_middle,split_first_name=True)==recorded
