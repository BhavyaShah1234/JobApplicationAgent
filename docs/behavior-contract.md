# Behavior contract (current; dated decisions override the original plan)

Last updated 2026-11-15 (step 16 additions: eligibility screen, pacing, review queue). Each line is something the agent does or never does, whatever a step's code or a model says.

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
- Treat a page whose frames or regions could not be read as not ready.
- Record what was sent: a frozen payload and one attempt row per click; a possibly-sent application is never filled or clicked again.
- Keep generated documents valid (one page, hard and claims checks) and tied to the posting they were built for.

**Learning is advisory.** Recommendations appear only at declared looks (100, 200, 400, 800, 1600 resolved applications); behavior switches are off by default; the site-mix switch is unsupported.
