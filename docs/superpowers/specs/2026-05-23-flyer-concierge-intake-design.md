# Flyer Concierge Intake Design

**Drift-check tag:** extends-Hermes

**New primitives introduced:** one Flyer intake status (`concierge_awaiting_choice`) and one returning-customer concierge prompt.

## Hermes-First Analysis

| Domain | Hermes/in-tree capability found? | Decision |
|---|---|---|
| WhatsApp ingress and identity | yes - Hermes gateway, `cf-router`, `identify-sender`, sender block handling | reuse; no new ingress path |
| Flyer intent detection | yes - `classify_flyer_intent`, `is_vague_flyer_start`, active intake lookup | reuse; no broad new classifier |
| Conversation state | yes - `FlyerIntakeSession` in `FlyerCustomerStore` | extend with one narrow status |
| Project creation and approval | yes - existing brief preview, `create-flyer-project`, quota/access, generation, final approval | reuse unchanged |
| Starter/sample ideas | yes - `sample_idea`, `starter_idea`, and starter prompt preferences | preserve for explicit sample flows; do not use as default concierge opener |

Hermes Skills Hub / awesome-hermes-agent check: no ready Flyer Studio WhatsApp concierge-intake skill was found in the checked ecosystem. Verdict: reuse Hermes/Flyer substrate and build the small missing state transition in-tree.

## Problem

A returning Flyer Studio customer can send a natural opener:

`Hey Flyer-Studio, I'd like you to help me create a flyer`

The current default on `origin/main` recognizes this as a vague flyer start, but routes active/trial customers into the sample-idea picker. That is better than a generic failure, but it still feels less mature than a business assistant. It assumes "pick an idea" instead of asking what the customer wants and offering a choice of interaction style.

The intended product behavior is:

```text
Flyer Studio
------------
Welcome back, Lakshmi's Kitchen. Yes, I am here to help. What are we creating today?

You can tell me in one message, or I can guide you step by step.
```

## Goals

- Returning active/trial customers with vague flyer starts get the warm concierge opener.
- The customer can reply with a complete one-message brief and receive the existing brief preview/approval flow.
- The customer can explicitly reply with guided intent, such as `guide me step by step`, and enter the existing guided flow.
- Still-vague follow-ups like `yes help me` do not become fake flyer briefs; they get one short open prompt.
- Concierge does not consume starter-prompt/sample-idea quota and ignores starter-prompt opt-out state.
- Existing sample-idea flows remain available only when explicitly invoked by existing onboarding or sample paths.
- A vague new-flyer opener from a returning active/trial customer is not swallowed by an existing active project; it starts concierge even when the customer has an active `awaiting_concept_selection`, `awaiting_final_approval`, or `manual_edit_required` project.

## Non-Goals

- No new renderer behavior.
- No new LLM/Hermes intent layer.
- No new project schema, quota contract, payment behavior, or final delivery behavior.
- No rewrite of starter ideas.
- No forced guided mode.

## Routing Design

`cf-router` remains the first-touch owner.

For active/trial customers, the vague-start branch changes from:

```text
vague flyer start -> starter prompt claim -> start_source=sample_idea -> sample ideas
```

to:

```text
vague flyer start -> start_source=concierge -> concierge_awaiting_choice
```

This route must not check:

- `flyer_starter_prompts_enabled`
- `flyer_starter_prompt_already_sent`
- `claim_flyer_starter_prompt_send`

Those controls belong to starter/sample suggestions. Concierge is not a sample prompt; it is the default assistant greeting for returning customers.

This route must run before active-project routing. Active projects still own approvals, status checks, concept selections, and concrete revisions, but a bare vague new-flyer opener such as `Create flyer` or `help me create a flyer` should not be interpreted as a correction to an existing flyer. It should open the concierge prompt.

`_try_flyer_intake_intercept` must add `concierge_awaiting_choice` to `protected_statuses`. Without this, a complete brief sent after the concierge opener can look like a fresh new flyer request and bypass the intake session, skipping brief preview/approval.

`start_source="concierge"` is only a control input to the intake script. The persisted `FlyerIntakeSession.source` remains `"new_flyer"`; do not add `"concierge"` to `FlyerIntakeSource`.

If cf-router writes a new audit reason such as `flyer_concierge_choice`, add that literal to `CfRouterIntercepted.reason` and cover it with a schema/log-entry test. Do not emit an audit reason that schema validation rejects.

## Intake State

Add one status:

```python
FlyerIntakeStatus = Literal[
    "choosing_language",
    "choosing_mode",
    "choosing_sample_idea",
    "concierge_awaiting_choice",
    "text_awaiting_brief",
    ...
]
```

When `handle_intake_message(..., start_source="concierge")` is called for an active/trial customer, it stores:

- `status="concierge_awaiting_choice"`
- `source="new_flyer"`
- customer preferred language
- original opener text
- any media path, if present

It returns `action="concierge_choice"` and the warm welcome-back copy.

## Continuation Behavior

When the session status is `concierge_awaiting_choice`:

- `cancel`, `stop`, or equivalent cancel replies discard the intake session.
- Explicit guided replies enter existing guided mode:
  - `guide`
  - `guided`
  - `guide me`
  - `guide me step by step`
  - `guide me please`
  - `you guide me`
  - `step by step`
  - `step by step please`
  - `ask me questions`
  - `ask questions`
  - `walk me through it`
- Still-vague replies keep the same session and ask:

```text
Flyer Studio
------------
Sure. What is the flyer for? You can send the event, offer, items/prices, date, or anything you already have.
```

- All other replies are treated as the customer’s one-message brief and go into the existing `brief_pending_approval` preview flow with `brief_source="text"`.

Do not map generic `help me` to guided mode. That phrase is too close to the original vague opener and would violate the product decision not to force step-by-step intake.

Ambiguous/non-brief replies such as `yes`, `ok`, `sure`, `please`, `help`, `help me`, `please help`, `yes help me`, `create flyer`, `make flyer`, and `I need a flyer` are not guided intent and are not enough to preview. Keep them in `concierge_awaiting_choice` and send the open prompt.

If the customer attached media to the concierge opener or follow-up, the brief preview should acknowledge it in customer-safe wording, for example: `I will use the attached reference or image if it is relevant.` Do not expose file paths, asset IDs, provider names, queues, project IDs, or internal source terms.

## Customer Copy Policy

Concierge and preview copy must not contain internal terms:

- `concierge`
- `intake`
- `brief_pending`
- `project_id`
- `project`
- `provider`
- `queue`
- `manual`
- `source`
- `audit`
- `workflow`

Customer-visible wording should stay short, warm, and outcome-first.

## Existing Flow Preservation

Existing flows remain unchanged:

- new customer setup and language choice
- quick-flyer guest orders
- explicit sample idea starts
- explicit starter idea flow after trial onboarding when configured
- text mode
- guided mode
- brief approval and project creation
- revisions and final approval

The only default changed is active/trial returning-customer vague starts.

## Tests

Add red-green tests for:

- schema accepts `concierge_awaiting_choice`
- returning customer gets welcome-back concierge copy
- concierge copy avoids sample ideas and internal jargon
- one-message brief after concierge becomes brief preview, not direct project creation
- guided phrase after concierge enters guided collection
- still-vague follow-up asks the open prompt and keeps session in `concierge_awaiting_choice`
- cf-router passes `start_source="concierge"` and preserves `original_text`
- cf-router does not call starter-prompt claim/release for concierge
- starter prompt opted-out and already-sent customers still get concierge
- `_try_flyer_intake_intercept` protects `concierge_awaiting_choice`
- vague starts route to concierge before active-project intercept for `awaiting_concept_selection`, `awaiting_final_approval`, and `manual_edit_required`
- ambiguous follow-ups `yes`, `ok`, `sure`, `help me`, and `please` keep the concierge session and ask the open prompt
- guided variants `ask questions`, `you guide me`, `guide me please`, `walk me through it`, and `step by step please` enter guided mode
- persisted concierge session `source` remains `new_flyer`
- existing explicit sample idea, text mode, guided mode, onboarding handoff, and starter/sample paths keep passing

Focused verification:

```powershell
python -m pytest tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tests/test_flyer_schemas.py tests/test_flyer_starter_briefs.py -q
python -m pytest tests/test_cf_router_plugin.py tests/test_flyer_scripts_static.py -q
```

## Rollback

Rollback is simple: remove the new status, helper copy, and cf-router source change. Existing sample/text/guided flows are preserved, and customer state with `concierge_awaiting_choice` would only exist after this version handles a vague returning-customer opener.

Rollback order:

1. Before deploying rollback code, delete `concierge_awaiting_choice` intake sessions or translate them to a status accepted by the old binary, such as `choosing_mode`.
2. Verify the old schema can load `/opt/shift-agent/state/flyer/customers.json`.
3. Deploy rollback code.

Without step 1, old binaries can fail to validate `customers.json` because `FlyerIntakeStatus` will not know `concierge_awaiting_choice`.
