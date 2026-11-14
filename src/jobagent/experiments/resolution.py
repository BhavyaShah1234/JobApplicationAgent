"""Evidence-bound resolution: models may select facts, never manufacture factual values."""
from dataclasses import dataclass, field
import re
from datetime import datetime
from jobagent.tools import forms


def norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s).lower()).strip()


@dataclass
class Fact:
    id: str
    aliases: list[str]
    value: str
    source: str
    section: str = ''
    variants: list[str] = field(default_factory=list)


@dataclass
class Decision:
    value: str | None
    source: str = ''
    fact_id: str = ''
    reason: str = ''


def key(f):
    return (f.frame, f.section, f.label, f.kind, f.slot)


ENTITY_HEADER = re.compile(r'^(?:(?:previous|prior|past)\s*)?(work\s*experience|employment(?:\s*history)?|professional\s*experience|education|academic(?:\s*(?:history|qualifications))?|references?|referrals?)\s*(?:entry\s*)?(\d+)?$')
ENTITY_COMPONENTS = {
    'first name','given name','middle name','last name','family name','surname','full name','name',
    'email','email address','phone','phone number','primary phone','telephone','mobile',
    'address','street address','address line 1','address line 2','city','town','state','province',
    'region','country','zip','zip code','postal code','post code','pincode',
}


def entity_scope(section):
    match=ENTITY_HEADER.fullmatch(norm(section))
    if not match:return None
    name=match[1]
    group='education' if name.startswith(('education','academic')) else 'reference' if name.startswith('reference') else 'referral' if name.startswith('referral') else 'employment'
    return group, int(match[2]) if match[2] else None


def scope_allows(f, fact):
    target=entity_scope(f.section)
    if fact.section:
        origin=entity_scope(fact.section)
        return target==origin if target and origin else norm(f.section)==norm(fact.section)
    # A general contact answer is not evidence for an employer, school or reference's contact details.
    if target and (norm(f.label) in ENTITY_COMPONENTS or any(norm(a) in ENTITY_COMPONENTS for a in fact.aliases)):
        return False
    return True


def percent_range(text):
    span=re.search(r'(\d+(?:\.\d+)?)\s*%?\s*[-–]\s*(\d+(?:\.\d+)?)\s*%',text)
    if span:return (float(span[1]),float(span[2]))
    nums=[float(x) for x in re.findall(r'(\d+(?:\.\d+)?)\s*%',text)]
    if len(nums)==2:return tuple(nums)
    if len(nums)!=1:return None
    n=nums[0];word=norm(text)
    if '<' in text or any(x in word for x in ('or less','up to','at most','less than')):return (0,n)
    if '>' in text or any(x in word for x in ('or more','at least','more than')):return (n,100)
    return (n,n)


def choice(f, values):
    if f.kind not in ('select', 'combobox', 'radio', 'buttons', 'checkboxes'):
        return values[0]
    for value in values:
        hit = forms.strict_option(value, f.options)
        if hit is not None:
            return hit
    for value in values:
        if norm(value) in ('yes','no'):
            hits=[o for o in f.options if re.match(r'^'+norm(value)+r'\b',norm(o))]
            if len(hits)==1:return hits[0]
        for canonical,alternates in forms._SYNONYMS.items():
            group={norm(x) for x in (canonical,*alternates)}
            if norm(value) in group:
                hits=[o for o in f.options if norm(o) in group]
                if len(hits)==1:return hits[0]
    target=percent_range(values[0])
    if target:
        hits=[o for o in f.options if percent_range(o)==target or
              (target[0]==target[1] and percent_range(o) and percent_range(o)[0]<=target[0]<=percent_range(o)[1])]
        if len(hits)==1:return hits[0]
    # Searchable pickers must search the actual identity, not pick the closest initial result.
    if f.kind == 'combobox':
        return values[0]
    return None


def from_fact(f, fact):
    value=choice(f,[fact.value,*fact.variants])
    if f.format and 'dd' in f.format.lower():
        parsed=None
        for fmt in ('%Y-%m-%d','%m/%d/%Y','%B %d, %Y'):
            try:parsed=datetime.strptime(fact.value,fmt);break
            except ValueError:pass
        if parsed is None:return Decision(None,fact.source,fact.id,'exact day missing from source')
        value=parsed.strftime('%Y-%m-%d' if f.kind=='date' or f.format.upper().startswith('YYYY') else '%m/%d/%Y')
    return Decision(value,fact.source,fact.id,'' if value is not None else 'source value not offered')


def resolve(f, facts):
    label=f.label
    suffix=' '.join(f.options)
    if suffix and label.endswith(suffix):label=label[:-len(suffix)].strip()
    matches = [x for x in facts if norm(label) in map(norm, x.aliases)
               and scope_allows(f,x)]
    human=[x for x in matches if x.source=='human']
    if len(human)==1:matches=human
    if len(matches) != 1:
        return Decision(None, reason='no unique source-backed mapping')
    fact = matches[0]
    return from_fact(f,fact)


def compile_facts(data, raw_jd):
    """Conservative mappings; unrecognized structured entries remain available to model mapping."""
    from jobagent.tools.applicant import entries
    facts = []
    component_aliases = {
        'Legal name': {'first': ['First Name', 'Given Name'], 'middle': ['Middle Name'], 'last': ['Last Name', 'Family Name']},
        'Current address': {'line1':['Address','Street Address','Address Line 1'],
                            'postal_code':['Zip Code','ZIP','Postal Code'], 'street': ['Address', 'Street Address', 'Address Line 1'], 'city': ['City'],
                            'state': ['State'], 'zip': ['Zip Code', 'ZIP', 'Postal Code'], 'country': ['Country']},
        'Phone': {'number': ['Phone', 'Primary Phone', 'Phone Number']},
    }
    for section, entry in entries(data):
        value = entry.get('answer'); q = entry['question']; source = f'applicant/{section}/{q}/answer'
        if isinstance(value, dict):
            for component, aliases in component_aliases.get(q, {}).items():
                if component in value:
                    facts.append(Fact(source+'/'+component, aliases, str(value[component]), source+'/'+component))
        elif isinstance(value,list) and all(isinstance(x,dict) for x in value):
            for i,row in enumerate(value,1):
                group='Education' if q=='Degrees' else 'Work Experience' if q=='Work history' else q
                aliases={'school':['School','University','Institute'], 'degree':['Degree'], 'field':['Field of Study','Major'],
                         'from':['From Date','Start Date'], 'start':['From Date','Start Date'],
                         'end':['End Date'], 'to':['End Date'], 'company':['Company Name','Employer'], 'title':['Job Title'],
                         'city':['City'], 'state':['State','Province'], 'country':['Country']}
                for component,names in aliases.items():
                    v=row.get(component)
                    variants=v if isinstance(v,list) else [v]
                    if variants and isinstance(variants[0],(str,int,float)):
                        facts.append(Fact(source+f'/{i-1}/{component}',names,str(variants[0]),source+f'/{i-1}/{component}',
                                          f'{group} {i}',[str(x) for x in variants[1:]]))
        elif isinstance(value, (str, bool, int, float)) and value != '':
            val = ('Yes' if value else 'No') if isinstance(value, bool) else str(value)
            facts.append(Fact(source, [q, *entry.get('seen', [])], val, source))
    # Only invoke this owner's explicit JD-dependent travel rule when present.
    travel_rule = next((e for _, e in entries(data) if e['question'] == 'Willing to travel?' and e.get('instructions')), None)
    if travel_rule:
        matches=list(re.finditer(r'(?i)travel[^.\n]{0,60}?\d+\s*%(?:\s*(?:or less|or more))?',raw_jd))
        intervals={percent_range(m[0]) for m in matches}
        if len(intervals)==1 and matches:
            m=matches[0]
            facts.append(Fact('jd.travel',['Willingness to travel','Percentage of travel'],m[0],
                              'jd.txt:'+str(raw_jd[:m.start()].count('\n')+1)))
        elif not re.search(r'(?i)\btravel\b',raw_jd) and re.search(r'(?i)choose\s+100\s*%',travel_rule['instructions']):
            facts.append(Fact('travel.fallback',['Willingness to travel','Percentage of travel'],'100%',
                              'applicant travel instruction; original jd.txt searched in full'))

    return facts
