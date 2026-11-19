"""One in-process, stage-resumable posting workflow, shared by `run` and the leased queue.

The command callbacks remain the single implementations of each audited stage. Click contexts supply their defaults and
release their locks in this process; no CLI subprocess, shell, or nested CliRunner is involved. Submission retains its own
review/approval capability and reconciliation gate and is never enabled by the queue.
"""
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import time

from jobagent.tools import workflow as W


@dataclass
class PipelineResult:
    state: str
    job_dir: str = ""
    code: int = 0
    note: str = ""
    stages: dict = field(default_factory=dict)


class Pipeline:
    def __init__(self, conn=None, settings=None, log=print, invoke=None):
        from jobagent import db
        from jobagent.config import get_settings
        self.conn = conn if conn is not None else db.connect()
        self.settings = settings or get_settings()
        self.log, self.invoke = log, invoke or self._invoke

    @staticmethod
    def _invoke(stage, **values):
        import click
        import typer
        from typer.main import get_command
        from jobagent.main import app
        command = get_command(app).commands[stage]
        try:
            with command.context_class(command) as ctx:
                defaults = {p.name: p.get_default(ctx) for p in command.params if p.name not in values}
                ctx.invoke(command.callback, **(defaults | values))
            return 0
        except (click.exceptions.Exit, typer.Exit) as exc:
            return exc.exit_code

    def _eligibility(self, d, row, ignored):
        """Screen the posting's hard requirements (citizenship, clearance, sponsorship) against the candidate's own file before any
        document or form work is spent. `blocked` stops the job as needs_review with the quoted sentence (override per posting with
        --ignore-eligibility; the queue never overrides); `review` is logged and recorded and the job continues."""
        from jobagent.tools import eligibility, jobqueue
        text = (d / "jd.txt").read_text() if (d / "jd.txt").exists() else ""
        verdict = eligibility.assess_job(d)
        import hashlib
        (d / "eligibility.json").write_text(json.dumps({"level": verdict.level, "reasons": verdict.reasons, "ignored": bool(ignored),
                                                      "jd_sha256": hashlib.sha256((d/'jd.txt').read_bytes() if (d/'jd.txt').exists() else b'').hexdigest()}, indent=1))
        if verdict.level == "ok":
            return None
        note = "eligibility: " + verdict.text()
        if verdict.level == "review" or ignored:
            self.log(f"pipeline: eligibility {verdict.level}{' (overridden for this posting)' if ignored else ''}: {verdict.text()}")
            return None
        jobqueue.mark(self.conn, row["url"], "needs_review", note[:400])
        self.log(f"pipeline: stopped before any document or form work: {note}")
        self.log("pipeline: apply anyway with --ignore-eligibility if you know better")
        return PipelineResult("needs_review", str(d), 4, note)

    def _posting(self, url):
        from jobagent import db
        return db.job_for_urls(self.conn, url)

    def _directory(self, row):
        from jobagent.tools.scrape import _slug
        return (self.settings.outputs_dir / _slug(row["company"] or "unknown") / str(row["job_id"] or "")).resolve()

    def _skill_graph(self, d, row) -> None:
        """Add this posting to the skill graph (step 4c); a posting read again with changed content replaces its old support. The graph
        never stops a run: any failure is one line in the log."""
        try:
            if not self.settings.skills_use_graph:
                return
            from jobagent.tools import matching, skillgraph as SG
            jd = json.loads((d / "jd.json").read_text())
            roles = matching.load_roles().get("roles", [])
            embed = SG.default_embed(self.settings)
            rid = (row["role_id"] if "role_id" in row.keys() and row["role_id"] else "") or SG.role_for(jd, roles, embed)
            if not rid:
                return
            titles = {r["id"]: r.get("title", "") for r in roles}
            res = SG.add_posting(jd, rid, self.settings, embed=embed, role_title=titles.get(rid, ""))
            if res and res["added_skills"]:
                self.log(f"skills: {len(res['added_skills'])} new skill(s) added to the graph")
        except Exception as exc:
            self.log(f"skills: graph not updated ({type(exc).__name__}: {str(exc)[:100]})")

    def run(self, url, *, through="apply", browser_mode="docker", allow_desktop_submit=False, force=False, no_ats=False,
            filling_strategy=None, ignore_eligibility=False, document_strategy=None, ats_refine=False):
        from jobagent.tools import bundle
        if through not in {"scrape", "tailor", "apply", "submit"}:
            raise ValueError("through must be scrape, tailor, apply or submit")
        from jobagent.tools.session import check_mode
        check_mode(browser_mode, submitting=through == "submit", allow_desktop_submit=allow_desktop_submit)
        if filling_strategy is not None:
            from jobagent.filling import select_strategy
            filling_strategy=select_strategy(filling_strategy)
        strategy = document_strategy or getattr(self.settings, 'document_strategy', 'current')
        if strategy not in ('current','incremental'):
            raise ValueError('document-strategy must be current or incremental')
        if ats_refine and (strategy != 'incremental' or no_ats):
            raise ValueError('--ats-refine requires incremental documents with ATS enabled')
        root = (self.settings.state_dir / "workflows" / hashlib.sha256(url.encode()).hexdigest()[:24]).resolve()
        from jobagent.progress import execution
        with bundle.job_lock(root, "pipeline"), execution(new=True, url=url):
            try:
                return self._run(url, through, browser_mode, allow_desktop_submit, force, root, no_ats, filling_strategy, ignore_eligibility, document_strategy, ats_refine)
            except BaseException as exc:
                from jobagent.progress import metadata
                from jobagent.log import progress
                progress(self.log, metadata().get('stage', 'scrape'), 'failed', detail=type(exc).__name__)
                raise

    def _run(self, url, through, browser_mode, allow_desktop_submit, force, root, no_ats, filling_strategy=None, ignore_eligibility=False, document_strategy=None, ats_refine=False):
        from jobagent.tools import research, places
        from jobagent.tools.queue_runner import outcome_of_apply, fresh_result
        from jobagent.log import progress
        plan = {"scrape": ["scrape"], "tailor": ["scrape", "research", "documents"], "apply": ["scrape", "research", "documents", "apply"],
                "submit": ["scrape", "research", "documents", "submit"]}[through]
        meta = {"job": "", "title": "", "company": ""}

        def P(stage, status="running", **kw):                      # where this job is, for the dashboard (run log only)
            from jobagent.progress import update
            update(stage=stage, plan=plan, url=url, **meta)
            progress(self.log, stage, status, plan=plan, url=url, **meta, **kw)
        P("scrape")
        wf = W.Workflow(root)
        inputs = {"url": url}
        old = wf.stage("scrape") or {}
        outputs = list(old.get("outputs", {}))
        if not force and outputs and wf.reusable("scrape", inputs, outputs):
            data = old["result"]
            self.log("pipeline: reused scrape; posting artifacts unchanged")
        else:
            def scrape():
                code = self.invoke("scrape", urls=[url], workers=1, refresh=force)
                row = self._posting(url)
                return {"code": code, "job_dir": str(self._directory(row)) if row is not None and row["job_id"] else ""}
            data, _ = wf.run("scrape", scrape, inputs=inputs, force=True, log=self.log,
                             check=lambda r: f"scrape exited {r['code']}" if r["code"] else None)
            if data["code"] == 0 and data["job_dir"]:
                d = Path(data["job_dir"])
                wf.stage("scrape")["outputs"] = {str(d / n): W.sha_file(d / n) for n in ("jd.json", "jd.txt")}
                wf.save()
        row = self._posting(url)
        if data["code"] or row is None or not data["job_dir"]:
            P("scrape", "failed", detail=(row["notes"] if row is not None else "scrape did not produce a posting") or "")
            return PipelineResult(row["status"] if row is not None else "failed", data["job_dir"], data["code"] or 1,
                                  row["notes"] if row is not None else "scrape did not produce a posting")
        d = Path(data["job_dir"])
        from jobagent.progress import update
        update(job_dir=str(d.resolve()))
        meta.update(job=d.name, title=row["title"] or "", company=row["company"] or "")
        current = W.Workflow(d)
        current.data["stages"]["scrape"] = wf.stage("scrape")
        current.save()
        P('scrape', 'running', label='Updating skills graph')
        self._skill_graph(d, row)
        P('scrape', 'done')
        if through == "scrape":
            return PipelineResult("scraped", str(d))
        P('research', 'running', label='Eligibility and company research')
        stop = self._eligibility(d, row, ignore_eligibility)
        if stop is not None:
            P("research", "blocked", detail=f"eligibility: {stop.note}"[:200])
            return stop
        company = row["company"]
        slug = research.slug(company)
        source = json.loads((d / "jd.json").read_text())
        source.pop("locations_resolved", None)
        source_sha = W.fingerprint(source)
        profiles = [str((self.settings.state_dir / "companies" / (slug + ext)).resolve()) for ext in (".json", ".md")]
        result, _ = current.run("research", lambda: {"code": self.invoke("research", companies=[company], workers=1)},
                                inputs={"company": company, "jd": source_sha}, outputs=profiles,
                                force=force, log=self.log, check=lambda r: "research failed" if r["code"] else None)
        if result["code"]:
            P("research", "failed", detail="research failed")
            return PipelineResult("failed", str(d), result["code"], "research failed")
        # Resolve derived metadata BEFORE the writer fingerprints jd.json. Otherwise
        # apply.context() adds it afterwards and a retry needlessly rebuilds valid PDFs.
        posting_outputs = [str(d / "jd.json")] + ([str(d / "jd.md")] if (d / "jd.md").exists() else [])
        current.run("locations", lambda: {"places": places.resolve_job(d, slug, log=self.log)},
                    inputs={"source": source_sha, "profile": W.sha_file(Path(profiles[0])),
                            "resolver_version": places.RESOLVER_VERSION},
                    outputs=posting_outputs, force=force, log=self.log)
        # The bootstrap cache tracks the posting after this owned enrichment step;
        # the locations stage separately records its inputs and resulting artifacts.
        wf.stage("scrape")["outputs"] = {str(d / n): W.sha_file(d / n) for n in ("jd.json", "jd.txt")}
        wf.save()
        current.data["stages"]["scrape"] = wf.stage("scrape")
        current.save()
        P("research", "done")
        P("documents", "running", label="Resume and cover letter")
        document_options = {}
        if document_strategy is not None: document_options['document_strategy'] = document_strategy
        if ats_refine: document_options['ats_refine'] = True
        code = self.invoke("tailor", job_dir=str(d), company=slug, force=force, no_ats=no_ats, **document_options)
        if code:
            P("documents", "failed", detail="document generation or document gate failed")
            return PipelineResult("failed", str(d), code, "document generation or document gate failed")
        P("documents", "done")
        if through == "tailor":
            return PipelineResult("tailored", str(d))
        stage = "submit" if through == "submit" else "apply"
        kwargs = {"job_dir": str(d), "company": slug, "browser_mode": browser_mode}
        if filling_strategy is not None:kwargs['filling_strategy']=filling_strategy
        if stage == "submit":
            kwargs["allow_desktop_submit"] = allow_desktop_submit
        started = time.time()
        P(stage, "running", label="Filling the form" if stage == "apply" else "Submitting")
        code = self.invoke(stage, **kwargs)
        result = fresh_result(d, started)
        if stage == "submit":
            sent = (result or {}).get("submission", {})
            P(stage, 'done' if sent.get('state') in ('submitted', 'confirmed') else 'blocked', detail=sent.get('state', 'failed'))
            return PipelineResult(sent.get("state", "failed"), str(d), code, sent.get("message", ""))
        out = outcome_of_apply(result, code)
        final = PipelineResult(*(out[:1] if out else ("unavailable",)), job_dir=str(d), code=code, note=out[1] if out else "posting unavailable")
        P(stage, "done" if final.state in ("filled", "review", "ready", "submitted", "confirmed") else
          "blocked" if final.state in ("blocked", "needs_review", "unavailable", "closed") else "failed", detail=f"{final.state} {final.note}"[:200])
        return final
