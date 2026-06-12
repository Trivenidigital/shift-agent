**Drift-check tag:** extends-Hermes

# Flyer Studio Product-Depth Autonomous Run - 2026-05-31

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress/egress | In-tree Hermes/cf-router substrate already handles sender identity, media ingress, bridge sends, audit, and action contexts. | Use existing substrate. Do not create messaging or audit primitives. |
| Flyer fact extraction | In-tree Flyer/Hermes semantic brief path already creates `locked_facts` and validated customer facts. | Use existing facts. Do not re-extract in preview copy. |
| Prompt/rendering | Flyer render code already owns deterministic prompt assembly, overlays, text manifests, and QA. | Extend only where a verified UX gap remains. |
| Preview approval copy | No Hermes skill is needed; this is deterministic customer-facing state copy from already-validated project facts. | Build a small Flyer customer-copy helper. |
| Hermes skills hub | `https://hermes-agent.nousresearch.com/docs/skills` currently lists no installable skills in the hub. | No external skill applies. |

Awesome Hermes Agent ecosystem check: reviewed `https://github.com/0xNyk/awesome-hermes-agent`; it lists broad Hermes resources and skills, but no production-ready flyer preview approval/fact-checklist primitive. Verdict: use existing in-tree Hermes/Flyer substrate and add only deterministic business UX logic.

## Current drift findings

- Source-edit deterministic overlay is already merged on `origin/main` (`render_source_edit_preview` writes `source_edit_overlay_recomposed`). Do not rebuild it.
- Starter prompts and sample-idea intake already exist (`starter_briefs.py`, `handle-flyer-intake`, cf-router sample prompt intercepts). Improvements should extend the library, not create another intake path.
- Preview sends are centralized at `src/plugins/cf-router/actions.py::_send_concept_preview_media`. Pass-tier preview copy still ends with a generic approval CTA and does not summarize the facts the customer is approving.

## Batch 3 slice 1 - preview fact checklist

- [x] Add red tests for deterministic preview fact checklist copy.
- [x] Implement a pure customer-copy helper sourced from `locked_facts`/project fields.
- [x] Wire pass-tier concept preview CTA to include the checklist before `APPROVE`.
- [x] Keep warn-tier preview copy unchanged.
- [x] Run focused tests.
- [x] Run subagent review before broad tests.
- [ ] Commit, PR, merge, deploy, and verify live.

### Review and verification

- Customer-safety reviewer initially BLOCKED missing `detail_###` / `offer_price` fact shapes and long-checklist truncation. Fixed by including those shapes and budget-selecting required `Items` / `Ends` lines before optional context.
- Hermes/drift reviewer APPROVED: the slice reuses existing `locked_facts`, cf-router preview delivery, action contexts, and flat-module fallback; no new substrate.
- Verification:
  - `python -m pytest tests/test_flyer_customer_copy_policy.py tests/test_cf_router_flyer_routing.py -q` -> 343 passed.
  - `python -m pytest tests/test_flyer_customer_copy_policy.py tests/test_cf_router_flyer_routing.py tests/test_cf_router_plugin.py tests/test_flyer_scripts_static.py -q` -> 378 passed, 138 skipped.
  - Flyer-focused gate -> 1426 passed, 139 skipped.
  - Full suite -> 2780 passed, 867 skipped.

## Later product-depth backlog

- [ ] Expand starter idea choices beyond two generic ideas per family.
- [ ] Add category-aware vague-request recovery after sample ideas have already been shown.
- [ ] Add real-model visual eval scenarios and a rubric for food/menu, salon, service, retail, and nonprofit flyers.
- [ ] Continue typed-edit lane: classify text-only edits and skip the visual edit model.
