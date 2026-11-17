"""Entity Add actions use their own accessible group and never native submit controls."""
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
