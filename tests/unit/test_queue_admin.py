"""Dashboard queue editing: waiting postings can be deleted, records and running work cannot, and a deleted posting does not come back."""
import http.client
import json
import threading

import pytest

from jobagent import db
from jobagent.config import Settings
from jobagent.dashboard import data
from jobagent.dashboard.demo import seed
from jobagent.dashboard.server import make_server
from jobagent.tools import jobqueue as Q
from jobagent.tools import queue_admin as QA
from jobagent.tools.locations import Decision
from jobagent.tools.matching import Match

US = Decision("us", "location text", "Austin, TX")
QUEUED = Match("queued", "regex", "swe", 0.7, "regex match")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def add(conn, n, status="queued"):
    Q.enqueue(conn, Q.Listing(company="Acme", title=f"Engineer {n}", url=f"https://jobs.acme.com/careers/{n}", locations=["Austin, TX"], req_id=f"R-{n}"), QUEUED, US)
    conn.execute("UPDATE jobs SET status = ? WHERE url = ?", (status, f"https://jobs.acme.com/careers/{n}"))
    conn.commit()
    return conn.execute("SELECT id FROM jobs WHERE url = ?", (f"https://jobs.acme.com/careers/{n}",)).fetchone()["id"]


@pytest.mark.parametrize("status", sorted(QA.DELETE_FROM))
def test_a_waiting_posting_is_deleted(conn, status):
    jid = add(conn, 1, status)
    assert QA.delete(conn, [jid]) == {"deleted": 1, "refused": []}
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("status", ["running", "scraped", "applied", "already_applied", "closed"])
def test_work_in_use_or_on_record_is_refused_and_kept(conn, status):
    jid = add(conn, 1, status)
    out = QA.delete(conn, [jid])
    assert out["deleted"] == 0 and out["refused"][0]["id"] == jid and "not deleted" in out["refused"][0]["why"]
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_a_posting_with_an_application_is_kept(conn):
    jid = add(conn, 1)
    conn.execute("INSERT INTO applications (job_id, job_dir, url, state) VALUES (?, 'outputs/acme/1', 'https://jobs.acme.com/careers/1', 'filled')", (jid,))
    conn.commit()
    out = QA.delete(conn, [jid])
    assert out["deleted"] == 0 and "application" in out["refused"][0]["why"]


def test_a_mixed_batch_deletes_what_it_may_and_says_why_for_the_rest(conn):
    a, b, c = add(conn, 1), add(conn, 2, "running"), add(conn, 3, "needs_review")
    out = QA.delete(conn, [a, b, c, 9999, a])
    assert out["deleted"] == 2 and {r["id"] for r in out["refused"]} == {b, 9999}
    assert [r["id"] for r in conn.execute("SELECT id FROM jobs")] == [b]


@pytest.mark.parametrize("bad", [None, [], "1", [1, "2"], [True], list(range(501)), [1.5]])
def test_bad_id_lists_are_refused(conn, bad):
    with pytest.raises(ValueError):
        QA.delete(conn, bad)


def test_a_deleted_posting_is_not_put_back_by_the_next_discovery(conn):
    jid = add(conn, 1)
    QA.delete(conn, [jid])
    listing = Q.Listing(company="Acme", title="Engineer 1", url="https://jobs.acme.com/careers/1/?utm_source=x", locations=["Austin, TX"], req_id="R-1")
    assert Q.enqueue(conn, listing, QUEUED, US) == "unchanged"
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    other = Q.Listing(company="Acme", title="Engineer 2", url="https://jobs.acme.com/careers/2", locations=["Austin, TX"], req_id="R-2")
    assert Q.enqueue(conn, other, QUEUED, US) == "queued"                       # only the deleted address is skipped


# --- the dashboard route -------------------------------------------------------------------------------------------------------------

@pytest.fixture
def server(tmp_path):
    settings = Settings(**seed(tmp_path))
    srv = make_server(settings, "127.0.0.1", 0, popen=lambda *a, **k: None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield settings, srv
    srv.shutdown()
    srv.server_close()


def post(srv, path, body, read_only_cookie=True, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    h = {"Cookie": f"jobagent_dashboard={srv.token}", "Content-Type": "application/json", "X-Dashboard": "1", **(headers or {})}
    c.request("POST", path, json.dumps(body), h)
    r = c.getresponse()
    return r.status, json.loads(r.read())


def test_the_queue_listing_says_which_postings_have_an_application(server):
    settings, _ = server
    rows = data.queue(settings)
    assert rows and all("has_application" in r for r in rows)


def test_the_route_deletes_waiting_postings_and_reports_refusals(server):
    settings, srv = server
    c = db.connect(settings.state_dir / "jobs.sqlite")
    waiting = add(c, 901)
    running = add(c, 902, "running")
    c.close()
    code, out = post(srv, "/api/queue/delete", {"ids": [waiting, running]})
    assert code == 200 and out["deleted"] == 1 and out["refused"][0]["id"] == running
    ids = {r["id"] for r in data.queue(settings)}
    assert waiting not in ids and running in ids


def test_the_route_is_guarded_like_every_other_post(server):
    _, srv = server
    assert post(srv, "/api/queue/delete", {"ids": [1]}, headers={"X-Dashboard": ""})[0] == 403
    assert post(srv, "/api/queue/delete", {"ids": [1]}, headers={"Origin": "http://evil.example"})[0] == 403
    assert post(srv, "/api/queue/delete", {"ids": "all"})[0] == 400


def test_a_read_only_dashboard_cannot_delete(tmp_path):
    settings = Settings(**seed(tmp_path))
    srv = make_server(settings, "127.0.0.1", 0, popen=lambda *a, **k: None, read_only=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert post(srv, "/api/queue/delete", {"ids": [1]})[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()
