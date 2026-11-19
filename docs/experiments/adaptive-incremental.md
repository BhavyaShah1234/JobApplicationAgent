# Adaptive filling and incremental documents

These are independent opt-in strategies. Production defaults remain `experimental`
for forms and `current` for documents. Neither flag enables automatic submission.
The owner-paused application batch must remain paused during validation.

## Usage

After deploying a revision containing this feature:

```bash
jobagent tailor outputs/<company>/<job-id> <company> --document-strategy incremental
jobagent apply outputs/<company>/<job-id> <company> --filling-strategy adaptive

# Fresh posting, stopping at the application review screen:
jobagent run 'https://<posting-url>' --through apply \
  --document-strategy incremental --filling-strategy adaptive

# Optional deeper ATS refinement, at most two additional revisions/panels:
jobagent tailor outputs/<company>/<job-id> <company> \
  --document-strategy incremental --ats-refine
```

Environment equivalents are `DOCUMENT_STRATEGY=incremental` and
`FILLING_STRATEGY=adaptive`; YAML settings live in `config/documents.yaml` and
`config/forms.yaml`. `--ats-refine` requires incremental generation with ATS
enabled. The older `--single` writer uses the current strategy. `--no-ats` remains
available, and the warn/block ATS policy is unchanged. `--force` discards this
job's incremental stage cache as well as regenerating its workflow stages.
Queue-driven `run` forwards both document options. A resumed application retains
its recorded filling strategy unless explicitly overridden.

## Implementation

`AdaptiveFiller` extends the existing source-routed planner/executor. It preserves
the classifier, exact offered choices and completeness markers, source validation,
batched missing-answer questions, entity binding, closed-root reader, upload
proofs, navigation rules, desktop fallback and approval/submission guards.

The new strategy watches question/help/choice mutations, including recovered
shadow roots. Decisions depend on the full field schema and bound source facts.
Narrative-backed answers also depend on their documents. Source-file changes
reload context once per change; a change of posting identity stops filling.
Independent accepted answers survive a changed neighbouring field. Before each
dispatch the planner checks that the live field still matches its decision.
Readback acceptance uses a bounded state poll rather than three fixed delays.
The existing two-attempt outer control-repair limit remains in force; exhausted
repairs produce a private screenshot for diagnosis and a blocker.

`application/adaptive-v1.json` stores serializable accepted decisions and field
schemas. Restoration requires unique matching semantics and current evidence;
it never transfers a repeated employment/education row by its row number.
Restored answers remain proposals until freshly executed/read back. Upload proofs,
page objects, cookies, OTPs and approval capabilities are never serialized there.
Files are owner-readable/writable only. Corrupt and incompatible checkpoints are
ignored. Potential schema fields remain potential until their applicability is
established; this does not promise discovery of unseen server-side questions.

The incremental document strategy reuses the existing parallel section writers,
measured layout fitting and bounded targeted repairs. Successful model outputs
and reviews are cached in `.incremental/v1/`, keyed by exact prompt, schema,
model and generation/validation code version. Project cards are refreshed before
selection-cache lookup. The summary runs after changed sections. Semantic review
is scoped to section dependencies: changed skills recheck skills and summary;
changed employer bullets recheck experience and summary. Failed calls are never
cached. Completed section work survives interruption. Compilation and deterministic
validity gates still run. `context-manifest.json` records source fingerprints using
the same source-snapshot interface used by adaptive filling.

Cover letters use the final resume and bind achievement sentences to exact quotes
under their owning employer/project. Deterministic quote/number checks precede a
cached semantic attribution review. This prevents treating a matching number under
another employer as evidence. It does not ask the owner to reconfirm generated
duties/metrics: the standing declaration and existing claims policy remain in force.
Degrees, employer identities, dates, personal histories and consents keep their
existing boundaries.

The external ATS panel runs after local resume validation, using the configured
providers and their existing repeats, quotas and feedback-only distinctions.
SkillsFirst remains feedback-only. There is no baseline/intermediate external
panel unless `--ats-refine` is explicitly requested. A missing required provider
leaves the five-provider average incomplete. PDF/posting identities are checked
around scoring. Rejected refinements restore the complete prior document bundle
and its own score identity. ATS scores remain a proxy, not factual validation or
a hiring-outcome measure.

## Evidence and promotion gates

`incremental-metrics-v1.json` separates document time from external ATS wait and
records cache/model stages. Cover-letter metrics are in
`incremental-letter-metrics-v1.json`; ATS panel identities/times are in
`incremental-ats-v1.json`. Application reports retain the shared filling metrics
and add adaptive restoration/invalidation counts. Existing reported model-token
metrics do not include embeddings or hidden SDK activity; missing values must
not be converted to zero.

The live acceptance gate remains separate from implementation/tests. Freeze 20
unsubmitted postings: four employers in each of Workday, Greenhouse, Lever, Ashby
and SuccessFactors; ten development and ten employer-disjoint held-out cases.
Compare against a frozen **experimental** default, using the same documents,
profile, model and answer sources, with execution order counterbalanced. Record
session/setup differences and unavailable/changed postings. Never resubmit an
uncertain prior application to obtain a benchmark observation.

An independent answer audit records correct/total required fields, critical errors,
technical assists, genuinely missing facts and repeated questions. Timing reports
include active/total time and median/p90, human wait, model starts/tokens, DOM reads,
repairs, upload attempts, document/review/compile rounds and ATS completeness/wait.
Document comparisons must use the same source inputs and independently reviewed
quality; cache-warm recovery results must be distinguished from fresh generation.

The report tool accepts a frozen manifest and paired application reports:

```bash
python -m jobagent.experiments.adaptive_acceptance manifest.json acceptance.json
```

Each case has `id`, `family`, `employer`, `url`, `split` (`development`/`held_out`),
`order` (the two strategy names), `experimental` and `adaptive` report paths,
`matched_input_fingerprint`, `model`, `session_notes`, and `review` keyed by arm.
Each review has `reviewer`, `complete`, `correct_required`, `total_required`,
`critical_errors`, `technical_assists`, `missing_fact_questions`, `repeated_questions`.
Missing runs/reviews/metrics keep the gate pending. The tool has no browser or
submission capability and never changes defaults.

Promotion requires zero observed critical errors, at least 18/20 correct drafts
without technical assistance, no held-out correctness regression, and the target
20% median active-fill improvement with no p90 regression. Incremental documents
have a separate 30% median generation-time target with no quality/p90 regression.
These are acceptance targets, not measured results or a guarantee for every portal.
Until those live gates pass, both strategies stay opt-in and the previous paths
remain available for rollback.
