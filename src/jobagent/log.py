"""Readable run logs (step 22).

One place decides how a run talks to the candidate:

- every line says which STEP it belongs to (``[apply]``, ``[tailor]``, ``[queue]`` ...), taken from the "apply: ..." prefix the steps already
  write, else from the command being run;
- colour carries meaning: red = error, yellow = warning (something failed, was refused or will be retried), green = success, cyan = a
  command you can paste, bold magenta in a box = a question that waits for you, dim = progress detail. ``NO_COLOR`` is honoured and a
  non-terminal gets plain text;
- noise is reduced: identical consecutive lines collapse to one with a count, long raw errors (a pydantic dump) are shortened on screen
  (the full text is always kept in the file), and a model fallback is one yellow line;
- a traceback never reaches the screen: one red line says what failed and where the full trace is;
- every run also writes ``state/logs/<date>-<step>-<job>.log`` (plain, timestamped) and a ``.jsonl`` twin ({ts, level, step, msg}) for later
  reading (and for the dashboard, step 17).

Steps keep calling ``log(message)``; `runlog(step)` returns that callable, so nothing has to be rewritten to benefit.
"""

import atexit
import contextvars
import json
import os
import re
import shlex
import sys
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

ERROR = re.compile(r"(?i)\b(error|exception|traceback|crashed|fatal)\b|\b\w+(Error|Exception)\b")
WARN = re.compile(r"(?i)\b(failed|failure|blocked|stopped|refused|not ready|unavailable|timed? out|timeout|retry(ing)?|falling back|"
                  r"rejected|refus\w*|skipped|cannot|could not|unreadable|mismatch|needs? (the )?candidate|left for review)\b")
OK = re.compile(r"(?i)^(\W*)(ok|done|verified|accepted|reached|signed in|saved|stored|passed|complete[d]?|reused|moved on|written)\b|"
                r"\b(all (fields )?verified|signed in|passed)\b")
TAG = re.compile(r"^(?:\[([a-z][a-z0-9_-]{1,18})\]\s+|([a-z][a-z0-9_-]{1,18}):\s+)(.*)$", re.S)
COMMAND = re.compile(r"^\$ ")
HITL = re.compile(r"^\[HITL ([0-9a-f]{6,10})\]\s*(.*)$", re.S)
SHORT_ERROR = 220
MAX_LINES = 6          # a message longer than this is a dump: shown as its first line


def in_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("container") is not None


def host_command(cmd: "str | list[str]", interactive: bool = False) -> str:
    """The command as you type it on the host: inside the container the in-container form does not work from your shell. A list is
    quoted argument by argument (`shlex.join`), so a path with spaces stays one argument. `interactive` (a hidden prompt, a typed
    approval) attaches stdin and a terminal: `docker exec -it`."""
    text = shlex.join(cmd) if isinstance(cmd, (list, tuple)) else cmd
    if in_container() and not text.startswith("docker "):
        return f"docker exec {'-it ' if interactive else ''}jobagent {text}"
    return text


def condense(message: str, limit: int = SHORT_ERROR) -> str:
    """A raw error shortened for the screen: its first line, cut at `limit`. (The file log keeps all of it.)"""
    first = " ".join(message.strip().splitlines()[0].split()) if message.strip() else ""
    return first if len(first) <= limit and len(message.strip().splitlines()) <= 1 else first[:limit].rstrip() + " ..."


def classify(message: str) -> tuple[str, str, str]:
    """(level, step tag or '', text) of a free-text line: level is error | warn | ok | cmd | hitl | dim | info."""
    if HITL.match(message):
        return "hitl", "", message
    if COMMAND.match(message):
        return "cmd", "", message[2:]
    tag, text = "", message
    if (m := TAG.match(message.strip())):
        tag, text = m.group(1) or m.group(2), m.group(3)
    if ERROR.search(text):
        level = "error"
    elif WARN.search(text):
        level = "warn"
    elif OK.search(text):
        level = "ok"
    elif message.startswith((" ", "\t")):
        level = "dim"
    else:
        level = "info"
    return level, tag, text.strip()


STYLE = {"error": "bold red", "warn": "yellow", "ok": "green", "cmd": "cyan", "dim": "dim", "info": ""}
ICON = {"error": "✗", "warn": "!", "ok": "✓", "cmd": "$", "dim": " ", "info": " "}


class RunLog:
    def __init__(self, step: str, job: str = "", console: Console | None = None, directory: Path | None = None, quiet: bool | None = None):
        if quiet is None:                                  # the effective setting: env, .env or config/logging.yaml
            try:
                from jobagent.config import get_settings
                quiet = get_settings().log_level == "quiet"
            except Exception:
                quiet = os.environ.get("LOG_LEVEL", "").lower() == "quiet"
        self.step, self.job, self.quiet = step, job, bool(quiet)
        self.run_id = uuid.uuid4().hex[:8]                 # tells two runs of one step on one day apart, in the file name and every record
        self.console = console or Console(highlight=False, soft_wrap=True)
        self.directory = directory
        _live.append(self)
        _current.set(self)                                 # code running in THIS thread/context that has no log of its own writes here
        self._last: tuple | None = None
        self._repeats = 0
        self._file = self._jsonl = None
        self._broken = False
        self.path: Path | None = None

    # -- files ---------------------------------------------------------------------------------------------------------------------
    def _open(self) -> None:
        if self._file is not None or self.directory is None or self._broken:
            return
        log = jsonl = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.job)[:40]
            stem = f"{datetime.now(UTC):%Y-%m-%dT%H%M%S}-{self.step}" + (f"-{safe}" if safe else "") + f"-{self.run_id}"
            path = self.directory / f"{stem}.log"
            log = open(path, "a", encoding="utf-8")
            jsonl = open(path.with_suffix(".jsonl"), "a", encoding="utf-8")
            self.path, self._file, self._jsonl = path, log, jsonl
        except OSError as exc:
            for f in (log, jsonl):
                try:
                    if f:
                        f.close()                          # a half-opened pair never leaks a handle; a failing close changes nothing
                except OSError:
                    pass
            self._disable(exc)

    def _disable(self, exc: BaseException) -> None:
        """The log file cannot be written: say so once on stderr, stop trying, and let the run go on."""
        if not self._broken:
            print(f"jobagent: run log disabled ({type(exc).__name__}: {exc}); progress is shown on screen only", file=sys.stderr)
        self._broken = True
        for f in (self._file, self._jsonl):
            try:
                if f:
                    f.close()
            except OSError:
                pass
        self._file = self._jsonl = None

    def _record(self, level: str, step: str, text: str, extra: dict | None = None) -> None:
        self._open()
        if self._file is None:
            return
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            self._file.write(f"{ts} {level:<5} [{step}] {text}\n")
            from jobagent.progress import metadata
            rec = {"ts": ts, "time_ns": time.time_ns(), "pid": os.getpid(), "run": self.run_id,
                   "cmd": self.step, "job": self.job, "level": level, "step": step, "msg": text, **metadata()}
            if extra:
                rec.update({k: v for k, v in extra.items() if v not in (None, "")})          # e.g. a progress record names its own job
            self._jsonl.write(json.dumps(rec, default=str) + "\n")
            self._file.flush()
            self._jsonl.flush()
        except (OSError, ValueError) as exc:               # disk full, closed handle: never let a progress line stop the workflow
            self._disable(exc)

    # -- screen --------------------------------------------------------------------------------------------------------------------
    def _flush_repeats(self) -> None:
        if self._repeats:
            self.console.print(f"  [dim]↳ the same line {self._repeats} more time(s)[/]")
            self._repeats = 0

    def emit(self, level: str, step: str, text: str, full: str | None = None) -> None:
        step = step or self.step
        self._record(level, step, full if full is not None else text)
        if level == "dim" and self.quiet:
            return
        key = (level, step, text)
        if key == self._last:
            self._repeats += 1
            return
        self._flush_repeats()
        self._last = key
        if level == "hitl":
            self._show_question(text)
            return
        style = STYLE.get(level, "")
        shown = condense(text) if level in ("error", "warn") and len(text) > SHORT_ERROR else text
        line = f"{ICON[level]} [bold]\\[{step}][/] " + (f"[{style}]{_esc(shown)}[/]" if style else _esc(shown))
        self.console.print(line)
        if shown != text and self.path is not None:
            self.console.print(f"  [dim](full text: {self.path})[/]")

    def _show_question(self, text: str) -> None:
        m = HITL.match(text)
        rid, body = (m.group(1), m.group(2)) if m else ("", text)
        body, _, hint = body.partition("\n  → answer with:")
        shown = Group(Text(body.strip()), *([Text(""), Text(host_command(hint.strip()), style="cyan")] if hint else []))   # literal text, never markup
        self.console.print(Panel(shown, title=f"[bold magenta]question for you{' ' + rid if rid else ''}[/]", border_style="magenta"))

    def __call__(self, message) -> None:
        message = str(message)
        lines = [x for x in message.splitlines() if x.strip()]
        if len(lines) > MAX_LINES and not HITL.match(message):
            # a raw dump (a validation error, a stack): the first line on screen, all of it in the file
            level, tag, text = classify(lines[0])
            if level not in ("error", "warn"):
                level = "warn" if re.search(r"(?i)validation|invalid|failed", message) else level
            self.emit(level, tag, f"{text} (+{len(lines) - 1} more lines)", full=message.strip())
            return
        for part in (message.splitlines() or [""]) if not HITL.match(message) else [message]:
            if not part.strip():
                continue
            level, tag, text = classify(part)
            self.emit(level, tag, text, full=part.strip())

    def progress(self, stage: str, status: str = "running", label: str = "", i: int | None = None, n: int | None = None, detail: str = "",
                 **extra) -> None:
        """Where the work is, for the dashboard: `stage` (scrape, research, documents, apply, submit), its `status` (running, done, failed, blocked,
        skipped), a `label` for the step inside it ("ATS round"), `i` of `n` when it iterates, and a short `detail`. Written to the files only."""
        text = f"{stage} {status}" + (f": {label}" if label else "") + (f" {i}/{n}" if i is not None and n else (f" {i}" if i is not None else "")) \
            + (f" ({detail})" if detail else "")
        self._record("progress", self.step, text, {"stage": stage, "status": status, "label": label, "i": i, "n": n, "detail": detail[:200], **extra})

    def info(self, text): self.emit("info", "", text)
    def ok(self, text): self.emit("ok", "", text)
    def warn(self, text): self.emit("warn", "", text)
    def error(self, text): self.emit("error", "", text)
    def cmd(self, text): self.emit("cmd", "", text)

    def crash(self, exc: BaseException) -> None:
        """A failed command: one red line (what failed, what to do) and the path of the full trace; never a traceback on screen."""
        self._flush_repeats()
        trace = "".join(traceback.format_exception(exc))
        self._record("error", self.step, trace)
        where = f" (full trace: {self.path})" if self.path else ""
        self.console.print(f"✗ [bold]\\[{self.step}][/] [bold red]{_esc(type(exc).__name__)}: {_esc(condense(str(exc), 160))}[/]{where}")

    def close(self) -> None:
        self._flush_repeats()
        if self._file is not None:
            self._record("end", self.step, "finished")          # lets the dashboard tell a finished run from one that is still going
        for f in (self._file, self._jsonl):
            try:
                if f:
                    f.close()
            except OSError:
                pass
        self._file = self._jsonl = None
        if self in _live:
            _live.remove(self)
        if _current.get() is self:
            _current.set(None)


def _esc(text: str) -> str:
    return text.replace("[", "\\[")


def runlog(step: str, job: str = "", **kw) -> RunLog:
    """The `log` callable for a command: `log = runlog("tailor", job_id)`. Writes to state/logs (see the module doc)."""
    from jobagent.config import get_settings
    try:
        directory = get_settings().state_dir / "logs"
    except Exception:
        directory = None
    return RunLog(step, job, directory=kw.pop("directory", directory), **kw)


_default: RunLog | None = None
_live: list[RunLog] = []                                   # loggers not yet closed (closed at exit)
_current: contextvars.ContextVar = contextvars.ContextVar("jobagent_runlog", default=None)   # the run of THIS thread / task


@atexit.register
def _close_all() -> None:
    for lg in list(_live):
        lg.close()


def progress(log, stage: str, status: str = "running", label: str = "", i: int | None = None, n: int | None = None, detail: str = "", **extra) -> None:
    """`log.progress(...)` when `log` is a run log; nothing (and never an error) for a plain print or a test's lambda."""
    fn = getattr(log, "progress", None)
    if callable(fn):
        try:
            from jobagent.progress import metadata
            context = {k: v for k, v in metadata().items() if k not in {'stage', 'status', 'label', 'i', 'n', 'detail'}}
            fn(stage, status, label, i, n, detail, **(context | extra))
        except Exception:
            pass


def with_context(fn):
    """`fn` that runs in a COPY of the caller's context (taken now, copied again per call, so a pool may run it in several threads): the
    run log that is current here stays current inside a worker thread, so a model-fallback notice lands in the right run's file."""
    parent = contextvars.copy_context()

    def run(*args, **kwargs):
        return parent.copy().run(fn, *args, **kwargs)
    return run


def default() -> RunLog:
    """The logger for code that has no `log` callable of its own (a model fallback notice): the run opened in this thread/context, so the
    warning lands in that run's file; a standalone stderr logger when there is none (e.g. in a worker thread that was not given the
    context: use `contextvars.copy_context().run(...)` when starting one)."""
    global _default
    cur = _current.get()
    if cur is not None and cur in _live:                   # never another thread's run: a concurrent run's logger is not ours to pick
        return cur
    if _default is None:
        from jobagent.config import get_settings
        try:
            directory = get_settings().state_dir / "logs"
        except Exception:
            directory = None
        _default = RunLog("llm", console=Console(file=sys.stderr, highlight=False, soft_wrap=True), directory=directory)
    return _default


def run_cli(app) -> None:
    """Entry point: run the typer app; an unexpected exception ends as one red line plus a trace file, exit code 1."""
    log = runlog("jobagent")
    try:
        app()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        log.warn("interrupted")
        raise SystemExit(130) from None
    except Exception as exc:
        log.crash(exc)
        raise SystemExit(1) from exc
    finally:
        log.close()
