# Desktop application fallback

`jobagent apply` can use a dedicated desktop Chrome for any public HTTPS application URL.
It is no longer restricted to Carvana. The Voyager planner, answer checker and form tools
run inside Docker and connect to that host browser. Chrome uses an isolated profile per
posting, never a personal browser profile.

From the project directory on the Linux desktop, keep the host bridge running:

```bash
PYTHONPATH=src python3 -m jobagent.tools.desktop_browser serve
```

Run the application agent in Docker:

```bash
docker exec -it jobagent jobagent apply outputs/COMPANY/JOB_ID COMPANY \
  --browser-mode auto --model qwen3-30b-a3b
```

`docker` remains the default mode. `auto` starts in Docker and immediately hands off to
desktop when a bot-protection challenge is detected; no terminal `skip` response is
needed for that handoff. `desktop` opens the dedicated host session directly. If the
desktop session shows verification, form actions pause for the human. A successful
resolution requires three readiness checks spaced two seconds apart; resolution is
bounded to two cycles with a 15-minute deadline each. The agent never solves challenges,
changes browser fingerprints or copies verification cookies between browsers.

The bridge uses an owner-only Unix socket under `state/desktop-browser`. Host networking
and the shared state mount in docker-compose.yml let Docker reach its loopback debugging
ports. Chrome chooses a different port for each session; existing review windows do not
occupy a single shared port. Process executable, profile lock and DevTools endpoint are
validated before attachment. Sessions live under `state/desktop-browser/agent-session/<url-hash>`.
The old `desktop_probe serve --authorized-stage serve` command delegates to the updated
broker, but an already-running old bridge must be restarted to load updated code.

Ordinary initial HTTPS redirects are supported. Subsequent pages can use the entry
origins, the JD source origin, application/login links and form actions published by the
loaded top-level page. Embedded frame origins do not grant top-level navigation permission. Same-origin paths, queries and fragments may
change between steps. Extra trusted login origins can be supplied explicitly:

```bash
docker exec -it jobagent jobagent apply outputs/COMPANY/JOB_ID COMPANY \
  --browser-mode auto --desktop-allow-origin https://identity.example.com
```

Public HTTPS embedded forms are discovered without an ATS-vendor frame allowlist. Links
with `target="_blank"` open in the dedicated application tab. Unexpected additional tabs,
unrecognized navigation origins and local/non-HTTPS destinations stop the application.
An application landing page can show Apply or sign-in first; desktop startup now follows
the same start/sign-in workflow as Docker, without reloading a usable posting.

The application stops at review and observes the filled desktop form for 30 seconds.
Chrome stays open for review. Both `apply` and `submit` use the same session abstraction. Filling never sends.
An explicitly authorized desktop submission uses the same dedicated session:

```bash
jobagent submit outputs/COMPANY/JOB_ID COMPANY --browser-mode desktop --allow-desktop-submit
```

The reviewed payload must still be approved at the final prompt. `auto` is a filling mode; a submission cannot silently
switch browsers. Scraping and ATS scoring keep their own browser workflows.

## Opt-in ordinary-Chrome handoff

Some portals reach a usable profile in ordinary Chrome but redirect the managed browser
to an unusable login flow. To preserve the ordinary browser's live session, use the
consented handoff. Restart the host bridge after upgrading its code. From the project
directory on the host, prepare the exact public posting URL stored as the job's apply URL:

```bash
PYTHONPATH=src python3 -m jobagent.tools.desktop_browser handoff 'PUBLIC_POSTING_URL'
```

This opens an isolated, agent-owned Chrome through the same public-only proxy, without
debugging startup flags. An existing managed window for that posting must be closed first;
the command refuses to take over another live profile. It never opens a personal profile.

Reach the application/profile form manually, stopping before Submit. In a new tab open
`chrome://inspect/#remote-debugging`, enable **Allow remote debugging for this browser
instance**, then close that tab and leave only the application. Run the normal agent:

```bash
docker exec -it jobagent jobagent apply outputs/COMPANY/JOB_ID COMPANY \
  --no-submit --browser-mode desktop --desktop-allow-origin https://login.icims.com
```

Chrome will ask you to **Allow** the connection. Every new connection needs Chrome's
permission. The agent attaches to the existing tab without restarting or navigating it,
then uses the ordinary Voyager filling, account, HITL and verification code. The broker
checks the exact process/profile/lock, current network guard, fresh endpoint and owning
loopback listener. iCIMS `/jobs/<id>/...` routes must still belong to the requested job;
other URL shapes initially require the same path/query. The existing target/origin scope
and submission approval rules continue to apply. A missing permission or ambiguous tab
stops attachment rather than falling back to an unrelated browser.

This is an explicit preparation path, not an automatic replacement for managed Chrome.
Live assisted tests filled Hyland's 15-control and SAS's 29-control profile pages without
Submit Profile. Those are early profile pages in four-step flows; a native Review label
there is not proof of reaching the portal's final application review. Later steps and
submissions through this transport remain unvalidated. For UI tests, `--documents-from`
can reuse a valid existing packet; that deliberately prevents submission and avoids
document generation.

## Temporary mailbox

The same host bridge can open Temp-Mail in a dedicated desktop session:

```bash
docker exec -it jobagent jobagent temp-mail --browser-mode desktop
```

`--browser-mode auto` (the mailbox command's default) tries Docker first and switches
to the host desktop only when it detects a security challenge. It waits up to two
minutes for a real mailbox address; adjust this with `--wait-minutes`. Loading text
is never returned as an address. Complete any human verification in the browser.
Desktop Chrome remains open for incoming verification messages after the command
finishes, including after a timeout. Repeating the desktop command reuses that
dedicated session; it does not change or delete the mailbox.

This command reads the mailbox address, without opening messages or registering
accounts elsewhere. A working mailbox does not establish that another website
accepts disposable email addresses or provides another free scan.

## Form filling audit

Field discovery and answer planning are shared across employers. Controls are read from
the current DOM and embedded frames; labels, options, sections and applicant facts are
sent to Voyager, then answers are checked and read back. The code does not contain a
closed list of posting IDs, employer-specific answer maps or fixed field positions.
Applicant wording examples and user-requested referral/address policies are data/policy,
not a list of supported forms.

There are platform-specific widget handlers (for example Workday dates/search pickers,
React selects and grouped buttons). These extend the generic reader. Lazy uploads now
use the clicked control's chooser/new input rather than fixed Carvana captions or IDs.
Native uploads, accessible custom radios and editable text regions are read back too.
Conditional fields and subsequent pages use the same planner.

Author closed shadow roots are recovered through Chrome's DOM protocol and remain closed to the site's ordinary DOM API.
The same reader and an agent selector engine traverse them, including option lists; native text/select/radio/upload controls
are exercised in real Chrome tests. Unreadable frames still produce extraction blockers. Unusual proprietary
widgets, JavaScript authentication popups and unsupported account flows may need human
help or another adapter. The random-address policy still resolves US locations; it runs
only when an actual candidate address/location question is encountered, before those
answers are planned. Forms without address questions no longer require a US address lookup.
Cloudflare can still challenge desktop Chrome; a successful navigation test cannot
guarantee acceptance on every site.
