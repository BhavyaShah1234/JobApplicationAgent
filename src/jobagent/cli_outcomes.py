"""CLI for step 15: `jobagent inbox sync` (read what came back) and `jobagent outcomes` (list, approve, reject, label)."""

import time

import typer
from rich.table import Table


def register(app: typer.Typer, console) -> None:
    out_app = typer.Typer(help="Outcome mail linked to your applications: list, approve, reject, label.",
                          invoke_without_command=True)
    app.add_typer(out_app, name="outcomes")

    @app.command("sync-inbox")
    def sync_inbox(
        since_days: int = typer.Option(0, help="Look back this many days instead of from the last sync."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be stored; write nothing."),
        no_model: bool = typer.Option(False, "--no-model", help="Phrase rules only; undecided mail is dropped."),
        watch: bool = typer.Option(False, "--watch", help="Keep running: sync every --every hours."),
        every: float = typer.Option(6.0, help="Hours between syncs with --watch."),
    ) -> None:
        """Step 15: read the agent's mailbox for replies to submitted applications (from the company's or its ATS's
        domain, naming the company or role, after the submission), classify them and store the outcome events. Read-only:
        nothing is trashed, labelled or marked read, and no mail body is kept. Run it by hand, with --watch, or from cron:
        `0 */6 * * * docker exec jobagent jobagent sync-inbox`."""
        from jobagent import db
        from jobagent.tools import outcomes
        from jobagent.tools.inbox import open_inbox

        log = lambda m: console.print(m, highlight=False)  # noqa: E731
        while True:
            with open_inbox() as inbox:
                res = outcomes.sync(inbox, db.connect(), since_days=since_days or None,
                                    model=None if no_model else "model", dry_run=dry_run, log=log)
            verb = "would store" if dry_run else "stored"
            console.print(f"sync-inbox: {res.applications} submitted application(s), {res.seen} message(s) read, "
                          f"{len(res.stored)} outcome(s) {verb}"
                          + (f"; dropped: {', '.join(f'{n} {k}' for k, n in res.dropped.items())}" if res.dropped else ""),
                          highlight=False)
            for e in res.stored:
                flag = " [yellow](needs review)[/]" if e["needs_review"] else ""
                console.print(f"  {e['received_at'][:10]}  {e['stage']:<16} {e['subject'][:60]}{flag}")
            if not watch:
                break
            time.sleep(every * 3600)

    @app.command("stats")
    def stats(
        csv_path: str = typer.Option(None, "--csv", help="Also write the groups to this CSV file."),
        as_of: str = typer.Option(None, help="Evaluate as of this date (YYYY-MM-DD); default today."),
    ) -> None:
        """Step 15b: how your applications are doing. Funnel, days to first response (Kaplan-Meier, so applications still
        waiting aren't counted as failures), response and positive rates by portal, role family, checker-score band and
        resume version. Columns: pos/neg/ghost/wait = applications by label; replied = any reply that isn't the automated
        confirmation; positive = an assessment, screen, interview or offer; both as a share of resolved applications with
        their 95% interval; median d and by 30d are Kaplan-Meier. Groups under 15 resolved applications say 'too few'."""
        import csv
        from datetime import UTC, datetime

        from jobagent import db
        from jobagent.tools import outcome_stats as S

        when = datetime.strptime(as_of, "%Y-%m-%d").replace(tzinfo=UTC, hour=23, minute=59) if as_of else datetime.now(UTC)
        conn = db.connect()
        apps = S.load(conn)
        if not apps:
            console.print("stats: no submitted applications yet; nothing to report")
            return
        sts = S.statuses(apps, when)
        funnel = {k: sum(1 for s in sts.values() if s.label == k) for k in ("pending", "positive", "negative", "ghosted", "withdrawn")}
        cutoff = S.ghost_cutoff(apps, when)
        console.print(f"stats: {len(apps)} submitted application(s) as of {when:%Y-%m-%d}: "
                      + ", ".join(f"{n} {k}" for k, n in funnel.items() if n)
                      + f"; silence counts as ghosted after {cutoff:.0f} days", highlight=False)
        rows = []
        for title, key in (("all", lambda a: "all"), ("portal", lambda a: a.portal), ("role family", lambda a: a.role_id),
                           ("checker score", lambda a: S.band(a.ats_average)), ("resume version", lambda a: a.resume_sha)):
            table = Table(title=title)
            for col in ("group", "n", "pos/neg/ghost/wait", "replied", "positive", "median d", "by 30d", "note"):
                table.add_column(col)
            for g in S.group_by(apps, sts, key):
                pct = lambda p, ci: "-" if p is None else f"{p:.0%} ({ci[0]:.0%}-{ci[1]:.0%})"  # noqa: E731
                table.add_row(g.key, str(g.n), f"{g.positive}/{g.negative}/{g.ghosted}/{g.pending}",
                              pct(g.response_rate, g.response_ci), pct(g.positive_rate, g.positive_ci),
                              "-" if g.median_days is None else f"{g.median_days:.0f}",
                              "-" if g.by_30d is None else f"{g.by_30d:.0%}", "[yellow]too few[/]" if g.too_few else "")
                rows.append({"dimension": title, **g.__dict__})
            console.print(table)
        if csv_path:
            with open(csv_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            console.print(f"stats: written to {csv_path}")

    @app.command("reflect")
    def reflect_cmd(
        dry_run: bool = typer.Option(False, "--dry-run", help="Show the findings; write no lessons."),
        as_of: str = typer.Option(None, help="Evaluate as of this date (YYYY-MM-DD); default today."),
    ) -> None:
        """Step 15c: what the history says (advisory). Which checker sites' scores went with positive replies and which
        skills went with replies in a role family, with their evidence. Needs at least 15 applications that got a positive
        reply and 15 that did not (per group) before it says anything. Nothing changes in tailoring or site choice unless
        you switch it on: LEARNING_USE_SITE_MIX, LEARNING_USE_TAILORING_HINTS, LEARNING_USE_LESSONS (all off)."""
        from datetime import UTC, datetime

        from jobagent import db
        from jobagent.config import get_settings
        from jobagent.tools import learning as L
        from jobagent.tools import outcome_stats as S

        when = datetime.strptime(as_of, "%Y-%m-%d").replace(tzinfo=UTC, hour=23, minute=59) if as_of else datetime.now(UTC)
        conn = db.connect()
        apps = S.load(conn)
        sts = S.statuses(apps, when)
        rows = L.resolved_outcomes(apps, sts)
        pos = sum(y for _, y in rows)
        console.print(f"reflect: {len(apps)} submitted, {len(rows)} resolved ({pos} positive, {len(rows) - pos} not) as of "
                      f"{when:%Y-%m-%d}", highlight=False)
        s = get_settings()
        console.print("switches (advisory by default): site mix {}, tailoring hints {}, lessons {}".format(
            *("[green]on[/]" if v else "off" for v in (s.learning_use_site_mix, s.learning_use_tailoring_hints, s.learning_use_lessons))))
        effects = L.site_effects(apps, sts)
        if not effects or all(e.verdict == "not enough data" for e in effects):
            console.print(f"reflect: not enough data yet: each group needs {L.MIN_ARM} positive and {L.MIN_ARM} other resolved "
                          "applications. At a few applications a week that takes months; `jobagent stats` shows the counts.")
        table = Table(title="checker sites: does a higher score go with more replies?")
        for col in ("site", "resolved", "positive", "AUC", "99% interval", "reading"):
            table.add_column(col)
        for e in effects:
            table.add_row(e.site, str(e.n), str(e.positives), "-" if e.auc is None else f"{e.auc:.2f}",
                          "-" if e.ci is None else f"{e.ci[0]:.2f}-{e.ci[1]:.2f}", e.verdict)
        if effects:
            console.print(table)
        found = L.findings(apps, sts)
        for f in found:
            console.print(("  * " if f["actionable"] else "  - ") + f["text"], highlight=False)
        if not dry_run:
            res = L.reflect(conn, apps, sts, when)
            console.print(f"reflect: {res['actionable']} recommendation(s); lessons {res['new']} new, {res['kept']} kept, "
                          f"{res['retired']} retired", highlight=False)

    @out_app.callback()
    def outcomes_list(ctx: typer.Context, review: bool = typer.Option(False, "--review", help="Only events waiting for you.")) -> None:
        """Every application's latest stage; --review lists events waiting for your approval."""
        if ctx.invoked_subcommand:
            return
        from jobagent import db
        from jobagent.tools import outcomes

        conn = db.connect()
        if review:
            table = Table(title="waiting for your approval (`outcomes approve <id>` / `outcomes reject <id>`)")
            for col in ("id", "application", "stage", "confidence", "date", "subject", "evidence"):
                table.add_column(col)
            for e in outcomes.pending_review(conn):
                table.add_row(str(e["id"]), f"{e['company']} - {e['job_title']}"[:34], e["stage"], f"{e['confidence']}",
                              e["received_at"][:10], (e["subject"] or "")[:34], (e["excerpt"] or "")[:50])
            console.print(table)
            return
        table = Table(title="application status")
        for col in ("app", "company", "title", "state", "submitted", "events", "current", "furthest", "last event"):
            table.add_column(col)
        for r in conn.execute("SELECT * FROM application_status ORDER BY application_id"):
            table.add_row(str(r["application_id"]), r["company"] or "", (r["title"] or "")[:34], r["state"] or "",
                          (r["submitted_at"] or "")[:10], str(r["events"]), r["current_stage"] or "-",
                          r["furthest_stage"] or "-", (r["last_event_at"] or "")[:10])
        console.print(table)

    @out_app.command("approve")
    def approve(ids: list[int] = typer.Argument(...)) -> None:
        """Count events that were waiting for you."""
        from jobagent import db
        from jobagent.tools import outcomes

        conn = db.connect()
        for i in ids:
            console.print(f"outcomes: event {i} " + ("approved" if outcomes.decide(conn, i, True) else "is not waiting"))

    @out_app.command("reject")
    def reject(ids: list[int] = typer.Argument(...)) -> None:
        """Discard events that were waiting for you (the same message is never added again)."""
        from jobagent import db
        from jobagent.tools import outcomes

        conn = db.connect()
        for i in ids:
            console.print(f"outcomes: event {i} " + ("rejected" if outcomes.decide(conn, i, False) else "is not waiting"))

    @out_app.command("label")
    def label(message_id: str = typer.Argument(..., help="The message's Message-ID, as shown by `outcomes --review`."),
              stage: str = typer.Argument(..., help="confirmation, assessment, recruiter_screen, interview, offer, "
                                                    "rejection or withdrawn.")) -> None:
        """Set a stored message's stage by hand."""
        from jobagent import db
        from jobagent.tools import outcomes

        try:
            ok = outcomes.label(db.connect(), message_id, stage)
        except ValueError as e:
            raise typer.BadParameter(str(e)) from e
        console.print("outcomes: labelled" if ok else "outcomes: no stored message with that id")
