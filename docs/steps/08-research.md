# Step 8 — Company research

## Run it

```bash
docker compose exec agent jobagent research "General Mills" "DOWL" [--refresh] [--workers 5]
docker compose exec agent jobagent step research-check     # 10 companies, fresh then cached
```

Profiles: `state/companies/<slug>.md` (readable, numbered sources) and `.json` (used by later steps). Cached 7 days.

Gate passed 2026-10-22: the `data/test.csv` companies (General Mills, KPMG, DOWL, Intel, Cafe Valley) and Stripe,
Palantir, OpenAI, NVIDIA, Amazon — every profile has a website, what-they-do and cited official pages; second pass
from cache in 0 s; ~25–60 s per company (10 in parallel ≈ 90 s).

## How it works

1. Official site: web search (ddgs), first hit on a non-aggregator / non-ATS domain whose name matches the company.
2. Official pages: home + about/mission/values links, plus a "<company> about us mission values" search that accepts the
   company's other domains (aboutamazon.com). Plain fetch + trafilatura; thin pages (JS-heavy, bot-shy: Intel, OpenAI)
   are rendered in a throwaway headless Chrome. Cookie/consent/privacy lines are stripped.
3. Wikipedia summary API; news from the last year (ddgs). The company's core name must appear as a whole word; social
   posts are excluded.
4. The fast model writes the profile under an **enforced JSON schema**: every item is `{text, sources}` with 1–4 source
   ids (minItems 1), lists are capped (maxItems), sentences capped (maxLength). Items citing unknown sources are dropped;
   news must cite a news source.

## Learned while building

- Free-form tool calls made the fast model loop (repeating sentences to the token cap) — prompts and frequency penalty
  didn't help; server-enforced maxItems/maxLength did (~8 s, ~500 tokens).
- Trailing "[n]" citation markers were sometimes forgotten; structural `sources` arrays fixed it and made runs repeatable.
- Constrained decoding at temperature 0 leaves early lists empty unless they have minItems.
- Substring name matching pulled in "Dow Jones" and "@PJ_Dowl" for DOWL; whole-word matching fixed it.
- A company's homepage can be its shop (amazon.com); search for its about pages.
