"""F05: the result of generating a job's documents, and the gate that keeps a failed build out of the application.

`documents.json` in the job folder is the typed result of one tailor run. It separates two questions:

  * VALIDITY - is there a current, compiled, one-page resume and cover letter with no unresolved hard finding (layout,
    page count, claims policy, consistency)? If not, nothing may be filled or sent: `blockers`.
  * ATS TARGET - did the resume reach the checkers' target? That is a separate, explicit policy (`ats_shortfall` in the
    settings: "warn" lets a valid below-target resume proceed with a warning in the review packet, "block" does not).

The record names the PDFs it describes by SHA-256, so a stale PDF from an earlier run, or one edited afterwards, cannot pass
as the current successful build.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

NAME = "documents.json"
STALE = ("resume.pdf", "resume.tex", "filled.json", "cover_letter.pdf", "cover_letter.tex", NAME)
EXIT_OK, EXIT_INVALID, EXIT_BELOW_TARGET = 0, 1, 8


def _fingerprint(job_dir: Path) -> str:
    from jobagent.tools import ats_result
    return ats_result.identity(job_dir)[1]


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def reset(job_dir: Path, names=STALE) -> None:
    """Remove the previous build's outputs before a new one starts, so that whatever is on disk afterwards was made by it."""
    for name in names:
        (Path(job_dir) / name).unlink(missing_ok=True)


def begin(job_dir: Path) -> None:
    """Start a build, fail closed: the previous outputs are removed and, BEFORE anything new is generated, an in-progress
    record replaces the manifest. A build that raises, is killed or is interrupted leaves this record behind, so a draft PDF
    it managed to write can never pass for a successful build; only `save` of a finished assessment replaces it."""
    reset(job_dir, tuple(n for n in STALE if n != NAME))
    save(job_dir, {"version": 1, "status": "building", "built_at": _stamp(), "valid": False, "ready": False,
                   "resume": {"valid": False, "pages": None, "sha256": None, "hard_issues": [], "claims_issues": [],
                              "build_findings": [], "failure": "a build is in progress or was interrupted"},
                   "cover_letter": {"valid": False, "pages": None, "sha256": None, "issues": [], "words": None},
                   "ats": {"status": "unscored", "average": None, "target": None, "complete": False, "missing": [], "policy": ""},
                   "estimates": [], "blockers": ["the build did not finish: run `jobagent tailor` again"], "warnings": []})


def begin_rewrite(job_dir: Path) -> None:
    """Enroll a rewrite of existing documents (the ATS loop, a regenerated cover letter) in the fail-closed lifecycle BEFORE it
    touches anything. Unlike `begin`, the documents it needs stay where they are, and what was known about them is kept:
    the manifest becomes `rewriting` (not valid, not ready) with the earlier record's findings carried along; a folder with
    no manifest (the legacy import) gets one, so a rewrite that then fails can no longer fall back to the legacy exception.
    A successful rewrite publishes a complete record through `refresh`; a failed or interrupted one leaves this behind."""
    job_dir = Path(job_dir)
    old = load(job_dir)
    if old is not None and old.get("status") in ("building", "corrupt"):
        return                                              # already failing closed
    if old is None:
        old = {"version": 1, "status": "legacy", "built_at": None, "valid": False, "ready": False,
               "resume": {"valid": False, "pages": None, "sha256": None, "hard_issues": [], "claims_issues": [],
                          "build_findings": [], "failure": None, "build_failure": None},
               "cover_letter": {"valid": False, "pages": None, "sha256": None, "issues": [], "words": None},
               "ats": {"status": "unscored", "average": None, "target": None, "complete": False, "missing": [], "policy": ""},
               "estimates": [], "blockers": [], "warnings": []}
    held = {**old, "status": "rewriting", "valid": False, "ready": False, "built_at": _stamp(),
            "rewrite_of": old.get("status"),
            "blockers": ["a rewrite of these documents is in progress or was interrupted: finish it or run `jobagent tailor`"]}
    save(job_dir, held)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def pdf_check(path: Path) -> tuple[int | None, str]:
    """(pages, problem): the file exists, is a non-empty PDF and pdfinfo can count its pages. problem is '' when fine."""
    path = Path(path)
    if not path.exists():
        return None, f"{path.name} does not exist"
    data = path.read_bytes()
    if not data.startswith(b"%PDF") or len(data) < 200:
        return None, f"{path.name} is not a valid PDF"
    info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True).stdout
    import re
    m = re.search(r"Pages:\s+(\d+)", info)
    if not m:
        return None, f"{path.name} cannot be read as a PDF"
    return int(m.group(1)), ""


def estimates(job_dir: Path) -> list[dict]:
    """Numbers the tailor estimated (not the candidate's own figures): the candidate confirms or replaces them at review."""
    p = Path(job_dir) / "filled.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except ValueError:
        return []
    out = []
    for role in data.get("roles", []):
        for b in role.get("bullets", []):
            if b.get("estimated"):
                out.append({"role": role.get("role"), "estimated": list(b["estimated"]), "bullet": b.get("text", "")})
    return out


def ats_status(average, complete: bool, target: float, scored: bool) -> str:
    if not scored:
        return "unscored"
    if average is None or not complete:
        return "incomplete"
    return "met" if average >= target else "below_target"


def assess(job_dir: Path, *, resume: dict, letter: dict | None, ats: dict, policy: str, target: float) -> dict:
    """Build the typed result. `resume`: {produced: bool, hard_issues, claims_issues, failure}; `letter`: the cover-letter
    report ({issues, words, ...}) or None when it was not written; `ats`: {average, complete, missing, scored}."""
    job_dir = Path(job_dir)
    blockers: list[str] = []
    rpages, rproblem = pdf_check(job_dir / "resume.pdf") if resume.get("produced") else (None, "no valid best version was produced")
    r_valid = resume.get("produced", False) and not rproblem and rpages == 1
    if resume.get("failure"):
        blockers.append(f"resume: {resume['failure']}")
    elif rproblem:
        blockers.append(f"resume: {rproblem}")
    elif rpages != 1:
        blockers.append(f"resume: {rpages} pages, it must be one")
    for h in resume.get("hard_issues", []):
        blockers.append(f"resume: {h}")
    for c in resume.get("claims_issues", []):
        blockers.append(f"resume claims: {c}")
    for b in resume.get("build_findings", []):
        blockers.append(f"resume: {b}")
    for b in resume.get("evidence_blockers", []):            # temporary: recomputed by every refresh, never carried forward
        blockers.append(f"resume: {b}")
    r_valid = r_valid and not resume.get("hard_issues") and not resume.get("claims_issues") and not resume.get("failure") \
        and not resume.get("build_findings") and not resume.get("evidence_blockers")
    if letter is None:
        lpages, lproblem, l_valid = None, "the cover letter was not written", False
        blockers.append("cover letter: not written")
    else:
        lpages, lproblem = pdf_check(job_dir / "cover_letter.pdf")
        l_valid = not lproblem and lpages == 1 and not letter.get("issues")
        if lproblem:
            blockers.append(f"cover letter: {lproblem}")
        elif lpages != 1:
            blockers.append(f"cover letter: {lpages} pages, it must be one")
        for i in letter.get("issues", []):
            blockers.append(f"cover letter: {i}")
    status = ats_status(ats.get("average"), ats.get("complete", False), target, ats.get("scored", False))
    warnings = []
    for c in resume.get("coverage_limited", []):             # F18: arithmetic limits of the page, not a defect of the build
        warnings.append(f"resume coverage: {c}")
    if status != "met":
        text = {"below_target": f"ATS average {ats.get('average')} is below the target {target}",
                "unscored": "the resume was not scored on the ATS checkers",
                "incomplete": f"the ATS average is incomplete (missing: {', '.join(ats.get('missing', [])) or 'sites'})"}[status]
        (blockers if policy == "block" else warnings).append(f"ATS: {text} (policy: {policy})")
    valid = r_valid and l_valid
    return {"version": 1, "status": "complete", "built_at": _stamp(),
            "resume": {"valid": bool(r_valid), "pages": rpages, "sha256": _sha(job_dir / "resume.pdf"),
                       "hard_issues": list(resume.get("hard_issues", [])), "claims_issues": list(resume.get("claims_issues", [])),
                       "build_findings": list(resume.get("build_findings", [])),
                       "failure": resume.get("failure") or (rproblem if rproblem else None),
                       "build_failure": resume.get("failure")},
            "cover_letter": {"valid": bool(l_valid), "pages": lpages, "sha256": _sha(job_dir / "cover_letter.pdf"),
                             "issues": list((letter or {}).get("issues", [])), "words": (letter or {}).get("words")},
            "ats": {"status": status, "average": ats.get("average"), "target": target, "complete": ats.get("complete", False),
                    "missing": list(ats.get("missing", [])), "policy": policy,
                    # the version this ATS result describes (a different PDF or job posting is a different result)
                    "resume_sha256": ats.get("resume_sha256", _sha(job_dir / "resume.pdf")),
                    "job_fingerprint": ats.get("job_fingerprint", _fingerprint(job_dir))},
            "estimates": estimates(job_dir), "valid": bool(valid), "ready": bool(valid and not blockers),
            "blockers": blockers, "warnings": warnings}


def save(job_dir: Path, result: dict) -> dict:
    """Write the manifest atomically (temp file, then rename): a reader never sees half of one."""
    import os
    path = Path(job_dir) / NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=2))
    os.replace(tmp, path)
    return result


_REQUIRED = {"version": int, "status": str, "valid": bool, "ready": bool, "resume": dict, "cover_letter": dict, "ats": dict,
             "blockers": list, "warnings": list, "estimates": list}


def load(job_dir: Path) -> dict | None:
    """The manifest, None when there genuinely is none (a folder from before F05). A manifest that exists but cannot be
    read, is not a JSON object or lacks the schema comes back as an INVALID record (status `corrupt`): a damaged
    record of a possibly failed build must never read as 'no record'."""
    p = Path(job_dir) / NAME
    if not p.exists():
        return None
    try:
        rec = json.loads(p.read_text())
        ok = isinstance(rec, dict) and all(isinstance(rec.get(k), typ) for k, typ in _REQUIRED.items()) and \
            all(isinstance(rec[k].get("sha256", ""), (str, type(None))) for k in ("resume", "cover_letter"))
    except (ValueError, OSError, AttributeError):
        ok = False
    if not ok:
        return {"version": 1, "status": "corrupt", "built_at": None, "valid": False, "ready": False,
                "resume": {"valid": False, "pages": None, "sha256": None, "hard_issues": [], "claims_issues": [],
                           "build_findings": [], "failure": "documents.json is unreadable or malformed"},
                "cover_letter": {"valid": False, "pages": None, "sha256": None, "issues": [], "words": None},
                "ats": {"status": "unscored", "average": None, "target": None, "complete": False, "missing": [], "policy": ""},
                "estimates": [], "warnings": [],
                "blockers": ["documents.json is unreadable or malformed: run `jobagent tailor` again"]}
    return rec


def exit_code(result: dict) -> int:
    """0 usable; 1 the documents are invalid (a failed build); 8 valid but the ATS policy is 'block' and the target is missed."""
    if not result["valid"]:
        return EXIT_INVALID
    return EXIT_OK if result["ready"] else EXIT_BELOW_TARGET


def verify(job_dir: Path, policy: str = "warn") -> dict:
    """Is what is in the job folder NOW a successful, current build? Returns {ok, reasons, warnings, record}. The record's
    PDFs must be byte-identical to the ones on disk (a stale or edited PDF is not the validated build). A folder without a
    record (made before F05) passes only if its PDFs are valid one-page files, with a warning that no build was recorded."""
    job_dir = Path(job_dir)
    rec = load(job_dir)
    reasons: list[str] = []
    warnings: list[str] = []
    if (job_dir / ".publish.json").exists():                 # F25: a resume version was being published when the process ended
        reasons.append("a resume publish was interrupted, so the files may belong to different versions: run `jobagent tailor` "
                       "again (it completes or replaces the version)")
    if rec is None:
        pages, problem = pdf_check(job_dir / "resume.pdf")
        if problem or pages != 1:
            reasons.append(f"resume: {problem or f'{pages} pages'} (and no build record: run `jobagent tailor`)")
        if (job_dir / "cover_letter.pdf").exists():
            lp, lproblem = pdf_check(job_dir / "cover_letter.pdf")
            if lproblem or lp != 1:
                reasons.append(f"cover letter: {lproblem or f'{lp} pages'}")
        warnings.append("no documents.json: these documents have no recorded validation")
        return {"ok": not reasons, "reasons": reasons, "warnings": warnings, "record": None}
    for key, name in (("resume", "resume.pdf"), ("cover_letter", "cover_letter.pdf")):
        if rec[key].get("sha256") != _sha(job_dir / name):
            reasons.append(f"{name} is not the file that was validated (missing, stale or edited since): re-run `jobagent tailor`"
                           " or `jobagent documents`")
    if rec.get("status") == "complete" and rec["ats"].get("job_fingerprint") not in (None, _fingerprint(job_dir)):
        reasons.append("the job posting changed since these documents were validated: run `jobagent documents`")
    if rec.get("status") not in ("complete",):
        reasons.append(f"the build record is '{rec.get('status')}', not a finished build")
    if not rec["valid"]:
        reasons += rec["blockers"] or ["the documents are not valid"]
    elif rec["ats"]["status"] != "met" and policy == "block":
        reasons.append(f"ATS: {rec['ats']['status']} (average {rec['ats']['average']}, target {rec['ats']['target']}; policy: block)")
    warnings += [w for w in rec.get("warnings", []) if not w.startswith("ATS")]
    if rec["valid"] and rec["ats"]["status"] != "met" and policy != "block":      # judged under the CURRENT policy, not the stored one
        warnings.append(f"ATS: {rec['ats']['status']} (average {rec['ats']['average']}, target {rec['ats']['target']}; policy: {policy})")
    return {"ok": not reasons, "reasons": reasons, "warnings": warnings, "record": rec}


def claims_from_filled(f) -> list[str]:
    """The owner-approved claims policy applied to a generated resume's content (both writers, and every refresh): the
    Summary may not claim more than the declared years of experience nor copy the posting's qualification wording."""
    from jobagent.steps import multi_agent as M
    from jobagent.tools import tailor as T
    return [c for c in M.summary_issues(T.to_plain(getattr(f, "summary", "") or ""), False)
            if "claims more than the declared" in c or "copies the posting" in c]


def resume_findings(job_dir: Path) -> dict:
    """Run the SAME hard and claims checks that rejected (or accepted) the build, on what is on disk now: the layout/page
    hard issues (`multi_agent.hard_issues`: not one page, project rules, blank band, empty skills lines) and the claims
    policy. `evidence` is False when they cannot be run (no valid filled.json or no PDF): the caller then keeps what it knew."""
    job_dir = Path(job_dir)
    from jobagent.steps import multi_agent as M
    from jobagent.tools import tailor as T
    try:
        f = T.Filled.model_validate_json((job_dir / "filled.json").read_text())
    except (OSError, ValueError):
        return {"evidence": False, "reason": "filled.json is missing or invalid", "hard": [], "claims": []}
    if not (job_dir / "resume.pdf").exists():
        return {"evidence": False, "reason": "resume.pdf is missing", "hard": [], "claims": claims_from_filled(f)}
    from jobagent.tools import skillgraph
    return {"evidence": True, "reason": "", "hard": M.hard_issues({"job_dir": job_dir}, f),
            "claims": claims_from_filled(f) + skillgraph.violations(job_dir, f)}


def refresh(job_dir: Path, policy: str, target: float, letter: dict | None = None) -> dict | None:
    """Re-validate the files on disk after something else changed them (the ATS loop, a hand edit, a rewritten letter) with
    the same checks as the build, and publish a complete record.
      * hard/claims findings: cleared only when their own validator passes now; when the evidence to run them is missing the
        earlier findings stay AND a temporary evidence blocker is added, which is recomputed on every refresh (it goes away
        when the evidence returns and the validators pass);
      * build-time findings (a failed build, locked parts, uncovered duties) are permanent until a new build;
      * the ATS status comes from the authoritative result for the CURRENT PDF and job (`ats_result.gather`), never from a
        cached summary or an earlier record of another version;
      * a build that never finished (`building`, `corrupt`) is left alone.
    Returns the new record, or None when there was no record at all."""
    old = load(job_dir)
    if old is None:
        return None
    job_dir = Path(job_dir)
    if old.get("status") not in ("complete", "rewriting"):
        return old
    from jobagent.tools import ats_result
    g = ats_result.gather(job_dir)
    ats = {"average": g["average"], "complete": g["complete"], "missing": g["missing"], "scored": bool(g["scores"]),
           "resume_sha256": g["resume_sha256"], "job_fingerprint": g["job_fingerprint"]}
    found = resume_findings(job_dir)
    old_resume = old["resume"]
    build = list(old_resume.get("build_findings", []))
    if old_resume.get("build_failure"):
        build.append(old_resume["build_failure"])            # a failed build stays failed
    evidence_blockers: list[str] = []
    if found["evidence"]:
        hard, claims = found["hard"], found["claims"]
    else:
        hard, claims = list(old_resume.get("hard_issues", [])), list(old_resume.get("claims_issues", []))
        evidence_blockers.append(f"the hard and claims checks could not be re-run ({found['reason']}); earlier findings kept")
    if letter is None:
        had = old.get("cover_letter", {})
        letter = {"issues": had.get("issues", []), "words": had.get("words")} if (job_dir / "cover_letter.pdf").exists() else None
    new = assess(job_dir, resume={"produced": (job_dir / "resume.pdf").exists(), "hard_issues": hard, "claims_issues": claims,
                                  "build_findings": list(dict.fromkeys(build)), "evidence_blockers": evidence_blockers},
                 letter=letter, ats=ats, policy=policy, target=target)
    return save(job_dir, new)
