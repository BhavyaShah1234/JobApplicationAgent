"""Experimental page planning inside the existing audited application workflow."""
import json
import hashlib
from pathlib import Path
from .facts import compile_facts,Decision,key
from .planned import PlannedFiller,read_view


def fill_page(page,ctx,files,model,log,record,page_no,env,known=None,errors=()):
    from jobagent.steps import apply as A
    from jobagent.tools import forms,application_address,learned
    documents=ctx.setdefault('_experimental_uploads',{}).setdefault(page_no,{})

    def upload_identity(view,field):
        node=forms.locate(view,field).evaluate('''e=>{
            const w=e.ownerDocument.defaultView;
            const r=w.__jobagentUploadNodes ||= {ids:new WeakMap(),next:0,
                epoch:crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`};
            if(!r.ids.has(e))r.ids.set(e,++r.next);
            return [e.id,e.getAttribute('name'),r.epoch,r.ids.get(e)];
        }''')
        frame=view.frames[field.frame]
        return (frame,'named',*node[:2]) if any(node[:2]) else (frame,'node',*node[2:])

    def observe_upload(view,field,role):
        ident=upload_identity(view,field)
        count=sum(upload_identity(view,g)==ident for g in view.fields if g.kind=='file')
        previous=documents.get(ident,{})
        documents[ident]={'role':role,'label':previous.get('label',field.label),
                          'ambiguous':previous.get('ambiguous',False) or count!=1 or
                          previous.get('role',role)!=role}

    def prepare(fields):
        matches=A.match_entries(fields,ctx.get('_jd',{}).get('company',''),ctx.get('_applicant') or {})
        if env.get('out_dir') and not ctx.get('_application_address') and any(
                e.get('question') in {'Current address','Current location (city)'} for _,e in matches.values()):
            application_address.prepare(ctx,env['out_dir'],model,log)
        return compile_facts(ctx.get('_applicant') or {},ctx.get('_jd_text',''))

    def sources(fields,decisions):
        if not fields:return {}
        matches=A.match_entries(fields,ctx.get('_jd',{}).get('company',''),ctx.get('_applicant') or {})
        eligible=[f for f in fields if f.id in matches and
                  (matches[f.id][1].get('instructions') or matches[f.id][1].get('answer') not in (None,''))]
        answers={f.id:A.Answer(id=f.id,value='',source='none',ask_human=True) for f in fields}
        if eligible:
            visible=read_view(page)
            proposed,_=A.plan(eligible,ctx,model,log,filled=[f for f in visible.fields if f.id not in {g.id for g in eligible}])
            A.enforce(eligible,proposed,ctx,log);A.check(eligible,proposed,ctx,model,log)
            A.apply_variants(eligible,proposed,ctx,log);A.enforce(eligible,proposed,ctx,log)
            A.resolve(eligible,proposed,ctx,model,{},log)
            answers.update(proposed)
        # Same saved-answer retrieval as the original method, without one-question-at-a-time HITL.
        A.consult_learned(fields,answers,ctx,model,log)
        updates={f.id:Decision(a.value,'validated '+a.source,reason=a.why)
                 for f in fields if (a:=answers.get(f.id)) and not a.ask_human and not a.lookup and a.value}
        for f in eligible:
            if f.id not in updates:updates[f.id]=Decision(None,reason='source check requires owner')
        return updates

    def ask(questions):
        hitl=env.get('hitl')
        if hitl is None:return {}
        prompt=(f"{env.get('company','')} application needs these answers. Reply with a JSON object mapping IDs to answers; "
                "use null to skip. For one question, plain text is also accepted.\n"+json.dumps(questions,ensure_ascii=False))
        reply=hitl.ask(prompt,timeout_s=env.get('ask_s',1800)).strip()
        if reply.lower()=='skip':return {}
        try:values=json.loads(reply)
        except (ValueError,TypeError):values={str(questions[0]['id']):reply} if len(questions)==1 else {}
        if not isinstance(values,dict):
            values={str(questions[0]['id']):str(values)} if len(questions)==1 else {}
        for q in questions:
            value=values.get(str(q['id']))
            if value is None:continue
            text=str(value)
            if A.RULE.match(text):
                learned.add(q['label'],q['options'],'',env.get('company',''),env.get('host',''),rule=A.RULE.sub('',text))
                values[str(q['id'])]=None  # a rule is not a literal answer; re-run uses it through the source resolver
            else:
                learned.add(q['label'],q['options'],text,env.get('company',''),env.get('host',''))
                values[str(q['id'])]=text
        return values

    filler=ctx.get('_experimental_filler')
    if filler is None:
        filler=PlannedFiller([],ask,model,
            {k:v for k,v in [('resume',ctx.get('RESUME','')),('jd',ctx.get('_jd_text','')),
                            ('company',ctx.get('COMPANY','')),('cover_letter',ctx.get('COVER_LETTER',''))] if v},
            {'company':ctx.get('_jd',{}).get('company',''),'contact':ctx.get('REFERRAL',{})},sources,prepare)
        ctx['_experimental_filler']=filler
    if filler.step!=page_no:
        filler.cache={};filler.step=page_no;filler.failures={}
    filler.upload_observer=observe_upload
    try:
        result=filler.fill(page,files,advance_pages=False)
    except Exception as exc:
        from jobagent.tools.challenge import Blocked
        if isinstance(exc,Blocked):raise
        for field in read_view(page).fields:
            row=A._blank_entry(field,page_no,f'experimental planner failed: {type(exc).__name__}')
            row.update(outcome='unplanned',verified=False,readback=field.value);record.append(row)
        raise A.PlannerFailed(f'experimental planner failed: {type(exc).__name__}') from exc
    view=read_view(page);history=result['records'];done={}
    if not view.fields and not record:
        result.update(state='blocked',blockers=['no application fields discovered'])
    for f in view.fields:
        entry=next((e for e in reversed(history) if e.get('frame')==f.frame and e['label']==f.label and e.get('slot')==f.slot),None)
        if entry is None:entry={}
        success=entry.get('outcome')=='verified'
        value=entry.get('answer','')
        known_blank=success and value=='' and not f.required
        row={'page':page_no,'section':f.section,'label':f.label,'kind':f.kind,'required':f.required,
             'options':f.options[:60],'before':entry.get('before',f.value),'answer':value,'why':'experimental source-backed plan',
             'source':entry.get('source','none'),'flag':'' if success else 'not verified by experimental filler' if f.required else '',
             'filled':files.get(value,'') if f.kind=='file' and success else entry.get('chosen','') if success else '',
             'error':entry.get('error',''),'answered':success,'frame':f.frame,'slot':f.slot,'clear':known_blank,
             'readback':f.value,'verified':success,'held_file_sha256':entry.get('held_file_sha256')}
        row['outcome']=A.outcome_of(row,f)
        record.append(row);done[A._key(f)]=row
    # A widget can disappear after dispatch, and its native required marker may
    # be absent even when the visible form requires a resume. Neither situation
    # makes a provided, recognised document optional to our verification.
    document_blockers=[]
    for ident,doc in documents.items():
        role=doc['role'];label=doc['label']
        matches=[done[A._key(f)] for f in view.fields if f.kind=='file' and upload_identity(view,f)==ident]
        row=matches[0] if len(matches)==1 else {}
        if not (not doc['ambiguous'] and row.get('verified') and row.get('answer')==role and
                row.get('held_file_sha256')==hashlib.sha256(Path(files[role]).read_bytes()).hexdigest()):
            document_blockers.append(f'{label}: provided {role} upload is not verified')
    if document_blockers:
        result.update(state='blocked',ready=False,blockers=result['blockers']+document_blockers)
    env['_experimental_blockers']=result['blockers'] if result['state']!='page_complete' else []
    audit=ctx.setdefault('_experimental_pages',[])
    audit.append({'page':page_no,**{k:v for k,v in result.items() if k not in ('records','events')},
                  'attempts':history,'events':result['events']})
    return view,done
