import pytest
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import yaml

from jobagent import db
from jobagent.tools import outcomes as O
from jobagent.tools.inbox import Mail

FIXTURES = yaml.safe_load((Path(__file__).parent.parent / "fixtures" / "outcome_emails" / "emails.yaml").read_text())
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)          # the submission
ISO = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731


UIDS = {f["id"]: str(1000 + i) for i, f in enumerate(FIXTURES)}        # numeric mailbox UIDs, as a real server assigns them


def mail_of(f, when=None, uid=None, thread=""):
    return Mail(uid or UIDS[f["id"]], f"<{f['id']}@x>", f["from"], f["subject"], when or T0 + timedelta(days=2), f["body"], thread)


# --- the labelled set --------------------------------------------------------------------------------------------

def test_there_are_at_least_forty_labelled_emails():
    assert len(FIXTURES) >= 40


def test_rules_alone_reach_the_gate_and_never_store_a_non_outcome():
    got = {f["id"]: O.classify(mail_of(f), model=None).stage for f in FIXTURES}
    wrong = {f["id"]: (f["expect"], got[f["id"]]) for f in FIXTURES
             if got[f["id"]] != (None if f["expect"] == "none" else f["expect"]) and not f.get("model")}
    plain = [f for f in FIXTURES if not f.get("model")]
    accuracy = 1 - len(wrong) / len(plain)
    assert accuracy >= 0.9, wrong
    # the priority: nothing that is not an outcome may be stored as one
    false_outcomes = [f["id"] for f in FIXTURES if f["expect"] == "none" and got[f["id"]] is not None]
    assert false_outcomes == [], false_outcomes


def test_the_messages_written_for_the_model_are_left_undecided_by_the_rules():
    for f in (x for x in FIXTURES if x.get("model")):
        v = O.classify(mail_of(f), model=None)
        assert v.stage is None and v.confidence < O.REVIEW_BELOW, f["id"]


def test_the_model_reads_what_the_rules_leave_and_a_failing_model_drops_the_message():
    f = next(x for x in FIXTURES if x["id"] == "model-soft-rejection")
    ok = O.classify(mail_of(f), model=lambda msgs: SimpleNamespace(stage="rejection", confidence=0.93, evidence="our team has chosen a different direction for this role"))
    assert (ok.stage, ok.classifier) == ("rejection", "model")
    assert O.classify(mail_of(f), model=lambda msgs: (_ for _ in ()).throw(RuntimeError("down"))).stage is None
    junk = O.classify(mail_of(f), model=lambda msgs: SimpleNamespace(stage="banana", confidence=0.9, evidence=""))
    assert junk.stage is None


def test_a_confirmation_that_says_unfortunately_is_still_a_confirmation():
    f = next(x for x in FIXTURES if x["id"] == "gh-confirm-with-unfortunately")
    assert O.classify(mail_of(f), model=None).stage == "confirmation"


# --- sync against a fake mailbox -----------------------------------------------------------------------------------

class FakeInbox:
    """Honours the server-side filters the sync uses: SINCE, UID n:*, FROM (OR'd) and X-GM-THRID."""
    def __init__(self, mails, uidvalidity="7"):
        self.mails = {m.uid: m for m in mails}
        self.searches, self.header_fetches, self.text_fetches = [], [], []
        self.uv = uidvalidity

    def uidvalidity(self, folder="INBOX"):
        return self.uv

    transient: dict = None      # uid -> how many more header fetches fail with a transient error
    gone: set = None            # uids the server says no longer exist

    def search(self, criteria, folder="INBOX"):
        self.searches.append(criteria)
        mid = re.search(r'HEADER Message-ID "(.+?)"', criteria)
        if mid:
            return [u for u, m in self.mails.items() if m.message_id == mid.group(1)]
        thr = re.search(r"X-GM-THRID (\d+)", criteria)
        if thr:
            return [u for u, m in self.mails.items() if m.thread_id == thr.group(1)]
        senders = re.findall(r'FROM "(.+?)"', criteria)
        uid_from = re.search(r"UID (\d+):\*", criteria)
        if uid_from:
            return [u for u, m in self.mails.items() if int(u) >= int(uid_from.group(1)) and any(s.lower() in m.sender.lower() for s in senders)]
        since = datetime.strptime(re.search(r'SINCE "(.+?)"', criteria).group(1), "%d-%b-%Y").replace(tzinfo=UTC)
        return [u for u, m in self.mails.items() if (m.delivered or m.date or since) >= since and any(s.lower() in m.sender.lower() for s in senders)]

    def fetch_headers(self, uid, folder="INBOX"):
        if self.transient and self.transient.get(uid, 0) > 0:
            self.transient[uid] -= 1
            raise OSError("connection reset")
        if uid not in self.mails or (self.gone and uid in self.gone):
            raise LookupError(uid)
        import dataclasses
        self.header_fetches.append(uid)
        return dataclasses.replace(self.mails[uid], text="", has_text=False, uidvalidity=self.uv)

    def fetch_text(self, head, folder="INBOX"):
        import dataclasses
        self.text_fetches.append(head.uid)
        return dataclasses.replace(self.mails[head.uid], uidvalidity=self.uv)

    def fetch(self, uid, folder="INBOX"):
        return self.fetch_text(self.fetch_headers(uid))


@pytest.fixture(autouse=True)
def verified_company_domains(monkeypatch):
    """Company sender domains come from verified metadata only (F09); these fixtures stand in for the research profile."""
    from jobagent.steps import submit as S
    known = {"stripe": {"stripe.com"}, "general mills": {"generalmills.com"}}
    monkeypatch.setattr(S, "company_domains", lambda company, jd: set(known.get((company or "").lower(), set())))


def make_db(tmp_path, apps=(("Stripe", "Backend Engineer", "https://job-boards.greenhouse.io/stripe/jobs/1"),)):
    conn = db.connect(tmp_path / "jobs.sqlite")
    for i, (company, title, url) in enumerate(apps, 1):
        conn.execute("INSERT INTO applications (job_dir, company, title, url, state, submitted_at) VALUES (?,?,?,?,?,?)",
                     (f"outputs/{company.lower()}/{i}", company, title, url, "submitted", ISO(T0)))
    return conn


def fx(name):
    return next(f for f in FIXTURES if f["id"] == name)


def test_sync_stores_matching_outcomes_confirms_the_application_and_dedupes(tmp_path):
    conn = make_db(tmp_path)
    mails = [mail_of(fx("gh-confirm-with-unfortunately"), T0 + timedelta(minutes=5), "1"),
             mail_of(fx("reject-other-candidates"), T0 + timedelta(days=9), "2"),
             mail_of(fx("gh-confirm"), T0 + timedelta(days=1), "3"),            # Palantir, same ATS sender: not this application
             mail_of(fx("none-job-alert"), T0 + timedelta(days=1), "4"),         # Palantir alert
             mail_of(fx("none-newsletter"), T0 + timedelta(days=1), "5")]        # Stripe newsletter: matched, then dropped
    box = FakeInbox(mails)
    res = O.sync(box, conn, as_of=T0 + timedelta(days=10), model=None, log=lambda m: None)
    got = {r["stage"] for r in conn.execute("SELECT stage FROM outcome_events")}
    assert got == {"confirmation", "rejection"} and len(res.stored) == 2
    assert conn.execute("SELECT state FROM applications").fetchone()[0] == "confirmed"
    assert conn.execute("SELECT COUNT(*) FROM outcome_events WHERE excerpt = ''").fetchone()[0] == 0
    # nothing but the short excerpt is kept: no body anywhere in the table
    assert max(len(r[0] or "") for r in conn.execute("SELECT excerpt FROM outcome_events")) <= O.EXCERPT
    again = O.sync(box, conn, as_of=T0 + timedelta(days=11), model=None, log=lambda m: None)
    assert again.stored == [] and conn.execute("SELECT COUNT(*) FROM outcome_events").fetchone()[0] == 2


def test_the_mailbox_is_never_searched_without_a_sender_filter(tmp_path):
    conn = make_db(tmp_path)
    box = FakeInbox([])
    O.sync(box, conn, as_of=T0 + timedelta(days=3), model=None, log=lambda m: None)
    assert box.searches and all('FROM "' in c or "X-GM-THRID" in c for c in box.searches)
    assert "greenhouse" in box.searches[0] and "stripe.com" in box.searches[0]
    # "General" is too common a first word to filter a shared mailbox by; the company's domain is used instead
    c2 = make_db(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path,
                 apps=(("General Mills", "Data Scientist", "https://genmills.wd1.myworkdayjobs.com/x/job/1"),))
    box2 = FakeInbox([])
    O.sync(box2, c2, as_of=T0 + timedelta(days=3), model=None, log=lambda m: None)
    assert 'FROM "general"' not in box2.searches[0] and 'FROM "generalmills.com"' in box2.searches[0]


def test_mail_from_before_the_submission_or_after_now_is_ignored(tmp_path):
    conn = make_db(tmp_path)
    box = FakeInbox([mail_of(fx("gh-confirm-with-unfortunately"), T0 - timedelta(days=3), "1"),
                     mail_of(fx("reject-other-candidates"), T0 + timedelta(days=30), "2")])
    res = O.sync(box, conn, as_of=T0 + timedelta(days=5), model=None, log=lambda m: None)
    assert res.stored == []


def test_a_reply_in_a_thread_an_outcome_started_is_linked_even_from_a_personal_address(tmp_path):
    conn = make_db(tmp_path)
    first = mail_of(fx("interview-select-time"), T0 + timedelta(days=4), "1", thread="777")
    reply = Mail("2", "<r1@x>", "Jamie Lee <jamie.lee.recruiter@gmail.com>", "Re: Next steps: schedule your interview",
                 T0 + timedelta(days=5), "Unfortunately, we will not be moving forward after the interview.", "777")
    box = FakeInbox([first, reply])
    O.sync(box, conn, as_of=T0 + timedelta(days=6), model=None, log=lambda m: None)
    first_pass = [r["stage"] for r in conn.execute("SELECT stage FROM outcome_events ORDER BY received_at")]
    assert first_pass == ["interview"]                       # the personal address alone isn't enough on the first pass
    O.sync(box, conn, as_of=T0 + timedelta(days=7), model=None, log=lambda m: None)
    assert [r["stage"] for r in conn.execute("SELECT stage FROM outcome_events ORDER BY received_at")] == ["interview", "rejection"]
    view = dict(conn.execute("SELECT * FROM application_status").fetchone())
    assert (view["current_stage"], view["furthest_stage"]) == ("rejection", "interview")


def test_low_confidence_and_ties_wait_for_the_candidate(tmp_path):
    conn = make_db(tmp_path, apps=(("Stripe", "Backend Engineer", "https://job-boards.greenhouse.io/stripe/jobs/1"),
                                   ("Stripe", "Backend Engineer", "https://job-boards.greenhouse.io/stripe/jobs/2")))
    box = FakeInbox([mail_of(fx("reject-other-candidates"), T0 + timedelta(days=3), "1")])
    res = O.sync(box, conn, as_of=T0 + timedelta(days=4), model=None, log=lambda m: None)
    assert len(res.stored) == 1 and res.stored[0]["needs_review"] == 1           # two equally good applications
    assert conn.execute("SELECT COUNT(*) FROM application_status WHERE current_stage IS NOT NULL").fetchone()[0] == 0
    ev = O.pending_review(conn)[0]
    assert O.decide(conn, ev["id"], approve=True) and O.pending_review(conn) == []
    assert conn.execute("SELECT current_stage FROM application_status WHERE application_id = ?", (ev["application_id"],)).fetchone()[0] == "rejection"


def test_a_rejected_event_is_never_added_again_and_a_label_overrides_the_classifier(tmp_path):
    conn = make_db(tmp_path)
    low = mail_of({**fx("model-soft-interview"), "from": "Stripe Recruiting <recruiter@stripe.com>"}, T0 + timedelta(days=3), "1")
    model = lambda msgs: SimpleNamespace(stage="interview", confidence=0.55, evidence="Would Thursday at 2pm PT work for a conversation with the hiring manager?")  # noqa: E731
    box = FakeInbox([low])
    O.sync(box, conn, as_of=T0 + timedelta(days=4), model=model, log=lambda m: None)
    ev = O.pending_review(conn)[0]
    assert O.decide(conn, ev["id"], approve=False)
    O.sync(box, conn, as_of=T0 + timedelta(days=5), model=model, log=lambda m: None)
    assert conn.execute("SELECT COUNT(*), MAX(needs_review) FROM outcome_events").fetchone()[:] == (1, 2)
    assert O.label(conn, low.message_id, "offer") and conn.execute(
        "SELECT stage, classifier, needs_review FROM outcome_events").fetchone()[:] == ("offer", "human", 0)


def test_dry_run_writes_nothing_and_new_interviews_are_announced(tmp_path):
    conn = make_db(tmp_path)
    box = FakeInbox([mail_of(fx("interview-select-time"), T0 + timedelta(days=2), "1")])
    said = []
    dry = O.sync(box, conn, as_of=T0 + timedelta(days=3), model=None, dry_run=True, log=said.append)
    assert len(dry.stored) == 1 and conn.execute("SELECT COUNT(*) FROM outcome_events").fetchone()[0] == 0 and said == []
    assert conn.execute("SELECT COUNT(*) FROM kv WHERE key = 'outcome_last_sync'").fetchone()[0] == 0
    O.sync(box, conn, as_of=T0 + timedelta(days=3), model=None, log=said.append)
    assert said and "NEW INTERVIEW" in said[0] and "Stripe" in said[0]
