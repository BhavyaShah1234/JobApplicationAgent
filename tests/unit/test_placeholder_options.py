"""A real SuccessFactors placeholder must never become a negative answer."""
import pytest

from jobagent.filling.facts import choice
from jobagent.tools import forms
from test_filling_strategy import world


@pytest.mark.parametrize('kind', ['combobox', 'select', 'radio', 'buttons'])
def test_no_selection_does_not_verify_a_no_answer(kind):
    assert not forms.same_value('No', 'No Selection', kind)
    assert forms.match_option('No', ['No Selection'])[0] is None


@pytest.mark.parametrize('kind', ['combobox', 'select', 'radio', 'buttons'])
def test_an_explicit_blank_never_verifies_an_existing_nonempty_choice(kind):
    assert not forms.same_value('', 'Information Technology', kind, 'Type of Business')
    assert not forms.same_value(' ', 'Other', kind, 'Type of Business')
    assert forms.same_value('', '', kind, 'Type of Business')


@pytest.mark.parametrize('complete', [False, True])
def test_placeholder_only_options_keep_a_search_or_require_an_answer(complete):
    field = forms.Field(0, 'combobox', 'Disability Status', options=['No Selection'],
                        options_state='complete' if complete else 'partial')
    assert choice(field, ['No']) == (None if complete else 'No')


def test_negative_declaration_ignores_placeholder_without_hiding_real_no_choices():
    offered = ['No Selection', 'I do not wish to answer', 'I have a disability (or previously had a disability)',
               'I do not have a disability']
    field = forms.Field(0, 'combobox', 'Disability Status', options=offered, options_state='complete')
    assert choice(field, ['No']) == offered[-1]
    assert forms.match_option('No', ['No Selection', 'Yes', 'No, I do not have a disability'])[0] == 'No, I do not have a disability'
    assert forms.match_option('No', ['No preference'])[0] == 'No preference'
    assert forms.match_option('None', ['None', 'No Selection'])[0] == 'None'


def test_native_filling_selects_negative_declaration_instead_of_clearing_it(world):
    world.setup('''<h1>Application</h1><form onsubmit="window.sent=true;return false">
      <label for=disability>Disability Status</label>
      <input id=disability readonly role=combobox aria-controls=choices placeholder="No Selection">
      <div id=choices role=listbox hidden>
        <div role=option data-value="">No Selection</div>
        <div role=option>Yes, I have a disability</div>
        <div role=option>I do not wish to answer</div>
        <div role=option>I do not have a disability</div>
      </div><button type=submit>Apply</button></form>
      <script>window.sent=false;disability.onclick=()=>choices.hidden=false;
      document.querySelectorAll('[role=option]').forEach(o=>o.onclick=()=>{
        disability.value=o.dataset.value??o.textContent;choices.hidden=true;
      });document.addEventListener('keydown',e=>{if(e.key==='Escape')choices.hidden=true});</script>''')
    world.page.goto('https://fixture.test/apply')
    view = forms.read_page(world.page)
    field = view.fields[0]
    forms.discover_options(world.page, view)
    assert 'No Selection' not in field.options
    value = choice(field, ['No'])
    assert value == 'I do not have a disability'
    forms.fill_field(world.page, view, field, value, {}, strict=True)
    assert forms.read_page(world.page).fields[0].value == value
    assert not world.page.evaluate('window.sent')
