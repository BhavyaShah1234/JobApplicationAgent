"""Evidence-bound resolution: models may select facts, never manufacture factual values."""
from dataclasses import dataclass, field, replace
import re
from datetime import datetime,timedelta
from jobagent.tools import forms

EMPLOYMENT_MONTH_POLICY = 'Month-only employment dates'
FIRST_DAY_POLICY = 'first_day_of_month'


def norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s).lower()).strip()


def expanded_immigration_support(text):
    q=norm(text)
    if not re.search(r'\bsponsor\w*\b|\bimmigration (?:related )?support\b',q):return False
    if (re.search(r'(?<!not )\b(?:include|includes|count|counts)\b.{0,180}\b(?:opt|cpt)\b',q) or
            re.search(r'\b(?:opt|cpt)\b.{0,50}(?<!not )\bcounts?\b',q)):
        return True
    # An affirmative parenthetical example list defines the scope too. Do not
    # turn an explicitly excluded OPT example into an expanded requirement.
    for example in re.findall(r'\([^()]{1,600}\)',str(text)):
        example=norm(example)
        if (re.match(r'^(?:e g|eg|for example|such as)\b',example) and
                re.search(r'\b(?:opt|cpt)\b',example) and
                not re.search(r'\b(?:not|never|except|exclud\w*)\b',example)):
            return True
    return False


@dataclass
class Fact:
    id: str
    aliases: list[str]
    value: str
    source: str
    section: str = ''
    variants: list[str] = field(default_factory=list)
    instructions: str = ''
    record: bool = False
    tools: list[str] = field(default_factory=list)
    reference_date: str = ''
    authority: str = 'owner'
    target_key: tuple | None = None
    date_policy: str = ''
    policy_fact_id: str = ''
    policy_context: str = ''


@dataclass
class Decision:
    value: str | None
    source: str = ''
    fact_id: str = ''
    reason: str = ''


def key(f):
    return (getattr(f,'frame_identity','') or f.frame, getattr(f, 'identity', '') or (f.section, f.label, f.slot), f.kind,
            '' if f.kind=='file' else norm(f.label), entity_scope(f.section))


def owner_only(f):
    """A resume establishes experience, not preferences, consent or sensitive history."""
    return bool(re.search(r'(?i)enjoy|prefer|desired|would you like|consent|agree|acknowledg|convict|felony|misdemeanor|'
                          r'(?:expect|desired).{0,30}(?:salary|compensation|pay)|(?:salary|compensation|pay).{0,30}(?:expect|desired)|'
                          r'pronouns|currently.*client|(?:opened|have) an? account|conferences?.*(?:attend|planning)|gatherings.*attend|citizen|sponsor|disab|medical|dismiss|religio|faith|gender|sexual|ethnic|\brace\b|birth|social security|\bssn\b|passport|clearance|resident|^legal name$|^full name$|^name$|\bemail\b|\bphone\b|share.*(?:external|partner)' , f.label))


ENTITY_HEADER = re.compile(r'^(?:(?:previous|prior|past)\s*)?(work\s*experience|employment(?:\s*history)?|professional\s*experience|education(?:\s*(?:history|details|information))?|academic(?:\s*(?:history|qualifications))?|references?|referrals?)\s*(?:entry\s*)?(\d+)?$')
ENTITY_COMPONENTS = {
    'employer','employer name','name of employer','company','company name','title','job title','your job title','position title',
    'school','university','institute','school or university','school institution name','other school institution name',
    'degree','major','field of study','major area of study','other major area of study','did you graduate',
    'gpa','g p a','grade point average','overall result gpa',
    'from date','start date','end date','from year','start year','end year',
    'first name','given name','middle name','last name','family name','surname','full name','name',
    'email','email address','phone','phone number','primary phone','telephone','mobile',
    'phone country','phone country code','country phone code','dial code','phone device type','phone extension',
    'skills','technical skills',
    'address','street address','address line 1','address line 2','city','town','state','province',
    'location','current location','current location city',
    'region','country','zip','zip code','postal code','post code','pincode','language','languages','years of relevant experience','years of experience',
}


def entity_scope(section):
    match=ENTITY_HEADER.fullmatch(norm(section))
    if not match:return None
    name=match[1]
    group='education' if name.startswith(('education','academic')) else 'reference' if name.startswith('reference') else 'referral' if name.startswith('referral') else 'employment'
    return group, int(match[2]) if match[2] else None


LOOSE_EDUCATION=re.compile(r'\b(education|school|university|degree|academic)\b')
LOOSE_EMPLOYMENT=re.compile(r'\b(employers?|employment|work history|work experience|previous jobs?)\b')
ADDRESS_PARTS={'city','state','country','zip','zip code','postal code','county','province','address','street address'}


def loose_entity_scope(section):
    """The school or employer a sentence-style section heading is about ("Please enter the highest level of education you have attained",
    "Please enter your last three employers"), where there is no numbered "Education 2" to read a row from; None for any other section."""
    q=norm(section or '')
    if entity_scope(section):return None
    if LOOSE_EDUCATION.search(q):return 'education'
    if LOOSE_EMPLOYMENT.search(q):return 'employment'
    return None


def scope_allows(f, fact):
    if fact.target_key is not None and fact.target_key!=key(f):return False
    target=entity_scope(f.section)
    if fact.section:
        origin=entity_scope(fact.section)
        if target and origin:
            return target[0]==origin[0] and (target[1] or 1)==(origin[1] or 1)
        if origin and not target:
            # Explicit global questions can read a source record outside a UI row.
            q=norm(f.label)
            if origin[0]=='education' and re.search(r'(currently attending|last attend)',q):return origin[1]==1
            if origin[0]=='employment' and re.search(r'(current|previous).*(employer|company|job title)',q):return origin[1]==1
        return norm(f.section)==norm(fact.section)
    # A general contact answer is not evidence for an employer, school or reference's contact details.
    if target and (norm(f.label) in ENTITY_COMPONENTS or any(norm(a) in ENTITY_COMPONENTS for a in fact.aliases)):
        return False
    # The same holds in a sentence-style education or employer section: the candidate's current address (or the job's city that
    # replaces it for this application) is not the school's or the employer's city, state or country.
    if loose_entity_scope(f.section) and (norm(f.label) in ENTITY_COMPONENTS or any(norm(a) in ENTITY_COMPONENTS for a in fact.aliases)):
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
    if f.options:
        f = replace(f, options=[o for o in f.options if not forms._NO_OPTIONS.fullmatch(o.strip())])
    if forms.PHONE_CODE_Q.search(f.label):
        specific = [v for v in values if re.search(r'[A-Za-z].*\+\d', v)]
        for value in specific or values:
            if hit := forms.phone_code_option(value, f.options):
                return hit
        if f.kind == 'combobox' and f.options_state != 'complete':
            # Prefer the country+prefix derived from this phone record over an
            # ambiguous prefix when a lazy picker needs a search string.
            return specific[0] if specific else values[0]
        return None
    if f.kind == 'combobox' and not f.options and f.options_state != 'complete':
        # Keep the known source value as a SEARCH, not a fabricated selection.
        # fill_field re-applies these rules to the real options once they load.
        return '; '.join(forms.selection_parts(values[0],f.label)) if f.multiple else values[0]
    if f.kind=='checkboxes' or f.multiple:
        parts=forms.selection_parts(values[0],f.label)
        values=['; '.join(parts),*values[1:]]
        picks=[forms.strict_option(x,f.options) for x in parts]
        if parts and all(x is not None for x in picks):return '; '.join(picks)
    if 'pronoun' in norm(f.label):
        equivalents={'he him his':'he him','she her hers':'she her','they them theirs':'they them'}
        target=equivalents.get(norm(values[0]))
        hits=[o for o in f.options if norm(o)==target] if target else []
        if len(hits)==1:return hits[0]
    if 'gender' in norm(f.label) and norm(values[0]) in ('male','female'):
        target={'male':'man','female':'woman'}[norm(values[0])]
        hits=[o for o in f.options if norm(o)==target]
        if len(hits)==1:return hits[0]
    if re.search(r'\brace\b|ethnic',norm(f.label)) and norm(values[0])=='asian':
        broad=[o for o in f.options if norm(o)=='asian']
        if len(broad)==1:return broad[0]
        # These suffixes identify a form's country, not an Asian subregion.
        # Do not treat parentheticals such as "South Asian" as equivalent.
        qualified=[o for o in f.options if re.fullmatch(
            r'Asian\s*\((?:United States(?: of America)?|U\.?S\.?(?:A\.?)?|Canada|United Kingdom|UK|Australia|New Zealand)\)',o,re.I)]
        if len(qualified)==1:return qualified[0]
        if len(qualified)>1:return None
        if f.options_state!='complete':
            return values[0] if f.kind=='combobox' else None
        # Owner decision 2026-11-24: a form that splits Asian by region gets the decline option, else 'Other'; never a region chosen for the candidate.
        decline=[o for o in f.options if re.search(r"(?i)don'?t wish|do not wish|prefer not|decline|don'?t want to self|choose not|not (?:wish|want) to (?:answer|disclose|self)",o)]
        if len(decline)==1:return decline[0]
        other=[o for o in f.options if norm(o)=='other']
        return other[0] if len(other)==1 else None
    if 'veteran' in norm(f.label) and norm(values[0]) in ('not a veteran','not veteran'):
        if f.kind not in ('select','combobox','radio','buttons','checkboxes'):return values[0]
        binary=[o for o in f.options if norm(o)=='no']
        if len(binary)==1:return binary[0]
        # Non-veteran and non-protected veteran are different statuses. Prefer
        # the explicit non-veteran answer and never affirm service by accident.
        prefix=r'(?:(?:i am|i m|i identify as) )?'
        hits=[o for o in f.options if re.fullmatch(prefix+r'not (?:a )?veteran|non veteran',norm(o))]
        if len(hits)==1:return hits[0]
        hits=[o for o in f.options if re.fullmatch(prefix+r'not (?:a )?protected veteran',norm(o))]
        if len(hits)==1:return hits[0]
        if f.kind=='combobox' and not f.options and f.options_state!='complete':return values[0]
        return None
    if re.search(r'(?i)disability',f.label) and (norm(values[0]) == 'no' or norm(values[0]).startswith('no ')):
        hits=[o for o in f.options if norm(o) == 'no' or re.match(
            r'^(?:no )?(?:i )?(?:do not|don t) have (?:a )?disability\b', norm(o))]
        if len(hits)==1:return hits[0]
    # Quarter choices denote a date interval, not a literal spelling of the
    # owner's available date. Select only the unique interval containing it.
    if re.search(r'(?i)start|availab',f.label) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',values[0]):
        try:
            date=datetime.strptime(values[0],'%Y-%m-%d')
            hits=[o for o in f.options if (m:=re.search(r'(?i)\bQ([1-4])\s+(\d{4})\b',o)) and int(m[2])==date.year and int(m[1])==(date.month-1)//3+1]
            if len(hits)==1:return hits[0]
        except ValueError:pass
    for value in values:
        if forms.is_identity(f.label):
            if f.options_state!='complete' and forms.OTHER_OPTION.match(value) and any(not forms.OTHER_OPTION.match(v) for v in values):continue
            hit=forms.literal_identity_option(value,f.options,person=bool(forms.PERSON_Q.search(f.label)))
        else:hit = forms.strict_option(value, f.options)
        if hit is not None:
            return hit
    for value in values:
        if norm(value) in ('yes','no'):
            hits=[o for o in f.options if re.match(r'^'+norm(value)+r'\b',norm(o))]
            if len(hits)==1:return hits[0]
        if not forms.is_identity(f.label):
            # Country phone selectors decorate the same country with its dial code.
            hits=[o for o in f.options if norm(re.sub(r'\s*\(?\+\d+\)?\s*$', '', o))==norm(value)]
            if len(hits)==1:return hits[0]
        for canonical,alternates in forms._SYNONYMS.items():
            group={norm(x) for x in (canonical,*alternates)}
            if norm(value) in group:
                hits=[o for o in f.options if norm(o) in group]
                if len(hits)==1:return hits[0]
    if not forms.is_identity(f.label) and re.search(r'(?i)gpa|grade point|years?',f.label) and re.fullmatch(r'\d+(?:\.\d+)?',values[0]):
        number=float(values[0]);hits=[]
        for option in f.options:
            span=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*',option)
            if span and float(span[1])<=number<=float(span[2]):hits.append(option)
            elif (lower:=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s+(?:or|and)\s+(?:above|higher|more)\s*',option,re.I)) and number>=float(lower[1]):hits.append(option)
            elif (upper:=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s+(?:or|and)\s+(?:below|lower|less)\s*',option,re.I)) and number<=float(upper[1]):hits.append(option)
        if len(hits)==1:return hits[0]
    target=percent_range(values[0])
    if target:
        hits=[o for o in f.options if percent_range(o)==target or
              (target[0]==target[1] and percent_range(o) and percent_range(o)[0]<=target[0]<=percent_range(o)[1])]
        if len(hits)==1:return hits[0]
    # Searchable pickers must search the actual identity, not pick the closest initial result.
    if f.kind == 'combobox' and f.options_state != 'complete' and (not f.options or f.options_state in ('partial','search_required','stale')):
        return values[0]
    return None


def declaration_notes_only(instructions):
    """Recognize confirmation provenance and no-invention cautions, not answer rules.

    Only a closed set of non-operative clauses can accompany literal answers.
    Conditions, output directives and unknown prose still require rule validation.
    The original instructions remain on the fact for audit and model context.
    """
    if not instructions:return False
    clauses=[x.strip() for x in re.split(r'[.;\n]+',instructions) if x.strip()]
    confirmation=r'Owner(?:[ -])(?:has )?confirmed'
    reference=r'use (?:this|the) (?:shorter|confirmed|saved|provided) (?:name|value|answer)'
    if not clauses or not any(re.match(r'(?i)^'+confirmation+r'\b',x) for x in clauses):return False
    operative=' '.join(x for x in clauses if not re.fullmatch(reference,x,re.I))
    if re.search(r'(?i)\b(?:if|unless|except|otherwise|instead|until|when|must|use|enter|answer|select|choose|leave|change|replace)\b',operative):return False
    return all(re.fullmatch(r'(?i)(?:'+confirmation+r'\s+.+|Do not (?:invent|guess|fabricate)\s+.+'
                            r'|'+reference+r')',x)
               for x in clauses)


def date_component(f):
    """Identify a split employment date part from its own control label."""
    label = norm(re.sub(r'\([^)]*\)', '', f.label))
    match = re.fullmatch(r'(start|from|end|to)(?: date)? (month|day|year)', label)
    return ('end' if match[1] in ('end', 'to') else 'start', match[2]) if match else None


def from_fact(f, fact):
    if declaration_notes_only(fact.instructions):
        from dataclasses import replace
        return from_fact(f,replace(fact,instructions=''))
    if fact.instructions:
        if fact.reference_date and re.search(r"(?i)today(?:'s| s)?",fact.instructions) and not re.search(r'(?i)\bif\b|\bunless\b',fact.instructions):
            return from_fact(f,Fact(fact.id,fact.aliases,fact.reference_date,fact.source+'; clock '+fact.reference_date,fact.section,fact.variants,authority=fact.authority))
        weekday=re.search(r'(?i)\b(?:coming|next)\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b',fact.instructions)
        if weekday and fact.reference_date and not re.search(r'(?i)\bif\b|\bunless\b',fact.instructions):
            if f.kind in ('select','combobox','radio','buttons','checkboxes') and not f.calendar and re.search(r'(?i)\bin a list\b.*\bimmediately\b',fact.instructions):
                rendered=choice(f,['immediately'])
                if rendered is not None:return Decision(rendered,fact.source,fact.id,'explicit list fallback in owner rule')
            today=datetime.strptime(fact.reference_date,'%Y-%m-%d')
            day=['monday','tuesday','wednesday','thursday','friday','saturday','sunday'].index(weekday[1].lower())
            value=(today+timedelta(days=(day-today.weekday())%7 or 7)).strftime('%Y-%m-%d')
            return from_fact(f,Fact(fact.id,fact.aliases,value,fact.source+'; clock '+fact.reference_date,fact.section,fact.variants))
        # Only unconditional literal rules can bypass the source-aware planner.
        literal=re.fullmatch(r'(?is)\s*(?:use|enter|answer)\s+["\']?([^\n]+?)["\']?\.?\s*', fact.instructions)
        clear=re.fullmatch(r'(?is)\s*(?:leave|keep)\s+(?:it\s+)?(?:blank|empty)\.?\s*', fact.instructions)
        if clear:return Decision('',fact.source,fact.id)
        if not literal or re.search(r'(?i)\b(if|unless|when|jd|resume|otherwise|posting)\b', literal[1]):
            return Decision(None,fact.source,fact.id,'conditional source rule requires validation')
        text=literal[1].strip().strip('"\'')
        quoted=bool(re.search(r'(?s)["\'].*["\']',fact.instructions))
        numeric=bool(re.fullmatch(r'[$£€]?\d+(?:[.,/]\d+)*(?:\s*[-–]\s*[$£€]?\d+(?:[.,/]\d+)*)?',text))
        if not (quoted or numeric or text==fact.value or forms.strict_option(text,f.options) is not None):
            return Decision(None,fact.source,fact.id,'source rule requires validation')
        fact=Fact(fact.id,fact.aliases,text,fact.source,fact.section,fact.variants)
    component = date_component(f)
    if component:
        boundary, part = component
        aliases = {'End Date'} if boundary == 'end' else {'Start Date', 'From Date'}
        if not aliases.intersection(fact.aliases):
            return Decision(None, reason='wrong source date boundary')
        if boundary == 'end' and norm(fact.value) in ('present','current','ongoing'):
            return Decision('' if not f.required else None, fact.source, fact.id, 'ongoing employment has no end date')
        parsed, precision = None, ''
        for fmt, granularity in [('%Y-%m-%d','day'),('%m/%d/%Y','day'),('%B %d, %Y','day'),
                                 ('%B %Y','month'),('%b %Y','month'),('%Y-%m','month'),('%m/%Y','month'),('%Y','year')]:
            try:
                parsed, precision = datetime.strptime(fact.value,fmt), granularity
                break
            except ValueError:pass
        if parsed is None or part == 'month' and precision == 'year':
            return Decision(None, fact.source, fact.id, 'date component missing from source')
        source, ident = fact.source, fact.id
        if part == 'day' and precision != 'day':
            if precision != 'month' or fact.date_policy != FIRST_DAY_POLICY or not fact.policy_fact_id:
                return Decision(None, source, ident, 'exact day missing from source')
            source += '; owner-approved first-day approximation: ' + fact.policy_fact_id
            ident += ',' + fact.policy_fact_id
        values = ([parsed.strftime('%B'), parsed.strftime('%b'), str(parsed.month), f'{parsed.month:02d}'] if part == 'month'
                  else [str(parsed.day), f'{parsed.day:02d}'] if part == 'day' else [str(parsed.year)])
        return Decision(choice(f,values), source, ident, 'date component from the same source role')
    value=choice(f,[fact.value,*fact.variants])
    rendered_source,rendered_ids=fact.source,fact.id
    format_hint=(f.format or '').upper()
    needs_day=f.kind=='date' or 'DD' in format_hint
    needs_month_year='MM' in format_hint and ('YY' in format_hint)
    if (needs_day or needs_month_year or format_hint in ('YYYY','YY')) and not f.required and \
            (entity_scope(fact.section) or ('',))[0]=='employment' and 'End Date' in fact.aliases and \
            norm(fact.value) in {'present','current','ongoing'}:
        return Decision('',fact.source,fact.id,'ongoing employment has no end date')
    if needs_day or needs_month_year or format_hint in ('YYYY','YY'):
        parsed=None
        for fmt in ('%Y-%m-%d','%m/%d/%Y','%B %d, %Y'):
            try:parsed=datetime.strptime(fact.value,fmt);break
            except ValueError:pass
        if parsed is None and needs_day and fact.date_policy==FIRST_DAY_POLICY and fact.policy_fact_id and \
                (entity_scope(fact.section) or ('',))[0] in ('employment','education'):
            for fmt in ('%Y-%m','%m/%Y','%m-%Y','%Y/%m','%B %Y','%b %Y'):
                try:parsed=datetime.strptime(fact.value,fmt);break
                except ValueError:pass
            if parsed is not None:
                rendered_source+='; owner-approved first-day approximation: '+fact.policy_fact_id
                rendered_ids+=','+fact.policy_fact_id
        if parsed is None and not needs_day:
            for fmt in ('%Y-%m','%m/%Y','%B %Y','%b %Y','%Y'):
                try:parsed=datetime.strptime(fact.value,fmt);break
                except ValueError:pass
            if needs_month_year and re.fullmatch(r'\d{4}',fact.value):parsed=None
        if parsed is None:return Decision(None,fact.source,fact.id,'exact day missing from source' if needs_day else 'month/year unavailable')
        template='YYYY-MM-DD' if f.kind=='date' else format_hint
        tokens={'YYYY':f'{parsed.year:04d}','YY':f'{parsed.year%100:02d}','MM':f'{parsed.month:02d}','DD':f'{parsed.day:02d}'}
        value=re.sub(r'YYYY|YY|MM|DD',lambda m:tokens[m[0]],template)
    return Decision(value,rendered_source,rendered_ids,'' if value is not None else 'source value not offered')


def resolve(f, facts):
    if component := date_component(f):
        aliases = {'End Date'} if component[0] == 'end' else {'Start Date','From Date'}
        dates = [fact for fact in facts if not fact.record and aliases.intersection(fact.aliases) and scope_allows(f,fact)]
        if len(dates) == 1:
            return from_fact(f,dates[0])
        return Decision(None, reason='no unique source date for this row')
    label=re.sub(r'(?i)\((?:optional|if applicable)\)', '', f.label).strip()
    suffix=' '.join(f.options)
    if suffix and label.endswith(suffix):label=label[:-len(suffix)].strip()
    matches = [x for x in facts if not x.record and norm(label) in map(norm, x.aliases)
               and scope_allows(f,x)]
    sheets=[x for x in matches if x.id.startswith('application.answer.')]
    explicit_human=[x for x in matches if x.source=='human']
    expanded=expanded_immigration_support(f.label)
    if expanded and not any(x.authority=='owner' for x in matches):
        return Decision(None,reason='explicit owner evidence missing')
    # A combined sponsorship question is an OR, not a synonym of the current-
    # sponsorship question. A negative answer requires both timeframes to be No.
    q=norm(f.label)
    if not (expanded or sheets or explicit_human) and 'sponsor' in q and 'now' in q and 'future' in q and 'or' in q.split() and not re.search(r'\bnot\b|\bwithout\b',q):
        timed={'now':[],'future':[]}
        for fact in facts:
            name=norm(fact.aliases[0]) if fact.aliases else ''
            if fact.record or fact.instructions or 'sponsor' not in name:continue
            if not fact.source.startswith(('applicant','learned:','human')):continue
            if 'future' in name:timed['future'].append(fact)
            elif 'now' in name:timed['now'].append(fact)
        if all(len(timed[t])==1 and norm(timed[t][0].value) in ('yes','no','true','false') for t in timed):
            bound=[timed[t][0] for t in timed]
            value='Yes' if any(norm(x.value) in ('yes','true') for x in bound) else 'No'
            return Decision(choice(f,[value]),'; '.join(x.source for x in bound),','.join(x.id for x in bound),'current OR future sponsorship')
        return Decision(None,reason='missing sponsorship timeframe evidence')
    if sheets:matches=sheets
    human=[x for x in matches if x.source=='human']
    if len(human)==1:matches=human
    if len(matches)>1:
        profile=[x for x in matches if x.source.startswith('applicant/')]
        if profile:
            # An explicitly saved self-identification choice can refine a broad
            # profile category absent from this menu. Never derive the subgroup
            # from nationality, and never replace a contradictory declaration.
            refinement=[]
            if re.search(r'\brace\b|\bethnicity\b',norm(f.label)) and f.options and all(
                    not x.instructions and forms.strict_option(x.value,f.options) is None for x in profile):
                refinement=[x for x in matches if x.source.startswith('learned:') and not x.instructions and
                    forms.strict_option(x.value,f.options) is not None and all(
                        norm(x.value).startswith(norm(p.value)+' ') and norm(p.value) for p in profile)]
            matches=refinement or profile
        if len({(x.value,x.instructions,x.section) for x in matches})==1:matches=matches[:1]
    if len(matches) != 1:
        return Decision(None, reason='no unique source-backed mapping')
    fact = matches[0]
    return from_fact(f,fact)


def compile_facts(data, raw_jd, reference_date=''):
    """Keep complete source records and conditional rules available to the planner."""
    from jobagent.tools.applicant import entries
    facts = []
    component_aliases = {
        'Legal name': {'first': ['First Name', 'Given Name'], 'middle': ['Middle Name'], 'last': ['Last Name', 'Family Name']},
        'Current address': {'line1':['Address','Street Address','Address Line 1'],
                            'line2':['Address Line 2','Address 2','Apartment','Suite','Unit'],
                            'postal_code':['Zip Code','ZIP','Postal Code'], 'street': ['Address', 'Street Address', 'Address Line 1'], 'city': ['City'],
                            'state': ['State', 'Province', 'Province/State', 'State/Province', 'State or Province'],
                            'zip': ['Zip Code', 'ZIP', 'Postal Code'], 'country': ['Country', 'Country/Region', 'Country or Region']},
        'Preferred name': {'first': ['Preferred first name'], 'last': ['Preferred last name'],
                           'full': ['Preferred name', 'Preferred full name']},
        'Phone': {'number': ['Phone', 'Primary Phone', 'Phone Number'],
                  'extension': ['Phone Extension'], 'device_type': ['Phone Device Type'],
                  'country':['Phone Country'], 'country_code':['Phone Country Code','Country Phone Code','Dial Code']},
        'Visa status / citizenship': {'visa_status':['Visa status','Current visa status'],
                                      'citizenship':['Country of citizenship','Citizenship country','Nationality']},
    }
    for section, entry in entries(data):
        value = entry.get('answer'); q = entry['question']; source = f'applicant/{section}/{q}/answer'
        tools=entry.get('tools') or []
        if isinstance(tools,str):tools=[tools]
        tools=[str(x) for x in tools]
        if isinstance(value, dict):
            import json
            facts.append(Fact(source,[q],json.dumps(value,ensure_ascii=False),source,instructions=entry.get('instructions',''),record=True,tools=tools))
            if q=='Legal name' and value.get('first') and value.get('last'):
                full=' '.join(str(value[k]).strip() for k in ('first','middle','last') if value.get(k))
                facts.append(Fact(source+'/full',['Name','Full Name','Legal Name'],full,source))
            for component in value:
                aliases=component_aliases.get(q, {}).get(component,[f'{q} {component.replace("_"," ")}'])
                v=value[component]
                if isinstance(v,(str,bool,int,float)):
                    variants = []
                    if q == 'Phone' and component == 'country_code' and value.get('country'):
                        variants = [f"{value['country']} ({v})", str(value['country'])]
                    facts.append(Fact(source+'/'+component, aliases, str(v), source+'/'+component,
                                      variants=variants, instructions=entry.get('instructions',''),tools=tools))
        elif isinstance(value,list) and value and all(isinstance(x,dict) for x in value):
            for i,row in enumerate(value,1):
                group='Education' if q=='Degrees' else 'Work Experience' if q=='Work history' else 'Reference' if norm(q) in ('reference','references') else 'Referral' if norm(q) in ('referral','referrals') else ''
                row_section=f'{group} {i}' if group else ''
                aliases={'school':['School','University','Institute','School/Institution Name','School or University'], 'degree':['Degree'], 'field':['Field of Study','Major','Major/Area of Study'],
                         'from':['From Date','Start Date'], 'start':['From Date','Start Date'],
                         'end':['End Date'], 'to':['End Date'], 'company':['Company','Company Name','Employer','Employer Name','Name of Employer'], 'title':['Job Title','Position Title','Title','Your Job Title'],
                         'current':['Current','Currently work here','I currently work here','Current position'],
                         'description':['Description','Responsibilities','Role Description'], 'gpa':['GPA','G.P.A.','Grade Point Average','Overall Result (GPA)'],
                         'city':['City'], 'state':['State','Province','State/Province','Province/State','State or Province'], 'country':['Country'], 'region':['Region','World region'],
                         'first_name':['First Name','Given Name'], 'last_name':['Last Name','Family Name','Surname'], 'prefix':['Prefix','Title'],
                         'phone':['Phone','Phone Number','Business Phone','Mobile Phone','Work Phone'], 'email':['Email','Email Address']}
                import json
                facts.append(Fact(source+f'/{i-1}',[q],json.dumps(row,ensure_ascii=False),source+f'/{i-1}',row_section,
                                  instructions=entry.get('instructions',''),record=True,tools=tools))
                for component in row:
                    names=aliases.get(component,[component.replace('_',' ')])
                    v=row.get(component)
                    if isinstance(v,list) and component not in ('school','degree','field'):continue
                    variants=v if isinstance(v,list) else [v]
                    if variants and isinstance(variants[0],(str,int,float)):
                        facts.append(Fact(source+f'/{i-1}/{component}',names,str(variants[0]),source+f'/{i-1}/{component}',
                                          row_section,[str(x) for x in variants[1:]],instructions='' if re.match(r'(?i)one (?:education )?entry per',entry.get('instructions','')) else entry.get('instructions',''),tools=tools))
        elif isinstance(value,list) and (value or entry.get('instructions')):
            import json
            facts.append(Fact(source,[q,*entry.get('seen',[])],json.dumps(value,ensure_ascii=False),source,
                              instructions=entry.get('instructions',''),record=True,tools=tools))
            rule=re.fullmatch(r'(?i)the ones most relevant to the job description,\s*at most (\d+)\.?',
                              (entry.get('instructions') or '').strip()) if q=='Skills' else None
            if rule and 1<=int(rule[1])<=50 and value and all(isinstance(s,str) and s.strip() for s in value):
                # Rank only supplied skills; JD text can order them, never add
                # qualifications. Keep the full record alongside this bounded
                # projection so the transformation remains auditable.
                skills=list(dict.fromkeys(s.strip() for s in value))
                def mentions(skill):
                    return len(re.findall(r'(?<![\w+#])'+re.escape(skill)+r'(?![\w+#])',raw_jd,re.I))
                ranked=sorted(skills,key=lambda s:-mentions(s))[:int(rule[1])]
                facts.append(Fact(source+'/selected',[q,*entry.get('seen',[])],
                                  '; '.join(ranked),source+'/selected',tools=tools))
        elif isinstance(value, (str, bool, int, float)) or (value is None and entry.get('instructions')):
            # A saved literal can be conditional (e.g. "Yes only if the resume
            # lists an honour"). Let the common source checker apply that rule.
            val = ('Yes' if value else 'No') if isinstance(value, bool) else '' if value is None else str(value)
            # An empty default is unknown; an explicit clear rule is evidence of a blank.
            if val or entry.get('instructions'):
                facts.append(Fact(source, [q, *entry.get('seen', [])], val, source,instructions=entry.get('instructions',''),tools=tools))
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

    origins={f'applicant/{sec}/{e["question"]}/answer':
             'lookup' if e.get('source')=='application_address_lookup' else 'resume'
             for sec,e in entries(data) if e.get('source')=='application_address_lookup' or 'resume' in str(e.get('source','')).lower()}
    for fact in facts:
        fact.reference_date=reference_date
        fact.authority=next((origin for prefix,origin in origins.items() if fact.source.startswith(prefix)), 'owner')
    policies=[f for f in facts if f.authority=='owner' and not f.record and
              EMPLOYMENT_MONTH_POLICY in f.aliases]
    if policies and {p.value for p in policies}=={FIRST_DAY_POLICY}:
        for fact in facts:
            if (entity_scope(fact.section) or ('',))[0] in ('employment','education') and not fact.record and \
                    any(a in ('From Date','Start Date','End Date') for a in fact.aliases):
                fact.date_policy=FIRST_DAY_POLICY;fact.policy_fact_id=policies[0].id
    return facts
