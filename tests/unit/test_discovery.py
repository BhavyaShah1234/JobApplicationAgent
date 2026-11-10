from jobagent import db
from jobagent.tools import discovery as D, jobqueue as Q, listings as Ls

CONFIG = {
    "semantic_threshold": 0.9, "global_exclude_patterns": [r"(?i)\b(senior|staff|principal|lead|manager)\b"],
    "roles": [{"id": "swe", "title": "Software Engineer", "patterns": [r"(?i)\bsoftware (engineer|developer)\b"],
               "description": "software engineering", "priority": 3}],
}
FOUND = {"url": "https://careers.acme.com", "how": "search", "board": {"family": "greenhouse", "token": "acme"}, "blocked": False}


def items():
    mk = lambda t, loc, i: Q.Listing("Acme", t, f"https://boards.greenhouse.io/acme/jobs/{i}", [loc], req_id=str(i))  # noqa: E731
    return [mk("Software Engineer I", "Austin, TX", 1), mk("Software Engineer", "Mumbai, India", 2),
            mk("Senior Software Engineer", "Austin, TX", 3), mk("Barista", "Austin, TX", 4),
            mk("Software Developer", "Remote", 5),
            Q.Listing("Acme", "Software Engineer II", "https://jobs.acme.com/other-link/1", ["Austin, TX"], req_id="1")]


def run(tmp_path, **kw):
    conn = db.connect(tmp_path / "jobs.sqlite")
    res = D.discover("Acme", CONFIG, conn, log=lambda m: None, resolver=lambda c, **k: FOUND,
                     collector=lambda f, c, q, log=print: items(), judge=None, embed=None, **kw)
    return res, conn


def test_us_filter_and_matching_decide_each_listing(tmp_path):
    res, conn = run(tmp_path)
    status = {r["req_id"]: (r["status"], r["notes"]) for r in conn.execute("SELECT req_id, status, notes FROM jobs")}
    assert status["1"][0] == "queued"                              # US + role
    assert status["2"][0] == "skipped" and status["2"][1].startswith("location: ")   # India
    assert status["3"][0] == "skipped" and status["3"][1].startswith("role: ")       # senior
    assert status["4"][0] == "skipped"                             # not a target role
    assert status["5"][0] == "needs_review" and "location" in status["5"][1]          # Remote, no country
    assert res.counts == {"queued": 1, "skipped": 3, "needs_review": 1, "duplicate": 1}  # #6 repeats requisition 1
    assert [q[0] for q in res.queued] == ["Software Engineer I"]


def test_only_us_can_be_switched_off_for_a_pasted_company_list(tmp_path):
    res, conn = run(tmp_path, only_us=False)
    assert conn.execute("SELECT status FROM jobs WHERE req_id = '2'").fetchone()[0] == "queued"


def test_a_company_without_a_careers_page_or_behind_a_bot_check_is_reported_not_fatal(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    none = D.discover("Ghost", CONFIG, conn, log=lambda m: None, resolver=lambda c, **k: {"url": ""})
    blocked = D.discover("Walled", CONFIG, conn, log=lambda m: None,
                         resolver=lambda c, **k: {**FOUND, "blocked": True})
    def refuse(f, c, q, log=print):
        raise Ls.Blocked("boards-api.greenhouse.io answered 403")
    refused = D.discover("Refused", CONFIG, conn, log=lambda m: None, resolver=lambda c, **k: FOUND, collector=refuse)
    boom = D.discover("Boom", CONFIG, conn, log=lambda m: None, resolver=lambda c, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert none.note == "no careers page found" and blocked.note.startswith("blocked")
    assert refused.note.startswith("blocked") and boom.note.startswith("error: RuntimeError")


def test_many_companies_continue_after_a_failure(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "jobs.sqlite")
    def resolver(c, **k):
        if c == "Bad":
            raise RuntimeError("down")
        return FOUND
    out = D.discover_many(["Bad", "Acme", "Acme", " "], log=lambda m: None, config=CONFIG, conn=conn, resolver=resolver,
                          collector=lambda f, c, q, log=print: items(), judge=None, embed=None)
    assert [r.company for r in out] == ["Bad", "Acme"] and out[0].note.startswith("error") and out[1].counts["queued"] == 1


def test_embeddings_are_batched_once_per_company(tmp_path):
    from jobagent.tools import matching as M
    calls = []
    def embed(texts):
        calls.append(len(texts))
        return [[float(len(t)), 1.0] for t in texts]
    look = M.precomputed(["a", "b", "a", "c"], embed, batch=2)
    assert calls == [2, 1] and look(["a", "c"]) == [[1.0, 1.0], [1.0, 1.0]] and calls == [2, 1]
