# Flyer Deterministic-First Routing by Content Class — Design

**Date:** 2026-06-20
**Status:** Design for review (no implementation until the plan is approved).
**Drift-check tag:** `extends-Hermes` — adds one content-class gate to the existing `_integrated_poster_eligible` mode decision, behind a new allowlist-scoped flag. Reuses the deployed mode-2 path (textless background + deterministic premium overlay), the `force_background_only` plumbing, the persisted `deterministic_recovery` final-export flag, the referee, and the shared allowlist. No new render machinery, no schema migration.

---

## 1. Problem & motivation (data-grounded)

The integrated (model-rendered text) path is the primary path for menu flyers, but live data shows it is the wrong tool for fact-dense content:

- Integrated render produces correct exact text on the **first try only ~24%** of the time (9/37 attempts in the current decisions.log window).
- The other ~76% fail QA → 27% auto-recover deterministically, **46% go to manual**.
- **~75–80% of failures are model text-fidelity** (garble / duplicate / dropped / misspelled fact), not genuinely dangerous content (~15% is wrong brand / wrong phone / fabricated).
- Each fix so far (F0174 brand-typo, F0176 en-dash, F0179 duplicate/dropped item) is the **same root cause** — the model cannot reliably render exact fact-dense text — surfacing as a new garble shape, met with a new recoverable-classifier patch.

The deterministic overlay already produces the correct **and** premium result (emblem, hero food, offer seal, dot-leader menu ≈ approved mockup A), and — since the flat-degrade fix (#500/#501) — it renders reliably in the live gateway via the `/usr/bin/python3` subprocess. So for fact-dense flyers we can stop asking the model to render exact text and route directly to the deterministic premium overlay. This removes the dominant failure mode at the root instead of patching its symptoms.

**Goal:** route fact-dense flyers directly to mode 2 (textless imagery + deterministic premium overlay), skipping the integrated text attempt; keep integrated only for sparse creative flyers. Scoped to `+17329837841` first.

**Non-goal (explicitly deferred):** expanding `_qa_failed_exact_text_recoverable` for the F0179 duplicate/dropped-item case. This design supersedes that patch for fact-dense content.

## 2. Architecture — where the router hooks (reuse, don't reinvent)

The mode-1 (integrated) vs mode-2 (textless background + deterministic overlay) decision already lives in `_integrated_poster_eligible(project)` (`render.py:1159`). It already returns `False` (→ mode 2) for: `_FORCE_BACKGROUND_ONLY` set, persisted `deterministic_recovery`, `FLYER_ALLOW_INTEGRATED_POSTER != "1"`, reference-extraction pending, source-edit, QR/barcode facts, and non-food projects.

**The router adds one more gate to the same function:** when deterministic-first is enabled for the project AND the project is fact-dense, `_integrated_poster_eligible` returns `False`. The existing complement `_background_only_eligible` then returns `True`, and the **primary** `render_concept_previews` call (no `force_background_only` needed) renders a textless background + applies the deterministic premium overlay — on the *first* render. No integrated attempt, no QA-fail→recover cycle, no manual path for text fidelity.

```
inbound brief → create project (locked_facts extracted, as today)
  → render_concept_previews (primary)
      → _integrated_poster_eligible(project)?
           NEW: deterministic-first ON + _is_fact_dense(project) → False  ──┐
           (existing gates unchanged)                                       │
      → integrated (mode 1)            ← sparse / flag off                  │
      → textless bg + deterministic premium overlay (mode 2) ←─────────────┘ fact-dense
  → visual_qa referee (UNCHANGED) → awaiting_final_approval | degrade flat | manual
```

This is a single additive branch in one already-central function. Everything downstream (overlay, scrims, coverage/ink, referee, finals re-overlay via the persisted flag, fail-closed) is unchanged.

## 3. (Section 1) Content-class router

**Mechanism: a deterministic heuristic over the project's structured `locked_facts`** — `_is_fact_dense(project) -> bool`. No LLM call, no new model failure surface, fully auditable. (Rejected alternative: an LLM content classifier — adds latency, cost, and a *new* model-judgment failure mode, which is exactly what we're trying to remove. A deterministic count of structured facts we already extract is simpler, stabler, and safer — matching the operator's stated bar.)

The router reuses existing fact helpers: `facts_by_id(project)`, `_distinct_grounded_item_count(...)`, `fact_value(project, fact_id)`, and `_is_food_or_grocery_project(project)`.

`_is_fact_dense(project)` returns `True` when ANY of:
1. **Menu / multi-item list:** ≥ 2 distinct `item:N:name` locked facts (`_distinct_grounded_item_count` ≥ 2).
2. **Exact price list:** ≥ 2 `item:N:price` locked facts, OR a `pricing_structure` fact (a *currency-amount* menu price such as "Any item $7.99" / "Buffet $11.99" — **not** a percentage discount like "10% off", which extracts as `offer:N`).
3. **Combo menu:** ≥ 2 `offer:N` facts (multiple bundled offers), OR a single `offer:N` whose value enumerates ≥ 2 priced combos (veg + non-veg combo).
4. **Schedule + price:** a `schedule` locked fact (recurring hours, e.g. "Sat & Sun 4–8 PM") AND a currency-amount price fact (`pricing_structure` / `item:N:price` / `offer:N` containing a currency amount). A one-off `event_date` + a percentage discount does **not** trigger this.

Otherwise `False` → **sparse**. (Note: a lone percentage-discount offer or a single event announcement with no item list and no currency-price list stays sparse → integrated.)

The router is gated (it only changes behavior when the new flag is enabled for the project — see §5). Order inside `_integrated_poster_eligible`: the new gate sits with the other early `return False` gates, AFTER the structural exclusions (reference-extraction, source-edit) so those keep their current behavior.

## 4. (Section 2) Fact-dense vs sparse criteria

| Signal (from `locked_facts`) | Fact-dense |
|---|---|
| distinct `item:N:name` count | ≥ 2 |
| `item:N:price` count | ≥ 2 |
| `pricing_structure` fact (currency menu price, e.g. "Any item $7.99"; not a % discount) | yes |
| `offer:N` count | ≥ 2 |
| `schedule` fact (recurring hours) + any currency-amount price fact | yes |
| none of the above (lone % discount, single event announcement, no item/price list) | **sparse** |

**Sparse** = a single headline/message, an event announcement, a greeting/promo with ≤ 1 exact priced/menu fact and no item list. Sparse flyers keep the integrated path (where the model's composition adds value and exact-text risk is low).

Rationale for thresholds: a single named item with one price (e.g., "Diwali Dinner $20") is low-garble-risk and reads fine integrated; the failure mode appears with *lists* (the model duplicates/drops/misspells when juggling ≥ 2 exact items) — so the dense threshold is "≥ 2 exact items/prices, or a structured price/combo/schedule+price." Thresholds are the one tunable; defaults above, adjustable after the scoped soak (§8).

## 5. (Section 3) Examples

| Brief | Facts | Class | Route |
|---|---|---|---|
| "Weekend Specials — Idli, Dosa, Vada, Uttapam, Pongal, Sambar, any item $7.99, Sat & Sun 4–8 PM" (F0179) | 6 items + pricing_structure + schedule | **fact-dense** | mode 2 deterministic |
| "Veg combo $12.99, Non-veg combo $15.99" | 2 offers + 2 prices | **fact-dense** (combo) | mode 2 deterministic |
| "Lunch buffet $11.99, Mon–Fri 11–3" | pricing_structure + schedule | **fact-dense** | mode 2 deterministic |
| "Diwali celebration this Saturday at our restaurant — everyone welcome!" | business_name + schedule, no menu/price | **sparse** | integrated (mode 1) |
| "Now hiring — apply in store" | business_name only | **sparse** | integrated (mode 1) |
| "Grand opening Dec 25, 10% off everything" | 1 offer, no item list | **sparse** | integrated (mode 1) |

## 6. (Section 4) Fallback behavior

Fact-dense → mode 2 → deterministic premium overlay. The fallback ladder is the **existing** one (unchanged), now reached on the *primary* render:
1. **Premium overlay renders** (correct text by construction) → referee verifies → `awaiting_final_approval`. (Premium delivered.)
2. **Premium overlay can't fit** (fit/coverage `FlyerRenderError`) → degrade to flat overlay (still correct text, flat look) → referee → `awaiting_final_approval`. Observable via `flyer_premium_overlay_outcome status=premium_overlay_degraded_to_flat` (no alert; expected).
3. **Flat overlay also can't fit** → `manual_edit_required` (same terminal as today).
4. **Unexpected premium-render error** → flat + `flyer_premium_overlay_outcome status=premium_overlay_failed_unexpected` + operator alert (from #500).

Because the deterministic overlay draws the exact locked facts and the coverage check (`ink`/`_covered`) enforces presence by construction, fact-dense flyers should essentially never manual for text fidelity — only the rare true can't-fit case. The integrated→QA→non-recoverable→manual path is **not reached** for fact-dense content. Sparse flyers retain today's integrated behavior (integrated → QA → deterministic-recovery rung → manual) unchanged.

## 7. (Section 5) Interaction with existing flags

New flag: **`FLYER_DETERMINISTIC_FIRST`** — gates the router. Read via `_deterministic_first_enabled(project)` mirroring `_deterministic_recovery_enabled` EXACTLY: `flag == "1"` AND (shared `FLYER_PREMIUM_OVERLAY_ALLOWLIST` empty ⇒ global, else `_normalize_sender(project.customer_phone)` in allowlist). Reuses the shared allowlist so the scoped cohort stays `+17329837841`. **Default off ⇒ flag-off byte-identical** (the new gate in `_integrated_poster_eligible` only fires when the flag is on for the project).

| Flag | Role under deterministic-first |
|---|---|
| `FLYER_DETERMINISTIC_FIRST` (new) | ON + fact-dense ⇒ skip integrated, go mode 2 |
| `FLYER_ALLOW_INTEGRATED_POSTER` | still required for integrated; deterministic-first simply makes fact-dense projects ineligible *before* this is consulted. Sparse projects still honor it. |
| `FLYER_PREMIUM_OVERLAY` (+ allowlist) | must be ON for the mode-2 overlay to render **premium** (else mode-2 falls to flat). Already ON for `+17329837841`. Deterministic-first without premium-overlay = flat menus (correct but not premium) — so the two ship together for the cohort. |
| `FLYER_DETERMINISTIC_RECOVERY` | becomes a no-op for fact-dense (no integrated attempt to recover from); still active for sparse. Left unchanged. |
| `FLYER_INTEGRATED_KILLSWITCH` | global kill of integrated; orthogonal. Deterministic-first is the targeted, content-aware version. |

Precedence inside `_integrated_poster_eligible`: existing structural gates (reference-extraction, source-edit, QR) first → then the new `deterministic-first AND fact-dense` gate → then the existing food/flag gates. So reference/source-edit/QR behavior is untouched.

## 8. (Section 6) QA / referee behavior

**Unchanged. The referee still gates every delivered asset.** The deterministic premium overlay output is read back by `visual_qa` (the same referee), which verifies every required locked fact is visible and correct, with the semantic-normalization from #498 (so formatting-only variants pass). Because the overlay draws from locked facts and the coverage check guarantees presence, the referee should pass cleanly; if the overlay ever garbled or dropped a fact, the referee catches it → degrade/manual (fail-closed preserved). Dangerous-content checks (fabricated price, unverified phone, wrong brand) remain — though they are effectively unreachable for deterministic output, which only ever draws operator/customer-locked facts (no fabrication). Net: the referee's role is unchanged; it simply has far less garble to catch because the model is no longer drawing the text.

## 9. (Section 7) Expected impact on manual rate

From the current window: ~46% of integrated attempts manual, ~75–80% of failures text-fidelity. If most pilot flyers are fact-dense (menus/combos/price lists — the operator's stated use), routing them deterministic-first:
- Eliminates the text-fidelity manual path for that class → fact-dense manual rate → **near 0** (only true can't-fit).
- Eliminates the integrated-render variance (and ~1 gemini call + ~30s latency) for that class.
- Residual manuals concentrate in (a) sparse flyers still on integrated, and (b) genuinely dangerous content (which *should* manual).

Quantified expectation to validate in the soak: for fact-dense briefs from `+17329837841`, **premium-delivered % → high (target ≥ 90%)**, **manual % → low single digits**, vs today's ~46% manual on the integrated path. (Pre-registered; measured, not assumed.)

## 10. (Section 8) Rollout plan — scoped to +17329837841 first

1. Build behind `FLYER_DETERMINISTIC_FIRST` (default off), allowlist-scoped via the shared `FLYER_PREMIUM_OVERLAY_ALLOWLIST` (already `+17329837841`). Premium overlay + the flat-degrade fix are already live for that number.
2. Tests + Codex + PR/CI/Codex gate; deploy dormant (flag off ⇒ byte-identical), verified by a deploy smoke gate that the router is a no-op when the flag is off.
3. Operator-gated activation: set `FLYER_DETERMINISTIC_FIRST=1` (scoped), restart gateway. Confirm scoped-only (`+1732…`→on / other→off).
4. Operator sends the fact-dense validation briefs (Weekend Specials / Veg-NonVeg Combo / Festival Dessert / schedule+price). Confirm decisions.log: `flyer_premium_overlay_outcome status=premium_overlay_delivered render_path=subprocess`, no integrated attempt for fact-dense, dangerous-leak = 0, premium editorial delivered.
5. Soak the scoped cohort; review metrics (§11) before any broadening. **Do NOT broaden beyond +17329837841 without explicit approval.**
6. Rollback: `FLYER_DETERMINISTIC_FIRST` off + restart → integrated-primary behavior returns exactly (and premium/recovery still available as today). Full artifact rollback available via the deploy snapshot.

## 11. (Section 9) Success metrics (pre-registered)

Measured on the scoped `+17329837841` cohort, fact-dense briefs:
- **Premium-delivered %** — `flyer_premium_overlay_outcome status=premium_overlay_delivered` / fact-dense flyers. Target **≥ 90%**.
- **Manual %** — `flyer_integrated_manual_review` + manual_edit_required / fact-dense flyers. Target **low single digits** (down from ~46%).
- **Dangerous-leak = 0** — zero wrong-brand / wrong-phone / fabricated-price / wrong-fact deliveries (referee + manual review of every delivered fact-dense flyer during soak). Hard gate.
- **Proudly-postable quality** — operator visual judgment: "would a restaurant owner post this?" on the delivered fact-dense flyers (the mockup-A bar). Qualitative gate; ship-broadening blocked until met.

## 12. Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Content classification (menu vs creative) | none — Hermes classifies sender_role / media_type, not flyer fact-density | build — a deterministic heuristic over our own structured `locked_facts` (no model call) |
| Mode selection (integrated vs deterministic render) | n/a (our render internals) | reuse existing `_integrated_poster_eligible` / `_background_only_eligible` |
| Deterministic image rendering | none (Pillow, our internals) | reuse the deployed mode-2 overlay |

awesome-hermes-agent ecosystem check: fact-density routing of a flyer pipeline is a project-internal concern with no Hermes/ecosystem skill overlap. Verdict: no Hermes substrate applies; the router is a thin deterministic gate over facts we already extract → `extends-Hermes`.

## 13. Deployed-pattern compliance (drift checklist)

- **Mode decision:** extends the existing `_integrated_poster_eligible` gate set; no parallel decision path. ✓
- **Flag + allowlist:** `_deterministic_first_enabled` mirrors `_deterministic_recovery_enabled` exactly (shared `FLYER_PREMIUM_OVERLAY_ALLOWLIST`, `_normalize_sender`). ✓
- **Flag-off byte-identical:** new gate only fires when the flag is on for the project. ✓
- **Fact helpers:** reuses `facts_by_id` / `_distinct_grounded_item_count` / `fact_value`; no new fact parsing. ✓
- **Referee / fail-closed:** unchanged; deterministic output still goes through visual_qa. ✓
- **No schema migration:** uses existing `locked_facts`; the persisted `deterministic_recovery` final-export flag already exists and is reused for finals re-overlay. ✓

## 14. Safety / preserved guarantees

- Referee gates every delivered asset; dangerous-content checks unchanged; fail-closed → flat → manual ladder intact.
- Deterministic overlay only ever draws operator/customer-locked facts → no fabrication surface.
- Flag-off byte-identical; scoped to one number; reversible by flag.
- No weakening of the recoverable-classifier (it's bypassed for fact-dense, not loosened); the F0179-class danger checks remain for any residual integrated path.

## 15. Residual risks

- **Mis-classification (sparse judged dense or vice-versa):** deterministic heuristic on structured facts is conservative; worst case a sparse flyer goes deterministic (still correct, just less "model-composed") or a borderline menu stays integrated (today's behavior). Neither is unsafe. Thresholds tunable post-soak.
- **Deterministic premium design quality** for unusual briefs (very long menus, mixed-language) — the overlay's layout solver handles fit/degrade; the referee + proudly-postable gate catch shortfalls. This is the dimension the soak validates.
- **Mixed flyers (menu + strong creative headline):** default to fact-dense (deterministic) because exact text is present and that's the risk; the headline still renders deterministically in the premium overlay.

## 16. Open decisions for operator review

1. **Thresholds** (§4): confirm "≥ 2 items/prices, or pricing_structure, or ≥ 2 offers, or schedule+price" as the dense bar (recommended), or adjust.
2. **New flag vs reuse:** new `FLYER_DETERMINISTIC_FIRST` (recommended) vs folding into an existing flag. Recommend new for clean, reversible, scoped control.
3. **Sparse path:** keep integrated for sparse (recommended, per your direction) vs eventually deterministic-everywhere. Out of scope for this pass; revisit after soak.

## 17. Out of scope (deferred)
Expanding `_qa_failed_exact_text_recoverable` for F0179 (superseded for fact-dense); LLM content classification; deterministic-everywhere (sparse retains integrated); broadening beyond `+17329837841`; overlay visual redesign (the v2/v2.1 overlay is the renderer); combo two-hero variant.
