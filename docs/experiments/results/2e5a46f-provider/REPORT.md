# Real-provider form-resolution experiment

Measured version: **`2e5a46ffd3ceb0607da8011586e2eb35f83d2120`**, 2026-11-13 (America/Phoenix).
Shared verified production checkpoint: `51cebb0`. **Prototype only; do not deploy.**

## Result and decision

Completed all **72 planned trials**: 12 synthetic browser fixtures × 3 repetitions × 2 engines.
Both arms used the actual configured filling model, **`devstral2-123b`**, with the same settings and supplied fact
content. Engine order alternated by repetition. Source hashes stayed unchanged; there was no batch abort and no
browser/model exception. No final Apply/Submit click occurred.

| Measure (36 trials per engine) | Current engine | Experimental engine |
|---|---:|---:|
| Independently correct DOM/file values | 30/36 | 36/36 |
| Reported ready | 33/36 | 33/36 |
| Correct **and** ready | 30/36 | 33/36 |
| Ready with incorrect values | 3 | 0 |
| Median active filling time, all trials | 10.76 s | 2.37 s |
| Total active filling time | 1,012.04 s | 325.54 s |
| Avoidable known-fact questions/confirmations | 9 | 0 |
| Necessary missing-fact questions | 0* | 3 |
| Actual provider calls | 75 | 12 |
| Provider-reported tokens | 253,187 | 2,691 |
| Held-out values correct | 9/12 | 12/12 |

Median active time decreased **77.95%**; avoidable questions decreased **100%** in this cohort. Human replies
were immediate, source-backed simulated replies; these are active filling times, not live user waiting times.
Token totals are provider-reported usage, not independently reconstructed billing.

**Do not adopt yet.** Despite the aggregate speed/assistance improvement, the experiment introduced a completion
regression on the same-named-document case: all three candidate runs held the correct document bytes but exhausted
the round budget instead of reaching review. Equal aggregate ready counts hide that per-case regression.
The later repair and its tests are a new prototype version, not a rewrite of these measurements. Actual personal-file
normalization and the planned live portal cohort remain unvalidated.

\* The current engine did not ask for the genuinely missing contact preference. In the original cohort it inferred
“Email” from the presence of an email address in all three trials. Matching the oracle by chance is **unsupported**,
not evidence that the preference was supplied. `audit.json` records these observations separately; the same trials
already failed value grading because the email field stayed blank.

## What the fixtures exposed

1. **Flat native upload labels:** the current engine uploaded the cover letter to both controls and reported ready.
   Its native field label included surrounding body text containing “Cover Letter”, which overrode the Resume
   caption. The candidate used the input's own associated label and checked the role-specific retained bytes.
   This is an additional caption/role issue; it does not undo F28's verified retained-input/trigger merge repair.
2. **Email-only minimal context:** “Your email address” matched the current engine's postal-address heuristic.
   Without a prepared postal address, enforcement erased the supplied email and requested a postal address.
   This demonstrates a helper/context failure; the normal production command prepares a postal address first.
   The separate sensitivity check below tests that distinction rather than masking it in the measured cohort.
3. **Already supplied school:** the current engine asked for the exact school and then asked the simulated owner
   to confirm keeping that correct answer: six avoidable interactions across three repetitions. The responder
   answered from the supplied source and confirmed it; the baseline was not denied a correct source-backed reply.
4. **Candidate upload caching:** the cached answer is a document role (`resume`/`cover_letter`), whereas control
   readback can be `attachment.pdf`. Comparing those strings repeatedly triggered uploads until the round budget
   expired. This failure is retained in `trials.json` and the table below.

## Per-fixture medians and outcomes

Each row has three repetitions per engine. Correct and ready are separate measures.

| Fixture | Current median s | Candidate median s | Current correct/ready | Candidate correct/ready |
|---|---:|---:|---:|---:|
| Text | 6.97 | 1.82 | 3/3 | 3/3 |
| Travel interval | 8.32 | 0.54 | 3/3 | 3/3 |
| School identity | 11.65 | 7.00 | 3/3 | 3/3 |
| Full date | 8.70 | 1.12 | 3/3 | 3/3 |
| Hidden branch | 7.30 | 0.73 | 3/3 | 3/3 |
| Delivered schema | 7.40 | 0.93 | 3/3 | 3/3 |
| Two native upload roles | 12.11 | 4.76 | 0/3 | 3/3 |
| Conditional field | 14.99 | 0.71 | 3/3 | 3/3 |
| Held-out email alias + missing preference | 9.84 | 2.73 | 0/0 | 3/3 |
| Held-out API-delivered question | 15.11 | 3.34 | 3/3 | 3/3 |
| Held-out same-named documents, byte grading | 11.09 | 55.69 | 3/3 | 3/0 |
| Held-out 50 scoped employment/education fields | 210.50 | 29.72 | 3/3 | 3/3 |

The held-out layouts were frozen before their provider outcomes were observed. They are now development cases;
future changes cannot use them to claim new unseen-case validation. Closed roots, iframes and wizard traversal have
focused browser tests but were not part of this provider cohort. Three repetitions and synthetic layouts do not
establish statistical generalization to arbitrary live portals.

## Separate prepared-address sensitivity check

After the original cohort revealed the email-context issue, six additional real-provider trials repeated that
contact case with a synthetic prepared postal address, made available to both arms. This is **post-observation
sensitivity evidence**, not another held-out test and not pooled into the 72-trial results.

- The current engine filled the email correctly in all three trials: the normal prepared-address precondition
  removes that specific email-clearing failure in this case.
- It left the required contact preference at `Choose`, asked no question, and reported ready in all three trials.
  The checker rejected an inferred preference, but the unanswered placeholder still escaped the readiness gate.
- The candidate asked the simulated owner for the missing preference and correctly reached review in all three.

Evidence: `context-sensitivity/case.json`, `trials.json`, source manifest and exact check script. A supplied email
is not a supplied preference; the independently observed dropdown value is the grading basis.

## Post-measurement prototype repair

The next prototype compares cached uploads by source role and current retained native bytes, rather than comparing
the role string to a filename. It revalidates bytes after other widgets mutate, so a same-name replacement cannot
silently inherit an earlier verification.

Two new browser regressions **failed before the repair**:

- Correct role-specific files both named `attachment.pdf` must reach review without repeated upload events.
- A later cover-letter widget action replacing the resume bytes under the same filename must be detected and
  repaired before readiness.

The repair is commit **`9d6aad1`**. The subsequent full experiment suite passed **24 tests**, with 13 dependency
warnings, in 69.39 s; see `post-measurement-tests.txt` and `post-repair-source-test-manifest.json`. These tests do not retroactively improve
the original ready count, establish a new provider latency result, or establish zero false-ready behavior on all
layouts. The same-name replacement case also exposed a false-ready defect outside the original frozen cohort.

## Live preflight and remaining experiment work

Read-only navigation inspected 14 saved generated-document pairs, excluding the already submitted Carvana/Pantex
postings. Stripe and Palantir's saved JDs were non-US and excluded from this US address-policy cohort. Results:

- DoorDash/Greenhouse: public application form visible (29 fields).
- OpenAI/Ashby: posting and application link visible; no fields on the listing page. Navigate to the application
  before starting any filling timer. A zero-field listing is not a completed application.
- Five direct Workday URLs (General Mills, two Intel, Adobe, NVIDIA): HTTP 521 error pages in Docker. Desktop
  navigation retry is still required; this is not proof that those postings are closed or that Workday is unsupported.
- Symbotic and Plante Moran: public listings load, with application links to Workday. Their listing pages are not
  application forms. They do not provide two additional independent portal families.
- Amazon: posting loads; application/account navigation still needed. GoDaddy: HTTP 404. Mercor: blank page in
  this observation; needs investigation rather than a claim of successful form discovery.

`live-preflight.json` contains public URLs, fields/buttons and navigation metadata, excluding raw support IDs and
network addresses. **No live application was filled or submitted during this experiment phase.** The available
saved pairs do not yet constitute the planned two comparable unsubmitted postings for each of six families.

Next: validate the repaired prototype with fresh layouts; prepare matched live US postings and identical frozen
applicant/documents/address/referral/company sources; complete authentication/desktop setup outside the filling
clock; independently grade both drafts and count all necessary questions, avoidable assists, technical rescues and
human challenges. Keep actual compilation of personal facts in scope for those live trials. Preserve this report
and the original baseline; do not tune the baseline in the middle of a comparison or merge the experiment to
production before the live correctness and completion evidence is reviewed.

## Evidence and reproduction

- `summary.json`, `trials.json`, `trials.csv`, `source-manifest.json`, `audit.json`, `run.txt`.
  Text-log trailing spaces and CSV line endings are normalized for Git; JSON measurements are unchanged.
- Prerequisite source/test manifest and 83-pass independent review: `../../shared-checkpoint/`.
- A four-trial pilot and three aborted preparation runs are excluded. Those partial runs corrected responder
  handling of supplied facts/keep-confirmations before the fresh held-out layouts were reached.
- Reproduce the measured engine from commit `2e5a46f`, with the project's Chrome/provider environment, using
  `PYTHONPATH=src python -m jobagent.experiments.benchmark --model --repetitions 3 --output /tmp/NEW-TRIAL-DIR`.
  Preserve unique output/state directories. Provider latency and generated decisions can vary across runs.
- Fixture inputs are synthetic and pre-normalized; actual applicant files, credentials, contact CSV, profiles and
  production databases are not included in these Git artifacts. The original production checkout and deployed
  `/app/src/jobagent` were not changed by the experiment.
