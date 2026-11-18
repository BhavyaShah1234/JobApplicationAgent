"""Highest completed degree summaries bind subject and completion to one record."""
import pytest
from jobagent.filling.facts import Decision,compile_facts
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.routing import answer_sheet_facts
from jobagent.tools.forms import Field

QUESTION='11e) * Please provide your highest degree completed and the field of study for that degree: Highest Degree: Field of Study: *'


def no_ask(q):raise AssertionError(q)


def facts(rows):
    return compile_facts({'education':[{'question':'Degrees','answer':rows}]},'','2026-11-17')


def plan(bank,section='',**kwargs):
    field=Field(0,'textarea',QUESTION,section=section,required=True)
    filler=PlannedFiller(bank,no_ask,model=object(),**kwargs)
    filler.invoke=lambda *a,**kw:pytest.fail('Source-bound education summaries need no model guess')
    result=filler.plan([field],allow_human=False)[0]
    assert filler.model_calls==filler.questions==0
    return result


@pytest.mark.parametrize('rows',[
    [{'degree':'Master of Science','field':'Artificial Intelligence','end':'May 2026'},
     {'degree':'Bachelor of Technology','field':'Computer Science','end':'April 2023'}],
    [{'degree':'Bachelor of Technology','field':'Computer Science','end':'April 2023'},
     {'degree':'Master of Science','field':'Artificial Intelligence','end':'May 2026'}],
])
def test_degree_and_subject_come_from_the_highest_completed_record_regardless_of_order(rows):
    result=plan(facts(rows));chosen=next(i for i,r in enumerate(rows) if 'Master' in r['degree'])
    assert result.value=='Highest Degree: Master of Science; Field of Study: Artificial Intelligence'
    assert f'/answer/{chosen}/field' in result.fact_id
    assert f'/answer/{1-chosen}/field' not in result.fact_id
    assert f'/answer/{chosen}/end' in result.fact_id


def test_known_future_degree_does_not_replace_a_completed_degree():
    result=plan(facts([{'degree':'Master of Science','field':'AI','end':'May 2027'},
                       {'degree':'Bachelor of Science','field':'Mathematics','end':'May 2023'}]))
    assert result.value=='Highest Degree: Bachelor of Science; Field of Study: Mathematics'


@pytest.mark.parametrize('rows',[
    [{'degree':'Master of Science','field':'AI'}],
    [{'degree':'Master of Science','field':'AI','end':'November 2026'}],
    [{'degree':'Master of Science','field':'AI','end':'2026'}],
    [{'degree':'Master of Science','field':'AI','end':'May 2026'},
     {'degree':'Master of Science','field':'Mathematics','end':'May 2024'}],
    [{'degree':'Master of Science','end':'May 2026'},
     {'degree':'Bachelor of Science','field':'Computer Science','end':'May 2023'}],
    [{'degree':'Unrecognized qualification','field':'AI','end':'May 2024'}],
])
def test_missing_or_ambiguous_completion_and_subject_evidence_is_not_guessed(rows):
    assert plan(facts(rows)).value is None


def test_owner_education_is_not_another_persons_degree():
    assert plan(facts([{'degree':'Master of Science','field':'AI','end':'May 2026'}]),section='Reference 1').value is None


def test_external_source_resolver_cannot_replace_missing_education_evidence():
    result=plan([],source_resolver=lambda *_:{0:Decision('Master of Science in AI','resume')})
    assert result.value is None


def test_explicit_current_application_answer_remains_authoritative():
    bank=answer_sheet_facts('| # | Question | Answer |\n| 1 | '+QUESTION+' | Master of Science in AI |')
    assert plan(bank).value=='Master of Science in AI'


def test_conditional_education_evidence_is_not_treated_as_an_unconditional_fact():
    bank=facts([{'degree':'Master of Science','field':'AI','end':'May 2026'}])
    for fact in bank:fact.instructions='Only if separately verified by the university.'
    assert plan(bank).value is None


def test_global_owner_answer_sheet_cannot_describe_a_references_education():
    bank=answer_sheet_facts('| # | Question | Answer |\n| 1 | '+QUESTION+' | Master of Science in AI |')
    assert plan(bank,section='Reference 1').value is None
