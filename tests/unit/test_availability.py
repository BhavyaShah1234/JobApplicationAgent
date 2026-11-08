from datetime import UTC, datetime

from jobagent import db
from jobagent.tools import availability as A

GODADDY_CLOSED = ("Thank you for your enthusiasm—we truly appreciate your interest in joining us! While this position "
                  "is no longer accepting applications, we're always on the lookout for talented individuals like you.")
GODADDY_OPEN = ("Software Development Engineer - AI Services\nR023891\nTempe, Arizona, United States\nLocation Details:\n"
                "This is a remote position, so you'll be working remotely from your home.\nThis position is not "
                "eligible to be performed in Alaska, Mississippi, North Dakota, or the Virgin Islands.\n"
                "Apply Now and Join the #GoDaddyLife today!")


def test_closed_pages_are_recognised():
    for text in (GODADDY_CLOSED, "Sorry, this job has expired.", "This position has been filled.",
                 "The job you are looking for is no longer available.", "We couldn't find that job.",
                 "This requisition is closed", "Applications for this role are closed."):
        assert A.from_text(text), text


def test_open_postings_are_not_flagged():
    assert A.from_text(GODADDY_OPEN, can_apply=True) is None
    assert A.from_text("We are accepting applications on a rolling basis. Apply now!") is None
    assert A.from_text("Closed-loop control systems experience; filled orders on time.") is None


def test_kinds_and_reason():
    gone = A.from_text(GODADDY_CLOSED, "https://x/1")
    assert (gone.kind, gone.status, gone.url) == ("closed", "closed", "https://x/1")
    assert "no longer accepting applications" in gone.reason
    assert A.from_text("Page not found. The job could not be found.").kind == "not_found"
    assert A.from_text("This job posting has expired").kind == "expired"
    already = A.from_text("You have already applied to this job.")
    assert (already.kind, already.status) == ("already_applied", "already_applied")


def test_a_footer_far_down_an_open_posting_does_not_close_it():
    text = "Senior Engineer\n" + "Duties and qualifications. " * 200 + "\nPositions that have been filled: see archive."
    text += "\nThis position has been filled."
    assert A.from_text(text, can_apply=True) is None
    assert A.from_text(text, can_apply=False) is not None


def test_valid_through_and_http_status():
    now = datetime(2026, 11, 7, tzinfo=UTC)
    assert A.from_jsonld({"validThrough": "2027-02-04T17:08:46Z"}, now=now) is None  # GoDaddy: still open
    assert A.from_jsonld({"validThrough": "2026-10-01"}, now=now).kind == "expired"
    assert A.from_jsonld({"validThrough": "soon"}, now=now) is None
    assert A.from_status(404).kind == "not_found" and A.from_status(410) and A.from_status(200) is None


def test_mark_and_known_round_trip(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "jobs.sqlite")
    assert A.known("https://x/1", conn) is None
    A.mark("https://x/1", A.from_text(GODADDY_CLOSED, "https://x/1"), conn)
    got = A.known("https://x/1", conn)
    assert got.kind == "closed" and "no longer accepting" in got.reason
    row = conn.execute("SELECT status, notes FROM jobs WHERE url = 'https://x/1'").fetchone()
    assert row["status"] == "closed" and row["notes"].startswith("closed: ")


def test_a_bot_check_that_keeps_coming_back_marks_the_job_blocked_not_closed(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    gone = A.Unavailable("blocked", "the site's cloudflare check came back after it was solved", "https://x/2")
    assert gone.status == "blocked"
    A.mark("https://x/2", gone, conn)
    assert A.known("https://x/2", conn) is None  # a later run may try again (the site may let it through)
