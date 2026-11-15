# Implementation audit: discovery, source routing and avoidable work

The current implementation is an incomplete test of the proposed discover–route–resolve–fill workflow. The failed live comparison establishes that this implementation is not ready to promote. It does not establish that the proposed workflow is inferior. No production runtime code was changed in this audit, and no new real applications were opened.

## Direct answers

**Dropdown options are supplied, but not reliably all options.** Native selects retain the first 300 enabled/visible option labels in `tools/forms.py:306`. Custom combobox discovery keeps at most 300 currently rendered choices (`forms.py:907–922`); it does not exhaust server search, pagination or virtualization. Some menus have no choices until queried. The experimental prompt receives its `Field.options` as JSON. The original prompt uses `Field.brief()`, which abbreviates lists longer than 40. Thus both option coverage and prompt size differ between engines.

**There is no separate source-routing classifier in the production experimental path.** `filling/planned.py:96` tries exact aliases and scoped facts, retrieves learned entries, then asks one large mapping model to find the sources, apply instructions, compose prose and choose options together. Its prompt receives all compiled facts and narrative sources, not a small routed evidence packet per question. `adapter.py:34` does call the existing field-to-applicant matcher, but uses its result for address preparation; it does not use those matches as the general answer-source routing stage.

**The original is already page-batched.** `steps/apply.py:476` plans a list of fields and `apply.py:558` checks the list. Per-field model tie-breaks and lookups are conditional fallbacks. The measured Ashby arms each used two writer starts; Lever original used four and experimental three. The actual comparison was not one batch versus one writer call for every field.

## Reproduced correctness and efficiency defects

### 1. Option truncation hides a valid saved answer

A local native-select fixture had 351 enabled choices. Its saved target was present in the real select at position 351, but the reader returned 300 choices and `resolve()` returned no value. This can cause a human prompt or a failed choice even though the supplied fact is correct and offered.

This does not explain every live education failure: the observed ASU choice was already in the captured list. Section/entity binding also failed there.

**Correction:** keep a complete native catalogue locally, including native values/IDs. Use exact aliases and indexed search to obtain a compact relevant candidate set before involving the model. For custom menus record `complete`, `partial`, `search_required` or `stale`, plus owning control and dependencies. Never represent a partial first page as the complete catalogue. Passing thousands of school names into every model prompt is not the remedy.

### 2. Combobox planning permits a value outside its given options

`filling/facts.py:125` deliberately returns the raw value when a combobox has no exact equivalent. A local call with offered Remote/Office returned the unoffered value Invented option. This supports searchable pickers, but there is no completeness/status flag to distinguish a legitimate source-backed search from an invalid value for a fully enumerated list. The failure is deferred to browser execution.

`forms.py:1215` can then try up to three query strings derived from that value, with fixed waits. These are shortened search queries, not necessarily different biographical answers; nevertheless they consume time and should not be needed for a known complete catalogue.

**Correction:** require an existing option ID for complete catalogues. Permit a search request only for a source-backed value on a search-capable incomplete control. Read the actual returned choices and select one supported option. An unavailable answer is a resolution result, not permission to invent another option.

### 3. Source scoping rejects known language facts

A local compilation of an English-language record produced three facts scoped to Languages 1. A normal Language Skill(s) field under Supplementary Questions could use **zero** of them because `scope_allows()` compares those section names (`facts.py:62–66`). This is a hard guard, so a model cannot fix it by selecting the correct fact ID.

The compiler treats list-of-record data as scoped UI entities (`facts.py:238–257`). A source record's identity is not always the same thing as the form's visual section. Similar restrictions affect questions asking for current employment outside a work-history row.

**Correction:** distinguish source entity identity, question subject and UI repeating-row identity. A global language-skills question may read the candidate's language records. A current-employer question may read the current employment record wherever the control appears. Two separate education rows must still be kept distinct. Do not remove all entity restrictions as a shortcut.

### 4. Option discovery reopens completed menus

`planned.py:293` passes the entire current view to `discover_options()`, even when only one new field needs planning. Fresh DOM reads recreate custom fields with empty option arrays; the catalogue is not retained across those reads.

In a local fixture with three known comboboxes and one conditionally revealed text input, each dropdown opened **three times**: first discovery, execution, then discovery again after the text field appeared. The second discovery pass saw all three values already selected. This completed without any model calls, so the extra work is in the implementation, not model reasoning.

Each ordinary discovery probe includes 700 ms after opening and 200 ms after closing; categories, search typing and selection add other waits. Repeating this across a large form is substantial.

**Correction:** cache catalogues by stable field identity and dependency/version fingerprint. Discover new or invalidated controls only. Refresh a country-dependent state list when Country changes, not every unrelated menu when a new text field appears. Wait for actual options/response completion with a deadline rather than relying only on fixed sleeps.

### 5. Full-page reading occurs around every field

`planned.py:309` reads the full page before each action and `planned.py:335` reads it again afterward. There are additional round/inventory/final reads. A local 20-text-field run with exact supplied facts and zero model calls performed **45 full-page reads**. The original executor, given precomputed answers and a stubbed planner/checker to isolate execution, performed **four**. Both filled their fields correctly.

These are structural diagnostics, not production speed claims: the original retains other source-enforcement work, and the headless local DOM is simpler than a real portal. They demonstrate that the candidate does not execute one precomputed plan with only targeted readbacks.

**Correction:** retain validated stable locators, check the target's connection/ownership before acting, and read that control afterward. Observe relevant structural/visibility/dependency changes; refresh affected regions when branches or repeating rows change. Keep a final full-page completeness check. Do not recover speed by restoring unsafe stale numerical handles or dropping verification.

### 6. Retrieval and prompts still do excessive shared work

`adapter.py:61–66` retrieves learned evidence separately for each unresolved question. `learned.relevant()` calls `_vectors()` on one query plus the shared learned bank. A successful primary cache helps, but fallback vectors are intentionally not saved into that primary model's cache; repeated per-field requests can redo work. The default `related_entries()` already batches multiple queries and the bank together.

Mapping sends all facts and narrative sources (`planned.py:138–140`), and the checker sends all facts, the narrative sources and per-proposal facts again (`planned.py:202–204`). The catalogue includes both structured records and components. Large repeated context can outweigh the savings from one fewer model invocation.

**Correction:** compile/index facts once per version; batch unresolved-query retrieval; reuse corpus vectors within a run and key caches by the actual embedding backend/model. Route questions to compact evidence packets including the applicable owner rule and related conflicting facts. Do not omit contradictory evidence merely to reduce tokens. Generate prose together only for fields needing prose. Use deterministic renderers for supported simple values and authorized date calculations.

### 7. Structured-output modes differ between arms

Original planning/checking explicitly uses `first="function_calling"` (`apply.py:492,576`). Experimental mapping/checking omits `first`, so `llm.structured_invoke()` starts with `json_schema` (`llm.py:252`). The retry helper can change methods after failure. The callback log records starts, completions and errors, but not the method or error category.

One experimental Greenhouse request errored after 300.191 seconds, followed by a successful 12.892-second request. The logs do not establish that the output mode caused the timeout. The difference is nevertheless an uncontrolled factor when attributing performance to workflow design.

**Correction:** use the same supported output mode, writer settings, deadlines and retry policy for matched trials, or explicitly make protocol choice another experimental factor. Record actual method, input/output tokens, retry reason and stage. Never report the hypothetical timeout-free duration as the measured result.

### 8. Source-check policy still allows wrong answers

The existing checker returning accepted field IDs is not a deterministic proof of the question-to-fact relationship. It accepted a state fact for the wrong residency predicate, one consent purpose for another, and a current-clearance fact for eligibility. The earlier follow-up findings contain the live screenshots and records.

The original also has conflicting instructions: `apply.py:121` encourages inferring unstated preferences from resume usage, while the newer owner-only guard requires owner evidence. `CHECK_PROMPT` at `apply.py:526` treats agreement to a consent needed to apply as supported. A required Yes/No question is not proof that Yes is required, or that a separate sharing permission has been granted. These policies must be aligned for a fair and correct comparison.

**Correction:** classify subject, predicate, entity, consent purpose and answer authority before resolution. Require complete support for that exact question. Distinguish the real candidate's facts from application-specific address overlays. A public lookup or JD cannot supply personal permission or employment history. Check ordinary typed facts locally; independently check necessary prose/rule reasoning with compact relevant evidence. Missing owner information must remain unknown, not become an affirmative answer because another entry contains Yes.

### 9. Upload errors add retries, not useful work

The earlier live audit established visibly attached documents that failed acknowledgement verification. The screenshot interval surrounding the failed experimental Greenhouse cover-letter operation was approximately 72 seconds. That interval is not an isolated timing of just the verifier. `fill_field()` does contain repeated polling/sleeps, and `UploadProof.verify()` repeatedly reads/encodes/hashes bytes during several readiness paths.

**Correction:** fix widget ownership and acknowledgement first. Capture and hash dispatched bytes once, retain versioned evidence, invalidate on a real file/widget change, and revalidate current bytes/ownership at final review/submission. Keep same-name/wrong-widget protections. Do not improve metrics by treating an unverified filename as success.

## Reconstructed live timing

Durations below come from existing writer callback timestamps and recorded active time, not fresh live runs. Human waiting and trial observation were already excluded from active time. Other active work includes necessary browser filling, site waits, discovery, embeddings/retrieval, verification, retries and bookkeeping. It is **not** all removable overhead.

| Portal | Arm | Active seconds | Writer callback seconds | Other active seconds |
|---|---|---:|---:|---:|
| Ashby | Original | 96.8 | 75.9 | 21.0 |
| Ashby | Experimental | 101.9 | 61.9 | 40.0 |
| Lever | Original | 182.4 | 153.1 | 29.3 |
| Lever | Experimental | 214.8 | 153.0 | 61.8 |
| Greenhouse | Original, stopped | Missing | 138.3 retained | Missing |
| Greenhouse | Experimental | 647.9 | 469.5 | 178.4 |

Ashby saved about 14 seconds inside writer calls but incurred about 19 more seconds elsewhere. Lever writer time was effectively equal, despite three experimental starts versus four original starts; the extra time occurred elsewhere. The Greenhouse timeout alone accounts for about 46% of its experimental active time. None of these arms forms a source-correct completed pair, so they cannot establish a completed-workflow speed advantage for either method.

See [timing_breakdown.csv](timing_breakdown.csv) and [diagnostic_results.json](diagnostic_results.json). Synthetic timings are single diagnostic samples, not benchmarks proving a production speed ratio. The diagnostic JSON records source hashes; both checkouts matched during review.

## Corrected experiment pipeline

1. Extract reachable controls, their full owned wording, types, requirements, row identity, dependencies and option-catalogue status. Keep stable control identities and collect accessible schema data without repeatedly reparsing unchanged bundles.
2. Route each question to canonical source categories and exact subject/predicate. Use deterministic classification where unambiguous and one small batched classification pass for ambiguous questions. This responsibility does not require a separate full-size agent call per question.
3. Retrieve only the applicable personal facts, owner rules, learned entries, resume passages, JD passages or company research. Batch retrieval. Use approved external lookup only when the routed fact/rule requires it.
4. Resolve simple facts and authorized transformations locally. Batch model work for genuinely ambiguous choice rendering and written answers. Complete catalogues return native option IDs; searchable catalogues return a source-backed query and then a supported actual option.
5. Validate source binding, purpose, entity, constraints and contradictions. Ask only genuinely missing owner questions together; distinguish unreadable controls, source conflicts and technical failures from missing personal information. Define the optional-field policy explicitly.
6. Execute the resolved plan without new answer generation for each field. Use targeted readback and branch/dependency invalidation. Replan only affected or newly discovered questions.
7. Perform final completeness, document and evidence checks. Retain submission approval and challenge handling. Record stage timings separately so correctness checks are optimized rather than silently removed.

After these corrections, rerun both strategies with the same answer policy, source inputs and model protocol. The current results should be described as an implementation failure, with some useful structural improvements already observed; the proposed workflow's speed advantage remains a hypothesis to test.
