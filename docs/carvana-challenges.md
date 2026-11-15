# Carvana human verification

Application runs use the Docker browser by default. Challenges pause form actions,
including submission. Resolve them in http://localhost:6080/vnc.html. Answering
`done` requests another check; it cannot declare verification successful. The expected
page must be usable on three checks spaced two seconds apart. Each application run
allows two human-resolution cycles, each with a 15-minute deadline. `skip`, timeout
or further recurrence saves a blocked result and checkpoint under the application's
output directory. Rerun the application command to reopen the application and re-read
the form; inspect checkpoint.json for prior progress. Checkpoints do not restore an
expired portal session or guarantee that the portal retained unsaved fields.

Diagnostics include screenshots, sanitized URLs, challenge types and visible error
identifiers. JSON diagnostics exclude page bodies, credentials and challenge tokens.
Screenshots and application checkpoints are private applicant data.

## Dedicated desktop feasibility probe

This is not an automatic fallback. Obtain the owner's separate authorization before
each stage. Run on the local Linux desktop, from the project directory, not inside
Docker. Commands require an explicit acknowledgment of the authorized stage; this
flag is not a substitute for obtaining that authorization.
If the full jobagent CLI is not installed on the host, the lightweight equivalent is
`PYTHONPATH=src python3 -m jobagent.tools.desktop_probe`; append the same stage and
options shown below. It needs Playwright, without installing the resume/LLM stack.

1. Manual stage: `jobagent desktop-probe manual --authorized-stage manual` launches
   Chrome with a dedicated `state/carvana-desktop/profile` and loopback debugging
   port 9223. The agent does not attach. The human navigates and solves verification.
2. Read stage: after separate authorization, run
   `jobagent desktop-probe read --authorized-stage read`. This inspects the selected
   application tab and observes readiness for 30 seconds. It exposes no general
   browser-control interface. Inspect the resulting screenshots and field IDs.
3. Field stage: only after the read stage succeeds and the owner agrees on a specific
   field/value, run `jobagent desktop-probe field --authorized-stage field --field-id
   AGREED_ID --value AGREED_VALUE`. Only one visible editable text/email/phone input
   in the top-level form can be filled, once. Observe for 30 seconds and review the
   screenshots. Embedded-frame filling is deliberately unsupported by this probe.

The probe checks the dedicated process/profile, selected URL, tabs and frame domains.
Unexpected navigation, popups or verification stop it. It does not close the human's
browser, click buttons, upload files or submit applications. Close that dedicated
Chrome window yourself after testing. No personal browser profile is copied or used.
Do not expose its debugging port through Docker port mappings or network forwarding.

Success is not guaranteed: a manual pass can be followed by a new challenge after
attachment or interaction. These stages gather evidence; broader desktop automation
requires a later decision. No fingerprint patches, automated challenge solving or
cookie transplantation are installed.

## Voyager-driven desktop applications

The production desktop fallback is now shared across employers. See
[Desktop application fallback](desktop-applications.md) for setup, navigation scope,
challenge handling and limitations. The manual feasibility probes above remain
Carvana-specific and are separate from the general application workflow.

Start the current host bridge with:

```bash
PYTHONPATH=src python3 -m jobagent.tools.desktop_browser serve
```

Then run `jobagent apply ... --browser-mode auto` in Docker. A detected Docker challenge
hands off immediately; a challenge in desktop Chrome still requires human resolution.

Carvana's cached job data can outlive its Greenhouse posting. If the cached questions
list is empty and the exact public Greenhouse job returns `404 Job not found`, the
agent records the application as unavailable and exits with code 3 instead of asking
for verification. Network failures and other API errors do not establish closure.
