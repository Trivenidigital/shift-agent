**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Sample Request Ask Shapes (2026-05-27)

## Hermes-first checklist
1. Receive WhatsApp inbound text/media, sender identity, and chat context -> [Hermes]
2. Route through cf-router pre-gateway hook and dispatch chain -> [Hermes]
3. Detect explicit request for sample prompts/ideas/examples and choose sample-intake route -> [net-new]
4. Create intake/session state and send deterministic reply copy -> [Hermes + existing Flyer scripts]
5. Audit route reason and operator evidence -> [Hermes + existing audit chokepoint]

Net-new work is only step 3 lexical/ask-shape detection and regression tests.

## Batch issue list (target 6)
1. `what can you suggest for ...` sample asks can miss sample-intake routing.
2. `any ideas for ...` sample asks can miss sample-intake routing.
3. `what should I write for ... flyer` ask-shape can miss sample-intake routing.
4. `help me with caption ideas for ...` ask-shape can miss sample-intake routing.
5. `give me few prompt ideas` quantifier/typo shape can miss sample-intake routing.
6. No focused regression test family pins these ask-shape variants together.

## TDD + verification
- Add RED routing tests in `tests/test_cf_router_flyer_routing.py` for all six phrases.
- Update only sample-request detection logic in `src/plugins/cf-router/actions.py`.
- Run:
  - `python3 -m py_compile src/plugins/cf-router/actions.py tests/test_cf_router_flyer_routing.py`
  - `pytest -q tests/test_cf_router_flyer_routing.py -k sample_prompt_variants_route_to_sample_idea_intake`
  - `pytest -q tests/test_cf_router_flyer_routing.py`
  - `git diff --check`

## Risk / merge policy
- Risk expected: low (routing heuristic + tests only, no payment/account/quota/manual-close/provider mutations).
- If checks pass and self-review finds no blockers, this batch is merge/deploy eligible under autonomous low-risk policy.
