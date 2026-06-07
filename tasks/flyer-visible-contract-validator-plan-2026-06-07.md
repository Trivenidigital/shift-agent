# Flyer Studio — Visible-Contract Validator (post-render referee)

**Drift-check tag:** `extends-Hermes` — adds a deterministic referee on top of Hermes' existing vision read-back, at the single bare-render QA chokepoint. No Hermes primitive changed.

**Date:** 2026-06-07 · **Branch:** `feat/flyer-visible-contract-validator` (off `origin/main` d7d13cf)

## Problem (operator)
The integrated image model draws all text; sometimes the delivered image violates the brief, yet the pipeline logs `OUTCOME=sent`. Live + autonomous-probe evidence: `[rice]`/`[price]` placeholder slots, invented prices when none requested (`6.00`, `1/-`), `$39.99` rendered as `$99`, "Any item" → "Every item", internal asset id `B0002` visible, title ending in raw word "flyer", requested catering/delivery/payment badges missing.

## Hermes-first analysis (referee, not brain)
| Step | Owner |
|---|---|
| Request → locked facts / brief / visual_direction (creative meaning) | **[Hermes]** — the brain (already built: Slice 1/3) |
| Integrated poster render (model draws text) | **[Hermes]** — LLM gateway image model |
| Vision read-back / OCR of the rendered PNG → `extracted_text` | **[Hermes]** — vision extraction (`visual_qa._vision_text`, verified working on box: `openrouter/ocr_vision`) |
| **Deterministic verification that the visible text obeys the concrete brief** | **[net-new]** — the referee; an LLM must not adjudicate its own output |
| Fail-closed reply | **[Hermes]** — existing FAILCLOSED messages |

Net-new = **1 step** (the referee). drift-tag `extends-Hermes`.

## §9a runtime-state finding (changes the design)
- **`FLYER_BARE_SKIP_VISUAL_QA=1` on the box** → `bare_render.run_visual_qa` short-circuits to `(True, ["visual_qa_disabled"])`. There is **no post-render check running today** — the misses aren't only *gaps in a gate*, the gate is **off**. (Broad QA was likely disabled to stop provider-unavailable holds; the operator also said "do not re-enable broad subjective QA".)
- **Vision read-back works now** (verified). So a new gate that re-reads the image is viable.

**Consequence:** the visible-contract gate must run **independently** of `FLYER_BARE_SKIP_VISUAL_QA`, and must run **only the concrete-contract checks** (not the broad subjective QA that stays disabled). This matches the operator's "targeted post-render check for concrete facts/copy only".

## Design (one named validator, one chokepoint)
**New module** `src/agents/flyer/visible_contract.py` → flat `flyer_visible_contract.py`:
```
validate_visible_contract(project: FlyerProject, extracted_text: str, normalized: str) -> list[str]
```
Driven by `project.locked_facts` + `project.raw_request` (already on the project). Pure function, returns blockers. Reuses deployed helpers from `visual_qa.py` (`_price_value_present_in`, `_text_value_present_in`, `_normalize_text_for_match`, `_PRICE_AMOUNT_RE`, `OPERATIONAL_CLAIM_PATTERNS`).

**Wiring (single chokepoint)** — `bare_render.run_visual_qa(image_bytes, project)` (covers render_grounded / render_reroll / render_iteration), BEFORE the skip short-circuit:
```python
if _visible_contract_armed(project):                 # FLYER_VISIBLE_CONTRACT=1 + allowlist on project.customer_phone
    ok, blockers = _run_visible_contract_gate(image_bytes, project)   # _vision_text + validate_visible_contract
    if not ok:
        return (False, blockers)                     # -> caller returns FAILCLOSED -> existing honest message
if _skip_visual_qa_enabled():
    return (True, ["visual_qa_disabled"])            # broad QA stays disabled (unchanged)
... existing full QA ...
```
Fail-closed reuses the existing FAILCLOSED messages (script lines 145/179/239 — "I couldn't render your flyer with all the correct details just now (I'd rather not show wrong info)…"), which already do NOT claim "here's your flyer". New contract blockers default to severity **block** (`classify_qa_severity` line 1143: any unrecognized blocker → block); registered in `_BLOCK_TIER_PATTERNS` for clean labeling.

## The 7 consolidated checks (→ detection)
1. **Garbled/placeholder slots** — `\[[^\]]{1,40}\]` (any bracket token: `[rice]`, `[price]`, `[Price]`) + `\b(PENDING|TBD|TBA)\b`.
2. **Requested prices visible exactly** — for each locked fact carrying a price amount (`item:N:price`, `offer:N`, `offer_price`, `pricing_structure`), every price token must appear via `_price_value_present_in` (cents-match, currency-aware). Catches `$39.99`→`$99`.
3. **Pricing qualifier preserved** — `pricing_structure` "any item …" must not surface as "every/all item …". Catches "Any item" → "Every item".
4. **No invented prices when none locked** — if NO locked price amount exists, reject any price-like token (`$\d`, `\d+\.\d{2}`, `\d+\s*/-`). Catches `6.00`, `1/-`. (Phones/ZIPs/times excluded by shape.)
5. **Requested badges/notes visible** — inverse of `_unrequested_operational_claim_blockers`: for each `OPERATIONAL_CLAIM_PATTERNS` claim the source requested, require it visible. Catches missing catering/delivery/payment badges.
6. **Internal asset IDs** — `\bB\d{4}\b` (brand asset) + project/menu ids. Catches `B0002`.
7. **Raw medium-word title leak** — standalone `flyer|flier|poster` visible → block. Catches "daily thali specials flyer".

## Regressions (new `tests/test_flyer_visible_contract.py`, in-process, no real vision call)
The 8 operator cases + the 4 must-still-pass creation cases (graduation, breakfast, combo, thali) stay green.

## Flag / rollout / deploy
- `FLYER_VISIBLE_CONTRACT` (default `0`) + `FLYER_VISIBLE_CONTRACT_ALLOWLIST` scoped to `+17329837841` (consistent with Slice 1/3). Global behavior unchanged until promoted.
- Deploy flat (`flyer_visible_contract.py` new; `flyer_visual_qa.py` if `_BLOCK_TIER_PATTERNS` touched; `flyer_bare_render.py` for the wiring) — **no gateway restart** (per-request subprocess modules). Reversible backup. flyer-deploy-smoke.
- Codex review before merge; retest from +17329837841 with the screenshot sequence + autonomous probe corpus; report **both** log outcome and visible output.

## Open decision (needs operator)
**Vision-provider-unavailable behavior** (when the read-back can't return text): **send-anyway-with-log** (preserves today's flow; gate only blocks on positive violations; no regression when vision is flaky) vs **hold/fail-closed** (strictest; but re-introduces holds when the provider blips — the very thing the broad-QA skip avoided).

## Out of scope (noted follow-ups, NOT this PR)
- **Prompt-side no-price fix** — the `[rice]`/invented-price class has a prompt-side root cause (empty/placeholder price slots interpolated into the prompt). Omitting empty price slots would let no-price requests *render correctly* instead of fail-closing. Recommended as the companion fix once the gate proves the failure class.
- Re-enabling the broad subjective QA (kept disabled per operator).
