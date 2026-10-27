"""Step 9: tailor the resume for one scraped job and write a review report.

    jobagent tailor outputs/<company>/<job-id> <company-slug>

Projects come from GitHub repo cards (tools/projects); Experience, Summary and Technical Skills from the JD
(tools/tailor), in up to 5 rounds that keep the best one-page result. Writes resume.tex/.pdf, filled.json (the
model's output) and changes.md (estimated metrics to confirm, where each duty went, projects and skills chosen),
then the matching cover letter (cover_letter.tex/.pdf).
"""

import re
import sys
import time
from pathlib import Path

from langchain_openai import ChatOpenAI

from jobagent.config import get_settings
from jobagent.state import JobDescription
from jobagent.tools import github
from jobagent.tools import projects as P
from jobagent.tools import tailor as T
from jobagent.tools.vault import open_vault


def report(r, jd, f, pages, problems, uncovered, missing, warn, rounds, filled_in=(), proj=None) -> str:
    lines = ["# Resume fill — review", "", f"- Pages: **{pages}**", f"- Rounds: {' → '.join(rounds)}",
             f"- Locked parts (header, section names, role subheadings, Education): "
             f"{'unchanged ✅' if not any('LOCKED' in p for p in problems) else 'CHANGED ❌'}",
             f"- JD duties covered: **{len(jd.responsibilities) - len(uncovered)}/{len(jd.responsibilities)}**"
             + (f" (missing: {uncovered})" if uncovered else ""),
             f"- JD tech-stack items not found verbatim anywhere: {', '.join(missing) or 'none'}", ""]
    dups = T.duplicates(r, f) + T.quality(r, jd, f)
    if problems or warn or dups:
        lines += ["## Problems", ""] + [f"- {p}" for p in problems + warn + dups] + [""]
    ph = [b.text for e in f.roles for b in e.bullets if T.PLACEHOLDER.search(b.text)]
    if ph:
        lines += ["## Placeholders left unfilled", ""] + [f"- {t}" for t in ph] + [""]
    est = [(e.role, b) for e in f.roles for b in e.bullets if b.estimated]
    if est:
        lines += ["## Estimated metrics — confirm or replace with your real figures", "",
                  "| Role | Estimated | Bullet |", "|---|---|---|"]
        lines += [f"| {r.roles[i].company} | {', '.join(b.estimated)} | {b.text.replace('|', '/')} |" for i, b in est]
        lines.append("")
    if filled_in:
        lines += [f"## Topped up from your original resume (roles with < {T.MIN_BULLETS} bullets)", "",
                  "| Role | Original bullet (verbatim) | JD similarity |", "|---|---|---|"]
        lines += [f"| {r.roles[i].company} | {t.replace('|', '/')} | {sc:.2f} |" for i, t, sc in filled_in]
        lines.append("")
    lines += ["## Model's notes", "", f.notes, "", "## Summary", "", T.to_plain(f.summary), ""]
    for e in sorted(f.roles, key=lambda e: e.role):
        role = r.roles[e.role] if e.role < len(r.roles) else None
        lines += [f"## {role.title if role else '?'} — {role.company if role else '?'}", "",
                  "| # | Bullet | JD duties | Source | Estimated |", "|---|---|---|---|---|"]
        for j, b in enumerate(e.bullets):
            src = ("original, topped up" if not b.duties else f"original ({b.original}), reworded") \
                if b.original is not None else "new"
            lines.append(f"| {j + 1} | {b.text.replace('|', '/')} | {', '.join(map(str, b.duties))} | {src} | {', '.join(b.estimated) or '—'} |")
        lines.append("")
    lines += ["## Duty → where it went", "", "| # | JD duty | Role(s) |", "|---|---|---|"]
    for i, d in enumerate(jd.responsibilities):
        where = sorted({r.roles[e.role].company for e in f.roles for b in e.bullets if i in b.duties
                        and e.role < len(r.roles)})
        lines.append(f"| {i} | {d[:110].replace('|', '/')} | {'; '.join(where) or '**not covered**'} |")
    if proj:
        lines += ["", "## Projects (from GitHub repo cards)", "", proj.notes, "",
                  "| Project | Repo | Owner | Relevance | Card (kind, substance, had README) | Paragraph |",
                  "|---|---|---|---|---|---|"]
        for e in proj.entries:
            sc, c = next((sc, c) for sc, c in proj.candidates if c["full_name"] == e.repo)
            lines.append(f"| {e.title} | {c['url']} | {'you' if c['owned'] else e.repo.split('/')[0]} | {sc:.2f} | "
                         f"{c['kind']}, {c['substance']}/5, {'yes' if c['had_readme'] else 'no'} | "
                         f"{e.paragraph.replace('|', '/')} |")
        lines += ["", "Other candidates considered: " + ", ".join(
            f"{c['full_name']} ({sc:.2f})" for sc, c in proj.candidates if c["full_name"] not in
            {e.repo for e in proj.entries})]
    old = {x.strip().lower() for _, v in r.skills for x in T._split_items(T.to_plain(v).rstrip("."))}
    lines += ["", "## Technical Skills (regenerated)", ""]
    for s in f.skills:
        lines.append(f"- **{s.category}**: " + ", ".join(
            re.sub(r"[^,()]+", lambda m: m.group(0) + (" *(yours)*" if m.group(0).strip().lower() in old else ""), x)
            for x in s.render()))
    return "\n".join(lines) + "\n"


def letter_report(cl: dict) -> str:
    lines = ["", "## Cover letter", "",
             f"- Template: `{cl['template']}`; header identical to the resume's: "
             f"{'yes ✅' if cl['header_matches_resume'] else 'NO ❌'}",
             f"- {cl['words']} words; consistency with the resume: "
             + ("all numbers and technologies found in the resume/posting/company profile ✅" if not cl["issues"]
                else "problems: " + " | ".join(cl["issues"])), ""]
    for k, p in enumerate(cl["letter"].paragraphs, 1):
        lines += [f"**Paragraph {k}** — based on: " + "; ".join(f"“{e}”" for e in p.evidence), ""]
    return "\n".join(lines) + "\n"


def cover_letter(job_dir: str | Path, company_slug: str, log=print) -> dict:
    """Write cover_letter.tex/.pdf from the job's (final) resume.tex; also usable after editing the resume."""
    from jobagent.tools import cover_letter as CL

    s = get_settings()
    job_dir = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    jd = JobDescription.model_validate_json((job_dir / "jd.json").read_text())
    company = (s.state_dir / "companies" / f"{company_slug}.md").read_text()
    writer = ChatOpenAI(model="glm-5-3", base_url=s.openai_base_url, api_key=s.openai_api_key, max_retries=1,
                        timeout=600, temperature=0, max_tokens=8000)
    cl = CL.write(job_dir, jd, company, writer, log=log)
    changes = job_dir / "changes.md"
    text = changes.read_text() if changes.exists() else ""
    text = text.split("\n## Cover letter\n", 1)[0].rstrip("\n") + "\n"  # replace an earlier letter's section
    changes.write_text(text + letter_report(cl))
    return cl


def main(job_dir: str, company_slug: str, rediscover: bool = True, log=None) -> dict:
    """Tailor one job end to end (resume, then cover letter); returns a summary for checks."""
    log = log or (lambda m: print(m, flush=True))
    s = get_settings()
    job_dir = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    jd = JobDescription.model_validate_json((job_dir / "jd.json").read_text())
    company = (s.state_dir / "companies" / f"{company_slug}.md").read_text()
    r = T.parse((s.data_dir / "templates" / "resume.tex").read_text())
    log(f"template: {len(r.roles)} roles; JD: {len(jd.responsibilities)} duties, {len(jd.technical_skills)} skills")
    model = ChatOpenAI(model="glm-5-3", base_url=s.openai_base_url, api_key=s.openai_api_key, max_retries=1,
                       timeout=600, temperature=0, max_tokens=16000).with_structured_output(T.filled_schema(r), method="json_schema")
    proj = None
    if P.projects_span(r.tex):
        t0 = time.monotonic()
        writer = ChatOpenAI(model="glm-5-3", base_url=s.openai_base_url, api_key=s.openai_api_key, max_retries=1,
                            timeout=600, temperature=0, max_tokens=12000)
        token = (open_vault().get("github") or {}).get("token")
        proj = P.choose(r, jd, writer, github.profile_user(r.tex), token, rediscover=rediscover, log=log)
        log(f"projects: {time.monotonic() - t0:.0f}s chose " + ", ".join(e.repo for e in proj.entries))
        all_projects = list(proj.entries)
    education = r.tex[r.tex.index("\\section{Education}"):r.skills_span[0]]
    max_bullets, feedback, rounds, last_ok = 14, "", [], None
    for attempt in range(1, 6):
        t0 = time.monotonic()
        try:
            f = T.to_filled(r, model.invoke(T.build_fill_prompt(r, jd, company, max_bullets, feedback)))
        except Exception as e:  # timeout / token limit on a slow round: try the next round, keep the best so far
            rounds.append(f"model error ({type(e).__name__})")
            log(f"round {attempt}: {time.monotonic() - t0:.0f}s model error {type(e).__name__}: {str(e)[:120]}")
            continue
        filled_in = T.backfill(r, jd, f)
        dups = T.duplicates(r, f) + T.quality(r, jd, f)
        uncovered, missing, warn = T.fill_checks(r, jd, f)
        tex, problems = T.apply_fill(r, f)
        if proj:  # every round starts from all chosen projects; drop the least relevant only as far as needed
            if education not in tex:
                problems.append("LOCKED PART CHANGED: Education")
            base = tex
            for k in range(len(all_projects), P.MIN_PROJECTS - 1, -1):
                proj.entries = all_projects[:k]
                tex = P.apply(base, proj.block())
                pages, err = T.compile_pdf(tex, job_dir)
                if err or pages == 1:
                    break
            if len(proj.entries) < len(all_projects):
                log(f"  {len(proj.entries)} of {len(all_projects)} projects fit")
        else:
            pages, err = T.compile_pdf(tex, job_dir)
        n = sum(len(e.bullets) for e in f.roles)
        rounds.append(f"{n} bullets ({len(filled_in)} topped up), {len(dups)} bullet issues, {len(uncovered)} duties missing → "
                      f"{pages or 'compile error'} page(s)")
        log(f"round {attempt}: {time.monotonic() - t0:.0f}s {rounds[-1]} {err}")
        asks = []
        if err:
            asks.append(f"The LaTeX failed to compile: {err}.")
        if uncovered:
            asks.append(f"These JD duty indices are not covered by any bullet: {uncovered}. Cover them.")
        if pages and pages > 1:
            asks.append(f"The resume came out {pages} pages; it must be ONE. Keep every duty covered but merge "
                        "related duties, drop the least relevant bullets from roles above their target and shorten every "
                        f"bullet to ONE line (<= 110 characters); never below {T.MIN_BULLETS} bullets per role; "
                        "give each tools_for entry at most 2 tools (never drop posting terms).")
            max_bullets = max(T.MIN_BULLETS * len(r.roles), n - 2)
        if len(missing) > 3:
            asks.append(f"These posting skills appear nowhere; add them as skill items: {missing}.")
        if dups:
            asks.append("Fix these bullets. Repeats: rewrite the second of each pair with different wording, angle and "
                        "tool/result. Academic wording, no technology or no result: rewrite as a professional technical "
                        f"accomplishment with named tools and a result (at most {T.MAX_NO_RESULT} bullet in the whole resume may "
                        "lack a result), or replace it with another duty: "
                        + " | ".join(dups))
        if pages == 1 and not uncovered:
            score = (len(dups), -len(proj.entries) if proj else 0)  # fewest issues, then most projects
            if last_ok is None or score < last_ok[8]:  # keep the best one-page round
                last_ok = (f, uncovered, missing, warn, problems, pages, filled_in, tex, score,
                           list(proj.entries) if proj else None)
            if not dups:  # clean: stop (chasing one more project costs whole rounds and rarely improves the page)
                break
        feedback = " ".join(asks)
    if last_ok is None and "f" not in locals():
        raise RuntimeError("every tailoring round failed: " + " → ".join(rounds))
    if last_ok:
        f, uncovered, missing, warn, problems, pages, filled_in, tex, _, kept = last_ok
        if proj:
            proj.entries = kept
        T.compile_pdf(tex, job_dir)  # the best round, not necessarily the last
    (job_dir / "filled.json").write_text(f.model_dump_json(indent=1))
    (job_dir / "changes.md").write_text(report(r, jd, f, pages, problems, uncovered, missing, warn, rounds, filled_in,
                                                     proj))
    log(f"done: pages={pages} uncovered={uncovered} missing_skills={len(missing)} problems={len(problems + warn)}")
    cl = cover_letter(job_dir, company_slug, log=log)
    log(f"cover letter: {cl['words']} words, {len(cl['issues'])} issue(s)")
    generated = [f.summary] + [b.text for e in f.roles for b in e.bullets] + \
        [x for line in f.skills for x in line.render()] + \
        ([x for e in proj.entries for x in (e.title, e.paragraph)] if proj else []) + \
        [p.text for p in cl["letter"].paragraphs]
    return {"job_dir": job_dir, "pages": pages, "uncovered": uncovered, "missing_skills": missing,
            "problems": problems + warn, "bullet_issues": T.duplicates(r, f) + T.quality(r, jd, f),
            "bullets_per_role": [len(e.bullets) for e in f.roles], "projects": len(proj.entries) if proj else None,
            "letter": cl, "dashes": sum(len(T.DASH.findall(t)) for t in generated)}


def check(urls: list[str], workers: int = 3, log=print) -> list[tuple[str, bool, str]]:
    """Step 9 gate: each posting end to end (live scrape → research → resume + cover letter), then the criteria:
    both PDFs compile to one page, locked parts unchanged, every duty covered, no bullet issues, cover letter
    consistent with the resume, header identical, 2-4 projects, no dash used as punctuation."""
    from concurrent.futures import ThreadPoolExecutor

    from jobagent.tools import repo_cards
    from jobagent.tools.hitl import open_hitl
    from jobagent.tools.research import research_many, slug
    from jobagent.tools.scrape import scrape_many

    s = get_settings()
    results: list[tuple[str, bool, str]] = []
    items = scrape_many(urls, open_hitl(log), refresh=True, log=log)
    jobs = []
    for it in items:
        if it.error or not it.result:
            results.append((it.url, False, f"scrape failed: {it.error}"))
            continue
        jobs.append((it.result.jd, it.result.out_dir))
    names = list(dict.fromkeys(jd.company for jd, _ in jobs))
    for name, profile, err, _ in research_many(names, log=log):
        if err:
            results.append((name, False, f"research failed: {err}"))
    template = (s.data_dir / "templates" / "resume.tex").read_text()
    if P.projects_span(template):  # once, before the parallel runs (one repo listing for all of them)
        repo_cards.refresh(github.profile_user(template), (open_vault().get("github") or {}).get("token"), log=log)

    def one(job):
        jd, out_dir = job
        tag = f"[{jd.company}]"
        try:
            return jd, main(str(out_dir), slug(jd.company), rediscover=False,
                            log=lambda m: print(f"{tag} {m}", flush=True)), None
        except Exception as e:
            return jd, None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(workers) as pool:
        for jd, out, err in pool.map(one, jobs):
            name = f"{jd.company}: {jd.title}"
            if err:
                results.append((name, False, err))
                continue
            cl, d = out["letter"], out["job_dir"]
            letter_pages = _pages(d / "cover_letter.pdf")
            text = lambda f: re.sub(r"(?<!\\)%.*", "", (d / f).read_text())
            failures = [msg for ok, msg in [
                (out["pages"] == 1, f"resume {out['pages']} pages"),
                (letter_pages == 1, f"cover letter {letter_pages} pages"),
                (not any("LOCKED" in p for p in out["problems"]), "locked part changed"),
                (not out["uncovered"], f"duties not covered {out['uncovered']}"),
                (not out["bullet_issues"], f"{len(out['bullet_issues'])} bullet issue(s)"),
                (not cl["issues"], f"cover letter issues: {cl['issues']}"),
                (cl["header_matches_resume"], "cover letter header differs from the resume's"),
                (out["projects"] is None or 2 <= out["projects"] <= 4, f"{out['projects']} projects"),
                (all(2 <= n <= 4 for n in out["bullets_per_role"]), f"bullets per role {out['bullets_per_role']}"),
                (not out["dashes"], f"{out['dashes']} dash(es) used as punctuation in generated text"),
            ] if not ok]
            detail = (f"bullets {out['bullets_per_role']}, {out['projects']} projects, letter {cl['words']} words"
                      + (f" | FAIL: {'; '.join(failures)}" if failures else "") + f" | {d}")
            results.append((name, not failures, detail))
    return results


def _pages(pdf: Path) -> int:
    import subprocess

    if not pdf.exists():
        return 0
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", out).group(1))


if __name__ == "__main__":
    main(*sys.argv[1:3])
