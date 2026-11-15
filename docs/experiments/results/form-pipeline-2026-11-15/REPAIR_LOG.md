# Form-planning repair and watched validation — 2026-11-15

Status: implementation and validation in progress. Experimental remains opt-in. No application submissions are authorized by this comparison or performed by its harness.

This follow-up retains the failed November 14 cohort and all subsequent diagnostic attempts. No speed claim or promotion follows from a partial, source-incorrect, assisted, or merely `ready` run.

## Implementation changes being validated

- Classify questions and select evidence per field before the existing batched writer. Log routes; do not let a field borrow another field's fact IDs. Keep owner preferences/consents distinct from resume-derived qualifications.
- Retain full native option catalogues, explicit catalogue completeness, and native values. Custom menus expose their rendered subset; search factual identities and owner-supplied variants. Reject unavailable choices in complete catalogues.
- Wait for visible loaded dropdown choices rather than hidden nodes belonging to closed pickers. Preserve catalogues for unchanged controls and invalidate dependencies when geography changes.
- Keep physical control references stable across numeric renumbering. Re-read an individual control after writing; refresh the full view after structural changes. Add all known education/work rows before answer planning.
- Batch learned-answer embeddings and cache source compilation. Use the same structured-output protocol as the original strategy. Preserve word limits, exact date rules, and purpose-specific personal history.
- Require the expected document bytes and owned upload evidence. Dispatch once and poll. Reject visible errors/busy widgets and acknowledgements from a different widget. A retained input without a filename must belong to its actual submission form or widget; an unrelated temporary body input is not sufficient.
- Preserve the real residence state separately from the application-specific address for residency predicates.
- Persist partial metrics during cancellation and bound individual model calls identically in both strategies.

## Watched diagnostic attempts

| Attempt | Outcome | Consequence |
| --- | --- | --- |
| Ashby smoke v1 | Draft correct after two avoidable sourced questions; upload, calendar and authorized acknowledgements visually inspected. | Fix observable account-email and numeric-years rules. Exclude from unassisted success and paired timing evidence. |
| Greenhouse smoke v1 | Rejected: unrelated Additional Information copied into optional SAT/ACT answer; second degree omitted. | Tighten evidence routing and associate Add another toolbar with its owned entity group. |
| Greenhouse smoke v2 | Both education rows correct, no failed dropdown writes, link/date resolved without assistance; rejected for unrelated conference-history answer. | Require purpose-specific conference/account history, with the same history guard in the original strategy. |
| Greenhouse smoke v3 | Passed source and visual audit, zero avoidable assistance. | Retain as an unmeasured correctness smoke; optional unknowns stayed blank. |

The SAT/ACT score and conference history are genuinely missing optional facts. They must remain blank unless the owner supplies them; they must never be synthesized from hobbies, previous applications, or future-contact permission.

## Validation checkpoints

- Earlier integration snapshot: 191 passed (older checkpoint, not a claim about the final source).
- v4 core: 94 passed.
- Forms/upload/approval/browser suite after shared-proof correction: 81 passed. Later ownership and menu changes still need their relevant checks.
- v8 pipeline regressions: 26 passed.
- Later core/targeted checks and final paired live comparison pending.

Private screenshots, source snapshots, fixtures, answers, network-related artifacts and detailed metrics are under the ignored `outputs/verification/form-pipeline-2026-11-15` directory. They are not committed.

## Subsequent evidence

- Greenhouse smoke v3 passed independent full-page visual and per-field source inspection: both education rows, GPA, offered preferences and date quarter, required answers, and owned resume/cover-letter byte proof. Zero avoidable questions and zero failed writes. Two genuinely unknown optional questions were skipped. It is a correctness diagnostic only because local checks overlapped it.
- Lever smoke v1 is rejected. It needed an avoidable location assist because hidden status text polluted the label. Later screenshot review also showed an unverified resume-parser website prefill. The saved Portfolio / website answers Other website; that question was avoidable too. The initial blank-field approval was incorrect and is explicitly corrected in the private audit, retaining the raw event and original attempt.
- Native/wrapping labels and unlabelled fallback text now filter hidden original DOM nodes rather than detached clones (which lose CSS visibility), and exclude option subtrees. A country catalogue can disambiguate a generically named Location selector. Custom choices within a checkbox question belong to that question, rather than becoming another question.
- Website variants are classified as the same explicit owner website source; the saved canonical URL replaces unverified parser prefills. Identity readback also rejects similarly named institutions and people.
- v9 core checkpoint: 106 passed. v10 focused checkpoint: 33 passed. v11 passed 97 before a missing public test fixture stopped the forms suite; the snapshot packaging was corrected, and the forms suite then passed 26. v12 targeted checkpoint: 50 passed. v13 pipeline/forms checkpoint: 61 passed. v14 latest pipeline/forms/observed-regression checkpoint: 79 passed. These are checkpoints, not additive independent test counts.

## Timed comparison protocol

Freeze one source version and the same input fixtures for each original/experimental pair. Run arms sequentially without concurrent local tests or model preparation. Use fresh browser contexts with no submitted application. Alternate order: Ashby original/experimental, Greenhouse experimental/original, Lever original/experimental. Retain every attempted arm and reject source-incorrect or incomplete pairs.

Use passive read-only CDP recording while filling, plus full-page visual/source inspection at each completed page. Blocking per-action screenshots are used for diagnostics, not the timed cohort: asynchronous site work can progress during a screenshot, so subtracting all such screenshot time could overstate autonomous speed. Report raw active time and observation-adjusted active time; human wait and page inspection are separately recorded. Genuine missing facts are distinct from avoidable assistance.

The selected third family is Spotify's active New York Lever posting, using an existing agent-generated backend resume identically in both arms (no company-specific cover letter reused). Preflight exclusions are retained: the Palantir posting needs unresolved clearance eligibility; Zoox needs citizenship-status dates; Wealthfront needs client history; Veeva requires a transcript and an owner safe-sender action. No filling outcome was used to exclude those preflights.


## Frozen candidate for paired runs

The final pre-cohort checkpoint passed 83 pipeline/forms/observed-regression checks. One additional regression verifies that the writer receives all 351 available options rather than an unusable catalogue-search marker (passed). Earlier full strategy tests passed before an unrelated missing public fixture stopped the following forms file; that packaging issue was corrected. The 81-check upload/approval/browser checkpoint and subsequent targeted ownership regressions remain part of the evidence. Counts from overlapping checkpoints are not added together.

Lever smoke v2 passed full-page and per-field inspection with zero questions, including the canonical Other website value and newly revealed demographics. It remains unmeasured because local checks overlapped it. The final candidate also renders explicit Male/Man and Not a veteran/No equivalents locally; Not a protected veteran does not establish No to a general veteran question.

The writer receives the complete *available* option catalogue whenever an unresolved field needs generation. Native catalogues are complete. Asynchronously loaded/virtualized menus remain explicitly partial; source-driven searches use only supplied literal values or approved source variants. Exact known matches bypass the model entirely.


## VPN reset and additional regression repairs

The owner reported that the VPN had been disconnected during earlier runs. All pre-reset timings are excluded from the final comparison, including successful diagnostic arms. The failures and source audits remain recorded. The writer and primary embedding endpoint both passed actual post-reset requests; the writer used Voyager devstral2-123b with function calling, and embeddings returned 4096 dimensions without fallback. Connection errors and timeouts now emit an explicit VPN/provider diagnostic.

Subsequent repairs preserve exact institution identity while accepting its own initialism plus institution-type suffix; they accept an explicitly selected Other without treating it as an alias for an actual institution. Conditional unlisted-school fields are reconciled after parent school selections in the original strategy as well. Qualification evidence retains its parameters (for example, overall experience is not C++ experience), contact records stay scoped to their own person, and historical residency requires explicit evidence. New human rules are applied immediately in a bounded planning pass; boolean and multiple-choice JSON answers retain their types.

An existing browser regression exposed school answers leaking between wizard steps. Entity-specific human literals now bind to their record or current step/control, and are not persisted as unscoped global learned answers. A fresh v23 suite covering the pipeline, observed regressions, structured model calls and all experiment tests passed **134 tests in 165.21 seconds**. The preceding v22 broader run passed 195 tests before catching that regression; it is retained as a failed checkpoint, not described as a passing suite. Earlier strategy/forms checks passed in that run before the affected experiment test. Checkpoint counts overlap and are not added together.

The post-VPN cohort uses fresh, identical retrieval caches, 600-second model-call limits and 12-minute active budgets. Both strategies run sequentially under the same frozen source with passive CDP recording and a full-page source/visual inspection gate. No submissions occur. Results will be recorded separately under the post-VPN cohort report.


The first post-VPN candidate (17b6a1f, snapshot v23) produced a correct Ashby pair and a correct Greenhouse experimental draft. Greenhouse original incorrectly selected Q2 2027 instead of the owner-authorized Q4 2026 start quarter and still claimed readiness. Independent inspection rejected it; its measured artifact and false-readiness outcome are retained. The original enforcement pass now applies deterministic owner start-date and preferred-name decisions before and after its model checker. A new regression deliberately supplies contradictory generated answers, verifies the owner rule wins, and verifies a newer explicit human answer still takes priority. The measured cohort is restarted on snapshot v24; the rejected draft is not counted as a valid completion pair.


The v24 broader check caught a second regression after 117 passes: a saved contact preference was filtered out because the referrer classifier also matched the middle of the word preferred. Referrer detection now requires a word boundary. The focused reproduction passed (2 tests), then the final v25 suite passed **221 tests in 308.61 seconds**, covering source routing, both strategies, forms, model error handling, observed regressions and all experiment tests. Snapshot v25 is the frozen runtime for the restarted live cohort. No measured portal arm overlapped that suite.


The next watched baseline (Lever original on v25) left an unverified HTTP resume-parser prefill in Other website despite a canonical HTTPS owner source. It is rejected under the same standard as the earlier experimental smoke; required fields and upload alone are insufficient. Original enforcement now resolves this website family from its owner source, so the value is actually written and verified. The new regression plus related original/website checks passed (5 tests, 4.62 seconds). Experimental execution is unchanged by this baseline-only enforcement repair. The final matched cohort is restarted on v26, retaining all earlier attempts; its six runs use one source snapshot.
