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


@pytest.mark.parametrize('question', [
    'Do you meet all Basic Qualifications for this role as specified in the job description?',
    'Please confirm you satisfy the minimum requirements for this position.',
    'Do you fulfill the eligibility criteria?',
])
def test_another_postings_aggregate_qualification_answer_is_not_a_reusable_fact(monkeypatch, question):
    monkeypatch.setattr(learned, '_vectors', lambda _: pytest.fail('Discarded attestations need no embeddings'))
    field = forms.Field(id=0, kind='select', label=question, options=['Yes', 'No'],
                        help='This posting requires ten years in a different specialist field.')
    prior = {'question': question, 'answer': 'Yes', 'company': 'fixture'}
    assert _saved_matches([field], [prior], 'fixture')[norm(question)] == []
    assert learned.relevant(question, 'fixture', entries=[prior]) == []


def test_qualification_literal_is_not_stored_as_a_company_wide_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(learned, 'path', lambda: tmp_path / 'learned.yaml')
    question = 'Do you meet the basic qualifications for this role?'
    result = learned.add(question, ['Yes', 'No'], 'Yes', 'fixture', 'portal.test')
    assert result['answer'] == 'Yes' and result['scope'] == 'current_application'
    assert result['stored'] is False and learned.load() == []


def test_a_qualification_comparison_rule_remains_reusable(monkeypatch, tmp_path):
    monkeypatch.setattr(learned, 'path', lambda: tmp_path / 'learned.yaml')
    question = 'Do you meet all the requirements for this role?'
    learned.add(question, [], '', 'fixture', 'portal.test', rule='Compare each current JD requirement to my saved facts; ask if unknown.')
    entry = learned.load()[0]
    monkeypatch.setattr(learned, '_vectors', lambda _: pytest.fail('An exact rule needs no embeddings'))
    field = forms.Field(id=0, kind='select', label=question)
    assert _saved_matches([field], [entry], 'fixture')[norm(question)] == [entry]


def test_the_candidates_answer_sheet_still_answers_this_postings_qualification_question():
    from jobagent.filling.routing import answer_sheet_facts
    from jobagent.filling.facts import resolve
    question = 'Do you meet all Basic Qualifications for this role?'
    field = forms.Field(id=0, kind='select', label=question, options=['Yes', 'No'])
    facts = answer_sheet_facts('| # | Question | Answer |\n| 1 | '+question+' | Yes |')
    decision = resolve(field, facts)
    assert decision.value == 'Yes'
    assert decision.source == 'applicant/application/answers.md'
