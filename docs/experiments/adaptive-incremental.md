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
Literal human replies retain their own question/help/choice scope; two equally
labelled questions cannot overwrite each other's fact ID. Changed scope reopens
a declined question. Referral CSV/alias edits refresh the referral context and
current date facts along with the other watched inputs.
An external source change after earlier wizard pages were filled stops the run
for reconciliation, rather than certifying answers on pages no longer visible.
Readback acceptance uses a bounded state poll rather than three fixed delays.
An unopened partial menu retains its last observed vocabulary for decision scope;
only a newly observed vocabulary changes that scope. Generic menu placeholders
such as Select... are excluded from decision help, so selecting a value does not
invalidate that accepted answer; substantive instructions remain part of scope. Evicting a menu cache after
a failed lookup cannot reset its retry count. A source literal that was tried and
rejected by a menu becomes one question with the actual offered choices, rather
than repeated model guesses (for example, Asian versus specific Asian subgroups).
Skipping that question stops unresolved filling without a retry-only loop.
A bare contact Location field uses the prepared city; explicit office guidance,
country catalogues and employer/school/reference row scopes retain their own sources.
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
selection-cache lookup. Document prompts use the current local date rather than
the current minute, and the exact normalized prompt is both sent and hashed.
Source text and its dates are not rewritten. Small coverage gaps repair existing bullets or their JD-index annotations, with
valid targets, retained prior coverage and a progress check before caching.
Each proposed patch also passes the final bullet-quality checks before caching;
it cannot introduce a new defect or leave an edited duty duplicated.
Incremental repair instructions preserve coverage across the section, allowing a
duplicate occurrence to become different relevant work. They do not require
each individual bullet to retain its former duties. Shared coverage normalization
excludes administrative salary, EEO and authorization clauses misclassified as
responsibilities; those facts remain in the original posting and eligibility sources.
Coverage-only metadata edits do not trigger a summary rewrite. The summary runs after visible changes to sections. Semantic review
is scoped to section dependencies: changed skills recheck skills and summary;
changed employer bullets recheck experience and summary. Failed calls are never
cached. Completed section work survives interruption. Compilation and deterministic
validity gates still run. `context-manifest.json` records source fingerprints using
the same source-snapshot interface used by adaptive filling.
Source guards span the complete tailoring command and the cover-letter stage;
an intervening profile, JD, template or resume edit invalidates publication while
retaining the owner's edits. Review prompts receive the local date and relevant
company context, so a mission clause is not treated as a previous-employer claim.
The target job is explicitly labelled as the application destination; its resume
headline is checked in code and omitted from the critic's candidate-history prose.
Standalone target-mission interest statements become separate company propositions,
still checked against research; explicit personal history stays in candidate prose.
Education context is supplied for credential/education claims, with commented-out
template material excluded. Unrelated education dates cannot become a prerequisite
for a technical experience statement.
Policy-authorized overall and domain tenure is omitted only from semantic-review input
when an owner-declared limit exists; the original summary is preserved, and its
ceiling is enforced separately, including fractional amounts and an explicit
zero-year limit. Substantive skill/credential text remains visible to review. Employer-specific
and protected education, credential or legal history keep their numerical context.
Unresolved incremental correctness findings remain blockers after the repair
budget; reaching that budget cannot certify an invalid packet. The current
document strategy keeps its previous acceptance policy.
Skills findings use a typed category-error/soft-skill schema and must identify an
actual displayed term/category pair. Omitted template terms cannot be submitted as
missing-skill findings. Broad domain headings admit relevant tools, concepts and
hardware; they do not require a literal type relationship to the heading's noun.

Proposed critical project contradictions receive an independent, source-bound
adjudication. Both claim and evidence quotes must belong to that same project;
uncertain or unbound results block publication and are not cached. Claims may
bind to the displayed title, stack or selected description. Original template
titles supply owner evidence for the same repository and its awards; they
cannot establish implementation features or another repository's claims. Supported
subsets, design intentions and the existing owner claims policy remain valid.
Targeted project repairs can change the full description, displayed short form
or generated title independently. Known template titles remain protected.

Cover letters use the final resume. Each sentence selects an ID from the actual
employer/project/company source inventory; the code assembles the prose and attaches
the corresponding evidence. The model does not duplicate its prose or copy freehand
evidence quotes. Project evidence comparisons use the same punctuation and
whitespace normalization as rendering. Deterministic identity/number checks precede a
cached semantic attribution review. This prevents treating a matching number under
another employer as evidence. It does not ask the owner to reconfirm generated
duties/metrics: the standing declaration and existing claims policy remain in force.
Incremental experience and summary drafting, small experience repairs and
letter drafting prefer JSON mode after live tool-output retries.
Source coverage is validated inside the bounded correction loop before caching.
Typed, claim-bound supported/unsupported/uncertain review verdicts prevent positive
observations from being recorded as errors; malformed or unbound verdicts fail
validation. Semantic review receives each source once rather than repeated full
quotes per sentence. A letter may omit a source metric but cannot change or add
one. Its valediction is constrained; the template supplies the candidate name.
Incremental duplicate checks require substantive shared phrases rather than tool
connectors such as using C++ and ROS2; actual copied duties and word overlap remain
checked. The current document strategy retains its previous duplicate policy.
Degrees, employer identities, dates, personal histories and consents keep their
existing boundaries. Scientific superscripts/subscripts, comparison qualifiers
and microsecond units survive LaTeX conversion and PDF compilation; they are
not silently dropped from the generated text.
Project and summary factual reviews, the independent project adjudicator and the
letter attribution reviewer use `MODEL_PLANNER`; drafting remains on `MODEL_WRITER`.
Both validator model identities and provider/transport configuration participate
in workflow invalidation. Exact sampling parameters and token budgets also bind stage
cache entries. Qwen 3.5 factual reviewers use its endpoint-verified non-thinking
profile (temperature 0.7, top-p 0.8, presence penalty 1.5); drafting and other model
families retain their existing settings. Unsupported provider parameters fail
through the bounded error path rather than silently changing models or modes.
Qwen factual reviews/adjudication prefer JSON mode after observed tool-call stalls;
the configured preference remains unchanged for other model families, and the
shared bounded fallbacks remain available. Review findings request concise issues
within the existing length bound.
Source-binding validation failures participate in the same bounded correction
loop as malformed schema output; invalid evidence never enters the cache. Full incremental experience drafts prefer JSON mode
after repeated live tool/schema retries; the shared bounded fallbacks remain in
place. This preference does not change the current strategy.
Long factual review requests share one provider dispatch slot after cache lookup;
cheap reviews remain parallel. Queue wait is recorded separately in stage metrics.
If a section fails, queued reviews stop before dispatch and diagnostics identify
the failing section/model. A title-only project task offers only its requested
indices and names; unrequested paragraphs and swaps cannot be applied.

Shared company research reads bounded official PDF text directly rather than
opening the browser PDF viewer. Cached and newly generated profiles pass the same
official-citation gate as the CLI: incomplete caches are regathered, and a failed
refresh preserves the last saved profile. These checks retain the existing
official-domain policy.

The external ATS panel runs after local resume validation, using the configured
providers and their existing repeats, quotas and feedback-only distinctions.
SkillsFirst remains feedback-only. There is no baseline/intermediate external
panel unless `--ats-refine` is explicitly requested. A missing required provider
leaves the five-provider average incomplete. PDF/posting identities are checked
around scoring; uploads use a private immutable PDF snapshot and rows keep that
snapshot's PDF/JD identity even if the working files change. Refinements publish
the selected candidate before scoring; unchanged text spends no extra scan.
Rejected or failed refinements restore the complete prior document bundle
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
