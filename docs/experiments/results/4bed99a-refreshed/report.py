import json,sys
from pathlib import Path
root=Path(sys.argv[1]);s=json.loads((root/'summary.json').read_text());a=json.loads((root/'audit.json').read_text())
rows=json.loads((root/'trials.json').read_text())
assert s['trials']==s['planned_trials']==72 and not s['sources_changed_during_run'] and s['formal_checkpoint_integrated'] and not s['abort_reason']
x,y=a['current'],a['candidate'];lines=[]
lines += ['# Refreshed provider comparison — 2026-11-14','',
    'Measured frozen commit: **`'+s['source_commit']+'`**. Shared verified production checkpoint: **`54dbf90`**.',
    'This report is a separate batch from `2e5a46f`; do not pool the cohorts or overwrite the earlier results.','',
    'Completed **72/72 trials**: 12 cases × 3 paired repetitions × 2 engines. Source hashes remained unchanged; the shared checkpoint matched. No final Apply/Submit click occurred.',
    '', '## Results','',
    '| Measure, 36 trials per engine | Current | Experimental |','|---|---:|---:|']
metrics=[('Correct values + required source questions','audited_correct',False),('Reported ready','ready',False),
    ('Correct and ready','ready_and_audited_correct',False),('False-ready at the filling boundary','false_ready_audited',False),
    ('Median active filling seconds','median_active_s',True),('Total active filling seconds','total_active_s',True),
    ('Avoidable known/repeated questions','avoidable_questions',False),('Necessary missing-fact questions','necessary_questions',False),
    ('Writer model starts','provider_calls',False),('Writer-reported tokens','reported_tokens',False),
    ('Fresh-case correct / 12','heldout_audited_correct',False),('Browser/model exceptions','exceptions',False)]
for label,k,decimal in metrics:
    fmt=lambda n:f'{n:,.2f}' if decimal else f'{n:,}'
    lines.append(f'| {label} | {fmt(x[k])} | {fmt(y[k])} |')
lines += ['', f"Median active time fell **{a['median_time_reduction_pct']:.2f}%** within this cohort.",
    'Simulated owner replies were immediate; this excludes real human waiting and is not end-to-end application time.',
    'The callback/token audit covers starts of the configured writer (`devstral2-123b`), not separate embedding lookups, every retry beneath the SDK, or total billing. Embedding latency, when used by the existing engine, remains included in active time.',
    '', '## Per-case outcomes','',
    'Each row contains three trials per engine. Correctness requires independently graded values and evidence that genuinely missing personal answers were asked.',
    '', '| Case | Current median s | Experimental median s | Current correct / ready | Experimental correct / ready |',
    '|---|---:|---:|---:|---:|']
for name,c in x['per_fixture'].items():
    d=y['per_fixture'][name]
    lines.append(f"| {name} | {c['median_active_s']:.2f} | {d['median_active_s']:.2f} | {c['audited_correct']} / {c['ready']} | {d['audited_correct']} / {d['ready']} |")
dates=[r for r in rows if r['fixture']=='fresh-compiled-conditional-date' and r['engine']=='current' and not r['graded_correct']]
lines += ['', '## What changed and what the experiment found','',
    '- Both previously failing review boundaries now pass. Independent verification: **91 passed**, 13 dependency warnings, 182.10 s. This includes the exact three reviewer cases and surrounding forms/address/upload/approval/extraction checks. Claude reports 1139 full-suite passes; no new independent full-hour suite is claimed.',
    '- The corrected production files and tests were captured in a shared checkpoint. The candidate retains its separately measured upload-cache repair. Both engines receive the same underlying source content; prepared-address facts are available to both.',
    '- The four fresh cases use the real candidate compiler on synthetic structured applicant data: name/email components plus unknown interview preference, education in a closed author shadow root, contact information through an iframe/wizard transition, and an exact-day question with only a month in its source.',
    '- The simulated owner reads only supplied facts or a declared missing-answer oracle. It uses entity context when supplied; ambiguous unscoped questions are not guessed. The grader never supplies an answer.',
    '- The eight remaining cases are development/regression cases, including the original upload and 50-field cases. These are not fresh evidence of unseen-layout support.',
    f'- The conditional-date baseline failed in **{len(dates)} of 3** trials. Inspect `trials.json` for its exact values and records. A month/year copied from a truthful source is still insufficient for a control requiring `MM/DD/YYYY`. The candidate asks for the missing day rather than assuming one.',
    '- This is a filling-readiness finding. No submission was attempted, and it does not establish that a real portal would accept an invalid date after Submit.',
    '', '## Interpretation and remaining gates','',
    'The principal tested speed mechanism is resolving known facts before invoking the model. The existing engine already inventories visible fields in a batch; these results do not show that merely scanning the page first caused the improvement.',
    'Each fresh case has at most one genuinely missing answer at a time. Focused tests exercise multi-question batching, but this provider cohort does not quantify its live waiting-time advantage.',
    'The native loop counters differ between engines; identical configured model budgets are not a strictly matched total resource budget. Order was counterbalanced by repetition plus fixture index (18 pairs per starting engine). Three repetitions, authored probes and synthetic pages are insufficient to generalize across all live ATS products.',
    'No production adoption or merge is authorized by this report. Live paired drafts, actual personal-file/document compilation, company/referral/learned-answer input parity, custom widgets, desktop/authentication setup and portal coverage remain required before that decision. Preserve the existing user policies and submission approval boundary.',
    'The previous saved-posting preflight did not supply matched US pairs across six portal families; several Workday URLs returned HTTP 521 in Docker. Live preparation must resolve that setup separately rather than calling listings or error pages completed applications.',
    '', '## Evidence and reproduction','',
    '- `summary.json`, `trials.json`, `trials.csv`, `cases.json`, `source-manifest.json`, `audit.json` and `run.txt`.',
    '- The original strict values/provenance scores are retained. The audit permits only formatting-equivalent telephone digits for the iframe/wizard case; it never repairs an answer, invents a date, chooses an unknown preference or ignores a missing source question.',
    f"- Telephone-format adjustments: **{len(x['semantic_adjustments'])+len(y['semantic_adjustments'])}**. Details, if any, are in `audit.json`.",
    '- `PROTOCOL.md` freezes the cohort and interpretation before fresh provider results. The four layouts are fresh runtime probes authored with knowledge of the system, not an independent third-party blind test set. They are now development cases for future work.',
    '- Reproduce from the measured commit with Chrome and the configured provider environment:',
    '  `PYTHONPATH=src python -m jobagent.experiments.benchmark --model --cohort refreshed --repetitions 3 --output /tmp/NEW-ROUND2-RUN`.',
    '- Every trial uses isolated data/state and browser context. No actual applicant, credentials, contact CSV, production database, real resume or real job application is present in these artifacts.',
    '- Production source and `/app/src/jobagent` were not changed by this experiment.']
(root/'REPORT.md').write_text('\n'.join(lines)+'\n')
