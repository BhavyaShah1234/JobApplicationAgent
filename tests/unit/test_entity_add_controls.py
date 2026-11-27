"""Entity Add actions use their own accessible group and never native submit controls."""
import pytest

from jobagent.filling.facts import Fact
from jobagent.filling.planned import PlannedFiller
from jobagent.tools import forms
from test_filling_strategy import world


def test_icon_prefixed_add_reads_its_named_group_without_a_heading_tag(world):
    world.setup('''<meta charset=utf-8><h1>Developer</h1><span id=work>Work Experience (Please enter your current or most recent employer first)</span>
      <div role=group aria-labelledby=work><div class=btnRowWrapper><div role=button tabindex=0 title="Add new row">
      <span role=presentation>\ue1f6</span> Add<span style="position:absolute;width:1px;overflow:hidden" aria-label="Work Experience"></span>
      </div></div></div><span id=ed>Education</span><div role=group aria-labelledby=ed><div role=button tabindex=0>
      <span role=presentation>\ue1f6</span> Add</div></div>''')
    world.page.goto('https://fixture.test/apply');view=forms.read_page(world.page)
    assert [(a['text'],a['section']) for a in view.adds]==[('Add','Work Experience'),('Add','Education')]


def test_an_add_caption_never_allows_a_native_submit_or_disabled_control(world):
    world.setup('''<h2>Education</h2><form><button>Add</button><button type=submit>Add Another</button>
      <button type=button disabled>Add</button><div role=button aria-disabled=true>Add</div></form>''')
    world.page.goto('https://fixture.test/apply')
    assert forms.read_page(world.page).adds==[]


def test_named_group_row_numbers_scope_native_and_nested_shadow_controls_to_their_own_record(world):
    world.setup('''<h1>Developer</h1><span id=work>Work Experience (Please enter your current or most recent employer first)</span>
      <div role=group aria-labelledby=work><div>
       <div><span id="11:_rowNum">Row number 1</span><div class=rcmSectionComponent id="11:_sectionComponent">
        <label>Employer<input></label><div id=dateHost></div></div></div>
       <div><span id="12:_rowNum">Row number 2</span><div class=rcmSectionComponent id="12:_sectionComponent">
        <label>Employer<input></label></div></div></div></div>
      <label>Outside field<input></label><script>document.getElementById('dateHost').attachShadow({mode:'open'}).
       innerHTML='<input aria-label="Start Date" placeholder="MM/DD/YYYY">';</script>''')
    world.page.goto('https://fixture.test/apply');view=forms.read_page(world.page)
    assert [f.section for f in view.fields if f.label=='Employer']==['Work Experience 1','Work Experience 2']
    assert next(f for f in view.fields if f.label=='Start Date').section=='Work Experience 1'
    assert next(f for f in view.fields if f.label=='Outside field').section=='Developer'


@pytest.mark.parametrize('caption,expected', [('Formal Education','Education'),('Employment History','Work Experience')])
def test_named_add_groups_use_the_same_entity_family_as_their_rows(world, caption, expected):
    world.setup(f'''<h1>Developer</h1><span id=title>{caption}</span><div role=group aria-labelledby=title>
      <div role=button tabindex=0 title="Add new row">Add</div></div>''')
    world.page.goto('https://fixture.test/apply')
    assert forms.read_page(world.page).adds[0]['section']==expected


def test_formal_education_keeps_masters_and_bachelors_with_their_own_school_and_dates(world):
    world.setup('''<h1>Developer</h1><span id=ed>Formal Education</span><form onsubmit="window.sent=true;return false">
      <div role=group aria-labelledby=ed>
      <div><span id="11:_rowNum">Row number 1</span><div class=rcmSectionComponent id="11:_sectionComponent">
        <label>School<input id=first></label><label>Degree<select id=firstDegree><option>Masters</option><option>Bachelors</option></select></label>
        <div id=firstDate></div></div></div>
      <div><span id="12:_rowNum">Row number 2</span><div class=rcmSectionComponent id="12:_sectionComponent">
        <label>School<input id=second></label><label>Degree<select id=secondDegree><option>Masters</option><option>Bachelors</option></select></label>
        <div id=secondDate></div></div></div></div><button>Apply</button></form>
      <script>window.sent=false;firstDate.attachShadow({mode:'open'}).innerHTML='<label>Start Date<input type=date></label>';
      secondDate.attachShadow({mode:'open'}).innerHTML='<label>Start Date<input type=date></label>';</script>''')
    world.page.goto('https://fixture.test/apply')
    facts=[]
    for index,(school,degree,date) in enumerate([('Graduate University','Masters','2024-08-01'),
                                                 ('Undergraduate Institute','Bachelors','2019-07-01')],1):
        for label,value in [('School',school),('Degree',degree),('Start Date',date)]:
            facts.append(Fact(f'{index}:{label}',[label],value,'profile',f'Education {index}'))
    facts.extend([Fact('old-degree',['Degree'],'Bachelors','learned:fixture:Degree'),
                  Fact('old-start',['Start Date'],'2019-07-01','learned:fixture:Start Date')])
    filler=PlannedFiller(facts,lambda _:pytest.fail('Both degrees already have source evidence'))
    result=filler.fill(world.page,max_rounds=3,advance_pages=False)
    assert result['state']=='page_complete'
    assert world.page.locator('#first').input_value()=='Graduate University'
    assert world.page.locator('#second').input_value()=='Undergraduate Institute'
    assert world.page.locator('#firstDegree').input_value()=='Masters'
    assert world.page.locator('#secondDegree').input_value()=='Bachelors'
    assert world.page.locator('#firstDate input').input_value()=='2024-08-01'
    assert world.page.locator('#secondDate input').input_value()=='2019-07-01'
    assert not world.page.evaluate('window.sent')
