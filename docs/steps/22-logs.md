# Step 22: readable run logs

`jobagent/log.py` is the one place that decides how a run talks. Every line carries its step tag (`[apply]`, `[queue]` ...), colour
means something (red error, yellow warning, green success, cyan command to paste, magenta box = a question waiting for you, dim =
detail), identical consecutive lines collapse, long dumps show their first line, and a crash is one red line plus the path of the
full trace. `NO_COLOR` is honoured; `log_level: quiet` (config/logging.yaml, or `LOG_LEVEL=quiet`) hides the dim detail on screen only.

Every run writes `state/logs/<UTC date-time>-<step>[-<job>]-<run id>.log` (plain) and a `.jsonl` twin with
`{ts, run, job, level, step, msg}` per record, so two runs on one day never share a file and a dashboard can follow one run. Model
fallback notices go to the run in progress. A log file that cannot be written is reported once on stderr and then skipped: logging
never stops a workflow. HITL questions print literally (no markup interpretation) with the command to answer them from the host
(`docker exec -it ...` when the answer is typed at a hidden prompt).
