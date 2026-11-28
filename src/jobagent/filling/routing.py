"""Question classification and evidence routing, before answer generation.

Source entity identity and UI row identity are separate. Sensitive predicates
must match their exact purpose; sharing a boolean value proves nothing.
"""
from dataclasses import dataclass,asdict,replace
import json,re
from rapidfuzz import fuzz
from jobagent.tools import forms
from .facts import norm,Decision,Fact,from_fact,choice,entity_scope,loose_entity_scope,ENTITY_COMPONENTS,scope_allows,owner_only,expanded_immigration_support,key
from .education import STUDY_COMPONENTS

DECLARATIONS=frozenset({'government_referral','government_relationship','ethics_ack',
                        'outside_activity','employer_personal_ties','immigration_support'})
PURPOSE_BOUND=DECLARATIONS | {'owner_identifier','work_documentation','government_employment',
    'age_threshold','work_restrictions','citizenship_countries','citizenship_status','visa_history','veteran',
    'enterprise_software_experience','technical_skill_possession','opt_participation',
    'stem_opt_extension','nonimmigrant_employment','termination_history','relocation_origin'}
DETERMINISTIC_BOUND=PURPOSE_BOUND | STUDY_COMPONENTS.keys() | {'education_summary','read_ack','residency','posting_title','application_date','current_title','current_employer'}

GOVERNMENT_HISTORY=frozenset({'current or former government employee',
    'are you a current or former government employee',
    'have you ever been a government employee', 'have you ever worked for the government'})


def government_employment_question(text):
    q=norm(text)
    if q in GOVERNMENT_HISTORY:return True
    if re.search(r'\b(?:relative|family|spouse|partner|parent|child|referred|related|married)\b',q):return False
    subject=r'\b(?:(?:are|were) you|have you (?:ever )?been)\s+'
    modifiers=r'(?:(?:a|an|current|former|currently|previously|or|federal|state|local|us|united|states)\s+)*'
    agency=r'(?:government|(?:us |united states )?department of defense|dod)\b'
    return bool(re.search(subject+modifiers+r'(?:employee|employed) (?:of|by|with|at) (?:the )?'+agency,q) or
                re.search(subject+modifiers+agency+r'\s+(?:employee|worker)\b',q))


def family(text):
    q=norm(text)
    if government_employment_question(text):return 'government_employment'
    if expanded_immigration_support(text):return 'immigration_support'
    rules=[
      ('nonimmigrant_employment',r'\bnon immigrant status\b|\bnonimmigrant status\b'),
      ('opt_participation',r'^(?:are|have|do|is|currently)\b(?=.*\b(?:currently|receiving|process)\b)(?=.*(?:(?<!stem )\bopt\b|\bcpt\b))'),
      ('stem_opt_extension',r'\bstem opt\b.*\bextension\b|\b(?:currently|already)\b.*\bstem opt\b'),
      ('visa_status',r'^(?:current )?visa status$'),
      ('termination_history',r'^have you ever been (?:terminated|dismissed)|^have you ever.*(?:asked to resign|resigned.{0,30}terminat)'),
      ('relocation_origin',r'\brelocat\w* from\b|^relocation origin$'),
      ('salary',r'\b(?:desired|expected|expectations|expect|target|requested|anticipated)\b.{0,45}\b(?:salary|compensation|pay)\b|\b(?:salary|compensation|pay)\b.{0,35}\b(?:expectations|expect|desired)\b'),
      ('gpa',r'\bgpa\b|grade point'),
      ('age_threshold',r'\bage (?:of )?\d+\b|\b\d+ years? (?:of age|old)\b'),
      ('work_restrictions',r'\bnon (?:compete|competition|disclosure|solicitation)\b|\bnoncompete\b|restrictive covenant|(?:contract|agreement).*?(?:restrict|impact|interfere|prevent|limit).*?(?:work|employ|colleague)'),
      ('visa_history',r'(?:in the past|\bever\b|previously|formerly|have you (?:been|held|had)).*?(?:\bvisas?\b|exchange visitor)'),
      ('citizenship_countries',r'\b(?:country|countries|nationality)\b.*?(?:citizen|national|permanent residen)|\bcitizenship\b.*?\bcountr(?:y|ies)\b|^nationality$'),
      ('government_referral',r'(?:were you|have you been|are you) referred.*government|government.*(?:were you|have you been|are you) referred'),
      ('government_relationship',r'are you related.*government|do you have.*(?:relatives|family members).*government'),
      ('ethics_ack',r'(?:please acknowledge|do you acknowledge|confirm your understanding).*?(?:ethics|code of conduct)|(?:ethics|code of conduct).*?(?:please acknowledge|do you acknowledge|confirm your understanding)'),
      ('employer_personal_ties',r'(?:close|intimate) personal relationships|shared financial interests|personal or business ties'),
      ('outside_activity',r'(?:do you|will you|are you).*?(?:second job|outside business|secondary employment)'),
      ('external_sharing',r'(?:shar|send|forward|disclos).*?(?:external|partner|third part)'),
      ('clearance_eligibility',r'(?:eligible|eligibility|obtain).*clearance'),
      ('clearance_held',r'(?:hold|have|possess|active).*clearance'),
      ('arbitration',r'arbitration'),
      ('read_ack',r'confirm i have read the above|^i (?:understand|acknowledge) (?:that|the|this|these|how)\b'),
      ('ai_consent',r'ai (?:notetaker|transcri)|use ai.*transcri'),
      ('ai_policy_ack',r'\bai (?:use |usage )?polic(?:y|ies)\b'),
      ('demographic_consent',r'demographic (?:data )?survey|voluntary (?:demographic|self identification)'),
      ('phone',r'^(?:sms(?: text)?|text) country code\b'),
      ('sms_consent',r'sms|whatsapp|text messages'),
      ('future_contact',r'future (?:job |employment )?opportunit|talent community'),
      ('privacy_consent',r'privacy|data processing'),
      ('on_site',r'on site|onsite|work (?:from|in|at).*office'),
      ('work_documentation',r'\b(?:can|could|will|would) you (?:provide|present|produce|supply|furnish|show)\b.*\b(?:documentation|documents?|proof|evidence)\b.*\b(?:identity|identification|right to work|eligibility to work|work eligibility|employment eligibility|work authorization)\b'),
      ('sponsorship',r'sponsor'),('work_authorization',r'legally authorized|eligible to work|authorized to work'),
      ('citizenship_status',r'\bcitizen(?:ship)?\b|\bnational\b|permanent resident|\brefugee\b|\basylee\b'),
      ('residency',r'(?:resident|reside|live) (?:of|in) (?:the )?(?:state|california|arizona|new york|texas|united states)|resident of|'
                   r'\b(?:located|based|reside|live)\b.*\b(?:east coast|west coast|time ?zones?)\b|'
                   r'\b(?:east coast|west coast|time ?zones?)\b.*\b(?:located|based|reside|live)\b'),
      ('offer_deadline',r'offer.*deadline|competing.*deadline'),
      ('office_preferences',r'location preference|office.*preference|locations.*(?:work|open|placed)'),
      ('pronouns',r'\bpronouns\b'),('gender',r'gender|^sex$'),('race',r'\brace\b|ethnicity'),
      ('veteran',r'veteran'),('disability',r'disability'),('hispanic',r'hispanic|latino|latinx'),
      ('technical_interests',r'technical (?:area|domain)|engineering (?:organization|preference)|engineer profile'),
      ('why_company',r'why.*(?:work|join|interest)|why (?:this|our) company|motivat.*(?:work|join|appl|position|role|locat|resid)'),
      ('enterprise_software_experience',r'^(?!.*\b(?:years?|months?)\b)(?:(?:have you (?:ever )?(?:worked|been employed)|do you have (?:any )?experience|experience)\b.*\benterprise (?:software|saas) environment\b|enterprise (?:software|saas) environment$)'),
      ('technical_skill_possession',r'^(?!.*\b(?:years?|months?|degree|certification|licen[cs]e)\b)(?:do you (?:have|possess)|have you|are you (?:experienced|proficient))\b.*\b(?:technical skills|tech(?:nology)? stack|programming languages?|specific language)\b'),
      ('technical_experience',r'describe.*experience|technical experience question'),
      ('project',r'favorite project|favourite project|proudest|accomplishment'),
      ('owner_identifier',r'\b(?:program|employee|applicant|candidate|referral|referrer|membership|registration|identification|passport|tax|social security)\b.{0,32}\b(?:code|id|number|identifier)\b|\bssn\b'),
      ('experience_years',r'(?:how many )?years.*experience'),
      ('account_status',r'currently.*client|(?:opened|have) an? account'),
      ('event_history',r'conferences?.*(?:attend|planning)|gatherings.*attend'),
      ('test_score',r'\b(?:sat|act|gre|gmat|toefl|ielts)\b.*(?:score|result)|(?:score|result).*\b(?:sat|act|gre|gmat|toefl|ielts)\b'),
      ('current_employer',r'^(?:\d+[a-z]? )?(?:(?:(?:what|who) (?:is|was) |(?:please )?(?:provide|enter|state) |name of )?(?:your )?(?:current (?:or (?:previous|most recent) )?(?:company|employer)|employer name|employer))(?: optional)?$'),
      ('current_title',r'^(?:\d+[a-z]? )?(?:(?:what is|which is|(?:please )?(?:provide|enter|state)) )?(?:your )?(?:current(?: or (?:most recent|previous))?|most recent) (?:job |position )?title(?: optional)?$'),
      ('languages',r'language skill|^languages$|^language$'),
      ('education_summary',r'(?=.*\bhighest\b)(?=.*\b(?:degree|education)\b)(?=.*\b(?:field of study|major|subject)\b)'),
      ('education_level',r'highest.*(?:education|degree)'),
      ('education_completion',r'level of completion|current education status'),
      ('study_region',r'where (?:did|do) you study.*region'),
      ('study_country',r'where (?:did|do) you study.*country'),
      ('study_school',r'where (?:did|do) you study.*(?:school|university|institution)'),
      ('discipline',r'(?:please specify|what is).*degree field of study'),
      ('school',r'school|university|institute'),('degree',r'\bdegree\b'),
      ('discipline',r'discipline|(?:field|area) of study|\bmajor\b'),
      ('gpa',r'\bgpa\b|grade point'),
      ('skills',r'^(?:type to add |add |technical |your )?skills(?: optional)?$'),
      ('country',r'country.*(?:resid|live)|country where'),
      ('start_date',r'when can you start|earliest start|available start|date available|available to start|'
                    r'when (?:are you (?:looking|available|able)|would you like) to start '
                    r'(?:a |the |your )?(?:new |next )?(?:position|role|job)\b'),
      ('signature_date',r'^(?:\d+[a-z]? )?(?:date|signature date|date signed|(?:please )?(?:enter )?today s date)(?: mm dd yyyy| yyyy mm dd)?$'),
      ('portfolio_link',r'portfolio|(?:personal|other) website|^website$'),('email',r'\bemail\b|e mail'),
      ('phone',forms.PHONE_CODE_Q.pattern+r'|\bphone\b|telephone|\bmobile\b'),
      ('preferred_name',r'preferred name|what.*call you|nickname'),
      ('legal_name',r'legal name|^full name$|^name$|^typed signature$|^electronic signature$'),
      ('location',r'^location$|current location|where.*(?:located|based)|location city|city.*state.*resid'),
    ]
    return next((kind for kind,pat in rules if re.search(pat,q)), 'other')


@dataclass
class Route:
    field_id:int
    family:str
    sources:list[str]
    fact_ids:list[str]
    reason:str=''


def field_family(field):
    """Use explicit office guidance to disambiguate a bare contact-style Location."""
    if norm(field.section)=='position details':
        if norm(field.label)=='position title':return 'posting_title'
        if re.fullmatch(r'application date (?:month|day|year)',norm(field.label)):return 'application_date'
    if not entity_scope(field.section) and norm(field.label)=='location' and re.search(
            r'\boffice\b|\b(?:job|position|preferred work) locations?\b',
            norm(field.section+' '+field.help)):
        return 'office_preferences'
    return family(field.label)


def fact_family(fact):
    return family(fact.aliases[0] if fact.aliases else fact.id)


def degree_level(value):
    """An education level, not an assertion that two different degrees are equivalent."""
    q=norm(value)
    for level,pattern in [(4,r'\b(?:phd|ph d|doctorate|doctoral)\b'),
                          (3,r'\b(?:master|masters|ms|msc|m sc)\b'),
                          (2,r'\b(?:bachelor|bachelors|btech|b tech|bs|bsc)\b'),
                          (1,r'\bassociate\b'),(0,r'\bhigh school\b')]:
        if re.search(pattern,q):return level
    return None


def education_query_facts(field,facts,kind):
    """Explicit global degree questions can read the named owner record outside a UI row."""
    if kind=='education_summary':
        if entity_scope(field.section):return []
        exact=[x for x in facts if x.authority=='owner' and not x.record and
               x.id.startswith('application.answer.') and norm(field.label) in map(norm,x.aliases)]
        if exact:return exact
        return [x for x in facts if x.authority=='owner' and not x.record and not x.instructions and
                (entity_scope(x.section) or ('',))[0]=='education' and
                any(a in x.aliases for a in ('Degree','Field of Study','End Date'))]
    if kind in STUDY_COMPONENTS:
        component=STUDY_COMPONENTS[kind]
        exact=[x for x in facts if x.authority=='owner' and not x.record and
               x.id.startswith('application.answer.') and norm(field.label) in map(norm,x.aliases)]
        if exact:return exact
        if entity_scope(field.section):
            return [x for x in facts if x.authority=='owner' and not x.record and
                    component in x.aliases and scope_allows(field,x)]
        context=[x for x in facts if x.id.startswith('form.education.selection:') and x.target_key==key(field)]
        if len(context)!=1:return []
        data=json.loads(context[0].value)
        if data.get('pending'):return context
        return context+[x for x in facts if x.authority=='owner' and
            (x.id==data['degree_id'] or (x.section==data['section'] and component in x.aliases and not x.record))]
    degrees=[x for x in facts if x.authority=='owner' and not x.record and
             (entity_scope(x.section) or ('',))[0]=='education' and 'Degree' in x.aliases]
    if entity_scope(field.section):degrees=[x for x in degrees if scope_allows(field,x)]
    if kind=='education_level':return degrees
    q=norm(field.label)
    requested=degree_level(q)
    if requested is None or kind not in ('degree','discipline'):return None
    groups={x.section for x in degrees if degree_level(x.value)==requested}
    # Degree qualifications need their own degree AND subject, not another
    # record's field. A field-of-study question needs only that named field.
    return [x for x in facts if x.authority=='owner' and x.section in groups and
            (('Field of Study' in x.aliases and not x.record) if kind=='discipline' else True)]



def qualification_signature(text,kind):
    """Parameters matter: a related question family is not the same claim."""
    q=norm(text.replace('C++','cpp').replace('c++','cpp').replace('C#','csharp').replace('c#','csharp'))
    if kind=='age_threshold':
        age=re.search(r'\bage (?:of )?(\d+)\b|\b(\d+) years? (?:of age|old)\b',q)
        comparison=('lt' if re.search(r'under|younger than|less than',q) else
                    'le' if re.search(r'or younger|and under',q) else
                    'gt' if re.search(r'(?:over|older than|more than) (?:the )?(?:age (?:of )?)?\d+',q) else 'ge')
        extras=tuple(w for w in ('authorized','experience','citizen','convicted','not') if re.search(r'\b'+w+r'\b',q))
        return (int(age[1] or age[2]) if age else None,comparison,extras)
    if kind=='citizenship_countries':
        return (bool(re.search(r'citizen|nationality',q)),bool(re.search(r'permanent residen',q)),
                bool(re.search(r'\bbirth\b|\bborn\b',q)),
                bool(re.search(r'\b(?:previous|previously|former|formerly|past|ever)\b',q)),
                bool(re.search(r'\b(?:not|no|never)\b',re.sub(r'^.*?please specify','',q))),
                bool(re.search(r'\bwhen\b|\b(?:date|year|obtained|acquired|acquisition)\b',q)))
    if kind=='work_documentation':
        # Identity documents and work-eligibility documents are distinct. A
        # combined question needs an owner declaration covering both purposes.
        return (bool(re.search(r'\bidentity\b|\bidentification\b',q)),
                bool(re.search(r'right to work|eligibility to work|work eligibility|employment eligibility|work authorization',q)))
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
    if kind in ('nonimmigrant_employment','opt_participation','stem_opt_extension'):
        return any(norm(alias)==norm(field.label) for alias in fact.aliases)
    if kind in ('age_threshold','citizenship_countries'):
        return any(family(alias)==kind and qualification_signature(alias,kind)==
                   qualification_signature(field.label,kind) for alias in fact.aliases)
    if kind in ('citizenship_status','work_restrictions','visa_history'):
        # These predicates include parties, history windows and legal scope.
        # Only an explicit matching owner declaration (or saved alias) proves
        # them; a shared visa class or agreement type is insufficient.
        return any(norm(alias)==norm(field.label) for alias in fact.aliases)
    if kind=='government_employment':
        if entity_scope(field.section) and not fact.section:return False
        if any(norm(alias)==norm(field.label) for alias in fact.aliases):return True
        if re.search(r'\bcontract\w*|\bconsult\w*',norm(field.label)):return False
        # Only an explicit all-history negative entails a narrower agency or
        # date window. A broad Yes or current-only No proves neither.
        return (not entity_scope(field.section) and fact.authority=='owner' and not fact.record and
                not fact.instructions and norm(fact.value) in {'no','false'} and
                any(norm(alias) in GOVERNMENT_HISTORY for alias in fact.aliases))
    if kind=='work_documentation':
        return any(family(alias)==kind and qualification_signature(alias,kind)==
                   qualification_signature(field.label,kind) for alias in fact.aliases)
    if kind=='owner_identifier':
        return any(identifier_key(alias)==identifier_key(field.label) for alias in fact.aliases
                   if family(alias)==kind)
    if kind in DECLARATIONS:
        # These declarations have different subjects and potentially lengthy
        # definitions. A similar neighbouring Yes/No is not supporting evidence.
        return any(norm(alias)==norm(field.label) for alias in fact.aliases)
    if kind not in ('experience_years','clearance_held','clearance_eligibility'):return True
    target=qualification_signature(field.label,kind)
    return any(qualification_signature(alias,kind)==target for alias in fact.aliases)


def identifier_key(text):
    """Keep the named identifier's subject, including employer/program qualifiers."""
    words=norm(text).split()
    noise={'please','provide','enter','specify','what','is','your','the','a','an','optional'}
    return tuple('id' if w in {'code','number','identifier'} else w for w in words if w not in noise)



def contact_party(text):
    q=norm(text)
    for party,pattern in [('emergency',r'emergency contact'),('referrer',r'\breferr\w*'),
                          ('reference',r'\breferences?\b'),('supervisor',r'\bsupervisor\b|\bmanager\b'),
                          ('relative',r'\brelative\b|family member')]:
        if re.search(pattern,q):return party
    if re.search(r"\b(?:their|his|her|that person s)\b.{0,45}\b(?:name|email|e mail|phone|address)\b",q):
        return 'third_party'
    return 'applicant'



def needs_entity_binding(label,section='',help_text='',kind=''):
    """A school/reference/employer literal needs its own record, not a global alias."""
    from jobagent.tools.forms import is_identity
    if not entity_scope(section) and kind in ('select','combobox','radio','buttons','checkbox','checkboxes') and \
            family(label) in DECLARATIONS:
        return False  # the candidate's declaration, not an officer/relative/manager's contact component
    return bool(entity_scope(section) or (loose_entity_scope(section) and norm(label) in ENTITY_COMPONENTS) or
                contact_party(label+' '+section+' '+help_text)!='applicant' or
                (is_identity(label) and family(label) not in ('current_employer','current_title')))


def _current_employment_candidates(field,facts,kind):
    """Select an explicit current role, or a uniquely dated most recent role.

    List position never establishes which employer is current. Preserve the
    original record IDs so a title cannot be justified by its company name.
    """
    from datetime import datetime

    if (kind not in ('current_title','current_employer') or entity_scope(field.section) or
            contact_party(field.label+' '+field.section+' '+field.help)!='applicant'):return None
    direct=[x for x in facts if x.authority in ('owner','resume') and not x.record and not x.section and x.value.strip() and
            (norm(field.label) in map(norm,x.aliases) or fact_family(x)==kind)]
    exact=[x for x in direct if x.id.startswith('application.answer.') and norm(field.label) in map(norm,x.aliases)]
    profile=[x for x in direct if not x.id.startswith('learned:')]
    if exact or profile:return exact or profile
    groups={entity_scope(x.section) for x in facts if x.authority in ('owner','resume') and
            (entity_scope(x.section) or ('',))[0]=='employment'}
    current=[];ranks={}
    for group in groups:
        row=[x for x in facts if x.authority in ('owner','resume') and not x.record and entity_scope(x.section)==group]
        flags={norm(x.value) for x in row if 'Current' in x.aliases}
        positive=bool(flags & {'true','yes','current','present'})
        negative=bool(flags & {'false','no','prior','previous'})
        ends={x.value.strip() for x in row if 'End Date' in x.aliases and x.value.strip()}
        ongoing=bool({norm(value) for value in ends} & {'present','current','ongoing'})
        if positive and negative or ongoing and negative or positive and ends and not ongoing:return []
        if positive or ongoing:current.append(group)
        if len(ends)==1 and not ongoing:
            end=next(iter(ends))
            for fmt in ('%B %Y','%b %Y','%Y-%m','%m/%Y','%Y-%m-%d','%m/%d/%Y'):
                try:
                    date=datetime.strptime(end,fmt);ranks[group]=(date.year,date.month);break
                except ValueError:pass
    if len(current)>1:return []
    if current:chosen=current[0]
    elif 'most recent' in norm(field.label) and groups and len(ranks)==len(groups):
        newest=max(ranks.values());latest=[g for g,rank in ranks.items() if rank==newest]
        if len(latest)!=1:return []
        chosen=latest[0]
    else:return direct
    component='Job Title' if kind=='current_title' else 'Company Name'
    return [x for x in facts if x.authority in ('owner','resume') and not x.record and x.value.strip() and
            entity_scope(x.section)==chosen and component in x.aliases]


def candidates(field,facts):
    kind=field_family(field)
    current=_current_employment_candidates(field,facts,kind)
    if current is not None:return current
    projected=education_query_facts(field,facts,kind)
    if projected is not None:return projected
    allowed=[x for x in facts if scope_allows(field,x)]
    if kind=='posting_title':return [x for x in allowed if x.id=='jd.job.title']
    if kind=='application_date':return [x for x in allowed if x.id.startswith('portal.application_date.') and x.target_key==key(field)]
    if kind=='why_company' and re.search(r'\blocati?on\b|\bresid\w*|\brelocat\w*',norm(field.label)):
        exact=[x for x in allowed if norm(field.label) in map(norm,x.aliases)]
        if exact:return exact
        return [x for x in allowed if x.authority=='owner' and not x.record and any(
            re.search(r'\bwilling to relocate\b|\bable to work on site\b',norm(a)) for a in x.aliases)]
    if kind in ('relocation_origin','salary'):
        explicit=[x for x in allowed if x.id.startswith('application.answer.') and norm(field.label) in map(norm,x.aliases)]
        if explicit:return explicit
        if kind=='relocation_origin':return [x for x in allowed if x.id in ('owner.residence.location','jd.job.location')]
        rules=[x for x in allowed if x.authority=='owner' and fact_family(x)=='salary' and x.instructions]
        if rules:return rules
    if kind=='owner_identifier':
        return [x for x in allowed if x.authority=='owner' and not x.record and same_qualification(field,x)]
    party=contact_party(field.label+' '+field.section+' '+field.help)
    if kind in ('email','phone','legal_name','preferred_name','current_employer','current_title','country','location','languages','residency','clearance_held','clearance_eligibility','experience_years','work_authorization','sponsorship','pronouns','gender','race','hispanic','veteran','disability'):
        allowed=[x for x in allowed if contact_party(' '.join(x.aliases)+' '+x.section)==party]
    if party!='applicant':
        components={'country':{'country'},'current_title':{'job title','title'},
                    'current_employer':{'company name','employer','company'},
                    'location':{'city','state','country','location','address'}}
        if kind in components:
            return [x for x in allowed if not x.record and any(norm(a) in components[kind] for a in x.aliases)]
    if kind in ('country','location') and (group:=entity_scope(field.section)):
        components={'country'} if kind=='country' else {'city','state','country','location','address'}
        return [x for x in allowed if entity_scope(x.section)==group and
                (x.record or any(norm(a) in components for a in x.aliases))]
    if kind=='current_title':
        values=[x for x in allowed if x.section=='Work Experience 1' and not x.record and any(norm(a)=='job title' for a in x.aliases)]
        if values:return values
    if kind=='country':
        if re.search(r'\bresiden\w*\b|\blive\b',norm(field.label)):
            return [x for x in allowed if x.id=='owner.residence.country']
        return [x for x in allowed if x.id.endswith('/Current address/answer/country')]
    if entity_scope(field.section) and re.search(r'\b(start|from|end|to)\b',norm(field.label)):
        end=bool(re.search(r'\b(end|to)\b',norm(field.label)))
        date_facts=[x for x in allowed if x.id.endswith(('/end','/to') if end else ('/start','/from'))]
        if date_facts:return date_facts
    exact=[x for x in allowed if norm(field.label) in [norm(a) for a in x.aliases]]
    if exact:
        sheet=[x for x in exact if x.id.startswith('application.answer.')]
        if sheet:return sheet
        if kind in PURPOSE_BOUND:
            # An observed alias is not permission to hide a conflicting global
            # declaration covering exactly the same predicate and parameters.
            return [x for x in allowed if x in exact or (fact_family(x)==kind and same_qualification(field,x))]
        return exact
    if kind=='read_ack':
        # A contact address or acknowledgement of a different statement cannot
        # prove this declaration, including any actions it says were completed.
        return []
    if kind=='location' and party=='applicant' and not entity_scope(field.section):
        city=[x for x in allowed if not x.record and any(
            norm(a)=='current location city' for a in x.aliases)]
        if city:return city
    if kind=='phone' and forms.PHONE_CODE_Q.search(field.label):
        # Reworded dial-code questions need the code component, not the phone
        # number/device/extension or the entire record. Its variants retain
        # the country from that same phone record for shared prefixes like +1.
        codes=[x for x in allowed if not x.record and any(
            forms.PHONE_CODE_Q.search(a) and re.search(r'(?i)\bcode\b',a) for a in x.aliases)]
        profile=[x for x in codes if x.source.startswith('applicant/')]
        return profile or codes
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
    kind=field_family(field);found=[x for x in candidates(field,facts) if same_qualification(field,x)]
    # A portal may call a country selector simply 'location'. Its actual
    # catalogue disambiguates the answer type before choosing a source.
    catalogue={norm(o) for o in field.options}
    if kind in ('other','location') and re.search(r'(?i)location|located',field.label) and {'canada','india','japan'}<=catalogue:
        kind='country';found=candidates(replace(field,label='Country where you reside'),facts)
    if kind in ('posting_title','application_date'):
        sources=['jd'] if kind=='posting_title' else ['portal']
    elif kind=='skills':
        sources=['resume','owner_rules','jd']
    elif kind=='relocation_origin':
        sources=['owner','jd']
    elif kind in ('why_company','project','technical_experience','technical_interests','salary'):
        sources=['owner_rules','resume','jd','company']
    elif kind in ('start_date','signature_date'):sources=['owner_rules','clock']
    elif kind in PURPOSE_BOUND or kind in ('external_sharing','privacy_consent','future_contact','ai_consent','demographic_consent','ai_policy_ack','sms_consent','arbitration','read_ack','event_history','account_status','clearance_eligibility','clearance_held','sponsorship','work_authorization','residency'):
        # A different owner's answer with the same boolean value is not evidence.
        found=[x for x in found if fact_family(x)==kind or (kind in ('arbitration','read_ack') and x.id.startswith('application.answer.'))]
        sources=['owner']
    else:sources=['owner','learned']
    return Route(field.id,kind,sources,[x.id for x in found])


def resolve_routed(field,facts,route_info=None):
    r=route_info or route(field,facts);bank={x.id:x for x in facts};selected=[bank[i] for i in r.fact_ids]
    owner=[x for x in selected if x.authority=='owner']
    if r.family in ('current_title','current_employer'):
        employment=[x for x in selected if x.authority in ('owner','resume')]
        if len(employment)!=1:return Decision(None,reason='explicit current employment evidence missing or ambiguous')
        return from_fact(field,employment[0])
    if r.family in ('posting_title','application_date'):
        return from_fact(field,selected[0]) if len(selected)==1 else Decision(None,reason='explicit portal metadata missing')
    if r.family=='opt_participation' and not owner:
        # A stated current OPT/CPT status proves participation. "No STEM
        # extension" and legal work authorization do not prove its negation.
        q=norm(field.label)
        requested={program for program,pattern in [('opt',r'(?<!stem )\bopt\b'),('cpt',r'\bcpt\b'),('stem',r'\bstem opt\b')]
                   if re.search(pattern,q)}
        union=(len(requested)==1 or re.search(r'\bor\b|/',field.label,re.I)) and not re.search(r'\b(?:not|never)\b',q)
        def participating(fact):
            """Require the stated program to satisfy the question's explicit union."""
            value=norm(fact.value)
            actual='stem' if 'stem opt' in value else 'opt' if re.search(r'\bopt\b',value) else 'cpt' if re.search(r'\bcpt\b',value) else None
            return union and (actual in requested or actual=='stem' and 'opt' in requested and 'initial' not in q)
        statuses=[x for x in facts if x.authority=='owner' and not x.record and not x.instructions
                  and fact_family(x)=='visa_status' and participating(x)
                  and not re.search(r'\b(?:no|not|never|former|previous|eligible|future|planned|pending|awaiting)\b',norm(x.value))]
        if len(statuses)==1:
            fact=statuses[0]
            return Decision(choice(field,['Yes']),fact.source,fact.id,'explicit current OPT/CPT status')
    if r.family=='relocation_origin':
        explicit=[x for x in owner if x.id.startswith('application.answer.')]
        if len(explicit)==1:return from_fact(field,explicit[0])
        origins=[x for x in owner if x.id=='owner.residence.location']
        if len(origins)!=1:return Decision(None,reason='explicit residency evidence missing')
        job=next((x for x in selected if x.id=='jd.job.location'),None)
        def location_key(value):
            """Compare a sourced city/state pair without changing its displayed wording."""
            parts=value.rsplit(',',1)
            if len(parts)!=2:return None
            state=forms.US_STATES.get(norm(parts[1]),norm(parts[1]).upper())
            return norm(parts[0]),state
        origin=origins[0]
        if job and location_key(origin.value)==location_key(job.value) and re.search(r'\bna\b|not applicable',norm(field.label)):
            return Decision('NA',origin.source+'; '+job.source,origin.id+','+job.id,'same recorded residence and job city')
        return from_fact(field,origin)
    if r.family in ('why_company','project','technical_experience','technical_interests'):
        exact=[x for x in owner if not x.record and not x.instructions and norm(field.label) in map(norm,x.aliases)]
        if len(exact)==1:return from_fact(field,exact[0])
        return Decision(None,reason='contextual narrative composition required')
    if r.family=='education_summary':
        from .education import highest_completed_summary
        return highest_completed_summary(field,owner)
    if r.family=='skills':
        projected=[x for x in selected if not x.record and x.id.endswith('/Skills/answer/selected')]
        if len(projected)==1:
            fact=projected[0];values=fact.value.split('; ')
            selectors=('select','combobox','radio','buttons','checkboxes')
            if field.kind in selectors and field.options_state=='complete':
                values=list(dict.fromkeys(hit for value in values if (hit:=forms.strict_option(value,field.options)) is not None))
                if not values:return Decision(None if field.required else '',fact.source,fact.id,'No sourced skills are offered')
            if field.kind in selectors and field.kind!='checkboxes' and not field.multiple:values=values[:1]
            return from_fact(field,replace(fact,value='; '.join(values)))
    if r.family in STUDY_COMPONENTS:
        exact=[x for x in owner if x.id.startswith('application.answer.')]
        context=[x for x in selected if x.id.startswith('form.education.selection:')]
        if context and json.loads(context[0].value).get('pending'):
            return Decision(None,reason='waiting for selected education record')
        component=STUDY_COMPONENTS[r.family]
        values=exact or [x for x in owner if component in x.aliases and not x.record]
        if len(values)!=1:return Decision(None,reason='explicit education evidence missing')
        result=from_fact(field,values[0])
        if result.value is None and r.family=='study_country':
            value,score=forms.match_option(values[0].value,field.options)
            if value is not None and score>=95:result=Decision(value)
        if result.value is None:return result
        bound=values if exact else selected
        return Decision(result.value,'; '.join(x.source for x in bound),','.join(x.id for x in bound),
                        'study component of the selected owner degree')
    if r.family=='education_level' and owner:
        levels=[degree_level(x.value) for x in owner]
        if any(level is None for level in levels):return Decision(None,reason='unknown degree level')
        highest=max(levels);top=[x for x in owner if degree_level(x.value)==highest]
        options=[o for o in field.options if degree_level(o)==highest]
        if len(options)==1:
            return Decision(options[0],'; '.join(x.source for x in top),','.join(x.id for x in top),'highest named owner degree')
    if r.family=='education_completion' and len(owner)==1 and norm(owner[0].value) in {
            'completed a graduate degree','completed an undergraduate degree','graduated','completed'}:
        options=[o for o in field.options if norm(o) in {'graduated','completed'}]
        if len(options)==1:return Decision(options[0],owner[0].source,owner[0].id,'explicit completed education status')
    if r.family=='legal_name':
        full=[x for x in owner if x.id.endswith('/full') and not x.record and not x.instructions]
        if len(full)==1:return from_fact(field,full[0])
    if r.family in PURPOSE_BOUND:
        # A current-job answer sheet takes precedence over reusable sources.
        # Equivalent duplicate facts are fine; conflicting declarations are not.
        scoped=[x for x in owner if x.id.startswith('application.answer.')]
        declared=[from_fact(field,x) for x in (scoped or owner) if not x.record]
        values={norm(x.value) for x in declared if x.value is not None}
        if len(values)==1 and all(x.value is not None for x in declared):return declared[0]
        return Decision(None,reason='explicit owner evidence missing' if not declared else 'conflicting owner declarations')
    if r.family in ('email','phone','preferred_name','legal_name','work_authorization','portfolio_link','pronouns','gender','race','veteran','disability','hispanic'):selected=owner
    if r.family=='external_sharing' and not owner:
        return Decision(choice(field,['No']),'policy:external-sharing-opt-out',reason='No purpose-specific permission; do not opt in to external sharing')
    if r.family in ('government_referral','government_relationship','ethics_ack','clearance_eligibility','arbitration','read_ack','event_history','account_status','privacy_consent','future_contact','sms_consent','ai_consent','demographic_consent','ai_policy_ack') and not owner:
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
            state=[x for x in facts if x.id=='owner.residence.state'] or [x for x in facts if x.authority=='owner' and x.id.endswith('/state') and '/Current address/' in x.id and not x.section]
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
    if (r.family in {'gender','race','veteran','disability','hispanic'} and len(scalar)>1 and
            all(x.authority=='owner' and not x.instructions and forms.is_disclosure_refusal(x.value) for x in scalar)):
        # Several same-purpose sources may share an explicit privacy preference
        # (e.g. gender and transgender identity). This is not factual inference.
        result=from_fact(field,scalar[0])
        return Decision(result.value,'; '.join(x.source for x in scalar),','.join(x.id for x in scalar),
                        'all same-purpose owner declarations explicitly decline disclosure')
    if len(scalar)==1:
        fact=scalar[0]
        if r.family=='email' and re.fullmatch(r"On portals with an account the form shows the account's email \(the agent's Gmail\): keep that\.",fact.instructions):
            # A resume parser can prefill an unrelated/stale email. Presence
            # alone does not establish account identity or owner approval.
            # Retain only an explicit owner email/variant; otherwise use the
            # saved address rather than relabelling UI data as owner evidence.
            prefill=field.value.strip()
            approved=[fact.value,*fact.variants]
            value=prefill if any(prefill.casefold()==str(v).strip().casefold() for v in approved) else fact.value
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
        if r.family in ('government_referral','government_relationship','ethics_ack','school','degree','discipline','current_employer','current_title','country','on_site','offer_deadline','portfolio_link','pronouns','languages','gender','race','veteran','disability','hispanic','experience_years','clearance_eligibility','clearance_held','start_date','signature_date','email','phone','preferred_name','legal_name','location','gpa','work_authorization','external_sharing','privacy_consent','future_contact','sms_consent','ai_consent','demographic_consent','ai_policy_ack','arbitration','read_ack','salary'):
            return from_fact(field,fact)
    return None


def evidence_allows(field,fact,route_info):
    if scope_allows(field,fact):return True
    if (fact.target_key is None and not entity_scope(field.section) and fact.authority in ('owner','resume') and
            (entity_scope(fact.section) or ('',))[0]=='employment' and
            route_info.family in ('current_title','current_employer') and fact.id in route_info.fact_ids):
        return True  # selected from explicit current markers or unique sourced end dates
    # Explicitly named global degree questions are the sole cross-row
    # projection here. Their candidates were selected from that degree above.
    return bool(fact.target_key is None and not entity_scope(field.section) and fact.authority=='owner' and
        (entity_scope(fact.section) or ('',))[0]=='education' and route_info.family in {'degree','discipline','education_level'} and
        degree_level(field.label) is not None and fact.id in route_info.fact_ids)


def binding_valid(field,bound,value,route_info):
    if any(x.id not in route_info.fact_ids for x in bound):return False
    if not all(evidence_allows(field,x,route_info) for x in bound):return False
    if route_info.family=='why_company' and field.kind in ('text','textarea') and len(re.findall(r'\b\w+\b',value))<5:
        return False
    if (route_info.family=='other' and norm(value) in ('yes','no','true','false') and
            not any(norm(field.label) in map(norm,x.aliases) for x in bound) and
            any(fact_family(x)!='other' for x in bound)):
        return False  # an unclassified predicate cannot borrow a typed neighbour's boolean
    protected=PURPOSE_BOUND | {'external_sharing','clearance_eligibility','clearance_held','arbitration','read_ack','privacy_consent','future_contact','sms_consent','ai_consent','demographic_consent','ai_policy_ack','sponsorship','work_authorization','residency'}
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
    if address is None and not ctx.get('_application_address'):
        from jobagent.tools import applicant
        address=next((entry.get('answer') for _,entry in applicant.entries(ctx.get('_applicant') or {})
                      if entry.get('question')=='Current address'),None)
    result=[]
    if isinstance(address,dict) and address.get('country'):
        result.append(Fact('owner.residence.country',['Country of residence','Current country of residence'],str(address['country']),'personal/address/country'))
    if isinstance(address,dict) and address.get('state'):
        result.append(Fact('owner.residence.state',['Actual residence state','State of residence'],str(address['state']),'personal/address/state'))
        if address.get('city'):
            result.append(Fact('owner.residence.city',['City of residence'],str(address['city']),'personal/address/city'))
            result.append(Fact('owner.residence.location',['Relocation origin'],f"{address['city']}, {address['state']}",'personal/address/city,state'))
    locations=(ctx.get('_jd') or {}).get('locations_resolved') or []
    if title:=(ctx.get('_jd') or {}).get('title'):
        result.append(Fact('jd.job.title',['Position Title'],str(title),'jd/title','Position Details',authority='document'))
    stated={(x.get('city'),x.get('state')) for x in locations if x.get('city') and x.get('state')}
    if len(stated)==1:
        city,state=next(iter(stated))
        result.append(Fact('jd.job.location',['Job location'],f'{city}, {state}','jd/locations_resolved',authority='document'))
    return result


def review_metadata_facts(view):
    """Bind the server's original application date, never learned employment dates.

    Args:
        view: The current form with original date defaults from its own controls.

    Returns:
        Three field-bound facts for a complete valid date, or no date evidence.
    """
    from datetime import datetime
    if view is None:return []
    fields=[f for f in view.fields if field_family(f)=='application_date']
    parts={norm(f.label).rsplit(' ',1)[-1]:f for f in fields}
    if len(fields)!=3 or set(parts)!={'month','day','year'} or len({f.frame for f in fields})!=1:return []
    if any(f.initial_value is None for f in fields):return []
    raw=' '.join(parts[p].initial_value for p in ('month','day','year'))
    for fmt in ('%b %d %Y','%B %d %Y','%m %d %Y'):
        try:datetime.strptime(raw,fmt);break
        except ValueError:continue
    else:return []
    return [Fact('portal.application_date.'+part,[f.label],f.initial_value,'portal/application-date/default',
                 f.section,authority='portal',target_key=key(f)) for part,f in parts.items()]


def unlisted_school(field,view,facts):
    target='school' if re.search(r'(?i)did not see.*(?:university|school)|school (?:is )?not listed',field.label) else (
        'discipline' if re.search(r'(?i)(?:field of study|major) is not listed',field.label) else None)
    if target is None:return None
    names=[];ids=[]
    for control in view.fields:
        if family(control.label)!=target or control.kind not in ('select','combobox') or not entity_scope(control.section):continue
        if entity_scope(field.section) and entity_scope(control.section)!=entity_scope(field.section):continue
        if not re.match(r'(?i)^other\b',control.value):continue
        selected=[x for x in facts if x.section==control.section and not x.record and fact_family(x)==target]
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
