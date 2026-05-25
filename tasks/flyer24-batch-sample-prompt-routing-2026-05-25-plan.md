# Flyer24 Batch Plan - Sample Prompt Routing Robustness (2026-05-25)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Parse inbound WhatsApp text/media and sender identity -> **[Hermes]** existing cf-router + identify-sender substrate.
2. Detect sample-prompt intent and preference commands -> **[net-new]** Flyer-specific deterministic regex policy.
3. Apply account preference command before sample-idea generation -> **[Hermes]** existing manage-flyer-account command path, **[net-new]** tighter matcher wiring.
4. Trigger sample-idea intake response for explicit request copy -> **[Hermes]** existing trigger-flyer-intake with `start_source=sample_idea`.
5. Audit intercept reason + safe skip behavior -> **[Hermes]** existing `audit_intercepted` + skip action.
6. Regression coverage -> **[net-new]** Flyer routing tests for real WhatsApp phrasing variants.

## MCP-first payment verdict
No payment/provider changes in this batch. MCP connector work is not required.

## Scope
Harden Flyer sample-prompt routing to reduce clarification loops and wrong preference handling when customer phrasing varies.

## Batch issues (target 6)
1. Preference commands fail with polite prefixes (`please don't show sample prompts`).
2. Preference commands fail with explicit `show me ... again` variants.
3. Starter preference detection and account-command detection can drift due duplicate regex blocks.
4. Sample prompt request detector misses short practical ask variants (`give flyer ideas`, `ad caption ideas`).
5. Mixed visibility text (sender header + body) preference commands are under-tested.
6. Guardrail priority for preference command over sample request is under-tested for prefixed phrasing.

## Files
- `src/plugins/cf-router/actions.py`
- `src/plugins/cf-router/hooks.py` (only if needed)
- `tests/test_cf_router_flyer_routing.py`

## Verification
- `python3 -m py_compile src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py`
- `pytest -q tests/test_cf_router_flyer_routing.py -k "sample or preference or clarification"`
- `pytest -q tests/test_cf_router_flyer_routing.py`
- `git diff --check`

## Risk
Low: deterministic matcher + tests only; no payment/quota/account mutation, no deploy/runtime state mutation.
