"""Project explicitly limited recent work history onto the portal's numbered rows."""
from dataclasses import replace
from datetime import datetime
import re
from .facts import entity_scope

_WORDS = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10}
_LIMIT = re.compile(r'\b(?:last|most recent|latest)\s+(?:(one|two|three|four|five|six|seven|eight|nine|ten)'
                    r'(?:\s*\(\s*(\d+)\s*\))?|(\d+))\s+(?:employers?|jobs?|roles?|positions?)\b',re.I)


def recent_employment_facts(facts, view):
    if view is None:return facts
    limits=set()
    for heading in view.headings:
        for match in _LIMIT.finditer(heading):
            value=_WORDS[match[1].lower()] if match[1] else int(match[3])
            if match[2] and int(match[2])!=value:
                raise ValueError('The portal gives conflicting limits for recent employment')
            limits.add(value)
    if not limits:return facts
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
