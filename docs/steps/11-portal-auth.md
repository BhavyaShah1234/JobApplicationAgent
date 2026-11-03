# Step 11 — Portal sign-in and registration

Before an application is filled (step 12), the agent must be signed in to the employer's job portal with its own
email (`bhavya24091952@gmail.com`). It signs in first; it registers only when the portal has no account for that
email; it never resets a password and never registers twice on the same portal.

**Status (2026-11-02):** Workday (Adobe, Symbotic), Lever and Amazon's own portal pass: account created, email
confirmed from the inbox, the used verification mail moved to Trash with an audit row, and a second run with the
session cleared signs in with the stored password without registering again. Oracle Cloud (JPMorgan) and iCIMS
(General Dynamics) show an hCaptcha puzzle after the email is submitted, so they need the user in noVNC.

## Run it

```bash
jobagent portal-login <posting or career-site URL>          # sign in (register the first time)
jobagent portal-login --fresh <URL>                        # forget the site's session first: a real sign-in
jobagent portal-logout <URL>                               # sign out (confirmed: the site no longer shows the account)
```

## How it works

```mermaid
flowchart TD
    URL[/"Posting or career-site URL"/] --> P{"Workday?"}
    P -- "yes" --> WD["Workday adapter<br/>Sign In > email > Create Account"]
    P -- "no" --> AG["General portal agent<br/>model picks one action per step<br/>over the visible fields (incl. iframes)"]
    WD --> S1
    AG --> S1
    S1["1. Sign in first<br/>vault portal:host password,<br/>else the shared password"] --> OK{"Signed in?<br/>(account email or name on the page<br/>or on its profile page)"}
    OK -- "yes" --> DONE[/"Signed in; vault + portal notes updated"/]
    OK -- "no account" --> REG["2. Register<br/>credentials saved in the vault first;<br/>email, password, legal name, phone"]
    REG --> MAIL["3. Verification mail<br/>sender in the portal's family, sent after the action;<br/>link opened or code typed and submitted;<br/>mail trashed + verification_log row"]
    MAIL --> S1
    S1 -- "CAPTCHA shown" --> H["Handed to the user in noVNC<br/>(answer 'done'); never solved by the agent"]
    H --> S1
```

## Credentials

| What | Where |
|---|---|
| Shared password for every portal (user's choice, so sign-in works the first time) | vault `portal_default` (`state/vault.enc`, encrypted) |
| Each portal: email, password used there, account / verified flags, dates | vault `portal:<host>` |
| A portal that refuses the shared password (too weak / common) gets its own strong password, saved before it's typed | `portal:<host>` (`note` says so); Amazon is one |
| Lever / Greenhouse: no account needed (the posting's form takes applications directly) | `portal:<host>` `no_account: true` |

## Rules

| Rule | Where |
|---|---|
| Sign in before registering; register only when the portal says no account exists; a used verification mail proves an account exists, so never register twice; never reset a password | `portal_auth.ensure_account` |
| Only fields a person would fill are filled: hidden fields (honeypots) are never listed to the model | `portal_agent.SNAPSHOT_JS` |
| The model types placeholders only: `{EMAIL}`, `{PASSWORD}`, `{ALT_PASSWORD}`, `{FIRST_NAME}`, `{LEGAL_FIRST_NAME}`, `{LAST_NAME}`, `{PHONE}`, `{CODE}`; a password only into a password field | `portal_agent.run` |
| "Signed in" only with proof: the account email or first name on the page, or on the site's own profile page (a "Sign out" link isn't proof) | `portal_agent.signed_in_evidence` |
| A visible CAPTCHA (also inside a portal's iframe) goes to the user; a hidden pre-loaded one doesn't; a puzzle that keeps coming back stops the run | `challenge.detect`, `portal_agent.run` |
| The same action three times stops the run with a screenshot (`state/logs/portal-stuck-<host>.png`) | `portal_agent.run` |
| Workday adapter can't find its elements (a redesigned page): the general agent takes over the same portal | `portal_auth.ensure_account` |
| Sign out: the agent opens the account menu and clicks Sign out / Log out; done when the landing page no longer shows the account (the profile page isn't revisited: a remembered browser can be signed straight back in) | `portal_auth.sign_out`, `portal_agent.run(goal="sign_out")` |
| Verification mail: sender domain in the portal's family (e.g. Workday tenants mail from myworkday.com, amazon.jobs from amazon.dev) and sent after the agent's action; used mail trashed with a `verification_log` row; nothing else is touched | `inbox.wait_for_verification`, `db.ATS_SENDER_FAMILIES` |

## Portal survey (`asu_contacts.csv`, top 120 companies)

| Platform | Companies | Account to apply |
|---|---|---|
| Workday | 35 (Bank of America, USAA, AT&T, Intel, Micron, Wells Fargo, Adobe, ...) | yes, one per employer |
| Oracle Cloud / Taleo | 13 (BAE, JPMorgan Chase, Oracle, Goldman Sachs, Chubb, ...) | yes |
| iCIMS | 5 (General Dynamics Mission Systems, Discount Tire, Exponent, ...) | yes |
| Paylocity, SmartRecruiters, Jobvite, Avature (Cisco), ADP, BrassRing, Dayforce, Greenhouse | 1-4 each | varies |
| Company's own portal / not identifiable from search | 54 (Amazon, Meta, Verizon, IBM, Yelp, ...) | varies |

## Learned while building

- Workday: the first run did create the Adobe account, but the code read Workday's screen-reader status ("Search for
  Jobs page is loaded") as an error; an unverified account answers "wrong email or password or your account might
  be locked"; after registering, Workday returns to the sign-in choices. Symbotic's tenant needs no email
  confirmation. "Create Account" is reliably submitted with Enter in the last field.
- Oracle Cloud candidate sites have no sign-up page: "Manage profile" only finds an existing profile ("We couldn't
  find this email"); a profile is created by starting an application on a posting (Apply, email, terms, an
  emailed code), so for Oracle registration happens at the start of step 12's application flow.
- iCIMS and Oracle Cloud show an hCaptcha puzzle after the email is submitted. hCaptcha closes by itself after a
  while and re-appears on every submit, so the user must say when it's solved.
- Oracle pre-loads a full-size hCaptcha frame hidden with opacity 0: only a frame that's really shown counts.
- amazon.jobs: "My career" menu shows "Sign out" even when signed out; the codes come from amazon.dev in six
  one-digit boxes; it refused the shared password as a new password; its sign-in service (on amazon.com) remembers
  the browser, so a later sign-in may skip the password.
- **Inbox date bug:** the verification wait searched `SINCE <UTC date>`, but Gmail compares its own (Pacific)
  calendar date, so every code arriving after about 5 pm Arizona time was invisible. It now searches from the day
  before and checks each message's own timestamp.
- The model often says "click Continue" but re-sends the same fill: a repeated fill becomes Enter; an entered code
  is submitted by code.
- Workday keeps the sign-in in session cookies (`CALYPSO_SESSION`, `PLAY_SESSION`): a sign-in doesn't survive into
  the next `jobagent` command's browser, so the application (step 12) signs in and applies in one browser session.
  Sign-in then sign-out in one session works (Adobe: open the account button, "Sign Out").
- amazon.jobs looks the same signed in or out (its menu always has "Sign out"); the model never declared itself
  signed out, so a click on a Sign out / Log out control followed by a clear landing page counts. Sign-in then
  sign-out works in two steps.

## How robust is it

| Part | Survives a redesigned page? | Proven on | Not handled |
|---|---|---|---|
| JD scrape (step 7) | Mostly: structured data, then visible text by headings, a model labels sections; vision fallback | 5 ATS families + company pages | promo / degree text sometimes labelled as duties; postings behind a sign-in |
| Workday adapter | No by itself (Workday's element ids), but falls back to the general agent | Adobe, Symbotic | |
| General agent | Yes by design (reads the visible page each step), reliability depends on the model; every mistake seen so far is now a code guard | Lever, Amazon | phone / SMS checks, authenticator apps, Google/LinkedIn-only sign-in, CAPTCHAs (user in noVNC) |
| Sign out | Yes (account menu, Sign out / Log out) | Adobe (Workday), Amazon | |

Next for robustness: a test suite of about 10 portals (one per platform in `asu_contacts.csv`) with success rates
and steps per portal, and replaying a portal's last successful route from its notes before asking the model.

