"""Codex review R11-R16: logging config and sinks, HITL rendering, review queue robustness, quoted commands."""
import io
import json
import shlex

import pytest
from rich.console import Console

from jobagent import db
from jobagent import log as L
from jobagent.config import get_settings
from jobagent.tools import review_queue
from jobagent.tools.hitl import Hitl


def make(tmp_path, quiet=None, job="a"):
    buf = io.StringIO()
    return L.RunLog("apply", job, console=Console(file=buf, width=160, highlight=False, soft_wrap=True), directory=tmp_path / "logs", quiet=quiet), buf


def test_logging_yaml_quiet_hides_detail_on_screen_but_not_in_either_file(monkeypatch, tmp_path):            # R11
    (tmp_path / "logging.yaml").write_text("log_level: quiet\n")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    log, buf = make(tmp_path)
    log("  detail: field 3 checked")
    log("apply: reached the review screen")
    log.close()
    assert "field 3" not in buf.getvalue() and "review screen" in buf.getvalue()
    assert "field 3 checked" in log.path.read_text() and "field 3 checked" in log.path.with_suffix(".jsonl").read_text()
    get_settings.cache_clear()


def test_hitl_question_text_with_brackets_prints_literally(tmp_path):                                       # R12
    log, buf = make(tmp_path)
    log("[HITL ab12cd34] Is [/missing] your [bold]name[/]?\n  → answer with: jobagent hitl answer ab12cd34 '<answer>'")
    out = buf.getvalue()
    assert "[/missing]" in out and "[bold]name[/]" in out and "jobagent hitl answer ab12cd34" in out


def test_a_failing_notify_does_not_leave_the_question_unusable(tmp_path):                                   # R12
    def boom(_):
        raise RuntimeError("terminal gone")
    h = Hitl(tmp_path / "hitl", boom)
    with pytest.raises(Exception) as err:
        h.ask("q?", timeout_s=0.1, poll_s=0.05)
    assert "RuntimeError" not in str(err.value)                                        # it timed out waiting; the notify error never escaped
    assert not list((tmp_path / "hitl" / "pending").glob("*.json"))                   # and the pending file was cleaned up


def test_two_runs_of_one_step_on_one_day_get_distinct_files_with_the_run_id_in_every_record(tmp_path):      # R13
    a, _ = make(tmp_path)
    b, _ = make(tmp_path)
    a("apply: one")
    b("apply: two")
    assert a.path != b.path
    rec = json.loads(a.path.with_suffix(".jsonl").read_text().splitlines()[0])
    assert rec["run"] == a.run_id and rec["job"] == "a" and a.run_id != b.run_id
    a.close(); b.close()


def test_a_fallback_notice_lands_in_the_open_runs_file(tmp_path):                                           # R13
    a, _ = make(tmp_path)
    a("apply: start")
    L.default().warn("model fell back")
    assert "model fell back" in a.path.read_text()
    a.close()


def test_a_broken_sink_never_stops_the_workflow_or_masks_the_error(tmp_path, capsys):                       # R14
    log, buf = make(tmp_path)
    log("apply: start")

    class Broken:
        def write(self, *_): raise OSError("disk full")
        def flush(self): pass
        def close(self): pass
    log._file = Broken()
    log("apply: next")                                                                 # must not raise
    log("apply: and another")
    assert "next" in buf.getvalue() and capsys.readouterr().err.count("run log disabled") == 1      # said once
    log.crash(ValueError("the real error"))
    assert "the real error" in buf.getvalue()


def test_a_half_opened_pair_of_sinks_is_closed(tmp_path, monkeypatch):                                      # R14
    opened = []
    real = open

    def fake(path, *a, **k):
        if str(path).endswith(".jsonl"):
            raise OSError("no space")
        f = real(path, *a, **k)
        opened.append(f)
        return f
    monkeypatch.setattr("builtins.open", fake)
    log, _ = make(tmp_path)
    log("apply: x")
    assert opened and all(f.closed for f in opened)


def test_the_review_queue_survives_malformed_result_files(tmp_path, monkeypatch):                           # R15
    monkeypatch.setattr(L, "in_container", lambda: False)
    conn = db.connect(tmp_path / "jobs.sqlite")
    cases = {"good": {"ready_to_submit": True, "blockers": []}, "list": [1], "string_flag": {"ready_to_submit": "yes", "blockers": []},
             "bad_blockers": {"ready_to_submit": True, "blockers": [1, 2]}, "missing": None}
    for name, res in cases.items():
        d = tmp_path / "o" / name
        (d / "application").mkdir(parents=True)
        if res is not None:
            (d / "application" / "application.json").write_text(json.dumps(res))
        conn.execute("INSERT INTO applications (job_dir, company, title, state, filled_at) VALUES (?, ?, 't', 'filled', '2026-11-01T00:00:00Z')", (str(d), name))
    conn.commit()
    by = {r["company"]: r for r in review_queue.waiting(conn, tmp_path)}
    assert set(by) == set(cases) and [n for n, r in by.items() if r["ready"]] == ["good"]
    assert all(by[n]["blockers"] for n in cases if n != "good")


def test_commands_are_quoted_and_attach_a_terminal_when_input_is_needed(monkeypatch):                       # R16
    monkeypatch.setattr(L, "in_container", lambda: True)
    cmd = L.host_command(["jobagent", "submit", "outputs/a b/1; rm -rf x", "acme"], interactive=True)
    assert cmd.startswith("docker exec -it jobagent ")
    assert shlex.split(cmd)[4:] == ["jobagent", "submit", "outputs/a b/1; rm -rf x", "acme"]
    assert L.host_command(["jobagent", "hitl", "answer", "ab", "<answer>"]).startswith("docker exec jobagent ")      # literal answer: no tty
    seen = []
    h = Hitl.__new__(Hitl)
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    h = Hitl(d, seen.append)
    with pytest.raises(Exception):
        h.ask("password?", secret=True, timeout_s=0.1, poll_s=0.05)
    assert "docker exec -it jobagent jobagent hitl answer" in seen[0] and "<answer>" not in seen[0]
