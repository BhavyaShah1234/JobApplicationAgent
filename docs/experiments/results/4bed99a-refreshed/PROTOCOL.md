# Refreshed comparison registration — 2026-11-14

Shared production checkpoint: `54dbf90` (91 independent targeted passes, including all three reviewer cases).
Candidate upload-cache repair: `9d6aad1`, retained unchanged. Production commands do not import the experiment.

The next commit freezes this protocol, harness and `cohort_v2.py` before provider outcomes on its four fresh
layouts are observed. The original `2e5a46f` results stay unchanged. This is a new batch, not a pooled rerun.

## Cohort and source handling

12 fixtures × 3 paired repetitions × 2 engines = **72 trials**. Eight development cases: text, travel, school
identity, full date, native upload roles, dependent country/state, same-named documents, 50 scoped entity fields.
Four fresh layouts:

1. Structured name/email compilation with an unknown interview-format preference and disabled prompt.
2. Structured education compilation in an author-controlled closed shadow root.
3. Structured contact compilation across an iframe-to-wizard transition; a hidden ledger grades the first step.
4. A conditional start-date field whose source gives only a month and explicitly requires asking for the day.

Both arms receive the same underlying applicant source content, with prepared-address information available to
both where used. The candidate compiler is exercised on the four fresh cases; the baseline receives the raw
structured applicant data. The eight development cases retain their original scoped fact registry representation.
Canonical source differences and automatic normalization are part of the algorithm under test, not grader hints.

The simulated owner uses only declared missing-answer facts or supplied sources. It uses entity context when a
prompt supplies it and does not guess the entity from an ambiguous unscoped question. Expected grader values are
never used to answer questions. A required interview preference or exact day must actually be asked; getting its
value right by guessing fails the provenance criterion.

Engine order alternates by repetition PLUS fixture index: 18 pairs start with each engine. Each trial has private
state/data and a new browser context/model audit. Source and case hashes are recorded. No source edits during the
batch. Save failures, exceptions and incomplete trials, including correctly filled forms that fail to reach review.
No final application click, live application, mailbox, actual applicant file or real document is involved.

## Measurement and decision

Actual writer model (`devstral2-123b` in the pilot), identical configured model timeout/deadline/retry budgets on
both arms. Record actual model starts and reported tokens. Browser/control and discovery loop caps remain native
to each implementation (baseline up to 15 page loops, candidate up to 12 discovery/filling rounds); these counters
are not equivalent resource units. Report any exhausted cap explicitly. Do not claim a strictly total-budget-
matched comparison or hide this limitation. Authentication/navigation/document generation/ATS scoring are excluded.

Primary: active filling time (simulated replies have effectively no human wait). Also value correctness, required
source-question evidence, ready AND correct, false-ready, model calls/tokens, necessary and avoidable questions.
Adoption targets remain >=30% lower median active time, >=50% fewer avoidable assists, no correctness/completion
regression or unsupported personal answers. Equal aggregate completion can hide per-fixture regressions.

A pass on synthetic fixtures is insufficient for production adoption. Next gates are fresh/live validation,
actual personal-file compilation, comparable unsubmitted postings per portal family, and desktop/auth setup outside
the primary clock. Never repeat the submitted Carvana/Pantex applications or submit experimental drafts.

## Preparation evidence

- `shared-checkpoint/round2-verification.txt`: 91 passes (production and reviewer acceptance checks).
- `round2/focused-tests.txt`: 26 passes on the integrated experiment before the final additional harness case.
- `round2/harness-tests.txt`: 3 passes after the final scoped-responder change; 27 unique experiment tests overall.
- `round2/pilot-summary.json`: four development-only real-provider trials; both arms 2/2 correct and ready.
  The pilot is not pooled with the registered batch and did not run any fresh layout.
