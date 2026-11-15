"""F10-F12: outcome mail is linked only through settled associations, ingestion loses nothing, and only a message that reports
THIS candidate's progression becomes an event."""
import dataclasses
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from jobagent import db
from jobagent.tools import inbox as I
from jobagent.tools import outcomes as O
from jobagent.tools.inbox import Mail
from test_outcomes import T0, FakeInbox, make_db, verified_company_domains  # noqa: F401  (autouse fixture is reused)

TWO_STRIPE = (("Stripe", "Backend Engineer", "https://job-boards.greenhouse.io/stripe/jobs/1"),
              ("Stripe", "Data Engineer", "https://job-boards.greenhouse.io/stripe/jobs/2"))
SENDER = "Stripe <no-reply@us.greenhouse-mail.io>"


def mail(uid, subject, body, when=None, sender=SENDER, thread="", mid=None, **kw):
    return Mail(str(uid), f"<m{uid}@x>" if mid is None else mid, sender, subject, when or T0 + timedelta(days=2), body, thread, **kw)


def run_sync(box, conn, days=3, model=None):
    return O.sync(box, conn, as_of=T0 + timedelta(days=days), model=model, log=lambda m: None)


def events(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM outcome_events ORDER BY id")]


# ============================================================ F10: thread associations =====================================================

CONFIRM = mail(2001, "Thank you for applying to Stripe", "Thank you for applying. We have received your application.", thread="900")
FOLLOW = mail(2002, "Re: Thank you for applying to Stripe", "Unfortunately, we will not be moving forward after the interview.",
              T0 + timedelta(days=4), sender="Jamie Lee <jamie.lee.recruiter@gmail.com>", thread="900")


def test_two_same_company_applications_produce_a_reviewable_ambiguity(tmp_path):
    conn = make_db(tmp_path, apps=TWO_STRIPE)
    run_sync(FakeInbox([CONFIRM]), conn)
    ev, = events(conn)
    assert ev["ambiguous"] == 1 and ev["needs_review"] == 1                         # not counted, waiting for the candidate
    cands = O.candidates(conn, ev["id"])
    assert {c["application_id"] for c in cands} == {1, 2} and all(c["evidence"] for c in cands)
    assert [r["id"] for r in O.pending_review(conn)] == [ev["id"]] and O.pending_review(conn)[0]["candidate_count"] == 2
    assert conn.execute("SELECT current_stage FROM application_status WHERE application_id = 1").fetchone()[0] is None


def test_before_assignment_a_follow_up_is_not_automatically_associated(tmp_path):
    conn = make_db(tmp_path, apps=TWO_STRIPE)
    box = FakeInbox([CONFIRM, FOLLOW])
    run_sync(box, conn)
    run_sync(box, conn, days=5)
    by_uid = {e["mailbox_uid"]: e for e in events(conn)}
    follow = by_uid["2002"]
    assert follow["ambiguous"] == 1 and follow["needs_review"] == 1                 # pending: it inherited the ambiguity, not an answer
    assert {c["application_id"] for c in O.candidates(conn, follow["id"])} == {1, 2}
    assert conn.execute("SELECT COUNT(*) FROM application_status WHERE current_stage IS NOT NULL").fetchone()[0] == 0


def test_an_unapproved_low_confidence_seed_cannot_attach_later_mail(tmp_path):
    conn = make_db(tmp_path)
    soft = mail(3001, "Re: Forward Deployed Software Engineer", "Would Thursday at 2pm PT work for a conversation with the hiring manager?",
                T0 + timedelta(days=3), sender="Stripe Recruiting <recruiter@stripe.com>", thread="55")
    follow = mail(3002, "Re: Forward Deployed Software Engineer", "Unfortunately, we will not be moving forward.", T0 + timedelta(days=4),
                  sender="Jamie <jamie@gmail.com>", thread="55")
    model = lambda msgs: SimpleNamespace(stage="interview", confidence=0.55, evidence="Would Thursday at 2pm PT work for a conversation with the hiring manager?")  # noqa: E731
    box = FakeInbox([soft, follow])
    run_sync(box, conn, days=4, model=model)
    run_sync(box, conn, days=5, model=model)
    es = {e["mailbox_uid"]: e for e in events(conn)}
    assert es["3001"]["needs_review"] == 1 and es["3002"]["needs_review"] == 1 and es["3002"]["ambiguous"] == 1


def test_choosing_the_other_application_works_is_audited_and_the_thread_follows(tmp_path):
    conn = make_db(tmp_path, apps=TWO_STRIPE)
    box = FakeInbox([CONFIRM, FOLLOW])
    run_sync(box, conn)
    run_sync(box, conn, days=5)
    seed = next(e for e in events(conn) if e["mailbox_uid"] == "2001")
    other = 2 if seed["application_id"] == 1 else 1
    assert O.assign(conn, seed["id"], other)
    es = {e["mailbox_uid"]: e for e in events(conn)}
    assert es["2001"]["application_id"] == other and es["2001"]["needs_review"] == 0 and es["2001"]["ambiguous"] == 0
    assert es["2002"]["application_id"] == other and es["2002"]["ambiguous"] == 0           # the waiting reply followed
    audit = [dict(r) for r in conn.execute("SELECT action, old_application_id, new_application_id FROM outcome_audit ORDER BY id")]
    assert audit[0] == {"action": "assign", "old_application_id": seed["application_id"], "new_application_id": other}
    assert any(a["action"] == "propagate" for a in audit)
    assert conn.execute("SELECT state FROM applications WHERE id = ?", (other,)).fetchone()[0] == "confirmed"
    # and it can be changed again, still auditable
    assert O.assign(conn, seed["id"], seed["application_id"]) and conn.execute("SELECT COUNT(*) FROM outcome_audit WHERE action = 'assign'").fetchone()[0] == 2
    assert not O.assign(conn, seed["id"], 999)                                       # not a submitted application


def test_approving_an_ambiguous_event_is_an_audited_assignment(tmp_path):
    conn = make_db(tmp_path, apps=TWO_STRIPE)
    run_sync(FakeInbox([CONFIRM]), conn)
    ev, = events(conn)
    assert O.decide(conn, ev["id"], True)
    now, = events(conn)
    assert now["ambiguous"] == 0 and now["needs_review"] == 0
    assert conn.execute("SELECT COUNT(*) FROM outcome_audit WHERE action = 'assign'").fetchone()[0] == 1


def test_an_approved_unambiguous_seed_still_attaches_a_recruiters_personal_address(tmp_path):
    conn = make_db(tmp_path)
    first = mail(4001, "Next steps: schedule your interview", "We would like to invite you to an interview. Please schedule an interview: calendly.com/x",
                 T0 + timedelta(days=4), sender="Stripe <jobs@stripe.com>", thread="777")
    reply = mail(4002, "Re: Next steps: schedule your interview", "Unfortunately, we will not be moving forward after the interview.",
                 T0 + timedelta(days=5), sender="Jamie <jamie.recruiter@gmail.com>", thread="777")
    box = FakeInbox([first, reply])
    run_sync(box, conn, days=6)
    run_sync(box, conn, days=7)
    stages = {e["mailbox_uid"]: (e["stage"], e["needs_review"], e["ambiguous"]) for e in events(conn)}
    assert stages == {"4001": ("interview", 0, 0), "4002": ("rejection", 0, 0)}


# ============================================================ F11: nothing is lost ========================================================

CONF = lambda uid, subject="Thank you for applying to Stripe", **kw: mail(uid, subject, "Thank you for applying. We have received your application.", **kw)  # noqa: E731


def test_a_model_outage_longer_than_the_overlap_loses_nothing(tmp_path):
    conn = make_db(tmp_path)
    hard = mail(5001, "An update on your Stripe application", "We took another look at everything you sent and we have made our choice. "
                "It is not the news you hoped for.", T0 + timedelta(days=2))                          # only the model can read this
    box = FakeInbox([hard])
    down = lambda msgs: (_ for _ in ()).throw(RuntimeError("model outage"))  # noqa: E731
    for day in (3, 6, 12, 20):                                                                       # far beyond the old 2-day overlap
        r = run_sync(box, conn, days=day, model=down)
        assert events(conn) == [] and r.retrying == 1
    led = conn.execute("SELECT state, attempts FROM mail_ledger").fetchone()
    assert (led["state"], led["attempts"]) == ("retry", 4)                                           # explicit, counted, not dropped
    up = lambda msgs: SimpleNamespace(stage="rejection", confidence=0.93, evidence="we have made our choice")  # noqa: E731
    run_sync(box, conn, days=21, model=up)
    ev, = events(conn)
    assert ev["stage"] == "rejection" and conn.execute("SELECT state FROM mail_ledger").fetchone()[0] == "stored"


def test_a_message_the_model_keeps_failing_on_is_eventually_handed_to_the_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "MAX_ATTEMPTS", 2)
    conn = make_db(tmp_path)
    box = FakeInbox([mail(5002, "An update on your Stripe application", "We took another look and made our choice. Not the news you hoped for.")])
    down = lambda msgs: (_ for _ in ()).throw(RuntimeError("down"))  # noqa: E731
    run_sync(box, conn, model=down)
    run_sync(box, conn, days=4, model=down)
    assert conn.execute("SELECT state FROM mail_ledger").fetchone()[0] == "undecided" and len(O.undecided(conn)) == 1
    key = "7:5002"
    assert O.requeue(conn, key) and conn.execute("SELECT state, attempts FROM mail_ledger").fetchone()[:] == ("retry", 0)
    run_sync(box, conn, days=5, model=down)
    run_sync(box, conn, days=6, model=down)
    assert O.dismiss(conn, key) and conn.execute("SELECT state FROM mail_ledger").fetchone()[0] == "unrelated"


def test_missing_and_duplicate_message_ids_do_not_collapse_distinct_mail(tmp_path):
    conn = make_db(tmp_path)
    a = CONF(6001, "Thank you for applying to Stripe (1)", mid="")
    b = CONF(6002, "Thank you for applying to Stripe (2)", mid="")
    c = CONF(6003, "Thank you for applying to Stripe (3)", mid="<same@x>")
    d = CONF(6004, "Thank you for applying to Stripe (4)", mid="<same@x>")
    run_sync(FakeInbox([a, b, c, d]), conn)
    es = events(conn)
    assert sorted(e["mailbox_uid"] for e in es) == ["6001", "6002", "6003", "6004"]
    assert len({e["message_id"] for e in es}) == 4 and {e["header_message_id"] for e in es} == {None, "<same@x>"}


def test_restart_and_duplicate_delivery_are_idempotent(tmp_path):
    conn = make_db(tmp_path)
    box = FakeInbox([CONF(6101), CONF(6102, "Thank you for applying to Stripe (2)")])
    run_sync(box, conn)
    first = events(conn)
    run_sync(FakeInbox(list(box.mails.values())), conn, days=4)                                     # a restart: a fresh connection
    run_sync(box, conn, days=5, )
    assert events(conn) == first
    assert conn.execute("SELECT COUNT(*) FROM mail_ledger").fetchone()[0] == 2
    # the same message re-delivered under a NEW UIDVALIDITY is recognised by header id + subject, not duplicated
    again = FakeInbox(list(box.mails.values()), uidvalidity="8")
    run_sync(again, conn, days=6)
    assert len(events(conn)) == 2


def test_late_delivery_is_found_by_uid_whatever_its_date_header_says(tmp_path):
    conn = make_db(tmp_path)
    box = FakeInbox([CONF(7001)])
    run_sync(box, conn, days=3)
    assert len(events(conn)) == 1
    delivered = T0 + timedelta(days=9)
    late = CONF(7050, "Thank you for applying to Stripe (late)", when=T0 + timedelta(days=1), delivered=delivered)     # old Date, new delivery
    box.mails[late.uid] = late
    run_sync(box, conn, days=10)
    es = {e["mailbox_uid"]: e for e in events(conn)}
    assert "7050" in es and es["7050"]["received_at"] == O._utc(delivered) and es["7050"]["delivered_at"] == O._utc(delivered)
    assert conn.execute("SELECT header_date FROM mail_ledger WHERE mailbox_uid = '7050'").fetchone()[0] == O._utc(T0 + timedelta(days=1))
    assert box.searches[-1].startswith("UID ")                                                         # the cursor is a UID, not a date


def test_missing_and_zoneless_date_headers_are_handled_predictably(tmp_path):
    raw = b"From: a@x.com\r\nSubject: Hi\r\nMessage-ID: <z@x>\r\nDate: Mon, 02 Nov 2026 10:00:00 -0000\r\n\r\nbody"
    zoneless = I.parse("1", raw)
    assert zoneless.date.tzinfo is not None and zoneless.date.utcoffset() == timedelta(0) and zoneless.date_ok
    junk = I.parse("2", raw.replace(b"Mon, 02 Nov 2026 10:00:00 -0000", b"not a date"))
    assert junk.date is None and junk.date_ok is False
    missing = I.parse("3", raw.replace(b"Date: Mon, 02 Nov 2026 10:00:00 -0000\r\n", b""))
    assert missing.date is None
    conn = make_db(tmp_path)
    nodate = dataclasses.replace(CONF(8001), date=None, date_ok=False, delivered=T0 + timedelta(days=2))
    run_sync(FakeInbox([nodate]), conn)
    ev, = events(conn)
    assert ev["received_at"] == O._utc(T0 + timedelta(days=2))                                       # placed by when it was delivered


def test_the_cursor_moves_only_after_the_messages_it_covers_are_recorded(tmp_path):
    conn = make_db(tmp_path)
    box = FakeInbox([CONF(9001), CONF(9002, "Thank you for applying to Stripe (2)")])
    real = box.fetch_text
    calls = {"n": 0}

    def flaky(head, folder="INBOX"):
        calls["n"] += 1
        if head.uid == "9002" and calls["n"] <= 2:
            raise RuntimeError("connection reset")
        return real(head, folder)
    box.fetch_text = flaky
    with pytest.raises(RuntimeError):
        run_sync(box, conn)
    assert conn.execute("SELECT COUNT(*) FROM kv WHERE key LIKE 'outcome_cursor:%'").fetchone()[0] == 0          # nothing advanced
    run_sync(box, conn, days=4)
    assert sorted(e["mailbox_uid"] for e in events(conn)) == ["9001", "9002"]
    assert conn.execute("SELECT value FROM kv WHERE key = 'outcome_cursor:1'").fetchone()[0] == "7:9002"


def test_unrelated_mail_is_recorded_once_and_never_read_again(tmp_path):
    conn = make_db(tmp_path)
    other = mail(9101, "Thank you for applying to Palantir", "Thank you for applying to Palantir.", sender="Palantir <no-reply@us.greenhouse-mail.io>")           # a shared ATS sender, another company
    box = FakeInbox([other])
    run_sync(box, conn)
    assert conn.execute("SELECT state FROM mail_ledger WHERE mailbox_uid = '9101'").fetchone()[0] == "unrelated"
    assert not hasattr(conn.execute("SELECT * FROM mail_ledger").fetchone(), "subject")                           # metadata only
    box.text_fetches.clear()
    run_sync(box, conn, days=4)
    assert box.text_fetches == []


# ============================================================ F12: descriptions are not events ============================================

def verdict(subject, body, **kw):
    return O.classify(Mail("1", "<x>", kw.pop("sender", "Stripe <jobs@stripe.com>"), subject, T0, body, **kw), model=None)


NEGATIVES = [
    ("Life at Stripe: how we hire", "Hi Bhavya,\n\nOur hiring process includes an online assessment, a technical interview and a final round with the team."),
    ("Prepare to shine", "Ace your online assessment with our prep course. 20% off this week only! Unsubscribe anytime."),
    ("Webinar: what to expect in an interview", "Join our webinar to learn what to expect in a technical interview at top companies. Register now."),
    ("Weekly hiring tips", "Candidates typically complete a coding challenge before the interview. Here are three tips to practice."),
    ("Our process", "Every application process has stages. In ours, applicants may be asked to take a technical assessment."),
    ("Stripe careers newsletter", "You can expect an online assessment as part of our interview process. View all open jobs."),
]


@pytest.mark.parametrize("subject,body", NEGATIVES)
def test_hiring_newsletters_and_marketing_are_not_outcomes(subject, body):
    assert verdict(subject, body).stage is None


def test_a_bulk_mailing_needs_an_explicit_personal_statement():
    body = "Complete the online assessment to see how you rank against other candidates."
    assert verdict("Practice", body, bulk=True).stage is None
    personal = "Hi Bhavya, you have been invited to take the online assessment for your application: https://hackerrank.com/x/abc"
    assert verdict("Your Stripe application", personal, bulk=True).stage == "assessment"


@pytest.mark.parametrize("subject,body,stage", [
    ("Next steps for your Stripe application", "Hi Bhavya,\n\nWe would like to interview you for the Backend Engineer role. Please schedule an interview: "
     "https://calendly.com/stripe/x\n\n--\nYou are receiving this because you applied. Unsubscribe | Job alerts | Weekly newsletter", "interview"),
    ("Your Stripe assessment", "Hi Bhavya,\n\nPlease complete the online assessment within 7 days: https://hackerrank.com/tests/abc\n\nView all open jobs | "
     "Unsubscribe", "assessment"),
    ("Your Stripe application", "Dear Bhavya, we regret to inform you that we have decided to move forward with other candidates.\n\nNewsletter | Unsubscribe", "rejection"),
])
def test_genuine_messages_with_newsletter_footers_are_still_recognised(subject, body, stage):
    assert verdict(subject, body).stage == stage


QUOTED_REPLY = ("Thanks for the update, I will check my calendar and get back to you.\n\nOn Mon, Nov 2, 2026 at 10:00 AM Stripe <r@stripe.com> wrote:\n"
                "> We would like to interview you. Please schedule an interview at https://calendly.com/stripe/x\n> and complete the online assessment.")


def test_a_neutral_reply_quoting_an_old_invitation_is_not_an_outcome_for_the_rules_or_the_model():
    assert verdict("Re: Next steps", QUOTED_REPLY, sender="Bhavya <b@gmail.com>").stage is None
    seen = {}

    def model(msgs):
        seen["text"] = msgs[1].content
        return SimpleNamespace(stage="interview", confidence=0.95, evidence="We would like to interview you.")      # from the quoted history
    m = Mail("1", "<x>", "Stripe <jobs@stripe.com>", "Re: Next steps", T0, QUOTED_REPLY)
    v = O.model_verdict(m, model)
    assert "calendly" not in seen["text"] and "would like to interview" not in seen["text"]              # the model never sees the quoted history
    assert v.stage is None and v.abstain                                                                 # and its evidence is not in the new text


def test_the_model_must_quote_a_sentence_that_reports_this_candidates_progress():
    m = Mail("1", "<x>", "Stripe <jobs@stripe.com>", "Hiring at Stripe", T0, "Hi Bhavya, our hiring process includes an online assessment and two interviews.")
    ok = O.model_verdict(m, lambda msgs: SimpleNamespace(stage="assessment", confidence=0.9, evidence="our hiring process includes an online assessment"))
    assert ok.stage is None and not ok.failed                                                            # a general statement
    made_up = O.model_verdict(m, lambda msgs: SimpleNamespace(stage="assessment", confidence=0.9, evidence="you must complete the assessment today"))
    assert made_up.stage is None and made_up.abstain
    unsure = O.model_verdict(m, lambda msgs: SimpleNamespace(stage="unsure", confidence=0.2, evidence=""))
    assert unsure.stage is None and unsure.abstain


def test_an_abstaining_model_leaves_the_message_with_the_candidate_not_stored(tmp_path):
    conn = make_db(tmp_path)
    vague = mail(9201, "Update", "We took another look at everything and have made our decision. It is not the news you hoped for.")
    run_sync(FakeInbox([vague]), conn, model=lambda msgs: SimpleNamespace(stage="unsure", confidence=0.1, evidence=""))
    assert events(conn) == [] and conn.execute("SELECT state FROM mail_ledger").fetchone()[0] == "undecided"


# ============================================================ second round (reviewer's counterexamples) =================================

def reassignment_world(tmp_path):
    conn = make_db(tmp_path, apps=TWO_STRIPE)
    box = FakeInbox([CONFIRM, FOLLOW])
    run_sync(box, conn)
    run_sync(box, conn, days=5)
    ev = {e["mailbox_uid"]: e for e in events(conn)}
    return conn, box, ev["2001"]["id"], ev["2002"]["id"]


def owner(conn, event_id):
    r = conn.execute("SELECT application_id, needs_review, ambiguous FROM outcome_events WHERE id = ?", (event_id,)).fetchone()
    return tuple(r)


def test_reassigning_the_seed_moves_the_dependent_replies_and_the_derived_state(tmp_path):                    # F10
    conn, box, seed, follow = reassignment_world(tmp_path)
    assert O.assign(conn, seed, 2)
    assert owner(conn, seed) == (2, 0, 0) and owner(conn, follow) == (2, 0, 0)
    assert conn.execute("SELECT state FROM applications WHERE id = 2").fetchone()[0] == "confirmed"
    assert O.assign(conn, seed, 1)                                                      # B -> A
    assert owner(conn, seed) == (1, 0, 0) and owner(conn, follow) == (1, 0, 0)          # the dependant follows the correction
    states = {r["id"]: r["state"] for r in conn.execute("SELECT id, state FROM applications")}
    assert states == {1: "confirmed", 2: "submitted"}                                    # app 2 lost the confirmation this event gave it
    assert O._events_seeding(conn, 1) == ["900"] and O._events_seeding(conn, 2) == []
    # a later recruiter reply attaches to the corrected owner only
    later = mail(2003, "Re: Thank you for applying to Stripe", "Unfortunately, we will not be moving forward.", T0 + timedelta(days=6),
                 sender="Jamie Lee <jamie.lee.recruiter@gmail.com>", thread="900")
    box.mails[later.uid] = later
    run_sync(box, conn, days=7)
    new = next(e for e in events(conn) if e["mailbox_uid"] == "2003")
    assert (new["application_id"], new["needs_review"], new["ambiguous"]) == (1, 0, 0)
    audit = [r["action"] for r in conn.execute("SELECT action FROM outcome_audit ORDER BY id")]
    assert audit.count("assign") == 2 and audit.count("propagate") == 2


def test_a_separate_human_decision_on_a_reply_survives_a_reassignment_of_the_seed(tmp_path):
    conn, box, seed, follow = reassignment_world(tmp_path)
    assert O.assign(conn, seed, 2)
    assert O.assign(conn, follow, 1)                                                    # the candidate decides this reply on its own
    assert O.assign(conn, seed, 1)
    assert owner(conn, follow) == (1, 0, 0)                                              # untouched by the seed's correction
    assert O.assign(conn, seed, 2)
    assert owner(conn, follow) == (1, 0, 0) and owner(conn, seed) == (2, 0, 0)          # the human override is kept ...
    assert O._events_seeding(conn, 1) == [] and O._events_seeding(conn, 2) == []        # ... but a conflicted thread seeds nothing
    assert "900" in O._pending_threads(conn)
    later = mail(2003, "Re: Thank you for applying to Stripe", "Unfortunately, we will not be moving forward.", T0 + timedelta(days=6),
                 sender="Jamie Lee <jamie.lee.recruiter@gmail.com>", thread="900")
    box.mails[later.uid] = later
    run_sync(box, conn, days=7)
    new = next(e for e in events(conn) if e["mailbox_uid"] == "2003")
    assert (new["needs_review"], new["ambiguous"]) == (1, 1)                              # pending, not inherited as unambiguous


# F11 --------------------------------------------------------------------------------------------------------------------------------

def test_a_repeated_message_id_and_subject_with_different_content_keep_both_messages(tmp_path):
    conn = make_db(tmp_path)
    a = mail(100, "Stripe application update", "Hi Bhavya, please complete the online assessment within 7 days: https://hackerrank.com/tests/abc",
             mid="<reused@sender>")
    b = mail(101, "Stripe application update", "Hi Bhavya, we would like to interview you. Please schedule an interview: https://calendly.com/stripe/x",
             mid="<reused@sender>")
    run_sync(FakeInbox([a, b]), conn)
    got = {e["mailbox_uid"]: e["stage"] for e in events(conn)}
    assert got == {"100": "assessment", "101": "interview"}
    assert conn.execute("SELECT COUNT(*) FROM mail_ledger WHERE state = 'stored'").fetchone()[0] == 2
    # a genuine re-delivery of the first (same id, subject AND content, new UID) is still one event
    again = dataclasses.replace(a, uid="150")
    box = FakeInbox([a, b, again])
    run_sync(box, conn, days=4)
    assert len(events(conn)) == 2


def test_a_queued_retry_does_not_read_the_message_that_now_holds_its_old_uid(tmp_path):
    conn = make_db(tmp_path)
    candidate = mail(100, "An update on your Stripe application", "We took another look at everything you sent and we have made our choice. "
                     "It is not the news you hoped for.", mid="<orig@stripe>")
    down = lambda msgs: (_ for _ in ()).throw(RuntimeError("model outage"))  # noqa: E731
    run_sync(FakeInbox([candidate]), conn, model=down)
    assert conn.execute("SELECT state, uidvalidity FROM mail_ledger").fetchone()[:] == ("retry", "7")
    stranger = Mail("100", "<other@x>", "Private <private@unrelated.example>", "Personal membership update", T0 + timedelta(days=2),
                    "We regret to inform you that your membership has lapsed and we will not be moving forward with it.")
    box = FakeInbox([stranger], uidvalidity="8")                                       # renumbered: UID 100 is someone else's mail now
    up = lambda msgs: SimpleNamespace(stage="rejection", confidence=0.95, evidence="we have made our choice")  # noqa: E731
    run_sync(box, conn, days=4, model=up)
    assert events(conn) == [] and "100" not in box.text_fetches                          # nothing read, nothing attributed
    old = conn.execute("SELECT state, reason FROM mail_ledger WHERE uidvalidity = '7' AND mailbox_uid = '100'").fetchone()
    assert old["state"] == "undecided" and "renumbered" in old["reason"]                 # explicit and recoverable
    assert conn.execute("SELECT COUNT(*) FROM outcome_events").fetchone()[0] == 0


def test_a_retry_whose_message_can_be_found_again_by_message_id_is_relocated_and_processed(tmp_path):
    conn = make_db(tmp_path)
    candidate = mail(100, "An update on your Stripe application", "We took another look at everything you sent and we have made our choice. "
                     "It is not the news you hoped for.", mid="<orig@stripe>")
    down = lambda msgs: (_ for _ in ()).throw(RuntimeError("down"))  # noqa: E731
    run_sync(FakeInbox([candidate]), conn, model=down)
    moved = dataclasses.replace(candidate, uid="205")                                  # the same message, renumbered
    stranger = Mail("100", "<other@x>", "Private <private@unrelated.example>", "Personal", T0 + timedelta(days=2), "hello")
    box = FakeInbox([stranger, moved], uidvalidity="8")
    up = lambda msgs: SimpleNamespace(stage="rejection", confidence=0.95, evidence="we have made our choice")  # noqa: E731
    run_sync(box, conn, days=4, model=up)
    ev, = events(conn)
    assert (ev["message_id"], ev["uidvalidity"], ev["mailbox_uid"], ev["stage"]) == ("8:205", "8", "205", "rejection")
    assert "100" not in box.text_fetches and conn.execute("SELECT state FROM mail_ledger WHERE mailbox_uid = '205'").fetchone()[0] == "stored"


def test_a_transient_header_failure_is_not_skipped_by_a_higher_uid(tmp_path):
    conn = make_db(tmp_path)
    low = CONF(100, "Thank you for applying to Stripe (low)")
    high = CONF(101, "Thank you for applying to Stripe (high)")
    box = FakeInbox([low, high])
    box.transient = {"100": 1}
    run_sync(box, conn)
    assert [e["mailbox_uid"] for e in events(conn)] == ["101"]
    cur = conn.execute("SELECT value FROM kv WHERE key = 'outcome_cursor:1'").fetchone()
    assert cur is None or int(cur[0].rpartition(":")[2]) < 100                          # the boundary stays below the gap
    run_sync(FakeInbox([low, high]), conn, days=4)                                      # a restart; the fetch works now
    assert sorted(e["mailbox_uid"] for e in events(conn)) == ["100", "101"]             # exactly once each
    assert conn.execute("SELECT COUNT(*) FROM outcome_events").fetchone()[0] == 2
    assert conn.execute("SELECT value FROM kv WHERE key = 'outcome_cursor:1'").fetchone()[0] == "7:101"


def test_a_confirmed_expunge_is_recorded_and_does_not_hold_the_cursor_back(tmp_path):
    conn = make_db(tmp_path)
    box = FakeInbox([CONF(100), CONF(101, "Thank you for applying to Stripe (2)")])
    box.gone = {"100"}
    run_sync(box, conn)
    assert [e["mailbox_uid"] for e in events(conn)] == ["101"]
    assert conn.execute("SELECT state, reason FROM mail_ledger WHERE mailbox_uid = '100'").fetchone()[:] == ("unrelated", "gone from the mailbox")
    assert conn.execute("SELECT value FROM kv WHERE key = 'outcome_cursor:1'").fetchone()[0] == "7:101"


# F12 --------------------------------------------------------------------------------------------------------------------------------

MORE_NEGATIVES = [
    ("Stripe newsletter: your guide", "Your guide to our online assessment is now available in the candidate handbook."),
    ("Stripe careers newsletter", "Interested in our online assessment? Explore the candidate handbook."),
    ("Hiring at Stripe", "Wondering what the technical interview is like? Read our engineers' stories."),
    ("Meet the team", "Your next interview could be with someone like Dana. Read her story."),
    ("Stripe jobs digest", "Your weekly digest: three roles with an online assessment stage."),
]


@pytest.mark.parametrize("subject,body", MORE_NEGATIVES)
def test_reader_directed_newsletter_wording_is_not_an_outcome_even_without_bulk_headers(tmp_path, subject, body):
    assert verdict(subject, body).stage is None
    conn = make_db(tmp_path)
    run_sync(FakeInbox([mail(900, subject, body, sender="Stripe <jobs@stripe.com>")]), conn)
    assert events(conn) == []                                                            # and nothing is stored by the real sync


def test_a_neutral_reply_under_an_interview_subject_is_not_an_interview(tmp_path):
    body = ("Thanks for the update; I will get back to you tomorrow.\n\nOn Mon, Nov 2, 2026 at 10:00 AM Stripe <r@stripe.com> wrote:\n"
            "> We would like to interview you. Please schedule an interview at https://calendly.com/stripe/x")
    m = Mail("901", "<n@x>", "Bhavya <bhavya@gmail.com>", "Re: Stripe interview invitation", T0 + timedelta(days=2), body, "66")
    seen = {}

    def model(msgs):
        seen["prompt"] = msgs[1].content
        return SimpleNamespace(stage="interview", confidence=0.95, evidence="Thanks for the update; I will get back to you tomorrow.")
    v = O.model_verdict(m, model)
    assert "interview invitation" not in seen["prompt"] and "Stripe interview" not in seen["prompt"]       # the old subject is not shown
    assert v.stage is None                                                                  # the evidence reports no stage
    conn = make_db(tmp_path)
    reply = dataclasses.replace(m, sender="Stripe Recruiting <recruiter@stripe.com>")
    run_sync(FakeInbox([reply]), conn, model=model)
    assert events(conn) == []


def test_evidence_that_is_present_but_reports_no_stage_is_refused():
    m = Mail("902", "<n2@x>", "Stripe <jobs@stripe.com>", "Hello", T0, "Hi Bhavya, hope you are well. Our offices are closed on Friday.")
    v = O.model_verdict(m, lambda msgs: SimpleNamespace(stage="offer", confidence=0.9, evidence="hope you are well"))
    assert v.stage is None


@pytest.mark.parametrize("stage,body,evidence", [
    ("rejection", "We looked carefully at your background and our team has chosen a different direction for this role.", "our team has chosen a different direction for this role"),
    ("interview", "Would Thursday at 2pm PT work for a conversation with the hiring manager?", "Would Thursday at 2pm PT work for a conversation with the hiring manager?"),
])
def test_semantically_valid_model_only_outcomes_are_kept(stage, body, evidence):
    m = Mail("903", "<n3@x>", "Stripe Recruiting <recruiter@stripe.com>", "Re: Your application", T0, body)
    assert O.model_verdict(m, lambda msgs: SimpleNamespace(stage=stage, confidence=0.93, evidence=evidence)).stage == stage


# ============================================================ third round =====================================================

def test_a_reply_that_arrives_after_the_seed_was_approved_records_its_provenance_and_follows_a_reassignment(tmp_path):   # F10
    conn = make_db(tmp_path, apps=TWO_STRIPE)
    box = FakeInbox([CONFIRM])
    run_sync(box, conn)                                                                    # the confirmation is ambiguous
    seed = events(conn)[0]["id"]
    assert O.assign(conn, seed, 2)
    box.mails[FOLLOW.uid] = FOLLOW                                                         # the recruiter writes AFTER the approval
    run_sync(box, conn, days=5)
    follow = next(e for e in events(conn) if e["mailbox_uid"] == "2002")
    assert (follow["application_id"], follow["needs_review"], follow["ambiguous"], follow["manual"]) == (2, 0, 0, 0)
    assert follow["inherited_from"] == seed                                                 # the dependency is recorded
    assert O.assign(conn, seed, 1)                                                          # the candidate corrects the seed
    after = {e["mailbox_uid"]: e for e in events(conn)}
    assert after["2002"]["application_id"] == 1 and after["2002"]["needs_review"] == 0 and after["2002"]["inherited_from"] == seed
    assert {r["id"]: r["state"] for r in conn.execute("SELECT id, state FROM applications")} == {1: "confirmed", 2: "submitted"}
    later = mail(2003, "Re: Thank you for applying to Stripe", "Unfortunately, we will not be moving forward.", T0 + timedelta(days=6),
                 sender="Jamie Lee <jamie.lee.recruiter@gmail.com>", thread="900")
    box.mails[later.uid] = later
    run_sync(box, conn, days=7)
    new = next(e for e in events(conn) if e["mailbox_uid"] == "2003")
    assert (new["application_id"], new["inherited_from"]) == (1, seed)                      # the corrected owner is used from now on


def test_events_whose_dependence_cannot_be_established_go_back_to_review_when_the_seed_is_reassigned(tmp_path):
    conn = make_db(tmp_path, apps=TWO_STRIPE)
    box = FakeInbox([CONFIRM])
    run_sync(box, conn)
    seed = events(conn)[0]["id"]
    assert O.assign(conn, seed, 2)
    # an automatic event from before provenance existed: counted, in the seed's thread, owner app 2, dependence unknown (NULL)
    conn.execute("INSERT INTO outcome_events (application_id, stage, message_id, thread_id, sender, subject, excerpt, received_at, classifier, "
                 "confidence, needs_review, ambiguous, manual, inherited_from) VALUES (2, 'interview', 'legacy:1', '900', 'x', 'y', 'z', ?, 'rule', 0.9, 0, 0, 0, NULL)",
                 ("2026-11-04T10:00:00Z",))
    # and one that is established as DIRECT (provenance 0): it matches its application with no thread at all
    conn.execute("INSERT INTO outcome_events (application_id, stage, message_id, thread_id, sender, subject, excerpt, received_at, classifier, "
                 "confidence, needs_review, ambiguous, manual, inherited_from) VALUES (2, 'assessment', 'direct:1', '900', 'x', 'y', 'z', ?, 'rule', 0.9, 0, 0, 0, 0)",
                 ("2026-11-04T11:00:00Z",))
    conn.commit()
    assert O.assign(conn, seed, 1)
    rows = {r["message_id"]: dict(r) for r in conn.execute("SELECT * FROM outcome_events")}
    assert (rows["legacy:1"]["needs_review"], rows["legacy:1"]["ambiguous"], rows["legacy:1"]["application_id"]) == (1, 1, 2)       # to review
    assert {c["application_id"] for c in O.candidates(conn, rows["legacy:1"]["id"])} == {1, 2}
    assert (rows["direct:1"]["needs_review"], rows["direct:1"]["ambiguous"]) == (0, 0)                                              # untouched
    assert conn.execute("SELECT COUNT(*) FROM outcome_audit WHERE action = 'requeue'").fetchone()[0] == 1


# F11/F08: relocation ---------------------------------------------------------------------------------------------------------------------

RELOCATE_BODY = "We took another look at everything you sent and we have made our choice. It is not the news you hoped for."


def stranded_retry(tmp_path):
    conn = make_db(tmp_path)
    original = mail(100, "An update on your Stripe application", RELOCATE_BODY, mid="<reused@sender>")
    down = lambda msgs: (_ for _ in ()).throw(RuntimeError("model outage"))  # noqa: E731
    run_sync(FakeInbox([original]), conn, model=down)
    assert conn.execute("SELECT state FROM mail_ledger").fetchone()[0] == "retry"
    return conn, original


UP = lambda msgs: SimpleNamespace(stage="rejection", confidence=0.95, evidence="we have made our choice")  # noqa: E731


def test_another_company_on_the_same_ats_domain_with_a_reused_header_id_is_not_taken_for_the_original(tmp_path):
    conn, original = stranded_retry(tmp_path)
    other = Mail("205", "<reused@sender>", "Palantir <no-reply@us.greenhouse-mail.io>", "Your Palantir application", T0 + timedelta(days=2),
                 "We regret to inform you that Palantir has decided to move forward with other candidates.")
    box = FakeInbox([other], uidvalidity="8")
    run_sync(box, conn, days=4, model=UP)
    assert events(conn) == [] and "205" not in box.text_fetches                              # headers were inspected, no body was read
    assert "205" in box.header_fetches
    row = conn.execute("SELECT state, reason FROM mail_ledger WHERE uidvalidity = '7' AND mailbox_uid = '100'").fetchone()
    assert row["state"] == "undecided" and "re-identified" in row["reason"]


def test_a_different_message_of_the_same_company_with_the_reused_header_id_is_not_the_original(tmp_path):
    conn, original = stranded_retry(tmp_path)
    sibling = dataclasses.replace(original, uid="206", subject="Another update on your Stripe application", date=T0 + timedelta(days=3))
    box = FakeInbox([sibling], uidvalidity="8")
    run_sync(box, conn, days=5, model=UP)
    # the sibling is an ordinary message of this company and is read as itself ...
    assert [(e["message_id"], e["mailbox_uid"]) for e in events(conn)] == [("8:206", "206")]
    # ... but it is NOT taken for the original, which stays an explicit unresolved item
    row = conn.execute("SELECT state, reason FROM mail_ledger WHERE uidvalidity = '7' AND mailbox_uid = '100'").fetchone()
    assert row["state"] == "undecided" and "re-identified" in row["reason"]
    # the very same message, renumbered (identical headers), IS recognised as the original
    same = dataclasses.replace(original, uid="207")
    box2 = FakeInbox([sibling, same], uidvalidity="8")
    conn.execute("UPDATE mail_ledger SET state = 'retry' WHERE mailbox_uid = '100'")
    conn.commit()
    run_sync(box2, conn, days=6, model=UP)
    assert sorted(e["message_id"] for e in events(conn)) == ["8:206", "8:207"]
    assert conn.execute("SELECT COUNT(*) FROM mail_ledger WHERE state IN ('retry', 'undecided')").fetchone()[0] == 0


def test_the_ledger_keeps_the_evidence_for_relocation_and_no_subject(tmp_path):
    conn, original = stranded_retry(tmp_path)
    row = conn.execute("SELECT * FROM mail_ledger").fetchone()
    assert row["sender_addr"] == "no-reply@us.greenhouse-mail.io" and len(row["subject_sig"]) == 64 and "subject" not in row.keys()


# F12 -------------------------------------------------------------------------------------------------------------------------------------

DESCRIPTIVE = [
    ("Stripe careers newsletter", "Your assessment guide explains the online assessment used by teams."),
    ("Stripe careers newsletter", "Your application handbook describes an online assessment, followed by interviews."),
    ("Stripe careers newsletter", "Your interview guide covers the technical interview and the final round."),
    ("Stripe updates", "Your application resources: how the online assessment and the coding challenge work."),
    ("Resources", "Your assessment tips and the technical interview checklist are in the candidate handbook."),
    ("Stripe candidate hub", "Your application portal now includes a page that describes the online assessment."),
]


@pytest.mark.parametrize("subject,body", DESCRIPTIVE)
def test_a_personal_noun_phrase_naming_a_resource_is_not_a_stage(tmp_path, subject, body):
    assert verdict(subject, body).stage is None
    conn = make_db(tmp_path)
    run_sync(FakeInbox([mail(910, subject, body, sender="Stripe <jobs@stripe.com>")]), conn)
    assert [e for e in events(conn) if e["needs_review"] == 0] == []


@pytest.mark.parametrize("subject,body,stage", [
    ("Stripe careers newsletter", "Hi Bhavya,\n\nYour application has been selected for an online assessment. Please complete it within 7 days: https://hackerrank.com/tests/x", "assessment"),
    ("Your Stripe application", "Hi Bhavya,\n\nYour interview is scheduled for Thursday at 2pm PT. Calendar invite: https://calendly.com/stripe/x\n\nUnsubscribe | Weekly newsletter", "interview"),
    ("Your Stripe application", "Hi Bhavya, you have been invited to take the online assessment: https://hackerrank.com/tests/abc", "assessment"),
])
def test_assertions_about_the_candidates_own_process_are_still_recognised(subject, body, stage):
    assert verdict(subject, body).stage == stage


# ============================================================ fourth round =======================================================

SOFT_BODY = "We took another look at everything you sent and we have made our choice. It is not the news you hoped for."


def queued_reply_world(tmp_path):
    conn = make_db(tmp_path, apps=TWO_STRIPE)
    box = FakeInbox([CONFIRM])
    run_sync(box, conn)
    seed = events(conn)[0]["id"]
    assert O.assign(conn, seed, 2)                                                           # the seed belongs to application 2
    soft = mail(2010, "Re: Thank you for applying to Stripe", SOFT_BODY, T0 + timedelta(days=4),
                sender="Jamie Lee <jamie.lee.recruiter@gmail.com>", thread="900")
    box.mails[soft.uid] = soft
    down = lambda msgs: (_ for _ in ()).throw(RuntimeError("model outage"))  # noqa: E731
    run_sync(box, conn, days=5, model=down)                                                    # the thread-only reply is queued
    saved = json.loads(conn.execute("SELECT candidates FROM mail_ledger WHERE mailbox_uid = '2010'").fetchone()[0])
    assert conn.execute("SELECT state FROM mail_ledger WHERE mailbox_uid = '2010'").fetchone()[0] == "retry"
    assert [(c["application_id"], c["seed"]) for c in saved] == [(2, seed)]
    return conn, box, seed


UPMODEL = lambda msgs: SimpleNamespace(stage="rejection", confidence=0.95, evidence="we have made our choice")  # noqa: E731


def test_a_reply_queued_before_its_seed_was_reassigned_follows_the_current_decision(tmp_path):                       # F10
    conn, box, seed = queued_reply_world(tmp_path)
    assert O.assign(conn, seed, 1)                                                              # reassigned while the reply waited
    run_sync(box, conn, days=6, model=UPMODEL)
    ev = next(e for e in events(conn) if e["mailbox_uid"] == "2010")
    assert (ev["application_id"], ev["needs_review"], ev["ambiguous"], ev["inherited_from"]) == (1, 0, 0, seed)    # never B with an A parent


@pytest.mark.parametrize("change", ["rejected", "pending", "ambiguous"])
def test_a_reply_queued_before_its_seed_lost_its_authority_is_published_pending(tmp_path, change):
    conn, box, seed = queued_reply_world(tmp_path)
    sql = {"rejected": "needs_review = 2", "pending": "needs_review = 1", "ambiguous": "ambiguous = 1"}[change]
    conn.execute(f"UPDATE outcome_events SET {sql} WHERE id = ?", (seed,))
    conn.commit()
    run_sync(box, conn, days=6, model=UPMODEL)
    ev = next(e for e in events(conn) if e["mailbox_uid"] == "2010")
    assert (ev["needs_review"], ev["ambiguous"]) == (1, 1) and ev["inherited_from"] is None                # for review, not counted
    assert 2 in {c["application_id"] for c in O.candidates(conn, ev["id"])}


# F11: content, not just headers ---------------------------------------------------------------------------------------------------

def test_different_content_behind_identical_headers_is_a_new_message_and_the_original_stays_unresolved(tmp_path):
    conn, original = stranded_retry(tmp_path)
    assert conn.execute("SELECT content_sig FROM mail_ledger").fetchone()[0]                    # captured when the body was first read
    impostor = dataclasses.replace(original, uid="205", text="Please complete your online assessment within seven days: https://hackerrank.com/tests/x")
    box = FakeInbox([impostor], uidvalidity="8")
    run_sync(box, conn, days=4, model=UP)
    assert [(e["message_id"], e["stage"]) for e in events(conn)] == [("8:205", "assessment")]       # a separate observation
    old = conn.execute("SELECT state, reason FROM mail_ledger WHERE uidvalidity = '7' AND mailbox_uid = '100'").fetchone()
    assert old["state"] == "undecided" and "content" in old["reason"]                               # the original is NOT retired into it
    assert conn.execute("SELECT COUNT(*) FROM outcome_events").fetchone()[0] == 1


def test_a_truly_identical_relocated_original_still_recovers_once(tmp_path):
    conn, original = stranded_retry(tmp_path)
    same = dataclasses.replace(original, uid="207")
    run_sync(FakeInbox([same], uidvalidity="8"), conn, days=4, model=UP)
    ev, = events(conn)
    assert (ev["message_id"], ev["stage"]) == ("8:207", "rejection")
    assert conn.execute("SELECT COUNT(*) FROM mail_ledger WHERE state IN ('retry', 'undecided')").fetchone()[0] == 0
    run_sync(FakeInbox([same], uidvalidity="8"), conn, days=5, model=UP)
    assert len(events(conn)) == 1


# F12: a negative label needs its own evidence -------------------------------------------------------------------------------------------

NEUTRAL = "Thanks for the update; I will get back to you tomorrow."


@pytest.mark.parametrize("stage", list(O.STAGES))
def test_neutral_current_text_is_refused_for_every_stage_label(tmp_path, stage):
    m = Mail("920", "<n@x>", "Stripe Recruiting <recruiter@stripe.com>", "Re: Your application", T0 + timedelta(days=2), NEUTRAL)
    v = O.model_verdict(m, lambda msgs: SimpleNamespace(stage=stage, confidence=0.95, evidence=NEUTRAL))
    assert v.stage is None
    conn = make_db(tmp_path)
    run_sync(FakeInbox([m]), conn, model=lambda msgs: SimpleNamespace(stage=stage, confidence=0.95, evidence=NEUTRAL))
    assert events(conn) == []                                                                       # nothing accepted through the real sync


@pytest.mark.parametrize("stage,text", [
    ("rejection", "After careful thought our team has chosen a different direction for this role."),
    ("rejection", "The position has been filled and we are no longer considering new applicants."),
    ("rejection", "We have made our decision and it is not the news you hoped for."),
    ("withdrawn", "As you asked, we have taken you out of the process."),
    ("withdrawn", "Per your request your application has been withdrawn."),
    ("withdrawn", "You told us you are no longer interested, so we have closed your application."),
])
def test_genuine_soft_rejections_and_withdrawals_are_kept(stage, text):
    m = Mail("921", "<s@x>", "Stripe Recruiting <recruiter@stripe.com>", "Re: Your application", T0, text)
    assert O.model_verdict(m, lambda msgs: SimpleNamespace(stage=stage, confidence=0.93, evidence=text)).stage == stage
