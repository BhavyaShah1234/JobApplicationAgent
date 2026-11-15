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
                if f:
                    f.close()                              # a half-opened pair never leaks a handle
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

    def _record(self, level: str, step: str, text: str) -> None:
        self._open()
        if self._file is None:
            return
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            self._file.write(f"{ts} {level:<5} [{step}] {text}\n")
            self._jsonl.write(json.dumps({"ts": ts, "run": self.run_id, "job": self.job, "level": level, "step": step, "msg": text}) + "\n")
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
        for f in (self._file, self._jsonl):
            try:
                if f:
                    f.close()
            except OSError:
                pass
        self._file = self._jsonl = None
        if self in _live:
            _live.remove(self)


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
_live: list[RunLog] = []                                   # loggers not yet closed; the newest named run receives code with no log of its own


@atexit.register
def _close_all() -> None:
    for lg in list(_live):
        lg.close()


def default() -> RunLog:
    """The logger for code that has no `log` callable of its own (a model fallback notice): the run in progress, so the warning lands
    in that run's file; a standalone stderr logger only when no run is open."""
    global _default
    running = [lg for lg in _live if lg is not _default]
    if running:
        return running[-1]
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
