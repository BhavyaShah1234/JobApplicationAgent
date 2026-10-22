"""Step 4: Overleaf account (optional). Failures are reported, never fatal for the rest of the agent."""

import difflib
import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from jobagent import db
from jobagent.config import get_settings
from jobagent.tools import overleaf
from jobagent.tools.browser import persistent_context
from jobagent.tools.hitl import Hitl, open_hitl
from jobagent.tools.vault import Vault, open_vault


def setup(vault: Vault | None = None, hitl: Hitl | None = None, log=print) -> dict:
    vault, hitl = vault or open_vault(), hitl or open_hitl(log)
    email = (vault.get("gmail") or {}).get("email", "")
    with persistent_context() as ctx:
        page = ctx.pages[0]
        try:
            how = overleaf.login_with_google(page, email, hitl, log)
        except overleaf.OverleafError as e:
            log(f"overleaf: {e}; handing the login to you in plain Chrome")
            how = None
    if how is None:
        overleaf.login_by_hand(hitl, log)
        how = "logged in by hand"
    with persistent_context() as ctx:
        page = ctx.pages[0]
        if not overleaf.logged_in(page):
            raise overleaf.OverleafError("still not logged in to Overleaf")
        info = overleaf.account_info(page)
    backend = "mcp-git" if info["git_bridge"] else "browser"
    vault.set("overleaf", login="google", email=info["email"] or email, user_id=info["user_id"],
              git_bridge=info["git_bridge"], backend=backend, checked=datetime.now(UTC).isoformat(timespec="seconds"))
    db.record_agent_domain(db.connect(), "https://www.overleaf.com", "overleaf")
    log(f"overleaf: {how}; account {info['email'] or email}; Git integration: {info['git_bridge']}; backend: {backend}")
    return info


def _words(pdf: Path) -> list[str]:
    text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    return re.findall(r"\w+", text.lower())


def _pages(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", out).group(1))


def check(log=print) -> list[tuple[str, bool, str]]:
    """Round-trip a real project: create from the resume template, compile on Overleaf, compare with local
    latexmk, read back as zip, delete."""
    s = get_settings()
    results: list[tuple[str, bool, str]] = []
    template = s.data_dir / "templates" / "resume.tex"
    if not template.exists():
        template = s.data_dir / "templates" / "resume.example.tex"
    source = template.read_text()
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "main.tex").write_text(source)
        subprocess.run(["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                       cwd=tmp, capture_output=True, timeout=180, check=True)
        local_pdf = tmpdir / "main.pdf"
        with persistent_context() as ctx:
            page = ctx.pages[0]
            if not overleaf.logged_in(page):
                return [("overleaf session", False, "not logged in — run `jobagent step overleaf`")]
            results.append(("overleaf session", True, f"logged in as {overleaf.account_info(page)['email']}"))
            name = f"jobagent-selftest-{datetime.now(UTC):%Y%m%dT%H%M%S}"
            project_id = None
            try:
                project_id = overleaf.create_project(page, {"main.tex": source}, name)
                results.append(("create project", True, f"{name} ({overleaf.BASE}/project/{project_id})"))
                remote_pdf = tmpdir / "overleaf.pdf"
                remote_pdf.write_bytes(overleaf.compile_pdf(page, project_id))
                lp, rp = _pages(local_pdf), _pages(remote_pdf)
                ratio = difflib.SequenceMatcher(None, _words(local_pdf), _words(remote_pdf)).ratio()
                keep = s.state_dir / "logs" / "overleaf-selftest.pdf"
                shutil.copy(remote_pdf, keep)
                ok = lp == rp and ratio >= 0.97
                results.append(("compile matches local", ok,
                                f"pages local {lp} / Overleaf {rp}; text similarity {ratio:.3f}; PDF {keep}"))
                files = zipfile.ZipFile(io.BytesIO(overleaf.download_zip(page, project_id))).namelist()
                content = zipfile.ZipFile(io.BytesIO(overleaf.download_zip(page, project_id))).read("main.tex").decode()
                same = content.replace("\r\n", "\n").strip() == source.replace("\r\n", "\n").strip()
                results.append(("read back as zip", same, f"files {files}; main.tex {'identical' if same else 'DIFFERS'}"))
            except Exception as e:
                results.append(("round-trip", False, f"{type(e).__name__}: {e}"))
            finally:
                if project_id:
                    deleted = overleaf.delete_project(page, project_id)
                    log(f"overleaf: test project {'deleted' if deleted else 'NOT deleted (remove it by hand)'}")
    return results
