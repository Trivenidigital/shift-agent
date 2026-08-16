# Autonomous session handoff — 2026-08-14 — 20-agent production push

## 1. EXECUTIVE STATUS

- **21 PRs merged** (#695–#714 in the deployed cut; #715 identity-memo sequenced alone, in CI at handoff time), every one adversarially verified by an independent agent before merge, every one CI-green. #680 closed as superseded.
- **Production deployed and runtime-verified:** `deploy-20260814-162029-d6f9ba8c` live on main-vps; byte-exact plugin parity, services green, alert channel proven (deploy-OK page delivered via Pushover).
- **Agent counts (post-session):** PRODUCTION_READY 2 (#4 Daily Brief, #5 EOD) · READY_TO_DEPLOY-dormant 1 (#19 Equipment — new this session) · READY_WITH_BUSINESS_INPUT 2 (#3 Multi-Location, #13 Compliance) · DEPLOYED_AWAITING_LIVE_E2E 2 (#2 Catering, #21 Expense) · PARTIAL 2 (#1 Shift, Flyer Studio) · REACHABLE_DORMANT 1 (#10) · BLOCKED_UNSUPPORTED 1 (#16 Sales Tax, refusal-only by design) · NOT_IMPLEMENTED 8 · RETIRED 3.
- **Catering verdict: CATERING_NOT_READY — on OPERATOR GATES only.** All engineering P0s closed this session (diet-aware proposals passed a three-round adversarial cycle; the owner-approval fromMe class was formally ruled accepted-risk pending the third owner number). What remains is the operator gate list + scheduled P1s, none producing a wrong customer artifact on an ungated path.
- **Flyer verdict: FLYER_STUDIO_NOT_READY — materially hardened.** Both audit P0s in code are closed (#696/#697/#702/#707 + #704/#711); remaining: the receipt-as-logo classification design ((b)+(c) spec ratified and saved, unbuilt), bare-path fact-safety fail-open, payment activation, reservation leak, jargon seam — plus credential/business gates.
- **Structural findings that reframed the portfolio:** the dispatcher SKILL matrix is non-executable in production (runtime-confirmed toolset disables); live entry classes are cf-router intercepts, systemd timers, and `shift_agent_read` plugin tools. The master spec overstated liveness for 3 of its 5 "LIVE" agents; Flyer — the largest real system — is absent from it.

## 2. 20-AGENT MATRIX
(see handoff-matrix.md — include verbatim)

## 3. CATERING
(see handoff-catering-section.md — include verbatim, incl. the amended verdict)

## 4. FLYER STUDIO
(see handoff-flyer-section.md — include verbatim; note #705/#706/#709/#711 landed after its drafting and are reflected in the ledger)

## 5. CHANGES
(see handoff-changes-ledger.md — include verbatim: 20-PR ledger + near-ready branches + follow-ups + standing rules learned)

## 6. PRODUCTION

- Deploy `deploy-20260814-162029-d6f9ba8c` @ commit d6f9ba8c; artifact sha256 c09929e8… (6,564,857 B, 518 entries, retained at C:/projects/_artifacts/); snapshot /root/pre-d6f9ba8c-state-20260814-161521.tgz sha 9a44cd6d… (1954 entries).
- First attempt failed-safe and auto-rolled-back (installed-vs-staged deploy-script bootstrap gap — full account + lesson in the deploy-authorization record). Second attempt per canonical checklist succeeded.
- Post-verify all green: commit-hash, services, NRestarts=0, cockpit 200, plugin byte-parity (CRLF era over), pycache fresh, sweep timers enabled, Pushover delivery proven.
- Rollback anchor: deploy-20260812-034757-ee45bd8f (on-box) + snapshot; catering-state-downgrade covers the additive lead fields.

## 7. LIVE TESTS

- Physically exercised this session (read-only or reversible): box config/env/state probes throughout (flags, stores, timers, plugin hashes); the deploy itself + smoke incl. real Pushover delivery; brand-asset baseline (B0009/B0010 verified cleaned with audit rows, only B0008 active).
- Deliberately NOT live-tested (operator/device-dependent): owner receipt/menu photo E2E (owner identity = bridge account — third number needed); catering pilot flows (env-gated); acceptance/amendment arming; Shift coverage rehearsal (real phones); Pushover priority-2 (re-notifies 60s×1h — do not fire casually).
- Structurally untestable from repo/session: Hermes fromMe logging to agent.log; live identify-sender latency; flock behavior in prod (suite stubs it by construction).
- EXPECTED within ~4h of deploy: exactly ONE F0226 recovery page (deliberate). If it does not arrive, that itself is a finding (check flyer-recovery-watchdog + Pushover).

## 8. OPEN BLOCKERS (genuine, classified)

- CREDENTIALS: both codex + claude OAuth tokens on main-vps revoked (recovery worker stays DETECTION_BUNDLE_ONLY; needs interactive login on the box; prefer claude — narrower sandbox; change worker_runner+worker_model together). QBO = per-customer Intuit credentials (mock-only fail-closed until then).
- BUSINESS INPUT: third owner-controlled number (unblocks owner receipt/menu E2E, F8 hook-path approvals, and the dropped PR-A class — REPLACEMENT of owner.phone scalar, not addition). Per-customer data: locations list, compliance items+enable, equipment enable+seed, pricebook, chart-of-accounts.
- OPERATOR ACTIONS: catering pilot gates (Pushover fallback test, pricebook+hand-calc, Stage A, sequential activation); sweep arming (dry-run first — line count == first-run alert count); acceptance/diet arming checklists (punchlists in the catering section incl. the "is free-text diet detection authoritative or owner-reviewed?" question); F0226 disposition after its page; manual-queue drain rota.
- ARCHITECTURE DECISIONS: fromMe/self-chat handling (recommend the number, not code); WhatsApp Business API / Hermes-upgrade path (long-standing); flyer flag-flip drain/quiesce design.
- EXTERNAL/UNSUPPORTED: sales-tax filing APIs (refusal-only posture correct); DocuSign/POS integrations for the unbuilt agents.

## 9. NO-GO FINDINGS (intentional behavior — do NOT "fix")

- The false-success invariant (`claims_action_completed AND NOT verified_action_result => REFUSE`) is context-bound at both outbound seams — never convert to a word blacklist (a test fails deliberately if you do).
- The deploy gate REFUSES stale payloads and a stale skills-manifest REFUSES tarball builds — both are correct fail-closed behavior, not bugs.
- catering-followup-sweep.timer is deliberately NOT enabled (triple-gated feature).
- multi_location_query is SHELVED by Phase-3 ruling (privacy: needs code-enforced location scoping).
- PR-A (fromMe F8 hoist) is DROPPED BY RULING: the agent's own cards are valid approve commands under the unanchored F8 grammar and authorship is structurally indistinguishable on the self-chat — the watchdog is the compensating control; the fix is the third number.
- "no non-veg" → unknown and verbal-negation diet phrasings → permissive branch are ACCEPTED pending the operator's authoritative-vs-owner-reviewed ruling.
- Windows-red test counts in unfixed files are import-order artifacts, not failures (post-#701 mostly gone; two files remain on the list).
- test_non_mixed_lead-style pins that LOOK wrong were replaced deliberately — check PR history before "fixing" tests that pin refusals.

## 10. NEXT SESSION ENTRYPOINT

1. main = d6f9ba8c deployed + verified; #715 (identity memo, MERGE_CERTIFIED at e13f0ce) merges alone on green CI, rides the NEXT deploy.
2. Frozen branch fix/catering-send-status-remaining @ cf4cf436 (pushed? NO — local worktree catering-p17b): mint-deposit + ack done + harness fix; amend reapply spec at scratchpad/amend-catering-lead-READY.md (safe_io fix it needed is MERGED in #713); select-catering-proposal (12 sites — must update the e2e harness injection) + create-catering-proposal-options remain.
3. Next flyer build: brand-asset (b)+(c) per scratchpad/design-brand-classifier.md (active=False ambiguous captures + one-shot clarification) — after #715 merges.
4. Check the F0226 page arrived; then operator triages it.
5. Deploy-builder determinism WIP abandoned in deploy-b worktree (uncommitted) — redo per its task spec if wanted; also fix the builder's usage hint (run from staging) — the bootstrap lesson.
6. Verify-first list for any new session: box .commit-hash, open PRs, tasks/audits/deploy-authorization-d6f9ba8c.md (when committed), this handoff.

---

# 20-AGENT MATRIX — post-session (main @ 279c3f8e+, box @ ee45bd8f pre-deploy)

Entry-point legend: the dispatcher SKILL matrix is NON-EXECUTABLE in production (runtime-confirmed: /root/.hermes/config.yaml:42 disables delegation/skills/browser/clarify/terminal/code_execution/file). Live entry classes: cf-router pre-LLM intercepts, systemd timers, shift_agent_read plugin tools (toolset name survives the disable list — progressive Tool Search discovery).

| # | Agent | Status | Tier | Reachability | Session delta | Blocker / next |
|---|---|---|---|---|---|---|
| 1 | Shift | PARTIAL | live | Timers LIVE (health/fsck/backup/proposal-sweep); sick-call MESSAGE path DEAD (dispatcher-only) + self-refuting F9 alert (hooks.py:6820 pages "verify handle_sick_call fires" then yields to an LLM that cannot run it) | — (not scheduled; P1a on backlog) | Deterministic cf-router sick-call arm (menu-photo pattern) or honest F9 alert copy; coverage rehearsal is operator-gated (real phones) |
| 2 | Catering | DEPLOYED_AWAITING_LIVE_E2E (code hardened) | SUPERVISED | cf-router deterministic: F8 approvals (via watchdog — hook path fromMe-blocked, RULED accepted-risk), F7 qualification, acceptance (armed=0), menu ingestion, amendment conflict (dormant) | #697 #699 #704 #708 #710 merged; diet v2 CONDITIONAL; escape-gate certified-pending; PR-A DROPPED (unsafe) | See catering section. Pilot gates unchanged (operator): Pushover, pricebook, Stage A, sequential activation |
| 3 | Multi-Location | READY_WITH_BUSINESS_INPUT | READ | find_nearest_location plugin tool LIVE; multi_location_query SHELVED (Phase-3 ruling, privacy) | — | locations: [] on box — per-customer store list = onboarding data. docs/portfolio.md "LIVE" claim overstated |
| 4 | Daily Brief | PRODUCTION_READY | live | send-daily-brief.timer, enabled, firing (brief_skipped dedupe rows daily) | — | none |
| 5 | EOD Reconcile | PRODUCTION_READY | live | eod-reconcile.timer, enabled, firing | — | none |
| 6 | Inventory | NOT_IMPLEMENTED | — | no entry point of any class | — | scaffold only |
| 7 | Supplier | NOT_IMPLEMENTED | — | none | — | scaffold only |
| 8 | Receiving & QA | NOT_IMPLEMENTED | — | no src dir | — | paper spec |
| 9 | VIP | NOT_IMPLEMENTED | — | none | — | scaffold only |
| 10 | Catering Follow-up | REACHABLE_DORMANT | — | catering-followup-sweep.timer installed NOT enabled (deliberate; triple-gated) | — | operator arming decision + §12a followups watchdog precondition |
| 11 | Festival Prep | NOT_IMPLEMENTED | — | no src dir | — | — |
| 12 | Hiring | NOT_IMPLEMENTED | — | none | — | DocuSign unbuilt (no ecosystem skill) |
| 13 | Compliance | READY_WITH_BUSINESS_INPUT | READ+cron | check-compliance-deadlines.timer (daily, honors enabled flag) + get_compliance_deadlines plugin tool (owner-gated) | #700: tool now honors compliance.enabled (phantom lever closed); box store NOT seeded → truthful non-answers | per-customer items + enabled:true = onboarding data |
| 14 | Employee Docs | NOT_IMPLEMENTED | — | none | — | scaffold only |
| 15 | Cash & AR | NOT_IMPLEMENTED | — | none | — | Stripe/Square per-customer |
| 16 | Sales Tax | BLOCKED_UNSUPPORTED_INTEGRATION | refusal-only | none | — | no filing API exists; truthful refusal posture correct |
| 17 | Unit Economics | RETIRED → #22 | — | — | — | — |
| 18 | Complaints | RETIRED → #9+#4 | — | — | — | — |
| 19 | Equipment Maint. | READY_TO_DEPLOY (dormant) | READ | get_equipment_maintenance_due plugin tool (owner-gated, enabled-flag honored, four truthful states) + seed CLI | **#695 (supersedes #680, closed): NOT_IMPLEMENTED → READY** | per-customer: enabled:true + seeded store. Live E2E blocked by owner-identity collision |
| 20 | Owner Wellbeing | RETIRED → #41 | — | — | — | — |
| 21 | Expense Bookkeeper | DEPLOYED_AWAITING_LIVE_E2E | DRAFT-only | cf-router owner-receipt intake (#690+#694 precedence) + prune timer | #694 runtime-verified this session (load-level); B0009/B0010 cleanup VERIFIED complete w/ audit | Physical receipt E2E blocked by owner-identity collision (third number); QBO = mock-only fail-closed (credentials, per-customer) |
| 22 | P&L Anomaly | NOT_IMPLEMENTED | — | none | — | needs POS provider |
| — | Flyer Studio | NOT_READY (materially hardened) | SUPERVISED | cf-router primary surface (~20 intercepts) + 2 watchdog timer pairs | #696 #697 #698 #701 #702 #703 #704 #705 #706 #707 #709 #711(CI) — see flyer section | See flyer section: remaining P0 = receipt-as-logo routing ((b)+(c) designed, unbuilt); P1s: bare-path fail-open, reservation leak, payment activation, jargon seam, flag-flip drain |

Counts: PRODUCTION_READY 2 (#4,#5) · READY_TO_DEPLOY dormant 1 (#19) · READY_WITH_BUSINESS_INPUT 2 (#3,#13) · DEPLOYED_AWAITING_LIVE_E2E 2 (#2,#21) · PARTIAL 2 (#1, Flyer) · REACHABLE_DORMANT 1 (#10) · BLOCKED_UNSUPPORTED 1 (#16) · NOT_IMPLEMENTED 8 · RETIRED 3.
Session start counts for comparison: PRODUCTION_READY 2 · reachable-dormant 3 · PARTIAL 3 · NOT_IMPLEMENTED/NOT_REACHABLE 10 — the movement is #19 (new capability), #13+#3 (query paths credited + phantom lever closed), #2/#21/Flyer (deep hardening), plus 16 merged correctness/safety PRs.

---

# CATERING (drafted by catering-auditor, final; lead annotations in [brackets])

## 1. VERDICT (amended after diet v3 PASS)
**CATERING_NOT_READY — on OPERATOR GATES only.** Engineering P0s are closed: P0-1 diet-aware proposals PASSED its third adversarial round (v3 @ bdddea94, PR #714) — seven negation reproducers serve zero meat end-to-end, counter-cases and byte-parity hold. P0-2 is the accepted-risk fromMe ruling (business input: third owner number). What separates catering from pilot-ready is now the operator gate list (§4) plus the scheduled P1 remainder (§3), none of which produces a wrong customer artifact on an ungated deterministic path. Was at session start: 2 P0 + 9 P1 engineering blockers.
Diet punchlist (operator question attached): verbal-negation phrasings ("we don't eat meat", "please avoid meat", "we are not meat eaters") + "no non-veg" land on the permissive branch with owner-review-before-send as the control — CONFIRM whether free-text diet detection is expected to be authoritative or owner-reviewed before real traffic. Also "no beef please, Hindu family" now yields veg_only (safe direction, but under-serves a chicken-eating family; fix shape recorded).

## 2. CLOSED THIS SESSION
#699 sweep scheduling (P1-8, verified on merged main; followup-sweep deliberately unenabled) · #697+#704 menu-cession precedence + vocabulary (F0226 class, owner/employee half) · #708 send-status (P1-7, PASS: no re-POST after uncertain, truthful markers, Pushover pages; two audit-brief corrections absorbed) · #710 acceptance detector v4 (P1-9, PASS after 4 adversarial rounds; linear perf) · P1-6 escape-gate try-scope PASS at 2a572a37 [lead: merged as #712 if CI green — see changes list] · PR-A fromMe hoist DROPPED as unsafe = ACCEPTED-RISK decision (agent's own cards would self-approve; real fix = third owner number; watchdog = compensating control).

## 3. STILL OPEN, RE-RANKED
1. **P0-1 diet proposals — CONDITIONAL/GATING** (v2 unverified; v1 fails: \bveg matches "vegetable/Veg Biryani" → mixed events lose their meat; veg_only branch skips section/main coverage → side+dessert "menus").
2. **P0-2 owner-approval reachability — BUSINESS-INPUT-BLOCKED (accepted risk)**: fromMe return precedes F8; watchdog compensates (follow_bridge_log still zero test coverage). Unblocks on the third owner number.
3. **P1-3 customer media catering inquiry claimed by flyer arm — UNSCHEDULED** (hooks.py:694-702 bare menu/lunch/price/weekday tokens; cession is owner/employee-only; no test cell). [lead: flyer.enabled=True live → real severity]
4. **P1-4 identity memo — PR-D BUILT at e0c1661, UNVERIFIED, held out of deploy cut** (10→1 spawns/turn; sequence alone; next session: adversarial verify then merge).
5. **P1-10 role gating router-only** (3 scripts trust --sender-role; select-proposal + record-acceptance have NO sender↔lead ownership check; safe only while terminal/code_execution disabled = config not invariant). [lead: disabled_toolsets CONFIRMED live this session]
6. **P1-11 amendment discriminator dormant** (flag off + empty allowlist; live amendments from flyer-active customers claimed as flyer revisions; on arming, conflict captures are write-only).
7. **catering-p17b PARTIAL**: mint-deposit done (committed 3e07c9b, stacked pre-squash #708 — needs rebase --onto), 4 scripts still status-blind: select-catering-proposal, send-catering-ack, create-catering-proposal-options, amend-catering-lead.

P2 cluster (carried verbatim): duplicate-lead fork vs FINALIZED · clarification no-state/no-cap · dup Option-2 "proposal missing" · dedupe skipped on empty native id + mark-before-handle permanent drop · parse-menu-photo unlocked counter · deposit float/basis (dormant at pct=0) · package itemization · deposit copy label · no owner WhatsApp decline post-SENT · CUSTOMER_FINALIZED no TTL · gate-off strands QUALIFYING · NO §12a sweep for the new *_delivery_status markers.

Test-integrity updated: money-quote retry now really covered; still: PYTEST_CURRENT_TEST self-disables dedupe guard; is_owner_chat/identify stubs everywhere; Windows skips. Baseline correction: the "2 pre-existing pricebook failures" were a missing-git artifact — surface is green; don't chase.

## 4. OPERATOR / HUMAN GATES
Unchanged: Pushover un-mute + fallback · pricebook import + hand-calc · Stage A identities · sequential activation.
Added: sweep arming = dry-run first (line count == first-run alert count) · acceptance-arming checklist: narrow the `number` exclusion ("number of guests" books) [lead: 5 unpinned rows WERE pinned pre-merge in #710]; rate cells shape-specific / cache unfalsifiable; dash-sentence + bare-"go ahead" gaps; **audit row on silent demotion still required** (None writes nothing — dropped acceptance indistinguishable from no reply) · deposit_pct=0 before first ≥50-guest lead unless template confirmed [lead: deposit_pct=0 CONFIRMED live this session].

## 5. STRUCTURALLY UNANSWERABLE FROM REPO — with lead's box answers where obtained
flyer.enabled=True ✓(probed) · owner replies from paired self-chat ✓(owner.self_chat_jid IS the bridge account — probed) · Hermes fromMe logging to agent.log: still unknown · terminal/code_execution disabled ✓(probed /root/.hermes/config.yaml:42) · deposit_pct=0 ✓(probed), template presence unknown · commerce_payment_confirmed needs terminal tool (disabled → dormant).

---

# FLYER STUDIO (drafted by flyer-auditor, 2026-08-14 ~05:12Z; lookup re-verified at d3b877d3)

**VERDICT: FLYER_STUDIO_NOT_READY.**
Re-derived post-session. The routing-precedence and silent-failure classes that dominated the audit are largely closed, but three ready-blocking items remain unfixed in code (brand-asset misclassification, customer-record identity, bare-path fact-safety) and the operator/business gates below are unresolved. The system is materially safer than at session start and is not yet safe to run unattended for a customer who is not being watched.

## What closed this session

- **P0-1 menu-cession precedence — #697.** The terminal brand-asset arm no longer swallows a menu photo before the cession can claim it; both cessions moved onto the same flag that arms capture, closing the kill-switch asymmetry. **#704 (CI)** widens the trigger vocabulary to `updated menu` / `menu updates` / `here is the menu`.
- **P0-3 LID-only session wipe — #696.** A new phone-less session no longer deletes every other phone-less principal's session across customers. **#702** closes the two residuals raised on it: onboarding sessions now converge on the canonical identity key, and the manual-edit finder gained a falsy-arg guard plus phone canonicalization on both sides.
- **P1-5 recovery escalation reachability + P1-6 SLA coverage — #698.** `operator_action_required` is reachable when the worker never reaches a terminal status, and the SLA watchdog monitors all 14 manual-review reasons instead of 2. **F0226 will page roughly 4h after deploy.** The arm is **on by default** (240 min, schema default, passed unconditionally) and the escalation loop is uncapped — burst sized to incidents queued older than 4h (measured 1 on main-vps today).
- **Chronological candidate ranking — #703.** Active-project selection no longer compares ISO timestamps lexicographically.
- **fcntl self-sufficiency — #701**, plus 3 flyer files now collected by `flyer-extended-ci` via #696. CI-inversion residual **partially** addressed: those files run and are self-sufficient; the bulk of `test_flyer_*` is still excluded by `send-path-ci.yml:58`.
- **Manual-review landmines — #705 (CI).** `--manual-reason-code` choices derive from the schema (two codes previously exited 2, queueing nothing), and requeue resets `queued_at`. The reset also repairs the customer-notification throttle: the SLA alert key embeds `queued_at`, so an inherited stamp reused the old row key and suppressed the update for genuinely new manual work.
- **Compliance-gate sibling — #700.**

**In flight, conditional:** PR-D identity memo (P1-7), PR-B truthful failure acks reduced form (P0-4), PR-C quota orphan (P1-10). None merged; treat their findings as open until they are.

## Still open, re-ranked

- **~~P0 — brand-asset capture has no content classifier.~~ CLOSED** on branch `fix/flyer-brand-asset-classification`. The six substrings plus "does an active project exist" are gone: `_flyer_brand_asset_authorized` now asks `reference_extract.classify_reference_role` (which had zero cf-router call sites) for the routing verdict, vetoes the roles other paths own, and requires an explicit brand-artifact noun before mutating the store. An active project is context, never evidence; a classifier failure writes nothing. Gated by `tests/test_flyer_brand_asset_routing.py` in `flyer-premium-ci`. **Residual, operator-owned:** assets captured under the old rule are still `active` in `customers.json` (B0009 was deactivated on 2026-08-11; B0003–B0008 were never re-examined) — the fix is prospective only.
- **P0 — customer-record lookup is anti-monotonic.** `actions.py:5042-5070`, unchanged at d3b877d3. The `primary_chat_id` fallback runs only when no phone resolves, so a customer onboarded under a LID becomes an *unknown sender* the moment the lid-cache **learns** their pairing — improving identity data breaks recognition. Also `len(matches) == 1`, so two customers sharing a number make both invisible. #702 converged onboarding sessions, not customer records.
- **P1 — bare-path fact safety fails open.** `bare_render.py:1148-1156`, `:1176-1180` send anyway on vision/validator/gate error; `:1183-1185` skips broad QA under `FLYER_BARE_SKIP_VISUAL_QA=1`, live and allowlisted for dogfooding. During a vision-provider outage bare-path flyers ship with zero locked-fact verification. Known-open since 2026-06-15.
- **P1 — flag-flip stranding, partially closed.** #697 fixed capture-vs-cession asymmetry. Still open: `flyer.enabled` true→false strands every non-terminal project with no drain, quiesce, or customer notice; reverse flip has no promotion path.
- **P1 — access-reservation leak.** `_reserve_flyer_access_or_reply` released by explicit call at 12 branch sites, no try/finally, no reaper; a crash consumes a paid quota unit permanently. All test sites stub these functions — money path 0% exercised.
- **P1 — no automated payment activation.** `--activate`/`--activate-customer` have zero callers; no webhook route. A paying customer stays `pending_payment`, told "Please complete payment first". No §12b alert at the write site.
- **P1 — reference-scope strands + transient cache path.** Three scope intercepts hard-return on `if not phone` (transient identity failure → un-exitable row); TTL not refreshed on `awaiting_source_vs_new_choice`; expiry silent. Pending row persists the transient Hermes cache path for 30 min — on a miss a generic flyer ships with no asset, no audit, no message.
- **P1 — operator-jargon guard on no outbound seam.** `scan_customer_text` has one call site (`intent.py:301`, LLM-intent validation). Seven send paths bypass all content policy; static AST scan in no CI job.
- **P2 cluster.** Inbound dedupe skipped without native message id + fails open silently; outbound dedupe content-hash-only (two distinct failures collapse into one message *audited as delivered*), media path has no dedupe; `flyer.creative_planner.enabled` dead lever; both flag readers swallow YAML errors into silent global disable; onboarding sessions no TTL; unknown `plan_id` grants unlimited quota via the same None sentinel as the unlimited tier.

## Credential / operator / business gates

- **Both codex and claude OAuth tokens revoked** → recovery stays DETECTION_BUNDLE_ONLY. #698 closed the code half; credentials are operator work; escalation worker also needs `mode` inside `WORKER_RUN_MODES`.
- **Third owner number.** `WHATSAPP_OWNER_JID` is the bridge's own account → owner-role inbound structurally unreachable. Also blocks the PR-A class: the self-chat cannot distinguish agent-sent from owner-typed (same account, same fromMe), and two agent-authored cards are already valid F8 approve commands. Ruling needed: strict anchor + outbound-id ledger, or fix the number. **Recommend the number.**
- **Manual-queue drain rota.** All 14 reasons now page (#698); nobody owns draining; CLI is the only drain.
- **F0226 disposition BEFORE deploy** — it will page ~4h post-deploy; decide resolved/closed/actionable first, else the first page of the new system is a three-week-old ghost.
- Verify on-box at deploy: `flyer.enabled`/`flyer.workflow_enabled`, `source_edit_provider_policy` (absent ⇒ 100% of exact edits to human queue — measured PRESENT today), `FLYER_BARE_SKIP_VISUAL_QA` (=1 today), `FLYER_QA_ALLOW_SIDECAR`.

## Structurally unanswerable from the repo

- Whether the menu misroute ever fired in prod (decisions.log for brand-asset rows whose media was a menu — 30d archive search found ONLY receipt incidents).
- Real identify-sender latency per turn; live `codex.status` distribution.
- Whether locking works in production (every test stubs flock — suite structurally incapable of evidence).
- Whether deployed tree matches merged main (needs box access; verified equal at session start for ee45bd8f).

## Note for whoever picks this up
Windows-local runs of `test_cf_router_flyer_routing.py` and `test_flyer_project_isolation.py` show large failure counts (225 and 9). These are NOT failures — import-order artifact; co-collect with any file calling `ensure_fcntl_stub()` and both go fully green (fixed at HEAD by #701). Do not chase them.

---

# CHANGES LEDGER — all merged this session (each: adversarially verified by an independent agent + 4 CI checks green)

| PR | merge | scope |
|---|---|---|
| #695 | 3646f6b20 | Agent #19 equipment READ tool in shift-agent-read + store/CLI (supersedes #680, closed w/ rationale). ~581 lines salvaged from #680 |
| #696 | 9ca846cbf | Flyer P0: LID-only session wipe (None!=None predicate collapse, 6 sites) + 3 test files into flyer-extended-ci |
| #697 | 117088de0 | Flyer/Catering P0: menu-caption candidate yields before brand capture (the B0009/B0010 class, menu instance); cessions share the capture flag gate; turn-identity hoist (−1 spawn/media turn) |
| #698 | b52190fbe | Flyer P1: worker-unavailable escalation arm (F0226 class pages ≤4h; default-ON, burst measured=1) + SLA watchdog covers all 14 reason codes + state_dirty write hoist |
| #699 | 6e6017fcb | Catering P1: proposal-expiry + lead-TTL sweep systemd units, enabled, provable no-ops until env-armed; 2 vacuous tests replaced |
| #700 | f8e21260f | Compliance: tool honors compliance.enabled (phantom lever); box measured unseeded → wording-only live change; both bind-failure tests made falsifiable |
| #701 | 3720e3394 | Test infra: 3 files fcntl-stub self-sufficient (225 phantom failures → 0; 240s → 12s) |
| #702 | f6568125e | Flyer: onboarding-session canonical-key convergence (write+read+cf-router finder) + manual-edit finder canonicalization + falsy-arg guard; bonus find_session self-row fix |
| #703 | d3b877d35 | Flyer: chronological (not lexicographic) active-project ranking; UTC-offset inversion (~50min) found during build |
| #704 | 690b348f1 | Menu-caption vocabulary widened (updated menu / menu updates / here-is-the-menu end-anchored); 42-caption superset proof; residuals pinned |
| #705 | c767620d5 | Flyer: manual-review reason choices derived from schema (2 drifted codes wrote NO row = exit 2); queued_at reset on requeue (also repairs SLA throttle key + customer-notification suppression) |
| #706 | f2bb8b3ba | Flyer P0-4 (reduced): truthful failure acks ("SEND IT" — the only phrase that works, probed); lint phrase family; finalize/delivery failures stamped into classifier → incidents/pages |
| #707 | 05d1df117 | Flyer P0: customer-record lookup monotonic (learning lid-cache pairing no longer breaks recognition); 91-cell A/B matrix as committed test; tiebreaker preserves shared-number refusal |
| #708 | c59605e58 | Catering P1 money-path: consume bridge status; NO re-POST after send_uncertain; truthful markers + typed row (rollback-verified new-tag choice); owner pages via Pushover not the failed bridge |
| #709 | dfdc06dfb | Flyer P1: quota precheck BEFORE project creation (definite-block early-out only; guest orders structurally exempt); staleness-bound catering admission (slot-filling inbounds recovered) |
| #710 | 279c3f8e1 | Catering P1 (pre-arming gate): acceptance detector — 4 adversarial rounds; guards on full text; linear perf (was 36s@18KB in v2); 61-row pinned genuine table; harness parses the table |
| #711 | b8e7bb8f2 | Flyer: brand-asset content validation (magic bytes JPEG/PNG/WEBP, content-derived MIME+ext, 10MiB cap); suite previously proved arbitrary bytes accepted |
| #712 | 3a08eec88 | Catering P1: escape-gate classify/act split — no contradictory message pairs; act-phase failures = audited terminal skip (best-effort audit wrap) |

Also: #680 CLOSED (superseded by #695).

# BRANCHES READY/NEAR-READY, NOT IN TONIGHT'S CUT
- fix/cf-router-turn-identity-memo @ e0c1661 (PR-D): MERGE_CERTIFIED conditional on FIX-1 (deep-copy payload — in progress). 10→1 identity spawns/turn. MERGE ALONE (hot-path, own advice). Pushed.
- fix/catering-diet-aware-proposals: v3 in progress (negated-flesh-words class: "all vegetarian, no chicken" → mixed → guard REQUIRES meat). v2 (545a67d) verified good on everything else. THE catering gating item.
- fix/arbitration-e2e-utf8: in progress (cp1252 Windows-red on main from #706/#708 copy).
- fix/catering-send-status-remaining (catering-p17b): PARTIAL — mint-deposit done (3e07c9b, stacked pre-squash #708, needs rebase --onto), 4 scripts remain; coder died, unverified.
- deploy-b worktree: ABANDONED uncommitted builder-determinism WIP.

# FOLLOW-UPS FILED THIS SESSION (beyond audit lists)
- clarification sender's own audit_intercepted unguarded (same best-effort wrap as #712's FIX-1) — pre-existing, reviewer-baselined.
- #712's except Exception: pass → add stderr write (reviewer nit; keeps §12a posture when audit store itself is down).
- 4 more create-then-block quota sites (2376/2524/2654/2752 shapes); replay-harness registration at hook boundary.
- "ok send it" conversational wrapper doesn't trigger retry (whole-message alias matching; copy now solicits it).
- test_flyer_audit_remediation_review_fixes.py + test_flyer_project_isolation.py to the fcntl self-sufficiency list.
- equipment _config docstring nit ("compliance_tool inlines" now false); compliance/equipment rollback residue in /usr/local/bin.
- §12a sweep for catering *_delivery_status markers; acceptance silent-demotion audit row (arming checklist).
- STANDING RULES LEARNED: (1) any SKILL.md edit → regenerate tools/skills-manifest.txt same commit (stale manifest REFUSES tarball build); (2) GOV-PR-PROJECT needs every affected project incl. repo-meta for workflow edits; body edits need close+reopen; (3) GOV-SUBSYSTEM-NOEXC keys on ADDED filenames matching rout(er|ing)/store/etc — name test files accordingly; (4) never quote "pre-existing failure" counts without a same-command baseline diff (5 bogus claims this session); (5) identical additions on parallel branches EOF-conflict — expect it.
