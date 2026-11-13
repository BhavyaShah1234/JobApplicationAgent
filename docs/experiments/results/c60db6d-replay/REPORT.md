# Exploratory replay results

Version: `c60db6d`; initial baseline snapshot: `011116f`. Twelve synthetic fixtures, three repetitions per arm,
alternating order. No provider calls, no live portals, and no final Apply click. Source hashes stayed unchanged
during the entire run. These results predate subsequent CLI/scoping hardening; they identify the exact tested version.

| Measure | Existing engine | Candidate |
|---|---:|---:|
| Trials with correct final values | 27 | 36 |
| Reported completed | 33 | 36 |
| False-ready trials | 6 | 0 |
| Human-question requests | 3 | 0 |

Existing-engine errors reproduced searchable-school substitution, document-role misrouting and the conditional
address case. The last case uses bare source-backed Country/State facts rather than a production address-lookup cache;
its setup limitation must not be mistaken for a universally representative portal result.

Raw timing measurements are retained in the JSON/CSV, but **do not establish real-model speed improvement**.
The existing engine uses a deterministic model stub, while straightforward candidate answers bypass the model.
No claims are made about the chosen 30%/50% adoption targets. All fixture facts were supplied; the question count is
not evidence that missing personal facts would never require a human. Separate browser tests exercise missing facts.

The initial implementation still contains defects Claude is fixing. Repeat both engines on the same reconciled
checkpoint before a formal comparison. Independently grade provider-backed and live trials with the trial ledger.
The report intentionally sets `formal_checkpoint_integrated=false` and `promotion_allowed=false`.
