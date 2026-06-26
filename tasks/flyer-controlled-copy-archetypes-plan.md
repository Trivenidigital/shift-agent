# Flyer Controlled Copy Archetypes (CCA) — PLAN (HELD for approval)

**Status:** DESIGN ONLY. No code, no commit, no deploy. Awaiting operator approval.
**Date:** 2026-06-26
**Branch (proposed):** FRESH branch off `origin/main` (a70037d) in a new worktree. Architecture C / Track-2b stays **parked** (uncommitted in `sme-track2`, kept as a patch/report, NOT PR'd). CCA does not build on C — it replaces the campaign-narrative source.

**Drift-check tag:** `extends-Hermes` — uses the Hermes gateway for a single constrained *classification* label, adds a deterministic Python template/slot composer, and reuses the existing deterministic firewall. No Hermes convention fought.

**New primitives introduced:**
- `flyer_copy_archetypes.py` — new module: the archetype enum, the approved template library (text + required slots + preconditions), slot extraction from `locked_facts`, the deterministic composer + selection, and the archetype classifier seam.
- A rewrite of `_resolve_campaign_narrative` (in `flyer_creative_resolver.py`) to source the headline from CCA (classify → compose → firewall → title) instead of brain free-text.

**Why CCA (the root finding it answers):** Both Track-2b and Architecture C failed at ~0/7 marketing-grade because the *open-vocabulary* problem lives in the SAFETY firewall — a deterministic vocabulary cannot distinguish safe-novel evocative words ("delightful") from fabricated-concrete ones ("falafel"), so vivid copy is filtered before any judge sees it; only bland copy survives. CCA removes the open vocabulary entirely: the marketing words come from a **finite approved library**, and the only variability is **grounded locked-fact slots**. The LLM's role shrinks to *classification* (reliable) instead of *generation* (the weak link).

---

## Hermes-first per-step checklist

| Step | Tag | Notes (capability / why net-new) | net-new LOC |
|---|---|---|---|
| 1. Brief proposed (CD v2) | `[Hermes]` | LLM gateway — existing | 0 |
| 2. Classify campaign → ONE archetype label | `[Hermes]` | LLM gateway, constrained single-label JSON (prompt net-new, call is substrate) | 0 |
| 3. Validate label against the archetype enum | `[net-new]` | tiny Python (unknown → none) | ~15 |
| 4. Extract slots (price/product/free-item/combo/event/date) from locked_facts | `[net-new]` | Python — no Hermes primitive | ~60 |
| 5. Select an approved template whose preconditions hold + slots are grounded | `[net-new]` | Python template library + selection | ~80 |
| 6. Compose the final headline (fill slots) | `[net-new]` | Python | ~50 |
| 7. Validate composed headline (deterministic firewall) | `[net-new]` | REUSE existing `is_safe`/`scrub_campaign_narrative` | ~10 |
| 8. Fallback to campaign_title | `[net-new]` | Python (existing pattern) | ~10 |
| 9. Observability (archetype/template/slots/outcome) | `[Hermes]` | audit chain / structured logs | ~20 |
| 10. Headline → overlay render | `[Hermes]` | existing render path — untouched | 0 |

**awesome-hermes-agent ecosystem check + verdict:** No SMB- or marketing-copy-template skill exists in the Hermes ecosystem (re-confirmed against `tasks/skills-roadmap.md`). The classification call rides the deployed gateway; the template library + slot composer are genuine net-new (≈255 LOC) and are the intended substitute for open-vocab LLM copy.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/flyer_creative_resolver.py` (`_resolve_campaign_narrative`, `_locked_value_by_id`, `resolve_creative_direction`) — CCA rewrites the narrative resolution here.
- ✅ Read `src/agents/flyer/flyer_narrative_quality.py` (`is_safe` / `evaluate_narrative_candidate` hard-reject classes) — CCA reuses this as the final firewall.
- ✅ Read `src/agents/flyer/flyer_brief_validator.py` (`scrub_campaign_narrative`, `_phrase_is_grounded`, `_first_ungrounded_commercial`) — the firewall that validates the composed headline.
- ✅ Read `src/agents/flyer/flyer_poster_archetype.py` (`select_poster_archetype` deterministic enum-mapping) — the structural pattern CCA's classification/selection mirrors (and confirms poster_archetype is VISUAL layout, orthogonal to copy).
- ✅ Read `src/platform/schemas.py` (`FlyerLockedFact` fact_id/value/source) — the only slot source.

Drift-check result: no copy-archetype / headline-template / phrase-library exists in tree (grep clean 2026-06-26) — CCA is net-new, not redundant.

---

## 1. Architecture

```
locked_facts + raw_request + brief.request_intent
   │
   ▼
[1] ARCHETYPE CLASSIFIER → ONE label from a FIXED enum
    (weekend_one_price | combo | bucket_meal | grand_opening |
     customer_appreciation | festival_dessert | event | none)
    The classifier EMITS A LABEL ONLY — never copy.
   │  archetype (unknown/none → title)
   ▼
[2] SLOT EXTRACTION (Python, from locked_facts only): price, product(s),
    free_item, combo_type, event_name, date/schedule, appreciation_offer
   │
   ▼
[3] TEMPLATE SELECTION (Python): for the archetype, the FIRST approved template
    whose PRECONDITIONS hold AND whose required SLOTS are all grounded.
    No eligible template → title.
   │
   ▼
[4] COMPOSE headline (Python): fill the template's slots from grounded facts.
   │
   ▼
[5] DETERMINISTIC FIREWALL (existing is_safe/scrub) on the composed headline.
   │  pass                                            fail → title
   ▼
selected headline   (classify=none / no eligible template / firewall fail → campaign_title;
                     campaign_title absent → "")
```

**Safe by construction:** (a) the LLM emits only an enum label — it cannot inject fabricated copy; a wrong label yields a different *grounded* template or the title. (b) Marketing words come only from the **approved template library**. (c) The only variables are **grounded locked-fact slots** — a template with an ungrounded required slot or unmet precondition is ineligible, so no fabricated price/product/offer/urgency. (d) The existing firewall still runs as the final backstop.

## 2. Supported archetypes

Each template carries **preconditions** (facts that must exist to make the claim TRUE) and **required slots** (variables filled from facts). `${...}` = a slot. Templates are tried in listed order (most offer-specific first).

| Archetype | Preconditions (grounded facts required) | Slots | Approved templates |
|---|---|---|---|
| `weekend_one_price` | a single shared price across items (pricing_structure / equal item prices) + weekend schedule | `price` | "${price} favorites all weekend." · "Weekend favorites, one easy price." · "One price. Weekend favorites." |
| `combo` | ≥1 combo offer/item | `combo_count?` | "Two combos. One easy choice." (needs combo_count=2) · "Dinner combos made easy." · "Family combos, ready to serve." |
| `bucket_meal` | a bucket/family-pack product | `product` | "${product}, served by the bucket." · "A ${product} feast for the table." |
| `grand_opening` | grand-opening intent/title + a grounded free item | `free_item`, `location?` | "New location. Free ${free_item}." · "A warm welcome, on us." |
| `customer_appreciation` | a grounded complimentary/appreciation offer | `free_item?` | "A thank-you ${free_item} on us." (needs grounded free_item) · "Our treat for your table." |
| `festival_dessert` | dessert products present | — | "Sweet trays for every celebration." · "Desserts made for sharing." |
| `event` (Diwali/festival) | event/festival title (+ optional date) | `event_name` | "${event_name} dinner, served festive." · "Celebrate ${event_name} around the table." |
| `none` | (classifier could not place it) | — | (no headline → campaign_title) |

Notes: a template with NO `${slot}` (e.g. "Our treat for your table.") still requires its **preconditions** (here: a grounded appreciation/free offer) — it never fires for an unrelated campaign. `free_item`/`event_name`/`product` slots are filled with the **exact grounded fact value** (or a safe normalized form), never invented. The library is small, finite, and hand-approved; growth is a deliberate edit, not open-vocab.

## 3. Required locked-fact inputs (slot sources)

| Slot | Source fact(s) | Grounding rule |
|---|---|---|
| `price` | `pricing_structure` ("any item $7.99") or equal `item:N:price` | only if a SINGLE shared price is present; emit the exact `$x.yy` |
| `product` | `item:N:name` | exact item name (must be in facts; also must clear the firewall's product check) |
| `free_item` | `offer:N` containing "free"/"complimentary" + object | only the grounded object (reuse `_phrase_is_grounded`); never "free X" unless "free X" ∈ facts |
| `combo_type`/`combo_count` | `offer:N` / `item:N:name` containing "combo" | count from the number of grounded combo offers |
| `event_name` | `campaign_title` (for event/festival archetypes) | exact title token(s) |
| `date`/`schedule` | `schedule` fact | used as a precondition (weekend/day grounding), not free-text injected |
| `appreciation_offer` | `offer:N` ("complimentary dessert ...") | precondition for `customer_appreciation` |

## 4. Classification

The classifier returns ONE label from the fixed enum. **Open choice for operator (§12):**
- **(A) LLM classification (as you specified):** one constrained gateway call (same seam/model as the brain), JSON `{"archetype": "<enum>"}`, validated against the enum (unknown → `none`). Low risk (label only), but still an LLM dependency.
- **(B) Deterministic classification from fact-structure (recommended to consider):** infer the archetype from the locked-fact shape (shared price + weekend → `weekend_one_price`; combo offers → `combo`; bucket/biryani product → `bucket_meal`; "free/complimentary" offer + opening title → `grand_opening`; "complimentary" + appreciation title → `customer_appreciation`; dessert items → `festival_dessert`; festival/event title + date → `event`). **No LLM at all on the headline path** — maximally robust, and the C eval showed the LLM is the weak link.
- **(C) Hybrid:** deterministic first; LLM only to disambiguate when the fact-structure is ambiguous.

Either way the label is enum-validated and unknown → `none` → title. The plan is written so the classifier is a single swappable function; the composer/templates are identical regardless of A/B/C.

## 5. Composition + selection (deterministic)

- For the chosen archetype, iterate its templates in priority order; pick the FIRST whose preconditions all hold and required slots are all grounded.
- Fill `${slot}` with the exact grounded value (price `$x.yy`; product/event/free_item from the fact, normalized for case/spacing only).
- No eligible template → return `campaign_title`.
- Selection is pure/deterministic — NO LLM ranking (the templates are pre-approved + grounded). Variety across campaigns comes from different archetypes/slots; within an archetype the priority order is deterministic. (Cross-flyer rotation is explicitly OUT of scope, consistent with the Track-2 no-history decision.)

## 6. Safety validation (unchanged firewall, as the backstop)

The composed headline is run through the existing deterministic firewall (`is_safe` → `scrub_campaign_narrative` + the hard-reject classes). Because templates are pre-approved and slots are grounded, this should always pass; if it ever does not (e.g. a slot value interacts oddly), the headline → `campaign_title`. Hard rejects retained exactly: fabricated prices/products/numbers/schedules, unsupported free/discount/BOGO, unsupported urgency, banned crutches, overlong. **No firewall weakening.**

## 7. Fallback behavior (all → safe `campaign_title`, else "")

- classifier returns `none` / unknown / errors → `campaign_title`;
- no template's preconditions+slots are satisfiable → `campaign_title`;
- composed headline fails the firewall → `campaign_title`;
- `campaign_title` itself absent → "" (legacy overlay promotes the title downstream; never headline-less).

## 8. Observability (bounded + traceable)

Structured log per resolution: classifier source (A/B/C) + archetype label, template id chosen (or none), slots used, firewall result, outcome (`archetype` | `title`). (Extending `FlyerCreativeDirectorRouted` deferred for the same reason as C — the emit site lacks the data without render-path plumbing, a non-goal; structured logs make every decision traceable in journalctl.)

## 9. Tests (TDD)

- **Classifier:** each archetype's representative facts → expected label (LLM path uses an injected fake classifier; deterministic path is pure). Unknown label → `none`.
- **Slot extraction:** price only from a shared-price fact; free_item only from a grounded free offer ("free mango lassi" ∈ facts → fillable; "free appetizer" ∉ facts → not fillable); product only from a grounded item; event_name from title.
- **Template selection:** preconditions enforced (a `weekend_one_price` template never fires without a shared-price fact); first-eligible chosen; no eligible → title.
- **Composition:** filled headline equals the expected approved phrase with the grounded value.
- **Safety:** every composed headline passes `is_safe` (property test across archetypes); a deliberately-broken template/slot → firewall catches → title.
- **Fallback:** classifier none/error → title; no fillable template → title; firewall fail → title; empty/absent title → "".
- **No behavior change when CD v2 off** (off-path parity).
- All offline (injected classifier gateway; no network).

## 10. 7-category eval plan

Same harness pattern + same 7 categories (Weekend F0188, Festival Dessert F0185, Combo F0186, Bucket, Grand Opening, Customer Appreciation, Diwali/Event), box, no deploy/no send. Per category capture: archetype label, eligible templates, slots, composed headline, firewall result, final selected. **Success bar (unchanged):** ≥6/7 marketing-grade, no fabricated facts, no banned crutches, no parroting, clear offer/benefit/emotional angle, meaningful variation, legacy fallback safe. CCA's hypothesis: because the templates are hand-written marketing-grade and fire only when grounded, 6/7 is reachable **iff** classification is right and slots are grounded — so the eval primarily measures classification accuracy + slot-grounding coverage (the two remaining failure modes), not open-vocab quality.

## 11. Non-goals (hard boundaries)

No composition changes; no rendering changes; no routing changes; no extraction changes; **no QA/firewall weakening**; no Phase 2/3; no rollout broadening; **no deploy**; no cross-flyer history/rotation. Architecture C / Track-2b stays parked (not merged, not PR'd). Review via SUBAGENT reviewers (Codex out of scope): one safety (firewall still the backstop; slots grounded-only; no fabrication) + one structure (classifier/composer correctness, enum validation, off-path parity, deploy packaging for the new module).

## 12. Open choices for operator

1. **Classifier:** (A) LLM label (as specified) · (B) deterministic-from-fact-structure (recommended — removes the last LLM dependency on the headline path) · (C) hybrid.
2. **Template library scope:** ship the 7 archetypes above as v1, or a narrower subset first (e.g. the 3 real categories) then expand.
3. **Slot value form:** inject the exact fact value (e.g. product name verbatim) vs a normalized/title-cased form.

## 13. Build sequence (after approval — NOT started)

1. `flyer_copy_archetypes.py`: archetype enum + approved template library (data) + slot extraction + composer/selection (+ classifier per the chosen option) — pure, TDD.
2. Wire `_resolve_campaign_narrative` → CCA path (classify → compose → firewall → title); preserve empty→"".
3. Deploy packaging (install line + smoke import probe + static test) for the new module — mirror the existing pattern; avoid the added-module-no-install-line footgun.
4. Focused tests + broader flyer suite green; subagent reviews (safety + structure); fix BLOCKER/MAJOR to CLEAN.
5. Re-run the 7-category eval; report OLD vs CCA table against the 6/7 bar. **Stop for operator decision.** No merge/deploy.
