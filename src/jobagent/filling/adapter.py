"""Experimental page planning inside the existing audited application workflow."""
import json
import hashlib
from pathlib import Path
from .facts import compile_facts,Decision,Fact,key,norm,entity_scope
from .planned import PlannedFiller,read_view


def _portal_education_facts(facts, view):
    """Apply an explicit one-school instruction while preserving original source IDs."""
    import re
    from dataclasses import replace
    from datetime import datetime

    if view is None:return facts
    instruction=re.compile(r'\b(?:only|just) (?:your |the )?(?:most recent|latest) (?:college|university|school|degree)\b')
    if not any(norm(b.get('section','')).startswith('education') and instruction.search(
            re.sub(r'\bnot (?:only|just)\b','',norm(b.get('help','')))) for b in view.adds):return facts
    present={entity_scope(f.section) for f in view.fields if (entity_scope(f.section) or ('',))[0]=='education'}
    if len(present)>1:
        raise ValueError('Portal requests only the most recent school but multiple education rows already exist; not filling extra rows')
    groups={entity_scope(f.section) for f in facts if (entity_scope(f.section) or ('',))[0]=='education'}
    if not groups:return facts
    ranks={}
    for group in groups:
        dates=set()
        for fact in facts:
            if entity_scope(fact.section)!=group or fact.record or not any(norm(a) in {
                    'end date','graduation date','graduation year','end year'} for a in fact.aliases):continue
            if norm(fact.value) in ('present','current','ongoing'):
                dates.add((9999,12));continue
            for fmt in ('%B %Y','%b %Y','%Y-%m','%m/%Y','%Y-%m-%d','%m/%d/%Y','%Y'):
                try:
                    date=datetime.strptime(fact.value,fmt);dates.add((date.year,None if fmt=='%Y' else date.month));break
                except ValueError:pass
        dates={d for d in dates if d[1] is not None or not any(y==d[0] and m is not None for y,m in dates)}
        if len(groups)>1 and len(dates)!=1:
            raise ValueError('Most recent education cannot be selected unambiguously from its source dates')
        ranks[group]=next(iter(dates)) if dates else (0,None)
    year=max(v[0] for v in ranks.values());latest=[g for g,v in ranks.items() if v[0]==year]
    if len(latest)>1:
        if any(ranks[g][1] is None for g in latest):
            raise ValueError('Most recent education is ambiguous within the same year')
        month=max(ranks[g][1] for g in latest);latest=[g for g in latest if ranks[g][1]==month]
    if len(latest)!=1:raise ValueError('Most recent education has tied source dates')
    chosen=latest[0]
    return [replace(f,section='Education 1') if entity_scope(f.section)==chosen else f
            for f in facts if (entity_scope(f.section) or ('',))[0]!='education' or entity_scope(f.section)==chosen]


def _saved_matches(fields, eligible, company):
    """Resolve literal saved questions before requesting semantic retrieval."""
    from jobagent.tools import learned
    from .routing import needs_entity_binding

    matches={};pending=[]
    for field in fields:
        question=norm(field.label)
        if question in matches:continue
        entries=[e for e in eligible if learned.reusable(e,field.label) and
                 (not needs_entity_binding(field.label,field.section,field.help,field.kind) or e.get('instructions'))]
        exact=[e for e in entries if question in [norm(t) for t in [e['question'],*(e.get('seen') or [])]]]
        matches[question]=exact[:3]
        if not exact and entries:pending.append((field,entries))
    if pending:
        texts=list(dict.fromkeys([learned._clean(f.label) for f,_ in pending]+
            [learned._clean(t) for _,entries in pending for e in entries for t in [e['question'],*(e.get('seen') or [])]]))
        vectors=learned._vectors(texts)
        vector_bank=dict(zip(texts,vectors)) if vectors else {}
        for field,entries in pending:
            matches[norm(field.label)]=learned.relevant(field.label,company,entries=[
                {**e,'company':'any' if e.get('company')=='any' else company} for e in entries],vector_bank=vector_bank)
    return matches


def fill_page(page,ctx,files,model,log,record,page_no,env,known=None,errors=()):
    from jobagent.steps import apply as A
    from jobagent.tools import forms,application_address,learned
    documents=ctx.setdefault('_experimental_uploads',{}).setdefault(page_no,{})

    def refresh_adaptive_sources():
        """Reload changed source files once per change, never silently switch jobs."""
        if ctx.get('_filling_strategy')!='adaptive':return
        from jobagent.config import get_settings
        from jobagent.clock import now
        from jobagent.tools.artifact_cache import digest
        settings=get_settings();job=Path(env['out_dir']).parent
        paths=[settings.data_dir/'applicant.yaml',settings.data_dir/'learned.yaml',
               settings.data_dir/'master_profile.yaml',job/'jd.json',job/'jd.txt',job/'resume.tex',
               job/'resume.pdf',job/'cover_letter.pdf',job/'application'/'answers.md',
               settings.state_dir/'companies'/f"{env.get('company','')}.md"]
        stamp=digest({'date':now().date().isoformat(),'files':{
            str(p):hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None for p in paths}})
        previous=ctx.get('_adaptive_source_stamp')
        if previous and stamp!=previous:
            fresh=A.context(job,env.get('company',''))
            original=ctx.get('_jd',{})
            if any(fresh.get('_jd',{}).get(k)!=original.get(k) for k in ('company','source_url','apply_url')):
                raise ValueError('Job identity changed during filling; restart this application')
            ctx.update(fresh)
            for name in ('_experimental_compiled_facts','_experimental_learned_entries',
                         '_experimental_learned_retrieval','_experimental_learned_facts'):
                ctx.pop(name,None)
            current=ctx.get('_experimental_filler')
            if current:
                current.narrative_sources.update({name:ctx.get(key,'') for name,key in
                    [('resume','RESUME'),('jd','_jd_text'),('company','COMPANY'),('cover_letter','COVER_LETTER')]})
        ctx['_adaptive_source_stamp']=stamp
        current=ctx.get('_experimental_filler')
        if current:current.source_version=stamp

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
        refresh_adaptive_sources()
        matches=A.match_entries(fields,ctx.get('_jd',{}).get('company',''),ctx.get('_applicant') or {})
        from .routing import family
        address_needed=any(e.get('question') in {'Current address','Current location (city)'} for _,e in matches.values()) or any(
            f.kind in ('text','combobox','select') and family(f.label)=='location' for f in fields)
        if env.get('out_dir') and not ctx.get('_application_address') and address_needed:
            application_address.prepare(ctx,env['out_dir'],model,log)
        if '_experimental_compiled_facts' not in ctx:
            ctx['_experimental_compiled_facts']=compile_facts(ctx.get('_applicant') or {},ctx.get('_jd_text',''),ctx.get('TODAY',''))
        from .routing import personal_facts
        facts=list(ctx['_experimental_compiled_facts'])+personal_facts(ctx)
        sheet=ctx.get('ANSWERS_FOR_THIS_APPLICATION','')
        from .routing import answer_sheet_facts
        facts+=answer_sheet_facts(sheet)
        if ctx.get('_application_address'):
            for fact in facts:
                if any('/'+q+'/answer' in fact.source for q in ('Current address','Current location (city)')):
                    fact.instructions=''  # the address policy has already resolved this application-local fact
        live=getattr(ctx.get('_experimental_filler'),'_live',None)
        if live and live.view:
            from .routing import unlisted_school
            for field in fields:
                conditional=unlisted_school(field,live.view,facts)
                if conditional is not None:
                    facts.append(Fact('condition:'+str(field.id),[field.label],conditional.value,conditional.source,field.section,
                        instructions='Leave blank' if conditional.value=='' else ''))
        from .education import selection_context
        return selection_context(_portal_education_facts(facts+list(ctx.get('_experimental_learned_facts',{}).values()),
                                 live.view if live else None),live.view if live else None)

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
        queries=[f for f in fields if not entity_scope(f.section) and norm(f.label) not in cache]
        cache.update(_saved_matches(queries,eligible,company))
        for f in fields:
            if entity_scope(f.section):continue  # old learned rows have no entity identifier
            for entry in cache[norm(f.label)]:
                value=entry.get('answer')
                if value is None and not entry.get('instructions'):continue
                value=('Yes' if value else 'No') if isinstance(value,bool) else '' if value is None else str(value)
                ident='learned:'+str(entry.get('company',''))+':'+entry['question']
                fact=Fact(ident,[entry['question'],*(entry.get('seen') or [])],value,ident,
                          instructions=entry.get('instructions',''))
                known[ident]=fact
        filler=ctx['_experimental_filler']
        filler.facts=[fact for fact in filler.facts if fact.id not in known]+list(known.values())
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
            text=('Yes' if value else 'No') if isinstance(value,bool) else '; '.join(map(str,value)) if isinstance(value,list) else str(value)
            if A.RULE.match(text):
                rule=A.RULE.sub('',text)
                learned.add(q['label'],q['options'],'',env.get('company',''),env.get('host',''),rule=rule)
                values[str(q['id'])]=Fact('human-rule:'+str(page_no)+':'+str(q['id']),[q['label']],'','human',
                    instructions=rule,reference_date=ctx.get('TODAY',''))
            else:
                from .routing import needs_entity_binding
                if not needs_entity_binding(q['label'],q.get('section',''),q.get('help',''),q.get('kind','')):
                    learned.add(q['label'],q['options'],text,env.get('company',''),env.get('host',''))
                values[str(q['id'])]=text
            ctx.pop('_experimental_learned_entries',None)
            ctx.pop('_experimental_learned_retrieval',None)
        return values

    filler=ctx.get('_experimental_filler')
    if filler is None:
        filler_class=PlannedFiller
        strategy_options={}
        if ctx.get('_filling_strategy')=='adaptive':
            from .adaptive import AdaptiveFiller
            from jobagent.tools import job_context
            filler_class=AdaptiveFiller
            shared=job_context.snapshot(env['out_dir'].parent, company=ctx.get('COMPANY',''),
                candidate=ctx.get('_applicant',{}))
            strategy_options={'checkpoint_path':Path(env['out_dir'])/'adaptive-v1.json',
                              'source_version':shared['fingerprint']}
        filler=filler_class([],ask,model,
            {k:v for k,v in [('resume',ctx.get('RESUME','')),('jd',ctx.get('_jd_text','')),
                            ('company',ctx.get('COMPANY','')),('cover_letter',ctx.get('COVER_LETTER','')),
                            ('today',ctx.get('TODAY','')),('date_facts',ctx.get('DATE_FACTS',''))] if v},
            {'company':ctx.get('_jd',{}).get('company',''),'contact':ctx.get('REFERRAL',{})},sources,prepare,**strategy_options)
        ctx['_experimental_filler']=filler
    if filler.step!=page_no:
        filler.cache={};filler.step=page_no;filler.failures={}
    filler.log=log
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
        success=entry.get('outcome')=='verified' and not f.invalid and (proof.verify() if proof else forms.same_value(entry.get('readback',''),f.value,f.kind,f.label))
        value=entry.get('answer','')
        known_blank=success and value=='' and not f.required
        row={'page':page_no,'section':f.section,'label':f.label,'kind':f.kind,'required':f.required,
             'identity':f.identity,'field_key':key(f),'fact_id':entry.get('fact_id',''),
             'options':f.options[:60],'before':entry.get('before',f.value),'answer':value,'why':'experimental source-backed plan',
             'source':entry.get('source','none'),'flag':'' if success else 'not verified by experimental filler' if f.required else '',
             'filled':files.get(value,'') if f.kind=='file' and success else entry.get('chosen','') if success else '',
             'error':entry.get('error',''),'answered':success,'frame':f.frame,'slot':f.slot,'clear':known_blank,
             'readback':f.value,'verified':success,'held_file_sha256':entry.get('held_file_sha256'),
             'upload_verification':entry.get('upload_verification'),
             'replaced_attachment_count':entry.get('replaced_attachment_count',0)}
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
