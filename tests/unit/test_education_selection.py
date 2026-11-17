"""Global education pickers keep the selected source degree, not the applicant address."""
from types import SimpleNamespace
import pytest
from jobagent.filling.education import selection_context
from jobagent.filling.facts import compile_facts
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.routing import resolve_routed
from jobagent.tools.forms import Field


def sources():
    return compile_facts({'education':[{'question':'Degrees','answer':[
        {'school':['Example University - Campus','Example University'],'degree':['Master of Science'],
         'country':'United States','region':'Americas'},
        {'school':['Other Institute'],'degree':['Bachelor of Technology'],
         'country':'India','region':'APJ'}]}],
        'address':[{'question':'Current address','answer':{'country':'Canada'}}]},'Job location: Germany')


def view(value='Masters',invalid=False):
    return SimpleNamespace(fields=[
        Field(id=0,kind='combobox',label='What is your highest level of education?',value=value,invalid=invalid,identity='highest'),
        Field(id=1,kind='combobox',label='Where did you study – Region?',options=['No Selection','Americas','APJ','EMEA/MEE']),
        Field(id=2,kind='combobox',label='Where did you study – Country?',options=['USA','India','Canada']),
        Field(id=3,kind='combobox',label='Where did you study – School?',options=['Example University','Other Institute'])])


@pytest.mark.parametrize('degree,expected',[('Masters',['Americas','USA','Example University']),
                                          ('Bachelors',['APJ','India','Other Institute'])])
def test_global_dependent_pickers_use_the_same_selected_degree_and_original_source_ids(degree,expected):
    v=view(degree);facts=selection_context(sources(),v)
    for f,want in zip(v.fields[1:],expected):
        d=resolve_routed(f,facts)
        assert d.value==want
        row='/0/' if degree=='Masters' else '/1/'
        assert row in d.fact_id and 'form.education.selection:' in d.fact_id
        assert ('/1/' if degree=='Masters' else '/0/') not in d.fact_id


@pytest.mark.parametrize('value,invalid',[('',False),('Masters',True)])
def test_dependent_questions_wait_for_an_accepted_parent_instead_of_asking_early(value,invalid):
    v=view(value,invalid);asked=[]
    filler=PlannedFiller(selection_context(sources(),v),lambda qs:asked.extend(qs) or {})
    answers=filler.plan(v.fields[1:])
    assert not asked
    assert all(d.value is None and d.reason=='waiting for selected education record' for d in answers.values())


def test_reselecting_a_degree_rebuilds_context_without_mutating_source_record_scopes():
    facts=sources();v=view();first=selection_context(facts,v)
    v.fields[0].value='Bachelors';second=selection_context(first,v)
    assert resolve_routed(v.fields[3],second).value=='Other Institute'
    assert len([f for f in second if f.id.startswith('form.education.selection:')])==3
    assert all(f.section=='Education 1' for f in facts if '/0/' in f.id)


def test_two_degrees_at_the_selected_level_are_ambiguous():
    facts=sources()
    extra=compile_facts({'education':[{'question':'Degrees','answer':[
        {'school':'Third University','degree':'Masters'}]}]},'')
    for f in extra:f.id='third:'+f.id;f.section='Education 3'
    v=view();d=resolve_routed(v.fields[3],selection_context(facts+extra,v))
    assert d.value is None


def test_missing_institution_country_cannot_borrow_the_applicants_or_jobs_country():
    facts=[f for f in sources() if not (f.section=='Education 1' and 'Country' in f.aliases)]
    v=view();assert resolve_routed(v.fields[2],selection_context(facts,v)).value is None


def test_no_parent_control_does_not_choose_between_two_owned_schools():
    v=view();v.fields=v.fields[1:]
    assert resolve_routed(v.fields[-1],selection_context(sources(),v)).value is None


def test_numbered_education_row_keeps_its_own_scope():
    f=Field(id=0,kind='select',label='Where did you study – School?',section='Education 2',
            options=['Example University','Other Institute'])
    assert resolve_routed(f,sources()).value=='Other Institute'
