# Flyer Concierge Intake Implementation Plan

**Drift-check tag:** extends-Hermes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make returning Flyer Studio customers with vague opener messages receive a warm concierge prompt that offers "one message" or "guide me step by step" instead of defaulting to sample ideas or generic failure copy.

**Architecture:** Keep Hermes/cf-router as the first-touch router. Reuse the existing Flyer intake session state machine, but add a returning-customer concierge start mode that stores an intake session and waits for either a full brief or an explicit guided-mode request. Still-vague follow-ups get one short open prompt. Keep project creation, quota, approval, and rendering unchanged.

**Tech Stack:** Python, Pydantic v2 schemas in `src/platform/schemas.py`, cf-router plugin hooks/actions, existing JSON-on-disk Flyer customer state, pytest.

**New primitives introduced:** one Flyer intake status (`concierge_awaiting_choice`) and one helper response for returning-customer vague starts.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress and sender identity | yes - in-tree Hermes gateway plus `cf-router`, `identify-sender`, sender-block validation | use existing ingress and identity plumbing |
| Conversational intent handling | yes - Hermes/cf-router already classifies Flyer intent and vague starts | reuse `is_vague_flyer_start`; do not add broad custom intent parsing |
| Multi-turn state | yes - Flyer `FlyerIntakeSession` and JSON state already track language/mode/guided/text/sample flow | extend the existing intake state machine narrowly |
| Flyer generation | yes - existing `create-flyer-project`, `generate-flyer-concepts`, preview approval, final package flow | leave unchanged |
| Skills ecosystem | Hermes Skills Hub lists many skills, including Creative and Social Media categories, but no ready WhatsApp Flyer Studio concierge-intake skill was found in the checked hub summary | build the small missing product behavior in-tree |

Awesome Hermes Agent ecosystem check: checked `awesome-hermes-agent`; no ready-made Flyer Studio returning-customer concierge intake skill was found. Verdict: reuse Hermes/Flyer substrate and add only the missing conversation state.

## Drift Check

Read before planning:

- `src/plugins/cf-router/hooks.py`: vague active-customer starts currently call `trigger_flyer_intake(... start_source="sample_idea")` and send `flyer_starter_ideas`.
- `src/agents/flyer/intake.py`: existing intake supports language, mode, text awaiting brief, sample idea picker, guided questions, and brief approval.
- `src/agents/flyer/starter_briefs.py`: existing starter ideas are useful after explicit sample selection, but should not be the default returning-customer opener.
- `src/platform/schemas.py`: `FlyerIntakeStatus` and `FlyerIntakeSession` are the schema surface to extend.
- Existing tests: `tests/test_cf_router_flyer_routing.py`, `tests/test_flyer_onboarding.py`, `tests/test_flyer_schemas.py`, `tests/test_flyer_starter_briefs.py`.

This plan extends Hermes. It does not introduce a parallel storage layer, new router, new LLM prompt, new render path, or a custom broad intent brain.

## File Structure

- Modify `src/platform/schemas.py`: add `concierge_awaiting_choice` to `FlyerIntakeStatus`.
- Modify `src/agents/flyer/intake.py`: add concierge start source handling, response copy, choice parser, and state transition from concierge to text/guided/sample.
- Modify `src/plugins/cf-router/hooks.py`: change active-customer vague-start default from `sample_idea` to the new concierge intake source and audit reason, make concierge independent of starter-prompt claims/preferences, and protect `concierge_awaiting_choice` in active intake routing.
- Modify `tests/test_flyer_onboarding.py`: cover concierge opener, full one-message brief continuation, and "guide me" continuation.
- Modify `tests/test_cf_router_flyer_routing.py`: cover cf-router sends the warm concierge opener and does not create a project.
- Modify `tests/test_flyer_schemas.py`: cover the new intake status is schema-accepted.

## Task 1: Schema and Intake Red Tests

**Files:**
- Modify: `src/platform/schemas.py`
- Modify: `tests/test_flyer_schemas.py`
- Modify: `tests/test_flyer_onboarding.py`

- [ ] **Step 1: Add failing schema test**

Add to `tests/test_flyer_schemas.py` near existing `FlyerIntakeSession` status tests:

```python
def test_flyer_intake_accepts_concierge_awaiting_choice_status():
    session = FlyerIntakeSession(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        status="concierge_awaiting_choice",
        source="new_flyer",
        started_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
    )

    assert session.status == "concierge_awaiting_choice"
```

- [ ] **Step 2: Run schema test and verify it fails**

Run:

```powershell
python -m pytest tests/test_flyer_schemas.py::test_flyer_intake_accepts_concierge_awaiting_choice_status -q
```

Expected: fails with a Pydantic literal validation error for `status`.

- [ ] **Step 3: Add failing returning-customer concierge intake tests**

Add to `tests/test_flyer_onboarding.py` near sample/text/guided intake tests:

```python
def test_returning_customer_vague_start_opens_concierge_choice(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 23, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmi's Kitchen",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"business_category": "restaurant", "preferred_language": "en"})
    state_path.write_text(FlyerCustomerStore(customers=[customer]).model_dump_json(indent=2), encoding="utf-8")

    result = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="welcome-back",
        text="Hey Flyer-Studio, I'd like you to help me create a flyer",
        start_source="concierge",
        now=now,
    )

    assert result.action == "concierge_choice"
    assert "Welcome back, Lakshmi's Kitchen" in result.reply_text
    assert "What are we creating today?" in result.reply_text
    assert "You can tell me in one message, or I can guide you step by step." in result.reply_text
    assert "Pick a sample idea" not in result.reply_text
    for internal in ("concierge", "intake", "brief_pending", "project_id", "source", "audit", "workflow"):
        assert internal not in result.reply_text.lower()

    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.intake_sessions[0].status == "concierge_awaiting_choice"
    assert store.intake_sessions[0].creation_mode == ""
```

```python
def test_returning_customer_concierge_accepts_one_message_brief(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 23, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmi's Kitchen",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"business_category": "restaurant", "preferred_language": "en"})
    state_path.write_text(FlyerCustomerStore(customers=[customer]).model_dump_json(indent=2), encoding="utf-8")

    handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="welcome-back",
        text="Create flyer",
        start_source="concierge",
        now=now,
    )

    preview = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="brief",
        text="Create a breakfast specials flyer Saturday 8 AM to 11 AM with Idli $4.99 and Dosa $8.99",
        now=now,
    )

    assert preview.action == "brief_preview"
    assert "I will create this flyer" in preview.reply_text
    assert "breakfast specials" in preview.reply_text
    assert "Reply APPROVE to start" in preview.reply_text
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.intake_sessions[0].brief_source == "text"
    for internal in ("concierge", "intake", "brief_pending", "project_id", "source", "audit", "workflow"):
        assert internal not in preview.reply_text.lower()
```

```python
def test_returning_customer_concierge_can_enter_guided_mode(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 23, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmi's Kitchen",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"business_category": "restaurant", "preferred_language": "en"})
    state_path.write_text(FlyerCustomerStore(customers=[customer]).model_dump_json(indent=2), encoding="utf-8")

    handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="welcome-back",
        text="Create flyer",
        start_source="concierge",
        now=now,
    )

    guided = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="guide",
        text="guide me step by step",
        now=now,
    )

    assert guided.action == "guided_question"
    assert "First, what are you promoting?" in guided.reply_text
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.intake_sessions[0].status == "guided_collecting_goal"
    assert store.intake_sessions[0].creation_mode == "guided"
```

```python
def test_returning_customer_concierge_still_vague_followup_asks_open_prompt(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 23, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmi's Kitchen",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"business_category": "restaurant", "preferred_language": "en"})
    state_path.write_text(FlyerCustomerStore(customers=[customer]).model_dump_json(indent=2), encoding="utf-8")

    handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="welcome-back",
        text="Create flyer",
        start_source="concierge",
        now=now,
    )

    followup = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="still-vague",
        text="yes help me",
        now=now,
    )

    assert followup.action == "concierge_choice"
    assert "What is the flyer for?" in followup.reply_text
    assert "event, offer, items/prices, date" in followup.reply_text
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.intake_sessions[0].status == "concierge_awaiting_choice"
    assert store.intake_sessions[0].creation_mode == ""
```

- [ ] **Step 4: Run intake tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_flyer_onboarding.py::test_returning_customer_vague_start_opens_concierge_choice tests/test_flyer_onboarding.py::test_returning_customer_concierge_accepts_one_message_brief tests/test_flyer_onboarding.py::test_returning_customer_concierge_can_enter_guided_mode tests/test_flyer_onboarding.py::test_returning_customer_concierge_still_vague_followup_asks_open_prompt -q
```

Expected: fail because `start_source="concierge"` is normalized to `new_flyer` and no `concierge_awaiting_choice` status exists.

## Task 2: Implement Schema and Intake Concierge State

**Files:**
- Modify: `src/platform/schemas.py`
- Modify: `src/agents/flyer/intake.py`

- [ ] **Step 1: Add schema literal**

In `src/platform/schemas.py`, add:

```python
"concierge_awaiting_choice",
```

to `FlyerIntakeStatus`.

- [ ] **Step 2: Add concierge source normalization**

In `src/agents/flyer/intake.py`, update `_normalize_source` so `"concierge"` maps to `"new_flyer"`:

```python
if source in {"start_trial", "act_now", "quick_flyer", "new_flyer"}:
    return source  # type: ignore[return-value]
if source == "concierge":
    return "new_flyer"
```

- [ ] **Step 3: Add concierge start handling**

In `handle_intake_message`, inside the `if start_source:` block before `sample_idea` handling, add:

```python
if start_source == "concierge" and customer and customer.status in {"trial", "active"}:
    session = FlyerIntakeSession(
        chat_id=chat_id,
        sender_phone=_phone_or_none(sender_phone),
        status="concierge_awaiting_choice",
        source=source,
        started_at=now,
        updated_at=now,
        last_message_id=message_id,
        original_text=original_text or normalized_text,
        preferred_language=customer.preferred_language,
        reference_media_path=media_path or "",
        reference_media_message_id=message_id if media_path else "",
    )
    store.replace_intake_session(session)
    write_customer_store(state_path, store)
    return IntakeResult(
        True,
        _concierge_choice_reply(customer),
        "concierge_choice",
        source=source,
        preferred_language=customer.preferred_language,
        customer_id=customer.customer_id,
    )
```

- [ ] **Step 4: Add concierge continuation branch**

Add before `if session.status == "choosing_language":`

```python
if session.status == "concierge_awaiting_choice":
    if _is_cancel_reply(text):
        store.discard_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(True, _brief_cancelled_reply(), "brief_cancelled")
    mode = parse_concierge_choice(normalized_text)
    if mode == "guided":
        session = session.model_copy(update={
            "creation_mode": "guided",
            "status": "guided_collecting_goal",
            "last_message_id": message_id,
            "updated_at": now,
            **media_update,
        })
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(
            True,
            _guided_goal_prompt(session.preferred_language),
            "guided_question",
            source=session.source,
            preferred_language=session.preferred_language,
            creation_mode="guided",
            customer_id=customer.customer_id if customer else "",
        )
    if mode == "vague":
        session = session.model_copy(update={"last_message_id": message_id, "updated_at": now, **media_update})
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(
            True,
            _concierge_still_vague_reply(customer),
            "concierge_choice",
            source=session.source,
            preferred_language=session.preferred_language,
            customer_id=customer.customer_id if customer else "",
        )
    raw_request = _build_pending_brief_request(session, customer, text, source="text")
    session = session.model_copy(update={
        "creation_mode": "text",
        "brief_raw_request": raw_request,
        "brief_display_request": _visible_reply_text(text),
        "brief_source": "text",
        "status": "brief_pending_approval",
        "last_message_id": message_id,
        "updated_at": now,
        **media_update,
    })
    store.replace_intake_session(session)
    write_customer_store(state_path, store)
    return IntakeResult(
        True,
        _brief_preview_reply(session, customer),
        "brief_preview",
        source=session.source,
        preferred_language=session.preferred_language,
        creation_mode="text",
        customer_id=customer.customer_id if customer else "",
    )
```

- [ ] **Step 5: Add helper functions**

Add near `_mode_prompt`:

```python
def _concierge_choice_reply(customer: FlyerCustomerProfile) -> str:
    business_name = customer.business_name.strip() or "your business"
    return (
        "Flyer Studio\n"
        "------------\n"
        f"Welcome back, {business_name}. Yes, I am here to help. What are we creating today?\n\n"
        "You can tell me in one message, or I can guide you step by step."
    )


def _concierge_still_vague_reply(customer: Optional[FlyerCustomerProfile]) -> str:
    del customer
    return (
        "Flyer Studio\n"
        "------------\n"
        "Sure. What is the flyer for? You can send the event, offer, items/prices, date, "
        "or anything you already have."
    )
```

Add near `parse_mode_choice`:

```python
def parse_concierge_choice(text: str) -> str:
    choice = " ".join((text or "").lower().split())
    if choice in {"guide", "guided", "guide me", "guide me step by step", "step by step", "ask me questions"}:
        return "guided"
    if choice in {"yes", "yes help", "yes help me", "help", "help me", "create flyer", "make flyer", "i need a flyer", "need flyer"}:
        return "vague"
    return "text"
```

- [ ] **Step 6: Run red tests and verify green**

Run:

```powershell
python -m pytest tests/test_flyer_schemas.py::test_flyer_intake_accepts_concierge_awaiting_choice_status tests/test_flyer_onboarding.py::test_returning_customer_vague_start_opens_concierge_choice tests/test_flyer_onboarding.py::test_returning_customer_concierge_accepts_one_message_brief tests/test_flyer_onboarding.py::test_returning_customer_concierge_can_enter_guided_mode tests/test_flyer_onboarding.py::test_returning_customer_concierge_still_vague_followup_asks_open_prompt -q
```

Expected: all pass.

## Task 3: Cf-Router Vague Start Routing

**Files:**
- Modify: `src/plugins/cf-router/hooks.py`
- Modify: `tests/test_cf_router_flyer_routing.py`

- [ ] **Step 1: Add failing cf-router test**

Replace or adapt `test_vague_flyer_start_for_active_customer_sends_starter_ideas` so it expects concierge copy and proves the hook passes `start_source="concierge"`:

```python
def test_vague_flyer_start_for_active_customer_sends_concierge_choice(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = {}
    created = {"called": False}
    intake_calls = {}
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Lakshmi's Kitchen",
        "business_category": "restaurant",
        "status": "trial",
        "_starter_prompt_mode": "auto",
        "_starter_prompt_sent_count": 0,
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_source_vs_new_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "claim_flyer_starter_prompt_send", lambda _customer_id: (_ for _ in ()).throw(AssertionError("concierge must not claim starter prompt quota")))
    def fake_intake(**kwargs):
        intake_calls.update(kwargs)
        return True, "", {
            "reply_text": (
                "Flyer Studio\n------------\n"
                "Welcome back, Lakshmi's Kitchen. Yes, I am here to help. What are we creating today?\n\n"
                "You can tell me in one message, or I can guide you step by step."
            ),
            "action": "concierge_choice",
            "source": kwargs.get("start_source"),
        }
    monkeypatch.setattr(actions, "trigger_flyer_intake", fake_intake)
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kwargs: created.update(called=True) or (True, "", {}))
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text: sent.update({"chat_id": chat_id, "text": text}) or (True, "concierge-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="Hey Flyer-Studio, I'd like you to help me create a flyer",
        chat_id="17329837841@s.whatsapp.net",
        message_id="m1",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer concierge choice sent"}
    assert created["called"] is False
    assert sent["chat_id"] == "17329837841@s.whatsapp.net"
    assert "Welcome back, Lakshmi's Kitchen" in sent["text"]
    assert "You can tell me in one message, or I can guide you step by step." in sent["text"]
    assert "Pick one idea" not in sent["text"]
    assert intake_calls["start_source"] == "concierge"
    assert intake_calls["original_text"] == "Hey Flyer-Studio, I'd like you to help me create a flyer"
```

- [ ] **Step 2: Add failing tests for opted-out and already-sent starter settings**

Add parameterized coverage near the concierge test:

```python
@pytest.mark.parametrize(
    ("mode", "sent_count"),
    [("off", 0), ("auto", 1)],
)
def test_vague_flyer_start_sends_concierge_even_when_starter_prompts_unavailable(monkeypatch, mode, sent_count):
    hooks, actions = _load_plugin_modules()
    sent = {}
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Lakshmi's Kitchen",
        "business_category": "restaurant",
        "status": "trial",
        "_starter_prompt_mode": mode,
        "_starter_prompt_sent_count": sent_count,
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_source_vs_new_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "claim_flyer_starter_prompt_send", lambda _customer_id: (_ for _ in ()).throw(AssertionError("concierge must not claim starter prompt quota")))
    monkeypatch.setattr(actions, "trigger_flyer_intake", lambda **_kwargs: (True, "", {
        "reply_text": (
            "Flyer Studio\n------------\n"
            "Welcome back, Lakshmi's Kitchen. Yes, I am here to help. What are we creating today?\n\n"
            "You can tell me in one message, or I can guide you step by step."
        ),
        "action": "concierge_choice",
    }))
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text: sent.update({"chat_id": chat_id, "text": text}) or (True, "concierge-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="Create flyer",
        chat_id="17329837841@s.whatsapp.net",
        message_id="m1",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer concierge choice sent"}
    assert "Welcome back, Lakshmi's Kitchen" in sent["text"]
```

- [ ] **Step 3: Add failing active-intake protection test**

Add near `_try_flyer_intake_intercept` tests:

```python
def test_concierge_awaiting_choice_is_protected_from_new_project_bypass(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = {}
    primary_called = {"called": False}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"status": "trial", "customer_id": "CUST0001"})
    monkeypatch.setattr(actions, "find_flyer_intake_session_by_sender", lambda _phone, _chat_id: {"status": "concierge_awaiting_choice"})
    monkeypatch.setattr(actions, "trigger_flyer_intake", lambda **_kwargs: (True, "", {
        "action": "brief_preview",
        "reply_text": "Flyer Studio\n------------\nI will create this flyer.\n\nReply APPROVE to start.",
    }))
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text: sent.update({"chat_id": chat_id, "text": text}) or (True, "preview-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", lambda *_args, **_kwargs: primary_called.update(called=True) or {"action": "skip", "reason": "unexpected primary"})

    result = hooks._try_flyer_intake_intercept(
        "Create a breakfast specials flyer Saturday 8 AM to 11 AM with Idli $4.99",
        "17329837841@s.whatsapp.net",
        SimpleNamespace(message_id="brief"),
    )

    assert result == {"action": "skip", "reason": "cf-router flyer intake: brief_preview"}
    assert primary_called["called"] is False
    assert "I will create this flyer" in sent["text"]
```

- [ ] **Step 4: Run cf-router tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_cf_router_flyer_routing.py::test_vague_flyer_start_for_active_customer_sends_concierge_choice tests/test_cf_router_flyer_routing.py::test_vague_flyer_start_sends_concierge_even_when_starter_prompts_unavailable tests/test_cf_router_flyer_routing.py::test_concierge_awaiting_choice_is_protected_from_new_project_bypass -q
```

Expected: fails because hooks currently pass `start_source="sample_idea"` and return `cf-router flyer starter ideas sent`.

- [ ] **Step 5: Change hook to start concierge intake independently of starter-prompt claim**

In `src/plugins/cf-router/hooks.py`, in the vague active-customer branch for active/trial customers, remove the `flyer_starter_prompts_enabled`, `flyer_starter_prompt_already_sent`, `claim_flyer_starter_prompt_send`, and release-claim gating around the default response. Always call:

```python
ok, detail, intake = actions.trigger_flyer_intake(
    chat_id=chat_id,
    sender_phone=phone,
    message_id=_extract_message_id(event, chat_id, text),
    text=text,
    media_path=media_path or "",
    start_source="concierge",
    original_text=text,
)
```

If intake fails, audit `flyer_intake_failed` and return `None` as the existing branch does. If it succeeds, send `reply_text`, audit `flyer_concierge_choice`, and return:

```python
return {"action": "skip", "reason": "cf-router flyer concierge choice sent"}
```

- [ ] **Step 6: Protect concierge status in active intake**

In `_try_flyer_intake_intercept`, add:

```python
"concierge_awaiting_choice",
```

to `protected_statuses`.

- [ ] **Step 7: Run cf-router tests and verify green**

Run:

```powershell
python -m pytest tests/test_cf_router_flyer_routing.py::test_vague_flyer_start_for_active_customer_sends_concierge_choice tests/test_cf_router_flyer_routing.py::test_vague_flyer_start_sends_concierge_even_when_starter_prompts_unavailable tests/test_cf_router_flyer_routing.py::test_concierge_awaiting_choice_is_protected_from_new_project_bypass -q
```

Expected: pass.

## Task 4: Regression Suite and Commit

**Files:**
- Verify all changed surfaces.

- [ ] **Step 1: Run focused Flyer tests**

Run:

```powershell
python -m pytest tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tests/test_flyer_schemas.py tests/test_flyer_starter_briefs.py -q
```

Expected: all pass.

- [ ] **Step 2: Run static/script tests for touched routing surface**

Run:

```powershell
python -m pytest tests/test_cf_router_plugin.py tests/test_flyer_scripts_static.py -q
```

Expected: all pass.

- [ ] **Step 3: Inspect diff**

Run:

```powershell
git diff -- src/platform/schemas.py src/agents/flyer/intake.py src/plugins/cf-router/hooks.py tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tests/test_flyer_schemas.py
```

Expected: only concierge-intake changes, no render/provider/account side effects.

- [ ] **Step 4: Commit**

Run:

```powershell
git add src/platform/schemas.py src/agents/flyer/intake.py src/plugins/cf-router/hooks.py tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tests/test_flyer_schemas.py docs/superpowers/plans/2026-05-23-flyer-concierge-intake.md tasks/todo.md
git commit -m "feat: add flyer concierge intake"
```

Expected: commit succeeds.

## Self-Review

Spec coverage: This plan covers returning active/trial customers, vague starts, warm copy, one-message continuation, guided continuation, schema acceptance, cf-router first-touch behavior, and regression tests.

Placeholder scan: no TBD/TODO placeholders remain.

Type consistency: `concierge_awaiting_choice`, `concierge_choice`, and `start_source="concierge"` are used consistently across schema, intake, hooks, and tests.
