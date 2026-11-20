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


def test_dashboard_telemetry_failure_cannot_turn_an_accepted_field_into_a_fill_failure(world, monkeypatch):
    world.setup('<label for=email>Email</label><input id=email type=email required>')
    world.page.goto('https://fixture.test/apply')
    def broken_progress(*args, **kwargs):
        raise RuntimeError('fixture telemetry failure')
    monkeypatch.setattr('jobagent.log.progress', broken_progress)
    result = PlannedFiller([Fact('email', ['Email'], 'fixture@example.test', 'applicant/contact')], no_questions).fill(
        world.page, advance_pages=False)
    assert result['state'] == 'page_complete'
    assert world.page.locator('#email').input_value() == 'fixture@example.test'


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


@pytest.mark.parametrize('adaptive',[False,True])
def test_timezone_residence_cannot_borrow_work_authorization_or_selected_application_address(adaptive):
    from jobagent.filling.adaptive import AdaptiveFiller
    from jobagent.filling.facts import Decision
    label='This role requires that you are located on the East Coast of the United States or in the Eastern Time Zone. Do you meet this requirement?'
    field=forms.Field(id=0,kind='radio',label=label,required=True,options=['Yes','No'])
    facts=[Fact('authorization',['Are you legally authorized to work in the United States?'],'Yes','applicant/work_authorization'),
           Fact('relocation',['Are you willing to relocate?'],'Yes','applicant/preferences'),
           Fact('owner.residence.state',['Actual residence state'],'Arizona','personal/address/state'),
           Fact('application-address',['State'],'Massachusetts','application/address')]
    assert route(field,facts).family=='residency'
    assert resolve_routed(field,facts).value is None
    assert not binding_valid(field,[facts[0]],'Yes',route(field,[facts[0]]))
    asked=[]
    def ask(questions):
        asked.append(questions);return {}
    filler=(AdaptiveFiller if adaptive else PlannedFiller)(facts,ask,
        source_resolver=lambda fields,answers:{0:Decision('Yes','applicant/work_authorization','authorization')})
    assert filler.plan([field])[0].value is None and len(asked)==1
    assert resolve_routed(field,[*facts,Fact('exact',[label],'No','applicant/history')]).value=='No'


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


@pytest.mark.parametrize('channel',['email','phone'])
def test_notification_acknowledgement_is_not_a_contact_question(channel):
    label=f'I understand that next steps will be sent via {channel} and I have marked the sender addresses above as safe.'
    field=forms.Field(id=0,kind='checkbox',label=label,required=True)
    facts=[Fact('email',['Email'],'owner@example.test','applicant/contact'),
           Fact('phone',['Phone'],'5551234567','applicant/contact'),
           Fact('other',['I understand that I have read the privacy notice.'],'Yes','applicant/consents')]
    assert route(field,facts).family=='read_ack'
    assert resolve_routed(field,facts).value is None
    approved=Fact('exact',[label],'Yes','applicant/consents')
    assert resolve_routed(field,[*facts,approved]).value=='Yes'


@pytest.mark.parametrize('adaptive',[False,True])
def test_unanswered_acknowledgement_asks_owner_instead_of_dispatching_contact_literal(adaptive):
    from jobagent.filling.adaptive import AdaptiveFiller
    from jobagent.filling.facts import Decision
    label='I understand that next steps will be sent via email and I have marked the sender addresses above as safe.'
    field=forms.Field(id=0,kind='checkbox',label=label,required=True)
    asked=[]
    def ask(questions):
        asked.append(questions);return {}
    filler=(AdaptiveFiller if adaptive else PlannedFiller)(
        [Fact('email',['Email'],'owner@example.test','applicant/contact')],ask,
        source_resolver=lambda fields,answers:{0:Decision('owner@example.test','legacy contact shortcut','email')})
    assert filler.plan([field])[0].value is None
    assert len(asked)==1 and asked[0][0]['label']==label
    assert filler.failures=={}


@pytest.mark.parametrize('adaptive',[False,True])
def test_notification_ack_stays_unchecked_without_owner_confirmation(world,adaptive):
    from jobagent.filling.adaptive import AdaptiveFiller
    label='I understand that next steps will be sent via email and I have marked the sender addresses above as safe.'
    world.setup('<label for=email>Email</label><input id=email type=email required>'
                f'<label for=ack>{label}</label><input id=ack type=checkbox required>')
    world.page.goto('https://fixture.test/apply');asked=[]
    def ask(questions):
        asked.append(questions);return {}
    filler=(AdaptiveFiller if adaptive else PlannedFiller)(
        [Fact('email',['Email'],'owner@example.test','applicant/contact')],ask)
    result=filler.fill(world.page,advance_pages=False)
    assert result['state']=='blocked' and len(asked)==1
    assert world.page.locator('#email').input_value()=='owner@example.test'
    assert not world.page.locator('#ack').is_checked() and filler.failures=={}
    assert not any('repair budget' in issue for issue in result['blockers'])


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


@pytest.mark.parametrize('contact,expected', [
    ({'name':'Referrer Owner','email':'referrer@example.test'},'Referrer Owner (referrer@example.test)'),
    ({},None),
])
def test_pronominal_referrer_followup_uses_company_contact_or_stays_unanswered(world,contact,expected):
    world.setup('''<h1>My Information</h1><label for=source>How Did You Hear About Us?</label>
    <input id=source value="Know Someone at the Company">
    <label for=person>What's their name or email address?</label><input id=person>
    <label for=email>Email</label><input id=email type=email>''')
    world.page.goto('https://fixture.test/apply')
    fields=forms.read_page(world.page).fields
    assert 'referrer' in fields[1].help
    assert 'referrer' not in fields[2].help
    facts=[Fact('personal-email',['Email'],'candidate@example.test','applicant/contact')]
    filler=PlannedFiller(facts,no_questions,model=object(),referral_context={'company':'Example','contact':contact},
                         source_resolver=lambda fields,answers:{})
    decisions=filler.plan(fields[1:],allow_human=False)
    assert decisions[fields[1].id].value==expected
    assert decisions[fields[2].id].value=='candidate@example.test'
    assert filler.model_calls==0


@pytest.mark.parametrize('label', ["What's their name or email address?",'Their phone number','Her email'])
def test_unbound_third_party_contact_cannot_resolve_to_applicant(label):
    field=forms.Field(id=0,kind='text',label=label)
    facts=[Fact('personal-email',['Email'],'candidate@example.test','applicant/contact'),
           Fact('personal-phone',['Phone'],'5551234567','applicant/contact')]
    assert not route(field,facts).fact_ids
    assert PlannedFiller(facts,no_questions).plan([field],allow_human=False)[0].value is None


def test_sourcing_context_does_not_cross_a_frame_or_an_unrelated_question():
    from jobagent.tools import referrals
    source=forms.Field(id=0,kind='text',label='How did you hear about this position?',value='Employee Referral')
    child=forms.Field(id=1,kind='text',label='Their email',frame=1)
    unrelated=forms.Field(id=2,kind='text',label='First Name')
    later=forms.Field(id=3,kind='text',label='Their email')
    referrals.contextualize([source,child,unrelated,later])
    assert not child.help and not later.help


def test_optional_resume_autofill_trigger_is_not_a_second_resume_attachment(world,tmp_path):
    from test_documents import PDF
    resume=tmp_path/'resume.pdf';resume.write_bytes(PDF)
    cover=tmp_path/'cover_letter.pdf';cover.write_bytes(PDF+b'\ncover letter')
    world.setup('''<h2>Autofill from resume</h2><div><button type=button
      onclick="window.parserClicks=(window.parserClicks||0)+1;const e=document.createElement('input');e.type='file';this.parentElement.append(e)">Upload file</button></div>
      <label for=resume>Resume*</label><input id=resume type=file required>
      <label for=cover>Cover Letter</label><input id=cover type=file>''')
    world.page.goto('https://fixture.test/apply')
    parser=next(f for f in forms.read_page(world.page).fields if f.kind=='file' and f.label=='Upload file')
    assert 'autofill' in parser.section.lower() and not parser.required
    result=PlannedFiller([],no_questions).fill(world.page,{'resume':str(resume),'cover_letter':str(cover)},advance_pages=False)
    assert result['state']=='page_complete'
    assert world.page.evaluate('window.parserClicks||0')==0
    assert world.page.locator('#resume').evaluate('e=>e.files[0].name')=='resume.pdf'
    assert world.page.locator('#cover').evaluate('e=>e.files[0].name')=='cover_letter.pdf'


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


def test_general_experience_is_not_a_technology_duration():
    fact=Fact('years',['Years of relevant experience'],'2.4','applicant/experience')
    field=forms.Field(id=0,kind='text',label='How many years of experience do you have with C++?')
    assert not route(field,[fact]).fact_ids
    assert resolve_routed(field,[fact]) is None
    explicit=Fact('cpp',['Years of experience with C++'],'3','applicant/qualified')
    assert resolve_routed(field,[fact,explicit]).value=='3'
    field.label='How many years of industry experience do you have?'
    assert resolve_routed(field,[fact]).value=='2.4'


def test_clearance_levels_and_history_do_not_borrow_a_different_predicate():
    field=forms.Field(id=0,kind='radio',label='Are you eligible to obtain a TS/SCI clearance?',options=['Yes','No'])
    fact=Fact('secret',['Are you eligible to obtain a Secret clearance?'],'Yes','applicant/clearance')
    assert not route(field,[fact]).fact_ids
    assert resolve_routed(field,[fact]).value is None
    exact=Fact('ts',[field.label],'Yes','applicant/clearance')
    assert resolve_routed(field,[fact,exact]).value=='Yes'
    current=Fact('current',['Do you hold a security clearance?'],'No','applicant/clearance')
    field.label='Have you ever held a security clearance?'
    answer=resolve_routed(field,[current])
    assert answer is None or answer.value is None


def test_new_owner_rule_is_applied_in_the_same_planning_pass_without_literal_rule_text():
    field=forms.Field(id=0,kind='text',label='What response should be entered?',required=True)
    requests=[]
    def answer(qs):
        requests.append(qs)
        return {'0':Fact('new-rule',[field.label],'','human',instructions='Use 42.')}
    # prepare_sources intentionally rebuilds the source bank; session evidence
    # must survive it, as it does in the real application adapter.
    filler=PlannedFiller([],answer,prepare_sources=lambda _:[])
    assert filler.plan([field])[0].value=='42'
    assert len(requests)==1


def test_literal_owner_answer_is_reused_on_the_next_step():
    field=forms.Field(id=0,kind='text',label='Which professional conference did you attend?',required=True,identity='first')
    asked=[]
    filler=PlannedFiller([],lambda qs:asked.append(qs) or {'0':'Owner supplied conference'},prepare_sources=lambda _:[])
    assert filler.plan([field])[0].value=='Owner supplied conference'
    filler.step=2;field=replace(field,id=1,identity='second',section='Additional questions')
    assert filler.plan([field])[1].value=='Owner supplied conference'
    assert len(asked)==1


def test_ambiguous_short_owner_answer_is_not_reused_for_another_control():
    one=forms.Field(id=0,kind='text',label='Other',identity='first')
    two=replace(one,id=1,identity='second')
    answers=[]
    def ask(qs):
        answers.append(qs);return {str(qs[0]['id']):'First value'} if len(answers)==1 else {}
    filler=PlannedFiller([],ask)
    assert filler.plan([one])[0].value=='First value'
    assert filler.plan([two])[1].value is None
    assert len(answers)==2


def test_reference_contact_and_language_remain_owned_by_the_reference():
    data={'contact':[{'question':'Email','answer':'candidate@example.test'}],
          'experience':[{'question':'Languages','answer':[{'language':'English'}]}],
          'references':[{'question':'References','answer':[{'name':'Reference Owner','email':'reference@example.test','language':'French'}]}]}
    facts=compile_facts(data,'')
    email=forms.Field(id=0,kind='email',label='Email',section='Reference 1')
    language=forms.Field(id=1,kind='select',label='Language',section='Reference 1',options=['English','French'])
    assert resolve_routed(email,facts).value=='reference@example.test'
    assert resolve_routed(language,facts).value=='French'


def test_supervisor_contact_cannot_use_the_applicants_email():
    field=forms.Field(id=0,kind='email',label='Supervisor email',section='Additional questions')
    fact=Fact('self',['Email'],'candidate@example.test','applicant/contact')
    assert route(field,[fact]).fact_ids==[]
    assert resolve_routed(field,[fact]) is None


def test_own_initialism_and_type_inside_parentheses_is_the_same_institution():
    name='Vellore Institute of Technology'
    assert forms.identity_option(name,[name+' (VIT University)'])==name+' (VIT University)'
    assert forms.identity_option(name,[name+' (VIT Competitor)']) is None
    assert forms.identity_option('Arizona State University',['Arizona State University (West Campus)']) is None


def test_explicit_other_readback_is_valid_but_never_aliases_a_real_name():
    assert forms.same_value('Other','Other','combobox','School')
    assert not forms.same_value('Example University','Other','combobox','School')


def test_strict_institution_filling_rejects_an_unapproved_parenthesized_branch(world):
    world.setup('<label for=s>School</label><select id=s><option>Arizona State University (West Campus)</option></select>')
    world.page.goto('https://fixture.test/apply');view=forms.read_page(world.page)
    with pytest.raises(forms.FillError):forms.fill_field(world.page,view,view.fields[0],'Arizona State University',{},strict=True)
    assert choice(view.fields[0],['Arizona State University']) is None


def test_legacy_school_condition_is_evaluated_after_all_rows_are_filled(world):
    from jobagent.steps import apply as A
    world.setup('<h2>Education</h2><label for=school>School</label><select id=school><option selected>Other</option></select>'
                '<h2>Additional questions</h2><label for=unlisted>If you did not see your university listed, please give its name</label><input id=unlisted>')
    world.page.goto('https://fixture.test/apply');ctx={'_applicant':{'education':[{'question':'Degrees','answer':[{'school':['Example University','Other']}]}]}}
    done={};view=A._reconcile_unlisted_school(world.page,forms.read_page(world.page),ctx,done,1,{},lambda _:None)
    assert world.page.locator('#unlisted').input_value()=='Example University'
    assert next(iter(done.values()))['source']=='applicant'


def test_application_adapter_accepts_boolean_and_multiple_choice_json_answers(world):
    from test_filling_strategy import SourceModel
    from jobagent.steps import apply as A
    world.setup('<label for=p>Do you prefer shift work?</label><select id=p required><option disabled selected>Choose</option><option>Yes</option><option>No</option></select>'
                '<fieldset><legend>Which technical topics do you prefer?</legend><label><input type=checkbox name=topics value=Backend>Backend</label><label><input type=checkbox name=topics value=Infrastructure>Infrastructure</label></fieldset><button>Apply</button>')
    class Human:
        calls=0
        def ask(self,prompt,**kwargs):
            self.calls+=1;qs=json.loads(prompt.split('\n',1)[1])
            return json.dumps({str(q['id']):True if q['kind']=='select' else ['Backend','Infrastructure'] for q in qs})
    human=Human();result=A.run(world.page,world.job,'fixture',model=SourceModel(),hitl=human,filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit'] and human.calls==1
    assert world.page.locator('#p').input_value()=='Yes'
    assert world.page.locator('input:checked').count()==2


def test_application_adapter_applies_a_new_rule_without_a_second_run(world):
    from test_filling_strategy import SourceModel
    from jobagent.steps import apply as A
    world.setup('<label for=x>What response should be entered?</label><input id=x required><button>Apply</button>')
    class Human:
        calls=0
        def ask(self,prompt,**kwargs):
            self.calls+=1;qs=json.loads(prompt.split('\n',1)[1]);return json.dumps({str(qs[0]['id']):'rule: Use 42.'})
    human=Human();result=A.run(world.page,world.job,'fixture',model=SourceModel(),hitl=human,filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit'] and human.calls==1
    assert world.page.locator('#x').input_value()=='42'


def test_reference_country_and_job_title_cannot_read_the_applicants_sources():
    data={'address':[{'question':'Current address','answer':{'country':'United States'}}],
          'experience':[{'question':'Work history','answer':[{'title':'Applicant title'}]}],
          'references':[{'question':'References','answer':[{'name':'Reference Owner','title':'Reference title','company':'Reference company','country':'Canada'}]}]}
    facts=compile_facts(data,'')
    country=forms.Field(id=0,kind='select',label='Country where you reside',section='Reference 1',options=['United States','Canada'])
    title=forms.Field(id=1,kind='text',label='Current job title',section='Reference 1')
    company=forms.Field(id=2,kind='text',label='Current employer',section='Reference 1')
    assert resolve_routed(country,facts).value=='Canada'
    assert resolve_routed(title,facts).value=='Reference title'
    assert resolve_routed(company,facts).value=='Reference company'


def test_current_residence_does_not_answer_historical_residency():
    facts=compile_facts({'address':[{'question':'Current address','answer':{'state':'Arizona'}}]},'')
    field=forms.Field(id=0,kind='radio',label='Have you ever been a resident of California?',options=['Yes','No'])
    assert resolve_routed(field,facts).value is None
    explicit=Fact('owner-history',[field.label],'Yes','applicant/history')
    assert resolve_routed(field,[*facts,explicit]).value=='Yes'


def test_posting_specific_owner_answer_wins_over_an_older_address():
    from jobagent.filling.routing import answer_sheet_facts
    field=forms.Field(id=0,kind='radio',label='Are you a resident of California?',options=['Yes','No'])
    facts=compile_facts({'address':[{'question':'Current address','answer':{'state':'Arizona'}}]},'')
    facts+=answer_sheet_facts('| 1 | Are you a resident of California? | Yes |')
    assert PlannedFiller(facts,no_questions).plan([field])[0].value=='Yes'


def test_long_institution_question_does_not_reuse_another_wizard_entity():
    field=forms.Field(id=0,kind='text',label='Which university did you attend?',identity='reused-node')
    calls=[]
    def ask(qs):
        calls.append(qs);return {'0':'First University' if len(calls)==1 else 'Second University'}
    filler=PlannedFiller([],ask)
    assert filler.plan([field])[0].value=='First University'
    filler.step=2
    assert filler.plan([field])[0].value=='Second University'
    assert len(calls)==2


def test_entity_literal_is_not_saved_as_an_unscoped_learned_contact(world):
    from test_filling_strategy import SourceModel
    from jobagent.steps import apply as A
    from jobagent.tools import learned
    world.setup('<label for=x>Institution</label><input id=x required><button>Apply</button>')
    class Human:
        def ask(self,prompt,**kwargs):
            q=json.loads(prompt.split('\n',1)[1])[0];return json.dumps({str(q['id']):'Example University'})
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),hitl=Human(),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit']
    assert not any(e['question']=='Institution' for e in learned.load())


def test_original_enforces_explicit_owner_availability_and_full_preferred_name():
    from jobagent.steps import apply as A
    data={'identity':[{'question':'Preferred name','answer':{'first':'Bhavya','last':'Shah','full':'Bhavya Shah'}}],
          'dates':[{'question':'Earliest start date','answer':'','instructions':"The coming Monday's date; in a list, 'immediately'."}]}
    fields=[forms.Field(id=0,kind='checkboxes',label='When are you available to start full-time?',options=['Q4 2026 (October - December)','Q2 2027 (April - June)']),
            forms.Field(id=1,kind='text',label='Preferred Name')]
    answers={0:A.Answer(id=0,value='Q2 2027 (April - June)',source='applicant'),1:A.Answer(id=1,value='Bhavya',source='applicant')}
    A.enforce(fields,answers,{'_applicant':data,'TODAY':'2026-11-15'},lambda _:None)
    assert answers[0].value=='Q4 2026 (October - December)'
    assert answers[1].value=='Bhavya Shah'
    answers[0]=A.Answer(id=0,value='Q2 2027 (April - June)',source='human')
    A.enforce(fields,answers,{'_applicant':data,'TODAY':'2026-11-15'},lambda _:None)
    assert answers[0].value=='Q2 2027 (April - June)'


def test_preferred_is_not_misclassified_as_a_referrer():
    from jobagent.filling.routing import contact_party,needs_entity_binding
    assert contact_party('Preferred contact method')=='applicant'
    assert not needs_entity_binding('Preferred contact method')
    assert contact_party('Referrer email')=='referrer'
    assert needs_entity_binding('Email','Reference 1')


def test_original_replaces_an_unverified_parser_website_with_owner_canonical_url():
    from jobagent.steps import apply as A
    data={'links':[{'question':'Portfolio / website','answer':'https://owner.example/profile'}]}
    field=forms.Field(id=0,kind='text',label='Other website',value='http://owner.example/profile')
    answers={0:A.Answer(id=0,value='',source='none')}
    A.enforce([field],answers,{'_applicant':data},lambda _:None)
    assert answers[0].value=='https://owner.example/profile'
    assert answers[0].source=='applicant' and not answers[0].ask_human
