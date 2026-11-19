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
