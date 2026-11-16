"""Question classification and evidence routing, before answer generation.

Source entity identity and UI row identity are separate. Sensitive predicates
must match their exact purpose; sharing a boolean value proves nothing.
"""
from dataclasses import dataclass,asdict,replace
import json,re
from rapidfuzz import fuzz
from .facts import norm,Decision,Fact,from_fact,choice,entity_scope,scope_allows,owner_only


def family(text):
    q=norm(text)
    rules=[
      ('external_sharing',r'(?:shar|send|forward|disclos).*?(?:external|partner|third part)'),
      ('clearance_eligibility',r'(?:eligible|eligibility|obtain).*clearance'),
      ('clearance_held',r'(?:hold|have|possess|active).*clearance'),
      ('arbitration',r'arbitration'),('read_ack',r'confirm i have read the above'),
      ('ai_consent',r'ai (?:notetaker|transcri)|use ai.*transcri'),
      ('sms_consent',r'sms|whatsapp|text messages'),
      ('future_contact',r'future (?:job |employment )?opportunit|talent community'),
      ('privacy_consent',r'privacy|data processing'),
      ('on_site',r'on site|onsite|work (?:from|in|at).*office'),
      ('sponsorship',r'sponsor'),('work_authorization',r'legally authorized|eligible to work|authorized to work'),
      ('residency',r'(?:resident|reside|live) (?:of|in) (?:the )?(?:state|california|arizona|new york|texas|united states)|resident of'),
      ('offer_deadline',r'offer.*deadline|competing.*deadline'),
      ('office_preferences',r'location preference|office.*preference|locations.*(?:work|open|placed)'),
      ('pronouns',r'\bpronouns\b'),('gender',r'gender|^sex$'),('race',r'\brace\b|ethnicity'),
      ('veteran',r'veteran'),('disability',r'disability'),('hispanic',r'hispanic|latino|latinx'),
      ('technical_interests',r'technical (?:area|domain)|engineering (?:organization|preference)|engineer profile'),
      ('why_company',r'why.*(?:work|join|interest)|why (?:this|our) company'),
      ('technical_experience',r'describe.*experience|technical experience question'),
      ('project',r'favorite project|favourite project|proudest|accomplishment'),
      ('experience_years',r'(?:how many )?years.*experience'),
      ('account_status',r'currently.*client|(?:opened|have) an? account'),
      ('event_history',r'conferences?.*(?:attend|planning)|gatherings.*attend'),
      ('test_score',r'\b(?:sat|act|gre|gmat|toefl|ielts)\b.*(?:score|result)|(?:score|result).*\b(?:sat|act|gre|gmat|toefl|ielts)\b'),
      ('current_employer',r'current (?:or previous )?(?:company|employer)|who is your.*employer'),
      ('current_title',r'(?:current|previous).*job title'),
      ('languages',r'language skill|^languages$|^language$'),
      ('school',r'school|university|institute'),('degree',r'\bdegree\b'),
      ('discipline',r'discipline|field of study|\bmajor\b'),
      ('gpa',r'\bgpa\b|grade point'),
      ('country',r'country.*(?:resid|live)|country where'),
      ('start_date',r'when can you start|earliest start|available start|date available|available to start'),
      ('signature_date',r'^date$|signature date|date signed|today s date'),
      ('portfolio_link',r'portfolio|(?:personal|other) website|^website$'),('email',r'\bemail\b|e mail'),('phone',r'\bphone\b|telephone|\bmobile\b'),
      ('preferred_name',r'preferred name|what.*call you|nickname'),
      ('legal_name',r'legal name|^full name$|^name$'),
      ('location',r'current location|where.*(?:located|based)|location city|city.*state.*resid'),
    ]
    return next((kind for kind,pat in rules if re.search(pat,q)), 'other')


@dataclass
class Route:
    field_id:int
    family:str
    sources:list[str]
    fact_ids:list[str]
    reason:str=''


def fact_family(fact):
    return family(fact.aliases[0] if fact.aliases else fact.id)



def qualification_signature(text,kind):
    """Parameters matter: a related question family is not the same claim."""
    q=norm(text.replace('C++','cpp').replace('c++','cpp').replace('C#','csharp').replace('c#','csharp'))
    if kind.startswith('clearance_'):
        levels=[]
        for name,pattern in [('ts sci',r'\bts sci\b|top secret.*sci'),('top secret',r'\btop secret\b'),
                             ('secret',r'(?<!top )\bsecret\b'),('public trust',r'\bpublic trust\b'),
                             ('q',r'\bq clearance\b'),('l',r'\bl clearance\b')]:
            if re.search(pattern,q):levels.append(name)
        if 'ts sci' in levels:levels=['ts sci']
        timeframe='past' if re.search(r'\bever\b|\bprevious(?:ly)?\b|\bformerly\b|\bhad\b',q) else 'current'
        return tuple(sorted(levels)),timeframe
    if kind=='experience_years':
        generic={'how','many','years','year','of','relevant','industry','professional','work','working','experience','do','you',
                 'have','in','a','an','the','total','your','overall','full','time','post','college','graduate','postgraduate',
                 'non','internship','noninternship','required','at','least','over','more','than','or','and','please','number',
                 'minimum','combined','related','with','to','is','are'}
        return tuple(sorted(set(q.split())-generic-{w for w in q.split() if w.replace('.','',1).isdigit()}))
    return ()


def same_qualification(field,fact):
    kind=family(field.label)
    if kind not in ('experience_years','clearance_held','clearance_eligibility'):return True
    target=qualification_signature(field.label,kind)
    return any(qualification_signature(alias,kind)==target for alias in fact.aliases)



def contact_party(text):
    q=norm(text)
    for party,pattern in [('emergency',r'emergency contact'),('referrer',r'\breferr\w*'),
                          ('reference',r'\breferences?\b'),('supervisor',r'\bsupervisor\b|\bmanager\b'),
                          ('relative',r'\brelative\b|family member')]:
        if re.search(pattern,q):return party
    if re.search(r"\b(?:their|his|her|that person s)\b.{0,45}\b(?:name|email|e mail|phone|address)\b",q):
        return 'third_party'
    return 'applicant'



def needs_entity_binding(label,section='',help_text=''):
    """A school/reference/employer literal needs its own record, not a global alias."""
    from jobagent.tools.forms import is_identity
    return bool(entity_scope(section) or contact_party(label+' '+section+' '+help_text)!='applicant' or
                (is_identity(label) and family(label) not in ('current_employer','current_title')))


def candidates(field,facts):
    kind=family(field.label)
    allowed=[x for x in facts if scope_allows(field,x)]
    party=contact_party(field.label+' '+field.section+' '+field.help)
    if kind in ('email','phone','legal_name','preferred_name','current_employer','current_title','country','location','languages','residency','clearance_held','clearance_eligibility','experience_years','work_authorization','sponsorship','pronouns','gender','race','hispanic','veteran','disability'):
        allowed=[x for x in allowed if contact_party(' '.join(x.aliases)+' '+x.section)==party]
    if party!='applicant':
        components={'country':{'country'},'current_title':{'job title','title'},
                    'current_employer':{'company name','employer','company'},
                    'location':{'city','state','country','location','address'}}
        if kind in components:
            return [x for x in allowed if not x.record and any(norm(a) in components[kind] for a in x.aliases)]
    if kind=='current_title':
        values=[x for x in allowed if x.section=='Work Experience 1' and not x.record and any(norm(a)=='job title' for a in x.aliases)]
        if values:return values
    if kind=='country':
        return [x for x in allowed if x.id.endswith('/Current address/answer/country')]
    if entity_scope(field.section) and re.search(r'\b(start|from|end|to)\b',norm(field.label)):
        end=bool(re.search(r'\b(end|to)\b',norm(field.label)))
        date_facts=[x for x in allowed if x.id.endswith(('/end','/to') if end else ('/start','/from'))]
        if date_facts:return date_facts
    exact=[x for x in allowed if norm(field.label) in [norm(a) for a in x.aliases]]
    if exact:
        sheet=[x for x in exact if x.id.startswith('application.answer.')]
        return sheet or exact
    sheets=[x for x in allowed if x.id=='application.answers' and norm(field.label) in norm(x.value)]
    if sheets:return sheets
    typed=[x for x in allowed if fact_family(x)==kind and same_qualification(field,x)] if kind!='other' else []
    if kind=='on_site':typed=[x for x in typed if not x.record and not any(w in norm(x.aliases[0]) for w in ('remote','hybrid'))]
    if typed or kind!='other':return typed
    noise={'what','your','the','is','are','you','a','an','of','in','to','for','do','have','please','information','additional','details','optional','part','share','provide','application','candidate','candidates','us','about','any','other','this','our','we'}
    words=set(norm(field.label).split())-noise
    ranked=[]
    for fact in allowed:
        if not fact.aliases:continue
        score=max(fuzz.token_set_ratio(norm(field.label),norm(a)) for a in fact.aliases)
        alias_words=set(norm(' '.join(fact.aliases)).split())-noise
        overlap=words & alias_words
        if alias_words and overlap and (len(overlap)>=2 or len(alias_words)==1) and score>=65:ranked.append((score,fact))
    return [x for _,x in sorted(ranked,key=lambda item:-item[0])[:6]]


def route(field,facts):
    kind=family(field.label);found=[x for x in candidates(field,facts) if same_qualification(field,x)]
    # A portal may call a country selector simply 'location'. Its actual
    # catalogue disambiguates the answer type before choosing a source.
    catalogue={norm(o) for o in field.options}
    if kind in ('other','location') and re.search(r'(?i)location|located',field.label) and {'canada','india','japan'}<=catalogue:
        kind='country';found=candidates(replace(field,label='Country where you reside'),facts)
    if kind in ('why_company','project','technical_experience','technical_interests'):
        sources=['owner_rules','resume','jd','company']
    elif kind in ('start_date','signature_date'):sources=['owner_rules','clock']
    elif kind in ('external_sharing','privacy_consent','future_contact','ai_consent','sms_consent','arbitration','read_ack','event_history','account_status','clearance_eligibility','clearance_held','sponsorship','work_authorization','residency'):
        # A different owner's answer with the same boolean value is not evidence.
        found=[x for x in found if fact_family(x)==kind or (kind in ('arbitration','read_ack') and x.id.startswith('application.answer.'))]
        sources=['owner']
    else:sources=['owner','learned']
    return Route(field.id,kind,sources,[x.id for x in found])


def resolve_routed(field,facts,route_info=None):
    r=route_info or route(field,facts);bank={x.id:x for x in facts};selected=[bank[i] for i in r.fact_ids]
    owner=[x for x in selected if x.authority=='owner']
    if r.family in ('email','phone','preferred_name','legal_name','work_authorization','portfolio_link','pronouns','gender','race','veteran','disability','hispanic'):selected=owner
    if r.family=='external_sharing' and not owner:
        return Decision(choice(field,['No']),'policy:external-sharing-opt-out',reason='No purpose-specific permission; do not opt in to external sharing')
    if r.family in ('clearance_eligibility','arbitration','read_ack','event_history','account_status','privacy_consent','future_contact','sms_consent','ai_consent') and not owner:
        return Decision(None,reason='explicit owner evidence missing')
    if r.family=='clearance_held' and len(selected)==1 and norm(selected[0].value) in ('none','no','false'):
        return Decision(choice(field,['No']),selected[0].source,selected[0].id)
    if r.family=='residency':
        exact=[x for x in owner if norm(field.label) in [norm(a) for a in x.aliases]]
        if len(exact)==1:return from_fact(field,exact[0])
        if re.search(r'\bever\b|\bpast\b|\blast \d|\bprevious|\bformerly\b|\blegal\b|\bpermanent\b|\btax\b|\bfor \d+ years',norm(field.label)):
            return Decision(None,reason='explicit residency evidence missing')
        from jobagent.tools.forms import US_STATES
        stated=[code for name,code in US_STATES.items() if re.search(r'\b'+re.escape(name)+r'\b',norm(field.label))]
        party=contact_party(field.label+' '+field.section+' '+field.help)
        if party=='applicant':
            state=[x for x in facts if x.id=='owner.residence.state'] or [x for x in facts if x.id.endswith('/state') and '/Current address/' in x.id and not x.section]
        else:
            state=[x for x in facts if scope_allows(field,x) and contact_party(' '.join(x.aliases)+' '+x.section)==party and any(norm(a)=='state' for a in x.aliases)]
        if len(set(stated))==1 and len(state)==1:
            actual=US_STATES.get(norm(state[0].value),norm(state[0].value))
            if actual not in set(US_STATES.values()):return Decision(None,reason='state evidence unavailable')
            value=actual==stated[0]
            if re.search(r'\bnot (?:a )?resident\b|\bnot reside\b',norm(field.label)):value=not value
            return Decision(choice(field,['Yes' if value else 'No']),state[0].source,state[0].id)
        return Decision(None,reason='explicit residency evidence missing')
    if field.kind in ('text','url') and re.search(r'(?i)linkedin.*(?:github|portfolio|website)',field.label):
        # The question explicitly accepts alternative links. Keep its stated
        # order and use a saved literal, never a discovered lookalike profile.
        for platform in re.findall(r'(?i)linkedin|github|personal website|portfolio',field.label):
            matches=[x for x in facts if x.authority=='owner' and not x.record and not x.instructions and
                any(norm(a)==norm(platform) for a in x.aliases) and re.fullmatch(r'https?://\S+',x.value)]
            if len(matches)==1:return Decision(matches[0].value,matches[0].source,matches[0].id)
    if r.family=='languages':
        values=[];bound=[]
        for x in facts:
            if x.record and x.aliases and x.aliases[0]=='Languages' and scope_allows(field,x):
                try:value=json.loads(x.value).get('language')
                except (ValueError,AttributeError):continue
                if value:values.append(value);bound.append(x)
        if values:
            from jobagent.tools.forms import strict_option
            picks=[strict_option(v,field.options) for v in values]
            if all(picks):return Decision('; '.join(picks),'; '.join(x.source for x in bound),','.join(x.id for x in bound))
    # Typed personal questions can use an unambiguous same-purpose source even
    # when the portal rewords it. Records still require a checked transformation.
    scalar=[x for x in selected if not x.record]
    if len(scalar)==1:
        fact=scalar[0]
        if r.family=='email' and re.fullmatch(r"On portals with an account the form shows the account's email \(the agent's Gmail\): keep that\.",fact.instructions):
            # A prefilled account email takes precedence; otherwise use the
            # saved address. This conditional has a directly observable input.
            value=field.value.strip() or fact.value
            if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',value):
                return from_fact(field,replace(fact,value=value,instructions=''))
        if r.family=='experience_years' and fact.value.replace('.','',1).isdigit() and re.fullmatch(r"For 'over N years' questions compare with it\.",fact.instructions):
            threshold=re.search(r'\bover (\d+(?:\.\d+)?) years\b',norm(field.label))
            if threshold:
                return Decision(choice(field,['Yes' if float(fact.value)>float(threshold[1]) else 'No']),fact.source,fact.id)
            if re.search(r'how many years|years of .*experience',norm(field.label)):
                fact=replace(fact,instructions='')
        if r.family=='experience_years' and fact.value.replace('.','',1).isdigit() and field.options:
            whole=str(int(float(fact.value)))
            if whole in field.options:return Decision(whole,fact.source,fact.id,'completed whole years')
        if re.search(r'\byear\b',norm(field.label)) and entity_scope(field.section):
            return from_fact(replace(field,format='YYYY'),fact)
        if r.family in ('school','degree','discipline','current_employer','current_title','country','on_site','offer_deadline','portfolio_link','pronouns','languages','gender','race','veteran','disability','hispanic','experience_years','clearance_eligibility','clearance_held','start_date','signature_date','email','phone','preferred_name','legal_name','location','gpa','work_authorization','external_sharing','privacy_consent','future_contact','sms_consent','ai_consent','arbitration','read_ack'):
            return from_fact(field,fact)
    return None


def binding_valid(field,bound,value,route_info):
    if any(x.id not in route_info.fact_ids for x in bound):return False
    protected={'external_sharing','clearance_eligibility','clearance_held','arbitration','read_ack','privacy_consent','future_contact','sms_consent','ai_consent','sponsorship','work_authorization'}
    if route_info.family in protected:
        if not bound or any(fact_family(x)!=route_info.family for x in bound):return False
        if any(x.authority!='owner' for x in bound):return False
    if owner_only(field) and not any(x.authority=='owner' and (x.instructions or fact_family(x)==route_info.family) for x in bound):return False
    if route_info.family in ('email','legal_name','preferred_name','phone','portfolio_link'):
        if not any(norm(value)==norm(v) for x in bound for v in [x.value,*x.variants]):return False
    return True


def field_packet(field):
    out=asdict(field)
    # Exact factual matches are resolved locally. When a model is needed, it
    # receives the full available catalogue and its completeness state. A marker
    # asking for a catalogue search without an actual search tool is unusable.
    out['options_total']=len(out['options'])
    out.pop('option_values',None)
    return out


def word_limits(field,facts):
    texts=[field.label+' '+field.help,*[x.instructions for x in facts if x.instructions]]
    for text in texts:
        match=re.search(r'(\d+)\s*[-–]\s*(\d+)\s*words',text,re.I)
        if match:return int(match[1]),int(match[2])
    return None


def personal_facts(ctx):
    address=ctx.get('_personal_address')
    if isinstance(address,dict) and address.get('state'):
        return [Fact('owner.residence.state',['Actual residence state'],str(address['state']),'personal/address/state')]
    return []


def unlisted_school(field,view,facts):
    if not re.search(r'(?i)did not see.*(?:university|school)|school (?:is )?not listed',field.label):return None
    names=[];ids=[]
    for control in view.fields:
        if family(control.label)!='school' or not entity_scope(control.section):continue
        if not re.match(r'(?i)^other\b',control.value):continue
        selected=[x for x in facts if x.section==control.section and not x.record and fact_family(x)=='school']
        if len(selected)==1:names.append(selected[0].value);ids.append(selected[0].id)
    return Decision('; '.join(dict.fromkeys(names)),'form:unlisted-school',','.join(ids),'Unlisted-school condition evaluated against selected education controls')


def answer_sheet_facts(sheet):
    if not sheet or sheet.strip()=='(none)':return []
    out=[Fact('application.answers',[],sheet,'applicant/application/answers.md',record=True)]
    for index,line in enumerate(sheet.splitlines()):
        cells=[x.strip() for x in line.strip().strip('|').split('|')]
        if len(cells)>=3 and cells[0].isdigit():
            value=cells[2]
            if value.lower().startswith(('yes;','no;')):value=value.split(';',1)[0]
            out.append(Fact(f'application.answer.{index}',[cells[1]],value,'applicant/application/answers.md'))
    return out
