"""`jobagent config show`: the effective value of every tunable setting and where it comes from (step 21)."""

import typer


def register(app: typer.Typer, console) -> None:
    config_app = typer.Typer(no_args_is_help=True, help="Inspect the settings in config/*.yaml.")
    app.add_typer(config_app, name="config")

    @config_app.command("show")
    def show(concern: str = typer.Argument(None, help="Only this file's settings (models, forms, documents, ats, browser, queue, learning, logging, skills).")) -> None:
        """List each setting with its effective value and its source (env, .env, yaml file or default)."""
        from rich.table import Table
        from jobagent.config import describe
        table = Table("concern", "setting", "value", "source")
        for r in describe():
            if concern is None or r["concern"] == concern:
                table.add_row(r["concern"], r["key"], str(r["value"]), r["source"])
        console.print(table)
