"""Company-specific owner choices remain usable when no employee source is offered."""
import pytest

from jobagent.filling.facts import Fact
from jobagent.filling.planned import PlannedFiller
from jobagent.tools.forms import Field


QUESTION='How Did You Hear About Us? *'


def saved(company='capital-one', value='Contacted by Recruiter', confirmed=True):
    source=f'learned:{company}:{QUESTION}'
    return Fact(source,[QUESTION],value,source,policy_context='employee-referral-v2' if confirmed else '')


def field(options=None):
    return Field(id=0,kind='combobox',label=QUESTION,required=True,
                 options=options or ['Contacted by Recruiter','Internet'],options_state='complete')


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


@pytest.mark.parametrize('caption',[
    'MongoDB Employee (Current or Former)', 'MongoDB Former Employee',
    'MongoDB Current or Former Employee', 'MongoDB Employee (Former)',
])
def test_employee_status_qualifiers_preserve_an_offered_referral_choice(caption):
    from jobagent.tools import referrals
    f=field(['Other',caption])
    assert referrals.answer_for(f,{},'MongoDB')['value']==caption


@pytest.mark.parametrize('caption',[
    'MongoDB Employee (not referred)', 'MongoDB Non-employee referral',
    'MongoDB Employee Recruiter', 'MongoDB Employee (external recruiter)',
])
def test_status_qualifier_support_does_not_accept_negative_or_recruiter_choices(caption):
    from jobagent.tools import referrals
    assert referrals._referral_option([caption],'MongoDB')==''


def test_adjacent_conditional_employee_source_details_use_the_matching_contact():
    from jobagent.tools import referrals
    parent=field(['MongoDB Employee (Current or Former)','Other'])
    child=Field(id=1,kind='text',label='If you answered MongoDB Employee, MongoDB Event, or Other, please specify here:')
    referrals.contextualize([parent,child])
    contact={'name':'Example Employee','email':'employee@example.test'}
    assert referrals.answer_for(child,contact,'MongoDB')['value']=='Referred by employee: Example Employee (employee@example.test)'
    assert referrals.answer_for(child,{},'MongoDB')['ask_human']


@pytest.mark.parametrize('boundary',['frame','section','unrelated','no_employee_choice'])
def test_conditional_employee_source_details_remain_bound_to_the_adjacent_source(boundary):
    from jobagent.tools import referrals
    parent=field(['Employee Referral','Other'])
    child=Field(id=1,kind='text',label='If you answered Employee, Event, or Other, please specify here:')
    if boundary=='frame':child.frame=1
    if boundary=='section':child.section='Unrelated'
    if boundary=='no_employee_choice':child.label='If you answered Yes, please specify here:'
    fields=[parent,child]
    if boundary=='unrelated':fields.insert(1,Field(id=2,kind='text',label='Favorite food'))
    referrals.contextualize(fields)
    assert referrals.answer_for(child,{'name':'Example','email':'e@example.test'}) is None


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


@pytest.mark.parametrize('selected',['','SAS Employee Referral'])
def test_named_company_source_specify_uses_referrer_details_not_other_source(selected):
    from jobagent.tools import referrals
    parent=Field(id=0,kind='select',label='How did you hear about SAS?',
                 options=['SAS Employee Referral','Job Advertisement'],value=selected)
    child=Field(id=1,kind='text',label='Please specify',required=True)
    referrals.contextualize([parent,child])
    contact={'name':'Casey Morgan','email':'casey@example.test'}
    assert referrals.answer_for(child,contact,'SAS')['value']=='Referred by employee: Casey Morgan (casey@example.test)'
    assert referrals.answer_for(child,{},'SAS')['ask_human']
    assert referrals.answer_for(parent,{},'SAS')['ask_human']
    assert referrals.answer_for(parent,contact,'SAS')['value']=='SAS Employee Referral'


@pytest.mark.parametrize('has_contact',[False,True])
def test_confirmed_company_source_survives_a_fresh_run_when_referrer_is_unavailable(has_contact):
    question='How did you hear about SAS?'
    approved=saved('sas','University Outreach or Emerging Careers Events')
    approved.aliases=[question]
    f=Field(id=0,kind='select',label=question,required=True,options_state='complete',
            options=['SAS Employee Referral','University Outreach or Emerging Careers Events'],value='University Outreach or Emerging Careers Events')
    from jobagent.tools import referrals
    referrals.contextualize([f,Field(id=1,kind='select',label='Please specify',options=['Career Fair'])])
    contact={'name':'Casey Morgan','email':'casey@example.test'} if has_contact else {}
    agent=PlannedFiller([approved],lambda _:pytest.fail('Confirmed source should be reusable'),
                        referral_context={'company':'SAS','contact':contact})
    expected='SAS Employee Referral' if has_contact else 'University Outreach or Emerging Careers Events'
    assert agent.plan([f],allow_human=False)[0].value==expected


def test_existing_nonemployee_source_detail_select_cannot_silently_switch_to_employee():
    from jobagent.tools import referrals
    parent=Field(id=0,kind='select',label='How did you hear about SAS?',
                 options=['SAS Employee Referral','University Outreach'],value='University Outreach')
    child=Field(id=1,kind='select',label='Please specify',options=['Career Fair'],value='Career Fair')
    referrals.contextualize([parent,child])
    assert parent.referrer_details_requested
    assert referrals.answer_for(parent,{},'SAS')['ask_human']


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


@pytest.mark.parametrize('options_state', ['partial', 'unreadable'])
def test_a_partial_or_unreadable_source_menu_does_not_validate_a_saved_fallback(options_state):
    f = field(); f.options_state = options_state
    d = filler([saved()]).plan([f], allow_human=False)[0]
    assert d.value != 'Contacted by Recruiter'


def test_historical_source_answer_without_current_policy_confirmation_cannot_override_referral():
    legacy = saved('paypal', 'Direct Source Candidates > PayPal Career site', confirmed=False)
    agent = PlannedFiller([legacy], lambda _: {}, referral_context={'company': 'PayPal', 'contact': {}})
    f = field(['Direct Source Candidates > PayPal Career site'])
    assert agent.plan([f], allow_human=False)[0].value is None


def test_unreadable_menu_is_not_treated_as_missing_employee_referral():
    from jobagent.tools import referrals
    f = field(['Employee Referral']); f.options_state = 'unreadable'
    assert referrals.answer_for(f, {})['ask_human']


def test_a_lazy_empty_source_picker_can_search_the_referral_policy_without_inventing_a_fallback():
    from jobagent.tools import referrals
    f = field(); f.options = []; f.options_state = 'unreadable'
    answer = referrals.answer_for(f, {})
    assert not answer['ask_human'] and 'Employee Referral' in answer['value']
    assert 'Recruiter' not in answer['value']


def test_current_job_owner_source_answer_is_not_lost_on_restart():
    from jobagent.filling.routing import answer_sheet_facts
    f = field(['Career site', 'Employee Referral'])
    facts = answer_sheet_facts('| # | Question | Answer |\n| 1 | '+QUESTION+' | Career site |')
    decision = filler(facts).plan([f])[0]
    assert decision.value == 'Career site'
    assert decision.fact_id.startswith('application.answer.')
