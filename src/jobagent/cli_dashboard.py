"""`jobagent dashboard`: serve the local dashboard (step 17)."""

import typer


def register(app: typer.Typer, console) -> None:
    @app.command("dashboard")
    def dashboard(host: str = typer.Option("127.0.0.1", help="Address to listen on. Keep it local: the page can answer questions and start runs."),
                  port: int = typer.Option(8765, help="Port to listen on."),
                  allow_host: list[str] = typer.Option([], "--allow-host", help="Extra Host names the page may be reached by (e.g. a tailnet name)."),
                  novnc_url: str = typer.Option("http://localhost:6080/vnc.html", help="Link shown for the browser view; '' hides it."),
                  read_only: bool = typer.Option(False, "--read-only", help="Only show things: no answering questions, no starting runs."),
                  print_url: bool = typer.Option(False, "--print-url", help="Print the access address of the dashboard and exit.")) -> None:
        """Watch the agent in a browser: questions waiting for you, applications to review, the queue, runs and their logs, outcomes. It is
        read-only except answering a (non-secret) question and starting `jobagent run` up to the review screen; approving or sending an
        application is never done from here."""
        from jobagent.config import get_settings
        from jobagent.dashboard.server import load_token, serve
        if print_url:
            console.print(f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/?t={load_token(get_settings())}", highlight=False)
            return
        serve(get_settings(), host, port, tuple(allow_host), novnc_url, echo=lambda m: console.print(m, highlight=False), read_only=read_only)
