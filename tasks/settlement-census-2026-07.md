# Settlement Census — Phase B (C2 deliverable, in progress)

**Drift-check tag:** Hermes-native (audit artifact — no new primitives; documents deployed state)

## Hermes-first analysis
| Domain | Hermes skill found? | Decision |
|---|---|---|
| env-flag inventory / audit | none found (introspection of our own deployment) | document in-repo |

Census artifact #1 below was produced by the flag-inventory agent at origin/main 34b84ca
(2026-07-04) and is the authoritative flag map for C2 verdicts. Live values appended from
/root/.hermes/.env as read 2026-07-04 ~18:3xZ.

## Live values (box, 2026-07-04)
FLYER_PREMIUM_REPAIR=0 (allowlist set but flag OFF) · FLYER_PREMIUM_OVERLAY=1 ·
FLYER_DETERMINISTIC_RECOVERY=1 · FLYER_DETERMINISTIC_FIRST=1 · FLYER_STYLE_REGISTERS=1 ·
ALL allowlists = +17329837841 (CD/scene/iteration/visible-contract/repair/overlay/ppv1/registers).
(FLYER_PREMIUM_POSTER_V1 / EXTRACTION_V2 / intent-mode values: re-read at C2 assembly.)

---

## Artifact #1: Flag-interaction inventory (agent report, verbatim)

(See session record; full text in memory file project_ppv1_hardening_review_2026_07_02.md addendum + below.)

---

# TRANCHE 1 (C2, assembled 2026-07-05 night — same-day objection window per weekend directive)

Live values re-read 2026-07-05 ~23:2xZ from /root/.hermes/.env: FLYER_PREMIUM_POSTER_V1=1 (allowlist +17329837841, N=1) ·
FLYER_EXTRACTION_V2=1 · FLYER_HERMES_INTENT_MODE=active · FLYER_HERMES_INTENT_CLASSIFIER=active · FLYER_CREATIVE_DIRECTOR_V2=1 ·
FLYER_CREATIVE_DIRECTOR_ENABLED=0. All file:line pointers verified against origin/main c50500a (post-#559) / dc05b40.

## A. PROVEN

| # | Item | Verdict | Evidence | Rationale |
|---|---|---|---|---|
| A1 | Style registers + typeset contract | PROVEN | PRs #543–#548 + #553; F0210 ladder rows 2026-07-04T18:31:23Z (typeset marker TRUE, integrated_passed, QA zero blockers, 4 formats on disk) | C1 pass — crowned register served live end-to-end after ROUTING/SCOPE/ENVIRONMENT fixes. |
| A2 | Occasion field | PROVEN | PR #547; fail-neutral defaults held on F0209/F0210 (no false festival theming) | Project-level enum, parity-exempt by ruling; structurally outside the fact contract. |
| A3 | QA hardening screens + uniform-price each/per bridge | PROVEN | PR #545/#552; F0208 ladder death 2026-07-04T17:5xZ ("invented offer qualifier: each") = labeled failure; F0210 QA pass post-bridge | Screens now target invented claims, not typeset grammar around locked values. |
| A4 | Composer demotion + demoted_typeset telemetry | PROVEN | PR #550/#551; F0210 demoted_typeset row 2026-07-04T18:2xZ | Register briefs preview via integrated; ppv1 stays primary for non-register projects. |
| A5 | Allowlist unification | PROVEN | PR #554 (seven-gate three-direction pin); §9a box check: all allowlists non-empty → behavior-neutral | empty=DISABLED uniform across 7 explicit + 4 bare-implicit gates; pre-design-partner blocker cleared. |
| A6 | Quoted-APPROVE binding + echo guard | PROVEN | PR #558; live rows 2026-07-05T22:50Z (unrecorded-mid fail-safe → status surface) + 22:56Z (binding_source=quoted_message_id → F0209 delivered) | Both branches verified live by the same thumb; cross-customer binding structurally impossible (own-mids allowlist). |
| A7 | Raw-body capture | PROVEN | PR #555/#556/#557; spec row 2026-07-05T18:56:21Z (hasQuotedMessage/quotedMessageId/quotedParticipant) | Capture permanent; #556 emitter bug closed with post-deploy row-landing probe. |
| A8 | Sample-prompts menu | PROVEN | decisions.log: 5 cf_router_intercepted reason=flyer_sample_prompt_requested (06-04T12:08, 12:13 deduped, 06-07T16:37, 16:46, 06-08T18:32; the 8 grep hits = 5 fires + 3 shadow intent rows), all rc=0 + ack delivered | 0 misfires; dedup fired correctly once; single shadow-classifier disagreement (advisory new_flyer@0.8, 06-07T16:37) is advisory-only — intercept requires explicit menu-request text. |
| A9 | Extraction v2 | PROVEN | extraction_v2_used=10 vs 1 audited fallback (HTTP 402, 2026-07-03T23:50:00Z); F0203/F0209/F0210 items_locked clean | Fail-closed to legacy held under the only transport failure; zero unaudited degradations. |

## B. DELETED (executed — record)

| # | Item | Verdict | Evidence | Rationale |
|---|---|---|---|---|
| B1 | Narrative referee | DELETED | PR #546 (−638 LOC) | Audition record corrected: redundant-under-CCA (firewall owns safety, composer owns priority). |
| B2 | Creative planner | DELETED | PR #548 (−238 LOC + dead branches) | Inert by construction (no config categories ever shipped); flat-alias coupling lesson recorded. |
| B3 | 8 oracle fixtures | DELETED | Graduation train #543–#548 | Retired with their consumer; rollback-safe deletion conditional pattern applied. |

## C. FIX docket (ordered; owner = build session unless noted)

| # | Item | Verdict | Evidence | Rationale |
|---|---|---|---|---|
| C1 | Per-item price column on uniform-price posters | FIX-MERGED | PR #559 (merged 2026-07-05T23:17Z); F0210 exhibit (4× "$10.99" beside medallion) | Counted prompt discipline + numeric QA backstop; deploy + next register-brief verify pending. |
| C2 | Medallion crowds headline | FIX-MERGED | PR #559 (same) | Clearance rule shipped; eyeball on next live register render. |
| C3 | Catering flake test_create_lead_idempotent_replay | FIX (P1 — ESCALATED, catering-owned) | send-path-ci runs 28756624955 / 28757180523 / 28757346840 (2026-07-05T22:12/22:33/22:47Z) ALL failed, IndexError, tests/test_catering_v02_scripts.py | No longer alternating — hard-red 3/3 tonight; degrades every merge train's CI signal. |
| C4 | Quarantine-before-recovery | FIX (P1) | F0197 (premium artifact + qa.json overwritten by recovery, 2026-07-02) + F0208 (register render unverifiable, 2026-07-04T17:5xZ) | Recovery must move, not overwrite, the failed rung's artifacts; operator already pulled forward. |
| C5 | Overlay prompt-bias flag-only read | FIX (P2) | render.py:2382 `os.environ.get("FLYER_PREMIUM_OVERLAY") == "1"` — no allowlist check | Prompt-bias only, but the last allowlist-bypassing read post-#554; pre-design-partner hygiene. |
| C6 | FLYER_BARE_ITERATION loose truthy parse | FIX (P2) | bare_render.py:180 (not-in-_FALSE_VALUES vs house `== "1"`; same form on REVISION_APPLY :177) | "2"/"on"/"enabled" arm it; align to house parse. |
| C7 | Premium-director phantom occasion fact-id | FIX (P2) | premium_poster_v1_director.py:75 ("occasion" in _DIRECTION_FACT_IDS; no locked-fact producer exists) | Silently resolves to ""; either read project.occasion or drop the id. |
| C8 | Extraction-seam eager import | FIX (P2) | create-flyer-project:22-24 (module-level import; bare_render already lazy) | Queued lazy-import hardening — the deploy-skew crash class. |
| C9 | Tripled _read_key_from_env_file | FIX (P3) | reference_extract.py:159 + semantic_brief.py:241 + visual_qa.py:1634 (near-twin scripts/check-flyer-reference-scope:50) | Dedup to one platform helper. |
| C10 | Dead helpers in facts.py | DELETE (P3) | facts.py:866 dead assignment; _requested_item_count_and_phrase :680 / _max_item_index :753 / _distinct_grounded_item_count :764 — zero src call sites (test-pinned only) | Remove with their test pins (tests/test_flyer_item_index_reconciliation.py). |
| C11 | Stale assert message | FIX (P3) | tests/test_flyer_facts.py:968-969 — message names removed creative_planner | Reword to current emitter set; same PR as C10. |
| C12 | grad9 LOWs (expired-row purge / empty-original audit skip / flags-off pop / ok-as-approve) | FIX (P3) | PR #558 review thread (no tasks/ doc row exists; flags-off-pop class also arch-review doc :92 CQ-8) | Batch as one hygiene PR. |
| C13 | Order-interference test failures (58 @ 2026-07-02) | FIX (P3) | Session record 2026-07-02 + tasks/flyer-premium-poster-v1-architecture-review-2026-07-02.md:92 (CQ-8: os.environ.pop without restore); repro = single-process pytest | Suite green only under process isolation; env-restore sweep; fresh count in tranche 2. |

## D. DELETE candidates (PROPOSED — same-day objection window; nothing executed)

Flag dispositions (all 22 undocumented code-read FLYER_* flags enumerated; pointers = origin/main read site):

| # | Flag(s) | Verdict | Evidence | Rationale |
|---|---|---|---|---|
| D1 | FLYER_EXTRACTION_V2 | DOCUMENT (urgent) | extraction_seam.py:26; LIVE=1 | The production extraction flag is undocumented — runbook gap. |
| D2 | FLYER_STYLE_REGISTERS(+_ALLOWLIST) | DOCUMENT | style_registers.py:197/200; LIVE=1 | Live crowned-register gate, undocumented. |
| D3 | FLYER_VISIBLE_CONTRACT(+_ALLOWLIST), FLYER_SKILL_DRIVEN_SCENE_ALLOWLIST, FLYER_BARE_ITERATION(+_ALLOWLIST), FLYER_BARE_REVISION_APPLY, FLYER_CREATIVE_DIRECTOR_ALLOWLIST | DOCUMENT | bare_render.py:991/992, :236, :180/:253, :177, :193; all set live | Live gates/companions of documented flags; document beside their parents. |
| D4 | FLYER_DISABLE_BRAND_ASSETS, FLYER_HERMES_INTENT_CLASSIFIER_TIMEOUT_MS, FLYER_REFERENCE_SCOPE_ALLOW_SPEND | DOCUMENT | render.py:1894 (runtime-mutated — /proc caveat), cf-router/actions.py:837, :4602 | Operational knobs; DISABLE_BRAND_ASSETS needs the runtime-mutation runbook note. |
| D5 | FLYER_BARE_BG_DIR/_SESSION_DIR/_REROLL_MAX_AGE_HOURS/_REVISION_CAPTURE_RAW_BG, FLYER_DECISIONS_LOG, FLYER_REFERENCE_ALLOW_SIDECAR | DOCUMENT (test-only, one line each) | bare_render.py:175/:174/:535/:181/:187, reference_extract.py:543 | Dev/test hooks; keep, label test-only. |
| D6 | FLYER_REFERENCE_VISION_MODEL, FLYER_SEMANTIC_BRIEF_MODEL, FLYER_CREATIVE_DIRECTOR_MODEL | DOCUMENT (conditional) | reference_extract.py:26, semantic_brief.py:66, flyer_context_builder.py:68 | Model overrides; SEMANTIC_BRIEF_MODEL deletes with D9 at WS3; CREATIVE_DIRECTOR_MODEL deletes with D10 if descoped. |
| D7 | FLYER_PARITY_MODE + FLYER_PARITY_TEST_CHAT_ID | DELETE (env lines, operator .env edit) | Live on /root/.hermes/.env (=operator_ab, =918522041562@s.whatsapp.net) but ZERO readers anywhere on origin/main (git grep empty, src+docs) | Dead config naming a foreign chat id; delete both lines under incident protocol. |
| D8 | Zombie config names: FLYER_ART_DIRECTOR_ORACLE, FLYER_INTENT_ROUTING_SHADOW, FLYER_PREMIUM_POSTER_V1_PATHS | ANNOTATE-AS-NEVER-WIRED (docs PR) | Zero src reads; mentions: docs/superpowers/specs/2026-06-20-flyer-creative-director-v2-design.md:170; tasks/flyer-marketing-agent-slice1-plan.md:16; arch-review doc :150/:213 | Historical spec text masquerading as live config; one-line zombie annotations, keep the docs. |
| D9 | semantic_brief vs extraction v2 | KEEP-UNTIL-WS3 | facts.py:803 call site; semantic_brief.py emits NO marker (silent seam — no grep-able fire evidence exists); legacy path ran once since v2 activation (audited 402 fallback 2026-07-03T23:50Z) + revision path (v2 owns new-brief seam only) | Redundant for new briefs, load-bearing for fallback/revision; delete with legacy-extraction retirement, not before. |
| D10 | CD-v2 (staged creative-director layer) | PROVE-OR-DESCOPE → recommend DESCOPE via sequenced off-flip | Gate render.py:3807 (flag=1 live + overlay allowlist); emitter render.py:4657 writes only in-memory project.creative_direction (exclude=True, :3337); ZERO decisions.log rows ever, zero sidecar trace on box (poster_archetype grep empty in state/) | Live-in-path (render.py:1471 hero-dish prompt bias) yet unobservable by construction — "last live fire" is unknowable, which is itself the verdict evidence; fails complexity budget. Sequence: 1-row telemetry → 48h observe → flag off if no register-visible delta → delete at WS3. |
| D11 | Guided intake | KEEP-DORMANT | intake.py:77 entry (sole caller scripts/handle-flyer-intake:52); box: 0 intake sessions in store, 537 flyer_intake_bypassed rows; intake.py emits NO audit rows (no fire marker exists) | Never fired live — but it is the designated mechanism for the standing bright-line rule (vagueness resolves by ASKING); deletion orphans the rule. Add fire telemetry when productized; roadmap write-up correction owed (arch confirmation #4). |

## E. HELD BASELINES (disposition per project; store read 2026-07-05: F0204–F0208 ALL preview mids=0)

Shared mechanics: with zero recorded mids, quoted-APPROVE structurally CANNOT bind any held project (fail-safe → status surface only, proven at 22:50Z); a stray bare APPROVE in the pilot chat binds the newest-updated awaiting project = **F0207** until these close. Expire path = PR #541 operator-close edge (`closed_no_send`, reason_code=operator_request, run as sudo -u shift-agent) — emits a TEXT-ONLY customer notification row, no asset attach, so it cannot look like a new flyer (precedent: F0200 close, 2026-07-03 ~19:36Z; verify exact copy from that row before firing).

| # | Project | Disposition | Evidence | Customer-visible message |
|---|---|---|---|---|
| E1 | F0204 Sunday Brunch | EXPIRE-WITH-NOTICE (close edge) | awaiting_final_approval, mids=0, created 2026-07-04T17:02Z; baseline-era composer preview | Text-only close notice; offer re-send of brief post-#559 for a register render. |
| E2 | F0205 Saturday Brunch | EXPIRE-WITH-NOTICE (close edge) | same class, 17:25Z | Same. |
| E3 | F0206 Evening Snacks | EXPIRE-WITH-NOTICE (close edge) | same class, 17:34Z | Same. |
| E4 | F0207 Festival Sweets | EXPIRE-WITH-NOTICE (close edge) — CLOSE FIRST | same class, 17:37Z; = newest-updated awaiting → stray-bare-APPROVE target | Same. |
| E5 | F0208 (each/per casualty) | CLOSE (close edge) | manual_edit_required (APPROVE not actionable), 17:48Z; superseded by #552; register render destroyed by recovery (exhibit for C4) | Text-only close notice. |
| E6 | F0211 (delivered duplicate) | RECORD-ONLY | delivered 2026-07-05T02:42Z (swipe-probe misroute, F0200 class reproduced under supervision) | None — already delivered, internally consistent; no recall. |

## F. Ops / infra

| # | Item | Verdict | Evidence | Rationale |
|---|---|---|---|---|
| F1 | Phantom flyer-premium CI gate | FIXED-PROVEN | Invalid YAML since PR #530; fixed in #558 round; FIRST GREEN RUNS observed 2026-07-05T22:33:39Z + 22:40:11Z (gh run list flyer-premium-ci) | §12a lesson: a gate isn't born until its first real run is observed; accidental mitigation was send-path-ci's glob. |
| F2 | Best-effort emitters need row-landing smoke | DISPOSITION: house rule ADOPTED | #556 fix train (house path symbol + CI workflow list + post-deploy row-landing probe; probe row 18:56:21Z) | stderr-only failure is invisible by construction; sweep of remaining best-effort emitters → tranche 2. |
| F3 | Live-env parity retro-check (past hand-set-env go/no-go conclusions) | DISPOSITION: mostly MOOT via live re-proof; two residues | Leg-1 verifier calibration → superseded live (F0197→F0210 chain + WS2 contract change); Leg-2 A/B → live-proven (F0201/F0203); model A/B → live-proven (F0203+ on gemini-3.1); WS2 exhibit → live-proven (F0203 premium final_pass); F0209 offline GO probes = the original violation, closed by #553 + C1 | RESIDUE NEEDING RE-VERIFY: (a) non-default registers (premium-dark, clean-modern, festive variants) + intensity dial "full" have harness-only evidence — one live exhibit each before catalog exposure; (b) PR #559 fix needs its first live register brief. |
| F4 | Single-mid legacy bundles (F0204–F0207) | CHECKED — WORSE: mids=0 | Box store read 2026-07-05 (all five held projects mids=0; F0209 was backfilled to 3) | Quoted-APPROVE can never match them (fail-safe only) — folded into E close-first dispositions; post-#558 sends record full bundles; F0209-style backfill exists if a deliver path were ever chosen. |

## Tranche 2 (pending — what evidence is missing)

1. Best-effort emitter sweep — needs emitter inventory + per-emitter row-landing check (F2 follow-through).
2. Fresh order-interference count on origin/main — needs one single-process pytest run (~8 min); 58 is the 2026-07-02 number.
3. Stale open PRs #513/#514/#516/#537/#538 (June-era flyer branches) — need per-PR overlap check vs post-graduation main before close/rebase verdicts.
4. flyer_source_edit_sla_alert fatigue debt (~6k rows since 05-30; 14 stuck source-edit projects) — queued after B4 per operator; needs stuck-project triage.
5. PDF rasterize-before-QA (WS5) + F0201/F0203 printable_pdf backfill — blocked on WS5 fix.
6. PR #559 live verification — deploy tag + one register-class brief.
7. 35 legacy-era awaiting_final_approval backlog (F0149 cohort) — staleness-check + staggered release; blocked on pilot-owner confirmation the projects are still wanted.
8. D7/D8/D10/D11 executions — operator go after objection window (env-line deletion, zombie annotations, CD-v2 telemetry-then-flip, intake roadmap correction).
