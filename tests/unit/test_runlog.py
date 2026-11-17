"""Step 22: readable, colour-coded run logs."""
import io
import json
import re
from pathlib import Path

import pytest
from rich.console import Console

from jobagent import log as L

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def make(tmp_path, color=True, step="apply", job="acme-1", quiet=False):
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=color, color_system="standard" if color else None, width=200, highlight=False, soft_wrap=True)
    return L.RunLog(step, job, console=console, directory=tmp_path / "logs", quiet=quiet), buf


def test_lines_are_classified_by_what_they_say():
    assert L.classify("apply: page 2: Contact (6 fields)")[:2] == ("info", "apply")
    assert L.classify("apply: the page keeps refusing Next")[0] == "warn"
    assert L.classify("tailor: RuntimeError: model down")[0] == "error"
    assert L.classify("apply: reached the review screen (Submit not clicked)")[0] == "ok"
    assert L.classify("  added an entry: Add (Work Experience)")[0] == "dim"
    assert L.classify("$ jobagent apply outputs/a/1 a")[0:3:2] == ("cmd", "jobagent apply outputs/a/1 a")
    assert L.classify("[HITL ab12cd34] Which option?")[0] == "hitl"
    assert L.classify("[llm] Voyager unavailable") == ("warn", "llm", "Voyager unavailable")


def test_every_line_names_its_step_and_colour_means_something(tmp_path):
    log, buf = make(tmp_path)
    log("apply: reached the review screen")
    log("apply: the page keeps refusing Next")
    log("scrape: RuntimeError: boom")
    log("$ jobagent apply outputs/a/1 a")
    log("  progress detail")
    log("plain line from the queue")
    out = buf.getvalue()
    assert "[apply]" in ANSI.sub("", out) and "[scrape]" in ANSI.sub("", out)
    assert "\x1b[32m" in out and "\x1b[33m" in out and "\x1b[1;31m" in out and "\x1b[36m" in out and "\x1b[2m" in out    # green yellow red cyan dim
    plain = ANSI.sub("", out)
    assert "✓ [apply] reached the review screen" in plain and "! [apply] the page keeps refusing Next" in plain
    assert "✗ [scrape] RuntimeError: boom" in plain and "[apply] plain line from the queue".replace("apply", "apply") in plain


def test_no_color_and_non_terminals_get_plain_text(tmp_path):
    log, buf = make(tmp_path, color=False)
    log("apply: reached the review screen")
    log("scrape: failed")
    assert "\x1b" not in buf.getvalue() and "[apply] reached the review screen" in buf.getvalue()


def test_identical_consecutive_lines_collapse_and_the_file_keeps_them_all(tmp_path):
    log, buf = make(tmp_path)
    for _ in range(4):
        log("apply: the page keeps refusing Next")
    log("apply: moved on (the address changed)")
    out = ANSI.sub("", buf.getvalue())
    assert out.count("the page keeps refusing Next") == 1 and "the same line 3 more time(s)" in out
    assert log.path.read_text().count("the page keeps refusing Next") == 4


def test_a_long_raw_error_is_shortened_on_screen_and_complete_in_the_file(tmp_path):
    log, buf = make(tmp_path)
    raw = "apply: failed: 2 validation errors for Answer\n" + "value\n  Field required [type=missing, input_value={...}]\n" * 12
    log(raw)
    shown = ANSI.sub("", buf.getvalue())
    assert shown.count("\n") <= 3 and "Field required" not in shown
    assert "Field required" in log.path.read_text()
    long_one = "tailor: RuntimeError: " + "x" * 500
    log(long_one)
    assert "(full text: " in ANSI.sub("", buf.getvalue()) and "x" * 500 in log.path.read_text()


def test_each_run_writes_a_plain_log_and_a_jsonl_twin(tmp_path):
    log, _ = make(tmp_path, step="tailor", job="acme/1 job")
    log("tailor: reached the review screen")
    log("tailor: failed: something")
    log.close()
    assert re.search(r"-tailor-acme-1-job-[0-9a-f]{8}\.log$", log.path.name) and re.match(r"\d{4}-\d{2}-\d{2}T\d{6}-", log.path.name)
    rows = [json.loads(x) for x in log.path.with_suffix(".jsonl").read_text().splitlines()]
    assert rows[-1]["level"] == "end"                                       # the closing record the dashboard uses
    rows = rows[:-1]
    assert [(r["level"], r["step"]) for r in rows] == [("ok", "tailor"), ("warn", "tailor")]
    assert all(set(r) == {"ts", "run", "cmd", "job", "level", "step", "msg"} for r in rows)
    assert "\x1b" not in log.path.read_text()


def test_a_crash_is_one_red_line_and_a_trace_file_never_a_traceback(tmp_path):
    log, buf = make(tmp_path, step="scrape")
    try:
        raise ValueError("bad posting " + "y" * 300)
    except ValueError as exc:
        log.crash(exc)
    out = ANSI.sub("", buf.getvalue())
    assert out.count("\n") == 1 and "Traceback" not in out and "ValueError: bad posting" in out and "full trace:" in out
    assert "Traceback (most recent call last)" in log.path.read_text()


def test_a_question_is_a_boxed_prompt_with_the_command_to_paste_on_the_host(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "in_container", lambda: True)
    log, buf = make(tmp_path)
    log("[HITL ab12cd34] Which option means yes?\n  → answer with: jobagent hitl answer ab12cd34 '<answer>'")
    out = ANSI.sub("", buf.getvalue())
    assert "question for you ab12cd34" in out and "Which option means yes?" in out
    assert "docker exec jobagent jobagent hitl answer ab12cd34 '<answer>'" in out
    assert L.host_command("docker exec jobagent jobagent x") == "docker exec jobagent jobagent x"       # never doubled
    monkeypatch.setattr(L, "in_container", lambda: False)
    assert L.host_command("jobagent hitl list") == "jobagent hitl list"


def test_the_hitl_request_itself_carries_the_host_form(tmp_path, monkeypatch):
    from jobagent.tools.hitl import Hitl
    monkeypatch.setattr(L, "in_container", lambda: True)
    seen = []
    h = Hitl(tmp_path / "hitl", seen.append)
    (tmp_path / "hitl" / "answers").mkdir(parents=True, exist_ok=True)
    import threading, time

    def answer():
        for _ in range(50):
            for p in (tmp_path / "hitl" / "pending").glob("*.json"):
                part = tmp_path / "hitl" / "answers" / (p.name + ".part")
                part.write_text(json.dumps({"value": "yes"}))
                part.rename(tmp_path / "hitl" / "answers" / p.name)         # atomic: the reader never sees a half-written file
                return
            time.sleep(0.05)
    threading.Thread(target=answer, daemon=True).start()
    assert h.ask("Continue?", poll_s=0.05, timeout_s=10) == "yes"
    assert "docker exec jobagent jobagent hitl answer" in seen[0]


def test_a_model_fallback_is_one_yellow_line(monkeypatch, capsys, tmp_path):
    from jobagent import llm
    from jobagent.config import get_settings
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(L, "_live", [])
    L._default = None
    n = llm._FallbackNotice()
    n.on_chat_model_start({"kwargs": {"model": "qwen3:4b"}}, [])
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "qwen3:4b" in err and "falls back" in err
    L._default = None


def test_quiet_hides_progress_detail_only(tmp_path):
    log, buf = make(tmp_path, quiet=True)
    log("  progress detail")
    log("apply: failed: something")
    out = ANSI.sub("", buf.getvalue())
    assert "progress detail" not in out and "something" in out
    assert "progress detail" in log.path.read_text()                                  # the file keeps everything


def test_the_command_entry_turns_an_unexpected_exception_into_one_line(tmp_path, monkeypatch, capsys):
    import typer
    from jobagent.config import get_settings
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    app = typer.Typer(pretty_exceptions_enable=False)

    @app.command()
    def boom() -> None:
        raise RuntimeError("model unavailable")
    monkeypatch.setattr("sys.argv", ["jobagent"])
    with pytest.raises(SystemExit) as e:
        L.run_cli(app)
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "RuntimeError: model unavailable" in out and "Traceback" not in out
    assert list((tmp_path / "logs").glob("*-jobagent-*.log"))
    get_settings.cache_clear()
