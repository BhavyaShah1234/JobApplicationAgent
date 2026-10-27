# Step 9 — Resume and cover letter tailoring

Built as a trial with the user on two postings (Amphenol Automation Engineer, Plante Moran AI Data Engineer) and
accepted 2026-10-27; cover letter added the same day.

**Gate passed 2026-10-27** (`jobagent step tailor-check`, the 5 step-7 postings: Stripe, Palantir, OpenAI,
NVIDIA, Amazon; live scrape → research → resume + cover letter, 3 in parallel): every resume and letter one page,
locked parts unchanged, all duties covered, 0 bullet issues, 2-4 bullets per role, 2-3 projects, letters consistent
with their resumes, no punctuation dashes. The first runs failed on gate-check bugs (template date ranges counted as
dashes; Pydantic/Jinja2 not recognised as tools) and on two pipeline gaps (a model timeout / token-limit error
aborted the whole job; the letter abbreviated an employer to "TGen"), all fixed in code; no output was edited by hand.

## Run it

```bash
jobagent scrape <url>                                  # step 7 → outputs/<company>/<job-id>/
jobagent research "<Company>"                          # step 8 → state/companies/<slug>.md
jobagent tailor outputs/<company>/<job-id> <slug>      # → resume + cover letter (.tex/.pdf), changes.md
jobagent cover-letter outputs/<company>/<job-id> <slug> # rewrite the letter after editing resume.tex
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

## Cover letter

- **Template:** `data/templates/cover_letter.tex` (user's, gitignored; the committed `cover_letter.example.tex` is
  the same design with placeholder contact details). Same fonts as the resume, the resume's header verbatim (in an
  `adjustwidth` block so it stays on one line), a rule, then `<<DATE>>`, `<<RECIPIENT>>` (Hiring Team, company,
  the posting's first location), `Re: <<SUBJECT>>` (title + req id), `<<SALUTATION>>`, `<<BODY>>` (justified), `<<CLOSING>>,` with the name on the next line.
- **Written from the job's final `resume.tex`** (the user's edits carry over), the JD and the company profile:
  3-4 paragraphs (role + one cited company fact, the 2-3 strongest duty matches with the resume's tools and
  numbers, close). Each paragraph lists the resume lines / company facts it used (in `changes.md`).
- **Consistency checks** (sent back to the model, up to 3 rounds, best kept): every number and every tool/proper
  noun in the letter must appear in the resume, the JD or the company profile; ~420 words max; one page; the
  letter's header must equal the resume's.

## Checks in code (sent back to the model)

A change by a percentage takes "by" ("cutting failed refreshes by 45%"): prompts ask for it and `to_latex` inserts
it when missing ("to", "from ... to" and plain metrics like "96.4% mAP" are left alone).

No dash as punctuation anywhere (em dash, or a spaced en dash / `--`): it reads as AI-written. Prompts forbid
it, the checks send it back (bullets, summary, letter paragraphs), and `to_latex` turns any that slips through into a
comma; ranges like "2025–2026" are untouched.


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

- Voyager is slow with 3 jobs in parallel (rounds of 4-20 min; some calls time out at 10 min): a failed model call
  is now one failed round, not a failed job, and the loop stops at the first clean one-page round.

- Rewording the template's bullets (first trial) mostly deleted them to fit one page and never reached the JD's
  duties; writing from the duties fixed that.
- Indices drift: one fixed schema slot per role (`role_0`...) stops bullets landing under the wrong role.
- `json_schema` maxLength truncates instead of failing: skill items were split into `terms` + `tools_for`, and
  limits on free text are set well above what the prompt asks for (the summary was cut mid-sentence at 500).
- `\par` after `\resumeProjectHeading` overlapped the heading on a full page (and hid real overflow); project
  paragraphs use the template's own item list with an empty label.
- In LaTeX source `\\%` is an escaped percent, not a comment: stripping comments naively cut every percentage off
  the resume text the letter is checked against.
- Unicode the fonts can't set (Cyrillic, →, ~) is mapped or dropped before compiling.
