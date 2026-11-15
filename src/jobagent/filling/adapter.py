"""Experimental page planning inside the existing audited application workflow."""
import json
import hashlib
from pathlib import Path
from .facts import compile_facts,Decision,Fact,key,norm,entity_scope
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
                          'field_key':key(field), 'identity':field.identity,
                          'ambiguous':previous.get('ambiguous',False) or count!=1 or
                          previous.get('role',role)!=role}

    def prepare(fields):
        matches=A.match_entries(fields,ctx.get('_jd',{}).get('company',''),ctx.get('_applicant') or {})
        if env.get('out_dir') and not ctx.get('_application_address') and any(
                e.get('question') in {'Current address','Current location (city)'} for _,e in matches.values()):
            application_address.prepare(ctx,env['out_dir'],model,log)
        facts=compile_facts(ctx.get('_applicant') or {},ctx.get('_jd_text',''))
        if ctx.get('_application_address'):
            for fact in facts:
                if any('/'+q+'/answer' in fact.source for q in ('Current address','Current location (city)')):
                    fact.instructions=''  # the address policy has already resolved this application-local fact
        return facts+list(ctx.get('_experimental_learned_facts',{}).values())

    def sources(fields,decisions):
        if not fields:return {}
        # Retrieve evidence, not free-form answers from a second planner. A
        # company-specific literal must never become another company's policy.
        company=norm(ctx.get('_jd',{}).get('company',''))
        company_keys={company.replace(' ',''),norm(env.get('company','')).replace(' ','')}
        company_keys.discard('')
        if '_experimental_learned_entries' not in ctx:ctx['_experimental_learned_entries']=learned.load()
        stored=ctx['_experimental_learned_entries']
        eligible=[e for e in stored if norm(e.get('company','')).replace(' ','') in company_keys|{'any'}]
        cache=ctx.setdefault('_experimental_learned_retrieval',{})
        known=ctx.setdefault('_experimental_learned_facts',{})
        for f in fields:
            if entity_scope(f.section):continue  # old learned rows have no entity identifier
            if norm(f.label) not in cache:
                exact=[e for e in eligible if norm(f.label) in [norm(x) for x in [e['question'],*(e.get('seen') or [])]]]
                cache[norm(f.label)]=exact[:3] or learned.relevant(f.label,company,entries=[
                    {**e,'company':'any' if e.get('company')=='any' else company} for e in eligible])
            for entry in cache[norm(f.label)]:
                value=entry.get('answer')
                if value is None and not entry.get('instructions'):continue
                value=('Yes' if value else 'No') if isinstance(value,bool) else '' if value is None else str(value)
                ident='learned:'+str(entry.get('company',''))+':'+entry['question']
                fact=Fact(ident,[entry['question'],*(entry.get('seen') or [])],value,ident,
                          instructions=entry.get('instructions',''))
                known[ident]=fact
        filler=ctx['_experimental_filler']
        existing={fact.id for fact in filler.facts}
        filler.facts.extend(fact for ident,fact in known.items() if ident not in existing)
        return {}

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
    from jobagent.tools import lookup
    filler.lookup_resolver=lookup.run
    try:
        result=filler.fill(page,files,advance_pages=False,errors=errors)
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
        entry=next((e for e in reversed(history) if e.get('step')==page_no and
                    e.get('field_key')==key(f)),None)
        if entry is None:entry={}
        proof=filler.upload_proofs.get((page_no,key(f))) if f.kind=='file' else None
        success=entry.get('outcome')=='verified' and not f.invalid and (proof.verify() if proof else forms.same_value(entry.get('readback',''),f.value,f.kind))
        value=entry.get('answer','')
        known_blank=success and value=='' and not f.required
        row={'page':page_no,'section':f.section,'label':f.label,'kind':f.kind,'required':f.required,
             'identity':f.identity,'field_key':key(f),'fact_id':entry.get('fact_id',''),
             'options':f.options[:60],'before':entry.get('before',f.value),'answer':value,'why':'experimental source-backed plan',
             'source':entry.get('source','none'),'flag':'' if success else 'not verified by experimental filler' if f.required else '',
             'filled':files.get(value,'') if f.kind=='file' and success else entry.get('chosen','') if success else '',
             'error':entry.get('error',''),'answered':success,'frame':f.frame,'slot':f.slot,'clear':known_blank,
             'readback':f.value,'verified':success,'held_file_sha256':entry.get('held_file_sha256'),
             'upload_verification':entry.get('upload_verification')}
        row['outcome']=A.outcome_of(row,f)
        record.append(row);done[key(f)]=row
    # A widget can disappear after dispatch, and its native required marker may
    # be absent even when the visible form requires a resume. Neither situation
    # makes a provided, recognised document optional to our verification.
    document_blockers=[]
    for ident,doc in documents.items():
        role=doc['role'];label=doc['label']
        matches=[done[key(f)] for f in view.fields if f.kind=='file' and f.identity==doc['identity']]
        row=matches[0] if len(matches)==1 else {}
        proof=filler.upload_proofs.get((page_no,doc['field_key']))
        if not matches and not doc['ambiguous'] and proof and proof.verify():
            entry=next((e for e in reversed(history) if e.get('step')==page_no and e.get('identity')==doc['identity'] and e.get('outcome')=='verified'),{})
            if entry:
                row={**entry,'page':page_no,'verified':True,'filled':files[role],'answered':True,
                     'options':[],'why':'matched dispatched bytes and acknowledgement in the same widget','source':'document role','flag':'','error':''}
                record.append(row)
        if not (not doc['ambiguous'] and row.get('verified') and row.get('answer')==role and
                proof and proof.verify() and
                row.get('held_file_sha256')==hashlib.sha256(Path(files[role]).read_bytes()).hexdigest()):
            document_blockers.append(f'{label}: provided {role} upload is not verified')
    if document_blockers:
        result.update(state='blocked',ready=False,blockers=result['blockers']+document_blockers)
    env['_experimental_blockers']=result['blockers'] if result['state']!='page_complete' else []
    audit=ctx.setdefault('_experimental_pages',[])
    audit.append({'page':page_no,**{k:v for k,v in result.items() if k not in ('records','events')},
                  'attempts':history,'events':result['events']})
    return view,done
