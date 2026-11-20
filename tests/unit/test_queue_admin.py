"""The queue editor: add links and companies, set aside, put back, reorder, edit, delete; and that the runner follows the candidate's order."""
import sqlite3
from datetime import UTC, datetime

import pytest

from jobagent import db
from jobagent.tools import jobqueue as Q
from jobagent.tools import queue_admin as QA

QA_REAL_RESOLVE = QA._resolve                                  # the conftest replaces QA._resolve in every test; this keeps the real one


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def job(conn, n, status="queued", score=0.5, company="Acme", **kw):
    with conn:
        conn.execute("INSERT INTO jobs (url, company, title, status, match_score, company_slug) VALUES (?,?,?,?,?,?)",
                     (f"https://jobs.example.test/{n}", company, f"Role {n}", status, score, Q.slug(company)))
    return conn.execute("SELECT id FROM jobs WHERE url = ?", (f"https://jobs.example.test/{n}",)).fetchone()[0]


def order(conn):
    return [r["url"].rsplit("/", 1)[1] for r in Q.pending(conn)]


def test_a_pasted_link_and_a_company_go_where_they_belong(conn):
    out = QA.add(conn, "https://job-boards.greenhouse.io/doordashusa/jobs/8225722\nZoox Robotics")
    assert [r["result"] for r in out] == ["added", "added"]
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["status"] == "queued" and row["company"] == "doordashusa" and row["source"] == "dashboard" and row["user_edited"] == 1
    assert row["url"] == "https://job-boards.greenhouse.io/doordashusa/jobs/8225722"                   # not job-https://...
    assert conn.execute("SELECT company, company_key FROM watchlist").fetchone()[:] == ("Zoox Robotics", "zoox-robotics")
    assert QA.add(conn, "zoox robotics")[0]["result"] == "exists"                                       # same employer, any spelling


def test_the_same_link_is_not_queued_twice_and_an_applied_one_is_never_requeued(conn):
    assert QA.add(conn, "https://jobs.example.test/9")[0]["result"] == "added"
    assert QA.add(conn, "https://jobs.example.test/9/")[0]["result"] == "already_queued"               # trailing slash = same posting
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    jid = job(conn, 5, status="applied")
    assert QA.add(conn, "https://jobs.example.test/5")[0]["result"] == "already_applied"
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()[0] == "applied"
    job(conn, 6, status="running")
    assert QA.add(conn, "https://jobs.example.test/6")[0]["result"] == "running"


def test_bad_inputs_are_refused_not_executed(conn):
    out = QA.add(conn, "--limit 99\nftp://x.test/a\n" + "x" * 1200)
    assert all(r["result"] in {"refused", "warning"} for r in out) and conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    with pytest.raises(ValueError):
        QA.add(conn, "")
    with pytest.raises(ValueError):
        QA.add(conn, "\n".join(f"Company {i}" for i in range(101)))
    for junk in ("https://evil.test/x", "mailto:hr@acme.com", "javascript:alert(1)", "file:///etc/passwd", "hr@acme.com"):
        assert QA.add_company(conn, junk)["result"] == "refused", junk
    assert QA.add_company(conn, "Acme: Robotics")["result"] == "added"                                   # a colon followed by a space is part of a name
    with pytest.raises(ValueError):
        QA.add_company(conn, "Ok", max_jobs=99)


def test_the_runner_takes_jobs_in_the_candidates_order(conn):
    a, b, c = job(conn, 1, score=0.9), job(conn, 2, score=0.8), job(conn, 3, score=0.7)
    assert order(conn) == ["1", "2", "3"]                                                              # by match score
    QA.reorder(conn, [c, a, b])
    assert order(conn) == ["3", "1", "2"]
    claimed = Q.claim(conn, "w1")
    assert claimed["id"] == c                                                                          # the top row is the one the runner takes
    QA.move(conn, b, "top")
    assert order(conn) == ["2", "1"]
    assert QA.move(conn, a, "bottom") == 2 and order(conn) == ["2", "1"]
    with pytest.raises(LookupError):
        QA.move(conn, c, "up")                                                                         # running: not movable


def test_reorder_ignores_unknown_and_non_queued_ids_and_keeps_the_rest_in_place(conn):
    a, b, c = job(conn, 1), job(conn, 2), job(conn, 3)
    d = job(conn, 4, status="skipped")
    QA.reorder(conn, [c, 9999, d])
    assert order(conn) == ["3", "1", "2"]
    for bad in ("x", [1.5], [True], list(range(6000))):
        with pytest.raises(ValueError):
            QA.reorder(conn, bad)


def test_several_links_added_at_the_top_keep_the_order_they_were_typed(conn):
    job(conn, 1)
    out = QA.add(conn, "https://jobs.example.test/a\nhttps://jobs.example.test/b\nhttps://jobs.example.test/c", where="top")
    assert [r["input"][-1] for r in out] == ["a", "b", "c"] and all(r["result"] == "added" for r in out)      # results come back in typed order too
    assert order(conn) == ["a", "b", "c", "1"]
    QA.add(conn, "https://jobs.example.test/x\nhttps://jobs.example.test/y", where="bottom")
    assert order(conn)[-2:] == ["x", "y"]


def test_adding_at_the_top_or_bottom(conn):
    job(conn, 1, score=0.9)
    job(conn, 2, score=0.1)
    QA.add(conn, "https://jobs.example.test/top", where="top")
    QA.add(conn, "https://jobs.example.test/bot", where="bottom")
    assert order(conn) == ["top", "1", "2", "bot"]
    with pytest.raises(ValueError):
        QA.add(conn, "https://x.test/y", where="sideways")


def test_set_aside_put_back_and_the_refusals(conn):
    a, run, done = job(conn, 1), job(conn, 2, status="running"), job(conn, 3, status="applied")
    out = QA.set_state(conn, [a, run, done, 777], "skip")
    assert out["changed"] == 1 and {r["id"] for r in out["refused"]} == {run, done, 777}
    row = conn.execute("SELECT status, user_edited, notes FROM jobs WHERE id = ?", (a,)).fetchone()
    assert (row["status"], row["user_edited"], row["notes"]) == ("skipped", 1, "set aside by you")
    assert QA.set_state(conn, [a], "queue")["changed"] == 1 and order(conn) == ["1"]
    assert QA.set_state(conn, [done], "queue")["refused"]
    for bad in ((conn, [], "skip"), (conn, [a], "delete"), (conn, ["1"], "skip")):
        with pytest.raises(ValueError):
            QA.set_state(*bad)


def test_discovery_does_not_undo_what_the_candidate_decided(conn):
    from jobagent.tools.matching import Match
    from jobagent.tools.locations import Decision
    a = job(conn, 1)
    QA.set_state(conn, [a], "skip")
    item = Q.Listing(company="Acme", title="Role 1 (renamed)", url="https://jobs.example.test/1")
    Q.enqueue(conn, item, Match("queued", "regex", "swe", 0.9, "ok"))
    row = conn.execute("SELECT status, title FROM jobs WHERE id = ?", (a,)).fetchone()
    assert (row["status"], row["title"]) == ("skipped", "Role 1")
    other = job(conn, 2)                                                                              # a row the candidate never touched is still updated by discovery
    Q.enqueue(conn, Q.Listing(company="Acme", title="Role 2 v2", url="https://jobs.example.test/2"), Match("queued", "regex", "swe", 0.9, "ok"))
    assert conn.execute("SELECT title FROM jobs WHERE id = ?", (other,)).fetchone()[0] == "Role 2 v2"


def test_edit_changes_text_only_and_never_a_running_row(conn):
    a, run = job(conn, 1), job(conn, 2, status="running")
    assert QA.edit(conn, a, company="Beta Corp", title="  Staff   Engineer ", url="https://evil.test", status="applied") == {
        "company": "Beta Corp", "title": "Staff Engineer", "company_slug": "beta-corp"}
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (a,)).fetchone()
    assert row["status"] == "queued" and row["url"].endswith("/1") and row["user_edited"] == 1
    with pytest.raises(LookupError):
        QA.edit(conn, run, title="x")
    with pytest.raises(LookupError):
        QA.edit(conn, 999, title="x")
    for kw in ({}, {"company": " "}):
        with pytest.raises(ValueError):
            QA.edit(conn, a, **kw)


def test_delete_keeps_records_and_anything_with_an_application(conn):
    a, kept, withapp, done = job(conn, 1), job(conn, 2, status="applied"), job(conn, 3, status="failed"), job(conn, 4, status="skipped")
    with conn:
        conn.execute("INSERT INTO applications (job_id, job_dir, company, title, url, state) VALUES (?,?,?,?,?,?)",
                     (withapp, "outputs/acme/3", "Acme", "Role 3", "https://jobs.example.test/3", "filled"))
    out = QA.delete(conn, [a, kept, withapp, done])
    assert out["deleted"] == 2 and {r["id"] for r in out["refused"]} == {kept, withapp}
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_listing_orders_running_then_queued_by_position_and_flags_editable(conn):
    a, b, run, rev = job(conn, 1, score=0.4), job(conn, 2, score=0.9), job(conn, 3, status="running"), job(conn, 4, status="needs_review")
    rows = QA.listing(conn)
    assert [r["id"] for r in rows] == [run, b, a, rev]
    assert [r.get("position") for r in rows] == [None, 1, 2, None] and [r["editable"] for r in rows] == [False, True, True, True]


def test_company_list_counts_postings_and_can_be_switched_off_and_removed(conn):
    job(conn, 1, company="Zoox")
    job(conn, 2, company="Zoox Inc.", status="skipped")
    wid = QA.add_company(conn, "Zoox")["id"]
    row = QA.watch_listing(conn)[0]
    assert (row["postings"], row["queued"], row["enabled"]) == (2, 1, True)
    QA.watch_update(conn, wid, enabled=False, max_jobs=3, note="dream job")
    row = QA.watch_listing(conn)[0]
    assert (row["enabled"], row["max_jobs"], row["note"]) == (False, 3, "dream job")
    with pytest.raises(ValueError):
        QA.watch_update(conn, wid, max_jobs=0)
    assert QA.watch_update(conn, wid, remove=True)["removed"] and QA.watch_listing(conn) == []
    with pytest.raises(LookupError):
        QA.watch_update(conn, wid, enabled=True)


def test_guess_company_from_links():
    g = QA.guess_company
    assert g("https://boards.greenhouse.io/acme/jobs/1") == "acme" and g("https://jobs.lever.co/zoox/abc") == "zoox"
    assert g("https://intel.wd1.myworkdayjobs.com/en-US/External/job/x") == "intel" and g("https://careers.example.com/jobs/1") == "example"
    assert g("https://www.bbc.co.uk/jobs/1") == "bbc" and g("not a url") == "unknown"
    assert g("https://apply.workable.com/huggingface/j/19A136F8E2/") == "huggingface" and g("https://jobs.gem.com/retool/4003629005") == "retool"
    assert g("https://ats.rippling.com/acme-inc/jobs/abc") == "acme-inc" and g("https://acme.bamboohr.com/careers/12") == "acme"
    assert g("https://netflix.recruitee.com/o/eng") == "netflix" and g("https://app.dover.com/apply/zeta/123") == "zeta"
    assert g("https://jobs.jobvite.com/jobvite/job/oXYZ") == "jobvite" and g("https://careers-acme.icims.com/jobs/1") == "acme"


def test_an_old_database_without_the_new_columns_is_upgraded_in_place(tmp_path):
    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, company TEXT, title TEXT, status TEXT NOT NULL DEFAULT 'queued', notes TEXT, "
              "discovered_at TEXT, updated_at TEXT)")
    c.execute("INSERT INTO jobs (url, company, title) VALUES ('https://x.test/1', 'Old', 'Role')")
    c.commit()
    c.close()
    c = db.connect(p)
    assert c.execute("SELECT priority, user_edited FROM jobs").fetchone()[:] == (0, 0)
    c.close()


def test_a_pasted_link_joins_the_company_of_its_board_siblings(conn):
    """`job-boards.greenhouse.io/doordashusa/...` must not become a second, unpaced company next to the discovered `DoorDash` rows."""
    with conn:
        conn.execute("INSERT INTO jobs (url, company, title, status, company_slug) VALUES (?,?,?,?,?)",
                     ("https://job-boards.greenhouse.io/doordashusa/jobs/1", "DoorDash", "Backend", "queued", "doordash"))
        conn.execute("INSERT INTO jobs (url, company, title, status, company_slug) VALUES (?,?,?,?,?)",
                     ("https://job-boards.greenhouse.io/other/jobs/1", "Other Co", "SWE", "queued", "other-co"))
    QA.add(conn, "https://job-boards.greenhouse.io/doordashusa/jobs/8225722")
    row = conn.execute("SELECT company, company_slug FROM jobs WHERE url LIKE '%8225722'").fetchone()
    assert (row["company"], row["company_slug"]) == ("DoorDash", "doordash")
    assert Q.employer_key(row["company"]) == "doordash"
    QA.add(conn, "https://job-boards.greenhouse.io/newco/jobs/5")                                       # an unknown board falls back to the guess
    assert conn.execute("SELECT company FROM jobs WHERE url LIKE '%newco%'").fetchone()[0] == "newco"
    QA.add(conn, "https://job-boards.greenhouse.io/doordashusa%/jobs/9")                                 # LIKE wildcards in a link are not wildcards
    assert conn.execute("SELECT company FROM jobs WHERE url LIKE '%9' AND source = 'dashboard'").fetchone()[0] != "DoorDash"


@pytest.mark.parametrize("url", ["http://localhost:8765/api", "http://127.0.0.1/", "http://[::1]/x", "http://169.254.169.254/latest/meta-data/",
                                 "http://10.0.0.5/job", "http://192.168.1.1/", "http://user:pw@jobs.example.test/1", "file:///etc/passwd", "ftp://x.test/a",
                                 "http://intranet.corp/jobs/1", "http://printer.local/", "http://0x7f.1/", "http://2130706433/"])
def test_links_to_this_machine_or_a_private_network_are_refused(conn, url):
    out = QA.add(conn, url)
    assert out[0]["result"] == "refused" and conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_a_name_that_resolves_to_a_private_address_is_refused_and_a_public_one_is_not(conn, monkeypatch):
    answers = {"rebind.example.test": ["10.1.2.3"], "mixed.example.test": ["93.184.216.34", "127.0.0.1"], "jobs.example.test": ["93.184.216.34"], "gone.example.test": []}
    monkeypatch.setattr(QA, "_resolve", lambda host, port: answers[host])
    assert QA.add(conn, "https://rebind.example.test/jobs/1")[0]["result"] == "refused"
    assert QA.add(conn, "https://mixed.example.test/jobs/1")[0]["result"] == "refused"                   # one private answer is enough
    assert QA.add(conn, "https://jobs.example.test/jobs/1")[0]["result"] == "added"
    assert QA.add(conn, "https://gone.example.test/jobs/2")[0]["result"] == "added"                      # unresolvable: nothing internal is reached


def test_one_stalled_dns_answer_is_retried_once(conn, monkeypatch):
    calls = []

    def flaky(*a, **k):
        import time
        calls.append(1)
        if len(calls) == 1:
            time.sleep(1)                                                                                   # first answer arrives after the limit
        return [(2, 1, 6, "", ("93.184.216.34", 443))]
    monkeypatch.setattr(QA, "_resolve", QA_REAL_RESOLVE)
    monkeypatch.setattr(QA, "DNS_TIMEOUT_S", 0.3)
    monkeypatch.setattr(QA.socket, "getaddrinfo", flaky)
    assert QA.add(conn, "https://jobs.example.test/jobs/7")[0]["result"] == "added" and len(calls) == 2


def test_a_stuck_resolver_refuses_quickly_instead_of_hanging(conn, monkeypatch):
    import time
    monkeypatch.setattr(QA, "_resolve", QA_REAL_RESOLVE)
    monkeypatch.setattr(QA, "DNS_TIMEOUT_S", 0.3)
    monkeypatch.setattr(QA.socket, "getaddrinfo", lambda *a, **k: time.sleep(3))
    started = time.monotonic()
    out = QA.add(conn, "https://slow.example.test/jobs/1")
    assert out[0]["result"] == "refused" and "DNS" in out[0]["note"] and time.monotonic() - started < 3
