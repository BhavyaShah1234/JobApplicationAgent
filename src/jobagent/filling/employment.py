"""Project explicitly limited recent work history onto the portal's numbered rows."""
from dataclasses import replace
from datetime import datetime
import re
from .facts import entity_scope,norm

_WORDS = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10}
_LIMIT = re.compile(r'\b(?:last|most recent|latest)\s+(?:(one|two|three|four|five|six|seven|eight|nine|ten)'
                    r'(?:\s*\(\s*(\d+)\s*\))?|(\d+))\s+(?:employers?|jobs?|roles?|positions?)\b',re.I)


def recent_employment_facts(facts, view):
    """Select source employment records using only an explicit last-N-employers instruction."""
    if view is None:return facts
    limits=set()
    for heading in view.headings:
        for match in _LIMIT.finditer(heading):
            value=_WORDS[match[1].lower()] if match[1] else int(match[3])
            if match[2] and int(match[2])!=value:
                raise ValueError('The portal gives conflicting limits for recent employment')
            limits.add(value)
    if not limits:return prefilled_employment_facts(facts,view)
    if len(limits)!=1:raise ValueError('The portal gives conflicting limits for recent employment')
    limit=limits.pop()
    rows={scope[1] for field in view.fields if (scope:=entity_scope(field.section)) and scope[0]=='employment'}
    if not 1<=limit<=10 or rows!=set(range(1,limit+1)):return facts
    groups={scope for fact in facts if (scope:=entity_scope(fact.section)) and scope[0]=='employment'}
    if len(groups)<limit:return facts
    ranks={}
    for group in groups:
        ends={fact.value.strip().casefold() for fact in facts if entity_scope(fact.section)==group and 'End Date' in fact.aliases}
        if len(ends)!=1:raise ValueError('Recent employment requires one evidenced end date per role')
        value=ends.pop()
        if value in ('present','current','ongoing'):
            ranks[group]=(9999,12)
            continue
        # Source dates establish ordering; never invent a missing month/day.
        for fmt in ('%B %Y','%b %Y','%Y-%m','%m/%Y','%Y-%m-%d','%m/%d/%Y'):
            try:
                date=datetime.strptime(value,fmt);ranks[group]=(date.year,date.month);break
            except ValueError:pass
        else:raise ValueError('Recent employment cannot be ordered from an incomplete source end date')
    if len(set(ranks.values()))!=len(ranks):
        raise ValueError('Recent employment end dates are tied; source ordering needs clarification')
    ordered=sorted(groups,key=lambda g:ranks[g],reverse=True)[:limit]
    mapping={group:f'Work Experience {index}' for index,group in enumerate(ordered,1)}
    return [replace(fact,section=mapping[scope]) if scope in mapping else fact
            for fact in facts if not (scope:=entity_scope(fact.section)) or scope[0]!='employment' or scope in mapping]


def prefilled_employment_facts(facts,view):
    """Match copied review rows by sourced employer AND title, not source-list order.

    Args:
        facts: Owner employment facts with their original record IDs.
        view: Current fields; every numbered row must identify one unique record.

    Returns:
        Facts projected onto the identified UI rows, or unchanged on ambiguity.
    """
    rows={entity_scope(f.section) for f in view.fields if (entity_scope(f.section) or ('',))[0]=='employment'}
    if not rows or any(not isinstance(row[1],int) or row[1]<1 for row in rows):return facts
    groups={scope for f in facts if (scope:=entity_scope(f.section)) and scope[0]=='employment' and isinstance(scope[1],int)}
    mapping={}
    for row in rows:
        fields=[f for f in view.fields if entity_scope(f.section)==row]
        companies={norm(f.value) for f in fields if norm(f.label) in {'employer','employer name','name of employer','company','company name'} and f.value.strip()}
        titles={norm(f.value) for f in fields if norm(f.label) in {'title','job title','your job title','position title'} and f.value.strip()}
        if len(companies)!=1 or len(titles)!=1:return facts
        matches=[]
        for group in groups:
            own=[f for f in facts if entity_scope(f.section)==group and f.authority in ('owner','resume') and not f.record and not f.instructions]
            employers={norm(v) for f in own if any(norm(a) in {'employer','company','company name','employer name'} for a in f.aliases) for v in [f.value,*f.variants]}
            jobs={norm(v) for f in own if any(norm(a) in {'title','job title','position title'} for a in f.aliases) for v in [f.value,*f.variants]}
            if companies<=employers and titles<=jobs:matches.append(group)
        if len(matches)!=1 or matches[0] in mapping:return facts
        mapping[matches[0]]=f'Work Experience {row[1]}'
    # Preserve unused source records for portals that offer Add Experience.
    # They must not retain an ordinal now owned by a matched review row.
    next_row=max(row[1] for row in rows)+1
    for group in sorted(groups-mapping.keys()):
        mapping[group]=f'Work Experience {next_row}';next_row+=1
    return [replace(f,section=mapping[group]) if (group:=entity_scope(f.section)) in mapping else f for f in facts]
