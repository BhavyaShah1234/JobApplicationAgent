"""Regression cases from watched portals; expected answers are independently asserted."""
import json
from dataclasses import replace
import pytest
from jobagent.tools import forms
from jobagent.filling.facts import Fact,compile_facts,choice
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.routing import route,resolve_routed,binding_valid
from test_filling_strategy import world


def no_questions(_):pytest.fail('All required facts were provided')


def test_a_reader_refresh_cannot_redirect_school_answer_into_degree(world):
    world.setup('<input id=transient><label for=school>School</label><input id=school><label for=degree>Degree</label><input id=degree>')
    world.page.goto('https://fixture.test/apply');old=forms.read_page(world.page);school=old.fields[1]
    world.page.locator('#transient').evaluate('e=>e.remove()');forms.read_page(world.page)
    forms.fill_field(world.page,old,school,'Example University',{})
    assert world.page.locator('#school').input_value()=='Example University'
    assert world.page.locator('#degree').input_value()==''


def test_full_native_catalogue_resolves_an_offered_value_after_position_300(world):
    world.setup('<label for=s>University</label><select id=s required><option disabled selected>Select...</option>'+''.join(f'<option>School {i}</option>' for i in range(351))+'</select>')
    world.page.goto('https://fixture.test/apply');filler=PlannedFiller([Fact('s',['University'],'School 350','applicant/education')],no_questions)
    result=filler.fill(world.page,advance_pages=False)
    assert result['state']=='page_complete'
    assert world.page.locator('#s').input_value()=='School 350'
    assert len(forms.read_page(world.page).fields[0].options)==351


def test_completed_catalogue_does_not_accept_an_invented_option():
    f=forms.Field(id=0,kind='combobox',label='Arrangement',options=['Remote','Office'],options_state='complete')
    assert choice(f,['Invented option']) is None


def test_global_language_question_uses_candidate_language_records():
    facts=compile_facts({'experience':[{'question':'Languages','answer':[{'language':'English','reading':'Fluent'}]}]},'')
    f=forms.Field(id=0,kind='checkboxes',label='Language Skill(s) (Check all that apply)',section='SUPPLEMENTARY QUESTIONS',options=['English (ENG)','French (FRA)'])
    answer=resolve_routed(f,facts)
    assert answer.value=='English (ENG)'


def test_future_contact_permission_does_not_grant_external_sharing():
    facts=compile_facts({'consents':[{'question':'Contact about future job opportunities','answer':True}]},'')
    f=forms.Field(id=0,kind='radio',label='Please share my resume with external partners',options=['Yes','No'])
    r=route(f,facts)
    assert not binding_valid(f,facts,'Yes',r)
    assert resolve_routed(f,facts,r).value=='No'


def test_no_current_clearance_does_not_establish_eligibility():
    facts=compile_facts({'work':[{'question':'Do you hold a security clearance?','answer':'none'}]},'')
    f=forms.Field(id=0,kind='radio',label='Are you eligible to obtain a security clearance?',options=['Yes','No'])
    answer=resolve_routed(f,facts)
    assert answer.value is None and answer.reason=='explicit owner evidence missing'


def test_residence_predicate_compares_the_actual_state():
    facts=compile_facts({'address':[{'question':'Current address','answer':{'state':'New York'}}]},'')
    f=forms.Field(id=0,kind='radio',label='Are you a resident of California?',options=['Yes','No'])
    assert resolve_routed(f,facts).value=='No'


def test_today_rule_is_authorized_date_evidence():
    facts=compile_facts({'dates':[{'question':'Signature date','seen':['Date'], 'answer':'',
        'instructions':"Today's date in the form's format (MM/DD/YYYY)."}]},'',reference_date='2026-11-15')
    f=forms.Field(id=0,kind='text',label='Date',format='MM/DD/YYYY')
    assert resolve_routed(f,facts).value=='11/15/2026'


def test_owned_question_text_beats_generic_placeholder(world):
    world.setup('<li class=application-question><div class=application-label>Preferred Name | What would you like us to call you?</div><div class=application-field><input placeholder="Type your response"></div></li>')
    world.page.goto('https://fixture.test/apply')
    assert forms.read_page(world.page).fields[0].label.startswith('Preferred Name')


def test_styled_education_heading_is_scoped_to_its_own_region(world):
    world.setup('<h1>Apply</h1><div><div class=education--header><p>Education</p></div>'
       '<label for=school--0>School</label><input id=school--0>'
       '<label for=school--1>School</label><input id=school--1></div><label for=email>Email</label><input id=email>')
    world.page.goto('https://fixture.test/apply');fs=forms.read_page(world.page).fields
    assert [f.section for f in fs[:2]]==['Education 1','Education 2']
    assert fs[2].section=='Apply'


def test_simple_plan_has_bounded_full_scans_and_correct_targeted_readback(world,monkeypatch):
    count=0;original=forms.read_page
    def counted(*a,**k):
        nonlocal count
        count+=1;return original(*a,**k)
    monkeypatch.setattr(forms,'read_page',counted)
    world.setup(''.join(f'<label for=f{i}>Field {i}</label><input id=f{i} required>' for i in range(20)))
    world.page.goto('https://fixture.test/apply')
    result=PlannedFiller([Fact(str(i),[f'Field {i}'],f'value{i}','applicant') for i in range(20)],no_questions).fill(world.page,advance_pages=False)
    assert result['state']=='page_complete' and count<=8
    assert all(world.page.locator(f'#f{i}').input_value()==f'value{i}' for i in range(20))


def test_upload_widget_with_auxiliary_buttons_retains_owned_ack(world):
    resume=world.job/'resume.pdf';resume.write_bytes(b'%PDF test actual bytes')
    world.setup('<div class=file-upload><label for=r>Resume</label><input type=file id=r '
        'onchange="document.getElementById(\'ack\').textContent=this.files[0].name;this.remove()">'
        '<button type=button>Attach</button><button type=button>Dropbox</button><span id=ack></span></div>')
    world.page.goto('https://fixture.test/apply')
    result=PlannedFiller([],no_questions).fill(world.page,{'resume':str(resume)},advance_pages=False)
    assert result['state']=='page_complete',result['blockers']
    assert world.page.locator('#ack').inner_text()=='resume.pdf'
    assert any(r.get('held_file_sha256') for r in result['records'])


def test_resume_derived_preference_cannot_bypass_owner_authority():
    fact=Fact('derived',['Preferred technical domain'],'Backend','applicant/derived',authority='resume')
    f=forms.Field(id=0,kind='text',label='Preferred technical domain')
    asked=[];filler=PlannedFiller([fact],lambda qs:asked.extend(qs) or {})
    assert filler.plan([f])[0].value is None
    assert len(asked)==1


def test_posting_answer_sheet_has_purpose_specific_consent_and_priority():
    from jobagent.filling.routing import answer_sheet_facts
    from jobagent.filling.facts import resolve
    sheet='| 1 | I acknowledge the arbitration agreement | Yes; read by owner |'
    fs=answer_sheet_facts(sheet)
    field=forms.Field(id=0,kind='checkbox',label='I acknowledge the arbitration agreement')
    assert resolve_routed(field,fs).value=='Yes'
    conflicting=Fact('learned:old',[field.label],'No','learned:old')
    assert resolve(field,[conflicting,*fs]).value=='Yes'


def test_writer_constraints_cannot_be_waived_by_a_model_checker():
    from types import SimpleNamespace
    from jobagent.filling.planned import Mappings,Mapping,Supported
    fact=Fact('writing',['Technical experience question'],'','applicant/written',instructions='Write 120-200 words from the resume.')
    class ShortWriter:
        model_name='fixture'
        def with_structured_output(self,schema,**kwargs):
            return SimpleNamespace(invoke=lambda _:Mappings(mappings=[Mapping(field_id=0,fact_id='writing',value='Too short.')]) if schema is Mappings else Supported(supported_ids=[0]))
    field=forms.Field(id=0,kind='textarea',label='Describe your experience building a backend service',required=True)
    filler=PlannedFiller([fact],no_questions,ShortWriter())
    assert filler.plan([field])[0].value is None
    assert filler.model_calls==2


def test_inapplicable_unlisted_school_field_is_not_a_missing_owner_answer():
    from types import SimpleNamespace
    from jobagent.filling.routing import unlisted_school
    field=forms.Field(id=1,kind='text',label='If you did not see your University listed, provide the school name')
    selected=forms.Field(id=0,kind='select',label='School',section='Education 1',value='Example University')
    facts=[Fact('school',['School'],'Example University','applicant/education',section='Education 1')]
    assert unlisted_school(field,SimpleNamespace(fields=[selected]),facts).value==''
    selected.value='Other'
    assert unlisted_school(field,SimpleNamespace(fields=[selected]),facts).value=='Example University'


@pytest.mark.parametrize('state',['busy','error'])
def test_native_bytes_do_not_override_upload_widget_busy_or_error(world,state):
    from jobagent.filling.uploads import UploadProof
    path=world.job/'resume.pdf';path.write_bytes(b'%PDF independently checked bytes')
    indicator='<span aria-busy=true>Uploading</span>' if state=='busy' else '<span role=alert>Upload failed</span>'
    world.setup('<div class=file-upload><label for=r>Resume</label><input type=file id=r>'+indicator+'</div>')
    world.page.goto('https://fixture.test/apply');native=world.page.locator('#r')
    proof=UploadProof(path,native);proof.bind(native);native.set_input_files(str(path))
    assert not proof.verify()
    world.page.locator('span').evaluate('e=>e.remove()')
    assert proof.verify()


def test_observable_email_and_plain_year_rules_do_not_need_the_writer():
    email=Fact('email',['Email'],'owner@example.test','applicant/contact',instructions="On portals with an account the form shows the account's email (the agent's Gmail): keep that.")
    empty=forms.Field(id=0,kind='email',label='Email')
    assert resolve_routed(empty,[email]).value=='owner@example.test'
    empty.value='account@example.test'
    assert resolve_routed(empty,[email]).value=='account@example.test'
    years=Fact('years',['Years of relevant experience'],'2.4','applicant/experience',instructions="For 'over N years' questions compare with it.",authority='resume')
    field=forms.Field(id=1,kind='text',label='How many years of industry experience do you have?')
    filler=PlannedFiller([years],no_questions)
    assert filler.plan([field])[1].value=='2.4'
    field.label='Do you have over 3 years of industry experience?';field.kind='radio';field.options=['Yes','No']
    assert resolve_routed(field,[years]).value=='No'


def test_available_date_renders_only_the_containing_offered_quarter():
    from jobagent.filling.facts import from_fact
    fact=Fact('start',['Earliest start date'],'','applicant/start',instructions='Use the coming Monday. In a list choose immediately.',reference_date='2026-11-15')
    field=forms.Field(id=0,kind='checkboxes',label='When are you available to start full-time?',options=['Q4 2026 (October - December)','Q1 2027 (January - March)'])
    assert from_fact(field,fact).value=='Q4 2026 (October - December)'
    field.options=['Q1 2027 (January - March)']
    assert from_fact(field,fact).value is None


def test_profile_link_alternatives_use_a_saved_literal_in_question_order():
    field=forms.Field(id=0,kind='text',label='LinkedIn Profile, Github, Personal Website, or Portfolio')
    facts=[Fact('linkedin',['LinkedIn'],'https://linkedin.com/in/owner','applicant/links'),Fact('github',['GitHub'],'https://github.com/owner','applicant/links')]
    assert resolve_routed(field,facts).value=='https://linkedin.com/in/owner'
    facts[0].aliases.append('LinkedIn Profile')
    assert resolve_routed(field,facts).value=='https://linkedin.com/in/owner'


def test_unrelated_generic_information_is_not_a_test_score_source():
    field=forms.Field(id=0,kind='text',label="As part of our commitment to understanding candidates backgrounds, share your SAT or ACT score. This information is optional.")
    facts=[Fact('extra',['Additional information'],'Unrelated hobbies','applicant/written')]
    assert route(field,facts).family=='test_score'
    assert route(field,facts).fact_ids==[]
    assert not binding_valid(field,facts,'Unrelated hobbies',route(field,facts))


def test_add_another_toolbar_binds_to_the_nearest_owned_entity_group(world):
    world.setup('<h1>Apply</h1><section><div><div class=education--header><p>Education</p></div><label for=school--0>School</label><input id=school--0></div><footer><button>Add another</button></footer></section><label for=email>Email</label><input id=email>')
    world.page.goto('https://fixture.test/apply');view=forms.read_page(world.page)
    assert view.adds[0]['section']=='Education'


def test_dropdown_discovery_waits_for_visible_choices_not_a_hidden_other_menu(world):
    world.setup('''<div hidden role=option>Hidden stale choice</div><label for=c>Degree</label><input id=c role=combobox>
      <div id=menu></div><script>c.onclick=()=>setTimeout(()=>{menu.innerHTML=`<div role="option">Master's Degree</div>`},80)</script>''')
    world.page.goto('https://fixture.test/apply');view=forms.read_page(world.page)
    forms.discover_options(world.page,view)
    assert view.fields[0].options==["Master's Degree"]


def test_combobox_can_use_only_supplied_source_variants_without_a_failed_first_write(world):
    world.setup('''<label for=c>School</label><input id=c role=combobox><div id=menu hidden><div role=option>Arizona State University</div><div role=option>Adams State University</div></div>
      <script>c.onclick=()=>menu.hidden=false;menu.onclick=e=>{c.value=e.target.textContent;menu.hidden=true}</script>''')
    world.page.goto('https://fixture.test/apply');view=forms.read_page(world.page)
    selected=forms.fill_field(world.page,view,view.fields[0],'Arizona State University - Tempe',{},strict=True,alternatives=['Arizona State University'])
    assert selected=='Arizona State University'
    assert world.page.locator('#c').input_value()==selected


def test_all_source_education_rows_are_added_before_answer_planning(world):
    world.setup('''<h1>Apply</h1><section><div id=entries><div class=education--header><p>Education</p></div><label for=school--0>School</label><input id=school--0 required></div><footer><button type=button onclick="entries.insertAdjacentHTML('beforeend','<label for=school--1>School</label><input id=school--1 required>')">Add another</button></footer></section>''')
    world.page.goto('https://fixture.test/apply')
    facts=[Fact('s1',['School'],'First University','applicant/education',section='Education 1'),Fact('s2',['School'],'Second University','applicant/education',section='Education 2')]
    result=PlannedFiller(facts,no_questions).fill(world.page,advance_pages=False)
    assert result['state']=='page_complete'
    assert [world.page.locator(f'#school--{i}').input_value() for i in range(2)]==['First University','Second University']


def test_referrer_email_is_not_overwritten_with_applicants_email():
    field=forms.Field(id=0,kind='email',label='Employee referral email')
    facts=[Fact('personal-email',['Email'],'candidate@example.test','applicant/contact')]
    filler=PlannedFiller(facts,no_questions,referral_context={'company':'Example','contact':{'name':'Referrer Owner','email':'referrer@example.test'}})
    assert filler.plan([field])[0].value=='referrer@example.test'


def test_unsupplied_conference_history_requires_owner_evidence():
    field=forms.Field(id=0,kind='textarea',label='Please let us know of any conferences you are planning to attend or have previously attended.')
    facts=[Fact('extra',['Additional information'],'Unrelated hobbies','applicant/written')]
    answer=resolve_routed(field,facts)
    assert answer.value is None and answer.reason=='explicit owner evidence missing'


def test_retained_file_of_a_different_form_cannot_acknowledge_this_widget(world):
    from jobagent.filling.uploads import UploadProof
    world.setup('<form id=a><div class=upload><button id=trigger>Resume</button></div></form><form id=b><input type=file name=resume id=native></form>')
    world.page.goto('https://fixture.test/apply');path=world.job/'resume.pdf';path.write_bytes(b'%PDF-1.4 proof')
    proof=UploadProof(path,world.page.locator('#trigger'));proof.bind(world.page.locator('#native'));world.page.locator('#native').set_input_files(str(path))
    assert not proof.verify()
    proof.close()


def test_missing_account_history_cannot_use_a_previous_application_answer():
    field=forms.Field(id=0,kind='select',label='Are you currently a Wealthfront client?',options=['Yes','No'])
    other=Fact('old',['Have you previously applied?'],'No','applicant/history')
    assert resolve_routed(field,[other]).value is None
    assert route(field,[other]).fact_ids==[]


def test_missing_personal_history_is_asked_without_a_model_guess():
    class DoNotCall:
        def with_structured_output(self,*a,**k):pytest.fail('Known missing personal history should not trigger writer calls')
    field=forms.Field(id=0,kind='textarea',label='Which conferences are you planning to attend or have previously attended?')
    facts=[Fact('old',['Have you applied or interviewed here before?'],'No','applicant/history'),Fact('future',['Contact about future job opportunities'],'Yes','applicant/consents')]
    questions=[];filler=PlannedFiller(facts,lambda qs:questions.extend(qs) or {},DoNotCall())
    assert filler.plan([field])[0].value is None
    assert len(questions)==1 and filler.model_calls==0


def test_original_guard_also_rejects_unrelated_personal_history():
    from jobagent.steps import apply as A
    field=forms.Field(id=0,kind='textarea',label='Which conferences are you planning to attend?')
    answers={0:A.Answer(id=0,value='No prior applications; contact me in future',source='applicant')}
    A.enforce([field],answers,{'_applicant':{},'_jd':{},'REFERRAL':{}},log=lambda _:None)
    assert answers[0].ask_human and answers[0].value==''


def test_identity_readback_rejects_a_similar_institution_after_selection():
    assert not forms.same_value('Arizona State University','Arizona State University - Competitor','combobox','School')
    assert forms.same_value('Arizona State University','Arizona State University (ASU)','combobox','School')
    assert not forms.same_value('Jane Doe','Jane Doe Jr','combobox','Employee referral name')


def test_wrapping_native_select_label_excludes_its_catalogue(world):
    world.setup('<label>What is your location?<select><option>Select...</option><option>United States</option><option>India</option></select></label>')
    world.page.goto('https://fixture.test/apply');field=forms.read_page(world.page).fields[0]
    assert field.label=='What is your location?'
    assert field.options==['Select...','United States','India'] or field.options==['United States','India']


def test_country_catalogue_disambiguates_a_generic_location_question():
    field=forms.Field(id=0,kind='select',label='What is your location?',options=['Canada','India','Japan','United States'])
    facts=compile_facts({'address':[{'question':'Current address','answer':{'city':'Tempe','country':'United States'}}]},'')
    assert route(field,facts).family=='country'
    assert resolve_routed(field,facts).value=='United States'


def test_custom_choice_is_part_of_its_owned_pronoun_group(world):
    world.setup('<li class=application-question><div class=application-label>Pronouns</div><ul><li><label><input type=checkbox name=pronouns value="He/him">He/him</label></li><li><label><input type=checkbox name=pronouns value="They/them">They/them</label></li><li><label><input type=checkbox id=customPronounsOption value=Custom>Custom</label></li></ul></li>')
    world.page.goto('https://fixture.test/apply');view=forms.read_page(world.page)
    assert len(view.fields)==1 and view.fields[0].options==['He/him','They/them','Custom']
    facts=[Fact('pronouns',['Pronouns'],'He/Him/His','applicant/identity')]
    result=PlannedFiller(facts,no_questions).fill(world.page,advance_pages=False)
    assert result['state']=='page_complete'
    assert world.page.locator('input[value="He/him"]').is_checked()
    assert not world.page.locator('#customPronounsOption').is_checked()


def test_question_caption_does_not_expose_hidden_location_status(world):
    world.setup('<label>Current location<input name=location><span style="display:none">No location found. Try entering a different location</span><span hidden>Loading</span></label>')
    world.page.goto('https://fixture.test/apply');field=forms.read_page(world.page).fields[0]
    assert field.label=='Current location'


def test_other_website_uses_the_supplied_owner_website_and_replaces_an_unverified_prefill():
    field=forms.Field(id=0,kind='text',label='Other website',value='http://example.test/owner')
    fact=Fact('portfolio',['Portfolio / website','Portfolio URL','Website','Personal website'],'https://example.test/owner','applicant/links')
    filler=PlannedFiller([fact],no_questions)
    assert filler.plan([field])[0].value=='https://example.test/owner'


def test_unlabelled_question_fallback_also_excludes_hidden_status(world):
    world.setup('<div>Current location<input name=location><span hidden>Loading</span></div>')
    world.page.goto('https://fixture.test/apply');field=forms.read_page(world.page).fields[0]
    assert field.label=='Current location'


def test_explicit_gender_and_no_veteran_render_without_another_model_batch():
    gender=forms.Field(id=0,kind='radio',label='What best describes your gender?',options=['Woman','Man','Non-binary','Prefer not to disclose'])
    veteran=forms.Field(id=1,kind='radio',label='Are you a veteran/have you served in the military?',options=['Yes','No','Prefer not to disclose'])
    facts=[Fact('gender',['Gender'],'Male','applicant/self'),Fact('veteran',['Veteran status'],'Not a veteran','applicant/self')]
    answers=PlannedFiller(facts,no_questions).plan([gender,veteran])
    assert answers[0].value=='Man' and answers[1].value=='No'
    assert choice(veteran,['Not a protected veteran']) is None


def test_writer_packet_receives_the_complete_available_catalogue():
    from jobagent.filling.routing import field_packet
    field=forms.Field(id=0,kind='select',label='Unresolved choice',options=[f'Choice {i}' for i in range(351)])
    packet=field_packet(field)
    assert packet['options']==field.options and packet['options_total']==351
    assert packet['options_state']=='complete'
