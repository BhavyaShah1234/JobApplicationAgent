# Opt-in field planning

New applications use the existing filler. To try the staged discovery, source resolution,
batched questions and verified filling method:

```sh
jobagent apply outputs/COMPANY/JOB_ID COMPANY --filling-strategy experimental
jobagent run POSTING_URL --filling-strategy experimental
```

`submit` accepts the same option. Submission still uses the existing review, approval and
submission checks. Setting `FILLING_STRATEGY=experimental` selects it for new runs;
`original` is the default. An application's recorded strategy is retained on resume unless
explicitly overridden. A failed experimental fill blocks; it does not silently change engines.

The runtime is in `jobagent.filling`; it does not import the experiment harness. Both
strategies share application navigation, account handling, source checks, referrals,
address selection, form controls, challenge handling, checkpoints and submission guards.
The experimental planner discovers the current reachable controls, prepares an answer
plan, asks about missing answers together, fills and verifies, then repeats when conditions
or wizard pages expose more controls. It cannot inspect unrequested server-side pages.

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
small rollout gate, not proof that every future portal will work. Until that gate passes,
retain the original default and both explicit strategy choices.

Private screenshots, resumes, prompts and answers stay in ignored `outputs/verification`.
Only redacted measurements and findings belong in Git.
