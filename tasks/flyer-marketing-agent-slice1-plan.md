# Flyer Marketing Agent — Slice-1 Implementation Plan (2026-06-05)

**Drift-check tag:** extends-Hermes
**Status:** overnight autonomous build; each PR Codex-reviewed + tested; **everything dormant (flag-OFF / shadow)** so the live bare-mode path is untouched. Live-flip held for the operator.
**Builds on:** `tasks/flyer-marketing-agent-design-2026-06-05.md` (Rev 3.1).

## Resolved decisions (design §11)

1. **Spine storage** — extend the existing flyer customer/profile store with an **additive, dormant** `commerce_seam` (catalog item identity + CTA / order-link / price-as-commerce-object). No new store, no DB; `safe_io` JSON. Backed by `src/platform/commerce/` primitives only in slice 3.
2. **`FlyerBrief` schema** (Pydantic, `extra="forbid"`): `request_intent` (combo_offer|menu|event|source_edit|new), `offer_structure`, `visual_direction{theme_family,palette[],motifs[],visual_subjects[]}`, `layout_strategy`, `grouping`, `must_not_add[]`, `background_brief` (str), `fact_refs[FactRef]` where `FactRef = {fact_id?: str, raw_span?: str, provenance: locked|customer_text}`. **No raw commercial values.**
3. **Components** (operator-specified):
   - `flyer_generation/SKILL.md` = **Creative Director** (all creative judgment: intent/occasion/theme/layout/structure/what-not-to-add).
   - `flyer_context_builder` = **callable tool/plugin** (Python) that invokes the skill via the Hermes gateway (structured-LLM) and returns **one `FlyerBrief`**.
   - `FlyerBriefValidator` = **deterministic Python**: computes the required-fact set from locked facts ONLY (never the model-authored `request_intent`); validates every `FactRef` maps to a `FlyerLockedFact` ID **or** a verified `raw_span` of the customer request; enforces `must_not_add`; **materializes** validated spans → `FlyerLockedFact(source="customer_text")`. Overlay renders `required_fact_ids ∩ locked_facts` only.
   - **Stay Python:** identity/sender resolution, state transitions, audit, delivery, retries, danger gates, deterministic overlay.
4. **Flags** — `FLYER_CREATIVE_DIRECTOR_ENABLED` (default `0`); `FLYER_INTENT_ROUTING_SHADOW` (default shadow). **Flag-OFF = byte-identical current behavior** (gate every new branch).
5. **Send-rate metric** — numerator = golden cases **not** danger-blocked AND passing truth+commercial gates; denominator = all cases; danger-blocks and dense-layout failures accounted **separately**; promote threshold ≥ 0.90 send-rate with **0** truth-gate failures.

## Hermes-first analysis

| Step | Tag | Note |
|---|---|---|
| Creative judgment (intent/occasion/theme/layout) | `[Hermes]` | `flyer_generation` skill — the brain; replaces `_image_prompt`/`campaign_scene_prompts.py` |
| `flyer_context_builder` gateway call → FlyerBrief | `[Hermes]` | plugin-backed structured-LLM via the existing gateway/OpenRouter seam |
| `FlyerBriefValidator` (required-fact authority, ID/span, must_not_add) | `[net-new]` | deterministic Python firewall; small |
| Spine `commerce_seam` fields | `[net-new]` | additive Pydantic; dormant |
| Deterministic overlay (`required ∩ locked`) | `[net-new]` | consolidate existing `apply_critical_text_overlay` |
| QA danger-gate remap | `[net-new]` | rewrite severity/action in `generate-flyer-concepts` |
| Oracle send-rate | `[net-new]` | on top of existing `tests/flyer_oracle` gates |
| Audit/delivery/identity/danger gates | `[Hermes]` | reuse deployed substrate |

External ecosystem: per design §8 — no flyer-specific public Hermes skill; build/extend in-tree `flyer_generation`. Hermes `prompt_builder.py` is core assembly (not forked).

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/skills/flyer_generation/SKILL.md` — integrated-poster guidance to rewrite to Creative Director.
- ✅ Read `src/agents/flyer/scripts/generate-flyer-concepts` + `src/agents/flyer/render.py` (`_image_prompt`) — Python prompt path + QA block (`run_visual_qa`→`manual_edit_required`) to migrate behind the flag.
- ✅ Read `src/platform/schemas.py` (`FlyerCustomerStore`, `FlyerLockedFact`) + `tests/flyer_oracle/oracle.py` (`GateResult` truth|delivery|commercial) before spec'ing the seam + the send-rate.

## PR sequence (flag-OFF, tested, Codex-reviewed, reversible)

- **PR1 — spine seam:** additive dormant `commerce_seam` on the flyer store/profile + tests. No behavior change.
- **PR2 — Creative Director core:** `FlyerBrief` + `FlyerBriefValidator` + `flyer_context_builder` + rewrite `flyer_generation/SKILL.md`; all behind `FLYER_CREATIVE_DIRECTOR_ENABLED`. Unit tests + oracle cases.
- **PR3 — wiring:** `generate-flyer-concepts`/cf-router delegate to `flyer_context_builder` behind the flag; overlay renders `required ∩ locked`; flag-OFF path unchanged.
- **PR4 — QA danger-gate:** remap severity/action so only danger blocks; demote `visual_qa_failed`; leaked-text guard. Behind the flag.
- **PR5 — oracle send-rate metric** (offline gate).
- **PR6 — routing shadow** (Hermes intent logs disagreements; regex authoritative).

## Deploy posture

Dormant/flag-OFF/shadow; reversible (backup + `py_compile` + import-smoke; flags OFF). **Held for operator:** the live-flip (enable flag after offline oracle green + their eyeball), the deploy-tree cleanup (RC4), live WhatsApp tests, and the multi-day soak. No WhatsApp/live customer tests run by me.

## Non-goals

No new parallel renderer; no creative judgment in Python; one creative brain (no per-task mini-skills); no payment/broadcast in slice 1; no pro-model spend or compositor until gated.
