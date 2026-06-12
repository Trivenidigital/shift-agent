# Flyer Semantic Brief Contract Plan - 2026-05-26

**Drift-check tag:** extends-Hermes

**New primitives introduced:** `semantic_brief` policy helpers for Flyer Studio only. No new storage backend, no new queue, no new deployment path.

**Hermes-first analysis:**

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp identity and routing | yes - existing Hermes/cf-router identity, `identify-sender`, sender-role gate | use existing; do not trust message text for account identity |
| Structured extraction from customer text | yes - Hermes LLM gateway/structured-output pattern already used by Flyer reference extraction and intent layer | keep deterministic parser as baseline; add a narrow semantic contract that can later accept Hermes classifier output |
| Vision/OCR QA | yes - existing Flyer visual QA uses Hermes/OpenRouter vision path | reuse; change what it validates, not the provider |
| Recovery/ARE | yes - existing recovery incidents, worker bundles, worker drafts, deploy gates | reuse; do not grant live-send/deploy authority in this slice |
| Payments/account changes | yes - existing regulated action registry/payment state machine work | out of scope; keep fail-closed behavior unchanged |

Awesome-Hermes ecosystem check: no turnkey Flyer marketing-brief skill is present in the known local codebase; the right path is to extend Flyer’s existing Hermes-backed extraction/QA substrate rather than introduce a new external skill.

## Problem

Flyer Studio still treats customer messages like rigid form fields. A normal request such as:

> Create a Special Biryani's Flyer using golden background...

should mean:

- account identity: stored customer profile, e.g. `Lakshmi's Kitchen`
- campaign/title: `Special Biryani's`
- contact/address: stored profile values
- item facts: chicken/goat biryani prices
- style: golden background, item pictures stand out

Today a generated flyer can visibly show the campaign title while visual QA blocks it as `missing required visible fact: business_name`, because QA requires the stored business name as an exact required fact. The ARE worker correctly diagnosed this for F0103 but suggested a risky parser extension that could turn campaign titles into business-name overrides.

## Goals

- Separate account business identity from customer-facing campaign title before generation.
- Make QA validate customer trust risk, not exact field labels.
- Preserve hard gates for wrong business, wrong price, missing contact/address when the request says to use stored details, unauthorized copied brands, and payment/account state.
- Keep the implementation small and conflict-aware while other sessions are active.
- Keep ARE in bounded-draft mode for code fixes; this slice improves the product path and leaves autonomous promotion as a follow-up unless already implemented by another PR.

## Non-Goals

- No payment or account-command changes.
- No new database/storage backend.
- No live customer send from a recovery worker.
- No broad LLM prompt rewrite.
- No automatic deploy authority for ARE in this PR.

## Proposed Approach

1. Add `src/agents/flyer/semantic_brief.py`.
   - It derives a read-only `SemanticVisibilityPolicy` from the full `FlyerProject`, including `locked_facts`, raw request, fields, assets, and `reference_extractions`.
   - It is not persisted and does not introduce new schema fields.
   - It classifies top-level identity facts into:
     - `account_business_name`
     - `campaign_title`
     - `visible_brand_names_allowed`
     - `brand_visibility_required_exact`
     - `brand_visibility_preferred`
     - `contact_required`
     - `location_required`
   - It keeps `business_name` from `customer_profile` as account identity. Flyer creation never updates or re-sources account identity; account-name changes must stay on the regulated account action registry/admin path.

2. Wire creation/facts to preserve campaign title.
   - Do not accept the ARE draft direction of treating `Create <X> Flyer` as a business-name override.
   - Ensure `Special Biryani's` stays `campaign_title`, while `business_name` remains the stored account identity.

3. Update render prompts to express the semantic contract.
   - Prompt should say the title/headline may be campaign/product/service copy.
   - Stored business/contact/location should appear as brand/footer/contact details when required/preferred.
   - Avoid forcing the stored business into the main title.

4. Update visual QA to use semantic policy.
   - Required campaign title remains required.
   - Contact/phone remains required when stored contact is used or request asks to use stored details.
   - Location remains required when stored address is used or request asks to use stored details.
   - Business name exact visibility is preferred by default, but not blocking if campaign title plus verified account anchors are visible.
   - Business name remains blocking when the customer explicitly asked to use saved business name/logo/brand, when saved brand assets are active in the generation path, or when the request is a source-preserving brand edit.
   - Wrong-brand visibility is always blocking: if OCR/vision sees a distinct organization/brand name that is not the account business, campaign title, or authorized source/target brand, fail the preview even if phone/address match.
   - Existing source-contract negative assertions stay unchanged: forbidden source text, copied brands, replaced phone/address/name, and required source-preserving replacements remain hard blockers.

5. Add focused regression coverage.
- `Special Biryani's` with saved Lakshmi profile passes QA when OCR includes campaign title, phone, and address but not the exact stored business name.
- Same request still fails if campaign title is absent.
- Same request still fails if contact phone is absent.
- Explicit saved-brand request still fails if stored business name is absent.
- Same request fails if OCR shows an unrelated brand such as `Other Restaurant`.
- Source/reference edit tests continue to fail on forbidden source brand text unless authorized/replaced by source-contract rules.
- Parser test proves `Create Special Biryani's Flyer` does not override account business name.

## Hard Invariants

These remain blocking regardless of semantic relaxation:

- missing campaign title when customer supplied one
- missing or wrong customer-provided prices, offer prices, and item-price pairs
- missing or wrong stored phone when the request says to use stored details or the phone is selected as the account anchor
- missing or wrong stored address when the request says to use stored details or the address is selected as the account anchor
- visible placeholder/debug/instruction text
- visible unrelated organization/brand name that is not account, campaign, or authorized source/target brand
- forbidden source text still visible after a source-preserving edit
- required source-preserving replacements missing
- account/payment/admin commands bypassing deterministic regulated-action gates

## Review Questions For Plan Reviewers

1. Is relaxing business-name QA safe enough when contact/location still match the stored profile?
2. Does the wrong-brand negative gate cover the trust risk created by no longer requiring exact business-name visibility for every normal campaign flyer?
3. Should the pure semantic policy helper live in a new module or stay inside `visual_qa.py`?
4. Is there a higher-risk interaction with reference-scope authorization, source edits, or account-name updates?

## Plan Review Results Folded In

- Product/safety review required wrong-brand visibility as a hard negative gate; added to approach, tests, and invariants.
- Product/safety review required account identity not be changed through flyer creation; tightened policy to keep account updates on regulated account action paths.
- Code/drift review required the helper to accept full `FlyerProject` and inspect `reference_extractions`; added.
- Code/drift review warned about saved-brand retry behavior keyed to `missing required visible fact: business_name`; plan now preserves that blocker for saved-brand/logo/source-edit contexts and changes only normal campaign flyers.
- Code/drift review recommended no schema/storage change; plan now states the policy is pure/read-only.

## Implementation Guardrails

- Work only in branch `codex/flyer-semantic-brief`.
- Avoid touching cf-router, account/payment handlers, or recovery-worker deploy authority in this PR.
- Use TDD: write red tests before code.
- Keep changes limited to Flyer facts/render/visual QA plus tests and docs.
- If another PR already solves a slice after this branch started, rebase/merge current `origin/main` and drop duplicate code.

## Verification Plan

- `python -m pytest tests/test_flyer_facts.py tests/test_flyer_visual_qa.py tests/test_flyer_renderer.py -q`
- `python -m pytest tests/test_flyer_generate_concepts.py tests/test_flyer_create_project.py -q`
- `python -m pytest tests/test_cf_router_flyer_routing.py -q` if touched behavior can affect routing or project creation.
- `git diff --check`
