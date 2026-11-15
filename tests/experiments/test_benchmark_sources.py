"""The benchmark must never obtain a missing applicant answer from the grading oracle."""
from jobagent.experiments.benchmark import inputs_for,human_reply
from jobagent.experiments.resolution import Fact


def test_canonical_profile_is_compiled_without_answer_leakage_or_source_mutation():
    case={'name':'source-contract','applicant':{'Contact':[
        {'question':'Legal name','answer':{'first':'Jordan','last':'Lee'}},
        {'question':'Email','answer':'jordan@example.test'}]},
        'expected':{'Unknown personal fact':'DO NOT LEAK THIS'},'human_answers':{'Missing preference':'Phone'}}
    data,facts=inputs_for(case)
    assert human_reply(case,facts,'Given Name')=='Jordan'
    assert human_reply(case,facts,'Unknown personal fact') is None
    assert human_reply(case,facts,'Missing preference')=='Phone'
    data['Contact'][0]['answer']['first']='Changed in private trial'
    assert case['applicant']['Contact'][0]['answer']['first']=='Jordan'


def test_prepared_address_facts_are_available_to_both_arms():
    case={'name':'prepared-address','facts':[],'prepared_address':True}
    data,facts=inputs_for(case)
    address=data['contact'][0]['answer']
    assert human_reply(case,facts,'City')==address['city']
    assert human_reply(case,facts,'Postal Code')==address['postal_code']


def test_simulated_owner_uses_entity_context_when_the_question_supplies_it():
    facts=[Fact('home',['City'],'Houston','Current address'),
           Fact('job',['City'],'Chicago','Work history','Work Experience 2')]
    assert human_reply({},facts,'City','Work Experience 2')=='Chicago'
    assert human_reply({},facts,'City') is None  # an unscoped prompt does not identify the requested entity
