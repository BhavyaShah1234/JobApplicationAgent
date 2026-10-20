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
def step_gmail() -> None:
    """Step 1: create/verify the agent's Gmail account, app password and inbox access (idempotent)."""
    from jobagent.steps.gmail import setup

    setup(log=lambda m: console.print(m, highlight=False))
    console.print("[green]Gmail account ready.[/]")


@step_app.command("gmail-check")
def step_gmail_check() -> None:
    """Step 1 e2e: vault entry, SMTP→IMAP round-trip with trashing, browser session still signed in."""
    from jobagent.config import get_settings
    from jobagent.steps.gmail import check_browser_session, check_imap_roundtrip
    from jobagent.tools.vault import open_vault

    vault = open_vault()
    entry = vault.get("gmail") or {}
    if entry.get("status") != "ready":
        console.print(f"[red]gmail not set up (status={entry.get('status')!r}); run `jobagent step gmail`[/]")
        raise typer.Exit(1)
    failed = False
    for name, fn in (("imap/smtp", lambda: check_imap_roundtrip(vault)),
                     ("browser session", lambda: check_browser_session(get_settings()))):
        try:
            console.print(f"[green]PASS[/] {name}: {fn()}", highlight=False)
        except Exception as e:
            failed = True
            console.print(f"[red]FAIL[/] {name}: {type(e).__name__}: {e}", highlight=False)
    console.print(f"account: {entry['email']}")
    if failed:
        raise typer.Exit(1)


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
