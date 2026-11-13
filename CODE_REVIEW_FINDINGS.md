# Code review findings and correction checklist

Review date: **2026-11-10**

This is a handoff of the review requested by the project owner. It describes the implementation reviewed on that date, including uncommitted changes. **Creating this document did not change application code.** Findings must be revalidated against the current implementation before fixing them.

Plan reviewed: `/home/bhavya-shah/.claude/plans/pasted-content-id-6de4-jobapplicationag-dynamic-wren.md`.

## Instructions for working through this review

1. Work on the finding the user selects. Preserve unrelated changes and avoid broad refactors unless needed for that finding.
2. Read the referenced functions in the current working tree. Function names are the primary references because line numbers move.
3. Reproduce the problem with an isolated regression test before changing behavior where practical. Use fixtures, fake IMAP servers and temporary databases; do not use real application submissions or destructive mailbox operations as test reproductions.
4. Implement the correction while preserving current user requirements. If a proposed correction conflicts with a later user decision, follow that decision and document the resolution.
5. Run the targeted tests and relevant integration checks. Record the commands, results, changed files and remaining limitations in the finding's resolution notes.
6. Check off an item only when its acceptance checks pass. If it has already been fixed, record the evidence. Do not mark it resolved merely because its happy-path test passes.

The index checkboxes below are the completion tracker. Resolution notes under each finding should explain the actual result.

## Requirements that must not be reverted to match the older plan

- The current random-address/location selection feature is intentional. Do not replace it with the older plan's prohibition or silently substitute the candidate's personal address.
- The employee-referral answering policy and company contact lookup in `asu_contacts.csv` are intentional. Do not reintroduce an obsolete consent gate from the plan as part of these fixes.
- The requested ATS-site automation and five-site numeric average are intentional. SkillsFirst supplies writing feedback rather than a numeric sixth score.
- The generalized desktop fallback is intentional and must remain employer-independent. Tightening its task boundaries must not restore a Carvana-only allowlist.
- Preserve the current user-approved GPA and claims policy. In particular, do not restore the removed 3.57 GPA reference.
- Final job-application submission requires human approval. Challenges requiring human verification remain human-operated.

## Review evidence and limits

- The review used source inspection and isolated in-memory reproductions of selected functions. No live application, ATS scan, inbox sync or mailbox deletion was performed during the review.
- Reproductions confirmed the startup Apply-click permission, empty records after planner failure, unsafe cleanup fallback, sender display-name acceptance, unowned queue recovery, newsletter misclassification, inconsistent review ATS average, incorrect historical inclusion, responder-only ghost cutoff and acceptance of an unrelated existing desktop tab.
- All **67 Python source files** compared matched between the workspace and running container at review time. This is a source-alignment check, not proof that a recreated container image contains those changes.
- No fresh full-project pytest run was performed as part of this read-only review. Existing test reports do not establish coverage for the counterexamples below.
- Some items are confirmed defects; others are design or integration gaps. The evidence and acceptance checks distinguish those cases.

The original review and dated resolution notes below are historical. **The current audit at the end supersedes earlier “not done” notes.**

## Finding index

- [x] [F01](#f01) — Critical: startup can click a final Apply button before approval. **Verified fixed after follow-up.**
- [x] [F02](#f02) — Critical: mailbox cleanup can delete after failed COPY and expunge unrelated messages. **Verified fixed.**
- [x] [F03](#f03) — High: planner failure can be reported as successful review. **Verified fixed after follow-ups.**
- [ ] [F04](#f04) — High: submission attempt state machine is fixed, but retained synthetic upload widgets omit attachment identity from approval hashing. **Reopened for Claude Code.**
- [x] [F05](#f05) — High: document-generation failures do not reliably stop the pipeline. **Verified fixed after follow-ups.**
- [x] [F06](#f06) — High: review ATS scores and learning snapshots can describe the wrong score set/version. **Verified fixed after follow-ups.**
- [x] [F07](#f07) — High: desktop scope can trust the wrong existing tab and unrelated frame origins. **Verified fixed after follow-ups.**
- [x] [F08](#f08) — High: shared-inbox privacy and ownership guarantees are not enforced. **Verified fixed after follow-ups.**
- [x] [F09](#f09) — High: company-looking display names bypass sender-domain validation. **Verified fixed after follow-up.**
- [x] [F10](#f10) — High: unapproved or ambiguous thread associations can become trusted. **Verified fixed in the 2026-11-12 re-review; see current audit below.**
- [x] [F11](#f11) — High: ingestion cursors and message identities can cause permanent message loss. **Verified fixed in the 2026-11-12 re-review; see current audit below.**
- [x] [F12](#f12) — High: newsletter and quoted-history content can become false outcome events. **Verified fixed in the 2026-11-12 re-review; see current audit below.**
- [x] [F13](#f13) — High: queue recovery and claiming are unsafe with multiple workers. **Verified fixed in the 2026-11-12 re-review; see current audit below.**
- [x] [F14](#f14) — Medium: desktop filling and production submission use different browser paths. **Verified fixed in the 2026-11-12 re-review; see current audit below.**
- [x] [F15](#f15) — Medium: durable end-to-end workflow resume is incomplete. **Verified fixed in the 2026-11-12 re-review; see current audit below.**
- [x] [F16](#f16) — Medium: wizard progress detection relies too heavily on headings. **Verified fixed in the 2026-11-12 re-review; see current audit below.**
- [ ] [F17](#f17) — Medium: extraction errors and unsupported controls can disappear silently. **Closed-root reading implemented; reopened for approval coverage and legacy attachment inventory gaps (2026-11-13).**
- [ ] [F18](#f18) — Medium: output schemas contradict document-length requirements. **Partially fixed; still open. The original-index exemption accepts changed, over-budget text.**
- [ ] [F19](#f19) — Medium: LLM retry, deadline and fallback policies are inconsistent. **Partially fixed; still open. An in-flight call can exceed the total deadline and return a successful late result.**
- [x] [F20](#f20) — Medium: ATS input payloads and repeated-score provenance need correction. **Verified fixed in the 2026-11-12 re-review; see current audit below.**
- [x] [F21](#f21) — Medium: the ghosting cutoff is not censoring-aware. **Verified fixed in the 2026-11-12 re-review; see current audit below.**
- [x] [F22](#f22) — Medium: rate definitions and historical evaluations need correction. **Verified fixed in the 2026-11-12 re-review; see current audit below.**
- [ ] [F23](#f23) — Medium: learning ignores ghost weights and misses its original false-positive gate. **Partially fixed; still open. Per-analysis calibration is improved; repeated evolving-data monitoring remains uncalibrated.**
- [x] [F24](#f24) — Medium: outcome review, dry runs and lesson provenance are incomplete. **Verified fixed in the 2026-11-12 re-review; see current audit below.**
- [x] [F25](#f25) — Medium: persistent identities and writes need stronger transactional boundaries. **Verified fixed in the 2026-11-12 re-review; see current audit below.**
- [x] [F26](#f26) — Medium: discovery silently relaxes validation and does not enforce its advertised budgets. **Verified fixed in the 2026-11-12 re-review; see current audit below.**

- [ ] [F27](#f27) — Medium: portal login rejection counting blocks registration and cycles between account forms. **New live-run finding; Claude Code handoff.**
- [ ] [F28](#f28) — High: multiple lazy upload widgets can receive the wrong document, produce duplicate records and misattribute readback. **New live-run finding; Claude Code handoff.**
- [ ] [F29](#f29) — High: fuzzy school matching can select a different institution and retain applicant provenance. **New live Pantex finding; Claude Code handoff.**
- [ ] [F30](#f30) — High: application planning drops raw JD facts and the checker excludes the JD, producing wrong instruction-dependent answers. **New owner-detected travel failure; Claude Code handoff.**

## F01

**Critical — Startup can click a final Apply button before human approval.**

**Where:** [apply.py](src/jobagent/steps/apply.py), `_start`, `START`; [forms.py](src/jobagent/tools/forms.py), `click_button`.

**Issue and reason:** Forms with fewer than three non-file controls fall through to startup-button handling. That handling passes `allow_submit=True`, and startup patterns include a bare `Apply`. A short or prefilled application whose final button is Apply can therefore be clicked outside the approval hook. An isolated two-field reproduction confirmed that the submission-enabled helper is called. Website validation may prevent a particular submission, but the approval boundary itself is not enforced.

**Correction:** Separate application-opening navigation from final submission. Startup should only possess navigation capability. Final application submission should require a typed approval capability available exclusively to the reviewed submission path. Preserve legitimate account sign-in/registration flows without confusing them with final job submission.

**Acceptance checks:** One-field, two-field and prefilled forms with a final Apply button cannot submit through `apply` or startup. A navigation-only Apply link can still open an application. Only the explicit approved application-submission path can send it.

**Resolution notes (2026-11-11, assistant; checkbox left for the owner to tick):**

- **Reproduced first.** `tests/unit/test_start_navigation.py` drives the real `_start` in headless Chrome on HTML pages whose form handlers record whether Apply was clicked. Against the pre-fix code, four cases were submitted by startup (`sent: true`): a one-field form, a two-field prefilled form, an `input type=submit value=Apply` form and a select+textarea form whose `<button>` has the default submit type. The same file ran against the saved pre-fix `forms.py` / `apply.py` / `submit.py`: 6 failed (the four above plus the two capability checks below), the four navigation cases passed.
- **Cause.** `forms.SUBMIT` deliberately matches a bare `Apply` (the final button of many forms), while `_start` passed `allow_submit=True` and its own `START` list also matches a bare `Apply`. Whenever a page had fewer than three non-file fields, startup clicked it.
- **Change.**
  - `tools/forms.py`: the `allow_submit` flag is removed; `click_button` never clicks a `SUBMIT`-matching text. New `click_open_application` (navigation only) decides by the element, not the text: it refuses any submit-type button (`<button>` default type included, or `input type=submit`) that belongs to a form with at least one visible field to fill (`SENDS_FORM_JS`); links, buttons outside forms and submit buttons of forms with nothing to fill (hidden fields only) still open the application. A control it cannot classify is not clicked. New typed capability `SubmitApproval` (constructible only with the module-private issuer, otherwise `TypeError`) is required by `click_final_submit` and `click_submit_input`.
  - `steps/apply.py` `_start` calls `click_open_application`. A refused final button leaves the form to the normal path (planner, fill, review), where the existing `at_review` hook is the only way to send it.
  - `steps/submit.py`: `click_submit(page, view, approval)` now requires the `SubmitApproval`, which `make_at_review` creates only after the candidate answered `approve` to that application's review prompt.
- **Checks run (all in the container, against a copy of the whole current working tree including your uncommitted desktop/ATS files, not a clean HEAD copy).**
  - `tests/unit/test_start_navigation.py`: 10 passed (4 refusals; link, button outside a form, hidden-fields-only form and "Continue application" still open; capability objects; a source scan showing only `steps/submit.py` calls the sending functions and `allow_submit` no longer exists).
  - Related existing tests (`test_forms`, `test_submit`, `test_generic_forms`, `test_generic_desktop`, `test_desktop_application`): 88 passed together with the above.
  - Full unit suite on the whole working tree (including the uncommitted desktop, ATS and address files): 535 passed after this fix; 546 passed in a second clean run after F02 was also applied (same tree, 11 more tests).
  - Read-only look at live pages (no clicks): GoDaddy's final `Apply` (`BUTTON:submit`, 35 fields to fill) is classed as sending and would never be clicked at startup; Ashby's `Apply for this Job` (`BUTTON:submit` in a form with nothing to fill) and Lever's `APPLY` link are classed as navigation.
- **Remaining limits.** (1) A careers-site filter form (a search box plus a submit button labelled "Apply") would be treated as a final button and left to the planner instead of opened; none was seen. (2) `SubmitApproval` is a convention enforced by types and a source-scan test, not a security boundary inside one Python process. (3) The payload hash and attempt state that F04 asks for are not part of this change: the approval object carries only the job folder and a request stamp. (4) Nothing here exercised a real submission.

**Earlier independent verification (Codex; superseded by the final verification below): STILL OPEN — the original native-form cases were fixed, but startup still submitted other short forms without approval.**

- Inspected the current `SENDS_FORM_JS`, `click_open_application`, `_start`, and reviewed submission path. The classifier only refuses a native submit control with visible descendant fields inside its form. A `type="button"` handler can submit through JavaScript, and HTML fields associated through `form="..."` need not be descendants of the form. Neither case is protected by this classifier. The approval capability is never consulted on these startup clicks.
- Reproduced **both cases through the real `_start` in headless Chrome** using the following isolated HTML fixtures. Each returned one application field and recorded `window.sent === true`, with no approval prompt or capability. The fixtures prevented normal submission, all network requests were blocked, and the page stayed at `about:blank`; no real application was sent.

```html
<!-- Case 1: JavaScript-driven final Apply button. -->
<h1>Job application</h1>
<form onsubmit="window.sent = true; return false;">
  <input name="email" value="me@example.org">
  <button type="button" onclick="this.form.requestSubmit(); this.remove();">Apply</button>
</form>
```

```html
<!-- Case 2: native submission with an externally associated field. -->
<h1>Job application</h1>
<input name="email" form="application" value="me@example.org">
<form id="application"
      onsubmit="window.sent = true; this.querySelector('button').remove(); return false;">
  <button type="submit">Apply</button>
</form>
```

- **Required before closure:** add both counterexamples to `tests/unit/test_start_navigation.py`. Include externally associated controls when classifying forms (native controls can be enumerated through `form.elements`). Do not assume that links, `type="button"` controls, or buttons outside forms are navigation solely from their element type: JavaScript can submit from them. When application fields are already present and an Apply action cannot be established as navigation, leave it to filling/review. Preserve the existing genuine application-opening cases and the approval requirement for final submission.
- The capability/source-scan tests alone did not cover these bypasses: the sending action occurred in the page's event handler after an ordinary navigation-helper click. Passing those regressions did not satisfy F01's acceptance checks. **F01 remained unchecked at that revision.**
- **Regression result:** the shared verification command recorded under F02 passed all 121 selected tests, including the 10 existing startup-navigation tests. The two additional counterexamples above are independent reproductions beyond that existing suite.

**Follow-up to the independent verification (2026-11-11, assistant; F01 left unchecked for the owner):**

- **Agreed and reproduced.** Both Codex fixtures were added verbatim to `tests/unit/test_start_navigation.py` and failed against the first fix (`sent: true`). Three more bypasses of the same kind were added and also failed: a JavaScript `<a href="#">` link or a `type=button` outside the form while a form field is on the page; a form whose only visible control is a search box (the field reader skips those, so the page showed "no fields"); and a real link to another page with a click handler (the test records a request made by the handler, because the page navigation wipes any in-page flag).
- **Cause.** The first fix decided by element type (a native submit control with visible descendant fields). A `type=button` control, a link or a button outside the form can submit through JavaScript, and `form="id"` fields are not descendants of their form.
- **Change** (`tools/forms.py`, `steps/apply.py`):
  - `SENDS_FORM_JS` now takes the element's form as `e.form || closest('form')` and counts its visible fillable controls through `form.elements`, which includes fields attached from outside. Any control of a form with at least one such control is that form's own control: never acted on at startup, whatever its type.
  - `click_open_application`: a real link to another page (http/https, different page than the current one) is followed by loading its address with `page.goto`, so no click handler runs. Any other control is clicked only while the page has no application fields at all (nothing for it to send). With fields on the page and navigation not established, it is left to the planner, the filler and the reviewed submission path. A control it cannot classify is not acted on.
  - When this leaves startup with no form to work on, the stop message says that an Apply control belonging to a form with fields is never clicked at startup.
  - The approval object and its source-scan test are unchanged: only `steps/submit.py` can call the sending functions.
- **Checks run.** `tests/unit/test_start_navigation.py`: 15 passed (the 10 earlier cases plus the 5 above). Against the first fix, the 5 new tests failed (4 bypasses plus the link-handler case). The two Codex fixtures are included as written. Live, read-only-by-construction: `_start` on a Palantir Lever posting followed the `APPLY` link to `/apply` (24 fields) and on a Mercor Ashby posting clicked `Apply for this Job` (a submit button in a form with nothing to fill) to reach `/application` (10 fields); no field was filled and nothing was sent. Full unit suite on the whole working tree (uncommitted desktop, ATS and address files included): 551 passed (546 before this follow-up plus the 5 new tests).
- **Remaining limits.** (1) A page with a visible search box plus an Apply button inside the same form is now left alone and ends with the stop message instead of opening; that is the safe direction, and none was seen on the sites checked. (2) A real link is followed by address even if its click handler would have done something else (for example set a cookie first); a site that needs that handler would stop with the "no application form" message rather than submit anything. (3) `requestSubmit()` from the page's own scripts without any user action cannot be prevented by this code; startup never clicks a control that would run such a handler while fields are present. (4) Fields hidden in closed shadow roots or unreadable frames are F17's subject: with no readable fields a form there is still protected only by the form check above.

**Final independent verification (Codex): VERIFIED FIXED — F01 checked off after the follow-up.**

- Re-read the updated `SENDS_FORM_JS`, `click_open_application`, `_start`, and the approval-gated submission functions. Form controls are now enumerated through `form.elements`, including externally associated fields. Controls belonging to a form with visible fields are refused regardless of button type. Other ambiguous controls are left alone while application fields are present. Links to another page are followed with `page.goto`, avoiding their click handlers.
- **Both previously reported bypasses now pass** in `tests/unit/test_start_navigation.py`: the JavaScript `requestSubmit()` button and the native form with an externally associated input remain unsubmitted at startup. The expanded suite also covers links/buttons outside forms, a search-only form, and navigation without executing a link's click handler. Genuine application-opening cases still pass.
- Tested an isolated copy of the current workspace inside Docker, including the current uncommitted source/test files and required example applicant fixture. Used `PYTHONPATH=src`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and `TMPDIR=/dev/shm`:

```bash
pytest --basetemp=/dev/shm/f01-verification-1xvZx2 -q \
  tests/unit/test_start_navigation.py \
  tests/unit/test_inbox_cleanup.py \
  tests/unit/test_inbox.py \
  tests/unit/test_submit.py \
  tests/unit/test_forms.py \
  tests/unit/test_generic_forms.py \
  tests/unit/test_generic_desktop.py \
  tests/unit/test_desktop_application.py
```

  Result: **126 passed**, including all **15 startup-navigation tests**; 13 dependency deprecation warnings, no skips or failures. This was a targeted regression run, not a fresh full-project suite.
- **Additional independent approval checks: four passed.** Exercised the real `_start`, `make_at_review`, `click_submit`, and browser click helpers on a local HTML fixture. Startup did not submit. `skip`, a blank response, and `HitlTimeout` each left the form unsubmitted and returned `skipped`; only `approve` caused its submission handler to run. Calling `click_submit` without an approval object raised `TypeError` in every case. Database recording, screenshots, receipt detection, and confirmation-mail polling were mocked; network requests were blocked. No real application submission or mailbox access occurred.
- Confirmed all **118 Python source/unit-test files** in the workspace still matched the tested snapshot before updating this checklist. Application source and test files were not changed during verification, and the live container's `/app/src` tree was not replaced.
- **Scope of closure:** F01's reported startup-click approval bypasses and specified acceptance cases are resolved. This does not close F04's submission-attempt/payload-binding finding or F17's extraction limitations, and does not claim that arbitrary page-initiated scripts can be prevented from submitting themselves. The earlier open verdict above is retained as history and is superseded by this result.

## F02

**Critical — Mailbox cleanup can delete after failed COPY and affect unrelated messages.**

**Where:** [inbox.py](src/jobagent/tools/inbox.py), `Inbox.consume_and_trash`.

**Issue and reason:** When MOVE fails, the code performs COPY, marks the source `\Deleted`, and calls unrestricted EXPUNGE without checking COPY success. An isolated reproduction made both MOVE and COPY fail; STORE and EXPUNGE still ran. EXPUNGE can also affect other messages already marked deleted in the selected mailbox. The ownership check on the selected UID does not protect those other messages.

**Correction:** Require confirmed COPY success before deleting the source. Use UID-scoped expunge where supported; otherwise leave the message untouched and report the limitation. Distinguish attempted cleanup from completed cleanup in the audit log, recording success only after it occurs.

**Acceptance checks:** Failed COPY leaves the original untouched. An unrelated deleted message survives cleanup of an agent verification message. Successful MOVE and a safe supported fallback remain idempotent. Test entirely against fake/isolated mailboxes.

**Resolution notes (2026-11-11, assistant; checkbox left for the owner to tick):**

- **Reproduced first**, entirely against a fake IMAP session (`tests/unit/test_inbox_cleanup.py`, a strict `Mailbox` that models per-message flags, capabilities, per-command failures, and what a plain `EXPUNGE` removes). On the pre-fix `inbox.py`: (1) MOVE and COPY both refused, yet STORE `\Deleted` and EXPUNGE still ran; (2) a second, unrelated message already flagged `\Deleted` was expunged together with the agent's; (3) a server with neither MOVE nor UIDPLUS still got COPY + STORE + unrestricted EXPUNGE. No real mailbox was touched.
- **Change** (`tools/inbox.py`, `db.py`):
  - `consume_and_trash` keeps its ownership and protected-mail refusals (they run first and write nothing), then calls `_move_to_trash`, which returns a `Cleanup(status, detail)` (`completed` / `failed` / `unsupported`, `.ok`).
  - `_move_to_trash`: MOVE; on a refusal or an `imaplib` error, the fallback runs only if the server advertises `UIDPLUS`, and then checks each step: COPY must answer OK before anything is flagged, STORE must answer OK before anything is expunged, and the expunge is `UID EXPUNGE <uid>`, never the plain `EXPUNGE`. Without `UIDPLUS` nothing beyond the refused MOVE is sent and the message stays in place (`unsupported`, with the reason). After a good COPY but a failed STORE a copy is in Trash and the original stays (reported in `detail`).
  - Audit: the `verification_log` row is now written after the attempt, with `status` (`completed` only when the mail reached Trash, else `failed`) and `detail` (new columns by migration; existing rows read as `completed`). Before, the row was written before any IMAP command and was indistinguishable from a success.
  - `sweep_stale_verification` reports a subject as trashed only when the cleanup completed. Other callers (portal auth, portal agent, GitHub, the Gmail self-test) ignore the return value as before.
- **Checks run** (container, copy of the full working tree): `test_inbox_cleanup.py` 12 passed and `test_inbox.py` 20 passed together (32); the three counterexample tests fail on the saved pre-fix `inbox.py` and pass now. Full unit suite on the whole working tree with F01 and F02 both applied: 546 passed.
- **Remaining limits.** (1) Verified against the fake session only; Gmail's real MOVE/UIDPLUS behaviour is unchanged from the earlier live self-test, but this fallback path was not run against the live account because that would need a deliberately broken MOVE. (2) A server with neither MOVE nor UIDPLUS now keeps used verification mail instead of trashing it; the sweep and the audit log say so. (3) Ownership of the message being cleaned (F08) is a separate finding and is not changed here.

**Independent verification (Codex): VERIFIED FIXED — F02 checked off.**

- Reviewed `Inbox.consume_and_trash`, `_move_to_trash`, `sweep_stale_verification`, and the verification-audit migration/writer. COPY failure stops before STORE; STORE failure stops before expunge; the fallback requires UIDPLUS and uses `imap.uid("EXPUNGE", uid)` rather than unrestricted `imap.expunge()`. Audit completion is recorded after successful cleanup, and failed/unsupported cleanup is not counted as a successful sweep.
- Tested an isolated copy of the current workspace source, selected unit tests, and `data/applicant.example.yaml` inside the existing Docker container. The live `/app/src` tree was not replaced. Temporary test databases were stored in `/dev/shm`; no real mailbox or application was used.
- **Command and result:** from that isolated copy, with `PYTHONPATH=src`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and `TMPDIR=/dev/shm`:

```bash
pytest --basetemp=/dev/shm/critical-review-pytest-QibVtM-final -q \
  tests/unit/test_start_navigation.py \
  tests/unit/test_inbox_cleanup.py \
  tests/unit/test_inbox.py \
  tests/unit/test_submit.py \
  tests/unit/test_forms.py \
  tests/unit/test_generic_forms.py \
  tests/unit/test_generic_desktop.py \
  tests/unit/test_desktop_application.py
```

  Result: **121 passed**, 13 dependency deprecation warnings, no skips or failures. This is a targeted regression run, not a full-project test run. The host environment could not collect all tests because dependencies were missing; the container supplied the project's installed dependencies.
- **Additional independent checks: eight passed**, executed in memory using the strict fake mailbox from `test_inbox_cleanup.py`: COPY, STORE, and UID EXPUNGE each returning refusal or raising `imaplib.IMAP4.error` (six scenarios); repeated cleanup after successful MOVE and after successful UIDPLUS fallback (two scenarios). Each failure reported failure without removing the unrelated already-deleted message; COPY failure never flagged the original, STORE failure never expunged, and repeated cleanup did not change mailbox contents. No unrestricted EXPUNGE occurred.
- **Scope of closure:** the reported failed-COPY deletion and unrelated-message expunge defects are fixed. This does not close F08's separate ownership/privacy finding, establish live-provider behavior, or establish that every historical audit row represents a confirmed cleanup. No application source or test file was changed during this verification; only this review document was updated.

## F03

**High — Planner failure can produce an apparently successful application report.**

**Current status: verified fixed.** The final independent verification at the end of this finding supersedes the earlier open verdicts retained below as review history.

**Where:** [apply.py](src/jobagent/steps/apply.py), `fill_page`, `_run`; [main.py](src/jobagent/main.py), `apply_cmd`.

**Issue and reason:** `fill_page` logs a planning exception and breaks without creating records for unanswered controls. Later code can enter review because a Submit button exists. An isolated reproduction with one visible required field and a failing planner returned normally with zero recorded fields. This can create a misleading `0/0 verified; 0 flags` report. The CLI also returns success for blocked or stuck results, misleading shell chains and queue execution.

**Correction:** Give every discovered control an explicit outcome, including unplanned, unsupported and failed. A fatal planner failure must produce a blocked result and an appropriate nonzero CLI status. Review readiness must account for required fields, uploads and extraction errors, rather than just button presence. Keep a deliberate distinction between reaching the review UI and being ready to submit.

**Acceptance checks:** Planner failure with visible required fields cannot yield a clean report or successful workflow status. Optional unanswered fields are represented honestly. Queue and shell callers receive the correct failure state.

**Resolution notes (2026-11-11, assistant; checkbox left for the owner to tick):**

- **Reproduced first**, through the real `_run` in headless Chrome on a one-required-field page with the model-backed stages replaced (`tests/unit/test_planner_failure.py`). With a failing planner on the pre-fix code: the visible required field had no record (`0 == 1`), and `_run` returned `state: "review"` and called the approval hook. A real-portal run then found a second path: the address-preparation model call that runs *before* `plan` raised straight out of `fill_page`, so there was no record, no `application.json`, no report, only a traceback.
- **Changes** (`steps/apply.py`, `steps/submit.py`, `main.py`, `cli_discovery.py`):
  - Every field found now gets a record with an explicit `outcome`: `verified`, `mismatch`, `failed`, `gone`, `unplanned` (and `unplanned (optional)`), `unanswered (required)`, `left empty (optional)`. When the planner raises, a record per control is created with outcome `unplanned` before anything else happens (`_blank_entry`, `outcome_of`).
  - A planner failure raises `PlannerFailed` after the records are saved; `_run` ends `state: "blocked"`, `blocked_by: "planner"`, with the reason, and never offers a review. A model failure in the address-preparation step is treated the same way. A bot check is `blocked_by: "challenge"`, a page that keeps refusing Next `blocked_by: "page"`. Any other unexpected exception in `run()` now writes a `failed` result (`application.json` / `.md`) before propagating.
  - Reaching the review screen is separate from being ready to send: the result has `blockers` (required fields that are unplanned, left empty, failed or mismatched, plus page errors) and `ready_to_submit`. The report and review packet state it ("Ready to submit: NOT ready" and each blocker). The "fields verified" count now counts only fields read back correctly (an optional field nobody answered was counted before), with the other outcomes listed.
  - The review prompt names the blockers. When the candidate answers `approve`, `live_blockers` re-reads the page: required fields still empty or page errors mean nothing is clicked (`state: "not_ready"`); filling them in noVNC first still works.
  - Exit codes: `apply`/`submit` return 0 ready, 5 on the review screen but not ready, 4 blocked, 1 failed or stuck (3 stays "posting unavailable"); a submit that failed exits 1, one that was approved but refused for missing answers exits 5. The queue maps them to `filled`, `blocked`, `needs_attention`, `failed`, and leaves status 3 alone (before, exit 3 was overwritten as `blocked` and any exit 0 meant `filled`).
- **Checks run** (container, copy of the whole working tree): `test_planner_failure.py` 21 passed and `test_apply_exit_codes.py` 20 passed; against the saved pre-fix files 29 of those 41 fail (the rest are cases that must behave the same). They cover: planner failure with a visible required field cannot give a clean report, review or success status; optional unanswered fields are reported as such and do not block; a skipped required field is a blocker even with a Submit button; the hook still runs when not ready so the candidate can fix things, and approve re-checks the live page; a dead address-preparation model; an unexpected error leaves a failed result; a bot check stays `blocked`; exit codes of `apply` and `submit` for every state through the real commands; the queue's status for each code. Real portal: `jobagent apply` on a live Mercor (Ashby) form with a nonexistent model exited 4 with `blocked (planner)`, 14 field records all `unplanned`, blockers listed in `application.md`, no review prompt, nothing filled or sent. Full unit suite on the whole working tree: 592 passed. One existing test needed its expectation changed because the behaviour it asserted is exactly what this finding forbids: `tests/unit/test_desktop_application.py::test_apply_auto_falls_back_only_for_a_challenge_block` asserted exit code 0 for a run that ended `blocked`; it now expects 4 in that case (and 0 when the desktop retry ends on a ready review screen), and its fake `run` results now carry `ready_to_submit` like every real result.
- **Remaining limits.** (1) A page error banner is only read at the end of the form and at approval. (2) A required field the page marks required in a way the field reader does not detect (F17) would not be a blocker. (3) `live_blockers` treats an empty required field as blocking and has no override: a field wrongly detected as required can only be fixed by filling it. (4) Resume after a blocked planner failure still re-opens the application from the start (F15). (5) The shell exit codes are for `apply`/`submit`; `tailor` and other commands are F05.



**Earlier independent verification (2026-11-11, Codex; see the follow-up and latest verification below): PARTIALLY FIXED — required-upload readiness was not enforced at approval.**

- **Verified improvements:** planner/address-preparation failures now preserve field records and block the run rather than offering a clean review. Optional unanswered fields have explicit outcomes. Readiness is distinguished from reaching the review screen, and the CLI/queue map blocked, failed, and not-ready results to distinct statuses.
- **Regression run:** tested an isolated copy of the current workspace in Docker, with the required example fixtures, `PYTHONPATH=src`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and `TMPDIR=/dev/shm`:

```bash
pytest --basetemp=/dev/shm/f03-verification-ix29zQ -q \
  tests/unit/test_planner_failure.py \
  tests/unit/test_apply_exit_codes.py \
  tests/unit/test_start_navigation.py \
  tests/unit/test_submit.py \
  tests/unit/test_forms.py \
  tests/unit/test_generic_forms.py \
  tests/unit/test_generic_desktop.py \
  tests/unit/test_desktop_application.py \
  tests/unit/test_jobqueue.py \
  tests/unit/test_cli_discovery.py
```

  Result: **144 passed**, 13 dependency deprecation warnings, no skips or failures. This was a targeted regression run, not a fresh full-project suite. All **120 Python source/unit-test files** still matched the tested snapshot before this document was updated.
- **Remaining defect:** `steps/submit.py::live_blockers` explicitly excludes `kind == "file"`. Its docstring says uploads are judged by the fill record, but `make_at_review` calls it with only the live view and never checks the recorded required-upload blockers before creating `SubmitApproval` and calling `click_submit`. Consequently, approving an application with an unresolved required upload still invokes the sending path. Initial `ready_to_submit=False` does not prevent it. `main.py::submit_cmd` returns success if the resulting submission state is `submitted` or `confirmed`, even if that readiness flag remains false.
- **Two independent browser reproductions:** used the real `_run`, field extraction/readback, `make_at_review`, and final-click helpers on an isolated form with a prefilled required email, an empty required resume upload, and a JavaScript-driven final button. The planner and other model-backed stages were stubbed; the candidate response was `approve`.
  1. No resume file in the job folder: the upload record was `outcome: mismatch`, with a `no resume file in the job folder` flag. The result correctly listed the required-upload blocker and `ready_to_submit: false`, but the final button's handler still ran with **zero attached files**.
  2. A resume path existed, but `forms.fill_field` was made to raise `FillError("fixture upload rejected")`: the upload record was `outcome: failed`, the blocker named the upload failure, and readiness was false. Approval again ran the final button's handler with **zero attached files**. This separately exercises upload failure rather than a missing generated document.

```html
<h1>Job application</h1>
<form>
  <label>Email <input type="email" name="email" value="me@example.org" required></label>
  <label>Resume <input type="file" name="resume" required></label>
  <button type="button" onclick="window.sent = true">Submit application</button>
</form>
```

- The second fixture's resume path was a test placeholder; no PDF parsing or document-generation validation was being tested. The final button intentionally uses a JavaScript handler so browser-native form validation cannot conceal the missing agent-side gate. Network requests were blocked; database writes, receipt detection, and confirmation-mail polling were mocked. The mocked receipt made the returned submission state `submitted`; that is **not** evidence of remote receipt. The decisive observation is that the real click helpers ran the handler while no resume was attached. No real application or mailbox was used.
- **Required correction:** at approval, reconcile required-upload records with current attachment evidence before creating the approval capability or clicking Submit. Missing, failed, mismatched, or otherwise unverified required uploads must return `not_ready` and CLI exit 5. Support native file inputs and upload widgets that replace the input with an attachment filename/link. Do not merely trust the earlier record after the candidate edits the page, and do not reject a demonstrably successful manual correction solely because the old record failed.
- **Required regression checks:** add missing-required-upload and failed-required-upload cases to the approval-hook tests, asserting no submit click, `not_ready`, and exit 5. Include a successful manual upload after the initial failure that clears the blocker, plus an optional empty upload that remains nonblocking. Retain the passing planner-failure, shell/queue-status, and F01 approval-boundary checks.
- Only this review document was changed during verification; application source and test files were not edited, and the live container's `/app/src` tree was not replaced. The remaining issue is within F03's stated requirement that readiness account for required uploads; F04's attempt tracking, F05's document-generation gate, and F17's extraction limits remain separate findings.

**Follow-up to the independent verification (2026-11-11, assistant; F03 left unchecked for the owner and Codex):**

- **Agreed and reproduced.** `tests/unit/test_approval_uploads.py` runs the real `make_at_review` on real pages with the real final-click helpers and a JavaScript final button, including Codex's fixture verbatim. On the pre-fix `forms.py`/`submit.py`, 7 of its first 10 tests fail: a missing resume in the job folder (`outcome: mismatch`) and a failed upload (`outcome: failed`) were both approved through to the click (`state: submitted`, handler ran with no file attached).
- **Cause.** `live_blockers` skipped `kind == "file"` and trusted the earlier fill record for uploads, and the hook never consulted the recorded blockers at approval.
- **Change** (`tools/forms.py`, `steps/submit.py`):
  - `forms.upload_blockers(page, view, records, known)` judges required uploads from the page as it is now, never from the stored outcome. A live native input is judged by the files it holds (the field reader reports `input.files` names). An upload whose input was replaced, or that never reports files, counts as attached only if a known file name (the job folder's PDFs, the recorded upload path) is on the page or a file name appears in the block around its caption (`attachment_evidence`, which does not climb into the form, page or sections, so an unrelated `privacy-policy.pdf` link is not evidence).
  - `submit.live_blockers(page, view, records, job_dir)` now includes those upload blockers. Missing, failed, mismatched or removed required uploads return `not_ready` before the approval object is created or any click, and `apply`/`submit` exit 5. A successful manual upload clears the blocker even though the earlier record failed; a file removed after a `verified` record raises it; an optional empty upload never blocks.
  - On a pass the result is updated to describe what is actually sent (`ready_to_submit: true`, `blockers: []`, `readiness_rechecked_at_approval`); on a refusal it records the live blockers. This addresses the note that `submit_cmd` could return success while the flag stayed false.
  - Found while checking real widgets: the field reader only recognised `*` and `(required)`, so a required upload marked with Lever's `✱` (U+2731) was reported optional, and the upload branch judged required-ness from the container caption only. It now accepts `✱`, `∗`, `＊` and `⁎` and judges from the full label. This is a small part of F17's extraction limits, fixed here because readiness depends on it.
- **Checks run** (container, copy of the whole working tree): `test_approval_uploads.py` 16 passed (missing resume file, failed upload, manual upload after a failure clears it, a verified record not trusted after the file is removed, optional empty upload, an uploader that replaces its input with the file name, an empty uploader input, an attachment chip with no input, an unrelated filename not counting, exit code 5, five star lookalikes, no mark stays optional); the earlier F03 and F01 tests still pass. Live, no submission: on a real Mercor (Ashby) form the required resume is flagged before a fixture PDF is attached through the page's file chooser and cleared after; on a real Palantir (Lever) form the `✱` resume upload is now detected as required, flagged, then cleared. Full unit suite on the whole working tree: 608 passed (592 before this follow-up plus the 16 upload tests).
- **Remaining limits.** (1) Attachment evidence for widgets that replace their input depends on the file name appearing in the block around the caption; an uploader that shows no name at all would be reported as "no attachment shown" and need the candidate to confirm in noVNC, which cannot currently be overridden. (2) An upload the reader does not find (closed shadow roots, unreadable frames) is F17. (3) The check uses file-name evidence, not the content or validity of the uploaded PDF.

**Earlier independent verification (2026-11-11, Codex; see the second follow-up and latest verification below): STILL OPEN — attachment evidence was not reliably associated with its upload field.**

- The previous missing/failed-upload regressions now pass, as do manual correction, removed attachment, optional upload, and replacement-widget cases. The hook now checks uploads before creating the approval capability and updates readiness after the live check.
- **Remaining defect in `tools/forms.py`:** `attachment_evidence` accepts any known PDF filename anywhere in any frame's body as evidence for the requested upload. `submit.live_blockers` supplies all PDFs in the job folder as those known names, and `upload_blockers` accepts this fallback even for an explicitly empty native file input. Therefore a displayed `resume.pdf` can clear the blocker for an empty required cover-letter input. Separately, `NEAR_JS` can climb into a shared `div` containing adjacent upload widgets and accept a neighboring upload's filename. Removing the global filename match alone does not fix that second path.
- **Two independent browser reproductions passed through the defective gate.** Used the real field reader, `live_blockers`, `make_at_review`, and final-click helpers on a form with a prefilled required email and two correctly detected required native uploads: resume and cover letter. Attached only the resume, left the cover-letter input empty, supplied a failed cover-letter record, and answered `approve`.
  1. Separate upload containers, with `resume.pdf` in the job folder and shown next to the resume input: `live_blockers` returned `[]`; the hook changed readiness from false to true and ran the final submit handler, with **zero cover-letter files attached**. The global known-name match attributed the resume to the cover letter.
  2. Both upload containers inside a shared `div`, with `candidate_resume.pdf` attached from outside the job folder and **no known job-folder PDFs**: `attachment_evidence` for the empty cover-letter field returned `Resume candidate_resume.pdf`. Again there were no live blockers and the submit handler ran with **zero cover-letter files attached**. The ancestor search crossed into the neighboring resume widget.

```html
<h1>Job application</h1>
<form>
  <label>Email <input type="email" value="me@example.org" required></label>
  <div class="documents">
    <div>
      <label>Resume <input type="file" name="resume" required></label>
      <span>candidate_resume.pdf</span>
    </div>
    <div>
      <label>Cover letter <input type="file" name="cover_letter" required></label>
    </div>
  </div>
  <button type="button" onclick="window.sent = true">Submit application</button>
</form>
```

- The fixture PDF bytes were test placeholders, not document-validity tests. Network requests were blocked. Receipt detection and mail polling were mocked, and SQLite state was confined to temporary directories. The returned `submitted` state used the mocked receipt; the decisive evidence is that the actual browser handler ran while the cover-letter input had no files. No real application or mailbox was used.
- **Required correction:** associate attachment evidence with the specific upload control/widget, including its frame. A native input's nonempty `files` is evidence for that input. For custom/replacement widgets, require attachment evidence inside that widget's own verified boundary; do not accept filenames globally or climb across neighboring upload controls/captions. A known filename can help validate evidence already associated with a field, but cannot establish that association. If association cannot be established, retain the required-upload blocker.
- **Required regression checks before closure:** add both two-upload fixtures above. With only the resume attached, approval must return `not_ready`, leave readiness false, report the cover-letter blocker, avoid creating a submission capability/clicking Submit, and produce CLI exit 5. Attaching the cover letter manually must then clear only its blocker and permit approved submission. Include adjacent widgets under a common `div` and attachments in different frames, and retain all existing single-upload, optional-upload, planner-failure, queue/CLI, and F01 tests.
- **Regression command:** ran the earlier F03 verification selection plus `test_approval_uploads.py`, using an isolated copy of the current working tree in Docker with the required example fixtures. Environment: `PYTHONPATH=src`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, `TMPDIR=/dev/shm`.

```bash
pytest --basetemp=/dev/shm/f03-upload-verification-utLorH -q \
  tests/unit/test_approval_uploads.py \
  tests/unit/test_planner_failure.py \
  tests/unit/test_apply_exit_codes.py \
  tests/unit/test_start_navigation.py \
  tests/unit/test_submit.py \
  tests/unit/test_forms.py \
  tests/unit/test_generic_forms.py \
  tests/unit/test_generic_desktop.py \
  tests/unit/test_desktop_application.py \
  tests/unit/test_jobqueue.py \
  tests/unit/test_cli_discovery.py
```

  Result: **160 passed**, 13 dependency deprecation warnings, no skips or failures. The two additional failing-behavior reproductions above were executed independently in memory and are not covered by those passing tests. This was not a fresh full-project suite. All **121 Python source/unit-test files** still matched the tested snapshot before this document was updated.
- **F03 remains unchecked.** Its planner-error handling is fixed, but required-upload readiness still admits a demonstrated incomplete form. This is an evidence-association defect with correctly extracted native inputs, rather than an F17 unreadable-field limitation. Only this review document was edited; application source/tests and the live container's `/app/src` were not replaced or changed by this verification.

**Second follow-up to the independent verification (2026-11-11, assistant; F03 left unchecked for the owner and Codex):**

- **Agreed and reproduced.** Codex's two fixtures (separate containers with `resume.pdf` in the job folder; a shared `div.documents` with `candidate_resume.pdf` and no job-folder PDFs) are in `tests/unit/test_approval_uploads.py`. On the previous `forms.py` both submitted with the cover-letter input empty.
- **Cause.** `attachment_evidence` accepted any known job-folder file name anywhere in any frame, and `NEAR_JS` climbed into a shared `div` and accepted a neighbouring upload's name.
- **Change** (`tools/forms.py`): evidence is tied to one upload control and its frame. A native input's non-empty `files` is evidence. Otherwise a file name counts only inside that control's own boundary: it climbs from the control (max 4 levels) and stops at FORM/BODY/MAIN/HTML/SECTION/ARTICLE or before any parent holding another file input, another field, or another upload's caption. If the input is gone, the recorded caption must match exactly one place on the page (across frames); none or several means no evidence. Known names only confirm evidence already inside the boundary. Blockers now name the specific upload.
- **Checks.** `test_approval_uploads.py`: 23 passed (both Codex fixtures give `not_ready`, readiness stays false, no submit click, exit 5, only the cover-letter blocker; attaching the cover letter manually clears only its blocker and permits approval; adjacent widgets under one `div`; attachment in a different frame; ambiguous caption; two blockers; all earlier single-upload, optional, replaced-input, chip, privacy-link and star cases). Full unit suite on the whole working tree: 615 passed (608 before plus the 7 new upload tests).
- **Limits.** An uploader that shows no file name inside its own boundary is reported as "no attachment shown" and cannot be overridden from the prompt. A live Lever/Ashby widget recheck of this version has not been re-run (the earlier script was lost); the previous version passed it. Unreadable frames and closed shadow roots remain F17.

**Earlier independent verification (2026-11-11, Codex; see the third follow-up and latest verification below): STILL OPEN — distinct uploads could overwrite one another before they were checked.**

- **Verified improvements:** the previous global-filename and neighboring-widget bypasses now have passing regression coverage. Evidence lookup is limited to a control's own frame/widget boundary, and ambiguous replacement-widget captions retain their blockers.
- **Remaining defect:** `tools/forms.py::upload_blockers` builds `ups` using only `g.label.rsplit(" / ", 1)[-1]`, and merges records using the same key. Distinct controls commonly share a native name such as `file`, including across separate frames. Their full labels, frame indexes, and extracted field IDs can differ, yet this dictionary silently keeps only the last control. If that control is attached or optional, an earlier empty required upload is never checked. The boundary checker cannot protect a control that has already been discarded from the lookup.
- **Two independent browser reproductions:** used the real field reader, `live_blockers`, `make_at_review`, and final-click helpers with a required prefilled email and the candidate response `approve`:
  1. Main-frame empty required cover letter: `(frame=0, id=1, label="Cover letter / Cover letter / file")`. An iframe contained an attached required resume: `(frame=1, id=2, label="Resume / Resume / file")`. Both inputs used `name="file"`. Both were correctly extracted as required. `live_blockers` returned `[]`, approval changed readiness from false to true, and the actual final-button handler ran with **zero cover-letter files attached**.
  2. On one page, an empty required cover letter and an empty **optional** additional-document input both used `name="file"`. The reader correctly reported distinct IDs and required flags, but the optional input replaced the required one in `ups`. Again no blocker remained and the final-button handler ran with **zero cover-letter files attached**, even though no document was attached anywhere.

```html
<!-- Same-frame reproduction: both inputs are valid, separately labeled controls. -->
<h1>Job application</h1>
<form>
  <label>Email <input type="email" value="me@example.org" required></label>
  <div><label>Cover letter <input type="file" name="file" required></label></div>
  <div><label>Additional document <input type="file" name="file"></label></div>
  <button type="button" onclick="window.sent = true">Submit application</button>
</form>
```

- The cross-frame fixture served its iframe locally through Playwright routing; every other network request was blocked. Receipt detection and mail polling were mocked, SQLite state was temporary, and no real application/mailbox was used. The observed `submitted` state relies on the mocked receipt; the decisive evidence is that the actual click handler ran while the required cover-letter input remained empty.
- **Required correction:** preserve every distinct upload using an identity that includes its frame and an unambiguous control/widget identifier. Do not deduplicate or merge live controls by the last label segment. Reconcile old records to live/replacement widgets without discarding either control or weakening a required flag when a match is ambiguous. Also audit `steps/apply.py::_key`, which uses the same last-segment shortcut for file controls within a frame, so the correction preserves one outcome record per discovered upload throughout F03's pipeline. Native IDs/names and truncated captions alone are not globally unique identities.
- **Required regressions:** cover both fixtures above, reversed DOM order, repeated native names/IDs in separate frames, and a required/optional pair sharing a name. Assert that every discovered upload retains its own outcome, the required cover-letter blocker survives, readiness remains false, approval creates no submission capability/click, and the CLI returns 5. Attaching the missing required document must then clear only its blocker. Retain the existing boundary, manual-correction, optional-upload, planner-failure, queue/CLI, and F01 tests.
- **Regression result:** ran the same eleven test files in the preceding verification command, using `--basetemp=/dev/shm/f03-boundary-verification-7HJITR`, an isolated current-workspace copy in Docker, and the same test environment/fixtures. **167 passed**, including all **23 approval-upload tests**; 13 dependency deprecation warnings, no skips or failures. The two additional duplicate-identity reproductions were run independently in memory and are not covered by that passing suite. This was not a fresh full-project suite.
- Confirmed all **121 Python source/unit-test files** still matched the tested snapshot before updating this document. **F03 remains unchecked.** These are correctly extracted native fields, so the demonstrated loss happens in readiness bookkeeping rather than F17's unreadable-control extraction. Only this review document was edited; application source/tests and the live container's `/app/src` were not changed by this verification.

**Third follow-up to the independent verification (2026-11-11, assistant; F03 left unchecked for the owner and Codex):**

- **Agreed and reproduced.** `upload_blockers` keyed uploads by the last label segment (usually the native name, e.g. `file`) and `apply._key` did the same, so same-named controls (same frame, or different frames) overwrote each other and an attached or optional one could hide an empty required one. Codex's required/optional pair is in `tests/unit/test_approval_uploads.py`; on the previous code 4 of the 6 new tests fail (the reversed-order and "attach the required one" cases pass there only because the required control happens to come last).
- **Change.** `forms.Field.slot` numbers same-named uploads within a frame in DOM order (set in `read_page`). `apply._key` for uploads is `(frame, kind, name, slot)`; every outcome record (`_blank_entry` and the normal entry) carries `frame` and `slot`. `upload_blockers` keeps every live upload as its own entry with its own required flag and evidence; a fill record joins a live control only when frame, slot and name all match (a record without a position joins only when exactly one live upload has that name), otherwise it stays an upload of its own and is never dropped or weakened.
- **Checks.** `test_approval_uploads.py` 29 passed (required/optional pair sharing a name, reversed DOM order, attaching the optional one does not satisfy the required one, attaching the required one clears only it, two frames with the same native name, one outcome key per upload); all earlier boundary, single-upload, optional, planner-failure, queue/CLI and F01 tests still pass. Full unit suite on the whole working tree: 621 passed.
- **Limits.** Slot is DOM order: if a page inserts or removes a same-named upload between the fill and the approval, a record can fail to join its control and then stands as a record-only upload judged by its caption, which keeps the blocker unless evidence is shown. Live Lever/Ashby widgets were not re-run for this change.

**Earlier independent verification (2026-11-11, Codex; superseded by the fourth follow-up and final verification below): STILL OPEN — slot reassignment could merge a required upload into a different optional control.**

- **Verified improvements:** the earlier same-frame duplicate-name and cross-frame cases now pass. Every live upload is kept in the list, and fill records carry `frame` and `slot`. The earlier planner-error, optional-field, widget-boundary, and CLI/queue checks also pass.
- **Remaining defect:** `read_page` recalculates `slot` from the current DOM order on every read. `upload_blockers` joins an old record to a live control using only `(frame, slot, last label segment)`. When a preceding same-named input disappears, a different control takes its slot. The merge neither checks the complete caption/widget identity nor retains the old required flag; it only copies `filled` and discards the record. Thus a required upload can be merged into an optional upload. Contrary to the third follow-up's limitation note, a shifted record does not necessarily fail to join and remain blocked: it can join the wrong control.
- **Independent reproduction:** used `tests/unit/test_approval_uploads.py`'s `SAME_NAME` fixture, adding `*` to the cover-letter caption. Initially the reader correctly found:

| Upload | Frame | Slot | Required | Attached file |
|---|---|---|---|---|
| Cover letter | 0 | 0 | Yes | None |
| Additional document | 0 | 1 | No | None |

  Created real-position records from that view. During the simulated review wait, removed only the first file input, leaving the visible `Cover letter *` caption and no attachment evidence. This models a widget changing between filling and approval. On the next read the optional additional-document input became `(frame=0, slot=0, name=file)`. The earlier required cover-letter record incorrectly joined it. The real approval hook then returned the mocked-receipt `submitted` result, set `ready_to_submit=True`, and ran the actual final-button handler with **zero attached files anywhere on the page**.

```python
# Mutation during the fixture's review wait; keep the required caption visible.
env.before_answer = lambda: env.page.locator("input[type=file]").first.evaluate(
    "e => e.remove()"
)
```

- The reproduction used the actual field reader, `live_blockers`, `make_at_review`, and browser final-click helpers. Network requests were blocked; receipt detection and confirmation polling were mocked; SQLite/artifacts were temporary. No real application or mailbox was used. The returned submission state is not evidence of remote receipt; the failed acceptance check is the observed browser click despite the retained required-upload record having no attachment.
- **Required correction:** do not treat DOM order as proof that an old record belongs to a live widget. Validate the control/caption association when reconciling records, and preserve an unmatched or ambiguous required record as its own upload for evidence checking. A required record must not silently lose its required status by joining a different optional control. A replacement widget with a confirmed attachment can clear that record through its own associated evidence; a different widget occupying its former slot cannot. Stable widget identity or explicit ambiguity handling is needed wherever slots can move.
- **Required regression checks:** add the disappearing-input case above, plus replacement without a filename, reordered same-named widgets, and insertion/removal before the original control. Assert `not_ready`, readiness false, the cover-letter blocker retained, no approval capability/final click, and CLI exit 5. Include the positive case where the required input is replaced by its correctly associated attachment filename, and retain all earlier duplicate-name, cross-frame, optional, manual-correction, planner-failure, and queue/CLI tests.
- **Regression result:** ran the same eleven test files listed in the earlier verification command on an isolated current-workspace copy in Docker, with `--basetemp=/dev/shm/f03-identity-verification-ceotBt` and the same test environment/fixtures. **173 passed**, including all **29 approval-upload tests**; 13 dependency deprecation warnings, no skips or failures. The additional slot-shift reproduction was executed independently in memory and is not covered by that passing suite. This was not a fresh full-project suite.
- Confirmed all **121 Python source/unit-test files** still matched the tested snapshot before updating this document. **F03 remains unchecked.** This is readiness-record reconciliation with correctly extracted controls; F04's payload/attempt tracking and F17's unreadable-field extraction remain separate. Only this review document was edited; application source/tests and the live container's `/app/src` were not changed by this verification.

**Fourth follow-up to the independent verification (2026-11-11, assistant; F03 left unchecked for the owner and Codex):**

- **Agreed and reproduced.** `slot` is recomputed from DOM order on every read, and `upload_blockers` joined a record to a live control by `(frame, slot, name)`; with the first same-named input removed, the optional control took slot 0 and absorbed the required cover-letter record (my third-follow-up limitation note was wrong: a shifted record can join the wrong control). On the previous code 3 of the 6 new tests fail (removed first input; replacement widget with no filename; removal before the original).
- **Change** (`tools/forms.py`): a position is never proof of identity. A record joins a live upload only when frame and the whole label (captions plus native name, marks and spacing ignored) match and the match is unambiguous (one record and one control, or equally many of each with identical labels, ordered by slot). Otherwise the record stays an upload of its own with its own required flag and is judged by the evidence at its one unambiguous caption, so a different control in its old position can neither absorb nor clear it. On a join the stricter flag wins. Also fixed: a file name beside a caption no longer stops the caption being found (the replaced-by-its-own-filename case).
- **Checks.** `test_approval_uploads.py` 35 passed (adds Codex's disappearing-input case with real-position records, replacement without a filename keeps the blocker, replacement by its own filename clears it, reordered controls, an insertion before the original, a removal before an attached required control that stays itself); every earlier test still passes. Full unit suite on the whole working tree: 627 passed.
- **Limits.** Fail-closed by design: when a record cannot be tied to a control with certainty and no attachment is shown at its caption, the blocker stays and the candidate must attach or confirm it in noVNC (which cannot be overridden from the prompt). Live Lever/Ashby widgets were not re-run for this change.

**Final independent verification (2026-11-11, Codex): VERIFIED FIXED — F03 checked off.**

- Reviewed the current planner-failure handling, per-field outcomes, readiness calculation, upload evidence/reconciliation, approval hook, CLI exit codes, and queue status mapping. Upload records now match by frame and normalized full label rather than slot alone. Ambiguous/unmatched records remain separate for evidence checking, and a matched record preserves the stricter required flag.
- **The last reported bypass is fixed:** when the required cover-letter input disappears and the optional same-named input takes its slot, the required record remains blocked at its own caption. The candidate's `approve` does not create a submission capability or click the final button. Earlier duplicate-name, cross-frame, global-filename, neighboring-widget, missing/failed-upload, and planner-error cases also pass their regressions.
- **Regression command:** ran an isolated copy of the complete current source/unit-test tree in Docker, including the required example fixtures, with `PYTHONPATH=src`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and `TMPDIR=/dev/shm`:

```bash
pytest --basetemp=/dev/shm/f03-final-verification-An2mPm -q \
  tests/unit/test_approval_uploads.py \
  tests/unit/test_planner_failure.py \
  tests/unit/test_apply_exit_codes.py \
  tests/unit/test_start_navigation.py \
  tests/unit/test_submit.py \
  tests/unit/test_forms.py \
  tests/unit/test_generic_forms.py \
  tests/unit/test_generic_desktop.py \
  tests/unit/test_desktop_application.py \
  tests/unit/test_jobqueue.py \
  tests/unit/test_cli_discovery.py
```

  Result: **179 passed**, including all **35 approval-upload tests**; 13 dependency deprecation warnings, no skips or failures. This verifies the selected failure, readiness, approval, shell/queue, and related form/navigation paths; it is not a fresh full-project suite.
- **Four additional independent browser checks passed**, using the real field reader, approval hook, and final-click helpers:
  - Required input removed during review: `not_ready`, readiness false, retained cover-letter blocker, submit handler did not run.
  - Required input replaced by an empty widget during review: same blocked result.
  - Required document manually attached during review: blocker cleared, readiness true, approved fixture submission handler ran.
  - Native `required` attribute removed after filling while the earlier required record remained: the stricter recorded flag survived reconciliation, so approval stayed `not_ready` with no click.
- Those independent checks blocked network requests and mocked receipt detection/mail polling; SQLite/artifacts were temporary. The successful fixture case demonstrates approval/click behavior, not remote receipt or PDF validity. No real application or mailbox was used. All **121 Python source/unit-test files** matched the tested snapshot during verification; application source/tests and the live container's `/app/src` were not edited or replaced. Only this review document was updated.
- **Scope of closure:** F03's planner-error reporting, explicit field outcomes, required-field/upload readiness, and CLI/queue failure-state requirements are verified for the acceptance cases above. F04's submission-attempt/payload tracking, F05's document validity gate, F15's durable resume, and F17's unreadable/undetected controls remain separate open findings. Attachment filename evidence does not establish document-content validity, and no fresh live Lever/Ashby check was performed for this final revision.

## F04

**Codex verification (2026-11-12–13): REOPENED — handoff to Claude Code.** Attempt reservations, duplicate guards and uncertain-outcome reconciliation remain fixed. A separate approval-binding counterexample remains for retained synthetic upload widgets. When a Choose File button is still exposed as `kind="file"` after its native input disappears, `_attachments` records only `files=None, unverified="bytes unreadable"`. `widget_uploads` excludes it because it still has a live synthetic field, so its displayed filename never enters the frozen payload. In isolated real Chrome, a one-widget form with a retained Choose File button changed its held File and visible name from `a.pdf` to `b.pdf`; `freeze_payload` returned the SAME SHA-256. Its frozen attachment was only `{frame:0,slot:0,label:"Resume/CV*",files:None,unverified:"bytes unreadable"}`. This matches the retained synthetic widgets seen in the Carvana run.

**Correction for Claude Code:** For every unreadable/synthetic upload, include its uniquely scoped current visible filename/status in the approval payload even while the synthetic control remains live. Refuse ambiguous identity or require a fresh explicit review under the documented opaque-upload policy. Do not substitute a same-named disk file's hash for unreadable browser bytes; keep learning snapshots unverifiable when appropriate. Preserve native-input byte hashing and the attempt state machine. Add a retained-synthetic-widget regression where replacing the widget file changes the hash/invalidate capability, alongside the existing removed-input and same-name/native-byte tests. The controlled Carvana test adds a separate task-specific widget-name check immediately before the click; that runtime check is not a production F04 fix. No F04 source implementation changes made by Codex.

**High — Submission retries and uncertain outcomes lack an attempt state machine.**

**Current status: verified fixed.** The final independent verification below supersedes the earlier open verdicts retained as review history.

**Where:** [submit.py](src/jobagent/steps/submit.py), `make_at_review`, `record`; [main.py](src/jobagent/main.py), `submit_cmd`; [availability.py](src/jobagent/tools/availability.py), `known`.

**Issue and reason:** Entering review rewrites application state to filled without protecting an existing submitted or confirmed attempt. Availability checks do not reject an ordinary applied job record. After a Submit click, the code can mark the application submitted without proving receipt. A crash between the remote action and local database update creates an ambiguous result that a retry could submit again. Reusing the same mutable application row also risks retaining an old snapshot for a later attempt.

**Correction:** Introduce explicit application attempts and states such as submission_in_progress and submission_unknown. Freeze the reviewed payload and document hashes before the click. On uncertain completion, reconcile portal evidence and confirmation mail before allowing another attempt. Do not silently reset a submitted attempt. Invalidate approval if the payload changes after approval.

**Acceptance checks:** Re-running a submitted/confirmed attempt does not click Submit again. A crash immediately before or after the click resumes through reconciliation. Unproven receipt remains explicitly uncertain. The stored payload and submission timestamp describe the actual attempt.

**Resolution notes (2026-11-11, assistant; left unchecked for the owner and Codex):**

- **Reproduced first.** `tests/unit/test_submission_attempts.py` (26 tests): on the previous `submit.py`/`db.py`/`main.py`/`availability.py` all 26 fail. Confirmed on the old code: a re-run rewrote a submitted/confirmed row back to `filled`, a click with no thank-you page was recorded as `submitted` (and the job as `applied`), nothing was written before the click, `availability.known` ignored an applied job and our own sent application.
- **Change.**
  - `db.py`: table `submission_attempts` (attempt_no, state in_progress/unknown/submitted/confirmed/refused/not_sent, payload hash and file, approved/started/clicked/finished times, evidence) and the application states `submission_in_progress` and `submission_unknown`.
  - `steps/submit.py`: `record` never moves a sent state (`SENT`) backwards (only the attempt code, having proved nothing went out, may move in_progress to failed); `guard` stops a refill/re-click of a sent or possibly sent application, in the hook and in `submit_cmd` before a browser opens; `freeze_payload` (field values plus hashes of the job folder's PDFs) taken at approval and compared again right before the click, a changed page returns `not_ready` and writes no attempt; `begin_attempt` commits the attempt and `submission_in_progress` before the click and clears the previous attempt's snapshot; the outcome is settled from evidence: thank-you page gives `submitted`, site errors give `refused`/`failed` (a new attempt is allowed), no Submit button gives `not_sent`, an exception during the click or its aftermath, or a click with no thank-you page and no error, gives `submission_unknown` (never `submitted`, job marked `needs_attention`, not `applied`); a matching confirmation mail settles `confirmed`.
  - Reconciliation: `confirm` (and `jobagent confirm`) looks for the mail since the attempt began; an unproven attempt can also be settled by the candidate after checking the portal with `--attest sent` or `--attest not-sent` (the only way a new attempt can start).
  - `main.py`/`apply.py`: new exit code 6 (`EXIT_UNCERTAIN`) for "may have been sent, unproven", returned both when a sent application is re-run and when a run ends unproven.
  - `tools/availability.py`: `known` rejects a posting whose job is `applied` or whose application is in any sent state (the message for an uncertain one says to run `jobagent confirm`).
- **Acceptance checks.** Re-running a submitted/confirmed attempt: no prompt, no click, row byte-for-byte unchanged (parametrized over all four sent states). Crash before/after the click: simulated both (attempt written then process "dies"; click raising); resume goes through `confirm`; no mail keeps it uncertain, a mail confirms it, `--attest` settles it either way. Unproven receipt stays `submission_unknown` and the payload file, hash and click time describe the real attempt. Targeted: 26 passed. Full unit suite on the whole working tree: 653 passed.
- **Limits.** (1) The payload is the page's field values plus document hashes, not a network capture of the actual request. (2) A hard crash after the click but before the next write leaves `submission_in_progress`, which is treated as uncertain (never as not sent). (3) Reconciliation evidence is the confirmation mail or the candidate's word; the agent does not log into the portal to look for the application. (4) `jobagent confirm --attest` is tested through `confirm()`, not through the CLI parser. (5) Rows from before this change keep their old states; a previously "submitted" row without a thank-you page is not rewritten.


**Earlier independent verification (2026-11-11, Codex; see the follow-up and latest verification below): PARTIALLY FIXED — F04 remained unchecked.**

- **Verified improvements:** attempts are committed before the high-level submit call; ordinary reruns of sent/uncertain applications are guarded; unproven receipt can remain uncertain; reconciliation and human attestation are available; and records protect established sent states from routine backward updates. The existing regressions for those paths pass.
- Four independent checks nevertheless found the following gaps. They used isolated HTML fixtures, real browser/approval helpers, temporary SQLite state, blocked network requests, and mocked receipt/mail checks. No real application or mailbox was used. Mocked receipt results demonstrate state handling, not remote receipt.

**1. A low-level click timeout can be mistaken for proof that nothing was sent.**

- **Where:** `tools/forms.py::click_submit_input`, `steps/submit.py::make_at_review`.
- **Reproduction:** an otherwise complete form had an unlabelled `<input type="submit" value="">` and an `onsubmit` handler that incremented a browser counter and prevented navigation. Wrapped the real `Locator.click` so it delivered the click and then raised Playwright `TimeoutError`, simulating a lost acknowledgement after dispatch. `click_submit_input` caught the exception and returned false. The hook reported `failed: no Submit button`, settled the attempt as `not_sent` with `clicked_at=None`, and `guard` returned `None`. A second review clicked again: the actual browser submission counter became **2**.
- **Required correction:** distinguish "no actionable submit control found, no click attempted" from "a click was attempted but its outcome is unknown." Once an action is dispatched, propagate uncertainty to the attempt controller and stop searching/clicking other controls or frames. A timeout after dispatch must retain `submission_unknown`/an uncertain attempt, preserve the retry guard, and require reconciliation before any further click. Do not infer `not_sent` from a helper's generic false result after it swallowed an action exception.
- **Regression:** exercise the real unlabelled-input helper with a delivered-then-timeout click. Assert one browser submission, an uncertain attempt/application, uncertainty exposed to the CLI, and no additional click on rerun. Keep a separate genuine-no-button case that can safely settle `not_sent`.

**2. An approval that was already waiting can send again after another review completes.**

- **Where:** `steps/submit.py::guard`, `make_at_review`, `begin_attempt`.
- **Reproduction:** during one real review hook's approval wait, completed a second real review hook for the same application, then let the first approval continue. This deterministically simulates overlapping reviews without threads. The first completed review wrote `submitted`; the stale outer review then called `begin_attempt`, which unconditionally changed that application back to `submission_in_progress` and created another attempt. The actual browser submission counter became **2**, and both attempt rows ended `submitted`.
- **Required correction:** atomically reserve permission to begin an attempt from an allowed unsent state, with a per-application active-attempt constraint or equivalent compare-and-set/transactional check. The earlier pre-prompt guard is insufficient after a human wait. Recheck state after approval and prevent `begin_attempt` itself from resetting a sent/uncertain application. Payload artifact allocation must belong to the winning reservation so overlapping workers cannot overwrite one another's frozen data. This is protection at the submission boundary, independent of F13's queue-worker ownership fix.
- **Regression:** interleave two approval hooks (and, where practical, independent database connections). After one sends or becomes uncertain, the other must stop with no second click, no backward state update, and no second active/sent attempt. Test both successful and unknown first outcomes.

**3. The frozen document hash need not describe the attached document.**

- **Where:** `steps/submit.py::freeze_payload` and its use by the approval hook.
- **Reproduction:** manually attached fixture A from a separate directory as `resume.pdf`, while the job folder contained different fixture B under the same basename. Read the native browser `File` contents to verify that A was attached. Approved through the real hook. The saved attempt payload's `documents['resume.pdf']` hash matched **B**, not **A**; both pre-click snapshot hashes agreed because both inspected the unrelated job-folder file. The filename alone did not reveal the mismatch.
- **Required correction:** freeze and bind the hashes of the actual attachments that will be submitted, including manual replacements, to their upload controls. Keep an immutable copy or verifiable upload-time/browser-file identity rather than assuming a same-named job-folder file supplies the bytes. If a custom widget's actual bytes cannot be verified, represent that uncertainty explicitly instead of attributing another file's hash. A changed actual attachment must invalidate the prior approval or be reviewed and frozen as the newly approved document. This concerns F04's attempt identity; it does not require solving F05's PDF-content validation.
- **Regression:** two different PDFs with the same filename (manual attachment versus generated job-folder file) must never produce a frozen hash falsely attributed to the attached file. Also cover manual replacement between snapshots and unchanged filenames with changed bytes. Inspect browser-held bytes as the independent expected value.

**4. The attempt's click timestamp is recorded at settlement time.**

- **Where:** `steps/submit.py::settle_attempt`, the post-click receipt wait, and `confirm`.
- **Reproduction:** a controlled clock read `12:00:00` when the hook clicked and captured `applications.submitted_at`. Receipt checking then advanced the clock to `12:02:00`. The attempt row stored `clicked_at=12:02:00`, equal to `finished_at`, while the application correctly stored `submitted_at=12:00:00`. `settle_attempt(clicked=True)` uses a fresh `_now()` after waiting rather than the captured click time. Later reconciliation can use that inaccurate attempt timestamp as the application's submission time.
- **Required correction:** persist the captured dispatch/click time immediately at the action boundary, independently of the later receipt/settlement time. Pass explicit timestamps into settlement rather than treating `clicked=True` as "the click happened now." Preserve honest uncertainty for a crash where the exact dispatch time is unknown; distinguish an attempted-action timestamp from proven receipt. Reconciliation must retain the original action time or an explicitly marked estimate.
- **Regression:** with a delayed receipt and with unknown-to-confirmed reconciliation, assert distinct accurate action and settlement timestamps. Waiting for evidence must not move the original action time forward.

**Regression run and scope:**

```bash
pytest --basetemp=/dev/shm/f04-verification-xJLoQq -q \
  tests/unit/test_submission_attempts.py \
  tests/unit/test_submit.py \
  tests/unit/test_availability.py \
  tests/unit/test_approval_uploads.py \
  tests/unit/test_planner_failure.py \
  tests/unit/test_apply_exit_codes.py \
  tests/unit/test_start_navigation.py \
  tests/unit/test_desktop_application.py \
  tests/unit/test_jobqueue.py \
  tests/unit/test_cli_discovery.py \
  tests/unit/test_ats_summary.py
```

- Executed against an isolated copy of the current workspace in Docker with required example fixtures, `PYTHONPATH=src`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and `TMPDIR=/dev/shm`. Result: **183 passed**, including all **26 submission-attempt tests**; 13 dependency deprecation warnings, no skips or failures. This was a targeted regression run, not a fresh full-project suite. The four additional failing-behavior reproductions were executed independently in memory and are not covered by the passing suite.
- Confirmed all **122 Python source/unit-test files** matched the tested snapshot before updating this document. Only this review document was edited; application source/tests and the live container's `/app/src` were not changed by verification. F01, F02, and F03 remain checked off. **Keep F04 unchecked until the acceptance failures above are corrected and tested.**

**Follow-up to the independent verification (2026-11-11, assistant; F04 left unchecked for the owner and Codex):**

- **Agreed and reproduced** (`tests/unit/test_submission_attempts.py`, now 38 tests; 11 of the 12 new ones fail on the previous code, several of those by the changed `freeze_payload` signature rather than the behaviour, so they show the interface change more than the bug).
- **1. Lost click acknowledgement.** `click_submit_input` swallowed a click exception and returned false, which the hook read as "no Submit button" / `not_sent`. Now a failure once a click was started raises `forms.ClickOutcomeUnknown`, no other control or frame is tried afterwards, and the hook settles `unknown` (application `submission_unknown`, job `needs_attention`), returns that state (CLI exit 6) and keeps the retry guard. `not_sent` is used only when no control was found and nothing was clicked (separate test).
- **2. Stale approval.** `begin_attempt` now reserves atomically: one `BEGIN IMMEDIATE` transaction refuses unless the application is unsent with no open or completed attempt, then inserts the attempt and moves the application; a unique index (`one_open_attempt`) allows one open attempt per application whatever the code does; the payload file is written only by the winning reservation (a failed write releases it); the hook also re-checks `guard` after the approval wait and treats `AttemptRefused` as uncertain. Tests interleave two real review hooks (first outcome submitted and unknown) and two database connections: one click, one attempt row, no backward state change.
- **3. Document hash.** `freeze_payload(view)` now hashes the bytes the browser holds in each upload control (read through the page, per frame and slot, with name and size), not a same-named job-folder file. Same name with different bytes, swapped files and manual replacement between the two snapshots change the hash and stop the send; the saved payload names the real file's hash. A widget that shows only a file name is recorded as `unverified` (no hash), never attributed another file's bytes.
- **4. Click time.** `action_at` is committed just before the click starts (new column, migrated), `clicked_at` is captured when the click returns and written at once; `settle_attempt` takes the captured time and never uses "now" for it (`finished_at` is the settlement time). Reconciliation keeps the original: `clicked_at`, else `action_at`, else `started_at`. Tests use a controlled clock: click at 12:00:00, receipt check ends 12:02:00, mail three hours later; action and settlement times stay distinct.
- **Checks.** `test_submission_attempts.py` 38 passed; full unit suite on the whole working tree: 665 passed.
- **Limits.** (1) Hashes cover files in native inputs; custom widgets that keep bytes elsewhere are recorded as unverified. (2) Any exception from a click, including one raised before delivery, is treated as possibly sent (conservative). (3) A crash between the commit of `action_at` and the click's return leaves only the attempted-action time, which reconciliation uses as an estimate. (4) The `--attest` option is tested through `confirm()`, not the CLI parser.

**Earlier independent verification (2026-11-11, Codex; see the second follow-up and latest verification below): STILL OPEN — replacement-widget coverage and final-dispatch validation were incomplete.**

- **Verified improvements:** the delivered-then-timeout path now preserves uncertainty and blocks another click; stale reviews and independent connections cannot both reserve the demonstrated attempt; native-file hashes describe browser-held bytes; and action/click timestamps are kept separate from receipt/settlement time. The expanded submission-attempt suite passes all 38 tests.

**1. A supported replacement upload widget is omitted from the frozen payload.**

- **Where:** `steps/submit.py::_attachments`, `freeze_payload`, and the approval hook.
- **Reproduction:** used the real replacement-input `WIDGET` from `tests/unit/test_approval_uploads.py`. Its native input is replaced by a span containing the chosen filename. Captured its required-upload record before attachment, then attached `reviewed_resume.pdf`. F03 correctly recognized the attachment from its widget, but `freeze_payload(forms.read_page(page))` returned `attachments: []`: the filename span was not a discovered form field. Changed the visible attachment name to `different_resume.pdf`; the frozen hash stayed identical and `attachments` was still empty.
- Repeated the filename change between the hook's two actual snapshot calls. Approval still ran the real submit handler and wrote an attempt whose saved `attachments` was `[]`. The existing unverified-widget test uses a readonly text input containing a filename; it does not cover a real replacement widget whose filename is ordinary text rather than a form field.
- **Required correction:** construct the attempt's attachment inventory from supported live widgets and recorded upload identities, not only current `view.fields`. A replaced/custom widget must remain represented with its own frame/control association, observed attachment name(s), and explicit verification status. Unknown bytes must have an explicitly unknown hash; the attachment must not disappear. Include that metadata in the approval fingerprint so a changed widget attachment invalidates approval. Preserve actual native-file hashing and F03's widget-boundary checks.
- **Regression:** use the existing onchange-replacement `WIDGET`, not a readonly textbox stand-in. Assert that its attachment appears as unverified when bytes cannot be retrieved, that changing its filename changes the fingerprint, and that a change between snapshots produces `not_ready` with no final click. Cover a chooser widget that remains visible without a native input as well.

**2. The last payload check precedes potentially slow preparation and is not enforced at final dispatch.**

- **Where:** `steps/submit.py::make_at_review`, `begin_attempt`, `click_submit`, and the final-click approval boundary.
- **Reproduction:** started with `reviewed@example.org` in the required email field. Wrapped the real `begin_attempt` so it completed its reservation/payload write, then changed the live field to `changed-after-freeze@example.org` before returning. This deterministically simulates a page update during reservation/filesystem work after the two consecutive hashes. The real final button read and recorded the changed email, but the saved attempt payload still contained `reviewed@example.org`. The hook returned the mocked-receipt `submitted` state and the attempt ended `submitted`.
- The two hashes are currently checked before `page_text`, reservation/database/filesystem work, `mark_action`, and the challenge guards inside the click helpers. Changes during those operations, including a human-verification wait, are not bound to the earlier approved fingerprint. The approval object still carries only a job folder and request stamp, so the final helper cannot validate its payload identity.
- **Required correction:** enforce the expected approved fingerprint at the final dispatch boundary after potentially blocking preparation/challenge handling. Bind final-click authorization to the reviewed payload rather than only to the existence of an approval object. If the page or attachment changes before any click, invalidate approval, retain an accurate not-sent/aborted reservation, and require a fresh review. Do not classify a proven pre-dispatch invalidation as a delivered submission. Preserve the atomic reservation and duplicate-click guard.
- **Regression:** mutate a field and a native attachment after the reservation step but before dispatch, and after any challenge/HITL wait inside the sending path. Assert that no handler runs, the old approval is invalidated, the attempt cannot be recorded as submitted, and a new approval is needed. Retain the existing between-snapshot tests, which cover a different, earlier interval.

**Evidence and scope:**

- Both additional reproductions used real browser field reads, the approval hook, attempt persistence, and final-click helpers. Network requests were blocked; receipt detection and confirmation polling were mocked; SQLite/artifacts were temporary. No real application or mailbox was used. The returned submission status used a mocked receipt; the decisive evidence is the actual handler running after an undetected attachment change or with a field value different from the saved payload.
- Ran the same eleven files in the previous F04 regression command against an isolated current-workspace copy in Docker, using `--basetemp=/dev/shm/f04-followup-verification-OCsBaI`, the required example fixtures, and the same test environment. **195 passed**, including all **38 submission-attempt tests**; 13 dependency deprecation warnings, no skips or failures. The two additional failing-behavior reproductions were executed independently in memory and are not covered by that passing suite. This was not a fresh full-project suite.
- Confirmed all **122 Python source/unit-test files** matched the tested snapshot before updating this document. **F04 remains unchecked** because the frozen payload and invalidation requirements still fail for demonstrated supported application flows. F01, F02, and F03 remain checked. Only this review document was edited; application source/tests and the live container's `/app/src` were not changed by verification.

**Second follow-up to the independent verification (2026-11-11, assistant; F04 left unchecked for the owner and Codex):**

- **Agreed and reproduced** (`tests/unit/test_submission_attempts.py`, now 46 tests; all 8 new ones fail on the previous code).
- **1. Replacement widget missing from the payload.** The inventory was built from `view.fields` only, so an upload whose native input was replaced by a file name vanished from the fingerprint. Now `forms.widget_uploads` (built on the same record/live join as the F03 readiness check, `_uploads`) lists every upload with no live file input, with the file name seen inside its own boundary; `submit._attachments` records it as `unverified` (`only the name is shown: <name>` or `no attachment shown`) with no hash, and live controls whose bytes cannot be read are listed as `bytes unreadable`. `freeze_payload(page, view, records)` takes the review's field records, so the name is part of the approval fingerprint: renaming the file in the widget changes it, between the two snapshots gives `not_ready` with no click and no attempt row. Covered with the real onchange-replacement `WIDGET` and with a chooser that has no native input.
- **2. Last check before the slow steps, not at dispatch.** `SubmitApproval` now carries the approved `payload_sha` and a verifier, and the click helpers (`click_final_submit`, `click_submit_input`) call `approval.still_valid()` immediately before the click is dispatched, after the challenge guard, the button search, reservation, database and filesystem work. If the page or an attachment changed, `forms.ApprovalInvalidated` is raised before any click: the hook settles the attempt `not_sent` (action time cleared, since nothing was dispatched), the application `failed` (a fresh review may start), returns `not_ready` (CLI exit 5) and never records `submitted`. A new approval is required and works. Tests mutate a field, a native attachment and a widget file name after the reservation, and a field during the challenge wait inside the sending path (both for the labelled button and the unlabelled input): no handler runs, attempt `not_sent`, never `submitted`. The atomic reservation and duplicate-click guard are unchanged.
- **Checks.** `test_submission_attempts.py` 46 passed; full unit suite on the whole working tree: 673 passed.
- **Limits.** (1) Bytes of custom widgets cannot be read: they are recorded as unverified with the observed name, so a different file with the same name in such a widget would not change the fingerprint. (2) The verifier re-reads the page once, right before the dispatch; a change in the few milliseconds between that read and the browser handling the click cannot be excluded.

**Earlier independent verification (2026-11-11, Codex; superseded by the third follow-up and final verification below): STILL OPEN — the frozen payload omitted earlier-page answers.**

- **Verified improvements:** supported replacement widgets remain in the attachment inventory with explicit unverified filename metadata, and changing that metadata changes the fingerprint. Both final-click helpers enforce the approved fingerprint after reservation/challenge handling. The previously reported timeout, reservation, native-byte-hash, timestamp, and reconciliation regressions also pass.
- **Four independent checks passed:** replacement-widget inventory and filename-change invalidation; post-reservation field change blocked before dispatch with a `not_sent` attempt; native attachment hash matching the browser-held bytes rather than a different same-named disk file; and a delivered-then-timeout click remaining uncertain with no second click on retry.
- **Remaining defect:** `steps/submit.py::freeze_payload` constructs `payload['fields']` solely from the current `view.fields`. It accepts all application `records`, but uses them only for attachment inventory. Ordinary field answers from previous wizard pages therefore disappear from the attempt snapshot, even though `_run` collected and verified them and the review packet contains them. On a final review screen with no editable controls, the frozen ordinary-field list can be empty. This fails F04's requirement to freeze the reviewed application payload rather than just the currently visible controls.
- **Independent end-to-end reproduction:** drove the real `_run` through a two-page HTML wizard with real form extraction, filling, Next navigation, the approval hook, final-click helpers, and attempt persistence. Only model-backed planning/checking stages and receipt/mail services were stubbed. Page 1 collected required `Full name = Reviewed Candidate`; clicking Next retained it in the fixture application's state. Page 2 contained required `Email = me@example.org` and the final button, whose handler consumed both values. The resulting evidence was:

| Evidence | Fields represented |
|---|---|
| `_run` field records | Page 1 Full name; page 2 Email, both read back correctly |
| Actual final handler payload | `full_name: Reviewed Candidate`, `email: me@example.org` |
| Frozen `submission-1.json` ordinary fields | Email only |

  `Reviewed Candidate` did not appear anywhere in the frozen payload. The hook returned the mocked-receipt `submitted` state; the actual browser handler consumed both fields. Both pages were successfully read, so this is not F17's unreadable-field issue.
- **Required correction:** freeze the complete reviewed application from the accumulated field records, retaining page/frame/section/control identity, values/readbacks, and their verification provenance. Reconcile current live fields with their corresponding records without dropping earlier-page fields or merging distinct repeated questions. Include this complete representation in the approved fingerprint and the immutable attempt artifact. A value last verified on a previous page should be identified as such rather than falsely described as freshly read on the final screen. Preserve native-file hashing, explicit unknown hashes for opaque widgets, and the final-dispatch verifier.
- **Required regression checks:** use a real two-page wizard through `_run`, not just a single-page approval-hook fixture. Assert that every reviewed earlier answer appears in the saved attempt and participates in its fingerprint. Add a read-only final summary screen and repeated labels across pages/sections. Changes to the reviewed historical values must invalidate their previous fingerprint; unchanged prior answers must survive navigation and reconciliation. Retain all existing retry/crash, atomic-reservation, filename/native-byte, final-dispatch, and F01/F03 cases.
- **Regression result:** ran the same eleven files in the original F04 verification command using `--basetemp=/dev/shm/f04-final-verification-aYQ51t` against an isolated current-workspace copy in Docker, with required fixtures and the same test environment. **203 passed**, including all **46 submission-attempt tests**; 13 dependency deprecation warnings, no skips or failures. The four additional checks above passed independently in memory, but the additional two-page reproduction exposed behavior not covered by that suite. This was not a fresh full-project suite.
- All fixtures blocked network requests and used temporary SQLite/artifacts with mocked receipt detection and confirmation polling. No real application or mailbox was used, and mocked receipt is not proof of remote submission. Confirmed all **122 Python source/unit-test files** matched the tested snapshot before updating this document. **F04 remains unchecked** until the complete reviewed payload is frozen. F01/F02/F03 remain checked; only this review document was edited, with no application-source/test edits or replacement of the live container's `/app/src`.

**Third follow-up to the independent verification (2026-11-11, assistant; F04 left unchecked for the owner and Codex):**

- **Agreed and reproduced** with a real three-page wizard through `apply._run` and the real approval hook (`tests/unit/test_submission_attempts.py`, now 49 tests; the 3 new ones fail on the previous code, partly by a missing key, since the new payload keys do not exist there). Earlier-page answers were not in the frozen payload because `freeze_payload` built `fields` from the visible controls only.
- **Change** (`steps/submit.py::freeze_payload`): the payload and the approval fingerprint now have three parts: `reviewed` (every answer of every page, one entry per accumulated field record in order, never merged, with page, section, kind, label, required, answer, filled value, read-back, verification outcome, source, flag, error, and a provenance line saying it was filled and read back on page N and not on the final screen), `screen` (the final screen's fields as read now) and `attachments` (as before). Passwords are redacted. Repeated labels on different pages stay separate. The final-dispatch verifier recomputes the same fingerprint, so a changed answer, screen value, file or widget name invalidates the approval before the click.
- **Checks.** The wizard saves `Reviewed Candidate`, the email and both "Phone" entries (111-1111 page 1, 222-2222 page 2) in `submission-1.json`, with an empty `screen` for the read-only final page; the final handler really consumed the same values. Changing, dropping or swapping a historical answer changes the fingerprint; unchanged records give the same hash; a password never reaches the file. Full unit suite on the whole working tree (with F05 and F06 in place): 717 tests; 716 passed in the run, and the one failure (an old Step 15 snapshot test that encoded the pre-F06 behaviour) was updated and passes.
- **Limits.** The records are the ones `_run` collected; an answer typed by hand in noVNC on an earlier page after Next is not in them (it is not on the final screen either). Provenance says where a value was last read, not that it was re-read at the end.

**Final independent verification (2026-11-11, Codex): VERIFIED FIXED — F04 checked off.**

- Reviewed the latest `freeze_payload` implementation and its use by the final-dispatch verifier. The attempt now retains the complete accumulated `reviewed` field records separately from the live final `screen`, with page/section/control context and verification provenance. Earlier-page answers participate in the fingerprint instead of disappearing when the form advances. Native attachment hashes and explicit unverified widget metadata remain included.
- **The previous multi-page reproduction now passes independently:** drove the real `_run` through a two-page fixture with real extraction, filling, Next navigation, the approval hook, final-click helpers, and attempt persistence. The final browser handler consumed `Full name = Reviewed Candidate` from page 1 and `Email = me@example.org` from page 2. Both values appeared in the saved attempt's `reviewed` entries with their correct pages. Changing a historical answer changed the fingerprint. Model-backed stages, receipt detection, and mail polling were stubbed; network requests were blocked and no real application/mailbox was used.
- All **49 submission-attempt tests** passed in the shared unit run below, including the three-page read-only-final-screen wizard, repeated labels on different pages, historical-answer fingerprinting, password redaction, retries/crash reconciliation, atomic reservation, actual native-file hashes, widget metadata, and final-dispatch invalidation.
- **Shared F04/F05/F06 verification:** copied the complete current source/unit-test tree and required example fixtures into an isolated directory in Docker. The live `/app/src` tree was not replaced. Environment: `PYTHONPATH=src`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and `TMPDIR=/dev/shm`.

```bash
pytest --basetemp=/dev/shm/f04-f06-verification-VOPhln -q tests/unit
```

  Result: **716 passed, 1 failed** out of 717 unit tests. The failure was `tests/unit/test_scrape_parallel.py::test_scrape_many_runs_in_parallel_and_skips_cached`, outside these three findings. It then **passed on an isolated rerun**:

```bash
pytest --basetemp=/dev/shm/f04-f06-scrape-recheck-VOPhln -q \
  tests/unit/test_scrape_parallel.py::test_scrape_many_runs_in_parallel_and_skips_cached
```

  Do not describe this as a clean 717-pass full-suite run or infer the original failure's cause from the successful rerun. All F04/F05/F06 regression tests passed in the full run; F05/F06 remain open because of the additional independent counterexamples below.
- All **126 Python source/unit-test files** still matched the tested snapshot before updating this document. Only this review document was edited during verification. F04 closure covers the specified attempt/approval/payload acceptance cases, with recorded provenance rather than a claim of network-request capture. Opaque widget bytes remain explicitly unverified; historical answers retain their last-read provenance. F05's document gate and F06's derived learning snapshot are separate and remain open.

## F05

**High — Document-generation failures do not reliably stop the pipeline.**

**Current status: verified fixed.** The final independent verification below supersedes the earlier open verdicts retained as review history.

**Where:** [main.py](src/jobagent/main.py), `tailor_cmd`, `ats_loop_cmd`; [documents.py](src/jobagent/tools/documents.py), `reset`, `load`, `verify`, `refresh`; [multi_agent.py](src/jobagent/steps/multi_agent.py), `run_job`; [cover_letter.py](src/jobagent/tools/cover_letter.py), `write`; [submit.py](src/jobagent/steps/submit.py), `review_text`.

**Issue and reason:** Tailoring prints page counts and findings without reliably returning failure. Exhausting the graph without a valid best version can leave generated files behind and return `pages=None` with an empty hard-issue list. Cover-letter generation returns its best available draft even if findings remain. These results can proceed through a successful shell command. Document estimates or other claims requiring review also need to reach the review packet, rather than remaining only in intermediate data.

**Correction:** Publish a typed document result separating validity from ATS-target attainment. Missing documents, compile failures, invalid page counts and violations of the current approved claims policy must prevent application filling. Define a separate explicit policy for ATS shortfalls. Include document findings and estimates requiring confirmation in the review packet.

**Acceptance checks:** Invalid PDFs, no valid best version and unresolved hard findings fail the generation stage. A valid below-target resume is accurately identified as such. A stale previous PDF cannot masquerade as the current successful build. Shell `&&` chains stop on actual generation failure.

**Resolution notes (2026-11-11, assistant; left unchecked for the owner and Codex):**

- **Reproduced first** (`tests/unit/test_documents.py`, 28 tests; 13 of the 14 behaviour tests run against the previous code fail): `run_job` with no best version returned `pages=None` and an empty hard-issue list and left earlier files behind, the cover-letter writer returned its best draft with no validity, a failed compile left an earlier `cover_letter.pdf` in place, `tailor` always exited 0, and `apply`/`submit` never looked at the documents.
- **Change.**
  - `tools/documents.py` is the typed result (`documents.json`): resume and cover-letter validity (PDF exists, is a PDF, one page, no hard issue, no claims-policy finding, no unresolved cover-letter finding), the PDFs' SHA-256, findings, the estimated figures, `blockers` and `warnings`, and the ATS status (`met`, `below_target`, `unscored`, `incomplete`) kept apart from validity. The ATS shortfall is a separate explicit policy: settings `ats_shortfall` (`warn` default: a valid below-target resume proceeds with a warning; `block`: it does not) and `ats_target`.
  - Stale files: `run_job` and the single-model path delete the previous build's PDFs, `.tex`, `filled.json` and `documents.json` first; with no valid best version the leftover resume PDF is removed and `run_job` reports `produced: false` and a failure; the cover-letter writer removes its old files first, returns `valid`/`pages`, and a failed compile removes the PDF.
  - `jobagent tailor` writes the record, prints validity and the ATS status separately and exits 1 for invalid documents (no cover letter is written from a failed resume) or 8 when the policy is `block` and the target is missed. New `jobagent documents <dir>` re-validates what is on disk (exit 0/1/8); `ats-loop` and `cover-letter` re-validate automatically.
  - Gate: `apply` and `submit` exit 4 before a browser opens when the record is invalid, or when a PDF no longer matches its recorded hash (stale or edited); a folder with no record passes only with valid one-page PDFs and a warning.
  - Review packet: a Documents section with validity, ATS status against the target, cover-letter findings and a table of the estimated figures to confirm.
- **Acceptance checks.** Invalid or corrupt PDFs, no valid best version and unresolved hard findings fail the stage; a valid below-target resume is identified as `below_target` (warn: exit 0, block: exit 8); a stale PDF cannot pass (hash check, and the old PDFs are deleted first); `tailor`, `cover-letter`, `documents`, `apply` and `submit` exit nonzero on real failure so `&&` chains stop. Full unit suite on the whole working tree: 717 tests; 716 passed in the run, and the one failure (an old Step 15 snapshot test, see F06) was updated and passes. Three other tests changed with the behaviour: `fake_world` in `test_apply_exit_codes.py` now gives its job folder a valid documents record, the desktop fallback test stubs the new gate, and `run_job` tolerates a test double without a summary.
- **Limits.** (1) The claims-policy check for the resume covers what the code can test (the Summary's years and copied qualification wording, via the existing `summary_issues`); the content review by the validator model is advisory and not a hard gate. (2) Folders built before F05 have no record and pass on valid PDFs with a warning. (3) After a hand edit of `resume.pdf` the record must be refreshed with `jobagent documents`.

**Earlier independent verification (2026-11-11, Codex; see the follow-up and latest verification below): PARTIALLY FIXED — F05 remained unchecked.**

- The existing **28 document tests** passed in the shared full-unit run recorded under F04. They verify important successful changes: invalid/missing/multipage PDFs and explicitly recorded hard findings block; ordinary stale hashes are detected; document validity and ATS policy are separate; unresolved letter findings are surfaced; default tailoring failures return nonzero. Independent checks found four remaining gaps.

**1. Interrupted builds and corrupt manifests fall through to the legacy allow path.**

- **Reproduction:** ran the real `multi_agent.run_job` with its graph stubbed to write a valid one-page draft `resume.pdf` and then raise a model failure. `documents.reset` had removed the previous manifest and cover letter. After the exception, `documents.json` and the cover letter were absent, but `documents.verify` returned **`ok=True`** with only the legacy warning. Thus a draft from a newly failed build is indistinguishable from an old trusted folder and can pass the application gate.
- Separately saved a hard-invalid build's record, then replaced the manifest text with a truncated JSON value. Both one-page PDFs remained on disk. `load` returned `None`, and `verify` again returned **`ok=True`**, discarding the failed build's known validation result.
- **Required correction:** persist a fail-closed build/in-progress record before replacing generation outputs, and retain an invalid/error state after exceptions or interruption. Distinguish a genuinely absent legacy manifest from an existing unreadable/malformed/schema-invalid manifest. Atomic manifest writes can prevent partial JSON but must not turn an interrupted new build into a legacy exception. Preserve any deliberate legacy-import policy without applying it to incomplete current builds.
- **Regressions:** crash after a draft PDF is written, including the period before the final manifest write; corrupt an existing failed-build manifest. `apply`/`submit` must stop before browser use in both cases. A completed successful rebuild and any explicitly supported legacy import should remain distinguishable.

**2. Refresh can clear unresolved hard findings without checking their underlying conditions.**

- **Reproduction:** stored an invalid record with `9 blank lines at the bottom`. With the PDF unchanged and the layout checker still reporting excessive blank space (`free_pt=99`, one page, a blank-space issue), `documents.refresh` discarded the old hard finding, recorded `hard_issues=[]`, and changed the application gate from **false to true**. The layout response was stubbed to isolate the decision; assessment, manifest writes, refresh, and gate were real.
- **Cause:** refresh re-runs only a prefix-filtered subset of layout issues. It does not apply the multi-agent hard blank-space threshold, empty-skills/project rules, or the full set of previously enforced hard checks. It can also discard the failed-build reason. A refresh is therefore not equivalent to the validation that initially rejected the build.
- **Required correction:** share the actual hard/claims validation contract across generation and refresh. Re-run the checks using their required current PDF, layout, filled-resume, and project evidence. Keep a known finding unresolved when the required evidence/check is unavailable; do not silently replace the set with a narrower check's empty result. Genuine corrections should clear findings only after the corresponding validator passes.
- **Regressions:** an unchanged invalid PDF must remain invalid after `jobagent documents` and automatic refresh. Cover excessive blank space, empty skills/project constraints, and failed-build state, then demonstrate that a real corrected artifact clears the appropriate finding.

**3. The single-model writer bypasses the existing claims-policy gate.**

- **Reproduction:** invoked the real `tailor --single` command with a writer stub producing one-page fixture PDFs and `filled.json` containing `Engineer with 99 years of professional experience.` The existing declared-experience policy was set to 5 years; `multi_agent.summary_issues` correctly flagged that exact summary. Nevertheless the command exited **0**, persisted `valid=True`, and recorded `claims_issues=[]` because the single-model branch hardcodes an empty claims list.
- **Required correction:** apply the same current owner-approved claims checks to both writers and later refreshes. Validate the final generated content rather than assuming a successful writer return proves policy compliance. This does not authorize reverting the owner's GPA, address, referral, or other approved policies to the older plan.
- **Regression:** the same over-limit summary must fail both writer modes with a durable invalid record and a nonzero exit. The valid, within-policy counterpart must pass. Keep model/content review advisory only where the current policy explicitly makes it advisory.

**4. ATS-loop revalidation failures are printed but not propagated as command failure.**

- **Reproduction:** invoked the real `ats-loop` CLI with ATS work stubbed and an unresolved cover-letter consistency finding in the build. Refresh persisted `valid=False`, the command printed `documents: INVALID`, and the application gate remained false, yet the CLI returned **exit 0**. A shell `&&` chain would continue after the command had reported the build invalid.
- **Required correction:** propagate the refreshed document result's failure/policy exit code from document-writing entry points, including `ats_loop_cmd`, rather than only printing it. Preserve the explicit distinction between invalid artifacts and valid below-target artifacts under warn/block policy.
- **Regression:** ATS-loop refresh of an invalid build must return nonzero; valid warn-policy shortfalls and valid successful builds should keep their intended exit codes. Test the actual CLI wrapper, not just `documents.exit_code` in isolation.

- These checks used temporary files, fixture PDFs, stubbed writer/ATS/model stages, and real gate/CLI logic; no live ATS scan or application was performed. The blank-space refresh check deliberately supplied a still-failing layout result. The single-model check supplied a synthetic over-limit summary to test the existing policy rather than changing it. **F05 remains unchecked** pending the lifecycle, validator-parity, and exit-code fixes above.

**Follow-up to the independent verification (2026-11-11, assistant; F05 left unchecked for the owner and Codex):**

- **Agreed and reproduced** (`tests/unit/test_documents.py`, now 41 tests; all 11 new ones fail on the previous code).
- **1. Interrupted build / damaged manifest read as legacy.** `documents.begin` now starts a build fail closed: the previous outputs are removed and, before anything new is generated, an in-progress manifest (`status: building`) replaces `documents.json` (written atomically: temp file, then rename). A build that raises or is killed leaves it behind, so a draft PDF it wrote cannot pass; only a finished assessment (`status: complete`) replaces it, and `verify` refuses any status but `complete`. `load` separates the cases: no file = the deliberate legacy import (valid one-page PDFs, with a warning); a file that is unreadable, not a JSON object or missing the schema comes back as an invalid `corrupt` record. Both `run_job` and `tailor --single` start with `begin`. Tests crash the real `run_job` after a draft PDF is written (the earlier cover letter is also gone) and damage a failed build's manifest three ways; `apply` and `submit` exit 4 before any browser step in each case.
- **2. Refresh did not match the build's validation.** `documents.resume_findings` runs the same checks that decide a build: `multi_agent.hard_issues` (not one page, project rules, blank band, empty skills lines) and the claims policy, on the current PDF and `filled.json`. `refresh` replaces the findings with its result only when it could run them; when the evidence is missing (no valid `filled.json`/PDF) the earlier findings stay and a blocker says the checks could not be re-run. Build-time findings (a failed build, locked parts, uncovered duties) are carried as `build_findings`/`build_failure` and cannot be cleared by a refresh, and a refresh leaves an unfinished (`building`/`corrupt`) record alone. Tests: an unchanged invalid PDF stays invalid through `refresh`, a real correction (validator passes) clears it, missing evidence keeps the finding, and a failed build stays failed.
- **3. `tailor --single` skipped the claims gate.** Both writers now run `documents.claims_from_filled` (the owner-approved declared-years and copied-qualification-wording checks) on the generated Summary, and refresh re-runs it. The same over-limit summary ("99 years" against the declared 5) fails both modes with a durable invalid record and exit 1; the within-policy counterpart passes. The validator model's content review stays advisory.
- **4. `ats-loop` revalidation was only printed.** `ats-loop` now returns the refreshed result's exit code (1 invalid, 8 valid-but-below-target under `block`, 0 otherwise); `cover-letter` does the same. Tested through the real CLI wrappers.
- **Checks.** `test_documents.py` 41 passed; full unit suite on the whole working tree: 737 passed.
- **Limits.** The legacy import for a folder with no manifest remains by design. A hand edit of a PDF still needs `jobagent documents` before the gate accepts it. The claims check covers the Summary's declared years and copied qualification wording; it does not parse years inside bullets.

**Earlier independent verification (2026-11-11, Codex; superseded by the second follow-up and final verification below): STILL OPEN — the rewrite lifecycle and refresh paths were incomplete.**

- All **41 document tests** passed in the fresh 737-test full-unit run recorded under F06 below. Independent rechecks confirmed that a crashed team build leaves `status=building` and is blocked, and that a malformed manifest is `corrupt` rather than accepted as legacy. The new tests also pass for shared claims checking, preserved hard findings, and CLI failure propagation. The remaining independently reproduced cases are below.

**1. A new ATS rewrite of a legacy folder can still leave an accepted failed draft.**

- **Reproduction:** started with a legitimate legacy folder containing valid one-page fixture PDFs and no manifest. Invoked the real `ats-loop` CLI with its ATS/model stage stubbed to write a rewritten draft PDF and then raise. The CLI correctly exited 1, but there was still **no `documents.json`** and `documents.verify` returned **`ok=True`** with the legacy warning. A subsequent application command could accept this newly failed rewrite as an untouched legacy build.
- **Cause:** `run_job` and `tailor --single` now enroll in the fail-closed lifecycle, but `ats_loop_cmd` starts rewriting first and refreshes only after a successful return. The deliberate legacy exception is therefore still available after a new generation attempt fails through this entry point.
- **Required correction:** enroll every document-writing entry point in the build/rewrite lifecycle before it can modify artifacts, including ATS-loop and standalone cover-letter regeneration where applicable. A rewrite must preserve its necessary input documents and prior validation context while persisting a blocking in-progress state; it cannot simply delete the input it needs. Only successful validation/publication should restore a complete record. Keep untouched legacy import separate from a newly interrupted rewrite.
- **Regression:** interrupt ATS-loop on a legacy folder after it writes a valid-looking draft; assert nonzero generation exit and a later `apply`/`submit` gate refusal before browser use. Cover both legacy and previously validated inputs, plus successful completion that publishes the new record.

**2. Refresh can attach another PDF version's ATS result to the newly validated PDF.**

- This is the shared F05/F06 integration gap detailed under F06 below. A current, valid-but-unscored PDF B was revalidated using PDF A's complete average 93 from `ats_summary.json`. Refresh wrote B's new PDF hash together with `ATS status=met`, and **`documents.verify(..., policy='block')` returned `ok=True`**, although the authoritative result for B had `average=None` and all required sites missing.
- **Required correction:** use the authoritative result for the exact current PDF/JD when refreshing its manifest. A cached summary or old manifest average cannot survive an identity change merely because the PDFs pass validity checks. Preserve the valid/unscored or incomplete distinction: warn policy may proceed with an honest warning; block policy must stop.
- **Regression:** change the PDF without rescoring it, run document revalidation, and assert no inherited `met` status or old average. Verify both warn/block exits and the final application gate, followed by a genuine same-version rescore that permits progress.

**3. A temporary lack of validation evidence becomes an irreversible build finding.**

- **Reproduction:** saved a record with an unresolved hard finding, temporarily omitted `filled.json`, and refreshed. Restored valid filled-resume evidence and made the current hard/claims validators pass. Refresh cleared the original hard finding but still returned **`valid=False`**, solely because the old message `the hard and claims checks could not be re-run (filled.json is missing or invalid); earlier findings kept` remained in `build_findings`.
- The heavy content validators were stubbed to represent repaired evidence and successful checks; the manifest, refresh, and gate were real. This is separate from preserving genuinely irreversible generation failures or locked-content violations.
- **Cause:** a transient refresh-validation error is appended to the persistent build-only finding list, which subsequent refreshes carry forward even when the missing-evidence condition no longer exists.
- **Required correction:** keep dynamic evidence/validation blockers distinct from immutable build failures. Re-evaluate and clear the temporary blocker when the necessary evidence returns and its validator succeeds. Continue preserving genuine build failures and unresolved hard findings until their own required repair occurs.
- **Regression:** missing/invalid evidence blocks; restored valid evidence with successful validators clears that blocker without requiring an unrelated new generation; still-invalid evidence or an actual permanent build failure remains blocked.

- **F05 remains unchecked.** The old findings are materially improved, but a failed rewrite can still pass the legacy fallback, stale ATS evidence can defeat block policy, and a repaired validation-evidence condition cannot recover through the documented refresh workflow. Only the review document was edited during verification.

**Second follow-up to the independent verification (2026-11-11, assistant; F05 left unchecked for the owner and Codex):**

- **Agreed and reproduced** (`tests/unit/test_documents.py`, now 48 tests; 7 of the 8 new behaviour tests run against the previous code fail, the eighth passes there because the old guard already covered it).
- **1. A failed ATS rewrite of a legacy folder passed the legacy fallback.** Every document-writing entry point is now enrolled in the fail-closed lifecycle before it touches anything: `documents.begin_rewrite` (used by `ats-loop` and `cover-letter`) turns the manifest into `rewriting` (not valid, not ready) with the earlier record's findings carried, without deleting the inputs the rewrite needs; a folder with no manifest gets one, so only an untouched legacy folder is a legacy import. A rewrite that fails or is interrupted leaves it behind (`verify` refuses any status but `complete`); `apply` and `submit` exit 4 before a browser opens. A successful rewrite publishes a complete record through `refresh`. Tested with a legacy folder and a previously validated folder (ATS loop crashing after writing a draft), a failing cover-letter regeneration, and a successful completion.
- **2. Refresh attached another PDF's ATS result.** `refresh` now takes the ATS data from the authoritative result for the current PDF and job (`ats_result.gather`), never from `ats_summary.json` or the old manifest; the manifest records the PDF hash and job fingerprint its ATS result describes, and `verify` also refuses a manifest whose job posting changed. A new unscored PDF reads `unscored` (average null), block stops it, warn proceeds with a warning that follows the current policy (also for a manifest saved under the other policy); a genuine same-version rescore restores the status. A changed posting does the same, also through the real `documents` CLI (exit 8 under block).
- **3. Temporary evidence loss became a permanent finding.** The "checks could not be re-run" message is now an `evidence_blocker`: added to the blockers, recomputed by every refresh and never stored with the build findings. When `filled.json` returns and the validators pass it clears without a new generation; still-invalid evidence, unresolved hard findings and genuine build failures (a failed build, locked parts, uncovered duties) stay blocked.
- **Checks.** `test_documents.py` 48 passed; full unit suite on the whole working tree: 748 tests, 747 passed in the run and the one failure (an assertion on the old warning wording, "below the target") updated and passing.
- **Limits.** A hand edit of a PDF still needs `jobagent documents` before the gate accepts it. The legacy import for a folder never touched by this version remains, by design.

**Final independent verification (2026-11-11, Codex): VERIFIED FIXED — F05 checked off.**

- Reviewed the current build/rewrite lifecycle, manifest parsing and publication, shared hard/claims validators, temporary versus permanent findings, ATS identity selection, application gate, and CLI exit handling. All **48 document tests** passed in the shared verification run below, including the earlier failure cases for both writers, interrupted/corrupt builds, stale artifacts, missing evidence, and warn/block policy.
- **Three independent scenario groups passed:**
  - Invoked the real `ats-loop` CLI on a legacy folder, with its ATS stage made to write a valid-looking draft and raise. The command returned nonzero, the manifest remained `rewriting`, and `documents.verify` blocked application use. The old legacy fallback bypass did not recur.
  - Removed the evidence needed to revalidate a hard finding, confirmed refresh blocked, restored the evidence, and supplied successful current hard/claims validators. Refresh became valid and the gate accepted it; no obsolete evidence error remained in permanent build findings.
  - Created five valid scores for PDF A, changed the PDF to B without rescoring, and refreshed under block policy. The current average was null, the result was not `met`, and the block gate refused it. Warn policy allowed the valid documents with an honest warning. Recording all five scores for B and refreshing restored readiness. A's cached summary was not reused as B's score.
- The model/ATS work and heavy content validators in those independent cases were stubbed to isolate failure or successful-validation conditions. PDF checks, manifest persistence, refresh/gate decisions, score gathering, and the CLI wrapper were real; files/databases were temporary and no external ATS site or application was used.
- **Shared F05/F06 full-unit verification:** ran against an isolated copy of the current source/unit-test tree and required example fixtures in Docker. Environment included `PYTHONPATH=src`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and `TMPDIR=/dev/shm`; default state/output directories were isolated in `/dev/shm`, and the data path pointed at the isolated example fixtures.

```bash
pytest --basetemp=/dev/shm/f05-f06-final-UidH8E -q tests/unit
```

  Result: **747 passed, 1 failed** out of 748 unit tests, with 13 dependency deprecation warnings. The sole failure was the previously observed scrape-concurrency test, outside F05/F06. It **passed when rerun alone**:

```bash
pytest --basetemp=/dev/shm/f05-f06-final-scrape-recheck-UidH8E -q \
  tests/unit/test_scrape_parallel.py::test_scrape_many_runs_in_parallel_and_skips_cached
```

  Do not describe this as a clean full-suite run or assume the original scrape failure's cause from the isolated pass. All F05/F06 regressions passed in the full run, and the independent acceptance checks above passed.
- **Scope of closure:** the documented generation-failure, rewrite, revalidation, stale-PDF/score, claims-gate, and CLI policy cases are verified. Untouched legacy imports still follow their explicit warning policy; a new rewrite enters the lifecycle. This does not claim comprehensive semantic validation of arbitrary manually authored PDFs or resolve F18/F19/F25. Application source and tests were not edited during verification.

## F06

**High — Review ATS scores and learning snapshots can describe the wrong score set or document version.**

**Current status: verified fixed.** The final independent verification below supersedes the earlier open verdicts retained as review history.

**Where:** [ats_result.py](src/jobagent/tools/ats_result.py), `gather`; [submit.py](src/jobagent/steps/submit.py), `ats_line`; [outcome_stats.py](src/jobagent/tools/outcome_stats.py), `snapshot`, `_status`, `load`; [ats_sites.py](src/jobagent/tools/ats_sites.py), `write_summary`; [main.py](src/jobagent/main.py), `ats_record_cmd`.

**Issue and reason:** Review selects only the highest ATS round, but a final round can contain budget-limited sites while unlimited sites were scored earlier. An isolated example reported 66.7 from three sites, although all five same-version scores averaged 70.0. Learning snapshots similarly select a latest round without establishing that its rows describe the submitted PDF. They derive a resume hash from score history rather than independently hashing the submitted artifact.

**Correction:** Use one authoritative versioned ATS result everywhere: PDF hash, JD fingerprint, required site set, individual scores, completeness and average. Gather scores by exact version identity, not round alone. Freeze actual submitted artifact hashes independently of ATS availability. Backfilled historical evidence should distinguish verified snapshots from reconstructed/unknown ones.

**Acceptance checks:** Scores split across rounds produce the required five-site average when they describe the same version. Scores from different PDFs/JDs cannot mix. Review, saved summary and learning snapshot agree. Missing sites preserve `average: null` rather than silently reducing the denominator.

**Resolution notes (2026-11-11, assistant; left unchecked for the owner and Codex):**

- **Reproduced first** (`tests/unit/test_ats_result.py`, 13 tests; the 4 behaviour tests run against the previous code fail): the review line averaged only the highest round's rows (73.3 from three sites where all five same-version scores average 70.0), and the learning snapshot took the latest round and a hash from score history.
- **Change.**
  - `tools/ats_result.py` is the one authoritative result: it gathers the required sites' scores from `ats_runs` by exact version, the SHA-256 of the current `resume.pdf` and the job fingerprint (JD text, title, company), whatever the round; the latest matching row per site wins; a hand-entered score for this PDF counts; rows for another PDF, another posting, or without a job identity and not manual are excluded and counted. Any missing required site leaves `average: null`; the denominator is never reduced.
  - Review: `submit.ats_line` reports that result (average over the five required sites, or "average unavailable (null)" with the missing sites). Saved summary: `ats_sites.write_summary` merges the scores just obtained with the same-version history and records each score's provenance. Learning snapshot: `outcome_stats.snapshot` freezes the SHA-256 of `resume.pdf` and `cover_letter.pdf` and the job fingerprint themselves (so they exist even when no site was reachable), the ATS scores and average from the same result (null unless complete), and `snapshot_status`: `verified` when the hash matches the bytes the browser attached (the F04 attempt payload), `mismatch` when the PDF on disk is not among them, `reconstructed` when there is no payload (older rows), `unknown` without a PDF. A new attempt clears the old snapshot; a later backfill never overwrites a frozen one. New columns on `applications` (migrated): `resume_pdf_sha256`, `cover_letter_sha256`, `jd_fingerprint`, `snapshot_status`, `ats_sites`; `resume_sha` is now the PDF's own hash.
- **Acceptance checks.** Scores split across rounds give the 70.0 five-site average for the same version; review line, saved summary and snapshot agree (also on the PDF hash); scores of another PDF or another posting cannot mix (three excluded rows in the test); a missing site keeps the average null in all three places; hashes are frozen with no ATS rows; verified/mismatch/reconstructed/unknown are distinguished. Full unit suite on the whole working tree: 717 tests; 716 passed in the run, and the one failure, `test_snapshot_freezes_the_resume_scores_role_and_skills`, encoded the old behaviour (hash from score history, average of two sites) and was rewritten to the new contract; it passes with the related files.
- **Limits.** (1) Rows recorded before this change without a job fingerprint and not entered by hand are excluded from the average, so an old application may show a null average where the earlier code showed a partial one. (2) `snapshot_status` of rows snapshotted before this change stays empty (legacy, derived from score history). (3) The backfill in the sync only covers rows whose `resume_sha` is empty.

**Earlier independent verification (2026-11-11, Codex; see the follow-up and latest verification below): PARTIALLY FIXED — F06 remained unchecked.**

- All **13 authoritative-result tests** passed in the shared full-unit run under F04. They verify the five-site same-version average across rounds, exclusion of ordinary wrong-PDF/wrong-JD rows, and a null average when a required site is missing. Independent checks found the following remaining gaps.

**1. Learning snapshots still use the disk document rather than the actual submitted document.**

- **Reproduction:** the frozen attempt identified attached resume B, while the job folder held resume A. Recorded five same-job scores of 90 for A and five scores of 40 for B. The snapshot stored **A's hash and average 90**, merely marking it `mismatch`; the learning loader still exposed average 90. B's correct submitted-version identity and available score set were not frozen instead. `snapshot_status` is not used by the learning calculations to exclude this known mismatch.
- A second check put B in the frozen attempt's resume control and A only in its cover-letter control, while disk `resume.pdf` was A. `_status` reported **`verified`** because it searches the set of hashes from all attachments, without associating the hash with the resume control. The stored resume hash still did not match the actual submitted resume.
- **Required correction:** derive submitted resume and cover-letter identities from their specific frozen-attempt attachment associations. Use those exact identities, with the frozen job identity, when selecting scores. Current disk hashes can be diagnostic metadata, but must not be labeled as the submitted version merely because a different attachment contains those bytes. When attachment role/content is unverifiable, preserve explicit uncertainty and avoid attributing another version's scores to it or using known-mismatched scores as submitted-version learning evidence.
- **Regressions:** disk A versus submitted B, with distinct available score sets; and disk A appearing only in the cover-letter slot while the resume slot holds B. Assert the correct submitted hashes and corresponding ATS result, or an explicit unknown/null result where role/hash verification is unavailable. A different attachment must not falsely verify the resume.

**2. Per-column backfill can create a falsely verified mixture of two versions.**

- **Reproduction:** froze resume A with a matching attempt payload, four site scores of 90, `ats_average=None`, and `snapshot_status='verified'`. Then changed disk `resume.pdf` to B, recorded all five B scores as 20, and called `snapshot` again. The database retained **A's hash and `verified` status**, retained the **four A scores of 90**, but filled the average with **B's 20**. `COALESCE` protected individual non-null cells while allowing null cells to be filled from an unrelated current version.
- **Required correction:** freeze or update a coherent snapshot as one versioned unit. Existing snapshot identity must govern any deliberate backfill; never recompute missing cells from the folder's current PDF/JD and independently merge them into an older snapshot. Keep hashes, site set, scores, completeness, average, status, and provenance consistent. Return the persisted snapshot rather than a different newly computed version if the update was intentionally refused.
- **Regressions:** partial A snapshot followed by complete B scores and a second snapshot/confirmation/backfill; the stored row must remain a coherent A result, with null average if A is still incomplete. Also cover a legitimate same-version completion if that is supported, and freeze review/summary/learning agreement for that version. Do not rely on checking only that the old hash/status stayed unchanged.

**3. Manual scores bypass the job-fingerprint requirement.**

- **Reproduction:** used the real `ats-record` CLI to record the fifth required site's score for posting A. It stored `manual=True` without a job fingerprint. Changed the folder's JD text to posting B, kept identical PDF bytes, and recorded the other four sites for B. `gather` accepted the old manual A score and reported **complete=True, average=90, missing=[]** for B. The predicate `manual or own_job` explicitly bypasses exact job identity for manual rows.
- **Required correction:** record PDF and job/JD identity together for manual scores and enforce both on retrieval, just as for automated scores. Preserve any genuinely job-independent scorer policy explicitly per scorer rather than treating all manual scores as job-independent. Legacy unidentified manual scores need an explicit unverified/re-record policy; they cannot silently complete an exact-version five-site result. An explicit conflicting fingerprint must never be overridden by `manual=True`.
- **Regression:** record a manual score through the CLI, change the JD while keeping the PDF, and verify that the old score does not complete the new result. A newly recorded manual score for the current JD should complete it. Include manual rows with an explicitly different fingerprint.

- These four data-association reproductions used temporary SQLite databases, synthetic documents/scores, constructed frozen-attempt payloads in the format F04 writes, and the real snapshot/gather/loader/ATS-record logic. No external score provider, mailbox, or application was contacted. **F06 remains unchecked.** The authoritative aggregation handles normal histories, but submitted-version identity, immutable coherent snapshots, and manual-score job identity still fail the acceptance contract. F25's broader job-ID/persistence design and F23's statistical model remain separate findings.

**Follow-up to the independent verification (2026-11-11, assistant; F06 left unchecked for the owner and Codex):**

- **Agreed and reproduced** (`tests/unit/test_ats_result.py`, now 20 tests; the 7 new behaviour tests fail on the previous code).
- **1. Snapshot used the disk file, not the submitted one.** The frozen attempt payload (F04) now records the submitted identity derived from the upload controls themselves: `ats_result.attachment_roles` assigns each attachment to the resume or cover-letter role by its own control label (a file in the cover-letter slot never counts for the resume; a role is only a hash if every control of that role was readable and held the same bytes), plus the job fingerprint, and it is part of the approval fingerprint. `outcome_stats.snapshot` takes the resume and cover-letter hashes and the job fingerprint from there and selects the ATS scores for exactly that hash and fingerprint (`ats_result.gather(..., sha=, fingerprint=)`), whatever the folder holds now; the disk hashes are kept only as a diagnostic in `snapshot_detail` (`disk_matches_submitted`). Statuses: `verified`, `unverifiable` (the resume control could not be read or was ambiguous: no hash, no scores attributed), `reconstructed` (no payload: older rows), `unknown` (no PDF). The old `mismatch` status is gone, since the submitted identity no longer depends on the disk file. Tests: disk A vs submitted B with distinct score sets (B's hash and B's average), A only in the cover-letter slot, an unreadable resume control.
- **2. Per-column backfill mixed two versions.** A snapshot is now written once as one unit, never merged cell by cell (no per-column `COALESCE`). If it exists, `snapshot` returns it as persisted; the only update is completing its OWN version's scores (same hash, same job fingerprint) as a whole (scores, average and site set together). A new attempt clears the whole unit. Rows with no snapshot status (before F06) are re-derived once as `reconstructed`. Test: a partial A snapshot, then a re-tailored B with five scores and a confirmation re-snapshot leave the row exactly A's (null average, four A scores); then A's own fifth score completes A.
- **3. Manual scores bypassed the job fingerprint.** `ats_sites.record` now stores the job fingerprint on every row, manual ones included, and `gather` and `manual_scores` require it for every row: a manual score for posting A no longer completes posting B's result, a conflicting fingerprint never counts, and a legacy manual score without one is listed as `unidentified` and the review line says to record it again with `jobagent ats-record`. Tested through the real `ats-record` CLI (A recorded, folder re-pointed at B with the same PDF bytes, other four sites recorded for B: incomplete; re-recorded for B: complete).
- **Also fixed.** `ats_sites.cached` ignored rows carrying the new top-level fingerprint key; it now builds the cached score from the score's own fields only.
- **Checks.** `test_ats_result.py` 20 passed; full unit suite on the whole working tree: 737 passed.
- **Limits.** A submission recorded before this change has no payload identity, so its snapshot is `reconstructed` from the disk files. The learning code keeps using `ats_average` as before; it does not yet filter by `snapshot_status` (F23 covers the learning model).

**Earlier independent verification (2026-11-11, Codex; superseded by the second follow-up and final verification below): STILL OPEN — two consumers still used unbound document evidence.**

- All **20 authoritative-result tests** passed. Independent checks also confirmed that submitted B's hash and average 40 are selected despite disk A and its average 90, the cover-letter slot does not stand in for the resume, partial A remains coherent after complete B scores, and a manually recorded score for posting A cannot complete posting B. These earlier failures are fixed.

**1. Document refresh bypasses the authoritative versioned ATS result.**

- **Where:** `tools/documents.py::refresh`, its cached-summary/old-manifest handling, and the review's Documents section.
- **Reproduction:** created a coherent five-site ATS summary for valid PDF A at average 93, with its PDF hash and job fingerprint. Changed `resume.pdf` to a different, still-valid one-page PDF B without rescoring. Confirmed `ats_result.gather(B)` reported **`average=None`**. With the hard/claims validators passing, `documents.refresh(..., policy='block', target=90)` nevertheless read the old summary and wrote a new manifest with **B's PDF hash, average 93, and `status='met'`**. The block-policy application gate accepted it. The summary's A hash and the new manifest's B hash were independently compared.
- **Cause:** refresh reads `ats_summary.json` values, or falls back to old manifest values, without validating the resume hash and job fingerprint. The newly correct `gather` API is not used by this consumer. Thus the review Documents section can claim `met` for a version whose authoritative ATS line says no current-version scores.
- **Required correction:** resolve refresh's ATS data through the same authoritative exact-version identity as review/summary/snapshot, including required site set and completeness. Reject or regenerate an identity-mismatched cached summary; never relabel A's average as B's. If retaining historical summaries, keep their identity explicit and separate from the current manifest/result.
- **Regressions:** PDF change and JD change independently invalidate an old complete summary, through both document-refresh APIs and the real CLI. Missing sites must keep the current average null and must respect warn/block policy. A same-version rescore should restore agreement across the manifest, review, saved current summary, and snapshot. This also resolves the score-policy gate failure noted under F05.

**2. An unverifiable submitted resume still supplies unrelated disk-file skills to learning.**

- **Where:** `tools/outcome_stats.py::snapshot`, `load`; downstream keyword-feature consumption in `tools/learning.py`.
- **Reproduction:** used a frozen attempt whose resume bytes were unreadable, giving `snapshot_status='unverifiable'`, `resume_sha=None`, and `ats_average=None`. Placed `disk_only_skill` in the current disk `filled.json`. Snapshot stored **`keywords=['disk_only_skill']`**, and `outcome_stats.load` supplied that feature to learning despite having no identity connecting the submitted resume to this disk file.
- **Cause:** the keyword guard explicitly permits `resume_sha is None`, in addition to a matching disk hash. The new code correctly withholds unidentified ATS scores, but the same unknown resume identity still admits document-derived keyword features. The learning code does not exclude these features based on snapshot status.
- **Required correction:** only attribute disk-derived resume features when the submitted identity is verified against that disk version, or under an explicit, labeled legacy reconstruction policy. An unverifiable current submission should have unknown/empty document features unless another frozen version-bound source establishes them. If retaining disk features diagnostically, store them separately with provenance and keep them out of submitted-resume learning by default. This is source-identity handling, not F23's statistical estimator design; valid application outcome/timestamp data need not be discarded.
- **Regression:** an unreadable/custom-widget resume with no verified hash must not acquire current-disk keywords in the submitted-resume feature set. Matching verified artifacts should still supply their own keywords; a known different disk version must not. Cover the actual loader/learning feature input, not only ATS average and hash columns.

**Shared verification evidence for this follow-up:**

```bash
pytest --basetemp=/dev/shm/f05-f06-followup-93o8Jh -q tests/unit
```

- Ran against an isolated copy of the current source/unit-test tree and required example fixtures in Docker, with `PYTHONPATH=src`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and `TMPDIR=/dev/shm`. **737 passed**, 13 dependency deprecation warnings, no skips or failures. This is a fresh full-unit-suite pass; no tests were rerun after it. The independent failure cases above were executed in memory and are not covered by that passing suite.
- The lifecycle test used a stubbed ATS/model failure after writing a fixture draft; the refresh tests stubbed heavy validators to isolate their pass/fail state. ATS data tests used synthetic PDFs/scores and temporary SQLite databases, with the real ATS-record CLI, gather/snapshot/loader logic, and document gate. No real application, mailbox, or external ATS site was used. All **126 Python source/unit-test files** still matched the tested snapshot before updating the document.
- **F05 and F06 remain unchecked; F01–F04 remain checked.** Only this review document was edited; application source/tests and the live container's `/app/src` were not changed by verification.

**Second follow-up to the independent verification (2026-11-11, assistant; F06 left unchecked for the owner and Codex):**

- **Agreed and reproduced** (`tests/unit/test_ats_result.py`, now 24 tests; the new keyword test fails on the previous code).
- **1. Document refresh bypassed the authoritative ATS result.** Fixed with F05's second item: `documents.refresh` and the manifest use `ats_result.gather` for the current PDF and job and record the identity they describe, so the review's Documents section, the saved summary, the manifest and the snapshot cannot disagree about a version. Tested for a PDF change and a job posting change (separately), through both the refresh API and the real `documents` CLI, with warn and block policy, and a same-version rescore restoring agreement.
- **2. An unverifiable resume still received disk-file skills.** `outcome_stats.snapshot` now attributes the folder's `filled.json` skills to the submitted resume only when the submitted identity is verified to equal the disk file, or under the labelled legacy policy (`reconstructed`: no attempt payload at all). An unverifiable or unreadable resume control, and a known different disk version, get none; the withheld skills are kept apart as `disk_keywords_withheld` in `snapshot_detail` and never reach the loader. Tests cover the actual `outcome_stats.load` input: unverifiable (empty), verified match (its own keywords), submitted B with disk A (empty), legacy reconstruction (disk keywords, labelled).
- **Checks.** `test_ats_result.py` 24 passed; full unit suite on the whole working tree: 748 tests, 747 passed in the run and the one failure updated and passing (see F05).
- **Limits.** The learning code still does not filter by `snapshot_status` (that is F23's estimator); it receives empty features for unverifiable rows instead.

**Final independent verification (2026-11-11, Codex): VERIFIED FIXED — F06 checked off.**

- Reviewed exact PDF/JD identity matching for automated and manual scores, the document-refresh consumer, submitted attachment-role identity, coherent snapshot updates, and the loader's resume-feature provenance. All **24 authoritative-result tests** passed in the shared full-unit run recorded under F05, along with the related score-summary, submission-attempt, and outcome/learning tests.
- The independent stale-score/rescore scenario recorded under F05 confirmed that manifest refresh uses the same authoritative current-version result. An unscored replacement PDF cannot inherit another version's average or `met` status; genuine same-version scores restore readiness under the configured policy.
- **Two independent loader/provenance checks passed:**
  - With a frozen attempt whose resume bytes were unverifiable and a disk `filled.json` containing `disk_only_skill`, snapshot retained `resume_sha=None`, `snapshot_status='unverifiable'`, and no submitted-resume keywords. The actual `outcome_stats.load` result supplied an empty keyword set to learning. Disk keywords remained only in the diagnostic `disk_keywords_withheld` field.
  - With a verified submitted resume matching the disk PDF, the loader retained that resume's own keywords. Thus the correction withholds unbound features without discarding supported data.
- Earlier regressions also pass for five-site cross-round averages, null averages when incomplete, submitted B versus disk A, role-specific attachment hashes, coherent same-version backfill, and manual scores tied to the exact JD. Opaque/unverifiable attachments remain explicitly unknown; labeled legacy reconstruction remains a separate policy rather than a claim of verified submission identity. F23's estimator design and F25's broader persistence design remain separate open findings.
- The independent checks used synthetic fixture documents/metadata, temporary SQLite state, real snapshot/gather/gate/loader logic, and no live application, mailbox, or ATS site. All **126 Python source/unit-test files** still matched the tested snapshot before updating this checklist. **F05 and F06 are checked off; F01–F06 are now checked.** Only this review document was edited; application source/tests and the live container's `/app/src` were not changed by verification.

## F07

**High — Desktop scope can trust the wrong existing tab and unrelated frame origins.**

**Current status: verified fixed.** The final independent verification below confirms the proxy-restart lifecycle correction and supersedes the earlier open verdicts retained as history.

**Where:** [browser.py](src/jobagent/tools/browser.py), `application_context`; [desktop_browser.py](src/jobagent/tools/desktop_browser.py), `Scope`, `serve`, `allowed`.

**Issue and reason:** The client accepts the sole public HTTPS tab and Scope automatically trusts its current origin. That does not establish that a reused tab still belongs to the requested job. An isolated check requested a careers URL while the existing tab pointed to an unrelated public site; scope accepted it. Every embedded frame origin is also promoted into the main navigation allowlist, including frames unrelated to applying. The public-URL check inspects URL text, not resolved network destinations.

**Correction:** Bind browser leases to application tasks and browser targets. Distinguish fresh initial redirects from reused sessions and persist approved destinations. Separate frame permissions from top-level navigation permissions. Do not authorize arbitrary embedded content as an application destination. If public-only access is intended as a network boundary, enforce resolved-IP and redirect checks too. Preserve employer-independent support.

**Acceptance checks:** A reused unrelated tab is rejected before planning or filling. Legitimate application redirects and embedded ATS forms still work. An unrelated frame cannot grant top-level navigation permission. Explicit trusted login origins remain supported without a Carvana-only/vendor-only restriction.

**Resolution notes (2026-11-11, assistant; left unchecked for the owner and Codex):**

- **Reproduced first** (`tests/unit/test_generic_desktop.py`): on the previous code a reused tab on an unrelated public site was accepted because `Scope` trusted the tab's current origin, and every embedded frame's origin (and every link found in a frame) was promoted into the top-level navigation allowlist. The new tests fail there (some by a missing API).
- **Change** (`tools/desktop_browser.py`, `tools/browser.py`): the lease is bound to the task and the browser target. The host broker now reports whether the session is `fresh`. A fresh session may land on the posting's ordinary initial redirect (the one time the tab's origin is approved without being requested); a reused session gets no grace: its tab must already be on an approved origin (the requested URL, explicit `--desktop-allow-origin` origins, destinations approved earlier for this task and persisted in `approved.json` beside the session) and, when a target id was recorded, be the same CDP target, or `Blocked` is raised before anything is planned or filled. Frames never grant navigation: an embedded form's origin is recorded as a frame origin and tolerated if it is a public destination, but only application links published on the TOP-LEVEL page extend the navigation origins. "Public" is enforced on the resolved addresses too (`resolves_public`): a public-looking name resolving to a private, loopback or link-local address is refused (page, links, frames, requested URL); a name that cannot be resolved here falls back to the textual rules, since the browser's resolver may differ. No employer or vendor list was added: unseen employers and ATS vendors, and explicit trusted login origins, still work.
- **Checks.** Unrelated reused tab rejected; fresh initial redirect approved and remembered on resume; a lease for another task does not approve this task's tab; a different browser target is refused; explicit login origins and unseen vendors work when reused; a frame cannot grant top-level navigation and does not publish links; private resolution refused; unresolvable names fall to the textual rules. Full unit suite on the whole working tree: 765 passed (with F08 and F09).
- **Limits.** DNS can change between the check and the browser's own lookup (rebinding is not solved by checking at attach and policy time); an old broker that does not report `fresh` is treated as reused, which is the stricter reading; redirects after the check are caught at the next policy call, not at request time.

**Earlier independent verification (2026-11-11, Codex; see the latest follow-up below): PARTIALLY FIXED — F07 remained unchecked.**

- **Confirmed improvements:** reused tabs on a different unapproved origin are refused; fresh posting redirects and explicitly trusted login origins remain supported; approvals are persisted per requested URL; embedded-frame origins and links no longer become top-level navigation permissions. The added unit regressions cover these cases without an employer/vendor allowlist.
- **Shared F07–F09 validation:** copied current source, unit tests, fixtures and required example data into `/tmp/f07-f09-verification.7euqGt` in the running Docker container. Ran the full unit suite against that isolated source: **765 passed, 13 dependency deprecation warnings in 238.13 seconds**. The passing suite includes `test_generic_desktop.py` and `test_inbox_privacy.py`; the failures documented below were additional inline independent reproductions, not failures reported by those existing tests. All **127 Python source/unit-test files** matched the tested snapshot. Application source and tests were not edited, and neither live applications nor the real mailbox were used. Command:

  ```bash
  docker exec -w /tmp/f07-f09-verification.7euqGt \
    -e PYTHONPATH=/tmp/f07-f09-verification.7euqGt/src \
    -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 -e PYTHONDONTWRITEBYTECODE=1 \
    -e TMPDIR=/dev/shm -e STATE_DIR=/dev/shm/f07-f09-default-state-7euqGt \
    -e OUTPUTS_DIR=/dev/shm/f07-f09-default-outputs-7euqGt \
    -e DATA_DIR=/tmp/f07-f09-verification.7euqGt/data \
    jobagent pytest --basetemp=/dev/shm/f07-f09-verification-7euqGt -q tests/unit
  ```

**1. The public-destination boundary detects a redirect after the destination has already been requested.**

- **Reproduction:** used a separate headless Chrome with every request intercepted and fulfilled locally. The synthetic careers page published an Apply link to a public application origin. That destination returned a 302 to `https://private.synthetic.example/landing`; the injected resolver consistently mapped that hostname to `10.1.2.3`. After the initial `Scope(page)` approved the Apply link, clicking it produced requests for the careers page, the public application URL, **and the private destination**, in that order. Only the next policy call raised `Blocked`; the page had already reached the private URL. No external or private-network traffic was actually sent in this test.
- **Reason:** `Scope` is a checkpoint, not a request guard. `launch` also starts Chrome on the requested URL after only `allowed`'s textual check, before the client constructs `Scope`. Checking the resolved page URL after navigation does not enforce the documented public-only network boundary. This is a deterministic redirect gap, separate from the already disclosed DNS-rebinding race.
- **Required correction:** enforce the chosen destination policy before the host's initial navigation and before requests along redirect chains, including embedded navigation where the same public-only promise applies. Preserve legitimate public employer-to-ATS redirects. Use a browser/network guard that actually covers the initial launch and redirects; adding another post-navigation `Scope` call cannot prevent these requests. Define and test how DNS failure is handled instead of treating the current fail-open lookup as a strict public-only guarantee.
- **Regression:** a public Apply link redirecting to a consistently private-resolving hostname must be stopped before that destination's intercepted request is delivered. Cover the initial broker navigation too, and retain positive public redirect and embedded ATS cases.

**2. A failed current-target lookup silently bypasses a persisted browser-target binding.**

- **Reproduction:** persisted a lease with target `T1`, then supplied a replacement page on the same allowed origin with `target_id=None`, the value `application_context` uses when its CDP target query fails. `Scope(..., fresh=False)` and its subsequent policy call both accepted the replacement and retained `T1` as `scope.target_id`, despite never verifying the current target. This was reproduced with isolated page/context stubs and a temporary lease file.
- **Reason:** the mismatch condition only runs when **both** the saved and supplied target IDs are truthy. The exception handler in `application_context` therefore turns inability to establish identity into acceptance; reusing the saved ID is not evidence that it is the current tab.
- **Required correction:** require a successfully obtained current browser-target identity before accepting a lease that promises target binding. When identity cannot be established, stop or explicitly establish a new verified session; do not silently reuse the saved ID as the current one.
- **Regressions:** a recorded `T1` plus a missing/failed current CDP target query must be refused before planning/filling, as must a different target `T2`. A positively verified `T1` must still resume normally.

**Earlier follow-up independent verification (2026-11-12, Codex; superseded by final verification below): PREVIOUS COUNTEREXAMPLES FIXED; PROXY REUSE GAP REMAINED — F07 stayed unchecked.**

- **Verified corrections:** `serve`/`launch` validate the initial public destination and start Chrome through `PublicProxy`, including `--proxy-bypass-list=<-loopback>`. The proxy vets all resolved addresses, fails closed on DNS failure, and connects to the IP it vetted. The new real-Chrome/local-TLS regression passed: a public Apply redirect to a private-resolving hostname never reaches its upstream server, a private initial navigation is refused, and a public-to-public redirect works. A separate stream-safe socket reproduction also delivered a public tunnel's payload and refused private, unresolved and loopback destinations before calling the connector. Browser scope remains generic; frame origins do not grant navigation. Independently repeated the persisted-target cases: `None`, empty ID and `T2` are refused for recorded `T1`; verified `T1` resumes. `application_context` now stops on a failed CDP identity lookup and rejects unguarded legacy sessions.
- **Shared F07–F09 validation:** the source snapshot `/tmp/f07-f09-followup.D78nqZ` in Docker produced **797 passed, 2 failed, 13 dependency deprecation warnings in 248.25 seconds**. Both failed tests passed together on one isolated rerun (**2 passed in 4.47 seconds**). The first failure was `test_generic_desktop.py::test_the_proxy_tunnels_to_a_vetted_public_address_and_refuses_everything_else`: its first `recv(4096)` already contained both the CONNECT response and `hello from upstream`, so the second `recv` returned EOF and the test incorrectly failed. The other was the previously intermittent `test_scrape_parallel.py::test_scrape_many_runs_in_parallel_and_skips_cached`, which lost one fake scrape result in the full run. This is **not** a clean full-suite pass. All remaining new F07–F09 tests passed. All **128 Python source/unit-test files and the vendored public-suffix data file** matched the tested snapshot. Tests and independent reproductions used temporary state, synthetic pages/local servers and fake mailboxes; no real application or mailbox operation occurred. Only this review document was edited. Full-suite command:

  ```bash
  docker exec -w /tmp/f07-f09-followup.D78nqZ \
    -e PYTHONPATH=/tmp/f07-f09-followup.D78nqZ/src \
    -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 -e PYTHONDONTWRITEBYTECODE=1 \
    -e TMPDIR=/dev/shm -e STATE_DIR=/dev/shm/f07-f09-followup-state-D78nqZ \
    -e OUTPUTS_DIR=/dev/shm/f07-f09-followup-outputs-D78nqZ \
    -e DATA_DIR=/tmp/f07-f09-followup.D78nqZ/data \
    jobagent pytest --basetemp=/dev/shm/f07-f09-followup-D78nqZ -q tests/unit
  ```

**Remaining correction: bind browser reuse to the currently active proxy, not a historical manifest flag.**

- **Reproduction:** exercised the real `serve` request handler with an isolated temporary session manifest and injected process validation, without touching the live desktop bridge. Simulated the old bridge's proxy closing while its Chrome remained alive, then started a new proxy. The browser's saved `proxy_port` was **35675**; the new active proxy was **42945**; the old port had no listener. An `open` request returned **`ok=True, fresh=False, guarded=True`** for that browser. The broker neither relaunched it nor refused the obsolete proxy binding. Ports shown are illustrative ephemeral values from the reproduction.
- **Reason and effect:** every `serve` invocation allocates a new random proxy port. An already-running Chrome retains its original `--proxy-server` argument, while the reuse path computes `guarded` from `bool(manifest.get('proxy_port'))` alone. It does not establish that the browser uses this broker's live guard. After a bridge restart, future browser connections can fail against the retired port even though the client accepts the lease as guarded. Reusing saved origin/target approvals does not repair the network configuration.
- **Required correction:** define the proxy/browser lifetime together. Either keep an owned, verified proxy endpoint stable across supported bridge restarts, or reject/recreate sessions whose actual configured proxy does not belong to the current live guard. Validate the proxy configuration as part of process/session identity and report a specific restart requirement rather than returning `guarded=True` for a retired endpoint. Preserve per-task target and origin checks when deliberately creating a fresh session.
- **Regressions:** create a guarded session, stop/restart the broker while its browser remains alive, and request the same task. Assert either working public navigation through the current guard with private destinations still refused, or an explicit refusal before planning/filling. A current guard must still permit normal reuse. Also fix the CONNECT test to parse the response header delimiter and preserve any payload already received; TCP does not preserve the test's assumed read boundaries.

**Follow-up to the independent verification (2026-11-12, assistant; F07 left unchecked for the owner and Codex):**

- **Agreed and reproduced** (`tests/unit/test_generic_desktop.py`; the new tests fail on the previous code): `serve` computed `guarded` from `bool(manifest.get('proxy_port'))` alone, and every `serve` run took a random proxy port, so a Chrome that survived a bridge restart kept its old `--proxy-server` while the broker reported it guarded.
- **Change** (`tools/desktop_browser.py`). The proxy and the browser now have one lifetime:
  - `start_proxy(root)` reuses the proxy port stored in `proxy.json` when it is free, so a supported bridge restart keeps running browsers pointed at a live guard; if the old port cannot be had it takes a new one.
  - `open_request` (the broker's reply, now a function the handler calls) reuses a running browser only when the proxy port it was started with IS the live proxy's, and `validate_process` also requires the Chrome command line to carry `--proxy-server=http://127.0.0.1:<that port>` and `--proxy-bypass-list=<-loopback>` (`proxy_arguments_ok`). A browser bound to a retired proxy, or started before the proxy existed, is refused with the specific restart requirement ("uses a retired network guard ... close that Chrome window and open the application again"), never reported as guarded. Per-task target and origin checks are unchanged and still apply to a deliberately fresh session.
  - The CONNECT test reads to the header's blank-line delimiter and keeps any payload that arrived with it (`connect` returns the socket, the header and the bytes already read).
- **Checks.** A guarded session is reused normally with the current proxy; a browser bound to another port (and a legacy one with no proxy) is refused; a bridge restart keeps the same port, the reused browser is reported guarded and the restarted proxy still refuses a private destination; with the old port taken by someone else the old browser is refused; the process identity needs the matching proxy flags. Full unit suite on the whole working tree: 813 passed.
- **Limits.** If the old port is occupied at restart, running browsers must be reopened (the refusal says so); WebSocket and every other connection ride the same CONNECT tunnel, so nothing is exempt, but the proxy cannot inspect TLS content, only the destination.

**Final independent verification after the proxy-lifecycle follow-up (2026-11-12, Codex): VERIFIED FIXED — F07 checked off.**

- **Source verification:** `start_proxy` persists its endpoint in `proxy.json` and rebinds the same port on a supported bridge restart. `open_request`, which the real broker handler calls, requires a reused browser's saved proxy endpoint to match the current live guard. `validate_process` additionally checks the actual Chrome command line for that proxy and `--proxy-bypass-list=<-loopback>`; mismatched or legacy browsers are refused. A port conflict creates a new guard and produces explicit instructions to close/reopen the old browser rather than labeling it guarded.
- **Independent restart reproduction now passes:** used temporary broker/session metadata, real local proxy sockets and an isolated local echo upstream, with only browser process existence/identity injected. Public traffic was delivered before and after stopping/restarting the guard; the restarted proxy reused the saved port, and the broker accepted reuse with `fresh=False, guarded=True`. A private-resolving destination returned **403 before the upstream connector ran**. No live desktop bridge, real Chrome session or external network destination was changed by this reproduction.
- **Independent occupied-port reproduction now passes:** after closing the guard, bound another local listener to its old endpoint. `start_proxy` chose a different port, and `open_request` refused the old browser with **`uses a retired network guard ... close that Chrome window and open the application again`**. It no longer returned a guarded lease for the retired endpoint.
- **Retained acceptance checks:** the tests continue to cover refusal of an unrelated reused tab, per-task persisted origins and verified browser targets, missing/current-target lookup failures, separation of frame permissions from top-level navigation, explicit login origins, unseen employers/vendors, private/unresolved destinations, and a real-Chrome/local-TLS public redirect that cannot contact a private destination. Legitimate public redirects still work. The CONNECT test now preserves payload bytes received with the response headers rather than relying on separate TCP reads.
- **Scope of closure:** the task/tab, frame-origin, public-destination and proxy-lifecycle counterexamples are addressed. If another process occupies the saved proxy port, reopening affected application browsers is an explicit supported requirement; silently using a retired proxy is no longer supported. No employer-specific allowlist was introduced. Shared test results and source-alignment evidence for this verification are recorded below under F08.

## F08

**High — Shared-inbox privacy and ownership guarantees are not enforced.**

**Current status: verified fixed.** Final independent verification below confirms the successful-use, candidate ownership, necessary-text retrieval and recovery-path header-filter corrections. It supersedes the earlier open verdicts retained as history. F11's remaining cross-epoch content-identity issue is tracked separately; this closure establishes the documented plausible-candidate privacy contract, not an absolute promise that shared-inbox headers can identify every unrelated message.

**Where:** [inbox.py](src/jobagent/tools/inbox.py), `is_agent_mail`, `wait_for_verification`, `fetch`, `sweep_stale_verification`; [submit.py](src/jobagent/steps/submit.py), `wait_for_confirmation`; [outcomes.py](src/jobagent/tools/outcomes.py), `sync`; [verification.py](src/jobagent/tools/verification.py), `outcome`; [portal_auth.py](src/jobagent/tools/portal_auth.py), `_confirm_email`; [portal_agent.py](src/jobagent/tools/portal_agent.py), `run`.

**Issue and reason:** A previously used domain is treated as proof of agent ownership. That cannot distinguish the user's personal verification mail from the agent's mail at the same service. Confirmation/verification waits search recent mail and download complete messages before filtering. Outcome FROM filters still admit unrelated mail from shared senders. Full-message fetches also retrieve attachments that the body parser later ignores. IMAP FROM searches use substring matching, not exact ownership matching.

**Correction:** Separate ownership, validated sender identity and application/action matching. Fetch headers first, then only necessary text parts for plausible candidates. Associate verification mail with a specific action or account, especially before cleanup. A strict promise never to read unrelated mail needs stronger routing/ownership metadata; sender-domain membership alone cannot provide it. Document the achievable privacy contract honestly.

**Acceptance checks:** Personal and agent codes from the same vendor are distinguished. Unrelated candidate messages are rejected before unnecessary body/attachment retrieval where possible. Destructive cleanup requires action-specific ownership. Fake IMAP tests verify actual FETCH requests, not just search strings.

**Reference:** [IMAP specification](https://www.rfc-editor.org/rfc/rfc9051.html).

**Resolution notes (2026-11-11, assistant; left unchecked for the owner and Codex):**

- **Reproduced first** (`tests/unit/test_inbox_privacy.py`, a strict fake IMAP that records every FETCH and raises on a whole-message fetch, RFC822 or an attachment part): on the previous code a used domain was proof of ownership, every candidate was downloaded whole before filtering, and the IMAP FROM substring match was the only sender test.
- **The achievable privacy contract** (also in `inbox.py`): the mailbox is shared and read through server-side filters (sender, date); every candidate is judged on its HEADERS first, and only a plausible one has its text retrieved, never an attachment. A used domain is not ownership: the user's own code from the same vendor looks identical. Ownership is action-specific: `agent_actions` records each thing the agent did that expects mail (site, the exact address used, time window, the consumed Message-ID). A mail is the agent's only if an action consumed it, it was delivered to an action's plus-addressed address (`name+tag@...`, a routing key the user's own mail never carries), or it is the agent's tagged self-test. Where the agent used the plain address, a same-vendor mail in the same window can be read to find the agent's code but is never deleted unless that action consumed it.
- **Change.** `fetch_headers` (`BODY.PEEK[HEADER.FIELDS (...)]`), `fetch_text` (`BODYSTRUCTURE`, then only the non-attachment text/plain and text/html parts, each capped at 64 KB, decoded by their encoding and charset), `fetch` = both. `wait_for` takes a `header_filter`; `wait_for_verification` records an action and reads a candidate only if its headers show the site's exact sender domain (not a substring), a date inside the action's window and, for a plus-addressed action, that exact recipient; the matching mail is recorded as consumed. `is_agent_mail`/`consume_and_trash` require that ownership. `sweep_stale_verification` no longer sweeps vendor domains: it looks only at plus-addressed unconsumed actions and the self-test tag, checks headers, and reads a body only for provable agent mail. `submit.wait_for_confirmation` and `outcomes.sync` use headers first (`ApplicationMatch.plausible`: a shared ATS sender must name the company in its display name or subject before its body is retrieved).
- **Checks.** Personal and agent codes from the same vendor are distinguished and the personal body/structure is never requested; lookalike sender domains rejected despite the server's substring match, with no body fetch; out-of-window mail unread; cleanup refuses unowned same-vendor mail and trashes only the consumed one; the sweep leaves the personal mail alone and reads only the owned one; attachments never requested (asserted on the FETCH log), parts capped and decoded; nested structures parsed; confirmation candidates for another company or a forged sender are rejected on headers (body fetched only for the match). Existing inbox, cleanup, outcome and submit tests were updated to the new ownership contract. Full unit suite on the whole working tree: 765 passed.
- **Limits.** The portal and GitHub flows still enter the vault address (no plus-address) unless a caller passes `address`; for those the sweep deletes nothing and only consumed mail is trashed. A shared-ATS mail that names the company only in its body (not display name or subject) is not read by the outcome sync or the confirmation wait: privacy over recall. The server-side FROM/TO/SUBJECT searches are substring prefilters only.

**Earlier independent verification (2026-11-11, Codex; see the latest follow-up below): PARTIALLY FIXED — F08 remained unchecked.**

- **Validation:** the shared full-suite run recorded under F07 passed all **765 tests**. Additional inline fake-mailbox reproductions below used the tested source, temporary SQLite databases and FETCH logs; existing passing tests do not cover these counterexamples.
- **Confirmed improvements:** messages are fetched as headers first; sender-domain membership is checked after IMAP substring prefilters; confirmation/outcome candidates have a header filter; simple PDF/text attachments are excluded; cleanup no longer treats every previously used vendor domain as ownership. The new strict fake-IMAP tests assert actual FETCH requests. However, the following independent fake-mailbox cases still violate the stated contract. No real mailbox was accessed or changed.

**1. The supposedly exact action recipient is still matched as a substring of raw header text.**

- **Reproduction:** the action expected `agent+job42@gmail.com`. A vendor verification message had `To: "Personal agent+job42@gmail.com" <agent+personal@gmail.com>`: the expected address appears only in the display name, while the actual recipient is another alias of the shared mailbox. `wait_for_verification(..., address="agent+job42@gmail.com")` read the body, returned its personal code `111111`, marked it consumed, and `consume_and_trash` completed a MOVE of that message in the fake mailbox. `_owned_by_action` independently has the same raw substring check.
- **Required correction:** retain recipient headers as structured addresses, parse their mailbox components, and compare complete normalized addresses in both candidate filtering and ownership checks. Display names, larger addresses, and header substrings cannot establish action ownership. A server-side `TO` search may remain a prefilter, but must not substitute for the exact client-side check.
- **Regressions:** expected address only in a recipient display name, an address with extra prefix/suffix characters, a different plus alias, and a genuine exact recipient. For rejected recipients, assert no BODYSTRUCTURE/text fetch and no cleanup permission; cover stale sweep as well as the wait.

**2. Selecting a code is recorded as successful action consumption before the caller uses it.**

- **Reproduction:** two messages from the same vendor, to the plain `agent@gmail.com`, both inside the action window: UID 1 was the agent's code `222222`; newer UID 2 was the user's personal code `111111`. Calling `wait_for_verification` without an `address` override selected UID 2, immediately wrote its Message-ID into `agent_actions`, and made it eligible for successful `consume_and_trash`; the agent's UID 1 remained. No browser interaction or successful verification was needed to acquire cleanup permission.
- **Production relevance:** the current `portal_auth._confirm_email`, `portal_agent` verification path, and GitHub wait still use this plain-address calling pattern. In `portal_agent`, cleanup can happen immediately after retrieving a code, before the next model iteration even types it. Thus the personal-versus-agent distinction in the new plus-address test is not established in those production paths.
- **Required correction:** distinguish a selected candidate from a successfully correlated/used verification message. Carry the action/account/routing identity through actual callers. Where supported, use an action-specific routing address; where the shared plain-address messages cannot be distinguished, keep ambiguous mail unowned and ineligible for automatic cleanup. Merely matching a vendor, date and code regex must not manufacture the ownership evidence used to authorize deletion. Confirm successful use separately rather than setting `consumed` inside the search.
- **Regressions:** exercise real caller patterns with simultaneous personal and agent codes from the same vendor/plain address; wrong/no code field and rejected-code paths must not establish consumption or trash permission. Preserve processing of correctly correlated agent mail without expanding cleanup authority to ambiguous personal messages.

**3. Text inside a multipart attachment is still fetched.**

- **Reproduction:** supplied a valid BODYSTRUCTURE with outer `multipart/mixed`, a normal text body at part `1`, and an attached `multipart/mixed` at part `2` bearing `Content-Disposition: attachment; filename="private.mime"`, whose text child was part `2.1`. `Inbox.fetch` issued **`BODY.PEEK[2.1]<0.64000>`** and returned `Main message body\nPRIVATE ATTACHED TEXT`. The independent fake logged every FETCH; no actual attachment was read.
- **Reason:** `text_parts` descends into multipart children before checking any attachment disposition on the containing multipart. Excluding only leaf text attachments and application/pdf parts does not exclude an attached multipart subtree. The current strict fake models only flat message parts and does not catch this case.
- **Required correction:** parse multipart extension fields correctly, distinguish MIME child bodies from extension/parameter lists, and stop traversing an entire attachment subtree before selecting readable text descendants. Preserve ordinary inline multipart/alternative and nested message-body handling.
- **Regression:** a real-shaped nested BODYSTRUCTURE with an attached multipart containing text/plain and text/html must generate no FETCH for any attachment descendant; unrelated headers must still prevent any body fetch. Keep the existing flat PDF/text attachment and inline multipart tests.

**Earlier follow-up independent verification (2026-11-12, Codex; see the latest verification below): RETRIEVAL FIXES VERIFIED; SUCCESS/OWNERSHIP GAP REMAINED — F08 stayed unchecked.**

- **Verified corrections:** `_recipients` parses all relevant recipient headers into complete normalized mailbox addresses; the previous display-name-only address is refused before BODYSTRUCTURE/text retrieval and does not establish ownership. The new suffix/prefix/different-alias tests and stale-sweep regression pass. `text_parts` now separates multipart children from extension fields and skips entire attachment subtrees; the previous `2.1` reproduction returns only the main body and issues no attachment-descendant FETCH. Plain-address selection now writes `candidate_message_id` without consumption; an independent direct cleanup attempt immediately after selection raises `PermissionError`. The GitHub path defers cleanup until logged-in evidence. The shared full-suite results and source-snapshot checks are recorded under F07.

**1. `portal_auth._confirm_email` treats absence of one error regex as positive acceptance.**

- **Independent reproduction:** opened a synthetic code form in a separate headless Chrome; its submit handler only calls `event.preventDefault()`, so verification never succeeds and the same code field/prompt remain. A fake shared mailbox supplied code `111111`. Calling the real `_confirm_email` returned **True**, changed the action to **`status='consumed'`** with evidence **`vendor.com accepted it`**, and completed MOVE of UID `2`. The page still said **`Enter the verification code / Verify`** and still contained its code input. No live site or mailbox was involved.
- **Reason:** after typing the code or opening the link, the function only tests whether `_REJECTED` matches the first 4,000 body characters. A stalled form, unfinished asynchronous request, challenge, error with different wording or irrelevant landing page passes that negative check. The new `mark_used` API consequently receives a success claim that was not established by the browser.
- **Required correction:** wait for affirmative verification/account evidence associated with the current attempt and intended account, with a bounded timeout. A recognized rejection should fail promptly, but absence of a rejection is not success. If acceptance remains unproven, leave the candidate unconsumed and ineligible for cleanup. Log the actual cleanup outcome separately from whether verification succeeded.
- **Regressions:** unchanged code form after Enter, asynchronous/pending verification, alternate rejection wording, challenge/error landing page and genuinely accepted verification. Use the real caller with a fake inbox; assert both the return state and absence/presence of ownership, consumed audit fields and MOVE calls.

**2. `portal_agent.run` cleans up all selected candidates, including an unused personal code.**

- **Independent reproduction:** used the real generic portal loop with a separate synthetic browser page, fake IMAP/SQLite and a deterministic mocked model. The loop first retrieved personal code **`111111`** (UID 1), then a newly delivered agent code **`222222`** (UID 2). Only `{CODE}` for **222222** was filled/submitted; the page accepted exactly that code and displayed **`Welcome agent@gmail.com`**. The loop ended `signed_in`, then moved **both `['1', '2']`** to Trash and marked both actions consumed with the same generic signed-in evidence. UID 1 was never typed or opened.
- **Reason:** `pending_mails` contains all retrieved candidates. Once the run eventually signs in, the final loop calls `mark_used` and cleanup for **every** entry. Eventual account access does not establish that earlier candidates were used, accepted, or belonged to this action; the run may have superseded them or signed in another way.
- **Required correction:** track the specific verification candidate actually entered/opened and associate its accepted result with its own action/account. Superseded, rejected, unused and ambiguous candidates must not become owned merely because another verification or password/Google sign-in later succeeded. Do not batch-promote the whole pending list to consumption.
- **Regressions:** select two candidates and use only the second; only that proven-used candidate may gain consumption/cleanup permission. Also cover a rejected first code followed by a correct second code, and eventual password/Google success without using any selected mail. Preserve candidates that never received action-specific acceptance evidence.

**Follow-up to the independent verification (2026-11-12, assistant; F08 left unchecked for the owner and Codex):**

- **Agreed and reproduced** with the real callers (`tests/unit/test_verification_flows.py`: real Chrome pages, the real `Inbox` over the strict fake IMAP; the new tests fail on the previous code).
- **1. `_confirm_email` took the absence of one error regex for acceptance.** New `tools/verification.outcome` needs AFFIRMATIVE evidence within a bounded time: success wording (verified, welcome, account created, ...) or the agent's own address shown as signed in, no challenge or error page, and, when a code was typed, its field gone and the page no longer asking for a code (or, with no success wording, the field gone and nothing asking for more); a recognised rejection ends the wait at once; anything else when the time is up is `unproven`. `_confirm_email` marks the mail used and trashes it only on `accepted`; the cleanup outcome is logged separately from the verification result. Tests: an unchanged form after Enter, a request still pending past the wait, a challenge landing page and an unrecognised rejection wording all return False with the action still `open`, no evidence recorded, no MOVE and the mail not the agent's; a genuinely accepted code returns True, records consumption with its evidence and moves the mail; a failing MOVE reports verification success and the failed cleanup separately.
- **2. `portal_agent.run` cleaned up every selected candidate.** The loop now tracks the one candidate whose code was entered, or whose link was opened, and then seen to be ACCEPTED (`verification.outcome` right after the entry). At the end only that mail is marked used and trashed, and only if the run signed in; superseded, rejected and unused candidates never become the agent's, and signing in another way (password, Google) claims nothing. Tests drive the real loop with a scripted model on a real page: a personal code retrieved first and a newer agent code used (only UID 2 moves), a rejected first code then a correct second (only the second moves), and a sign-in that used no mail (nothing moves, no action consumed).
- **Checks.** `test_verification_flows.py` 9 passed (about 7 minutes: the real polling loops run); full unit suite on the whole working tree: 813 passed.
- **Limits.** Acceptance is judged from generic page evidence, so a site with unusual wording may be reported `unproven` and its mail left in place (kept, not deleted); that errs toward not deleting. A site that shows success wording and an unrelated signed-in view could still be misjudged.

**Earlier independent verification after the verification-helper follow-up (2026-11-12, Codex; see the latest review below): PARTIALLY FIXED — F08 remained unchecked.**

- **Shared F07/F08 verification:** tested the isolated source snapshot `/tmp/f07-f08-final.wcnhac` in Docker, with temporary state/output directories and no changes to `/app/src`. The full unit suite produced **812 passed, 1 failed, 13 dependency deprecation warnings in 258.13 seconds**. The sole failure, `test_scrape_parallel.py::test_scrape_many_runs_in_parallel_and_skips_cached`, again omitted one fake parallel scrape result; the exact failed test passed on one isolated rerun (**1 passed in 4.07 seconds**). All desktop-scope/proxy, inbox-privacy and **9 real-caller verification-flow tests** passed. This is not a clean initial full-suite pass, and those existing tests do not cover the additional independent failures below. All **130 Python source/unit-test files plus the vendored PSL data file** matched the tested snapshot. Only this review document was edited. Full-suite command:

  ```bash
  docker exec -w /tmp/f07-f08-final.wcnhac \
    -e PYTHONPATH=/tmp/f07-f08-final.wcnhac/src \
    -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 -e PYTHONDONTWRITEBYTECODE=1 \
    -e TMPDIR=/dev/shm -e STATE_DIR=/dev/shm/f07-f08-final-state-wcnhac \
    -e OUTPUTS_DIR=/dev/shm/f07-f08-final-outputs-wcnhac \
    -e DATA_DIR=/tmp/f07-f08-final.wcnhac/data \
    jobagent pytest --basetemp=/dev/shm/f07-f08-final-wcnhac -q tests/unit
  ```

- **Verified improvements:** the previous unchanged code form now returns **False**, leaves its action **open**, records no consumed Message-ID/evidence and makes no MOVE. The generic portal loop now associates acceptance with the candidate actually entered/opened instead of promoting every selected candidate; the earlier unused/superseded-mail regressions are covered by the new real-caller tests. Exact recipient filtering, attachment-subtree exclusion, headers-first retrieval, and separation of candidate selection from consumption remain in place. These are substantive corrections; the remaining problem is the acceptance decision made by `verification.outcome`.

**1. Disappearance of the code field still substitutes for affirmative acceptance.**

- **Independent real-caller reproduction:** used a separate headless Chrome with all network requests aborted and a real `Inbox` backed by the strict fake IMAP and temporary SQLite. The synthetic form's submit handler removes the code form and shows only **`Processing your request / Please wait.`**. There is no success wording or signed-in evidence. The real `_confirm_email(..., settle_s=0.2)` nevertheless returned **True**, set **`status='consumed'`** with evidence **`vendor.com accepted it`**, and moved UID **1** to Trash in the fake mailbox. A second handler showed **`We could not confirm your identity / Please contact support.`**; it produced the same incorrect success, consumption and MOVE.
- **Reason:** the second acceptance branch in `verification.outcome` returns `accepted` for any nonempty body once the supplied code-field check is false and the finite prompt/error regexes do not match. It does not require positive success or account evidence. A pending screen or unfamiliar error that replaces the form therefore becomes proof of acceptance. The helper returns immediately in these reproductions; increasing the settling timeout alone does not correct the decision.
- **Required correction:** remove form disappearance as an independent success condition. Require affirmative verification/account evidence associated with the attempted action and intended account before granting consumption/cleanup permission. Pending and unrecognized pages must stay `unproven` until bounded waiting establishes a supported result. Avoid trying to close this by extending only the error-word list; the default for missing evidence must remain unproven. Apply the rule to the shared helper used by both the dedicated caller and generic portal loop.

**2. Negated success wording is accepted as success.**

- **Independent real-caller reproduction:** the form transitions to **`Email not verified / Please contact support.`**. `_confirm_email` returned **True**, recorded `vendor.com accepted it`, consumed the candidate and moved UID **1**. `SUCCESS` matches the bare word `verified` inside the explicit negative statement, and neither rejection nor blocked-page matching prevents acceptance.
- **Required correction:** positive evidence must be interpreted as an assertion of completed verification, accounting for negation and contradictory/pending status. `not verified`, `verification not complete` and similar states cannot establish successful use. A generic `welcome` header or quoted/instructional success word likewise must not stand in for completion of this action.

**3. An inspection error is treated as proof that the field is gone.**

- **Independent helper reproduction:** the page still contained a visible code input and showed **`Processing your request / Please wait.`**. Passed a `still_present` callback that raises a simulated transient frame-inspection error. `verification.outcome(..., settle_s=0)` returned **`accepted`** while the real browser query confirmed the input still existed.
- **Required correction:** represent failed/unknown inspection separately from confirmed disappearance. Retry within the existing bounded wait or return `unproven`; do not turn an exception into acceptance evidence.

**Required regression additions:** run the real `_confirm_email` with pending and unfamiliar-error screens that **replace** the form, explicit negated verification wording, and a failed field-inspection callback. Assert `False`/`unproven`, an open action, no consumed Message-ID/evidence, no cleanup permission and no MOVE. Retain the positive accepted-code case (independently confirmed to return True and clean up only the accepted message), the unchanged-form cases, and the generic loop's only-second-code-used regression. Add a generic-loop case where a pending/error transition is followed by sign-in another way; the unproven code must not become owned through eventual account access.

**Second follow-up to the independent verification (2026-11-12, assistant; F08 left unchecked for the owner and Codex):**

- **Agreed and reproduced** with the real callers on real pages (`tests/unit/test_verification_flows.py`; the new tests fail against the previous helper). `verification.outcome` accepted a code whose form merely disappeared ("Processing your request. Please wait.", "We could not confirm your identity"), accepted "Email not verified" because `verified` matched, and took a `still_present` callback error as proof the field was gone.
- **Change** (`tools/verification.py`; both `_confirm_email` and the generic portal loop use it). Acceptance now needs AFFIRMATIVE evidence, with the default "unproven":
  - form disappearance is no longer a success condition; a bare "welcome" is not either;
  - `affirms_completion` needs a sentence that asserts completion ("your email is verified", "verification complete", "successfully verified") that is itself not negated, and no sentence on the page that says the verification failed, is pending, or is still required ("not verified", "verification not complete", "could not confirm", "processing", "please wait", challenge or error pages) - so contradicted pages stay unproven;
  - a signed-in view counts only as the agent's address together with a sign-out or account control, with nothing pending, failed or asking for a code (the address alone, as in "we sent a code to ..." never does);
  - the code field's state is tri-state: visible, gone, or UNKNOWN when the inspection raises; acceptance needs "gone" (or no field involved, for a link), and an unknown state is polled again inside the bounded wait and ends `unproven`;
  - a recognised rejection still ends the wait at once.
- **Checks.** Real `_confirm_email` on pages that stall, stay pending, show a challenge, use unfamiliar rejection wording, REPLACE the form with a pending screen, replace it with an unfamiliar error, show "Email not verified", show only "Welcome!", or show an unrelated "Thanks" page: all return False with the action `open`, no consumed Message-ID or evidence, no MOVE; the genuinely accepted code returns True and trashes only that mail; a pending screen that confirms inside the wait is accepted; a failing callback is `unproven` (also with a success sentence on the page). Generic loop: a pending transition followed by another sign-in does not own the code, and the earlier only-the-second-code cases still hold. Full unit suite on the whole working tree, together with F10-F12: 859 passed. (An earlier full run of this change showed two failures that were test defects, fixed: a stale caller stub without `is_visible`, and mail dated 20 s in the past that fell outside the 30 s window on a loaded machine; the loop tests now date their mail in the future.)
- **Limits.** Acceptance is still judged from page text: a site that confirms in wording the patterns do not know stays `unproven`, so its mail is left in place (never deleted by mistake); a page that says "verified" next to unrelated negative sentences about verification is also left unproven.

**Earlier independent verification with F10–F12 (2026-11-12, Codex; see the latest follow-up below): PARTIALLY FIXED — F08 remained unchecked.**

- **Verified corrections:** independently repeated the real `_confirm_email` cases on synthetic Chrome pages with a real `Inbox` over fake IMAP/temporary SQLite. Replacing the form with `Processing your request. Please wait.` or `Email not verified. Please contact support.` now returns **False**, leaves the action **open**, and makes no MOVE. The positive `Your email is verified.` case still returns **True** and cleans up its accepted message. The new helper also represents failed field inspection as unknown and no longer accepts mere field disappearance or a bare welcome message.
- **Remaining helper reproduction:** a page contains **`Email not verified`**, **`agent@gmail.com`** and a **`My account`** control, with the code field gone. `affirms_completion(body)` correctly returns **False**, but `outcome(page, ..., email='agent@gmail.com')` returns **`accepted`**. The `signed` alternative checks the email/account control and selected blocked/pending/prompt patterns, but does not apply the negated/contradictory-verification veto used by `affirms_completion`.
- **Confirmed through the real generic caller:** drove `portal_agent.run` with a scripted model on that synthetic page. After entering a selected code, its handler displayed exactly the unverified account view above. The loop ended `signed_in`, moved **UID 1** to Trash in the fake mailbox, and recorded **`status='consumed'`** with evidence **`vendor.com accepted it and the account is signed in`**. Nothing on the page asserted that this verification succeeded; it explicitly asserted the opposite. No real site, account or mailbox was used.
- **Required correction:** apply contradictory/failed/pending verification checks to **every** acceptance alternative. Existing account access, an email address and an account navigation item do not override an explicit statement that this code/email verification remains unverified. Preserve the action-specific successful-use requirement and leave contradictory states unproven without granting cleanup ownership.
- **Regression:** exercise both `outcome(..., email=...)` and the real generic loop with `Email not verified` plus the expected email and `My account`/Sign out controls. Assert unproven verification, an open action, no consumed Message-ID/evidence, and no MOVE, even if the loop can otherwise access an account view. Keep the positive completed-verification and genuine consistent signed-in cases. Shared test/source-alignment evidence for this review is recorded under F12.

**Earlier independent follow-up of F08/F10–F12 (2026-11-12, Codex; superseded by final verification below): VERIFICATION CORRECTED; RETRY-RECOVERY PRIVACY BLOCKER REMAINED — F08 stayed unchecked.**

- **Remaining verification reproduction now passes:** `contradicts(body)` is applied to both the completion and signed-in alternatives. With `Email not verified` or `Verification not complete`, the expected email and My account/Sign out controls no longer cause acceptance. Independent helper checks returned **`unproven`**. Drove the real generic loop with the same scripted model and page transition as the prior failure: it could report account access as `signed_in`, but made **no MOVE**, left the action **open**, and recorded **no consumed Message-ID or evidence**. This is the intended separation between accessing an account and proving this verification succeeded.
- **Positive behavior retained:** a consistent completed-verification page and a consistent signed-in account view still return accepted. The real dedicated caller leaves pending and negative-verification pages untouched, while a genuine `Your email is verified.` result returns True and cleans up its accepted message. Existing unknown-field, superseded-code, rejected-code, attachment and exact-recipient regressions remain covered.
- **Why this broader finding cannot yet be closed:** F11's retry relocation still identifies a message using only a repeated header Message-ID and the shared sender domain. In the independent F11 reproduction below, the old candidate belonged to Stripe, but the relocated header explicitly named **Palantir**. The ordinary application header filter would reject it; the retry path nevertheless retrieved its body and attributed it to Stripe. No real mailbox was used. This violates F08's acceptance requirement to reject unrelated candidates before unnecessary body retrieval where possible.
- **One underlying follow-up, shared with F11:** validate relocated-message identity and current application/header ownership before body retrieval and before reusing saved candidates. Once that recovery path is corrected, rerun its strict body-fetch assertion to close F08. Do not repeat the now-fixed verification-helper work. Shared suite/source-alignment evidence for this review is recorded under F12.

**Final independent verification after the recovery-header follow-up (2026-11-12, Codex): VERIFIED FIXED — F08 checked off.**

- **The remaining privacy reproduction now passes:** queued the synthetic Stripe message under UIDVALIDITY 7, then offered a Palantir message under UIDVALIDITY 8 with the same Message-ID and ATS sender domain. The real sync inspected the Palantir headers but performed **zero text/body fetches**, stored **no outcome**, and left the original Stripe retry **undecided** with explicit recovery instructions. This repeats the exact case that previously blocked closure.
- **Source verification:** relocation compares the recorded full sender address, subject fingerprint and normalized Date in addition to Message-ID, and requires current application/header ownership before body retrieval. A shared ATS domain alone no longer establishes permission to read a relocated candidate. The ledger retains metadata/fingerprints for these checks, not raw message bodies.
- **Earlier acceptance checks retained:** exact parsed recipient matching, header-first candidate rejection, bounded non-attachment text retrieval, exclusion of attached multipart subtrees, and action-specific cleanup remain covered. The verification helper applies contradiction checks to both completion and signed-in alternatives. `verification.py`, `portal_auth.py` and `portal_agent.py` are byte-for-byte unchanged from the prior snapshot where the real caller preserved unverified/pending mail, refused failed inspections, and cleaned up only the specifically accepted candidate. Current verification-flow/privacy tests also pass.
- **Scope of closure:** this establishes the documented shared-inbox contract: header filtering precedes plausible-candidate text retrieval, and specifically owned/used verification messages alone qualify for cleanup. It does not infer ownership merely from a vendor domain. Different content with *identical plausible headers* can still be mistaken for an unresolved original during F11 recovery; that remaining identity problem does not reproduce the missing pre-body ownership gate closed here. F11 remains open and must still be corrected before describing outcome recovery as reliable.
- **Validation and limits:** shared suite results, the externally updated test, current-source alignment and isolated-reproduction conditions are recorded under F12. No real mailbox or application was used; only this review document was edited.

## F09

**High — A company-looking display name bypasses sender-domain validation.**

**Current status: verified fixed.** Final follow-up verification on 2026-11-12 confirms the original display-name bypass and the shared-suffix counterexamples are addressed. The final verification below supersedes the earlier open verdict retained as history.

**Where:** [submit.py](src/jobagent/steps/submit.py), `ApplicationMatch.__call__`, `application_match`.

**Issue and reason:** The first company word appearing anywhere in the raw sender string can establish that a message came from the company. An isolated reproduction accepted `Carvana Recruiting <outsider@unrelated.example>` when the subject mentioned Carvana. The matcher also guesses company domains by removing spaces and appending `.com`, which rejects legitimate companies using other domains.

**Correction:** Require exact validated sender-domain membership before interpreting display names or body content. Obtain company domains from verified company research and portal metadata. Display names can help identify an application but must not establish sender authority. Centralize domain normalization and ATS-family handling.

**Acceptance checks:** A forged company display name cannot bypass an unrelated domain. Genuine configured company subdomains and ATS sender families work. A company whose actual domain differs from its concatenated name is supported through verified metadata.

**Resolution notes (2026-11-11, assistant; left unchecked for the owner and Codex):**

- **Reproduced first** (`tests/unit/test_inbox_privacy.py`): `Carvana Recruiting <outsider@unrelated.example>` with a Carvana subject was accepted because the first company word anywhere in the raw sender string counted as "from the company"; the company's own domain was guessed as `name + .com`.
- **Change** (`steps/submit.py`): `ApplicationMatch` answers two separate questions in order: WHO sent it (the registrable domain parsed from the address and compared exactly against the company's verified own domains and the portal/ATS sender family; a display name, subject or body never establishes it) and then WHAT it is about (company and role words). `company_domains` gets own domains from verified metadata only: `company_domains` listed on the job, the company research profile's official site and official pages, and the careers site discovery resolved (shared job boards and ATS families excluded). The name guess is gone; `plausible` gives the header-only form of the same rule for the privacy-first retrieval (F08).
- **Checks.** A forged company display name cannot pass an unrelated domain, in either the full or the header-only check; a genuine company subdomain and the ATS family still work; a company whose real domain differs from its name (Meta Platforms to meta.com, fb.com, metacareers.com) is supported through the research and careers metadata while the guessed `metaplatforms.com` is rejected; a shared ATS host is never mistaken for the company's own domain. Full unit suite on the whole working tree: 765 passed.
- **Limits.** A company with no research profile, careers cache or explicit domains has no verified own domain: its mail is accepted only through the portal/ATS sender family (and the company named), not from its own corporate domain, until `jobagent research` or discovery has run.

**Earlier independent verification (2026-11-11, Codex; superseded by final verification below): PARTIALLY FIXED — F09 remained unchecked.**

- **Validation:** all **765 tests** passed in the shared isolated run recorded under F07, including the new display-name and verified-metadata tests. The suffix counterexample below was an additional inline reproduction against the same tested source.
- **Confirmed improvements:** the original `Carvana Recruiting <outsider@unrelated.example>` reproduction is now rejected in both header-only and full-message matching. Company words no longer override a disallowed normalized sender domain, the guessed `name + .com` rule is removed, and explicit/research/careers metadata supplies actual company domains. Genuine configured subdomains, ATS sender families and non-concatenated company domains are covered by the new tests.
- **Remaining reproduction through the actual metadata path:** created a temporary database and an Acme JD with `company_domains=["https://acme.co.za"]` and a Greenhouse apply URL. `application_match` produced `own={"co.za"}` and `allowed={"co.za", "greenhouse.io", "greenhouse-mail.io"}`. Both `matcher.plausible(mail)` and `matcher(mail)` returned **True** for `Acme Recruiting <hr@unrelated.co.za>` with an Acme application subject. The unrelated domain was admitted before the company words were interpreted. All data was synthetic; no mail was fetched or stored outside the isolated test.
- **Reason:** `db.registrable_domain` uses a small hand-maintained suffix set, omitting `co.za`, and otherwise takes the last two labels. It therefore maps **both** `acme.co.za` and `unrelated.co.za` to the same supposedly trusted identity. It also maps distinct `*.github.io` tenants to `github.io`. Centralizing normalization is an improvement, but comparing those collapsed values is not exact company-domain validation. This affects verification/privacy ownership too because `inbox.sender_domain` uses the same normalizer.
- **Required correction:** preserve validated company-host identity and match exact hosts/explicit subdomains, or use a maintained public-suffix-aware registrable-domain implementation with an explicit shared/private-suffix policy. Do not admit a public suffix itself as a company's verified own sender domain. Apply the same policy to configured company metadata, parsed sender addresses, portal families and action ownership; retain intentional ATS-family expansion separately. Do not repair this by adding only `co.za` to the short list.
- **Regressions:** configure `acme.co.za`; accept `hr@acme.co.za` and an allowed company subdomain, while rejecting `hr@unrelated.co.za` even with the company display name, subject and body. Cover more than one multi-label suffix and shared hosted tenants under the chosen policy. Also assert that the metadata path never produces a public suffix as `own`, while preserving the existing Meta Platforms metadata and ATS-family tests.

**Final follow-up independent verification (2026-11-12, Codex): VERIFIED FIXED — F09 checked off.**

- **Source verification:** `db.registrable_domain` now delegates to `tools/psl.py` with the vendored Public Suffix List at `src/jobagent/data/public_suffix_list.dat` (file version `2026-11-11_10-02-15_UTC`). The parser handles exact, wildcard and exception rules, including PRIVATE shared-hosting suffixes. `company_domains` excludes public suffixes themselves from company-owned identities. Company metadata and parsed sender domains share this policy; explicit ATS-family expansion remains separate.
- **Independent acceptance checks through `application_match`, not just the domain helper:** for configured `acme.co.za`, the matcher now produces `own=['acme.co.za']`, refuses both header-only and full-message matching for `Acme Recruiting <hr@unrelated.co.za>` even with Acme in the subject/body, and accepts genuine `mail.acme.co.za` sender subdomains. Repeated the same checks for **`co.uk`, `com.au`, `github.io` and `herokuapp.com`**: unrelated companies/tenants are refused and genuine configured company subdomains work. Configuring bare `co.za` and `github.io` produces no company-owned domains. The original forged display name with `outsider@unrelated.example` is refused, while `no-reply@us.greenhouse-mail.io` with the company named still matches through the intentional ATS family.
- **Regression evidence:** all F09 tests in `test_inbox_privacy.py` passed in the shared run, including Meta Platforms' real-domain metadata, rejection of guessed `metaplatforms.com`, multiple multi-label/private suffixes, bare-suffix exclusion, and PSL wildcard/exception behavior. The complete run was **797 passed, 2 failed**; both failures were outside F09 and passed on the isolated rerun, as detailed under F07. This closure does not describe that initial full run as clean or close F08's separate caller-ownership defects.
- **Scope and limits:** verified current source and vendored data against isolated synthetic messages/metadata, not a live mail-provider scan. The vendored PSL requires maintenance as suffix policy changes. Only this review document was edited during verification.

## F10

**High — Unapproved or ambiguous thread associations can become trusted.**

**Current status: partially fixed; remains unchecked.** Normal replies now record provenance and follow reassignment. A reply waiting in the classification retry ledger still carries the former candidate application after its seed is reassigned; the eventual event can be automatically approved under a different application from its recorded parent. See the latest verification below.

**Where:** [outcomes.py](src/jobagent/tools/outcomes.py), thread queries and candidate selection in `sync`, `decide`; [cli_outcomes.py](src/jobagent/cli_outcomes.py), review commands.

**Issue and reason:** Thread associations with `needs_review != 2` are usable, so unapproved events can seed inheritance. When two applications tie, sync stores one arbitrary association and marks it for review. A later message in that thread can inherit the unapproved association and become accepted. Review cannot choose a different application or inspect the full candidate-association evidence.

**Correction:** Inherit only approved, unambiguous thread associations. Store candidate application IDs and matching evidence. Provide assignment/reassignment operations and propagate pending association status to descendants. Retain useful thread linking for legitimate recruiter replies without treating an uncertain seed as authoritative.

**Acceptance checks:** Two same-company applications produce a reviewable ambiguity. Before assignment, a follow-up cannot become automatically associated. Selecting the other application works and is auditable. Approved thread inheritance still handles legitimate personal recruiter addresses.

**Resolution notes (2026-11-12, assistant; left unchecked for the owner and Codex):**

- **Reproduced first** (`tests/unit/test_outcome_integrity.py`): thread associations with `needs_review != 2` seeded inheritance (so an unapproved event could attach later mail), a tie stored one arbitrary application, and review could neither show the candidates nor choose another application.
- **Change** (`tools/outcomes.py`, `cli_outcomes.py`, `db.py`). A thread seeds inheritance only through an approved, unambiguous event (`needs_review = 0` and `ambiguous = 0`). A tie (or an event inherited from an unsettled thread) is stored `ambiguous` and `needs_review`, with EVERY candidate application and its matching evidence in `outcome_candidates`. Replies in a thread whose seed is unsettled are stored pending for the same candidates (not auto-associated); legitimate personal-address recruiter replies still attach through an approved seed. `outcomes candidates <event>` shows the evidence; `outcomes assign <event> <application>` chooses any submitted application, counts the event, audits it (`outcome_audit`), updates the application's confirmation state, and lets the waiting replies in the thread follow (audited as `propagate`); reassignment works and is audited again. Approving an ambiguous event is an audited assignment to its current application.
- **Checks.** Two same-company applications give a reviewable ambiguity (two candidates with evidence, not counted, no current stage); a follow-up before assignment stays pending and ambiguous; an unapproved low-confidence seed cannot attach later mail; choosing the other application is audited and the thread follows; an approved seed still handles a recruiter's personal address. Full unit suite on the whole working tree: 859 passed.
- **Limits.** Candidate scores are word-match counts, so a real tie needs the candidate's decision; an `assign` can pick an application the matcher never considered (by design).

**Earlier independent verification (2026-11-12, Codex; see the latest follow-up below): PARTIALLY FIXED — F10 remained unchecked.**

- **Verified improvements:** `_events_seeding` requires approved events with `ambiguous=0`; pending thread replies retain candidate applications and review status; candidate evidence and assignment/audit CLI operations exist. The added regressions cover initial ties, choosing the other application, low-confidence seeds and legitimate personal-address recruiter replies. These correct the original initial-approval gap.
- **Independent reassignment reproduction:** used the two submitted Stripe applications, ambiguous confirmation **UID 2001**, and pending recruiter follow-up **UID 2002** from the synthetic fixtures. Assigned the confirmation to application **2**; the follow-up correctly followed it. Then reassigned the confirmation to application **1**, as the new API explicitly supports. The result was **`2001 -> app 1, needs_review=0, ambiguous=0`** but **`2002 -> app 2, needs_review=0, ambiguous=0`**. The follow-up had only been automatically propagated, not separately assigned by the user. `_events_seeding` now returned thread **900 for both applications**.
- **Reason:** `assign` propagates only to descendants that still have `ambiguous=1`. Its first propagation clears that flag, so later correction of the authoritative assignment cannot update or invalidate the dependent approvals. Per-event flags then incorrectly imply that the conflicting thread associations are unambiguous.
- **Required correction:** preserve the dependency/provenance of automatically inherited associations. On reassignment, update or requeue those dependants, while respecting separately audited human overrides. Do not allow a thread with conflicting approved ownership to seed automatic inheritance as though it were unambiguous. Recompute affected derived application state consistently with the corrected associations.
- **Regression:** perform initial assignment **A -> B**, verify the pending descendants follow B, then reassign the seed **B -> A** and verify dependent descendants follow A or return to review. Check approved thread owners and a subsequent recruiter reply, not just the count of `assign` audit rows. Also retain an explicit manual descendant override case so correction does not silently erase a separate human decision. Shared suite results are recorded under F12.

**Earlier independent follow-up (2026-11-12, Codex; see the latest verification below): PARTIALLY FIXED — F10 remained unchecked.**

- **Verified corrections:** the previous pending-before-assignment reproduction now follows **app 2 -> app 1** correctly, with `inherited_from` recorded during assignment propagation. Manual overrides are preserved, derived confirmation state is moved, and conflicting approved owners make `_events_seeding` return no automatic inheritance source. These address the previously documented failures.
- **Independent ordinary-arrival reproduction:** ingested only confirmation **UID 2001**, assigned it to **app 2**, then delivered recruiter follow-up **UID 2002** and synced again. It was automatically approved under app 2 with **`manual=0, inherited_from=NULL, needs_review=0, ambiguous=0`**. Reassigned the seed to **app 1**. The seed moved, but UID 2002 stayed approved under **app 2** with the same null dependency. This chronology differs from the new test, which delivers the follow-up while the seed is still pending.
- **Reason:** normal `sync` ingestion records candidate evidence and the approved application, but does not store which approved seed/root supplied the thread association. The new `assign` cascade can follow `inherited_from` only for events whose dependency was recorded by an earlier propagation. It misses the ordinary replies received after approval.
- **Impact and correction:** the new conflict guard correctly stops *future* automatic inheritance, but it does not repair or requeue the already counted wrong-owner follow-up. Record association provenance during normal approved-thread ingestion too, preserve it through retries, and update/requeue dependent events on seed correction, including later descendants. Preserve separately audited manual overrides. Review existing automatic null-provenance events conservatively when their dependence cannot be established.
- **Regression:** approve a seed, **then** receive/ingest a personal-address recruiter reply, then reassign the seed. Assert that the reply follows or returns to review, its provenance is recorded, derived status stays consistent, and a subsequent reply uses the corrected ownership policy. Retain the pending-before-approval and manual-override cases.

**Latest independent verification after the normal-ingestion provenance follow-up (2026-11-12, Codex): PARTIALLY FIXED — F10 remains unchecked.**

- **Verified correction:** repeated the previous chronology: approve confirmation UID 2001 for application 2, receive recruiter reply UID 2002 afterwards, then reassign the seed to application 1. The reply now records its seed and follows the correction. Historical automatic events without provenance are conservatively requeued; manual overrides and the conflicting-owner guard remain intact.
- **Remaining queued-reply reproduction:** assigned the seed to **application 2**, received a personal-address recruiter reply whose classification failed, and left that reply in the retry ledger. Its stored candidate was **`application_id=2, seed=1`**. Reassigned seed event **1** to **application 1 before restoring the classifier**. On the next sync, the reply was written as **`application_id=2, inherited_from=1, needs_review=0, ambiguous=0`**, while seed event 1 belonged to application 1. The retry retained the provenance ID, but not a valid current ownership decision.
- **Reason:** reassignment corrects existing events, while the retry path republishes saved candidate applications without reconciling them against the seed's current application/approval state. Saving a parent ID does not reauthorize that parent's former owner. The reply did not yet have an event row when the cascade ran.
- **Required correction:** revalidate or refresh seed-dependent candidate ownership when retries are evaluated and before an event is accepted. A changed, rejected, pending or conflicted parent must cause reassociation under the current authoritative decision or explicit review. Coordinate seed correction with queued work, or validate parent state at publication, so classifier outages and long model calls cannot publish stale approved associations. Preserve independent manual assignments.
- **Regression:** approve seed for B; queue a thread-only reply during a model outage; reassign seed to A; restore the model. The event must follow A or remain pending, never be automatically counted for B while pointing to an A parent. Also test removal of a seed's authority while work is queued, and retain ordinary-after-approval, pending-before-approval and manual-override cases.

**Response to the 2026-11-12 verdict (round 3, Claude; checkbox left for Codex/user):**

- **Provenance of every event.** `outcome_events` records `inherited_from` (the seed event id; 0 = matched directly; NULL = unknown), `manual`, `ambiguous`, `content_sig`, header Message-ID, mailbox UID, UIDVALIDITY and delivery time. `consider()` records the seed per application, so a thread-only reply knows which approved event gave it its owner. `assign()` marks the event manual, moves its derived confirmation state, propagates to descendants with `inherited_from = event_id` or ambiguous (never to manual ones), and sends counted events of the seed's FORMER owner whose dependence cannot be established (NULL provenance) back to review (audit action `requeue`).
- **The queued-reply case.** `_revalidate_seeds` runs before publishing: a reply queued during a model outage follows its seed to its CURRENT owner after a reassignment, and is published pending (ambiguous, needs review) if the seed was rejected, pending, ambiguous or gone. `_events_seeding` excludes conflicted threads; `_pending_threads` includes them.
- **Tests** `test_outcome_integrity.py`: reply queued before the seed was reassigned (both directions), seed that lost its authority (rejected / pending / ambiguous), provenance after ordinary arrival, null-provenance requeue.

## F11

**High — Ingestion cursors and message identities can permanently lose messages.**

**Current status: partially fixed; remains unchecked.** The prior cross-company/shared-ATS privacy failure is fixed. Relocation now checks a composite of headers and current application ownership, but different content with identical recorded headers can still be treated as the original message and retire its unresolved retry. See the latest verification below.

**Where:** [outcomes.py](src/jobagent/tools/outcomes.py), `classify`, `sync`; [inbox.py](src/jobagent/tools/inbox.py), `parse`; [db.py](src/jobagent/db.py), outcome identity schema.

**Issue and reason:** Classification failures become no-stage results, are dropped, and do not prevent cursor advancement. Once failures outlast the two-day overlap, messages can fall outside future searches. Missing Message-ID headers become the empty string, causing unrelated messages to collapse under the claimed-message dictionary and database uniqueness constraint. Header Date alone is also an unreliable ingestion-progress boundary.

**Correction:** Persist classified, unrelated, retryable-failure and review-required processing states. Use provider identity or mailbox UID plus UIDVALIDITY as durable identity, with Message-ID as secondary metadata. Advance cursors only after durable processing. Preserve delivery metadata separately from the message's claimed Date and retry undecided/failed items explicitly.

**Acceptance checks:** A model outage longer than the overlap loses no candidate messages. Missing/duplicate Message-ID headers do not collapse distinct mail. Late delivery and malformed or timezone-missing Date headers are handled predictably. Restart and duplicate delivery remain idempotent.

**Resolution notes (2026-11-12, assistant; left unchecked for the owner and Codex):**

- **Reproduced first** (`tests/unit/test_outcome_integrity.py`): classification failures were dropped as "could not tell" while the date cursor (with a two-day overlap) moved on, an empty Message-ID collapsed unrelated mail under the claimed-message dictionary and the unique column, and a missing or unparsable Date silently excluded a message.
- **Change.** `mail_ledger` records every message the sync looked at by DURABLE identity (the folder's UIDVALIDITY and the UID; the Message-ID is only metadata, kept in `header_message_id`), with its state: `unrelated` (terminal), `stored` (an event was written), `retry` (the classifier failed or was unavailable: re-evaluated on every sync with its stored candidates, attempts counted), `undecided` (after `MAX_ATTEMPTS`, or when the model abstains: for the candidate; `outcomes retry` / `outcomes dismiss`). The per-application cursor is the highest UID covered (`UID n:*` searches, no date window) and advances only after every message it covers has a ledger row; a crash mid-sync leaves it where it was. New events are keyed `uidvalidity:uid`; a mail seen again (restart, re-delivery, a new UIDVALIDITY) is recognised by the key, or by header id plus subject, never by an empty or repeated id alone. Delivery time (`INTERNALDATE`) is stored apart from the claimed Date, which is parsed to UTC (a zoneless Date is read as UTC; a missing or malformed one leaves `date` empty and the delivery time places the message), so a forged or malformed Date cannot move a message out of reach and a late delivery is found by UID. Classifier errors, and `--no-model`, are `failed`: never "not an outcome".
- **Checks.** A model outage across 3, 6, 12 and 20 days loses nothing and the message is stored when the model returns; a message the classifier keeps failing on becomes `undecided` and can be requeued or dismissed; messages with a missing or a repeated Message-ID stay distinct; restart, duplicate sync and re-delivery under a new UIDVALIDITY are idempotent; a late-delivered mail with an old Date is found by UID with its delivery time preserved; zoneless, missing and junk Date headers are handled; a failure mid-sync does not advance the cursor and the next sync stores exactly the two messages; unrelated mail is recorded once, with metadata only, and never fetched again. Full unit suite on the whole working tree: 859 passed.
- **Limits.** Mail judged unrelated is permanent in the ledger; if a company's verified domain appears later, `sync-inbox --since-days N` rescans such mail. Events written before this change keep their header Message-ID as `message_id` and are matched by header id plus subject.

**Earlier independent verification (2026-11-12, Codex; see the latest follow-up below): PARTIALLY FIXED — F11 remained unchecked.**

- **Verified improvements:** durable processing states, retry/undecided review operations, per-application UID cursors, delivery timestamps and normalized Date parsing exist. The new tests cover ordinary multi-day classifier outages, missing header IDs, repeated IDs with *different subjects*, missing dates, and a body-fetch exception that aborts the whole sync. The following additional cases still violate identity/recovery guarantees. All reproductions used temporary databases and synthetic fake inboxes.

**1. Distinct messages with a repeated Message-ID and the same subject are still merged.**

- **Reproduction:** UID **100** reported an assessment and UID **101** an interview. Both had header Message-ID **`<reused@sender>`** and subject **`Stripe application update`**, but different bodies and durable identities. Sync stored only **`7:100 / assessment`**. UID 101 was recorded in the ledger as **`stored / already an event`**, with no interview event. It is consequently treated as terminally processed rather than retried.
- **Reason/correction:** `_stored_already` treats matching header ID plus subject as sufficient identity, including for newly written events, not just legacy records. Use durable provider identity and a documented redelivery policy; any secondary cross-epoch/legacy reconciliation must establish that the same message was redelivered rather than merging distinct content on header/subject equality. Preserve genuinely idempotent restart/redelivery handling without discarding different messages.
- **Regression:** repeated header ID **and subject**, different UIDs and bodies/stages, must preserve both observations. Test duplicate delivery separately rather than assuming two matching header strings prove it.

**2. A queued retry uses its old UID in the new UIDVALIDITY and processes unrelated mail.**

- **Reproduction:** under UIDVALIDITY **7**, UID **100** was a Stripe candidate message whose model classification failed, leaving a retry row. Replaced the fake mailbox with UIDVALIDITY **8**, where UID **100** was now unrelated mail from **`private@unrelated.example`**, subject **`Personal membership update`**. The normal sender search did not claim that unrelated mail. The retry pass nevertheless fetched its body and stored it as an approved Stripe **rejection**, with **`message_id='7:100'` but `uidvalidity='8'`**. The old `7:100` ledger row stayed `retry`; a new `8:100` row became `stored`.
- **Reason/correction:** the retry pass calls `fetch_headers(row['mailbox_uid'])` without checking the row's UIDVALIDITY against the mailbox/current header, then carries the old candidate association and old event key into the new message. Never interpret a UID outside its recorded mailbox epoch. Reconcile using stable provider identity or leave the unresolved item explicitly recoverable/reviewable; revalidate relocated message identity and application ownership before reading its body. Keep key, UIDVALIDITY, candidates and ledger state coherent. This is also an inbox-privacy failure, not just a duplicate-count issue.
- **Regression:** a pending retry survives an epoch change where its old numeric UID is reassigned to unrelated mail. Assert no unrelated body retrieval or attributed event, no mixed-epoch key, and an explicit recovery/review state for the original unresolved candidate.

**3. A header-fetch failure is skipped while a higher UID advances the cursor.**

- **Reproduction:** matching UIDs **100** and **101** were available. `fetch_headers('100')` raised one simulated transient `LookupError`; UID 101 succeeded. Sync stored only UID 101 and advanced the cursor to **`7:101`**, with **no ledger row for UID 100**. On the next sync the search began **`UID 102:*`** and UID 100 was never retried even though its fetch now worked.
- **Reason/correction:** the header-fetch exception is caught with `continue`, but a later UID can still move the cursor past that unrecorded work. Distinguish a confirmed vanished message from a transient/unproven fetch failure. Durably queue the gap for retry, or prevent cursor advancement across it until it is resolved; do not claim that every covered message has a ledger state when one does not.
- **Regression:** a one-time failed header fetch below a successfully processed higher UID, followed by restart, must eventually process the lower UID exactly once. Assert the durable pending state/cursor boundary, as well as the final event count. Include a confirmed-expunge case separately. Shared suite results are recorded under F12.

**Earlier independent follow-up (2026-11-12, Codex; see the latest verification below): PARTIALLY FIXED — F11 remained unchecked.**

- **Verified corrections:** independent repeated-header/subject messages with different bodies now retain both assessment and interview events through `content_sig`. A transient header failure keeps the cursor below the gap and both messages are processed on the next sync; actual failed IMAP FETCH responses now raise a transient IMAP error instead of falsely establishing expunge. A reset where the old numeric UID belongs to a different sender/Message-ID no longer reads that unrelated body and leaves the original retry explicitly undecided. New tests cover genuine relocated originals and confirmed expunge separately.
- **Remaining independent relocation reproduction:** in epoch **7**, UID **100** was a Stripe candidate message with header ID **`<reused@sender>`**, queued after model failure. In epoch **8**, the only message with that header ID was UID **205**, from **`Palantir <no-reply@us.greenhouse-mail.io>`**, subject **`Your Palantir application`**, reporting Palantir's rejection. The original Stripe message was absent. Both senders share the ATS registrable domain. Sync read body **205** and stored **`8:205 / rejection / application_id=1 / needs_review=0`** against the submitted **Stripe** application.
- **Reason:** relocation requires a single header-ID hit with the same sender domain, then transfers the old ledger identity and candidate associations without establishing it is the old message or reapplying current application/header matching. A sender domain used by many companies is not a message identity; uniqueness among today's search results does not make a reused header ID reliable. The earlier duplicate-ID requirement applies across mailbox epochs as well as within one epoch.
- **Required correction:** correlate an unresolved retry with stable provider identity or previously captured immutable evidence sufficient to distinguish the original content; treat a header ID/domain match as a candidate lookup, not proof. Check current header/application ownership **before** retrieving the relocated body, and revalidate association before carrying old candidates forward. If identity cannot be established, leave an explicit undecided/recovery item rather than attributing another company's mail. Keep genuine relocation, durable retry and deduplication behavior.
- **Regression:** repeat the epoch reset using a *different company on the same ATS domain* and a reused header ID. The header should be inspectable without a body fetch; assert no unrelated text retrieval, no Stripe event, and an explicit state for the original unresolved retry. Also cover another distinct message from the same company with the reused header ID so domain/company equality alone is not mistaken for original-message identity. This single correction also addresses the remaining F08 recovery/privacy blocker.

**Latest independent verification after the header-fingerprint follow-up (2026-11-12, Codex): PARTIALLY FIXED — F11 remains unchecked.**

- **Verified corrections:** the prior Palantir/shared-ATS relocation stops before body retrieval and leaves the original explicitly undecided; this closes F08's last pre-body privacy blocker. Changed sender/subject/date metadata no longer silently identifies another message as the original. New tests retain genuine relocation, distinct same-company messages, durable fetch-gap recovery and duplicate-header/content separation.
- **Remaining independent identity reproduction:** epoch **7**, UID **100**, was a queued Stripe message whose body reported a model-only soft rejection. In epoch **8**, supplied a distinct message at UID **205** with the **same Message-ID, full sender, subject and Date**, but a different body: **`Please complete your online assessment within seven days.`**. The original was absent. Sync transferred the old retry to UID 205 and ended with **only `8:205 / stored / assessment (rule)`** in the ledger/event results. There was **no remaining unresolved record for the original**. The headers matched, but the content and reported outcome did not.
- **Important distinction:** the new message is plausibly about the submitted company and may legitimately be read/processed separately. That is why the test expectation corrected during this run is valid. The defect here is retiring the *original unresolved work* as though different content proved it had been found. Expected behavior is to process the new message independently while preserving/reviewing the unresolved original unless stable identity or captured content evidence proves it is the same message.
- **Required correction:** treat the header composite as a relocation prefilter, not durable identity. Capture a stable provider message identifier or sufficient immutable content evidence when the original body is first read, before classification can fail. After current header/privacy checks permit retrieval, verify relocated content/provider identity before moving the original ledger row or carrying its candidates forward. Event-only `content_sig` does not protect a retry whose ledger never captured the original content evidence. Do not conflate this with forbidding ordinary processing of another legitimate company message.
- **Regression:** same header ID, sender, subject **and Date**, different body/stage across a UIDVALIDITY reset. Assert a separate new-message observation and an explicit unresolved/recovery state for the missing original; do not let the original disappear into `stored` for the replacement. A truly identical relocated original must still recover idempotently. Keep the existing different-subject/date sibling as a separate positive discovery case.

**Response to the 2026-11-12 verdict (round 3, Claude; checkbox left for Codex/user):**

- **A relocated retry must be the same message.** `mail_ledger` gained `sender_addr`, `subject_sig`, `content_sig`; `_record` captures the content signature when the body was read. After a UIDVALIDITY change a retry is re-attached only if Message-ID, the FULL sender address, the subject hash, the Date and the current `plausible()` ownership all match AND the fetched body's `content_sig` equals the recorded one; otherwise the row becomes `undecided` ("could not be re-identified from its headers and content"). Identical headers with a different body are therefore never accepted as the original.
- **Transient header errors** (`OSError`, `imaplib.IMAP4.error`; `inbox.fetch_headers` now raises on a non-OK FETCH) block the per-application cursor below the gap; a vanished message records `unrelated` ("gone from the mailbox").
- **Tests** `test_outcome_integrity.py`: different content with identical headers, sibling with a reused header id, same-ATS Palantir mail, relocation with each signature differing, transient vs gone; `FakeInbox` gained header search, transient/gone injection and `uidvalidity()`.

## F12

**High — Newsletter and quoted-history content can become false outcome events.**

**Current status: partially fixed; remains unchecked.** The descriptive newsletter/resource cases and neutral-interview model case now pass. The model evidence check still unconditionally treats rejection/withdrawal labels as candidate progression, allowing a neutral acknowledgement to become an automatically accepted negative outcome. See the latest verification below.

**Where:** [outcomes.py](src/jobagent/tools/outcomes.py), `rule_verdict`, `fresh_text`, `model_verdict`.

**Issue and reason:** Strong stage phrases bypass the newsletter exclusion. An isolated synthetic newsletter saying that the company's hiring process includes an online assessment was classified as assessment at confidence 0.9. Rules use cleaned new text, but the model receives the raw body, including quoted historical replies. These paths undermine the claim that non-outcomes are never stored as outcomes.

**Correction:** Require evidence that the message reports this candidate's progression, not a description of a general hiring process. Give rules and the model a consistent cleaned input. If history is necessary, label it separately and prevent historical text from serving as current-event evidence. Validate evidence spans and retain an explicit abstain/review state.

**Acceptance checks:** Hiring newsletters, marketing descriptions of assessments and neutral replies quoting old invitations are non-outcomes. Genuine invitations with newsletter footers remain recognized. Add held-out negative cases rather than only more positive phrase variants.

**Resolution notes (2026-11-12, assistant; left unchecked for the owner and Codex):**

- **Reproduced first** (`tests/unit/test_outcome_integrity.py`): a newsletter saying the company's hiring process includes an online assessment was classified as an assessment at 0.9, and the model was given the raw body including quoted history.
- **Change** (`tools/outcomes.py`, `tools/inbox.py`). A stage phrase counts only in a sentence that reports THIS candidate's progression (`about_this_candidate`): descriptions of the hiring process, general statements ("candidates typically ..."), marketing and announcements ("ace your online assessment", webinars, discounts) never count; a mailing (List-Unsubscribe or Precedence: bulk) needs an explicit personal statement ("your application", "you have been invited"); otherwise the sentence or the message must address the candidate (you/your, a greeting, "thank you for applying", a question put to the reader, or a personal reply), and rejections/withdrawals, which are about one application by nature, pass unless the process/marketing vetoes apply. The model gets the SAME cleaned text as the rules (the new part only, quoted and forwarded history removed) and may answer `unsure`; its answer stands only if its quoted evidence is found in that cleaned text and reports this candidate's progress; otherwise it abstains (`undecided`, for the candidate, never stored) or is a non-outcome.
- **Checks.** Six hiring-newsletter / marketing / webinar / generic-process negatives are non-outcomes; a bulk mailing without a personal statement is not, one with it is; genuine interview, assessment and rejection messages with newsletter footers are still recognised; a neutral reply quoting an old invitation is a non-outcome for the rules, the model never receives the quoted text, and evidence taken from the quoted history makes it abstain; general-statement and invented evidence are refused; an abstaining model stores nothing. The existing labelled set and held-out accuracy tests still pass. Full unit suite on the whole working tree: 859 passed.
- **Limits.** The test is sentence-level and heuristic: a marketing line written in the second person with none of the known marketing or process markers could still be read as personal; held-out negatives here are written by the assistant and need real mail to be tested further.

**Earlier independent verification (2026-11-12, Codex; see the latest follow-up below): PARTIALLY FIXED — F12 remained unchecked.**

- **Shared F08/F10/F11/F12 validation:** ran the full unit suite against isolated source `/tmp/f08-f10-f12-review.1CA7xI` in Docker: **859 passed, 13 dependency deprecation warnings in 288.97 seconds**. This includes the new verification-flow and outcome-integrity regressions. The counterexamples below and under F08/F10/F11 were additional independent inline reproductions; the existing passing suite does not cover them. All **131 Python source/unit-test files plus the vendored PSL data** matched the tested snapshot. Tests used temporary databases/state, synthetic Chrome pages, deterministic mocked models and fake mailboxes. No live application, real mailbox operation or model-provider request was performed. Only this review document was edited; application source/tests and `/app/src` were not changed. Command:

  ```bash
  docker exec -w /tmp/f08-f10-f12-review.1CA7xI \
    -e PYTHONPATH=/tmp/f08-f10-f12-review.1CA7xI/src \
    -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 -e PYTHONDONTWRITEBYTECODE=1 \
    -e TMPDIR=/dev/shm -e STATE_DIR=/dev/shm/f08-f10-f12-state-1CA7xI \
    -e OUTPUTS_DIR=/dev/shm/f08-f10-f12-outputs-1CA7xI \
    -e DATA_DIR=/tmp/f08-f10-f12-review.1CA7xI/data \
    jobagent pytest --basetemp=/dev/shm/f08-f10-f12-review-1CA7xI -q tests/unit
  ```

- **Verified improvements:** both paths use `fresh_text` for the body; ordinary quoted history is removed; the model's evidence must occur in that cleaned text; general-process/marketing negatives and explicit abstention are implemented. Existing tests also preserve genuine invitations with newsletter footers. The following held-out cases still reproduce the reported false-outcome mechanism.

**1. A newsletter's guide or rhetorical question still becomes an assessment at 0.9 confidence.**

- **Independent rule-path reproductions:** subject **`Stripe newsletter: your guide`**, body **`Your guide to our online assessment is now available in the candidate handbook.`**, classified as **`assessment / 0.9`**. Subject **`Stripe careers newsletter`**, body **`Interested in our online assessment? Explore the candidate handbook.`**, also classified as **`assessment / 0.9`**. Neither announces this candidate's progression or assigns an assessment. These synthetic emails intentionally have no bulk headers; a newsletter label does not require those optional headers.
- **Storage confirmed:** passed both messages through the real `sync` with the fake inbox and a submitted Stripe application. It stored **both assessment events with `needs_review=0`**; the false positives are not confined to a helper return value.
- **Reason:** `about_this_candidate` accepts a generic `your` or any question as personal progression. That produces a strong stage hit, which still bypasses `NOT_OUTCOME` even when the subject explicitly says newsletter. The process/marketing blacklist does not repair the original stage-phrase override for other ordinary wording.
- **Required correction:** require an actual invitation, assignment or report of this candidate's stage; reader-directed phrasing, a rhetorical question or a greeting alone is insufficient. Treat explicit newsletter/marketing context consistently, while distinguishing a genuine stage sentence from an unsubscribe/newsletter footer. Expand held-out negatives rather than fixing only the two exact phrases above.

**2. The model still sees an old reply subject and can use neutral text as stage evidence.**

- **Independent model-path reproduction:** subject **`Re: Stripe interview invitation`**; the only new text was **`Thanks for the update; I will get back to you tomorrow.`**, followed by a standard quoted old interview invitation. `fresh_text` correctly removed the reply subject and old body. Captured the actual model prompt: `model_verdict` reintroduced **`Subject: Re: Stripe interview invitation`** outside that cleaned input. A deterministic model response of `interview`, confidence **0.95**, quoting only the neutral new sentence was accepted with **`abstain=False`**. No current invitation or progression was reported.
- **Storage confirmed:** the real `sync` stored this neutral acknowledgement as an **interview with `needs_review=0`**, using that neutral sentence as its excerpt.
- **Reason/correction:** an evidence substring plus a personal/reply context is not evidence for the selected stage; `about_this_candidate` admits any non-bulk `Re:` reply. Supply a consistent current-event input to rules and model. If a historical thread subject is supplied, label it as historical context and prevent it from establishing the current stage. Require the quoted evidence to substantiate current candidate progression/the chosen outcome; neutral acknowledgements must lead to none/abstain/review rather than automatic high-confidence acceptance.
- **Regressions:** guide/handbook newsletters and rhetorical-question promotions without bulk headers; neutral replies whose old subjects name an interview/assessment; and model evidence that is genuinely present but reports no stage. Assert no accepted outcome events through sync, not just cleaner prompt text. Retain genuine invitations with newsletter footers and semantically valid model-only outcomes.

**Earlier independent follow-up (2026-11-12, Codex; see the latest verification below): PARTIALLY FIXED — F12 remained unchecked.**

- **Shared latest F08/F10/F11/F12 validation:** the full unit suite against isolated Docker source `/tmp/f08-f10-f12-followup.K9pISO` passed **882 tests**, with **13 dependency deprecation warnings**, in **310.96 seconds**. This includes the new follow-up regressions. The remaining failures documented here were additional independent inline reproductions, not failures in that passing suite. Verified all **131 Python source/unit-test files plus the vendored PSL data** still matched the tested snapshot. Used temporary SQLite/state, synthetic Chrome pages, mocked models and fake mailboxes/local process memory; no live application, real mailbox operation or model-provider request occurred. Only this review document was edited; application code/tests and `/app/src` were not changed. Command:

  ```bash
  docker exec -w /tmp/f08-f10-f12-followup.K9pISO \
    -e PYTHONPATH=/tmp/f08-f10-f12-followup.K9pISO/src \
    -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 -e PYTHONDONTWRITEBYTECODE=1 \
    -e TMPDIR=/dev/shm -e STATE_DIR=/dev/shm/f08-f10-f12-followup-state-K9pISO \
    -e OUTPUTS_DIR=/dev/shm/f08-f10-f12-followup-outputs-K9pISO \
    -e DATA_DIR=/tmp/f08-f10-f12-followup.K9pISO/data \
    jobagent pytest --basetemp=/dev/shm/f08-f10-f12-followup-K9pISO -q tests/unit
  ```

- **Verified corrections:** independently repeated the previous `Your guide to our online assessment...` and `Interested in our online assessment?...` negatives: neither reports an assessment now. The old `Re: Stripe interview invitation` subject is no longer reintroduced into the model prompt, and the model's neutral `Thanks for the update; I will get back to you tomorrow.` evidence no longer establishes an interview. Source/tests retain genuine invitation/footer and semantically valid model-only outcomes.
- **New held-out rule-and-storage reproductions:** subject **`Stripe careers newsletter`** and body **`Your assessment guide explains the online assessment used by teams.`** produce **`assessment / 0.9`**. The same subject with **`Your application handbook describes an online assessment, followed by interviews.`** produces the same classification. The real `sync` stored **both as assessment events with `needs_review=0`** in separate synthetic databases. Neither sentence assigns an assessment, invites this candidate, or reports application progression.
- **Reason:** in newsletter context, `about_this_candidate` returns `personal` directly, and `PERSONAL` treats the noun phrases `your assessment`/`your application` as sufficient evidence. The non-newsletter branch similarly uses `personal OR ASSIGN`. The added assignment/report detector is therefore bypassed by a personal noun phrase describing a guide or handbook. This preserves the original stage-phrase/newsletter override for another ordinary wording family.
- **Required correction:** distinguish references to a candidate/resource from assertions of actual progression. A personal noun phrase alone must not establish that an assessment was assigned; require an invitation, assignment, deadline/action or substantive report appropriate to the stage, especially in explicit newsletter/marketing context. Preserve genuine personal stage sentences with newsletter footers. Avoid repairing only these two word sequences.
- **Regressions:** descriptive guides, handbooks and informational announcements containing `your application`, `your assessment`, `your interview`, etc., with assessment/interview phrases elsewhere in the sentence. Test the real sync and assert no automatically accepted event. Retain direct personal invitations, assigned assessments and the now-fixed neutral-reply cases.

**Latest independent verification after the personal-resource follow-up (2026-11-12, Codex): PARTIALLY FIXED — F12 remains unchecked.**

- **Verified corrections:** repeated `Your assessment guide explains the online assessment used by teams.` and `Your application handbook describes an online assessment, followed by interviews.` under the newsletter subject. Both produce no outcome through the real sync. The raw reply subject remains excluded from the model input, and the neutral-interview evidence reproduction remains refused. The revised personal statement rules retain actual candidate assertions/invitations.
- **Remaining independent model-path reproduction:** current text was only **`Thanks for the update; I will get back to you tomorrow.`**. Tested a deterministic model answer for each stage, quoting that exact neutral sentence at confidence **0.95**. Confirmation, assessment, recruiter screen, interview and offer were refused. **Rejection and withdrawn were both accepted at 0.95.** With a submitted Stripe application, the real sync stored the rejection as **`needs_review=0`**, with that neutral acknowledgement as the deciding excerpt.
- **Reason:** `about_this_candidate` returns True for `stage in ('rejection', 'withdrawn')` after general marketing/process checks, without establishing that the evidence reports either decision. That assumption is reused by `model_verdict`; accepting a model's negative label cannot itself validate its supporting evidence. Fixing neutral evidence for invitation labels did not close this unconditional negative-label path.
- **Required correction:** require evidence in its current-message context to substantiate a rejection/withdrawal decision too. A neutral acknowledgement, greeting or reference to an application is not progression merely because the model chose a negative label. Unsupported evidence should produce none/abstain/review, while preserving semantically valid soft rejections and withdrawals that do not use the exact phrase-rule vocabulary. Do not default negative labels to True on the theory that they are inherently about one application.
- **Regressions:** the same neutral current text with model outputs across **all stage labels**, including rejection and withdrawal; assert no automatically accepted event through sync. Retain genuine soft-rejection decisions, actual withdrawals, held-out newsletter negatives and legitimate invitations with newsletter footers.

**Shared validation for this final F08 and follow-up F10–F12 review (2026-11-12, Codex):**

- Full initial snapshot `/tmp/f08-f10-f12-final.XmG82A`: **894 passed, 2 failed, 13 dependency deprecation warnings in 302.06 seconds**. One failure was the assertion in `test_a_different_message_of_the_same_company_with_the_reused_header_id_is_not_the_original` that forbade any reading of a separate, legitimately matching company message. The other was the previously intermittent `test_scrape_many_runs_in_parallel_and_skips_cached`, which again lost one fake parallel scrape result.
- While the full run was active, another agent changed **only `tests/unit/test_outcome_integrity.py`** to correct that expectation: allow normal processing of the sibling, but retain the original unresolved retry. Reviewed the exact diff and tested the current file in a new isolated snapshot `/tmp/f08-f10-f12-test-refresh.RKlBvA`: **57 passed in 3.04 seconds**. The parallel-scrape failure passed on one isolated rerun: **1 passed in 4.04 seconds**. This does **not** constitute a clean fresh full-suite run of the edited test tree.
- All **131 Python source/unit-test files plus the vendored PSL data** match the refreshed snapshot; the only difference from the full-run snapshot is that test file. Application code did not change during verification. The remaining F10–F12 failures above are additional independent reproductions and are not covered by these passing regressions.
- All checks used temporary databases/state, synthetic pages, fake mailboxes and deterministic mocked models. No real application, mailbox operation or model-provider request occurred. Only this review document was edited by Codex; application code/tests and `/app/src` were not changed. Initial full-suite command:

  ```bash
  docker exec -w /tmp/f08-f10-f12-final.XmG82A \
    -e PYTHONPATH=/tmp/f08-f10-f12-final.XmG82A/src \
    -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 -e PYTHONDONTWRITEBYTECODE=1 \
    -e TMPDIR=/dev/shm -e STATE_DIR=/dev/shm/f08-f10-f12-final-state-XmG82A \
    -e OUTPUTS_DIR=/dev/shm/f08-f10-f12-final-outputs-XmG82A \
    -e DATA_DIR=/tmp/f08-f10-f12-final.XmG82A/data \
    jobagent pytest --basetemp=/dev/shm/f08-f10-f12-final-XmG82A -q tests/unit
  ```

**Response to the 2026-11-12 verdict (round 3, Claude; checkbox left for Codex/user):**

- **Negative labels no longer default to true.** `about_this_candidate` accepts rejection/withdrawn only when the current message matches the stage's rule vocabulary (`RULES`) or the new soft-decision vocabulary (`DECISION`), in addition to the personal-assertion checks. `PERSONAL` needs assertions ("your application has been/is ...", "you have been ...", "invite/interview/offer you", rejection phrasing), not noun phrases, and the newsletter context only counts a label that precedes the stage sentence.
- **Tests** `test_outcome_integrity.py`: the neutral text `Thanks for the update; I will get back to you tomorrow.` with a model answer for EVERY stage label at 0.95 stores nothing through the real sync; soft rejections and withdrawals that avoid the exact rule phrases are kept; descriptive guides/handbooks stay refused; legitimate invitations with newsletter footers are kept.

## F13

**High — Queue recovery and claiming are unsafe with multiple workers.**

**Where:** [jobqueue.py](src/jobagent/tools/jobqueue.py), `requeue_stale`, `pending`, `mark`; [cli_discovery.py](src/jobagent/cli_discovery.py), `queue_run`.

**Issue and reason:** Recovery resets every running job without checking age or owner; an in-memory reproduction confirmed this. Selecting queued jobs and marking them running are separate operations, so two workers can claim the same job. The runner also treats CLI exit zero as filled even when apply reports blocked, and maps exit code 3 to blocked even when the actual result is unavailable/closed.

**Correction:** Claim jobs atomically with worker identity and leases. Recover only expired or dead leases. Consume structured workflow results instead of inferring business state solely from process exit codes. Preserve specific unavailable, blocked, failed and review states.

**Acceptance checks:** Two workers cannot claim one job. Starting another runner does not reset live work. A killed worker is recoverable. Closed jobs remain closed, planner failures remain failures, and review is not reported for blocked forms.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- **Claims.** `jobs` gained `claimed_by` and `lease_until`. `jobqueue.claim` chooses and marks the job `running` in one `BEGIN IMMEDIATE` write transaction, recording the worker id (`host:pid:random`) and a lease (default 15 min). `renew` extends it (a heartbeat thread in `queue_runner.Heartbeat`, own connection, every third of the lease); `finish` records an outcome only if the caller still holds the claim, so a stale runner cannot overwrite the new owner's job.
- **Recovery.** `requeue_stale` now puts back only a `running` job whose lease expired or whose owner is a dead process on this host. A live runner's job is never touched; a row from before leases (no owner, no lease) is recovered only after a full lease of inactivity; a job a step moved past `running` (scraped...) whose runner died keeps its status and loses the dead claim. The old test that asserted "reset every running job" was changed to the new contract.
- **Structured results.** `tools/queue_runner.run_queue` (extracted from `queue_run`) reads `application/application.json` (state, ready_to_submit, blockers, blocked_by) written during THIS apply (mtime check) and maps it: review+ready -> `filled`, review not ready -> `needs_attention`, blocked(challenge/page) -> `blocked`, blocked by the planner or stuck/failed -> `failed`; a posting a step recorded as closed/already applied stays exactly as the step recorded it. The exit code is only the fallback when no fresh result exists; if the two disagree the structured result wins and the note says so ("exit code N said X").
- **Tests** `tests/unit/test_queue_claims.py` (15 tests): 6 threads on separate connections claim 12 jobs exactly once each; best-match order and company filter; non-owner `finish`/`renew` refused; live work not reset while a dead owner's job is recovered; remote owner with a live lease not declared dead; legacy rows; exit 0 with a blocked result is `blocked`, not `filled`; planner failure stays `failed`; closed postings stay closed and the next job starts; a runner that lost its claim does not overwrite; stale `application.json` ignored; heartbeat extends the lease. `test_jobqueue.py`, `test_apply_exit_codes.py`, `test_cli_discovery.py`, `test_planner_failure.py` still pass.
- **Limits.** A killed runner on another machine is recovered only when its lease runs out. A step that is killed after changing `jobs.status` itself (e.g. scrape wrote `scraped`) is not re-queued; its claim is dropped and its status shows how far it got. The queue shells out to the CLI steps as before.

## F14

**Codex live integration follow-up (2026-11-12):** The actual Pantex run exposed a desktop-only startup gap: a listing's Language picker prevented `_start` from running. Desktop now uses the same guarded entry initializer as Docker and retains the intended posting as its return URL after authentication. A bounded SuccessFactors listing adapter requires the intended listing route/requisition, complete extraction, only Language utility controls, a carrier form with no native candidate inputs, and the literal CMS entry handler. Candidate fields, application routes, other requisitions and extra handler code are refused. Hidden listing state supports verified postback routes. Existing short-form final-Apply protection is retained. Real host preflight reached **Career Opportunities: Sign In** via the agent's guarded Apply navigation, without filling credentials or submitting an application; evidence: `outputs/verification/all-findings-2026-11-12/pantex-entry-preflight.png`. New regressions are in `test_start_navigation.py`; final suite results are recorded at the end. This is part of Codex's assigned F14 work, not a handoff to Claude.

**Medium — Desktop filling and production submission use different browser paths.**

**Where:** [main.py](src/jobagent/main.py), `apply_cmd`, `submit_cmd`; [browser.py](src/jobagent/tools/browser.py), browser context factories.

**Issue and reason:** Apply supports desktop fallback, but submit always launches the Docker browser. A successfully filled desktop application cannot continue through submit in that same session. This is a documented integration gap, not a reason to remove desktop support.

**Correction:** Share a browser-session abstraction between apply and submit and attach the reviewed submission hook to the intended session. Preserve the approval capability and document identity from F01/F04. Expose browser-mode configuration in queue execution too. Do not silently enable unattended desktop submission while fixing this gap.

**Acceptance checks:** A desktop application can proceed to an explicitly approved submission in the correct session when that workflow is authorized. Merely filling an application never submits it. Docker behavior remains supported and browser-mode selection reaches queue execution.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- **One session abstraction.** `tools/session.py` (`open_session`, `Session`, `check_mode`) is used by `apply_cmd` and `submit_cmd` (and `queue run` passes the mode on). `Session.run_options()` gives `steps.apply.run` the same `already_open`, `browser_policy` (the desktop `Scope`), `observe_s` and `desktop_fallback` arguments in both commands, so `submit` runs in the kind of browser `apply` filled in, with the reviewed-submission hook (`make_at_review`) attached to that session's page.
- **No silent unattended desktop submission.** `submit --browser-mode desktop` is refused unless `--allow-desktop-submit` is also given; `auto` is refused for submit (a submission cannot start in one browser and finish in another); neither opens a browser before refusing. In every mode the candidate's `approve` for that application is still required at the review prompt (F01/F04 approval capability and frozen payload untouched). `apply` still refuses `--submit`.
- **Queue.** `jobagent queue run --browser-mode docker|desktop|auto` (default docker) reaches the `apply` step; the queue never submits.
- **Tests** `tests/unit/test_browser_session.py` (9): the mode rules; run options identical for apply/submit; docker submit unchanged; desktop submit refused without the flag and nothing opens; desktop submit with the flag runs `already_open=True` with the policy and the hook; apply passes the same options and never attaches a hook; queue passes the mode to apply and rejects unknown modes.
- **Limits.** A real desktop bridge was not exercised (no host browser here); the desktop path is tested at the command/session boundary with the bridge replaced.

## F15

**Codex live integration follow-up (2026-11-12):** The first apply context added `locations_resolved` to `jd.json` after tailoring. This correctly changed its fingerprint, but needlessly invalidated completed document stages on restart. The in-process pipeline now resolves locations as a durable stage after research and BEFORE tailoring. Upstream research uses a canonical source-JD fingerprint excluding that derived field; bootstrap artifact hashes capture the owned enrichment. A new regression calls the real registered tailor callback twice around the actual location-resolution behavior and requires one resume/letter generation, one scrape/research invocation and two fresh apply reads. `run --no-ats` exposes the existing tailor opt-out for an application test after scan allowances are exhausted; default scans and document validity gates are unchanged. This correction belongs to Codex's assigned F15 work. Final verification results are below.

**Medium — Durable end-to-end workflow resume is incomplete.**

**Where:** [multi_agent.py](src/jobagent/steps/multi_agent.py), `build_graph`; [apply.py](src/jobagent/steps/apply.py), checkpoints; [main.py](src/jobagent/main.py), CLI orchestration; [cli_discovery.py](src/jobagent/cli_discovery.py), subprocess orchestration.

**Issue and reason:** The tailoring graph is compiled without a checkpointer. Application checkpoint JSON records progress but does not reconstruct the complete workflow after a crash. The planned top-level run orchestration remains split across commands/subprocesses. Reopening a page and rereading controls is useful recovery, but is not equivalent to durable workflow resume.

**Correction:** Persist stage results and immutable document versions under a workflow ID. Resume from the last validated stage while reconciling browser state. Treat external side effects as operations requiring reconciliation rather than blind replay. Centralize stage execution so apply, submit and queue do not diverge.

**Acceptance checks:** Termination during research, document generation, scoring and filling resumes without losing valid artifacts or repeating irreversible actions. Resume behavior is accurately documented. A completed stage is not unnecessarily rerun just because a later process crashed.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- **Durable stage results.** `tools/workflow.py`: `<job>/workflow.json` holds a workflow id and, per stage, status, start/finish, a fingerprint of its inputs and the SHA-256 of every output file. `jobagent tailor` runs `resume` and `cover_letter` as stages: a finished stage whose inputs (posting, template, company profile, model, sites) and output files are unchanged is reused ("reused ... inputs and outputs unchanged"); a changed input or an edited/missing output reruns it; a crash in the cover letter does not cost the resume. `--force` rebuilds. `jobagent workflow <job>` shows the stages. `apply` and `submit` record their stage; a stage left `running` by a killed process is shown as `interrupted`.
- **Side effects are reconciled, not replayed.** A stage declared `side_effect=True` that was interrupted needs a `reconcile` verdict (`done` / `not_done`) or the run stops with `NeedsReconciliation`. For submission the real protection remains F04's attempt records and `guard` (checked before any browser opens); the workflow record adds the audit trail and the message about an interrupted earlier submit.
- **Concurrency.** `tailor`, `apply` and `submit` take the job's build lock (F25) so two commands cannot work on one job.
- **Accurate resume documentation.** Resumption is at STAGE level. A stage that was running restarts from its beginning: the tailoring graph's state holds live objects (callbacks, models), so its internal rounds are not checkpointed with a LangGraph checkpointer (the bootstrap graph still is). What survives a crash is every stage that finished and the last published resume bundle (F25).
- **Not done.** `queue run` still shells out to the individual commands (which now share the same stage records) rather than a single in-process orchestrator; research and scrape keep their own caches rather than workflow stages.
- **Tests** `tests/unit/test_workflow_resume.py` (12): reuse with the stored result; input change / edited / missing output reruns; crash in a later stage keeps the earlier one; a killed process leaves `interrupted`; failed and unproduced stages are never reusable; interrupted side effects never replay and reconcile both ways; `tailor` reuse, `--force`, cover-letter crash, changed posting, build lock; `apply` records its stage.

## F16

**Medium — Wizard progress detection relies too heavily on headings.**

**Where:** [apply.py](src/jobagent/steps/apply.py), `_run` after Next/Continue.

**Issue and reason:** Heading comparisons primarily determine whether a page advanced. A wizard can keep the same heading while changing fields or steps. Successful navigation can be mistaken for refusal, previous-page records can be reused incorrectly, and the application can eventually block.

**Correction:** Combine URL, active step, field-schema changes and page state to detect progress. Keep records associated with their actual page. Distinguish within-page conditional changes from movement to a new wizard step.

**Acceptance checks:** At least three steps with identical headings and a constant URL complete correctly. Query/path-changing wizards also work. Conditional fields remain on their original page, and genuine refused navigation still produces a clear blocked result.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- `tools/wizard.py` judges progress from the ADDRESS (path, sorted query, fragment), the page's STEP MARKER (`aria-current`, selected tab, progress bar, Workday active step, "Step 2 of 4"), and the FIELD SCHEMA (kind + label + section of each question). New step = address or marker changed, or at least half of the earlier questions were replaced, or questions appeared where there were none, or they vanished into a page with only buttons (review). Added/removed questions inside the same step (conditional fields) are "same". A heading change over identical questions and buttons is NOT progress. A page with no questions and no buttons is "loading", so the wait continues. `steps/apply._run` uses `wizard.state_of` / `wait_for_progress`; records stay associated with their page because a conditional change is a retry of the same page and a real new step starts a new one.
- **Refusal** is still `same` twice -> `blocked_by: page` with the page's errors.
- **Tests** `tests/unit/test_wizard_progress.py` (10): the pure decision (same heading + new questions, single-question swap, conditional appear/vanish, heading-only change, address / hash route / marker change, review page vs loading, URL normalisation) and real Chrome pages: a three-step wizard with one heading and one address recognised step by step, a path-changing wizard, and a refused Next reported as the same step.
- **Limit.** The `_run` loop itself is exercised through the extracted functions on real pages; a full planner-driven run on a live portal was not repeated.

## F17

**Medium — Extraction errors and unsupported controls can disappear silently.**

**Where:** [forms.py](src/jobagent/tools/forms.py), `FIELDS_JS`, `frames_of`, `read_page`; [apply.py](src/jobagent/steps/apply.py), readiness/reporting.

**Issue and reason:** Frame extraction failures are caught and skipped. The JavaScript reader does not traverse shadow roots, and the frame enumerator excludes srcdoc/blank embedded documents. All discovered fields being verified therefore does not establish that all application controls were inspected.

**Correction:** Represent extraction completeness separately from answer verification. Report unreadable frames and unsupported control regions. Add open-shadow-root and srcdoc coverage where practical; otherwise provide an explicit human handoff or adapter requirement. Keep the useful portal-specific widget handlers rather than pretending every widget is generic.

**Acceptance checks:** An unreadable frame cannot produce a falsely clean report. Representative open-shadow, srcdoc and custom-widget forms are either handled or explicitly marked unsupported. Normal embedded ATS forms and challenge-frame exclusions continue working.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- **Completeness is separate from verification.** `forms.read_page` now returns `PageView.coverage` (`Coverage`): frames read, frames that could not be read (with a trimmed address and reason, after one retry; a frame that is gone is not counted), human-check frames left out on purpose, open shadow roots read and fields found in them, and opaque regions (a visible custom element with no light children and no open shadow root, 120x24 px or more: a closed shadow root). `apply` stores it per page (`pages[n].extraction`), sets `extraction_complete`, and turns every problem into a readiness blocker ("page N: the form was not fully read: ..."), so a form with an unread frame is never `ready_to_submit`.
- **Coverage added.** Open shadow roots are traversed by the field reader (labels and groups resolved inside their own root; Playwright's selectors pierce open roots, so the controls can be filled; the `data-jf` cleanup is deep); `srcdoc`, `about:blank`, `data:` and `blob:` frames are read; only challenge widgets stay excluded (and are listed). Portal-specific widget handlers (Workday, Ashby, react-select) are untouched.
- **Tests** `tests/unit/test_extraction_coverage.py` (7, real Chrome): open shadow root read and filled; srcdoc form read; ordinary embedded ATS form still read and a reCAPTCHA frame excluded and listed; a frame that throws is reported and the readable part kept; a closed shadow root reported as opaque; small custom elements (icons) not flagged; clean page has no problems. Existing `test_forms.py` and `test_start_navigation.py` still pass.
- **Limits.** Closed shadow roots cannot be read, only detected heuristically; option lists (`OPTIONS_JS`) inside shadow roots are not traversed.


**Independent live follow-up (2026-11-13, Codex): PARTIALLY FIXED; F17 reopened.**

- The assigned closed-author-shadow-root implementation is verified: existing roots are recovered by public CDP, future roots retain closed mode, and deep read/action tests pass. The broader completeness acceptance check still fails at approval.
- **Reproduction:** construct a `PageView` with no fields/errors and `Coverage(unreadable=[{"url":"https://fixture.test/frame","reason":"unreadable"}])`; `coverage.complete` is false but `steps.submit.live_blockers(None, view)` returns `[]`. `make_at_review` then replaces the earlier blockers with an empty list and sets `ready_to_submit=True`. Thus initial apply coverage blockers are insufficient to protect the final click. This is a separate failure from reading closed roots.
- **Live inventory gap:** the signed-in Pantex My Candidate Profile exposed buttons “Click here to attach your Resume” and “Click here to attach your Cover Letter”, but its 24-field inventory contained no file fields and coverage was reported complete. The lazy file-trigger recognizer must handle these attachment links or explicitly report them unsupported. Coordinate this with F28's role-scoped upload identities; do not count two visible attachment controls as zero questions.
- **Correction for Claude Code:** include fresh extraction problems in approval blockers, and retain or explicitly reconcile unreadable earlier wizard pages rather than silently clearing their historical blockers. Extend supported attachment-trigger discovery with representative legacy controls, or produce a visible coverage/adapter requirement. Add real approval-hook regressions asserting that an unreadable frame cannot reach the final click, including an earlier wizard page, and a legacy two-attachment fixture that cannot report complete with both controls omitted. Keep challenge-frame exclusions intact.
- **Disposition:** leave F17 unchecked until these bounded completeness gaps are fixed. The controller must inspect actual coverage and attachment roles before approving either authorized live application; the existing successful closed-root tests do not establish this broader guarantee.

## F18

**Codex verification (2026-11-12): STILL OPEN — handoff to Claude Code.** The original-index exemption is not validated against the template. `FillBullet(text="New and changed text " + "x" * 160, duties=[0], original=0)` accepts 181 characters despite `BULLET_MAX=130`; rendering retains changed text. Require template-aware equality before exempting length, or normalize an original reference to the exact template text and refuse altered text. Align prompts, schemas, repair feedback and final validation. Add a regression for an over-budget changed bullet carrying a valid original index, plus a genuine verbatim original that is legitimately exempt. See the current audit below for the independent probe. Do not check off until both cases pass. No F18 implementation changes were made by Codex.

**Medium — Output schemas contradict document-length requirements.**

**Where:** [tailor.py](src/jobagent/tools/tailor.py), `FillBullet`, `Filled`, repair schemas and prompts; [multi_agent.py](src/jobagent/steps/multi_agent.py), `SummaryOut`, validators.

**Issue and reason:** Bullet schema allows 300 characters and describes aiming for 200, while the prompt asks for roughly 110 and no more than 130. Summary schema allows 800 while validation requires 450. Structurally accepted outputs are repeatedly rejected later, increasing latency without addressing the inconsistent contract.

**Correction:** Share budget definitions across generation schemas, prompts, repair schemas and validators. Include actual validation failures in repair requests. Detect infeasible combinations of coverage and page constraints before repeatedly calling models. Retain locked template regions and validate the rendered page; character budgets alone do not guarantee fit.

**Acceptance checks:** Over-budget outputs receive coherent schema/repair feedback. Long JDs terminate with an honest validity result rather than repeated contradictory requests. Valid concise content is preserved, and the one-page gate still uses the PDF.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- **One budget definition** (`tools/tailor.py`): `BULLET_TARGET=110`, `BULLET_MAX=130` (a bullet copied verbatim from the template is exempt, schema ceiling 300), `SUMMARY_TARGET=400`, `SUMMARY_MAX=450` (was 800 in the schemas), `SUMMARY_TIGHT=300`, `PARAGRAPH 300-420` (was 700). The generation schemas (`FillBullet`, `Filled`, `filled_schema`, `SummaryOut`, `BulletFix`, `ProjectFix`, `KeywordPass`), their field descriptions, the fill/repair/keyword/projects prompts and the validators (`summary_issues`, the page-overflow tasks) all read these constants. A new bullet over budget now fails the schema with the measured length, the limit and the text, which `structured_invoke` (F19) sends back as repair feedback; it is no longer accepted and then rejected later.
- **Infeasibility** is found before the models are called: `coverage_target` / `feasibility` (3 items per bullet, roles x minimum bullets). Required items beyond what the available bullets can hold are not demanded of the writer (`fill_checks(..., slots)`), are listed as `coverage_limited`, and appear as a warning in `documents.json` ("resume coverage: ...") - an honest validity result instead of repeated contradictory requests.
- **Kept.** Locked template regions and the rendered one-page PDF gate are unchanged; character budgets are a first filter only.
- **Tests** `tests/unit/test_length_budgets.py` (8): over-budget new bullet refused with length and limit, verbatim bullet exempt, Summary schema and validator share 450, prompts/descriptions quote the budgets, overflow rule uses the shared tight budget, coverage arithmetic, `fill_checks` with slots. One older test (`test_ats_loop`) now builds the 600-character summary past the schema, as the guard it checks is meant to hold even then.

## F19

**Codex verification (2026-11-12): STILL OPEN — handoff to Claude Code.** An in-flight model request can outlive the total deadline and return success. Independent probe: a fake structured invocation sleeping 0.12 seconds succeeds with `deadline_s=0.01`. Clamp each attempt's transport timeout to the remaining shared deadline, handle non-cooperative calls/cancellation explicitly, and reject late completions. Cover nested shared deadlines, retries/fallbacks near expiration and successful replies arriving after expiration. Preserve existing failure classification and repair feedback. See the current audit below. No F19 implementation changes were made by Codex.

**Medium — LLM retry, deadline and fallback policies are inconsistent.**

**Where:** [llm.py](src/jobagent/llm.py), `writer`, `structured`, `structured_invoke`; callers with additional retry loops.

**Issue and reason:** Writer disables client retries and has no Ollama fallback. Structured invocation tries output methods, but many failures are treated as reasons to try another method rather than categorized as authentication, transient service, unsupported-method or content-validation errors. Nested retries can cause long runs and repeated unsuitable requests.

**Correction:** Classify errors and apply an overall deadline and retry budget. Back off transient capacity/rate-limit failures, fail promptly for configuration/authentication problems, switch methods only when appropriate, and repair invalid content with useful feedback. If writers intentionally remain Voyager-only, document it; otherwise use an explicit common fallback policy and record the actual provider/model.

**Acceptance checks:** Authentication failure does not trigger repeated schema-method calls. Rate limits respect bounded retry/backoff behavior. Unsupported output modes can fall through appropriately. Total call duration remains bounded across nested callers, and fallback metadata is accurate.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- **Classification** (`llm.classify`): config (401/403/404, exhausted quota) -> `LLMConfigError` after ONE call, no other output method tried; rate limit / 5xx / connection -> wait (Retry-After if sent, else exponential with jitter, capped 30-60 s) and retry the SAME method, `llm_rate_retries` times; unsupported (400/422) and token-cap -> next method; timeout -> one other method then stop; invalid content -> asked again once WITH the refusal reason (pydantic field and rule), then the next method.
- **One budget per call**: `llm_call_deadline_s` (900), `llm_max_calls` (8), plus an outer `with llm.deadline(seconds)` that bounds nested callers. `StructuredError.kind` says what stopped it.
- **Fallback policy.** `chat()` / `structured()` fall back to Ollama only for an unavailable Voyager (rate limit, 5xx, connection, timeout) - a wrong key or model no longer silently becomes a small local model. The **writers are Voyager-only by design** (documented in `llm.writer`: a small local model's resume is worse than a delayed one); `last_call()` records provider, model, method, calls, waited seconds and error kinds, and `FALLBACKS` lists every Ollama fallback taken.
- **Tests** (`tests/unit/test_llm_structured.py`, 15 total, 9 new): auth/unknown model/quota = one call; Retry-After honoured; bounded waits; unsupported falls through; repair feedback reaches the model; call budget and deadline (including the outer context); only transient errors fall back; metadata accurate.
- **Limit.** A single attempt can still run up to its own client timeout after the deadline check; the deadline stops new attempts.

## F20

**Medium — ATS input payloads and repeated-score provenance need correction.**

**Where:** [ats_sites.py](src/jobagent/tools/ats_sites.py), adapters, `score_all`, `cached`; [ats_providers.py](src/jobagent/tools/ats_providers.py).

**Issue and reason:** Several default adapters silently send only the first 8,000 JD characters, while other sites receive the full JD. Long postings are scored against different effective requirements. Repeated-score aggregation also overwrites the first result before saving the individual scores: original scores 60 and 80 become recorded runs 70 and 80. Cached results need consistent provider/parser provenance as adapters change.

**Correction:** Record the actual submitted payload, truncation, payload hash, provider/parser version and immutable raw observations. Keep aggregates separate from source observations. Treat provider UI limits explicitly rather than silently implying the full posting was analyzed. Quota exhaustion should produce a clear incomplete/waiting state, not pointless retries or a reduced-denominator average.

**Acceptance checks:** Long-JD payload differences are recorded and exposed. Raw repeated scores remain unchanged after averaging, including partial failures. Parser-version changes invalidate incompatible cached results. The required five-site average remains incomplete until its required observations exist for the exact version.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- **Payloads.** The four adapters that cut the posting at 8,000 characters (AutoApplyMax, HireFlow, ResumeAtlas, ATS Friendly) now take it through `jd_payload`, which records total/sent characters, the limit, whether it was truncated and both SHA-256s in `breakdown["payload"]`; providers that take the whole text record that too. `ats_result.gather` carries the payload in `provenance` and lists `truncated_posting` in `ats_summary.json`.
- **Raw observations.** `ats_sites.observe` keeps every run exactly as observed in `breakdown["observations"]` (deep copy; a mean never overwrites them). The aggregate is a separate value. If any required run fails, the site has no score (a mean of fewer runs would be a different measurement), the successful observation is kept, and an exhausted allowance / challenge stops the remaining runs at once.
- **Versions.** `PROVIDER_VERSIONS` (jobscan 2, resumeatlas 2, others 1) is stored per result; `cached` reuses a score only for the same provider version (a legacy row without one only for a provider still at version 1). The required five-site average stays incomplete (`null`) until every required site has a score for the exact PDF, job and version.
- **Tests** `tests/unit/test_ats_provenance.py` (8) plus the existing ATS suites: truncation recorded and surfaced; 60/80 stay 60/80 next to 70; a failed repeat leaves no score and keeps the first observation; quota stops after one call; version mismatch / legacy / bump invalidate the cache.
- **Limit.** The provider adapters in `ats_providers.py` were not changed; they receive the full posting.

## F21

**Medium — The ghosting cutoff is not censoring-aware.**

**Where:** [outcome_stats.py](src/jobagent/tools/outcome_stats.py), `ghost_cutoff`.

**Issue and reason:** The function uses a percentile of responders only, not the Kaplan–Meier distribution described in the plan. An isolated check with 20 replies at day two and 80 applications still silent at day 30 produced a 21-day cutoff. That does not establish that 90% of applications should have responded by then.

**Correction:** Define whether ghosting is a product heuristic or a statistical estimate. Use a censoring-aware quantile only when identifiable; otherwise retain a conservative fallback and disclose uncertainty. A percentile conditional on observed responders must not be described as an unconditional response deadline.

**Acceptance checks:** A small set of fast responders does not manufacture a well-established deadline for everyone else. Cohorts with substantial censoring or nonresponse return an explicit fallback/uncertain result. Late responses still update labels correctly.

**Reference:** [NIST Kaplan–Meier reference](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/kaplan.htm).

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- **Decision: the ghost cutoff is a statistical estimate only when identifiable; otherwise a labelled heuristic.** `ghost_cutoff_info` returns (days, basis). It is the Kaplan-Meier 90th percentile of ALL applications (responders and still-waiting, censored) - per portal, else overall - only if at least 20 responses exist, the curve actually reaches 90% and at least 15 applications are still informative there. Otherwise the default 45 days is used and the basis says "uncertain". A percentile over responders alone is never used. `stats` prints the cutoff and its basis.
- **Tests** (`test_outcome_stats.py`): 20 replies on day 2 plus 80 silent applications -> 45 days and "uncertain", nobody ghosted on day 30; a curve that cannot reach 90% (old silent applications) falls back; clean cohorts still learn and clamp (21-120); a late reply updates the label from ghosted to positive.

## F22

**Medium — Rate definitions and historical evaluations need correction.**

**Where:** [outcome_stats.py](src/jobagent/tools/outcome_stats.py), `load`, `status_of`, `group_by`; [cli_outcomes.py](src/jobagent/cli_outcomes.py), `stats`, `reflect_cmd`.

**Issue and reason:** Response/positive rates among resolved applications are not overall callback probabilities: fast responders and older applications are selected into that denominator. This distinction is documented but easy to misinterpret. Historical evaluations also include applications submitted after `as_of`; an isolated check produced a pending application with age minus five days.

**Correction:** Separate resolved-cohort composition from response probability at a fixed horizon. Exclude applications that did not exist at the evaluation date. Define denominators and uncertainty clearly in CLI/CSV output and learning inputs.

**Acceptance checks:** Historical reports exclude future submissions and future events. Cohorts with different submission dates and delayed replies produce understandable rates. No negative application ages appear. A resolved-only proportion is never labelled as an unconditional callback probability.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- `outcome_stats.visible` / `statuses` exclude applications submitted after `as_of` (no negative ages); `group_by`, `observations` and `learning.resolved_rows` skip ids not evaluated. `stats` and `reflect` evaluate only visible applications.
- **Definitions.** `stats` columns are now "replied (share of resolved)" / "positive (share of resolved)" (resolved-cohort composition, with Wilson intervals) and "replied by 30d (KM)" / "median d (KM)" (Kaplan-Meier, censoring-aware, shown only with enough applications at risk). The `Group` dataclass documents that the resolved share is not a callback probability; the CSV carries `n`, `resolved`, the counts and `horizon_days` beside the rates.
- **Tests:** future submissions excluded from statuses, groups and visible lists, ages never negative (`test_outcome_stats.py`).

## F23

**Codex verification (2026-11-12): PARTIALLY FIXED, STILL OPEN — handoff to Claude Code.** Weighting, company-cluster permutations, within-analysis max-statistic correction and temporal confirmation are implemented. The original acceptance criterion also includes repeated analyses: successive overlapping cohorts are currently tested at the same alpha without a sequential budget or declared fixed-look protocol. Choose/document a fixed-look or sequential policy, then calibrate the whole deployed policy across null streams with repeated looks, all comparison families and company dependence. Report uncertainty; Monte Carlo slack in per-analysis tests (6/50 or 11/100) does not establish an arbitrary repeated-monitoring 5% guarantee. This is an evidence/scope gap, not proof that the per-analysis method is defective. Keep behavioral switches off. See the current audit below. No F23 implementation changes were made by Codex.

**Medium — Learning ignores ghost weights and misses its original false-positive gate.**

**Where:** [learning.py](src/jobagent/tools/learning.py), `resolved_outcomes`, site/keyword analyses; [outcome_stats.py](src/jobagent/tools/outcome_stats.py), `Status.weight`; [test_learning.py](tests/unit/test_learning.py), null-world test.

**Issue and reason:** Ghosted applications carry weight 0.5 in Status but become ordinary binary negatives in learning. The null-world test permits recommendations in 10% of simulations, whereas the plan requests at most 5%. Multiple sites, role families and repeated analyses must be considered together. Score predictiveness also does not show that using a checker or inserting a keyword causes more replies.

**Correction:** Implement the intended weighting or explicitly revise and validate the model. Calibrate the complete recommendation procedure, including repeated analyses and correlated applications, against its stated false-positive tolerance. Use held-out temporal evaluation and account for role/company differences. Keep behavior-changing switches off by default.

**Acceptance checks:** Ghost weighting has the documented effect. Null simulations evaluate the complete recommendation family at the agreed tolerance. Held-out validation is distinct from tuned fixtures. Reports distinguish association from causal improvement.

**Plan correction:** Predictive-score analysis is a defensible replacement for the proposed bandit. Do not casually implement the plan's fractional failures for still-pending applications: that conflicts with treating those observations as censored.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- **Model revised and validated** (`tools/learning.py`). Ghosted applications carry weight 0.5 in every statistic (weighted AUC with Kish effective sizes; weighted positive-share difference); pending applications stay censored and are NOT counted as fractional failures (the plan's idea is not implemented, per the plan correction). Skills are compared only when the snapshot's skill list is the submitted one (`snapshot_status` not mismatch/unknown/unverifiable).
- **The whole recommendation family is one test.** `analyze` finds eligible comparisons (every site and every skill-by-role-family over the data floor) on the earliest 70% of resolved applications and tests them TOGETHER by a Westfall-Young max-statistic permutation test (outcomes shuffled within role families, whole companies moving together, companies of equal size trading outcome vectors); adjusted p < 0.05 is the family-wise error target. The latest 30% only confirm: a finding is actionable only if the later applications point the same way; `contradicted` is reported and not acted on; `untested` stays unconfirmed.
- **Association, not cause.** Every finding text says so; role differences are handled by stratification and company dependence by cluster shuffling; seniority, referrals and timing are not in the data and the report says that. The switches stay off by default (`learning_use_site_mix` is reported as unsupported - see F24).
- **Calibration measured** (not tuned on): effect-free worlds, whole procedure, 99 shuffles: `test_the_whole_recommendation_family...` allows at most 6 of 50 worlds (nominal 2.5); clustered-company worlds: 6/200 alarms (3%) with company shuffling versus 19/200 (9.5%) when applications are shuffled one by one (test allows 11 of 100). Planted effects (odds x3, AUC slope 1.0, 900 applications) are recovered in at least 5 of 6 worlds with at most one noise flag; a reversing effect is `contradicted` and not actionable.
- **Tests** `tests/unit/test_learning.py`; the old BH/Fisher/bootstrap tests were replaced because those functions no longer exist.
- **Limits.** The hold-out is a single temporal split; with few resolved applications nothing is recommended ("not enough data"). Permutation count is 299 by default (smallest adjusted p 1/300).

## F24

**Medium — Outcome review, dry runs and lesson provenance are incomplete.**

**Where:** [outcomes.py](src/jobagent/tools/outcomes.py), `sync`, `decide`, `label`; [submit.py](src/jobagent/steps/submit.py), `application_match`; [cli_outcomes.py](src/jobagent/cli_outcomes.py); [learning.py](src/jobagent/tools/learning.py), `_snapshot`, `reflect`, `site_mix`.

**Issue and reason:** Dry-run sync constructs matchers through a function that writes agent-domain records. The review UI does not display the Message-ID expected by its label command and cannot fully repair an accepted false positive. Lesson hashes omit scores, keywords and analysis parameters; reflection can retain old evidence when rounded prose remains unchanged. The site-mix switch is exposed but its behavior hook is documented as unwired.

**Correction:** Separate pure matching configuration from domain registration and test dry-run database invariance. Provide consistent event-ID-based assign/relabel/discard operations and synchronize derived status fields after corrections. Snapshot complete evidence and update it even when prose is unchanged. Either wire the site-mix switch into a clearly defined policy compatible with the five required scorers, or explicitly expose it as unavailable rather than effective.

**Acceptance checks:** Dry runs leave business records unchanged. Accepted false positives can be removed from analysis without direct SQL. Review shows usable identifiers. Evidence changes produce an auditable version change. Every enabled feature switch either has a tested effect or gives a clear unsupported message.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- **Dry runs.** `application_match(conn, jd, register=False)` builds the same matcher without recording the portal as an agent domain (`db.agent_domain_family` is the pure part of `record_agent_domain`); `sync(dry_run=True)` uses it. `test_a_dry_run_sync_leaves_every_table_exactly_as_it_found_it` compares a dump of every table before and after a dry run that reads and would store mail.
- **Repairing an accepted false positive without SQL.** New by-event-id operations: `outcomes events [-a APP]` lists every event with the id the commands take (counted / waiting / ambiguous / discarded); `outcomes discard <id>` removes an event from the analysis even if already counted (row kept as rejected so the message is never re-added; the application's derived confirmation state is withdrawn; replies that followed the event return to review; audited); `outcomes label <id> <stage>` (an event id, or a Message-ID as before) relabels by event, moving confirmation state both ways and auditing; `assign` and `approve/reject` unchanged. The review table already printed event ids; its help text and the `label` help now say so.
- **Lessons.** `_snapshot` hashes every resolved application's outcome, weight, scores, skills, role, snapshot status and company, plus the analysis parameters; `reflect` compares text, evidence AND snapshot, so a lesson becomes a new version (old one retired) whenever its evidence changes even if its sentence does not.
- **Site-mix switch.** `learning_use_site_mix` is reported as unsupported with no effect (`site_mix_unsupported`, printed by `reflect`); `site_mix` always returns every required scorer, so the five-site average stays comparable.
- **Tests** `tests/unit/test_outcome_review.py` (6) and `test_learning.py` (versioning, snapshot coverage, switch).

## F25

**Medium — Persistent identities and writes need stronger transactional boundaries.**

**Where:** [db.py](src/jobagent/db.py); [submit.py](src/jobagent/steps/submit.py), `record`; [outcome_stats.py](src/jobagent/tools/outcome_stats.py), `snapshot`; [vault.py](src/jobagent/tools/vault.py), `set`, `_save`; [multi_agent.py](src/jobagent/steps/multi_agent.py), rollback/version publishing.

**Issue and reason:** Application identity depends on raw job-directory strings, so relative/absolute paths can produce separate records. URL canonicalization differs across stages, and snapshot role lookup can compare an apply URL against a stored source URL. Vault mutations are unlocked read-modify-write operations: atomic rename prevents partial files but not lost concurrent updates. Resume rollback does not restore all corresponding layout metadata.

**Correction:** Use stable job/attempt IDs, URL aliases, explicit foreign keys and versioned migrations. Store paths as attributes, not business identity. Lock vault mutations and publish each document version as a coherent artifact bundle, including layout/project metadata. Protect a job against concurrent tailoring.

**Acceptance checks:** Relative/absolute paths and equivalent URLs resolve to the intended job/attempt. Source/apply URL differences do not lose role-family linkage. Two concurrent vault updates preserve both entries. Rollback restores matching PDF, source and metadata, and interrupted publishing cannot expose a mixed version.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- **Identity.** `db.job_key` canonicalises a job folder to `outputs/<company>/<job-id>` (relative, `./`, trailing slash, `/app/...`, host absolute all equal); `submit.record/guard/begin_attempt/mark_action/settle_attempt`, `outcome_stats.snapshot`, `ats_sites.record/manual_scores` and `ats_result` use it. A one-time idempotent migration at connect rewrites existing rows; a canonical key already taken by another application leaves the legacy row untouched and is listed in `kv.job_key_conflicts` (never merged silently). Paths remain attributes of where the files are.
- **Addresses.** `url_aliases` records an application's apply link against the posting's own address; `db.job_for_urls` finds the `jobs` row through the given address, its canonical form (tracking parameters ignored) and aliases both ways; the snapshot's role family is found that way, also from the folder's `jd.json` addresses, so an apply URL that differs from the stored source URL no longer loses the role.
- **Vault.** Every mutation (`set`, `delete`, `restore_backup`) holds an exclusive `flock` on `<vault>.lock` across read-modify-write; 24 concurrent writers all persist.
- **Versions.** `tools/bundle.publish` stages `resume.tex`, `resume.pdf`, `projects.json` and `filled.json`, writes a journal, then moves them; `recover` completes an interrupted publish (roll forward) and clears staged leftovers; a version without a projects layout removes the file. Multi-agent rollback, the final best-version write and the ATS-loop `undo` use it; `documents.verify` reports an interrupted publish as invalid until `tailor` completes it.
- **Concurrency.** `bundle.job_lock` (non-blocking flock on the job folder) guards `tailor`, `apply` and `submit`.
- **Not done.** Surrogate integer IDs and explicit foreign keys between `applications`, `submission_attempts` and `jobs` were not introduced (rows are keyed by the canonical folder key); `ats_runs` has no foreign key. `outcome_events` etc. were already keyed by `application_id`.
- **Tests** `tests/unit/test_identity_persistence.py` (11): spellings, one application, migration and conflict, URL aliases, snapshot role through the apply link, concurrent vault writers, publish all-or-nothing, crash before/after the journal, rollback restoring metadata, build lock.

## F26

**Medium — Discovery silently relaxes validation and does not enforce advertised budgets.**

**Where:** [discovery.py](src/jobagent/tools/discovery.py), `discover`; [matching.py](src/jobagent/tools/matching.py), `precomputed`, `match_listing`.

**Issue and reason:** Judge failures or the judgment cap fall back to regex-only decisions that can queue jobs even though the normal preference check did not run. The advertised semantic cap only limits precomputation: the lookup function embeds missing entries on demand, including entries outside the capped batch. This weakens selection quality and can make latency/cost exceed the stated budget.

**Correction:** Default degraded results to needs_review. Retain regex-only automatic acceptance only for an explicit user-selected mode such as --no-judge. Enforce budgets at execution time, record which checks completed and avoid logging a capped workload while performing uncapped work.

**Acceptance checks:** Provider failure and budget exhaustion do not silently produce fully validated queue entries. Explicit no-judge mode behaves as documented. Actual model/embedding calls stay within configured budgets and excess candidates remain inspectable rather than disappearing.

**Resolution notes (2026-11-12, Claude; checkbox left for Codex/user):**

- `matching.precomputed(texts, embed, batch, cap, stats)` embeds ONLY the capped batch and raises `BudgetExceeded` for any other text (it never embeds on demand); a listing outside the embedding budget is skipped with "not evaluated (the embedding budget was reached)". `match_listing(..., degraded=)`: when the judge failed or its cap was reached, the listing goes to `needs_review` ("NOT validated by the judge"), never straight to the queue; explicit `--no-judge` keeps its documented behaviour. `Result.budget` reports embedding/judge caps and counts (texts, calls, judged, failed, skipped, unevaluated) and the log line reflects them.
- **Tests** `tests/unit/test_discovery_budget.py` plus the existing matching/discovery suites.

## F27

**Medium — Portal login rejection counting blocks registration and cycles between account forms. New live-run finding; handoff to Claude Code.**

**Where:** `src/jobagent/tools/portal_agent.py`, `run`, especially the `wrong_pw` increment after `snapshot` and the guard rejecting every `{PASSWORD}` fill for `goal == "sign_in"`.

**Observed evidence:** Real Pantex host-browser run on 2026-11-12. One Sign In action produced `Invalid email address or password.` Subsequent snapshots of that same visible error incremented `wrong_pw` again, producing "password rejected twice" while the agent was following Create Account. The agent then alternated Sign In/Create Account, refused password fills even on the registration form, and ended in its repeated-fill stop. Account registration did not complete. Both the standalone production auth agent and the application entry flow showed the cycle. No final job submission was attempted. Private diagnostic: `outputs/verification/all-findings-2026-11-12/pantex-auth-failure.log` (no password values printed).

**Additional verified registration-control gaps (same account agent):** A fresh registration-only run avoided the old login loop, but repeatedly issued `fill` against Pantex's native Country/Region Code SELECT. Its actual choices included `United States (+1)`; the auth snapshot did not expose those options, and action handling did not guide the model to `select`. A controller used the existing general `forms.fill_field` helper to select the option and reconcile the registration fields, then returned control to the account agent. Its Create Account click reached a signed-in My Candidate Profile page, candidate ID 120005, with Sign Out. The intermediate plain `Loading...` page was incorrectly classified as an `unknown` puzzle and sent to HITL; no actual challenge was shown or solved. The controller resumed only after the ordinary signed-in page was visibly loaded. Account-state, native-select handling and transient-loading classification all need regressions before F27 is resolved.

**Reason:** Error observations are counted per loop iteration rather than per distinct submitted credential attempt/response. The lockout guard covers the overall `sign_in` goal, including its registration subflow, so rejecting a login password also prohibits setting a new account password. Alternating page actions evade the immediate repeated-action check for many steps.

**Correction for Claude Code:** Track distinct credential submissions and consume each rejection once. Scope rejected-login protection to login attempts for the same account; separately identify explicit registration/new-password forms. Permit authorized account creation without weakening login lockout protection or allowing password resets implicitly. When an account's status/credentials cannot be resolved, stop with a clear human handoff instead of cycling. Preserve the explicit `reset_once` authorization boundary and masked credential logging. Do not assume generic invalid credentials prove no account exists.

**Acceptance checks:** Native select snapshots expose label/value options and an incompatible fill produces actionable feedback or uses the general forms selector; the phone-code select completes. Ordinary transient Loading pages are waited out rather than claimed to be human puzzles. One unchanged rejection across three reads counts as one rejection; two real rejected submissions stop further login attempts; following Create Account after a failed login can complete the registration subflow without inheriting the login-only password ban; alternating Sign In/Create Account is bounded and hands off; an existing account with unknown credentials is not silently reset; no password appears in logs. Confirm through a real permitted portal after fixture tests.

**Current disposition:** Unresolved; no F27 implementation changes made by Codex, per the owner's instruction to hand off items outside the four assigned fixes. That earlier account-setup blocker was subsequently cleared by the documented controller-assisted registration; candidate ID 120005 is signed in. The production F27 defects remain open. The F14 session fixes retain the live POST-backed login page and stop application filling on explicit auth failure; they do not claim to solve this separate account-state bug.

## F28

**High — Multiple lazy upload widgets can receive the wrong document, produce duplicate records and misattribute readback. New live-run finding; Claude Code handoff.**

**Where:** `tools/forms.py` (`FIELDS_JS` native/lazy upload discovery, field IDs/slots, `fill_field` chooser association); `steps/apply.py` (`fill_page` field identity and file readback).

**Observed evidence:** The real Carvana 8188615 desktop run filled the answers, then its Resume/CV widget contained `cover_letter.pdf` while the Cover Letter widget was empty. The report contained two Resume/CV records and two Cover Letter records (slots 0 and 1 for each), although only one widget of each role remained on the page. It marked the cover letter's first record verified because its filename existed elsewhere on the page. Later retries failed with `Upload button did not expose one identifiable file input`. The required-resume guard correctly stopped submission. Evidence: `carvana-upload-state.png` and `application/before-upload-reconciliation.json` under `outputs/verification/all-findings-2026-11-12/` / the Carvana application folder. The browser retained no native file inputs. Explicit controller repair using the existing labelled drag-and-drop widgets successfully placed both correct files (`carvana-upload-repaired.png`, `carvana-upload-repair.json`). That is a test workaround, not an autonomous uploader fix.

**Reason / investigation:** The evidence establishes cross-widget document assignment, duplicate native/lazy identities, and page-wide filename readback accepting another widget's file. Isolate whether transient inputs, re-rendered `data-jf` indices, chooser ownership or their combination causes the mis-targeting. Do not assume a numeric slot or a filename anywhere in `body` proves widget identity. Existing single-widget lazy-upload tests do not cover this case.

**Correction for Claude Code:** Give each upload a stable, scoped widget identity through native-input creation/removal and re-render. Resolve the live target at action time and associate each chooser/input with its initiating widget. Merge a transient native control and its lazy trigger only with explicit association evidence; preserve distinct genuinely separate uploads. Verify filenames/bytes within the corresponding widget rather than the whole page. Support the site's exposed drag-and-drop path when no identifiable native chooser exists, or hand off explicitly without misreporting success. Keep source PDF roles and immutable attempt payloads distinct.

**Acceptance checks:** Two independent lazy widgets (resume + cover letter), asynchronous transient/detached inputs and a re-render between actions never swap documents. One real widget does not become two records. A cover letter filename under Resume/CV cannot verify Cover Letter. Required mismatches block submission; correct repair can be reconciled without discarding the original audit history. Verify a real allowed portal after fixture tests.

**Current disposition:** Unresolved; no F28 source changes made by Codex, per the owner's scope instruction. The Carvana test uses documented controller-assisted repairs and preserves the initial records separately.

## Cross-cutting plan, testing and deployment requirements

- Maintain a current behavior contract plus dated decisions. The reviewed plan mixes greenfield assumptions, superseded exclusions, completed features and unfinished promises. Update it without removing the newer user-approved features.
- Prioritize the approval boundary, mailbox cleanup, false-success handling and document/score identity before unattended production use.
- Add the counterexamples above to regression coverage. A previously successful application run does not establish the behavior of short forms, failed planners, unchanged wizard headings, ambiguous threads or failed mailbox operations.
- Test the complete current working tree, including the desktop and ATS additions. A clean copy of HEAD that omits pending feature files is not an integration test of the user's current codebase.
- Before deployment, build the image from the exact tested revision and complete feature file set. Source copied into a running container is not by itself a reproducible image build. Do not accidentally include private data such as the real contacts CSV or credentials in commits/images.
- Preserve existing modifications. Do not reset the workspace to HEAD or replace whole files merely to make a clean-copy test pass.
- When an item is fixed, record its regression test, relevant integration evidence, actual failure behavior and any still-unverified external-service assumptions. Do not interpret a simulated challenge fixture as proof that every live Cloudflare challenge will accept desktop Chrome.

## Verification run for the F10-F26 changes (2026-11-12, Claude)

Full suite on the final tree (copy in a fresh directory inside the container, `pytest tests/unit`): **1026 passed in 4139.60 seconds (1:08:59), 0 failed.** An earlier run of the same changes had 22 failures, all caused by this work and fixed before this run: the snapshot's folder/attempt lookups after job keys became canonical, workflow stages that finish without their declared files, the rollback bundle assuming a model object, and label-by-Message-ID no longer reviving a rejected event. The flaky `test_scrape_many_runs_in_parallel_and_skips_cached` passed this time.

All tests ran inside the `jobagent` container against a copy of the tree (host pytest breaks on a ROS plugin). No external service, mailbox, portal or provider was contacted; browsers were local headless Chrome on synthetic pages. Nothing was committed. Checkboxes are intentionally left unticked for the reviewer.

## F29

**High — Fuzzy factual-identity matching can substitute a different school and pass source checking.**

**Observed (2026-11-13, Codex):** Pantex's Institutes field was actually filled with **Adams State University**, while the applicant education file lists **Arizona State University** (and Vellore Institute of Technology for the other degree). The run logged the Adams answer as `source=applicant`, and subsequent rounds retained it. This is a wrong institution, not a harmless spelling variant. No Pantex submission was approved.

**Independent helper reproduction:** `forms.match_option("Arizona State University", ["Adams State University", "Other"])` returns `("Adams State University", 84)` with the normal acceptance threshold. This establishes that the generic choice matcher itself can accept a different institution; it does not prove which exact planner/check step first proposed the live answer. The live readback confirms the wrong answer persisted after the plan checker.

**Decisive saved record:** the initial field record has `answer="Arizona State University"`, `filled="Adams State University"`, `readback="Adams State University"`, `outcome="verified"` and `source="applicant"`. Thus the filler changed a factually correct plan into a different institution and verification accepted the substituted choice. The plan checker runs before this conversion, so checking only the plan cannot prevent the mismatch. Subsequent rounds retained Adams as already populated.

**Contributing design decisions:** generic option matching accepts token similarity for factual identities. Verification judges the selected option against its readback rather than enforcing identity with the source fact. The education instructions also say to choose the closest offered option, and `apply.check` can treat applicant instructions as support; neither closeness of spelling nor generic instructions establish an institution's identity.

**Correction for Claude Code:** distinguish factual identities (schools, employers, people, credentials) from approximate category wording. Search the actual known school and approved aliases; if absent, use Other plus the genuine institution name where offered, or explicitly hand off. Do not substitute a similarly named school. Narrow the generic instruction-based checker exemption and retain fact-specific provenance. Do not solve this merely by globally raising a fuzzy threshold: legitimate category matching needs different rules. Clarify the education instruction so “closest” permits naming variants, not a different institution.

**Acceptance checks:** Arizona State cannot become Adams State; similarly named but distinct employers/people cannot become each other. Exact aliases and campus variants still work. A searchable list finds the actual ASU item even when it is outside the initial options page; absent ASU chooses Other and fills Arizona State University or reports a blocker. Exercise the planner/check/fill/readback path and review packet, not just the string helper. Preserve the live initial records when a controller corrects the school.

**Controller correction verified:** calling the existing `forms.fill_field(..., "Arizona State University", strict=True)` searched the combobox and selected the real **Arizona State University** option. No Other fallback was needed. Therefore ASU was available; the default fuzzy path accepted Adams from the initial options before searching the true institution. Evidence: `pantex-school-repair.json` / `.png`; original records retained as `application/before-profile-corrections.json`.

**Disposition:** open; no production F29 source changes made. The actual school is now corrected in the live unsubmitted Pantex profile, but the default production matching defect remains.

## F30

**High — Application planning omits raw JD facts; source checking excludes the JD.**

**Owner-detected live failure (2026-11-13):** Pantex's raw `jd.txt`, line 3, explicitly says **Travel - 30% or less**. Saved applicant instructions say use the JD percentage, and only if none is given choose 100%. The application was filled **Travel - 50% or more**, recorded `source=applicant`, `outcome=verified`, with no flag. It reached a ready review screen. The owner caught it before submission.

**Verified root cause:** `steps.apply.context` supplies `JD` from only the first **5000 characters of `jd.md`**. The generated summary has no travel requirement anywhere; structured `jd.json` also contains no travel fact. The exact source `jd.txt` contains it on line 3 but is never supplied to application planning. Consequently the model applied the absent-percentage fallback and chose the highest offered bracket. `steps.apply.check` explicitly excludes `JD` from its source packet, so even including a fact in the planner's JD would not establish independent source validation for this class of instruction. Readback proves the chosen option was entered, not that the raw JD supported it.

**Correction for Claude Code:** supply the authoritative scraped posting text to application planning and validation, with explicit provenance. Avoid blind prefix truncation as the only retrieval policy: requirements can be in metadata headers or late in a long posting. Preserve structured facts such as travel where useful, but do not make a lossy summary the only source. For instruction-dependent fields retrieve/validate the relevant original JD passages and distinguish verified absence from absence in a summary. Match numeric intervals semantically and choose the actual **30% or less** option; no fuzzy similarity between incompatible numeric ranges. A source checker must be given the source needed to validate the instruction, and must flag unsupported/conflicting fallback answers.

**Acceptance checks:** raw posting travel in its header but absent from `jd.md` / `jd.json`; travel beyond character 5000; equivalent percentage/range wording; truly absent travel using the owner's explicit fallback; conflicting structured/summary/raw values. Exercise application context, planner, checker, fill and readiness/review, asserting the selected interval and actual cited evidence. Keep the initial erroneous live records as evidence. Generalize the source-preservation fix to other JD-dependent questions, rather than hardcoding Pantex or 30%.

**Disposition:** open; no production F30 source changes made, per the owner's instruction to hand off items outside the four assigned fixes. The controller corrected the live dropdown and fresh review to **Travel - 30% or less**. The old review was answered skip, so it could not submit the wrong answer. The actual authorized Apply click occurred once at **2026-11-13T08:07:28Z**, and the site reported submitted. `application/submission-1.json` independently confirms that the frozen reviewed travel answer and readback are both 30% or less. This runtime correction does not fix the production context/checker defect.

## Current independent audit (2026-11-12, Codex)

Scope: all F01–F26, including the current uncommitted working tree. No reset of Claude's or the owner's changes. The owner additionally authorized implementing the remaining parts of F14/F15/F17/F25 and two specific real application submissions; those live outcomes will be recorded separately. Review checkboxes cover the findings' bounded acceptance criteria, not a guarantee about every external portal or repeated statistical analysis.

**Original F01–F26 result: 21 resolved; F04 and F17 (reopened), F18, F19 and F23 remain open. New live-run findings F27–F30 are also open for Claude Code: 21 resolved and 9 open in the expanded list.**

| Finding | Current verification and disposition |
|---|---|
| F01 | Resolved. Startup navigation distinguishes entering an application from final submission; only the reviewed capability may click a final button. `test_start_navigation`, `test_submit`, `test_approval_uploads`. |
| F02 | Resolved. Successful COPY required, exact message deletion with UIDPLUS, no unrelated EXPUNGE; failure paths covered by `test_inbox_cleanup`. No real mailbox cleanup performed. |
| F03 | Resolved. Planner exceptions/malformed output and missing progress cannot yield a clean review; business state survives CLI/queue mapping. `test_planner_failure`, `test_apply_exit_codes`. |
| F04 | **Reopened.** Attempt reservation/reconciliation is fixed, but a retained synthetic file widget freezes only `bytes unreadable`; changing its visible filename and held file leaves the approval hash unchanged. Independent local Chrome counterexample; see current F04 handoff. |
| F05 | Resolved. Produced files, hard checks, claims checks and identity gates propagate generation failure. `test_documents`, `test_multi_agent`, `test_ats_loop`. |
| F06 | Resolved. The reviewed score set and submitted document hash are authoritative; mismatched/partial/duplicate/manual rows do not become a fabricated average. `test_ats_result`, `test_ats_summary`, `test_ats_provenance`. Canonical attempt-link regression caused by the new relationship rollout was corrected and rerun. |
| F07 | Resolved. Host attachment and origins are bound to the intended task, including navigations and child frames. `test_generic_desktop`, `test_desktop_application`. |
| F08 | Resolved. Shared-inbox sender/search scopes, provenance and content access are enforced, with denied/unowned paths covered by `test_inbox_privacy` and `test_outcome_integrity`. |
| F09 | Resolved. Sender display text cannot replace authenticated address/domain matching. `test_outcomes`, `test_outcome_integrity`. |
| F10 | Resolved. Queued replies revalidate their seed's current application/authority on retry. Independent fake-mail probe reassigned a seed while a reply was queued: only the new owner received the reply; no stale association. `test_outcome_integrity`. |
| F11 | Resolved. Original content signature participates in UIDVALIDITY relocation; same headers with a different body cannot replace an unresolved original. Independently reproduced this collision: original remained undecided. `test_outcome_integrity`. |
| F12 | Resolved. Current candidate-specific stage evidence is required for negative stages too. Independent neutral-message probe over every model stage stored no outcome. Newsletter and quoted-history regressions remain covered. `test_outcome_integrity`. |
| F13 | Resolved. Transactional claims, ownership, leases, heartbeat and fresh structured results. Production queue now uses the in-process pipeline; injected legacy step support remains for compatibility/tests. `test_queue_claims`, `test_jobqueue`, `test_apply_exit_codes`. |
| F14 | Resolved. Actual Docker client -> production Unix host broker -> headful host Chrome/CDP was exercised, including real form extraction/fill, approved frozen payload, linked attempt, click and thank-you detection. See host evidence below. `test_browser_session` covers mode rules. |
| F15 | Resolved at documented stage granularity. New `tools/pipeline.py` shares one in-process orchestration between `jobagent run` and queue. Scrape and research are hash-validated workflow stages; existing resume/letter stages are reused. Apply rereads external state, submit keeps attempt reconciliation. `test_workflow_resume`, `test_remaining_features`. No claim of restoring a graph mid-model-call. |
| F16 | Resolved. URL/step marker/question-schema comparison supports unchanged-heading wizards and separates conditional changes. Real Chrome fixtures in `test_wizard_progress`. |
| F17 | **Partially fixed, reopened.** Assigned closed-author-root reading is implemented and tested through Chrome public CDP and a mode-preserving init script, including text/select/radio/file/custom combobox/dynamic roots. Approval still ignores extraction coverage and can clear unreadable-frame blockers; live Pantex legacy attachment links are omitted without a warning. See the 2026-11-13 handoff under F17. |
| F18 | **Open.** New content can claim an `original` index to bypass the 130-character bullet budget. Details below. |
| F19 | **Open.** Total deadline is not enforced on an in-flight model call. Details below. |
| F20 | Resolved. Measured provider-specific JD payloads and explicit truncation, consistent PDF identity, provider/run provenance, duplicate stability and explicit incomplete results. `test_ats_provenance`, `test_ats_providers`, `test_ats_summary`. Live provider availability/quotas are independent. |
| F21 | Resolved. All applications, including censored observations, determine the KM quantile only when identifiable; otherwise the labelled uncertain fallback is used. `test_outcome_stats`. |
| F22 | Resolved. Future submissions/events excluded at `as_of`; resolved composition and fixed-horizon estimates separately labelled with denominators. `test_outcome_stats`. |
| F23 | **Open, partially implemented.** Ghost weighting, clustered permutation family and temporal confirmation are improved. Repeated monitoring scope remains unvalidated. Details below. |
| F24 | Resolved. Dry-run invariance, event-ID repair, derived-state correction, complete evidence hashes/versioning, explicitly unsupported site-mix switch. `test_outcome_review`, `test_learning`. |
| F25 | Resolved for the requested jobs/applications/attempts chain. Integer primary keys linked by explicit foreign keys; versioned backfill, conservative orphan handling and uniqueness/parent checks added. Existing canonical aliases, locked vault writes and coherent bundle recovery retained. `test_identity_persistence`, `test_remaining_features`. `ats_runs` remains path/hash-provenanced rather than part of this FK chain. |
| F26 | Resolved. Execution-time embedding caps, counted calls and needs-review on judge failure/exhaustion; explicit no-judge behavior preserved. `test_discovery_budget`, `test_matching`, `test_discovery`. |

### Implementation and integration evidence for the four requested additions

- **F25:** `db.connect` registers canonical identity support, performs `relationships_v1` backfill and enables foreign-key enforcement. `applications.job_id -> jobs.id`; `submission_attempts.application_id -> applications.id`. Parent deletion and mismatched attempt parents are refused. Missing legacy jobs receive nonqueueable identity rows; orphan attempt histories become uncertain applications requiring reconciliation. The migration preserves surrogate IDs and audit history. Normal record/begin-attempt writers now supply the IDs. Compatibility triggers link old path-only inserts; external raw SQLite clients should supply IDs or use `db.connect`. The legacy migration test explicitly removes today's triggers before constructing historical data, so it tests a genuine pre-migration state rather than defeating active constraints.
- **F15:** `tools/pipeline.py`, `cli_discovery.py`, `tools/queue_runner.py`, `main.py`. Stages invoke the existing registered callbacks in the same process and close their contexts/locks. No production subprocess or nested CLI parser. A fresh-result check prevents an old ready JSON from concealing a current failed fill. CLI: `jobagent run URL --through scrape|tailor|apply|submit`; submit still requires the normal review gate and explicit desktop mode flag. Queue never submits.
- **F17:** `tools/shadow.py`, `forms.py`, context installation in `browser.py`. Recovery uses `DOM.getDocument(pierce=True)`, resolves author closed-root backend nodes, and registers them for deep traversal. Existing root `.shadowRoot` stays null. Native and custom options share the same traversal. Future `attachShadow` calls preserve the requested closed mode. Public selector engine supplies actionable locators; unsupported/read failures stay visible in coverage.
- **F14:** Host bridge evidence at `outputs/verification/host-bridge-F14/verification.json`, `host-desktop-after.png`, `fixture.sqlite`, and `application/` review/attempt artifacts. A synthetic HTTPS-origin fixture was loaded in real host Chrome; network requests were aborted and its submit handler only displayed a local thank-you. Result: `submitted`, page detection true, linked attempt ID 1. This exercises the production approval/attempt/click path without sending an external job application. It does not establish that every live anti-bot challenge can be resolved.
- Documentation updated in `docs/workflow.md` and `docs/desktop-applications.md` to match stage resume, fixed-session submission, database linkage and closed-root behavior.

### F18: remaining counterexample and required correction

`FillBullet(text="New and changed text " + "x" * 160, duties=[0], original=0)` is accepted with **181 characters**, although it is new text above `BULLET_MAX=130`. The exemption trusts the presence of an original index; it does not establish equality to the indexed template bullet. Rendering uses the new text when it differs, so this is not an exemption limited to a true verbatim original. Require template-aware equality before exempting length (or normalize an original reference to its exact template text and reject altered text). Keep schema, prompt, repair feedback and final validation consistent. Existing tests prove ordinary new bullets are refused, but do not close this bypass.

### F19: remaining counterexample and required correction

A fake structured model whose invocation sleeps **0.12 seconds** returns a successful result from `structured_invoke(..., deadline_s=0.01)`. The deadline prevents beginning a later retry but does not bound the in-flight request to the remaining total budget or reject a late completion. Clamp request timeouts to remaining time at every attempt, define cancellation for non-cooperative providers, and reject late results consistently. Retain bounded retries/fallback and repair feedback already implemented. A source-level deadline check before the call alone does not satisfy a total wall-clock contract.

### F23: remaining scope and required verification

The current max-statistic procedure controls the comparison family **within one analysis**, with company clusters, ghost weights, temporal confirmation and disabled behavioral switches. Re-running it on successive overlapping cohorts at the same alpha introduces another testing dimension. There is no sequential alpha allocation or declared fixed-look protocol, and the simulations do not include this repeated monitoring process. The tests' Monte Carlo slack (6/50 or 11/100) is not proof of an arbitrary repeated-monitoring 5% bound. Choose and document a fixed-look or sequential procedure and calibrate the complete deployed policy across null streams, repeated looks, families and cluster dependence. This is an unclosed acceptance criterion, not a claim that the implemented per-analysis procedure has been empirically shown defective.

### Final test result and deployment record

Independent final full suite, exact current source snapshot: **1034 passed, 0 failed, 13 dependency deprecation warnings, 311.48 seconds**. Command: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 pytest --basetemp=/dev/shm/review-final-complete -q tests/unit` inside Docker at `/tmp/review-verified-final.ZEh957`, with isolated data examples/state/output directories. Durable output copied to `outputs/verification/all-findings-2026-11-12/pytest-final.log`. Prior full run had one legacy-fixture failure; that fixture was corrected to remove current triggers before constructing legacy rows, passed 11/11 targeted tests, then the entire suite passed. Production Python source hashes match the tested snapshot (`source-sha256.json`).

A copy of the real database successfully migrated with zero foreign-key violations and zero unlinked applications/attempts. Backup before rollout: `state/backups/jobs-before-relationships-20261113T051245Z.sqlite` (mode 0600). Runtime source is copied into the current container for these authorized runs; this is not a claim of a rebuilt immutable Docker image.

### Authorized live runs: initial checkpoints

After the successful full suite, the active `/app/src/jobagent` package was verified byte-for-byte against the tested Python manifest and the live database migration completed with FK enforcement enabled and zero violations/unlinked rows. A pre-existing, unimported nested `/app/src/src` copy was excluded from the active-package comparison and left untouched.

- NS2 Cloud requisition 17649 resolves to **Pantex — Cloud Solutions Engineer, Amarillo, TX**. The actual production host bridge opened the requested posting in headful desktop Chrome at its exact HTTPS URL; screenshot: `outputs/verification/all-findings-2026-11-12/pantex-host-preflight.png`. No fields or submission were changed by this navigation-only preflight. The in-process end-to-end run completed scrape/research and is generating documents.
- Carvana 8188615 is still active (**Data Product Engineer, Tempe, AZ**), independently checked via its public posting endpoint and a fresh successful agent scrape. No prior submission attempt existed.

These checkpoints are not submission receipts. Final outcomes must be recorded below when the actual submission/reconciliation path finishes.

### Final verification after the live integration corrections

**1044 passed, 0 failed, 13 dependency deprecation warnings, 356.27 seconds** in a fresh isolated full-suite run (`pytest --basetemp=/dev/shm/review-complete-final -q tests/unit`, Docker snapshot `/tmp/review-verified-final.ZEh957`, plugin autoload disabled, example data and isolated state/output). This supersedes the earlier 1034/1043-test source versions. Durable log: `outputs/verification/all-findings-2026-11-12/pytest-final-latest.log`; exact Python manifest: `source-sha256-final.json`. The active deployed `/app/src/jobagent` package matches that manifest. Focused verification: 52 entry/desktop tests and 50 workflow/relationships/session tests passed. The latter includes the actual registered tailor callback and the location-enrichment/reuse regression. An isolated actual `scrape.save` probe also preserved a claimed queue row's `running` state and successful lease renewal.

The first Pantex resume obtained all five scores (72 / 56 / 75 / 33 / 42; average 55.6). Those scores belong only to that initial version. Final rescoring later exhausted Jobscan's allowance and ATS Friendly failed to return a score; the final average correctly stayed incomplete. SkillsFirst refused the long JD's 7000-character limit. After the unnecessary restart exposed the ordering bug, the remaining application test explicitly uses `--no-ats`; current generated files must pass the document gate and must not inherit the initial version's scores. Live submission outcomes remain to be recorded.

### Carvana real submission (2026-11-13)

Carvana **Data Product Engineer, 8188615** was actually submitted through `steps.submit.make_at_review` and its normal typed approval, frozen payload, attempt reservation and final-click functions. The site displayed **“Thank you for applying!”**; application ID **3**, attempt ID **1** / attempt number **1**, click **2026-11-13T07:01:21Z** (00:01:21 America/Phoenix), attempt state **submitted**. Evidence: `outputs/carvana/8188615/application/submitted.png`, `after-submit.png`, `submission-1.json`, and `review.md`. Email confirmation is checked separately; page acceptance is not represented as email verification. No duplicate click was made.

**Limits:** This was controller-assisted, not a fully autonomous upload success. The job agent filled the answers, but F28 put the cover letter in the Resume widget and left duplicate failed upload records. Controller drag-and-drop repair placed the correct generated PDFs into the two labelled widgets, with dispatched-file SHA-256s recorded separately from unreadable retained browser bytes. Initial records are preserved in `before-upload-reconciliation.json` and `initial_fill_history`. A fresh 17-field review labelled the two upload records `reviewer`, retained explicit opaque-byte flags, passed the live blockers and was approved under the owner's explicit instruction to submit. The controlled run added a `challenge.session` policy verifying both reviewed widget filenames immediately before the final click; it does not close production F04 or F28. Normal F06 provenance remains conservative: opaque widget bytes are not assigned a same-named disk file's hash.

Pantex remains **not submitted**, with zero attempt rows. Its current generated resume/letter passed the validity gate; their JD fingerprint stayed unchanged after entering apply. The saved portal credentials were rejected and registration did not complete (F27). No Pantex account creation is claimed.

### Latest source/test and receipt record (2026-11-13)

After the F14 live-login handoff and explicit auth-failure stop: **1049 passed, 0 failed, 13 dependency deprecation warnings in 395.93 seconds**, isolated full suite at Docker snapshot `/tmp/review-verified-final.ZEh957` with `pytest --basetemp=/dev/shm/review-auth-final -q tests/unit`. Focused entry/auth/session/verification run: **75 passed**. Exact tested/deployed manifest: `outputs/verification/all-findings-2026-11-12/source-sha256-final-auth.json`; full log: `pytest-auth-final.log`. This supersedes earlier source-version test counts. A green suite does not close the independent retained-widget counterexample: F04 is reopened. Current checklist after the coverage follow-up: **21 resolved, 9 open** (F04/F17/F18/F19/F23 plus new F27/F28/F29/F30). Only the assigned F14/F15/F17/F25 source work was implemented; the other items are actionable Claude Code handoffs in their own sections.

The current general login helper preserves an explicitly handed-off live POST-backed page (`already_open=True`) instead of reloading its transient URL. Application callbacks return the actual `PortalLogin` result, and startup refuses an explicit failed authentication. Existing standalone navigation and fresh-login behavior remain supported. This is separate from F27's rejected-password/registration loop.

Carvana email confirmation **received** at `2026-11-13T07:02:44Z`: sender `no-reply@carvana.com`, subject “BOOM. Application received. It's just that easy.” Application ID 3 is **confirmed**; attempt 1 was submitted once and remains linked to application 3. Page and email evidence agree. Pantex remains unsubmitted pending account setup.

### Pantex account recovery accepted (2026-11-13)

The controller-assisted registration was accepted: the real site displayed **My Candidate Profile**, **Sign Out**, and candidate ID **120005**. The production account agent then independently returned `signed_in`, `registered=True`, and `signed_in_verified=True`; no email-verification requirement was observed (`verified_mail=False`). Evidence: `pantex-registration-assisted.json`, `pantex-registration-assisted.png`, and `pantex-after-registration-click.png` under the verification folder. No actual CAPTCHA was shown or solved: the false unknown-challenge handoff was a transient Loading screen, resumed only after the signed-in profile was visibly loaded. The normal application run is being resumed from the intended posting with the validated documents. Account setup needed controller assistance and does not resolve F27. Earlier notes saying no Pantex account had been created describe the earlier failed attempts, not this accepted recovery.

### Pantex profile progress and legacy header correction (2026-11-13, Codex)

The signed-in legacy SuccessFactors page changes from `/career` to `/portalcareer`. Its masthead Language picker was mistakenly counted as an application field, preventing startup Apply navigation. The field reader now excludes controls only inside the identified `#headerNavBar` / `#rcmCareerLangDropDown` navigation containers. A candidate Language question in the application stays in the inventory and keeps a final Apply button protected. Focused startup-navigation suite: **27 passed**, including both regressions. One initial test assertion incorrectly expected an exact label without its option text; correcting that assertion did not change production behavior.

A guarded entry-only preflight reached **My Candidate Profile, 24 fields**, without filling or submitting. Pantex requires this profile before the job-specific form. The controller uploaded the actual generated resume and cover letter through their separately titled native Upload dialogs, sending PDF bytes to host Chrome rather than container paths. The site displays candidate document titles rather than original filenames; dispatched hashes and that readback limitation are recorded in `pantex-profile-uploads.json`. This is controller-assisted attachment setup, not a production F17/F28 fix. The job agent then resumed the normal desktop submission workflow with `--no-ats`; its log confirms scrape, research, location, resume and cover-letter stages were reused. Pantex has not been submitted at this checkpoint.

### Pantex required-date stop (2026-11-13, Codex)

The normal profile run ended `blocked`, with zero Pantex submission attempts, after the site repeatedly rejected From Date values. The actual UI5 shadow-root input placeholders are **MM/DD/YYYY**; the agent entered `01/2026`, `08/2024` and `05/2026`, and those controls reported `aria-invalid=true`. Readback matching the typed string did not mean the site accepted it. Source files give month/year for the two start dates; the owner has been asked for exact dates or an explicit month-only approximation. No day has been invented. The school has separately been corrected through the supported strict-search helper. These runtime repairs are not production source fixes.

### Latest complete validation after the masthead fix (2026-11-13, Codex)

**1051 passed, 0 failed, 13 dependency deprecation warnings in 728.28 seconds**, full isolated unit suite at `/tmp/review-verified-final.ZEh957`; log `outputs/verification/all-findings-2026-11-12/pytest-header-final.log`. Verified **all 149 source/unit-test Python files** exactly match the tested snapshot, and all **78 active deployed Python files** match the host source. Manifests: `tested-inputs-final-header.json` and `source-sha256-final-header.json`. No production F04/F17 completeness/F18/F19/F23/F27/F28/F29 fix is implied by that passing suite; their independent counterexamples and Claude handoffs remain open. **Current expanded checklist: 21 resolved, 9 open (30 total), after the owner-detected F30 follow-up.**

The owner authorized `01/01/2026` and `08/01/2024` as month-only start-date approximations and answered **No** to dismissal/resignation in lieu of termination, felony conviction and misdemeanor conviction. The dates were accepted by the actual UI5 controls; profile Next opened the actual Cloud Solutions Engineer application with **50 fields** and no page errors. Those three history answers were stored through the normal company-specific learned-answer helper. The normal desktop job-agent workflow resumed on the actual application; documents and the validated address were reused. No Pantex submission has occurred at this checkpoint.

### Pantex job-question confirmations (2026-11-13, Codex)

The owner chose **Company Recruiter** after the actual source dropdown and search offered no employee-referral choice. This was answered through the job agent's HITL; its conditional “Details?” choices were Direct - LinkedIn Inmail / Direct - Recruiter, so Direct - Recruiter was supplied as the matching detail. The owner also explicitly confirmed **Yes** to both 3+ years Azure/AWS cloud-administration / hosted IaaS-SaaS-PaaS questions. Those two facts were stored through the normal company-specific learned-answer helper. The citizenship radio is **No**, matching saved Indian citizenship; no citizenship or clearance was invented to meet the JD. Final submission remains pending review at this checkpoint.

### Pantex actual authorized submission outcome (2026-11-13, Codex)

**Pantex Cloud Solutions Engineer (17649) was submitted once at 2026-11-13T08:07:28Z (01:07:28 America/Phoenix).** The actual site's receipt was detected (`page=true`); no confirmation email arrived during the two-minute check window. `applications.id=5` and linked attempt 1 are `submitted`. Do not refill or resubmit; a later email check is `jobagent confirm outputs/pantex/6f42d34151`.

Evidence: `outputs/pantex/6f42d34151/application/submitted.png`, `after-submit.png`, `submission-1.json`, `application.json`, `review.md`. The frozen payload explicitly contains **Travel - 30% or less**, verified independently in `outputs/verification/all-findings-2026-11-12/pantex-submission-travel-check.json`. Original erroneous records remain in `before-profile-corrections.json` and `before-travel-correction.json`.

**Limits:** this was a controller-assisted integration test, not fully autonomous production success. Recovery included registration native-select reconciliation, two role-specific document uploads, strict ASU school search, owner-approved full dates and the owner-detected travel correction. The normal job agent filled the job questions; the production reviewed-submission hook reserved the attempt and clicked Apply after approval under the owner's original explicit submission instruction. A runtime policy additionally checked document-control identity metadata and extraction coverage before actions. Retained attachment bytes remain unverified and the frozen `resume_sha256` / `cover_letter_sha256` are correctly `null`; dispatched-file hashes are labelled separately. No same-named disk bytes were claimed as browser-held evidence. The submitted citizenship answer is **No**; the role requires U.S. citizenship, so successful submission does not establish eligibility or acceptance by a recruiter.

The fresh controller helper initially summarized all boolean-verified records as 57/57, including eight empty optional fields. Corrected that helper-only report metadata to **49 verified / 57 records, eight optional empty** after completion; values, frozen payload and submission state were unchanged. `review-at-click.md` retains the original report. This correction is not a production F03 source change.

**Final live outcomes:** Carvana 8188615 **confirmed by site and email**, Pantex 17649 **submitted with site receipt, email not yet received**. Each has exactly one linked submission attempt. All requested original findings were reviewed; 21 findings are checked off and 9 remain actionable for Claude Code (F04/F17/F18/F19/F23/F27/F28/F29/F30). The four specifically assigned implementations and the two live tests are documented with their practical limits above.
