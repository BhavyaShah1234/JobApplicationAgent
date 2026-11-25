# SAP: what the agent found, what it fixed, and where it stopped

## Update — 2026-11-25: City defect diagnosed in a live attempt

One normal native-agent attempt reached the SmartRecruiters form without a restriction. The trace had been appending input/network evidence after menu HTML to an error string capped at 200 characters, discarding the evidence. Candidate code saves those fields separately.

The new trace shows HTTP **200** autocomplete responses, a focused input containing `San`, and suggestions inside **`spl-select-option`** elements. The reader searched explicit DOM ARIA roles and missed these custom elements. This establishes a selector/ownership defect in this attempt; it does not show an autocomplete network refusal.

Candidate changes add these hosts to option discovery/clicking, preserve an empty owned menu during search, and select within the control's own menu. Real-Chrome regressions cover a shadow-root City input plus an unrelated menu offering identical text. Live revalidation, the second SAP role, and another SmartRecruiters employer remain pending.

SAP's Developer application is SmartRecruiters; Ericsson and Teradyne are separate SuccessFactors targets. Evidence: `outputs/verification/portal-acceptance-20261125T003317Z/smartrecruiters-city-evidence-1.json` and the matching log/screenshot. Nothing was submitted. Source snapshots and status are in the same directory; do not infer deployment or resolution from local tests.

Written 2026-11-24. Status: **parked** by the owner until Codex's usage replenishes. Nothing was submitted and no account was created.
Evidence: `outputs/sap/*/application/` (challenge screenshots and JSON, `application.json`), `state/logs/combobox-failures.jsonl`,
`state/logs/apply-start.png`, `outputs/benchmarks/ten-portals-20261122T110915Z/BENCHMARK_LOG.md`.
No emails, passwords or tokens are written in this file.

## 0. In one page

- SAP has **two different front doors**, and neither is an ordinary form. The careers site is behind a **Cloudflare** check; the
  "Developer" application itself is a **SmartRecruiters** form behind a **DataDome** human check.
- The agent now reaches that form, recognises both checks as human steps (never solves them), and **fills 8 of 13 fields correctly**
  (first and last name, email, email confirmation, phone, LinkedIn, website, interest question).
- It stops at **City**: the type-ahead's suggestion list stays empty when the agent types. **The cause is not proven.** Two explanations
  fit and the evidence to separate them was never collected (section 5).
- The owner then saw SmartRecruiters' **"Access is temporarily restricted"** page. All SAP runs were stopped. Further attempts are on hold.
- The "experiments to learn what is monitored and how bans are decided" were **not** run, for the reasons in section 7.

## 1. How SAP's application path works (as observed)

| Step | What was seen |
|---|---|
| Old posting links | `jobs.sap.com/job/<City>-<Title>-<Code>/<id>/` (the format a search engine returns). **They now redirect to the new careers site's general "Open jobs" list**, so a closed or moved posting looks like a page with no form. Two test postings were lost this way. |
| New posting links | `jobs.sap.com/en/jobs/<id>/<slug>/` (found by search; for example the San Ramon "Developer" posting). A real job page with its description. |
| Careers site protection | **Cloudflare** "Performing security verification / Verifying…" (a spinner, no puzzle). It usually clears by itself, but only in a foreground window, and a plain headless browser stays on "Just a moment…". |
| The application | The "Developer" posting applies through **SmartRecruiters** (`jobs.smartrecruiters.com/oneclick-ui/company/SAPITBusinessSysteme/publication/<uuid>`), not through SAP's own SuccessFactors. |
| SmartRecruiters protection | **DataDome**: the top-level page body is **empty**; the only content is an iframe from `geo.captcha-delivery.com` showing "Verification Required: Slide right to secure your access" and the text "We detected unusual activity from your device or network". |
| The form (when reached) | A SmartRecruiters web-component form (`spl-` elements): profile image and file uploads, name, email and confirm email, **City \*** (type-ahead), country code, phone, LinkedIn, website, "Let the company know about your interest". |

## 2. Timeline of the attempts (UTC; the owner's clock is 7 hours earlier)

| When | Run | Result |
|---|---|---|
| 2026-11-23 20:41-20:45 | first SAP posting (San Ramon "Developer - CA 945") | Cloudflare check handed to the owner, then the posting redirected to the general list |
| 11-24 07:51 | same posting, again | Cloudflare check; the owner's `done` arrived 14 minutes in, 30 s before the shared deadline, and the retry had 23 s left: **blocked** (fixed, section 4) |
| 11-24 08:10 | same posting | no check this time; ended at the general list ("no application form") |
| 11-24 08:31 | new live posting `744000155054281` | page was an empty body with a DataDome frame; reported as "no form" (detection gap, fixed) |
| 11-24 08:35-09:13 | five more runs of the same posting | fixes applied between runs; each reaches the form (8/13 fields) and stops at City |
| 11-24 ~09:20 | (the owner's window) | **"Access is temporarily restricted"** |

In total: **six apply runs against the SmartRecruiters form in about 45 minutes (08:31-09:14 UTC), plus one read-only probe visit** from the
same IP. Each run opens the page, scrolls and types into it, and attaches a debugging connection to the browser.

## 3. What worked

- The posting is found, scraped and recognised; the Developer role's apply link is followed to SmartRecruiters.
- Cloudflare's and DataDome's checks are **detected and handed to the owner** (never answered by the agent).
- Name, both email fields, phone, LinkedIn, website and the interest question are filled and **read back verified**.
- City is derived by the existing job-city logic ("San Ramon"), unchanged.

## 4. Fixes shipped while working on SAP (all tested, all deployed)

| Commit | Fix |
|---|---|
| `41ca008` | A **blank page whose only content is a verification provider's frame** (DataDome, hCaptcha, Cloudflare challenge) is that challenge for the human, not "unknown". Before this the run reported "no application form". |
| `31dd91d` | A `done` answered just before the shared deadline now gets **90 more seconds** for its three readiness checks. The new test fails without the fix. |
| `5daa170` | A **bare city** ("San Ramon") takes the one United States suggestion; two US matches, a non-US place, or a longer name that contains it are never guessed. |
| `476fed4`, `69bf644`, `dfd5d91` | When a combobox cannot match, the agent now keeps **evidence**: the offered options, the listbox's HTML (through shadow roots), the typed value and focus, and **the page's network requests while it typed** (URL without query values, status or failure, console errors). |
| `eabaf27` | **"Access is temporarily restricted" is a stop**, not a puzzle: no human request, no retry; the message says to wait (usually an hour or more) and run once. |

Related fixes made on the way, on other portals, are in `BENCHMARK_LOG.md` (new-tab Apply buttons, cookie banners, optional resume-match
uploads, combined account-and-application pages, search-and-click picklists).

## 5. The open defect: City never receives suggestions (unproven cause)

Evidence collected before the trace existed (`state/logs/combobox-failures.jsonl`, label `City *`):
- the input is `role=combobox`, `aria-controls="menu-spl-form-element_8"`, `aria-autocomplete=list`, `data-sr-id=location-autocomplete-search-search-input`;
- its listbox exists but is **empty** even after typing "San Ramon" and then "San" (only a template comment inside);
- the owner, typing "San" by hand in the same kind of window, **does** get suggestions including "San Ramon, CA, US".

So the widget works for a person. Two explanations fit and have **not** been separated:
1. **The autocomplete's own network request is refused or returns nothing for the controlled session** (for example answered by the bot-protection layer). The new trace would show this as a failed or non-200 request.
2. **The agent's typing does not trigger the search** (key events, focus, or a debounce). The trace's input value and focus would show this.

The trace code is deployed (`dfd5d91`) but **has never completed a run to the City box**, because the last run stopped at a human-check
request and was then stopped on purpose when the restriction appeared. **No claim about the cause is made in this report.**

How to settle it, once the restriction has cleared: run the application **once**, with a person watching, and read the last `City *` record in
`state/logs/combobox-failures.jsonl` (sections `input:` and `network since typing:`). A non-200 or failed autocomplete request means
explanation 1; a request that never fires or an empty input value means explanation 2.

## 6. The restriction

The page the owner saw lists these reasons, quoted in summary: rapid taps or clicks; JavaScript disabled or not working; automated (bot)
activity on the network (showing the IP address); use of developer or inspection tools. It is **IP-wide and temporary**.

What is and is not known:
- **Known:** it appeared after six apply runs and one probe from one IP within roughly an hour, each attaching a debugging connection.
- **Plausible, not proven:** the repeated sessions and the debugging connection contributed. The page's own reasons point both ways.
- **Unknown:** which signal DataDome weighted, how long the restriction lasts, and whether a person on the same network is also affected
  (the owner saw the page in their own browser window).
- The agent cannot remove the debugging-connection signal. Hiding it would be the disguise the project rules forbid.

## 7. Why the "monitoring and ban experiments" were not run

The owner asked for more experiments using the SmartRecruiters portal "in different ways" to learn what is monitored and how temporary
bans are decided. These were deliberately **not** done:
1. **It is detection-evasion research.** Mapping which behaviours a bot-protection system watches and where its thresholds sit has one use:
   to stay under them. The project's firm rule (see the plan: "Spoofing and evasion code") excludes that work, whoever asks.
2. **It makes the problem worse.** The network is already restricted; every probe lengthens the restriction and could extend it to the
   owner's ordinary browsing and to other SmartRecruiters-hosted employers.
3. **It is not needed to fix anything the agent is allowed to fix.** The agent's job is to fill forms and hand human checks to a person.

Legitimate ways to learn more, all passive: (a) the evidence already on disk (this report); (b) the vendor's own public documentation, read
by a person; (c) the owner comparing, by hand and without the agent, how the same page behaves in an ordinary browser; (d) asking
SAP or SmartRecruiters whether an application route for automated assistants exists.

## 8. Recommendations

1. **Pacing rule for protected portals:** at most one attempt per portal per hour from one IP, and stop after any restriction page. The
   restriction stop is implemented (`eabaf27`); a per-portal cooldown record is not yet.
2. **Do not iterate live on a protected portal.** Reproduce behaviour offline (the tests use fake pages) and spend live attempts only when a
   fix is ready.
3. **When resumed:** wait for the restriction to clear (the owner confirms the page loads normally), then one watched run; read the trace; then decide.
4. **If the trace shows the autocomplete request is refused:** the agent cannot fill this form. The honest fallback is that the candidate
   completes SmartRecruiters forms in an ordinary browser, with the agent supplying the values it has (name, contact, job-city address).
5. SAP's own sites are Cloudflare-gated; the agent's desktop-browser mode with a person passing the check is the supported route.

## 9. Open items

- Prove or rule out each explanation in section 5 (one watched run after the restriction clears).
- Per-portal cooldown record so a restricted portal is skipped automatically for a period.
- SAP's SuccessFactors-based roles (if any are still live) are still untested; Ericsson's two roles stand as the SuccessFactors proof.
