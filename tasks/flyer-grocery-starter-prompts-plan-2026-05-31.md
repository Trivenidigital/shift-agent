**Drift-check tag:** extends-Hermes

# Flyer Grocery Starter Prompts

## New primitives introduced

- No new substrate. This adds grocery-specific starter idea copy to the existing Flyer starter brief module.

## Hermes-first analysis

Hermes already owns WhatsApp delivery, customer profile routing, and the onboarding/intake state machine. Flyer code already owns deterministic starter brief and starter idea copy.

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Customer profile / business category | Existing Flyer account and onboarding state over Hermes ingress | Reuse `business_category`; no new classifier. |
| Starter prompt delivery | Existing Flyer onboarding/intake hooks | Reuse existing `starter_idea_choices_message`. |
| Prompt copy library | Existing `starter_briefs.py` deterministic copy | Add grocery-specific ideas there; no new LLM prompt generation. |

Awesome Hermes Agent ecosystem check: no external Hermes skill is needed for deterministic category starter copy.

## Drift check

- `starter_brief_for_category("grocery")` already resolves to the grocery starter brief.
- `starter_idea_choices()` currently maps `brief.category_id in {"restaurant", "grocery"}` to `_RESTAURANT_IDEAS`.
- Existing tests cover restaurant ideas but not grocery ideas.
- Review found that removing broad `food` as a restaurant keyword would regress food-court / food-truck categories. The final fix keeps targeted food-business keywords and makes grocery/supermarket categories win when a hybrid says both grocery and food court.

## Plan

- [x] Add RED test that grocery idea choices avoid restaurant thali/snack prompts and mention grocery/sale/product concepts.
- [x] Add compact grocery-specific idea choices.
- [x] Preserve pure food-court / food-truck / food-special categories as restaurant starter ideas.
- [x] Run starter brief and onboarding/routing affected tests.
- [x] Multi-vector review.
- [ ] Full verification, PR, merge, deploy.

## Review notes

- Customer/product copy review initially found a regression: removing broad `food` made pure food-court/food-truck categories resolve to local-business copy. Fixed with targeted food-business keywords plus a grocery/supermarket hybrid override.
- Hermes/drift review initially found the same category regression and no Hermes substrate issue. Re-review cleared the final diff: grocery/supermarket hybrids resolve grocery; pure food categories resolve restaurant; deterministic copy only.
- Local Claude review timed out twice and was not used as evidence.
