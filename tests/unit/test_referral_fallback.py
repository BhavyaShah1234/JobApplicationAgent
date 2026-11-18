"""Company-specific owner choices remain usable when no employee source is offered."""
import pytest

from jobagent.filling.facts import Fact
from jobagent.filling.planned import PlannedFiller
from jobagent.tools.forms import Field


QUESTION='How Did You Hear About Us? *'


def saved(company='capital-one', value='Contacted by Recruiter'):
    source=f'learned:{company}:{QUESTION}'
    return Fact(source,[QUESTION],value,source)


def field(options=None):
    return Field(id=0,kind='combobox',label=QUESTION,required=True,
                 options=options or ['Contacted by Recruiter','Internet'],options_state='partial')


def filler(facts=()):
    return PlannedFiller(list(facts),lambda _:pytest.fail('Already supplied by the owner'),
                         referral_context={'company':'Capital One','contact':{}})


@pytest.mark.parametrize('other', [False,True])
def test_scoped_source_answer_is_retrieved_before_requesting_it_again(other):
    f=field(['Contacted by Recruiter','Other'] if other else None);agent=filler();requested=[]
    def retrieve(fields, answers):
        requested.extend(fields);agent.facts.append(saved());return {}
    agent.source_resolver=retrieve
    decision=agent.plan([f])[0]
    assert requested==[f] and decision.value=='Contacted by Recruiter'
    assert decision.source.startswith('learned:capital-one:') and agent.questions==0 and agent.model_calls==0


def test_an_offered_employee_option_still_takes_precedence():
    f=field(['Contacted by Recruiter','Employee Referral']);agent=filler([saved()])
    assert agent.plan([f])[0].value=='Employee Referral'


def test_company_named_source_question_uses_the_same_scoped_owner_choice():
    f=field();f.label='How did you hear about Capital One?'
    assert filler([saved()]).plan([f])[0].value=='Contacted by Recruiter'


@pytest.mark.parametrize('fact', [saved('any'),saved('another-company'),saved(value='Unavailable answer')])
def test_global_other_company_and_unoffered_answers_cannot_supply_the_fallback(fact):
    assert filler([fact]).plan([field()],allow_human=False)[0].value is None


def test_empty_options_and_missing_referrer_contacts_are_not_overridden():
    agent=filler([saved()]);f=field();f.options=[]
    assert agent.plan([f],allow_human=False)[0].value!='Contacted by Recruiter'
    contact=Field(id=1,kind='email',label='Referrer email',required=True)
    assert agent.plan([contact],allow_human=False)[1].value is None


def test_person_category_precedes_other_but_explicit_employee_precedes_category():
    from jobagent.tools import referrals
    f=field(['Other','From a Person'])
    assert referrals.answer_for(f, {})['value']=='From a Person'
    f.options.append('Employee Referral')
    assert referrals.answer_for(f, {})['value']=='Employee Referral'


@pytest.mark.parametrize('caption', ['If "Other" please specify', 'Other please specify', 'Please specify'])
def test_adjacent_source_explanation_uses_referral_policy(caption):
    from jobagent.tools import referrals
    parent=field(['Other']);parent.value='Other'
    child=Field(id=1,kind='text',label=caption)
    referrals.contextualize([parent,child])
    assert referrals.answer_for(child,{})['value']=='Referred by employee'


def test_adjacent_source_category_child_selects_an_offered_employee_answer():
    from jobagent.tools import referrals
    parent=field(['From a Person']);parent.value='From a Person'
    child=Field(id=1,kind='combobox',label='Please select',options=['Recruiter','Employee Referral'])
    referrals.contextualize([parent,child])
    assert referrals.answer_for(child,{})['value']=='Employee Referral'


@pytest.mark.parametrize('boundary', ['frame','section','unrelated','unbound','salary'])
def test_generic_followup_cannot_borrow_a_source_across_a_boundary(boundary):
    from jobagent.tools import referrals
    parent=field(['Other']);parent.value='Other'
    child=Field(id=1,kind='text',label='Please specify',help='How did you hear about us?')
    if boundary=='frame':child.frame=1
    if boundary=='section':child.section='Other section'
    if boundary=='salary':child.label='Please specify your desired salary'
    fields=[parent,child]
    if boundary=='unbound':fields=[child]
    if boundary=='unrelated':fields.insert(1,Field(id=2,kind='text',label='Favorite food'))
    referrals.contextualize(fields)
    assert referrals.answer_for(child,{}) is None
