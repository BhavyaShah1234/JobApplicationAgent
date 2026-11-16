# Step 16 - several inputs in one run, with a review queue

`jobagent run INPUT [INPUT ...]` takes job URLs, company names (comma lists) and CSV/XLSX files in any mix and takes each as far as
`--through` (scrape, tailor, apply or submit). It stops at the review screen; sending needs your typed `approve` for that application.

| Input | What happens |
|---|---|
| a job URL | the shared pipeline (scrape, research, locations, documents, apply); `--through submit` still stops at the typed approval |
| a company name, or a file's rows | discovery (careers page, US-only filter, role matching), then the queue is worked, at most `--limit` jobs per run |

A company's jobs are never submitted from a batch (`--through submit` is taken as apply for them). One input failing does not stop the
others; the table at the end says what happened to each, and the command exits 1 if any failed.

**Per-company pacing.** The queue skips a company that already has `PACING_MAX_PER_COMPANY` (default 2) applications started or sent in the
last `PACING_WINDOW_DAYS` (default 7); its other jobs wait and the log says so. `PACING_MAX_PER_COMPANY=0` turns it off. An explicit URL
is never blocked.

**Eligibility screen.** After the posting is read and before any document or form work, the posting's hard requirements are compared with
what your own file says (`data/applicant.yaml`: work authorization, sponsorship, citizenship, clearance):
- U.S. citizenship required and your file says you are not a citizen: the job stops as `needs_review` with the quoted sentence
  (`--ignore-eligibility` goes on for that posting; the queue never does);
- a clearance, or "no sponsorship", that your file does not satisfy: the same;
- anything your file does not settle (citizenship left blank, an ITAR / "U.S. person" statement): logged and recorded in the job's
  `eligibility.json`, and the job continues.
It never invents a fact: a clearance or citizenship not in your file is not assumed. (Why it exists: Pantex, 2026-11-14, rejected the
day after submission a role that required citizenship.)

**Review queue.** `jobagent review-queue` lists the applications filled to their review screen and waiting for your decision (oldest
first, how ready each is, its blockers, and the exact `jobagent submit ...` command that opens its review). It never approves anything.
Plain `apply` and batch/queue applications register a `filled` database row when they reach review, including drafts with unresolved
fields. Their own result file determines readiness. This registration needs no submission hook or approval and preserves submitted
or uncertain states; it also keeps the company's pacing slot counted after the worker releases its active claim.

**Outcomes that are not email.** `jobagent outcomes add APPLICATION STAGE [--date YYYY-MM-DD] [--note ...]` records a call or a message
as your own outcome event; it counts in `stats` and the learning looks like a mail outcome.

**Scheduled sync.** `SYNC_INBOX_EVERY_HOURS=6` in `.env` makes the container run `jobagent sync-inbox` every 6 hours (read-only,
no overlap, log in `state/logs/sync-inbox.log`); it takes effect when the container is recreated. Off by default.


## Behaviour fixed after review (2026-11-15)
- A batch works **only the companies it named** (jobs already queued for them count; nobody else's jobs are claimed, and a company with
  nothing found claims nothing).
- `--force`, `--no-ats` and `--filling-strategy` reach queue-run applications too; `--ignore-eligibility` and desktop submission never do.
- Every input and every worked job gets its own row with its exit code; `jobagent run` exits non-zero when anything stopped short
  (`needs_review` exits 4, a failed job or a discovery failure exits 1). A job that failed is not counted as having reached the stage.
- An unreadable file (corrupt XLSX, bad encoding) is one failed row; the other inputs still run.
- File rows keep their source ("targets.csv row 3"); a consented referral stays with its own company, conflicting duplicates attach none,
  and queue runs do not attach referrals (the run says so; use `jobagent apply` on that posting).
- Pacing slots are reserved inside the claim transaction and count jobs other runners are working right now, so two runners cannot both
  take a company's last slot; a failed job gives its slot back.
- The eligibility screen treats an unknown clearance as "ask" (not blocked), an "able to obtain" clearance as "ask", a negated or
  "preferred" requirement as none, compares clearance levels, and reads "citizen **and** work authorization" as two requirements.
