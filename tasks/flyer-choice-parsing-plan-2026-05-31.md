**Drift-check tag:** extends-Hermes

# Flyer Intake Choice Parsing

## New primitives introduced

- No new substrate. This extends the existing deterministic language/mode choice parsers to accept normal WhatsApp reply phrasing.

## Hermes-first analysis

Hermes already owns WhatsApp ingress and sender identity. Flyer intake already owns the deterministic language/mode state machine. This slice stays inside those existing parsers so no LLM classifier or parallel routing is introduced.

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp inbound text | Existing Hermes ingress | Reuse normalized inbound text passed to Flyer intake. |
| Intake state machine | Existing Flyer intake session flow | Extend existing `parse_language_choice` / `parse_mode_choice`. |
| Semantic classification | Not needed for bounded choices | Use deterministic token/phrase parsing rather than a new Hermes call. |

Awesome Hermes Agent ecosystem check: no external Hermes skill is needed for deterministic button-choice parsing.

## Drift check

- `parse_language_choice()` currently accepts exact aliases only.
- `parse_mode_choice()` currently accepts exact aliases only.
- Existing tests pin language numbering and starter/guided/text flow, but not natural reply wrappers like "English please" or "option 2 please".

## Plan

- [x] Add RED tests for natural language and mode replies.
- [x] Extend parsing conservatively: support option/choice number wrappers and clear language/mode words with polite filler.
- [x] Preserve ambiguous / unrelated text as no-choice.
- [x] Run focused intake/onboarding tests.
- [x] Multi-vector review.
- [x] Full local verification.
- [ ] PR, merge, deploy.

## Review

- Customer-flow safety reviewer caught a blocking first-pass bug: numeric wrappers such as "option 2 create flyer for dosa" skipped the language/mode prompt. Fixed by requiring every token in a numeric-wrapper reply to be a bounded choice/filler/alias token.
- Hermes/drift reviewer found no substrate drift. Follow-up false-negative notes ("in English please", "go with option 2", "let me type it") were added as tests and accepted with bounded filler words.
- Final re-review found no blockers and confirmed freeform flyer-content replies still fail closed.

## Focused verification

- `python -m pytest tests/test_flyer_onboarding.py tests/test_flyer_workflow.py -q` -> 129 passed.
- `python -m pytest tests/test_cf_router_flyer_routing.py -q -k "intake or onboarding or choice or starter"` -> 112 passed, 204 deselected.
- `python -m pytest` -> 2821 passed, 867 skipped, 40 warnings.
