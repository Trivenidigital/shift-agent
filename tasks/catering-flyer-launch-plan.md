# Catering and Flyer launch implementation plan

**Drift-check tag:** extends-Hermes — repairs existing product transitions and uses existing rehearsal/release workflows; no parallel subsystem.

**Goal:** Prove customer-ready Catering and Flyer journeys by 2026-10-02 09:00 America/New_York, fixing verified blockers and reporting any remaining external prerequisites honestly.
**Authorization:** User explicitly authorized overnight work, controlled WhatsApp mimicry, and redesign where necessary after the three-vector launch review. Continue without routine confirmation. Live messages use verified operator/test identities only. No automatic commits under repository policy.
**Architecture:** Keep Hermes interpretation and existing deterministic stores, pricing, approvals, account activation, rendering and transport. Fix existing transitions; do not create parallel pipelines. Source-tested, deployed, transport-rehearsed and organic-customer-proven are separate evidence levels.
**New primitives introduced:** None.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Conversation and registered tool selection | Existing gateway and shift-agent-read plugin; https://hermes-agent.nousresearch.com/docs/skills/ | Reuse current handlers and controlled transcript tests |
| Paid account state transitions | No applicable replacement identified in checked skill hub | Repair existing account.py; no new billing system |
| Catering pricing and approval | Existing repo kernels own integer money and authorization | Reuse import, finalize and owner-decision paths |
| Flyer facts and rendering | Existing model generation, QA and manual queue | Reuse; verify actual artifacts |

Awesome Hermes ecosystem checked during preceding review: https://github.com/0xNyk/awesome-hermes-agent. No ecosystem replacement is needed for the repository-specific state-transition bugs; adding a new runtime would expand the launch scope.

## Baseline and governance

- Source HEAD: 98bfbd86851cb6f2aa4f565e781b9626a6b7b2f0; branch codex/catering-flyer-launch-20261002.
- Original checkout untouched. Its 15-file pending Catering patch is copied into this isolated worktree for validation; patch stored at ../original-catering-changes.patch, SHA256 9171B9D98C1F1CD9790FB580971C112F426B7ACFCA1E10E6DD18B7A50B2A1467.
- Universal v1.2.0 blob 31d7706d42b5a9477ce236f4dcb3841223b74a6f; registry v1.0.0 blob 207ad4183e4b0fd37b74eeec29e7dc215546c4cd.
- Catering v1.0.0 blob 858bd9f29d591e023b895ff5cbd2f3804f146ead; Flyer v1.0.0 blob fe45ce8094eb666057e8393be1e989ec64f7c324; shared v1.1.0 blob 9c6ce25607d59ac3dc3918ab4c908c77a65bde5f.
- No architecture exception proposed. Preserve pricing, sender authority, approval, STOP, idempotency, locked-fact and QR controls. Production evolution follows tests/review/PR/tarball policy. No direct source hotpatches.

## Capability Reuse Map — catering-studio
- Requested outcome: customer inquiry through accurate owner-approved quote delivery.
- Affected projects: catering-studio; shift-platform dependency.
- Applicable directives: docs/governance/engineering-directive.md, docs/governance/projects/catering-studio.md, docs/governance/shared-platform-directive.md.
- Existing platform/model capabilities reused: Hermes interpretation, menu extraction and conversation.
- Existing deterministic kernels reused: catering_pricing, quote ledger, owner decision, sender validation, safe_io.
- Existing stores/workflows reused: menu, pricebook, lead, proposal and approval records.
- Thin adapters: none planned; repair existing call boundaries if tests prove a gap.
- Custom runtime code genuinely unavoidable: only reproduced existing-path fixes.
- New subsystem: no.
- Evidence existing capabilities were insufficient: pending price gate conflicts with legacy menu-only finalization; validate chosen commercial workflow.
- Architecture exception: none.
- Shared-platform impact: shared transport runtime examined; any change requires caller compatibility tests.
- Other agents affected: Shift and Daily Brief share bridge; no routing or flag changes planned for them.
- Vertical E2E proof: inquiry, pricing, approval, delivery, STOP and duplicate/uncertain-send checks.

## Capability Reuse Map — flyer-studio
- Requested outcome: paid registration/trial upgrade through accurate flyer approval and delivery.
- Affected projects: flyer-studio; shift-platform dependency.
- Applicable directives: docs/governance/engineering-directive.md, docs/governance/projects/flyer-studio.md, docs/governance/shared-platform-directive.md.
- Existing platform/model capabilities reused: Hermes and current image providers.
- Existing deterministic kernels reused: account activation, fact locking, QA, repair, approval, delivery.
- Existing stores/workflows reused: customer profiles, payment records, projects and manual queue.
- Thin adapters: none.
- Custom runtime code genuinely unavoidable: existing trial upgrade transition correction; single-item layout and counted-copy prompt; marker-scoped OCR duplicate-headline check.
- New subsystem: no.
- Evidence existing capabilities were insufficient: trial with pending paid plan returns no_pending_activation; a managed synthetic image repeated its title and included empty menu rows despite QA passing.
- Architecture exception: none.
- Shared-platform impact: bridge delivery prerequisite; no alternate transport.
- Other agents affected: none for Flyer-local account/render/QA repairs.
- Vertical E2E proof: paid activation, request, real rendered artifact, approval and package delivery; trial replay and wrong-payment guards.

## Capability Reuse Map — shift-platform
- Requested outcome: connected, correctly identified and controlled customer transport.
- Affected projects: shift-platform, catering-studio, flyer-studio.
- Applicable directives: docs/governance/engineering-directive.md and docs/governance/shared-platform-directive.md.
- Existing platform/model capabilities reused: Hermes gateway and WhatsApp bridge.
- Existing deterministic kernels reused: sender validation, outbound gate, audit, existing health checks.
- Existing stores/workflows reused: deployed runtime and tarball release/rollback.
- Thin adapters: none.
- Custom runtime code genuinely unavoidable: not established; diagnose first.
- New subsystem: no.
- Evidence existing capabilities were insufficient: 02:29 UTC live bridge health disconnected despite setup gate READY.
- Architecture exception: none.
- Shared-platform impact: any restart affects all gateway customers; inspect queues and reason before recovery.
- Other agents affected: Shift, Daily Brief and any other active shared-gateway agent; preserve config and state.
- Vertical E2E proof: connected transport and controlled actual inbound/outbound evidence; simulation does not substitute for live proof.

## Runtime assumptions to verify before acting
1. Current deployed source hashes and feature flags match the tested path.
2. Bridge session is connected; determine disconnect cause and whether relinking requires the user's device.
3. Owner identity differs from bridge identity and approval reaches the owner handler.
4. Actual menu/pricebook supports chosen quote; deposits are disabled for initial slice.
5. Flyer selected account state and payment destination permit activation; F0226 current disposition known.
6. Known test identities, traffic/queue rate, operator alert path and bounded rehearsal cost permit safe tests.

## Capability Reuse Map — repo-meta
- Requested outcome: persistent execution and evidence record.
- Affected projects: repo-meta.
- Applicable directives: docs/governance/engineering-directive.md and docs/governance/projects/repo-meta.md v1.1.0 (read from current HEAD).
- Existing platform/model capabilities reused: existing tasks documentation conventions.
- Existing deterministic kernels reused: none; documentation only.
- Existing stores/workflows reused: tasks/todo.md, appended without removing history.
- Thin adapters: none.
- Custom runtime code genuinely unavoidable: none.
- New subsystem: no.
- Evidence existing capabilities were insufficient: existing task files suffice; documentation only, no runtime capability added.
- Architecture exception: none.
- Shared-platform impact: documents existing runtime checks only.
- Other agents affected: none.
- Vertical E2E proof: links to executed checks and artifacts, distinguishing simulated from live evidence.

## Execution checklist
- [x] Isolate checkout and preserve pre-existing changes.
- [x] Initial read-only live checks: gateway active, setup gate 17/17, bridge disconnected.
- [x] Diagnose bridge disconnect and gather sanitized live state; restore only through existing supported recovery if safe.
- [x] Flyer: test trial -> paid pending -> manual activation -> replay rejection/idempotency; minimal fix in account.py and tests/test_flyer_onboarding.py.
- [ ] Catering: verify pending price gate with existing owner-decision and lifecycle suites; prove priced path and expected refusal for missing commercial provenance.
- [ ] Exercise controlled transcript suites for normal, revision, unauthorized approval, STOP, duplicate and uncertain sends.
- [ ] Verify real flyer output using existing generation and QA if provider/test context permits; visually inspect final asset.
- [ ] Independent reviewers: code/money boundary plus runtime/operational readiness and reuse/scope.
- [ ] Prepare deployable patch, rollback notes and exact reviewable scope; do not bypass repository commit/PR gates.
- [ ] Record separate verdicts for source correctness, deployment, transport rehearsal and customer onboarding. No organic-traffic claims from simulation.

## Review focus
Trial upgrades must preserve payment-reference binding and reject wrong amount/currency. Catering finalization must not imply a quote is deliverable when its price is unapproved. No fake inbound may reach a real customer's identity. No retry after uncertain sends. Human queue completion requires actual fact/design inspection.

## Capability Reuse Map — expense-bookkeeper (test fixture only)
- Requested outcome: restore the existing vision-failure regression's ability to reach vision after image validation; baseline reproduces the same failure.
- Affected projects: expense-bookkeeper, test fixture only.
- Applicable directives: docs/governance/engineering-directive.md; docs/governance/projects/expense-bookkeeper.md v1.1.0, blob dc0f1783350b8f298e367680652ba498e00b58dc.
- Existing platform/model capabilities reused: existing receipt image normalization and mocked vision boundary.
- Existing deterministic kernels reused: existing orphan audit/persistence assertions, unchanged.
- Existing stores/workflows reused: the test's isolated temporary receipt and lead files.
- Thin adapters: none.
- Custom runtime code genuinely unavoidable: none; create a valid synthetic JPEG using the existing Pillow dependency instead of invalid fake bytes.
- New subsystem: no.
- Evidence existing capabilities were insufficient: existing capabilities are sufficient; the fixture failed image validation before reaching its intended injected vision failure.
- Architecture exception: none.
- Shared-platform impact: no runtime impact.
- Other agents affected: none; no Expense runtime change, activation, or accounting write.
- Vertical E2E proof: existing offline orphan/vision-failure test reaches its intended boundary; no live Expense readiness claim.

## Results
In progress. Hourly thread heartbeat `catering-and-flyer-overnight-readiness` continues authorized work through deadline and pauses after closeout.

### User steering — Jev
- User asked to verify API-key availability and use Jev where helpful. No Jev integration found in current repository source/docs; no JEV/TypeSafe-named key found in project .env, local process/user/machine environments, Codex config, deployed Hermes/app .env files or gateway process environment. Only setting names/presence were inspected; credentials were not printed.
- Official TypeSafe API reference verified: https://api.typesafe.ai/redoc. Await confirmed provider/key location (location or setting name only, not secret text). Do not send an existing key to an unverified third-party endpoint.
- Candidate use after authentication: bounded structured QA on synthetic rehearsal conversations, measured against known expected outcomes. Preserve existing deterministic pricing, authorization, STOP and delivery controls. No runtime routing replacement or new subsystem proposed; current full source suite continues unchanged.

### User steering — funding restored
- User reported funding; reran the existing isolated synthetic deployed quality smoke. It succeeded with one high-quality Gemini image, correct dimensions, and passing text/OCR checks. No real customer messages.
- Human inspection rejected composition: model-painted headline duplicates the deterministic header, and a large mostly empty panel obscures the design. Investigate whether smoke exercised the actual customer route before changing product code/config. Existing managed versus bare/integrated paths are being traced read-only.
- Evidence lives in the sibling directory `C:/Users/srini/.codex/worktrees/catering-flyer-launch/artifacts/`; it is outside the SME-Agents checkout. Preview: flyer-funded-preview.png.

## Capability Reuse Map — daily-brief (test isolation only)
- Requested outcome: prevent an existing Daily Brief test loader from replacing shared schema classes used by later Flyer tests.
- Affected projects: daily-brief test fixture; later Flyer tests regain consistent module identity.
- Applicable directives: docs/governance/engineering-directive.md and docs/governance/projects/daily-brief.md v1.0.0, blob 31e2cc80749510a85e5c8a4e0fc89cc073bc0e20.
- Existing platform/model capabilities reused: existing Python imports and the current test loader.
- Existing deterministic kernels reused: Daily Brief aggregation, send gates, and audit code unchanged.
- Existing stores/workflows reused: temporary test fixtures only.
- Thin adapters: test loader reuses already imported shared modules.
- Custom runtime code genuinely unavoidable: none.
- New subsystem: no.
- Evidence existing capabilities were insufficient: clean-HEAD diagnostic first diverged after test_aggregate_birthdays_match_today; loader unconditionally replaced shared schemas in sys.modules.
- Architecture exception: none.
- Shared-platform impact: test process isolation only; no runtime module or timer changes.
- Other agents affected: Flyer tests consume the same canonical schema identity; other runtime agents unchanged.
- Vertical E2E proof: reproduce test-order failure and verify the same sequence after the fixture repair; no Daily Brief production readiness claim.

### Runtime evidence, 2026-10-02 02:29–02:44 UTC
- main-vps source receipt a98431f5 (September 2); installed Hermes cc4cab2f matches the modern repository pin.
- Initial bridge disconnected after 428/408 closures; no render/send jobs active, outbound queue empty. Controlled existing `systemctl restart hermes-gateway` at 02:36 UTC recovered connection. Rechecked connected at 02:44 with empty queue. No credentials deleted, relinking, source deployment or customer sends.
- Existing gateway policy/sender-context and five-tool preflights passed on restart.
- `pilot-readiness-check --text`: 17 passed, 0 failed. This validates setup, not approval reachability.
- Owner config and environment identity both equal bridge account (suffix 1562): real independent owner approval blocked. Asked user for separate owned rehearsal number; awaiting reply.
- Live pricebook v2: placeholder=false, 77 item overrides, two packages. Deposit percentage 0. Source/price correctness still needs owner confirmation.
- Live menu v3 inspected read-only: 77 available items, zero with confirmed `serves`. Automatic selected-option sizing must refuse until serving sizes are supplied; existing explicitly quantified/package workflows are being checked for a narrower pilot.
- STOP/takeover master/subflags absent in inspected .env and initial process environment: pilot activation must be scoped to confirmed test identities, then verified through both gateway and timer send paths.
- Flyer has seven trial profiles, no pending upgrades; F0226 remains manual_edit_required since August 1. Live watchdog already contains one-shot customer-update guard. No state disposition fabricated.
- Actual configured image model google/gemini-3.1-flash-image-preview. Isolated synthetic fixture invoked existing deployed smoke and failed immediately with OpenRouter HTTP402 insufficient credits. Asked user to fund account. No image or customer send occurred. Retry only after funding or a user-approved viable provider change.

### Verification evidence
- Flyer one-line trial activation repair reviewed independently: GO, no HIGH/BLOCKER.
- Linux network-disabled container `sme-test:latest`, source mounted read-only: onboarding/payment/quota/lifecycle-copy/incident/rollout/send-chokepoint suites: 143 passed.
- Linux renderer/visual-QA/manual-queue suites: 286 passed, 1 skipped.
- Linux STOP/takeover/schema/gateway/bridge controls and stale-edit watchdog suites: 161 passed.
- Offline overlay unit artifact generated and visually inspected at ../../artifacts/overlay-check-1/test_render_premium_overlay_wr0/out.png. Fixture uses a solid background: proves exact-text compositor, not commercial generative design quality.
- Native Windows replay import failures were missing fcntl; Linux rerun passed. Do not count those as product defects or mask production locking.
- Planning-file overwrite was caught in isolated worktree and corrected immediately: original tasks/todo.md restored byte-for-byte as prefix, launch checklist appended (18 added lines, no removed history).
- Catering repair in progress: bind delivered quote to exact frozen cents, ignore untrusted drafted money, preserve retry terms/expiry; independent review found date-crossing retry issue and sent it for regression repair.

## Plan revision — selected-option quantities

The captured existing lifecycle exposed a real production path gap: a 50-guest request selects eight dishes at qty=1 each ($50.92). `select-catering-proposal._selected_items` supplies these explicit quantities, bypassing existing headcount scaling in finalization. Price overrides do not fix serving coverage. Independent structural and strategy reviews agreed this blocks the release.

**Approved implementation ruling under user's overnight authorization:** reuse a small shared servings arithmetic helper extracted from default_basket, restricted to exactly the selected canonical names. Automatic selected-option sizing requires known headcount and menu servings; unknown serving units refuse before selection mutation/customer pricing with actionable menu-correction guidance. Never invoke cheapest-package/first-five-item default selection, never invent serving data, never clamp an undersized order. Existing explicit quantities and package workflows remain unchanged. No schema/store/new subsystem.

- [x] Test 50 guests/10 servings -> 5 units; missing servings/headcount refusal; exact selected-name preservation; quantity overflow refusal.
- [x] Test explicit quantities/packages unchanged, message replay stable, current headcount honored and concurrent headcount change refused.
- [x] Implement in separate catering-portion-readiness worktree while first candidate full suite stays frozen.
- [x] Independent code and strategy review, integrate seven-file patch, affected Linux group: 276 passed, zero skips/failures.
- [x] Recapture corrected simulated transcript; prior transcript retained as defect evidence only. Corrected fixture explicitly uses synthetic servings=10, not approved live menu data.
- [x] Run final full test checks after test-harness failures are classified/repaired (10,137 passed, 56 skipped).

The prior 777-test result proves the first candidate's checked contracts, not portion correctness or the expanded candidate. Release authorization remains conditional on final scope review and green checks.

### Full-suite triage and final verification setup
- First candidate full run deliberately interrupted at 62% after portions expanded the candidate: 56 failed, 6,245 passed, 79 skipped. It is not a complete release gate.
- Baseline comparison reproduced expired Catering date fixtures and an invalid Expense JPEG fixture. Those fixtures are repaired without runtime changes. Exact-money E2E assertion now checks independently computed cents rather than the legacy rounded dollar field: 17 passed. Expense fixture suite: 4 passed.
- Flyer class-identity failure reproduced with three existing tests on clean HEAD. Daily Brief's test loader replaced shared schema modules. Test-only import fix plus regression integrated; four affected files: 177 passed. No runtime schema validation weakened.
- Final test environment adds Git and extracts a frozen source archive into disposable Linux storage, preserving executable modes and allowing explicit compilation. It indexes files for repository tests without commits. Source hashes are checked after execution.
- Continuation: inspect running container `codex-launch-final-tests-20261002` and `../../artifacts/final-suite.log` / `final-suite.exit` before starting any further suite. Root execution session 95429. Do not replace `final-source.tar` while this run is active. Verify `final-source-integrity.json` at completion, classify failures, and update the release record. Source snapshot SHA256 c6a3ba12e339d7a77fe181d0dc61402d220958f14ca6d3d6bb2ef339eb07335a.

### Verification update — 03:55 UTC
- Frozen full suite completed: **115 failed, 10,005 passed, 56 skipped**. All 1,785 source files retained their hashes. This is a failed release gate.
- Failure triage: five Catering lifecycle fixtures lack the newly required synthetic serving facts; eight skill tests assert obsolete generated quote prose; one tarball fixture requires Git HEAD; 101 other failures pass together in a fresh process (485 passed), indicating test-order contamination. Shared-schema replacement is reproduced; remaining safe_io leakage is under investigation.
- All four known Daily Brief/shared test loader repairs are now integrated; affected group: 213 passed. No runtime validation weakened.
- The next disposable Linux snapshot will contain a synthetic fixture commit solely to support tests requiring HEAD. No source worktree is committed or deployed.
- Correct managed Flyer rehearsal inherited verified live flags and used actual deployed managed generation, isolated synthetic state and no outbound delivery. One image and one OCR call succeeded after funding. Human inspection found repeated Dosa Night title and two empty menu rows; automated QA incorrectly passed. Evidence: ../../artifacts/managed-rehearsal/.
- Captured prompt supplies the title only once and one menu item. Existing plural layout guidance competes with the one-item design; this is one observed model failure, not a claim that repetition is inevitable. Narrow existing prompt/layout and OCR QA repairs are being developed in the isolated flyer-render-readiness worktree. No new rendering subsystem.
- JEV key remains unavailable in inspected configured locations. Awaiting key location/secret name; no key values requested or printed, and no JEV invocation claimed.

### Managed Flyer candidate rehearsal — 04:02 UTC
- Candidate changes extend existing single-item prompt assembly and marker-scoped OCR QA; no new classifier, provider, workflow or retry budget. Independent strategy and structural review found no blocker in the narrow change. Focused Linux result: 267 passed.
- Original captured duplicate-title image now fails offline QA using its original OCR and matching image SHA. The new blocker routes to existing manual review and does not trigger an irrelevant item-spelling retry.
- Actual isolated candidate generation completed with one image request and one OCR request, zero outbound notifications. Source hashes recorded in ../../artifacts/managed-candidate-rehearsal/evidence.json. Preview SHA a93dc898dae004a95180d5be923a906c14c2f06d1c4bcd21991fb96f9d2480cf.
- Human inspection confirms duplicate title and empty menu rows are absent. However, an unrelated roast-chicken icon appears beside the dosa offer; final approval is held while the existing prompt is narrowed to pictorial elements matching the declared dish. This is one observed image, not evidence of a guaranteed generative quality rate.
- Candidate final-package harness is prepared but unexecuted; it requires the exact visually reviewed preview SHA, copies only synthetic state, blocks new image generation/customer delivery, and permits at most six existing OCR calls.

### Frozen full verification v2
- Running container codex-launch-final-v2-tests-20261002; source archive SHA256 3e54f6e9e7320a3b131936614251b610fbfaed184632cc49bb423de8d4bf6f62. Root session 11729; evidence ../../artifacts/final-v2-suite.log and final-v2-suite.exit. Do not replace final-source.tar while running.
- Includes all four shared test loader fixes, Catering test-contract updates, synthetic Git HEAD fixture, and Flyer prompt/QA repair including dish-faithful icons. Focused Flyer regression group 268 passed.

### Current continuation state — v3 supersedes v2
- V2 was intentionally interrupted at 573 passed/1 skipped after the exact remaining fixture leak was identified: test_flyer_update_project created a MonkeyPatch without undo. Its two-line fixture fix reproduces red then green and passes 167 affected tests. Partial v2 evidence is retained under ../../artifacts/full-suite-v2/.
- **Current full run:** codex-launch-final-v3-tests-20261002, root session 90838, log ../../artifacts/final-v3-suite.log, completion code final-v3-suite.exit. Snapshot SHA256 3d8a966cbb18bdebca8d5ebc1acc40b717d391c57b5e27d5132cf3bffe072f30. Do not rebuild/replace final-source.tar until it finishes.
- Final-source managed render v2 was correctly blocked for a repeated item; v3 (same source, separate synthetic attempt) passed OCR and two visual reviews. Accepted preview SHA23e2c8d3328458120568b16b244421d258410c9a39443e9d50f0aa68bd69522f. One accepted/one rejected final-source sample is not a success-rate estimate.
- Four actual final formats passed QA; independent PDF render and square/story inspection preserve content (optional social formats use white padding). Finalize made4OCRcalls/0imagecalls/0sends and retained original evidence.
- Existing deployed send-flyer-package --dry-run-bridge passed with real cached QA/hash gates: all4formats, only dry-run outbound IDs,0network/0subprocessattempts, copiedstate delivered, originalfinalevidence unchanged. Evidence ../../artifacts/send-rehearsal-summary.json. No real identity/transport approval claim.
- Original checkout diff still byte-for-byte matches preserved initial15-file patch, SHA9171b9d98c1f1cd9790fb580971c112f426b7acfca1e10e6dd18b7a50b2a1467. Source has not been committed/pushed/deployed.
- Live bridge follow-up approximately04:16UTC connected, queue0, uptime5957s.
- After full result: inspect source-integrity report and failures; update release report/todo/reviewbody, regenerate reviewbundle with exact files, rerun governance/diff checks. Do not infer success while fullsuite pending.

### Completed source verification
- Full v3 finished exit0:10,137passed/56skipped/193warnings in1118.97s. Source integrity:1,787files,mutated[]. No runtime/test edits after frozen snapshot; only status docs updated.
- Source review candidate ready. Real owner identity/STOP transport rehearsal, pilot commercial facts and explicit commit/release authorization remain pending. Do not call production ready or repeat paid synthetic generation absent a new issue.


## Authorized release continuation — 2026-10-02

The user explicitly authorized commit, PR and deployment after checks, and controlled WhatsApp rehearsals with owner +918522041562 and customer +17329837841. Approval does not supply missing serving quantities. Reuse the six Capability Reuse Maps above. No new subsystem or architecture exception is proposed.

The old checkout was removed externally. All 39 reviewed files were recovered byte-for-byte from the SHA-verified review bundle into branch codex/catering-flyer-release-20261002. Original checkout remains untouched. Fresh full-suite verification runs from a frozen recovered snapshot.

- [x] Restore and verify exact reviewed file hashes.
- [ ] Complete fresh full tests, governance and independent release/runtime reviews.
- [ ] Commit reviewed candidate; create/attach PR; complete required checks and authorized merge.
- [ ] Build canonical tarball from clean reviewed main commit, verify source modes and artifact SHA.
- [ ] Verify current deployed receipt, ancestry, idle queues, identities, flags and rollback target.
- [ ] Deploy through existing staged tarball procedure; verify hashes, runtime and pilot-readiness gate.
- [ ] Scope existing intake/STOP/takeover settings only after owner echo/reachability review. Shared gateway impact applies to all active agents; preserve nonpilot behavior or explicitly document any required restriction.
- [ ] Exercise verified test identities through real approval and delivery; retain audit and transport evidence.
- [ ] Report truthful product readiness; defer broad onboarding and automatic Catering sizing if facts remain unavailable.


### Live role correction and release review

At 2026-10-02T23:51:37Z the user-designated customer phone was removed from owner.authorized_identities only. Backup: /opt/shift-agent/config.yaml.before-customer-role-20261002T235137Z. Schema validation passed before/after. identify-sender now returns owner-only for India and employee-only (no owner) for both US phone and its LID. Existing roster e008 was preserved; customer-path evidence will therefore be employee-as-customer, not stranger onboarding. Gateway cached settings will be reverified after the authorized deployment restart.

Independent scope review: actual live receipt a98431f5 is an ancestor of candidate base98bf; intervening committed changes are audit docs and retirement of a LID-cache patch generator. Existing live Hermes pin gate passes with all overrides unset; do not use the stale July override. Prior deploy-20260902-015029-a98431f5.tgz is present for rollback.

Owner forwarding remains disabled: review showed bot-mode outgoing chunks/media captions are not persistently marked, and recentlySentIds resets on restart. Enabling forwarding alone is unsafe. Existing OTP-protected cockpit owner decision is an alternative for a supervised Catering pilot; WhatsApp self-owner proof remains blocked.


### Scoped configuration staged

2026-10-02T23:54:02Z: existing master, STOP and takeover flags set to1 for only the two verified phones and their LIDs; FLYER_STYLE_REGISTERS_ALLOWLIST narrowed from* to the US customer phone/LID. Backup /root/.hermes/.env.before-pilot-controls-20261002T235402Z. Every other environment value preserved; env symlink unchanged; forwarding remains off. Independent reviewers verified matching and consuming paths. No new store/control implementation.

Propagation requirement: gateway, cockpit and relevant long-running owner-action-watchdog processes retain old env until restart. Timer jobs load EnvironmentFile on next run; disabled follow-up timer stays disabled. Verify effective process env after source release. New typeset generation is scoped; duplicate-headline QA still checks existing artifacts bearing a typeset marker regardless of allowlist.


### Fresh release gate passed

Recovered candidate full Linux suite: **10,137 passed,56skipped,193warnings in1130.97s; exit0**. All1,787snapshot files unchanged. Evidence: ../artifacts/release-suite.log,release-suite.exit,final-source-integrity.json. Governance and skill manifest pass. Staged39files match worktree bytes;11files differ from recovered bytes only by Git-required LF normalization. Existing source/test bytes match the verified frozen input after that normalization; no runtime behavior changed while tests ran.

The canonical tarball builder will use --skip-pytest after this complete exact-source gate to avoid repeating the same18-minute suite; its skill-manifest check remains mandatory. Linux clone from the actual release commit preserves executable modes. Every packaged file byte and executable mode must match its Git blob before shipping.


### CI dependency correction

The local full suite passed, but PR send-path CI failed one of 6,488 tests: the repaired Expense fixture imported optional Pillow, while that CI job deliberately installs only pytest, Pydantic and YAML. Replace only fixture generation with identical valid JPEG bytes using the standard library; retain actual image validation and all orphan/audit assertions. No production code or CI dependency change. Verify both with and without Pillow, then rerun CI before merge/deploy.

Fixture verification: 4 tests passed with Pillow and the same 4 passed without Pillow in isolated Linux containers. CI failure retained; the original 10,137-test local full result remains evidence for the runtime candidate, followed by this test-only correction.


### Authorized deployment completed — 2026-10-03

PR #796 passed all eight checks and was squash-merged to main commit 0ea5af389a4625fd31f529cb9f87bb6064b77840. Canonical tarball SHA256 f258949763dfe88b841ee63ade2893ba01f57fb0b9cdc89633380802148aa807; all 421 packaged file bytes and executable modes match Git. Remote checksum verified before extraction. Existing staged deploy completed as deploy-20261003-004544-0ea5af38 with all smoke checks passing; DEPLOY_RECEIPT.json confirms the full commit. No pin override or Hermes/bridge upgrade.

Postdeploy gateway, cockpit and owner-action watchdog active; bridge connected with queue zero; pilot-readiness-check passed17/17. All three processes have the scoped automation settings, owner forwarding remains off, and catering-followup-sweep.timer remains disabled. Installed closure gate proved76 flat runtime modules byte-identical and80 dependency imports current. Prior rollback retained.

Controlled STOP/RESUME simulation used real normal WhatsApp acknowledgements to the verified US number, with zero intervening suppression sends and final conversation mode active. This does not prove genuine handset ingress or customer-authored approval. Catering serving quantities remain unconfirmed, and self-owner WhatsApp approval remains blocked; use the existing OTP-protected owner cockpit for supervised approval.

New runtime finding: linked SQLite3.50.4 warning on existing Hermes WAL databases. Investigating supported dependency remedy separately; no blind Hermes upgrade or live journal-mode change.


### Reproduced postdeploy brief routing gap

The normal public router rejected an explicit NEW fictional test brief because factual Business name: metadata matched the existing account guard before generation. F0226 unchanged; no new project/render. Planned narrow fix: retain exact account-command precedence; only for explicit strong NEW requests remove standalone declarative Business name:/Business address: label tokens from matching, preserve their values, then apply every existing account/payment guard. Reuse existing cf-router functions/store/locked-fact/approval/render/send workflow; no new primitive or subsystem. Shared impact: routing correction for Flyer, retaining account protection and Catering priority. Existing Capability Reuse Maps and Hermes-first analysis remain applicable. Validate recorded/newline briefs plus mixed payment/mutation cases; independent safety review; normal CI and canonical redeploy. User's existing approval covers fixing reproduced launch blockers.

Independent review found two mixed account-update phrases could lose the label guard. Both added regressions failed; label exclusion now applies only without change/update/set/edit/modify/replace/remove/delete verbs anywhere. This deliberately preserves existing conservative handling for briefs mixed with editing/account instructions. Plain brief live rehearsal generated F0227 and bridge-confirmed preview3EB0404DC25A3350A29EA1; approved test headline and profile facts visually inspected, separate simulated approval pending.

Final conservative condition additionally preserves guard for save/rename/store/remember/overwrite/reset and account/profile/settings/details/saved contexts. Three further reviewer cases reproduced failing and corrected. All415 router tests pass.

F0227 rehearsal completed through public router: actual preview, visually inspected headline/item9.99/lockedfacts, separately quoted simulated APPROVE, all4normal final asset sends bridge-confirmed (WhatsApp image, Instagram post/story, PDF). F0226 unchanged. Inbound request and approval are explicitly simulated, not handset or organic evidence. Unlabeled raita in food photography accepted as accompaniment context, no extra offered/priced dish claim.
