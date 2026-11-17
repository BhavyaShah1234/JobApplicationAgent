"""Hard eligibility requirements in a posting, checked against what the candidate's own file says, BEFORE any work is spent (step 29).

A posting that requires U.S. citizenship, an active security clearance, or no visa sponsorship is a near-certain rejection for a
candidate who is not a citizen, holds no clearance, or needs sponsorship (Pantex, 2026-11-14: submitted 11-13, "not selected" the next
day, for a role that required citizenship). Nothing here invents a fact: the candidate's file is the only source, an UNKNOWN fact
never blocks (it asks), and a held clearance or citizenship is never assumed. The result is advice with the quoted sentence; the
candidate may override it for one posting (`--ignore-eligibility`), and the queue never does.
"""

import re
import hashlib
import json
from pathlib import Path
from dataclasses import dataclass, field

# (requirement, pattern). Each pattern is matched against sentences of the posting; the matching sentence is quoted back.
CITIZENSHIP = re.compile(
    r"(?i)(must be (a |an )?(u\.?s\.?|united states|american)\s+citizen|(u\.?s\.?|united states)\s+citizens?(hip)?\s+(is |are )?"
    r"(required|a must|only)|requires?\s+(u\.?s\.?|united states)\s+citizenship|citizenship\s+(is\s+)?required|"
    r"only\s+(u\.?s\.?|united states)\s+citizens|(u\.?s\.?)\s+citizens?\s+only|must\s+be\s+a\s+citizen\s+of\s+the\s+united states|"
    r"(u\.?s\.?|united states)\s+citizenship\s+is\s+(a\s+)?(requirement|condition)|"
    r"(u\.?s\.?|united states)\s+citizen(ship|s)?\b[^.;]{0,60}\b(is|are)\s+(required|needed|mandatory))")
CLEARANCE = re.compile(
    r"(?i)(ts\s*/\s*sci|top[- ]secret|secret\s+clearance|(active|current|valid|dod|government|federal)\s+(security\s+)?clearance|"
    r"(obtain|hold|maintain|possess|have)\s+(and\s+maintain\s+)?(a\s+|an\s+)?(u\.?s\.?\s+)?(government\s+)?(security\s+)?clearance|"
    r"security\s+clearance\s+(is\s+)?(required|needed)|q\s+clearance|dod\s+secret|public trust\s+(clearance|position)|"
    r"without\s+(a\s+)?(security\s+)?clearance)")
NO_SPONSORSHIP = re.compile(
    r"(?i)\b(unable|not\s+able|cannot|can'?t|will\s+not|won'?t|does\s+not|do\s+not|not\s+in\s+a\s+position)\s+(to\s+)?sponsor\b|(will\s+not|won'?t|unable\s+to|cannot|can'?t|does\s+not|do\s+not|not\s+able\s+to|not\s+offer|no)\s+(provide\s+|offer\s+)?"
    r"(visa\s+|immigration\s+|employment\s+)?sponsorship|without\s+(the\s+need\s+for\s+)?(visa\s+|immigration\s+)?sponsorship|"
    r"not\s+(currently\s+)?(sponsoring|eligible\s+for\s+sponsorship)|sponsorship\s+is\s+not\s+(available|offered|provided)")
NO_IMMIGRATION_SUPPORT = re.compile(
    r"(?i)\b(?:will\s+not|cannot|can't|won't|does\s+not|do\s+not|unable\s+to)\s+"
    r"(?:(?:offer|provide)\s+(?:any\s+)?immigration(?:[-\s]+related)?\s+support|"
    r"sponsor\b[^.;\n]{0,120}\b(?:or|nor)\s+(?:offer|provide)\s+(?:any\s+)?immigration(?:[-\s]+related)?\s+support)")
US_PERSON = re.compile(r"(?i)(\bITAR\b|export[- ]controlled|export\s+control\s+(laws|regulations)|\bU\.?S\.?\s+persons?\b)")
# An either/or is only an alternative when "or" joins the two ("citizen or permanent resident"); "and" makes it a second requirement.
ALTERNATIVE = re.compile(r"(?i)(citizens?(hip)?|national)\b(?:(?!\band\b)[^.;]){0,40}\bor\b[^.;]{0,40}(?P<alt>permanent\s+resident|green\s*card|lawful\s+permanent|"
                         r"authori[sz]ed\s+to\s+work|work\s+authori[sz]ation|authori[sz]ation\s+to\s+work)")
# a requirement that is absent or only wished for is not a requirement
NO_CLEARANCE_NEEDED = re.compile(r"(?i)(\bno\s+(active\s+|current\s+|valid\s+)?(security\s+)?clearance|without\s+(a\s+)?(security\s+)?clearance|clearance\s+(is\s+)?(not|never)\s+(required|needed)|"
                                 r"not\s+require\s+(a\s+)?(security\s+)?clearance|clearance\s+is\s+not\s+necessary)")
EXCLUDES = re.compile(r"(?i)(not\s+eligible|ineligible|cannot\s+(be\s+considered|apply)|(will|can)\s+not\s+be\s+considered|not\s+(be\s+)?considered|disqualif)")
LAPSED = re.compile(r"(?i)\b(expired|lapsed|inactive|former|formerly|pending|interim|revoked|suspended|in\s+process)\b")
CLAUSE_SPLIT = re.compile(r"(?i);\s*|,\s+(?:and|but)\s+|\s+(?:and|but)\s+(?=(?:an?\s+|the\s+)?(?:active|current|valid|u\.?s\.?\s|us\s|security|clearance|ts\b|top\b|secret\b|visa|"
                          r"sponsorship|we\b|you\b|candidates\b|applicants\b|no\b))")
PREFERRED = re.compile(r"(?i)\b(preferred|a plus|nice to have|desired|desirable|bonus|advantage|an asset|ideally)\b")
REQUIRED = re.compile(r"(?i)\b(must|required|requires?|requirement|mandatory|necessary|only)\b")
# "able to obtain" is a requirement the employer can usually satisfy by sponsoring the investigation; a held / active one is not
OBTAINABLE = re.compile(r"(?i)\b(able|ability|eligible|eligibility|willing|willingness)\s+(to\s+)?(obtain|be\s+granted|get|acquire|apply)|"
                        r"\bobtain\s+(and\s+maintain\s+)?a\b|\bcan\s+obtain\b|eligible\s+for\s+(a\s+)?(security\s+)?clearance")
ACTIVE = re.compile(r"(?i)\b(active|current|existing|currently\s+hold|must\s+(currently\s+)?hold)\b")
LEVELS = (("public trust", 1), ("confidential", 1), ("secret", 2), ("q clearance", 3), ("top secret", 3), ("ts/sci", 4), ("ts / sci", 4), ("sci", 4))


def clearance_level(text: str) -> int | None:
    """Rank of the highest clearance named in `text` (public trust/confidential 1, secret 2, top secret 3, TS/SCI 4); None when unnamed."""
    low = (text or "").lower()
    hits = [rank for name, rank in LEVELS if name in low]
    return max(hits) if hits else None


@dataclass
class Verdict:
    level: str = "ok"                                     # ok | review | blocked
    reasons: list = field(default_factory=list)           # [{"requirement", "quote", "why"}]

    def text(self) -> str:
        return "; ".join(f"{r['requirement']}: {r['why']} (\"{r['quote'][:140]}\")" for r in self.reasons)


def assess_job(job_dir: Path) -> Verdict:
    """Current profile plus explicit answers owned by this posting, never generated resume claims."""
    from jobagent.tools import applicant
    from jobagent.filling.routing import answer_sheet_facts
    from jobagent.filling.facts import expanded_immigration_support, norm
    job_dir=Path(job_dir);facts=applicant.eligibility_facts()
    sheet=job_dir/'application'/'answers.md'
    if sheet.exists():
        declarations=[x.value for x in answer_sheet_facts(sheet.read_text()) if not x.record and
                      any(expanded_immigration_support(a) for a in x.aliases)]
        values={norm(x) for x in declarations}
        if values:
            facts['needs_immigration_support']=True if values<={'yes','true'} else False if values<={'no','false'} else None
    text=job_dir/'jd.txt'
    return screen(text.read_text() if text.exists() else '',facts)


def review_blockers(job_dir: Path, verdict: Verdict | None = None) -> list[str]:
    """Recheck at review/approval; an explicit override belongs to this exact posting text."""
    job_dir=Path(job_dir);text=job_dir/'jd.txt'
    if not text.exists():return []
    verdict=verdict or assess_job(job_dir)
    if verdict.level=='ok':return []
    saved=job_dir/'eligibility.json'
    try:override=json.loads(saved.read_text())
    except (OSError,ValueError):override={}
    if isinstance(override,dict) and override.get('ignored') is True and override.get('jd_sha256')==hashlib.sha256(text.read_bytes()).hexdigest():
        return []
    return [f'eligibility {verdict.level}: {verdict.text()}; review the quoted requirement before submission']


def _sentences(text: str) -> list[tuple[str, str]]:
    """[(sentence as written, the same with the dots of "U.S." removed, for matching)]. The dots of "U.S." must not end a sentence."""
    protected = re.sub(r"\b([Uu])\.([Ss])\.", "\\1\u2024\\2\u2024", text or "")
    out = []
    for s in re.split(r"(?<=[.!?\n])\s+|\n+|[\u2022\u00b7]", protected):
        if s and len(s.strip()) > 8:
            out.append((s.strip().replace("\u2024", "."), s.strip().replace("\u2024", "")))
    clauses = []
    shared = re.compile(r"(?i)\b(are|is)\s+(required|needed|mandatory)\W*$")
    for quote, s in out:                              # independent clauses are judged on their own ("... required and ... preferred")
        parts = [c.strip() for c in CLAUSE_SPLIT.split(s) if c and len(c.strip()) > 8]
        if len(parts) > 1 and (m := shared.search(parts[-1])):
            # "X and Y are required": the closing predicate belongs to the clauses before it that have none of their own
            parts = [c if (REQUIRED.search(c) or PREFERRED.search(c)) else f"{c} {m.group(0)}" for c in parts[:-1]] + [parts[-1]]
        clauses += [(quote, c) for c in parts]
    return clauses


def screen(text: str, facts: dict) -> Verdict:
    """Compare the posting's hard requirements with the candidate's `facts` (`applicant.eligibility_facts`).

    blocked: a requirement the candidate's own file says they do not meet (not a citizen, no clearance, needs sponsorship).
    review: a requirement the file does not settle (citizenship or sponsorship never filled in), or an export-control statement that
    may or may not exclude them. ok: nothing found, or the file satisfies it."""
    v = Verdict()
    citizen, clearance, sponsor, authorized = (facts.get(k) for k in ("us_citizen", "clearance", "needs_sponsorship", "authorized"))
    permanent = bool(facts.get("permanent_resident"))

    def add(level: str, requirement: str, quote: str, why: str) -> None:
        v.reasons.append({"requirement": requirement, "quote": quote, "why": why})
        if level == "blocked" or v.level == "ok":
            v.level = level

    for quote, s in _sentences(text):
        if CITIZENSHIP.search(s):
            alt = ALTERNATIVE.search(s)
            via_permanent = bool(alt and re.search(r"(?i)permanent|green|lawful", alt.group("alt")))
            via_authorized = bool(alt and not via_permanent)
            if citizen is True or (via_permanent and permanent) or (via_authorized and authorized is True):
                pass                                      # met, or the posting itself offers an alternative the candidate meets; the rest of the clause is still read
            elif citizen is False and not alt:
                add("blocked", "U.S. citizenship", quote, "required, and your file says you are not a U.S. citizen")
            elif citizen is False:
                add("review", "U.S. citizenship", quote, "required or an alternative (permanent residence / work authorization): check you qualify")
            else:
                add("review", "U.S. citizenship", quote, "required; your file does not say whether you are a U.S. citizen")
        if CLEARANCE.search(s) and (not NO_CLEARANCE_NEEDED.search(s) or EXCLUDES.search(s)) and not (PREFERRED.search(s) and not REQUIRED.search(s)):
            held = (clearance or "").strip().lower()
            if clearance is None:
                add("review", "security clearance", quote, "a clearance is mentioned; your file does not say whether you hold one")
            elif held in ("none", "no", "n/a", "not applicable"):
                if OBTAINABLE.search(s):
                    add("review", "security clearance", quote, "asks you to be able to obtain one; your file lists none (the employer may sponsor it)")
                else:
                    add("blocked", "security clearance", quote, "required, and your file says you hold no clearance")
            else:
                need, have = clearance_level(s), clearance_level(held)
                if LAPSED.search(held) or need is None or have is None or have < need:
                    add("review", "security clearance", quote, f'required; your file lists "{clearance}": check the level matches')
        if NO_SPONSORSHIP.search(s):
            if sponsor is True:
                add("blocked", "no sponsorship", quote, "the employer will not sponsor, and your file says you will need sponsorship")
            elif sponsor is None:
                add("review", "no sponsorship", quote, "the employer will not sponsor; your file does not say whether you need it")
        if NO_IMMIGRATION_SUPPORT.search(s):
            support=facts.get('needs_immigration_support')
            exception=bool(re.search(r'(?i)\bexcept\b|\bother than\b|\bexcluding\b',s))
            if support is True:
                add('review' if exception else 'blocked','immigration-related support',quote,
                    'the employer limits immigration-related support, which your file says you need' +
                    ('; an exception requires review' if exception else ''))
            elif support is None and not (citizen is True or permanent):
                add('review','immigration-related support',quote,
                    'a No to visa sponsorship does not settle the broader immigration-documentation requirement; '
                    'confirm whether support is needed' + (f" (saved status: {facts['visa_status']})" if facts.get('visa_status') else ''))
        if US_PERSON.search(s) and not (citizen is True or permanent):
            add("review", "export control (U.S. person)", quote, "restricted to U.S. persons (citizens, permanent residents): check you qualify")
    return v
