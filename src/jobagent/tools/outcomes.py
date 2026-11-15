"""Outcome mail (step 15a): read what came back for each submitted application, classify it, link it, keep it.

**What is read.** Only mail that matches a submitted application (`submit.application_match`: from the company's or its
ATS's domain, naming the company or the role), sent after the submission, or in a Gmail thread an earlier outcome of that
application already started. The IMAP search is filtered by sender and date on the server, so unrelated mail (the
mailbox is shared with the candidate's own) is never downloaded. The sync is read-only: it never trashes, labels or
marks anything. Bodies are never stored: an event keeps sender, subject, the mail's own Date and a 200-character excerpt.

**Stages.** confirmation, assessment, recruiter_screen, interview, offer, rejection, withdrawn. A message that is not
an outcome (job alerts, newsletters, a verification code) is dropped, not stored.

**Classifier.** Phrase rules decide when exactly one stage's phrases appear (confidence 0.9); the fast model decides
when none or several do. Below `REVIEW_BELOW` confidence, or when two applications fit equally well, the event is stored
`needs_review` and is not counted until the candidate approves it (`jobagent outcomes approve`).
"""

import hashlib
import imaplib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobagent import db
from jobagent.config import get_settings

STAGES = ("confirmation", "assessment", "recruiter_screen", "interview", "offer", "rejection", "withdrawn")
POSITIVE = {"assessment", "recruiter_screen", "interview", "offer"}
REVIEW_BELOW = 0.7
RULE_CONFIDENCE = 0.9
EXCERPT = 200
# a company's first word too common to filter the mailbox by ("General Mills", "American Express"): its domain is used
GENERIC_WORDS = {"general", "global", "american", "national", "united", "international", "advanced", "first", "premier",
                 "digital", "applied", "universal", "standard", "central", "western", "eastern", "northern", "southern"}
NOTIFY = {"assessment", "recruiter_screen", "interview", "offer"}  # the ones the candidate should hear about now

RULES: dict[str, re.Pattern] = {
    "offer": re.compile(
        r"(?i)(pleased|happy|excited|delighted|thrilled) to (offer|extend)|offer of employment|extend (you )?an offer|"
        r"formal (job )?offer|your offer letter|offer (details|package)|we(’|')d like to offer you|"
        r"(would|will) like to offer you|offer you the (position|role|job)|offer (of|for) (employment|the (position|role))"),
    "interview": re.compile(
        r"(?i)invit\w+ you to (an? |the )?(\w+ )?interview|schedule (an?|your|the) ((phone|video|technical|onsite|on-site|"
        r"virtual|final|panel) )*interview|interview (invitation|invite|scheduled|confirmation|availability|slots)|"
        r"(select|choose|pick) a time (for|to) (your|an?) interview|calendly\.com|goodtime\.io|"
        r"we would like to interview you|next (round|stage) (of|is) (the )?interview|\binterview for (the|your|this)\b|"
        r"next steps?:? (an )?interview"),
    "recruiter_screen": re.compile(
        r"(?i)(phone|recruiter|initial|intro(ductory)?|screening) (screen|call|chat|conversation)|"
        r"(set up|schedule|arrange|find) (a )?(quick |brief |short )?(call|chat|conversation|time to (talk|chat|speak))|"
        r"(chat|talk|speak) (with|to) (a |our )?recruiter|learn more about your (background|experience)"),
    "assessment": re.compile(
        r"(?i)online assessment|coding (challenge|assessment|test|exercise)|take[- ]home|technical (assessment|test|screen)|"
        r"hackerrank\.com|codesignal\.com|codility\.com|hirevue|karat\.com|testgorilla|"
        r"(complete|take|start) (the|this|your|an?) (\w+ )?(assessment|challenge|test)\b"),
    "rejection": re.compile(
        r"(?i)regret to inform|(we |we've |have )?decided to (move|proceed|go)( forward| ahead)? with (other|another|different|"
        r"a different|fewer) "
        r"(candidate|applicant|profile)s?|not (be )?(moving|proceeding|going) forward|will not be (moving|proceeding)|"
        r"unfortunately,? [^.]{0,80}\b(not|unable|cannot|can't|won't|will not)\b[^.]{0,60}\b(move|moving|proceed|proceeding|"
        r"continue|continuing|select|selected|offer|advance|advancing|forward|consider|considering)\b|"
        r"(will not|won't|wont|are not going to) be (moving|proceeding|continuing|going)|"
        r"not (been )?selected|position (has been|was) filled|"
        r"(unable|not able) to (offer|move)|pursue (other|another) candidate|other candidates? (whose|who|that)"),
    "withdrawn": re.compile(
        r"(?i)(you have|you've|your request to) withdraw(n|ing)? (your )?application|application (has been|was) withdrawn|"
        r"withdrawal of your application"),
}
# automated acknowledgement of receipt: the same phrases step 13 waits for
CONFIRMATION = re.compile(
    r"(?i)thank(s| you) for (applying|your (application|interest))|application (has been |was )?(received|submitted)|"
    r"we('ve| have) received (your|the) application|received your application|application confirmation|"
    r"confirm(ing|ation of) your application")
# never an outcome, whatever else the text says
NOT_OUTCOME = re.compile(
    r"(?i)(job alert|new jobs? (for you|matching)|jobs? you (may|might) like|recommended (jobs|roles)|similar (jobs|roles|"
    r"positions)|weekly (digest|newsletter)|newsletter|view all (open )?jobs|your (daily|weekly) job|"
    r"people (also )?viewed|upcoming events?|webinar|career fair|welcome to the (\w+ )?(candidate |career |job |talent )?(portal|careers|community|site)|(candidate |job seeker )?account (has been |was )?"
    r"created|create your (candidate )?(account|profile)|how was your (application )?experience|finish your application|"
    r"application is incomplete|complete your profile)")


@dataclass
class Verdict:
    stage: str | None          # None: not an outcome
    confidence: float
    classifier: str            # rule | model
    evidence: str = ""         # the sentence that decided it
    stages_seen: tuple = ()    # every stage whose phrases appeared (rules)
    failed: bool = False       # the classifier errored or was not available: retry, never "not an outcome"
    abstain: bool = False      # it looked and could not decide / could not back its answer with the message: for the candidate


def _sentence(text: str, m: re.Match) -> str:
    start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
    ends = [i for i in (text.find(".", m.end()), text.find("\n", m.end())) if i != -1]
    return re.sub(r"\s+", " ", text[start:(min(ends) + 1 if ends else m.end() + 120)]).strip()[:EXCERPT]


QUOTED = re.compile(r"(?m)^(On .{8,140} wrote:|-{2,} ?Original Message ?-{2,}|-{3,} ?Forwarded message ?-{3,}|_{5,}|From:\s.+)$")


def fresh_text(mail) -> str:
    """Subject + the new part of the body: a reply's "Re: ..." subject and the quoted earlier message are what the
    earlier mail said, not what this one says."""
    subject = "" if re.match(r"(?i)^\s*(re|fwd?|aw):", mail.subject) else mail.subject
    body = QUOTED.split(mail.text[:6000])[0]
    body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith(">"))
    return f"{subject}\n{body[:3000]}".replace("\u2019", "'").replace("\u2018", "'")


# F12: a phrase only counts when the sentence reports what is happening to THIS candidate. A description of how hiring works
# ("our process includes an online assessment"), marketing for preparation, or an announcement is not a stage.
GENERAL = re.compile(r"(?i)\b(our|the|a|every|each|this|that) (typical |usual |standard |general |full )?(hiring|interview|recruit(ing|ment)|"
                     r"selection|application) process\b|\bprocess (includes?|consists?|involves?|has|may include|will include)\b|"
                     r"\b(candidates|applicants|people|everyone|all) (will|may|can|typically|usually|often|should|who)\b|"
                     r"\bwe (typically|usually|often|always|generally)\b|\bwhat to expect\b|\bhow (to|we|our|do|does)\b|"
                     r"\blearn (more )?(about|how)\b|\bin (this|our) (issue|newsletter|post|article|episode)\b|\bwhy (join|work)\b")
MARKETING = re.compile(r"(?i)\b(ace|prepare for|prep(aration)?|practice|master|tips?|course|bootcamp|webinar|register (now|today|here)|"
                       r"subscribe|unsubscribe|upgrade|discount|\d+% off|free trial|sale|sponsored|advertisement|workshop|"
                       r"career (advice|coach)|resume (review|template|service))\b")
# F12: what an INVITATION, ASSIGNMENT or REPORT looks like. Reader-directed wording ("your guide to", "interested in ...?"),
# a greeting or a reply context alone is not one: the sentence must ask the candidate to do something, schedule something,
# offer a time, or report what happened to them.
ASSIGN = re.compile(
    r"(?i)\b(?:invit\w+|schedul\w+|book (?:a|your)|(?:select|pick|choose) (?:a|your) (?:time|slot)|"
    r"please (?:complete|take|start|schedule|select|choose|reply|confirm|book|use|click|follow|sign)|"
    r"(?:complete|take|start|begin) (?:the|this|your|an?) (?:\w+ ){0,2}(?:assessment|challenge|test|exercise|interview)|"
    r"we(?:'d| would| will|'ll)? (?:like|love) to|we(?:'re| are) (?:pleased|happy|excited|delighted|thrilled|writing)|"
    r"congratulations|you(?:'ve| have) (?:been (?:invited|selected|shortlisted|chosen)|passed|advanced|moved)|"
    r"you (?:are|will be|were) (?:invited|being invited|selected)|your (?:interview|assessment|offer|screen|call) (?:is|has been|was|will)|"
    r"(?:link|details) (?:below|here|attached)|within \d+ (?:day|hour|business)|due (?:by|on|in)|available (?:times|slots)|"
    r"(?:reply|respond) with your availability|are you (?:free|available)|(?:free|available) (?:on|at|for)|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|\d{1,2}(?::\d\d)? ?(?:am|pm)\b|calendly\.com|goodtime\.io|hackerrank\.com|"
    r"codesignal\.com|codility\.com|hirevue|karat\.com|testgorilla|offer (?:letter|details|package))")
CANDIDATE = re.compile(r"(?i)\b(you|your|you've|you're|yours)\b|\bthe candidate\b")
PERSONAL = re.compile(r"(?i)\byou(?:'ve| have) been\b|\byou (?:are being|were|are invited)\b|"
                      r"\b(?:invit\w+|inform|congratulat\w+|interview|offer|meet|call|speak with|talk to) you\b|"
                      r"\bwith (?:other|another|different) (?:candidate|applicant)s?\b|\bregret\b|\bdecided to (?:move|proceed|go)\b|"
                      # "your application HAS BEEN ...", "your interview IS ...": an assertion about the candidate's own process.
                      # A noun phrase ("your assessment guide", "your application handbook") only names a resource.
                      r"\byour (?:application|candidacy|interview|assessment|offer|screen)\s+(?:has been|have been|was|is|will be|is now)\b")
DECISION = {
    "rejection": re.compile(
        r"(?i)\b(?:chosen|decided|selected|opted|going|moving|proceeding|proceed|move)\b[^.!?]{0,60}\b(?:different|another|other|alternative)\b|"
        r"\bnot (?:be )?(?:moving|proceeding|going|selected|advancing|a fit|the right fit|the news you hoped for)\b|"
        r"\b(?:position|role|req(?:uisition)?) (?:has been|was|is) (?:filled|closed|cancel+ed|on hold)\b|"
        r"\bno longer (?:considering|pursuing|being considered|available|moving)\b|"
        r"\b(?:will not|won't|cannot|can't|unable to|not able to) (?:be )?(?:offer\w*|mov\w+|proceed\w*|continu\w+|pursu\w+|advanc\w+|extend\w*)\b|"
        r"\bother candidates?\b|\bmade our (?:choice|decision)\b|\bregret\b|\bunfortunately\b[^.!?]{0,80}\b(?:not|unable|cannot|can't|won't)\b"),
    "withdrawn": re.compile(
        r"(?i)\bwithdr[ae]w\w*\b|\bno longer (?:interested|want\w*)\b|\bcancel+ed your application\b|\bremov\w+ your application\b|"
        r"\btaken you out of (?:the|our) (?:process|running)\b|\bclosed your (?:application|file)\b"),
}
NEWSLETTER_CONTEXT = re.compile(r"(?i)\b(newsletter|digest|handbook|weekly|monthly|roundup|bulletin|announcement|blog|webinar)\b")


def about_this_candidate(text: str, sentence: str, mail=None, stage: str | None = None) -> bool:
    """Does `sentence` (found in `text`) invite, assign or report something to THE CANDIDATE this mail is addressed to?
      * a description of the hiring process, general statements, marketing and announcements never do;
      * in a mailing (List-Unsubscribe, or a newsletter-labelled subject or opening) only an explicit personal statement does;
      * a rejection or withdrawal is about one application by nature;
      * any other stage needs an actual invitation, assignment or report (ASSIGN or an explicit personal statement). A
        greeting, a generic 'your', a rhetorical question or the context of a reply is not enough."""
    if GENERAL.search(sentence) or MARKETING.search(sentence):
        return False
    personal = bool(PERSONAL.search(sentence))
    bulk = bool(mail is not None and getattr(mail, "bulk", False))
    subject = getattr(mail, "subject", "") if mail is not None else ""
    # a newsletter label BEFORE the sentence (subject, opening) makes the whole message a mailing; one AFTER it is a footer
    head, found, _ = (text or "").partition(sentence[:40]) if sentence[:40] else ("", "", "")
    if bulk or NEWSLETTER_CONTEXT.search(subject or "") or (found and NEWSLETTER_CONTEXT.search(head[-400:])):
        return personal
    if stage in ("rejection", "withdrawn"):
        # a decision must be STATED: the rule vocabulary, or a recognisable soft way of saying it. A neutral acknowledgement, a
        # greeting or a mention of the application is not a decision, whatever label a model chose.
        return bool(RULES[stage].search(sentence) or DECISION[stage].search(sentence))
    return personal or bool(ASSIGN.search(sentence))


# "we will contact you to schedule an interview if ..." is what a confirmation says, not an invitation
CONDITIONAL = re.compile(r"(?i)\b(if|should|may|might|in the event|once we|when we|would be)\b|"
                         r"will (contact|reach out|be in touch|notify|email|get back)")


# words that mean a message reports more than receipt, whatever else it says
PROGRESS = re.compile(r"(?i)\b(decided|next steps?|move forward|moving forward|regret|congratulations|excited to|pleased to|"
                      r"interview for|schedule|invite you|offer)\b")


def unconditional(text: str) -> str:
    """The text without sentences that only say what would happen ("if your qualifications match, we will ...")."""
    return " ".join(s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if not CONDITIONAL.search(s))


def stage_hits(text: str, mail=None) -> dict[str, re.Match]:
    """Stages whose phrases appear in a sentence that reports THIS candidate's progression. For the stages a confirmation
    talks about in the future (interview, screen, assessment, offer), a phrase inside a conditional sentence doesn't count,
    and no stage counts inside a description of the hiring process, marketing or a mailing's announcement (F12)."""
    hits = {}
    for k, r in RULES.items():
        m = r.search(text)
        while m and ((k in ("interview", "recruiter_screen", "assessment", "offer") and CONDITIONAL.search(_sentence(text, m)))
                     or not about_this_candidate(text, _sentence(text, m), mail, k)):
            m = r.search(text, m.end())          # look for a later mention that reports this candidate's stage
        if m:
            hits[k] = m
    return hits


def rule_verdict(mail) -> Verdict:
    """Phrase rules over subject + the first 3,000 characters of the body."""
    from jobagent.tools.inbox import classify_verification

    text = fresh_text(mail)
    strong = bool(stage_hits(text, mail))
    if classify_verification(mail) is not None:
        return Verdict(None, 0.95, "rule", "a verification code or link")
    # an alert or newsletter subject, or one in the body, unless the message also reports a stage; a job-alert footer
    # on a real confirmation (subject says so) doesn't hide it
    if not strong and (NOT_OUTCOME.search(mail.subject) or (NOT_OUTCOME.search(text)
                                                            and not CONFIRMATION.search(mail.subject))):
        return Verdict(None, 0.95, "rule", "a job alert or newsletter")
    hits = stage_hits(text, mail)
    if CONFIRMATION.search(text) and not hits:
        # "Thank you for applying ... we decided to proceed with other candidates" is not a receipt: the model reads it
        if PROGRESS.search(unconditional(text)):
            return Verdict(None, 0.0, "rule", "", ("confirmation",))
        m = CONFIRMATION.search(text)
        return Verdict("confirmation", RULE_CONFIDENCE, "rule", _sentence(text, m), ("confirmation",))
    if len(hits) == 1:
        (stage, m), = hits.items()
        return Verdict(stage, RULE_CONFIDENCE, "rule", _sentence(text, m), (stage,))
    if hits:
        # an interview invitation that also says "unfortunately we can't reply by phone" etc.: let the model read it
        return Verdict(None, 0.0, "rule", "", tuple(hits))
    return Verdict(None, 0.0, "rule", "", ())


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\u2019", "'").replace("\u2018", "'")).strip().lower()


def model_verdict(mail, model=None) -> Verdict:
    """The fast model reads one message that the rules could not decide. It gets the SAME cleaned text the rules use (the new
    part of the message only: a reply's quoted earlier mail is what that mail said, never evidence for this one), and its
    answer stands only if it quotes a sentence that really is in that text and reports this candidate's progression."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from pydantic import BaseModel, Field

    from jobagent import llm

    class Reading(BaseModel):
        stage: str = Field(description="one of: " + ", ".join(STAGES) + ", none, unsure")
        confidence: float = Field(ge=0, le=1)
        evidence: str = Field(max_length=180, description="the sentence of the email that says so, copied exactly")

    cleaned = fresh_text(mail)
    msgs = [SystemMessage(
        "You read one email a company or its applicant-tracking system sent about the candidate's job application and "
        "say what stage it reports: confirmation (an automated acknowledgement that the application was received), "
        "assessment (a coding challenge or test to take), recruiter_screen (a call with a recruiter being arranged), "
        "interview (an interview being scheduled or confirmed), offer, rejection (the company is not moving forward), "
        "withdrawn (the application was withdrawn), or none (job alerts, newsletters, marketing, descriptions of how hiring "
        "works, a verification code, anything that is not about THIS candidate's application progress, and account-created "
        "or welcome messages), or unsure when you cannot tell. A confirmation "
        "is only an acknowledgement that a specific application was received, never an account or marketing message. A "
        "rejection that offers to keep the resume on file is still a rejection. Only the new part of the message is shown; "
        "a general description such as 'our process includes an online assessment' is none. Quote the deciding sentence "
        "exactly as written; never invent one."),
        HumanMessage(f"From: {mail.sender}\n{cleaned}")]      # fresh_text carries the subject unless it is a reply's ("Re: ...")
    out = (model or (lambda m: llm.structured(Reading, "fast", timeout=60).invoke(m)))(msgs)
    stage = out.stage.strip().lower().replace(" ", "_")
    evidence = out.evidence[:EXCERPT]
    if stage == "unsure":
        return Verdict(None, 0.0, "model", "the model could not tell", abstain=True)
    if stage not in STAGES:
        return Verdict(None, float(out.confidence), "model", evidence)
    # the answer must be backed by the message: a quoted sentence found in the new text that reports this candidate's progression
    ev = _norm(evidence)
    if len(ev) < 12 or ev not in _norm(cleaned):
        return Verdict(None, 0.0, "model", "its evidence is not in the new part of the message", abstain=True)
    if stage == "confirmation":
        if not CONFIRMATION.search(evidence) and not re.search(r"(?i)\b(received|applied|application)\b", evidence):
            return Verdict(None, 0.9, "model", "the quoted sentence does not acknowledge an application")
    elif not about_this_candidate(cleaned, evidence, mail, stage):
        return Verdict(None, 0.9, "model", "a general statement or a neutral reply, not this candidate's progress")
    return Verdict(stage, float(out.confidence), "model", evidence)


def classify(mail, model="model") -> Verdict:
    """Rules first; the model when they find none or several stages. `model=None` keeps the rules only, and anything they
    cannot decide comes back `failed` (to be retried when a model is available), never as 'not an outcome'. A model that
    errors is `failed` too: an outage must not turn mail into non-outcomes."""
    v = rule_verdict(mail)
    if v.stage is not None or v.confidence >= 0.9:
        return v
    if model is None:
        return Verdict(None, 0.0, "rule", "no model was used", v.stages_seen, failed=True)
    try:
        mv = model_verdict(mail, None if model == "model" else model)
        # a model that calls something a confirmation must have something about an application to point at
        if mv.stage == "confirmation" and not re.search(r"(?i)applic|applied|applying", f"{mail.subject} {mail.text[:3000]}"):
            return Verdict(None, 0.9, "model", "no mention of an application")
        return mv
    except Exception as exc:
        return Verdict(None, 0.0, "model", f"the model could not read it ({type(exc).__name__})", v.stages_seen, failed=True)


# --- linking and storing -------------------------------------------------------------------------------------------

MAX_ATTEMPTS = 24     # a message the classifier keeps failing on is then handed to the candidate (state `undecided`)


@dataclass
class SyncResult:
    stored: list = field(default_factory=list)       # dicts of the events written (or that would be, on a dry run)
    seen: int = 0                                     # messages fetched
    dropped: dict = field(default_factory=dict)       # reason -> count
    applications: int = 0
    retrying: int = 0                                 # messages left for the next sync (classifier unavailable or failing)
    undecided: int = 0                                # messages waiting for the candidate

    def drop(self, why: str) -> None:
        self.dropped[why] = self.dropped.get(why, 0) + 1


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _or_from(terms: list[str]) -> str:
    terms = [t for t in dict.fromkeys(terms) if t]
    if not terms:
        return ""
    crit = f'FROM "{terms[-1]}"'
    for t in reversed(terms[:-1]):
        crit = f'OR FROM "{t}" {crit}'
    return crit


def _job_json(row) -> dict:
    """The application's jd.json (company, title, apply url) from its job folder, else what the row itself holds."""
    s = get_settings()
    for base in (Path("/app"), s.outputs_dir.parent, Path.cwd()):
        p = Path(row["job_dir"]) if Path(row["job_dir"]).is_absolute() else base / row["job_dir"]
        if (p / "jd.json").exists():
            try:
                return json.loads((p / "jd.json").read_text())
            except ValueError:
                break
    return {"company": row["company"], "title": row["title"], "apply_url": row["url"], "source_url": row["url"]}


def _score(match, mail) -> int:
    about = f"{mail.subject} {mail.text[:3000]}".lower()
    return sum(1 for w in match.words if w in about)


def Mail_stub(uid: str, uidvalidity: str):
    """A header-less placeholder to put a ledger row on a message that cannot be read (e.g. expunged)."""
    from jobagent.tools.inbox import Mail
    return Mail(str(uid), "", "", "", None, "", uidvalidity=uidvalidity)


def message_key(uidvalidity: str, uid: str) -> str:
    """The durable identity of a mailbox message: the folder's UIDVALIDITY and the message's UID. The Message-ID header is
    only metadata (it can be missing or repeated)."""
    return f"{uidvalidity}:{uid}"


def _ledger(conn, uv: str, uid: str):
    return conn.execute("SELECT * FROM mail_ledger WHERE uidvalidity = ? AND mailbox_uid = ?", (uv, uid)).fetchone()


def _record(conn, mail, state: str, reason: str = "", candidates=None, inherited: bool = False, bump: bool = False,
            error: str = "") -> None:
    """Write (or update) the ledger row of one message: metadata only, never the subject or body."""
    from jobagent.tools.inbox import sender_domain
    uv = mail.uidvalidity or ""
    import email.utils
    addr = (email.utils.parseaddr(mail.sender)[1] or "").lower()
    sig = hashlib.sha256((mail.subject or "").encode()).hexdigest() if mail.subject else None
    csig = content_sig(mail) if getattr(mail, "has_text", False) and mail.text else None     # captured when the body is first read
    with conn:
        conn.execute(
            "INSERT INTO mail_ledger (uidvalidity, mailbox_uid, header_message_id, thread_id, sender_domain, sender_addr, subject_sig, "
            "header_date, delivered_at, state, reason, candidates, inherited, attempts, last_error, content_sig) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uidvalidity, mailbox_uid) DO UPDATE SET state = excluded.state, reason = excluded.reason, "
            "candidates = COALESCE(excluded.candidates, candidates), attempts = attempts + ?, last_error = excluded.last_error, "
            "sender_addr = COALESCE(excluded.sender_addr, sender_addr), subject_sig = COALESCE(excluded.subject_sig, subject_sig), "
            "content_sig = COALESCE(excluded.content_sig, content_sig), "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
            (uv, mail.uid, mail.message_id or None, mail.thread_id or None, sender_domain(mail), addr or None, sig,
             _utc(mail.date) if mail.date else None, _utc(mail.delivered) if mail.delivered else None, state, reason[:200],
             json.dumps(candidates) if candidates is not None else None, int(inherited), int(bump), error[:200], csig, int(bump)))


def _cursor(conn, app_id: int):
    r = conn.execute("SELECT value FROM kv WHERE key = ?", (f"outcome_cursor:{app_id}",)).fetchone()
    if not r or ":" not in r[0]:
        return None
    uv, _, last = r[0].rpartition(":")
    return uv, int(last)


def _set_cursor(conn, app_id: int, uv: str, last: int) -> None:
    with conn:
        conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (f"outcome_cursor:{app_id}", f"{uv}:{last}"))


def content_sig(mail) -> str:
    """A fingerprint of what the message SAYS (header id, sender, subject and the new part of the body). The same message
    delivered again, under another UID or after a UIDVALIDITY change, has the same one; two different messages that merely
    repeat a Message-ID and a subject do not."""
    return hashlib.sha256(json.dumps([mail.message_id, mail.sender, mail.subject, fresh_text(mail)], ensure_ascii=False).encode()).hexdigest()


def _stored_already(conn, mail, key: str) -> bool:
    """Is this very mail already an event? By durable key; by content signature (a re-delivery); or, for events written
    before durable keys existed (no mailbox UID recorded), by header Message-ID together with the subject. A repeated or
    missing header alone never merges two mails, and neither do a repeated header and subject with different content."""
    if conn.execute("SELECT 1 FROM outcome_events WHERE message_id = ?", (key,)).fetchone():
        return True
    if conn.execute("SELECT 1 FROM outcome_events WHERE content_sig = ?", (content_sig(mail),)).fetchone():
        return True
    if mail.message_id and conn.execute(
            "SELECT 1 FROM outcome_events WHERE mailbox_uid IS NULL AND (message_id = ? OR header_message_id = ?) AND subject = ?",
            (mail.message_id, mail.message_id, mail.subject[:200])).fetchone():
        return True
    return False


def _events_seeding(conn, app_id: int):
    """Thread ids an APPROVED, UNAMBIGUOUS event of this application started: only these may attach later replies to it, and
    only while no other application also holds an approved event in the same thread (a thread with conflicting owners seeds
    nothing)."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT e.thread_id FROM outcome_events e WHERE e.application_id = ? AND e.thread_id != '' AND e.thread_id IS NOT NULL "
        "AND e.needs_review = 0 AND e.ambiguous = 0 AND NOT EXISTS (SELECT 1 FROM outcome_events o WHERE o.thread_id = e.thread_id "
        "AND o.needs_review = 0 AND o.ambiguous = 0 AND o.application_id != e.application_id)", (app_id,))]


def _seed_event(conn, app_id: int, thread_id: str) -> int:
    """The approved event of this application that a thread-only reply depends on (0 if none can be named)."""
    r = conn.execute("SELECT id FROM outcome_events WHERE application_id = ? AND thread_id = ? AND needs_review = 0 AND ambiguous = 0 "
                     "ORDER BY received_at, id LIMIT 1", (app_id, thread_id)).fetchone()
    return r[0] if r else 0


def _revalidate_seeds(conn, entry: dict, mail) -> None:
    """A candidate that depends on a seed event (a thread-only reply) is only as good as that seed's CURRENT decision. Work that
    was queued before the seed was reassigned, demoted, rejected or made ambiguous must not publish the old owner. The
    candidate follows the seed's current application when the seed is still approved and unambiguous; otherwise the message
    becomes pending (ambiguous, for review) for the applications involved."""
    seeds = entry.get("seed") or {}
    changed = False
    for app_id, seed_id in list(seeds.items()):
        if not seed_id or app_id not in entry["cands"]:
            continue
        seed = conn.execute("SELECT application_id, needs_review, ambiguous FROM outcome_events WHERE id = ?", (seed_id,)).fetchone()
        score, ev = entry["cands"][app_id]
        if seed is not None and seed["needs_review"] == 0 and not seed["ambiguous"]:
            if seed["application_id"] != app_id:                   # reassigned while queued: follow the current decision
                del entry["cands"][app_id]
                entry["cands"][seed["application_id"]] = (score, "followed the reassigned seed event")
                seeds.pop(app_id, None)
                seeds[seed["application_id"]] = seed_id
            continue
        # the seed lost its authority (rejected, pending, ambiguous or gone): nothing automatic is left to rely on
        changed = True
        entry["cands"][app_id] = (score, ev + "; its seed event is no longer approved")
        if seed is not None:
            entry["cands"].setdefault(seed["application_id"], (0, "the application of its (now unsettled) seed event"))
    if changed:
        entry["inherited"] = True
        entry["seed"] = {}


def _pending_threads(conn) -> dict[str, set]:
    """Threads whose ownership is not settled: a seed waiting for approval, an ambiguous one, or approved events that belong to
    different applications. Later replies stay pending for those candidate applications until the candidate assigns one."""
    out: dict[str, set] = {}
    for e in conn.execute("SELECT id, thread_id, application_id FROM outcome_events WHERE thread_id != '' AND thread_id IS NOT NULL "
                          "AND needs_review != 2 AND (needs_review = 1 OR ambiguous = 1)"):
        apps = out.setdefault(e["thread_id"], {e["application_id"]})
        apps |= {r[0] for r in conn.execute("SELECT application_id FROM outcome_candidates WHERE event_id = ?", (e["id"],))}
    for r in conn.execute("SELECT e.thread_id, GROUP_CONCAT(DISTINCT e.application_id) AS apps FROM outcome_events e WHERE e.thread_id != '' "
                          "AND e.thread_id IS NOT NULL AND e.needs_review = 0 AND e.ambiguous = 0 GROUP BY e.thread_id "
                          "HAVING COUNT(DISTINCT e.application_id) > 1"):
        out.setdefault(r["thread_id"], set()).update(int(x) for x in r["apps"].split(","))
    return out


def _event_time(mail, as_of: datetime) -> datetime:
    """When the message arrived: the mailbox's delivery time; else the Date header; else now. A forged or malformed Date
    cannot move a message out of reach."""
    return mail.delivered or mail.date or as_of


def sync(inbox, conn, as_of: datetime | None = None, since_days: int | None = None, model="model",
         dry_run: bool = False, log=print) -> SyncResult:
    """Read, classify and link the mail of every submitted application; store the new outcome events.

    Progress is durable (F11): every message looked at is written to `mail_ledger` by (UIDVALIDITY, UID), with its state:
    unrelated, stored, retry (classifier failed or unavailable) or undecided. A message the classifier could not read is
    retried on every sync until it can; a per-application cursor (the highest UID covered) advances only after the ledger
    rows of everything it covers are written. Thread replies attach to an application only through an approved,
    unambiguous event (F10); replies to an unsettled thread are stored pending for the same candidates."""
    from jobagent.steps.submit import application_match

    as_of = as_of or datetime.now(UTC)
    res = SyncResult()
    apps = conn.execute("SELECT * FROM applications WHERE state IN ('submitted', 'confirmed') AND submitted_at IS NOT NULL "
                        "ORDER BY id").fetchall()
    res.applications = len(apps)
    uv_now = inbox.uidvalidity() if hasattr(inbox, "uidvalidity") else ""
    if not dry_run:
        from jobagent.tools import outcome_stats
        for a in apps:
            if a["snapshot_status"] is None:  # older rows, or submitted before the snapshot existed
                try:
                    outcome_stats.snapshot(conn, a["job_dir"])
                except Exception:
                    pass
    matchers = {a["id"]: application_match(conn, _job_json(a), register=not dry_run) for a in apps}
    # key -> {"mail": Mail, "cands": {app_id: (score, evidence)}, "inherited": bool}
    claimed: dict[str, dict] = {}
    seen_heads: dict[str, object] = {}
    top_uid: dict[int, int] = {}
    blocked: dict[int, int] = {}         # app id -> the lowest UID whose headers could not be fetched this time
    pending = _pending_threads(conn)

    def consider(head, app_id, m, submitted, via_thread: bool, pending_thread: bool = False):
        key = message_key(head.uidvalidity or uv_now, head.uid)
        led = _ledger(conn, head.uidvalidity or uv_now, head.uid)
        if led is not None and led["state"] in ("unrelated", "stored", "undecided") and not (since_days is not None and led["state"] == "unrelated"):
            res.drop("already processed")                       # (an explicit --since-days rescan looks at 'unrelated' mail again)
            return
        if led is not None and led["state"] == "retry":
            return                                           # re-evaluated in the retry pass below
        seen_heads.setdefault(key, head)
        when = _event_time(head, as_of)
        if when < submitted - timedelta(minutes=2) or when > as_of + timedelta(days=1):
            res.drop("outside the application's dates")
            return
        entry = claimed.get(key)
        if entry is not None and app_id in entry["cands"]:
            return
        if pending_thread:
            mail = entry["mail"] if entry else inbox.fetch_text(head)
            claimed.setdefault(key, {"mail": mail, "cands": {}, "inherited": True})["cands"][app_id] = (0, "reply in a thread still waiting for an application")
            return
        if not (m.plausible(head) or via_thread):
            res.drop("not about this application")
            return
        mail = entry["mail"] if entry else inbox.fetch_text(head)
        if not (m(mail) or via_thread):
            res.drop("not about this application")
            return
        score = _score(m, mail) + (1 if via_thread else 0)
        words = [w for w in m.words if w in f"{mail.subject} {mail.text[:3000]}".lower()]
        ev = ("reply in a thread this application's approved event started; " if via_thread else "") + ("matched " + ", ".join(words) if words else "sender domain")
        direct = bool(m.plausible(head) and m(mail))                  # would it match this application with no thread at all?
        entry = claimed.setdefault(key, {"mail": mail, "cands": {}, "inherited": False, "seed": {}})
        entry["cands"][app_id] = (score, ev)
        entry.setdefault("seed", {})[app_id] = 0 if direct else (_seed_event(conn, app_id, head.thread_id) if via_thread else 0)

    for a in apps:
        m = matchers[a["id"]]
        submitted = _parse(a["submitted_at"])
        word = m.company.split()[0] if m.company else ""
        terms = sorted(m.allowed) + ([word] if len(word) >= 6 and word not in GENERIC_WORDS else [])
        if not terms:
            continue  # never an unfiltered search of a shared mailbox
        cur = _cursor(conn, a["id"])
        if since_days is None and cur is not None and cur[0] == uv_now:
            criteria = f"UID {cur[1] + 1}:* {_or_from(terms)}".strip()      # exactly the mail delivered since the last sync
            floor_uid = cur[1]
        else:
            floor = submitted - timedelta(days=1)
            if since_days is not None:
                floor = max(floor, as_of - timedelta(days=since_days))
            criteria = f'SINCE "{floor.strftime("%d-%b-%Y")}" {_or_from(terms)}'.strip()
            floor_uid = 0
        searched = [u for u in inbox.search(criteria) if int(u) > floor_uid]
        uids = list(searched)
        # replies in a thread an APPROVED, UNAMBIGUOUS event of this application started (a recruiter's personal address)
        threads = _events_seeding(conn, a["id"])
        for t in threads:
            for u in inbox.search(f"X-GM-THRID {t}"):
                if u not in uids:
                    uids.append(u)
        for uid in uids:
            try:
                head = inbox.fetch_headers(uid)                    # headers only: the body of an unrelated mail is never read
            except LookupError:                                    # the server says there is no such message: it was expunged
                if not dry_run:
                    _record(conn, Mail_stub(uid, uv_now), "unrelated", "gone from the mailbox")
                continue
            except (OSError, imaplib.IMAP4.error):                 # transient: NOT proof that it is gone. The cursor must not pass it
                blocked[a["id"]] = min(blocked.get(a["id"], int(uid)), int(uid))
                res.retrying += 1
                continue
            res.seen += 1
            in_thread = bool(head.thread_id) and head.thread_id in threads
            consider(head, a["id"], m, submitted, in_thread)
            if uid in searched:                                   # only what the cursor's own search covers moves the cursor
                top_uid[a["id"]] = max(top_uid.get(a["id"], 0), int(uid))
    # replies in threads whose seed is not settled: stored pending, for the same candidate applications
    for t, app_ids in pending.items():
        for uid in inbox.search(f"X-GM-THRID {t}"):
            try:
                head = inbox.fetch_headers(uid)
            except LookupError:
                continue
            if head.thread_id != t:
                continue
            res.seen += 1
            for app_id in sorted(app_ids):
                a = next((x for x in apps if x["id"] == app_id), None)
                if a is not None:
                    consider(head, app_id, matchers[app_id], _parse(a["submitted_at"]), True, pending_thread=True)
    # messages every application rejected: durably unrelated, so they are never read again
    unrelated = [(k, h) for k, h in seen_heads.items() if k not in claimed]
    # the retry pass: ledger rows the classifier could not handle before, with the candidates they already matched
    retries = conn.execute("SELECT * FROM mail_ledger WHERE state = 'retry' ORDER BY id").fetchall()
    for row in retries:
        key = message_key(row["uidvalidity"], row["mailbox_uid"])
        if key in claimed:
            continue
        uid_now = row["mailbox_uid"]
        if row["uidvalidity"] != uv_now:
            # The mailbox's UIDs were renumbered: this UID may now be a different, unrelated message. A Message-ID search is only
            # a way to find CANDIDATES (header ids repeat, and one ATS domain serves many companies). A candidate is accepted
            # only if its HEADERS (read without its body) match what was recorded about the original - the same Message-ID,
            # the same full sender address, the same subject, the same Date - AND it still passes the current ownership
            # check of at least one of the applications the original was matched to. Otherwise the original stays an explicit
            # undecided item and no body is retrieved.
            uid_now = None
            mid = row["header_message_id"]
            if mid and row["sender_addr"] and row["subject_sig"]:
                from jobagent.tools.inbox import sender_domain
                for cand in [u for u in inbox.search(f'HEADER Message-ID "{mid}"') if u][:20]:
                    try:
                        probe = inbox.fetch_headers(cand)
                    except (LookupError, OSError, imaplib.IMAP4.error):
                        continue
                    import email.utils
                    same = (probe.message_id == mid and (email.utils.parseaddr(probe.sender)[1] or "").lower() == row["sender_addr"]
                            and hashlib.sha256((probe.subject or "").encode()).hexdigest() == row["subject_sig"]
                            and (_utc(probe.date) if probe.date else None) == row["header_date"])
                    saved_apps = [c["application_id"] for c in json.loads(row["candidates"] or "[]")]
                    owned = any(matchers[a_id].plausible(probe) or (probe.thread_id and probe.thread_id in _events_seeding(conn, a_id))
                                for a_id in saved_apps if a_id in matchers)
                    if same and owned and row["content_sig"]:
                        # the headers fit and the current ownership check passes, so the body may be read; it must also SAY the
                        # same thing as the original did when its body was first read (headers alone are a prefilter, not identity)
                        try:
                            if content_sig(inbox.fetch_text(probe)) == row["content_sig"]:
                                uid_now = cand
                                break
                        except (LookupError, OSError, imaplib.IMAP4.error):
                            continue
            if uid_now is None:
                if not dry_run:
                    with conn:
                        conn.execute("UPDATE mail_ledger SET state = 'undecided', reason = ? WHERE id = ?",
                                     ("the mailbox was renumbered (UIDVALIDITY changed) and this message could not be re-identified "
                                      "from its headers and content: review it or dismiss it", row["id"]))
                res.undecided += 1
                continue
            if not dry_run:           # relocated: the row now names the message in the current epoch
                with conn:
                    conn.execute("UPDATE mail_ledger SET uidvalidity = ?, mailbox_uid = ? WHERE id = ?", (uv_now, uid_now, row["id"]))
            key = message_key(uv_now, uid_now)
        try:
            head = inbox.fetch_headers(uid_now)
            if head.uidvalidity and head.uidvalidity != (uv_now or head.uidvalidity):
                raise LookupError("epoch mismatch")
            mail = inbox.fetch_text(head)
        except LookupError:
            if not dry_run:
                with conn:
                    conn.execute("UPDATE mail_ledger SET state = 'unrelated', reason = 'the message is gone from the mailbox' WHERE id = ?",
                                 (row["id"],))
            continue
        except (OSError, imaplib.IMAP4.error):
            continue                                             # transient: it stays queued as it is
        saved = json.loads(row["candidates"] or "[]")
        cands = {c["application_id"]: (c["score"], c["evidence"]) for c in saved}
        if cands:
            claimed[key] = {"mail": mail, "cands": cands, "inherited": bool(row["inherited"]), "retry_row": row["id"],
                            "seed": {c["application_id"]: c.get("seed") for c in saved}}
    if not dry_run:
        for key, head in unrelated:
            _record(conn, head, "unrelated", "not about any submitted application")
    for key, entry in claimed.items():
        mail = entry["mail"]
        if not mail.uidvalidity:
            mail.uidvalidity = uv_now
        _revalidate_seeds(conn, entry, mail)
        cands = sorted(((s, app, ev) for app, (s, ev) in entry["cands"].items()), key=lambda c: (-c[0], c[1]))
        seeds = entry.get("seed") or {}
        cand_json = [{"application_id": app, "score": s, "evidence": ev, "seed": seeds.get(app)} for s, app, ev in cands]
        if _stored_already(conn, mail, key):
            res.drop("already stored")
            if not dry_run:
                _record(conn, mail, "stored", "already an event")
            continue
        v = classify(mail, model)
        if v.failed:
            res.retrying += 1
            if not dry_run:
                led = _ledger(conn, mail.uidvalidity, mail.uid)
                attempts = (led["attempts"] if led else 0) + (1 if model is not None else 0)
                _record(conn, mail, "undecided" if attempts >= MAX_ATTEMPTS else "retry", v.evidence, cand_json,
                        entry["inherited"], bump=model is not None, error=v.evidence)
            res.drop("classifier unavailable: will retry")
            continue
        if v.abstain:
            res.undecided += 1
            res.drop("undecided: waits for the candidate")
            if not dry_run:
                _record(conn, mail, "undecided", v.evidence, cand_json, entry["inherited"])
            continue
        if v.stage is None:
            res.drop("not an outcome" if v.confidence >= 0.5 else "could not tell")
            if not dry_run:
                _record(conn, mail, "unrelated" if v.confidence >= 0.5 else "undecided", v.evidence or "not an outcome", cand_json,
                        entry["inherited"])
            continue
        top, second = (cands + [(None, None, None)])[0], (cands + [(None, None, None)])[1]
        tie = second[0] is not None and second[0] == top[0] and second[1] != top[1]
        ambiguous = bool(tie or entry["inherited"])
        review = int(ambiguous or v.confidence < REVIEW_BELOW)
        when = _event_time(mail, as_of)
        event = {"application_id": top[1], "stage": v.stage, "message_id": key, "thread_id": mail.thread_id,
                 "sender": mail.sender[:200], "subject": mail.subject[:200], "excerpt": v.evidence[:EXCERPT],
                 "received_at": _utc(when), "classifier": v.classifier, "confidence": round(v.confidence, 2),
                 "needs_review": review, "header_message_id": mail.message_id or None, "mailbox_uid": mail.uid,
                 "uidvalidity": mail.uidvalidity, "delivered_at": _utc(mail.delivered) if mail.delivered else None,
                 "ambiguous": int(ambiguous), "content_sig": content_sig(mail),
                 "inherited_from": (entry.get("seed") or {}).get(top[1]) if not ambiguous else None}
        res.stored.append(event)
        if dry_run:
            continue
        with conn:
            cur = conn.execute("INSERT OR IGNORE INTO outcome_events (application_id, stage, message_id, thread_id, sender, "
                               "subject, excerpt, received_at, classifier, confidence, needs_review, header_message_id, "
                               "mailbox_uid, uidvalidity, delivered_at, ambiguous, content_sig, inherited_from) VALUES (:application_id, :stage, :message_id, "
                               ":thread_id, :sender, :subject, :excerpt, :received_at, :classifier, :confidence, :needs_review, "
                               ":header_message_id, :mailbox_uid, :uidvalidity, :delivered_at, :ambiguous, :content_sig, :inherited_from)", event)
            event_id = cur.lastrowid
            if cur.rowcount:
                conn.executemany("INSERT OR IGNORE INTO outcome_candidates (event_id, application_id, score, evidence) VALUES (?, ?, ?, ?)",
                                 [(event_id, app, s, ev) for s, app, ev in cands])
            if v.stage == "confirmation" and not review:
                _confirm(conn, event["application_id"], event["received_at"], key, f"{mail.sender}: {mail.subject}")
        _record(conn, mail, "stored", f"{v.stage} ({v.classifier})", cand_json, entry["inherited"])
        if v.stage in NOTIFY and not review:
            co = conn.execute("SELECT company, title FROM applications WHERE id = ?", (top[1],)).fetchone()
            log(f"outcomes: NEW {v.stage.upper()} for {co['company']} - {co['title']}: {mail.subject[:80]}")
    if not dry_run:
        # the cursor moves only now, after every message it covers has a ledger row
        for app_id, last in top_uid.items():
            if app_id in blocked:                  # a header fetch failed at that UID: stop just below it so it is searched again
                last = min(last, blocked[app_id] - 1)
                cur = _cursor(conn, app_id)
                if last <= 0 or (cur is not None and cur[0] == uv_now and last <= cur[1]):
                    continue
            _set_cursor(conn, app_id, uv_now, last)
        with conn:
            conn.execute("INSERT INTO kv (key, value) VALUES ('outcome_last_sync', ?) ON CONFLICT(key) DO UPDATE SET "
                         "value = excluded.value", (_utc(as_of),))
    return res


def _confirm(conn, app_id: int, received_at: str, key: str, text: str) -> None:
    conn.execute("UPDATE applications SET state = 'confirmed', confirmed_at = COALESCE(confirmed_at, ?), "
                 "confirmation_id = COALESCE(confirmation_id, ?), confirmation = COALESCE(confirmation, ?) "
                 "WHERE id = ? AND state = 'submitted'", (received_at, key, text[:300], app_id))


# --- the candidate's decisions -------------------------------------------------------------------------------------

def pending_review(conn) -> list:
    return conn.execute("SELECT e.*, a.company, a.title AS job_title, (SELECT COUNT(*) FROM outcome_candidates c WHERE c.event_id = e.id) "
                        "AS candidate_count FROM outcome_events e JOIN applications a ON a.id = e.application_id "
                        "WHERE e.needs_review = 1 ORDER BY e.received_at").fetchall()


def undecided(conn) -> list:
    """Messages read but not classifiable (the classifier kept failing, or could not back its answer): for the candidate."""
    return conn.execute("SELECT * FROM mail_ledger WHERE state = 'undecided' ORDER BY id").fetchall()


def requeue(conn, key: str) -> bool:
    """Put an undecided message back for the next sync (e.g. after the model is working again)."""
    uv, _, uid = key.rpartition(":")
    with conn:
        cur = conn.execute("UPDATE mail_ledger SET state = 'retry', attempts = 0, last_error = NULL WHERE uidvalidity = ? AND "
                           "mailbox_uid = ? AND state = 'undecided'", (uv, uid))
    return cur.rowcount == 1


def dismiss(conn, key: str) -> bool:
    """The candidate read an undecided message and it is not an outcome: it is never looked at again."""
    uv, _, uid = key.rpartition(":")
    with conn:
        cur = conn.execute("UPDATE mail_ledger SET state = 'unrelated', reason = 'dismissed by the candidate' WHERE uidvalidity = ? "
                           "AND mailbox_uid = ? AND state = 'undecided'", (uv, uid))
    return cur.rowcount == 1


def candidates(conn, event_id: int) -> list:
    """Every application the event could belong to, with the matching evidence (the chosen one is `application_id`)."""
    return conn.execute("SELECT c.application_id, c.score, c.evidence, a.company, a.title, (e.application_id = c.application_id) AS chosen "
                        "FROM outcome_candidates c JOIN applications a ON a.id = c.application_id "
                        "JOIN outcome_events e ON e.id = c.event_id WHERE c.event_id = ? ORDER BY c.score DESC, c.application_id",
                        (event_id,)).fetchall()


def _audit(conn, event_id: int, action: str, old, new, detail: str = "") -> None:
    conn.execute("INSERT INTO outcome_audit (event_id, action, old_application_id, new_application_id, detail) VALUES (?, ?, ?, ?, ?)",
                 (event_id, action, old, new, detail[:300]))


def _move_confirmation(conn, ev, old_app: int | None, new_app: int | None) -> None:
    """Keep the application's derived confirmation state consistent when a counted confirmation event changes owner: the
    old owner loses the confirmation this event gave it (and takes another counted one, if it has one); the new owner gets it."""
    if ev["stage"] != "confirmation":
        return
    if old_app is not None and old_app != new_app:
        conn.execute("UPDATE applications SET state = CASE WHEN state = 'confirmed' THEN 'submitted' ELSE state END, "
                     "confirmed_at = NULL, confirmation_id = NULL, confirmation = NULL WHERE id = ? AND confirmation_id = ?",
                     (old_app, ev["message_id"]))
        other = conn.execute("SELECT * FROM outcome_events WHERE application_id = ? AND stage = 'confirmation' AND needs_review = 0 "
                             "AND id != ? ORDER BY received_at LIMIT 1", (old_app, ev["id"])).fetchone()
        if other is not None:
            _confirm(conn, old_app, other["received_at"], other["message_id"], f"{other['sender']}: {other['subject']}")
    if new_app is not None:
        _confirm(conn, new_app, ev["received_at"], ev["message_id"], f"{ev['sender']}: {ev['subject']}")


def assign(conn, event_id: int, application_id: int) -> bool:
    """The candidate decides which application an event belongs to (any submitted one: the candidate may know better than
    the matcher). The event is then approved and counted as the candidate's own decision (`manual`), audited, and its derived
    state follows. Replies that inherited their association from this event (`inherited_from`) follow it, now and whenever it is
    reassigned; a reply the candidate assigned separately (`manual`) is never overwritten by that."""
    app = conn.execute("SELECT id FROM applications WHERE id = ? AND state IN ('submitted', 'confirmed')", (application_id,)).fetchone()
    ev = conn.execute("SELECT * FROM outcome_events WHERE id = ? AND needs_review != 2", (event_id,)).fetchone()
    if app is None or ev is None:
        return False
    with conn:
        conn.execute("UPDATE outcome_events SET application_id = ?, ambiguous = 0, needs_review = 0, classifier = 'human', manual = 1, "
                     "inherited_from = NULL WHERE id = ?", (application_id, event_id))
        conn.execute("INSERT OR IGNORE INTO outcome_candidates (event_id, application_id, score, evidence) VALUES (?, ?, 0, 'chosen by the candidate')",
                     (event_id, application_id))
        _audit(conn, event_id, "assign", ev["application_id"], application_id, "chosen by the candidate")
        fresh = dict(ev) | {"application_id": application_id}
        _move_confirmation(conn, fresh, ev["application_id"] if ev["needs_review"] == 0 and not ev["ambiguous"] else None, application_id)
        if ev["thread_id"]:
            for d in conn.execute("SELECT * FROM outcome_events WHERE thread_id = ? AND id != ? AND needs_review != 2 AND manual = 0 AND "
                                  "(ambiguous = 1 OR inherited_from = ?)", (ev["thread_id"], event_id, event_id)).fetchall():
                counted = d["confidence"] is None or d["confidence"] >= REVIEW_BELOW
                conn.execute("UPDATE outcome_events SET application_id = ?, ambiguous = 0, needs_review = ?, inherited_from = ? WHERE id = ?",
                             (application_id, 0 if counted else 1, event_id, d["id"]))
                conn.execute("INSERT OR IGNORE INTO outcome_candidates (event_id, application_id, score, evidence) VALUES (?, ?, 0, "
                             "'followed the thread assignment')", (d["id"], application_id))
                _audit(conn, d["id"], "propagate", d["application_id"], application_id, f"followed event {event_id}")
                if counted:
                    _move_confirmation(conn, dict(d) | {"application_id": application_id},
                                       d["application_id"] if d["needs_review"] == 0 and not d["ambiguous"] else None, application_id)
            # events counted automatically whose dependence on this seed cannot be established (no recorded provenance) and that
            # sat with the seed's former owner: not carried silently to the new owner, not left silently with the old one -
            # they go back to review for both applications
            if ev["needs_review"] == 0 and not ev["ambiguous"] and ev["application_id"] != application_id:
                for d in conn.execute("SELECT * FROM outcome_events WHERE thread_id = ? AND id != ? AND manual = 0 AND inherited_from IS NULL "
                                      "AND needs_review = 0 AND ambiguous = 0 AND application_id = ?",
                                      (ev["thread_id"], event_id, ev["application_id"])).fetchall():
                    conn.execute("UPDATE outcome_events SET needs_review = 1, ambiguous = 1 WHERE id = ?", (d["id"],))
                    conn.executemany("INSERT OR IGNORE INTO outcome_candidates (event_id, application_id, score, evidence) VALUES (?, ?, 0, "
                                     "'its thread owner was reassigned and its own ownership is not recorded')",
                                     [(d["id"], ev["application_id"]), (d["id"], application_id)])
                    _audit(conn, d["id"], "requeue", d["application_id"], d["application_id"],
                           f"thread owner (event {event_id}) was reassigned; ownership not recorded")
                    _move_confirmation(conn, dict(d), d["application_id"], None)       # a pending event confirms nothing
    return True


def decide(conn, event_id: int, approve: bool) -> bool:
    """Approve (counted from now on) or reject (kept as rejected so the same message is never re-added). Approving an event
    that more than one application fits is choosing the application it currently has: an assignment, audited as such."""
    ev = conn.execute("SELECT * FROM outcome_events WHERE id = ? AND needs_review = 1", (event_id,)).fetchone()
    if ev is None:
        return False
    if approve and ev["ambiguous"]:
        return assign(conn, event_id, ev["application_id"])
    with conn:
        conn.execute("UPDATE outcome_events SET needs_review = ?, classifier = CASE WHEN ? THEN 'human' ELSE classifier END "
                     "WHERE id = ? AND needs_review = 1", (0 if approve else 2, int(approve), event_id))
        _audit(conn, event_id, "approve" if approve else "reject", ev["application_id"], ev["application_id"])
    return True


def _counted(ev) -> bool:
    return ev["needs_review"] == 0 and not ev["ambiguous"]


def relabel(conn, event_id: int, stage: str, detail: str = "set by the candidate") -> bool:
    """The candidate sets an event's stage by hand, by EVENT id (the id `outcomes --review` and `outcomes events` show). The
    event becomes the candidate's decision: approved and counted, audited, and the derived confirmation state of its
    application follows (a confirmation relabelled as an interview stops confirming; an interview relabelled as a
    confirmation confirms). A rejected or discarded event is revived by an explicit relabel (the candidate's decision overrides)."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}")
    ev = conn.execute("SELECT * FROM outcome_events WHERE id = ?", (event_id,)).fetchone()
    if ev is None:
        return False
    with conn:
        if ev["stage"] == "confirmation" and stage != "confirmation" and _counted(ev):
            _move_confirmation(conn, dict(ev), ev["application_id"], None)
        conn.execute("UPDATE outcome_events SET stage = ?, classifier = 'human', needs_review = CASE WHEN ambiguous = 1 THEN needs_review "
                     "ELSE 0 END WHERE id = ?", (stage, event_id))
        now = conn.execute("SELECT * FROM outcome_events WHERE id = ?", (event_id,)).fetchone()
        if stage == "confirmation" and _counted(now):
            _confirm(conn, now["application_id"], now["received_at"], now["message_id"], f"{now['sender']}: {now['subject']}")
        _audit(conn, event_id, "relabel", ev["application_id"], ev["application_id"], f"{ev['stage']} -> {stage}: {detail}")
    return True


def discard(conn, event_id: int, detail: str = "removed by the candidate") -> bool:
    """Remove an event from the analysis, whether it is waiting for review or was already counted (an accepted false positive,
    for instance). The row is kept, marked rejected, so the same message is never added again; the application's derived
    confirmation state is withdrawn if this event gave it; replies that took their association from this event
    (`inherited_from`) lose that basis and go back to review. Audited. No SQL needed."""
    ev = conn.execute("SELECT * FROM outcome_events WHERE id = ? AND needs_review != 2", (event_id,)).fetchone()
    if ev is None:
        return False
    with conn:
        if _counted(ev):
            _move_confirmation(conn, dict(ev), ev["application_id"], None)
        conn.execute("UPDATE outcome_events SET needs_review = 2, ambiguous = 0 WHERE id = ?", (event_id,))
        _audit(conn, event_id, "discard", ev["application_id"], None, detail)
        for d in conn.execute("SELECT * FROM outcome_events WHERE inherited_from = ? AND id != ? AND needs_review != 2 AND manual = 0",
                              (event_id, event_id)).fetchall():
            if _counted(d):
                _move_confirmation(conn, dict(d), d["application_id"], None)
            conn.execute("UPDATE outcome_events SET needs_review = 1, inherited_from = NULL WHERE id = ?", (d["id"],))
            conn.execute("INSERT OR IGNORE INTO outcome_candidates (event_id, application_id, score, evidence) VALUES (?, ?, 0, "
                         "'the event it followed was discarded')", (d["id"], d["application_id"]))
            _audit(conn, d["id"], "requeue", d["application_id"], d["application_id"], f"the event it followed ({event_id}) was discarded")
    return True


def label(conn, ref: str, stage: str) -> bool:
    """Set an event's stage by hand. `ref` is the event id (a number) or, for the first real outcome emails that teach the
    fixtures, the stored message's Message-ID / durable key. Several stored rows can share a Message-ID; each is relabelled."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}")
    if str(ref).isdigit():
        return relabel(conn, int(ref), stage)
    ids = [r["id"] for r in conn.execute("SELECT id FROM outcome_events WHERE (message_id = ? OR header_message_id = ?)",
                                          (ref, ref))]
    return bool(ids) and all([relabel(conn, i, stage) for i in ids])


def add_manual(conn, application_id: int, stage: str, when: datetime | None = None, note: str = "") -> int:
    """Record an outcome that did not arrive by email (a phone call, a LinkedIn message, a text from a recruiter) as the candidate's own
    event: counted at once, marked `manual`, audited, and its derived state follows (a manual confirmation confirms the application).
    The date is when it happened (default now) and may not precede the submission. Returns the new event id. Raises ValueError for an
    unknown stage, an unknown or unsubmitted application, or a date before the submission."""
    import uuid

    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}")
    app = conn.execute("SELECT * FROM applications WHERE id = ? AND state IN ('submitted', 'confirmed') AND submitted_at IS NOT NULL",
                       (application_id,)).fetchone()
    if app is None:
        raise ValueError(f"application {application_id} is not a submitted application")
    when = when or datetime.now(UTC)
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    if when < _parse(app["submitted_at"]) - timedelta(minutes=2):
        raise ValueError("an outcome cannot precede the submission")
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    key = f"manual:{uuid.uuid4().hex[:12]}"
    with conn:
        cur = conn.execute(
            "INSERT INTO outcome_events (application_id, stage, message_id, sender, subject, excerpt, received_at, classifier, confidence, "
            "needs_review, manual, inherited_from) VALUES (?, ?, ?, ?, ?, ?, ?, 'human', 1.0, 0, 1, 0)",
            (application_id, stage, key, "candidate", f"{stage} (recorded by the candidate)", (note or "")[:EXCERPT], stamp))
        eid = cur.lastrowid
        _audit(conn, eid, "add", None, application_id, note[:200] or "recorded by the candidate, outside email")
        if stage == "confirmation":
            _confirm(conn, application_id, stamp, key, "candidate: recorded by hand")
    return eid


def events(conn, application_id: int | None = None) -> list:
    """Every stored event with its id (counted, waiting for review, or discarded), newest first - what the candidate needs to
    find an accepted false positive and discard or relabel it."""
    q = ("SELECT e.id, e.application_id, a.company, a.title AS job_title, e.stage, e.received_at, e.subject, e.sender, "
         "CASE e.needs_review WHEN 0 THEN CASE WHEN e.ambiguous = 1 THEN 'ambiguous' ELSE 'counted' END WHEN 1 THEN 'waiting' ELSE 'discarded' END AS state, "
         "e.classifier FROM outcome_events e JOIN applications a ON a.id = e.application_id")
    args: tuple = ()
    if application_id is not None:
        q += " WHERE e.application_id = ?"
        args = (application_id,)
    return conn.execute(q + " ORDER BY e.received_at DESC, e.id DESC", args).fetchall()
