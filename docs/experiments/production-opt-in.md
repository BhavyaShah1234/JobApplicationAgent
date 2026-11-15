# Source-routed field planning

New applications use staged discovery, source resolution, batched questions and verified
filling by default. The [post-VPN three-portal comparison](results/form-pipeline-vpn-2026-11-15/REPORT.md)
passed the rollout gate on November 15, 2026. These explicit commands select the same method:

```sh
jobagent apply outputs/COMPANY/JOB_ID COMPANY --filling-strategy experimental
jobagent run POSTING_URL --filling-strategy experimental
```

`submit` accepts the same option. Submission still uses the existing review, approval and
submission checks. Setting `FILLING_STRATEGY=experimental` selects it for new runs;
`experimental` is the default. An application's recorded strategy is retained on resume unless
explicitly overridden. A failed experimental fill blocks; it does not silently change engines.

The runtime is in `jobagent.filling`; it does not import the experiment harness. Both
strategies share application navigation, account handling, applicant sources, referrals,
address selection, form controls, challenge handling, checkpoints and submission guards.
The experimental planner discovers the current reachable controls, prepares an answer
plan, asks about missing answers together, fills and verifies, then repeats when conditions
or wizard pages expose more controls. It cannot inspect unrequested server-side pages.

The [four-portal live pilot](results/live-optin-2026-11-14/REPORT.md) did not meet the
promotion gate. The recorded choice-rendering, source-binding and field-identity
failures motivated the regression fixes below. Those fixes do not change the historical
measurements. The later post-VPN cohort linked above passed after the recorded repairs;
the original method also produced an unsupported preference in that pilot.

The subsequent [watched three-portal follow-up](results/live-observed-2026-11-14/REPORT.md)
also failed promotion. It caught a false readiness claim, wrong residency,
unsupported external sharing in both engines, upload verification failures and
stale original control handles. [Recorded corrections](results/live-observed-2026-11-14/FOLLOW_UPS.md)
are retained as historical findings; the current repairs and validation are linked above; neither an observed attachment nor a verified readback proves that the
answer is supported for the right question or consent purpose.

`application.json` records `filling_strategy` and `filling_metrics`. Active filling time
starts after navigation/login and excludes recorded human waiting and the optional
post-fill observation period. Writer invocation/token counts exclude embedding requests
and internal SDK retries. Prompt counts require manual classification into necessary,
avoidable, challenge and technical assistance; a batch is not one logical question.

## Live comparison protocol

Use `python -m jobagent.experiments.live_compare --help` in the configured container.
The harness launches the real production workflow with the selected strategy, a fresh
Chrome context, private copies of the same candidate/job files, and no submission hook.
Additional trial-only browser guards block final submission. It never solves CAPTCHAs.
Account setup, document generation and address acquisition belong outside filling time.
Record any account setup assistance separately.

Freeze source and inputs before the first arm; alternate the arm order across portals.
Audit source correctness independently of the filler’s readback verdict. Record every
attempt, including blocked, unavailable and timed-out ones. A repeated application with
server-restored answers is confounded unless both arms demonstrably started alike;
fresh cookies alone do not establish that.

Before changing the default, require at least three comparable completed pairs across
three portal families, no fabricated answers or false readiness, no required-field
correctness/completion regression, at least 30% lower median active filling time and at
least 50% fewer avoidable assists. If the original needs none, the experimental method
must also need none. Include all attempted portal outcomes in the decision. This is a
small rollout gate, not proof that every future portal will work. It passed in the linked
post-VPN cohort. Keep both explicit strategy choices for comparison and rollback, and
preserve recorded strategies when resuming existing drafts.

Private screenshots, resumes, prompts and answers stay in ignored `outputs/verification`.
Only redacted measurements and findings belong in Git.

## Regression fixes after the pilot

The runtime and the standalone candidate now share the same source-bound planning
pass and independent evidence check. Complete structured source records, conditional
rules and multiple fact IDs are available in that pass. Prose is considered for
general experience textareas, while personal preferences, consent and sensitive
history require owner evidence. Unconditional literal rules can resolve without
model calls. A known blank is distinct from an unknown default.
Saved answers retain company scope and enter as facts rather than answers from a
second planner. Explicitly permitted web/place/maps lookups are bounded, cached and
checked together with the owner rule that requested them.

Field identity is captured with the DOM snapshot and includes frame/document identity,
author identifiers or unambiguous question context, control kind and entity scope.
Popup headings do not replace employment/education sections. The planner, retry cache
and adapter audit use the same identity. Verification records persist across same-page
retries; server validation invalidates affected controls. A rejected control exhausts
its own repair budget while independent and newly revealed controls continue filling.

Date rendering preserves source precision and requested token order/separators.
Country/dial-code choices and unambiguous numeric brackets have deterministic
renderers; other semantic rendering goes through the source check. Strict choices
and explicit institution variants retain strict matching, including recursive variants.
Company-qualified employee referral options are recognized without accepting recruiter
or nonemployee options.

Passive discovery starts before navigation and accepts JSON responses without a
Content-Length header, subject to the existing parsing limits. Explicitly applicable
schema questions join the visible questions in one planning/HITL batch and can be
reused when they become visible. Parent branch conditions are preserved. Hidden
branches without established applicability are not questions for the applicant yet;
the agent still cannot inspect unrequested server-controlled pages.

Upload verification accepts retained native bytes, or captured dispatched bytes plus
a newly appearing filename acknowledgement inside the same exclusively owned widget.
An old acknowledgement or a filename elsewhere does not verify an upload. Lazy
file-chooser inputs are associated with their initiating widget. Upload polling has
bounded locator timeouts. Widgets whose ownership or acceptance cannot be established
remain blocked for review.

Shared safeguards also changed: a failed source checker no longer keeps unchecked
answers, a generic matching value elsewhere in the profile does not override a
rejected conditional rule, and unvalidated prefilled values are not counted as verified.
Required unverified prefills block readiness. Per-call experimental timing excludes
only that call's human waiting; whole-application totals remain cumulative.

Validation uses isolated Chrome fixtures with synthetic candidate documents. These
tests exercise the production adapter as well as the standalone candidate, including
retry evidence, entity rows, conditional/schema discovery, upload replacement and
negative cases, strict options, missing facts, source-check failures and approval
boundaries. No new live portal measurements or submissions are part of this fix.

Completed Docker/Chrome checks for these fixes (separate suites with overlapping cases):

- Core planner, historical experiment fixtures and shared source/control checks: 147 passed.
- Final upload/chooser regressions after the native-handle correction: 18 passed.
- Original-filler prefill, explicit clearing and planner-failure regressions: 29 passed.
- Selected approval, attachment integrity and frozen-payload guards: 15 passed.
- Smoke tests importing the updated `/app/src` runtime: 5 passed.

The core fixture suite ran on the experiment branch; final focused suites covered
the later native-input and original-filler prefill corrections. The main checkout
and Docker runtime contain the same runtime changes. These checks establish the
tested behavior, not a new time/HITL comparison or a promotion decision.
