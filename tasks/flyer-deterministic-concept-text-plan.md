# Flyer Studio Priority-1 — deterministic exact-text at the concept stage

**Drift-check tag:** `extends-Hermes`

The deterministic text-composition layer already exists in `src/agents/flyer/render.py`.
This work does NOT build a new renderer or parallel substrate — it moves an existing
deterministic overlay earlier in the pipeline (and, slice 2, asks the image model for a
background instead of text). Generation, send, audit, identity, routing, approval all
stay on Hermes substrate.

## Hermes-first capability checklist (per-step)

| # | Step | Tag | Owner / why |
|---|---|---|---|
| 1 | WhatsApp inbound "create a flyer…" ingested | `[Hermes]` | WhatsApp inbound media |
| 2 | Identify sender, dispatch to Flyer intake | `[Hermes]` | sender_role + skill dispatch |
| 3 | Extract structured facts (title/items/prices/contact) | `[Hermes]` | vision/text extraction + structured output |
| 4 | Create project + reserve usage in state | `[Hermes]` | per-VPS JSON state |
| 5 | `render_concept_previews` → image model | `[Hermes]` | LLM gateway (image), swappable provider |
| 6 | Model generates flyer image (background + attempted text) | `[Hermes]` | LLM gateway image |
| 7 | `apply_exact_identity_overlay` (business/contact banners) | `[net-new]` | image-text compositing — already written |
| 8 | Visual QA / `inspect_rendered_asset` on the concept | `[Hermes]` | vision extraction (fact-check logic is Flyer code) |
| 9 | **Deterministic title/offer/items/prices overlay AT CONCEPT** | `[net-new]` | **THE fix** — model can't render exact text (100% prod fail); reuses overlay primitives |
| 10 | Send concept previews to customer | `[Hermes]` | multi-channel response |
| 11 | Customer approves | `[Hermes]` | approval workflow |
| 12 | `render_final_package` deterministic overlay | `[net-new]` | already exists (`apply_critical_text_overlay`) |
| 13 | Audit each step | `[Hermes]` | decisions.log via log-decision-direct |

**Net-new:** only #9 is new work in this plan (#7, #12 already exist). 1 of 13 genuinely
net-new → no red flag; this is a call-site wiring change, not new infrastructure.

## Drift-rule self-checks
- ✅ Read `src/agents/flyer/render.py` (`render_concept_previews`:2348, `_render_model`:2334, `apply_critical_text_overlay`:1357, `apply_exact_identity_overlay`:1996, `_menu_overlay_payload`:735) before drafting — confirmed the deterministic overlay exists and is applied at final-package but only identity-overlay at concept.
- ✅ Read `src/agents/flyer/render.py` `_exact_identity_overlay_payload`:1961 before drafting — confirmed concept overlay carries only business/location/contact/schedule, NOT campaign_title/offer/items (the QA-failing facts).
- ⬜ Will Read `src/agents/flyer/visual_qa.py` + `inspect_rendered_asset` and `tests/test_flyer_renderer.py` before writing slice-1 code.

## The gap (origin/main 0126d4d)
Production is ~100% `visual_qa_failed` because the **concept preview is QA'd on
model-rendered text**, while the deterministic full-text overlay only runs at the
final-package stage that approval never reaches:
- `_render_model` (render.py:2334) → model image → only `apply_exact_identity_overlay` (render.py:2345).
- `_exact_identity_overlay_payload` (render.py:1961) carries only business/location/contact/schedule.
- Model still renders title/offer/items → garbled → QA "missing required visible fact" (live: F0113=`2.png`; F0103/F0108–F0113 all `visual_qa_failed`).
- Full menu/items overlay `apply_critical_text_overlay` (render.py:1357) only invoked from `render_final_package` (render.py:2446,2459).

Evidence: `project_flyer_generation_failure_rootcause` memory + main-vps `decisions.log`.

## Scope: NEW-flyer concept generation only
Source-edit previews (`render_source_edit_preview`, render.py:2362) are a separate
integrity-preserving lane needing `OPENAI_API_KEY` (operator-gated) — OUT of scope.

## Slice plan (smallest safe; TDD; Codex-reviewed each)
1. **Slice 1 — deterministic exact-text at concept QA.** New-flyer concept carries
   campaign_title/offer/menu-items/prices as deterministic overlay text (reuse existing
   overlay drawing), so QA runs on deterministic text → "missing/garbled fact" class
   becomes structurally impossible. Tests: render concept over synthetic background;
   assert every required fact present + manifest validates.
2. **Slice 2 — reserved-zone prompt (layout contract).** `_image_prompt` asks for a
   decorative background with clean reserved zones (no menu text). Visual refinement;
   real-model smoke is credential/operator-gated.
3. **Slice 3 — composite QA.** Shift QA toward legibility/contrast/overflow on the
   deterministic composite (facts guaranteed present by construction).

## Guardrails
- No new routing/identity/approval/audit/state/messaging substrate.
- Customer copy stays outcome-only; operational detail in audit/state.
- Real-model visual quality + source-edit lane are credential-gated → not blocked on here.
