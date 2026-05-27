**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Sample Request Phrase Gaps (2026-05-27)

## Hermes-first checklist
1. Receive WhatsApp text/media and sender identity context -> [Hermes]
2. Run pre-gateway cf-router deterministic intercepts -> [Hermes]
3. Detect explicit sample/example/idea ask-shapes and route to sample-idea intake -> [net-new]
4. Trigger existing Flyer intake and send deterministic sample-idea reply -> [Hermes + existing Flyer scripts]
5. Emit route/audit trail -> [Hermes + existing audit chokepoints]

Net-new scope is only step 3 lexical coverage hardening.

## Batch issue list (6 related)
1. `sample flyer request please` falls through instead of sample-idea routing.
2. `what should be on my flyer` falls through instead of sample-idea routing.
3. `what should i put on my flyer` falls through instead of sample-idea routing.
4. `suggest flyer wording for summer sale` falls through instead of sample-idea routing.
5. `need ideas for caption` falls through instead of sample-idea routing.
6. `can i get some flyer ideas?` falls through instead of sample-idea routing.

## TDD + verification
- Add RED coverage in `tests/test_cf_router_flyer_routing.py` under `test_sample_prompt_variants_route_to_sample_idea_intake` for all six phrases.
- Patch `_SAMPLE_PROMPT_REQUEST` in `src/plugins/cf-router/hooks.py` with narrow lexical additions only.
- Verify:
  - `python3 -m py_compile src/plugins/cf-router/hooks.py tests/test_cf_router_flyer_routing.py`
  - `pytest -q tests/test_cf_router_flyer_routing.py -k sample_prompt_variants_route_to_sample_idea_intake`
  - `pytest -q tests/test_cf_router_flyer_routing.py`
  - `git diff --check`

## Risk / merge policy
- Risk expected: low (routing regex + tests only).
- No payment/account/quota/provider/manual-close/runtime mutation.
- Merge/deploy eligible autonomously if checks/review pass.
