"""CLI for step 14: `jobagent discover` (company names / CSV -> queued roles) and `jobagent queue` (look, decide, run)."""

import subprocess
import sys

import typer
from rich.table import Table


def register(app: typer.Typer, console) -> None:
    queue_app = typer.Typer(help="The discovery queue: list, decide and run discovered roles.", invoke_without_command=True)
    app.add_typer(queue_app, name="queue")

    @app.command("discover")
    def discover_cmd(
        inputs: list[str] = typer.Argument(..., help="Company names (comma lists ok) or a .csv/.xlsx file of companies."),
        refresh_careers: bool = typer.Option(False, help="Ignore careers pages cached in the last 30 days."),
        no_judge: bool = typer.Option(False, "--no-judge", help="Skip the model judge: a pattern hit is queued as is."),
        all_countries: bool = typer.Option(False, "--all-countries", help="Don't apply the US-only filter."),
        limit: int = typer.Option(0, help="Read only the first N listings per company (a quick trial)."),
    ) -> None:
        """Step 14: find each company's careers page, read its open listings, keep the US roles that match
        data/target_roles.yaml, and queue them (needs_review for the unsure ones). A pasted job URL isn't discovery:
        use `jobagent scrape` for those."""
        from jobagent.graph.router import route
        from jobagent.tools import discovery

        routed = route(inputs[0] if len(inputs) == 1 else ", ".join(inputs))
        companies = [i.company or i.value for i in routed.items if i.kind == "company"]
        skipped = [i.value for i in routed.items if i.kind == "url"]
        for u in skipped:
            console.print(f"[yellow]discover: {u} is a job URL, not a company: read it with `jobagent scrape`[/]", highlight=False)
        if not companies:
            console.print("[red]discover: no company names to look up[/]")
            raise typer.Exit(1)
        log = lambda m: console.print(m, highlight=False)  # noqa: E731
        results = discovery.discover_many(companies, log=log, refresh=refresh_careers, only_us=not all_countries,
                                          judge=None if no_judge else "model", limit=limit or None)
        table = Table(title=f"{len(results)} company(ies)")
        for col in ("company", "careers page", "found by", "listings", "queued", "review", "skipped", "note"):
            table.add_column(col)
        for r in results:
            table.add_row(r.company, r.careers_url[:48], r.how[:14], str(r.listings), str(r.counts.get("queued", 0)),
                          str(r.counts.get("needs_review", 0)), str(r.counts.get("skipped", 0)),
                          f"[yellow]{r.note[:60]}[/]" if r.note else "")
        console.print(table)
        console.print("Look at them with `jobagent queue`; unsure ones with `jobagent queue --status needs_review`.")

    @queue_app.callback()
    def queue_list(
        ctx: typer.Context,
        status: str = typer.Option("queued", "--status", "-s", help="queued, needs_review, skipped, scraped, ... or all"),
        company: str = typer.Option(None, "--company", "-c"),
        limit: int = typer.Option(50),
        review: bool = typer.Option(False, "--review", help="Same as --status needs_review."),
    ) -> None:
        """List queued roles (default), with counts per status."""
        if ctx.invoked_subcommand:
            return
        from jobagent import db
        from jobagent.tools import jobqueue as Q

        conn = db.connect()
        status = "needs_review" if review else status
        counts = Q.summary(conn, company)
        console.print("queue: " + (", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "empty"), highlight=False)
        rows = (conn.execute("SELECT * FROM jobs WHERE source IS NOT NULL ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
                if status == "all" else Q.pending(conn, status, company, limit))
        table = Table(title=f"{status} (best match first)")
        for col in ("id", "company", "title", "location", "role", "score", "why"):
            table.add_column(col)
        for r in rows:
            table.add_row(str(r["id"]), r["company"] or "", (r["title"] or "")[:40], (r["location"] or "")[:28],
                          r["role_id"] or "", f"{r['match_score'] or 0:.2f}", (r["notes"] or "")[:60])
        console.print(table)

    def _decide(ids: list[int], status: str) -> None:
        from jobagent import db
        from jobagent.tools import jobqueue as Q

        conn = db.connect()
        for i in ids:
            row = conn.execute("SELECT url, status FROM jobs WHERE id = ?", (i,)).fetchone()
            if not row:
                console.print(f"[red]queue: no job {i}[/]")
            elif row["status"] not in Q.OPEN:
                console.print(f"[yellow]queue: job {i} is already {row['status']}; left alone[/]")
            else:
                Q.mark(conn, row["url"], status, f"decided by the candidate ({status})")
                console.print(f"queue: job {i} -> {status}")

    @queue_app.command("approve")
    def queue_approve(ids: list[int] = typer.Argument(...)) -> None:
        """Move needs_review (or skipped) roles into the queue."""
        _decide(ids, "queued")

    @queue_app.command("reject")
    def queue_reject(ids: list[int] = typer.Argument(...)) -> None:
        """Skip roles for good."""
        _decide(ids, "skipped")

    @queue_app.command("run")
    def queue_run(
        through: str = typer.Option("scrape", help="How far each job goes: scrape, tailor (+research, ~45 min each) or "
                                                    "apply (+ fill the form to its review screen; never submits)."),
        limit: int = typer.Option(5, help="At most N jobs in this run."),
        company: str = typer.Option(None, "--company", "-c"),
    ) -> None:
        """Take queued roles one at a time through the existing steps. A job closed or blocked is recorded and the
        next one starts; a run killed midway resumes (its `running` job goes back to the queue)."""
        if through not in {"scrape", "tailor", "apply"}:
            raise typer.BadParameter("through must be scrape, tailor or apply")
        from jobagent import db
        from jobagent.config import get_settings
        from jobagent.tools import jobqueue as Q
        from jobagent.tools.scrape import _slug

        conn = db.connect()
        back = Q.requeue_stale(conn)
        if back:
            console.print(f"queue: {back} interrupted job(s) put back in the queue")
        py = [sys.executable, "-m", "jobagent.main"]

        def step(cmd: list[str]) -> int:
            console.print("[cyan]$ " + " ".join(["jobagent", *cmd]) + "[/]", highlight=False)
            return subprocess.run([*py, *cmd]).returncode

        done = 0
        for row in Q.pending(conn, "queued", company, limit):
            url = row["url"]
            console.print(f"[bold]queue: {row['company']} - {row['title']}[/]", highlight=False)
            Q.mark(conn, url, "running")
            code = step(["scrape", url])
            now = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
            if code == 3:
                console.print(f"[yellow]queue: {now['status']}: {now['notes']}[/]", highlight=False)
                continue
            if code != 0:
                Q.mark(conn, url, "failed", "scrape failed")
                continue
            Q.mark(conn, url, "scraped")
            job_dir = get_settings().outputs_dir / _slug(now["company"] or row["company"]) / now["job_id"]
            rel = f"outputs/{job_dir.parent.name}/{job_dir.name}"
            co = Q.slug(now["company"] or row["company"])
            if through != "scrape":
                if step(["research", now["company"] or row["company"]]) != 0 or step(["tailor", rel, co]) != 0:
                    Q.mark(conn, url, "failed", "research or tailor failed")
                    continue
                Q.mark(conn, url, "tailored")
            if through == "apply":
                code = step(["apply", rel, co])
                Q.mark(conn, url, "filled" if code == 0 else "blocked" if code == 3 else "failed",
                       "" if code == 0 else f"apply exited {code}")
            done += 1
        console.print(f"queue: {done} job(s) taken through `{through}`", highlight=False)
