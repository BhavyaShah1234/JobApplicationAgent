"""`jobagent` CLI. Workflow commands (run, bootstrap, queue, …) are added step by step."""

import json

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(no_args_is_help=True, help="Job application agent + harness.")
step_app = typer.Typer(no_args_is_help=True, help="Run a single workflow step on its own.")
vault_app = typer.Typer(no_args_is_help=True, help="Inspect the encrypted credential vault.")
app.add_typer(step_app, name="step")
app.add_typer(vault_app, name="vault")

console = Console()


@step_app.command("harness")
def step_harness(
    only: list[str] = typer.Option(None, "--only", help="Run only these checks (repeatable)."),
) -> None:
    """Step 0: verify volumes, binaries, LaTeX, Chrome, vault and Voyager/Ollama models."""
    from jobagent.steps.harness import run_all

    results = run_all(only=set(only) if only else None)
    table = Table(title="Harness check")
    for col in ("check", "result", "time", "detail"):
        table.add_column(col)
    for r in results:
        table.add_row(r.name, "[green]PASS[/]" if r.ok else "[red]FAIL[/]", f"{r.seconds:.1f}s", r.detail)
    console.print(table)
    failed = [r.name for r in results if not r.ok]
    if failed:
        console.print(f"[red]{len(failed)} failed:[/] {', '.join(failed)}")
        raise typer.Exit(1)
    console.print("[green]All harness checks passed.[/]")


@step_app.command("gmail")
def step_gmail(
    existing: str = typer.Option(None, "--existing", help="Use this already-created Gmail address instead of signing up."),
) -> None:
    """Step 1: set up the agent's Gmail account, app password and inbox access (idempotent)."""
    from jobagent.steps.gmail import setup

    setup(existing_email=existing, log=lambda m: console.print(m, highlight=False))
    console.print("[green]Gmail account ready.[/]")


@step_app.command("gmail-check")
def step_gmail_check() -> None:
    """Step 1 e2e: vault entry, SMTP→IMAP round-trip with trashing, browser session still signed in."""
    from jobagent.config import get_settings
    from jobagent.steps.gmail import check_browser_session, check_imap_roundtrip, check_ownership_guard
    from jobagent.tools.vault import open_vault

    vault = open_vault()
    entry = vault.get("gmail") or {}
    if entry.get("status") != "ready":
        console.print(f"[red]gmail not set up (status={entry.get('status')!r}); run `jobagent step gmail`[/]")
        raise typer.Exit(1)
    failed = False
    log = lambda m: console.print(m, highlight=False)  # noqa: E731
    for name, fn in (("imap/smtp", lambda: check_imap_roundtrip(vault)),
                     ("shared-inbox guard", lambda: check_ownership_guard(vault)),
                     ("browser session", lambda: check_browser_session(get_settings(), vault, log=log))):
        try:
            console.print(f"[green]PASS[/] {name}: {fn()}", highlight=False)
        except Exception as e:
            failed = True
            console.print(f"[red]FAIL[/] {name}: {type(e).__name__}: {e}", highlight=False)
    console.print(f"account: {entry['email']}")
    if failed:
        raise typer.Exit(1)


@step_app.command("browser-check")
def step_browser_check() -> None:
    """Step 2 e2e: profile/cookie persistence, challenge detection and hand-off, Google session state."""
    from jobagent.steps.browser import check

    failed = False
    for name, ok, detail in check(log=lambda m: console.print(m, highlight=False)):
        failed |= not ok
        console.print(f"{'[green]PASS[/]' if ok else '[red]FAIL[/]'} {name}: {detail}", highlight=False)
    if failed:
        raise typer.Exit(1)


@step_app.command("overleaf")
def step_overleaf() -> None:
    """Step 4 (optional): log in to Overleaf with the agent's Google session and detect the plan's features."""
    from jobagent.steps.overleaf import setup

    try:
        setup(log=lambda m: console.print(m, highlight=False))
    except Exception as e:  # optional step: report, don't break the rest of the agent
        console.print(f"[yellow]Overleaf unavailable: {type(e).__name__}: {e}[/]")
        raise typer.Exit(1)
    console.print("[green]Overleaf ready.[/]")


@step_app.command("overleaf-check")
def step_overleaf_check() -> None:
    """Step 4 e2e: create a project from the resume template, compile on Overleaf, compare, read back, delete."""
    from jobagent.steps.overleaf import check

    failed = False
    for name, ok, detail in check(log=lambda m: console.print(m, highlight=False)):
        failed |= not ok
        console.print(f"{'[green]PASS[/]' if ok else '[red]FAIL[/]'} {name}: {detail}", highlight=False)
    if failed:
        raise typer.Exit(1)


def _print_bootstrap(result: dict) -> None:
    for line in result["messages"][-1].content.splitlines()[1:]:
        console.print(("[yellow]" if line.startswith("warning") else "") + line, highlight=False)
    console.print(f"changed: {result['changed'] or 'nothing'}", highlight=False)


@app.command("route")
def route_cmd(trigger: str = typer.Argument(..., help="Job URL(s), company names, or a .csv/.xlsx path.")) -> None:
    """Dry run: show how an input is split into work items (nothing is applied to)."""
    from jobagent.graph.router import route

    routed = route(trigger)
    table = Table(title=f"Routed as: {routed.kind} ({len(routed.items)} item(s))")
    for col in ("#", "kind", "value", "referral", "source"):
        table.add_column(col)
    for n, item in enumerate(routed.items, 1):
        ref = f"{item.referral.name} <{item.referral.email}>" if item.referral else ""
        table.add_row(str(n), item.kind, item.value, ref, item.source)
    console.print(table)
    for w in routed.warnings:
        console.print(f"[yellow]warning:[/] {w}", highlight=False)


def _scrape_table(items, title: str) -> tuple[Table, bool]:
    table = Table(title=title)
    for col in ("portal", "title / company", "locs", "pay", "resp", "basic", "pref", "skills", "complete", "notes", "time"):
        table.add_column(col)
    failed = False
    for it in items:
        if it.error:
            failed = True
            table.add_row("?", f"[red]{it.error[:70]}[/]", *[""] * 8, f"{it.seconds:.0f}s")
            continue
        jd = it.result.jd if it.result else it.cached
        missing = jd.missing_core()
        failed |= bool(missing)
        pay = "yes" if jd.pay_range and (jd.pay_range.min or jd.pay_range.raw) else "—"
        notes = "cached" if it.cached else ("reused" if it.result.used_memory else "learned")
        table.add_row(jd.portal, f"{jd.title[:38]} / {jd.company[:18]}", str(len(jd.locations)), pay,
                      str(len(jd.responsibilities)), str(len(jd.basic_qualifications)),
                      str(len(jd.preferred_qualifications)), str(len(jd.technical_skills)),
                      "[green]yes[/]" if not missing else f"[red]{', '.join(missing)}[/]", notes, f"{it.seconds:.0f}s")
    return table, failed


def _read_urls(urls: list[str], file: str | None) -> list[str]:
    from pathlib import Path

    out = list(urls)
    if file:
        out += [l.split("#")[0].strip() for l in Path(file).read_text().splitlines() if l.split("#")[0].strip()]
    return out


@app.command("scrape")
def scrape_cmd(
    urls: list[str] = typer.Argument(None, help="One or more posting URLs."),
    file: str = typer.Option(None, "--file", "-f", help="A file with one URL per line."),
    workers: int = typer.Option(5, help="Postings read in parallel."),
    refresh: bool = typer.Option(False, help="Re-read postings already in the jobs table."),
) -> None:
    """Read job postings (in parallel) into outputs/<company>/<job-id>/jd.json and jd.md."""
    import time

    from jobagent.tools.hitl import open_hitl
    from jobagent.tools.scrape import scrape_many

    targets = _read_urls(urls or [], file)
    if not targets:
        console.print("[red]give at least one URL or --file[/]")
        raise typer.Exit(1)
    log = lambda m: console.print(m, highlight=False)  # noqa: E731
    t0 = time.monotonic()
    items = scrape_many(targets, open_hitl(log), workers=workers, refresh=refresh, log=log)
    table, failed = _scrape_table(items, f"{len(items)} posting(s) in {time.monotonic() - t0:.0f}s ({workers} workers)")
    console.print(table)
    if failed:
        raise typer.Exit(1)


@step_app.command("scrape-check")
def step_scrape_check(
    urls_file: str = typer.Option("/app/tests/e2e/scrape_urls.txt", help="One posting URL per line."),
    workers: int = typer.Option(5),
) -> None:
    """Step 7 e2e: read every posting in parallel twice; JDs complete; the 2nd pass reuses the portal notes."""
    import time

    from jobagent.tools.hitl import open_hitl
    from jobagent.tools.scrape import scrape_many

    urls = _read_urls([], urls_file)
    quiet = lambda m: None  # noqa: E731
    failed = False
    for n in (1, 2):
        t0 = time.monotonic()
        items = scrape_many(urls, open_hitl(quiet), workers=workers, refresh=True, log=quiet)
        table, bad = _scrape_table(items, f"Pass {n}: {len(urls)} postings in {time.monotonic() - t0:.0f}s ({workers} workers)")
        if n == 2:
            bad |= any(it.result and not it.result.used_memory for it in items)
        failed |= bad
        console.print(table)
    if failed:
        raise typer.Exit(1)


@app.command("bootstrap")
def bootstrap() -> None:
    """Global default action: check/repair the harness and accounts (resumes after a crash)."""
    from jobagent.graph.bootstrap import run

    _print_bootstrap(run(log=lambda m: console.print(m, highlight=False)))


@step_app.command("bootstrap-check")
def step_bootstrap_check() -> None:
    """Step 5 e2e: idempotent (2nd run changes nothing) and resumes after a crash without redoing done nodes."""
    import os

    from jobagent.graph.bootstrap import CRASH_ENV, BootstrapCrash, run

    quiet = lambda m: None  # noqa: E731
    checks = []
    first = run(log=quiet)
    checks.append(("first run", True, f"ran {first['ran_now']}; changed {first['changed'] or 'nothing'}; "
                                      f"warnings {len(first['warnings'])}"))
    second = run(log=quiet)
    checks.append(("idempotent", not second["changed"], f"second run changed {second['changed'] or 'nothing'}"))
    os.environ[CRASH_ENV] = "browser"
    try:
        run(log=quiet)
        checks.append(("crash/resume", False, "the simulated crash did not happen"))
    except BootstrapCrash:
        os.environ.pop(CRASH_ENV)
        resumed = run(log=quiet)
        ok = resumed["resumed"] and resumed["ran_now"] == ["browser", "linkedin", "overleaf"] and not resumed["changed"]
        checks.append(("crash/resume", ok, f"resumed={resumed['resumed']}; re-ran only {resumed['ran_now']}"))
    finally:
        os.environ.pop(CRASH_ENV, None)
    failed = False
    for name, ok, detail in checks:
        failed |= not ok
        console.print(f"{'[green]PASS[/]' if ok else '[red]FAIL[/]'} {name}: {detail}", highlight=False)
    _print_bootstrap(second)
    if failed:
        raise typer.Exit(1)


browser_app = typer.Typer(no_args_is_help=True, help="The agent's Chrome profile.")
app.add_typer(browser_app, name="browser")


@browser_app.command("google-login")
def browser_google_login() -> None:
    """Sign in to the agent's Google account by hand, once, in plain (non-automated) Chrome via noVNC."""
    from jobagent.steps.browser import google_login
    from jobagent.tools.hitl import open_hitl
    from jobagent.tools.vault import open_vault

    log = lambda m: console.print(m, highlight=False)  # noqa: E731
    state = google_login(open_vault(), open_hitl(log), log)
    if state != "ok":
        console.print("[red]the agent's browser is still signed out (Google may have rejected the session)[/]")
        raise typer.Exit(1)
    console.print("[green]the agent's browser opens Gmail signed in[/]")


@browser_app.command("warm-up")
def browser_warm_up(minutes: float = typer.Option(5, help="How long to browse.")) -> None:
    """Ordinary browsing so the profile collects normal cookies and history (optional)."""
    from jobagent.steps.browser import warm_up
    from jobagent.tools.hitl import open_hitl

    log = lambda m: console.print(m, highlight=False)  # noqa: E731
    warm_up(minutes, open_hitl(log), log)


@browser_app.command("status")
def browser_status() -> None:
    """Profile size, cookie count and whether Gmail opens signed in."""
    from jobagent.steps.browser import status

    for k, v in status().items():
        console.print(f"{k}: {v}", highlight=False)


hitl_app = typer.Typer(no_args_is_help=True, help="Answer the agent's human-in-the-loop requests.")
app.add_typer(hitl_app, name="hitl")


@hitl_app.command("list")
def hitl_list() -> None:
    """Show pending requests."""
    from jobagent.tools.hitl import open_hitl

    reqs = open_hitl().list_pending()
    if not reqs:
        console.print("no pending requests")
    for r in reqs:
        console.print(f"[bold]{r.id}[/] {r.created} {'(secret) ' if r.secret else ''}{r.prompt}", highlight=False)


@hitl_app.command("answer")
def hitl_answer(
    req_id: str = typer.Argument(None, help="Request id (default: oldest pending)."),
    value: str = typer.Argument(None, help="Answer text (prompted if omitted; hidden for secrets)."),
) -> None:
    """Answer a pending request. Secrets are read hidden and stored straight into the vault."""
    from jobagent.tools.hitl import open_hitl

    hitl = open_hitl()
    reqs = hitl.list_pending()
    req = next((r for r in reqs if r.id == req_id), reqs[0] if reqs and not req_id else None)
    if req is None:
        console.print("[red]no matching pending request[/]")
        raise typer.Exit(1)
    if value is None:
        console.print(req.prompt, highlight=False)
        value = typer.prompt("answer", hide_input=req.secret, default=req.default or "")
    hitl.answer(req.id, value)
    console.print(f"answered {req.id}" + (" (stored in vault)" if req.secret else ""))


@hitl_app.command("done")
def hitl_done(req_id: str = typer.Argument(None)) -> None:
    """Tell the agent you finished what it asked (e.g. a step in noVNC)."""
    from jobagent.tools.hitl import open_hitl

    req = open_hitl().answer(req_id, "done")
    console.print(f"marked {req.id} done")


inbox_app = typer.Typer(no_args_is_help=True, help="The agent's Gmail inbox.")
app.add_typer(inbox_app, name="inbox")


@inbox_app.command("sweep")
def inbox_sweep(max_age_h: float = 24) -> None:
    """Trash unused verification mail older than --max-age-h hours (never application mail)."""
    from jobagent.tools.inbox import open_inbox

    with open_inbox() as inbox:
        trashed = inbox.sweep_stale_verification(max_age_h)
    console.print(f"trashed {len(trashed)} stale verification mail(s)")
    for subject in trashed:
        console.print(f"  - {subject}", highlight=False)


@vault_app.command("list")
def vault_list() -> None:
    """List entry names (no secrets)."""
    from jobagent.tools.vault import open_vault

    for name in open_vault().names():
        console.print(name)


@vault_app.command("set")
def vault_set(
    name: str,
    field: str,
    value: str = typer.Argument(None, help="Omit to be prompted (hidden) — keeps secrets out of shell history."),
) -> None:
    """Set one field of a vault entry, e.g. `jobagent vault set gmail password`."""
    from jobagent.tools.vault import open_vault

    if value is None:
        value = typer.prompt(f"{name}.{field}", hide_input=True, confirmation_prompt=True)
    open_vault().set(name, **{field: value})
    console.print(f"stored {name}.{field}")


@vault_app.command("import-file")
def vault_import_file(
    name: str,
    field: str,
    path: str,
    shred: bool = typer.Option(True, help="Overwrite and delete the plaintext file afterwards."),
) -> None:
    """Encrypt a value from a plaintext file into the vault (never printed), then shred the file.

    E.g. `jobagent vault import-file gmail password /app/state/password.txt`.
    """
    import os
    from pathlib import Path

    from jobagent.tools.vault import open_vault

    src = Path(path)
    value = src.read_text().strip()
    if not value:
        console.print("[red]file is empty[/]")
        raise typer.Exit(1)
    vault = open_vault()
    if src.resolve() == vault.path.resolve():
        # The plaintext was written over vault.enc itself: start a fresh vault (restored from backup if any).
        os.replace(src, src.with_name(src.name + ".plaintext"))
        src = src.with_name(src.name + ".plaintext")
        if vault.backup_path.exists():
            vault.restore_backup()
        vault = open_vault()
    vault.set(name, **{field: value})
    if shred:
        size = src.stat().st_size
        with open(src, "r+b") as f:
            f.write(os.urandom(size))
            f.flush()
            os.fsync(f.fileno())
        src.unlink()
    console.print(f"stored {name}.{field} ({len(value)} characters){'; plaintext file shredded' if shred else ''}")


@vault_app.command("restore")
def vault_restore() -> None:
    """Replace an unreadable vault.enc with the backup from the last good save."""
    from jobagent.tools.vault import open_vault

    names = open_vault().restore_backup()
    console.print(f"restored vault from backup; entries: {', '.join(names) or '(none)'}")


@vault_app.command("show")
def vault_show(name: str) -> None:
    """Print one entry, including secrets (e.g. `jobagent vault show gmail`)."""
    from jobagent.tools.vault import open_vault

    entry = open_vault().get(name)
    if entry is None:
        console.print(f"[red]no vault entry named {name!r}[/]")
        raise typer.Exit(1)
    console.print_json(json.dumps(entry))


@app.command("report-capability")
def report_capability(name: str, why: str, install_hint: str = "") -> None:
    """Record a capability the harness is missing (read by Claude Code when updating the Dockerfile)."""
    from jobagent.tools.capabilities import report_missing_capability

    added = report_missing_capability(name, why, install_hint)
    console.print("recorded" if added else "already reported")


if __name__ == "__main__":
    app()
