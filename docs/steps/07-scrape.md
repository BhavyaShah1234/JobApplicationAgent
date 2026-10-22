# Step 7 — Reading job postings, portal memory

## Run it

```bash
docker compose exec agent jobagent scrape URL [URL ...] [--file urls.txt] [--workers 5] [--refresh]
docker compose exec agent jobagent step scrape-check                                        # 5 ATS families
docker compose exec agent jobagent step scrape-check --urls-file /app/tests/e2e/scrape_urls_testcsv.txt   # test.csv companies
```

Output per posting: `outputs/<company>/<job-id>/jd.json` + `jd.md`, a row in `jobs` (state/jobs.sqlite), and portal
notes in `state/portals/<portal>.json/.md`.

Gate (automated part) passed 2026-10-22: Greenhouse (Stripe), Lever (Palantir), Ashby (OpenAI), Workday (NVIDIA),
amazon.jobs, plus the `data/test.csv` companies — General Mills (Workday), KPMG (own site), DOWL (UKG UltiPro), Intel
(Workday), Cafe Valley (own site). All complete, second pass reused the portal notes; 5 postings in 31–45 s.

## `jd.txt` — the posting exactly as written

`outputs/<company>/<job-id>/jd.txt` (and `jobs.jd_text`) is the job description converted from the posting's own
HTML with no model involved: headings, `- ` bullets and paragraphs in their original order; scripts, hidden elements
and navigation removed. Linked job-description files (`.pdf`/`.docx`, not terms/privacy) are appended verbatim under
`[Attachment: …]`. The ATS step (10) submits this text unchanged.

Source choice: the page's description element (keeps headings) unless it is clearly thinner than the schema.org
JSON-LD description — JSON-LD was only Cafe Valley's overview and lost Lever's headings.

Sections: headings are `<h1-6>`, any short all-bold block (however nested — Workday), or a short `label:` line right
before a list. Recognised headings get fixed labels from rules (minimum/required/need to see → basic;
preferred/nice to have/stand out/thrive → preferred; what you'll (be) do(ing)/duties → responsibilities; who you
are/what we value → values; benefits/you'll enjoy; technologies we use → skills). Only unrecognised headings and
prose-only sections go to the fast model (temperature 0). List fields are filled with section items verbatim.
Folder ids come from the URL (gh_jid, jobId, opportunityId, Workday JR…, /jobs/N, UUIDs).

## Pipeline

1. Navigate in a throwaway Chrome session (public postings don't need the signed-in profile), with backoff and the
   challenge hand-off from step 2.
2. schema.org `JobPosting` JSON-LD if present.
3. Richest text block — the remembered selector first, then known per-ATS candidates; vision model if the text is thin.
4. Fast model (`qwen3-30b-a3b`) fills the schema; missing locations/company/title come from JSON-LD; the planner model
   is only a fallback.
5. The page's own bullets, grouped by heading (minimum/required → basic, preferred/nice-to-have → preferred, what
   you'll do → responsibilities, who you are/what we value → values, technologies we use → skills, benefits),
   replace the model's lists verbatim; model items duplicating a page bullet of another list are dropped.
6. `--workers N` postings in parallel; postings already in `jobs` are skipped unless `--refresh`.

## Learned while building

- One giant-model call per posting (~2 min) dominated the time; the fast model was 8x quicker with the same fields.
- Models drop or misfile bullet lists (Stripe's "Minimum requirements", Lever's "What We Value"): take lists from the DOM.
- Workday JSON-LD carries legal entity names ("2100 NVIDIA USA"): ask for the brand name.
- Lever's location sits outside the description block: fill it from JSON-LD instead of escalating models.
- The single signed-in Chrome profile must be used one run at a time: `state/browser.lock` queues runs.
- Cafe Valley's job site has a `?blackhole=` honeypot link: discovery (step 14) must never crawl every link.
