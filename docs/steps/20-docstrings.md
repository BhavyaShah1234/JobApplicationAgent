# Step 20: docstrings

**Standard:** Google style. A module says what it is for in a paragraph; a public class says what one instance is; a public function says what it does in one line,
and, when it takes parameters, returns something non-obvious or raises on purpose, has `Args:`, `Returns:` and `Raises:` sections. Existing explanations of *why*
(comments and docstrings) were kept.

**How it is measured:** `python scripts/docstring_audit.py` (summary and the worst files), `--list FILE` (every undocumented public name in a file),
`--check` (the ratchet), `--write-baseline`. "Public" means no leading underscore, including nested helpers with public names. The ratchet is a test
(`tests/unit/test_docstring_audit.py`): no file may have more undocumented public names than `docs/docstring_baseline.json` records, so coverage can only go up;
after documenting a file, run `--write-baseline` and commit it.

**State:** coverage went from 54.8% to the figure in `docs/docstring_baseline.json`. Documented with real descriptions, read from the code: the skill graph, job queue,
eligibility, multi-input runs, logging, configuration, the dashboard server and data layer, the outcome statistics, the tailoring module (`tools/tailor.py`),
the multi-agent writer and the inbox. **Not done:** the rest, mostly the form filler (`filling/`, `steps/apply.py`, `tools/forms.py`), the ATS checker adapters,
the desktop browser and the experiments. They need a careful read of each function, and a wrong docstring is worse than none, so they were left rather than generated.
`scripts/docstring_audit.py --list FILE` gives the to-do list per file.
