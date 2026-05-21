# Flyer Contract Lifecycle Design

**Drift-check tag:** extends-Hermes

## Goal

Fix the recurring Flyer Studio contract/lifecycle failure class by separating business identity from campaign title, making registered profile facts authoritative, blocking malformed required facts before generation, and enforcing a single customer-visible copy contract.

## Hermes-first checklist

| Step | Hermes-owned? | Decision |
|---|---|---|
| WhatsApp sender identity/chat routing | yes | reuse existing cf-router/Hermes identity helpers |
| Inbound message capture/audit | yes-ish | reuse existing decisions.log / cf-router audit chokepoints |
| Customer profile lookup | Flyer-specific on Hermes state | reuse existing Flyer customer store helpers |
| Natural language request extraction | Hermes-capable substrate | deterministic contract rules now; Hermes structured extractor deferred |
| Business identity authority | Flyer-specific policy | profile business_name/contact/address win unless explicit override |
| Campaign/offer title extraction | Flyer-specific policy | add locked `campaign_title`, keep `event_or_business_name` as campaign/event/offer field |
| Source-contract / visual QA | Flyer-specific schema, Hermes vision substrate | keep QA strict; add better locked facts before QA |
| Operator/self-eval reporting | Hermes/operator brief substrate | extend existing self-eval only with small read-only tripwires |
| Audit emission | Hermes pattern | existing audit details keep project IDs/internal reasons |
| Provider routing/source-edit | out of scope | no provider policy changes |
| Customer copy | Flyer-specific policy on Hermes messaging substrate | outcome-only, no project IDs/internal queue/provider wording |

## Field And Fact Contract

### Business identity

Authoritative source order for locked `business_name`:

1. Explicit customer override, only when the request says `business name is X`, `change business name to X`, or `replace OLD with NEW` where `OLD` matches the registered business name.
2. Registered `FlyerCustomerProfile.business_name` for trial/active customers.
3. Parsed text only for unregistered/paid-guest project flows where no trial/active profile exists and the value passes sanity checks.

Generic campaign text must never become locked `business_name`. Instruction-like values are invalid if they contain patterns such as `I'd like`, `help me with`, `create flyer`, `flier from`, `include`, or exceed the short-title threshold with command verbs.

Profile lookup must be sender-aware: `create-flyer-project` accepts `--chat-id` and resolves customers by phone plus `primary_chat_id`/authorized sender semantics. This mirrors cf-router identity behavior and prevents LID-only registered customers from losing profile facts.

### Contact and location

Locked `contact_phone` and `location` come from the actual `FlyerCustomerProfile` for registered trial/active customers. Generic `fields.contact_info` and `fields.venue_or_location` are not labeled `customer_profile` unless they came from that profile object.

For this PR, profile contact/location win by default. One-time contact/location override parsing is deferred unless already explicit in source-contract replacement flow.

Paid guest/unregistered fallback: if no trial/active profile is found, sane text-derived `business_name` and `contact_phone` remain allowed with source `customer_text` so paid guest orders do not regress. Missing guest contact still blocks render readiness through existing required-fact behavior.

### Campaign title

`fields.event_or_business_name` remains the campaign/event/offer title. When sane, present, and not equal to the locked business name, it creates:

```text
fact_id: campaign_title
label: Campaign
source: customer_text
required: true
value: e.g. Evening Snacks
```

For brand/logo prompts where `event_or_business_name` equals the registered business name, no `campaign_title` fact is created from that brand value. Renderer then uses an explicit `headline` if present, otherwise a safe offer fallback.

For the F0065 text, expected contract:

- `business_name`: `Lakshmis Kitchn`, source `customer_profile`, required.
- `contact_phone`: `+17329837841`, source `customer_profile`, required.
- `location`: `90 Brybar Dr St Johns FL`, source `customer_profile`, required when profile address exists.
- `campaign_title`: `Evening Snacks`, source `customer_text`, required.
- `event_time`: `16:00` or display schedule `4 PM to 7 PM`.
- notes/details include `5 top South Indian snack items` and Wednesday through Saturday.

## Rendering Contract

Renderer text plan must separate brand and title:

- `Business/brand:` uses locked `business_name`.
- `Title:` uses locked `campaign_title` when present, then locked `headline`, then a safe fallback.
- Location/contact use locked profile facts.
- Menu overlay, poster copy plan, text manifest, and direct generation prompt must all use the same title/business selection so QA checks the same values that generation was asked to render.

This changes the old behavior where `business_name` was both brand and poster title.

## QA Contract

Visual QA continues to enforce every required locked fact. This PR should not add a parallel QA engine.

Required facts for F0065-class registered customers:

- `business_name`
- `campaign_title`
- `contact_phone`
- `location` when registered profile location exists

Visual QA tests must prove missing campaign title fails even when business name is visible, and missing business name fails even when campaign title is visible.

## Pre-Generation Gate

Before the project can be treated as render-ready:

1. Build profile facts from the actual customer object.
2. Build text/campaign facts.
3. Merge facts.
4. Run malformed required-fact blockers:
   - instruction-like `business_name`
   - missing `business_name`
   - missing `contact_phone`
   - missing required `campaign_title` when the parser found one
5. Queue manual review with `reason_code=missing_required_facts` only if the profile/override path cannot repair the contract.

F0065 must not queue manual review; profile facts repair identity upstream.

## Customer Copy Matrix

Forbidden in customer WhatsApp text:

- `F####` project IDs and `project F`
- `created flyer project`
- `queued project`
- `operator`
- `provider`
- `reason_code`
- raw internal generation errors
- `source-preserving workflow`

Allowed in audit/Cockpit details:

- project IDs
- reason codes
- provider/manual-review details
- raw error summaries

Customer-facing copy:

| Context | Copy contract |
|---|---|
| New request processing | `Flyer Studio\n------------\nGot it. I'm creating your flyer now and will send a preview here shortly. Flyer generation usually takes 5-6 minutes.` |
| Intake / missing immediate generation fallback | `Flyer Studio\n------------\nGot it. I have your flyer request and will send an update here shortly.` |
| Manual/review fallback | `Flyer Studio\n------------\nI couldn't finish this automatically. I'll review it and send an update here.` |
| Missing details | Ask for the missing flyer details without project ID. |
| Status check | State the current outcome/status without project ID. |
| Regeneration failure | State that the revised flyer could not be finished automatically yet, without project ID or automation/provider details. |
| Active intake prompt | Ask for the full flyer request/logo/photos without project ID. |

Existing source-edit status copy may say a designer will apply the edit by hand, but must not include source-preserving/provider/reason-code/project-ID wording.

## Duplicate Ack Policy

If `send_flyer_processing_ack()` has already succeeded for a branch:

- On preview success: preview/media is the second customer-visible message, allowed.
- On manual-review fallback: send one manual/review fallback only if it communicates a materially different outcome.
- On generic generation failure: do not send `send_flyer_intake_ack()` as a second initial ack; rely on the processing ack plus audit/operator evidence.

Implementation should use a shared helper for generation failure after processing ack and replace the four equivalent branches:

- existing active project retry
- direct new project
- reference/media new project
- active intake-ready project

Do not change cf-router branch predicates/order, `flyer_source_edit_preflight`, provider resolution, or source-vs-new choice semantics. This PR only changes copy and duplicate-ack behavior at those call sites.

## Tests

Add transcript-level tests, not only helper tests:

- F0065 registered customer project creation: profile business/contact/location, `campaign_title`, no malformed `business_name`.
- LID/`primary_chat_id` registered customer creation: profile facts hydrate with `--chat-id` even if phone-only lookup would miss.
- Paid guest creation: sane text-derived business/contact facts remain render-ready; missing guest contact still blocks.
- Brand/logo registered prompt: brand is not duplicated as required `campaign_title`; headline becomes title.
- Renderer/manifest uses `campaign_title` as title and profile business as brand.
- Visual QA fails when either `business_name` or `campaign_title` is absent from OCR text.
- All ack/status/fallback helpers reject forbidden terms and project IDs.
- Duplicate ack is prevented for the four generation-failure branches.
- PR #150 routing remains green: evening-snacks fresh intent bypasses stale active project; revision/status/approval/update paths still route to active project.
- PR #140/#143/#146 manual/source-edit copy intent remains green.

## Deferred

- Hermes structured extractor for Flyer requests.
- One-time profile contact/location override policy outside explicit source replacement flows.
- Dashboard active-risk lane and push alerts.
- Full source-contract QA enforcement across every source-edit path.
