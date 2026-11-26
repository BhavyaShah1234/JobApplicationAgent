# iCIMS portals: everything learned, what the agent does, what it cannot, and why

## Current acceptance status — 2026-11-26

**Not checked off yet.** Production image `bec2cb1` is deployed (`sha256:b22d834d8749335e59f605ff5f3ba07032a71b62f6d08099b4eac636a09f4140`). All 136 image Python files match that committed revision. Claude is independently implementing the dashboard presence toggle; its unfinished files are excluded from this pinned validation image. File/runtime reservations are recorded in `CHAT.md`.

On resumption after the machine restart, the isolated desktop showed an entirely white Chrome window. Disabling remote Xvfb shared-memory rendering restored the visible posting; x11vnc also needed `-noshm`. The launcher now checks the actual RFB backend instead of treating the static noVNC page as sufficient readiness. Twelve launcher tests pass.

A native run then signed in successfully with saved credentials and read back all 16 Garmin screening answers. It stopped before intermediate advancement because it repeatedly replanned the two referral fields. A real-browser regression reproduced the failure: saving a HITL answer changes the source fingerprint, after which projecting derived agreement facts from only unfinished controls makes the broad referral evidence oscillate. Commit `fc85762` projects against the full visible form, retaining applicable future hints. The regression failed before the fix and passes afterward. The round budget was not increased.

The same approved connection captured the actual iCIMS identifier structure without field values or callback tokens: one email form with an unnamed invisible password control, plus seven button-only provider forms. Commit `bec2cb1` recognizes this structure only on the explicitly trusted, exact iCIMS identifier route; named/visible password additions, mixed forms, wrong hosts/routes and multiple credential forms remain rejected. It also adds origin-only diagnostics for ambiguous tabs. The related source-routing, adaptive, agreement, referral, identity and handoff group passed **274 tests in 250.99 seconds**. These checks overlap prior groups and are not a full-suite result.

The first retry from the rebuilt image expired while awaiting Chrome Allow, before any form input. The browser is retained at `http://127.0.0.1:6083/vnc.html`, and the bounded supervisor is stopped. Corrected native screening → actual final review and independent semantic audit are still required. Earlier 80/80 counts failed the motivation audit and are not acceptance evidence. This remains assisted verification of an existing draft, with a recorded same-job screening navigation and explicitly supervised salary/test-motivation answers; it is not proof of fresh, unassisted portal coverage.

No final application submission, new documents or ATS scans. Evidence: `outputs/verification/icims-final-review-20261125T082138Z/`, particularly `status.json`, `resumed-final-regressions.txt`, `referral-loop-before.txt`, `referral-loop-after.txt`, `resumed-committed-image-proof.json`, `actual-identifier-form-structure.json`, `native-final-bec2cb1.log` and `display-rendering-fix-proof.json`.

## Previous acceptance status — 2026-11-25

**Not checked off yet.** The production image is now `765aa15`; all136 installed Python files match this checkout. Garmin18475 has reached its actual Confirm Application / Application Review stage without final submission. The latest completed correction run read back80/80 fields, but failed the independent motivation check because it contained only a city name. Raw verification counts from that run are not acceptance evidence.

Live audit subsequently confirmed correct posting title, original November23/2026 application date, legal first name, ASU/Arizona, three distinct employers with their own titles and dates, both immigration answersYes with the proper sources, relocation originTempe/Arizona, GPA4.0-or-above, matching CSV referral contact and both signature boxes checked. Code fixes now reject a bare city as motivation and preserve typed routing after source refresh. Salary and supported test motivation were forwarded through native HITL and are explicitly supervisor assistance, not autonomous native research/writing.

A subsequent repeated salary request exposed an adaptive-cache defect: freshly scoped HITL facts were not in the evidence fingerprint until the next source refresh. The new `765aa15` fix includes those session facts immediately;58 adaptive/browser/semantic tests passed in62.77s, including a regression for this exact transition. The new native connection request expired before Chrome approval; that attempt made no form changes. The bounded supervisor has been stopped and the browser remains open at `http://127.0.0.1:6083/vnc.html`. Correct screening and final-review readbacks must be audited before checking the service off.

The correction test uses the installed production session and filling APIs. Its one assisted navigation opens this same job's previously visited screening URL, observed from the dedicated browser's history, without any query/token reuse. All field input, answer verification and intermediate progression remain native Voyager. This is staged assisted verification; it is not a fresh, autonomous, single-run acceptance matrix. Human CAPTCHA solving and Chrome permission remain necessary when requested.

Two exact job-specific salary/test-motivation literals introduced by this test were removed from shared learned answers, with a private backup, so they do not become defaults for other Garmin roles. Genuine saved owner declarations are retained. No application has been submitted, no documents have been generated and no ATS scans have been run during these checks.

Evidence and current state: `outputs/verification/icims-final-review-20261125T082138Z/status.json`, `native-cache-fixed.log`, `human-refresh-tests.txt`, `human-refresh-production-image-proof.json`. Earlier failed attempts remain archived. Test groups overlap; their counts must not be added together. No full-suite claim is made here.

## Garmin actual final-review test — corrections deployed, acceptance pending (2026-11-25)

**iCIMS is not checked off yet.** Garmin18475 progressed through its real three-stage wizard: Candidate Profile, Job Specific Questions, then Confirm Application / Application Review. The normal installed9c0e3b4 CLI saved the two intermediate stages without clicking final Submit. Visual inspection confirmed the final page explicitly says changes occur before submission. However, the run blocked on reading the generated review iframe, and independent inspection found incorrect screening answers despite successful value readback. That run is a failed accuracy check, not a service pass.

The fixes are committed in4ff3ad7 and4fab8e9 and deployed in the main image4fab8e9; all136 installed Python files match the canonical checkout. Native DOM ownership/collection getters handle generated forms that shadow `element.form` with a JavaScript model or `form.elements` with a named control. A read-only live probe now sees64 visible review fields with complete coverage. Generated iForm captions use their actual single-field cell text and explicit collection row IDs, preserving school/employer identity and complete split-date labels rather than database names such as TITLE or EmpName. Focused real-Chrome fixtures cover these cases, including externally associated form controls.

Screening fixes separate current OPT participation, current STEM OPT extension, current nonimmigrant employment, and termination history; unrelated authorization/age/prior-company booleans cannot support them. Relocation FROM uses the original personal residence, not the generated job-city mailing address. Motivation requires supported prose. GPA classification precedes degree classification and understands “4.0 or above.” Adjacent generic referral-info fields bind to the sourcing question. Expected-salary questions route to the owner's JD-range/otherwise-local-median policy. The owner explicitly confirmed Yes to Garmin's combined current-status/potential-future-filing question, and the exact question was saved privately.

The final targeted regression group passed **233 tests in165.13s**; the docstring ratchet and whitespace checks pass. A separate replay of the actual captured Garmin screening questions against current saved sources gives nonimmigrantYes, OPTYes, terminationNo, GPA4orabove and relocation originTempeArizona; it deliberately requires composition for motivation and policy lookup for salary. This is source-replay evidence, not a corrected live application result. The preceding live salary lookup was performed by the supervisor, selecting the offered70–79,999/year bucket from Salary.com's Junior Software Engineer Olathe median77,978; it must not be counted as autonomous native research.

Both final-review signature boxes were still unchecked when inspected. A fresh normal-CLI run must correct the screening answers, fill and audit the true review, including those acknowledgements, and stop before final Submit. No application has been submitted, and no new documents or ATS scans were generated. Both correctly spelled public posting URLs, `https://careers.garmin.com/jobs/18475` and its `/job` variant, returned HTTP200 through the guarded proxy. Earlier generic-page reports did not establish a shortened-URL defect: subsequent screenshots showed mistyped hostnames. This job's apply_url/source_url remain unchanged.

Evidence: `outputs/verification/icims-final-review-20261125T082138Z/`, particularly `actual-review-with-semantic-errors-artifacts/`, `stage3-passive.png`, `review-reader-fixed-evidence.json`, `review-markup.json`, `review-final-regression-tests.txt`, `watched-questions-source-replay.json` and `semantic-production-image-proof.json`. Test groups overlap; do not add their counts together. No full-suite or universal iCIMS guarantee is claimed.

## Production handoff and background check — SAS second employer, 2026-11-25

The opt-in ordinary-Chrome same-session handoff is integrated and deployed in the production image at **c5629e4**. All136 runtime Python files were hash-checked against the canonical checkout. The normal installed `jobagent apply` command (no experimental factory or PYTHONPATH override) filled SAS45908's candidate/profile page: **29/29 satisfied**, including two credential-presence checks and a byte-verified resume dispatch with a fresh successful parser POST receipt. Screenshot and final records confirm Cary/North Carolina, owner Gmail, ASU/Masters/completed YES, May1/2026, blank inapplicable education details and **University Outreach or Emerging Careers Events > Career Fair**. Native filling101.10s included6.01s waiting for three saved-fact/owner-choice HITL batches forwarded by the test supervisor; six writer starts. Manual entry/Chrome setup and earlier failed attempts are excluded, so this is not an autonomous speed benchmark.

One preceding production attempt stopped safely after the resume-parser POST had no successful receipt and the embedded form became empty. The owner reopened a fresh profile, and the next run passed. The cause of the blank response was not isolated; do not claim a code fix or guaranteed recovery. Another earlier raw29/29 result failed semantic source review and remains archived as a failure. Regression fixes now re-evaluate a cached policy referral when required referrer details appear without an available company contact. The latest launcher/referral/handoff/desktop/controller group passed147tests in25.40s; groups in other logs overlap, and no full-suite claim is made.

**This is still the first profile page of a four-step flow, stopping before Submit Profile.** No account creation or application submission was confirmed. Full iCIMS cross-opening/cross-company acceptance remains incomplete. Hyland and SAS both passed assisted profile fills; later portal pages remain unvalidated.

For background operation, `python3 scripts/desktop_background.py` runs the host bridge with an isolated authenticated X display, visible at **http://127.0.0.1:6083/vnc.html**. Ordinary Chrome stays on the host, using the same dedicated profile and public-only proxy. The GUI-only container is retained while that browser is live. The final window check found one agent Chrome window on the isolated display and none on the main display. Visible/minimized Chrome had interrupted the owner; after moving displays the owner reported one interruption, then none so far. Its cause/timing remain unresolved. A separate native connection survived390.5s of deliberate idle and resumed successfully. Every new CLI connection still requires Chrome's own Allow; no cross-process connection pool or extension was installed.

Commands and limits: [desktop-applications.md](docs/desktop-applications.md). Evidence: `outputs/verification/icims-cross-employer-20261125T063204Z/sas-isolated-production-pass-proof.json`, `sas-isolated-production-pass-artifacts/`, `isolated-production-image-proof.json`, `display-isolation-final-proof.json`, and `sas-isolated-fresh-cli.log`.

## Consented same-session handoff — live profile-fill success, 2026-11-25

Hyland14637 now works with an **assisted same-session handoff**: the owner reaches the candidate form in ordinary agent-owned Chrome, enables Chrome's remote-debugging UI and approves its connection request; the native Voyager job agent attaches without restarting and fills the existing form. Final candidate-v42/8a79e47 run reported Review15/15:12 ordinary source-verified fields, two credential-presence checks and one byte-backed resume upload. Direct DOM checks confirmed owner Gmail for Login and Email, Columbus/Ohio address, blank Address2 and both password fields present; secret values were not exported. One resume parse POST occurred. **Neither Submit Profile nor the application Submit button was clicked.**

This is the first candidate-profile page of a four-step flow. The native Review label means it stopped before Submit Profile; it does **not** prove reaching the portal's final application review, creating an account, or completing the required cross-company acceptance matrix. iCIMS remains unresolved overall. The same-session transport is now available as an opt-in production CLI feature (c5629e4 image deployed), retaining the exact-tab/profile and network guards. An assisted SAS45908 second-employer profile test passed29/29 before integration. The first isolated installed-image check stopped safely on a blank resume-parser response; the fresh-profile retry then passed29/29 as detailed above. Full-service acceptance remains incomplete.

Live observation uncovered and fixed embedded-parser settling/repeated uploads, the owner-email prefill rule, suffix password-confirmation recognition, byte-backed upload verification after document replacement, identical repeated uploadResume flags, and the final adapter's stale document-epoch record lookup. Receipt acceptance requires verified dispatched bytes, one successful same-frame/candidate-route parse POST, the new owned document and completed widget metadata; a filename alone never passes. The final adapter carries the old record only when that receipt owns the current file control and its recorded digest matches. Negative tests cover later navigation, conflicting flags, wrong bytes/digest, stale filenames and errors. Latest focused receipt/adapter/generic attachment group: **31 passed110.83s**, not a full-suite claim.

Final native filling took49.83s, with four writer starts and zero new fact-HITL prompts in that run. This excludes earlier manual entry, Chrome setup/approvals and failed diagnostic attempts, so it is not a speed benchmark or full-application timing. Borrowed existing documents deliberately keep the draft non-submittable. Two remaining summary review flags are credential-presence records, not blank passwords.

Evidence: `outputs/verification/portal-acceptance-20261125T003317Z/consented-handoff-9-proof.json`, `icims-hyland-consented-handoff-9.log`, `icims-consented-handoff-9-artifacts/`, and `resume-adapter-tests-v42.txt`. The filled browser remains open for owner inspection; no native fill process is active.

## Hyland session diagnosis — 2026-11-25

The owner reached Hyland14637's candidate form in ordinary Chrome with the same agent email and without explicitly signing in. The owner **corrected the initial Incognito report: Incognito also reaches the candidate form**. The earlier saved-session diagnosis depended on the withdrawn report and is retracted. An exact-posting native desktop test started with a previously nonexistent dedicated profile and no iCIMS login cookies, but reached the password page. After the email/privacy POST, the server returned303 to the tenant login-only route, then302 through the shared identity service to its identifier/password pages. The agent did not discard a candidate form or replace the callback URL. No password was attempted and nothing was submitted.

The captured cookie exclusions were ordinary DomainMismatch/NotOnPath cases; no other blocking reason appeared in this capture. This does not inspect every browser-internal decision or the owner's personal profile. **The cause remains unconfirmed.** Old cookies inherited from another employer are unlikely in this fresh dedicated profile. Differences in browser configuration, page instrumentation, navigation/interaction or server-side decisions still need isolation. Do not infer a missing saved login, an existing account, or automation detection from the password page alone. Never reuse a token-bearing callback from another browser as an application URL.

Further browser-mode comparisons narrowed the problem:

| Hyland14637 test | Result | Evidence type |
|---|---|---|
| Owner's ordinary and Incognito Chrome, same email | Candidate/profile form | Owner report, corrected Incognito result |
| Native Voyager in fresh managed desktop Chrome | Password-only login | Native artifacts and passive redirect capture |
| Owner manually performs entry in that managed window | Same password-only login | Owner report and passive path/heading/screenshot observation |
| Human-only Chrome, same agent-owned persistent profile and same public-only proxy; no automation/debugging connection | Candidate/profile form | Owner report |
| Normal native restart on that retained profile after human-only success | Password-only login again | `icims-hyland-native-after-human-entry.log` and final artifacts |

These results implicate the managed-browser setup rather than requiring native field-filling actions to reproduce the failure. They do not isolate a particular flag or prove deliberate automation blocking: process restarts also change in-memory/session state. The managed password page had cookies enabled and no installed shadow helper at the time inspected; its `attachShadow` function remained native. No browser-identification property was modified. Human-only Chrome was never automated. Only stale process-lock symlinks owned by its confirmed dead process were removed before the standard broker resumed; authentication cookies/storage were not cleared or copied. No password, reset, account submission or application submission occurred.

The startup/restart handoff failed in these comparisons. A subsequent experimental connection to the existing browser, without restart and with Chrome's explicit permission plus profile/origin/submission guards, succeeded in filling the candidate-profile page; see the latest result above. This does not isolate the earlier causal flag or validate the full application flow. Official reference: [Chrome DevTools session connection](https://developer.chrome.com/blog/chrome-devtools-mcp-debug-your-browser-session).

Evidence: `outputs/verification/portal-acceptance-20261125T003317Z/hyland-session-diagnosis.json`, `hyland-matched-redirects.jsonl` (origins/paths/query-key names only), and `icims-cookie-metadata-diagnosis.json` (metadata only, no values). **The earlier universal claim that remote-controlled Chrome explains the missing registration route is not established.** The historical Peraton observations below do not override this matched Hyland comparison.

## Latest live check — 2026-11-25: two corrected Garmin cases, matrix incomplete

Garmin19098 also reached Review with distinct, source-bound employment rows and correct split dates: 67 source-verified answers plus two portal-held credential presence checks out of70 records. The owner reports solving one CAPTCHA during login; this is an assisted result. No submission. Another employer is still required before iCIMS is resolved.

SAS45908 reached the shared password-only page with no signup route. The owner's requested registration-only restart stopped before a proposed password-reset action; it made no additional password attempt or reset request. An earlier explicitly approved recovery requested one email, but none was found in INBOX or Spam. No SAS account creation or password change is confirmed. Commit6a405d9 preserves the recovery browser and existing credentials, records completion only after signed-in success, ends a reset after one missing-email wait, and redacts auth URL queries. The older blanket conclusions below about registration capability and candidate-only password entry are historical observations, superseded by the guarded native account flow described in these updates.

The alternate-employer Hyland14564 test also ended at the shared password-only page without a signup route. Native apply automatically switched Docker to desktop and verified three entry/privacy/email records, then handed authentication to the account agent. Registration-only recovery protection stopped the proposed reset before dispatch. No password attempt, recovery request, account creation or submission occurred. This is a blocked cross-company test, not Review or a third acceptance pass. Proof and final artifacts: `outputs/verification/portal-acceptance-20261125T003317Z/icims-hyland-1-proof.json` and `icims-hyland-1-artifacts/`.

The native Voyager agent signed in to Garmin with the employer-scoped saved credentials after a later password screen. The observed shared login origin (`https://login.icims.com`) was explicitly trusted for this test; the global desktop origin guard was not relaxed.

Watching the 68-field profile exposed errors hidden by the old verification count: all three work-history rows reused a generic saved employer/title; after that was fixed, split end-date month/day controls still retained incorrect prefills. The reader now recognizes the form's own numbered fieldset legends. Entity guards reject unscoped employer/title/date facts in role rows; an explicit last-three-employers instruction selects roles by their own source dates. Split date parts are rendered from the corresponding source date, with first-day approximations only under the saved owner policy. Optional ongoing end dates are cleared through the real blank select option. Writer proposals for date parts must match that rendering.

A corrected native run on Garmin18475 reached Review: **65 source-verified answers plus two portal-held credential presence checks out of68 fields**, with the optional suffix blank. Direct DOM and final-artifact checks verified distinct Logos/Anton/TGen roles, matching titles and dates, blank current-role end date, August2026 for Anton, job-city address and EmployeeReferral. Known locations and owner-confirmed leaving reasons were forwarded through one HITL batch; this was not an unassisted run. The only blocker is the intentional borrowed-document guard. Nothing was submitted and no documents or ATS scores were generated.

The earlier66/68 Garmin baseline had duplicated employer/title answers and is **not semantic proof of correctness**. Latest source snapshots, failed artifacts, corrected artifacts and test logs are under `outputs/verification/portal-acceptance-20261125T003317Z/`. Tests:117 filling/date/entity cases passed109.76s; nine focused split-date/no-writer cases passed3.80s;32 date-policy/education/predicate cases passed1.30s (overlapping groups). A second Garmin opening and another iCIMS employer remain pending. Peraton registration is still unconfirmed; no recovery email was used or password changed.


## Update — 2026-11-25: agent-owned registration requested; validation pending

The owner now requests registration by the job agent using the normal signup path and vault-backed credentials; visible human challenges still go to the owner. The older conclusion below that all iCIMS registration must be manual is not a proved platform capability limit.

A new Peraton attempt found a late authentication defect: after the email/privacy page, the password page went to the application planner instead of the account agent. Candidate code adds a bounded handoff on later credential screens and scopes universal iCIMS credentials/rejection history to the employer's posting host.

The resumed registration model clicked password reset despite its prompt forbidding it and requested a reset email. The run was stopped; no new-password entry or password change was observed. The recovery email is left untouched. Candidate code now blocks recovery controls, URLs and emails except under the explicit reset command, and refuses a registration-only password fill on a login form.

A declared new-account password/confirmation pair in a combined application can be filled from the vault without clicking any account/application action. Full and incremental readers expose only a presence marker. Existing accounts and unknown prefilled credentials are not overwritten.

**No Peraton account creation or three-application iCIMS acceptance has succeeded yet.** Evidence, source snapshots and deployment/validation status: `outputs/verification/portal-acceptance-20261125T003317Z/`. Local tests alone do not establish service resolution.

Last updated 2026-11-24. Companies exercised: **Garmin** (reaches the review screen), **Peraton** (blocked at account creation).
Evidence: `outputs/benchmarks/ten-portals-20261122T110915Z/BENCHMARK_LOG.md`, `state/portals/icims.md`, `state/logs/combobox-failures.jsonl`,
and the screenshots listed in section 11. No emails or passwords are written in this file.

## 0. In one page

- The agent can **fill Garmin18475 and19098 to Review** with semantically checked employment rows and dates after signing in. Counts distinguish source-verified answers from portal-held credential presence checks (latest live check above).
- Native account creation at a new iCIMS employer remains **unverified**. The owner reaches Hyland's candidate form in ordinary and Incognito Chrome, while the fresh agent profile reaches its password page. The cause has not been isolated (latest diagnosis above).
- The agent must not disguise its browser or solve puzzles itself (project rules). Working saved credentials/session state should be used through the normal login flow; a human-only registration requirement is not established for every employer.
- Accounts are **per company (tenant)**: the same email is unknown at Peraton while it has an account at Garmin (section 2).
- Open choices for making that step less tedious are in section 8.

## 1. How iCIMS is built (what the agent sees)

| Piece | Detail |
|---|---|
| Hosts | `careers-<tenant>.icims.com` (often in a frame, `?in_iframe=1`); company skins such as `careers.garmin.com/jobs/<id>` and `careers.discounttire.com/<tenant>/jobs/<id>`; sign-in on `login.icims.com`. |
| Posting page | The job text is in a frame; a plain fetch returns almost nothing (13 characters) and the scraper falls back to the vision model. Postings carry JSON-LD with `validThrough`. |
| Listings | `https://careers-<tenant>.icims.com/jobs/search?ss=1&searchKeyword=<query>&in_iframe=1` is server-rendered and current. Web-search results for iCIMS jobs are often stale (HTTP 410 or "no longer accepting"), so check a listing before queuing a job. |
| Apply flow | Apply for this job online, "Enter Your Information" (email plus two privacy checkboxes), Next. |
| After Next, a returning candidate | hCaptcha puzzle, then the universal login (`login.icims.com/u/login/identifier`: "Welcome, Log in to <company> Careers"), then "Enter Your Password". |
| After Next, a new candidate in an ordinary browser | the registration form on the same company site (`.../candidate?from=login`): Candidate profile with "Create a login" (Login, Password, Re-enter), "Enter your information", education, employment, resume upload. |
| Social buttons on the login page | Google, Microsoft, Apple, Facebook, LinkedIn, Indeed, Corporate Login. They only sign in an account already linked to that provider; otherwise "Unable to log in with the social account. Please make sure your iCIMS account is connected to one". |
| Candidate profile page | 65 to 68 fields on one page: account (Login, Password, Re-enter), name and suffix, phone type and number, address type, address (an autosuggest list), country, state, source ("Please specify"), highest education (school, other school, degree, major, other major, graduated, city, country, state), three employers (employer, title, start and end date parts, reason for leaving, city, country, state), resume ("My Computer"), additional attachments, an SMS-consent select. |
| Dropdown widget | An `<a role="combobox" class="dropdown-select">` whose value is in `span.dropdown-text`; behind it a hidden native `<select class="dropdown-hide">` that holds only the options already loaded; the list loads over ajax into a panel `<id>_icimsDropdown_ctnr` with an `input.dropdown-search` and results in `<id>_dropdown-results`. The search filters on key presses, not on a programmatic `fill()`. |
| Upload widget | A styled "My Computer" button that clicks a hidden `<input type=file>` inside a zero-size wrapper. |

## 2. Accounts are per company

Garmin accepted the agent's email (the account was created by the candidate); Peraton, asked in an incognito window with the same email, showed the
**registration** form, so Peraton did not know the email. Therefore an iCIMS account is per tenant, not shared across iCIMS companies, and every
new iCIMS company needs its own registration.

## 3. Historical Peraton registration experiments (cause not isolated)

Same email, same clicks, different result depending only on the browser:

| Who clicks | Browser | After Next |
|---|---|---|
| Candidate | normal Chrome, incognito window | no puzzle; registration form |
| Candidate | the agent's own desktop Chrome, **fresh empty profile** | puzzle, then the universal login page |
| Candidate | the agent's own desktop Chrome, profile that had visited iCIMS | the login page |
| Agent | Docker Chromium | puzzle, then the login page |

What this rules out: the account state (same email), the agent's clicking (a person clicked), and stale cookies (fresh profile gave the same result).
The agent's Chrome is started with a remote-debugging port so it can be attached to. That difference was previously proposed as the cause, but it was not isolated from other browser, instrumentation, interaction and server differences. The owner corrected the newer Hyland Incognito comparison: it reaches the candidate form, unlike the fresh agent profile. Do not generalize these observations into a proved claim that iCIMS withholds registration from all controlled browsers.

**Rule: the agent does not work around this.** No hiding of the debugging port or webdriver flag, no fingerprint masking, no automatic puzzle solving
(project rules, `Spoofing and evasion code`). If the normal account flow remains unavailable, report the actual blocker rather than treating evasion or a universal manual-registration rule as the remedy.

"Option B" (the candidate clicking through in the agent's own desktop window, the agent taking over at the profile page) was built as an opt-in flag
`--candidate-start`, tried on Peraton, shown not to help, and **reverted on 2026-11-24** (commit `893b033`).

## 4. The working procedure (new iCIMS company)

1. **A person registers once**, in an ordinary browser, with the agent's email: open the posting, Apply, email and both privacy boxes, Next, fill the
   registration form, choose the password.
2. Store the password for the agent with a hidden prompt (it never appears in a file or a log):
   `docker exec -it jobagent jobagent vault set portal:<host> password` (for example `<host>` = `careers-peraton.icims.com`).
3. Documents: `jobagent run <url> --through tailor`. Then the form:
   `jobagent apply outputs/<company>/<id> <company> --no-submit --browser-mode auto --desktop-allow-origin https://login.icims.com`.
4. A person solves any hCaptcha and answers it: `docker exec jobagent jobagent hitl answer <id> done`. The agent never answers a CAPTCHA request itself.
   In `auto` mode the puzzle can be in the agent's desktop Chrome window (title "Login | General Data Protection Regulation (GDPR)"), which can sit
   behind a larger window: Alt+Tab. If a request says "solve it in noVNC", the puzzle is in the Docker browser at `http://localhost:6080/vnc.html`.
5. The agent signs in with the vault password, fills the profile page, asks the person to type any empty password field, and stops at the review screen.
   It never submits and never types or asks for a password.
6. HITL questions during the profile page repeat on every run (school, employer, location rows). Answer them by **section and label, not by id**: the ids
   shift between runs.

## 5. What the agent now handles on iCIMS (verified live on Garmin unless noted)

| Behaviour | Where |
|---|---|
| A page with no fields and a safe button (Skip & continue, Next, Not now) is moved on; opt-ins and Submit never | `tools/forms.interstitial_button` |
| The dropdown widget is opened, searched with typed key events, and the exact whole entry clicked (never a highlighted fragment: "India" must not pick "British Indian Ocean Territory"); the value is read from `dropdown-text` | `forms._fill_icims_dropdown` |
| A state list that says "No states available" is accepted as it is; an address list that offers only "Enter Manually" takes that entry | same |
| A file input in a zero-size wrapper behind a styled button is an upload field | `forms.read_page` |
| An upload's role comes from its own caption (a Resume field that mentions cover letters is still the resume) | `steps/apply.upload_role` |
| The hidden `<select>` behind a widget, disabled controls and a closed post-Submit consent modal are not "missing required fields" | `filling/discovery.INVENTORY` |
| A bare City/State/Country in a sentence-style education or employer section never takes the current or job-city address | `filling/facts.scope_allows` |
| A page that merely contains account fields is not a sign-in page; password fields are never planned, filled or asked | `filling/planned.credentials_only_page` |
| When only empty password fields are left, the candidate is asked to type them and the run continues by itself (unit and real-Chrome tested; Garmin showed them already held) | `steps/apply._candidate_types_passwords` |
| A page that says "we just emailed you a link" is recognised, the mail read from the agent inbox, the link opened in the same browser (unit and real-Chrome tested; **not yet seen live**, see section 7) | `tools/email_gate` |
| Social and corporate login buttons are refused on iCIMS (they dead-end); the "after two password rejections use Continue with Google" hint was removed; a rule was added to the iCIMS portal notes | `portal_agent.social_login_refusal` |
| Company research falls back to the employer's own posting when the company website answers 403 (Peraton) | `tools/research.posting_source` |
| A project shown under a bare repo name gets its readable title without a model call | `steps/multi_agent._readable_titles` |
| A posting whose only location is a region ("San Francisco Bay Area") no longer fails the whole form | `tools/application_address.NoUsLocation` |

Garmin progression on the profile page: 48, then 59, 61, 63, 64 and finally 66 of 68 verified; state `review`, ready, no blockers, resume uploaded as
`resume.pdf`, cover letter in an additional attachment, Country India and State Maharashtra for the Pune employer, password fields held by the account.

## 6. Defects found on the way (all fixed unless marked)

1. Hidden `<select>` behind the widget counted as an unfilled field; selecting in it did not update the widget and read back empty.
2. Search typed with `fill()` left the full list (the widget filters on keyup).
3. The list panel id was guessed wrong (`_listbox`); the real one is `_icimsDropdown_ctnr`, found from the saved HTML of the real page.
4. Highlight fragments matched instead of whole entries ("India" selected British Indian Ocean Territory); caught only by reading the readback.
5. The resume field received the cover letter (the role came from "Cover letters ... can be added below" in the field's own help text).
6. A disabled hidden select beside a filled text box was a "missing required field".
7. Education City took the job's address city (Olathe) because the section heading is a sentence, not "Education 1".
8. The Google detour: after the puzzle the agent clicked "Continue with Google" on the login page and repeated it until it stopped as "stuck"; fixed in code
   and notes (section 5).
9. Open: the profile page's location, school and employer questions are still asked every run; they should be stored as applicant-file entries.

## 7. Known limits and open items

- **New-employer registration remains unverified.** Peraton and native Hyland are blocked. The corrected Hyland comparison does not establish a cookie/session cause or an automation restriction. Cross-company acceptance remains incomplete.
- **The emailed-link feature has not been exercised live on iCIMS**: Peraton's ordinary-browser path shows no verification mail, and Garmin's mail arrived
  only during the candidate's own manual signup.
- **Hand-off windows hide** behind larger windows; no window-raising tool is installed.
- **Search-engine results for iCIMS jobs go stale quickly**; use the tenant's own search page (section 1).
- The Address field offers only "Enter Manually" for the job-city address the lookup chose; the agent takes that entry and types the street.
- The two optional fields Suffix and Other School are left blank on purpose.

## 8. Options to make registration less tedious (none built yet; owner decides)

1. **A registration sheet**: a ready-to-copy list of the form's values (name, phone, address, education, three employers) from the applicant file, so
   registering is paste, choose a password, Next.
2. **A bookmarklet** run in the person's own ordinary Chrome that fills the registration form from that sheet and leaves the password and any puzzle to
   the person (acts at the person's click, like a password manager; whether iCIMS objects cannot be promised).
3. **Prefer companies whose account already exists** when queuing iCIMS jobs, and say in advance which new ones need the one-time step.

## 9. Constraints that shape every choice here

No submission without the candidate's typed approval; no CAPTCHA solving; no fingerprint spoofing, stealth or webdriver masking; passwords never typed
or asked by the agent and stored only through the vault's hidden prompt; the job-city address logic for current address and location stays as built.

## 10. Key commits

`0c6b7cd` intermediate pages; `448b3ae` region locations; `2a5818b` closed modal; `423887f` to `8b21802` the dropdown widget (panel id, key events, whole
entries); `9e566f7` hidden upload and widget select; `0af137e` upload role; `21198aa` password hand-off; `8701c1b` to `f2dc6f3` emailed-link feature;
`d0fc892` research fallback; `a261309` social-login guard; `893b033` option B reverted.

## 11. Evidence files

- Peraton puzzle: `outputs/peraton/173607/application/challenge-20261124-015739-786310.png`; login-only page and the Google dead end:
  `state/logs/portal-stuck-careers-peraton.icims.com.png`; the Docker browser seen through noVNC: login page, empty email box.
- The candidate's incognito screenshots (login step and registration form): `~/Pictures/Screenshots/Screenshot from 2026-11-23 22-04-34.png` and `... 22-04-53.png`.
- Garmin final state: `outputs/garmin/18475/application/application.json`; dropdown failure records: `state/logs/combobox-failures.jsonl`.
