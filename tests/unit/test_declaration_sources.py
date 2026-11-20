"""Owner declarations must not reuse contact details or unrelated history answers."""
import pytest
from jobagent.tools import forms
from jobagent.filling.facts import Fact,Decision
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.routing import route,resolve_routed,binding_valid


@pytest.mark.parametrize('adaptive',[False,True])
def test_enterprise_environment_experience_is_not_previous_employment_at_this_company(adaptive):
    from jobagent.filling.adaptive import AdaptiveFiller
    field=forms.Field(id=0,kind='radio',required=True,options=['Yes','No'],
                      label='Have you worked in an enterprise software environment before?')
    previous=Fact('previous-employer',['Have you worked for this company before?'],'No','applicant/employer_history')
    assert route(field,[previous]).family=='enterprise_software_experience'
    assert resolve_routed(field,[previous]).value is None
    assert not binding_valid(field,[previous],'No',route(field,[previous]))
    asked=[]
    def ask(questions):
        asked.append(questions);return {}
    filler=(AdaptiveFiller if adaptive else PlannedFiller)([previous],ask,
        source_resolver=lambda fields,answers:{0:Decision('No',previous.source,previous.id)})
    assert filler.plan([field])[0].value is None and len(asked)==1
    owner=Fact('enterprise',['Enterprise software environment'],'Yes','applicant/experience')
    assert resolve_routed(field,[previous,owner]).value=='Yes'


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



@pytest.mark.parametrize('adaptive', [False, True])
def test_technical_skills_use_owner_technical_confirmation_not_spoken_language(adaptive):
    from jobagent.filling.adaptive import AdaptiveFiller
    question = 'Do you have professional experience working with the specific language or tech stack for this role (e.g., Python, Java, React)?'
    field = forms.Field(0, 'radio', question, required=True, options=['Yes', 'No'])
    english = Fact('spoken', ['Languages'], 'English', 'applicant/experience/Languages/answer/0/language')
    owner = Fact('technical', ['Do you possess the technical skills required for this job?'], 'Yes', 'learned:any:owner-technical-confirmation')
    assert route(field, [english]).family == 'technical_skill_possession'
    assert resolve_routed(field, [english]).value is None
    assert not binding_valid(field, [english], 'Yes', route(field, [english]))
    assert resolve_routed(field, [english, owner]).fact_id == owner.id
    filler = (AdaptiveFiller if adaptive else PlannedFiller)([english, owner], lambda _: pytest.fail('Owner confirmed'))
    decision = filler.plan([field])[0]
    assert decision.value == 'Yes' and decision.fact_id == owner.id


def test_technical_skill_confirmation_does_not_establish_years_or_credentials():
    owner = Fact('technical', ['Do you possess the technical skills required for this job?'], 'Yes', 'owner')
    for label in ['Do you have 5 years of professional experience with this tech stack?',
                  'Do you have the required degree and technical skills?',
                  'Do you have the technical certification required for this job?']:
        f = forms.Field(0, 'radio', label, required=True, options=['Yes', 'No'])
        decision = resolve_routed(f, [owner])
        assert decision is None or decision.value is None
