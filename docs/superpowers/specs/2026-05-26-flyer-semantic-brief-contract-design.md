# Flyer Semantic Brief Contract Design

**Drift-check tag:** extends-Hermes

**New primitives introduced:** `SemanticVisibilityPolicy`, a pure derived view over an existing `FlyerProject`.

**Hermes-first analysis:**

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Identity/routing | yes - existing Hermes/cf-router sender identity and role gates | use as account authority |
| Structured extraction | yes - existing Hermes LLM/structured-output conventions and Flyer fact extraction | use current locked facts; add only a deterministic policy view |
| Vision QA | yes - existing OpenRouter/Hermes vision QA path | reuse provider; change policy checks |
| Recovery/ARE | yes - existing recovery incidents and worker drafts | no deploy authority change in this slice |

Awesome-Hermes ecosystem check: no external Flyer marketing-brief skill is already installed in this repo. This design extends the existing Flyer/Hermes substrate.

## Intent

Flyer Studio should behave like a marketing assistant, not a form validator. Customers can write:

> Create a Special Biryani's Flyer using golden background...

The system should interpret:

- `Lakshmi's Kitchen`: account identity from the registered WhatsApp/customer profile
- `Special Biryani's`: campaign title/headline
- stored phone/address: customer contact anchors
- chicken/goat prices: offer facts
- golden background/item photos: style direction

It should not reinterpret `Special Biryani's` as an account business-name change, and it should not fail QA solely because the exact stored business name is absent when verified contact anchors are visible.

## Current Behavior

Current code already has partial separation:

- `src/agents/flyer/facts.py` emits `business_name` from `customer_profile` and `campaign_title` from customer text.
- `src/agents/flyer/render.py` uses `campaign_title` for poster title and keeps business/contact in secondary copy.
- `src/agents/flyer/visual_qa.py` loops through every required `locked_fact` and blocks on exact absence.

The failure is that QA has no semantic difference between:

- hard trust facts: phone, price, source-contract replacements
- flexible campaign facts: title/headline
- preferred account-brand display: stored business name

## Architecture

Add `src/agents/flyer/semantic_brief.py` with:

```python
@dataclass(frozen=True)
class SemanticVisibilityPolicy:
    effective_business_name: str
    campaign_title: str
    allowed_identity_names: tuple[str, ...]
    allowed_headline_names: tuple[str, ...]
    forbidden_source_brand_names: tuple[str, ...]
    brand_visibility_required_exact: bool
    brand_visibility_preferred: bool
    require_contact_anchor: bool
    require_location_anchor: bool
    require_account_anchor_if_brand_absent: bool
```

Public API:

```python
def semantic_visibility_policy(project: FlyerProject) -> SemanticVisibilityPolicy:
    ...

def required_visual_fact_ids(project: FlyerProject) -> set[str]:
    ...

def visible_wrong_brand_blockers(project: FlyerProject, extracted_text: str) -> list[str]:
    ...
```

Rules:

- `effective_business_name` mirrors the current render/business display behavior: the current-project `business_name` locked fact from trusted sources, then registered customer fallback. This preserves already-reviewed explicit business-name override tests, but this PR does not add any new account-update route.
- `campaign_title` comes from `campaign_title`, `headline`, or `fields.event_or_business_name`.
- `allowed_identity_names` includes normalized effective business name and source-contract `target_business_name` when present.
- `allowed_headline_names` includes campaign title/headline values. These are allowed as title/headline copy, but not as proof of account identity.
- `forbidden_source_brand_names` includes source-contract source business names unless they match the effective business or authorized target.
- `brand_visibility_required_exact` is true for saved brand/logo requests, source-preserving edit requests, active saved brand asset paths, and explicit “use saved business name/logo/brand” language.
- For normal campaign flyers, exact account business visibility is preferred, not blocking, when contact and location anchors are present.
- `account_anchor_satisfied` means the OCR text visibly contains both the account-owned contact phone and the full account-owned address/location, matched with the existing phone/address matchers. Customer-text phone/address values do not satisfy this anchor unless they are the same values as the profile/source-contract values.
- Phone/location stay blocking when present as required locked facts or when the request says to use stored contact/address.
- Raw customer text must never mutate or replace account identity for an existing trial/active account inside flyer creation. Account-name updates remain regulated account actions.

No schema change. No new persisted fields. No account update side effect.

## Visual QA Changes

`run_visual_qa()` should:

1. Keep existing OCR/provider behavior unchanged.
2. Keep placeholder, English-only, provider-note, and source-contract forbidden-substring checks unchanged.
3. Build `policy = semantic_visibility_policy(project)`.
4. For each required locked fact:
   - skip exact `business_name` only when `brand_visibility_required_exact` is false and account anchors are present;
   - still require `campaign_title`, `headline`, `contact_phone`, `location`, item names/prices, offer price, and source-edit facts.
5. Add wrong-brand blockers from `visible_wrong_brand_blockers(...)`.
6. Preserve the exact blocker string `missing required visible fact: business_name` whenever `brand_visibility_required_exact=True`, because `generate-flyer-concepts` uses that blocker to trigger the saved-brand-assets retry path.

Wrong-brand detection should be conservative and deterministic in this PR:

- It should block known source-contract source business names when they are still visible and not equal to the effective business or authorized target.
- It should block obvious wrong identity labels/mastheads from testable patterns such as `Business: Other Restaurant`, `Brand: Other Restaurant`, or `Other Restaurant` when that value is known from source-contract/source/reference context.
- It must not treat a campaign title as an allowed identity/brand name unless it matches the effective business or source-contract target.
- It must not try broad NER against arbitrary title text.
- Tests should cover obvious wrong-brand strings; broader Hermes/LLM brand classification can be a follow-up.

## Render Prompt Changes

`_poster_copy_block()` should keep:

- `Business/brand: <stored account name>` when available
- `Title: <campaign title>`
- contact/location/details/items

Add wording that the title may be a campaign/product/service headline, while the business/brand is secondary account identity. This guides image generation without changing field storage.

## ARE Scope

ARE already diagnosed F0103 and produced a draft patch. This PR does not give ARE production deploy authority. It improves the product path so F0103-class requests do not enter manual review only because campaign title replaced exact business-name visibility.

Autonomous promotion remains a separate architecture slice:

worker draft -> PR -> review -> deploy -> regenerate affected project -> close incident.

## Tests

Add/adjust tests:

- `tests/test_flyer_visual_qa.py`
- normal campaign passes when OCR contains campaign title, correct stored phone, and correct stored address but not exact business name;
- normal campaign fails without campaign title;
- normal campaign fails without phone/contact anchor;
- normal campaign fails without the full stored address when exact business name is absent;
- explicit saved-brand request fails without exact stored business name;
- wrong visible brand fails even if phone/address match.
- source-contract source brand still visible fails unless it is the effective business or authorized target.
- saved-brand retry behavior remains keyed to `missing required visible fact: business_name`.

- `tests/test_flyer_facts.py`
  - `Create a Special Biryani's Flyer...` with a saved Lakshmi profile keeps `business_name=Lakshmi's Kitchen` and `campaign_title=Special Biryani's`.

- `tests/test_flyer_renderer.py`
  - prompt distinguishes business/brand from title for campaign-title flyers.

## Rollout And Risk

Risk: too-loose brand QA could allow wrong-business flyers.

Mitigation:

- keep exact business-name requirement for saved-brand/source-edit requests;
- require both profile phone and full profile address anchors when exact account brand is absent;
- add wrong-brand negative tests;
- preserve source-contract forbidden-substring checks.

Risk: conflicts with active Flyer work.

Mitigation:

- keep helper pure and small;
- touch only `semantic_brief.py`, `visual_qa.py`, `render.py`, and focused tests unless red tests prove more is required;
- rebase/merge current `origin/main` before coding and before PR.

## Design Review Results Folded In

- Product design review required campaign title not be accepted as business identity; split `allowed_identity_names` from `allowed_headline_names`.
- Product design review required both profile phone and full profile address before skipping exact business-name visibility; tightened `account_anchor_satisfied`.
- Product design review required raw customer text never mutate account identity inside flyer creation; added invariant.
- Implementation review required preserving explicit business-name override behavior already pinned by tests; changed wording from account-only identity to effective business display identity.
- Implementation review required saved-brand retry to keep exact `missing required visible fact: business_name`; added explicit QA rule and tests.
- Implementation review recommended exact/source-aware wrong-brand checks only; design now avoids broad NER in this slice.
