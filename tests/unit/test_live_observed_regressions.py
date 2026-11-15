from types import SimpleNamespace
import json
import pytest
from jobagent.filling.facts import compile_facts,resolve,Fact,from_fact
from jobagent.filling.planned import PlannedFiller,Mappings,Mapping,Supported
from jobagent.tools import forms
from jobagent.steps import apply as A
from test_filling_strategy import world,SourceModel,CONTACT


@pytest.mark.parametrize('now,future,expected',[(False,False,'No'),(False,True,'Yes'),(True,False,'Yes'),(True,True,'Yes')])
def test_combined_sponsorship_uses_both_timeframes(now,future,expected):
    facts=compile_facts({'Work':[{'question':'Will you now require sponsorship?','answer':now},
                               {'question':'Will you in the future require sponsorship for employment visa status?','answer':future}]},'')
    field=forms.Field(id=0,kind='buttons',label='Will you now or in the future require sponsorship for employment visa status in this country?',options=['Yes','No'])
    result=resolve(field,facts)
    assert result.value==expected and result.fact_id.count(',')==1


def test_current_no_cannot_answer_combined_sponsorship_without_future_source():
    facts=compile_facts({'Work':[{'question':'Will you now require sponsorship?','answer':False}]},'')
    field=forms.Field(id=0,kind='buttons',label='Will you now or in the future require sponsorship?',options=['Yes','No'])
    assert resolve(field,facts).value is None


def test_sponsorship_and_question_is_not_reinterpreted_as_or():
    facts=compile_facts({'Work':[{'question':'Will you now require sponsorship?','answer':False},
                               {'question':'Will you in the future require sponsorship?','answer':True}]},'')
    field=forms.Field(id=0,kind='buttons',label='Will you require sponsorship now and in the future?',options=['Yes','No'])
    assert resolve(field,facts).value is None


def test_original_enforcer_corrects_a_combined_sponsorship_misreading():
    data={'Work':[{'question':'Will you now require sponsorship?','answer':False},
                  {'question':'Will you in the future require sponsorship?','answer':True}]}
    field=forms.Field(id=0,kind='buttons',label='Will you now or in the future require sponsorship?',options=['Yes','No'])
    answers={0:A.Answer(id=0,value='No',source='applicant')}
    A.enforce([field],answers,{'_applicant':data,'_jd':{}},lambda _:None)
    assert answers[0].value=='Yes' and answers[0].source=='applicant'


def test_relative_date_rule_distinguishes_calendar_from_list():
    facts=compile_facts({'Preferences':[{'question':'Earliest start date','answer':'',
        'instructions':"The coming Monday's date; in a list, 'immediately'."}]},'',reference_date='2026-11-14')
    f=forms.Field(id=0,kind='text',label='Earliest start date',calendar=True,format='YYYY-MM-DD')
    assert resolve(f,facts).value=='2026-11-16'
    f.kind='select';f.calendar=False;f.format='';f.options=['Immediately','Two weeks']
    assert resolve(f,facts).value=='Immediately'


def test_model_cannot_replace_a_computed_calendar_date_with_list_fallback():
    facts=compile_facts({'Preferences':[{'question':'Earliest start date','answer':'',
        'instructions':"The coming Monday's date; in a list, 'immediately'."}]},'',reference_date='2026-11-14')
    class Model(SourceModel):
        def with_structured_output(self,schema,**kwargs):
            return SimpleNamespace(invoke=lambda _:Mappings(mappings=[Mapping(field_id=0,fact_id=facts[0].id,value='immediately')])
                if schema is Mappings else Supported(supported_ids=[0]))
    filler=PlannedFiller(facts,lambda _:pytest.fail('The date is supplied by the rule and clock'),Model())
    field=forms.Field(id=0,kind='text',label='When can you start a new role?',calendar=True,format='YYYY-MM-DD')
    assert filler.plan([field])[0].value=='2026-11-16'


def test_place_rendering_preserves_city_state_and_country():
    assert forms.strict_place('Sunnyvale, California',['Sunnyvale, CA, USA'])=='Sunnyvale, CA, USA'
    assert forms.strict_place('Sunnyvale, California',['Sunnyvale, TX, USA']) is None
    assert forms.strict_place('Sunnyvale, California',['Sunnyvale, CA, Canada']) is None
    assert forms.strict_place('Sunnyvale',['Sunnyvale, CA, USA','Sunnyvale, TX, USA']) is None


def test_veteran_rendering_does_not_choose_decline():
    f=forms.Field(id=0,kind='radio',label='Veteran Status',options=['I am not a protected veteran','I decline to self-identify for protected veteran status'])
    assert from_fact(f,Fact('v',['Veteran Status'],'Not a veteran','applicant')).value=='I am not a protected veteran'


def test_custom_calendar_requires_a_date_and_closes_after_exact_selection(world):
    world.setup('<div class=react-datepicker-wrapper><label for=x>When can you start?</label>'
        '<input id=x placeholder="Pick date..." onclick="document.getElementById(\'cal\').style.display=\'block\'"></div>'
        '<div id=cal class=react-datepicker style="display:none"><div>November 2026</div>'
        '<div role=option aria-label="Choose Monday, November 16th, 2026" onclick="document.getElementById(\'x\').value=\'11/16/2026\';this.parentElement.style.display=\'none\'">16</div></div>')
    world.page.goto('https://fixture.test/apply');view=forms.read_page(world.page);f=view.fields[0]
    assert f.calendar and f.format=='YYYY-MM-DD'
    with pytest.raises(forms.FillError):forms.fill_field(world.page,view,f,'immediately',{})
    assert world.page.locator('#x').input_value()==''
    assert forms.fill_field(world.page,view,f,'2026-11-16',{})=='11/16/2026'
    assert not world.page.locator('#cal').is_visible()


def test_candidate_input_class_is_not_a_date_widget(world):
    world.setup('<label for=x>Full name</label><input id=x class=candidate-input>')
    world.page.goto('https://fixture.test/apply');field=forms.read_page(world.page).fields[0]
    assert not field.calendar and field.format==''


def test_required_markers_are_bound_to_owned_label(world):
    world.setup('<style>.required:after{content:"*"}</style><div data-field-path=a>'
                '<label class=required for=a>Location</label><input id=a role=combobox></div>'
                '<div data-field-path=b><label for=b>Optional note</label><input id=b></div>')
    world.page.goto('https://fixture.test/apply');fields=forms.read_page(world.page).fields
    assert fields[0].required and not fields[1].required


def test_required_pseudo_marker_on_radio_question_header(world):
    world.setup('<style>.required:after{content:"*"}</style><div data-field-path=domain>'
        '<label class=required for=domain>Preferred domain</label><div id=domain>'
        '<label><input name=d type=radio value=Backend>Backend</label><label><input name=d type=radio value=Frontend>Frontend</label></div></div>')
    world.page.goto('https://fixture.test/apply');fields=forms.read_page(world.page).fields
    assert len(fields)==1 and fields[0].required


def test_checker_receives_unselected_facts_and_temporal_context(world):
    seen=[]
    facts=[Fact('nickname',['Nickname'],'CJ','applicant/name',instructions='Use lowercase'),Fact('future',['Future sponsorship'],'Yes','applicant/future')]
    class Model(SourceModel):
        def with_structured_output(self,schema,**kwargs):
            def invoke(msgs):
                if schema is Mappings:return Mappings(mappings=[Mapping(field_id=0,fact_id='nickname',value='cj')])
                data=json.loads(msgs[-1].content);seen.append(data)
                assert {x['id'] for x in data['all_facts']}=={'nickname','future'}
                assert data['sources']['today']=='2026-11-14'
                return Supported(supported_ids=[0])
            return SimpleNamespace(invoke=invoke)
    filler=PlannedFiller(facts,lambda _: {},Model(),{'today':'2026-11-14'})
    assert filler.plan([forms.Field(id=0,kind='text',label='What should we call you?')])[0].value=='cj'
    assert len(seen)==1


def test_application_answer_sheet_is_available_as_owner_evidence(world,monkeypatch):
    context=A.context
    def with_sheet(*args):
        ctx=context(*args);ctx['ANSWERS_FOR_THIS_APPLICATION']='Favourite editor: Vim';return ctx
    monkeypatch.setattr(A,'context',with_sheet)
    world.setup('<label for=x>Favourite editor</label><input id=x required><button>Apply</button>')
    class Model(SourceModel):
        def with_structured_output(self,schema,**kwargs):
            return SimpleNamespace(invoke=lambda _:Mappings(mappings=[Mapping(field_id=0,fact_id='application.answers',value='Vim')])
                if schema is Mappings else Supported(supported_ids=[0]))
    result=A.run(world.page,world.job,'fixture',model=Model(),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit'] and world.page.locator('#x').input_value()=='Vim'
