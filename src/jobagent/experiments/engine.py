"""Alternative form filler; no submission capability and no production monkey patches."""
from dataclasses import asdict
import json
import re
import base64
import hashlib
from pathlib import Path
import time
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage
from jobagent import llm
from jobagent.tools import forms, wizard
from .resolution import Decision, Fact, choice, key, norm, resolve, from_fact
from .discovery import Discovery


def read_view(page):
    policy=getattr(page.context,'jobagent_policy',None)
    if policy:policy(page)
    view=forms.read_page(page)
    for f in view.fields:
        loc=forms.locate(view,f)
        if f.kind=='file':
            labels=loc.evaluate('e=>[...(e.labels||[])].map(x=>x.innerText.trim()).filter(Boolean)')
            if len(labels)==1:f.label=labels[0]
        if not f.format:
            fmt=loc.get_attribute('placeholder') or ''
            if re.search(r'(?i)mm|yyyy|dd',fmt):f.format=fmt
    return view


class Mapping(BaseModel):
    field_id: int
    fact_id: str | None = None


class Mappings(BaseModel):
    mappings: list[Mapping] = Field(default_factory=list)


class Written(BaseModel):
    field_id: int
    value: str
    source: str
    quote: str


class Drafts(BaseModel):
    drafts: list[Written] = Field(default_factory=list)


class Supported(BaseModel):
    supported_ids: list[int] = Field(default_factory=list)


class Candidate:
    def __init__(self, facts, ask, model=None, narrative_sources=None, referral_context=None):
        self.referral_context=referral_context
        self.narrative_sources=narrative_sources or {}
        self.facts=facts;self.ask=ask;self.model=model;self.cache={};self.events=[];self.model_calls=0
        self.human_wait_s=0.;self.questions=0;self.batches=0;self.failures={};self.step=0;self.human_answers={}

    def plan(self, fields):
        answers={};missing=[]
        for f in fields:
            d=self.human_answers.get((self.step,key(f))) or self.cache.get(key(f)) or resolve(f,self.facts)
            if self.referral_context is not None and d.source!='human':
                from jobagent.tools import referrals
                policy=referrals.answer_for(f,self.referral_context.get('contact',{}),self.referral_context.get('company',''))
                if policy is not None:d=Decision(policy.get('value') or None,'saved referral policy',reason=policy.get('flag',''))
            answers[f.id]=d
            if d.value is None:missing.append(f)
        if missing and self.model:
            self.model_calls+=1
            mapped=llm.structured_invoke(self.model,Mappings,[SystemMessage(
                'Map each question to an existing fact ID only when it asks for that fact. '
                'Respect section/entity and all instructions. Never infer personal qualifications from a JD. '
                'Use null for conflicts, ambiguity, missing facts or a question requiring new written text.'),
                HumanMessage(json.dumps({'fields':[asdict(f) for f in missing],
                                         'facts':[asdict(x) for x in self.facts]}))])
            facts={x.id:x for x in self.facts}
            proposals=[]
            for m in mapped.mappings:
                f=next((f for f in missing if f.id==m.field_id),None);fact=facts.get(m.fact_id)
                if f and fact and (not fact.section or norm(fact.section)==norm(f.section)):
                    proposals.append((f,fact))
            if proposals:
                self.model_calls+=1
                checked=llm.structured_invoke(self.model,Supported,[SystemMessage(
                    'Independently validate question-to-fact mappings. Accept a field ID ONLY if that source fact answers '
                    'that question for the correct entity. A Yes/No for a different question is NOT evidence. '
                    'Reject unknown qualifications, conflicts, and ambiguous mappings.'),
                    HumanMessage(json.dumps([{'field':asdict(f),'fact':asdict(fact)} for f,fact in proposals]))])
                for f,fact in proposals:
                    if f.id in checked.supported_ids:answers[f.id]=from_fact(f,fact)
        essays=[f for f in missing if answers[f.id].value is None and f.kind=='textarea'
                and re.search(r'(?i)why|describe|tell us',f.label)
                and not re.search(r'(?i)convict|criminal|citizen|felony|misdemeanor|clearance|sponsor|dismiss|disab|medical|salary',f.label)]
        if essays and self.model and self.narrative_sources:
            self.model_calls+=1
            drafts=llm.structured_invoke(self.model,Drafts,[SystemMessage(
                'Write concise truthful application prose ONLY from supplied sources. Each draft must cite one source and '
                'an exact supporting quote. No new qualifications, employers, dates, achievements or numbers. '
                'Respect field maxlength. Omit a draft if sources do not support it.'),
                HumanMessage(json.dumps({'fields':[asdict(f) for f in essays],'sources':self.narrative_sources}))])
            eligible=[d for d in drafts.drafts if any(f.id==d.field_id and (not f.maxlength or len(d.value)<=f.maxlength) for f in essays)
                      and d.quote.strip() and d.quote in self.narrative_sources.get(d.source,'')]
            if eligible:
                self.model_calls+=1
                checked=llm.structured_invoke(self.model,Supported,[SystemMessage(
                    'Independently check every factual claim in each written answer against the sources and its question. '
                    'Accept an ID only if the whole answer is supported; a real quote alone is insufficient.'),
                    HumanMessage(json.dumps({'drafts':[d.model_dump() for d in eligible],'sources':self.narrative_sources}))])
                for d in eligible:
                    if d.field_id in checked.supported_ids:
                        answers[d.field_id]=Decision(d.value,'written:'+d.source+' quote:'+d.quote)
        unresolved=[f for f in missing if answers[f.id].value is None]
        if unresolved:
            self.events.append({'event':'human_questions','fields':[{'label':f.label,'section':f.section} for f in unresolved],'classification':'unclassified'})
            self.batches+=1;self.questions+=len(unresolved);started=time.monotonic()
            supplied=self.ask([{'id':f.id,'label':f.label,'section':f.section,'required':f.required,
                                'options':f.options,'format':f.format,'help':f.help} for f in unresolved])
            self.human_wait_s+=time.monotonic()-started
            for f in unresolved:
                value=supplied.get(str(f.id))
                if value is not None:
                    answers[f.id]=Decision(str(value),'human')
                    self.human_answers[(self.step,key(f))]=answers[f.id]
        return answers

    def fill(self,page,files=None,max_rounds=12):
        files=files or {};records=[];discovery=Discovery(page);started=time.monotonic()
        try:
            for round_no in range(max_rounds):
                view=read_view(page)
                # Add missing source-backed entity rows using the existing discovered controls.
                added=False
                for button in view.adds:
                    group=norm(button.get('section','')+' '+button.get('text',''))
                    prefix=next((x for x in ('education','work experience') if x in group),None)
                    if not prefix:continue
                    desired={norm(f.section) for f in self.facts if norm(f.section).startswith(prefix)}
                    present={norm(f.section) for f in view.fields if norm(f.section).startswith(prefix)}
                    if len(desired)>len(present):
                        view.frames[button['frame']].locator(f'[data-jf-add="{button["id"]}"]').click(timeout=3000)
                        page.wait_for_timeout(300);added=True;break
                if added:continue
                inventory=discovery.inventory()
                self.events.append({'event':'inventory','round':round_no,**inventory})
                if any(f.kind=='password' for f in view.fields):
                    return self.result('blocked',records,started,['authentication is outside the filling experiment'])
                if not view.coverage.complete:
                    return self.result('blocked',records,started,view.coverage.problems())
                if forms.human_check(page):
                    return self.result('challenge',records,started,['human challenge requires separate assistance'])
                todo=[f for f in view.fields if key(f) not in self.cache or
                      (self.cache[key(f)].value is not None and not forms.same_value(self.cache[key(f)].value,f.value,f.kind))]
                if todo:
                    forms.discover_options(page,view)
                    nonfiles=[f for f in todo if f.kind!='file']
                    decisions=self.plan(nonfiles)
                    for f in todo:
                        if f.kind=='file':
                            role='cover_letter' if 'cover' in f.label.lower() else 'resume' if any(x in f.label.lower() for x in ('resume','cv')) else None
                            decisions[f.id]=Decision(role,'document role')
                    # Parent controls first; always refresh the inventory after each mutation.
                    order=sorted(todo,key=lambda f:(0 if norm(f.label) in ('country','how did you hear about this position') else 1,f.id))
                    changed=False
                    for old in order:
                        fresh=read_view(page)
                        f=next((f for f in fresh.fields if key(f)==key(old)),None)
                        if f is None:continue
                        d=decisions[old.id]
                        if d.value is None:
                            if not f.required:self.cache[key(f)]=d
                            continue
                        try:
                            value=d.value
                            held_sha=None
                            if f.kind=='file':
                                if value not in files:raise ValueError('no role-specific file provided')
                            chosen=forms.fill_field(page,fresh,f,value,files,strict=f.kind in ('select','combobox'))
                            page.wait_for_timeout(100)
                            after=read_view(page)
                            actual=next((x for x in after.fields if key(x)==key(f)),None)
                            valid=actual is not None and forms.same_value(chosen,actual.value,f.kind)
                            if actual:
                                valid=valid and not forms.locate(after,actual).evaluate(
                                    'e=>e.getAttribute("aria-invalid")==="true" || (e.validity && !e.validity.valid)')
                            if valid and f.kind=='file':
                                encoded=forms.locate(after,actual).evaluate('''async e=>{const f=e.files?.[0];if(!f||f.size>5000000)return null;const b=new Uint8Array(await f.arrayBuffer());let s='';for(let i=0;i<b.length;i+=8192)s+=String.fromCharCode(...b.subarray(i,i+8192));return btoa(s)}''')
                                held_sha=hashlib.sha256(base64.b64decode(encoded)).hexdigest() if encoded else None
                                valid=bool(held_sha) and held_sha==hashlib.sha256(Path(files[value]).read_bytes()).hexdigest()
                            if not valid:raise ValueError('control not accepted or exact readback unavailable')
                            self.cache[key(f)]=d;changed=True
                            records.append({'label':f.label,'section':f.section,'answer':value,'readback':actual.value,
                                            'source':d.source,'fact_id':d.fact_id,'outcome':'verified','held_file_sha256':held_sha})
                        except Exception as e:
                            records.append({'label':f.label,'section':f.section,'outcome':'failed','error':str(e)[:200]})
                            self.failures[key(f)]=self.failures.get(key(f),0)+1
                            if self.failures[key(f)]>=2:return self.result('blocked',records,started,['control repair budget exhausted: '+f.label])
                    if changed:continue
                    remaining=[f.label for f in view.fields if f.required and
                               (key(f) not in self.cache or self.cache[key(f)].value is None)]
                    if remaining:return self.result('blocked',records,started,remaining)
                view=read_view(page)
                if view.errors:return self.result('blocked',records,started,view.errors)
                if not view.coverage.complete:return self.result('blocked',records,started,view.coverage.problems())
                # Potential required questions not yet explained cannot silently disappear from coverage.
                unknown=[x['label'] for x in inventory['fields'] if x.get('required') and not x.get('visible')
                         and not any(norm(x['label'])==norm(f.label) for f in view.fields)
                         and not x.get('condition')]
                if unknown:return self.result('blocked',records,started,['unresolved potential field: '+x for x in unknown])
                before=wizard.state_of(page,view)
                if forms.click_button(page,view,forms.NEXT):
                    progressed,_,_,why=wizard.wait_for_progress(page,before)
                    if progressed!='advanced':return self.result('blocked',records,started,['Next refused: '+str(why)])
                    self.cache={};self.step+=1;continue
                if not any(forms.SUBMIT.search(b) for b in view.buttons):
                    return self.result('blocked',records,started,['no review/final application control discovered'])
                return self.result('review',records,started,[])
            return self.result('blocked',records,started,['discovery/filling round budget exhausted'])
        finally:discovery.close()

    def result(self,state,records,started,blockers):
        return {'engine':'candidate','state':state,'ready':state=='review','records':records,'blockers':blockers,
                'elapsed_s':time.monotonic()-started,'human_wait_s':self.human_wait_s,
                'active_s':time.monotonic()-started-self.human_wait_s,'questions':self.questions,
                'question_batches':self.batches,'model_calls':self.model_calls,'events':self.events}
