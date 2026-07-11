# Phase C Design-Partner READINESS PACKET (Settlement Directive C4) — 2026-07

**Drift-check tag:** Hermes-native (documentation artifact)

## Hermes-first analysis
| Domain | Hermes skill found? | Decision |
|---|---|---|
| readiness/pitch documentation | none found (operator-facing summary of our own deployment) | document in-repo |

**Sources:** memory `project_ppv1_hardening_review_2026_07_02.md` (authoritative), `tasks/settlement-census-2026-07.md` (C2 tranche 1), `docs/portfolio.md`. Deploy baseline: deploy-20260706-004658 (+ #563 quarantine train). Audience: SriniY reviews; the strategy thread decides the actual send.

---

## 1. Demo exhibits

| Exhibit | What it shows | Evidence |
|---|---|---|
| F0209 | Baseline delivery — quoted-APPROVE binding verified live, both branches, same thumb (fail-safe status surface 22:50Z, then binding_source=quoted_message_id 22:56Z → 4 finals) | ladder rows 2026-07-05T22:50/22:56Z |
| F0210 | C1 PASS — crowned register (festive-premium) served live end-to-end: typeset marker TRUE, demoted_typeset telemetry, QA zero blockers, 4 formats correct geometry incl. first live letterbox exercise | ladder rows 2026-07-04T18:31:23Z; PRs #543–#553 |
| F0212 | Organic quoted-approve delivery on native post-#558 mid bundles (delivery #3) | close-out rows 2026-07-06 00:2xZ |
| F0213 | Premium-dark register exhibit — server-side override knobs (#562), chain clean, SriniY eyeball PASS; delivered (bounded incident, content pre-passed) | deploy-20260706-004658 |
| july4-full-demo | July-4th occasion theme at intensity=full: harness render, zero leaks/gibberish, audit SHOWABLE — the pitch-packet hero | aesthetic-r35/2-full.png (R3.5, first-try 8.0) |
| Diwali (full) | PENDING — exhibit #2 staged, awaiting SriniY go/hold tonight | placeholder |

The four-delivery arc (F0209 → F0210 → F0212 → F0213) is the story: baseline approve mechanics → register quality gate passed → organic customer gesture → premium-dark catalog breadth.

## 2. Cost sheet

- **$0.126 per delivered premium project** (measured, midnight-aligned usage_daily).
- **$2.47 for the full 19-sample evaluation funnel** — the cost of proving a register/model change before it ships.
- **99.5% margin at $300/store retainer.** Even the shelved premium model fits: gpt-5.4-image-2 at ~$1.30/flyer is fine inside a retainer, never the free-sample default.
- **Two-tier model strategy (ratified with pre-registered gates):** google/gemini-3.1 is the production default (tie on composite 7.92 vs 7.9, at ~1/13th the cost); gpt-5.4-image-2 shelved as premium-tier candidate at ~13x (zero-regen credential). Incumbent-with-evidence rule: no model churn without a >=1.0 composite win at <=3x cost.

## 3. Capability summary — what a design partner gets day 1

Pipeline (all live, all verified on real inbound WhatsApp briefs):
**brief → extraction v2** (fact-safe, word-boundary parity guard, fail-closed to legacy, audited per brief) **→ crowned register + typeset contract** (festive-premium default; leak-screened vocabulary) **→ 4 QA screens** (fact readback, invented-claim screen with uniform-price each/per bridge, strict typeset screen, numeric backstop) **→ preview → swipe-reply quoted APPROVE** (cross-customer binding structurally impossible; echo guard disambiguates weekly re-sends with NEW/APPROVE) **→ 4 formats** (WhatsApp 1080x1350, IG post 1080x1080, IG story 1080x1920, printable PDF).

- **Occasion detection:** 4 festival themes (July-4th / Diwali / Ramadan / Thanksgiving), fail-neutral — celebration-bait briefs return null and get the base register (proven on F0209/F0210).
- **Sample-prompts menu:** 5 live fires, 0 misfires, dedup verified (census A8).
- **Weekly re-send disambiguation:** identical brief 7 days apart correctly offers NEW vs APPROVE (echo guard, #558).
- **Register catalog:** premium-dark proven live (F0213); clean-modern/festive variants harness-proven, one live exhibit each owed before catalog exposure (census F3 residue). Per-customer selection is server-side config.

## 4. Operational hardening ledger (what changed since 2026-07-02)

| Item | State | Evidence |
|---|---|---|
| Quarantine-before-recovery | #563 built, grad10 APPROVE, merge+deploy train running — failed renders survive their own post-mortem (F0197/F0208 evidence-destruction class closed) | census C4 |
| Allowlist unification | empty=DISABLED uniform across 7 explicit + 4 bare-implicit gates; pre-design-partner blocker cleared | PR #554; census A5 |
| Config-sanity WARN at deploy | deploy-time env/config sanity check (grad8 round) | PR #554 round |
| Raw-body capture | permanent; quote-metadata spec captured; emitter bug closed with post-deploy row-landing probe (house rule adopted) | PRs #555–#557; census A7/F2 |
| Flag-interaction inventory | all 33 FLYER_* flags mapped: precedence DAG, shared-allowlist coupling, runtime-mutated flags | census artifact #1 |
| Real CI gates | flyer-premium-ci first GREEN runs observed 2026-07-05T22:33/22:40Z (phantom-gate §12a lesson closed); send-path catering flake FIXED+MERGED tonight (#560 — root cause: test calendar time-bomb, hardcoded event_date crossed midnight ET; production behavior was correct throughout); send-path gate signal restored | census F1/C3; PR #560 |
| Silent-close tooling | #561 --no-notify close edge merged+deployed; 5 held baselines closed silently, store fully settled (zero non-terminal) | deploy-20260706-002359 |

## 5. Open-risk list (honest)

1. **P1 — bare APPROVE surfaces status:** live trigger upstream unidentified; quoted APPROVE unaffected (verified 2x). Investigation queued, ISOLATED-STORE harness only.
2. **Single shared WhatsApp line + outbound cap 100/day** shared across all agents — a second customer shares both until BSP.
3. **Hermes pinned 0.14; BSP / WhatsApp Business-API migration pending.** The 2–4 week Meta paperwork clock is the go-to-market critical path — longest lead item, already on SriniY's checklist.
4. **Legacy backlog: 35 pre-fix awaiting_final_approval projects** (F0149 cohort, all legacy-era) — staleness-check + staggered release blocked on pilot-owner confirmation they're still wanted.
5. **Per-item price column class:** fixed (#559, live-verified pending next register brief) — but the model-improvisation risk *class* remains; numeric QA backstop in place (twice-by-design passes, 3+ blocks).
6. **Intensity/register selection is server-side only** — no customer-facing selector; no brief-cued mapping exists (ledger). "Which vibe?" one-tap is a banked product idea, not shipped.
7. **Catering-adjacent time-bombs due ~Aug 15** (PR #560's report: tests/test_catering_b1_cases.py (literals 2026-08-30..12-14) + tests/test_catering_expiry_stale_codes.py (2026-08-15/16) drive the real-clock date validator; first detonation ~2026-08-15). Catering-owned; shares the line and the CI signal.

## 6. Onboarding runbook skeleton (new design partner)

**HARD PRECONDITIONS before ANY allowlist expansion (operator ruling 2026-07-06, from the F0213 §5-breach near-miss):**
1. Dispatch/routing replays run against an ISOLATED store copy only (`FLYER_PROJECTS_PATH` -> tmp) — never the live store.
2. Harness stub nets are ALLOWLIST-SHAPED: stub every boundary by default, permit reads explicitly. Blocklist stubbing is prohibited.
Rationale: the F0213 incident (2026-07-06 01:04Z, unapproved delivery, EXECUTED-WITHOUT-RECORDED-APPROVAL) was a §5 hard-gate breach contained ONLY by pilot allowlist scope. With a design partner allowlisted, the same harness gap would have been customer-facing.

1. **Number allowlisting — NO LONGER an onboarding step for validated features (graduation wildcard, added 2026-07-11 after incident F0217).** Once the box's validated FLYER_* lists are set to the literal wildcard `*` (e.g. `FLYER_PREMIUM_OVERLAY_ALLOWLIST=*`), every onboarded customer gets the validated stack automatically — no per-customer env edit. The F0217 root cause was exactly this gap: a second onboarded customer (CUST0007) silently ran the raw, unprotected pipeline because nothing graduated validated features to all customers. `*` is explicit-allow and composes with numbers (`*,+1732…` stays global); empty/unset still = DISABLED (fail-closed) — the `*` is never the empty-list flip. **Keep per-number FLYER_* lists ONLY for scoped rollout of a NEW, not-yet-validated feature** (the correct original use), still on /root/.hermes/.env (edit the symlink TARGET, never sed the symlink; incident protocol: backup, one-line assert, restart, /proc verify). **Call-out: FLYER_PREMIUM_OVERLAY_ALLOWLIST is SHARED by FOUR gates (overlay / det-recovery / det-first / CD-v2) — one env line (incl. `*`) in-scopes four features at once.** All allowlists are explicit-allow, empty=off.
2. **Profile creation** — business identity block (name, address, phone); footer hydrates from profile by design when briefs omit contact facts.
3. **Register + occasion config** — server-side per-customer register choice (default festive-premium; premium-dark proven; others need one live exhibit first) + intensity (accent default, full on request); occasion detection is on and fail-neutral.
4. **First-brief handhold** — walk the owner through a proven brief shape (price statement + "Include" + item list; avoid "like"/bare colons); offer the sample-prompts menu as the self-serve path.
5. **Approval UX one-pager** — the gesture is swipe-reply + APPROVE on the preview message; re-sending the same brief next week gets a NEW/APPROVE disambiguation prompt; four formats arrive on approve.

---

*C4 deliverable. Objection/decision path: SriniY review → strategy thread owns the actual design-partner send (Mana Ruchulu vs Chopathi still open, gated on allowlist expansion + BSP paperwork).*

### Exhibit status (2026-07-06 02:0x)
- Exhibit #1 premium-dark (F0213) + Exhibit #2 Diwali-full (F0214): **audit-passed, operator-eyeball PENDING — not banked.** Quarantined at pitch-packet/exhibits/ with PENDING markers in the filenames; one SriniY verdict message banks both (Monday NEEDS-SRINIY list, ~30 seconds).
