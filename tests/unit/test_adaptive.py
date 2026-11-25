"""Decision survival must never imply stale evidence or live completion."""
from dataclasses import replace
import json
from types import SimpleNamespace

from jobagent.filling.adaptive import AdaptiveFiller
from jobagent.filling.facts import Decision, Fact, key
from jobagent.tools.forms import Field, PageView


def world(tmp_path, fields=None, facts=None):
    fields = fields or [Field(1, 'text', 'Email', identity='old', frame_identity='epoch1')]
    view = PageView(fields, [], [], [], {}, [])
    filler = AdaptiveFiller(facts or [Fact('email', ['Email'], 'one@example.test', 'owner')], lambda q: {},
                            checkpoint_path=tmp_path/'decisions.json')
    filler.step = 1
    filler._live = SimpleNamespace(view=view)
    return filler, view


def accept(filler, view, field=None, decision=None):
    field = field or view.fields[0]
    decision = decision or Decision('one@example.test', 'owner', 'email')
    filler.cache[key(field)] = decision
    filler.accepted(view, field, decision, {})
    return decision


def test_resume_rebinds_unique_semantics_but_does_not_claim_live_readback(tmp_path):
    filler, view = world(tmp_path)
    accept(filler, view)
    other = replace(view.fields[0], identity='rerendered', frame_identity='epoch2', value='')
    resumed, fresh = world(tmp_path, [other])
    resumed.observe_view(fresh)
    assert resumed.cache[key(other)].value == 'one@example.test'
    assert resumed.record_state == {} and resumed.rendered == {}
    assert (tmp_path/'decisions.json').stat().st_mode & 0o777 == 0o600


def test_new_human_answer_survives_its_own_source_refresh(tmp_path):
    field=Field(1,'select','Expected salary range?',options=['70-79k'],identity='salary',frame_identity='epoch')
    filler,view=world(tmp_path,[field])
    fact=Fact('human:salary',[field.label],'70-79k','human',target_key=key(field))
    filler.session_facts[fact.id]=fact
    decision=Decision(fact.value,'human',fact.id)
    filler.human_answers[(filler.step,key(field))]=decision
    accept(filler,view,field,decision)
    fingerprint=filler.evidence(decision)
    filler.facts=[*filler.facts,fact]  # the next normal prepared-facts refresh
    filler.source_version='own-learned-answer-was-saved'
    filler.observe_view(view)
    assert filler.evidence(decision)==fingerprint
    assert filler.cache[key(field)].value=='70-79k'
    assert filler.human_answers[(filler.step,key(field))].value=='70-79k'


def test_changed_fact_invalidates_only_its_dependents(tmp_path):
    a = Field(1,'text','Email',identity='email')
    b = Field(2,'text','Phone',identity='phone')
    facts = [Fact('email',['Email'],'one@example.test','owner'),Fact('phone',['Phone'],'1234567890','owner')]
    filler, view = world(tmp_path,[a,b],facts)
    accept(filler,view,a)
    accept(filler,view,b,Decision('1234567890','owner','phone'))
    facts[0].value = 'new@example.test'
    filler.observe_view(view)
    assert key(a) not in filler.cache and key(b) in filler.cache


def test_options_help_and_required_changes_cannot_reuse_old_decision(tmp_path):
    for name, value in [('help','Includes OPT documentation'),('options',['Yes','No','Some']),('required',True)]:
        field = Field(1,'select','Support',options=['Yes','No'])
        filler, view = world(tmp_path,[field])
        decision = accept(filler,view,field,Decision('No','owner'))
        changed = replace(field, **{name:value})
        assert not filler.can_execute(field,changed,decision)


def test_identical_questions_and_repeated_rows_are_not_restored_by_position(tmp_path):
    filler, view = world(tmp_path)
    accept(filler,view)
    resumed, fresh = world(tmp_path,[replace(view.fields[0],identity='a'),replace(view.fields[0],identity='b')])
    resumed.observe_view(fresh)
    assert resumed.cache == {}
    row = replace(view.fields[0],section='Work Experience 1')
    assert filler.semantic_id(row) is None


def test_invalid_control_does_not_reset_two_attempt_repair_budget(tmp_path):
    filler, view = world(tmp_path)
    accept(filler,view)
    f = view.fields[0]; f.invalid = True
    filler.failures[key(f)] = 2
    filler.observe_view(view); filler.observe_view(view)
    assert filler.failures[key(f)] == 2 and key(f) not in filler.cache


def test_unopened_partial_menu_preserves_repair_scope_but_new_choices_reset_it(tmp_path):
    field=Field(1,'combobox','Race',options=['Asian: East Asian','Asian: South Asian'],
                options_state='partial',identity='race',frame_identity='epoch1')
    filler,view=world(tmp_path,[field])
    filler.observe_view(view)
    filler.bindings[key(field)]={'schema':filler.signature(field),'evidence':filler.evidence(Decision('Asian'))}
    filler.failures[key(field)]=2
    spec=filler.signature(field)
    view.fields=[replace(field,options=[])]
    filler.observe_view(view)
    assert filler.signature(view.fields[0])==spec and filler.failures[key(field)]==2
    view.fields=[replace(field,options=['Asian'])]
    filler.observe_view(view)
    assert filler.signature(view.fields[0])!=spec and key(field) not in filler.failures


def test_a_transient_readback_error_is_not_an_unavailable_search_choice(tmp_path):
    from jobagent.tools import forms
    field=Field(1,'combobox','School',options=['Initial search suggestion'],options_state='partial')
    filler,_=world(tmp_path,[field]);decision=Decision('The actual school','owner','school')
    filler.control_failed(field,decision,ValueError('control not accepted or exact readback unavailable'))
    filler.control_failed(field,decision,forms.FillError('temporary widget failure'))
    assert filler.unavailable_choices=={}
    filler.control_failed(field,decision,forms.UnavailableOption('no exact offered match'))
    assert key(field) in filler.unavailable_choices


def test_menu_placeholder_disappearance_is_not_a_new_instruction(tmp_path):
    f=Field(1,'combobox','School',help='Select...',options=['Other'],options_state='partial')
    filler,_=world(tmp_path,[f]);before=filler.signature(f)
    assert filler.signature(replace(f,help=''))==before
    assert filler.signature(replace(f,help='Select an option'))==before
    assert filler.signature(replace(f,help='Select only your most recent school'))!=before


def test_corrupt_checkpoint_is_a_miss(tmp_path):
    (tmp_path/'decisions.json').write_text('{broken')
    filler, view = world(tmp_path)
    filler.observe_view(view)
    assert filler.cache == {}


def test_document_quote_dependency_is_not_lost_when_a_fact_is_also_bound(tmp_path):
    filler,view=world(tmp_path)
    filler.narrative_sources={'resume':'Built an API.'}
    accept(filler,view,decision=Decision('Built an API.','document:resume quote:Built an API.','email'))
    filler.narrative_sources['resume']='Built a compiler.'
    filler.observe_view(view)
    assert not filler.cache


def test_checkpoint_from_another_implementation_is_not_reused(tmp_path):
    filler,view=world(tmp_path)
    accept(filler,view)
    path=tmp_path/'decisions.json';saved=json.loads(path.read_text());saved['implementation']='old version'
    path.write_text(json.dumps(saved))
    resumed,fresh=world(tmp_path);resumed.observe_view(fresh)
    assert resumed.cache=={}


def test_only_explicitly_inapplicable_schema_branches_are_excluded(tmp_path):
    from jobagent.filling.discovery import extract
    fields=extract([{'label':'Not for this role','type':'text','required':True,'applicable':False},
                    {'label':'Unknown applicability','type':'text','required':True},
                    {'label':'Applicable','type':'text','required':True,'applicable':True}],'test')
    filler,_=world(tmp_path)
    inventory=filler.applicable_inventory({'fields':fields})
    assert [f['label'] for f in inventory['fields']]==['Unknown applicability','Applicable']


def test_codes_and_upload_proofs_are_never_checkpointed_as_answers(tmp_path):
    filler,view=world(tmp_path)
    assert filler.semantic_id(Field(1,'text','Email verification code')) is None
    assert filler.semantic_id(Field(1,'password','Password')) is None
    assert filler.semantic_id(Field(1,'file','Resume')) is None
    for field in (Field(1,'text','Code'),Field(1,'text','PIN'),Field(1,'text','OTP'),
                  Field(1,'text','Enter value',help='Security token from email')):
        assert filler.semantic_id(field) is None
    code=Field(1,'text','Code')
    filler,view=world(tmp_path,[code])
    accept(filler,view,code,Decision('123456','human'))
    assert '123456' not in (tmp_path/'decisions.json').read_text()


def test_changed_help_cannot_reuse_a_literal_human_reply_with_the_same_choices(tmp_path):
    field=Field(1,'select','Do you need sponsorship?',help='Visa sponsorship only',options=['Yes','No'],required=True,identity='support')
    filler,view=world(tmp_path,[field]);responses=iter(['No','Yes'])
    filler.ask=lambda items:{str(item['id']):next(responses) for item in items}
    assert filler.plan([field])[1].value=='No'
    changed=replace(field,help='Includes OPT and CPT documentation')
    view.fields=[changed]
    assert filler.plan([changed])[1].value=='Yes'
    assert filler.questions==2


def test_changed_question_scope_reopens_a_previously_declined_question(tmp_path):
    field=Field(1,'select','Which support option applies?',options=['Yes','No'],identity='support')
    filler,view=world(tmp_path,[field]);filler.ask=lambda _:{}
    assert filler.plan([field])[1].value is None
    assert (filler.step,key(field)) in filler.declined
    changed=replace(field,required=True,help='New scope and required')
    view.fields=[changed];filler.observe_view(view)
    filler.ask=lambda _:{'1':'Yes'}
    assert filler.plan([changed])[1].value=='Yes'


def test_same_label_human_replies_keep_their_own_value_and_scope_on_later_rounds(tmp_path):
    fields=[Field(1,'select','Do you need sponsorship?',help='Visa only',options=['Yes','No'],identity='visa'),
            Field(2,'select','Do you need sponsorship?',help='Includes OPT documentation',options=['Yes','No'],identity='opt')]
    filler,view=world(tmp_path,fields)
    filler.ask=lambda _: {'1':'No','2':'Yes'}
    first=filler.plan(fields)
    assert first[1].fact_id!=first[2].fact_id
    assert [filler.session_facts[first[n].fact_id].value for n in (1,2)]==['No','Yes']
    filler.ask=lambda _: (_ for _ in ()).throw(AssertionError('unchanged scoped answers must survive'))
    again=filler.plan(fields)
    assert [again[n].value for n in (1,2)]==['No','Yes']


def test_resume_uses_portal_context_instead_of_local_page_counter(tmp_path):
    filler, view = world(tmp_path)
    view.headings = ['Software Engineer', 'Application Questions 2 of 2']
    filler.step = 1
    accept(filler, view)
    resumed, fresh = world(tmp_path, [replace(view.fields[0], identity='new', frame_identity='new-session')])
    resumed.step = 4
    fresh.headings = list(view.headings)
    resumed.observe_view(fresh)
    assert resumed.restored == 1
    assert resumed.cache[key(fresh.fields[0])].value == 'one@example.test'
    assert resumed.record_state == {}, 'A restored proposal is not live verification'


def test_same_question_in_different_portal_step_is_not_restored(tmp_path):
    filler, view = world(tmp_path)
    view.headings = ['Current employment']
    accept(filler, view)
    resumed, fresh = world(tmp_path)
    fresh.headings = ['Previous employment']
    resumed.observe_view(fresh)
    assert not resumed.cache
