# Hermes Creative Director Loop — Planning Doc (PLANNING ONLY)

**Drift-check tag:** `extends-Hermes` — consolidates existing per-stage flyer referees/repair rungs into one CD-aware loop controller + a persisted design contract + a scored quality rubric. Reuses the deployed render/visual-QA/firewall/oracle modules; adds no new storage engine, no new approval-code generator, no dispatcher change.

> ⚠️ **STATUS: PLANNING ONLY. NO IMPLEMENTATION.** No code, no production config changes, no Flyer Studio architecture changes in code, no Hermes 0.17 work, no WhatsApp Business Cloud migration, no community skills. Each slice below is gated on explicit operator approval before any implementation. This doc is for review.

## Hermes-first analysis (capability check)

| Loop step | Hermes / existing-substrate capability? | Tag |
|---|---|---|
| Plan a design contract from intake | CD v2 brief resolution + CCA (`flyer_creative_resolver`, `flyer_copy_archetypes`) already build grounded creative direction | `[reuse]` |
| Generate the flyer image | `render._render_model` (integrated / deterministic overlay / source-edit) via OpenRouter image model | `[reuse]` |
| Factual evaluation (fail-closed) | `visual_qa.run_visual_qa` + `visible_contract` + `flyer_brief_validator` firewall | `[reuse]` |
| Subjective quality scoring | `flyer_art_director_oracle` (8-axis vision rubric) exists **dev-only, ungated** — promote to a scored evaluator | `[extend]` |
| Repair / retry | premium-repair loop + deterministic-recovery rung + Hermes-plan autorepair already exist (disparate) | `[reuse + consolidate]` |
| Persist loop state / traces | `projects.json` (FlyerProject) + `decisions.log` flyer_* LogEntry types + qa/oracle sidecars | `[reuse]` |
| Customer-feedback regenerate | revision routing (`update-flyer-project`, `workflow.apply_revision_text_edit_to_value`) exists for TEXT facts only | `[extend]` |
| Operator alert / escalation | recovery watchdog + `shift-agent-notify-owner` (Telegram) | `[reuse]` |

**Verdict:** the loop is mostly *consolidation + a quality-rubric extension*, not greenfield. The only genuinely net-new behavior is (a) a unified loop controller with one retry budget + trace, (b) a scored quality gate (promoting the oracle + adding deterministic checks for the unhandled failed-case classes), and (c) a structured customer-feedback→creative-direction regenerate path. No Hermes capability is missing; nothing here is built from scratch where substrate exists.

## Drift-rule self-checks (read deployed code before drafting)

- ✅ Read `docs/superpowers/specs/2026-06-14-flyer-architecture-A-slice1-design.md` (Architecture A baseline) before describing the current pipeline.
- ✅ Read `docs/superpowers/specs/2026-06-20-flyer-creative-director-v2-design.md` + `docs/superpowers/plans/2026-06-21-flyer-creative-director-v2-slice-b.md` (CD v2 + narrative referee).
- ✅ Read `src/agents/flyer/scripts/generate-flyer-concepts` (the procedural ladder, lines 762-1660) — the loop formalizes this, not replaces it.
- ✅ Read `src/agents/flyer/visual_qa.py` (`run_visual_qa`, `classify_qa_severity`, `_fabricated_offer_price_blockers`) — the fail-closed factual referees the loop must preserve.
- ✅ Read `src/agents/flyer/flyer_art_director_oracle.py` (8-axis `AXES`, `score_art_direction`, dev-only) — the rubric to promote.
- ✅ Read `src/agents/flyer/recovery.py` + `scripts/flyer-recovery-watchdog` (trust-risk hard-stops, `classify_flyer_qa_for_autorepair`) — the repair/escalation substrate.
- ✅ Read `src/platform/schemas.py` (`FlyerProject`, `FlyerVisualQAReport`, `FlyerManualReview`, flyer_* `LogEntry` union) before proposing any state/trace shape.

---

## 1. Problem statement

Flyer Studio produces customer-facing marketing flyers. Today the generation pipeline is a **procedural ladder** in `generate-flyer-concepts` with several **independently-gated** referees and repair rungs (visual QA, premium image-to-image repair, deterministic recovery, Hermes-plan autorepair, recovery watchdog, visible-contract, a dev-only art-director oracle). This works for **factual correctness** (facts present, no fabrication) but has structural gaps:

1. **No subjective quality gate.** Factually-correct flyers still ship with an "AI-generated look," low-contrast/washed-out/creamy palettes, and weak professional polish. The only quality scorer (the 8-axis art-director oracle) is dev-only and wired to nothing.
2. **Several named failure classes are unhandled** (grounding examples for the rubric):
   - AI-generated look / poor professional polish — *not handled*.
   - Unwanted location text ("South Riding" / "Northern Virginia") leaking into the flyer — *not handled* (the wrong-brand check is conservative identity-only, not geographic).
   - Low-contrast / overly creamy / washed-out designs — *not handled* (no colorimetric check).
   - Wrong QR attached to the wrong channel / QR preservation on regen — *not handled* (no QR content type; structurally guarded only).
   - Missing/dropped prices — *mostly handled* now (item-price-pair checks + the F0190 CCA `shared_price` fix); residual gaps where a shared price isn't marked `required`.
   - Fabricated offers/schedules — **handled** (pre-render firewall + post-render `_fabricated_offer_price_blockers`); **must not be loosened**.
3. **The repair/retry rungs are disjoint** — each has its own gate/flag and its own attempt ledger; there is no single retry budget, no single per-flyer trace of "contract → generation → evaluation → repair → outcome."
4. **Customer feedback does not drive creative regeneration.** A revision mutates **text facts** only; a customer saying "make it more festive / less creamy / bigger price" has nowhere structured to go (and the 2026-06-02 revision-routing identity bug can misroute it).
5. **Traces are scattered** across `projects.json`, qa sidecars, oracle sidecars, `autorepair_attempts.json`, and `decisions.log`, so reconstructing *why* a flyer failed is possible but fragmented.

**Goal of the loop:** one CD-aware iteration loop — *plan → generate → evaluate (factual + quality) → repair/regenerate → approve* — that (a) adds a measured quality gate for the unhandled classes, (b) consolidates the existing repair rungs under one budget + trace, and (c) lets customer feedback drive structured regeneration — **without loosening any fabrication/locked-fact safety**.

## 2. Current Architecture A baseline (what exists today)

```
customer (WhatsApp) → cf-router → create/update-flyer-project
  → intake.py (guided) / starter_briefs → locked_facts (FlyerLockedFact: source, required, confidence)
  → [CD v2, flag-gated] flyer_creative_resolver.resolve_creative_direction
        ↳ CCA (flyer_copy_archetypes) grounded headline + scrub_campaign_narrative + narrative_quality referee
  → render._render_model (one of):
        • integrated poster  (gemini-3.1-flash-image-preview; text+image in one)   [_integrated_poster_eligible]
        • deterministic overlay (textless bg + Pillow text from facts; Template A)  [_background_only_eligible / FLYER_DETERMINISTIC_FIRST]
        • source-edit (image-to-image of a reference flyer)                          [render_source_edit_preview]
  → visual_qa.run_visual_qa (OCR + deterministic blocker taxonomy) → FlyerVisualQAReport
  → classify_qa_severity → pass | warn | block
        • block → repair ladder: premium image-to-image repair (×2) → deterministic recovery rung
                  → Hermes-plan autorepair (classify_flyer_qa_for_autorepair; trust-risk = hard stop)
                  → manual_edit_required
        • warn  → delivered_with_warning (FlyerWarningSummary)
        • pass  → awaiting_final_approval → customer preview
  → customer APPROVE → finalize-flyer-assets → completed
  → customer change → update-flyer-project (text-fact mutation) → pending_revision_confirmation → regenerate
  async: flyer-recovery-watchdog (stale manual_edit, escalate operator_action_required, customer-ack)
```

**Key modules:** `render.py` (`_render_model`, eligibility gates), `visual_qa.py` (`run_visual_qa`, severity, fabrication blockers), `generate-flyer-concepts` (the ladder), `flyer_creative_resolver.py` + `flyer_copy_archetypes.py` + `flyer_brief_validator.py` (CD v2 narrative + firewall), `recovery.py` + `flyer-recovery-watchdog` (repair/escalation), `flyer_art_director_oracle.py` (dev-only 8-axis rubric), `visible_contract.py` (post-render visible checks). State: `projects.json` (`FlyerProject`), qa/oracle sidecars, `autorepair_attempts.json`, `recovery_incidents.json`, `decisions.log`.

**Quality baseline (measured):** `docs/superpowers/baselines/flyer-acceptance-baseline-20260615.md` — 87.6% first-draft acceptance (113/129), 0.53 avg revisions/approved. This is the OUTCOME metric the loop must not regress and ideally improves.

## 3. Proposed loop design

A **Creative Director Loop controller** that formalizes the existing ladder into four explicit roles operating over a **persisted design contract** and a **persisted loop trace**, with one retry budget and one set of stop gates. It WRAPS the existing modules; it does not replace `render` or `visual_qa`.

```
            ┌─────────────────────────── CD Loop controller ───────────────────────────┐
            │                                                                            │
 intake ──▶ │  PLANNER ─▶ design_contract ─▶ GENERATOR ─▶ artifact ─▶ EVALUATOR          │
            │     ▲                                                      │ (factual gate  │
            │     │                                                      │  + quality      │
            │     │                                                      │  rubric)        │
            │     │                                              pass ◀──┤                 │
            │     │                                                      │ fail            │
            │     │                              REPAIR ◀─ repairable ───┤                 │
            │     │                                 │                    │ trust-risk /    │
            │     └──── re-plan (customer feedback) ─┘                   │ budget-exhausted│
            │                                                            ▼                 │
            │                                                     STOP / MANUAL gate       │
            └────────────────────────────────────────────────────────────────────────────┘
                       every transition appended to the per-project loop trace
```

- **One retry budget** across the whole loop (e.g. ≤ N generations + ≤ M repairs), replacing the disjoint per-rung budgets.
- **Two-tier evaluation:** (1) the existing **factual referees** remain a hard fail-closed gate (block → never auto-ship); (2) a new **quality rubric** produces a score that gates softly (warn/manual below threshold) — measured in shadow before it gates.
- **Trust-risk hard stops** (fabrication / unverified phone / wrong-brand / location-leak) bypass repair and go straight to manual — preserving locked-fact safety.
- **Customer feedback re-enters at the PLANNER** (re-plan the contract), not as a raw text-fact patch.

## 4. Agent roles

| Role | Responsibility | Built from (reuse) | Net-new |
|---|---|---|---|
| **Planner** | Turn intake + locked facts (+ customer feedback on re-plan) into a **design contract**: required visible facts, creative direction (hero/hook/narrative/palette intent), channel→QR mapping, and acceptance criteria (rubric thresholds). | CD v2 `resolve_creative_direction`, CCA, `flyer_brief_validator` firewall | the explicit persisted contract + acceptance criteria; feedback→creative-direction mapping |
| **Generator** | Render the artifact satisfying the contract (integrated / deterministic overlay / source-edit). | `render._render_model` | records generation params into the trace |
| **Evaluator (Referee)** | Tier 1 **factual** (fail-closed): `visual_qa` + `visible_contract` + fabrication firewall — UNCHANGED. Tier 2 **quality rubric** (scored): promote `flyer_art_director_oracle` + add deterministic checks for the unhandled classes. Emits a `FlyerLoopEvaluation` (verdict + per-axis scores + blockers). | `visual_qa`, `visible_contract`, `flyer_art_director_oracle` | the rubric extension + the combined verdict |
| **Repair** | Bounded fix of a *repairable* defect within budget: targeted image-to-image text repair, deterministic-overlay recovery, or Hermes-plan repair. Trust-risk classes are NOT repairable. | premium-repair loop, deterministic-recovery rung, `recovery.classify_flyer_qa_for_autorepair`/`plan_flyer_autorepair` | one budget + trace; routes by evaluator verdict |

All roles are **deterministic Python orchestration** calling existing skills/renderers; the only LLM calls are the (existing) image model, the (existing) vision-QA model, and the (existing/extended) vision rubric. Hermes remains the brain for generation + vision; Python is the referee + controller (mirrors the project's firewall convention).

## 5. State files / artifacts

Reuse `FlyerProject` in `projects.json`; consolidate the scattered sidecars into one **loop trace** per project (additive — dormant until the loop runs).

- **`FlyerDesignContract`** (new, persisted on the project, dormant): `contract_id`, `project_id`, `required_visible_facts: [fact_id]`, `creative_direction` (hero_ref, marketing_hook, campaign_narrative, palette_intent), `channel_qr_map: {channel: qr_fact_id}`, `acceptance_criteria` (per-rubric-axis min scores + the hard factual gate list), `created_at`, `source` (intake | customer_feedback).
- **`FlyerLoopState`** / `loop_iterations: [FlyerLoopIteration]` on the project (new, dormant): each iteration = `{contract_ref, generation_params, evaluation (factual verdict + rubric scores + blockers), repair_action, outcome, ts}`. This is the single per-flyer trace.
- **`FlyerLoopEvaluation`** (new): `factual_verdict: pass|warn|block`, `factual_blockers: [str]` (from visual_qa/visible_contract — unchanged), `quality_scores: {axis: 1-10}`, `quality_verdict: pass|warn|fail`, `rubric_version`.
- **Reused unchanged:** rendered assets + `<asset>.qa.json` (visual QA) + `<asset>.text.json`. The oracle sidecar folds into `FlyerLoopEvaluation.quality_scores`.
- **`decisions.log`:** add `flyer_loop_*` LogEntry variants (iteration started/evaluated/repaired/escalated/approved) to the existing flyer_* union — same NDJSON chokepoint, no new audit substrate.

No new storage engine; JSON-on-disk + the existing `safe_io` atomic writes + the existing audit chokepoint (per Hermes drift rules).

## 6. Design contract schema (sketch — for review, not final)

```jsonc
{
  "contract_id": "DC-F0190-1",
  "project_id": "F0190",
  "source": "intake",                       // intake | customer_feedback
  "required_visible_facts": ["business_name","pricing_structure","item:0:name","contact_phone"],
  "creative_direction": {
    "hero_ref": "item:0",                    // grounded fact ref, never free invention
    "marketing_hook": "shared_price",        // CCA archetype id
    "campaign_narrative": "$7.99 for anything on the menu.",  // CCA/firewall-approved, grounded
    "palette_intent": "high_contrast_warm"   // intent hint to the generator; NOT a fabricated claim
  },
  "channel_qr_map": { "whatsapp": "qr:order", "instagram": "qr:profile" },  // future-proof; empty today
  "acceptance_criteria": {
    "factual_gate": ["no_fabricated_price","no_fabricated_offer","no_unverified_phone",
                     "no_location_leak","all_required_facts_visible","qr_channel_match"],
    "quality_min": { "professional_polish": 6, "contrast_legibility": 6,
                     "ai_look_inverse": 6, "message_clarity": 6 }
  },
  "rubric_version": "cca-loop-v1"
}
```

The contract is **negotiated before generation** (Planner emits it; Generator must satisfy it; Evaluator checks against it). On customer feedback the Planner produces a **new contract version** (diff persisted), so regeneration is contract-driven, not a raw text patch.

## 7. Quality rubric

Promote the dev-only `flyer_art_director_oracle` (8 axes) to a scored evaluator and ADD axes for the unhandled failure classes. Split into **deterministic** (cheap, reproducible) and **vision-LLM** (subjective) checks:

| Axis | Type | Maps to failed-case class | Method |
|---|---|---|---|
| `professional_polish` / `ai_look_inverse` | vision-LLM | AI-generated look / poor polish | oracle `would_i_post` + new prompt axis |
| `contrast_legibility` | **deterministic** | low-contrast / creamy / washed-out | colorimetric: text-region vs background luminance contrast ratio (WCAG-style), palette saturation/whiteness |
| `location_text_cleanliness` | **deterministic** | "South Riding"/"Northern Virginia" leak | OCR tokens checked against an allow-set of grounded identity/location facts; ungrounded geographic phrase → fail (NER-lite, conservative — only flags geographic tokens absent from facts) |
| `qr_channel_correctness` | **deterministic** | wrong QR / QR preservation | decode visible QR(s); verify payload matches the `channel_qr_map` for the delivery channel; verify QR present when a qr fact is required |
| `price_completeness` | **deterministic** | missing/dropped prices | every `required` price fact (incl. shared price) visible in OCR (extends today's item-price-pair check) |
| `message_clarity`, `hook_prominence`, `appetite_appeal`, `product_merchandising`, `offer_energy`, `brand_presence` | vision-LLM | general quality | existing oracle axes |

Scoring: each axis 1-10; `quality_verdict = fail` if any deterministic axis hard-fails or any acceptance-criteria `quality_min` is unmet; else `warn`/`pass`. **The factual fail-closed gate is independent and always wins** (a block-tier fabrication is never overridden by a high quality score). Rubric is **versioned** (`rubric_version`) so thresholds evolve without silent drift.

## 8. Evaluator / referee behavior

- **Tier 1 — factual (unchanged, fail-closed):** `visual_qa.run_visual_qa` + `visible_contract` + the fabrication firewall. `block` → never auto-ship. The loop does NOT modify these; it consumes their verdict. **This is the locked-fact-safety boundary and is out of scope to loosen.**
- **Tier 2 — quality rubric (new, measured-before-gating):** runs only when Tier 1 is `pass`/`warn`. Deterministic axes first (cheap, reproducible); vision-LLM axes only if deterministic axes pass (cost control). Produces `FlyerLoopEvaluation`.
- **Combined verdict:** `approve` (factual pass + quality pass) → deliver; `repairable` (factual block on a repairable text defect OR quality fail on a repairable axis) → Repair; `manual` (trust-risk class OR budget exhausted OR quality floor unmet after budget) → manual queue.
- **Never-auto-repair (trust-risk) set:** fabricated price/offer, unverified phone, wrong-brand, **location-leak** — straight to manual (mirrors `recovery.classify_flyer_qa_for_autorepair` `hard_stop`).

## 9. Repair / retry policy

- **One budget:** ≤ `MAX_GENERATIONS` (e.g. 2) + ≤ `MAX_REPAIRS` (e.g. 2) per project per contract version. Replaces the disjoint per-rung budgets.
- **Repair routing by evaluator verdict:** text-fidelity defect → targeted image-to-image premium repair; persistent integrated failure → deterministic-overlay recovery (text correct by construction); structured QA defect with a safe plan → Hermes-plan autorepair. All re-evaluated; a repair that introduces a trust-risk blocker is discarded.
- **Quality-driven repair (new, behind shadow→gate):** a quality-axis fail (e.g. low contrast) maps to a bounded regeneration with an adjusted `palette_intent` in the contract — NOT a free re-prompt. Capped by the same budget.
- **Hard stops:** trust-risk classes never repaired; budget exhaustion → manual; provider-unavailable → deterministic fallback → manual.

## 10. Customer-review loop

`customer draft → structured feedback → re-plan → regenerate → re-evaluate → approve`:

1. Customer receives preview (`awaiting_final_approval`).
2. Feedback is **classified**: (a) **factual edit** ("change the price to $6") → today's text-fact mutation path (unchanged); (b) **creative/aesthetic feedback** ("make it more festive", "too creamy", "bigger price") → **new**: the Planner produces a **new contract version** adjusting `creative_direction`/`palette_intent`/required-fact emphasis — routed through the SAME firewall (no fabrication).
3. Regenerate under the new contract; **re-evaluate** (scoped: re-run factual + the axes the feedback targeted); deliver; back to `awaiting_final_approval`.
4. Every feedback→contract-diff→outcome is recorded in the loop trace (so "0.53 revisions/flyer" becomes a measurable, attributable loop).
5. **Pre-req / dependency:** the 2026-06-02 revision-routing identity bug (stale intake-session hijack + LID/phone-JID split) must be resolved first or the feedback can misroute — flagged as a blocking dependency, not in this loop's scope to fix.

## 11. Observability / traces

- **One loop trace per project** (`loop_iterations`) capturing contract version, generation params, factual verdict + blockers, quality scores, repair action, outcome, customer feedback — so any failed flyer is fully reconstructable from disk (closes today's fragmentation across sidecars + log).
- **`decisions.log`** gains `flyer_loop_*` rows (iteration started/evaluated/repaired/escalated/approved/feedback-replanned) — same chokepoint.
- **Quality history:** rubric scores land in the trace (today the oracle sidecars are dev-only and not aggregated), enabling a quality trend the operator can watch (and a regression alarm — compose with the §12a freshness discipline if a loop table is added).
- **Cockpit:** surface the loop trace + quality scores read-only (extends the existing manual-queue/cockpit views).

## 12. Stop conditions & manual-review gates

Formalize the 14 existing gates as the loop's explicit stop conditions:

- **Trust-risk hard stop** (fabrication / unverified phone / wrong-brand / location-leak) → immediate manual, never repaired.
- **Budget exhausted** (generations + repairs spent) → manual with the best artifact + full trace.
- **Quality floor unmet after budget** (best quality score < acceptance min) → manual (only once the rubric gates; shadow first).
- **Kill-switch** (`FLYER_INTEGRATED_KILLSWITCH` and a new `FLYER_CD_LOOP_KILLSWITCH`) → bypass loop, deterministic-only or manual.
- **Provider unavailable** → deterministic fallback → manual.
- **Recovery watchdog escalation** (stale manual) → `operator_action_required` → Telegram (unchanged).
- **Cockpit close/no-send** (operator) — unchanged.

## 13. Rollout plan by slices (each its own PR + operator approval)

| Slice | Scope | Gate posture | Reversible |
|---|---|---|---|
| **0** | Additive dormant schema: `FlyerDesignContract`, `FlyerLoopIteration`, `FlyerLoopEvaluation` + `flyer_loop_*` LogEntry variants. No behavior change. | dormant | drop schema |
| **1** | Promote `art_director_oracle` to a **shadow evaluator**: score every flyer, write to the loop trace, **NO gating**. Measure the quality distribution vs the acceptance baseline. | shadow | flag off |
| **2** | Add the **deterministic** quality checks (contrast/colorimetric, location-text NER-lite, QR decode+channel, price completeness) as **shadow**. Measure false-positive rate. | shadow | flag off |
| **3** | **Loop controller**: refactor the procedural ladder into Planner/Generator/Evaluator/Repair with one budget + the persisted trace. **Behavior-preserving** (same verdicts as today), flag-gated, scoped allowlist. | behavior-preserving | flag off → old ladder |
| **4** | Activate the quality rubric as a **soft gate** (warn-tier, allowlist) — below-threshold flyers get `delivered_with_warning` + operator visibility, not blocked. Measure. | warn-only | flag off |
| **5** | **Customer-feedback → creative-direction regenerate** (structured, firewall-guarded). Depends on the revision-routing identity fix. | scoped | flag off |
| **6** | Promote the quality floor to a **block/manual gate** after the measured false-positive + acceptance numbers clear a pre-registered threshold. | gating | revert to warn |

Each slice ships behind a flag, scoped to the allowlist (`+17329837841`), measured in shadow before it gates — mirroring the project's CD v2 / firewall rollout discipline.

## 14. Risks & non-goals

**Risks**
- **Over-rejection** (quality floor too high → manual-queue flood). Mitigate: shadow-measure the distribution (Slice 1-2), pre-register the threshold, warn-tier before block-tier (Slice 4 before 6).
- **Vision-LLM cost/latency/nondeterminism** for the subjective axes. Mitigate: deterministic axes first; vision only when deterministic passes; per-flyer budget; cache; the rubric is versioned.
- **Customer-feedback creative-direction mutation is the riskiest path** — it could become a fabrication vector. Mitigate: route every re-planned contract through the SAME `flyer_brief_validator` firewall + CCA grounding; trust-risk classes still hard-stop.
- **Location-text NER-lite false positives** (legitimate location facts flagged). Mitigate: only flag geographic tokens ABSENT from grounded facts; shadow-measure first.
- **Refactor risk** (Slice 3 touches the ladder). Mitigate: behavior-preserving, golden-scenario-gated, flag-reversible to the old ladder.

**Non-goals (explicit)**
- NOT a rewrite of `render` / `visual_qa` / the firewall — the loop wraps them.
- NOT loosening any fabrication / locked-fact / price / schedule / phone blocking — the factual fail-closed gate is preserved verbatim.
- NOT changing the deployed model policy without operator approval.
- NOT Hermes 0.17, NOT WhatsApp Business Cloud migration, NOT community skills, NOT touching the Hermes version monitor (#510) or the F0190 fix (#511).
- NOT auto-deploying or auto-activating any gate — every slice is operator-approved + flag-scoped.
- NOT fixing the 2026-06-02 revision-routing identity bug here (flagged as a Slice-5 dependency).

## 15. Test strategy (per slice)

- **Unit:** each deterministic quality check (contrast ratio, location NER-lite, QR decode/channel-match, price completeness) as pure-function tests with grounded + adversarial fixtures (mirror `tests/test_flyer_copy_archetypes.py` style).
- **Referee/verdict:** `FlyerLoopEvaluation` combination logic — factual-block-always-wins, trust-risk-never-repairs, budget exhaustion → manual.
- **Golden scenarios:** extend `tests/test_flyer_golden_scenarios.py` with the named failure classes (AI-look fixture, location-leak fixture, low-contrast fixture, wrong-QR fixture, F0190 shared-price — already covered).
- **Shadow measurement:** run Slices 1-2 against the acceptance-baseline corpus; report the score distribution + false-positive rate before gating.
- **Behavior-preservation (Slice 3):** assert the loop controller produces the SAME verdicts as the current ladder on the golden corpus (diff test).
- **No-regression:** the full flyer suite (CCA, resolver, firewall, visual_qa, recovery) stays green; the factual referees are unchanged.

---

## Report (for the operator)

- **Doc path:** `docs/superpowers/plans/2026-06-29-flyer-creative-director-loop.md`
- **Key design decisions:** (1) the loop **consolidates** existing referees/repair rungs, it does not rewrite them; (2) **two-tier evaluation** — the factual fail-closed gate is preserved verbatim and always wins, a new scored quality rubric gates softly and only after shadow measurement; (3) the **art-director oracle is promoted** from dev-only to the rubric, extended with deterministic checks for the unhandled classes (contrast, location-leak, QR-channel, price-completeness); (4) **design contract negotiated before generation** + a **single per-flyer loop trace**; (5) **customer feedback re-plans the contract** (firewall-guarded), not raw text patches; (6) **one retry budget + explicit stop gates** replace the disjoint per-rung budgets.
- **Non-goals:** no rewrite of render/visual_qa; no loosening of fabrication/locked-fact safety; no model-policy/deploy change without approval; no Hermes 0.17 / WhatsApp migration / community skills; no auto-activation.
- **Proposed implementation slices:** 0 (dormant schema) → 1 (oracle shadow) → 2 (deterministic quality shadow) → 3 (behavior-preserving loop controller) → 4 (quality soft-gate, warn) → 5 (customer-feedback regenerate) → 6 (quality block-gate after measured threshold). Each its own flag-scoped PR.
- **Test strategy:** per-check unit tests + verdict-combination tests + golden-scenario fixtures for each named failure class + shadow measurement vs the acceptance baseline + a behavior-preservation diff test for the Slice-3 refactor + full-suite no-regression.
- **Deploy risk:** Slices 0-2 are dormant/shadow (near-zero); Slice 3 is a behavior-preserving flag-reversible refactor; Slices 4-6 gate only after measurement, allowlist-scoped, kill-switched.
- **Decisions I need from you before any implementation:**
  1. **Which failed-case axes should ever GATE (block/manual) vs stay shadow-only?** (esp. the subjective AI-look/polish axis — vision-LLM, nondeterministic.)
  2. **Is per-flyer vision-LLM quality scoring cost acceptable**, or should the gate be deterministic-only (contrast/location/QR/price) with the subjective axes shadow-only?
  3. **Is the customer-feedback → creative-direction regenerate path (Slice 5) in scope**, given it's the riskiest (fabrication vector) and depends on the revision-routing identity fix?
  4. **Allowlist + thresholds:** confirm activation stays scoped to `+17329837841` and that quality thresholds are pre-registered before any gate flips.

**No implementation will start until you explicitly approve this plan (and which slices).**
