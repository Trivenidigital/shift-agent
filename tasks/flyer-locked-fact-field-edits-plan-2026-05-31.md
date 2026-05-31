**Drift-check tag:** extends-Hermes

# Flyer Locked-Fact Field Edits

## New primitives introduced

- No new substrate. This slice keeps the existing deterministic revision mapper and updates the locked facts that the renderer already treats as the source of truth.

## Drift-rule self-checks

| Check | Evidence | Decision |
|---|---|---|
| Read edit application path | `src/agents/flyer/scripts/update-flyer-project` applies `extract_revision_patch` field updates, then only refreshes existing `customer_text` facts. | Add a bounded field-update-to-locked-fact sync for contact/location edits. |
| Read renderer source of truth | `src/agents/flyer/render.py` uses `fact_value(project, "location", fallback=fields.venue_or_location)` and `fact_value(project, "contact_phone", fallback=fields.contact_info)`. | Field updates must update corresponding locked facts or stale profile facts still render. |
| Read merge priority | `src/agents/flyer/facts.py` gives `customer_text` higher priority than `customer_profile`. | Store typed revisions as `customer_text`, preserving the existing precedence contract. |
| Read revision mapper | `src/agents/flyer/workflow.py` already parses contact and location field updates. | Reuse it; do not add a parallel NLU/classifier. |

## Hermes-first analysis

Hermes owns ingress, routing, identity, and the broad intent substrate. Flyer code owns deterministic project state, locked facts, rendering, and fail-closed revision application.

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Contact/location edit detection | Existing Flyer deterministic revision mapper already parses these edits. | Reuse `extract_revision_patch`; no Hermes prompt or new classifier for this slice. |
| Fact source-of-truth update | No Hermes skill; existing Flyer locked-fact model owns render facts. | Add a small adapter from approved field updates to locked facts. |
| Rendering | Existing deterministic overlay reads locked facts. | No render change; fix the project state feeding renderer. |
| Audit/reply | Existing revision capture and deterministic customer copy. | No new messaging substrate. |

Hermes skill-hub check: https://hermes-agent.nousresearch.com/docs/skills has no Flyer locked-fact edit primitive.

Awesome Hermes ecosystem check: https://github.com/0xNyk/awesome-hermes-agent has no applicable Flyer-specific locked-fact primitive.

## Build Checklist

- [x] RED update-project test: contact/location revision updates both fields and corresponding locked facts.
- [x] Add bounded locked-fact sync helper for `contact_info -> contact_phone` and `venue_or_location -> location`.
- [x] Reuse helper for immediate revision application and pending `APPLY` path.
- [x] Subagent review.
- [x] Focused and full verification.
- [ ] PR, merge, deploy.

## Review Notes

- Explorer validated the bug on `origin/main`: cf-router routes the revision, `update-flyer-project` updates fields, and the renderer still prefers stale profile locked facts.
- Structural reviewer: APPROVE. Verified immediate and pending-apply paths preserve locked-fact precedence and state transitions.
- Hermes/safety reviewer: APPROVE. No substrate duplication; contact/location edits are stored as `customer_text` without mutating profile state.
- Focused verification: `python -m pytest tests/test_flyer_update_project.py tests/test_cf_router_flyer_routing.py -q` -> 361 passed.
- Adjacent verification: `python -m pytest tests/test_flyer_workflow.py tests/test_flyer_visual_qa.py -q` -> 132 passed.
- Full verification: `python -m pytest` -> 2860 passed, 867 skipped, 48 warnings.
