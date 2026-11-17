from types import SimpleNamespace

import pytest

from jobagent.filling.adapter import _portal_education_facts
from jobagent.filling.facts import Fact
from jobagent.tools.forms import Field


def records():
    return [Fact('old-school',['School'],'Older University','applicant/education/0/school',section='Education 1'),
            Fact('old-end',['End Date'],'April 2023','applicant/education/0/to',section='Education 1'),
            Fact('new-school',['School'],'Latest University','applicant/education/1/school',section='Education 2'),
            Fact('new-end',['End Date'],'May 2026','applicant/education/1/to',section='Education 2')]


def view(help='Please ensure you add only your most recent college/university attended.'):
    return SimpleNamespace(adds=[{'section':'Education','help':help}],fields=[])


def test_most_recent_school_is_selected_by_dates_and_keeps_original_source_indices():
    facts=records();chosen=_portal_education_facts(facts,view())
    assert [f.id for f in chosen]==['new-school','new-end']
    assert all(f.section=='Education 1' for f in chosen)
    assert chosen[0].source=='applicant/education/1/school'
    assert facts[2].section=='Education 2' and len(facts)==4


@pytest.mark.parametrize('help',['Most recent university first.','Add all degrees, not only your most recent university.',''])
def test_first_is_ordering_and_not_only_is_not_a_one_school_limit(help):
    facts=records()
    assert _portal_education_facts(facts,view(help)) is facts


def test_missing_or_ambiguous_recency_blocks_instead_of_guessing():
    facts=records();facts[1].value='2026'
    with pytest.raises(ValueError,match='ambiguous'):_portal_education_facts(facts,view())
    facts[1].value='Unknown'
    with pytest.raises(ValueError,match='source dates'):_portal_education_facts(facts,view())


def test_extra_existing_rows_are_not_filled_against_a_one_school_instruction():
    current=view();current.fields=[Field(id=i,kind='text',label='School',section=f'Education {i+1}') for i in range(2)]
    with pytest.raises(ValueError,match='multiple education rows'):_portal_education_facts(records(),current)
