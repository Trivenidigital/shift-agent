# Flyer creative-planner — item-index reconciliation (DESIGN)

**Drift-check tag:** `extends-Hermes` — modifies the custom `facts.py` fact-merge layer that sits on top of Hermes text ingestion + LLM gateway; preserves all deployed conventions (Pydantic facts, deterministic extraction, dormant-by-flag). No Hermes convention is fought.

**Status:** DESIGN ONLY — no code until reviewed (Codex 5-lens + operator). Topic: `flyer-item-index-reconciliation`.

**Prerequisite framing:** this is the gating prerequisite to *ever* enabling the bounded creative planner (design #429). If a planner-inferred item can overwrite a customer-grounded item fact, the truth contract is not safe enough to flip any category flag. Operator-approved 2026-06-01.

---

## Hermes-first capability checklist (per-step)

| Step | Tag | Note |
|---|---|---|
| 1. Ingest the customer's request text | `[Hermes]` — WhatsApp inbound text ingestion (substrate) | already in place |
| 2. Grounded fact extraction (`_item_name_facts`/`_item_price_facts`) | `[net-new]` — custom flyer extractor; no Hermes primitive merges per-customer locked facts | existing custom code |
| 3. Planner item-name inference | `[net-new]` — custom planner contract (the LLM call itself is the `[Hermes]` gateway, already credited) | existing custom code |
| 4. Drop junk count-phrase item facts | `[net-new]` — custom merge-prep logic | this design |
| 5. Offset inferred indices past grounded | `[net-new]` — custom index-ownership invariant | this design |
| 6. Remainder-fill cap (mixed count) | `[net-new]` — custom | this design |
| 7. Flat-price pairing with customer provenance | `[net-new]` — custom | this design |
| 8. `merge_locked_facts` assembly | `[net-new]` — custom fact merge (untouched by this design) | existing custom code |

awesome-hermes-agent ecosystem check: no skill governs an app's internal locked-fact merge precedence. Verdict — the only net-new is inside our existing custom `facts.py` layer; nothing here is substrate Hermes already provides (steps 1 and the planner's LLM call are the only `[Hermes]` parts and are already wired).

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/facts.py` (`merge_locked_facts` at line 591, the `grouped[index][kind] = fact` overwrite at line 651, `_item_name_facts` at 395, `extract_text_facts` at 481) before drafting the merge invariant.
- ✅ Read `src/agents/flyer/creative_planner.py` (`materialize_inferred`, `is_active`, `request_matches_enabled_category`) before drafting the offset + dormant gating.
- ✅ Read `src/agents/flyer/visual_qa.py` (`_requested_item_count`, `_inferred_intent_count_blockers`) before proposing to reuse the count parser for the remainder-fill cap.
- ✅ Read `tests/test_flyer_creative_planner.py` and `tests/test_flyer_facts.py` to mirror the deployed test structure (in-process unit + injected-provider end-to-end).

---

## 1. Problem (grounded in the merge mechanics)

`extract_text_facts` (`facts.py:481`) assembles ONE list of `FlyerLockedFact`s and hands it to `merge_locked_facts` (`facts.py:591`). For **item** facts (`item:N:name` / `item:N:price`), merge does three things, in order, **per input list**:

1. **Group by index, last-seen wins** — `grouped[index][kind] = fact` (`facts.py:651`). If the same list carries two `item:0:name`, the **later one silently replaces** the earlier.
2. **Reconcile by name-key + source priority** — `add_or_replace_item` (`facts.py:620`, called `:656`) keys on the normalized item *name* and keeps the lower-priority source.
3. **Re-enumerate indices** — final `item:{i}` ids are assigned by `enumerate(item_records)` insertion order (`facts.py:658-665`).

The bug lives in step 1: a grounded item dropped at `:651` never reaches the name-key reconciliation at step 2. Because `extract_text_facts` appends planner-inferred facts **after** grounded facts, an inferred `item:N:name` (`hermes_inferred`) is the "last-seen" at a shared index and overwrites the grounded `item:N:name` (`customer_text`). **Source priority — the thing that's supposed to protect customer facts — is bypassed**, because it only runs in step 2 on whatever survived step 1.

This collision is **latent in the already-merged slice 5a** (inferred facts appended at `item:0+` alongside grounded items); the slice-5b flat-price work only made it visible. It is dormant today (no `hermes_inferred` facts exist until the planner is operator-enabled).

A second, compounding defect: `_item_name_facts` (`facts.py:395`) mis-parses the request count-phrase into a junk item. For `"include 6 famous indo-chinese items"`, `add_item("6 famous indo-chinese items")` passes every guard (4 words ≤ the `>5` reject at `:434`, no `$`/`price`) and emits `item:0:name = "6 famous indo-chinese items"` (`:440`). The famous-items fallback hides this by clearing `item_name_facts` when it fires (`facts.py:567-568`); the planner path does not.

---

## 2. Exact collision examples (from Codex review + probe)

**Example A — inferred overwrites a customer-grounded standalone name (Codex r1).**
Input list (planner active): `[item:0:name "Paneer Tikka" (customer_text), item:0:name "Veg Manchurian" (hermes_inferred)]`.
At `:651`, `grouped[0]["name"]` is set to "Paneer Tikka" then **overwritten** by "Veg Manchurian". Reconciliation (`:656`) only sees the inferred name. Result: the customer's "Paneer Tikka" is **gone**; an inferred item took its index. (Inferred is appended last in `extract_text_facts`.)

**Example B — inferred overwrites a customer-grounded *paired* name (Codex r2).**
`_item_price_facts` (`facts.py:181`) also emits `item:N:name` (name+price pairs), held in `paired_item_price_facts` — NOT cleared by the slice-5b r1 attempt. So `[item:0:name "Samosa" + item:0:price "$5" (customer_text), item:0:name <inferred>]` collides identically at `:651`.

**Example C — junk count-phrase rendered as an item (probe, 2026-06-01).**
`extract_text_facts(..., "Flyer for Dragon Bowl, include 6 famous indo-chinese items, any item at $8.99")` →
`_item_name_facts` returns `[item:0:name "6 famous indo-chinese items"]` (junk). With the planner active, that junk either renders as an item or is masked by the Example-A overwrite — both wrong.

**Why source priority does NOT save us:** `merge_locked_facts` ranks `hermes_inferred` lowest (`:619-628`), but that ranking is applied in step 2 (`add_or_replace_item`), *after* step 1 (`:651`) has already discarded the grounded fact. The protection is structurally unreachable for same-index collisions.

---

## 3. Proposed data / merge invariant

> **Item-index ownership invariant.** Within any single fact list handed to `merge_locked_facts`, customer-grounded item facts (`customer_text` / `customer_confirmed` / `customer_profile` / `operator` / reference / `uploaded_asset` / `system`) occupy a contiguous low-index block `item:0 .. item:M`. Planner-inferred item facts (`hermes_inferred`) occupy strictly higher indices `item:M+1 ..`. No `(index, kind)` pair is shared between a grounded and an inferred item. Therefore step-1 grouping (`:651`) never drops a grounded item in favor of an inferred one, and grounded items keep their values and relative order through re-enumeration.

Corollaries the implementation must satisfy:
- **Grounded items are never mutated or dropped** by anything the planner adds. (Primary truth guarantee.)
- **Junk count-phrase item facts are removed** before merge, so they neither render nor consume a low index.
- **Flat price provenance stays customer-grounded:** a flat customer price ("any item at $8.99") paired onto an inferred item is emitted `source="customer_text"` (the price is the customer's stated fact); only the item *name* is `hermes_inferred`.
- **Mixed-count fill:** when the customer names K items and requests N total, the planner contributes at most `N − K` inferred items, so the project commits to exactly N.

This is enforced **entirely in `extract_text_facts`** (offset + drop + cap + price-pair). `merge_locked_facts` needs **no signature/behavior change** — the offset removes the precondition for the collision. (Alternative considered: rewrite `merge_locked_facts` to reconcile purely by name-key ignoring within-list index. Rejected — it changes shared merge semantics for every caller (name/price pairing depends on index today), far higher blast radius than the localized offset.)

---

## 4. The fix (all in `extract_text_facts`, all dormant-gated on `inferred_facts`)

1. **Drop junk count-phrase item names.** Add `_is_count_phrase_item(value)` → true for values matching `^\d{1,2}\s+.*\bitems?$` (e.g. "6 famous indo-chinese items"). When the planner produced `inferred_facts`, filter these out of `item_name_facts` (and any paired entry whose name is a count-phrase). Surgical — keeps real customer items (Example A's "Paneer Tikka", Example B's "Samosa"); drops only the request-phrase artifact.
2. **Offset inferred indices past grounded.** Compute `base = max grounded item index + 1` across `item_name_facts ∪ item_price_facts`; re-index `inferred_facts` (and their paired prices) to start at `base`. `base == 0` (pure-vague, no grounded items) ⇒ no-op.
3. **Remainder-fill cap (mixed case).** When a total count N is requested (reuse the count parser, §6) and K grounded items exist, cap inferred to the first `max(0, N − K)` items so the project commits to exactly N.
4. **Flat-price pairing.** For each (capped, offset) inferred item, if a flat `generic_price` was extracted from customer text, emit `item:{base+i}:price = generic_price` with `source="customer_text"`.

Dormant default (`inferred_facts == []`): every step is a no-op ⇒ byte-identical to today.

---

## 5. File touch list

| File | Change |
|---|---|
| `src/agents/flyer/facts.py` | `extract_text_facts`: junk-drop + offset + remainder-cap + flat-price pairing (all gated on `inferred_facts`). New helpers: `_is_count_phrase_item`, `_max_item_index`, `_reindex_item_facts`, and a count-parse reuse (extract `_requested_item_count` to a shared spot or import from `visual_qa`). |
| `tests/test_flyer_facts.py` | merge-invariant unit tests (grounded never dropped on same-index collision; re-enumeration keeps grounded first). |
| `tests/test_flyer_creative_planner.py` | end-to-end: junk-drop, offset coexistence, mixed 2+6=8, flat-price provenance, pure-vague unchanged, dormant byte-identity. |
| **NOT touched** | `merge_locked_facts` (no signature/logic change); `visual_qa.py` (count-QA already merged); `schemas.py`; any flag/config; any customer-facing copy/clarification path. |

Count-parse sharing: `_requested_item_count` currently lives in `visual_qa.py` (slice 5b). To reuse it in `facts.py` without a `visual_qa → facts` import cycle, move it to a small shared spot (e.g. define in `facts.py` and import from `visual_qa`, or a tiny `request_intent` helper). Decided at build time; no behavior change to the QA copy.

---

## 6. Tests — prove grounded facts cannot be overwritten

1. **Collision-direct (Example A):** list with grounded `item:0:name "Paneer Tikka" (customer_text)` + inferred items → after `extract_text_facts`, "Paneer Tikka" present, `source==customer_text`, value unchanged; inferred items present at higher indices; **count of customer items preserved**.
2. **Paired collision (Example B):** grounded `item:0:name "Samosa" + item:0:price "$5"` + inferred → "Samosa"/"$5" survive unchanged; inferred coexist.
3. **Mixed 2 + 6 = 8:** customer names 2, requests 8 total, planner offers ≥6 → exactly 8 distinct items; the 2 named are `customer_text` with original values; 6 are `hermes_inferred`.
4. **Junk-drop (Example C):** "include 6 famous indo-chinese items" → "6 famous indo-chinese items" is NOT among rendered item names; only planner items.
5. **Flat-price provenance:** planner item names `hermes_inferred`; their paired prices `customer_text` == the stated flat price.
6. **Pure-vague:** no grounded items ⇒ inferred at `item:0+` (offset 0), unchanged from slice-5a behavior.
7. **Dormant byte-identity:** planner off ⇒ `extract_text_facts` output identical to `origin/main` (no junk-drop, no offset, no flat-price).
8. **`merge_locked_facts` invariant unit test:** a single list with a grounded + a higher-priority inferred at the SAME index — assert the grounded (higher-priority) survives. (Locks the latent footgun as a regression even though the offset means production never feeds it a collision.)

---

## 7. One PR or split?

**Recommendation: ONE PR.** The entire change is confined to `extract_text_facts` and is dormant-gated on `inferred_facts` — there is no portion that alters shared behavior for current (planner-off) callers, so an "inert refactor" PR would have an empty behavior delta and create an artificial seam. `merge_locked_facts` is deliberately untouched, so there is no risky shared-logic refactor to isolate.

The one nuance worth a possible split is **test #8** (the `merge_locked_facts` same-index invariant lock): it documents/guards the latent footgun independent of the planner. It can ship in the same PR as a pure test addition (no logic change). If review prefers, it can be a tiny precursor test-only PR — but that is optional, not load-bearing.

So: **single PR** = `facts.py` behavior (junk-drop + offset + cap + flat-price) + the full test set above, all dormant. Codex 5-lens (truth-guard + rollout-safety blocking) before merge.

---

## 8. Explicit non-goals (scope guardrails, operator-set 2026-06-01)

- **No flag flip** and no category enablement.
- **No customer-facing clarification changes** (§8 stays parked).
- **No §7d all-flyer no-fabricated-hard-fact QA** (parked; if it ever applies to all flyers it needs its own rollout strategy + likely a warn/observe mode first).
- No `merge_locked_facts` signature/behavior change beyond an optional regression test.
- Remains DORMANT after merge.
