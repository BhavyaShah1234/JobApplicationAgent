# Step 10 — ATS score loop

After the resume is tailored (step 9), the agent scores it on online ATS checkers, works the posting's missing
keywords and the checkers' own advice in where they truthfully fit, and rescores, up to 5 rounds, aiming for an
average of 90.
Every score is recorded, and the cover letter is written afterwards from the final resume.

**Gate passed 2026-10-29** (`jobagent step tailor-check` on the 5 step-7 postings, tailoring + ATS loop + cover
letter): every resume and letter passes the step 9 criteria; every score is recorded in `ats_runs`; each scorer
ran 24 times across the 5 jobs with 0 failures. No posting reached the 90 average, so the gap is reported:

| Posting | Average (start → end) | Local | AutoApplyMax | Jobalytics | Gap |
|---|---|---|---|---|---|
| OpenAI | 84.0 → 88.3 | 98.0 | 85 | 82 | 1.7 |
| Palantir | 84.9 → 86.2 | 92.5 | 88 | 78 | 3.8 |
| Amazon | 85.2 | 93.5 | 80 | 82 | 4.8 |
| NVIDIA | 80.5 → 83.5 | 90.6 | 72 | 88 | 6.5 |
| Stripe | 78.9 | 91.6 | 57 | 88 | 11.1 |

The local score is above 90 everywhere. What holds the average down is AutoApplyMax's word overlap (Stripe's
posting is mostly company language: "financial infrastructure", "economic growth") and Jobalytics' general score,
which skills don't move. Closing that gap would mean stuffing non-skill words, which the loop refuses to do.

**Advice loop (2026-10-29, OpenAI Fraud Engineering):** with the sites' advice worked in, the average went
65.6 -> 70.5 (ATS Friendly 38 -> 58, rescanned on the final resume; Jobalytics 84 -> 90; AutoApplyMax 82 -> 81;
HireFlow 58.5 -> 53, the mean of two runs). Gap 19.5. What is left is eligibility or noise, not missing work:
HireFlow keeps asking for "5 years of software engineering" and "2 years of fraud analysis" (its own advice is to
write "Over 5 years ..."), and ATS Friendly wants a Computer Science degree, the posting's exact title and a
city/state in the header (a locked template part, reported to the user).

## Run it

```bash
jobagent tailor outputs/<company>/<job-id> <slug>               # step 9 + this loop + cover letter (default)
jobagent ats-loop outputs/<company>/<job-id> <slug> [--target 90]   # the loop on an already-tailored resume
jobagent ats outputs/<company>/<job-id>                        # one round of online scores, recorded
jobagent ats-record outputs/<company>/<job-id> skillsfirst 92  # a score you got by hand; joins the average
```

## How it works

```mermaid
flowchart TD
    IN[/"Tailored resume (step 9)<br/>resume.pdf + filled.json + projects.json"/] --> SC
    subgraph SC["Score the resume online"]
        A["AutoApplyMax<br/>job-specific score + missing words"]
        J["Jobalytics<br/>general resume score"]
        H["HireFlow<br/>job match + missing phrases + recommendations"]
        T["ATS Friendly (2 scans a week)<br/>start and final resume only"]
        M["Your manual scans<br/>(jobagent ats-record)"]
    end
    SC --> REC[("ats_runs table<br/>one row per site per round")]
    SC --> AVG{"Average at least 90<br/>or nothing left to add?"}
    AVG -- "yes" --> OUT
    AVG -- "no" --> F["Collect missing keywords and advice<br/>word-overlap lists filtered to real skills,<br/>eligibility items dropped, template advice to the user"]
    F --> KP["Advice pass (glm-5-3)<br/>skills to the right category, phrases and soft skills<br/>into bullets or the Summary, repeated words varied"]
    KP --> CHK["Code checks<br/>no duplicates, posting's spelling,<br/>no filler category, no new bullet issues"]
    CHK --> FIT["Re-render and re-fit the page<br/>projects: full, then short, then one fewer"]
    FIT --> OK{"One page, nothing worse, and the<br/>per-round average rose (or held while more<br/>ATS Friendly phrases are now present)?"}
    OK -- "yes: keep" --> SC
    OK -- "no: undo" --> AVG
    OUT[/"Final resume: ATS Friendly scans it once more<br/>then the cover letter is written from it"/]
```

The loop runs at most 5 rounds. A model error or a rejected change costs one round; after a model error the
next round tries with fewer keywords.

## The scorers (online only, since 2026-10-29)

The agent's own local scorer was removed at the user's request: every score now comes from an online ATS checker.

| Site | Automated? | What it measures |
|---|---|---|
| AutoApplyMax | yes (agent account) | Job-specific: keywords 40%, formatting, completeness, readability; matched and missing words (word overlap) |
| Jobalytics | yes (agent account) | General resume quality: content, sections, ATS essentials (job match locked) |
| HireFlow | yes (no account) | Job match: keyword match, skills fit, experience fit, completeness; missing must-have / nice-to-have phrases |
| ResumeAtlas | on request only | Required skills shown in work bullets; only 2 job matches a month |
| ATS Friendly | yes (agent account via Google, 2026-10-29); 2 free scans a week, so start + final only, and a scan of the exact same PDF is reused | Job match: missing hard skills, soft skills and other keywords; job title, contact details, education notes |
| SkillsFirst, Jobscan, SkillSyncer, AtsAlign, Novoresume, RemoWork, Lessie | no: their terms forbid bots or scraping | Run by hand; `jobagent ats-record` |
| ApplyCove, Resmume | no: CAPTCHA on every scan | Run by hand; `jobagent ats-record` |

Scores the user records by hand count in the average for the exact resume version they scanned.

## Rules

| Rule | Where |
|---|---|
| A generic word (salary, location, build, tools, information, …) never becomes a skill | `tailor.is_generic` |
| A skill already in any category is not added again; the posting's spelling is used ("Rust", "AWS") | `tailor.apply_keyword_pass` |
| No filler category ("Technical Skills", "Other"), at most 8 categories | `tailor.apply_keyword_pass` |
| A change is kept only if the page stays one page, no bullet issue or uncovered item is added, and the average rises | `steps/ats.run` |
| Work phrases from a site ("leading small projects independently") go into an existing bullet; eligibility (years, degree, authorisation) is never written in | `steps/ats.evaluate`, keyword prompt |
| Projects make room first (short paragraphs, then one fewer, at least 2) | `projects.fit` |
| Every score of every round is recorded | `ats_runs` (`tools/ats_sites.record`) |
| Advice about years, degrees or eligibility is dropped; advice about locked template parts (header links, city/state in the header, the posting's title) is logged for the user, never applied | `steps/ats.evaluate`, `breakdown["template"]` |
| Soft skills are shown through the work in bullets or the Summary, never listed in Technical Skills | keyword prompt |
| The Summary may be rewritten (80-500 characters, same facts) | `tailor.apply_keyword_pass` |
| Every site gets the PDF as `First_Middle_Last_Resume.pdf` (Jobalytics flags `resume.pdf`) | `ats_sites.upload_name` |

## Learned while building

- The free "no login" checkers only score against a job description after a free sign-up.
- The resume header's icon font extracted as junk characters for every ATS; the templates give the icons empty
  "actual text" (`accsupp`), and the loop re-renders older resumes with the fixed template.
- AutoApplyMax's "missing keywords" are mostly boilerplate (posted, salary, workday); without filtering, the
  keyword pass tried to add them, and once added "build, tools, information" as skills.
- `glm-5-3` in json_schema mode spent its whole 16k-token budget deliberating over a handful of keywords;
  in function-calling mode it answers in about 100 s. The fast model was quick but put "rust" under LLMs.
- A full page leaves no room for one more skill: the loop re-fits the projects saved in `projects.json`.
- The 90 average is hard to reach honestly with online checkers: AutoApplyMax's word overlap
  HireFlow's exact-phrase keyword match (8-14%) and Jobalytics' general score don't respond to real skills; the
  first online-only run on OpenAI went 73.0 -> 74.7 (AutoApplyMax 82, Jobalytics 82, HireFlow 60).
- HireFlow's fit scores come from a model: the same unchanged resume scored 52, 57, 60 and 64. Judging an edit by
  one run kept or undid changes at random, so HireFlow is scored twice and averaged, and a change that works in
  2+ of ATS Friendly's phrases is kept unless the per-round average falls by more than 2 points.
- Rewrites dropped words AutoApplyMax already matched ("compliance", "security") and lengthened sentences
  (readability 90 -> 82); the advice pass now gets a KEEP list and may not make a line longer.
- The keyword pass added "backend systems" under "Backend and Data Systems" and "fraud remediation" next to "fraud
  detection and remediation"; multi-word terms already said by the category or one term are refused
  (`tailor.covered_by`). One-word tools always stay ("Azure" next to "Azure DevOps CI/CD").
- glm-5-3 sometimes answers a function call with nothing (about 1 round in 3 with the longer advice prompt);
  an empty answer is retried once, then the round is spent with fewer keywords next time.
- ATS Friendly allows 2 free scans a week: it is read at round 0 and rescanned on the final resume, a scan of the
  exact same PDF is reused from `ats_runs`, and with no scans left it is skipped and left out of the final
  average (logged).
