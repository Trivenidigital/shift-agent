# CD v2 Composition Pass — Phase 1: Message-First Poster (A) — Implementation Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Prove a message-first poster makes the **message visually DOMINANT** (not merely present) and **materially outperforms** the current title-first layout on F0187. Composition-only; flag-gated; flag-off byte-identical.

**Architecture:** A new deterministic `select_poster_archetype()` component (carrier-build time) writes `poster_archetype` into the carried `creative_direction` dict; the premium overlay renders the **A (message_first)** template when `poster_archetype == "message_first"`, else today's layout (B/C fall back to today's layout this phase). No brain/firewall/carrier-mechanism/QA/routing/extraction/deterministic-first change.

**Branches from** `feat/flyer-creative-director-v2` (current HEAD). Test: `PYTHONPATH="src;src/platform" python -m pytest <path> -v`. Windows; specific `git add` (never `-A`; ~30 phantom-deleted long-path docs must never stage).

**Drift-check tag:** `extends-Hermes` (composition-only). Reuses existing brain fields + the premium overlay.

---

## THE EXPLICIT TYPE HIERARCHY (the success contract)

**Current F0187 (title-first):** Brand emblem (dominant top) → `campaign_title` "Weekend Specials" (LARGEST, Playfair ~`width*0.072`) → narrative (small gold eyebrow) → hero food → offer seal → dot-leader menu → footer.

**Target A (message-first):** `campaign_narrative` (LARGEST) → `marketing_hook` (SECOND) → hero food → menu → Brand (demoted).

Concrete element ranking the A template MUST produce (deterministically assertable):
| Element | Role in A | Type scale (of canvas width) | vs current |
|---|---|---|---|
| `campaign_narrative` | **LARGEST** — the headline, top third, multi-line, Playfair-Black | `~0.072–0.082` | was a small eyebrow → now the hero |
| `marketing_hook` | **SECOND largest** — strong sub-headline / offer line | `~0.044–0.050` | was buried/seal-only |
| `campaign_title` | **DEMOTED to kicker** — small eyebrow above the narrative | `~0.026` | was LARGEST → now smallest headline-zone text |
| Brand lockup | **DEMOTED** — small top-corner or footer (no dominant emblem ring) | `~0.024` | was dominant top |
| Hero food | background (unchanged) | — | — |
| Menu / footer | below (unchanged) | — | — |
| Offer seal | retained if shared price; sized by `offer_priority` (no escalation this phase) | existing | — |

**Invariant:** `narrative_px > hook_px > title_px` AND brand-lockup is small (no dominant emblem). The message is literally the largest text. (Exact ratios are starting points; the implementer tunes within the bands so it fits — but the ORDERING is non-negotiable and unit-asserted.)

---

## Task 1: `select_poster_archetype()` + carrier wiring

**Files:** Create `src/agents/flyer/flyer_poster_archetype.py`; Modify `src/agents/flyer/render.py` (`_populate_creative_direction_v2`); Test `tests/test_flyer_poster_archetype.py`.

- [ ] **Step 1 — failing test:** `select_poster_archetype(request_intent, offer_priority="medium") -> str` returns: `menu`/`new`/`source_edit` → `"message_first"`; `combo_offer` → `"offer_first"`; `event` → `"event_first"`; unknown/empty → `"message_first"` (safe default). Pure, never raises. (Phase-1 mapping only; `offer_priority` accepted but unused for selection this phase.)
- [ ] **Step 2 — run → fail. Step 3 — implement** the pure mapping in the new module.
- [ ] **Step 4 — wire:** in `_populate_creative_direction_v2` (render.py), after resolving the direction, compute `archetype = select_poster_archetype(brief.request_intent, resolved.offer_priority)` and add `poster_archetype=archetype` to the `creative_direction` dict written to the carrier. (Flag-off path unchanged — block only runs under `_creative_director_v2_enabled`.) Test: with the flag on, the carrier dict carries `poster_archetype`; flag off → carrier None (unchanged).
- [ ] **Step 5 — run → pass. Commit** `feat(flyer): select_poster_archetype router + carrier wiring (Phase 1)`.

## Task 2: A (message_first) overlay template

**Files:** Modify `src/agents/flyer/premium_overlay.py`; Test `tests/test_flyer_cdv2_overlay.py` (append).

- [ ] **Step 1 — read** `premium_overlay.py`: `render_premium_overlay`, `plan_premium_layout`, the brand/kicker draw (`~:499-552`), the title block (`~:591-657`), the narrative kicker (B2.5), the offer seal (`~:659-730`), the fit ladder + required-fact ledger (`~:434-452`). Identify where to branch on `poster_archetype`.
- [ ] **Step 2 — failing tests** (`tests/test_flyer_cdv2_overlay.py`):
  - **Type-hierarchy (the contract):** render a `message_first` project (creative_direction has narrative + hook + `poster_archetype="message_first"`); assert the chosen font sizes satisfy `narrative_px > hook_px > title_px` and the brand lockup px is small (≤ title_px). Prefer asserting on the layout plan's computed sizes (expose them) over pixel-reading.
  - **Demotion:** the dominant emblem ring is NOT drawn in message_first (brand is a small lockup); `campaign_title` is rendered as a small kicker, not the headline.
  - **Byte-identical guard:** `poster_archetype` absent / `"offer_first"` / `"event_first"` / `creative_direction=None` → output byte-identical to today (the #1 regression assert — B/C fall back to today's layout).
  - **Fit + ledger:** a pathological long narrative still fits or degrades (narrative shrinks/drops before any required fact); the required-fact ledger still verifies every locked fact; over-emphasis never overflows.
  Run → fail.
- [ ] **Step 3 — implement:** when `poster_archetype == "message_first"`, compose the A hierarchy (narrative as the Playfair-Black headline at the top-third with the largest scale; hook as the second sub-headline; title as a small kicker; brand demoted to a small lockup; hero/menu/footer retained; seal retained if shared price). All other archetypes/None → existing layout unchanged. Preserve the fit ladder + ledger; narrative/hook best-effort (dropped before required facts).
- [ ] **Step 4 — run → pass.** `PYTHONPATH="src;src/platform" python -m pytest tests/ -k "cdv2 or premium_overlay or poster_archetype" -q` → 0 failed (except the known pre-existing contextvar flake). **Commit** `feat(flyer): message-first (A) overlay template — narrative dominant, title/brand demoted (flag-off byte-identical)`.

## Task 3: Validation against the F0187 baseline (operator-gated render)

- [ ] Codex review of the Phase-1 diff (Tasks 1+2) → fix BLOCKER/MAJOR → re-Codex until CLEAN.
- [ ] **Real-render validation (scratch full-render, no deploy/activation, capped spend):** render F0187 with the flag on + `poster_archetype=message_first` → pull the image. Compare to the current F0187 baseline (`.cdv2-baseline-F0187.png`).
- [ ] **Success criteria (measured against F0187 baseline):**
  1. **Structural (unit-asserted, already in Task 2):** `narrative_px > hook_px > title_px`; brand demoted. The message is the largest text — provable, not subjective.
  2. **Visual (operator's eye = arbiter):** on the real F0187 A-render, the narrative reads FIRST in ~2s and is visually dominant over the title; brand recedes — measured against the bucket-biryani reference. This is the "materially outperforms title-first" bar.
  3. **Oracle (directional only):** 8-axis before/after delta (acknowledging `message_clarity`'s ceiling at 9 — not the arbiter; reported for completeness).
  4. **Safety:** dangerous-leak = 0; QA verdict unchanged; all locked facts present.
- [ ] Bring the before/after F0187 images + the structural assertion result + oracle delta + a recommendation (does message-first materially outperform → proceed to Phase 2 B, or iterate A's composition).

---

## Boundaries
Composition + the archetype selector only. No brain/firewall/carrier-mechanism/QA/dispatcher-routing/extraction/deterministic-first change. Flag-gated (`FLYER_CREATIVE_DIRECTOR_V2`), scoped +17329837841, flag-off byte-identical. B/C = today's layout this phase (no half-built archetypes). No `offer_priority` escalation this phase. No merge/deploy without operator approval.

## Self-review
- Coverage: router (T1) ↔ A template (T2) ↔ validation (T3) map to the design §3/§4/§6/§7. Phase-1 mapping only; B/C fallback.
- Success is concrete: the type-hierarchy invariant is unit-asserted (`narrative_px > hook_px > title_px` + brand demoted); the "dominant/materially-outperforms" judgment is the operator's eye on a real F0187 render vs baseline.
- No placeholders; type-scale bands given; exact ratios tuned by the implementer within the bands, ordering non-negotiable.
