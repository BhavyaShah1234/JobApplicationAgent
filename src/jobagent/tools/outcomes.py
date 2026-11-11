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


def _sentence(text: str, m: re.Match) -> str:
    start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
    ends = [i for i in (text.find(".", m.end()), text.find("\n", m.end())) if i != -1]
    return re.sub(r"\s+", " ", text[start:(min(ends) + 1 if ends else m.end() + 120)]).strip()[:EXCERPT]


QUOTED = re.compile(r"(?m)^(On .{8,140} wrote:|-{2,} ?Original Message ?-{2,}|_{5,}|From:\s.+)$")


def fresh_text(mail) -> str:
    """Subject + the new part of the body: a reply's "Re: ..." subject and the quoted earlier message are what the
    earlier mail said, not what this one says."""
    subject = "" if re.match(r"(?i)^\s*(re|fwd?|aw):", mail.subject) else mail.subject
    body = QUOTED.split(mail.text[:6000])[0]
    body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith(">"))
    return f"{subject}\n{body[:3000]}".replace("\u2019", "'").replace("\u2018", "'")


# "we will contact you to schedule an interview if ..." is what a confirmation says, not an invitation
CONDITIONAL = re.compile(r"(?i)\b(if|should|may|might|in the event|once we|when we|would be)\b|"
                         r"will (contact|reach out|be in touch|notify|email|get back)")


# words that mean a message reports more than receipt, whatever else it says
PROGRESS = re.compile(r"(?i)\b(decided|next steps?|move forward|moving forward|regret|congratulations|excited to|pleased to|"
                      r"interview for|schedule|invite you|offer)\b")


def unconditional(text: str) -> str:
    """The text without sentences that only say what would happen ("if your qualifications match, we will ...")."""
    return " ".join(s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if not CONDITIONAL.search(s))


def stage_hits(text: str) -> dict[str, re.Match]:
    """Stages whose phrases appear. For the stages a confirmation talks about in the future (interview, screen,
    assessment, offer), a phrase inside a conditional sentence doesn't count."""
    hits = {}
    for k, r in RULES.items():
        m = r.search(text)
        while m and k in ("interview", "recruiter_screen", "assessment", "offer") and CONDITIONAL.search(_sentence(text, m)):
            m = r.search(text, m.end())          # look for a later, unconditional mention
        if m:
            hits[k] = m
    return hits


def rule_verdict(mail) -> Verdict:
    """Phrase rules over subject + the first 3,000 characters of the body."""
    from jobagent.tools.inbox import classify_verification

    text = fresh_text(mail)
    strong = bool(stage_hits(text))
    if classify_verification(mail) is not None:
        return Verdict(None, 0.95, "rule", "a verification code or link")
    # an alert or newsletter subject, or one in the body, unless the message also reports a stage; a job-alert footer
    # on a real confirmation (subject says so) doesn't hide it
    if not strong and (NOT_OUTCOME.search(mail.subject) or (NOT_OUTCOME.search(text)
                                                            and not CONFIRMATION.search(mail.subject))):
        return Verdict(None, 0.95, "rule", "a job alert or newsletter")
    hits = stage_hits(text)
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


def model_verdict(mail, model=None) -> Verdict:
    """The fast model reads one message that the rules could not decide."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from pydantic import BaseModel, Field

    from jobagent import llm

    class Reading(BaseModel):
        stage: str = Field(description="one of: " + ", ".join(STAGES) + ", none")
        confidence: float = Field(ge=0, le=1)
        evidence: str = Field(max_length=180, description="the sentence of the email that says so")

    msgs = [SystemMessage(
        "You read one email a company or its applicant-tracking system sent about the candidate's job application and "
        "say what stage it reports: confirmation (an automated acknowledgement that the application was received), "
        "assessment (a coding challenge or test to take), recruiter_screen (a call with a recruiter being arranged), "
        "interview (an interview being scheduled or confirmed), offer, rejection (the company is not moving forward), "
        "withdrawn (the application was withdrawn), or none (job alerts, newsletters, marketing, a verification code, "
        "anything that is not about this application's progress, and account-created or welcome messages). A confirmation "
        "is only an acknowledgement that a specific application was received, never an account or marketing message. A rejection that offers to keep the resume on file is "
        "still a rejection. Quote the deciding sentence; never invent one."),
        HumanMessage(f"From: {mail.sender}\nSubject: {mail.subject}\n\n{mail.text[:3000]}")]
    out = (model or (lambda m: llm.structured(Reading, "fast", timeout=60).invoke(m)))(msgs)
    stage = out.stage.strip().lower().replace(" ", "_")
    return Verdict(stage if stage in STAGES else None, float(out.confidence), "model", out.evidence[:EXCERPT])


def classify(mail, model="model") -> Verdict:
    """Rules first; the model when they find none or several stages; `model=None` keeps the rules only."""
    v = rule_verdict(mail)
    if v.stage is not None or v.confidence >= 0.9 or model is None:
        return v
    try:
        mv = model_verdict(mail, None if model == "model" else model)
        # a model that calls something a confirmation must have something about an application to point at
        if mv.stage == "confirmation" and not re.search(r"(?i)applic|applied|applying", f"{mail.subject} {mail.text[:3000]}"):
            return Verdict(None, 0.9, "model", "no mention of an application")
        return mv
    except Exception:
        return Verdict(None, 0.0, "model", "the model could not read it", v.stages_seen)


# --- linking and storing -------------------------------------------------------------------------------------------

@dataclass
class SyncResult:
    stored: list = field(default_factory=list)       # dicts of the events written (or that would be, on a dry run)
    seen: int = 0                                     # messages fetched
    dropped: dict = field(default_factory=dict)       # reason -> count
    applications: int = 0

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


def sync(inbox, conn, as_of: datetime | None = None, since_days: int | None = None, model="model",
         dry_run: bool = False, log=print) -> SyncResult:
    """Read, classify and link the mail of every submitted application; store the new outcome events."""
    from jobagent.steps.submit import application_match

    as_of = as_of or datetime.now(UTC)
    res = SyncResult()
    apps = conn.execute("SELECT * FROM applications WHERE state IN ('submitted', 'confirmed') AND submitted_at IS NOT NULL "
                        "ORDER BY id").fetchall()
    res.applications = len(apps)
    last = conn.execute("SELECT value FROM kv WHERE key = 'outcome_last_sync'").fetchone()
    cursor = _parse(last[0]) - timedelta(days=2) if last and since_days is None else None
    if not dry_run:
        from jobagent.tools import outcome_stats
        for a in apps:
            if a["resume_sha"] is None:  # older rows, or submitted before the snapshot existed
                try:
                    outcome_stats.snapshot(conn, a["job_dir"])
                except Exception:
                    pass
    matchers = {a["id"]: application_match(conn, _job_json(a)) for a in apps}
    claimed: dict[str, list[tuple[int, int, object]]] = {}   # message_id -> [(score, application id, mail)]
    for a in apps:
        m = matchers[a["id"]]
        submitted = _parse(a["submitted_at"])
        floor = submitted - timedelta(days=1)
        if since_days is not None:
            floor = max(floor, as_of - timedelta(days=since_days))
        elif cursor:
            floor = max(floor, cursor)
        word = m.company.split()[0] if m.company else ""
        terms = sorted(m.allowed) + ([word] if len(word) >= 6 and word not in GENERIC_WORDS else [])
        if not terms:
            continue  # never an unfiltered search of a shared mailbox
        criteria = f'SINCE "{floor.strftime("%d-%b-%Y")}" {_or_from(terms)}'.strip()
        uids = list(inbox.search(criteria))
        # replies in a thread an earlier outcome of this application started (a recruiter writing from a personal address)
        threads = [r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM outcome_events WHERE application_id = ? "
                                              "AND thread_id != '' AND needs_review != 2", (a["id"],))]
        for t in threads:
            uids += [u for u in inbox.search(f"X-GM-THRID {t}") if u not in uids]
        for uid in uids:
            try:
                mail = inbox.fetch(uid)
            except LookupError:
                continue
            res.seen += 1
            if mail.date is None or mail.date < submitted - timedelta(minutes=2) or mail.date > as_of + timedelta(days=1):
                res.drop("outside the application's dates")
                continue
            in_thread = bool(mail.thread_id) and mail.thread_id in threads
            if not (m(mail) or in_thread):
                res.drop("not about this application")
                continue
            claimed.setdefault(mail.message_id, []).append((_score(m, mail) + (1 if in_thread else 0), a["id"], mail))
    seen_ids = {r[0] for r in conn.execute("SELECT message_id FROM outcome_events")}
    for message_id, cands in claimed.items():
        if message_id in seen_ids:
            res.drop("already stored")
            continue
        cands.sort(key=lambda c: -c[0])
        _, app_id, mail = cands[0]
        tie = len(cands) > 1 and cands[1][0] == cands[0][0] and cands[1][1] != cands[0][1]
        v = classify(mail, model)
        if v.stage is None:
            res.drop("not an outcome" if v.confidence >= 0.5 else "could not tell")
            continue
        review = int(tie or v.confidence < REVIEW_BELOW)
        event = {"application_id": app_id, "stage": v.stage, "message_id": message_id, "thread_id": mail.thread_id,
                 "sender": mail.sender[:200], "subject": mail.subject[:200], "excerpt": v.evidence[:EXCERPT],
                 "received_at": _utc(mail.date), "classifier": v.classifier, "confidence": round(v.confidence, 2),
                 "needs_review": review}
        res.stored.append(event)
        if dry_run:
            continue
        with conn:
            conn.execute("INSERT OR IGNORE INTO outcome_events (application_id, stage, message_id, thread_id, sender, "
                         "subject, excerpt, received_at, classifier, confidence, needs_review) VALUES "
                         "(:application_id, :stage, :message_id, :thread_id, :sender, :subject, :excerpt, :received_at, "
                         ":classifier, :confidence, :needs_review)", event)
            if v.stage == "confirmation" and not review:
                conn.execute("UPDATE applications SET state = 'confirmed', confirmed_at = COALESCE(confirmed_at, ?), "
                             "confirmation_id = COALESCE(confirmation_id, ?), confirmation = COALESCE(confirmation, ?) "
                             "WHERE id = ? AND state = 'submitted'", (event["received_at"], message_id,
                                                                      f"{mail.sender}: {mail.subject}"[:300], app_id))
        if v.stage in NOTIFY and not review:
            co = conn.execute("SELECT company, title FROM applications WHERE id = ?", (app_id,)).fetchone()
            log(f"outcomes: NEW {v.stage.upper()} for {co['company']} - {co['title']}: {mail.subject[:80]}")
    if not dry_run:
        with conn:
            conn.execute("INSERT INTO kv (key, value) VALUES ('outcome_last_sync', ?) ON CONFLICT(key) DO UPDATE SET "
                         "value = excluded.value", (_utc(as_of),))
    return res


# --- the candidate's decisions -------------------------------------------------------------------------------------

def pending_review(conn) -> list:
    return conn.execute("SELECT e.*, a.company, a.title AS job_title FROM outcome_events e JOIN applications a ON "
                        "a.id = e.application_id WHERE e.needs_review = 1 ORDER BY e.received_at").fetchall()


def decide(conn, event_id: int, approve: bool) -> bool:
    """Approve (counted from now on) or reject (kept as rejected so the same message is never re-added)."""
    with conn:
        cur = conn.execute("UPDATE outcome_events SET needs_review = ?, classifier = CASE WHEN ? THEN 'human' ELSE "
                           "classifier END WHERE id = ? AND needs_review = 1", (0 if approve else 2, int(approve), event_id))
    return cur.rowcount == 1


def label(conn, message_id: str, stage: str) -> bool:
    """The candidate sets a message's stage by hand (the first real outcome emails teach the fixtures this way)."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}")
    with conn:
        cur = conn.execute("UPDATE outcome_events SET stage = ?, classifier = 'human', needs_review = 0 WHERE message_id = ?",
                           (stage, message_id))
    return cur.rowcount == 1
