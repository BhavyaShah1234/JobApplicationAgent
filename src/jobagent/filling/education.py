"""Bind unnumbered study-location pickers to the degree selected on this form."""
import hashlib
import json
from calendar import monthrange
from datetime import datetime
from .facts import Fact,Decision,from_fact,entity_scope,key,norm

STUDY_COMPONENTS={'study_school':'School','study_country':'Country','study_region':'Region'}


def highest_completed_summary(field,facts):
    """Compose a degree and its own subject only after completion is evidenced."""
    from .routing import degree_level
    missing=Decision(None,reason='explicit education evidence missing')
    exact=[x for x in facts if x.id.startswith('application.answer.')]
    if len(exact)==1:return from_fact(field,exact[0])
    if field.kind not in ('text','textarea'):return missing
    degrees=[x for x in facts if 'Degree' in x.aliases]
    completed=[];proof=[]
    for degree in degrees:
        ends=[x for x in facts if x.section==degree.section and 'End Date' in x.aliases]
        level=degree_level(degree.value)
        if level is None or len(ends)!=1:return missing
        end=ends[0];proof.extend([degree,end])
        if norm(end.value) in ('present','current','now'):continue
        try:today=datetime.strptime(degree.reference_date,'%Y-%m-%d').date()
        except ValueError:return missing
        for fmt in ('%Y-%m-%d','%m/%d/%Y','%B %Y','%b %Y','%m/%Y','%Y'):
            try:date=datetime.strptime(end.value,fmt).date()
            except ValueError:continue
            first=date
            last=(date.replace(month=12,day=31) if fmt=='%Y' else
                  date.replace(day=monthrange(date.year,date.month)[1]) if '%d' not in fmt else date)
            # A month/year spanning today does not prove graduation yet.
            if first<=today<last:return missing
            if last<=today:completed.append((level,degree))
            break
        else:return missing
    if not completed:return missing
    highest=max(level for level,_ in completed)
    top=[degree for level,degree in completed if level==highest]
    if len(top)!=1:return missing
    degree=top[0]
    subjects=[x for x in facts if x.section==degree.section and 'Field of Study' in x.aliases]
    if len(subjects)!=1 or not subjects[0].value.strip():return missing
    subject=subjects[0];proof.append(subject)
    return Decision(f'Highest Degree: {degree.value}; Field of Study: {subject.value}',
        '; '.join(dict.fromkeys(x.source for x in proof)),','.join(x.id for x in proof),
        'highest completed owner degree and subject from the same education record')


def selection_context(facts,view):
    """Observe accepted controls; never choose a source degree from the job location."""
    facts=[x for x in facts if not x.id.startswith('form.education.selection:')]
    if view is None:return facts
    from .routing import family,degree_level
    contexts=[]
    for field in view.fields:
        if family(field.label) not in STUDY_COMPONENTS or entity_scope(field.section):continue
        parents=[p for p in view.fields if p.frame==field.frame and not entity_scope(p.section)
                 and family(p.label)=='education_level']
        if len(parents)!=1:continue
        parent=parents[0]
        info={'parent_identity':parent.identity,'selected_value':parent.value}
        if not parent.value or parent.invalid or norm(parent.value) in {'no selection','select one','select'}:
            info['pending']=True
        else:
            level=degree_level(parent.value)
            degrees=[x for x in facts if x.authority=='owner' and not x.record and 'Degree' in x.aliases
                     and (entity_scope(x.section) or ('',))[0]=='education'
                     and level is not None and degree_level(x.value)==level]
            if len(degrees)!=1:continue
            info.update(degree_id=degrees[0].id,section=degrees[0].section)
        identity=hashlib.sha256(repr(key(field)).encode()).hexdigest()[:24]
        contexts.append(Fact('form.education.selection:'+identity,[],json.dumps(info),
            'form:highest-education '+parent.identity+'='+parent.value,record=True,
            authority='form',target_key=key(field)))
    return facts+contexts
