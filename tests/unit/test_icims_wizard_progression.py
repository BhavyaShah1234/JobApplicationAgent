"""An explicit later confirmation permits intermediate saves, never the final submit."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import forms, wizard

URL='https://portal.example.test/jobs/42/role/candidate'


def document(current=1,caption=None):
    labels=['Candidate Profile','Job Specific Questions','Confirm Application']
    steps=''.join(f'<li id="Step_{i}" class="iCIMS_Steps_{"Current" if i==current else "NotCurrent"}">'
        f'Step {i} of 3. {label}({"Current" if i==current else "Incomplete"} Step)</li>'
        for i,label in enumerate(labels,1))
    caption=caption or ('Update Profile' if current==1 else 'Submit')
    field='<label>Email<input type=email name=email value=owner@example.test></label>' if current==1 else '<label>Qualified<select name=q><option>Yes</option><option>No</option></select></label>'
    return f'<h1>Application</h1><ol>{steps}</ol><h2>{labels[current-1]}</h2><form id=profileForm method=post action="{URL}">{field}<button id=action>{caption}</button></form>'


@pytest.fixture(scope='module')
def chrome():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        yield browser
        browser.close()


@pytest.mark.parametrize('case,expected',[
    ('profile',True),('new_profile',True),('questions',True),('final',False),('final_continue',False),
    ('no_tracker',False),('ambiguous_current',False),('inconsistent_count',False),
    ('missing_step',False),('no_later_confirmation',False),('different_job',False),
    ('external_action',False),('other_target',False),('button_action',False),
    ('button_method',False),('get',False),('application_caption',False),
])
def test_only_proven_intermediate_saves_are_clicked(chrome,case,expected):
    stage=2 if case=='questions' else 3 if case in ('final','final_continue') else 1
    html=document(stage, 'Submit Profile' if case=='new_profile' else 'Submit Application' if case=='application_caption' else 'Continue' if case=='final_continue' else None)
    if case=='no_tracker':html=html[html.index('<h2>'):]
    if case=='ambiguous_current':html=html.replace('iCIMS_Steps_NotCurrent','iCIMS_Steps_Current')
    if case=='inconsistent_count':html=html.replace('Step 2 of 3','Step 2 of 4')
    if case=='missing_step':html=html.replace('id="Step_2"','id="not_a_step"')
    if case=='no_later_confirmation':html=html.replace('Confirm Application','Email Preferences')
    if case=='different_job':html=html.replace('action="'+URL+'"','action="'+URL.replace('/42/','/99/')+'"')
    if case=='external_action':html=html.replace('action="'+URL+'"','action="https://other.example.test/jobs/42/candidate"')
    if case=='other_target':html=html.replace('id=profileForm','target=_blank id=profileForm')
    if case=='button_action':html=html.replace('id=action','id=action formaction="https://other.example.test/submit"')
    if case=='button_method':html=html.replace('id=action','id=action formmethod=get')
    if case=='get':html=html.replace('method=post','method=get')
    html+='<script>document.querySelector("form").onsubmit=e=>{e.preventDefault();window.sent=true}</script>'
    page=chrome.new_page()
    try:
        page.route('**/*',lambda r:r.fulfill(content_type='text/html',body=html))
        page.goto(URL)
        clicked=forms.advance(page,forms.read_page(page))
        assert bool(clicked) is expected
        assert page.evaluate('window.sent===true') is expected
    finally:page.close()


@pytest.mark.parametrize('native_filler',[False,True])
def test_embedded_three_step_flow_saves_profile_and_questions_but_stops_at_confirmation(chrome,native_filler):
    posts=[];page=chrome.new_page()
    def route(r):
        if r.request.url.endswith('/shell'):
            body='<iframe title="Application" style="width:900px;height:800px" src="'+URL+'"></iframe>'
        else:
            if r.request.method=='POST':posts.append(r.request.url)
            body=document(1+len(posts))
        r.fulfill(content_type='text/html',body=body)
    try:
        page.route('**/*',route);page.goto('https://portal.example.test/shell')
        frame=page.frames[1]
        if native_filler:
            from jobagent.filling.facts import Fact
            from jobagent.filling.planned import PlannedFiller
            facts=[Fact('email',['Email'],'owner@example.test','applicant'),Fact('qualified',['Qualified'],'Yes','applicant')]
            def ask(_):pytest.fail('Every fixture answer is supplied')
            result=PlannedFiller(facts,ask).fill(page)
            assert result['state']=='review'
        else:
            for current in (1,2):
                frame.get_by_role('heading',name=['Candidate Profile','Job Specific Questions'][current-1],exact=True).wait_for()
                assert wizard.icims_step(page)['current']==current
                assert forms.advance(page,forms.read_page(page))
        frame.get_by_role('heading',name='Confirm Application',exact=True).wait_for()
        assert wizard.icims_step(page)['current']==3
        assert forms.advance(page,forms.read_page(page)) is None
        assert len(posts)==2
    finally:page.close()
