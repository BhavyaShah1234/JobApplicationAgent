"""Bind unnumbered study-location pickers to the degree selected on this form."""
import hashlib
import json
from .facts import Fact,entity_scope,key,norm

STUDY_COMPONENTS={'study_school':'School','study_country':'Country','study_region':'Region'}


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
