"""Step 0 end-to-end check: everything the harness must provide before any workflow step runs."""

import base64
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.messages import HumanMessage

from jobagent import llm
from jobagent.config import Settings, get_settings

PERSIST_KEY = "harness-check"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    seconds: float = 0.0


def _run(name: str, fn: Callable[[], str]) -> Check:
    start = time.monotonic()
    try:
        return Check(name, True, fn(), time.monotonic() - start)
    except Exception as e:  # each check reports its own failure; the run continues
        return Check(name, False, f"{type(e).__name__}: {e}", time.monotonic() - start)


def _voyager_invoke(role: llm.Role, s: Settings, messages, attempts: int = 3):
    """Call Voyager directly (no Ollama fallback), retrying transient connection errors.

    Returns (message, retries). Voyager occasionally drops connections or queues requests;
    a blip shouldn't fail the harness, but repeated failures should.
    """
    from openai import APIConnectionError, APITimeoutError

    for i in range(attempts):
        try:
            return llm._primary(role, s).invoke(messages), i
        except (APIConnectionError, APITimeoutError):
            if i == attempts - 1:
                raise
            time.sleep(5 * (i + 1))


def _model_name(msg) -> str:
    meta = msg.response_metadata or {}
    return meta.get("model_name") or meta.get("model") or "?"


def check_volumes(s: Settings) -> str:
    for d in (s.data_dir, s.state_dir, s.outputs_dir):
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
    return ", ".join(str(d) for d in (s.data_dir, s.state_dir, s.outputs_dir))


def check_binaries(_: Settings) -> str:
    missing = [b for b in ("google-chrome", "latexmk", "pdflatex", "pdftotext", "pdftoppm", "tesseract", "x11vnc")
               if shutil.which(b) is None]
    if missing:
        raise RuntimeError(f"missing on PATH: {', '.join(missing)}")
    return "chrome, latexmk, pdflatex, poppler, tesseract, x11vnc"


def check_latex(_: Settings) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        tex = Path(tmp) / "probe.tex"
        # Packages used by the user's resume template (fontawesome5 comes from texlive-fonts-extra).
        tex.write_text(r"\documentclass{article}\usepackage{latexsym,titlesec,marvosym,enumitem,fancyhdr,tabularx,"
                       r"fontawesome5,hyperref}\usepackage[empty]{fullpage}\usepackage[english]{babel}"
                       r"\input{glyphtounicode}\pdfgentounicode=1"
                       r"\begin{document}\faGithub\ Harness probe\end{document}")
        subprocess.run(["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", tex.name],
                       cwd=tmp, check=True, capture_output=True, timeout=120)
        text = subprocess.run(["pdftotext", "probe.pdf", "-"], cwd=tmp, check=True,
                              capture_output=True, text=True).stdout
    if "Harness probe" not in text:
        raise RuntimeError("compiled PDF text mismatch")
    return "latexmk → PDF → pdftotext round-trip ok"


def check_resume_template(s: Settings) -> str:
    template = s.data_dir / "templates" / "resume.tex"
    if not template.exists():
        return "no data/templates/resume.tex yet (skipped)"
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(template, Path(tmp) / "resume.tex")
        run = subprocess.run(["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "resume.tex"],
                             cwd=tmp, capture_output=True, text=True, timeout=180)
        if run.returncode:
            errors = [l for l in (Path(tmp) / "resume.log").read_text(errors="replace").splitlines() if l.startswith("!")]
            raise RuntimeError(f"template does not compile: {errors[:3] or run.stdout[-300:]}")
        pages = subprocess.run(["pdfinfo", "resume.pdf"], cwd=tmp, capture_output=True, text=True).stdout
    count = next((l.split()[-1] for l in pages.splitlines() if l.startswith("Pages:")), "?")
    return f"data/templates/resume.tex compiles ({count} page(s))"


def check_chrome(s: Settings) -> str:
    from jobagent.tools.browser import persistent_context, screenshot

    shot = s.state_dir / "logs" / "harness-chrome.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.unlink(missing_ok=True)  # the vision check must never read a stale screenshot
    with persistent_context(s) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_content("<h1 style='font:48px sans-serif'>JobAgent harness: Chrome OK</h1>")
        screenshot(page, str(shot))
        ua = page.evaluate("navigator.userAgent")
    # Persistent contexts have no Browser object, so read the version from the UA.
    version = next((t.split("/")[1] for t in ua.split() if t.startswith("Chrome/")), "?")
    mode = "headless" if s.headless else f"headful on {s.display}"
    return f"Chrome {version} ({mode}); screenshot {shot}"


def check_planner(s: Settings) -> str:
    # Call Voyager directly: through chat() a silent fallback to Ollama would look like a pass.
    msg, retries = _voyager_invoke("planner", s, [HumanMessage("Reply with exactly the word: pong")])
    return f"{_model_name(msg)} → {msg.content.strip()[:40]!r}" + (f" ({retries} retries)" if retries else "")


def check_vision(s: Settings) -> str:
    shot = s.state_dir / "logs" / "harness-chrome.png"
    if not shot.exists():
        raise RuntimeError("needs the Chrome screenshot from the chrome check")
    b64 = base64.b64encode(shot.read_bytes()).decode()
    msg, retries = _voyager_invoke("vision", s, [HumanMessage(content=[
        {"type": "text", "text": "What single word follows 'Chrome' in this screenshot? Answer with that word only."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ])])
    answer = msg.content.strip()
    if "ok" not in answer.lower():
        raise RuntimeError(f"vision model read {answer!r}, expected 'OK'")
    return f"{_model_name(msg)} read {answer!r}" + (f" ({retries} retries)" if retries else "")


def check_embeddings(s: Settings) -> str:
    emb = llm.embeddings(s)
    vec = emb.embed_query("software engineer, Python, distributed systems")
    src = "Ollama (fallback!)" if emb.used_fallback else s.model_embed
    if emb.used_fallback:
        raise RuntimeError(f"Voyager embeddings failed; only the fallback answered (dim {len(vec)})")
    return f"{src} dim={len(vec)}"


def check_fallback(s: Settings) -> str:
    broken = s.model_copy(update={"openai_base_url": "http://127.0.0.1:9/v1", "llm_max_retries": 0, "llm_timeout_s": 5})
    msg = llm.chat("fast", broken).invoke([HumanMessage("Reply with exactly the word: pong /no_think")])
    emb = llm.embeddings(broken)
    vec = emb.embed_query("fallback probe")
    if not emb.used_fallback:
        raise RuntimeError("embeddings did not fall back")
    return f"chat → {_model_name(msg)} {msg.content.strip()[-20:]!r}; embed → {s.fallback_embed} dim={len(vec)}"


def check_vault(s: Settings) -> str:
    from jobagent.tools.vault import Vault

    vault = Vault(s.vault_path, s.vault_key.get_secret_value())
    previous = vault.get(PERSIST_KEY)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    runs = (previous or {}).get("runs", 0) + 1
    vault.set(PERSIST_KEY, last_run=now, runs=runs)
    if vault.get(PERSIST_KEY)["last_run"] != now:
        raise RuntimeError("vault round-trip mismatch")
    seen = f"previous run {previous['last_run']}" if previous else "first run (no earlier entry)"
    return f"round-trip ok; run #{runs}; {seen}"


def check_capabilities_file(s: Settings) -> str:
    from jobagent.tools.capabilities import HEADER

    path = s.missing_capabilities_path
    if not path.exists():
        path.write_text(HEADER)
    pending = [l for l in path.read_text().splitlines() if l and not l.startswith("#")]
    return f"{path} ({len(pending)} pending)"


CHECKS: list[tuple[str, Callable[[Settings], str]]] = [
    ("volumes", check_volumes),
    ("binaries", check_binaries),
    ("latex", check_latex),
    ("resume template", check_resume_template),
    ("chrome", check_chrome),
    ("vault", check_vault),
    ("capabilities file", check_capabilities_file),
    ("voyager planner", check_planner),
    ("voyager vision", check_vision),
    ("voyager embeddings", check_embeddings),
    ("ollama fallback", check_fallback),
]


def run_all(settings: Settings | None = None, only: set[str] | None = None) -> list[Check]:
    s = settings or get_settings()
    return [_run(name, lambda fn=fn: fn(s)) for name, fn in CHECKS if not only or name in only]
