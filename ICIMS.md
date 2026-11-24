# iCIMS portals: what the agent can and cannot do, and why

Written 2026-11-24 after validating Garmin (passes to review) and trying Peraton (account creation blocked).
Evidence: `outputs/benchmarks/ten-portals-20261122T110915Z/BENCHMARK_LOG.md`, `state/portals/icims.md`, the screenshots named below.

## 1. Finding: the agent cannot register an iCIMS account, a person can

**Same email, same first steps, different result.**

| | Candidate in an incognito window | The agent |
|---|---|---|
| Steps | Apply, email, both privacy boxes, Next | the same |
| After Next | no puzzle; straight to the registration form (`.../jobs/<id>/<slug>/candidate?from=login`: "Create a login", "Enter your information") | an **hCaptcha puzzle**, then the **universal login page** (`login.icims.com`, `loginOnly=1`, `universalLogin=1`) |
| What that page allows | create the account | sign in to an existing account only; no sign-up |

The account state is identical in both cases (Peraton has never seen the email), so the account is not the cause. iCIMS treats the
remote-controlled browser differently from a person's browser: the person gets no puzzle and the registration form, the automated
browser gets a puzzle and a login-only page. The most likely reason is iCIMS's anti-abuse scoring (hCaptcha) withholding sign-up from an
automated Chrome. That is an inference, not something the logs prove.

**The agent must not work around it.** Disguising the browser or solving the puzzle itself is outside the project rules (no fingerprint
masking, no CAPTCHA solving). A person creates the account; the agent signs in afterwards and does the rest.

## 2. A second, separate defect (fixed): the Google detour

After the puzzle the agent landed on the login page, then clicked **Continue with Google**. iCIMS answers "Unable to log in with the social
account. Please make sure your iCIMS account is connected to one": social buttons only sign in an account already linked to that
provider. The agent repeated the click three times and stopped as "stuck".

Causes: a code rule ("after two password rejections, if the site offers Continue with Google use it"), and old failure notes in
`state/portals/icims.*` that told the same story.

Fix (commit `a261309`): `portal_agent.social_login_refusal` refuses a click on any social or corporate login button on an iCIMS page
(Google, Microsoft, Apple, Facebook, LinkedIn, Indeed, Corporate Login, single sign-on); the hint now says to use a create-account, sign-up or
forgot-password route and never a social login; an authoritative rule was added to the iCIMS portal notes.

## 3. How to bring a new iCIMS company onto the agent (the working procedure)

1. **A person creates the account once**, in a normal browser, with the agent's email: open the posting, Apply, email and privacy boxes,
   Next, then the registration form ("Create a login"). The person chooses the password.
2. Store the password for the agent without it appearing anywhere: `docker exec -it jobagent jobagent vault set portal:<host> password`
   (hidden prompt; `<host>` is for example `careers-peraton.icims.com`).
3. Run the posting: `jobagent run <url> --through tailor` for the documents, then
   `jobagent apply outputs/<company>/<id> <company> --no-submit --browser-mode auto --desktop-allow-origin https://login.icims.com`.
4. When iCIMS shows an **hCaptcha**, a person solves it and answers `docker exec jobagent jobagent hitl answer <id> done`. In `auto` mode the
   puzzle can appear in the agent's own desktop Chrome window ("Login | General Data Protection Regulation (GDPR)"), which is easy to miss
   behind a larger window; Alt+Tab to it. The agent never answers a CAPTCHA request itself.
5. The agent signs in, fills the candidate profile page, and stops at the review screen. It never submits, and never types a password.

## 4. What the agent now handles on iCIMS (all verified live on Garmin: 66 of 68 fields, the other two are optional blanks)

| Behaviour | Where |
|---|---|
| A page with no fields and a safe button ("Skip & continue", Next, Not now) is moved on; opt-ins and Submit never | `tools/forms.interstitial_button` |
| iCIMS dropdowns: the visible control is an `<a role="combobox" class="dropdown-select">`; its value is in `span.dropdown-text`; the list is ajax-loaded with a search box, results in `<id>_dropdown-results`, panel `<id>_icimsDropdown_ctnr` | `forms._fill_icims_dropdown` |
| Search is typed with key events (the widget filters on keyup, not on `fill()`) | same |
| Entries are matched whole, never highlighted fragments ("India" must not pick "British Indian Ocean Territory") | same |
| A state list that says "No states available" (a country with no states) is accepted as it is | same |
| An address list that offers only "Enter Manually" takes that entry | same |
| A file input in a zero-size wrapper behind a styled "My Computer" button is an upload field | `forms.read_page` |
| An upload's role comes from its own caption (a Resume field that mentions cover letters is still the resume) | `steps/apply.upload_role` |
| The hidden `<select>` behind a widget, and disabled controls, are not "missing required fields" | `filling/discovery.INVENTORY` |
| A closed (post-Submit) consent modal is not a missing field | same |
| A bare City/State/Country in a sentence-style education or employer section never takes the current or job-city address | `filling/facts.scope_allows` |
| A page that merely contains account fields is not a sign-in page; password fields are never planned, filled or asked | `filling/planned.credentials_only_page` |
| When only empty password fields are left, the candidate is asked to type them and the run goes on by itself | `steps/apply._candidate_types_passwords` |
| A page that says "we just emailed you a link" is recognised; the portal's mail is read from the agent inbox and its link opened in the same browser | `tools/email_gate` |
| Company research falls back to the employer's own posting when the company website answers 403 | `tools/research.posting_source` |

## 5. Known limits and open items

- **Registration stays human.** The email-link feature (section 4) has unit and real-Chrome tests but has not yet been exercised on a live
  iCIMS portal, because Peraton's registration path (section 1) shows no verification email at all.
- **Hand-off windows hide.** The desktop Chrome for a CAPTCHA can sit behind other windows; there is no window-raising tool installed.
- **The agent calls the Identity-verification stop "stuck"** on Amazon-style portals; it is actually waiting for the candidate.
- **Option B (not built):** let the person drive the first steps (Apply, email, Next, puzzle) in the agent's own desktop window and hand over
  at the "Candidate profile" page. This would remove the manual account creation, with the person, not the agent, passing iCIMS's checks.
- **Peraton end-to-end validation** is blocked until a person creates the account (section 3, step 1).
