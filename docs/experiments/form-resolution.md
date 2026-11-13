# Form-resolution experiment

This branch is an opt-in prototype. The production command path is unchanged.
The original checkout remains available to Claude Code; do not deploy these modules into its running container.

## Branch and checkpoint protocol

- Experiment branch: `experiment/form-resolution`, sibling worktree `JobApplicationAgent-form-experiment`.
- Initial source baseline: `011116f`. This includes the allowlisted dirty implementation from the original checkout,
  not just its earlier HEAD. `baseline-manifest.json` records the captured files. No actual applicant files or state
  were added to Git.
- Claude Code owns unchecked findings in the original checkout. This branch does not mark those findings resolved.
- After Claude commits completed fixes, record that commit, reconcile overlapping interfaces in this worktree, and
  rerun BOTH engines against the same integrated code and identical private inputs. No automatic merge or rebase
  of the owner's dirty checkout. A new checkpoint starts a new measurement batch.
- Until that checkpoint is available, all benchmark results are exploratory. Replay timings are not model timings
  and must never be used to claim the 30%/50% adoption targets were met.

## Running the isolated fixtures

Use the project's dependency environment with Chrome available:

```bash
PYTHONPATH=src pytest -q tests/experiments
PYTHONPATH=src python -m jobagent.experiments.benchmark \
  --output /tmp/form-experiment-replay --repetitions 3
```

The replay runs the actual existing `apply.fill_page` against a deterministic model stub, compared to the
experimental engine. It independently grades DOM values, preserves failures, alternates arm order, and writes
`trials.json`, `trials.csv`, `summary.json`, and a source manifest. It never repairs answers using the grader.
The synthetic upload PDFs are transport-test payloads, not valid production documents.
Add `--model` only for explicitly intended Voyager calls; both arms then use the configured planner model.
No live provider calls are made by replay or the tests.

Twelve cases include ordinary text, numeric travel choices, school identities, dates, hidden fields, embedded
schemas, dependent controls, two upload roles, repeated entity scopes, closed roots, embedded frames and a wizard.
Four cases are tagged held-out. Keep their results visible, and do not tune against them while describing them as
unseen. Additional browser tests exercise fetched question definitions and refusal of false readiness.

## A live draft trial

Each invocation creates a NEW private run directory and isolated Chrome profile, data and state directories.
Do not use an existing production directory. Supply the same frozen applicant/documents/model settings to both
arms. The source input directory is read-only to the runner; the private applicant copy and any learned answers
stay inside the run directory. Existing drafts/accounts can still be saved by the website: browser-profile
isolation does not isolate server-side state.

```bash
PYTHONPATH=src python -m jobagent.experiments.filling \
  --engine candidate --url 'https://PUBLIC-PORTAL/NEW-POSTING' \
  --job-dir /absolute/path/to/frozen-job-documents \
  --applicant /absolute/path/to/frozen-applicant.yaml \
  --run-dir /tmp/unique-candidate-trial --model
```

For the existing arm use `--engine current`, identical inputs, and another new run directory.
Configure provider credentials through environment variables, not source control. Authentication/navigation occur
before the primary filling timer; the runner pauses for setup in its own Chrome. No Submit/Apply click is issued.
Unknown candidate questions are printed as a batch. Reply with a JSON object mapping displayed IDs to answers;
`null` skips a question. The baseline retains its existing per-question prompts.

Optional `--facts /private/facts.json` supplies explicit source-backed mappings, shared as applicant entries for
both arms, with this schema (do not put personal files in Git):

```json
[{"id":"education.1.school","aliases":["School","University"],"value":"Arizona State University",
  "source":"applicant/education/0/school","section":"Education 1","variants":[]}]
```

Exact aliases, scoped structured education/work facts, raw-JD travel and known personal components resolve
without model calls. A model may map ambiguous questions only to existing fact IDs. Supported narrative questions
may use a separately checked, quoted resume/JD draft; missing or conflicting facts go to the candidate. Exact days
are never inferred from month-only dates. Searchable identities use strict selection, not fuzzy substitution.

## Measurement and acceptance

Primary: discovery-to-ready active time, excluding human waiting. Also retain total elapsed time, question/batch
counts, model calls, discovery events, field records, errors and source hashes. Readback and source evidence are
separate from independent grading. Include blocked trials in completion and error counts, not just successes.

For the formal report, label every interaction as necessary missing information, avoidable known/repeated question,
technical rescue, or human challenge. Count interventions by Codex/Claude as assists too. Document initial prefilled
fields, human setup/navigation time, portal family and account/server-draft differences in the trial ledger.

The 6-family live comparison uses two comparable open, unsubmitted postings per family; assign one to each arm,
record assignment before testing, and do not reuse submitted Carvana/Pantex postings. Missing families are reported,
not silently replaced. Live runs complement the paired fixture evidence; different postings are not perfect pairs.

Adoption requires >=30% less median active time and >=50% fewer avoidable assists on paired fixtures, no loss of
correctness/completion, zero fabricated personal facts, and zero false-ready required answers. A zero-assist baseline
uses absolute differences. Review the report before any production merge.

## Current boundaries

- Strict native upload verification includes bytes. Opaque/lazy uploaders without retained native evidence may block;
  their unsupported behavior must not be called autonomous success.
- Repeating rows use existing discovered Add controls. Unsupported controls or uncertain entity mappings stop or ask;
  this prototype does not claim every portal is supported.
- JSON-compatible form literals and delivered bounded JSON/JavaScript are inspected passively. Arbitrary minified
  bundle reverse engineering is not implemented. Required potential questions with unresolved applicability block.
- CAPTCHA/authentication are outside the filling engine. Human challenges stop the candidate arm.
- CLI runs emit metrics and evidence, but real-world assistance classification and answer grading require an
  independent reviewer. The harness cannot declare its own production adoption.

## Implementation checkpoint

Prototype commit: `c60db6d`; baseline snapshot: `011116f`. Experimental modules are standalone and are not
imported by `jobagent apply`, `submit`, `run`, or queue processing. No runtime production files were deployed.
Focused tests: 16 passed (13 dependency deprecation warnings), including exact identity refusal, original-JD travel,
scoped entity facts, missing-day refusal, native upload-role byte checks, conditional/API question discovery,
unknown-fact batching, authentication refusal, and no final Apply click. Local replay trials use synthetic identities
and synthetic document payloads. Formal provider and live comparisons remain pending the shared Claude checkpoint.

### Concurrent development status

The original branch still has no committed Claude-fix checkpoint beyond `5789c36` at the start of the experiment.
Consequently the paired replay is testing the captured implementation, not announcing formal adoption readiness.
A repeat on a reconciled checkpoint and provider-backed plus live-draft validation are required before deciding
whether the experimental approach actually meets the chosen time/assistance targets.

### Follow-up hardening

After the frozen replay, the draft CLI was hardened to snapshot all job inputs, use the actual existing context
builder for the baseline, share explicit facts with both arms without duplicating them, preserve the saved referral
policy, and accept an optional private `--contacts /path/to/asu_contacts.csv` snapshot for both arms. Browser scope
checks reuse the existing Scope guard. Human answers are scoped to a wizard step/entity rather than globally
reused for unrelated questions. Ambiguous model mappings receive an independent source check. Live result metrics
count actual model starts and provider-reported tokens; missing token usage remains unknown rather than zero.
These changes are verified separately and are not silently attributed to the earlier replay version.

The recorded replay is in `results/c60db6d-replay/REPORT.md`. It is explicitly not a provider-backed speed result.

### Verification of follow-up changes

- Full focused experiment suite after scoping/CLI/policy hardening: **21 passed**, 13 dependency warnings, 69.44 s.
- Subsequent percentage-symbol/hyphen boundary checks: **8 passed**, 0.25 s (includes existing resolver cases;
  this is not 8 additional unique tests). The suite now contains 22 unique test functions.
- CLI integration checks use a real isolated headless Chrome, explicit facts and a private input snapshot; assert
  that original inputs are byte-identical after filling. No live posting or provider request is involved.
- Scope is established from the initial navigation before the manual authentication/navigation pause and checked
  again afterwards, so unrelated setup navigation is not silently trusted as a fresh redirect.
- `git diff --check` passes. Production source and the original checkout were not edited by this experiment.
