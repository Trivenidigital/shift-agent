# Flyer Hermes Semantic Brief Reliability Design

**Drift-check tag:** extends-Hermes

**Goal:** Replace brittle Flyer Studio campaign/offer intake fragments with a Hermes-shaped semantic brief contract that feeds locked facts, rendering, QA, and autorepair without weakening customer-trust gates.

**New primitives introduced:** `FlyerSemanticBrief`, `FlyerSemanticOffer`, `build_semantic_flyer_brief`, semantic locked-fact projection, semantic renderer detail consumption, semantic QA presence checks for campaign/offer facts, and widened non-trust autorepair eligibility.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Free-form semantic understanding | Hermes already owns the messy-language substrate. `src/agents/flyer/intent.py` is the local precedent: schema + safety harness now, Hermes gateway provider later. | Follow the same pattern. Add schema/provider seam and bounded deterministic fallback for incident shapes. Do not build a broad free-form parser. |
| WhatsApp/customer routing | Hermes/cf-router already owns inbound routing and sender identity. | Reuse existing routing. Only adjust the business-scope guard’s candidate normalization. |
| Poster rendering | Existing Flyer renderer consumes locked facts into prompt/manifest text facts. | Add one shared render-detail projection used by both manifest text facts and the real image-generation prompt so new semantic facts cannot be ignored. |
| QA | Existing visual QA uses OpenRouter/Hermes env and locked facts. | Extend semantic presence only for campaign/offer wording; retain hard anchors for identity/contact/location/price/date/source contracts. |
| Autorepair | PR #308 added Hermes-planned autorepair. | Widen classifier vocabulary and keep the existing planner. |

Awesome-Hermes ecosystem check: `https://github.com/0xNyk/awesome-hermes-agent` has no restaurant flyer semantic brief skill. This repo needs its domain-specific contract, but the provider seam keeps the work aligned with Hermes.

## Data flow

1. `create-flyer-project` hydrates `FlyerRequestFields`.
2. `facts.extract_text_facts` calls `build_semantic_flyer_brief(fields, raw_request, customer_context, provider=None)`.
3. The semantic brief validates and normalizes campaign/offer/schedule fields.
4. Locked facts are projected:
   - `campaign_title`
   - `pricing_structure`
   - `offer:0..N`
   - `schedule`
   - `promotion_end`
5. `render._render_detail_lines` is the single semantic detail projection used by both `collect_text_facts` and `_poster_copy_plan`, so manifest and image prompt cannot drift.
6. `visual_qa.run_visual_qa` checks:
   - strict exact/numeric anchors for identity/contact/location/price/date/schedule,
   - semantic campaign-title equivalence for concise titles.
7. If QA still fails for non-trust campaign/offer/item visibility, `recovery.classify_flyer_qa_for_autorepair` allows the existing Hermes repair planner to regenerate once.

## Semantic brief contract

### Types

Add to `src/agents/flyer/semantic_brief.py`:

```python
@dataclass(frozen=True)
class FlyerSemanticOffer:
    text: str = ""
    kind: Literal["pricing", "bonus", "discount", "condition", "other"] = "other"
    required: bool = True

@dataclass(frozen=True)
class FlyerSemanticBrief:
    campaign_title: str = ""
    business_identity: str = ""
    pricing_structure: str = ""
    secondary_offers: tuple[FlyerSemanticOffer, ...] = ()
    schedule: str = ""
    promotion_end: str = ""
    style: str = ""
    stored_contact_policy: Literal["use_profile", "explicit", "unspecified"] = "unspecified"
```

`business_identity` is advisory only. For registered customers, it must never overwrite `profile_locked_facts`.

### Provider seam

```python
SemanticBriefProvider = Callable[[str, FlyerRequestFields, Mapping[str, str]], FlyerSemanticBrief | Mapping[str, object] | None]
```

`build_semantic_flyer_brief` accepts an optional provider. If provider returns valid structured output, source-ground it before use. If missing/invalid/unsupported, use deterministic bounded cleanup over existing fields/raw text.

This mirrors `intent.py`: Flyer code owns validation and safety; Hermes can later own the provider.

### Bounded deterministic cleanup

Support only incident-backed patterns:

- Campaign extraction:
  - Strip leading `create/make/design/build/need a flyer for`.
  - Stop campaign title before pricing/schedule/secondary-offer markers:
    `all items`, `any item`, `free`, `with purchase`, `runs until`, weekday list.
  - Title-case concise sale/special phrases.

- Pricing structure:
  - `All items 5-10% off`
  - `Any item $7.99`

- Secondary offers:
  - `Lucky draw eligible with purchase above $100`
  - `Free Masala Chai with any purchase above $12`

- Schedule/promotion end:
  - weekday pairs such as `Wednesday and Thursday`
  - end phrases such as `until June 25`

Do not infer prices or dates that are absent. This applies to both deterministic cleanup and provider output.

### Source-grounding validation

Before any provider-returned value becomes a locked fact:

- every price/threshold/percentage/date digit anchor must appear in the source text;
- every schedule weekday must appear in the source text;
- every promotion-end date must appear with an expiry relationship in the source text (`until`, `ends`, `expires`, `valid through`, `valid thru`, `runs through`);
- every offer noun phrase must be source-supported by significant source-text tokens;
- `business_identity` is advisory and cannot project to `business_name` when `allow_text_identity=False`.

Unsupported provider values are dropped. If dropping removes all campaign/offer meaning from a provider response, the builder falls back to deterministic bounded cleanup. Provider output must never invent the contract that QA later enforces.

## Locked fact projection

Modify `extract_text_facts`:

- Use semantic `campaign_title` instead of raw `fields.event_or_business_name` when present.
- Add `pricing_structure` fact when present.
- Add `offer:N` facts for secondary offers.
- Add `schedule` and `promotion_end` facts.
- Keep true item facts from explicit item lists.
- Suppress item facts where the item name is generic sale language:
  - `any item`
  - `all items`
  - `above $12`

Existing registered profile identity is protected by callsite contract, not merge priority: when profile facts exist, `extract_text_facts(..., allow_text_identity=False)` must not emit `business_name` from text or semantic `business_identity`. The implementation must not rely on `merge_locked_facts` priority for identity safety because customer-text facts currently outrank profile facts.

## Renderer design

Modify `render.collect_text_facts` / helpers:

- `_render_detail_lines(project)` returns:
  - `pricing_structure`
  - `offer:N` values
  - item lines from existing item facts
- Deduplicate against `_detail_clauses`.
- Add `promotion_end` as a date/detail line if `fields.event_date` is not sufficient.
- Keep `MAX_DETAIL_FACTS` and `MAX_TEXT_FACTS` limits.

`_render_detail_lines(project)` must be called from both:

- `collect_text_facts()` for manifest/QA-side fact list;
- `_poster_copy_plan()` / `_poster_copy_block()` for the real image-generation prompt.

Renderer tests must prove F0106/F0107 semantic facts appear in collected text facts and in `_poster_copy_block()` or equivalent prompt text before image generation.

## Business-scope guard design

Modify `_extract_requested_business_scope` normalization:

1. Extract candidate with current patterns.
2. Remove campaign suffixes from the candidate:
   - `store wide sale(s)`
   - `sale(s)`
   - `special(s)`
   - `promo/promotion`
   - `discount`
   - `offer`
3. Re-evaluate the remaining candidate with `_looks_like_business_scope`.
4. If nothing remains, return empty.

Expected:

- `Create a flyer for Diwali store wide sales` => no scope block.
- `Create flyer for Patel Grocery store-wide sale` under Lakshmi => requested scope `Patel Grocery`, block.

## QA design

Add helpers in `visual_qa.py`:

- `_semantic_campaign_present(normalized_text, fact_value)`:
  - normalize punctuation/apostrophes,
  - remove only generic `flyer/poster/banner` filler,
  - pass if the concise campaign core appears as a headline/title phrase,
  - if the locked title contains commercial intent words (`sale`, `special`, `promo`, `discount`, `offer`), require at least one such commercial modifier to remain visible. Bare `Diwali` must not satisfy `Diwali Sale`.

- `_semantic_offer_present(raw_text, fact_value)`:
  - require every numeric anchor from the fact (`$7.99`, `$12`, `5-10%`, `100`) to appear,
  - require key nonnumeric offer tokens such as `free`, `masala chai`, `lucky draw`, `off`, `eligible`.

- `_semantic_promotion_end_present(raw_text, fact_value)`:
  - require month/day anchors,
  - require an expiry/deadline token near the date: `until`, `ends`, `expires`, `valid through`, `valid thru`, `runs through`, `promotion runs until`.

Do not use semantic relaxation for:

- `business_name`
- `contact_phone`
- `location`
- source contract required/replacement facts
- phone/address/date/price numeric anchors

## Autorepair design

Update `classify_flyer_qa_for_autorepair`:

- Eligible:
  - `missing required visible fact: campaign_title`
  - `missing required visible fact: headline`
  - `missing required visible fact: offer:N`
  - `missing required visible fact: pricing_structure`
  - `missing required visible fact: item:N:name` only when real offer/item/detail locked facts exist

- Hard stop remains:
  - wrong business/brand
  - contact/phone/address
  - wrong price/price mismatch
  - provider/dependency failures remain manual
  - schedule/date/promotion-end missing or wrong blockers are not autorepair-eligible unless the planner is only asked to make an already-locked date/schedule more visible without changing it

Tighten `_project_has_offer_context` to inspect locked/render facts, not raw request alone.

Extend `repair_instruction_is_safe` to reject schedule/date/time mutation language:

- `change/replace/update schedule`
- `change/replace/update date`
- `change/replace/update time`
- `change/replace/update promotion end`
- `change/replace/update expiry/deadline`

## Tests

### RED tests first

1. `tests/test_flyer_semantic_brief.py`
   - F0106 parser produces `Diwali Sale`, `All items 5-10% off`, lucky draw offer.
   - F0107 parser produces `Evening Snacks Sale`, `Any item $7.99`, Masala Chai offer, Wednesday/Thursday, June 25.
   - Registered-customer `business_identity` never projects as business name.

2. `tests/test_flyer_create_project.py`
   - F0106 locked facts are clean.
   - F0107 locked facts are clean and no `item:0:name = any item`.

3. `tests/test_flyer_renderer.py`
   - F0106/F0107 semantic facts appear in `collect_text_facts`.
   - F0106/F0107 semantic facts appear in `_poster_copy_block()` or `_image_prompt`, proving the real image prompt consumes them.

4. `tests/test_cf_router_flyer_routing.py`
   - Diwali store-wide campaign does not block.
   - Patel Grocery store-wide sale does block under Lakshmi.

5. `tests/test_flyer_visual_qa.py`
   - Visible `Diwali Sale` satisfies campaign fact.
   - Visible `EVENING SNACKS SALE` satisfies campaign fact.
   - Missing `$7.99`, `$12`, or June 25 still fails.
   - Bare `Diwali` does not satisfy `Diwali Sale`.
   - `June 25` without an expiry token does not satisfy `promotion_end`.

6. `tests/test_flyer_autorepair.py`
   - Campaign/offer blocker is eligible.
   - Generic event with stray item blocker is manual_required.
   - Trust risks hard-stop.
   - Unsafe repair instructions that mutate date/schedule/time/promotion end are rejected.

7. `tests/test_flyer_generate_concepts.py`
   - Do not use auto sidecars as proof of rendering because sidecars can mirror locked facts.
   - Use monkeypatched `run_visual_qa` with realistic first-pass blockers to prove no `manual_required:unknown_blocker_pattern` skip.
   - Separately inspect renderer prompt/block tests for actual semantic-fact rendering.
   - Final persisted status must not be campaign-title manual review for the F0106/F0107 replay path.

## Verification

Before PR:

```powershell
python -m pytest tests/test_flyer_semantic_brief.py tests/test_flyer_create_project.py tests/test_flyer_renderer.py tests/test_flyer_visual_qa.py tests/test_flyer_autorepair.py tests/test_flyer_generate_concepts.py tests/test_cf_router_flyer_routing.py -q
python -m py_compile src/agents/flyer/semantic_brief.py src/agents/flyer/facts.py src/agents/flyer/render.py src/agents/flyer/visual_qa.py src/agents/flyer/recovery.py src/plugins/cf-router/actions.py
git diff --check origin/main...HEAD
```

Post-deploy:

1. Run deploy smoke.
2. Verify no new `regulated_send_*` rows.
3. Re-run F0106/F0107 generation only if operator-safe and confirm one of:
   - `flyer_assets_delivered`,
   - `flyer_closure_customer_notified`,
   - explicit operator handoff note.

## Safety invariants

- Registered profile business/contact/location remain authoritative.
- Semantic `business_identity` cannot overwrite registered profile identity.
- Wrong-business scope still blocks explicit other-business briefs.
- Numeric price/threshold/date anchors remain strict.
- Missing contact/location stays fail-closed.
- Source-contract negative assertions stay exact.
- Autorepair cannot change business, phone, address, price, schedule, date, time, promotion end, expiry, or deadline.
