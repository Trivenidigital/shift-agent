**Drift-check tag:** extends-Hermes

# Flyer Creation Flow Audit - 2026-05-18

**New primitives introduced:** none. This is a production-risk audit of the existing Flyer Studio create flow.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress and sender identity | Existing Hermes gateway, cf-router, `identify-sender`, Flyer customer state | Reuse; failures are in Flyer-specific parsing/render policy |
| Flyer state, audit, and asset storage | Existing Flyer JSON state, `safe_io`, render manifests, bridge delivery | Reuse; QA semantics need hardening |
| Vision/reference handling | Existing Hermes media cache and Flyer reference assets | Reuse; current failures reproduce without new media substrate |
| Category/style selection | No deployed Hermes primitive maps SMB category/location to safe flyer art direction | Build narrow in-tree category/style policy before generation |

Awesome-Hermes ecosystem check: no external Hermes skill is needed for this audit. The broken surface is local Flyer Studio prompt construction, request parsing, onboarding profile capture, and QA gating.

## Evidence Collected

- Inspected `C:\Testing\11.png` and `C:\Testing\22.png`.
- Checked live `main-vps` Flyer state for Chloe Hair Studio project `F0036`.
- Ran parser/prompt scenario matrix against salon, tax, cleaning, restaurant, and event requests.
- Ran focused existing suite: `python -m pytest tests\test_flyer_create_project.py tests\test_flyer_renderer.py tests\test_flyer_onboarding.py tests\test_cf_router_flyer_routing.py tests\test_flyer_guest_order.py -q` -> `127 passed`.

## Findings

### [P0] Salon/generic-service flyers are forced into Indian festive food/grocery style

`src/agents/flyer/render.py:738` hard-codes concept directions such as `premium ethnic grocery poster`, `marigold and mango-leaf accents`, and `South Indian festival motifs`. The same prompt also sets a global quality bar for `appetizing food visuals` and `festival warmth` at `src/agents/flyer/render.py:777`.

Live evidence: `F0036` is a Virginia salon request, but its fields show `style_preference` as `professional local food menu flyer with appetizing photography...` and the generated image uses marigold/banana-leaf festive styling. That is customer-hostile for a US salon.

Root cause: the renderer has no category/location-aware style policy and defaults all priced work toward Indian food/menu aesthetics.

Immediate containment: do not deliver or finalize `F0036` as-is. Mark Chloe Hair Studio work as manual review until category-safe salon styling exists.

### [P0] Customer request/instruction text leaks into flyer copy

`src/agents/flyer/scripts/create-flyer-project:94` captures everything after `flyer for` as the event name. For `Create flyer for Chloe Hair Studio promoting the $20 men haircut...`, this stores the title as `Chloe Hair Studio promoting the $20 men haircut, $80 perms, and other hair services`.

Then `src/agents/flyer/render.py:339` extracts price pairs from raw notes and turns the instruction phrase into a menu/detail item. Live manifest for `F0036` records `detail_001` as `Create flyer for chloe hair studio promoting the $20`, and `ok: true`.

Root cause: business name, offer copy, and operator instruction are not separated before render prompt construction. Price-before-service phrasing such as `$20 men haircut` is especially broken.

### [P0] QA manifest certifies bad copy because expected facts are self-derived

`src/agents/flyer/render.py:532` writes a text manifest from `collect_text_facts(project)` and uses the same derived facts as rendered facts. The `F0036` manifest is `ok: true` while it explicitly includes the leaked instruction text as a required detail.

Root cause: the current QA gate verifies internal consistency, not customer suitability. It cannot catch prompt leakage, wrong category/style, or nonsensical text facts when the parser created those bad facts upstream.

### [P1] Onboarding can store language as business category

Production Chloe profile stores `business_category: "English"` for `CUST0004`. Local parser check confirms `_parse_profile_text("English")` returns `("English", "en")` from `src/agents/flyer/onboarding.py:786`.

Root cause: at the profile step, a language-only answer is accepted as a category instead of triggering a repair prompt for the missing business type. This removes the only customer category signal that could have prevented salon -> food/festival styling.

### [P1] `Location:` and `Contact:` colon forms are not parsed

`src/agents/flyer/scripts/create-flyer-project:154` parses `location ...` but not `Location: ...`; `src/agents/flyer/scripts/create-flyer-project:170` parses `contact ...` but not `Contact: ...`.

Matrix evidence: `Location: Virginia Beach, VA. Contact: +1 757 555 0199` produced `venue_or_location=null` and `contact_info=null` unless customer-profile hydration filled them later.

Root cause: parser regexes accept space-delimited labels but not common colon-delimited labels.

### [P1] Any priced non-food service becomes a food/menu flyer

`src/agents/flyer/scripts/create-flyer-project:215` and `:217` set food-menu style whenever `$`, `price`, `special`, or similar markers appear. `src/platform/schemas.py:1325` also treats generic price/service markers as the price-list/menu path.

Matrix evidence:

- Salon `$20 men haircut` -> food menu style.
- Tax services `$99 filing` -> food menu style and bad item extraction.
- Cleaning services `$150 deep clean` -> food menu style and request text leakage.

Root cause: the system conflates priced services with restaurant/grocery menus.

### [P1] Business category is not available to render prompt policy

The renderer includes registered business name via `src/agents/flyer/render.py:700`, but it does not include `business_category`, city/state, or country in the style decision. Even if onboarding stores `Hair salon`, the current `style_by_concept` remains Indian grocery/festival.

Root cause: customer profile facts are only partially injected into rendering, and style is a static concept map.

## Scenario Matrix Summary

| Scenario | Result |
|---|---|
| Salon, price-before-service | Event title contains offer/prices; item becomes `Create flyer for Chloe Hair Studio promoting the - $20`; food/festival prompt terms present |
| Salon, service-then-price | Item extraction is better, but style remains ethnic grocery/food/festival |
| Salon, no prices | Still gets marigold/food/festival quality terms; contact may become a detail instead of contact if `Contact:` is used |
| Tax service with prices | Treated as food/menu flyer; business name plus offer becomes title |
| Cleaning service with prices | Treated as food/menu flyer; request phrase leaks into first priced item |
| Indian restaurant menu | Closer to intended domain, but title can still capture the first price fragment (`Idli $7`) |
| Event missing contact | Correctly blocks on missing contact, but still receives generic festival style terms in prompt |

## Recommended Fix Order

1. Add immediate fail-closed QA blockers for leaked instruction phrases: `create flyer`, `make flyer`, `generate flyer`, `promoting the - $`, and raw request prefixes inside title/detail/menu facts.
2. Repair live Chloe profile category from `English` to `Hair salon` and mark `F0036` manual/rejected before any final delivery.
3. Split parser output into `business_name`, `offer_headline`, `priced_items/services`, `location`, `contact`, and `style/category`; stop overloading `event_or_business_name`.
4. Add category-aware style policy: salon/beauty, restaurant/food, grocery, cleaning, tax/accounting, marketing, event, general local service. Default must be neutral US local-business creative, not Indian festive.
5. Teach price parsing both `Service $20` and `$20 service`, and apply it to services without using restaurant/menu layout language.
6. Reject language-only business-profile replies at onboarding profile step; ask for the business type.
7. Parse colon labels: `Location:`, `Address:`, `Contact:`, `Phone:`, plus multiline variants.
8. Extend QA beyond manifest consistency: banned phrase scan, category/style mismatch scan, business-category availability check, and optional OCR/vision review before customer delivery.

## Current Test Gap

The focused suite passes (`127 passed`) while live `F0036` is bad. The test suite needs regressions for:

- Virginia salon request with `$20 men haircut, $80 perms`.
- Language-only onboarding profile answer.
- `Location:` and `Contact:` labels.
- Price-before-service and service-before-price extraction.
- Non-food priced services not receiving food/menu/festival prompts.
- QA manifest failing when instruction text appears in expected facts.

## Fix Applied 2026-05-18

- Added regressions for the Chloe Hair Studio request, language-only onboarding profile replies, salon prompt policy, and manifest instruction leakage.
- Fixed `create-flyer-project` so `promoting/offering/featuring` does not pollute `event_or_business_name`, `Location:` and `Contact:` labels parse, and priced salon/tax/cleaning/marketing services no longer get food-menu style defaults.
- Fixed onboarding profile parsing so language-only replies such as `English` remain at `collecting_business_profile` and ask for a business type instead of storing `business_category="English"`.
- Fixed render prompt policy so salon and other non-food service businesses use category-safe service-offer direction instead of hard-coded ethnic grocery/South Indian/festival defaults.
- Added text-manifest blockers for instruction leakage such as `Create flyer...`, `flyer for...`, and `promoting the $...` inside customer-visible facts.
- VPS containment: repaired `CUST0004` to `business_category="Hair salon"` and moved `F0036` from `awaiting_final_approval` to `manual_edit_required`; backup suffix `20260518T200608Z`.
- VPS hotfix deployed to `/opt/shift-agent/flyer_render.py`, `/opt/shift-agent/flyer_onboarding.py`, and `/usr/local/bin/create-flyer-project`; deployed syntax check passed.
- Verification: focused local suite `132 passed`; `python -m compileall -q src\agents\flyer src\platform` passed; VPS smoke parsed a fresh Chloe request as title `Chloe Hair Studio`, location `Virginia Beach, VA`, contact `+1 757 555 0199`, with blocked food/festival prompt terms absent.
- Fresh Chloe candidate: generated `F0048` from repaired code path, not old `F0036`. Visual QA: salon-specific imagery, no Indian festive/food cues, clean title, service cards for `men haircut $20`, `perms $80`, and `Other hair services available`, readable address/contact. Text manifest `ok: true` with no instruction leakage. Earlier QA attempts `F0043` and `F0045` were contained as `manual_edit_required`; `F0048` is the only accepted preview candidate.
