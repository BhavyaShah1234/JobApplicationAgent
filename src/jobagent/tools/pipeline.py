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

    def _posting(self, url):
        from jobagent import db
        return db.job_for_urls(self.conn, url)

    def _directory(self, row):
        from jobagent.tools.scrape import _slug
        return (self.settings.outputs_dir / _slug(row["company"] or "unknown") / str(row["job_id"] or "")).resolve()

    def run(self, url, *, through="apply", browser_mode="docker", allow_desktop_submit=False, force=False, no_ats=False,
            filling_strategy=None):
        from jobagent.tools import bundle
        if through not in {"scrape", "tailor", "apply", "submit"}:
            raise ValueError("through must be scrape, tailor, apply or submit")
        from jobagent.tools.session import check_mode
        check_mode(browser_mode, submitting=through == "submit", allow_desktop_submit=allow_desktop_submit)
        if filling_strategy is not None:
            from jobagent.filling import select_strategy
            filling_strategy=select_strategy(filling_strategy)
        root = (self.settings.state_dir / "workflows" / hashlib.sha256(url.encode()).hexdigest()[:24]).resolve()
        with bundle.job_lock(root, "pipeline"):
            return self._run(url, through, browser_mode, allow_desktop_submit, force, root, no_ats, filling_strategy)

    def _run(self, url, through, browser_mode, allow_desktop_submit, force, root, no_ats, filling_strategy=None):
        from jobagent.tools import research, places
        from jobagent.tools.queue_runner import outcome_of_apply, fresh_result
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
            return PipelineResult(row["status"] if row is not None else "failed", data["job_dir"], data["code"] or 1,
                                  row["notes"] if row is not None else "scrape did not produce a posting")
        d = Path(data["job_dir"])
        current = W.Workflow(d)
        current.data["stages"]["scrape"] = wf.stage("scrape")
        current.save()
        if through == "scrape":
            return PipelineResult("scraped", str(d))
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
            return PipelineResult("failed", str(d), result["code"], "research failed")
        # Resolve derived metadata BEFORE the writer fingerprints jd.json. Otherwise
        # apply.context() adds it afterwards and a retry needlessly rebuilds valid PDFs.
        posting_outputs = [str(d / "jd.json")] + ([str(d / "jd.md")] if (d / "jd.md").exists() else [])
        current.run("locations", lambda: {"places": places.resolve_job(d, slug, log=self.log)},
                    inputs={"source": source_sha, "profile": W.sha_file(Path(profiles[0]))},
                    outputs=posting_outputs, force=force, log=self.log)
        # The bootstrap cache tracks the posting after this owned enrichment step;
        # the locations stage separately records its inputs and resulting artifacts.
        wf.stage("scrape")["outputs"] = {str(d / n): W.sha_file(d / n) for n in ("jd.json", "jd.txt")}
        wf.save()
        current.data["stages"]["scrape"] = wf.stage("scrape")
        current.save()
        code = self.invoke("tailor", job_dir=str(d), company=slug, force=force, no_ats=no_ats)
        if code:
            return PipelineResult("failed", str(d), code, "document generation or document gate failed")
        if through == "tailor":
            return PipelineResult("tailored", str(d))
        stage = "submit" if through == "submit" else "apply"
        kwargs = {"job_dir": str(d), "company": slug, "browser_mode": browser_mode}
        if filling_strategy is not None:kwargs['filling_strategy']=filling_strategy
        if stage == "submit":
            kwargs["allow_desktop_submit"] = allow_desktop_submit
        started = time.time()
        code = self.invoke(stage, **kwargs)
        result = fresh_result(d, started)
        if stage == "submit":
            sent = (result or {}).get("submission", {})
            return PipelineResult(sent.get("state", "failed"), str(d), code, sent.get("message", ""))
        out = outcome_of_apply(result, code)
        return PipelineResult(*(out[:1] if out else ("unavailable",)), job_dir=str(d), code=code,
                              note=out[1] if out else "posting unavailable")
