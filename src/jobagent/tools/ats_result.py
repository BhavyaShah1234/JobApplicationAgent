"""F06: the one authoritative ATS result for a resume version.

A score describes one PDF (SHA-256) for one job (fingerprint of the JD text, title and company). The result for a job is
gathered from `ats_runs` by that exact version identity, not by round: the required sites may have been scored in different
rounds (limited-credit sites late, unlimited ones earlier), and a late round may hold only some of them. Rows that belong to
another PDF or another job posting are never mixed in. A required site with no score for this version leaves the average
`null` (the denominator is never reduced). The review line, the saved `ats_summary.json` and the learning snapshot all read
this result.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from jobagent.tools import ats_sites


def sha256_file(path: Path) -> str | None:
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def identity(job_dir: Path) -> tuple[str | None, str]:
    """(SHA-256 of the job's resume.pdf, fingerprint of its JD text, title and company)."""
    job_dir = Path(job_dir)
    try:
        jd = json.loads((job_dir / "jd.json").read_text())
    except (OSError, ValueError):
        jd = {}
    txt = job_dir / "jd.txt"
    return sha256_file(job_dir / "resume.pdf"), ats_sites.job_fingerprint(txt.read_text() if txt.exists() else "",
                                                                          jd.get("title") or "", jd.get("company") or "")


def _keys(job_dir: Path) -> tuple[str, ...]:
    # rows carry the canonical key (db.job_key); older spellings (an absolute /app/outputs/... path) are still found
    from jobagent import db
    return tuple(dict.fromkeys((*db.job_key_aliases(job_dir),)))


def row_fingerprint(raw: dict) -> str | None:
    """The job identity a recorded row names: top level (every row since F06, manual ones too) or an adapter's breakdown."""
    return raw.get("job_fingerprint") or (raw.get("breakdown") or {}).get("job_fingerprint")


def gather(job_dir: Path | None, sites=ats_sites.AVERAGE_SITES, conn=None, fresh: dict | None = None,
           sha: str | None = None, fingerprint: str | None = None) -> dict:
    """The required sites' scores for ONE resume version and job posting, whatever rounds they came from. By default the
    version is the job folder's current PDF and JD; `sha`/`fingerprint` select another (the one that was submitted).
    `fresh`: scores just obtained for this very version (they win over recorded rows). A row counts only if it names the
    same PDF hash AND the same job fingerprint: a manual score is no exception (one recorded without a job identity is
    listed under `unidentified` and must be recorded again). Returns the summary (`average` null unless every required
    site has a score) plus `resume_sha256`, `job_fingerprint`, `provenance` (per site: row id, round, manual, or fresh),
    `excluded` (count of rows of this folder describing another PDF or job) and `unidentified`."""
    from jobagent import db
    cur_sha, cur_fp = identity(job_dir) if job_dir is not None else (None, "")
    sha = sha if sha is not None else cur_sha
    fingerprint = fingerprint if fingerprint is not None else cur_fp
    conn = conn or db.connect()
    required = ats_sites.numeric_sites(sites)
    scores: dict[str, float] = {}
    provenance: dict[str, dict] = {}
    excluded, unidentified = 0, []
    if sha is not None and job_dir is not None:
        marks = ",".join("?" * len(_keys(job_dir)))
        rows = conn.execute(f"SELECT id, site, round, score, raw, resume_sha FROM ats_runs WHERE job_dir IN ({marks}) "
                            "AND score IS NOT NULL ORDER BY id", _keys(job_dir)).fetchall()
        for r in rows:
            if r["site"] not in required or r["resume_sha"] != sha:
                if r["site"] in required:
                    excluded += 1
                continue
            try:
                raw = json.loads(r["raw"] or "{}")
            except ValueError:
                raw = {}
            row_fp = row_fingerprint(raw)
            if row_fp is None:
                if raw.get("manual"):
                    unidentified.append(r["site"])
                excluded += 1
                continue
            if row_fp != fingerprint:
                excluded += 1
                continue
            scores[r["site"]] = r["score"]                      # ordered by id: the latest matching row wins
            bd = raw.get("breakdown") or {}
            provenance[r["site"]] = {"row": r["id"], "round": r["round"], "manual": bool(raw.get("manual"))}
            for key, value in (("payload", bd.get("payload")), ("provider_version", bd.get("provider_version", bd.get("parser_version"))),
                               ("observations", len(bd.get("observations") or []))):
                if value:                                   # only what the row recorded (F20): older rows have none of it
                    provenance[r["site"]][key] = value
    provenance_prev = dict(provenance)
    for site, score in (fresh or {}).items():
        if site in required and isinstance(score, (int, float)) and not isinstance(score, bool):
            scores[site], provenance[site] = score, {"fresh": True}
            if (provenance_prev.get(site) or {}).get("payload"):
                provenance[site]["payload"] = provenance_prev[site]["payload"]
    summary = ats_sites.score_summary(scores, sites)
    truncated = {s: p["payload"] for s, p in provenance.items() if (p.get("payload") or {}).get("truncated")}
    summary.update({"resume_sha256": sha, "job_fingerprint": fingerprint, "provenance": provenance, "excluded": excluded,
                    "truncated_posting": truncated,          # F20: sites that scored only part of a long posting (what they got)
                    "unidentified": sorted(set(unidentified) - set(scores))})
    return summary


def scope_note(summary: dict) -> str:
    """Describe providers that evaluated a prefix rather than the whole posting."""
    parts = []
    for site, payload in summary.get('truncated_posting', {}).items():
        unit = 'words' if payload.get('word_limit') else 'chars'
        sent = payload.get('jd_words_sent' if unit == 'words' else 'jd_chars_sent', '?')
        total = payload.get('jd_words_total' if unit == 'words' else 'jd_chars_total', '?')
        parts.append(f'{site} {sent}/{total} {unit}')
    return 'partial JD scoring: ' + ', '.join(parts) if parts else ''


def line(job_dir: Path, conn=None) -> str:
    """The review packet's ATS line, from the authoritative result."""
    g = gather(job_dir, conn=conn)
    if not g["scores"]:
        return "no ATS scores recorded for this resume version"
    shown = json.dumps(g["scores"])
    scope = '; ' + scope_note(g) if g.get('truncated_posting') else ''
    if g["complete"]:
        return f"average {g['average']:.1f} over {len(g['sites'])} required sites {shown}" + scope
    return (f"average unavailable (null): {len(g['scores'])} of {len(g['sites'])} required sites scored this resume version "
            f"{shown}; missing {', '.join(g['missing'])}"
            + (f"; manual score(s) for {', '.join(g['unidentified'])} have no job identity: record again with `jobagent ats-record`"
               if g.get("unidentified") else "") + scope)


COVER = re.compile(r"(?i)cover")
RESUME = re.compile(r"(?i)resume|\bcv\b|curriculum|upload a file")


def attachment_roles(attachments: list[dict]) -> dict:
    """The submitted resume's and cover letter's identities, from the frozen attempt's attachments tied to their upload
    controls (by the control's own label). A role has a hash only when every control of that role held a readable file and
    they all held the same bytes; a control whose bytes could not be read, or two different files in one role, leave the
    role unverifiable (None). A file in one role is never taken as the other's."""
    out: dict[str, str | None] = {}
    for role, pat in (("resume", RESUME), ("cover_letter", COVER)):
        hashes, unreadable = set(), False
        for a in attachments:
            label = a.get("label", "")
            mine = pat.search(label) and not (role == "resume" and COVER.search(label))
            if not mine:
                continue
            if a.get("files") is None:
                unreadable = True
            else:
                hashes |= {f.get("sha256") for f in a["files"] if f.get("sha256")}
        out[role] = next(iter(hashes)) if len(hashes) == 1 and not unreadable else None
    return out
