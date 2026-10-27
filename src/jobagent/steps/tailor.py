"""Step 9: tailor the resume for one scraped job and write a review report.

    jobagent tailor outputs/<company>/<job-id> <company-slug>

Projects come from GitHub repo cards (tools/projects); Experience, Summary and Technical Skills from the JD
(tools/tailor), in up to 5 rounds that keep the best one-page result. Writes resume.tex/.pdf, filled.json (the
model's output) and changes.md (estimated metrics to confirm, where each duty went, projects and skills chosen).
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


def main(job_dir: str, company_slug: str) -> None:
    s = get_settings()
    job_dir = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    jd = JobDescription.model_validate_json((job_dir / "jd.json").read_text())
    company = (s.state_dir / "companies" / f"{company_slug}.md").read_text()
    r = T.parse((s.data_dir / "templates" / "resume.tex").read_text())
    print(f"template: {len(r.roles)} roles; JD: {len(jd.responsibilities)} duties, {len(jd.technical_skills)} skills",
          flush=True)
    model = ChatOpenAI(model="glm-5-3", base_url=s.openai_base_url, api_key=s.openai_api_key, max_retries=1,
                       timeout=600, temperature=0, max_tokens=16000).with_structured_output(T.filled_schema(r), method="json_schema")
    proj = None
    if P.projects_span(r.tex):
        t0 = time.monotonic()
        writer = ChatOpenAI(model="glm-5-3", base_url=s.openai_base_url, api_key=s.openai_api_key, max_retries=1,
                            timeout=600, temperature=0, max_tokens=12000)
        token = (open_vault().get("github") or {}).get("token")
        proj = P.choose(r, jd, writer, github.profile_user(r.tex), token)
        print(f"projects: {time.monotonic() - t0:.0f}s chose " + ", ".join(e.repo for e in proj.entries), flush=True)
        all_projects = list(proj.entries)
    education = r.tex[r.tex.index("\\section{Education}"):r.skills_span[0]]
    max_bullets, feedback, rounds, last_ok = 14, "", [], None
    for attempt in range(1, 6):
        t0 = time.monotonic()
        f = T.to_filled(r, model.invoke(T.build_fill_prompt(r, jd, company, max_bullets, feedback)))
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
                print(f"  {len(proj.entries)} of {len(all_projects)} projects fit", flush=True)
        else:
            pages, err = T.compile_pdf(tex, job_dir)
        n = sum(len(e.bullets) for e in f.roles)
        rounds.append(f"{n} bullets ({len(filled_in)} topped up), {len(dups)} bullet issues, {len(uncovered)} duties missing → "
                      f"{pages or 'compile error'} page(s)")
        print(f"round {attempt}: {time.monotonic() - t0:.0f}s {rounds[-1]} {err}", flush=True)
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
            if not dups and (not proj or len(proj.entries) == len(all_projects)):
                break
        feedback = " ".join(asks)
    if last_ok:
        f, uncovered, missing, warn, problems, pages, filled_in, tex, _, kept = last_ok
        if proj:
            proj.entries = kept
        T.compile_pdf(tex, job_dir)  # the best round, not necessarily the last
    (job_dir / "filled.json").write_text(f.model_dump_json(indent=1))
    (job_dir / "changes.md").write_text(report(r, jd, f, pages, problems, uncovered, missing, warn, rounds, filled_in,
                                                     proj))
    print(f"done: pages={pages} uncovered={uncovered} missing_skills={len(missing)} problems={len(problems + warn)}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
