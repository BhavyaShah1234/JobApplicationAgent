"""Visible optional Other fields follow their actual source selection, including changes."""
import pytest
from jobagent.tools import referrals, forms
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.facts import key


def fields(value='Employee Referral', *, required=False):
    return [forms.Field(1,'select','How did you hear about us?',value=value,options=['Employee Referral','Other'],section='Application'),
            forms.Field(2,'text','If Other, please specify',required=required,value='stale referral prose',section='Application')]


def filler():
    return PlannedFiller([],lambda *_:pytest.fail('An inapplicable optional field needs no human'),
                         referral_context={'company':'Example','contact':{}})


def test_optional_other_is_cleared_without_demanding_a_referrer_or_human():
    parent,child=fields();referrals.contextualize([parent,child]);agent=filler()
    decision=agent.plan([child])[2]
    assert decision.value=='' and agent.questions==agent.model_calls==0
    assert not parent.referrer_details_requested


def test_parent_changes_invalidate_the_old_policy_answer_in_both_directions():
    parent,child=fields('Other');referrals.contextualize([parent,child]);agent=filler()
    old=agent.plan([child])[2];assert old.value=='Referred by employee'
    agent.cache[key(child)]=old
    parent.value='Employee Referral';referrals.contextualize([parent,child])
    assert not agent.can_reuse(child)
    assert not agent.can_execute(child,child,old)
    clear=agent.plan([child])[2];assert clear.value==''
    agent.cache[key(child)]=clear
    parent.value='Other';referrals.contextualize([parent,child])
    assert not agent.can_reuse(child)
    assert agent.plan([child])[2].value=='Referred by employee'


def test_a_specific_source_category_keeps_the_other_followup_bound():
    parent,child=fields()
    specific=forms.Field(3,'combobox','Specific Source',value='Employee Referral',
                         options=['Employee Referral','Other'],section='Application')
    referrals.contextualize([parent,specific,child])
    assert referrals.answer_for(child,{})['clear']


@pytest.mark.parametrize('boundary',['frame','section','unrelated'])
def test_unrelated_other_fields_are_never_cleared(boundary):
    parent,child=fields();items=[parent,child]
    if boundary=='frame':child.frame=1
    if boundary=='section':child.section='Unrelated'
    if boundary=='unrelated':items.insert(1,forms.Field(3,'text','Favorite food',section='Application'))
    referrals.contextualize(items)
    assert referrals.answer_for(child,{}) is None


def test_an_unexpected_required_other_field_is_not_cleared():
    parent,child=fields(required=True);referrals.contextualize([parent,child])
    assert not referrals.answer_for(child,{}).get('clear')


@pytest.mark.parametrize('value',['Other (please specify)','Other - Please specify','Internet > Other'])
def test_qualified_other_choices_still_activate_the_followup(value):
    parent,child=fields(value);referrals.contextualize([parent,child])
    assert referrals.answer_for(child,{})['value']=='Referred by employee'


def test_parent_selection_is_part_of_adaptive_question_identity():
    from jobagent.filling.adaptive import schema
    parent,child=fields('Other');referrals.contextualize([parent,child]);before=schema(child)
    parent.value='Employee Referral';referrals.contextualize([parent,child])
    assert schema(child)!=before


def test_an_answer_sheet_does_not_make_an_inactive_other_field_applicable():
    from jobagent.filling.facts import Fact
    parent,child=fields();referrals.contextualize([parent,child])
    agent=filler();agent.facts=[Fact('application.answer.1',[child.label],'old Other text','applicant/application/answers.md')]
    assert agent.plan([child])[2].value==''
