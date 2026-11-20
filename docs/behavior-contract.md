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
- Preserve full explicit question captions for planning and source binding; shorten them only for display/logs.
  Government referral, government relationship, personal/financial ties, outside work and ethics acknowledgements
  are separate declarations. Each requires its own exact owner evidence, including its definitions. A neighbouring
  Yes/No cannot support it, even through a source-resolver or answer-sheet shortcut. A boolean acknowledgement
  mentioning a manager is distinct from the manager's contact details.
- Use literal confirmations of a posting's qualifications only for that application, through its current human reply
  or its own answer sheet. Never reuse an old company-wide Yes/No for another role's unstated requirements. Explicit
  instructions to compare each new JD against evidence can still be learned.
- Treat a page whose frames or regions could not be read as not ready.
- Return a resumable `blocked_by=page_start` result when the bounded startup wait finds neither an application
  nor an opening control. Preserve the checkpoint; an empty careers shell alone never triggers challenge fallback.
- Confirm that an Add action exposed a new source-backed row before clicking Add again. Hidden native date parts
  count through their own discovered composite question; unrelated hidden required questions remain blockers.
- Keep selected search-picker choices as partial option evidence without opening a covered search input just to
  catalogue choices. A prefill still needs an independent answer source; a different sourced answer can replace it.
  Recruitment-source policy still inspects actual referral choices before accepting a fallback.
- Before re-uploading a same-named document, replace only matching entries in that widget when their filename and
  explicit non-submitting delete control are both unambiguous. Preserve other files/widgets; an old filename alone
  never verifies a new dispatch. Unclear replacement controls block the upload.
- Record what was sent: a frozen payload and one ledger row per submission attempt; a possibly-sent application is never refilled or retried
  without reconciliation. The bounded email-code continuation below belongs to the existing attempt.
- An ordinary emailed application code may continue the same approved attempt automatically: match fresh mail to the candidate and company,
  fill only the newly identified code controls, and recheck the original approved answers, uploads and URL before one verification dispatch.
  A code is not a receipt. A changed application, ambiguous controls, rejected/missing code or actual CAPTCHA goes to human review;
  no second application attempt is created and no code is written into logs or the verification audit.
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
- Keep a locally validated resume independently of ATS availability. A failed provider leaves its score missing;
  document validity and the configured ATS warn/block policy remain separate. Later failed edits restore the exact
  accepted source, PDF, filled structure, projects and layout measurements.
- Apply the same bullet-quality, coverage, summary and serialized-structure checks to both document strategies.
  Retry an unavailable semantic review on the unchanged draft before blocking; never cache a failed review as success.
  Exhausted draft/coverage repairs may request one fresh draft with the remaining findings. Unexpected programming
  errors and invalid credentials still fail closed. Rejected drafts are retained separately in `.generation-drafts`.
- Independently adjudicate proposed default-writer critic errors against supplied document/source IDs and
  code-verified facts before treating them as defects. Uncertain findings remain blocking; deterministic quality
  and source checks cannot be voted away. A bullet that explicitly names its real stack is not rejected solely
  for also using a phrase such as "AI tools".
- Retry an invalid cover-letter stage without rebuilding an unchanged valid resume. Prefer a compiled one-page
  letter, retain its exact PDF instead of recompiling it at publication, and require its header to match the final resume.
  Changed generation code, project metadata or layout measurements invalidate the corresponding workflow cache.

**Learning is advisory.** Recommendations appear only at declared looks (100, 200, 400, 800, 1600 resolved applications); behavior switches are off by default; the site-mix switch is unsupported.

**Related skills.** The skill graph may put a skill on the resume only when your own files already show you have it; skills it finds that your files do not show are listed for you and never added.
