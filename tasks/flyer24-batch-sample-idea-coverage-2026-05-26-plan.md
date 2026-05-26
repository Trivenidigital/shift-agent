# Flyer24 Batch Plan - Sample Idea Coverage Gaps (2026-05-26)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Inbound message transport + sender identity resolution -> **[Hermes]**
2. Sample-prompt/example/idea intent detection policy -> **[net-new]**
3. Preference-command precedence over sample intent -> **[net-new]**
4. Intake response generation (`trigger-flyer-intake`) -> **[Hermes]**
5. Audit + skip routing behavior -> **[Hermes]**
6. Replay-safe regression tests for phrase variants -> **[net-new]**

## MCP-first verdict
No connector/payment/provider mutations in this batch.

## Batch issues (6)
1. `can you suggest hooks for my flyer` not treated as sample-idea request.
2. `give 3 ideas for weekend offer` not treated as sample-idea request.
3. `send me prompt examples` not treated as sample-idea request.
4. `help with promotion ideas` not treated as sample-idea request.
5. `example prompts for my offer` misses because matcher requires business/flyer token pairing.
6. Missing regression coverage for numeric-count + hook/copy phrasing family.

## Files
- `src/plugins/cf-router/hooks.py`
- `tests/test_cf_router_flyer_routing.py`

## Verification
- `python3 -m py_compile src/plugins/cf-router/hooks.py tests/test_cf_router_flyer_routing.py`
- `pytest -q tests/test_cf_router_flyer_routing.py -k "sample_prompt_variants_route_to_sample_idea_intake or explicit_sample_prompt_request"`
- `pytest -q tests/test_cf_router_flyer_routing.py`
- `git diff --check`
