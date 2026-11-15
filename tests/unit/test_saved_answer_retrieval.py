"""Known saved answers must work without initializing an embedding client."""
import pytest

from jobagent.filling.adapter import _saved_matches
from jobagent.filling.facts import norm
from jobagent.tools import forms,learned


@pytest.mark.parametrize('value',['Email',0,False,''])
def test_exact_saved_answers_need_no_embedding_provider(monkeypatch,value):
    monkeypatch.setattr(learned,'_vectors',lambda _:pytest.fail('An exact saved answer must not call embeddings'))
    field=forms.Field(id=0,kind='text',label='Saved response')
    entry={'question':'Saved response','answer':value,'company':'Fixture'}
    assert _saved_matches([field],[entry],'fixture')=={norm(field.label):[entry]}


def test_saved_question_alias_needs_no_embedding_provider(monkeypatch):
    monkeypatch.setattr(learned,'_vectors',lambda _:pytest.fail('A saved alias must not call embeddings'))
    field=forms.Field(id=0,kind='text',label='Favourite editor')
    entry={'question':'Preferred editor','seen':['Favourite editor'],'answer':'Vim','company':'Fixture'}
    assert _saved_matches([field],[entry],'fixture')[norm(field.label)]==[entry]


def test_saved_entity_answer_still_requires_its_binding(monkeypatch):
    monkeypatch.setattr(learned,'_vectors',lambda _:pytest.fail('No eligible saved answer needs embeddings'))
    field=forms.Field(id=0,kind='text',label='School')
    entry={'question':'School','answer':'Example University','company':'Fixture'}
    assert _saved_matches([field],[entry],'fixture')[norm(field.label)]==[]


def test_unmatched_wording_retains_batched_retrieval(monkeypatch):
    calls=[]
    monkeypatch.setattr(learned,'_vectors',lambda texts:calls.append(texts))
    field=forms.Field(id=0,kind='text',label='Preferred method of contact')
    entry={'question':'Preferred contact method','answer':'Email','company':'Fixture'}
    assert _saved_matches([field],[entry],'fixture')[norm(field.label)]==[{**entry,'company':'fixture'}]
    assert entry['company']=='Fixture'
    assert len(calls)==1
