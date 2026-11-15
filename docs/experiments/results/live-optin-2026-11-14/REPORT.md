# Opt-in production filling: live portal comparison

Decision: **retain the original default and keep experimental filling opt-in**.
Both methods were exercised on four real applications. The live results do not
establish that the experimental method outperforms the original. Nothing was
submitted; every run omitted the submission hook and used additional trial-only
submission guards.

## Results

Active time excludes recorded question waiting, login/navigation, address lookup
and document preparation. These are individual draft attempts, not estimates of
performance across all jobs.

| Portal | Original active seconds | Experimental active seconds | Avoidable questions, original / experimental | Independent outcome |
|---|---:|---:|---:|---|
| Greenhouse, DoorDash | Not retained after operator stop | 244.8 | 1 / 1 | Required internship selector cannot represent the saved zero count; original stopped, experimental blocked. No completed timing comparison. |
| Ashby, OpenAI | 108.3 | 135.9 | 0 / 5 | Original's ready claim contains an unsupported coding-time estimate; experimental blocked. Neither is a correct completed pair. |
| Workday, Symbotic | 463.5 | 101.1 | 1 / 3 | Original reached My Experience; experimental stopped on My Information. Ten initial values were restored from the earlier server draft. Timing is confounded. |
| Custom, Triumph Tech | 108.2 | 69.6 | 0 / 2 | Both produced source-supported filled drafts, with two operator/oracle answers needed by experimental. Final action untouched. |

The custom pair's experimental **active** time was 35.6% lower, but its elapsed
filling time was **225.7 seconds versus 108.2 seconds**, including **156.0 seconds
waiting for two avoidable answers**. It handed answer-writing work to the source
oracle. That is not a faster autonomous completion. Both methods started three
writer calls on this pair; experimental reported 27,724 writer tokens versus
35,715 for original, excluding the external oracle's work.

Only **one** source-supported completed draft pair across **one** portal family
was available, with operator assistance on the experimental arm. The gate
requires at least three comparable completed pairs across three families and
fewer avoidable assists. It failed. Failed/shorter runs and server-prefilled runs
cannot establish a speed win. There is no basis for deleting the original method.

[metrics.csv](metrics.csv) records all eight assigned attempts: active and elapsed
time, waiting, logical questions and prompt events, necessary/avoidable questions,
writer starts/tokens, record counts, readiness claims and independent grades.
[comparability.json](comparability.json) checks paired manifests and initial states.
All four pairs had identical input and production-source hashes; only Workday had
different initial values. Public manifests omit personal field values.

Across these unequal/incomplete attempts, the logs contain three original prompt
events with three logical questions, versus five experimental prompt events with
14 logical questions. Classified avoidable questions were **2 original versus
11 experimental**. Three experimental questions needed a missing preference or
owner acknowledgements; one original question was a portal/source conflict.
These totals are descriptive, not workload-adjusted rates. Batching alone did not
offset missing source bindings and repeated questions.

## Cohort and controls

Four job applications were assigned: DoorDash (Greenhouse), OpenAI (Ashby),
Symbotic (Workday), and Triumph Tech (custom). The order within pairs alternates:
original first for Greenhouse/Workday, experimental first for Ashby/custom.
The model is the configured production writer, `devstral2-123b`.

Each arm uses the real production `apply.run` workflow, fresh Chrome client state,
private copies of the same applicant/learned/contact data and generated documents,
the same resolved JD and preselected geocoded address. Account setup and address
acquisition happen before the filling clock. Company research is frozen per job.
The harness records input/source SHA-256 manifests and the initial fields on each
visited page. Workday server-restored drafts must be checked separately: fresh
browser state alone does not make that pair comparable. The comparison used a
frozen production filling snapshot from `c092e1a` plus the shared detector fix
`005cdce`; later upload/contact safeguards were evaluated separately. The core
production hashes match between the two arms of every assigned pair. Trials
after Greenhouse also retained independent native-validity observations. The
later harness revisions enforce a 30-minute active-time ceiling; no assigned arm
hit it.

An operator answered questions only when asked, using frozen records or an
already supplied owner rule. Those answers **count as assists**, even though
Codex supplied them rather than asking the owner to repeat known information.
Missing coding preference and acknowledgements were left unset, not invented.
Writer telemetry excludes embeddings, internal SDK retries, setup and the source
oracle's answer-generation work. Raw prompt classifications remain alongside
independent audits; the CSV contains reviewed classifications. In particular,
the custom commuting question was reclassified as avoidable after confirming the
saved current city and on-site preference. A preferred-name source-path annotation
was also corrected; its supplied value was present in the preferred-name entry.

The adoption gate, committed before the comparison, requires at least three
comparable completed pairs across three portal families, no fabricated answers or
false readiness, no required-field correctness/completion regression, at least 30%
lower median active filling time, and at least 50% fewer avoidable assists (or zero
experimental assists if the original has zero). Failed runs cannot establish a
speed improvement. All attempted outcomes remain in the report.

## Shared setup findings

- A first Greenhouse setup attempt stopped before filling because a reCAPTCHA
  branding badge was mistaken for an interactive challenge. The common detector
  now excludes the provider's badge container while retaining checks for actual
  checkbox/challenge frames. Twenty-two browser/controller checks passed. Both
  measured arms use this common fix; the preliminary attempt is retained as a
  setup diagnostic, not a measured completed arm.
- Workday signed in using the existing account. No account creation was needed.
- A Dayforce posting was considered during read-only preflight and explicitly
  reported that it had expired. It was excluded before assignment to the cohort.
- DoorDash offers only one or two-plus internships while the frozen profile says
  zero. This required-field mismatch must remain unresolved, rather than selecting
  an inaccurate answer. Neither arm completed this application; its attempts do
  not form a completed timing comparison.
- The first custom setup attempt exposed a harness bug decoding a compressed
  binary request as UTF-8. It failed before filling, with zero active filling
  seconds. The guard now reads request bytes safely. Both assigned custom arms
  used the corrected harness. The failed setup attempt is retained separately.

Private evidence lives in `outputs/verification/live-optin-2026-11-14/` in the main
checkout. It contains personal data and is excluded from Git. It includes private
screenshots, run logs, manifests, before/final views, planner-call logs and audits.
Workday's unsent server draft is retained; it was not withdrawn or deleted.

## Confirmed findings from the live pilot

1. **Readback is not source correctness.** The original Ashby run reported ready
   after entering `50` for the percentage of time the candidate enjoys coding.
   Its own reason says this was estimated from resume experience. No supplied
   preference supports that number. Native validation passed, but the independent
   audit rejects the readiness claim. Do not score this as a correct completion
   or count the absence of questions as successful autonomy. Track source support
   separately from control acceptance and block unsupported required answers.
2. **Fact retrieval needs a separate choice-rendering step.** The experimental
   Greenhouse run retrieved known facts but tried values such as a plain country,
   city/state, GPA number and veteran-status wording that the controls did not
   offer literally. Its strict selectors refused them. Use one bounded rendering
   pass over the actual choices with the supporting fact, followed by source
   validation. Preserve institution/person identity rules; never fix this with
   broad fuzzy matching. Numeric ranges and location/phone-code formatting need
   explicit support. An unoffered internship count must still remain unresolved.
3. **Transient headings are not stable field identity.** In Ashby, a date picker
   added a `November 2026` heading. The experimental question cache then treated
   the same three unanswered controls as new and asked again. Exclude overlay
   headings from section discovery and prefer stable field identifiers plus
   explicit repeating-row identity. Keep real education/employer row boundaries;
   merely dropping section identity would reintroduce cross-entity mistakes.
4. **File dispatch, portal acknowledgement and retained native bytes differ.**
   Both portals can replace/clear their native file inputs after accepting a PDF.
   The original verifier waited on a stale Greenhouse input; the experimental
   verifier could not prove retained bytes in Ashby. Record role-specific upload
   provenance at dispatch and bind the portal acknowledgement to that same widget.
   Keep the same-name/wrong-widget regression checks. A filename elsewhere on the
   page is insufficient evidence.
5. **Generic upload labels must not erase document roles.** In Greenhouse the
   experimental run skipped the native upload controls whose associated label
   was only a list of accepted file types. Their native `required` flags were
   false despite the visible resume requirement. A tested follow-up now ignores
   generic captions and retains a verification obligation for every recognised,
   provided document, even if its control disappears. Unverified uploads block.
   This safeguard is in the opt-in feature; the frozen timed cohort predates it,
   so these measurements do not establish the updated version's performance.
6. **Company-specific referral wording needs the saved rule.** Workday offered
   `Symbotic Employee`, but the original method asked the operator to choose the
   source. The standing employee-referral policy already covers it. Recognise a
   company-qualified employee source without broadening the rule to unrelated
   recruiter or contractor options. Keep referrer contact details bound to the
   correct company's CSV row.
7. **Source compilation must preserve structured answers and conditional rules.**
   Experimental asked for a saved preferred name, a known blank phone extension,
   and a former-employment answer already covered by an instruction. Structured
   preferred-name/phone aliases are now covered by regressions. Conditional
   entries still need a rule-binding representation: excluding their literals
   from direct facts is safe, but their instructions must remain discoverable
   even when a portal phrases the question differently. Never turn a conditional
   literal into an unconditional fact merely to remove a question.
8. **Narrative routing cannot depend on a few words.** The custom form's question
   about experiences in the past three years did not match the experimental
   `why|describe|tell us|additional information` route, despite the supplied
   resume answering it. It asked the oracle to write the answer. Bind prose
   questions to available documents/facts, then check the claims, rather than
   relying on this wording list. Support answers that combine several facts.
9. **Keep source support, accepted controls and progress separate.** Workday's
   experimental run counted all 14 records verified but still exhausted its
   repair budget on the phone-country picker and never advanced. Preserve a
   source-validated existing value when it is already correct; avoid blindly
   editing a collapsed control. Store rendered/accepted values separately from
   canonical facts. A raw verified-record count is not an independent completion
   grade. A field-level repair failure should also not prevent filling other
   newly discovered, answerable controls before the page is reported blocked.
10. **Resolve the Workday university picker without changing institutions.**
    The original method filled the work-history rows and other education values,
    but the required school picker repeatedly rejected the saved name/variants.
    Check picker option discovery and supported search interaction. Represent
    an owner-approved “not listed/Other” fallback separately from institution
    aliases, and supply the actual name in a follow-up field where offered.
    Do not relax identity matching to select an unrelated school.

## Follow-up safeguards and validation

The opt-in implementation now retains document obligations across dynamic file
captions and tracks unnamed native inputs by DOM node identity. It still requires
the supplied role's exact PDF bytes; a missing/replaced opaque input without
sufficient proof blocks. Tests cover generic captions, disappearing inputs,
changing filenames, named/unnamed inputs, structured preferred names and explicit
blank extensions. This prevents the stricter safeguard from confusing a filename
change with a missing upload.

A first post-cohort smoke check caught and safely blocked the unnamed-input
case. After the fix, a second smoke check of the shipped experimental code on
Triumph Tech verified **11/11 fields**, including the exact resume SHA-256,
and reached review with **no blockers**. It still needed two source-oracle
answers. Its 66.9 active seconds are **unpaired smoke data**, not new promotion
evidence. Both smoke attempts are retained outside the assigned eight-attempt
comparison; the first is not hidden or counted as successful.

Validation included **109 integration regression tests**, **22 browser/controller
tests** for the shared detector fix, **47 focused tests** after the upload
safeguards, **13 strategy integration tests** after the unnamed-input change, and
**14 final strategy integration tests**, including recorded-strategy resumption
through the public workflow API.
Submission was not tested or performed. The original algorithm remains available
and the default; the experimental selector uses the same approval/submission
boundary and does not silently fall back to another filling strategy.

Before another promotion trial, address the unresolved routing/rendering/identity
and picker findings above, retain the regressions, prepare matched server-side
starting states, and freeze a new cohort. Repeat the comparison on fresh active
postings with missing personal answers supplied consistently when asked. The
earlier synthetic results and this single successful custom pair do not establish
general superiority across real portals.
