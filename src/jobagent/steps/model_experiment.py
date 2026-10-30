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
    """One model end to end in its own job folder; never raises (a failure is a result)."""
    from jobagent.steps import tailor

    d = root / model
    d.mkdir(parents=True, exist_ok=True)
    for name in ("jd.json", "jd.txt", "jd.md"):
        if (src / name).exists():
            shutil.copy(src / name, d / name)
    logf = (d / "run.log").open("a")
    log = lambda m: (logf.write(f"{now():%H:%M:%S} {m}\n"), logf.flush())
    llm.WRITER_MODEL.set(model)
    t0 = time.monotonic()
    out = {"model": model, "dir": str(d)}
    try:
        res = tailor.main(str(d), slug, rediscover=False, ats=True, ats_sites=SITES, log=log, letter=False)
        ats = res.get("ats") or {}
        out.update(status="ok", pages=res.get("pages"), average=ats.get("average"), scores=ats.get("scores", {}),
                   history=ats.get("history", []), reached=ats.get("reached", False),
                   bullet_issues=len(res.get("bullet_issues", [])), uncovered=len(res.get("uncovered", [])))
    except Exception as e:
        log(traceback.format_exc())
        out.update(status=f"failed: {type(e).__name__}: {str(e)[:200]}")
    out["minutes"] = round((time.monotonic() - t0) / 60, 1)
    log(f"result: {json.dumps(out)}")
    logf.close()
    return out


def write_results(root: Path, results: list[dict], target: float, job: str) -> None:
    for r in results:  # the resume must be one page: anything else doesn't count
        if r.get("status") == "ok" and r.get("pages") != 1:
            r["status"], r["disqualified_average"], r["average"] = f"disqualified: {r.get('pages')} pages", \
                r.get("average"), None
    ranked = sorted(results, key=lambda r: (r.get("average") is None, -(r.get("average") or 0)))
    (root / "results.json").write_text(json.dumps(ranked, indent=1))
    lines = [f"# Model experiment: {job}", "",
             f"Same harness for every model: 5 tailoring rounds, then the ATS loop (5 rounds, target {target}) on "
             f"{', '.join(SITES)} (HireFlow averaged over {ats_sites.REPEATS.get('hireflow', 1)} runs). "
             f"Updated {now():%Y-%m-%d %H:%M} America/Phoenix.", "",
             "| Rank | Model | Average | Start → end | " + " | ".join(SITES) + " | Pages | Minutes | Status |",
             "|---|---|---|---|" + "---|" * len(SITES) + "---|---|---|"]
    for i, r in enumerate(ranked, 1):
        sc = r.get("scores") or {}
        hist = r.get("history") or []
        lines.append(f"| {i} | {r['model']} | {r.get('average', '-')}{' ✓' if r.get('reached') else ''} | "
                     f"{' → '.join(map(str, hist[:1] + hist[-1:])) if hist else '-'} | "
                     + " | ".join(str(sc.get(x, "-")) for x in SITES)
                     + f" | {r.get('pages', '-')} | {r.get('minutes', '-')} | {r.get('status', 'running')} |")
    (root / "results.md").write_text("\n".join(lines) + "\n")


def run(job_dir: str | Path, slug: str, models: list[str] | None = None, workers: int = 4, target: float = 90.0,
        log=print) -> Path:
    s = get_settings()
    src = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    models = models or chat_models()
    root = s.outputs_dir / "experiments" / f"{datetime.now():%Y%m%d-%H%M}-{src.parent.name}"
    root.mkdir(parents=True, exist_ok=True)
    log(f"model experiment: {len(models)} models, {workers} workers -> {root}")
    results, lock = [], Lock()
    with ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(run_one, m, src, root, slug, target): m for m in models}
        for fut in as_completed(futs):
            r = fut.result()
            with lock:
                results.append(r)
                write_results(root, results, target, src.parent.name + "/" + src.name)
            log(f"{r['model']}: {r.get('average', '-')} {r.get('scores', '')} {r['status']} ({r['minutes']} min) "
                f"[{len(results)}/{len(models)}]")
    return root
