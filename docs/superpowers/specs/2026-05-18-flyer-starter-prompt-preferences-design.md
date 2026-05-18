# Flyer Starter Prompt Preferences Design

**Drift-check tag:** extends-Hermes

**New primitives introduced:** rollback-safe starter prompt preference maps on `FlyerCustomerStore`, account preference commands, preference-aware starter prompt routing.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress and send | yes - deployed Hermes gateway, WhatsApp bridge, and `cf-router` | use it |
| Flyer prompt templates | yes - merged `agents.flyer.starter_briefs` catalog | extend copy helper only |
| Customer/account state | yes - existing Flyer JSON state and Pydantic store | extend top-level store metadata only |
| Preference command handling | partial - existing Flyer account command/audit path | extend account commands |
| Flyer creation after edited request | yes - existing project creation/rendering path | unchanged |

Hermes Skills Hub check: no purpose-built Flyer Studio starter-prompt preference skill; this is product routing/state behavior on top of Hermes messaging and Flyer Studio. Source: https://hermes-agent.nousresearch.com/docs/skills

awesome-hermes-agent ecosystem check: no relevant external skill/plugin for business-specific starter prompt preference. Verdict: keep this local to Flyer Studio.

## Problem

PR #102 added useful category starter prompts, but the flow still needs product discipline:

- New users should see a sample prompt at the moment it helps them start.
- Existing users should not be spammed with a full sample every time they tap a CTA or type a vague phrase.
- Customers who have learned the system should be able to turn sample prompts off, and later turn them back on.
- Opt-out text must be deterministic and must not fall through to the generic Hermes LLM.

## Decision

Use account-wide starter prompt behavior for the business account:

- `auto`: default. The account can receive one automatic full starter prompt.
- `off`: never send a full starter prompt automatically; ask a short clarification instead.

Re-enabling with `show sample prompts again` sets mode back to `auto` and resets the sent count to zero, so the next helpful moment can show the category starter again.

This keeps the UX simple and avoids per-requester state complexity. The copy says "for this business account" so a requester understands the setting is shared.

Any authorized requester for the Flyer account may toggle the setting. This is deliberately lower-risk than plan/payment/account-number changes: it only changes whether examples are shown, is reversible, and the copy makes the account-wide scope explicit.

## State Model

Do not add fields to `FlyerCustomerProfile`. It has `extra="forbid"`, so a nested new field would create a rollback hazard when old code reads new state.

Add top-level maps to `FlyerCustomerStore`, which already has `extra="ignore"`:

```python
starter_prompt_preferences: dict[str, Literal["auto", "off"]] = Field(default_factory=dict, max_length=5000)
starter_prompt_sent_counts: dict[str, int] = Field(default_factory=dict, max_length=5000)
```

Helper methods live on the store:

- `starter_prompt_mode(customer_id) -> str`
- `set_starter_prompt_mode(customer_id, mode)`
- `claim_starter_prompt_send(customer_id) -> bool`
- `release_starter_prompt_claim(customer_id) -> None`

Rollback behavior is acceptable:

- New code reads old state with default maps.
- Old code reads new state because store-level extras are ignored.
- If old code writes state during rollback, preference maps may be dropped, but Flyer Studio still works and returns to default starter behavior.

## Flow Rules

**First-time onboarding**

When a trial/active setup completes in Text Mode or no explicit mode, claim the automatic starter prompt with `store.claim_starter_prompt_send(customer_id)` before returning the reply. If the claim succeeds, include the starter prompt. If it fails, return the concise ready copy.

The claim increments the sent count under the same state lock used for the customer read-modify-write. That makes duplicate automatic starter sends unlikely even with fast repeated messages. If bridge delivery later fails, the claim remains consumed; this intentionally favors anti-spam and avoids repeated long examples after transient delivery uncertainty. Users can restore one more automatic sample with `show sample prompts again`, which resets the sent count.

Guided Mode never gets the full starter prompt.
When Guided Mode is chosen during first-time setup, mark the starter prompt as consumed for that account. Guided questions are the first-use aid, and the next vague existing-customer message should ask a short clarification rather than sending a full sample prompt.

If onboarding confirmation includes a trailing flyer request such as `CONFIRM. Create ...`, do not claim or send a starter prompt. Complete the account, acknowledge readiness briefly, and route the trailing request to project creation through the existing compound-confirm path.

**Existing customers**

Repeated campaign CTA or setup retries stay concise. `_send_flyer_active_customer_ready` and `_existing_account_ready_reply` do not append the full starter prompt.

For vague starts such as `Create flyer`:

- Active project branch runs first.
- If there is no active project and mode is `auto` with sent count `0`, atomically claim the starter prompt, send the category starter prompt, and do not create a project.
- If mode is `auto` but sent count is already positive, send a short clarification.
- If mode is `off`, send the same short clarification.

Detailed flyer requests continue directly to project creation.

Payment-pending, suspended, and cancelled customers get account/payment guidance, not starter prompts or language/mode intake.
After any campaign CTA or vague-start detection, resolve the customer before starting intake. If the customer exists and status is `payment_pending`, `suspended`, or `cancelled`, send `flyer_customer_not_active_reply` and return `skip`.

## Commands

Opt-out commands:

- `don't show sample prompts`
- `dont show sample prompts`
- `stop sample prompts`
- `hide sample prompts`
- `turn off sample prompts`
- `disable sample prompts`
- `stop showing examples`
- `no sample prompts`
- `no examples`
- `don't show examples`
- `hide examples`
- `stop examples`

Opt-in commands:

- `show sample prompts again`
- `enable sample prompts`
- `turn on sample prompts`
- `bring back sample prompts`
- `show examples again`
- `bring back examples`

The account command detector normalizes sender-block-wrapped text through visible-message stripping before matching. This handles live inbound text such as:

```text
[shift-agent-sender v=1 role=customer ...]
don't show sample prompts
```

Account command handling resolves customers by phone or `primary_chat_id`, so LID-only registered customers can change the setting.

If a starter-preference command is recognized, `cf-router` must return `skip` for every outcome: no phone, no customer, subprocess failure, or unhandled result. Unknown customer copy should say the setting can be changed after account setup. Runtime failure copy should say the setting could not be updated and ask the customer to try again. The generic LLM must never see recognized preference commands.

The account layer adds `find_customer_by_sender(phone, chat_id)` on `FlyerCustomerStore`. It first tries `find_customer_by_phone`, then falls back to a unique `primary_chat_id` match for LID-only customers.

## Component Changes

`src/platform/schemas.py`

Add top-level store maps and helper methods on `FlyerCustomerStore`.

`src/agents/flyer/starter_briefs.py`

Add:

- `STARTER_PROMPT_OPT_OUT_HINT`
- `include_opt_out_hint` parameter on `starter_brief_message`

Full starter prompt messages include:

```text
Tip: reply "don't show sample prompts" anytime to turn off future examples for this business account.
```

`src/agents/flyer/account.py`

Add preference command parsing and state updates through existing `handle_account_command`.

Starter prompt preference commands bypass the admin-only account mutation gate. Any authorized requester matched by phone or `primary_chat_id` may toggle the setting. Higher-risk account mutations remain admin-gated.

Off reply:

```text
Flyer Studio
------------
Sample prompts are off for this business account. Reply "show sample prompts again" to turn them back on.
```

On reply:

```text
Flyer Studio
------------
Sample prompts are on for this business account. I will show one helpful example when it can save time.
```

Audit with existing `flyer_account_updated` using command `starter_prompt_mode`.

`src/agents/flyer/onboarding.py`

When completing trial setup, compute `should_include_starter` from the store before building the reply. If starter is included, record it sent on the store before writing.

Existing-account ready replies remain concise and do not include full starter prompts.

`src/agents/flyer/intake.py`

Text Mode ready replies accept an explicit `should_include_starter` boolean. If starter is included, record it sent on the store before writing.

`src/plugins/cf-router/actions.py`

Changes:

- Make `is_flyer_account_command` sender-block-safe.
- Add preference phrases to account command detection.
- Make `find_flyer_customer_by_sender` merge store-level starter preference metadata into returned customer dicts under transient namespaced keys: `_starter_prompt_mode` and `_starter_prompt_sent_count`. These enriched dicts are never persisted or passed into Pydantic customer validation.
- Add `flyer_starter_prompts_enabled(customer)`.
- Add `flyer_starter_prompt_already_sent(customer)`.
- Add `flyer_vague_request_clarification_reply(customer)`.
- Add `claim_flyer_starter_prompt_send(customer_id)`.
- Add `release_flyer_starter_prompt_claim(customer_id)`.

`claim_flyer_starter_prompt_send` uses the customer state lock and `safe_io.atomic_write_text` with existing local-test fallback style. It re-reads the state under lock, verifies mode is not `off` and sent count is zero, increments the count, writes, and returns true. `release_flyer_starter_prompt_claim` decrements only when the count is positive and is used only when a synchronous send definitely failed before delivery uncertainty.

`src/plugins/cf-router/hooks.py`

Routing order changes:

1. Campaign CTA
2. Account command
3. Intake
4. Account/state/reference/media flows as currently ordered
5. Existing onboarding
6. Guest order
7. Active project
8. Vague-start starter/clarification
9. New project

The important guarantee is that account preference commands and active projects outrank vague-start starter prompts.

For Campaign CTA, payment-pending/suspended/cancelled customers are handled inside the CTA branch before any intake restart. Active/trial CTA retries stay concise and never include full starter prompts.

## Tests

Focused tests cover:

- Store defaults and rollback-safe top-level map validation.
- Starter prompt opt-out hint copy.
- Account opt-out/opt-in commands update store maps.
- LID-only `primary_chat_id` customer can opt out.
- Sender-block-wrapped preference commands are detected.
- Account command lookup failures, subprocess failures, and unhandled results do not fall through to the generic LLM.
- Trial completion records the first starter prompt sent.
- Text Mode respects opted-out / already-sent behavior.
- Existing-account CTA retry does not include a full starter prompt.
- Compound `CONFIRM. Create ...` suppresses the starter prompt and routes the trailing request.
- Guided Mode setup consumes starter auto-eligibility so a later vague start asks for a short clarification.
- Opt-out command beats an existing intake/guided session.
- Atomic claim test proves the second claim returns false after the first claim writes state.
- Vague starts:
  - opted-in and never-sent customer gets starter prompt.
  - opted-out customer gets short clarification.
  - already-sent customer gets short clarification.
  - active project state routes to project handling, not starter prompt.
- Payment-pending CTA gets payment guidance, no starter prompt or intake.

Verification commands:

```powershell
python -m pytest tests/test_flyer_starter_briefs.py tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py -q
python -m py_compile src\agents\flyer\starter_briefs.py src\agents\flyer\onboarding.py src\agents\flyer\intake.py src\agents\flyer\account.py src\plugins\cf-router\actions.py src\plugins\cf-router\hooks.py src\platform\schemas.py
git diff --check
```

## Non-Goals

- No new starter prompt categories.
- No database or external Airtable/Notion template storage.
- No per-requester preference in this pass.
- No change to project creation, rendering, approval, or final package generation.
