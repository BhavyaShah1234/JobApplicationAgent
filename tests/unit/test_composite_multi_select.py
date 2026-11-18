"""The search input inside Select2 multiple is not its selected value or question."""
import pytest
from jobagent.tools import forms
from jobagent.filling.live_view import LiveView
from jobagent.filling.planned import read_view
from test_filling_strategy import world

HTML='''<style>.select2-hidden-accessible{position:absolute;width:1px;height:1px;clip:rect(0,0,0,0)}</style>
<h1>Application</h1><label id=question>Countries lived in for 12 consecutive months</label>
<label><select multiple aria-labelledby=question class=select2-hidden-accessible tabindex=-1><option value=IN selected>India</option><option value=CA>Canada</option></select>
<span class=select2-container><span class="select2-selection select2-selection--multiple" role=group aria-required=true aria-invalid=false>
<ul class=select2-selection__rendered><li class=select2-selection__choice title=India><span role=button>×</span>India</li>
<li><input class=select2-search__field role=textbox aria-autocomplete=list style="width:20px"></li></ul></span></span></label>'''


@pytest.mark.parametrize('invalid',[False,True])
def test_selected_countries_and_question_match_in_full_and_targeted_reads(world,invalid):
    world.setup(HTML);world.page.goto('https://fixture.test/apply')
    world.page.locator('[role=group]').evaluate('(e,v)=>e.setAttribute("aria-invalid",String(v))',invalid)
    live=LiveView(world.page,read_view);view=live.capture()
    assert len(view.fields)==1
    f=view.fields[0]
    assert f.kind=='combobox' and f.multiple and f.required
    assert f.label=='Countries lived in for 12 consecutive months'
    assert f.value=='India' and f.invalid==invalid
    assert live.refresh(f).value=='India' and live.refresh(f).invalid==invalid
    world.page.locator('select').evaluate("e=>e.options[1].selected=true")
    assert live.refresh(f).value=='India; Canada'
    snapshot=forms.read_page(world.page)
    assert snapshot.fields[0].label==f.label and snapshot.fields[0].value=='India; Canada'


def test_styled_checkbox_uses_its_associated_label_and_preserves_checked_value(world):
    world.setup('''<style>input{position:absolute;opacity:0;width:16px;height:16px}label{display:inline-block;position:relative;background:white;padding:5px}</style>
    <h1>Application</h1><input id=accept type=checkbox required><label for=accept>I agree to the supplied acknowledgement</label>
    <script>window.changes=0;accept.onchange=()=>changes++;</script>''')
    world.page.goto('https://fixture.test/apply');view=forms.read_page(world.page);f=view.fields[0]
    assert forms.fill_field(world.page,view,f,'checked',{})=='checked'
    assert world.page.locator('#accept').is_checked() and world.page.evaluate('changes')==1
    assert forms.fill_field(world.page,view,f,'checked',{})=='checked'
    assert world.page.evaluate('changes')==1
