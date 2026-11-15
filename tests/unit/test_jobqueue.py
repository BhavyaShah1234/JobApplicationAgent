from datetime import UTC, datetime, timedelta
from jobagent import db
from jobagent.tools import jobqueue as Q
from jobagent.tools.locations import Decision
from jobagent.tools.matching import Match

QUEUED = Match("queued", "regex", "swe", 0.7, "regex match")
SKIPPED = Match("skipped", "none", reason="no target role fits")
REVIEW = Match("needs_review", "semantic", "data", 0.62, "semantic match")
US = Decision("us", "location text", "Austin, TX")


def listing(**kw):
    base = dict(company="Acme Corp", title="Software Engineer", url="https://jobs.acme.com/careers/123",
                locations=["Austin, TX"], req_id="R-123")
    return Q.Listing(**{**base, **kw})


def test_canonical_urls_drop_tracking_but_keep_the_job_id():
    a = Q.canonical_url("https://Jobs.Acme.com/careers/123/?utm_source=x&gh_jid=99#apply")
    assert a == "https://jobs.acme.com/careers/123?gh_jid=99"
    assert Q.canonical_url("https://x.com/a/") == Q.canonical_url("https://X.com/a?utm_medium=mail")


def test_status_combines_location_and_role(tmp_path):
    c = db.connect(tmp_path / "jobs.sqlite")
    assert Q.enqueue(c, listing(), QUEUED, US) == "queued"
    assert Q.enqueue(c, listing(url="https://x/2", req_id="2"), QUEUED, Decision("non_us", "text", "Mumbai")) == "skipped"
    assert Q.enqueue(c, listing(url="https://x/3", req_id="3"), SKIPPED, US) == "skipped"
    assert Q.enqueue(c, listing(url="https://x/4", req_id="4"), QUEUED, Decision("ambiguous", "none", "Remote")) == "needs_review"
    assert Q.enqueue(c, listing(url="https://x/5", req_id="5"), REVIEW, US) == "needs_review"
    notes = {r["url"]: r["notes"] for r in c.execute("SELECT url, notes FROM jobs")}
    assert notes["https://x/2"].startswith("location: ") and notes["https://x/3"].startswith("role: ")
    assert Q.summary(c) == {"queued": 1, "skipped": 2, "needs_review": 2}


def test_duplicates_by_url_and_by_requisition_id(tmp_path):
    c = db.connect(tmp_path / "jobs.sqlite")
    Q.enqueue(c, listing(), QUEUED, US)
    assert Q.enqueue(c, listing(url="https://jobs.acme.com/careers/123?utm_source=li"), QUEUED, US) == "queued"  # same URL
    assert Q.enqueue(c, listing(url="https://boards.greenhouse.io/acme/jobs/777"), QUEUED, US) == "duplicate"  # same R-123
    assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_a_job_past_the_queue_is_never_overwritten_and_a_killed_run_resumes(tmp_path):
    c = db.connect(tmp_path / "jobs.sqlite")
    Q.enqueue(c, listing(), QUEUED, US)
    url = Q.canonical_url(listing().url)
    me = Q.worker_id()
    assert Q.claim(c, me)["url"] == url
    assert Q.enqueue(c, listing(), SKIPPED, US) == "unchanged"
    assert c.execute("SELECT status FROM jobs WHERE url = ?", (url,)).fetchone()[0] == "running"
    assert Q.requeue_stale(c) == 0                      # F13: this runner is alive and its lease is current: its work is not reset
    later = datetime.now(UTC) + timedelta(seconds=Q.LEASE_S + 5)
    assert Q.requeue_stale(c, now=later) == 1           # the runner was killed: its lease ran out
    assert [r["url"] for r in Q.pending(c)] == [url]
    Q.mark(c, url, "scraped")
    assert Q.pending(c) == [] and Q.requeue_stale(c) == 0


def test_pending_is_best_match_first_and_filtered_by_company(tmp_path):
    c = db.connect(tmp_path / "jobs.sqlite")
    Q.enqueue(c, listing(url="https://a/1", req_id="1"), Match("queued", "judge", "swe", 0.75), US)
    Q.enqueue(c, listing(url="https://a/2", req_id="2"), Match("queued", "judge", "swe", 0.95), US)
    Q.enqueue(c, listing(company="Other Inc", url="https://o/1", req_id="1"), QUEUED, US)
    assert [r["req_id"] for r in Q.pending(c, company="Acme Corp")] == ["2", "1"]
    assert len(Q.pending(c, limit=1)) == 1


def test_a_skipped_twin_does_not_hide_the_same_requisition_elsewhere(tmp_path):
    c = db.connect(tmp_path / "jobs.sqlite")
    india = Decision("non_us", "text", "Pune")
    assert Q.enqueue(c, listing(url="https://x/pune", req_id="JR9"), QUEUED, india) == "skipped"
    assert Q.enqueue(c, listing(url="https://x/austin", req_id="JR9"), QUEUED, US) == "queued"
    assert Q.enqueue(c, listing(url="https://x/austin-copy", req_id="JR9"), QUEUED, US) == "duplicate"
