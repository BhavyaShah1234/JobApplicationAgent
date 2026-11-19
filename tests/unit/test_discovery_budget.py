"""F26: a degraded judgment never silently validates a listing, and the advertised budgets are the real ones."""
import pytest

from jobagent import db
from jobagent.tools import discovery as D, jobqueue as Q, matching as M

CONFIG = {
    "semantic_threshold": 0.5, "global_exclude_patterns": [],
    "roles": [{"id": "swe", "title": "Software Engineer", "patterns": [r"(?i)\bsoftware engineer\b"],
               "description": "software engineering", "priority": 3}],
}
FOUND = {"url": "https://careers.acme.com", "how": "search", "board": {"family": "greenhouse", "token": "acme"}, "blocked": False}


def listings(n_regex=3, n_other=0):
    mk = lambda t, i: Q.Listing("Acme", t, f"https://boards.greenhouse.io/acme/jobs/{i}", ["Austin, TX"], req_id=str(i), snippet=t)  # noqa: E731
    return ([mk(f"Software Engineer {i}", i) for i in range(n_regex)] +
            [mk(f"Engineering Associate {i}", 100 + i) for i in range(n_other)])


def run(tmp_path, items, **kw):
    conn = db.connect(tmp_path / "jobs.sqlite")
    res = D.discover("Acme", CONFIG, conn, log=kw.pop("log", lambda m: None), resolver=lambda c, **k: FOUND,
                     collector=lambda f, c, q, log=print: items, **kw)
    return res, conn


def status(conn):
    return {r["req_id"]: (r["status"], r["notes"]) for r in conn.execute("SELECT req_id, status, notes FROM jobs")}


GOOD_JUDGE = lambda title, snippet, role, prior: (True, 0.95, "fits")  # noqa: E731


def test_a_provider_failure_sends_the_candidate_to_review_not_the_queue(tmp_path):
    def broken(title, snippet, role, prior):
        raise RuntimeError("provider down")
    res, conn = run(tmp_path, listings(2), judge=broken, embed=None)
    assert {s for s, _ in status(conn).values()} == {"needs_review"} and res.counts == {"needs_review": 2}
    assert all("NOT validated" in n for _, n in status(conn).values()) and res.budget["judge_failed"] == 2


def test_the_judge_budget_is_enforced_and_the_excess_is_reviewed_not_queued(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "MAX_JUDGE", 2)
    calls = []
    def judge(title, snippet, role, prior):
        calls.append(title)
        return True, 0.95, "fits"
    res, conn = run(tmp_path, listings(5), judge=judge, embed=None)
    assert len(calls) == 2                                                        # the real number of model calls
    assert sorted(s for s, _ in status(conn).values()) == ["needs_review"] * 3 + ["queued"] * 2
    assert res.budget["judged"] == 2 and res.budget["judge_skipped_budget"] == 3
    assert any("judge budget" in n for s, n in status(conn).values() if s == "needs_review")


def test_explicit_no_judge_mode_keeps_its_documented_behaviour(tmp_path):
    res, conn = run(tmp_path, listings(3), judge=None, embed=None)
    assert res.counts == {"queued": 3}                                           # a pattern hit is queued as is, by the user's choice


def test_unrelated_and_excluded_titles_do_not_spend_the_judge_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "MAX_JUDGE", 1)
    config = {**CONFIG, "global_exclude_patterns": [r"(?i)\bsenior\b"]}
    items = [Q.Listing("Acme", title, f"https://boards.greenhouse.io/acme/jobs/{i}",
                       ["Austin, TX"], req_id=str(i)) for i, title in enumerate([
                           "Account Executive", "Senior Software Engineer", "Software Engineer",
                           "Office Administrator", "Senior Software Engineer", "Software Engineer II"])]
    calls = []
    def judge(title, *args):
        calls.append(title)
        return True, 0.95, "fits"
    conn = db.connect(tmp_path / "jobs.sqlite")
    res = D.discover("Acme", config, conn, log=lambda _: None, resolver=lambda *a, **k: FOUND,
                     collector=lambda *a, **k: items, judge=judge, embed=None)
    assert calls == ["Software Engineer"]
    assert res.budget["judged"] == res.budget["judge_skipped_budget"] == 1
    assert status(conn)["2"][0] == "queued"
    assert status(conn)["5"][0] == "needs_review"
    assert all(status(conn)[str(i)][0] == "skipped" for i in (0, 1, 3, 4))


def test_failed_judge_calls_still_spend_the_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "MAX_JUDGE", 1)
    calls = []
    def broken(title, *args):
        calls.append(title)
        raise RuntimeError("provider down")
    res, conn = run(tmp_path, listings(3), judge=broken, embed=None)
    assert len(calls) == res.budget["judged"] == res.budget["judge_failed"] == 1
    assert res.budget["judge_skipped_budget"] == 2
    assert res.counts == {"needs_review": 3}


def test_the_embedding_cap_is_the_real_number_of_embedded_texts_and_excess_titles_stay_inspectable(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "MAX_SEMANTIC", 4)
    embedded = []
    def embed(texts):
        embedded.extend(texts)
        return [[1.0, 0.0] if "Associate" in t or "software" in t else [0.0, 1.0] for t in texts]
    logs = []
    res, conn = run(tmp_path, listings(0, 10), judge=GOOD_JUDGE, embed=embed, log=logs.append)
    role_texts = M.role_texts(CONFIG)
    assert len(embedded) <= 4 + len(role_texts)                                  # nothing outside the capped batch was embedded
    assert res.budget["embedding_texts"] == len(embedded) and res.budget["unevaluated_semantic"] == 6
    left = [(r["req_id"], r["status"], r["notes"]) for r in conn.execute("SELECT req_id, status, notes FROM jobs") if "budget" in (r["notes"] or "")]
    assert len(left) == 6 and all(s == "skipped" and "embedding budget" in n for _, s, n in left)   # recorded, not vanished
    assert any("left unevaluated" in l and "6" in l for l in logs) and any("budget used" in l for l in logs)


def test_precomputed_never_embeds_on_demand():
    seen = []
    lookup = M.precomputed(["a", "b"], lambda ts: (seen.extend(ts), [[1.0]] * len(ts))[1])
    assert lookup(["a"]) == [[1.0]] and seen == ["a", "b"]
    with pytest.raises(M.BudgetExceeded):
        lookup(["c"])
    assert seen == ["a", "b"]                                                    # the lookup made no call
    with pytest.raises(M.BudgetExceeded):
        M.precomputed(["x", "y", "z"], lambda ts: [[1.0]] * len(ts), cap=2)


def test_an_embedding_failure_is_reported_as_not_evaluated_not_as_no_fit(tmp_path):
    def boom(texts):
        raise RuntimeError("embedding provider down")
    res, conn = run(tmp_path, listings(1, 3), judge=GOOD_JUDGE, embed=boom)
    notes = {r["req_id"]: r["notes"] for r in conn.execute("SELECT req_id, notes FROM jobs")}
    assert all("not evaluated" in notes[str(100 + i)] for i in range(3)) and res.budget["unevaluated_semantic"] == 3
    assert status(conn)["0"][0] == "queued"                                      # the regex hit was still judged and queued


def test_a_validated_listing_is_still_queued(tmp_path):
    res, conn = run(tmp_path, listings(2), judge=GOOD_JUDGE, embed=None)
    assert res.counts == {"queued": 2} and res.budget["judged"] == 2
