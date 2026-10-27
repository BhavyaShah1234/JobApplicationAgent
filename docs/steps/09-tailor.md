# Step 9 — Resume tailoring (in progress)

Built as a trial with the user on two postings (Amphenol Automation Engineer, Plante Moran AI Data Engineer) and
accepted 2026-10-27. Still to do: the cover letter, then the 5-JD gate.

## Run it

```bash
jobagent scrape <url>                                  # step 7 → outputs/<company>/<job-id>/
jobagent research "<Company>"                          # step 8 → state/companies/<slug>.md
jobagent tailor outputs/<company>/<job-id> <slug>      # → resume.tex/.pdf, filled.json, changes.md
python -m jobagent.tools.repo_cards [workers] [--force] # (re)build the GitHub repo cards on their own
```

## What the agent writes

- **Kept byte-for-byte:** preamble, header, section names, role subheadings (title, dates, company, location),
  Education.
- **Summary:** fresh for each job and company (posting's title wording, the requirements the user meets, one clause
  tied to the company's business from the research profile); only facts (degree, years) come from the template.
- **Experience:** pass 1 distributes the JD's duties over the roles (soft-skill duties fold into a technical bullet
  or are skipped); pass 2 adds the template's bullets marked `% backfill` where relevant. 2-4 bullets per role
  (schema-enforced), no fixed target; a role below 2 is topped up from marked bullets by embedding similarity.
  Metrics are believable estimates, every one listed in `changes.md` for the user to confirm.
- **Technical Skills:** regenerated from the JD's tech stack and qualifications; abstract terms carry concrete tools
  ("Machine Vision (OpenCV, YOLOv8)"), the user's own tools preferred.
- **Projects:** 2-4 GitHub projects, each a heading (linked name | stack | year) and one unbulleted paragraph (problem,
  solution, system). Chosen from repo cards (below) ranked by embedding similarity to the JD; shared repos are
  described as whole projects; titles/years already in the template are kept.
- **One page:** up to 5 rounds; the least relevant project is dropped only as far as a round needs; the best
  one-page round (fewest issues, then most projects) is kept.

## Checks in code (sent back to the model)

Duty coverage; repeats across bullets (shared 4-word phrase or > 50% overlap); academic/unpaid wording; a bullet
naming no technology; more than one bullet without a result; locked parts changed; LaTeX errors.

## Repo cards (`state/github/cards/`)

One card per repo of the resume's GitHub account (own + contributed), written by reading the code: source archive
from codeload.github.com (nameless temp file, deleted after reading; 80 MB cap; vendored/data dirs skipped) →
tree, manifests, original README, main source files and notebooks → summary, README for the agent, tech stack,
fields, results, kind, substance 1-5, embedding. Each card stores the default branch's commit SHA (from the
archive's pax header); a repo is re-analysed only when that SHA changes, checked via the git smart-HTTP refs only
when `pushed_at` moved. First build: 104 repos in ~16 min (6 workers); a no-change refresh ~25 s.

## Learned while building

- Rewording the template's bullets (first trial) mostly deleted them to fit one page and never reached the JD's
  duties; writing from the duties fixed that.
- Indices drift: one fixed schema slot per role (`role_0`...) stops bullets landing under the wrong role.
- `json_schema` maxLength truncates instead of failing: skill items were split into `terms` + `tools_for`, and
  limits on free text are set well above what the prompt asks for (the summary was cut mid-sentence at 500).
- `\par` after `\resumeProjectHeading` overlapped the heading on a full page (and hid real overflow); project
  paragraphs use the template's own item list with an empty label.
- Unicode the fonts can't set (Cyrillic, →, ~) is mapped or dropped before compiling.
