"""Several inputs in one run (step 29): job URLs, company names and CSV/XLSX files together.

Each input is routed (`graph.router.route`): a job URL goes through the shared pipeline on its own; company names and the rows of a file
go through discovery (careers page, US-only filter, role matching) and the queue is then worked for THOSE companies only (jobs already
queued for them count; every other company's jobs are left alone), `limit` jobs per run, subject to the queue's pacing and eligibility
rules. Nothing here sends an application: `through` may be scrape, tailor or apply for company inputs (a company's jobs are never
submitted from a batch); a URL given `submit` still stops at the candidate's typed approval. One input's failure never stops the others,
every input and every worked job gets its own row with its exit code, and `exit_code(rows)` is non-zero when any of them stopped short."""

from dataclasses import dataclass, field

from jobagent.tools.jobqueue import canonical_url, slug

# states that mean "this input did not reach what was asked", whatever the exit code says
STOPPED = {"failed", "blocked", "needs_review", "unavailable", "closed", "not_ready", "error"}
# options only a single, explicitly named URL may use: the queue never overrides the eligibility screen or submits
URL_ONLY = ("ignore_eligibility", "allow_desktop_submit")


@dataclass
class Row:
    input: str
    kind: str
    state: str
    note: str = ""
    code: int = 0

    @property
    def stopped(self) -> bool:
        return self.code != 0 or self.state in STOPPED


def exit_code(rows: list["Row"]) -> int:
    """0 only when every input and job reached what was asked; otherwise the first non-zero code, or 1."""
    bad = [r for r in rows if r.stopped]
    return 0 if not bad else next((r.code for r in bad if r.code), 1)


@dataclass
class Plan:
    urls: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failures: list[Row] = field(default_factory=list)         # inputs that could not be read at all (corrupt file, bad encoding)
    sources: dict = field(default_factory=dict)               # company (lower case) -> where it came from, e.g. "targets.csv row 3"
    referrals: dict = field(default_factory=dict)             # company (lower case) -> Referral from the file row (consented only)


def plan(inputs: list[str]) -> Plan:
    """Route every input; URLs are de-duplicated by canonical address, companies by name. An input that cannot be routed (an unreadable
    file) becomes a failed row and the others carry on."""
    from jobagent.graph.router import route

    p, seen_urls, seen_cos = Plan(), set(), {}
    for raw in inputs:
        try:
            routed = route(raw)
        except Exception as exc:                                         # expected: bad files; the type name keeps real bugs recognisable
            p.failures.append(Row(raw, "input", "failed", f"{type(exc).__name__}: {str(exc)[:160]}"))
            continue
        p.warnings += routed.warnings
        for item in routed.items:
            if item.kind == "url":
                key = canonical_url(item.value)
                if key in seen_urls:
                    p.warnings.append(f"duplicate input skipped: {item.value}")
                    continue
                seen_urls.add(key)
                p.urls.append(item.value)
            else:
                name = (item.company or item.value).strip()
                key = name.lower()
                if key in seen_cos:
                    if (item.referral or None) != seen_cos[key]:
                        p.warnings.append(f"{name}: listed more than once with different referrals; no referral is used for it")
                        p.referrals.pop(key, None)
                    continue
                seen_cos[key] = item.referral or None
                p.companies.append(name)
                if item.source:
                    p.sources[key] = item.source
                if item.referral:
                    p.referrals[key] = item.referral
    return p


def run_inputs(inputs: list[str], *, through: str = "apply", limit: int = 5, log=print, pipeline=None, discover=None, work_queue=None,
               **pipeline_options) -> list[Row]:
    """Take every input as far as `through`. `pipeline.run(url, through=..., **pipeline_options)` handles URLs; `discover(companies)`
    and `work_queue(through, limit, companies=..., options=..., results=[...])` handle companies (defaults: the production discovery
    and queue runner)."""
    p = plan(inputs)
    for w in p.warnings:
        log(f"run: {w}")
    rows: list[Row] = list(p.failures)
    if not p.urls and not p.companies:
        return rows or [Row(", ".join(inputs), "none", "failed", "no job URL or company name in the input", 1)]
    if p.urls:
        if pipeline is None:
            from jobagent.tools.pipeline import Pipeline
            pipeline = Pipeline(log=log)
        for url in p.urls:
            try:
                res = pipeline.run(url, through=through, **pipeline_options)
                rows.append(Row(url, "url", res.state, res.note or (f"exit {res.code}" if res.code else ""), res.code or 0))
            except Exception as exc:                                   # one input's failure does not stop the others
                rows.append(Row(url, "url", "failed", f"{type(exc).__name__}: {str(exc)[:160]}", 1))
    if p.companies:
        queue_through = "apply" if through == "submit" else through
        if through == "submit":
            log("run: a company's jobs are never submitted from a batch; they are taken through apply and wait for your review")
        for key, ref in p.referrals.items():
            log(f"run: referral {ref.name} on {p.sources.get(key, 'the file')} is not attached by queue runs; "
                "use `jobagent apply` on that posting to attach it")
        mentioned = set(map(str.lower, p.companies))
        try:
            if discover is None:
                from jobagent.tools import discovery
                discover = lambda names: discovery.discover_many(names, log=log, only_us=True, judge="model")  # noqa: E731
            found = discover(p.companies)
            names = set(p.companies)
            for r in found:
                names.add(r.company)
                where = p.sources.get(r.company.lower() or "", "")
                note = f"{r.counts.get('queued', 0)} queued, {r.counts.get('needs_review', 0)} to review, {r.listings} listings"
                if getattr(r, "note", "") and not r.listings:
                    rows.append(Row(r.company, "company", "failed", f"discovery: {r.note[:140]}" + (f" ({where})" if where else ""), 1))
                else:
                    rows.append(Row(r.company, "company", "discovered", note + (f" ({where})" if where else "") +
                                    (f"; {r.note[:80]}" if getattr(r, "note", "") else "")))
            mentioned |= {n.lower() for n in names}
        except Exception as exc:
            rows.append(Row(", ".join(p.companies), "company", "failed", f"discovery: {type(exc).__name__}: {str(exc)[:160]}", 1))
        else:
            options = {k: v for k, v in pipeline_options.items() if k not in URL_ONLY}
            slugs = tuple(sorted({slug(n) for n in names}))
            results: list[dict] = []
            if work_queue is None:
                from jobagent import db
                from jobagent.config import get_settings
                from jobagent.tools.queue_runner import run_queue
                from jobagent.tools.scrape import _slug

                def work_queue(t, n, *, companies, options, results):          # noqa: E731
                    return run_queue(db.connect(), through=t, limit=n, company=None, companies=companies, pipeline_options=options,
                                     results=results, outputs_dir=get_settings().outputs_dir, slug_of=_slug, log=log,
                                     browser_mode=options.get("browser_mode", "docker"))
            try:
                done = work_queue(queue_through, limit, companies=slugs, options=options, results=results)
                for r in results:
                    rows.append(Row(r["url"], "job", r["state"], f"{r['company']}: {r['note']}"[:140], r["code"]))
                short = [r for r in results if r["code"] or r["state"] in STOPPED]
                rows.append(Row("(queue)", "queue", "partial" if short else "worked",
                                f"{done} of {len(results)} job(s) reached {queue_through}" + (f"; {len(short)} stopped short" if short else ""),
                                1 if short else 0))
            except Exception as exc:
                rows.append(Row("(queue)", "queue", "failed", f"{type(exc).__name__}: {str(exc)[:160]}", 1))
    return rows
