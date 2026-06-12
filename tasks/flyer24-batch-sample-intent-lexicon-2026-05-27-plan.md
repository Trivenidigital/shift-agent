**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Sample Intent Lexicon Misses (2026-05-27)

## Hermes-first checklist
1. Inbound WhatsApp routing and sender resolution: **[Hermes]**
2. Pre-gateway deterministic flyer intercept ordering: **[Hermes + Flyer existing]**
3. Sample-prompt/example intent detection phrase coverage: **[net-new]**
4. Sample-idea intake dispatch and customer reply send: **[Hermes + Flyer existing]**
5. Audit rows for intercept outcomes: **[Hermes + Flyer existing]**
6. Regression coverage for phrase variants: **[net-new]**

Net-new scope is only steps 3 and 6.

## Batch issue list (6 related misses)
1. `give me some taglines for my poster` misses sample-idea routing.
2. `need catchy slogans for my store offer` misses sample-idea routing.
3. `share a few ad copies for my weekend sale` misses sample-idea routing.
4. `what are good promo captions for my business` misses sample-idea routing.
5. `can you suggest punchlines for my business poster` misses sample-idea routing.
6. `give marketing slogan options for my shop ad` misses sample-idea routing.

## Root cause
`_SAMPLE_PROMPT_REQUEST` is broad for `idea/prompt/example/caption/copy`, but under-covers common creative-request nouns (`tagline`, `slogan`, `punchline`) and plural `copies`/`options` variants used by customers asking for starter ideas.

## Implementation
1. Add RED routing test coverage for all six misses in `tests/test_cf_router_flyer_routing.py`.
2. Extend `_SAMPLE_PROMPT_REQUEST` in `src/plugins/cf-router/hooks.py` with tightly scoped lexical additions (`tagline(s)`, `slogan(s)`, `punchline(s)`, `copies`, `option(s)`) without widening beyond flyer/business context.
3. Run focused verification (`pytest` targeted file, `py_compile`, `git diff --check`).

## Risk
Low. Deterministic routing lexicon-only change; no payment/quota/account mutation and no provider/deploy/runtime script changes.
