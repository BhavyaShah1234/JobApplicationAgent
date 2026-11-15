# How a job's work resumes (F13-F16, F25)

**Queue.** `jobagent queue run` claims a job atomically (worker id + a 15-minute lease renewed by a heartbeat); two runners
never take the same job and a runner never resets another's live work. A killed runner's job is recovered when its lease
runs out, or at once if its process is gone on this machine. What each step achieved is read from its structured result
(`application/application.json`, the `jobs` row), not guessed from the exit code. `--browser-mode docker|desktop|auto`
reaches the `apply` step; the queue never submits.

The queue now calls the shared `Pipeline` in the same Python process. It no longer shells out to CLI stages. The same
orchestrator is available directly:

```bash
jobagent run 'https://employer.example/jobs/123' --through apply --browser-mode auto
```

Scrape and research record input fingerprints and output hashes, including their artifacts outside the job folder.
Completed stages are reused only while their files are unchanged; browser filling always reopens and reconciles the form.
`--through submit` is explicit, retains the final review approval, and requires a fixed browser mode; desktop submission also
requires `--allow-desktop-submit`.

**Stages.** `<job>/workflow.json` records each stage's inputs and output hashes. `jobagent tailor` reuses a finished
`resume` or `cover_letter` stage when its inputs and files are unchanged, redoes only what changed or was interrupted, and
`--force` rebuilds. Resumption is by STAGE: a stage that was running when the process ended restarts from its beginning
(the tailoring graph's state holds live objects and is not checkpointed). `jobagent workflow <job>` shows the stages.

**Side effects.** Filling never sends; reopening the page and reading it again is the recovery. Sending is guarded by the
submission attempts (a possibly-sent application is never sent again; `jobagent confirm` settles an unknown one) and by the
candidate's `approve` for that application in every browser mode. Submitting from the desktop browser also needs
`--allow-desktop-submit`; `auto` is for filling only.

**One build at a time.** `tailor`, `apply` and `submit` take a lock on the job folder. A resume version is published as one
bundle (`resume.tex`, `resume.pdf`, `projects.json`, `filled.json`); an interrupted publish is completed by the next
`tailor` and `documents.verify` refuses the folder until then. Job folders are identified by `outputs/<company>/<job-id>`,
however they were spelled.

**Relational identity.** `applications.job_id` references the existing integer `jobs.id`, and
`submission_attempts.application_id` references `applications.id`; foreign-key enforcement is enabled on every connection.
Legacy records are migrated without deleting history. Unknown legacy postings receive non-queueable identity rows, and an
orphan attempt creates an uncertain application that must be reconciled. Canonical folder keys remain compatibility attributes.

Location resolution is recorded before tailoring so applying does not change the document stage's posting fingerprint. `jobagent run URL --through submit --browser-mode desktop --allow-desktop-submit --no-ats` skips new ATS scans explicitly; document validity checks and review approval still apply, and the default run retains its configured checkers.
