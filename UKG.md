# UKG / UltiPro: what the agent found, what it fixed, and where it stopped

## Update — 2026-11-25: registration attempted by the agent; live validation pending

The owner confirmed that setup/reset was not completed and asked the job agent to perform registration. One normal `apply --register-account --no-submit` reached the second UKG signup step, then crashed at `_target` when navigation destroyed the execution context during the model's decision. No account creation is confirmed.

Candidate code binds actions to the actual controls captured for that turn. A redirect/re-render invalidates an old action instead of applying its numeric index to a replacement page. Navigation races trigger rediscovery; unrelated script errors still surface. Auth challenges use the shared verified handoff, including correct desktop/Docker instructions and an immediate stop for explicit access restrictions.

Real-Chrome and auth-loop regressions pass, but a new live UKG registration and the required second DOWL role/other employer are still pending. The previous advice below to switch networks is not part of this test: wait out a restriction; do not attempt to lift it by clearing cookies or changing networks. No passwords, submissions, or account resets are authorized by a local test pass.

Evidence and exact source/deployment status: `outputs/verification/portal-acceptance-20261125T003317Z/`.

Written 2026-11-24. Status: **on hold** (UKG is rate-limiting this network). Nothing was submitted. The agent's email has a UKG sign-in
account in an unknown, half-created state. No emails, passwords or tokens are written in this file.
Evidence: `outputs/dowl/*/application/` (`page-1.png`, `application.json`), `state/logs/*apply-f8cc737a*.log`,
`state/portals/ultipro.com.json` and `.md`, `outputs/benchmarks/ten-portals-20261122T110915Z/BENCHMARK_LOG.md`.
The test company is DOWL (a civil-engineering firm on UKG/UltiPro): its postings are real and current, and role fit does not matter for a form test.

## 0. In one page

- UKG/UltiPro job boards are an ordinary, **CAPTCHA-free** sign-in (Auth0-style "Welcome, come on in!") followed by a normal application, **except**
  that **account creation has a second step with a Cloudflare Turnstile** that only a person can pass.
- The agent now reads the posting, opens its **Apply now** button (a web component the reader could not see), reaches the sign-in page, uses the
  **Sign up** route correctly, fills **step 1 of the sign-up** (email, password, Continue) and starts **step 2**.
- It never finished. About **eleven runs in 87 minutes** made UKG answer first "the account is locked due to too many incorrect login attempts" and then
  **"too many attempts were detected from this network, try again later"** at step 2 of the sign-up. That was my doing (section 6).
- Several real defects were found and fixed on the way (section 5). Two of them were mine and made things worse: stale portal notes, and a password-attempt cap that
  counted per run instead of across runs.
- Open: the Turnstile hand-off, the **Create account** click, and the application form itself have never run end to end on UKG.

## 1. How UKG's application path works (as observed)

| Step | What was seen |
|---|---|
| Boards | `recruiting2.ultipro.com/<TENANT>/JobBoard/<board-uuid>/OpportunityDetail?opportunityId=<uuid>`; discovery lists them (DOWL: 25 live postings, all US). |
| The posting page | Static text and an **Apply now** control. It is `<ukg-button class="apply-now-button">`, a web component whose visible text is **slotted from outside its shadow tree**: its inner `<button>` has no text of its own. |
| Sign-in | `signin-us.ultipro.com/u/login`: Email address, Password, "Create or reset your password", **Sign in**, "Don't have an account? **Sign up**", **Continue with Google**. No CAPTCHA, no cookie wall. |
| Sign-in errors | **The same message ("Invalid credentials" / "Invalid email or password") for a wrong password and for an email with no account**, so the page cannot be used to tell which. |
| Lockout | After repeated rejections: "The account is locked due to too many incorrect login attempts". Seen on a login with an email that already has an account. |
| Sign-up, step 1 | `signin-us.ultipro.com/u/signup`: "Create your account": Email, Password, **Continue**, "Already have an account? Log in", Continue with Google. Works for any address, including a gmail-style one (tested by hand with nothing submitted). |
| Sign-up, step 2 | "**Almost there!** Please provide your name to set up your account": First name, Last name, Phone number, an **SMS opt-in checkbox**, a **Cloudflare Turnstile** and a disabled **Create account** button. In the agent's Docker browser the Turnstile shows "**Verification failed / Troubleshoot**" and the button stays disabled. |
| Rate limit | On step 2: "too many attempts were detected from this network and advises to try again later". |

## 2. Timeline (UTC; the owner's clock is 7 hours earlier)

| When | What happened |
|---|---|
| 09:33 | Apply now was not listed as a button at all (web component): "no application form and no Apply button". |
| 09:37-10:04 | Five runs of the sign-in agent: the shared password was rejected ("Invalid credentials"); the model went to **Continue with Google** (a dead end), then ping-ponged between **Log in** and **Sign up**. One run ended at **"The account is locked due to too many incorrect login attempts."** |
| 10:33-10:35 | `--register-account` runs: the sign-in-first rule skipped; step 1 filled and Continue clicked; one crashed on a detached frame; one reached step 2 and stopped "stuck": **no human check was raised for the Turnstile**. |
| 10:40 | Docker run: step 2 reached, then **"too many attempts from this network"**. |
| 10:57 | A run that failed because stored notes told the model Google was the way in. |
| 11:00-11:01 | A corrected run: Sign up, Create your account, email, password, Continue, first name; then **"too many attempts"** again. Stopped by the new lockout check. |
| 11:05 onward | Hold. One passive page load at 10:56 showed the login page without any restriction text, so the block is on the sign-up submission, not on viewing the site. |

About eleven apply runs against `signin-us.ultipro.com` in 87 minutes from one IP.

## 3. What worked

- Posting discovery, scrape and the Apply now click (after the web-component fix).
- Recognising the sign-in page, refusing Google there, clicking the real **Sign up** link, and filling **step 1** of the sign-up.
- The Turnstile is now recognised as a human check in a real-Chrome test (detection only; the agent never solves it).
- After a successful sign-in the **application's first page** also loaded and verified four fields (first name, last name, phone, SMS opt-in) once, on a session that had been signed in.

## 4. What did not work

- **Step 2 of the sign-up has not completed**: the Turnstile has not yet been passed by a person, and "Create account" has not yet been pressed by the agent.
- The application form behind the account has therefore not been filled on UKG.
- Whether the half-created account (email and password accepted at step 1) is usable is **unknown**.

## 5. Defects found and fixed (all tested, all deployed)

| Defect | Fix |
|---|---|
| The reader dropped `<ukg-button>` (text slotted from the host) so "Apply now" was never listed | the host's text is the label (`0cf9658`) |
| The model chose **Continue with Google** on the first "Invalid credentials" | Google is refused on UKG and iCIMS; a click that lands on Google from such a portal is sent back (`00a1226`, `6d5bbed`) |
| After a rejected login the model's chosen index drifted onto the wrong element | the code names the page's own Sign up control by index after a rejection (`aeaef73`) |
| The model read its own failed login as "the account already exists" and clicked **Log in** on the sign-up page | on a "Create your account" page after a rejection it is told to fill the form (`95f8fc3`) |
| Sign-in-first made every run try the shared password again | `apply --register-account` goes straight to Sign up and completes the verification (`90a0b89`) |
| A detaching frame during navigation crashed the cookie-notice scan | skipped instead of fatal (`09bbe92`) |
| A verification widget that left the form with no usable button ended as "stuck" | handed to the candidate (noVNC in Docker, the desktop window in `auto`); **Create account** counts as a step forward (`4101d72`) |
| **Stored portal notes** from my failed runs said Google was the way in, and the model followed them | standing `RULE:` notes always reach the model; failed Google visits are hidden where Google is a dead end; the UKG notes were rewritten (`97ef945`) |
| **The password-attempt cap counted per run**, so every rerun started from zero (about ten rejected passwords in total, which locked the account) | rejections are counted across runs; **every failed sign-in run is a strike**; a "too many attempts / locked" page is two; two in six hours stop all attempts at that host (`8547a8d`, `f52b294`) |
| No way to reset a portal's stale browser state | `jobagent portal-forget <url>` and `apply --forget-portal` clear one portal's cookies and site storage (never Google, never automatic, no effect on rate limits) (`5b83a22`) |

## 6. Why it got blocked, and what I take from it

The block is the portal doing its job. The agent sent about eleven sign-in and sign-up attempts in 87 minutes from one IP, with the same email each time, and the
first runs repeated a rejected password. UKG's "too many attempts from this network" is exactly that pattern.
Three of my own mistakes made it worse: the per-run cap, the stale notes that kept sending the model down the Google route, and retrying while the portal was
still saying "try again later". The fixes above remove the first two; the third is a rule: **one attempt per portal per hour, and stop at the first rate-limit or lockout message**.

Clearing cookies (`portal-forget`) cannot remove this block: it is held on UKG's side for the network and the email. It is for stale sessions only.

## 7. What is needed to finish UKG

1. **Wait** for the rate limit to expire (hours, not minutes), or use a different network for one attempt.
2. **One attempt**, run with `apply ... --browser-mode docker --register-account --documents-from <any valid job>` and the owner at **http://localhost:6080/vnc.html**:
   the agent fills step 1, fills step 2 and asks for the Turnstile; the owner passes it and answers `done`; the agent presses **Create account**.
3. If a verification email is sent, the agent reads it from its own inbox and opens the link.
4. The agent then fills the application to the review screen (`--no-submit`); the owner types the password only if a field is left empty, and approval is never given by the agent.
5. If UKG says the account **already exists**, stop: ask the owner to reset the password themselves and store it with
   `jobagent vault set portal:signin-us.ultipro.com password`. The agent never resets it.
6. Then the two further proofs: a **second DOWL role** (same company) and **another company's UKG board** (a different tenant).

## 8. Open items

- The Turnstile hand-off, **Create account**, the verification email and the application form are untested live on UKG.
- A per-portal cooldown record that survives a restart is in place for sign-in strikes (6 h window); a general "one attempt per hour" pacing rule for every protected portal is not yet.
- The same pattern (a second sign-up step guarded by a Cloudflare Turnstile) is likely on other UKG tenants.
