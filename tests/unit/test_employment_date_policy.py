"""An explicit owner policy can supply a day, without changing recorded precision."""
import pytest
from jobagent.filling.facts import compile_facts,from_fact,EMPLOYMENT_MONTH_POLICY,FIRST_DAY_POLICY
from jobagent.tools.forms import Field


def facts(value='January 2026',policy=FIRST_DAY_POLICY,source='owner',education=False):
    data={'experience':[{'question':'Work history','answer':[{'start':value}]}],
          'policies':[{'question':EMPLOYMENT_MONTH_POLICY,'answer':policy,'source':source}]}
    if education:data['education']=[{'question':'Degrees','answer':[{'start':value}]}]
    return compile_facts(data,'')


def date_fact(values,section='Work Experience 1'):
    return next(f for f in values if f.section==section and 'Start Date' in f.aliases)


@pytest.mark.parametrize('month',['January 2026','Jan 2026','2026-01','01/2026'])
def test_owner_approved_month_only_employment_date_uses_day_one_and_both_sources(month):
    values=facts(month);f=date_fact(values)
    d=from_fact(Field(id=0,kind='date',label='Start Date',section=f.section),f)
    assert d.value=='2026-01-01' and f.value==month
    assert f.id in d.fact_id and f.policy_fact_id in d.fact_id
    assert 'approximation' in d.source


def test_an_exact_date_is_preserved_without_an_approximation_claim():
    f=date_fact(facts('2026-01-17'));d=from_fact(Field(id=0,kind='date',label='Start Date'),f)
    assert d.value=='2026-01-17' and d.fact_id==f.id and 'approximation' not in d.source


@pytest.mark.parametrize('policy,source',[(FIRST_DAY_POLICY,'resume'),('leave_unknown','owner')])
def test_no_owner_approval_means_no_invented_day(policy,source):
    f=date_fact(facts(policy=policy,source=source))
    assert from_fact(Field(id=0,kind='date',label='Start Date'),f).value is None


def test_the_rule_never_supplies_an_unknown_month_and_covers_education_only_with_the_owners_approval():
    f=date_fact(facts('2026'));assert from_fact(Field(id=0,kind='date',label='Start Date'),f).value is None      # year only: never guessed
    # owner decision 2026-11-24: "use the 1st of the month for everything if they need day information" (education included)
    f=date_fact(facts(education=True),'Education 1')
    d=from_fact(Field(id=0,kind='date',label='Start Date'),f)
    assert d.value=='2026-01-01' and 'approximation' in d.source
    f=date_fact(facts('2026',education=True),'Education 1')
    assert from_fact(Field(id=0,kind='date',label='Start Date'),f).value is None                               # year-only education too
    f=date_fact(facts(policy='leave_unknown',education=True),'Education 1')
    assert from_fact(Field(id=0,kind='date',label='Start Date'),f).value is None                               # no approval, no day


def test_ongoing_employment_clears_an_optional_end_date_without_inventing_one():
    from jobagent.filling.facts import Fact
    fact=Fact('current.end',['End Date'],'Present','resume/role1',section='Work Experience 1')
    d=from_fact(Field(id=0,kind='text',label='End Date',format='MM/DD/YYYY'),fact)
    assert d.value=='' and d.fact_id=='current.end'
    assert from_fact(Field(id=0,kind='date',label='End Date',required=True),fact).value is None
