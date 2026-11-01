"""Model experiment: every Voyager chat model writes the same job's resume with the same harness (step 9's 5 tailoring
rounds, then step 10's ATS loop: 5 rounds, target 90) to see which reaches the best average ATS score.

    jobagent model-experiment outputs/<company>/<job-id> <company-slug> [--model m ...] [--workers 4]

Each model gets its own copy of the job folder under outputs/experiments/<stamp>/<model>/. All models are scored on
the same unlimited online checkers (ATS Friendly's 2 scans a week can't cover every model, so it is left out). The
writing model is swapped per worker thread (llm.WRITER_MODEL); scoring takes turns on the one browser profile.
results.md / results.json are rewritten as each model finishes.
"""

import json
import shutil
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock

import httpx

from jobagent import llm
from jobagent.clock import now
from jobagent.config import get_settings
from jobagent.tools import ats_sites

# Not resume writers: the router's intent classifier, glm-5-3's speculative-decoding draft model ("not meant for
# direct use") and glm-5-3 with speculative decoding (same model as glm-5-3)
SKIP = {"router-classifier-v2", "north-mini-code-glm", "glm-5-3-spec"}
SITES = tuple(x for x in ats_sites.DEFAULT_SITES if x not in ats_sites.BUDGET_SITES)


def chat_models() -> list[str]:
    """Voyager's chat models that can return structured output (function calling or a response schema)."""
    s = get_settings()
    data = httpx.get(f"{s.openai_base_url}/models", timeout=30,
                     headers={"Authorization": f"Bearer {s.openai_api_key.get_secret_value()}"}).json()["data"]
    return sorted(m["id"] for m in data if m.get("mode") == "chat" and m["id"] not in SKIP
                  and (m.get("supports_function_calling") or m.get("supports_response_schema")))


def run_one(model: str, src: Path, root: Path, slug: str, target: float) -> dict:
    """One model on one job, in root/<job>/<model>/; never raises (a failure is a result)."""
    from jobagent.steps import tailor

    job = src.parent.name
    d = root / job / model
    d.mkdir(parents=True, exist_ok=True)
    for name in ("jd.json", "jd.txt", "jd.md"):
        if (src / name).exists():
            shutil.copy(src / name, d / name)
    logf = (d / "run.log").open("a")
    log = lambda m: (logf.write(f"{now():%H:%M:%S} {m}\n"), logf.flush())
    llm.WRITER_MODEL.set(model)
    t0 = time.monotonic()
    out = {"model": model, "job": job, "dir": str(d)}
    try:
        res = tailor.main(str(d), slug, rediscover=False, ats=True, ats_sites=SITES, log=log, letter=False)
        ats = res.get("ats") or {}
        out.update(status="ok", pages=res.get("pages"), average=ats.get("average"), scores=ats.get("scores", {}),
                   history=ats.get("history", []), reached=ats.get("reached", False),
                   bullet_issues=len(res.get("bullet_issues", [])), uncovered=len(res.get("uncovered", [])),
                   layout_issues=res.get("layout", []))
    except Exception as e:
        log(traceback.format_exc())
        out.update(status=f"failed: {type(e).__name__}: {str(e)[:200]}")
    out["minutes"] = round((time.monotonic() - t0) / 60, 1)
    log(f"result: {json.dumps(out)}")
    logf.close()
    return out


def run_model(model: str, jobs: list[tuple[Path, str]], root: Path, target: float) -> list[dict]:
    """One model on every job, one after another (its worker thread keeps the model)."""
    return [run_one(model, src, root, slug, target) for src, slug in jobs]


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def write_results(root: Path, results: list[dict], target: float, jobs: list[str]) -> None:
    for r in results:  # the resume must be one page: anything else doesn't count
        if r.get("status") == "ok" and r.get("pages") != 1:
            r["status"], r["disqualified_average"], r["average"] = f"disqualified: {r.get('pages')} pages", \
                r.get("average"), None
    (root / "results.json").write_text(json.dumps(results, indent=1))
    models = sorted({r["model"] for r in results})
    rows = []
    for m in models:
        rs = {r["job"]: r for r in results if r["model"] == m}
        ok = [r for r in rs.values() if r.get("average") is not None]
        rows.append({"model": m, "avg": _mean([r["average"] for r in ok]) if len(ok) == len(jobs) else None,
                     "done": len(ok), "per_job": {j: rs.get(j, {}).get("average") for j in jobs},
                     "sites": {x: _mean([r["scores"].get(x) for r in ok]) for x in SITES},
                     "layout": _mean([len(r.get("layout_issues") or []) for r in ok]),
                     "minutes": round(sum(r.get("minutes", 0) for r in rs.values()), 1)})
    rows.sort(key=lambda x: (x["avg"] is None, -(x["avg"] or 0), -x["done"]))
    lines = [f"# Model experiment: {', '.join(jobs)}", "",
             f"Same harness for every model and job: 5 tailoring rounds (with the layout validator), then the ATS loop "
             f"(5 rounds, target {target}) on {', '.join(SITES)} (HireFlow averaged over "
             f"{ats_sites.REPEATS.get('hireflow', 1)} runs). Updated {now():%Y-%m-%d %H:%M} America/Phoenix.", "",
             "## Summary (average over all jobs; a model counts only when every job produced a one-page resume)", "",
             "| Rank | Model | Average | " + " | ".join(jobs) + " | " + " | ".join(SITES) + " | Layout issues | Minutes |",
             "|---|---|---|" + "---|" * (len(jobs) + len(SITES)) + "---|---|"]
    for i, x in enumerate(rows, 1):
        lines.append(f"| {i} | {x['model']} | {x['avg'] if x['avg'] is not None else '-'} "
                     f"({x['done']}/{len(jobs)}) | " + " | ".join(str(x["per_job"][j] or "-") for j in jobs) + " | "
                     + " | ".join(str(x["sites"][s] or "-") for s in SITES) + f" | {x['layout'] if x['layout'] is not None else '-'} | {x['minutes']} |")
    best = {x: max((r["scores"].get(x) or 0 for r in results if r.get("average") is not None), default=None) for x in SITES}
    lines += ["", "Best single score per site: " + ", ".join(f"{k} {v}" for k, v in best.items()), ""]
    for j in jobs:
        lines += [f"## {j}", "", "| Model | Average | Start → end | " + " | ".join(SITES) + " | Pages | Layout | Minutes | Status |",
                  "|---|---|---|" + "---|" * len(SITES) + "---|---|---|---|"]
        for r in sorted([r for r in results if r["job"] == j], key=lambda r: (r.get("average") is None, -(r.get("average") or 0))):
            sc, hist = r.get("scores") or {}, r.get("history") or []
            lines.append(f"| {r['model']} | {r.get('average', '-')}{' ✓' if r.get('reached') else ''} | "
                         f"{' → '.join(map(str, hist[:1] + hist[-1:])) if hist else '-'} | "
                         + " | ".join(str(sc.get(x, "-")) for x in SITES)
                         + f" | {r.get('pages', '-')} | {len(r.get('layout_issues') or [])} | {r.get('minutes', '-')} | "
                         f"{r.get('status', 'running')[:80]} |")
        lines.append("")
    (root / "results.md").write_text("\n".join(lines) + "\n")


def run(jobs: list[tuple[str | Path, str]], models: list[str] | None = None, workers: int = 4, target: float = 90.0,
        log=print) -> Path:
    """jobs: (outputs/<company>/<job-id>, company slug) pairs; every model tailors every job."""
    s = get_settings()
    jobs = [(Path(d) if Path(d).is_absolute() else Path("/app") / d, slug) for d, slug in jobs]
    names = [src.parent.name for src, _ in jobs]
    models = models or chat_models()
    root = s.outputs_dir / "experiments" / f"{datetime.now():%Y%m%d-%H%M}-{'-'.join(names)}"
    root.mkdir(parents=True, exist_ok=True)
    log(f"model experiment: {len(models)} models x {len(jobs)} jobs, {workers} workers -> {root}")
    results, lock = [], Lock()
    with ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(run_model, m, jobs, root, target): m for m in models}
        for fut in as_completed(futs):
            rs = fut.result()
            with lock:
                results += rs
                write_results(root, results, target, names)
            done = len({r["model"] for r in results})
            log(f"{futs[fut]}: " + "; ".join(f"{r['job']} {r.get('average', '-')} {r.get('scores', '')} {r['status'][:40]}"
                                              for r in rs) + f" ({sum(r['minutes'] for r in rs):.0f} min) [{done}/{len(models)}]")
    return root
