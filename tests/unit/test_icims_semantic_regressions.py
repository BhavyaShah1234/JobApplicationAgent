"""Independent expectations from the watched Garmin application; no production IDs."""
from dataclasses import replace

import pytest

from jobagent.filling.facts import Fact, choice
from jobagent.filling.routing import route, resolve_routed, binding_valid, family, personal_facts,review_metadata_facts
from jobagent.tools.forms import Field
from jobagent.tools import referrals

OPT='Are you currently on or in the process of receiving an F-1 OPT/OPT EAD/STEM OPT (Optional Practical Training) or CPT (Curricular Practical Training)?'
STEM='Is your current EAD the STEM OPT extension?'
NONIMMIGRANT='Are you working in the U.S. in non-immigrant status (e.g., H-1B, O-1, or an L) and/or will/might you need Acme to file an H-1B transfer (or other status) OR register you in the H-1B lottery to work in the U.S., legally?'
ORIGIN='Please indicate the city and state you would be relocating from for this opportunity. Enter NA if you will not need to relocate for this opportunity.'


def binary(label):
    return Field(id=1,kind='select',label=label,options=['Yes','No'],required=True)


@pytest.mark.parametrize('label',[OPT,'Are you currently on OPT or a STEM OPT extension?','Are you currently receiving CPT?'])
def test_current_opt_is_not_negated_by_no_stem_extension(label):
    status=Fact('visa',['Visa status'],'F-1 OPT','applicant/visa')
    extension=Fact('extension',[STEM],'No','applicant/stem')
    field=binary(label);facts=[status,extension];r=route(field,facts)
    assert r.family=='opt_participation'
    # CPT-only questions require specific CPT evidence, unlike an OPT/CPT union.
    if label.endswith('CPT?'):
        assert resolve_routed(field,facts,r).value is None
    else:
        result=resolve_routed(field,facts,r)
        assert result.value=='Yes' and result.fact_id=='visa'
    assert not binding_valid(field,[extension],'No',r)


def test_no_stem_extension_alone_cannot_answer_opt_participation():
    extension=Fact('extension',[STEM],'No','applicant/stem')
    field=binary(OPT);facts=[extension]
    assert resolve_routed(field,facts).value is None
    field=binary(STEM)
    assert resolve_routed(field,facts).value=='No'


@pytest.mark.parametrize('value',['Former F-1 OPT','Eligible for OPT','Not currently on OPT','Awaiting OPT approval'])
def test_opt_status_derivation_requires_actual_current_participation(value):
    fact=Fact('visa',['Visa status'],value,'applicant')
    assert resolve_routed(binary(OPT),[fact]).value is None


def test_current_opt_status_does_not_affirm_a_negated_question():
    fact=Fact('visa',['Visa status'],'F-1 OPT','applicant')
    assert resolve_routed(binary('Are you currently not on OPT?'),[fact]).value is None


def test_nonimmigrant_employment_cannot_borrow_work_authorization_or_age_boolean():
    facts=[Fact('authorized',['Are you legally authorized to work in the United States?'],'Yes','applicant'),
           Fact('age',['Are you of legal age to work?'],'Yes','applicant')]
    field=binary(NONIMMIGRANT);r=route(field,facts)
    assert r.family=='nonimmigrant_employment'
    assert resolve_routed(field,facts,r).value is None
    assert not binding_valid(field,facts,'No',r)


def test_owner_answer_to_exact_nonimmigrant_question_can_be_used():
    fact=Fact('owner-answer',[NONIMMIGRANT],'Yes','human')
    assert resolve_routed(binary(NONIMMIGRANT),[fact]).value=='Yes'


def test_termination_history_does_not_mean_prior_employment_at_this_company():
    label='Have you ever been terminated or asked to resign by a previous employer?'
    field=binary(label);previous=Fact('previous',['Have you worked for this company before?'],'No','applicant')
    assert resolve_routed(field,[previous]).value is None
    fact=Fact('termination',[label],'No','applicant/history')
    result=resolve_routed(field,[previous,fact])
    assert result.value=='No' and result.fact_id=='termination'


def test_motivation_requires_narrative_not_a_city_literal():
    field=Field(id=1,kind='textarea',label='What is your motivation for wanting to work and reside where this position is located?')
    city=Fact('city',['Current location (city)'],'Olathe, Kansas','applicant')
    r=route(field,[city])
    assert r.family=='why_company' and 'jd' in r.sources
    assert resolve_routed(field,[city],r).value is None
    assert not binding_valid(field,[city],city.value,r)


def test_location_motivation_gets_owner_relocation_preferences_not_bare_residence():
    field=Field(id=1,kind='textarea',label='What is your motivation for wanting to work and reside where this position is located?')
    city=Fact('city',['Current location (city)'],'Olathe, Kansas','applicant')
    preference=Fact('relocate',['Willing to relocate?'],'Yes','applicant')
    r=route(field,[city,preference])
    assert r.fact_ids==['relocate']
    assert resolve_routed(field,[city,preference],r).value is None
    assert not binding_valid(field,[preference],'Olathe, Kansas',r)
    assert binding_valid(field,[preference],'I am willing to relocate for this opportunity.',r)


def test_source_refresh_cannot_restore_a_stale_salary_literal_over_the_owner_rule():
    from jobagent.filling.planned import PlannedFiller
    field=Field(id=1,kind='select',label='What is your expected annual salary range?',options=['$70,000 to $79,999/year'])
    rule=Fact('rule',['Desired salary'],'','applicant',instructions='Use the JD salary range; otherwise research the local median.',tools=['web_search'])
    old=Fact('learned:old',[field.label],'$70,000 to $79,999/year','learned:old')
    asked=[]
    def ask(fields):
        asked.extend(fields);return {str(f['id']):None for f in fields}
    filler=PlannedFiller([rule,old],ask,source_resolver=lambda *_:{})
    result=filler.plan([field])
    assert asked and result[field.id].value is None


@pytest.mark.parametrize('job_city,expected',[('Olathe','Tempe, Arizona'),('Tempe','NA')])
def test_relocation_origin_uses_original_residence_not_generated_application_address(job_city,expected):
    ctx={'_personal_address':{'city':'Tempe','state':'Arizona'},
         '_jd':{'locations_resolved':[{'city':job_city,'state':'Arizona' if job_city=='Tempe' else 'Kansas'}]}}
    facts=personal_facts(ctx)+[Fact('generated',['Current address city'],'Olathe','application/address')]
    field=Field(id=1,kind='text',label=ORIGIN)
    assert resolve_routed(field,facts).value==expected
    assert resolve_routed(field,[facts[-1]]).value is None


def test_dynamic_salary_rule_outranks_a_previously_learned_numeric_bucket():
    field=Field(id=1,kind='select',label='What is your expected annual salary range?',options=['$70,000 to $79,999/year'])
    rule=Fact('rule',['Desired salary'],'','applicant',instructions="The job description's range; if none, local median.",tools=['web_search'])
    old=Fact('old',[field.label],'$70,000 to $79,999/year','learned:company:old job')
    r=route(field,[rule,old])
    assert r.family=='salary' and r.fact_ids==['rule']
    assert resolve_routed(field,[rule,old],r).value is None
    assert family('Will you continue outside employment for compensation?')!='salary'


def test_highest_degree_gpa_is_numeric_not_the_degree_name():
    field=Field(id=1,kind='select',label='What is/was your cumulative grade point average (GPA) on a 4.0 scale for your highest earned degree?',
                options=['2.99 or lower','3.0 - 3.24','3.25 - 3.49','3.50 - 3.74','3.75 - 3.99','4.0 or above'])
    fact=Fact('gpa',['GPA'],'4.0','applicant')
    assert family(field.label)=='gpa'
    result=resolve_routed(field,[fact])
    assert result.value=='4.0 or above' and result.fact_id=='gpa'
    assert choice(field,['3.8'])=='3.75 - 3.99'


def test_generic_referral_info_binds_only_to_the_adjacent_source():
    source=Field(id=0,kind='select',label='How did you hear about this opportunity?',options=['Employee Referral','Careers website'],value='Employee Referral')
    child=Field(id=1,kind='text',label='If applicable, please provide info.')
    referrals.contextualize([source,child])
    answer=referrals.answer_for(child,{'name':'Casey Example','email':'casey@example.test'})
    assert 'Casey Example' in answer['value'] and 'casey@example.test' in answer['value']
    referrals.contextualize([source,replace(binary('Are you over age 18?'),id=2),child])
    assert not child.source_followup


def test_review_source_details_keeps_the_matching_employee_contact():
    source=Field(id=0,kind='text',label='Source',value='Employee Referral')
    child=Field(id=1,kind='text',label='Source Details')
    referrals.contextualize([source,child])
    answer=referrals.answer_for(child,{'name':'Casey Example','email':'casey@example.test'})
    assert 'Casey Example' in answer['value'] and 'casey@example.test' in answer['value']
    referrals.contextualize([child])
    assert referrals.answer_for(child,{'name':'Casey Example','email':'casey@example.test'}) is None


def test_unclassified_boolean_cannot_borrow_typed_visa_or_work_authorization_fact():
    field=binary('A previously unseen qualification?')
    fact=Fact('authorized',['Are you legally authorized to work in the United States?'],'Yes','applicant')
    r=route(field,[fact])
    assert not binding_valid(field,[fact],'Yes',r)


def test_posting_title_and_application_date_cannot_borrow_employment_learned_answers():
    from jobagent.tools.forms import PageView
    title=Field(id=0,kind='text',label='Position Title',section='Position Details')
    fields=[Field(id=i,kind='text',label='Application Date '+part,section='Position Details',
                  value='wrong',initial_value=value) for i,(part,value) in enumerate([('Month','Nov'),('Day','23'),('Year','2026')],1)]
    wrong=[Fact('learned:employer',['Title'],'Old Job','learned'),Fact('learned:date',['Start Date Month'],'May','learned')]
    facts=personal_facts({'_jd':{'title':'Posted Engineer'}})+wrong
    assert resolve_routed(title,facts).value=='Posted Engineer'
    for f in fields:assert resolve_routed(f,facts).value is None
    facts+=review_metadata_facts(PageView(fields,[],[],[],{}))
    for f,expected in zip(fields,['Nov','23','2026']):assert resolve_routed(f,facts).value==expected
    fields[1].initial_value='32'
    assert not review_metadata_facts(PageView(fields,[],[],[],{}))
