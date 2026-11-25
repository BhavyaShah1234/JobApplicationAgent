"""Experimental page planning inside the existing audited application workflow."""
import json
import hashlib
from pathlib import Path
from .facts import compile_facts,Decision,Fact,key,norm,entity_scope
from .planned import PlannedFiller,read_view


def _split_name_for_middle_field(facts, fields, *, split_first_name=False):
    """Preserve recorded name components unless a known portal decomposes them.

    Args:
        facts: Original owner facts, including legal-name components.
        fields: The current form's controls.
        split_first_name: Explicitly enable the observed Dayforce decomposition;
            merely having a middle-name field does not establish this behavior.

    Returns:
        The unchanged facts by default, or the source-preserving decomposition.
    """
    import re
    from dataclasses import replace
    from .facts import Fact,entity_scope
    if not split_first_name:return facts
    if not any(re.fullmatch(r'(?i)\s*middle\s*(?:name|initial)?\s*\*?\s*',f.label or '') and not entity_scope(f.section) for f in fields):
        return facts
    first=[x for x in facts if x.authority=='owner' and not x.record and not x.section and x.id.endswith('/Legal name/answer/first')]
    middle=[x for x in facts if x.id.endswith('/Legal name/answer/middle') and (x.value or '').strip()]
    if len(first)!=1 or middle:return facts
    words=first[0].value.split()
    if len(words)<2:return facts
    out=[replace(first[0],value=words[0],source=first[0].source+'; first word of the legal first name (the form has a Middle Name field)') if x is first[0] else x
         for x in facts if not (x.id.endswith('/Legal name/answer/middle') and not (x.value or '').strip())]     # the empty placeholder gives way to the real value
    out.append(Fact(first[0].id+'.rest',['Middle Name','Middle Initial'],' '.join(words[1:]),first[0].source+'; rest of the legal first name (the form has a Middle Name field)',
                    authority='owner'))
    return out


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


MAX_INTERSTITIAL_STREAK=3     # the same kind of field-less page this many times in a row means the click is not moving it on


def _parsed_upload_entry(filler, page_no, view, field, history):
    """Carry an upload record across only its proven parser document replacement."""
    from jobagent.tools import forms

    element=forms.locate(view,field)
    proofs={}
    for (step,field_key),proof in filler.upload_proofs.items():
        if step==page_no and proof.can_rebind(element):
            proofs.setdefault(id(proof),(proof,set()))[1].add(field_key)
    if len(proofs)!=1:return {},None
    proof,keys=next(iter(proofs.values()))
    entry=next((row for row in reversed(history) if row.get('step')==page_no and
                row.get('field_key') in keys and row.get('kind')=='file' and
                row.get('outcome')=='verified' and row.get('held_file_sha256')==proof.expected),{})
    return (entry,proof) if entry else ({},None)


def _save_planner_failure(exc,out_dir,page_no,log):
    """Keep what the planner crashed on: the message and traceback go to the job folder (a bare class name such as RuntimeError diagnoses nothing)."""
    import traceback
    text=''.join(traceback.format_exception(type(exc),exc,exc.__traceback__))
    try:
        log(f'planner crashed on page {page_no}: {type(exc).__name__}: {str(exc)[:300]}')
        if out_dir:(Path(out_dir)/f'planner-failure-page{page_no}.txt').write_text(text[-6000:])
    except Exception:pass


def fieldless_page(view,env,page_no,log):
    """What to do with a page that has no application fields.

    A page without fields is not necessarily the end of the road: portals show progress trackers, optional notification opt-ins and
    "before you start" pages between the questions. Move on when the page offers a safe button (Next, Continue, Skip and continue, Not now,
    Get started; never an opt-in or a Submit); stop when it does not, or when such pages keep coming back because the click did not move it.

    Args:
        view: The page as read now (no fields).
        env: The run's mutable state (`_interstitial_streak` counts consecutive field-less pages).
        page_no: The page number, for the log line.
        log: The run's logger.

    Returns:
        The result update: `{'state': 'page_complete', ...}` to move on, or a `blocked` state with the reason.
    """
    from jobagent.tools import forms
    step=forms.interstitial_button(view);streak=env.get('_interstitial_streak',0)
    if step and streak<MAX_INTERSTITIAL_STREAK:
        env['_interstitial_streak']=streak+1
        log(f"apply: page {page_no} has no fields ({'; '.join(view.headings[:3]) or 'no heading'}): moving on with {step!r}")
        return {'state':'page_complete','blockers':[],'interstitial':step}
    if step:
        return {'state':'blocked','blockers':[f'stuck on an intermediate page without fields: {step!r} did not move it on']}
    return {'state':'blocked','blockers':['no application fields discovered']}


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
               settings.data_dir/'master_profile.yaml',settings.data_dir/'asu_contacts.csv',
               settings.data_dir/'company_aliases.yaml',job/'jd.json',job/'jd.txt',job/'resume.tex',
               job/'resume.pdf',job/'cover_letter.pdf',job/'application'/'answers.md',
               settings.state_dir/'companies'/f"{env.get('company','')}.md"]
        file_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None for p in paths}
        today=now().date().isoformat()
        stamp=digest({'date':today,'files':file_hashes})
        previous=ctx.get('_adaptive_source_stamp')
        if previous and stamp!=previous:
            old_files=ctx.get('_adaptive_file_hashes',{})
            learned_path=str(settings.data_dir/'learned.yaml')
            core_changed=any(value!=old_files.get(name) for name,value in file_hashes.items() if name!=learned_path)
            core_changed=core_changed or today!=ctx.get('_adaptive_source_date')
            current=ctx.get('_experimental_filler')
            if core_changed:
                if current and any(step<page_no for step,_ in current.record_state):
                    raise ValueError('Source inputs changed after an earlier page was filled; restart to reconcile every page')
                fresh=A.context(job,env.get('company',''))
                original=ctx.get('_jd',{})
                if any(fresh.get('_jd',{}).get(k)!=original.get(k) for k in ('company','source_url','apply_url')):
                    raise ValueError('Job identity changed during filling; restart this application')
                ctx.update(fresh)
                if ctx.get('_application_address'):
                    ctx.pop('_personal_address',None)
                    application_address.prepare(ctx,env['out_dir'],model,log)
            for name in ('_experimental_compiled_facts','_experimental_learned_entries',
                         '_experimental_learned_retrieval','_experimental_learned_facts'):
                ctx.pop(name,None)
            if current:
                external=core_changed
                external=external or (file_hashes.get(learned_path)!=old_files.get(learned_path) and
                    file_hashes.get(learned_path)!=ctx.get('_adaptive_own_learned_sha'))
                if external:
                    # A newer owner source must not be silently overridden by
                    # an earlier in-memory human literal. Independent compiled
                    # facts remain cached and are invalidated by their own hash.
                    current.session_facts.clear();current.session_fact_steps.clear();current.human_answers.clear()
                    for field_key,decision in list(current.cache.items()):
                        if decision.source=='human' or decision.fact_id.startswith('human:'):
                            current.cache.pop(field_key,None)
                current.narrative_sources.update({name:ctx.get(key,'') for name,key in
                    [('resume','RESUME'),('jd','_jd_text'),('company','COMPANY'),('cover_letter','COVER_LETTER'),
                     ('today','TODAY'),('date_facts','DATE_FACTS')]})
                current.referral_context = {'company':ctx.get('_jd',{}).get('company',''),
                                            'contact':ctx.get('REFERRAL',{})}
        ctx['_adaptive_source_stamp']=stamp
        ctx['_adaptive_source_date']=today
        ctx['_adaptive_file_hashes']=file_hashes
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
        from .routing import family,field_family
        address_needed=any(e.get('question') in {'Current address','Current location (city)'} for _,e in matches.values()) or any(
            f.kind in ('text','combobox','select') and field_family(f)=='location' for f in fields)
        if env.get('out_dir') and not ctx.get('_application_address') and address_needed and not ctx.get('_application_address_unavailable'):
            try:application_address.prepare(ctx,env['out_dir'],model,log)
            except application_address.NoUsLocation as exc:
                # A region or remote posting: go on, so the fields that do not need an address are still filled; the ones that do
                # fall back to the recorded facts or a question. Said once, with the real reason.
                ctx['_application_address_unavailable']=str(exc)
                log(f"address: not prepared ({exc}); the fields that need it use the recorded facts or are asked")
        if '_experimental_compiled_facts' not in ctx:
            ctx['_experimental_compiled_facts']=compile_facts(ctx.get('_applicant') or {},ctx.get('_jd_text',''),ctx.get('TODAY',''))
        from .routing import personal_facts,review_metadata_facts
        live=getattr(ctx.get('_experimental_filler'),'_live',None)
        facts=list(ctx['_experimental_compiled_facts'])+personal_facts(ctx)+review_metadata_facts(live.view if live else None)
        sheet=ctx.get('ANSWERS_FOR_THIS_APPLICATION','')
        from .routing import answer_sheet_facts
        facts+=answer_sheet_facts(sheet)
        from .agreements import project
        facts=project(fields,facts,learned.load())
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
        from .employment import recent_employment_facts
        view = live.view if live else None
        from urllib.parse import urlsplit
        dayforce=bool(view and any((urlsplit(frame.url).hostname or '').lower().endswith(
            ('.dayforcehcm.com','.dayforce.com')) for frame in view.frames.values()))
        facts=_split_name_for_middle_field(facts,fields,split_first_name=dayforce)
        facts = facts+list(ctx.get('_experimental_learned_facts',{}).values())
        return selection_context(_portal_education_facts(recent_employment_facts(facts, view), view), view)

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
                          instructions=entry.get('instructions',''),policy_context=entry.get('policy_context',''))
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
                    from jobagent.tools import referrals
                    import re
                    company=ctx.get('_jd',{}).get('company','') or env.get('company','')
                    label=re.sub(re.escape(company),'company',q['label'],flags=re.I) if company else q['label']
                    contact=ctx.get('REFERRAL') or {}
                    confirmation = ({'policy_context': referrals.SOURCE_POLICY} if referrals.SOURCE.search(label)
                                    and text in q['options'] and (not referrals._referral_option(q['options']) or
                                    not (contact.get('name') and contact.get('email'))) else {})
                    learned.add(q['label'],q['options'],text,env.get('company',''),env.get('host',''),**confirmation)
                values[str(q['id'])]=text
            ctx.pop('_experimental_learned_entries',None)
            ctx.pop('_experimental_learned_retrieval',None)
        if ctx.get('_filling_strategy')=='adaptive' and learned.path().is_file():
            ctx['_adaptive_own_learned_sha']=hashlib.sha256(learned.path().read_bytes()).hexdigest()
        return values

    filler=ctx.get('_experimental_filler')
    if filler is None:
        filler_class=PlannedFiller
        strategy_options={}
        if ctx.get('_filling_strategy')=='adaptive':
            from .adaptive import AdaptiveFiller
            from jobagent.tools import job_context
            filler_class=AdaptiveFiller
            shared=job_context.snapshot(Path(env['out_dir']).parent, company=ctx.get('COMPANY',''),
                candidate=ctx.get('_applicant',{}))
            strategy_options={'checkpoint_path':Path(env['out_dir'])/'adaptive-v1.json',
                              'source_version':shared['fingerprint']}
        filler=filler_class([],ask,model,
            {k:v for k,v in [('resume',ctx.get('RESUME','')),('jd',ctx.get('_jd_text','')),
                            ('company',ctx.get('COMPANY','')),('cover_letter',ctx.get('COVER_LETTER','')),
                            ('today',ctx.get('TODAY','')),('date_facts',ctx.get('DATE_FACTS',''))] if v},
            {'company':ctx.get('_jd',{}).get('company',''),'contact':ctx.get('REFERRAL',{})},sources,prepare,**strategy_options)
        ctx['_experimental_filler']=filler
    filler.evidence_dir=env.get('out_dir')
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
        _save_planner_failure(exc,env.get('out_dir'),page_no,log)
        for field in read_view(page).fields:
            row=A._blank_entry(field,page_no,f'experimental planner failed: {type(exc).__name__}')
            row.update(outcome='unplanned',verified=False,readback=field.value);record.append(row)
        raise A.PlannerFailed(f'experimental planner failed: {type(exc).__name__}') from exc
    view=read_view(page);history=result['records'];done={}
    if not view.fields and not record:
        result.update(fieldless_page(view,env,page_no,log))
    elif view.fields:
        env['_interstitial_streak']=0
    for f in view.fields:
        entry=next((e for e in reversed(history) if e.get('step')==page_no and
                    e.get('field_key')==key(f)),None)
        if entry is None:entry={}
        proof=filler.upload_proofs.get((page_no,key(f))) if f.kind=='file' else None
        if f.kind=='file' and not entry:
            entry,rebound=_parsed_upload_entry(filler,page_no,view,f,history)
            if rebound is not None:proof=rebound
        if f.kind=='file' and proof is None:proof=filler.uploaded_labels.get((page_no,f.label))      # a same-label input satisfied by a verified upload
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
        if f.kind=='password' and forms.has_value(f):
            row['flag']=''  # presence only; never expose or claim source-verification of the secret
        record.append(row);done[key(f)]=row
    # A widget can disappear after dispatch, and its native required marker may
    # be absent even when the visible form requires a resume. Neither situation
    # makes a provided, recognised document optional to our verification.
    document_blockers=[]
    for ident,doc in documents.items():
        role=doc['role'];label=doc['label']
        matches=[done[key(f)] for f in view.fields if f.kind=='file' and f.identity==doc['identity']]
        row=matches[0] if len(matches)==1 else {}
        # Two inputs of one page can carry one label (Dayforce: one HTML id twice). When every one of them holds the verified upload the
        # document is not ambiguous.
        same_label=[done[key(f)] for f in view.fields if f.kind=='file' and f.label==label]
        duplicates_ok=len(same_label)>1 and all(r.get('verified') and r.get('answer')==role for r in same_label)
        if not row and duplicates_ok:row=same_label[0]
        proof=filler.upload_proofs.get((page_no,doc['field_key']))
        if not matches and not doc['ambiguous'] and proof and proof.verify():
            entry=next((e for e in reversed(history) if e.get('step')==page_no and e.get('identity')==doc['identity'] and e.get('outcome')=='verified'),{})
            if entry:
                row={**entry,'page':page_no,'verified':True,'filled':files[role],'answered':True,
                     'options':[],'why':'matched dispatched bytes and acknowledgement in the same widget','source':'document role','flag':'','error':''}
                record.append(row)
        if not ((not doc['ambiguous'] or duplicates_ok) and row.get('verified') and row.get('answer')==role and
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
