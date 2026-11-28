"""Saved answers must identify the employer, not merely its current row number."""
import hashlib
import json
import pytest

from jobagent import progress
from jobagent.tools import saved_questions as SQ, presence
from jobagent.tools.hitl import CandidateAway, Hitl


def question(employer=None, title='Engineer'):
    q={'id':1,'label':'City','section':'Work Experience 2','kind':'text','required':True,
       'help':'','options':[],'format':''}
    if employer is not None:q['record_context']={'type':'employment','employer':employer,'title':title}
    return q


def parse(q):
    return SQ.parse('Fixture application needs these answers\n'+json.dumps([q]))


def test_same_row_different_employer_is_a_different_saved_question(tmp_path):
    old,new=parse(question('Anton')),parse(question('TGen'))
    SQ.record(tmp_path,old,{SQ.key_of(old[0]):'Tempe'})
    assert SQ.key_of(old[0])!=SQ.key_of(new[0])
    assert SQ.saved_answers(tmp_path,new)=={}
    assert SQ.saved_answers(tmp_path,parse({**question('Anton'),'id':99}))=={SQ.key_of(old[0]):'Tempe'}
    assert SQ.load(tmp_path)[0]['record_context']['employer']=='Anton'


@pytest.mark.parametrize('context',[None,{}, {'type':'employment','employer':'Anton'},
                                   {'type':'education','school':'Anton','degree':'MS'}])
def test_legacy_or_unbound_ordinal_answers_are_not_replayed(tmp_path,context):
    q=question();q['record_context']=context
    qs=parse(q);SQ.record(tmp_path,qs,{SQ.key_of(qs[0]):'Tempe'})
    assert SQ.saved_answers(tmp_path,qs)=={}


def test_global_question_hashes_are_backward_compatible():
    q={**question(),'section':'Contact'}
    original={k:(bool(q.get(k)) if k=='required' else q.get(k) or ([] if k=='options' else '')) for k in SQ.IDENTITY}
    expected=hashlib.sha256(json.dumps(original,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
    assert SQ.identity(q)==expected


def test_new_employer_row_is_not_silently_answered_while_away(tmp_path):
    job=tmp_path/'job';state=tmp_path/'state'
    old=parse(question('Anton'));SQ.record(job,old,{SQ.key_of(old[0]):'Tempe'})
    presence.set_available(False,state_dir=state)
    h=Hitl(state/'hitl',lambda text:pytest.fail('Away must defer, not wait'))
    prompt='Fixture application needs these answers\n'+json.dumps([question('TGen')])
    with progress.execution(new=True,job_dir=str(job)):
        with pytest.raises(CandidateAway):h.ask(prompt,timeout_s=.01)
    assert {q['record_context']['employer'] for q in SQ.load(job)}=={'Anton','TGen'}
