**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Sample Idea Phrasing Coverage (2026-05-25)

## Hermes-first checklist
1. Receive WhatsApp text and run pre-gateway hook chain: **[Hermes]**
2. Detect explicit sample/example/idea request intent: **[net-new]** (Flyer regex policy in `src/plugins/cf-router/hooks.py`)
3. Route to sample-idea intake and send customer copy: **[Hermes + Flyer]** (Hermes send/audit substrate; Flyer route decision)
4. Prevent project creation for sample-idea asks: **[Hermes + Flyer]** (existing deterministic intercept order)
5. Add transcript-shape regression coverage: **[net-new]**

Net-new scope is only step 2 + 5.

## Batch issue list (6 related misses)
1. `give me ad ideas for my business` is not recognized as sample-idea help.
2. `send promotional ideas for my shop` is not recognized as sample-idea help.
3. `share campaign ideas for my business flyer` can miss sample-idea routing.
4. `show ad examples for my business flyer` can miss sample-idea routing.
5. `provide promo ideas for our business flyer` can miss sample-idea routing.
6. `suggest marketing ideas for my shop flyer` can miss sample-idea routing.

## Root-cause hypothesis
`_SAMPLE_PROMPT_REQUEST` heavily keys on `sample/example/starter prompt/idea` with explicit flyer keywords, but under-covers common business-owner wording (`ad`, `promo`, `campaign`, `marketing ideas`) used when asking for starter ideas.

## Implementation steps
1. Add RED routing tests in `tests/test_cf_router_flyer_routing.py` for six phrasing variants.
2. Expand `_SAMPLE_PROMPT_REQUEST` in `src/plugins/cf-router/hooks.py` with tightly scoped idea synonyms (`ad`, `promo`, `campaign`, `marketing`) while still requiring flyer/business context.
3. Run focused verification (`py_compile`, Flyer routing pytest, `git diff --check`).

## Risk
Low. Intent-phrase broadening only; no payment/account/quota mutation, no provider calls, no deploy/runtime scripts.
