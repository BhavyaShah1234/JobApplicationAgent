# Findings from the watched live comparison

These items remain open. The measured runtime is commit `8866ff9`. Fixes made
later must not be attributed to these measurements. Private evidence is under
`outputs/verification/live-observed-2026-11-14/` in the main checkout.

## V01 — Critical: a verified value can contradict its cited source

- [ ] Correct source binding and purpose-specific consent validation before promotion.

Both engines selected unsupported external partner sharing. The experimental
Lever run additionally selected California residency even though neither the
saved address nor the application-local address was in California. Its record
cited the state fact and still marked the answer verified. It also transferred
permission for future-opportunity contact to permission for external partner
sharing. Clearance eligibility was answered using the absence of a current
clearance, while the actual eligibility entry was blank. The engine ultimately
reported ready. Independent review rejects that result.

Greenhouse supplied another binding failure: the email value was correct, but
its provenance cited the legal-name fact. Correct values found elsewhere in the
context do not repair an incorrect evidence chain.

Review `filling/planned.py` (`Mappings`, `Supported`, `plan`), `filling/facts.py`
and the shared original checker in `steps/apply.py`. A fact ID must establish
the question's subject, predicate, entity and consent purpose. A boolean's value
alone is insufficient. Keep other facts available to detect contradictions,
but require the checker to identify the complete supporting evidence and then
validate that binding. A checker that returns only accepted field IDs can conceal
the original bad binding. Derived resume facts must retain their actual origin;
an `applicant/` prefix must not turn inferred preferences into owner instructions.
Missing purpose-specific permission must remain unresolved or follow an explicit
opt-out policy; it must never inherit another consent's Yes.

Required regression cases: non-California state cannot support California Yes;
no current clearance cannot establish eligibility; permission for direct contact
cannot grant third-party sharing; a name fact cannot establish an email address.
The release gate must test semantic correctness separately from DOM readback.

Evidence: `lever-experimental-v2/critical-choice-audit.json`,
`lever-experimental-v2/critical-choice-1.png`, `critical-choice-2.png`,
`critical-choice-3.png`, and `greenhouse-experimental-v2/independent-audit.json`.

## V02 — High: original filling uses stale numerical control handles

- [ ] Rebind every action and readback to a unique stable control identity.

Greenhouse replaced upload inputs after accepting files. A later reader pass
renumbered `data-jf` controls. The original engine then searched for the saved
university in Degree while School remained empty. Initial and subsequent DOM
snapshots show School shifting from 8 to 7 and Degree from 9 to 8. The watched
arm was stopped. Experimental filling refreshed the inventory and selected the
correct controls in the same situation.

Review `tools/forms.py` (`read_page`, `locate`, `fill_field`) and the original
`steps/apply.py` filling loop. A numerical position belongs to one snapshot,
not to a question across mutations. Validate document/frame identity and use
unique author identity or an equally unambiguous captured control identity.
Fail closed if the control disappears or becomes ambiguous. Apply the same rule
to option discovery, action, verification and upload callbacks. Do not fix this
by accepting a similarly labelled different control.

The original Lever audit also collapsed two distinct optional controls sharing
the placeholder Type your response: 30 final DOM fields became 29 records.
Preserve control identity when merging audit records, not only when acting.

Add an integration regression with two uploads that replace/remove their native
inputs, followed by School and Degree controls; verify which native control
receives each answer. Include reordered and ambiguous controls.

Evidence: `greenhouse-original-v2/education-drift-native.json`,
`education-drift.png`, `filmstrip/`, `initial-views.json`, `operator-stop.json`.

## V03 — High: upload boundaries and duplicate references block real attachments

- [ ] Reconcile native inputs, lazy triggers and passive schema references by owned widget identity.

Both Ashby arms were prevented from readiness by a second Resume reference,
after the real resume had been attached. The experimental arm verified the
actual native bytes. The original attempted a stale duplicate trigger; the
experimental engine treated a passive Resume reference as unresolved.

On Greenhouse, the cover-letter filename visibly appeared, but the filler said
it never appeared. The experimental arm eventually blocked both provided
uploads. Inspection shows an upload widget with its own file controls and
auxiliary buttons. `UploadProof.widget_for` can stop at those buttons before
retaining the actual widget that later displays the acknowledgement.

Review `tools/forms.py` upload discovery, `filling/discovery.py`,
`filling/uploads.py`, `filling/planned.py` potential-field reconciliation and
`filling/adapter.py` document obligations. Retain dispatched byte hashes and a
unique persistent widget boundary. Permit that widget's auxiliary controls
without broadening evidence to a neighbour. A native input, its trigger and
its schema reference are one obligation only when their ownership is proven;
matching the word Resume anywhere is insufficient.

Test a widget that replaces its input and contains attach/remove/alternate-source
buttons, a native input plus nested lazy-looking trigger, and a passive schema
reference to that same widget. Retain wrong-widget and same-filename safeguards.

Evidence: both `ashby-*-v2/job/application/application.json`,
`greenhouse-experimental-v2/cover-letter-widget-dom.json`, and upload screenshots.

## V04 — High: styled question and section text is lost

- [ ] Recover owned question/section text without relying solely on heading tags or placeholders.

Greenhouse's Education section is a styled paragraph, so its controls were
assigned to the page-wide section. The experimental engine could not bind the
known degree facts and did not add the second degree row required by the saved
instruction. Lever exposed two questions as Type your response and its required
university selector with no label, although the actual wording was present in
the enclosing question container. Read-only DOM inspection recovered preferred
name, optional name pronunciation and the current/last university question.

Review `tools/forms.py` label, placeholder, section and repeating-row discovery,
and `filling/facts.py` entity scoping. Prefer explicit accessibility relations,
then bounded owned question containers. Generic placeholders are not questions.
Exclude select option text before evaluating nearby question text. Recognize
semantic section titles rendered as ordinary text within a section container;
preserve row ownership and indices. Do not relax entity scoping globally to
make missing section metadata disappear.

Test these real structural patterns, including two education rows with distinct
dates and schools. Confirm both row creation and entity-specific values.

Evidence: `greenhouse-experimental-v2/education-section-dom.json`,
`lever-experimental-v2/unlabelled-question-ancestors.json` and initial views.

## V05 — Medium: known facts and rules still produce avoidable questions

- [ ] Complete source resolution, authorized date rendering and optional-field handling.

After the control fixes, Ashby still asked for the rule-backed start date and
technical domain. Greenhouse asked nine avoidable questions, including known
education/current employment and optional unknown/inapplicable fields. Lever
asked for known language, preferred name, university, completed years and the
clock-backed signature date; optional pronunciation could have stayed empty.
Count logical questions, not just batches.

Review `filling/facts.py`, `filling/adapter.py` and `filling/planned.py`. Honor
explicit owner rules with the actual control's cardinality and temporal context.
The exact-date guard must admit dates explicitly authorized by a today/weekday
rule and trusted clock while continuing to reject invented dates from
month-only history. Integer-year rendering must preserve the supported meaning,
not round up experience. Optional unknown fields should be left for review
without an obligatory prompt unless the owner explicitly requests otherwise.

Investigate the exact mapping/rejection path before adding more synonyms.
Improve private diagnostics so the chosen evidence, rendered proposal and
rejection reason can be audited without reconstructing them from a filename.
Keep sensitive prompts and values out of public logs.

## V06 — Medium: instruction compliance needs independent checks

- [ ] Validate explicit writing constraints and preserve factual evidence for prose.

The experimental Lever technical response contained 72 words and original
contained 66, below the saved default 120–200-word range. Original
company-interest prose also fell below its saved 80-word minimum. They were
accepted by their checkers. The experimental technical answer's factual claims were
present in the generated resume; that does not establish compliance with the
separate length rule. Validate applicable min/max words or characters before
filling, and distinguish grounded resume claims from biographical verification.
Do not invent details to satisfy a minimum length.

## V07 — Medium: stop handling can lose benchmark telemetry

- [ ] Make cancellation bounded and persist partial measurements.

Stopping the original Greenhouse arm through SIGTERM did not promptly unwind
the Playwright operation. The operator closed only that trial browser and
terminated only its process. Final metrics were not retained, so its active
time is reported missing. The per-action evidence and writer timestamps remain.
A later experimental Greenhouse request used the full 300-second writer timeout
before a retry. Writer starts include the failed invocation; token reporting
cannot account for unreported failed-call tokens.

Review the trial harness and the production request deadline path. Persist
periodic partial metrics atomically, propagate the remaining trial deadline into
blocking operations, and bound cleanup. A watchdog must not depend solely on a
signal being handled inside a synchronous browser operation. Test interrupted
browser/model waits and ensure no final application action can occur during
cleanup. Do not replace missing active time with wall time or omit stopped arms.
