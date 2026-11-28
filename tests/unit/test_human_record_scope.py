"""A numbered UI row is not the same employer or school on another page."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from jobagent.filling.planned import PlannedFiller
from jobagent.filling.adaptive import AdaptiveFiller
from jobagent.filling.facts import key
from jobagent.tools.forms import Field, PageView


@pytest.fixture(params=[PlannedFiller, AdaptiveFiller])
def filler(request, tmp_path):
    kwargs = {'checkpoint_path': tmp_path/'answers.json'} if request.param is AdaptiveFiller else {}
    return request.param([], lambda questions: {str(q['id']): 'Tempe' if q['id'] == 1 else 'Phoenix'
                        for q in questions}, prepare_sources=lambda fields: [], **kwargs)


def cities(section='Work Experience'):
    return [Field(1, 'text', 'City', section=section+' 1', identity='city1', frame_identity='profile'),
            Field(2, 'text', 'City', section=section+' 2', identity='city2', frame_identity='profile')]


@pytest.mark.parametrize('section', ['Work Experience', 'Education', 'References'])
def test_human_record_answers_survive_rereads_but_not_a_new_form_page(filler, section):
    fields = cities(section)
    filler.step = 1
    first = filler.plan(fields)
    assert [first[f.id].value for f in fields] == ['Tempe', 'Phoenix']
    filler.ask = lambda questions: pytest.fail('Same controls must retain their confirmed answers')
    assert [filler.plan(fields)[f.id].value for f in fields] == ['Tempe', 'Phoenix']
    filler.step = 2
    fresh = [replace(f, frame_identity='review') for f in fields]
    assert all(x.value is None for x in filler.plan(fresh, allow_human=False).values())


@pytest.mark.parametrize('cache_name', ['cache', 'prepared'])
def test_prepared_or_cached_human_answer_cannot_escape_its_record_page(filler, cache_name):
    fields = cities(); filler.step = 1
    first = filler.plan(fields)
    filler.step = 2
    fresh = [replace(f, frame_identity='review') for f in fields]
    target = key(fresh[0]) if cache_name == 'cache' else filler.question_key(fresh[0])
    getattr(filler, cache_name)[target] = first[1]
    assert filler.plan(fresh, allow_human=False)[1].value is None


def test_reordered_review_can_receive_new_employer_bound_answers(filler):
    fields = cities(); filler.step = 1
    filler.plan(fields)
    filler.step = 2
    fresh = [replace(f, frame_identity='review') for f in fields]
    # The review's first employer is the profile's second employer.
    filler.ask = lambda questions: {'1': 'Phoenix', '2': 'Tempe'}
    answers = filler.plan(fresh)
    assert [answers[f.id].value for f in fresh] == ['Phoenix', 'Tempe']


def test_global_preference_keeps_each_strategys_existing_reuse_policy(filler):
    field = Field(1, 'text', 'What is your favorite development editor?', identity='editor', frame_identity='profile')
    filler.ask = lambda questions: {'1': 'Vim'}
    filler.step = 1
    assert filler.plan([field])[1].value == 'Vim'
    filler.step = 2
    filler.ask = lambda questions: pytest.fail('A global preference is not a numbered record')
    decision=filler.plan([replace(field, frame_identity='review')],allow_human=False)[1]
    # Adaptive already binds all human literals to a live field; preserve that
    # stricter policy rather than weakening it to match the original planner.
    assert decision.value == (None if isinstance(filler,AdaptiveFiller) else 'Vim')


def test_human_questions_name_the_visible_employer_and_title(filler):
    fields=cities();filler.step=1
    anchors=[Field(3,'text','Employer',section='Work Experience 1',value='First Co'),
             Field(4,'text','Title',section='Work Experience 1',value='First Title'),
             Field(5,'text','Name of Employer',section='Work Experience 2',value='Second Co'),
             Field(6,'text','Your Job Title',section='Work Experience 2',value='Second Title')]
    filler._live=SimpleNamespace(view=PageView([*fields,*anchors],[],[],[],{}))
    seen=[]
    def answer(questions):
        seen.extend(questions)
        return {'1':'Tempe','2':'Phoenix'}
    filler.ask=answer;filler.plan(fields)
    assert [q.get('record_context') for q in seen]==[
        {'type':'employment','employer':'First Co','title':'First Title'},
        {'type':'employment','employer':'Second Co','title':'Second Title'}]


def test_changing_one_entity_invalidates_only_that_rows_human_evidence(filler):
    fields=cities();filler.step=1
    anchors=[Field(3,'text','Employer',section='Work Experience 1',value='First Co'),
             Field(4,'text','Title',section='Work Experience 1',value='First Title'),
             Field(5,'text','Employer',section='Work Experience 2',value='Second Co'),
             Field(6,'text','Title',section='Work Experience 2',value='Second Title')]
    view=PageView([*fields,*anchors],[],[],[],{})
    filler._live=SimpleNamespace(view=view);filler.observe_view(view)
    first=filler.plan(fields)
    filler.cache.update({key(f):first[f.id] for f in fields})
    changed=PageView([*fields,replace(anchors[0],value='Different Co'),
                     replace(anchors[1],value='Different Title'),*anchors[2:]],[],[],[],{})
    filler._live.view=changed;filler.observe_view(changed)
    assert key(fields[0]) not in filler.cache
    assert filler.cache[key(fields[1])].value=='Phoenix'
    again=filler.plan(fields,allow_human=False)
    assert again[1].value is None and again[2].value=='Phoenix'


def test_filling_an_initially_empty_employer_does_not_erase_the_same_rows_answer(filler):
    fields=cities();filler.step=1
    view=PageView(fields,[],[],[],{});filler._live=SimpleNamespace(view=view);filler.observe_view(view)
    first=filler.plan(fields);filler.cache.update({key(f):first[f.id] for f in fields})
    named=PageView([*fields,Field(3,'text','Employer',section='Work Experience 1',value='First Co'),
                  Field(4,'text','Title',section='Work Experience 1',value='First Title')],[],[],[],{})
    filler._live.view=named;filler.observe_view(named)
    assert filler.cache[key(fields[0])].value=='Tempe'
