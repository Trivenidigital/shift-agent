# Plan — Hermes skill-driven creative brief drives the INTEGRATED poster renderer

**Drift-check tag:** `extends-Hermes` — reuses the existing `flyer_generation` Hermes skill + `FlyerBrief` model + brief firewall + LLM gateway, and threads the skill's *occasion-aware visual direction* into the integrated renderer's prompt (replacing ad-hoc Python occasion logic). It explicitly does **not** promote CD+overlay and does **not** add Python occasion keyword lists.

**Operator directive (2026-06-07):** "The integrated renderer must be driven by Hermes skill output, not ad hoc Python prompt logic. Do not revive CD+overlay as the first move — the integrated model is producing high-quality food flyers and should remain the draft renderer. No Python holiday keyword lists. No prompt sprawl. No CD+overlay promotion until golden-set evidence says it is ready." End goal: *vague request → Hermes interprets intent/theme → integrated model drafts a useful flyer → owner can re-roll or revise naturally.*

## Required corrections (operator conditional go-ahead, 2026-06-07) — binding

The skill is an **advisory art director** for the integrated renderer, NOT a truth firewall and NOT a new fail-closed surface. The biggest trap: reusing `build_flyer_brief()` unchanged would bind this to the **old CD flag + CD fail-closed contract**. So:

1. **Decouple from `FLYER_CREATIVE_DIRECTOR_ENABLED`.** `build_flyer_brief()` is CD-gated and its contract says invalid/unavailable must NOT fall back — that belongs to CD+overlay/firewall mode. Do **not** call it for scene guidance. Add a **new gate `FLYER_SKILL_DRIVEN_SCENE`** (+ allowlist). A separate advisory entrypoint reads the same `flyer_generation` SKILL via the gateway but with advisory semantics.
2. **No CD fail-closed semantics for scene guidance.** If the skill is disabled/unavailable/invalid OR can't produce a valid `visual_direction`, **keep today's integrated Python scene** — never fail closed. This feature improves vague-request quality; it must not add a new failure surface.
3. **`visual_direction` ONLY, not `background_brief`.** `background_brief` is the textless CD background — not used here. The integrated renderer gets a scene/theme block derived from `theme_family` + `palette` + `motifs` + `visual_subjects`, plus the existing Python-injected controlled facts (`_poster_copy_block`).
4. **Allowlist + scoped rollout.** Flag + allowlist (the test sender) before global. This is creative behavior, not a firewall.
5. **Tests must prove** (see Test plan below): graduation prompt includes graduation visual language (caps/diplomas/stage/celebration/school cues); graduation prompt avoids family-dinner/food-table unless food is the subject; breakfast/combo/Indo-Chinese keep food/product-closeup composition; skill unavailable/invalid → today's integrated scene (fallback); CD+overlay remains off and untouched.

---

## Hermes-first analysis (what already exists vs net-new)

| Capability | Status on origin/main | Decision |
|---|---|---|
| Hermes skill that returns a structured creative brief | **EXISTS** — `flyer_generation/SKILL.md` invoked via gateway by `flyer_context_builder.build_flyer_brief()` → returns `FlyerBrief` (`flyer_brief.py:117`) | **use it** |
| Occasion-aware `visual_direction` (theme_family / palette / motifs / visual_subjects) | **EXISTS** — `FlyerBrief.visual_direction` (`flyer_brief.py:39`); SKILL.md already has a Memorial-Day worked example inferring "patriotic Americana" | **use it** (add graduation-class rules as SKILL prose, not Python) |
| `request_intent` enum | **EXISTS** — `"combo_offer" \| "menu" \| "event" \| "source_edit" \| "new"` | **extend** → add `occasion_event \| reroll \| specific_revision \| style_reuse` |
| Facts-by-reference (`must_show`) + `must_not_add` | **EXISTS** — `fact_refs: list[FactRef]` (every locked fact must be referenced) + `must_not_add` | **use it** |
| Brief truth-firewall (no commercial values in free-text; fact-ref provenance) | **EXISTS** — validated in `build_flyer_brief` / `flyer_brief.py` invariants | **use it** |
| Skill → brief → render wiring | **EXISTS but flag-OFF and pointed at CD+OVERLAY** — `bare_render._render_creative_director_grounded` → `build_flyer_brief` → `_render_creative_director(brief.background_brief)` (textless bg + Pillow overlay) | **repurpose** the brief to drive the integrated renderer instead |
| Integrated renderer prompt | **ad-hoc Python** — `render._image_prompt` (`render.py:1582`) → `_campaign_scene_block_for_project` (`render.py:1521`) → `select_campaign_scene` with hardcoded occasion sets (`campaign_scene_prompts.py:84` `_FAMILY_DISCOVERY_SIGNALS`/`_HUMAN_BILLBOARD_SIGNALS`, `render.py:95` `FOOD_CATEGORY_TERMS`) | **net-new seam**: replace with skill `visual_direction` |
| Render + hard-fact vision QA + audit + identity/state | **EXISTS** (`render_concept_previews`, `run_visual_qa`, `decisions.log`/`send.log`, `resolve_customer`/`_load_session`) | **use it unchanged** |

**Net-new is small and is a *seam + extension*, not a new skill:** (1) thread `visual_direction` into `_image_prompt`; (2) extend `request_intent` + SKILL prose; (3) brief-field validation on the integrated path; (4) routing reachability. ~4 net-new of 10 steps.

---

## Root cause this fixes (last-night failures)

The integrated model is excellent at **food/menu** flyers (its "food spread" composition is perfect — see the breakfast flyer). For **occasion themes** it reuses that same food-table composition and adds token caps → the graduation flyer reads as a **family dinner** ("this is not a family reunion"). The reason: the integrated prompt's scene/theme is chosen by **hardcoded Python keyword sets** (`_FAMILY_DISCOVERY_SIGNALS`, `FOOD_CATEGORY_TERMS`) that have no concept of "graduation visual language." The skill's `visual_direction` does — it's just not wired to the integrated renderer.

---

## Architecture (the seam)

```
customer request ──► [Python] resolve facts + session + prev-flyer summary
                      │
                      ▼  (only if FLYER_SKILL_DRIVEN_SCENE + sender allowlisted)
            [Hermes flyer_generation skill]  ◄── raw request + facts + context
                      │  advise_scene_direction() reads ONLY visual_direction
                      ▼  (theme_family/palette/motifs/visual_subjects)
            [Python] parse visual_direction → VisualDirection | None
                      │     (ANY problem → None → today's Python scene; NEVER fail-closed)
                      ▼
            [Python] _image_prompt = scene block from visual_direction (SCENE/THEME)
                      │              + _poster_copy_block (EXACT facts, Python-injected)
                      │              + layout    ◄── integrated model still draws all text
                      ▼
            [integrated model] → render → hard-fact vision QA (unchanged) → SEND / fail-closed → audit
```

**Invariants:** (a) facts stay **Python-injected by reference** — the skill's `visual_direction` carries NO business names/prices/dates (it's theme/palette/motifs/subjects only); Python supplies the truth via `_poster_copy_block`. (b) The skill is **advisory** — its absence or failure silently falls back to today's scene; it is never a reason a render fails. (c) `background_brief` and the CD+overlay path are untouched.

**The single seam (Slice 1):** `render.py:1640` — when an armed caller passes a `scene_direction: VisualDirection`, build the scene block from it (`_scene_block_from_visual_direction`) instead of `_campaign_scene_block_for_project`; otherwise today's Python path. Integrated model unchanged; QA unchanged; **no overlay, no CD promotion, no new fail-closed surface.**

---

## Slice plan (each: flag-gated, Codex-reviewed, golden-set-measured, reversible)

**Slice 1 — advisory `visual_direction` → integrated `_image_prompt` (the quality fix).**
- **New advisory entrypoint** (e.g. `flyer_context_builder.advise_scene_direction(raw_request, locked_facts, business_profile) -> VisualDirection | None`) that reuses the gateway-call + `flyer_generation` SKILL body, but is **NOT** `build_flyer_brief()` and is **NOT** CD-gated. It returns a `VisualDirection` on success and **`None` on ANY problem** (gateway off/error/timeout, parse fail, missing/empty `visual_direction`). It never raises and never fail-closes. It reads ONLY `visual_direction` from the brief (ignores `background_brief`, `offer_groups`, etc.).
- **New gate `FLYER_SKILL_DRIVEN_SCENE` (default off) + allowlist `FLYER_SKILL_DRIVEN_SCENE_ALLOWLIST`** (start with the test sender). Caller (`render_grounded`, integrated path only) checks flag+allowlist; if not armed → today's Python scene unchanged.
- When armed AND `advise_scene_direction()` returns a `VisualDirection`: `_image_prompt` composes the scene block via new `render._scene_block_from_visual_direction(vd)` (from theme_family/palette/motifs/visual_subjects) instead of `_campaign_scene_block_for_project`. Facts still via `_poster_copy_block` (unchanged). Threaded as an optional `scene_direction` param down `_generate_poster → render_concept_previews → _render_model → _image_prompt`.
- **Fallback (no fail-closed):** flag off / not allowlisted / `advise_scene_direction()` returns None → `_campaign_scene_block_for_project` (today's path). No change to `_poster_copy_block`, QA, overlay, or CD. `FLYER_CREATIVE_DIRECTOR_ENABLED` untouched and irrelevant to this path.
- Golden set: graduation renders graduation-themed (not family dinner); food/combo/breakfast unchanged-or-better; measured on `tests/flyer_oracle` before any flag-on beyond the allowlist.

**Slice 2 — extend `request_intent` + SKILL occasion rules (no Python keywords).**
- Add `occasion_event | reroll | specific_revision | style_reuse` to `FlyerRequestIntent`.
- SKILL.md: add the *rule* "infer occasion visual language (graduation → caps/diplomas/stage/balloons/school-celebration; NOT family-dinner/food-table composition unless food is the actual subject); never invent commercial facts." As a skill example/rule — **not** Python.
- Begin retiring the hardcoded occasion sets (`_FAMILY_DISCOVERY_SIGNALS` etc.) once Slice 1 is golden-validated (kept as fallback until then).

**Slice 3 — routing reachability (the iteration fixes).**
- Re-roll reachable even when phrased with "design"/"change": the cf-router `--revision` branch (or `render_grounded`) attempts a **pure re-roll** before the generic "resend full details" fallback.
- `specific_revision` (e.g. "make background graduation") → a clear "here's what I can change" response (or the revision-capable path), not "resend full details."
- `style_reuse` ("use this design/theme for weekend breakfast") → its own skill intent (reuse prior visual_direction for a new fact set), not generic revision.

---

## What stays Python (the boundary)
Resolve locked facts/session; call the skill; **validate** fact-references/source-spans + must_not_add (firewall); inject exact facts (`_poster_copy_block`); invoke the integrated renderer; hard-fact QA; audit/send/fail-closed. Python never authors creative theme; the skill never authors facts.

## Safety / rollout
- Every slice **flag-gated** + fail-closed to today's path; reversible flat deploy.
- **No CD+overlay promotion** (integrated model stays the draft renderer).
- **No Python occasion keyword lists** added; existing ones retired only after golden-set evidence.
- Golden-set (`tests/flyer_oracle`) gates each flag-on; owner-approval still gates broadcast.
- Codex review before merge; deploy flat after merge; CD flag stays off.

## Test plan (Slice 1 — binding, per operator correction 5)
1. `_scene_block_from_visual_direction(graduation_vd)` → prompt includes graduation visual language: caps, diplomas, stage/celebration décor, school/graduation cues.
2. Graduation scene block **avoids** family-dinner / food-table composition (asserts those phrases absent) unless food is the stated subject.
3. Food intents (breakfast / combo / Indo-Chinese `visual_direction`, or flag-off) → keep the food/product-closeup composition (today's `_campaign_scene_block_for_project` path or a food-subject scene).
4. `advise_scene_direction()` returns `None` on gateway-disabled / error / unparseable / empty `visual_direction` → `_image_prompt` uses today's integrated scene (fallback, no raise, no fail-closed).
5. Flag off / sender not allowlisted → today's path byte-for-byte; `FLYER_CREATIVE_DIRECTOR_ENABLED` and the CD+overlay path remain off and untouched (no `_render_creative_director*` call).
6. `_image_prompt` with a `scene_direction` still injects the exact controlled facts via `_poster_copy_block` (facts unchanged; no commercial values sourced from the skill).

## Files to touch (Slice 1)
`src/agents/flyer/render.py` (`_scene_block_from_visual_direction` + `_image_prompt`/`_render_model`/`render_concept_previews` `scene_direction` param), `src/agents/flyer/bare_render.py` (flag+allowlist gate on the integrated path; call `advise_scene_direction`; thread into `_generate_poster`), `src/agents/flyer/flyer_context_builder.py` (new advisory `advise_scene_direction` reusing the gateway+SKILL body, NOT `build_flyer_brief`, NOT CD-gated), tests (`tests/test_flyer_renderer.py`, `tests/flyer_oracle/`). **No edits to** `build_flyer_brief` semantics, the CD path, the firewall, or `FLYER_CREATIVE_DIRECTOR_ENABLED`.
