**Drift-check tag:** extends-Hermes

# Flyer Studio Production Readiness Backlog

**Date:** 2026-05-19
**Validated code baseline:** `66cb0f3 fix(flyer): harden qa-blocking project facts`
**Current verdict:** QA hotfix is real and necessary, but Flyer Studio is still about **60-65% production-ready**, not 90%.
**Target:** Raise Flyer Studio to a customer-safe production pilot where bad flyers are blocked before delivery, uploaded references are actually understood, exact edits preserve source artwork, and every project state has a deterministic customer response.

## Review And Validation

What I validated before creating this backlog:

- `docs/qa/flyer-studio-e2e-qa-2026-05-19.md` confirms the live QA failures and the post-hotfix readiness verdict: visual/OCR QA, reference extraction, source-template editing, and context isolation remain blockers.
- Local branch state initially did not contain `66cb0f3`; review was rebased to the hotfix commit before validating code.
- Focused Flyer suite passed locally on the hotfix baseline:

```text
python -m pytest tests/test_flyer_onboarding.py tests/test_flyer_guest_order.py tests/test_flyer_create_project.py tests/test_flyer_renderer.py tests/test_flyer_workflow.py tests/test_flyer_starter_briefs.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py tests/test_flyer_delivery_retry.py tests/test_flyer_schemas.py -q
Result: 247 passed
```

Evidence from deployed code:

- `src/agents/flyer/render.py` has a text-manifest gate, but `write_text_manifest()` sets `rendered = list(expected)`. This proves internal consistency, not actual image/OCR correctness.
- `src/agents/flyer/render.py` has a source-edit path using OpenAI image edits, but the QA report shows it was blocked in live QA by missing `OPENAI_API_KEY`.
- `src/agents/flyer/scripts/create-flyer-project` copies reference media into project assets, but it does not run OCR/vision extraction before generation.
- `src/platform/schemas.py` has a Flyer project state machine, but production readiness needs customer-safe status replies and manual-review semantics across every state, not just valid transitions.
- `docs/superpowers/plans/2026-05-18-flyer-source-edit-pipeline.md` and `docs/superpowers/plans/2026-05-15-flyer-quality-phase2.md` cover partial implementation. This backlog treats those as already-started work, not new discovery.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp/media ingress and delivery | yes - existing Hermes gateway, cf-router, bridge media sends | use it |
| Sender identity and role gating | yes - sender block, `identify-sender`, Flyer account state | use it |
| Project state, audit, and asset storage | yes - Flyer JSON state, `safe_io`, Pydantic schemas, existing audit pattern | use it |
| Image/reference ingestion | yes - Hermes media cache plus Flyer project reference assets | use it |
| OCR/document extraction | yes - Hermes Skills Hub lists productivity/media capabilities including OCR-adjacent skills; project docs already identify `productivity/ocr-and-documents` | use Hermes/deployed OCR first, add only Flyer-specific schema mapping |
| Vision understanding | yes - Hermes runtime/docs and prior project docs identify vision capability | use it for reference/menu/logo/template role analysis |
| Image generation | yes/partial - existing Flyer OpenRouter renderer and Hermes creative/image ecosystem | keep current provider path; add QA and fallback gates |
| Source-preserving exact edits | partial - current in-tree OpenAI image-edit path exists; no complete deployed Flyer approval loop was proven healthy | finish provider readiness, preflight, and manual fallback |
| Visual/OCR QA against generated flyer | none found as a turnkey Flyer product skill | build local QA gate around Hermes OCR/vision |
| Manual review queue | partial - admin dashboard/manual states exist, but no complete work queue/status lifecycle | extend local Flyer ops surface |

Awesome Hermes Agent ecosystem check: reviewed `https://github.com/0xNyk/awesome-hermes-agent`; it lists broad OCR/MCP/image-generation integrations, but no production-ready WhatsApp Flyer Studio pipeline that replaces this product logic. Verdict: reuse Hermes substrate and build only Flyer-specific extraction, QA, state, and operator workflow.

Hermes Skills Hub check: `https://hermes-agent.nousresearch.com/docs/skills` currently lists 690 skills and includes creative/image and productivity capabilities. No direct "Flyer Studio production QA" skill was found; OCR/vision/image primitives should still be reused before custom substrate is added.

## Production Bar

Do not call Flyer Studio 90% production-ready until all gates below are true:

- New flyer requests are project-isolated and cannot inherit stale facts from older projects.
- Required customer facts are structured, locked, and visible in project state before generation.
- Uploaded menu/reference/logo/template media is classified and OCR/vision-extracted before generation or exact edit.
- Customer-facing previews pass visual/OCR QA against locked facts, not just text-manifest self-consistency.
- Exact edits preserve uploaded artwork or are queued for designer/manual review without pretending they were automated.
- Every project state has a deterministic customer status response.
- Golden scenarios include real customer-style media and regressions for repeated corrections, vague starts, exact edits, and stale-project separation.
- Manual review is an explicit queue with operator visibility and customer-safe status replies.

## Backlog

### P0-1: Project Context Isolation And Stale-State Guard

**Problem:** QA found stale session/project context can bleed into new flyers. Existing routing has many fixes for active-project precedence, but there is no single project-isolation invariant or regression harness that proves each new request starts clean.

**Scope:**

- Define the allowed context for a new project: customer profile, brand kit, active uploaded assets for this message/session, and current message text.
- Exclude prior flyer prompts, old revisions, pending stale projects, and previous model context unless the customer explicitly says to reuse them.
- Add an invariant check in project creation and generation: every prompt/fact must be traceable to current request, registered customer facts, or current attached assets.
- Add stale-project expiry or explicit "continue existing project?" status behavior for old awaiting-approval/manual-edit projects.

**Acceptance:**

- A customer with an old awaiting-approval project can send a complete new flyer request and receives a new clean project.
- A customer correction still applies to the latest project for that account across phone/LID/authorized requester identities.
- Tests cover old active project plus new media-backed request, old manual-edit queue plus new poster request, and repeated "create flyer" retries.

### P0-2: Structured Fact Extraction And Locked Flyer Facts

**Problem:** `FlyerRequestFields` currently overloads `event_or_business_name`, `notes`, and style text. Hotfixes cleaned bad labels, but production needs a typed fact layer before image generation.

**Scope:**

- Introduce or emulate a locked fact model for business name, headline, tagline, offer/menu items, prices, schedule/date/time, address, phone, language, style, logo/reference/template roles, and customer category.
- Split parser output into customer-visible facts and non-visible instructions.
- If a required fact is missing, ask one clear question and do not create a misleading render-ready project.
- Persist extraction provenance per fact: customer text, customer profile, OCR/vision, uploaded asset, or operator/manual.

**Acceptance:**

- Price/service examples such as `$20 men haircut`, `Idly $7`, and `Any Item for $9.99` are stored as item/price facts, not leaked instruction text.
- Headline/tagline requests are present as explicit fields and are checked before delivery.
- Missing required facts produce one deterministic prompt, not a random generated flyer.

### P0-3: Reference Media OCR/Vision Extraction

**Problem:** The current project can attach `reference_image`, but F0057 proved attached sample-menu facts were not extracted into controlled fields before generation.

**Scope:**

- Classify uploaded media role: logo, menu/price list, old flyer/reference, exact-edit template, generic inspiration, or unsupported.
- Run OCR/vision extraction before project readiness when the customer says "use/extract from attached/sample/reference."
- Store extracted item names, prices, dates, times, addresses, phone numbers, and visible business names as locked facts with confidence.
- Ask for confirmation only when extraction is low-confidence or conflicts with typed customer facts.
- Keep Hermes OCR/document skills as the first provider; add a narrow fallback only if deployed Hermes capability cannot read the asset type.

**Acceptance:**

- A sample menu image with item names/prices creates a project whose facts include those exact items/prices before generation.
- A logo upload is not treated as a menu/reference source.
- A reference flyer's visible stale facts do not override newer customer-typed facts.
- Tests include restaurant menu, grocery price list, service price sheet, logo-only, old flyer recreation, and unsupported/low-quality image.

### P0-4: Real Visual/OCR QA Gate Before Preview And Delivery

**Problem:** The current text manifest can pass while the actual image contains `[price]` or omits headline/tagline. It verifies what the renderer claims, not what the customer sees.

**Scope:**

- After generation, run OCR/vision against the actual preview/final image.
- Compare extracted visible text and visual attributes against locked facts:
  - business name
  - headline/tagline
  - item names/prices
  - offer/date/time/schedule
  - address/phone when requested
  - logo presence when required
  - category/style sanity
  - no placeholders such as `[price]`, `[phone]`, lorem ipsum, fake QR codes, or random unrelated themes
- On failure, regenerate once with the QA findings injected as constraints.
- If still failing, move to manual review and send a clear customer status.

**Acceptance:**

- F0054-style `[price]` cannot be sent to the customer.
- F0055-style missing headline/tagline/logo semantics fails QA even if the image looks premium.
- QA output is stored next to the project and visible in admin/operator tooling.
- There is a break-glass path only for manual/operator sends with explicit reason and audit.

### P0-5: Source-Preserving Exact Edit Readiness

**Problem:** Exact edits are partially implemented, but F0056 was provider/config-blocked. A request like "change date on this flyer" must not recreate from scratch.

**Scope:**

- Make source-edit provider readiness a startup/deploy smoke gate or mark exact edits unavailable before accepting them.
- Support image inputs first; explicitly queue PDFs or unsupported formats unless `nano-pdf`/provider path is proven.
- Preserve source artwork, layout, logo, colors, and all unchanged readable text.
- Add visual diff/QA for exact edits: only requested text regions should change unless manual review approves broader changes.
- Ensure final packages derive from the exact approved edit preview.

**Acceptance:**

- Missing provider key returns "designer-assisted editing queued" before spending customer time, not a failed generation.
- Exact edit preview does not add generic titles like "Uploaded Flyer Template."
- "Remove extra 08:00" preserves the uploaded flyer composition and removes only the duplicate text.
- Customer "any update?" on the queued edit returns queue status.

### P0-6: Customer-Safe Project State Machine And Status Replies

**Problem:** Schema transitions exist, but production needs deterministic customer UX across intake, generation, approval, revision, manual queue, delivery, and status checks.

**Scope:**

- Define customer-facing response for every `FlyerWorkflowStatus` and intake/onboarding state.
- Ensure "status", "any update", "is it ready", and similar checks never route to generic LLM or revision parsing.
- Add max-attempt and timeout behavior for generation/QA/revision loops.
- Ensure failed generation, QA failure, provider unavailable, and manual-review queue are first-class states or state annotations.

**Acceptance:**

- Every state has a tested status response.
- No infinite loop can create repeated empty projects or repeated missing-info prompts.
- The admin dashboard can show stuck projects by state, age, and last failure reason.

### P0-7: Golden Scenario Regression Suite With Visual Checks

**Problem:** Existing deterministic suites are strong, but live QA still found wrong visuals and OCR/fact failures. Production needs scenario coverage that looks like real customers.

**Scope:**

- Build 50-100 golden scenarios from real customer-style prompts and assets:
  - restaurant menu flyer
  - grocery promotion
  - halal meat flyer
  - salon/service flyer
  - tutor/class flyer
  - temple/event flyer
  - logo upload
  - exact template edit
  - reference flyer recreation
  - price correction
  - language-specific flyer
  - vague prompt recovery
  - repeated corrections
  - stale/new project separation
- Include visual/OCR assertions, not only JSON/schema assertions.
- Split into free deterministic CI, spend-gated real-model eval, and manual visual spot-checks.

**Acceptance:**

- A release candidate cannot be called production-ready unless the deterministic suite passes and the latest spend-gated eval has no P0/P1 customer-facing failures.
- Scenario failures become backlog items with IDs and owner/status.
- The suite includes at least 10 uploaded-reference media cases and 10 revision/exact-edit cases.

### P0-8: Manual Review Queue And Operator Escape Hatch

**Problem:** Some requests require human/design review. The current system can mark `manual_edit_required`, but production needs a usable queue, customer messaging, and operator completion path.

**Scope:**

- Add or finish admin dashboard queue for manual/source-edit projects.
- Queue entries should show original request, uploaded assets, locked facts, QA failures, requested edit, customer account, age, and status.
- Customer-facing copy should say the edit needs designer-assisted handling when automation cannot safely complete it.
- Operator completion should attach approved assets, update status, and send preview/final package through normal delivery gates.

**Acceptance:**

- Manual review queue is visible without SSH/state-file spelunking.
- Customer status checks return queue position or "queued for designer-assisted editing" style copy.
- Operator sends are audited and do not bypass visual/text QA unless break-glass is explicit.

### P1-1: Provider Health, Deploy Smoke, And Runtime Readiness Gate

**Scope:**

- Add provider preflight for OpenRouter image generation, OpenAI/source edit, OCR/vision, and bridge media send.
- Gate production readiness on configured provider health, not only import/syntax smoke.
- Keep spend-gated real-model checks explicit.

**Acceptance:**

- `main-vps` can report Flyer Studio provider readiness in one command.
- Source-edit unavailable is visible before the first customer exact-edit request.

### P1-2: Category And Style Policy Hardening

**Scope:**

- Maintain a category-safe style matrix for food, grocery, salon, tax/accounting, cleaning, education, temple/event, real estate, and generic local services.
- Feed business category/city/language into prompt policy without exposing internal Hermes copy.
- QA blocks category mismatch, e.g. salon rendered as restaurant/grocery.

**Acceptance:**

- Non-food priced services never receive restaurant/menu/festival defaults unless explicitly requested.
- Tests include at least five non-food local-business categories.

### P1-3: Platform-Specific Output Truthfulness

**Scope:**

- Either generate truly platform-specific assets for WhatsApp, square post, story/status, and PDF, or rename outputs honestly.
- Add story-safe composition QA before calling an asset "Instagram story."

**Acceptance:**

- No final package claims a channel-specific creative unless the generation/QA path enforces that channel's visual constraints.

### P1-4: Productized Revision Semantics

**Scope:**

- Expand deterministic revision parsing for item swaps, price corrections, headline/tagline changes, language changes, logo/reference changes, and layout requests.
- Revisions must invalidate stale previews/finals and block approval until regenerated or manually completed.

**Acceptance:**

- Clear corrections after delivery reopen the project or create a linked revision without losing the approved asset history.
- Ambiguous corrections ask a precise clarification question.

### P2-1: Reporting, Metrics, And Release Dashboard

**Scope:**

- Track project counts by state, render failure causes, QA failure causes, manual queue age, provider failures, regenerate rate, customer approval rate, and delivery success rate.
- Add weekly operator report or dashboard widget.

**Acceptance:**

- Readiness estimates move from subjective percentages to measurable gates.

## Suggested Execution Order

1. P0-1 Project Context Isolation
2. P0-2 Structured Fact Extraction
3. P0-3 Reference OCR/Vision Extraction
4. P0-4 Visual/OCR QA Gate
5. P0-5 Source-Preserving Exact Edit Readiness
6. P0-6 State Machine And Status Replies
7. P0-7 Golden Scenario Suite
8. P0-8 Manual Review Queue
9. P1 provider/category/platform/revision hardening
10. P2 metrics dashboard

## 90% Readiness Exit Criteria

- Focused Flyer unit/regression suite passes.
- Golden deterministic scenario suite passes with 0 P0/P1 failures.
- Spend-gated real-model eval passes with 0 P0 failures and documented/manual-approved P1 exceptions.
- Source-edit provider preflight is green, or exact edits are explicitly queued/manual with no automated promise.
- OCR/vision QA blocks placeholder/wrong-fact/wrong-category outputs.
- Admin/manual review queue can clear a failed/queued project without SSH.
- `main-vps` readiness command shows gateway active, WhatsApp bridge connected, configured providers healthy, and no stale critical manual queue items.

## Review Notes

The other session's recommendation is validated with one adjustment: source-preserving exact edit support is not wholly absent; it exists as a partial in-tree implementation. The current production blocker is provider readiness, QA strength, and operator/customer lifecycle around that path.

The biggest conceptual correction is that image generation should be treated as one worker inside a controlled production pipeline. The product boundary is not "make a nice image"; it is "preserve customer facts, prove the visible output, and never send uncertainty as if it were done."
