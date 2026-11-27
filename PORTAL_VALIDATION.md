# Portal validation — 26 November 2026 (Phoenix)

## Correction candidate — 27 November 2026 (Phoenix)

The current validation container is built from **`6e13893500dd4beb19854a6d9321a780192a701d`**. All **145 installed source/assets match** the committed build snapshot. Voyager was reachable in the native CLI preflight and the authenticated dashboard checks returned200. Production presence remains Away; the database remains20 filled applications and0 submission attempts. No application was submitted, no new application documents were generated, and no ATS scans were performed during this correction phase.

Three commits contain the corrections: `cec3135` preserves owner provenance and live field ownership; `de2841a` fixes unnecessary research on retry and repairs stale test fixtures; `6e13893` reconciles original upload intent with independently proven current attachments after portal refreshes. The runtime corrections cover:

- Confirmation notes on a saved employer name no longer force unnecessary model/HITL resolution; additional start-date wordings use the saved availability rule.
- Preparing a job-specific postal address preserves actual current-location evidence. Generated address facts are marked as lookup evidence and cannot prove actual residency.
- A terminal account-agent failure can be corrected by independently verified current profile identity. A name or Sign Out link alone is insufficient, and authentication holds remain enforced.
- Optional “If Other” source explanations follow the current parent choice, including cached decisions, answer-sheet values and the original filler's final repair. Required or unrelated fields are not automatically cleared.
- Completed SuccessFactors attachment slots remain visible to the reader. Exact current server bytes can be reused; wrong attachments are replaced only through their owned delete/upload controls and confirmed dialog. Ambiguous controls or refused byte verification stop replacement.
- File uploads retain authored identity through reordering and stay within their document/section. A generic Upload a file control under Cover Letter receives cover-letter bytes. One clearly owned previous primary document can be replaced even when its filename differs; ambiguous document lists remain untouched.
- Company-research fingerprints exclude both derived location values and their resolver version. An unchanged retry reuses research; a resolver upgrade refreshes locations; genuinely changed posting content still invalidates research.
- After a delete or upload refreshes the document and renumbers its controls, a later successful upload can satisfy the original document obligation. Reconciliation requires a unique current owner, unchanged frame/application route/section and exact current file bytes. A matching filename or stale document handle does not establish success.

**Verification is qualified, not a new full-suite pass.** The complete isolated offline unit run on immutable `cec3135`, including browser and simulation tests, finished **4,019 passed /18 failed in5,943.82s** (runner wall5,946.70s). Its145 runtime source/assets remained unchanged. Twelve failures were stale fixture assumptions (offline CLI preflight, model-fallback title schema and a context mock's optional third argument); four verification fixtures invalidated their own observed target by taking another snapshot; two failures exposed the research fingerprint defect. The corrected surrounding group passed **288 tests in430.05s**, including all18 failures and new positive/negative cache cases. Earlier focused groups passed96 tests in196.07s, six final upload-boundary cases in23.85s, and338 surrounding cases in881.52s. These groups overlap and must not be summed. A fresh complete suite on the final corrected revision is **still pending**.

**Current-code live SuccessFactors retest passed.** A fresh private Teradyne13179 run on `de2841a` first stopped **blocked despite66/66 verified fields**: its provided-document ledger rejected uploads after attachment IDs changed. Saved history shows failed initial delete attempts followed by independently byte-verified uploads under new IDs. This failed attempt is retained. Its two supervisor answer batches (3+16 answers) are not autonomy or timing-comparison evidence.

The four matched refresh regressions fail on unchanged `de2841a` (99.39s). On `6e13893`, the surrounding upload/filling/approval group passed **205 tests in1,049.58s**, including refreshes after deletion and upload in both planned and adaptive filling, and rejection of changed bytes, captions, routes, duplicate owners and neighboring slots. This focused group overlaps prior groups; it is not a complete unit-suite rerun.

The live retry on `6e13893` reached **review66/66, no flags, 73/73 audit checks passed**. Independent owned-Download checks proved both existing Amazon PDFs before filling and both different Zoox PDFs afterward. Final DOM values matched the verified answer records, including all four work entries and both schools/dates. The native job agent performed all filling; the supervisor supplied one private14-answer HITL batch. It reused prior private learned answers and is **not a fresh autonomy or speed benchmark**. Setup65.85s; filling221.63s; HITL wait58.06s; active filling163.56s; whole supervised command294.78s. Its exit5 is the intentional borrowed-document prohibition; the only remaining blocker states that the documents are never submittable. Per-record replacement counts remain0 after failed initial attempts, so those counters are not used to claim a delete count. The before/after server-byte audit establishes the two document replacements.

The remaining portal results below are historical results on their stated revisions. Next are current-code unsubmitted checks and three paired original/experimental comparisons using identical inputs and separate fresh private browser/checkpoint/HITL state. Prepared cases are Ashby/Coram, Lever/Arrive Logistics and Greenhouse/Everpure. These compare the retained original filling strategy against the current default on common current source, not the entire old and new document-generation pipelines. Existing documents are borrowed under the non-submittable guard; no document generation is needed. Human-assisted iCIMS/SmartRecruiters checks await current owner availability; UKG restriction clearance is unknown and UKG remains deferred.

Evidence: `outputs/verification/correctness-closeout-20261127/`, especially `final-cec3135/suite.log`, `suite-junit.xml` in that folder, `repair-verification-v7.txt`, `attachment-regression-baseline.txt`, `attachment-correction-final.txt`, `attachment-document-candidate/attachment-correction-junit.xml`, `deployment-proof-current.json`, `post-6e13893-deploy.json`, `live/successfactors-teradyne-proof-retry/` and `paired-benchmark-plan.json`. Failed before-fix cases and the verification-target diagnostic are retained. The test-only fixes preserve the production model preflight, stale-target checks and verification-mail ownership rules.

## Result and scope

- [x] SuccessFactors **supervised filling to the review boundary**: two Teradyne roles plus a different employer, Ericsson. See the exact matrix below.
- [x] Retest Workday, Ashby, Lever, Greenhouse and the newly accepted SuccessFactors service on real postings.
- [ ] SmartRecruiters: visible human-verification block; City-control fix still needs live validation and the full matrix.
- [ ] UKG: explicit server/network restriction; registration and application filling remain unvalidated.
- [ ] Fresh iCIMS retest: visible hCaptcha. The previous assisted Garmin review acceptance is retained, but this retest did not pass.
- [x] Build and recreate the latest runtime, verify installed source/assets, check dashboard/Voyager, and inspect Docker resources.

**No application was submitted. No resumes, cover letters or ATS scans were generated.** Native `jobagent apply --no-submit` and its configured Voyager/fact-routing code performed filling. The supervisor supplied answers through native HITL and captured screenshots/readbacks. Existing Zoox documents were borrowed under the independent never-submittable guard. Private job/data/HITL directories kept test answers out of the production applicant profile and application tables.

These results establish supervised review-stage filling for the named cases. They do not establish universal support, zero-assistance account creation, successful submission, higher ATS scores, interview-rate improvement, or a controlled old/new speedup.

## SuccessFactors matrix

| Required case | Native result | Independent answer checks | Runtime code | Evidence under this report's evidence directory |
|---|---|---|---|---|
| Real role with corrections: Teradyne Sr. Software Engineer (Compute Test), North Reading, req 13179 | Review 64/64; no flags | 65 passed | `c3af90c` | `matrix/current-image-teradyne-13179/` |
| Different role, same employer: Teradyne Software Engineer (Compute Test), req 12773 | Review 64/64; no flags | 65 passed | `b79bfd1` | `matrix/current-image-teradyne-second/` |
| Different employer: Ericsson Compiler Engineer, Austin, req 791253 | Review boundary 31/31 outcomes; two secure credential-presence records remain separately flagged | 36 passed | `9474ecc` | `matrix/current-image-ericsson/` |

This matrix used successive frozen, tested revisions; the image now includes all their corrections. Teradyne independently verified four distinct work entries, both degrees and dates, current employer/title, JD city, referral/contact, immigration answers and demographics. Existing-account/profile forms had no fresh resume input. Optional business-category answers are permitted private test classifications, not newly confirmed owner facts. The second role repeats the owned primary telephone in an optional alternate field; no distinct alternate number is claimed.

Ericsson independently verified 29 ordinary fields plus two password-presence outcomes, and both uploaded PDFs by SHA-256. It left the optional second citizenship blank, retained Indian first citizenship, matched Austin/Texas, and stopped before **Apply**. The vault helper filled the new-account password pair without clicking account/application actions; account creation is **not** inferred. Credential secrets were not read back. Its race menu offered `Asian (not Hispanic or Latino)` rather than plain Asian; the saved no-plain-category privacy fallback selected decline. The audit does not infer an ethnicity conjunction or a regional identity from nationality.

Earlier Corning 79603 and 80003 passes on `037f23b` remain historical evidence. A later Corning recheck was refused by its stored authentication cooldown **before another password attempt**, so it was not counted as a current pass or bypassed. The alternate Teradyne/Teradyne/Ericsson matrix above supplies the required employer/role coverage.

A separate earlier fresh Teradyne trial reached review65/65 and passed 65 answer checks, including current server-side resume bytes after parser replacement. That trial used a documented removal of this test's borrowed attachment to force a fresh upload. It does **not** prove automatic replacement of every existing server resume.

## Retests of checked services

| Service / posting | Result | Independent checks | Assistance and qualification |
|---|---|---|---|
| SuccessFactors / Teradyne req13179, post-matrix | Review64/64, no flags | 65 passed | Final `9474ecc` installed image; native saved-credential sign-in, all four work entries and both distinct degrees/dates correct. One cache-served ask, no new supervisor request. |
| Workday / Intel Senior Software Engineer JR0289456-1 | Review76/76, no flags | 70 passed | Final `b79bfd1` run resumed a draft with private cached answers. One cache-served native ask, no new pending supervisor request. Both actual named schools, degrees and GPA correct; 17 conditional questions stay on one logical page. |
| Ashby / Coram Member of Technical Staff, Real Time Systems | Review10/11; all required and substantive fields verified | 17 passed | `9474ecc`; no HITL and no writer calls needed. The sole unused optional autofill utility remains flagged; the actual required resume is byte-verified. |
| Lever / Arrive Logistics Software Engineer | Review19/19, no flags | 26 passed | `9474ecc`; initial supervised batch, then corrected rerun with cached answers and one additional native HITL question. Availability follows the saved coming-Monday rule. Exact .NET version is authorized private test data, not a newly verified experience claim. |
| Greenhouse / Everpure AI Enablement Engineer 8331163 | Review27/27, no flags | 35 passed | `9474ecc`; one two-question HITL batch. Both distinct education entries filled. VIT school and AI major used explicitly permitted Other fallbacks after the bounded named-search path. Export-control No is private test data, not a newly verified legal declaration. |
| iCIMS / Garmin 18475 | **Blocked on visible hCaptcha** | Not an acceptance pass | `9474ecc`; one normal Docker attempt. Native presence handler stopped when the owner was unavailable. Earlier ordinary-Chrome-assisted scope also needs owner connection approval; it was not simulated. |

For single-page portals, review means the completed form is held before the final Submit/Apply action; it does not imply that the portal supplies a separate review page. All passing cases retain the intentional borrowed-document submission blocker. Screenshots, literal final DOM readbacks and answer audits are retained beside each run.

## Timing from accepted attempts

All values are seconds. Active filling is native elapsed filling minus native HITL wait; it includes browser/model latency. Setup and filling do not include the complete scrape/research/document pipeline, account preparation outside the run, rejected attempts or supervisor diagnostics. Cache/account conditions differ, so these are **not** controlled comparative benchmarks.

| Case | Code | Setup | Filling elapsed | HITL wait | Active filling | Native ask events |
|---|---|---:|---:|---:|---:|---:|
| SuccessFactors / Teradyne 13179 | `c3af90c` | 64.76 | 197.10 | 80.05 | 117.05 | 1 |
| SuccessFactors / Teradyne 12773 | `b79bfd1` | 22.32 | 202.98 | 80.06 | 122.92 | 1 |
| SuccessFactors / Ericsson 791253 | `9474ecc` | 22.19 | 256.92 | 0.00 | 256.92 | 0 |
| SuccessFactors / Teradyne 13179 post-matrix | `9474ecc` | 62.68 | 92.94 | 0.02 | 92.92 | 1 |
| Workday / Intel JR0289456-1 | `b79bfd1` | 14.63 | 150.95 | 0.02 | 150.93 | 1 |
| Ashby / Coram | `9474ecc` | 11.99 | 16.09 | 0.00 | 16.09 | 0 |
| Lever / Arrive Logistics | `9474ecc` | 10.17 | 54.36 | 24.04 | 30.33 | 1 |
| Greenhouse / Everpure | `9474ecc` | 6.11 | 69.48 | 10.01 | 59.47 | 1 |

The final Lever attempt's ask count excludes its first batch served during the preceding attempt; the Workday and post-matrix Teradyne events were cache-served. These counts are not the total assistance used to prepare each case. The final Workday and Lever timings must not be described as fresh-run autonomy measurements.

## Corrections implemented and verified

1. Exclude job-alert utilities, prioritize the real Apply menu item, use accessible action captions, and bind account actions to the foreground privacy dialog.
2. Preserve an explicit JD city with trailing country/ZIP. Use a state-to-city fallback only when the JD has no specific city.
3. Keep dropdown choices and actions tied to their owning control, including lazy menus; do not use unrelated visible choices.
4. Route SMS/Text Country Code to the saved telephone prefix, not SMS consent.
5. Reject No Selection as a real No answer. Handle completed upload classes and verify current bytes returned by the exact owned Download action when parsing replaces the widget. Do not infer ownership through disconnected containers.
6. Scope Formal Education and Employment History records; upload a pending resume before clicking Add so parser-created rows are discovered first.
7. Bind Workday School or University and Overall Result (GPA) to the same degree record. Keep transient Errors Found headings out of question scope.
8. Reset the unchanged-refusal counter for actual retained-question schema changes, allowing successive required follow-ups while keeping unchanged-form and outer iteration limits.
9. Accept broad Asian choices with explicit neutral country metadata before applying the owner privacy fallback. Partial menus remain searches; regional identity is not inferred.
10. Search named institutions before their approved Other fallback. Reject a nonempty dropdown value as proof of an explicit blank.
11. Preserve/clear empty optional native selects safely even when reported as comboboxes. Never clear a required picker or substitute a real country for a missing empty option.
12. For an explicitly blank optional custom combobox, leave it untouched **only** after a fresh, uniquely owned whole-widget read confirms it remains empty, optional and valid. This corrects the actual Ericsson second-citizenship INPUT. Fresh/stale required, invalid, populated and native-SELECT cases are guarded.

The first Ericsson diagnosis incorrectly assumed a native SELECT; its valid native-select fix did not resolve the actual INPUT. That failed retry is preserved and excluded. The subsequent actual-control smoke verified zero click/input/change events, and the native full retry passed the field in 0.1 seconds.

Focused frozen groups passed: 117 upload/picker, 117 entity/integration, 11 final guarded-download, 102 education/pipeline, 50 wizard/form-scope/entity, 73 workflow/wizard, 44 protected-choice/option, 55 identity/search/readback, 26 native-select/choice, and finally 24 custom/native empty-picker/readback tests in 43.17s. **Groups overlap; do not sum them. No new full-suite pass is claimed.** New cases have preserved before/after failures. The latest source also passed actual live upload/no-op checks and the portal retests above.

## Rejected attempts and remaining limits

- Independent audits rejected earlier incorrect ASU/VIT degree/date pairing, stale conditional blockers, the wrong Workday race fallback and premature Other-school selection. Those attempts remain archived and are excluded from accepted timings.
- The supervisor's first Lever reply used a two-week availability despite the saved Monday rule. Its private cached answer was corrected to 11/30/2026, then the native command reran successfully. The first command also ended with signal-derived status143 after writing review artifacts; the cause is unconfirmed. The corrected rerun ended with the expected borrowed-document status5. No generic cleanup fix is claimed from that observation.
- Native HITL still asks some questions whose underlying facts/rules exist in the profile, including current employer/availability variants. This is supervised filling proof, not proof that answer routing needs no further work.
- The requested JD-address policy also supplies JD city to generic current-location widgets. Lever separately answers its explicit actual-Chicago-residence question from the saved Tempe residence. These meanings differ: generated application address is not proof of actual residence. This policy distinction needs owner review before unsupervised production use.
- Corning previously reported account failure despite a signed-in form. Generic race-definition captions can lose their full heading, and a visible optional If Other field received redundant referral prose. These are recorded limitations; no universal registration or zero-supervision claim is made.
- Existing server attachments can hide the upload input. Current-server byte proof is implemented, but automatic replacement of every stale existing attachment remains unproven.
- Earlier assisted iCIMS review acceptance was staged; see `ICIMS.md`. It was not a fresh three-application, zero-assistance matrix. The present hCaptcha block does not overturn that narrower historical acceptance.

## External blocks and priority

SmartRecruiters: one paced native SAP Developer attempt reached DataDome human verification before any form fields. No reload/bypass loop was attempted. Its full same-role/second-role/other-employer matrix remains outstanding. See `SAP.md`.

UKG: one registration-only DOWL attempt reached Almost there and the server explicitly reported “Too many attempts were detected from this network. Please try again later.” Attempts stopped. No password reset, network/cookie change, cooldown clearing or repeated sign-in was performed. Expiry of a local hold is not evidence that the service's restriction has expired. See `UKG.md`.

iCIMS: the fresh Garmin Docker check reached a visible human puzzle. With the owner away, it deferred rather than fabricate a done answer. The ordinary-Chrome handoff needs a real Chrome approval on a new connection. Resume this separately when the owner is available; do not count the email-entry step as application review.

Among locally mapped contact employers, SuccessFactors has three observed employers and SmartRecruiters/UKG one each. These are incomplete lower bounds across 2,765 unique contact-company labels, **not market shares**. Most employers remain unmapped. SuccessFactors was already active; the ready SmartRecruiters control correction broke the tie before UKG. No global adoption ranking is inferred from this sample.

## Release, collaboration and data safety

Runtime revision: `9474ecc686729cb9f5ab9469c1f11b9eedf07fdb`.
Image: `sha256:99f6f3e6f87364c2d3187e6fa7d7a174775d00863e8f252ec19bc6ab76fb7e52`.

Built from an immutable Git archive, recreated while no application was active, then tested from installed `/app/src/jobagent` code. All **145 source/assets** match the frozen archive and image revision label. Later report-only commits do not change runtime source. Authenticated dashboard `/jobs`, `/review`, `/questions`, `/api/config`, `/api/presence` and `/api/saved-questions` returned 200. Voyager returned HTTP200. Owner presence remains **Away**, unchanged.

Claude's earlier presence/saved-question changes are included. Their separate review, 66 frontend checks and installed UI/native-answer replay smoke are recorded in `outputs/verification/icims-final-review-20261125T082138Z/combined-review-status.json`; that earlier proof is not presented as a new full-suite result for this release. Work and runtime reservations were communicated through `CHAT.md`; no concurrent external source edits were observed during these checks.

Production application/submission tables remain unchanged: **20 filled application records, zero submission attempts**, matching the pre-test snapshot. Root contact CSV remains private and untracked. Test answers, diagnostic resumes and browser evidence are not added to Git.

Only two necessary containers remain: the current jobagent and the isolated host-display container supporting noVNC6083. No redundant stopped containers or old agent image manifests remain; the display container must be retained. No volumes, browser/vault state or build cache were pruned.

## Reproduce a no-generation, no-submission check

The canonical Intel JD is retained. From the project root, this normal command borrows the existing test packet and stops before submission:

```bash
docker exec -it jobagent jobagent apply outputs/intel/JR0289456-1 intel \
  --no-submit --browser-mode docker \
  --documents-from outputs/zoox/ea3fbfaf-588b-434a-9a5a-c61edc731a40
```

Use the native HITL command printed by that run for questions. Unlike the private supervised harness used for this report, this normal command uses the owner's standard question/profile state. Borrowed documents deliberately make the application never submittable; exit5 at review is expected. A browser challenge still requires the owner. Inspect the running image's revision without printing its environment:

```bash
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' jobagent
```

## Evidence and next actions

Evidence directory: `outputs/verification/successfactors-corning-20261126/`.

- `status.json`: explicit per-matrix and retest status, including blocked cases.
- Each accepted run's `semantic-audit.json`, `final-readback.json`, screenshots and native log.
- `deployment-proof-current.json`, `release-source-manifest-current.json`, `final-runtime-proof.json`.
- `submission-count-before-remaining.json`, `submission-count-after.json`, `docker-cleanup.json`.
- Frozen before/after test snapshots/logs and rejected attempt folders.

Remaining live prerequisites: real human verification for SmartRecruiters and iCIMS, and legitimate clearance of UKG's server restriction. After access is available, complete the missing same-employer/different-role and different-employer matrix cases before checking off SmartRecruiters or UKG. Do not reopen blocked sign-in/reload loops while the owner is away.
