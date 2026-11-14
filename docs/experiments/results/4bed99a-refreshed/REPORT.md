# Refreshed provider comparison — 2026-11-14

Measured frozen commit: **`4bed99ade6463621e1baa2514f9c62707978ee53`**. Shared verified production checkpoint: **`54dbf90`**.
This report is a separate batch from `2e5a46f`; do not pool the cohorts or overwrite the earlier results.

Completed **72/72 trials**: 12 cases × 3 paired repetitions × 2 engines. Source hashes remained unchanged; the shared checkpoint matched. No final Apply/Submit click occurred.

## Results

| Measure, 36 trials per engine | Current | Experimental |
|---|---:|---:|
| Correct values + required source questions | 33 | 36 |
| Reported ready | 36 | 36 |
| Correct and ready | 33 | 36 |
| False-ready at the filling boundary | 3 | 0 |
| Median active filling seconds | 14.70 | 4.48 |
| Total active filling seconds | 1,186.73 | 250.67 |
| Avoidable known/repeated questions | 2 | 0 |
| Necessary missing-fact questions | 3 | 6 |
| Writer model starts | 84 | 29 |
| Writer-reported tokens | 271,936 | 24,095 |
| Fresh-case correct / 12 | 9 | 12 |
| Browser/model exceptions | 0 | 0 |

Median active time fell **69.56%** within this cohort. Avoidable questions fell from **2 to 0**. Total questions were **5 current / 6 experimental**: the candidate correctly asked three exact-day questions the baseline missed, while avoiding two known-fact questions.
Simulated owner replies were immediate; this excludes real human waiting and is not end-to-end application time.
The callback/token audit covers starts of the configured writer (`devstral2-123b`), not separate embedding lookups, every retry beneath the SDK, or total billing. Embedding latency, when used by the existing engine, remains included in active time.

## Per-case outcomes

Each row contains three trials per engine. Correctness requires independently graded values and evidence that genuinely missing personal answers were asked.

| Case | Current median s | Experimental median s | Current correct / ready | Experimental correct / ready |
|---|---:|---:|---:|---:|
| text | 7.66 | 0.35 | 3 / 3 | 3 / 3 |
| travel | 8.89 | 0.71 | 3 / 3 | 3 / 3 |
| identity | 15.04 | 6.98 | 3 / 3 | 3 / 3 |
| date | 7.70 | 0.56 | 3 / 3 | 3 / 3 |
| uploads | 11.46 | 4.73 | 3 / 3 | 3 / 3 |
| conditional | 14.36 | 5.62 | 3 / 3 | 3 / 3 |
| regression-same-filename-documents | 15.40 | 4.77 | 3 / 3 | 3 / 3 |
| regression-fifty-fields | 219.59 | 43.41 | 3 / 3 | 3 / 3 |
| fresh-compiled-contact | 19.38 | 3.84 | 3 / 3 | 3 / 3 |
| fresh-compiled-shadow-education | 14.15 | 1.00 | 3 / 3 | 3 / 3 |
| fresh-compiled-frame-wizard | 19.92 | 5.75 | 3 / 3 | 3 / 3 |
| fresh-compiled-conditional-date | 15.59 | 4.27 | 0 / 3 | 3 / 3 |

## What changed and what the experiment found

- Both previously failing review boundaries now pass. Independent verification: **91 passed**, 13 dependency warnings, 182.10 s. This includes the exact three reviewer cases and surrounding forms/address/upload/approval/extraction checks. Claude reports 1139 full-suite passes; no new independent full-hour suite is claimed.
- The corrected production files and tests were captured in a shared checkpoint. The candidate retains its separately measured upload-cache repair. Both engines receive the same underlying source content; prepared-address facts are available to both.
- The four fresh cases use the real candidate compiler on synthetic structured applicant data: name/email components plus unknown interview preference, education in a closed author shadow root, contact information through an iframe/wizard transition, and an exact-day question with only a month in its source.
- The simulated owner reads only supplied facts or a declared missing-answer oracle. It uses entity context when supplied; ambiguous unscoped questions are not guessed. The grader never supplies an answer.
- The eight remaining cases are development/regression cases, including the original upload and 50-field cases. These are not fresh evidence of unseen-layout support.
- The conditional-date baseline failed in **3 of 3** trials. Inspect `trials.json` for its exact values and records. A month/year copied from a truthful source is still insufficient for a control requiring `MM/DD/YYYY`. The candidate asks for the missing day rather than assuming one.
- This is a filling-readiness finding. No submission was attempted, and it does not establish that a real portal would accept an invalid date after Submit.

## Interpretation and remaining gates

The principal tested speed mechanism is resolving known facts before invoking the model. The existing engine already inventories visible fields in a batch; these results do not show that merely scanning the page first caused the improvement.
Each fresh case has at most one genuinely missing answer at a time. Focused tests exercise multi-question batching, but this provider cohort does not quantify its live waiting-time advantage.
The native loop counters differ between engines; identical configured model budgets are not a strictly matched total resource budget. Order was counterbalanced by repetition plus fixture index (18 pairs per starting engine). Three repetitions, authored probes and synthetic pages are insufficient to generalize across all live ATS products.
No production adoption or merge is authorized by this report. Live paired drafts, actual personal-file/document compilation, company/referral/learned-answer input parity, custom widgets, desktop/authentication setup and portal coverage remain required before that decision. Preserve the existing user policies and submission approval boundary.
The previous saved-posting preflight did not supply matched US pairs across six portal families; several Workday URLs returned HTTP 521 in Docker. Live preparation must resolve that setup separately rather than calling listings or error pages completed applications.

## Evidence and reproduction

- `summary.json`, `trials.json`, `trials.csv`, `cases.json`, `source-manifest.json`, `audit.json` and `run.txt`.
- The original strict values/provenance scores are retained. The audit permits only formatting-equivalent telephone digits for the iframe/wizard case; it never repairs an answer, invents a date, chooses an unknown preference or ignores a missing source question.
- Telephone-format adjustments: **0**. Details, if any, are in `audit.json`.
- `PROTOCOL.md` freezes the cohort and interpretation before fresh provider results. The four layouts are fresh runtime probes authored with knowledge of the system, not an independent third-party blind test set. They are now development cases for future work.
- Reproduce from the measured commit with Chrome and the configured provider environment:
  `PYTHONPATH=src python -m jobagent.experiments.benchmark --model --cohort refreshed --repetitions 3 --output /tmp/NEW-ROUND2-RUN`.
- Every trial uses isolated data/state and browser context. No actual applicant, credentials, contact CSV, production database, real resume or real job application is present in these artifacts.
- Production source and `/app/src/jobagent` were not changed by this experiment.


## Additional post-measurement compiler boundary and repair

Two additional checks, outside the registered 72 trials, **failed on the measured candidate**:

- A Current-address City of Austin was chosen for a Work Experience City even though the source work row supplied Boston. The compiler had not retained row-level city facts, and an unscoped contact alias was allowed in an employer section.
- The applicant's Email address was chosen for a References 1 email field without a supplied reference email.

This is a material limit on the measured prototype despite its 36/36 result inside the registered cohort. Do not
interpret that cohort result as universal personal-file/compiler correctness. The checks are in
`test_scope_boundary.py`, with their original failures in `scope-boundary-before.txt`. No real form was filled.

A separate post-measurement repair, **`ba14d7b`**, retains structured row-level city/state/country facts, normalizes
common numbered education/employment/reference headings while preserving entity indices, and rejects general
contact components for those other entities in both direct resolution and model-selected fact mappings.
Missing entity-specific information is left for the owner rather than borrowed from the applicant.

The resulting focused run is **35 passed**, 13 dependency warnings, 72.56 s (the experiment suite plus both
independent boundary cases). See `scope-repair-tests.txt` and its source/test manifest. These tests do not
retroactively change the measured cohort, constitute a new provider speed result or establish complete hierarchy
recognition on arbitrary portals. Unnumbered/novel entity headings, compound personal facts and actual profile
compilation still need live validation. The latest prototype must be validated separately from the measured one
before production adoption.

`date-native-validation.json` independently replays only the observed date values into the unchanged registered
control: candidate `01/04/2027` is valid; current `January 2027` has `patternMismatch=true`. This diagnostic does
not refill or repair an agent trial and involves no real posting.

Text-log trailing spaces and CSV line endings are normalized in the Git copies. JSON measurements remain unchanged.


A follow-up compatibility repair, **`c6db5d4`**, also preserves an explicitly blank Address Line 2 as a known
value, so it clears a stale prefill instead of being treated as missing information. An absent component stays
unknown. The address and entity-source checks passed together: **8 passed**, 13 dependency warnings, 4.05 s.
See `address-source-tests.txt`. This is another post-measurement change, not a new latency measurement.
