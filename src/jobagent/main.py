"""`jobagent` CLI. Workflow commands (run, bootstrap, queue, …) are added step by step."""

import contextlib
import json

import typer
from jobagent.log import runlog
from rich.console import Console
from rich.table import Table

from jobagent.tools.availability import EXIT_UNAVAILABLE

app = typer.Typer(no_args_is_help=True, help="Job application agent + harness.", pretty_exceptions_enable=False)
step_app = typer.Typer(no_args_is_help=True, help="Run a single workflow step on its own.")
vault_app = typer.Typer(no_args_is_help=True, help="Inspect the encrypted credential vault.")
app.add_typer(step_app, name="step")
app.add_typer(vault_app, name="vault")

console = Console()

from jobagent import cli_discovery  # noqa: E402

cli_discovery.register(app, console)  # step 14: discover, queue

from jobagent import cli_outcomes  # noqa: E402

cli_outcomes.register(app, console)  # step 15: sync-inbox, outcomes

from jobagent import cli_config  # noqa: E402

cli_config.register(app, console)  # step 21: config show

from jobagent import cli_skills  # noqa: E402

cli_skills.register(app, console)  # step 4c: skills build, sync, show, ...


@app.command('temp-mail')
def temp_mail_cmd(
    browser_mode: str = typer.Option('auto', help='docker, desktop, or auto (desktop after a Docker security check).'),
    wait_minutes: float = typer.Option(2, min=0.01, max=60, help='Time to wait for the mailbox or human verification.'),
):
    """Open Temp-Mail and read its address; desktop mode leaves the inbox open."""
    from jobagent.tools import temp_mail, challenge

    try:
        result = temp_mail.open_mailbox(browser_mode, wait_s=wait_minutes * 60, log=console.print)
    except (ValueError, challenge.Blocked, RuntimeError) as exc:
        console.print(f'temp-mail: {exc}', markup=False)
        raise typer.Exit(1) from exc
    console.print(result['email'], markup=False)


@app.command("desktop-probe")
def desktop_probe_cmd(
    stage: str = typer.Argument(..., help="manual, read, or field; each stage needs separate owner authorization."),
    authorized_stage: str = typer.Option(..., help="Acknowledge the stage authorized by the account owner."),
    url: str = typer.Option("https://www.carvana.com/careers/apply?gh_jid=8188615"),
    field_id: str = typer.Option("", help="Exact input id agreed for a single-field test."),
    value: str = typer.Option("", help="Value agreed for that one field; never a password."),
):
    """Gated desktop feasibility probe; never submits or runs automatically as a fallback."""
    from pathlib import Path
    from jobagent.tools import desktop_probe
    if stage not in {"manual", "read", "field"} or authorized_stage != stage:
        raise typer.BadParameter("Explicit authorization for this exact stage is required")
    root = Path.cwd() / 'state' / 'carvana-desktop'
    result = desktop_probe.launch(root, url) if stage == 'manual' else desktop_probe.probe(root, stage, field_id, value)
    console.print(json.dumps(result, indent=2), highlight=False)


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

    setup(existing_email=existing, log=runlog("step-gmail"))
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
    log = runlog("step-gmail-check")
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
    for name, ok, detail in check(log=runlog("step-browser-check")):
        failed |= not ok
        console.print(f"{'[green]PASS[/]' if ok else '[red]FAIL[/]'} {name}: {detail}", highlight=False)
    if failed:
        raise typer.Exit(1)


@step_app.command("overleaf")
def step_overleaf() -> None:
    """Step 4 (optional): log in to Overleaf with the agent's Google session and detect the plan's features."""
    from jobagent.steps.overleaf import setup

    try:
        setup(log=runlog("step-overleaf"))
    except Exception as e:  # optional step: report, don't break the rest of the agent
        console.print(f"[yellow]Overleaf unavailable: {type(e).__name__}: {e}[/]")
        raise typer.Exit(1)
    console.print("[green]Overleaf ready.[/]")


@step_app.command("overleaf-check")
def step_overleaf_check() -> None:
    """Step 4 e2e: create a project from the resume template, compile on Overleaf, compare, read back, delete."""
    from jobagent.steps.overleaf import check

    failed = False
    for name, ok, detail in check(log=runlog("step-overleaf-check")):
        failed |= not ok
        console.print(f"{'[green]PASS[/]' if ok else '[red]FAIL[/]'} {name}: {detail}", highlight=False)
    if failed:
        raise typer.Exit(1)


@step_app.command("github")
def step_github() -> None:
    """Step 4b (optional): log in to GitHub with the agent's Google session."""
    from jobagent.steps.github import setup

    try:
        user = setup(log=runlog("step-github"))
    except Exception as e:  # optional step: report, don't break the rest of the agent
        console.print(f"[yellow]GitHub unavailable: {type(e).__name__}: {e}[/]")
        raise typer.Exit(1)
    console.print(f"[green]GitHub ready ({user}).[/]")


@step_app.command("github-check")
def step_github_check() -> None:
    """Step 4b e2e: session still logged in; the resume's GitHub repos (own + contributed) are discoverable."""
    from jobagent.steps.github import check

    failed = False
    for name, ok, detail in check(log=runlog("step-github-check")):
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
        if it.unavailable:  # closed / expired / removed / already applied: skipped, not a failure
            table.add_row("?", f"[yellow]skipped ({it.unavailable.split(':')[0]})[/]", *[""] * 8, f"{it.seconds:.0f}s")
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
    log = runlog("scrape")
    t0 = time.monotonic()
    items = scrape_many(targets, open_hitl(log), workers=workers, refresh=refresh, log=log)
    table, failed = _scrape_table(items, f"{len(items)} posting(s) in {time.monotonic() - t0:.0f}s ({workers} workers)")
    console.print(table)
    for it in items:
        if it.unavailable:
            console.print(f"[yellow]skipped {it.url}: {it.unavailable}[/]", highlight=False)
    if failed:
        raise typer.Exit(1)
    if all(it.unavailable for it in items):  # nothing left to tailor or apply to: stop a `&&` chain here
        console.print("[yellow]scrape: no posting can be applied to; stopping[/]")
        raise typer.Exit(EXIT_UNAVAILABLE)


def _stop_if_unavailable(job_dir, step: str) -> None:
    """Exit (code 3) before a step when the job was already found closed / already applied to."""
    from pathlib import Path

    from jobagent.tools import availability

    p = Path(job_dir) / "jd.json"
    jd = json.loads(p.read_text()) if p.exists() else {}
    # the posting itself decides: a stale apply link can be closed while the posting's own Apply button works
    if (url := jd.get("source_url") or jd.get("apply_url")) and (gone := availability.known(url)):
        console.print(f"[yellow]{step}: skipped, {jd.get('title', '')} @ {jd.get('company', '')} can't be applied "
                      f"to ({gone.kind}: {gone.reason})[/]", highlight=False)
        raise typer.Exit(EXIT_UNAVAILABLE)


def _lock_job(ctx: typer.Context, job_dir, what: str):
    """F15/F25: take the job's build lock for the length of this command and open its workflow record. Returns (path, workflow)."""
    from pathlib import Path

    from jobagent.tools import bundle
    from jobagent.tools import workflow as W

    d = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    if not d.exists():
        d = Path(job_dir)
    stack = contextlib.ExitStack()
    try:
        stack.enter_context(bundle.job_lock(d, what))
    except bundle.JobBusy as busy:
        console.print(f"[red]{what}: {busy}[/]", highlight=False)
        raise typer.Exit(1) from busy
    ctx.call_on_close(stack.close)
    return d, W.Workflow(d)


def _stop_if_documents_invalid(job_dir, step: str) -> None:
    """F05: exit 4 (blocked) before a browser opens when the job's resume / cover letter are not a current, valid build."""
    from pathlib import Path

    from jobagent.config import get_settings
    from jobagent.tools import documents
    from jobagent.steps.apply import EXIT_BLOCKED

    d = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    check = documents.verify(d, get_settings().ats_shortfall)
    for w in check["warnings"]:
        console.print(f"[yellow]{step}: {w}[/]", highlight=False)
    if not check["ok"]:
        console.print(f"[red]{step}: blocked, the documents are not a valid current build:[/]", highlight=False)
        for r in check["reasons"]:
            console.print(f"[red]  - {r}[/]", highlight=False)
        raise typer.Exit(EXIT_BLOCKED)


def _stopped(job_dir, gone, step: str) -> None:
    """A posting found unavailable while applying: record it on the job and the application, exit with code 3."""
    from pathlib import Path

    from jobagent import db
    from jobagent.steps.submit import record
    from jobagent.tools import availability

    p = Path(job_dir) / "jd.json"
    jd = json.loads(p.read_text()) if p.exists() else {}
    conn = db.connect()
    # the job is the posting: mark it (the page that said so is in the reason's URL)
    availability.mark(jd.get("source_url") or gone.url, gone, conn)
    record(conn, Path(job_dir), jd, state=gone.status, notes=f"{gone.kind}: {gone.reason}")
    what = "the agent can't apply here" if gone.kind == "blocked" else "can't be applied to"
    console.print(f"[yellow]{step}: stopped, {jd.get('title', '')} @ {jd.get('company', '')}: {what} "
                  f"({gone.kind}: {gone.reason}); nothing was sent[/]", highlight=False)
    raise typer.Exit(EXIT_UNAVAILABLE)


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


RESEARCH_TEST_COMPANIES = ["General Mills", "KPMG LLP", "DOWL", "Intel Corporation", "Cafe Valley, Inc.",
                           "Stripe", "Palantir Technologies", "OpenAI", "NVIDIA", "Amazon"]


def _research_table(results, title: str) -> tuple[Table, bool]:
    table = Table(title=title)
    for col in ("company", "website", "does", "mission", "products", "values", "news", "sources (official)", "time"):
        table.add_column(col)
    failed = False
    for name, p, err, secs in results:
        if err or p is None:
            failed = True
            table.add_row(name, f"[red]{err}[/]", *[""] * 6, f"{secs:.0f}s")
            continue
        official = sum(1 for s in p.sources.values() if s["kind"] == "official")
        bad = not p.website or not p.what_they_do or official == 0
        failed |= bad
        table.add_row(name, p.website or "[red]—[/]", str(len(p.what_they_do)), str(len(p.mission)), str(len(p.products)),
                      str(len(p.values)), str(len(p.recent_news)),
                      f"{len(p.sources)} ({official})" if not bad else f"[red]{len(p.sources)} ({official})[/]", f"{secs:.0f}s")
    return table, failed


def _skill_inputs(d, s) -> str:
    """The skill graph's effect on this posting, as a resume-stage input: a graph that grew, a skill you approved or the graph switched off
    rebuilds the resume (a changed fingerprint), without --force."""
    try:
        from jobagent.state import JobDescription
        from jobagent.tools import skillgraph, tailor as T
        r = T.parse((s.data_dir / "templates" / "resume.tex").read_text())
        return skillgraph.input_fingerprint(JobDescription.model_validate_json((d / "jd.json").read_text()), r, s)
    except Exception:
        return "unavailable"


@app.command("tailor")
def tailor_cmd(
    ctx: typer.Context,
    job_dir: str = typer.Argument(..., help="outputs/<company>/<job-id> from `jobagent scrape`."),
    company: str = typer.Argument(..., help="Company slug of the research profile (state/companies/<slug>.md)."),
    no_ats: bool = typer.Option(False, "--no-ats", help="Skip the step 10 ATS loop."),
    single: bool = typer.Option(False, "--single", help="The older one-model writer instead of the agent team."),
    force: bool = typer.Option(False, "--force", help="Rebuild every stage; without it a finished resume or cover letter whose "
                                                      "inputs and files are unchanged is reused (see `jobagent workflow`)."),
) -> None:
    """Tailor the resume to a scraped job (one page) with the agent team and its validator (ATS loop included), then
    write the matching cover letter. A stage that already finished is reused after a crash or a second run; `--force` redoes it."""
    from pathlib import Path

    from jobagent.config import get_settings
    from jobagent.steps.tailor import cover_letter, main

    _stop_if_unavailable(job_dir, "tailor")
    log = runlog("tailor", str(job_dir).rstrip("/").rsplit("/", 1)[-1])
    from jobagent.tools import documents

    s = get_settings()
    d = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    from jobagent.tools import bundle
    lock = contextlib.ExitStack()
    try:
        lock.enter_context(bundle.job_lock(d, "tailor"))        # F25: one build of a job at a time
    except bundle.JobBusy as busy:
        console.print(f"[red]tailor: {busy}[/]", highlight=False)
        raise typer.Exit(1) from busy
    ctx.call_on_close(lock.close)
    if bundle.recover(d):
        log("tailor: completed a resume publish that an earlier run was interrupted in")
    from jobagent.tools import workflow as W
    wf = W.Workflow(d)
    jd_sha, txt_sha = W.sha_file(d / "jd.json"), W.sha_file(d / "jd.txt")
    template_sha = W.sha_file(s.data_dir / "templates" / "resume.tex")
    profile_sha = W.sha_file(s.state_dir / "companies" / f"{company}.md")
    order = ["resume", "cover_letter"]
    if force:
        wf.forget_from("resume", order)
    if single:
        documents.begin(d)
        out = main(job_dir, company, ats=not no_ats)
        ats = (out.get("ats") or {})
        found = documents.resume_findings(d)         # the same hard and claims checks as the agent-team writer and every refresh
        build = [p for p in out.get("problems", []) if "LOCKED" in p] + \
            ([f"required duties not covered: {out['uncovered']}"] if out.get("uncovered") else []) + \
            ([] if found["evidence"] else [f"the hard and claims checks could not be run ({found['reason']})"])
        res = documents.assess(d, resume={"produced": (d / "resume.pdf").exists(), "hard_issues": found["hard"],
                                          "claims_issues": found["claims"], "build_findings": build,
                                          "template_sha256": out.get("template_sha256"),
                                          "coverage_limited": out.get("coverage_limited", [])},
                               letter=out.get("letter"),
                               ats={"average": ats.get("average"), "complete": ats.get("average") is not None,
                                    "missing": [], "scored": bool(ats)}, policy=s.ats_shortfall, target=s.ats_target)
    else:
        from jobagent.steps import multi_agent as M

        model = s.model_writer  # the same model for every agent (chosen 2026-11-02)
        sites = () if no_ats else M.SITES
        # F15: each finished stage is kept. A rerun after a crash (or a second `tailor`) reuses the resume if its inputs and files
        # are unchanged, and redoes only the cover letter; `--force` rebuilds everything.
        rj, how = wf.run("resume", lambda: M.run_job(d, company, {k: model for k in M.DEFAULT_MODELS}, sites, log=log),
                         inputs={"jd": jd_sha, "jd_text": txt_sha, "template": template_sha, "profile": profile_sha, "model": model,
                                 "sites": list(sites), "skills": _skill_inputs(d, s), "document_provenance": 1,
                                 "project_content_validation": 1, "versioned_content_review": 3},
                         outputs=["resume.pdf", "resume.tex", "filled.json"], force=force, log=log,
                         check=lambda r: None if r.get("produced") else (r.get("failure") or "no valid resume was produced"))
        log(f"resume ({how}): average {rj['average']} {rj['scores']}, {rj['pages']} page(s), hard issues {rj['hard_issues']}")
        cl = None
        if (rj["produced"] and rj.get("template_sha256")
                and rj["template_sha256"] == documents.template_fingerprint()):
            # Do not spend another model call when the source changed during generation.
            cl, how = wf.run("cover_letter", lambda: cover_letter(d, company, log=log),
                             inputs={"resume_tex": W.sha_file(d / "resume.tex"), "profile": profile_sha, "model": model,
                                     "candidate_credentials": 1},
                             outputs=["cover_letter.pdf", "cover_letter.tex"], force=force, log=log)
            log(f"cover letter ({how}): {cl['words']} words, {len(cl['issues'])} issue(s)")
        res = documents.assess(d, resume=rj, letter=cl, ats={"average": rj["average"], "complete": rj["average_complete"],
                                                             "missing": rj["average_missing"], "scored": bool(rj["average_sites"])},
                               policy=s.ats_shortfall, target=s.ats_target)
    documents.save(d, res)
    _print_documents(res)
    if code := documents.exit_code(res):
        raise typer.Exit(code)


@app.command("run")
def run_pipeline_cmd(
    inputs: list[str] = typer.Argument(..., help="Job URLs, company names (comma lists ok) and/or CSV/XLSX files, in any mix."),
    through: str = typer.Option("apply", help="scrape, tailor, apply or submit; submit still requires review approval (job URLs only)."),
    limit: int = typer.Option(5, help="For company names and files: at most N queued jobs are worked in this run."),
    browser_mode: str = typer.Option("docker", help="docker, desktop, or auto (filling only)."),
    allow_desktop_submit: bool = typer.Option(False, "--allow-desktop-submit"),
    force: bool = typer.Option(False, help="Refresh input stages and regenerate documents."),
    no_ats: bool = typer.Option(False, "--no-ats", help="Skip ATS scans while generating documents; document validity still gates submission."),
    filling_strategy: str = typer.Option(None, help="original or experimental; default FILLING_STRATEGY/experimental."),
    ignore_eligibility: bool = typer.Option(False, "--ignore-eligibility", help="Go on although the posting's hard requirements "
        "(U.S. citizenship, a clearance, no sponsorship) conflict with your file. The queue never does this."),
) -> None:
    """Take every input through the shared workflow: a job URL directly, company names and files through discovery and the queue (US-only,
    role-matched, paced per company). Stops at the review screen; sending needs your typed `approve` per application (`review-queue`)."""
    from rich.table import Table

    from jobagent.tools import multi_input
    log = runlog("run")
    try:
        rows = multi_input.run_inputs(inputs, through=through, limit=limit, log=log, browser_mode=browser_mode,
                                      allow_desktop_submit=allow_desktop_submit, force=force, no_ats=no_ats,
                                      filling_strategy=filling_strategy, ignore_eligibility=ignore_eligibility)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    table = Table(title="run: what happened to each input")
    for col in ("input", "kind", "state", "note"):
        table.add_column(col)
    for r in rows:
        table.add_row(r.input[:60], r.kind, r.state, r.note[:100])
    console.print(table)
    if code := multi_input.exit_code(rows):
        raise typer.Exit(code)


@app.command("review-queue")
def review_queue_cmd() -> None:
    """Applications filled to their review screen and waiting for YOUR decision, oldest first, with how ready each is and the command
    that opens its review. Nothing is approved or sent from here; every submission still needs your typed `approve` for that application."""
    from rich.table import Table

    from jobagent import db
    from jobagent.tools import review_queue

    rows = review_queue.waiting(db.connect())
    if not rows:
        console.print("review-queue: nothing is waiting for you")
        return
    table = Table(title=f"{len(rows)} application(s) waiting for your decision")
    for col in ("id", "company", "title", "ready", "waiting h", "blockers", "open its review"):
        table.add_column(col)
    for r in rows:
        table.add_row(str(r["id"]), (r["company"] or "")[:22], (r["title"] or "")[:34], "[green]yes[/]" if r["ready"] else "[yellow]no[/]",
                      "-" if r["age_hours"] is None else f"{r['age_hours']:.0f}", str(len(r["blockers"])), r["review_command"])
    console.print(table)
    for r in rows:
        for b in r["blockers"][:3]:
            console.print(f"  [yellow]{r['company']}: {b[:140]}[/]", highlight=False)


@app.command("workflow")
def workflow_cmd(job_dir: str = typer.Argument(..., help="outputs/<company>/<job-id>")) -> None:
    """The durable stages of a job's workflow: which finished, which were interrupted, and what a rerun will reuse."""
    from pathlib import Path

    from jobagent.tools import workflow as W

    d = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    wf = W.Workflow(d)
    rows = wf.summary()
    if not rows:
        console.print(f"workflow {wf.id}: no stage has run for this job yet")
        return
    console.print(f"workflow {wf.id}", highlight=False)
    for r in rows:
        extra = f" - {r['error']}" if r.get("error") else f" - {r['note']}" if r.get("note") else ""
        console.print(f"  {r['stage']:<13} {r['status']:<12} {r.get('finished_at') or ''}{extra}", highlight=False)


def _print_documents(res: dict) -> None:
    """The result of generating a job's documents, with validity and the ATS target on separate lines."""
    console.print(f"documents: {'VALID' if res['valid'] else 'INVALID'}; resume {res['resume']['pages']} page(s), cover letter "
                  f"{res['cover_letter']['pages']} page(s); ATS {res['ats']['status']} (average {res['ats']['average']}, target "
                  f"{res['ats']['target']}, policy {res['ats']['policy']})", highlight=False)
    for b in res["blockers"]:
        console.print(f"[red]  blocker: {b}[/]", highlight=False)
    for w in res["warnings"]:
        console.print(f"[yellow]  warning: {w}[/]", highlight=False)
    if res["estimates"]:
        console.print(f"  {len(res['estimates'])} estimated figure(s) to confirm at review (changes.md)", highlight=False)


@app.command("documents")
def documents_cmd(job_dir: str = typer.Argument(..., help="outputs/<company>/<job-id> of a tailored job.")) -> None:
    """Re-validate a job's resume and cover letter on disk (after the ATS loop or a hand edit) and show the result.
    Exit 0 usable, 1 invalid, 8 valid but below the ATS target while the policy is 'block'."""
    from pathlib import Path

    from jobagent.config import get_settings
    from jobagent.tools import documents

    s = get_settings()
    d = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    res = documents.refresh(d, s.ats_shortfall, s.ats_target)
    if res is None:
        console.print("[red]no documents.json: run `jobagent tailor` first[/]")
        raise typer.Exit(documents.EXIT_INVALID)
    _print_documents(res)
    if code := documents.exit_code(res):
        raise typer.Exit(code)


@app.command("cover-letter")
def cover_letter_cmd(
    job_dir: str = typer.Argument(..., help="outputs/<company>/<job-id> with a tailored resume.tex."),
    company: str = typer.Argument(..., help="Company slug of the research profile (state/companies/<slug>.md)."),
) -> None:
    """(Re)write the cover letter from the job's resume.tex — e.g. after editing the resume's numbers."""
    from jobagent.steps.tailor import cover_letter

    from pathlib import Path

    from jobagent.config import get_settings
    from jobagent.tools import documents

    d = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    s = get_settings()
    documents.begin_rewrite(d)           # fail closed BEFORE the old letter is replaced
    cl = cover_letter(job_dir, company, log=runlog("cover-letter"))
    console.print(f"cover letter: {cl['words']} words, {len(cl['issues'])} issue(s)", highlight=False)
    res = documents.refresh(d, s.ats_shortfall, s.ats_target, letter=cl)
    if res:
        _print_documents(res)
    if not cl.get("valid", not cl["issues"]):
        raise typer.Exit(documents.EXIT_INVALID)
    if res and (code := documents.exit_code(res)):
        raise typer.Exit(code)


@app.command("ats")
def ats_cmd(
    job_dir: str = typer.Argument(..., help="outputs/<company>/<job-id> with resume.pdf and jd.txt."),
    site: list[str] = typer.Option(None, "--site", help="Registered ATS checker; repeat for multiple sites."),
) -> None:
    """Score the job's resume on the online ATS checkers (recorded in ats_runs)."""
    from pathlib import Path

    from jobagent.state import JobDescription
    from jobagent.tools import ats_sites

    d = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    jd = JobDescription.model_validate_json((d / "jd.json").read_text())
    selected = tuple(site) if site else ats_sites.DEFAULT_SITES
    results = ats_sites.score_all(d / "resume.pdf", (d / "jd.txt").read_text(), title=jd.title or "",
                                 sites=selected, company=jd.company,
                                 log=runlog("ats"))
    for s in results:
        ats_sites.record(d, s.site, 0, s.score, s.missing, s.to_dict())
        status = s.score if s.score is not None else (
            "written feedback" if s.breakdown.get("feedback_only") else s.breakdown.get("status", "failed")
        )
        console.print(f"{s.site}: {status}  missing: {', '.join(s.missing[:12]) or '-'}", highlight=False)
        for tip in s.advice + s.breakdown.get("template", []):
            console.print(f"   advice: {tip}", highlight=False)
    summary = ats_sites.write_summary(d, {s.site: s.score for s in results}, selected)
    count, total = len(summary["scores"]), len(summary["sites"])
    if summary["complete"]:
        console.print(f"ATS average ({count}/{total} sites): {summary['average']}/100", highlight=False)
    else:
        console.print(f"ATS average incomplete ({count}/{total} sites); "
                      f"missing: {', '.join(summary['missing']) or 'no numeric sites selected'}", highlight=False)
    console.print(f"Summary: {d / 'ats_summary.json'}", highlight=False)


@app.command("ats-record")
def ats_record_cmd(
    job_dir: str = typer.Argument(..., help="outputs/<company>/<job-id> whose resume.pdf you scanned."),
    site: str = typer.Argument(..., help="Any registered numeric ATS scorer, e.g. jobscan."),
    score: float = typer.Argument(..., help="The score the site gave (0-100)."),
) -> None:
    """Record a numeric score obtained by hand on any registered scorer; it joins the ATS average."""
    from pathlib import Path

    from jobagent.tools import ats_sites

    if site not in ats_sites.RECORDABLE_SITES:
        console.print(f"[red]unknown numeric site {site!r}; use one of {', '.join(ats_sites.RECORDABLE_SITES)}[/]")
        raise typer.Exit(1)
    d = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    ats_sites.record(d, site, 0, score, [], {"manual": True})
    console.print(f"recorded {site} = {score} for the current resume.pdf", highlight=False)


@app.command("ats-loop")
def ats_loop_cmd(
    job_dir: str = typer.Argument(..., help="outputs/<company>/<job-id> with a tailored resume."),
    company: str = typer.Argument(..., help="Company slug of the research profile."),
    target: float = typer.Option(90.0, help="Stop when the average online score reaches this."),
    site: list[str] = typer.Option(None, "--site", help="Online checkers to use (default: all automated ones)."),
) -> None:
    """Step 10: score the resume on online ATS checkers and work their missing keywords and advice in until the target."""
    from jobagent.steps.ats import run
    from jobagent.tools import ats_sites

    from pathlib import Path

    from jobagent.config import get_settings
    from jobagent.tools import documents

    d = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    s = get_settings()
    documents.begin_rewrite(d)           # fail closed BEFORE the loop touches the resume; a failed rewrite stays blocked
    run(job_dir, company, target, sites=tuple(site) if site else ats_sites.DEFAULT_SITES,
        log=runlog("ats-loop"))
    if res := documents.refresh(d, s.ats_shortfall, s.ats_target):    # the loop rewrote the resume: validate what is on disk now
        _print_documents(res)
        if code := documents.exit_code(res):                          # an invalid build is a failed command, not just a printout
            raise typer.Exit(code)


@app.command("model-experiment")
def model_experiment_cmd(
    jobs: list[str] = typer.Argument(..., help="outputs/<company>/<job-id>:<company-slug>, one or more."),
    model: list[str] = typer.Option(None, "--model", help="Voyager models to try (default: every chat model)."),
    workers: int = typer.Option(4, help="Models written in parallel (scoring takes turns on the browser)."),
    target: float = typer.Option(90.0, help="ATS average each model's loop aims for."),
) -> None:
    """Every Voyager chat model tailors every given job (5 rounds + ATS loop); ranks them by average ATS score."""
    from jobagent.steps.model_experiment import run

    pairs = [tuple(j.rsplit(":", 1)) for j in jobs]
    root = run(pairs, list(model) if model else None, workers, target,
               log=runlog("model-experiment"))
    console.print(f"results: {root / 'results.md'}", highlight=False)


@app.command("multi-agent")
def multi_agent_cmd(
    jobs: list[str] = typer.Argument(..., help="outputs/<company>/<job-id>:<company-slug>, one or more."),
    role: list[str] = typer.Option(None, "--role", help="agent=model, e.g. experience=qwen3-coder-next "
                                   "(agents: experience, projects, skills, summary, router)."),
    same_model: str = typer.Option(None, "--same-model", help="Variant: this one model for every agent and the "
                                   "validator."),
    no_ats: bool = typer.Option(False, "--no-ats", help="Dry run: agents and layout validator only, no ATS sites."),
) -> None:
    """Experiment: a team of section agents (one model each) and validators writes each job's resume."""
    from jobagent.steps.multi_agent import DEFAULT_MODELS, SITES, run

    models = {k: same_model for k in DEFAULT_MODELS} if same_model else {}
    models.update(dict(x.split("=", 1) for x in role or []))
    run([tuple(j.rsplit(":", 1)) for j in jobs], models, () if no_ats else SITES,
        log=runlog("multi-agent"))


@app.command("portal-login")
def portal_login_cmd(
    url: str = typer.Argument(..., help="A posting or career-site URL on the portal."),
    fresh: bool = typer.Option(False, "--fresh", help="Forget the portal's session first (tests a real sign-in)."),
) -> None:
    """Step 11: sign in to the posting's job portal with the agent's email (the account is created the first time)."""
    from jobagent.tools.browser import persistent_context
    from jobagent.tools.hitl import open_hitl
    from jobagent.tools.inbox import open_inbox
    from jobagent.tools.portal_auth import ensure_account
    from jobagent.tools.vault import open_vault

    log = runlog("portal-login")
    vault = open_vault()
    email = (vault.get("gmail") or {})["email"]
    with open_inbox() as inbox, persistent_context() as ctx:
        res = ensure_account(ctx.pages[0], url, vault, inbox, open_hitl(log), email, log=log, fresh=fresh)
    console.print(res, highlight=False)


@app.command("portal-logout")
def portal_logout_cmd(url: str = typer.Argument(..., help="A posting or career-site URL on the portal.")) -> None:
    """Sign the agent out of a job portal (confirmed: the site no longer shows the account)."""
    from jobagent.tools.browser import persistent_context
    from jobagent.tools.hitl import open_hitl
    from jobagent.tools.inbox import open_inbox
    from jobagent.tools.portal_auth import sign_out
    from jobagent.tools.vault import open_vault

    log = runlog("portal-logout")
    email = (open_vault().get("gmail") or {})["email"]
    with open_inbox() as inbox, persistent_context() as ctx:
        ok = sign_out(ctx.pages[0], url, open_vault(), inbox, open_hitl(log), email, log=log)
    console.print(f"signed out: {ok}", highlight=False)


@app.command("apply")
def apply_cmd(
    ctx: typer.Context,
    job_dir: str = typer.Argument(..., help="outputs/<company>/<job-id> with jd.json, resume.pdf (and cover_letter.pdf)."),
    company: str = typer.Argument(..., help="Company slug of the research profile (state/companies/<slug>.md)."),
    no_submit: bool = typer.Option(True, "--no-submit/--submit", help="Step 12 always stops at the review screen."),
    ask_minutes: float = typer.Option(30, help="How long to wait for your answer to a question (then it's left for review)."),
    model: str = typer.Option(None, help="Voyager model that plans the answers (default: the writer model)."),
    browser_mode: str = typer.Option('docker', help="docker, desktop, or auto (Docker then desktop on a challenge block)."),
    filling_strategy: str = typer.Option(None, help="original or experimental; default FILLING_STRATEGY/experimental."),
    desktop_allow_origin: list[str] = typer.Option(None, "--desktop-allow-origin",
        help="Additional trusted application/login HTTPS origin; repeat for extra origins."),
) -> None:
    """Step 12: fill the job's application up to its review screen and read every field back (never submits)."""
    from jobagent.steps.apply import run
    from jobagent.tools.session import check_mode, open_session
    from jobagent.tools.hitl import open_hitl
    from jobagent.tools.inbox import open_inbox
    from jobagent.tools.portal_auth import ensure_account
    from jobagent.tools.vault import open_vault

    from jobagent.tools.availability import Unavailable

    if not no_submit:
        raise typer.BadParameter("submitting comes with the human review (step 13)")
    from jobagent.filling import select_strategy
    try:strategy=select_strategy(filling_strategy,resume_job=job_dir)
    except ValueError as e:raise typer.BadParameter(str(e)) from e
    try:
        check_mode(browser_mode, submitting=False)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    _stop_if_unavailable(job_dir, "apply")
    _stop_if_documents_invalid(job_dir, "apply")
    _, wf = _lock_job(ctx, job_dir, "apply")
    log = runlog("apply", str(job_dir).rstrip("/").rsplit("/", 1)[-1])
    if (wf.stage("apply") or {}).get("status") == "interrupted":
        log("apply: an earlier run was interrupted; the page is reopened and read again (filling never sends)")
    wf.mark("apply", "running")
    vault = open_vault()
    email = (vault.get("gmail") or {})["email"]
    from pathlib import Path
    jd = json.loads((Path(job_dir) / 'jd.json').read_text())
    url = jd.get('apply_url') or jd.get('source_url')
    try:
        modes = ('docker','desktop') if browser_mode == 'auto' else (browser_mode,)
        for mode in modes:
            log(f'apply: browser={mode}; planner=Voyager {model or "configured writer"}')
            related = [u for u in (jd.get('source_url'), *(desktop_allow_origin or [])) if u]
            with open_inbox() as inbox, open_session(mode, url, related) as sess:
                page = sess.page
                sign_in = lambda url: ensure_account(page, url, vault, inbox, open_hitl(log), email, log=log, already_open=True)
                res = run(page, job_dir, company, ensure_signed_in=sign_in, log=log, hitl=open_hitl(log),
                          ask_s=ask_minutes * 60, model_name=model, filling_strategy=strategy,
                          **sess.run_options(observe_s=30, auto_fallback=browser_mode == 'auto'))
            if not (res.get('state')=='blocked' and res.get('blocked_by')=='challenge'
                    and res.get('resumable') and mode=='docker' and browser_mode=='auto'):
                break
    except Unavailable as gone:
        wf.mark("apply", "failed", error=f"unavailable: {gone.kind}")
        _stopped(job_dir, gone, "apply")
    if res["state"] == "review":
        from datetime import UTC, datetime

        from jobagent import db
        from jobagent.steps.submit import record

        # Plain apply and queue runs never enter submit's review hook. Persist the
        # draft here too, so review-queue can find it and pacing keeps its slot
        # after the queue's active claim is released. record preserves sent states.
        conn = db.connect()
        try:
            record(conn, Path(job_dir), jd, state="filled",
                   filled_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
        finally:
            conn.close()
    wf.mark("apply", "done" if res["state"] == "review" else "failed", result={"state": res["state"], "ready": bool(res.get("ready_to_submit"))})
    console.print(f"apply: {res['state']}; {res['verified']}/{len(res['fields'])} fields verified; "
                  f"{len(res['flags'])} for review -> {job_dir}/application/application.md", highlight=False)
    from jobagent.steps.apply import exit_code
    if res["state"] == "blocked":
        console.print(f"[yellow]apply: blocked ({res.get('blocked_by', '')}): {res.get('reason', '')}[/]", highlight=False)
    elif not res.get("ready_to_submit"):
        for b in (res.get("blockers") or [f"the form ended '{res['state']}'"])[:6]:
            console.print(f"[yellow]apply: not ready: {b}[/]", highlight=False)
    if code := exit_code(res):  # 4 blocked, 5 on the review screen but not ready, 1 failed or stuck
        raise typer.Exit(code)


@app.command("submit")
def submit_cmd(
    ctx: typer.Context,
    job_dir: str = typer.Argument(..., help="outputs/<company>/<job-id> with jd.json, resume.pdf (and cover_letter.pdf)."),
    company: str = typer.Argument(..., help="Company slug of the research profile (state/companies/<slug>.md)."),
    ask_minutes: float = typer.Option(30, help="How long a question while filling waits for your answer."),
    review_minutes: float = typer.Option(60, help="How long the filled form waits for your approve / skip."),
    mail_minutes: float = typer.Option(15, help="How long to watch the inbox for the confirmation mail."),
    filling_strategy: str = typer.Option(None, help="original or experimental; resumes the application's recorded strategy by default."),
    browser_mode: str = typer.Option('docker', help="docker (default) or desktop: the browser that holds the application "
                                     "(`apply --browser-mode auto` is for filling only)."),
    allow_desktop_submit: bool = typer.Option(False, "--allow-desktop-submit",
        help="Required with --browser-mode desktop: sending from the desktop browser must be asked for explicitly."),
    desktop_allow_origin: list[str] = typer.Option(None, "--desktop-allow-origin",
        help="Additional trusted application/login HTTPS origin; repeat for extra origins."),
) -> None:
    """Step 13: fill the application, show it for review, and submit it only when you answer 'approve'; then match
    the confirmation mail. The same approval is needed in every browser mode."""
    from pathlib import Path

    from jobagent import db
    from jobagent.steps.apply import run
    from jobagent.steps.submit import make_at_review
    from jobagent.tools.session import check_mode, open_session
    from jobagent.tools.hitl import open_hitl
    from jobagent.tools.inbox import open_inbox
    from jobagent.tools.portal_auth import ensure_account
    from jobagent.tools.vault import open_vault

    from jobagent.tools.availability import Unavailable

    from jobagent.steps.apply import EXIT_UNCERTAIN
    from jobagent.steps.submit import guard

    from jobagent.filling import select_strategy
    try:
        check_mode(browser_mode, submitting=True, allow_desktop_submit=allow_desktop_submit)
        strategy=select_strategy(filling_strategy,resume_job=job_dir)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if stop := guard(db.connect(), Path(job_dir)):     # before a browser opens: a sent application is never filled again
        console.print(f"submit: {stop['message']}", highlight=False)
        raise typer.Exit(EXIT_UNCERTAIN if stop["state"] == "submission_uncertain" else 0)
    _stop_if_unavailable(job_dir, "submit")
    _stop_if_documents_invalid(job_dir, "submit")
    _, wf = _lock_job(ctx, job_dir, "submit")
    log = runlog("submit", str(job_dir).rstrip("/").rsplit("/", 1)[-1])
    if (wf.stage("submit") or {}).get("status") == "interrupted":
        # not replayed blindly: the submission attempts (guard above) decide what is safe; this only says why a look is worthwhile
        log("submit: an earlier submit run ended before it finished; its submission attempts were checked above and nothing "
            "that may have been sent is sent again")
    wf.mark("submit", "running", side_effect=True)
    vault = open_vault()
    email = (vault.get("gmail") or {})["email"]
    jd = json.loads((Path(job_dir) / "jd.json").read_text())
    url = jd.get("apply_url") or jd.get("source_url")
    conn = db.connect()
    try:
        related = [u for u in (jd.get("source_url"), *(desktop_allow_origin or [])) if u]
        log(f"submit: browser={browser_mode}")
        with open_inbox() as inbox, open_session(browser_mode, url, related) as sess:
            page = sess.page
            sign_in = lambda url: ensure_account(page, url, vault, inbox, open_hitl(log), email, log=log, already_open=True)  # noqa: E731
            hook = make_at_review(Path(job_dir), jd, open_hitl(log), inbox, conn, log, ask_s=review_minutes * 60,
                                  mail_s=mail_minutes * 60)
            res = run(page, job_dir, company, ensure_signed_in=sign_in, log=log, hitl=open_hitl(log),
                      ask_s=ask_minutes * 60, at_review=hook, filling_strategy=strategy, **sess.run_options())
    except Unavailable as gone:
        wf.mark("submit", "failed", error=f"unavailable: {gone.kind}")
        _stopped(job_dir, gone, "submit")
    sub = res.get("submission") or {"state": f"not submitted: the form ended '{res['state']}'"}
    wf.mark("submit", "done" if sub.get("state") in ("submitted", "confirmed", "already_submitted", "skipped") else "failed",
            result={"state": sub.get("state")})
    console.print(f"submit: {sub.get('state')}; {res['verified']}/{len(res['fields'])} fields verified -> "
                  f"{job_dir}/application/review.md", highlight=False)
    from jobagent.steps.apply import EXIT_FAILED, EXIT_NOT_READY, exit_code
    state = sub.get("state")
    if state in ("submitted", "confirmed", "already_submitted"):
        return
    if state in ("submission_unknown", "submission_uncertain"):   # clicked or may have been, receipt unproven: never "ok"
        raise typer.Exit(EXIT_UNCERTAIN)
    if state == "failed":
        raise typer.Exit(EXIT_FAILED)
    if state == "not_ready":                      # you approved but required answers were still missing: nothing was sent
        raise typer.Exit(EXIT_NOT_READY)
    if code := exit_code(res):                    # blocked 4, failed or stuck 1, review but not ready 5; skipped while ready 0
        raise typer.Exit(code)


@app.command("confirm")
def confirm_cmd(job_dir: str = typer.Argument(..., help="outputs/<company>/<job-id> of a submitted application."),
                minutes: float = typer.Option(1, help="How long to watch the inbox."),
                attest: str = typer.Option("", help="After checking the portal yourself, settle an attempt of unknown outcome: "
                                           "'sent' or 'not-sent' ('not-sent' is what lets a new attempt start).")) -> None:
    """Look for a submitted application's confirmation mail again; settles an attempt whose outcome is unknown."""
    from pathlib import Path

    from jobagent import db
    from jobagent.steps.submit import confirm
    from jobagent.tools.inbox import open_inbox

    with open_inbox() as inbox:
        if attest not in ('', 'sent', 'not-sent'):
            raise typer.BadParameter("--attest is 'sent' or 'not-sent'")
        console.print(confirm(Path(job_dir), inbox, db.connect(), timeout_s=minutes * 60, attest=attest or None), highlight=False)


@app.command("applications")
def applications_cmd() -> None:
    """Every application's state: filled, approved, submitted, confirmed, skipped or failed."""
    from rich.table import Table

    from jobagent import db

    rows = db.connect().execute("SELECT * FROM applications ORDER BY COALESCE(submitted_at, decided_at, filled_at) "
                                "DESC").fetchall()
    table = Table("company", "title", "state", "submitted", "confirmation", "notes")
    for r in rows:
        table.add_row(r["company"] or "", (r["title"] or "")[:40], r["state"], r["submitted_at"] or "",
                      (r["confirmation"] or "")[:50], (r["notes"] or "")[:50])
    console.print(table if rows else "no applications yet")


@app.command("locate")
def locate_cmd(job_dir: str = typer.Argument(..., help="outputs/<company>/<job-id> with jd.json."),
               company: str = typer.Argument(..., help="Company slug of the research profile (state/companies/<slug>.md).")
               ) -> None:
    """Resolve the job's locations ("Corporate Headquarters", "HQ", "Main Campus") to cities, into jd.json."""
    from pathlib import Path

    from jobagent.tools.places import resolve_job

    resolve_job(Path(job_dir), company, log=runlog("locate"))


@app.command("learned")
def learned_cmd(question: str = typer.Argument(None, help="Show the entries closest to this question instead.")) -> None:
    """What the agent learned from your on-the-spot replies (data/learned.yaml): rules for every company and
    company-specific answers. With a question: the entries the agent would look up for it."""
    from rich.table import Table

    from jobagent.tools import learned

    entries = learned.relevant(question, "", k=5) if question else learned.load()
    table = Table("question", "rule / answer", "company", "learned")
    for e in entries:
        table.add_row(e["question"][:70], (f"rule: {e['instructions']}" if e.get("instructions")
                                           else str(e.get("answer")))[:90], str(e.get("company", "")),
                      str(e.get("learned_from") or e.get("date", "")))
    console.print(table if entries else "nothing learned yet" if not question else "nothing close")


@app.command("portal-reset")
def portal_reset_cmd(url: str = typer.Argument(..., help="A career-site URL on the portal.")) -> None:
    """One user-approved password reset of an existing portal account to the shared password, then sign in."""
    from jobagent.tools.browser import persistent_context
    from jobagent.tools.hitl import open_hitl
    from jobagent.tools.inbox import open_inbox
    from jobagent.tools.portal_auth import reset_once
    from jobagent.tools.vault import open_vault

    log = runlog("portal-reset")
    vault = open_vault()
    email = (vault.get("gmail") or {})["email"]
    with open_inbox() as inbox, persistent_context() as ctx:
        res = reset_once(ctx.pages[0], url, vault, inbox, open_hitl(log), email, log=log)
    console.print(res, highlight=False)


@app.command("research")
def research_cmd(
    companies: list[str] = typer.Argument(None),
    file: str = typer.Option(None, "--file", "-f", help="One company per line."),
    workers: int = typer.Option(5),
    refresh: bool = typer.Option(False, help="Ignore profiles cached in the last 7 days."),
) -> None:
    """Research companies (cited profiles in state/companies/<company>.md)."""
    import time

    from jobagent.tools.research import research_many

    names = list(companies or []) + _read_urls([], file)
    if not names:
        console.print("[red]give at least one company or --file[/]")
        raise typer.Exit(1)
    t0 = time.monotonic()
    results = research_many(names, workers=workers, refresh=refresh, log=runlog("research"))
    table, failed = _research_table(results, f"{len(names)} compan(ies) in {time.monotonic() - t0:.0f}s")
    console.print(table)
    if failed:
        raise typer.Exit(1)


@step_app.command("tailor-check")
def step_tailor_check(
    file: str = typer.Option("tests/e2e/scrape_urls.txt", "--file", "-f", help="Posting URLs, one per line."),
    workers: int = typer.Option(3, help="Postings tailored in parallel."),
) -> None:
    """Step 9 gate: postings end to end (scrape → research → resume + cover letter), then the one-page,
    locked-parts, coverage, bullet, letter-consistency, projects and no-dash criteria."""
    from jobagent.steps.tailor import check

    failed = False
    for name, ok, detail in check(_read_urls([], file), workers, log=runlog("step-tailor-check")):
        failed |= not ok
        console.print(f"{'[green]PASS[/]' if ok else '[red]FAIL[/]'} {name}: {detail}", highlight=False)
    if failed:
        raise typer.Exit(1)


@step_app.command("research-check")
def step_research_check(workers: int = typer.Option(5)) -> None:
    """Step 8 e2e: 10 companies researched with cited sources; the second pass comes from the cache."""
    import time

    from jobagent.tools.research import research_many

    failed = False
    for n, refresh in ((1, True), (2, False)):
        t0 = time.monotonic()
        results = research_many(RESEARCH_TEST_COMPANIES, workers=workers, refresh=refresh, log=lambda m: None)
        elapsed = time.monotonic() - t0
        table, bad = _research_table(results, f"Pass {n} ({'fresh' if refresh else 'cache'}): {elapsed:.0f}s")
        if n == 2 and elapsed > 10:
            bad = True
            console.print("[red]second pass should come from the cache[/]")
        failed |= bad
        console.print(table)
    if failed:
        raise typer.Exit(1)


@app.command("bootstrap")
def bootstrap() -> None:
    """Global default action: check/repair the harness and accounts (resumes after a crash)."""
    from jobagent.graph.bootstrap import run

    _print_bootstrap(run(log=runlog("bootstrap")))


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
        ok = resumed["resumed"] and resumed["ran_now"] == ["browser", "linkedin", "overleaf", "github"] and not resumed["changed"]
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

    log = runlog("browser-google-login")
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

    log = runlog("browser-warm-up")
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
    if not req_id and (len(reqs) > 1 or any(_decides_submission(r.prompt) for r in reqs)):
        # the oldest-pending shortcut never answers a submission decision, nor guesses among several questions
        console.print("[red]give the request id[/] (`jobagent hitl list`): "
                      + ("a submission is waiting for approval" if any(_decides_submission(r.prompt) for r in reqs)
                         else f"{len(reqs)} requests are pending"))
        raise typer.Exit(1)
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

    hitl = open_hitl()
    reqs = hitl.list_pending()
    if not req_id and (len(reqs) > 1 or any(_decides_submission(r.prompt) for r in reqs)):
        console.print("[red]give the request id[/] (`jobagent hitl list`)")
        raise typer.Exit(1)
    req = hitl.answer(req_id or None, "done")
    console.print(f"marked {req.id} done")


def _decides_submission(prompt: str) -> bool:
    """The step 13 review question: only ever answered by its id."""
    return "'approve' to SUBMIT" in prompt


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


def cli() -> None:
    """The `jobagent` command: the typer app, with a failure ending as one red line and a trace file instead of a traceback."""
    from jobagent.log import run_cli
    run_cli(app)


if __name__ == "__main__":
    cli()
