# Behavior contract (current; dated decisions override the original plan)

Last updated 2026-11-16 (template provenance and live-acceptance corrections). Each line is something the agent does or never does, whatever a step's code or a model says.

**Never**
- Submit an application without the candidate's typed `approve` for THAT application at its review prompt (in every browser mode; desktop submit also needs `--allow-desktop-submit`). Filling, queueing and running a pipeline stop at the review screen.
- Solve or evade a CAPTCHA / bot check automatically. The candidate resolves challenges in noVNC or their own browser.
- Invent facts: a current address (it comes from the candidate's file or an address the candidate configured), degrees, clearance, work authorization, employers, citizenship. Claims the candidate declared (years, skills, title) may be used.
- Substitute a similarly named school, employer or person; only an exact name, a corporate suffix, the organisation's own initialism, or an alias the candidate listed in `applicant.yaml` counts.
- Read, keep or delete mail that is not tied to an application or an agent-owned domain; the mailbox is shared with personal mail. Outcome sync is read-only.
- Reset an existing portal account's password, or create a duplicate account to work around a rejected login.
- Spend document or form work on a posting whose hard requirements (U.S. citizenship, a clearance, no sponsorship) your own file says you do not meet: it stops as `needs_review` with the quoted sentence; `--ignore-eligibility` overrides it for one posting, the queue never does, and an unknown fact asks rather than blocks.
- Send one employer a stream of applications: the queue paces per company (default 2 per 7 days); an explicit URL is never blocked.
- Put secrets, the contacts CSV, state, outputs or `.env` into a commit or an image.

**Always**
- Verify every filled value by reading it back; a required field that is unanswered, unverified, or showing a control's own prompt blocks readiness.
- Determine requiredness from the original caption bound to the control, before truncating its display text. A long
  definition must not hide its final required marker, and another question's marker must not make a neighbour required.
- Treat a page whose frames or regions could not be read as not ready.
- Return a resumable `blocked_by=page_start` result when the bounded startup wait finds neither an application
  nor an opening control. Preserve the checkpoint; an empty careers shell alone never triggers challenge fallback.
- Confirm that an Add action exposed a new source-backed row before clicking Add again. Hidden native date parts
  count through their own discovered composite question; unrelated hidden required questions remain blockers.
- Record what was sent: a frozen payload and one attempt row per click; a possibly-sent application is never filled or clicked again.
- Keep generated documents valid (one page, hard and claims checks) and tied to the posting they were built for.
- Record the SHA-256 of the actual resume template read by either writer. Before filling or submission, require it to match
  the current `DATA_DIR/templates/resume.tex`. An edited template or an older document without provenance requires a new
  `jobagent tailor` run; revalidating documents or rewriting only the cover letter cannot certify a new template.
- Compile a changed source before starting a fresh writer. A malformed locked template stops with a source error instead of
  spending model rounds on generated content that cannot repair it. The untailored source may exceed one page.
- Reject a displayed project description without sentence-ending punctuation and route it back to the project writer.
  Fitting its character budget and PDF bounds alone does not establish that the description is finished.
- Preserve SkillsFirst's written report and recommendations as feedback; it contributes no numeric score. A missing
  required numeric provider keeps the five-site average incomplete.
- Bind ATS measurements to a personal report, wait for animated scores to settle, and record provider input limits
  and actual readback. The default numeric panel is AutoApplyMax, HireFlow, SkillSyncer, ATSAlign and ATS Friendly.
- Prefer function calling for structured model output. Default network timeout is 120 seconds, and one structured
  invocation has 180 seconds total across modes/retries; explicit model settings may change those budgets.
- In the default multi-agent writer, bind semantic review to the visible candidate version. Reuse unchanged findings,
  review edited content again, and reject skills-category errors or an unavailable content review before spending ATS scans.

**Learning is advisory.** Recommendations appear only at declared looks (100, 200, 400, 800, 1600 resolved applications); behavior switches are off by default; the site-mix switch is unsupported.

**Related skills.** The skill graph may put a skill on the resume only when your own files already show you have it; skills it finds that your files do not show are listed for you and never added.
