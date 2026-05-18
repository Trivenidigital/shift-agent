# Flyer Starter Prompt Preferences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Flyer Studio starter prompts appear at the right customer-flow moments and let customers turn those sample prompts off or back on after they understand the system.

**Architecture:** Reuse the existing `starter_briefs.py` catalog and existing Flyer WhatsApp routing. Add rollback-safe store-level preference metadata keyed by `customer_id`, centralize the "should show a starter prompt?" decision, and route opt-out/opt-in text through the existing account command path so the state change is audited and deterministic.

**Tech Stack:** Python 3, Pydantic v2 schemas, JSON-on-disk Flyer customer state, cf-router pre-gateway hooks, pytest.

---

**Drift-check tag:** extends-Hermes

**New primitives introduced:** `FlyerCustomerStore.starter_prompt_preferences`, `FlyerCustomerStore.starter_prompt_sent_counts`, starter-prompt preference parsing, preference-aware starter prompt helpers.

## Drift Check

| Work type | Read first | Evidence | Decision |
|---|---|---|---|
| Schema work | `src/platform/schemas.py` | `FlyerCustomerProfile` uses `extra="forbid"`, while `FlyerCustomerStore` uses `extra="ignore"`. Repo comments warn that new nested `extra="forbid"` fields can break rollback. | Do not add a nested customer-profile field. Add top-level store metadata so old code can ignore or drop it without crashing. |
| Account commands | `src/agents/flyer/account.py` and `src/agents/flyer/scripts/manage-flyer-account` | Account state changes already flow through `handle_account_command`, `FileLock`, `atomic_write_text`, and `flyer_account_updated` audit rows. | Add opt-out/opt-in as a low-risk account command instead of direct JSON writes in `cf-router`. |
| Onboarding/intake | `src/agents/flyer/onboarding.py`, `src/agents/flyer/intake.py` | Starter prompts are currently appended to trial activation and Text Mode ready replies. Guided mode explicitly suppresses full starter prompts. | Preserve those placements, but gate them through the customer preference. |
| Routing / dispatcher work | `src/plugins/cf-router/hooks.py`, `src/plugins/cf-router/actions.py` | Active/trial vague starts already send category starter prompts and skip project creation. Account command intercept runs before the vague-start branch. | Add preference commands to the account-command detector; for opted-out vague starts, send a short clarification instead of a full starter prompt. |
| Deploy work | `src/agents/shift/scripts/shift-agent-deploy.sh`, `src/agents/shift/scripts/shift-agent-smoke-test.sh` | PR #102 installed and smoked `flyer_starter_briefs.py`; deploy used staging script because installed deploy script lagged new wiring. | No new deploy artifact expected; smoke should still import the existing modules. |

Conclusion: this is not a new workflow. It is a narrow preference layer on top of the merged starter-brief flow.

## Hermes-First Analysis

Checked Hermes Skills Hub on 2026-05-18: it lists messaging, creative, productivity, and agent skills, but no Flyer Studio starter-prompt preference mechanism. The hub confirms broad creative and messaging substrate exists, while this feature is local product behavior in the Flyer customer state. Source: https://hermes-agent.nousresearch.com/docs/skills

Checked awesome-hermes-agent on 2026-05-18: it catalogs Hermes skills/plugins/integrations and confirms Hermes has broad messaging, skills, memory, cron, and MCP substrate, but no business-specific Flyer Studio starter prompt preference. Source: https://github.com/0xNyk/awesome-hermes-agent

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp message receive/send | yes - deployed Hermes gateway and cf-router | use it |
| Customer profile state | yes - existing Flyer JSON state and Pydantic schemas | extend top-level store metadata narrowly |
| Starter brief content | yes - merged `agents.flyer.starter_briefs` catalog | reuse it |
| Preference persistence | none specific | add rollback-safe top-level store maps |
| Opt-out command interpretation | none specific | add deterministic account command parsing |
| Flyer generation after edited prompt | yes - existing project creation/rendering | unchanged |

awesome-hermes-agent ecosystem verdict: no external skill/plugin should be introduced; this belongs in the existing Flyer Studio customer preference layer.

Per-step Hermes-first checklist:

| Step | Owner | Decision |
|---|---|---|
| Receive user text | [Hermes] | Use existing gateway/cf-router. |
| Resolve customer/business category | [Hermes] | Use existing customer lookup. |
| Decide whether to show sample prompt | [net-new] | Check store-level mode and sent count. |
| Send sample prompt or short clarification | [Hermes] | Use existing WhatsApp send helper. |
| Update preference on user command | [net-new] | Add deterministic account command update. |
| Create flyer from edited request | [Hermes] | Existing project creation unchanged. |

## Product Flow

Starter prompts should appear only when they save the customer time:

1. **First-time trial activation, Text Mode, or no explicit creation mode:** show the category starter prompt only if mode is `auto` and this account has not already received an automatic starter prompt.
2. **Guided Mode:** do not show the full starter prompt; guided questions are the prompt.
3. **Existing active/trial customer taps a ready/setup CTA:** do not append a full starter prompt. CTA retries stay concise and idempotent.
4. **Existing active/trial customer sends a vague start (`Create flyer`, `Need flyer`):**
   - `auto` and automatic starter count is zero: send the category starter prompt and do not create a project.
   - `auto` and automatic starter was already sent: send a short clarification and do not create a project.
   - `off`: send a short clarification prompt and do not create a project.
5. **Detailed flyer request:** never prepend or send a starter prompt; create the flyer normally.
6. **Revision/approval/finalization/payment states:** never send starter prompts.
7. **Active project exists:** route to the existing active-project logic before starter/clarification logic so vague text cannot interrupt approval, revision, manual-edit, or generation states.

Supported preference commands:

- Turn off: `don't show sample prompts`, `dont show sample prompts`, `stop sample prompts`, `hide sample prompts`, `turn off sample prompts`, `disable sample prompts`, `stop showing examples`
- Turn on: `show sample prompts again`, `enable sample prompts`, `turn on sample prompts`, `bring back sample prompts`, `show examples again`, `bring back examples`

Preference is account-wide for the business account. The off confirmation must say that clearly and include the restore command:

`Sample prompts are off for this business account. Reply "show sample prompts again" to turn them back on.`

Customer-facing opt-out copy should be present on full starter prompt messages:

`Tip: reply "don't show sample prompts" anytime to skip these examples.`

## File Structure

- Modify `src/platform/schemas.py`: add rollback-safe top-level store maps plus helper methods to `FlyerCustomerStore`.
- Modify `src/agents/flyer/starter_briefs.py`: add opt-out hint text and preference-aware message formatting.
- Modify `src/agents/flyer/onboarding.py`: gate starter prompt appending through the helper and include a starter prompt in existing-account ready replies only when enabled.
- Modify `src/agents/flyer/intake.py`: gate Text Mode starter prompt through the helper.
- Modify `src/agents/flyer/account.py`: parse opt-out/opt-in commands, update store-level starter prompt preference metadata, and audit the preference change.
- Modify `src/plugins/cf-router/actions.py`: detect preference commands as account commands; make command detection sender-block-safe; enrich returned customer dicts with store-level preference/sent-count metadata; add preference-aware starter/clarification helpers for dict customers.
- Modify `src/plugins/cf-router/hooks.py`: route account preference commands before intake, fail closed on recognized account-command errors, move active-project handling ahead of vague-start starter logic, and branch vague starts on preference/sent-count metadata.
- Modify `tests/test_flyer_starter_briefs.py`: helper and message-contract tests.
- Extend `tests/test_flyer_onboarding.py`: first-time, existing-account, account-command preference tests.
- Extend `tests/test_cf_router_flyer_routing.py`: existing-customer vague-start preference tests.
- Update `tasks/todo.md`: track plan, reviews, design, build, PR, and final review results.

## State and Rollback Compatibility

Do **not** store this preference inside `FlyerCustomerProfile`. That model is `extra="forbid"`, and once new code writes the nested field, rollback to old code can fail to parse customer rows.

Store the preference at the top level of `FlyerCustomerStore`, which already has `extra="ignore"`:

```python
starter_prompt_preferences: dict[str, Literal["auto", "off"]] = Field(default_factory=dict, max_length=5000)
starter_prompt_sent_counts: dict[str, int] = Field(default_factory=dict, max_length=5000)
```

Rollback behavior:

- New code can read old state because both maps default empty.
- Old code can read new state because store-level extras are ignored.
- If old code writes the state during rollback, the maps may be dropped, but the agent keeps working and simply returns to default starter-prompt behavior.

## Task 1: Schema and Starter Helper Contract

**Files:**
- Modify: `src/platform/schemas.py`
- Modify: `src/agents/flyer/starter_briefs.py`
- Test: `tests/test_flyer_starter_briefs.py`
- Test: `tests/test_flyer_onboarding.py`

- [ ] **Step 1: Add failing schema/helper tests**

Add tests:

```python
def test_customer_store_default_keeps_starter_prompts_auto():
    customer = _customer()
    store = FlyerCustomerStore(customers=[customer])
    assert store.starter_prompt_mode(customer.customer_id) == "auto"
    assert store.claim_starter_prompt_send(customer.customer_id) is True

def test_customer_store_claim_allows_only_one_auto_starter_prompt():
    customer = _customer()
    store = FlyerCustomerStore(customers=[customer])
    assert store.claim_starter_prompt_send(customer.customer_id) is True
    assert store.claim_starter_prompt_send(customer.customer_id) is False

def test_customer_store_preference_top_level_is_rollback_safe():
    raw = json.loads(FlyerCustomerStore(customers=[_customer()]).model_dump_json())
    raw["starter_prompt_preferences"] = {"CUST0001": "off"}
    raw["starter_prompt_sent_counts"] = {"CUST0001": 1}
    store = FlyerCustomerStore.model_validate(raw)
    assert store.starter_prompt_mode("CUST0001") == "off"

def test_starter_message_can_include_opt_out_hint():
    message = starter_briefs.starter_brief_message(
        "salon",
        business_name="Demo Salon",
        include_opt_out_hint=True,
    )
    assert 'reply "don\\'t show sample prompts"' in message

```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_flyer_starter_briefs.py tests/test_flyer_onboarding.py::test_customer_store_default_keeps_starter_prompts_auto tests/test_flyer_onboarding.py::test_customer_store_claim_allows_only_one_auto_starter_prompt tests/test_flyer_onboarding.py::test_customer_store_preference_top_level_is_rollback_safe -q
```

Expected: fails on missing store maps/methods or missing helper parameters.

- [ ] **Step 3: Implement minimal schema/helper changes**

Add to `FlyerCustomerStore`:

```python
starter_prompt_preferences: dict[str, Literal["auto", "off"]] = Field(default_factory=dict, max_length=5000)
starter_prompt_sent_counts: dict[str, int] = Field(default_factory=dict, max_length=5000)

def starter_prompt_mode(self, customer_id: str) -> str:
    return self.starter_prompt_preferences.get(customer_id, "auto")

def set_starter_prompt_mode(self, customer_id: str, mode: Literal["auto", "off"]) -> None:
    self.starter_prompt_preferences[customer_id] = mode
    if mode == "auto":
        self.starter_prompt_sent_counts[customer_id] = 0

def claim_starter_prompt_send(self, customer_id: str) -> bool:
    if self.starter_prompt_mode(customer_id) == "off":
        return False
    if self.starter_prompt_sent_counts.get(customer_id, 0) > 0:
        return False
    self.starter_prompt_sent_counts[customer_id] = self.starter_prompt_sent_counts.get(customer_id, 0) + 1
    return True

def release_starter_prompt_claim(self, customer_id: str) -> None:
    if self.starter_prompt_sent_counts.get(customer_id, 0) > 0:
        self.starter_prompt_sent_counts[customer_id] -= 1
```

Add to `starter_briefs.py`:

```python
STARTER_PROMPT_OPT_OUT_HINT = 'Tip: reply "don\\'t show sample prompts" anytime to skip these examples.'
```

Change `starter_brief_message` signature:

```python
def starter_brief_message(
    category: str,
    *,
    business_name: str = "",
    include_opt_out_hint: bool = False,
) -> str:
```

Append the hint only when `include_opt_out_hint` is true.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m pytest tests/test_flyer_starter_briefs.py tests/test_flyer_onboarding.py::test_customer_store_default_keeps_starter_prompts_auto tests/test_flyer_onboarding.py::test_customer_store_claim_allows_only_one_auto_starter_prompt tests/test_flyer_onboarding.py::test_customer_store_preference_top_level_is_rollback_safe -q
```

Expected: pass.

## Task 2: Account Preference Commands

**Files:**
- Modify: `src/agents/flyer/account.py`
- Modify: `src/plugins/cf-router/actions.py`
- Test: `tests/test_flyer_onboarding.py`
- Test: `tests/test_cf_router_flyer_routing.py`

- [ ] **Step 1: Add failing account command tests**

Add tests proving:

```python
def test_customer_can_turn_sample_prompts_off_and_on(tmp_path):
    state_path = tmp_path / "customers.json"
    customer = _customer().model_copy(update={"status": "trial"})
    store = FlyerCustomerStore(customers=[customer])
    state_path.write_text(store.model_dump_json(), encoding="utf-8")

    off = handle_account_command(
        state_path=state_path,
        sender_phone="+17329837841",
        sender_role="customer",
        chat_id="17329837841@s.whatsapp.net",
        text="don't show sample prompts",
    )
    assert off.ok is True
    assert "Sample prompts are off for this business account" in off.reply_text
    updated_store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated_store.starter_prompt_mode(customer.customer_id) == "off"

    on = handle_account_command(
        state_path=state_path,
        sender_phone="+17329837841",
        sender_role="customer",
        chat_id="17329837841@s.whatsapp.net",
        text="show sample prompts again",
    )
    assert on.ok is True
    updated_store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated_store.starter_prompt_mode(customer.customer_id) == "auto"
```

Add LID-only coverage:

```python
def test_lid_only_customer_can_turn_sample_prompts_off(tmp_path):
    state_path = tmp_path / "customers.json"
    customer = _customer().model_copy(update={"status": "trial", "primary_chat_id": "201975216009469@lid"})
    state_path.write_text(FlyerCustomerStore(customers=[customer]).model_dump_json(), encoding="utf-8")

    result = handle_account_command(
        state_path=state_path,
        sender_phone=None,
        sender_role="customer",
        chat_id="201975216009469@lid",
        text="[shift-agent-sender v=1 role=customer]\ndon't show sample prompts",
    )

    assert result.ok is True
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.starter_prompt_mode(customer.customer_id) == "off"
```

Also add cf-router classifier tests:

```python
def test_sample_prompt_preference_text_is_account_command():
    actions = _load_actions()
    assert actions.is_flyer_account_command("don't show sample prompts")
    assert actions.is_flyer_account_command("show sample prompts again")
    assert actions.is_flyer_account_command("[shift-agent-sender v=1 role=customer]\nstop showing examples")
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_flyer_onboarding.py::test_customer_can_turn_sample_prompts_off_and_on tests/test_flyer_onboarding.py::test_lid_only_customer_can_turn_sample_prompts_off tests/test_cf_router_flyer_routing.py::test_sample_prompt_preference_text_is_account_command -q
```

Expected: fails because commands are unrecognized.

- [ ] **Step 3: Implement command parsing**

In `account.py`, add regex helpers:

```python
STARTER_PROMPT_OFF_RE = re.compile(r"\b(?:don'?t|do not|stop|hide|turn off|disable)\b.*\b(?:sample|starter)\s+prompts?\b|\bstop\s+showing\s+examples\b", re.IGNORECASE)
STARTER_PROMPT_ON_RE = re.compile(r"\b(?:show|enable|turn on|bring back)\b.*\b(?:sample|starter)\s+prompts?\b|\b(?:show|bring back)\s+examples\s+again\b|\bbring\s+back\s+examples\b", re.IGNORECASE)

def _parse_starter_prompt_preference(text: str) -> str:
    if STARTER_PROMPT_OFF_RE.search(text or ""):
        return "off"
    if STARTER_PROMPT_ON_RE.search(text or ""):
        return "auto"
    return ""
```

Before generic status/help handling in `handle_account_command`, normalize `body` through the same visible-message stripping semantics as `flyer_visible_message_text`, resolve the customer by phone or `primary_chat_id`, then apply:

```python
preference = _parse_starter_prompt_preference(body)
if preference:
    store.set_starter_prompt_mode(customer.customer_id, preference)
    customer = customer.model_copy(update={"updated_at": now})
    _replace_customer(store, customer)
    write_customer_store(state_path, store)
    _audit_account_update(
        audit_log_path,
        customer_id=customer.customer_id,
        command="starter_prompt_mode",
        actor_phone=sender_phone,
        actor_role=sender_role,
        allowed=True,
        reason=f"starter_prompt_{preference}",
    )
    reply = (
        "Flyer Studio\n------------\n"
        'Sample prompts are off for this business account. Reply "show sample prompts again" to turn them back on.'
        if preference == "off"
        else "Flyer Studio\n------------\nSample prompts are on for this business account. I will show one helpful example when it can save time."
    )
    return AccountResult(True, True, reply, customer.customer_id, customer.status)
```

In `actions.is_flyer_account_command`, normalize through `flyer_visible_message_text` and add equivalent phrases so cf-router routes them to account handling before vague-start handling.

In `hooks.py`, move `_try_flyer_account_intercept(...)` before `_try_flyer_intake_intercept(...)`. For recognized account commands:

- Do not return `None` just because `identify-sender` has no phone; use `actions.find_flyer_customer_by_sender(phone, chat_id)` to support `primary_chat_id` / LID-only accounts.
- If `trigger_flyer_account_command` fails, send a Flyer Studio scoped recovery reply and return `{"action": "skip", "reason": "cf-router flyer account command failed"}` so the generic LLM never sees the command.

Add a regression:

```python
def test_sample_prompt_preference_command_failure_does_not_fall_through(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = []
    monkeypatch.setattr(actions, "is_flyer_account_command", lambda _text: True)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "trigger_flyer_account_command", lambda **_kwargs: (False, "boom", {}))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_account_intercept("don't show sample prompts", "201975216009469@lid", SimpleNamespace(message_id="m1"))

    assert result == {"action": "skip", "reason": "cf-router flyer account command failed"}
    assert "I could not update that setting" in sent[0]
```

- [ ] **Step 4: Run focused account tests**

Run:

```powershell
python -m pytest tests/test_flyer_onboarding.py::test_customer_can_turn_sample_prompts_off_and_on tests/test_flyer_onboarding.py::test_lid_only_customer_can_turn_sample_prompts_off tests/test_cf_router_flyer_routing.py::test_sample_prompt_preference_text_is_account_command tests/test_cf_router_flyer_routing.py::test_sample_prompt_preference_command_failure_does_not_fall_through -q
```

Expected: pass.

## Task 3: Preference-Aware First-Time and Ready Replies

**Files:**
- Modify: `src/agents/flyer/onboarding.py`
- Modify: `src/agents/flyer/intake.py`
- Test: `tests/test_flyer_onboarding.py`

- [ ] **Step 1: Add failing first-time/ready reply tests**

Add tests proving:

```python
def test_text_mode_ready_respects_starter_prompt_opt_out():
    customer = _customer().model_copy(update={"business_category": "salon"})
    reply = flyer_intake._text_mode_ready_reply("en", customer=customer, should_include_starter=False)
    assert "Here is a starter flyer request" not in reply

def test_existing_account_ready_reply_does_not_include_starter_on_cta_retry():
    customer = _customer().model_copy(update={"business_category": "salon"})
    reply = flyer_onboarding._existing_account_ready_reply(customer)
    assert "Here is a starter flyer request" not in reply

def test_trial_completion_records_first_starter_prompt_sent(tmp_path):
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    state_path = tmp_path / "customers.json"
    session = FlyerOnboardingSession(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        status="confirming_summary",
        started_at=now,
        updated_at=now,
        business_name="Demo Salon",
        business_address="111 Main St",
        public_phone="+17329837841",
        business_whatsapp_number="+17329837841",
        authorized_request_number="+17329837841",
        business_category="salon",
        preferred_language="en",
        plan_id="trial",
    )
    state_path.write_text(FlyerCustomerStore(onboarding_sessions=[session]).model_dump_json(), encoding="utf-8")

    result = handle_onboarding_reply(
        state_path=state_path,
        chat_id=session.chat_id,
        sender_phone="+17329837841",
        text="CONFIRM",
        message_id="m-confirm",
        now=now,
    )

    assert "Here is a starter flyer request" in result.reply_text
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.claim_starter_prompt_send(result.customer_id) is False
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_flyer_onboarding.py::test_text_mode_ready_respects_starter_prompt_opt_out tests/test_flyer_onboarding.py::test_existing_account_ready_reply_does_not_include_starter_on_cta_retry tests/test_flyer_onboarding.py::test_trial_completion_records_first_starter_prompt_sent -q
```

Expected: fails because existing helpers are not preference-aware yet.

- [ ] **Step 3: Implement preference gates**

In `onboarding.py` and `intake.py`, pass an explicit `should_include_starter` boolean from the store method. Do not have reply helpers read customer state implicitly.

Update starter append checks:

```python
if (
    include_starter_brief
    and creation_mode != "guided"
    and customer
    and customer.status in {"trial", "active"}
    and should_include_starter
):
    reply = f"{reply}\n\n{starter_brief_message(..., include_opt_out_hint=True)}"
```

When onboarding or Text Mode is about to append a starter prompt, call `store.claim_starter_prompt_send(customer.customer_id)` while holding the customer store lock. Append the starter only when the claim returns true. If a setup flow enters Guided Mode, call the same claim helper without appending a full starter prompt so the guided questions consume first-use assistance. `_existing_account_ready_reply(customer)` must remain concise and must not append full starter prompt text.

- [ ] **Step 4: Run onboarding/intake tests**

Run:

```powershell
python -m pytest tests/test_flyer_onboarding.py -q
```

Expected: pass.

## Task 4: Preference-Aware Existing-Customer Vague Starts

**Files:**
- Modify: `src/plugins/cf-router/actions.py`
- Modify: `src/plugins/cf-router/hooks.py`
- Test: `tests/test_cf_router_flyer_routing.py`

- [ ] **Step 1: Add failing cf-router tests**

Add tests:

```python
def test_vague_flyer_start_for_opted_out_customer_asks_short_clarification(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = []
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {
        "customer_id": "CUST0001",
        "business_name": "Demo Salon",
        "business_category": "salon",
        "status": "trial",
        "_starter_prompt_mode": "off",
        "_starter_prompt_sent_count": 0,
    })
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not create project")))

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="Create flyer",
        chat_id="17329837841@s.whatsapp.net",
        message_id="m-vague",
    ), None, None)

    assert result == {"action": "skip", "reason": "cf-router flyer starter preference off clarification sent"}
    assert "Here is a starter flyer request" not in sent[0]
    assert "What should this flyer promote?" in sent[0]

def test_vague_flyer_start_after_first_starter_asks_short_clarification(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = []
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {
        "customer_id": "CUST0001",
        "business_name": "Demo Salon",
        "business_category": "salon",
        "status": "trial",
        "_starter_prompt_mode": "auto",
        "_starter_prompt_sent_count": 1,
    })
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not create project")))

    result = hooks.pre_gateway_dispatch(SimpleNamespace(text="Create flyer", chat_id="17329837841@s.whatsapp.net", message_id="m-vague"), None, None)

    assert result == {"action": "skip", "reason": "cf-router flyer starter already sent clarification sent"}
    assert "Here is a starter flyer request" not in sent[0]
    assert "What should this flyer promote?" in sent[0]

def test_vague_start_during_active_project_routes_to_project_not_starter(monkeypatch):
    hooks, actions = _load_plugin_modules()
    monkeypatch.setattr(actions, "is_vague_flyer_start", lambda _text, has_media=False: True)
    monkeypatch.setattr(hooks, "_try_flyer_active_project_intercept", lambda *_args: {"action": "skip", "reason": "active project"})
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not send starter")))

    result = hooks.pre_gateway_dispatch(SimpleNamespace(text="Create flyer", chat_id="17329837841@s.whatsapp.net", message_id="m-active"), None, None)

    assert result == {"action": "skip", "reason": "active project"}
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
python -m pytest tests/test_cf_router_flyer_routing.py::test_vague_flyer_start_for_opted_out_customer_asks_short_clarification tests/test_cf_router_flyer_routing.py::test_vague_flyer_start_after_first_starter_asks_short_clarification tests/test_cf_router_flyer_routing.py::test_vague_start_during_active_project_routes_to_project_not_starter -q
```

Expected: fails because current branch sends full starter prompts.

- [ ] **Step 3: Implement action/helper and hook branch**

In `actions.py`, add:

```python
def _starter_prompt_metadata(store: dict, customer_id: str) -> dict[str, object]:
    return {
        "_starter_prompt_mode": (store.get("starter_prompt_preferences") or {}).get(customer_id, "auto"),
        "_starter_prompt_sent_count": int((store.get("starter_prompt_sent_counts") or {}).get(customer_id, 0) or 0),
    }

def flyer_starter_prompts_enabled(customer: dict) -> bool:
    return str(customer.get("_starter_prompt_mode") or "auto").strip().lower() != "off"

def flyer_starter_prompt_already_sent(customer: dict) -> bool:
    try:
        return int(customer.get("_starter_prompt_sent_count") or 0) > 0
    except (TypeError, ValueError):
        return False

def flyer_vague_request_clarification_reply(customer: dict) -> str:
    return (
        "Flyer Studio\n------------\n"
        "What should this flyer promote? Send the offer, products or services, prices, date/time if any, and the style you want. "
        "You can attach a reference flyer, logo, menu, or photos too."
    )
```

Update `find_flyer_customer_by_sender` so returned customer dicts include `_starter_prompt_metadata(...)` under transient namespaced keys only. Add `claim_flyer_starter_prompt_send(customer_id)` that locks `FLYER_CUSTOMERS_PATH`, re-reads state, verifies mode is not `off` and sent count is zero, increments `starter_prompt_sent_counts[customer_id]`, writes with `safe_io.atomic_write_text` fallback, and returns true. Add `release_flyer_starter_prompt_claim(customer_id)` for synchronous definite send failures.

In `hooks.py`, move `_try_flyer_active_project_intercept(...)` before the vague-start starter/clarification branch. Then replace the active/trial vague branch with:

```python
if customer and customer.get("status") in {"trial", "active"}:
    if actions.flyer_starter_prompts_enabled(customer) and not actions.flyer_starter_prompt_already_sent(customer) and actions.claim_flyer_starter_prompt_send(customer.get("customer_id") or ""):
        reply = actions.flyer_starter_brief_reply(customer)
        reason = "flyer_starter_brief"
        result_reason = "cf-router flyer starter brief sent"
    else:
        reply = actions.flyer_vague_request_clarification_reply(customer)
        reason = "flyer_starter_clarification"
        result_reason = (
            "cf-router flyer starter preference off clarification sent"
            if not actions.flyer_starter_prompts_enabled(customer)
            else "cf-router flyer starter already sent clarification sent"
        )
    ...
    return {"action": "skip", "reason": result_reason}
```

- [ ] **Step 4: Run routing tests**

Run:

```powershell
python -m pytest tests/test_cf_router_flyer_routing.py -q
```

Expected: pass.

## Task 5: CTA Payment-State Regression Coverage

**Files:**
- Modify: `src/plugins/cf-router/hooks.py`
- Test: `tests/test_cf_router_flyer_routing.py`

- [ ] **Step 1: Add failing payment-pending CTA tests**

Add a test proving campaign CTA retries for payment-pending customers do not start intake and do not show starter prompts:

```python
def test_payment_pending_customer_campaign_cta_gets_payment_guidance(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = []
    monkeypatch.setattr(actions, "flyer_campaign_source", lambda _text: "start_trial")
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {
        "customer_id": "CUST0001",
        "business_name": "Demo Salon",
        "status": "payment_pending",
    })
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)
    monkeypatch.setattr(hooks, "_start_flyer_intake", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not start intake")))

    result = hooks._try_flyer_campaign_cta_intercept("Start Free Trial", "17329837841@s.whatsapp.net", SimpleNamespace(message_id="cta"))

    assert result == {"action": "skip", "reason": "cf-router flyer customer not active"}
    assert "waiting for payment" in sent[0].lower()
    assert "Here is a starter flyer request" not in sent[0]
```

- [ ] **Step 2: Implement campaign CTA payment-state handling**

In `_try_flyer_campaign_cta_intercept`, if `customer` exists but is not `trial` or `active`, send `actions.flyer_customer_not_active_reply(customer)` and return `{"action": "skip", "reason": "cf-router flyer customer not active"}`.

- [ ] **Step 3: Run CTA tests**

Run:

```powershell
python -m pytest tests/test_cf_router_flyer_routing.py::test_payment_pending_customer_campaign_cta_gets_payment_guidance -q
```

Expected: pass.

## Task 6: Verification, Task Log, PR

**Files:**
- Modify: `tasks/todo.md`

- [ ] **Step 1: Run focused suite**

Run:

```powershell
python -m pytest tests/test_flyer_starter_briefs.py tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py -q
```

Expected: pass.

- [ ] **Step 2: Run compile and diff checks**

Run:

```powershell
python -m py_compile src\agents\flyer\starter_briefs.py src\agents\flyer\onboarding.py src\agents\flyer\intake.py src\agents\flyer\account.py src\plugins\cf-router\actions.py src\plugins\cf-router\hooks.py src\platform\schemas.py
git diff --check
```

Expected: both pass.

- [ ] **Step 3: Update task log**

Add a `tasks/todo.md` entry under Flyer Studio with:

- branch name
- plan/design/review/build/PR status
- verification commands and results

- [ ] **Step 4: Commit and create PR**

Run:

```powershell
git status --short
git add docs/superpowers/plans/2026-05-18-flyer-starter-prompt-preferences.md docs/superpowers/specs/2026-05-18-flyer-starter-prompt-preferences-design.md src/platform/schemas.py src/agents/flyer/starter_briefs.py src/agents/flyer/onboarding.py src/agents/flyer/intake.py src/agents/flyer/account.py src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py tests/test_flyer_starter_briefs.py tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tasks/todo.md
git commit -m "feat: add flyer starter prompt preferences"
git push -u origin codex/flyer-starter-prompt-preferences
gh pr create --base main --head codex/flyer-starter-prompt-preferences --title "Add Flyer Studio starter prompt preferences" --body-file <generated-pr-body.md>
```

Expected: PR opens against `main`.

## Review Checklist

- Starter prompts never create projects by themselves.
- Detailed flyer requests still bypass starter prompts.
- Guided Mode still never receives full starter prompt text.
- Opted-out vague starts do not create empty projects.
- Opt-out/opt-in is persisted and reversible.
- Existing rows without starter prompt preference maps validate with default `auto`.
- Preference updates are audited.
- No new deploy artifact is required beyond existing modules.
