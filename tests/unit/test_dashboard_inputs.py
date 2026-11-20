"""Dashboard: structured questions (what, which company and role, which kind, every option) and the queue/company editor over HTTP."""
import json
import sqlite3

import pytest

from jobagent.dashboard import data
from jobagent.dashboard.demo import seed
from jobagent.tools import hitl as H
from tests_dashboard_support import call, make_env   # noqa: F401  (fixture helpers shared with test_dashboard.py)


@pytest.fixture
def env(tmp_path):
    yield from make_env(tmp_path, structured=True)


def pending(s, rid):
    return next(q for q in data.hitl_pending(s) if q["id"] == rid)


def answer(s, rid):
    p = s.state_dir / "hitl" / "answers" / f"{rid}.json"
    return json.loads(p.read_text())["value"] if p.exists() else None


# --- what the Questions page is told -----------------------------------------------------------------------------------------------------

def test_a_question_carries_company_role_kind_and_every_option(env):
    s, _, _ = env
    q = pending(s, "dem1sel1")
    assert q["structured"] and q["context"]["company"] == "Example Robotics" and q["context"]["title"] == "Robotics Engineer"
    spec = q["questions"][0]
    assert spec["kind"] == "select" and spec["options"] == ["United States", "Canada", "United Kingdom", "India", "Germany"]
    batch = pending(s, "dem1bat1")
    assert [x["kind"] for x in batch["questions"]] == ["checkboxes", "text", "number", "select"] and batch["reply_format"] == "json"


def test_an_unstructured_prompt_still_works_and_offers_a_quoted_one_word_reply(env):
    s, _, _ = env
    q = pending(s, "dem0a1b2")
    assert not q["structured"] and q["questions"][0]["kind"] == "text" and q["questions"][0]["quick"] == []
    req = H.Request(id="done0001", prompt="Complete it in noVNC, then answer 'done'.")
    (s.state_dir / "hitl" / "pending" / "done0001.json").write_text(json.dumps(req.__dict__))
    assert pending(s, "done0001")["questions"][0]["quick"] == ["done"]


# --- answering --------------------------------------------------------------------------------------------------------------------------

def test_a_choice_is_checked_against_the_offered_options(env):
    s, srv, _ = env
    code, out = call(srv, "POST", "/api/hitl/answer", {"id": "dem1sel1", "answers": {"f1": "Atlantis"}})
    assert code == 400 and "not one of the options" in out["error"] and answer(s, "dem1sel1") is None
    code, _ = call(srv, "POST", "/api/hitl/answer", {"id": "dem1sel1", "answers": {"f1": "canada"}})           # case-insensitive, sent in the form's own spelling
    assert code == 200 and answer(s, "dem1sel1") == "Canada"


def test_a_button_word_and_an_empty_answer_mean_skip(env):
    s, srv, _ = env
    assert call(srv, "POST", "/api/hitl/answer", {"id": "dem1sel1", "answers": {"f1": "skip"}})[0] == 200 and answer(s, "dem1sel1") == "skip"
    assert call(srv, "POST", "/api/hitl/answer", {"id": "dem1rad1", "answers": {"f2": ""}})[0] == 200 and answer(s, "dem1rad1") == "skip"


def test_a_batch_is_sent_as_the_json_object_the_filler_reads(env):
    s, srv, _ = env
    body = {"id": "dem1bat1", "answers": {"f3": ["LinkedIn", "Referral"], "f4": "January 2027", "f6": "Boston"}}
    assert call(srv, "POST", "/api/hitl/answer", body)[0] == 200
    assert json.loads(answer(s, "dem1bat1")) == {"f3": "LinkedIn; Referral", "f4": "January 2027", "f5": None, "f6": "Boston"}


def test_several_choices_for_a_pick_one_and_unknown_ids_are_refused(env):
    s, srv, _ = env
    assert call(srv, "POST", "/api/hitl/answer", {"id": "dem1bat1", "answers": {"f6": ["Boston", "Remote"]}})[0] == 400
    assert call(srv, "POST", "/api/hitl/answer", {"id": "dem1bat1", "answers": {"zzz": "x"}})[0] == 400
    assert call(srv, "POST", "/api/hitl/answer", {"id": "dem1bat1", "answers": ["not", "an", "object"]})[0] == 400
    assert call(srv, "POST", "/api/hitl/answer", {"id": "dem1bat1", "value": "plain text for a structured request"})[0] == 400
    assert answer(s, "dem1bat1") is None


def test_a_rule_can_be_taught_for_a_single_text_question_only(env):
    s, srv, _ = env
    req = {"id": "rule0001", "prompt": "q", "secret": False, "created": "2026-11-20T00:00:00+00:00", "pid": __import__("os").getpid(),
           "questions": [{"id": "x", "label": "Why us?", "kind": "textarea", "options": [], "quick": []}], "context": {}}
    (s.state_dir / "hitl" / "pending" / "rule0001.json").write_text(json.dumps(req))
    assert call(srv, "POST", "/api/hitl/answer", {"id": "rule0001", "answers": {"x": ""}, "rule": "mention robotics"})[0] == 200
    assert answer(s, "rule0001") == "rule: mention robotics"


def test_wire_answers_for_every_kind():
    assert H.wire_answer({"kind": "checkbox"}, True) == "Yes" and H.wire_answer({"kind": "checkbox"}, False) == "No"
    spec = {"kind": "combobox", "options": ["A", "B"], "options_complete": False}
    assert H.wire_answer(spec, "Something else") == "Something else"                       # a partial list does not block typing
    assert H.wire_answer({"kind": "select", "options": ["A"], "options_complete": False}, "Z") == "Z"
    with pytest.raises(ValueError):
        H.wire_answer({"kind": "radio", "options": ["Yes", "No"]}, "Maybe")
    assert H.wire_answer({"kind": "text"}, None) == "skip" and H.wire_answer({"kind": "checkboxes", "options": ["A", "B"], "multiple": True}, ["B", "A"]) == "B; A"


def test_question_spec_reads_forms_fields_and_dicts():
    from jobagent.tools.forms import Field
    f = Field(id="7", kind="buttons", label="Are you 18?", required=True, options=["Yes", "No"])
    spec = H.question_spec(f, quick=["skip"])
    assert (spec["id"], spec["kind"], spec["options"], spec["required"], spec["quick"]) == ("7", "radio", ["Yes", "No"], True, ["skip"])
    assert H.question_spec({"id": 3, "kind": "weird", "label": "x"})["kind"] == "other"
    assert H.question_spec({"id": 3, "kind": "date_parts", "label": "x"})["kind"] == "date"


def test_the_agent_side_ask_stores_the_structure(tmp_path):
    h = H.Hitl(tmp_path / "hitl", lambda m: None)
    import threading
    got = {}
    t = threading.Thread(target=lambda: got.update(v=h.ask("p", questions=[{"id": "a", "label": "L", "kind": "radio", "options": ["x", "y"]}],
                                                           context={"company": "Acme", "title": ""}, timeout_s=10, poll_s=0.05)))
    t.start()
    import time
    for _ in range(100):
        reqs = h.list_pending()
        if reqs:
            break
        time.sleep(0.05)
    assert reqs[0].questions[0]["options"] == ["x", "y"] and reqs[0].context["company"] == "Acme" and "title" not in reqs[0].context
    h.answer(reqs[0].id, "x")
    t.join(5)
    assert got["v"] == "x"


# --- the queue editor over HTTP -----------------------------------------------------------------------------------------------------------

def test_add_reorder_and_run_the_queue_over_http(env):
    s, srv, started = env
    code, out = call(srv, "POST", "/api/queue/add", {"text": "https://jobs.lever.co/zoox/abc-123\nAcme Labs", "where": "top"})
    assert code == 200 and [r["result"] for r in out["results"]] == ["added", "added"]
    queue = call(srv, "GET", "/api/queue")[1]
    queued = [x for x in queue if x["status"] == "queued"]
    assert queued[0]["company"] == "zoox" and queued[0]["position"] == 1 and queued[0]["user_edited"]
    assert any(w["company"] == "Acme Labs" for w in call(srv, "GET", "/api/watchlist")[1])
    ids = [x["id"] for x in reversed(queued)]
    assert call(srv, "POST", "/api/queue/reorder", {"ids": ids})[0] == 200
    assert [x["id"] for x in call(srv, "GET", "/api/queue")[1] if x["status"] == "queued"] == ids
    code, out = call(srv, "POST", "/api/queue/run", {"through": "apply", "limit": 2})
    assert code == 200 and started and started[-1][0][-8:-6] != "run" and "queue" in started[-1][0] and "--limit" in started[-1][0]
    assert "submit" not in " ".join(started[-1][0])
    assert call(srv, "POST", "/api/queue/run", {"through": "submit", "limit": 2})[0] == 400


def test_company_run_goes_through_jobagent_run_and_marks_the_list(env):
    s, srv, started = env
    wid = call(srv, "GET", "/api/watchlist")[1][0]
    code, _ = call(srv, "POST", "/api/watchlist/run", {"companies": [wid["company"]], "limit": 3})
    assert code == 200 and started[-1][0][1:3] == ["run", wid["company"]] or "run" in started[-1][0]
    assert call(srv, "GET", "/api/watchlist")[1][0]["last_run_at"]


def test_set_aside_edit_delete_and_their_errors_over_http(env):
    s, srv, _ = env
    rows = call(srv, "GET", "/api/queue")[1]
    with_app = next(x for x in rows if x["applications"])
    assert call(srv, "POST", "/api/queue/delete", {"ids": [with_app["id"]]})[1]["deleted"] == 0            # an application is on record: kept
    call(srv, "POST", "/api/queue/add", {"text": "https://jobs.example.test/fresh"})                       # a posting with no application yet
    q = next(x for x in call(srv, "GET", "/api/queue")[1] if x["url"].endswith("/fresh"))
    assert call(srv, "POST", "/api/queue/state", {"ids": [q["id"]], "action": "skip"})[1]["changed"] == 1
    assert call(srv, "POST", "/api/queue/edit", {"id": q["id"], "company": "Renamed Co", "title": "New title"})[1]["changed"]["company"] == "Renamed Co"
    assert call(srv, "POST", "/api/queue/edit", {"id": "1"})[0] == 400
    assert call(srv, "POST", "/api/queue/move", {"id": q["id"], "to": "top"})[0] == 400                    # no longer queued
    assert call(srv, "POST", "/api/queue/delete", {"ids": [q["id"]]})[1]["deleted"] == 1
    assert call(srv, "POST", "/api/queue/nothing", {})[0] in (400, 404)


def test_the_new_routes_obey_the_same_guards(env):
    s, srv, started = env
    for path in ("/api/queue/add", "/api/queue/reorder", "/api/queue/state", "/api/queue/delete", "/api/queue/run", "/api/watchlist/add"):
        assert call(srv, "POST", path, {"text": "x"}, cookie=False)[0] == 401
        assert call(srv, "POST", path, {"text": "x"}, headers={"X-Dashboard": "0"})[0] == 403
        assert call(srv, "POST", path, {"text": "x"}, host="evil.example")[0] == 403
        assert call(srv, "POST", path, {"text": "x"}, headers={"Origin": "http://evil.example"})[0] == 403
    assert not started


def test_read_only_refuses_every_queue_change(tmp_path):
    from jobagent.config import Settings
    from jobagent.dashboard.server import make_server
    import threading
    s = Settings(**seed(tmp_path))
    srv = make_server(s, "127.0.0.1", 0, read_only=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        for path in ("/api/queue/add", "/api/queue/reorder", "/api/queue/state", "/api/watchlist/update"):
            assert call(srv, "POST", path, {"text": "https://x.test/a", "ids": [1]})[0] == 403
        assert call(srv, "GET", "/api/queue")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_database_not_yet_upgraded_still_lists_the_queue(tmp_path):
    from jobagent.config import Settings
    s = Settings(**seed(tmp_path))
    c = sqlite3.connect(s.state_dir / "jobs.sqlite")
    c.execute("ALTER TABLE jobs RENAME COLUMN priority TO prio_old")
    c.commit()
    c.close()
    rows = data.queue(s)
    assert rows and "company" in rows[0]
