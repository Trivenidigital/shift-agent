# Flyer Hermes Semantic Brief Reliability Plan

**Drift-check tag:** extends-Hermes

**Goal:** Make Flyer Studio handle free-form customer flyer briefs through a Hermes-first semantic brief contract so campaign/offer facts are clean before rendering, renderer copy consumes those facts, QA validates meaning without weakening hard anchors, and autorepair can fix non-trust visual omissions.

**New primitives introduced:** one `FlyerSemanticBrief` schema/validator, one Hermes-shaped provider interface, one semantic locked-fact projection path, one renderer consumption path for semantic offer facts, one semantic QA equivalence helper, and widened autorepair classification for non-trust campaign/offer visibility gaps.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Free-form intent understanding | Hermes LLM/gateway substrate already exists in this repo through OpenRouter/Hermes env conventions used by renderer, visual QA, and PR #308 autorepair. `src/agents/flyer/intent.py` already establishes the local pattern: Hermes owns messy language, while Flyer code owns schema validation and safety. Public Hermes docs confirm skills/gateway are the intended extension point for reusable agent behavior. | Add the `FlyerSemanticBrief` schema/validator and provider seam now. Use deterministic incident fixtures/cleanup only as bounded fallback, not a broad production regex parser. |
| Customer message ingestion / WhatsApp routing | Hermes already owns gateway + WhatsApp delivery and cf-router integration in this repo. | Do not add a new messaging path. Use existing `create-flyer-project`, `cf-router`, `send-flyer-package`, and `decisions.log` paths. |
| Vision / OCR QA | Existing Flyer visual QA uses OpenRouter vision and Hermes env conventions. | Extend existing QA semantics; do not add a new provider. |
| Autorepair planning | PR #308 added `plan_flyer_autorepair` using Hermes/OpenRouter configuration. | Widen classifier eligibility; do not add a second repair planner. |

Awesome-Hermes ecosystem check: `https://github.com/0xNyk/awesome-hermes-agent` lists Hermes skills/tools/integrations, but no ready-made restaurant flyer semantic-brief skill matched this domain-specific contract. Build the narrow schema/validator/projector in-tree on top of Hermes conventions and keep the provider seam ready for an actual Hermes skill.

Sources checked: `https://hermes-agent.nousresearch.com/docs/`, `https://hermes-agent.nousresearch.com/docs/guides/work-with-skills/`, `https://github.com/0xNyk/awesome-hermes-agent`.

## Drift-check evidence

- `src/agents/flyer/semantic_brief.py` exists, but it is only a semantic visibility policy for QA. It does not parse customer briefs into campaign/offer/schedule facts.
- `src/agents/flyer/facts.py` creates `campaign_title` from `fields.event_or_business_name`, which can carry truncated prompt fragments such as `evening snacks sale, Wednesday and Thursday , any item $7`.
- `src/agents/flyer/visual_qa.py` loops over required locked facts and exact-matches each value; this blocks when a flyer visibly shows a semantically correct title like `EVENING SNACKS SALE` but not the exact brittle fragment.
- `src/agents/flyer/recovery.py` classifies `missing required visible fact: campaign_title` as `manual_required:unknown_blocker_pattern`; PR #308 autorepair fires but skips F0106/F0107.
- `src/plugins/cf-router/actions.py` business-scope parsing is regex-based and can treat campaign phrases containing business-ish words like `store` as account scope.

## Runtime incident targets

- F0106: Diwali sale rendered visibly but failed `missing required visible fact: campaign_title`; autorepair skipped.
- F0107: Evening snacks sale rendered visibly but failed `missing required visible fact: campaign_title`; autorepair skipped.
- F0105: Daily thali preview rendered but QA blocked item-name visibility/duplication; useful as non-trust autorepair coverage, not the primary intake bug.

## Scope

### In scope

1. Add `FlyerSemanticBrief` schema/validator and a provider interface that can be backed by Hermes structured output.
2. Add bounded deterministic incident fixtures/cleanup for F0106/F0107 shapes only, so offline tests can prove the contract without pretending regex is the long-term brain.
3. Project validated semantic brief fields into clean locked facts before project creation.
4. Update renderer copy planning so semantic `offer:*`, `pricing_structure`, `schedule`, and `promotion_end` facts are actually used in the poster prompt/manifest.
5. Fix business-vs-campaign disambiguation by stripping campaign suffixes after candidate extraction, then evaluating any remaining explicit business identity.
6. Let visual QA validate campaign title semantic equivalence while keeping exact numeric/date anchors for price, thresholds, schedule, phone, address, and promotion end.
7. Make `campaign_title`, `headline`, `offer:*`, and repairable item-name visibility blockers autorepair-eligible when no trust-risk token is present.
8. Add replay tests for F0106 and F0107; add/extend F0105-style autorepair tests for item-name visibility.
9. Preserve deterministic fail-closed behavior for wrong business, wrong price, missing contact, missing schedule, unauthorized brand, and billing/payment state.
10. Add a post-deploy recovery gate for existing F0106/F0107: either `flyer_assets_delivered`, `flyer_closure_customer_notified`, or an explicit operator handoff if live regeneration is not safe.

### Out of scope

- New WhatsApp routing or bridge send path.
- New image generation provider.
- F0105 manual design repair. F0106/F0107 recovery verification is in scope after deploy because those are the current customer-visible failures this PR prevents going forward.
- Replacing Hermes/OpenRouter gateway configuration.
- Broad source-preserving image edit pipeline.

## Proposed design

### Component 1: Semantic brief contract and provider seam

Create/extend `src/agents/flyer/semantic_brief.py` with a pure `FlyerSemanticBrief` schema/validator and `build_semantic_flyer_brief(raw_request, fields, customer, provider=None)` helper. The helper accepts a provider callable shaped for future Hermes structured output. In production, this PR uses existing deterministic field extraction plus bounded incident cleanup when provider output is unavailable; it must not grow a broad free-form parser.

Fields:
- `campaign_title`
- `business_identity` as advisory only; for registered customers it must never overwrite the profile `business_name`.
- `pricing_structure`
- `secondary_offers`
- `schedule`
- `promotion_end`
- `style`
- `stored_contact_policy`

The first implementation is deterministic and testable but deliberately narrow. It should repair the immediate incident shapes:
- `Create a flyer for Diwali sale, All items 5-10% off...` => campaign `Diwali Sale`, pricing `All items 5-10% off`, secondary offer `Lucky draw eligible with purchase above $100`.
- `Create a flyer for evening snacks sale, Wednesday and Thursday, any item $7.99. Free Masala Chai... until June 25.` => campaign `Evening Snacks Sale`, pricing `Any item $7.99`, secondary offer `Free Masala Chai with any purchase above $12`, schedule `Wednesday and Thursday`, promotion end `June 25`.

### Component 2: Locked fact projection

Modify `src/agents/flyer/facts.py` so `extract_text_facts` uses the validated semantic brief to create clean customer-text facts:
- `campaign_title`
- `offer:0`
- `offer:1`
- `pricing_structure`
- `schedule`
- `promotion_end`

Keep existing item facts for true item lists. Do not create `item:0:name = any item`; model generic sale pricing as `pricing_structure` and secondary terms as `offer:*`.

### Component 3: Renderer consumption

Modify `src/agents/flyer/render.py` so `collect_text_facts()` / `_detail_clauses()` consume semantic locked facts:
- clean `campaign_title` becomes title,
- `pricing_structure` becomes a required detail line,
- each `offer:*` becomes a required detail line,
- `schedule` and `promotion_end` become visible schedule/date lines when present.

Add renderer tests proving F0106/F0107 semantic facts appear in generated text facts / prompt lines and cannot be silently ignored.

### Component 4: Business scope disambiguation

Modify `src/plugins/cf-router/actions.py` so `_extract_requested_business_scope` strips campaign suffixes after candidate extraction, then evaluates any remaining explicit business identity. `Diwali store wide sales` should not block. `Create flyer for Patel Grocery store-wide sale` under Lakshmi should still block as wrong business.

### Component 5: Semantic QA equivalence

Modify `src/agents/flyer/visual_qa.py` so required fact checking uses semantic presence for:
- `campaign_title`
- `headline`
- `offer:*`
- `pricing_structure`
- `promotion_end`

For campaign title, accept normalized subset/head/title equivalence where the visible text contains the core campaign phrase without offer details. For offers, schedule, thresholds, prices, and promotion end, require exact numeric/date anchors even if words move around.

Do not relax:
- `business_name`
- `contact_phone`
- `location`
- source-contract replacements
- exact wrong-brand blockers

### Component 6: Autorepair classifier widening

Modify `src/agents/flyer/recovery.py` so these blockers are Hermes-plan eligible when no trust-risk token is present:
- `missing required visible fact: campaign_title`
- `missing required visible fact: headline`
- `missing required visible fact: offer:*`
- `missing required visible fact: pricing_structure`
- `missing required visible fact: item:N:name` when project has offer/item context
- `instruction text leaked into flyer copy`

Keep current hard-stop for trust-risk tokens. Tighten `_project_has_offer_context` so item-name blockers are eligible only when real `offer:*`, `item:*`, `pricing_structure`, or rendered detail facts exist, not merely because `raw_request` is nonblank.

## Test plan

- `tests/test_flyer_semantic_brief.py`: pure parser tests for F0106/F0107 and business-vs-campaign examples.
- `tests/test_flyer_create_project.py`: create-project replay tests prove clean locked facts for F0106/F0107 and no `any item` item-name poison.
- `tests/test_flyer_renderer.py`: semantic facts appear in renderer text facts / prompt lines for F0106/F0107.
- `tests/test_cf_router_flyer_routing.py`: business scope false-positive regression for `Diwali store wide sales`.
- `tests/test_cf_router_flyer_routing.py`: wrong-business regression for `Create flyer for Patel Grocery store-wide sale` under Lakshmi still blocks.
- `tests/test_flyer_visual_qa.py`: campaign semantic QA passes for visible `Diwali Sale` and `EVENING SNACKS SALE`; price/date/schedule anchor tests cover `WED & THU`, `Until June 25`, missing/wrong date, and missing/wrong price.
- `tests/test_flyer_autorepair.py`: classifier accepts campaign/offer visibility gaps, tightens item context, and still hard-stops trust risks.
- `tests/test_flyer_generate_concepts.py`: F0106/F0107 replay proves generate path does not end in campaign-title manual review or `flyer_autorepair_skipped`.
- Focused command bundle before PR:
  - `python -m pytest tests/test_flyer_semantic_brief.py tests/test_flyer_create_project.py tests/test_flyer_renderer.py tests/test_flyer_visual_qa.py tests/test_flyer_autorepair.py tests/test_flyer_generate_concepts.py tests/test_cf_router_flyer_routing.py -q`
  - `python -m py_compile src/agents/flyer/semantic_brief.py src/agents/flyer/facts.py src/agents/flyer/render.py src/agents/flyer/visual_qa.py src/agents/flyer/recovery.py src/plugins/cf-router/actions.py`
  - `git diff --check origin/main...HEAD`

## Commit plan

1. `feat(flyer): add Hermes-shaped semantic brief contract`
2. `fix(flyer): project semantic brief into locked facts`
3. `fix(flyer): render semantic offer facts`
4. `fix(cf-router): keep campaign suffixes out of business mismatch guard`
5. `fix(flyer): use semantic visibility checks in QA`
6. `fix(flyer): widen Hermes autorepair coverage for semantic QA gaps`
7. `docs(flyer): record semantic reliability plan and review receipts`

## Review gates

Plan review vectors:
- Hermes/scope reviewer: challenge whether this duplicates existing Hermes substrate or over-customizes deterministic code.
- Runtime/reliability reviewer: challenge whether the plan actually fixes F0106/F0107 and avoids trust-risk regressions.

Design review vectors:
- Intake/QA structural reviewer: inspect data flow from customer text to locked facts to QA.
- Safety reviewer: inspect fail-closed boundaries for business, price, contact, schedule, source contract, and billing state.

PR review vectors:
- Code correctness reviewer.
- Product/reliability reviewer.
- Deployment/runtime reviewer.

## Open risk

This PR improves the semantic contract and adds the provider seam, but it does not require a live Hermes call during intake. That is intentional for testability and blast-radius control. The contract names the boundary Hermes should own; once this is stable, a follow-up can route the provider through an actual Hermes skill invocation behind the same tests.
