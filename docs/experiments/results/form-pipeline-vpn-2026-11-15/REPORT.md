# Watched post-VPN form-filling comparison — November 15, 2026

All six final drafts passed independent source and visual inspection across Ashby, Greenhouse and Lever. Median active filling time was **105.95 seconds original versus 22.85 seconds experimental (78.44% lower)**. Avoidable logical questions fell from **1 to 0**. No applications were submitted.

This meets the previously documented small rollout gate. Default-dispatch checks passed; the source-routed strategy is now the default for new applications. Existing drafts retain their recorded strategy; both explicit choices remain available for comparison and rollback. This is not a guarantee about every future portal.

## Provider and comparison controls

The owner reported the VPN connected before this cohort. Actual writer and primary-embedding requests passed; the writer used Voyager `devstral2-123b` with function calling, and embeddings returned 4096 dimensions without fallback. Every live draft recorded that Voyager writer and reported token usage. All earlier pre-VPN timings are excluded. Connection errors/timeouts now log a VPN/provider diagnostic.

The final six arms used source commit `7a623ec`, snapshot v26, identical source hashes across all portals, and identical input hashes within each pair. Initial field inventories and values also matched within each pair. Each arm used fresh browser state and a fresh identical retrieval cache. Arms ran sequentially without concurrent tests or model preparation; model-call limits were 600 seconds and active budgets 12 minutes in both modes. Final order: Lever original/experimental, Greenhouse experimental/original, Ashby original/experimental.

Filling was performed by the job agent. Codex monitored a passive read-only CDP filmstrip, inspected completed screenshots, compared every recorded value with the frozen sources and checked document SHA ownership. Operator answers to avoidable baseline questions are counted as assists. Source/visual inspection time and recorded human waiting are excluded from adjusted active time; raw active and wall time are retained below and in [summary.json](summary.json). No operator typed into application controls.

## Final paired metrics

| Portal / public draft | Original active s | Experimental active s | Reduction | Writer starts O / E | Reported writer tokens O / E | Avoidable questions O / E | Genuine unknown questions O / E |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Ashby | 99.33 | 18.97 | 80.90% | 2 / 2 | 35,558 / 10,244 | 0 / 0 | 0 / 0 |
| Greenhouse | 223.06 | 49.47 | 77.82% | 4 / 2 | 77,245 / 14,339 | 1 / 0 | 0 / 2 |
| Lever | 105.95 | 22.85 | 78.44% | 4 / 4 | 47,397 / 15,238 | 0 / 0 | 0 / 0 |

Ashby: OpenAI Software Engineer, Scaled Abuse. Greenhouse: Stripe US posting 8128744. Lever: Spotify Senior Software Engineer, Enterprise AI. All were unsubmitted public drafts with live dynamic controls.

Greenhouse experimental asked two genuinely missing optional questions (SAT/ACT score and conference attendance) in one batch; both stayed blank. Original silently left those optional fields blank but asked one avoidable discipline question. Therefore **total human prompt events were one in each mode**, and total logical questions were 1 original versus 2 experimental. The improvement is in avoidable assistance, not a claim of fewer total human prompts.

All six final arms passed source inspection and had zero unverified required records. Experimental recorded zero failed writes, with 4 / 28 / 5 internal DOM reads for Ashby / Greenhouse / Lever. Original does not have an equivalent full-read counter; it is not assigned an invented zero. Writer counts exclude embeddings and internal SDK retries.

| Portal | Original active incl. observation s | Experimental active incl. observation s | Original total wall s | Experimental total wall s |
| --- | ---: | ---: | ---: | ---: |
| Ashby | 120.26 | 63.37 | 126.01 | 69.15 |
| Greenhouse | 260.08 | 80.77 | 312.76 | 108.81 |
| Lever | 155.61 | 51.97 | 161.47 | 58.72 |

Raw active includes gated inspection; wall time additionally includes setup and human waiting. The adjustment does not subtract per-action screenshots during asynchronous filling: the timed runs used passive observation, with blocking screenshots only at the shared start/completed-page boundaries.

## Repairs that account for the change

- Classify fields and route evidence in code before generation. Resolve exact owner facts locally; batch the remaining questions through a mapper and independent source check. No extra per-field classifier-model call is added. Preserve qualifications, person/record scope and consent purpose.
- Send complete available option catalogues and actual native values for unresolved choices. Mark virtual/asynchronous catalogues partial; search only supplied identities/variants. Preserve physical control identity as numeric inventories change.
- Create known repeated entries before planning; retain catalogues for unchanged controls and refresh affected controls after structural changes. Use targeted readback, batch embeddings and cache source compilation.
- Verify expected document bytes in the owned widget/form. Preserve canonical saved links rather than trusting parser prefills. Apply new human rules in the current run, retain typed boolean/multi-choice replies and prevent answers leaking between education wizard entries.
- Apply the same source-correctness standard to the baseline. Watched baseline failures exposed incorrect availability and unverified website prefills; these were corrected and the entire final cohort rerun on one source version.

The original strategy also uses page-batched planning; it is not described as calling the model once for every field. On Lever both modes made four writer starts, but experimental sent much smaller relevant evidence packets. The results reflect fewer/reduced planning passes and execution overhead, not the VPN alone.

## All retained post-VPN attempts

Earlier successful runs are superseded when the source changes. Failed audits remain failures even when the runtime claimed readiness. They are not included in the final-version paired estimate. The earlier pre-VPN diagnostics remain in the [repair log](../form-pipeline-2026-11-15/REPAIR_LOG.md) and private artifacts.

| Attempt | Active s | Source/visual audit | Final cohort |
| --- | ---: | --- | --- |
| `ashby-experimental-v1` | 18.54 | Pass | No |
| `ashby-experimental-v2` | 18.94 | Pass | No |
| `ashby-experimental-v3` | 18.97 | Pass | Yes |
| `ashby-original-v1` | 99.91 | Pass | No |
| `ashby-original-v2` | 98.72 | Pass | No |
| `ashby-original-v3` | 99.33 | Pass | Yes |
| `greenhouse-experimental-v1` | 50.30 | Pass | No |
| `greenhouse-experimental-v2` | 49.93 | Pass | No |
| `greenhouse-experimental-v3` | 49.47 | Pass | Yes |
| `greenhouse-original-v1` | 245.44 | Rejected | No |
| `greenhouse-original-v2` | 252.02 | Pass | No |
| `greenhouse-original-v3` | 223.06 | Pass | Yes |
| `lever-experimental-v2` | 22.85 | Pass | Yes |
| `lever-original-v1` | 110.38 | Rejected | No |
| `lever-original-v2` | 105.95 | Pass | Yes |

Greenhouse original v1 was rejected for an incorrect required availability quarter and incomplete preferred name. Lever original v1 was rejected for an unverified parser website prefill. Their `ready` claims were not accepted as proof of correctness.

## Validation and limits

The pre-rollout suite passed **221 tests**; the subsequent canonical-website repair passed **five focused checks**. After changing the default, the broader suite passed **334 tests** and exposed five legacy test-fixture failures: those tests mocked the original planner while implicitly selecting the new default. The planner fixture now explicitly selects original; the guarded-navigation fixture now covers both strategies. The affected planner/default rerun passed **25 tests**, and the entire navigation file passed **28 tests**, including both engine variants. All five observed failures were rechecked successfully; no runtime change was needed to accommodate the mocks. These are overlapping runs, not one summed clean-suite count. The installed Docker package then passed two smoke checks in 4.81 seconds: configured/default strategy selection and the default fill path stopping before submission. Every installed Python module matched the project checkout by SHA-256.

There is one final pair per portal, without a confidence interval; repeated earlier runs are diagnostics, not independent samples for a stronger statistical claim. Provider load/cache effects remain uncontrolled. This cohort covers public single-page forms with dynamically revealed fields and repeated education rows. It does not establish Workday, account creation, desktop-fallback, CAPTCHA or final-submission performance. Document generation, address acquisition and account setup are outside the measured filling phase.

Private resumes, applicant/contact data, prompts, field values, screenshots, filmstrips and per-field audits remain in ignored `outputs/verification/form-pipeline-vpn-2026-11-15`. Only redacted results and code are committed.
