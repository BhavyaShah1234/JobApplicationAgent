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
