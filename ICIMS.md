# iCIMS portals: everything learned, what the agent does, what it cannot, and why

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

- The agent can **fill an iCIMS application to the review screen** (Garmin: 66 of 68 fields verified, the other two are optional blanks) **once an account exists**.
- The agent **cannot create an iCIMS account**. A person in an ordinary browser gets the registration form; the agent's remote-controlled Chrome gets a
  puzzle and a login-only page, even when a person does the clicking in the agent's own window with a fresh profile (section 3).
- The agent must not disguise its browser or solve puzzles itself (project rules). So registration stays a **one-time human step per company**.
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

## 3. Why the agent cannot register (the experiments)

Same email, same clicks, different result depending only on the browser:

| Who clicks | Browser | After Next |
|---|---|---|
| Candidate | normal Chrome, incognito window | no puzzle; registration form |
| Candidate | the agent's own desktop Chrome, **fresh empty profile** | puzzle, then the universal login page |
| Candidate | the agent's own desktop Chrome, profile that had visited iCIMS | the login page |
| Agent | Docker Chromium | puzzle, then the login page |

What this rules out: the account state (same email), the agent's clicking (a person clicked), and stale cookies (fresh profile gave the same result).
What differs is the browser itself: the agent's Chrome is started with a remote-debugging port so it can be attached to; an ordinary Chrome is not.
iCIMS appears to treat a remote-controlled browser as automated and withholds registration from it. This is an inference that fits every row; it is
not proven.

**Rule: the agent does not work around this.** No hiding of the debugging port or webdriver flag, no fingerprint masking, no automatic puzzle solving
(project rules, `Spoofing and evasion code`). Registration is therefore done by a person in an ordinary browser window.

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

- **Registration is human** (sections 3 and 4). Peraton's end-to-end validation is blocked until a person registers and stores the password.
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
