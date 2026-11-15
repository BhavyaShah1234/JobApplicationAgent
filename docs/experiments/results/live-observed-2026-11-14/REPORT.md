# Watched live follow-up: original versus experimental filling

Decision: **do not promote experimental filling or remove the original engine**.
The watched follow-up has already exposed a false readiness claim, incorrect
residency, unsupported sharing permission, source-binding errors and upload
failures. The original engine also has confirmed defects. Keeping its default
is not a claim that it is correct on every portal.

All six measured draft attempts have ended across three portal families. The
original Lever arm left clearance eligibility unanswered while the owner question
remained pending, and continued only the independent fields. External partner
sharing permission was also requested by the independent observer and remained
absent from the frozen inputs. No application was submitted.

## Results

Times below exclude recorded human waiting and trial-only screenshot/inspection
overhead. Raw times, observation time and writer telemetry are retained separately.
Failed, stopped or incorrectly filled arms are not completed speed comparisons.

| Portal / application | Original active seconds | Experimental active seconds | Avoidable questions, original / experimental | Independent result |
|---|---:|---:|---:|---|
| Ashby / OpenAI | 96.8 | 101.9 | 0 / 2 | Both blocked from readiness by duplicate Resume references despite visibly filled required controls. |
| Greenhouse / Stripe US | Missing after operator stop | 647.9 | 0 / 9 | Original searched a university in Degree after upload-induced renumbering; stopped. Experimental selected the right controls but blocked upload verification and omitted the second education row. |
| Lever / Palantir US | 182.4 | 214.8 | 1 / 6 | Experimental falsely claimed ready. Original correctly chose non-California residency and left missing clearance eligibility blank, but also selected unsupported external sharing; not ready. |

There are **zero independently correct, completed pairs** in this follow-up. Do not compute
a median completed-pair speed improvement from these unequal outcomes. Experimental
Lever's `ready=true` is a **false readiness claim**, not a successful completion.

Ashby experimental used two writer starts and 50,176 reported tokens; original
used two starts and 36,017 tokens. Greenhouse experimental used five starts and
102,298 reported tokens, including a request that errored after approximately
300 seconds before retrying. Lever experimental used three starts and 79,540
reported tokens; original used four starts and 66,855 reported tokens. Reported tokens exclude embeddings and tokens not returned for
failed requests; writer starts include failed invocations.

[metrics.csv](metrics.csv) records all six matched arms and the initial unpaired
failed diagnostic, including raw/adjusted active time, elapsed filling time,
observation overhead, question waiting, prompt events, logical questions, writer
starts/tokens, record counts and independent grades. The three experimental v2
arms asked **17 avoidable logical questions in four prompt events**. Original
asked **one avoidable and one necessary logical question in two prompt events**;
the genuine eligibility question was left unanswered. These totals describe
incomplete workloads, not success-adjusted rates. Experimental's zero necessary
questions is not a success: it supplied unsupported answers instead of asking
about clearance eligibility and external sharing.

Lever experimental elapsed filling time was 371.6 seconds, with 85.0 seconds
of question waiting and 71.8 seconds of visual observation. Original elapsed
was 495.6 seconds, with 240.1 seconds of question waiting and 73.2 seconds of
observation. The original wait includes the owner's unanswered eligibility
question before continuing independent fields. Faster elapsed time from
supplying unsupported answers is not a completion advantage. Greenhouse
experimental elapsed filling was 845.7 seconds, including 59.0 seconds of
question waiting and 138.8 seconds of observation. Its 647.9 adjusted active
seconds include the failed model request and retry.

## What was actually watched

The real job-agent workflow and its configured `devstral2-123b` writer performed
the filling. Codex did not type application answers directly into the browser.
Trial instrumentation saved screenshots after actions, a read-only CDP filmstrip,
native-control snapshots and a gated page-end inspection. Codex inspected actual
calendar/location selections, education controls, attachment displays, consent
choices, source records and written answers. Additional screenshots captured the
whole Lever questions to establish which radio choice belonged to each question.

When the agent asked about an answer already in the frozen sources, Codex supplied
it through the normal question interface. Those answers count as **avoidable
assists**, even when they are grouped into one prompt or include skipping an
optional unknown field. They are not autonomous resolutions. Missing owner facts
are classified separately. The independently asked external-sharing question is separate from the engine
prompt counts; neither engine requested that missing permission.

Three genuine setup answers were obtained during the initial watched Ashby
attempt: the owner's coding percentage and two explicit acknowledgements. Both
restarted Ashby arms received the same posting-specific answer sheet. These are
three owner clarifications for the overall process, not zero-assistance evidence.
The acknowledgement permission applies to this unsubmitted test only.

## Controls and deviations

The measured code is frozen at `8866ff9`, with the same runtime and observation
harness for every v2 arm. Fresh Chrome client contexts, the same frozen candidate
files and learned-answer snapshot, identical generated documents, pinned validated
addresses and frozen company research are used within each pair. Source/input
SHA manifests and first-page values match for all three pairs. The [comparability file](comparability.json) confirms Lever matches too. No performance tests run concurrently
with the measured browser arms.

Order: experimental then original on Ashby; original then experimental on
Greenhouse; experimental then original on Lever. Address acquisition, document
preparation and account/navigation setup are outside the filling clock. Stripe
and Palantir US use an unchanged, previously generated resume for the same
company and job title; their cover letters were regenerated by the real agent
for the active US postings. This measures filling, not resume quality or ATS
scoring. Public postings:

- [OpenAI / Ashby](https://jobs.ashbyhq.com/openai/3c67f712-697d-48d8-b05c-01be896e61da/application)
- [Stripe / Greenhouse](https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=8128744)
- [Palantir / Lever](https://jobs.lever.co/palantir/ab7e3425-81d5-4705-a7b5-cd60c8a45cdb/apply)

Workday was considered as a fourth pair. Authenticated preflight restored 14
existing My Information controls from an unsent server draft. Repeating against
that account would change later-page state between arms. It was excluded before
measured filling rather than resetting the existing draft or treating a warm
wizard as a clean comparison. Its account/navigation artifacts and the protocol
deviation remain in the private evidence. The measured follow-up spans three
portal families. Previously submitted postings were excluded; unavailable and
non-US candidates were screened during preflight, not counted as successful tests.

The original Greenhouse arm was explicitly stopped after the wrong-control
search. SIGTERM did not promptly unwind the browser operation. Only its trial
browser and process were terminated. Final active-time telemetry was lost; the
report leaves that time missing rather than estimating it from wall time.
Per-action evidence and model invocation timestamps remain.

## Initial failed attempt and corrections

An earlier `ashby-experimental-v1` diagnostic was stopped before the matched v2
cohort. It typed a non-date list fallback into a calendar, failed the known city
selection and requested known answers. It retained 106.4 adjusted active seconds,
two prompt events, six logical questions: three necessary owner clarifications
and three avoidable questions. It is not paired with a v1 original arm and is
not used to claim a speed improvement.

Its initial sponsorship finding was **withdrawn** after re-reading the frozen
profile: current and future sponsorship were both No, so the entered No matched
the source. A preventive combined-timeframe regression remains useful, but that
was not a confirmed live wrong answer. The correction is preserved in the audit.

Commit `8866ff9` adds control/date/location/required-marker fixes and fuller source
context. The restarted runs visually confirmed the real calendar selection and
same-city/state autocomplete rendering. They also showed that known-rule mapping
still asks avoidable questions. Passing those control regressions did not prove
full workflow correctness.

Verification: 169 broad checks passed in an isolated snapshot. Two additional
checks initially failed because their fixture file was absent from that snapshot;
they passed when rerun with the fixture alongside the newest regressions and
observer checks: 21 checks passed. These counts overlap and must not be summed.

## Corrections required before another promotion attempt

[FOLLOW_UPS.md](FOLLOW_UPS.md) gives evidence, affected components, reasons and
regression expectations for seven open findings. The priorities are:

1. Validate the meaning and purpose of bound evidence; block false readiness.
2. Rebind original actions after DOM changes, including upload-related renumbering.
3. Reconcile upload triggers/schema/native inputs and retain owned acknowledgements.
4. Recover styled question/section text and preserve repeating education entities.
5. Resolve known rules and skip optional unknowns without avoidable prompts.
6. Enforce explicit writing constraints independently of factual grounding.
7. Preserve telemetry and bound cancellation across browser/model waits.

The promotion gate remains at least three comparable correct completed pairs
across three families, no fabricated answers or false readiness, no required
correctness/completion regression, at least 30% lower median paired active time
and at least 50% fewer avoidable assists (zero experimental if original has zero).
The current evidence fails the correctness and completion requirements before
any speed threshold can be assessed. Retain both engines and the explicit opt-in.

Raw personal data, resumes, browser states, prompts, screenshots and answers
remain private under `outputs/verification/live-observed-2026-11-14/`; they are
not included in Git. Resume-grounded answers are checked against supplied files,
not independently verified as real-world biographical claims. Native validity
of an individual unchecked member of a framework checkbox group is not treated
alone as proof of group failure; no final submit/remote validation was attempted.
