# Step 6 — Router and inputs

## Run it

```bash
docker compose exec agent jobagent route "https://boards.greenhouse.io/acme/jobs/123"
docker compose exec agent jobagent route "Google, Microsoft; NVIDIA Corporation"
docker compose exec agent jobagent route targets.csv          # a file in data/ (or a full path)
```

`route` is a dry run: it prints the work items and warnings, nothing is applied to. Gate passed 2026-10-22:
16 fixture tests (URLs, company lists, CSV with mixed referral cases, semicolon CSV with header aliases, XLSX,
missing file / unknown columns, the LangGraph node) plus live runs on `data/targets.csv`.

## Rules

| Input | Becomes |
|---|---|
| existing `.csv` / `.xlsx` / `.xlsm` (in `data/` or a full path) | one item per row: the row's job URL if present, else its company |
| text with `http(s)://` links, or links without a scheme that have a path (`jobs.lever.co/acme/123`) | one `url` item per link (deduped) |
| anything else | company names split on `,` `;` new lines; legal suffixes rejoined ("Acme, Inc."); case-insensitive dedupe |

File columns are matched loosely (company/organisation/employer, url/job url/link, contact/referrer, email,
consent). A referral is attached only when consent is yes/true/1, a contact name is present and any email is
valid; otherwise the row is used without a referral and a warning names the row. Rows with neither a company
nor a URL, and duplicate rows, are skipped with a warning.
