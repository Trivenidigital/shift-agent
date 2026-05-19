# Flyer Studio E2E QA - 2026-05-19

**Drift-check tag:** extends-Hermes

## Hermes-first analysis

| Step | Existing Hermes / Flyer capability | Net-new finding |
|---|---|---|
| WhatsApp-style sender identity | Hermes `identify-sender`, Flyer customer JSON, cf-router primary Flyer path | None |
| Project creation from brief | Existing `create-flyer-project` and Flyer schemas | Parser was poisoning project facts from logo/reference wording |
| Image generation | Existing Flyer OpenRouter renderer | Missing retry on transient chunked response failure |
| Revision handling | Existing Flyer workflow revision patching | Decimal-price replacement corrupted prices |
| Exact template edits | Existing manual/source-edit branch | Blocked by missing `OPENAI_API_KEY` for source-preserving edit provider |
| Reference-menu extraction | Existing reference asset attachment | No real OCR/vision extraction step; renderer can ignore attached item/price source |
| Visual QA | Existing text manifest QA | Manifest is self-reported, not OCR/visual verification |

## Scope

Autonomous production-style QA was run against the live `main-vps` Flyer Studio flow using existing customer `CUST0001` / `+17329837841` in dry-run delivery mode. The test acted like a real customer: create new flyer, request revision, use a logo/reference, upload a template, ask for extraction from a sample, inspect generated images, and approve final delivery without sending real customer spam.

Local pulled visual outputs are in `C:\projects\sme-agents\.qa_outputs`.

## Live scenarios

| Project | Scenario | Status | Result |
|---|---|---|---|
| F0054 | Telugu/English weekend breakfast menu flyer, then price/background revision | Delivered dry-run | Visually polished, but Kheema Dosa rendered `[price]`; text manifest falsely passed |
| F0055 | Logo/reference flyer, “Family Combo Feast” headline/tagline/badges | Awaiting approval | Wrong headline, missing tagline, bad location `and`; parser polluted business/location facts |
| F0056 | Exact source-template edit from uploaded image | Manual edit required | Blocked because source edit provider needs `OPENAI_API_KEY` |
| F0057 | Extract items/prices from attached sample menu and create new flyer | Awaiting approval | Generic flyer; failed to extract item names/prices from reference image |

## Fixed in this branch

- Cleaned extracted business names so phrases like `using the attached logo` and `new original` do not become the business/title.
- Rejected invalid venue fragments like `and`, `bottom`, `customer profile`, or contact/address placeholders.
- Fixed revision price replacement so changing `$12.99` to `$13.99` does not produce `$13.99.99`.
- Added OpenRouter image-render retry for transient `IncompleteRead`, `URLError`, and timeout failures.
- Tightened recurring schedule parsing so a marketing badge like `Weekend Special` does not become `Schedule: Weekend Special...`.

## Remaining production blockers

1. Visual QA is not trustworthy. F0054 passed the text manifest while the actual image showed `[price]`.
2. Reference extraction is not implemented end to end. F0057 attached a sample menu, but no item/price facts were extracted into controlled project fields.
3. Exact uploaded-template editing is config/provider-blocked. F0056 cannot run without a source-edit provider key/path.
4. The image model can ignore required headline/tagline/logo semantics. F0055 looked premium but did not satisfy the customer request.
5. A long context/session can still carry stale facts into new flyers unless the runtime explicitly resets project context and uses only current project fields/assets.

## Readiness verdict

Current production readiness after this pass: **about 60-65%, not 90%**.

The branch fixes several deterministic bugs that caused wrong facts and stuck generations, but Flyer Studio is not production-ready until visual/OCR QA, reference extraction, source-template editing, and context isolation are hardened.

## Verification

- `python -m pytest tests/test_flyer_create_project.py::test_create_project_cleans_logo_prompt_business_and_bad_venue tests/test_flyer_create_project.py::test_create_project_cleans_new_original_reference_business_name tests/test_flyer_workflow.py::test_extract_revision_patch_replaces_decimal_price_before_period tests/test_flyer_renderer.py::test_openrouter_image_renderer_retries_incomplete_chunk_read tests/test_flyer_renderer.py::test_image_prompt_does_not_turn_weekend_special_badge_into_schedule -q` -> 5 passed.
- `python -m pytest tests/test_flyer_onboarding.py tests/test_flyer_guest_order.py tests/test_flyer_create_project.py tests/test_flyer_renderer.py tests/test_flyer_workflow.py tests/test_flyer_starter_briefs.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py tests/test_flyer_delivery_retry.py tests/test_flyer_schemas.py -q` -> 245 passed.
