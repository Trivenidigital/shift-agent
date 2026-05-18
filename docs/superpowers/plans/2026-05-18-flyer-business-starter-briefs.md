# Flyer Business Starter Briefs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add business-category starter briefs that Flyer Studio can show after registration or before vague flyer creation so customers edit a strong sample instead of writing from a blank page.

**Architecture:** Reuse the existing Flyer customer profile, intake sessions, cf-router pre-gateway routing, project creation, and renderer. Add one focused catalog module that maps normalized business categories to editable starter brief text, then call it from onboarding/intake/cf-router at the points where the customer is ready to submit a flyer request but has not supplied a complete brief.

**Tech Stack:** Python 3, Pydantic v2 schemas, JSON-on-disk state through existing Flyer store helpers, pytest subprocess/in-process tests.

---

**Drift-check tag:** extends-Hermes

**New primitives introduced:** `agents.flyer.starter_briefs` catalog module, a starter-brief reply branch in Flyer intake/onboarding, and tests for category matching plus WhatsApp copy.

## Drift Check

Existing repo primitives:

| Primitive | Evidence | Decision |
|---|---|---|
| Business category stored at registration | `src/platform/schemas.py` has `FlyerCustomerProfile.business_category` and `FlyerOnboardingSession.business_category`. | Reuse. Do not add a second profile field. |
| Language and mode intake | `src/agents/flyer/intake.py` already asks language and Guided/Text Mode. | Reuse. Starter brief should prepare Text Mode or improve Guided completion, not replace either mode. |
| Pre-gateway Flyer routing | `src/plugins/cf-router/hooks.py` routes campaign CTAs, vague starts, active projects, and primary project creation before the dispatcher. `src/plugins/cf-router/actions.py` owns `is_vague_flyer_start` and `flyer_project_has_required_fields`. | Extend this route for active/trial vague starts; otherwise existing customers who type `Create flyer` will bypass intake and create empty projects. |
| Complete project creation | `src/agents/flyer/scripts/create-flyer-project` already extracts fields and hydrates saved customer contact/location. | Reuse. Starter brief remains normal raw request text. |
| Service-business renderer policy | `src/agents/flyer/render.py` already blocks food/festival imagery for service flyers. | Extend only if the starter brief exposes a gap. |
| Existing service request markers | `src/agents/flyer/scripts/create-flyer-project` and `src/agents/flyer/render.py` already recognize digital marketing services. | Reuse and test that catalog text flows through. |

Conclusion: no redundant workflow, database, or renderer should be introduced.

## Hermes-First Analysis

Checked live `main-vps` with two-step SSH redirect/read on 2026-05-18. Installed Hermes skills include `flyer_generation`, `dispatch_shift_agent`, the project skill set, `productivity/google-workspace`, `productivity/airtable`, `productivity/notion`, `productivity/ocr-and-documents`, `productivity/maps`, and creative skills. Installed plugin inventory shows `cf-router`.

Checked ecosystem sources:

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp intake/routing | yes - existing `dispatch_shift_agent`, `flyer_generation`, `cf-router` on VPS | use it |
| Image/flyer generation | yes - existing Flyer Studio renderer plus Hermes creative/image skills at https://hermes-agent.nousresearch.com/docs/skills | use existing Flyer Studio path |
| Business-specific starter brief catalog | none found in live skills/plugins or Hermes Skills Hub | build small catalog from scratch |
| External template storage | yes - Airtable/Notion/Google Workspace could store records | do not use now; local code catalog is simpler and deployable |

awesome-hermes-agent ecosystem check: reviewed https://github.com/0xNyk/awesome-hermes-agent; no purpose-built business-category flyer starter prompt catalog found. Verdict: build a small local catalog and keep it portable so it can later move to Airtable/Notion if operators want no-code editing.

Per-step Hermes-first checklist:

| Step | Owner | Decision |
|---|---|---|
| Receive WhatsApp text/media | [Hermes] | Use existing gateway and `cf-router` hook. |
| Resolve sender/customer/business category | [Hermes] | Use existing sender identity and `FlyerCustomerStore` lookup. |
| Choose starter brief by category | [net-new] | Build small deterministic catalog. |
| Send editable sample to customer | [Hermes] | Use existing `actions.send_flyer_text` or existing onboarding/intake reply path. |
| Customer edits and submits request | [Hermes] | Treat edited text as a normal Flyer Studio request. |
| Create project, hydrate saved contact/location | [Hermes] | Use existing `create-flyer-project`. |
| Generate and deliver flyer | [Hermes] | Use existing renderer/generation/final package path. |

## File Structure

- Create `src/agents/flyer/starter_briefs.py`: pure catalog and category matching helpers. No state writes.
- Modify `src/agents/flyer/intake.py`: show a starter brief when Text Mode becomes ready for a registered customer.
- Modify `src/agents/flyer/onboarding.py`: after trial/active setup completes, include the category starter brief when no trailing flyer request exists.
- Modify `src/plugins/cf-router/hooks.py`: divert active/trial vague flyer starts to a starter-brief reply instead of creating an incomplete project.
- Modify `src/plugins/cf-router/actions.py`: add a thin helper for customer starter-brief message lookup if needed by `hooks.py`.
- Modify `src/agents/shift/scripts/shift-agent-deploy.sh`: install `starter_briefs.py` as `/opt/shift-agent/flyer_starter_briefs.py`.
- Modify `src/agents/shift/scripts/shift-agent-smoke-test.sh`: import `flyer_starter_briefs`.
- Add `tests/test_flyer_starter_briefs.py`: pure tests for category matching and output quality.
- Extend `tests/test_flyer_onboarding.py`: onboarding completion includes the right starter brief for digital marketing and restaurants.
- Extend `tests/test_flyer_create_project.py`: starter brief text for restaurant, grocery, and digital marketing can become valid projects after customer hydration.
- Extend `tests/test_cf_router_flyer_routing.py`: active/trial vague `Create flyer` receives the starter brief and does not create an empty project.
- Update `tasks/todo.md`: track plan, reviews, design, build, verification, PR, and reviewer findings.

## Starter Categories

Initial catalog should include 10 categories:

1. Restaurant / food special
2. Grocery / supermarket
3. Digital marketing agency
4. Salon / beauty
5. Realtor / real estate
6. Tutor / education
7. Event planner
8. Tax / accounting
9. Temple / nonprofit event
10. Home services

Each brief must be editable customer-facing text, not hidden image-model instruction. It should include:

- What to create.
- Suggested heading.
- Items/services/details section.
- Style direction.
- Visual direction.
- Saved business info instruction.
- A final line asking the user to edit and send back.

## Task 1: Catalog Module

**Files:**
- Create: `src/agents/flyer/starter_briefs.py`
- Test: `tests/test_flyer_starter_briefs.py`

- [ ] **Step 1: Write failing catalog tests**

Add tests that import `starter_briefs` and assert:

```python
def test_digital_marketing_category_gets_agency_brief():
    brief = starter_briefs.starter_brief_for_category("Digital marketing agency")
    assert brief is not None
    assert "business growth" in brief.body.lower()
    assert "Social Media Marketing" in brief.body
    assert "no food or festival visuals unless I ask for them" in brief.body

def test_unknown_category_gets_local_business_brief():
    brief = starter_briefs.starter_brief_for_category("custom gifts and printing")
    assert brief is not None
    assert brief.category_id == "local_business"
    assert "Edit anything below" in brief.body

def test_all_starter_briefs_are_whatsapp_sized_and_customer_editable():
    for brief in starter_briefs.all_starter_briefs():
        assert len(brief.body) <= 1800
        assert "Edit anything below" in brief.body
        assert "Use my saved business" in brief.body
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `python -m pytest tests/test_flyer_starter_briefs.py -q`

Expected: fail because `agents.flyer.starter_briefs` does not exist.

- [ ] **Step 3: Implement the catalog**

Create a frozen dataclass:

```python
@dataclass(frozen=True)
class StarterBrief:
    category_id: str
    label: str
    body: str
```

Expose:

```python
def all_starter_briefs() -> list[StarterBrief]: ...
def starter_brief_for_category(category: str) -> StarterBrief: ...
def starter_brief_message(category: str, *, business_name: str = "") -> str: ...
```

Matching should be keyword based and deterministic. Digital marketing keywords must include `digital marketing`, `marketing agency`, `seo`, `social media`, `paid ads`, `performance marketing`, `aeo`, `geo`, and `ai marketing`.

Customer-facing brief copy must stay editable and neutral. It may suggest a heading such as `Grow Your Business with Modern Marketing`, but it must not assert unverified claims like `AI-powered` unless the business category or user text already says AI. Internal instructions should be phrased as customer-editable style preferences, for example `Clean modern agency style; no food or festival visuals unless I ask for them.`

- [ ] **Step 4: Verify catalog GREEN**

Run: `python -m pytest tests/test_flyer_starter_briefs.py -q`

Expected: all catalog tests pass.

- [ ] **Step 5: Prove starter briefs can become valid project requests**

Add tests in `tests/test_flyer_create_project.py` that iterate over `all_starter_briefs()`, pass each body to `create-flyer-project` with a saved customer profile, and assert:

```python
assert module.FlyerProject.model_validate(project).fields.missing_required_fields() == []
assert actions.flyer_project_has_required_fields(project)
```

Cover every catalog category, including the `local_business` fallback.

- [ ] **Step 6: Verify parser GREEN**

Run: `python -m pytest tests/test_flyer_starter_briefs.py tests/test_flyer_create_project.py -q`

Expected: all starter catalog and parser-validity tests pass.

## Task 2: Intake Integration

**Files:**
- Modify: `src/agents/flyer/intake.py`
- Test: `tests/test_flyer_onboarding.py`

- [ ] **Step 1: Write failing intake/onboarding tests**

Add a test where a trial customer with `business_category="digital marketing agency"` selects Text Mode and receives a starter brief in the ready reply.

Expected assertions:

```python
assert "Here is a starter flyer request" in result.reply_text
assert "Grow Your Business with Modern Marketing" in result.reply_text
assert "Reply with your edited version" in result.reply_text
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_flyer_onboarding.py::test_text_mode_ready_includes_category_starter_brief -q`

Expected: fail because the reply only says Text Mode is ready.

- [ ] **Step 3: Integrate catalog into intake**

Import with the deployed fallback pattern:

```python
try:
    from agents.flyer.starter_briefs import starter_brief_message
except ModuleNotFoundError:
    from flyer_starter_briefs import starter_brief_message  # type: ignore
```

Change `_text_mode_ready_reply` to accept optional `customer` and append `starter_brief_message(customer.business_category, business_name=customer.business_name)` when available.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_flyer_onboarding.py::test_text_mode_ready_includes_category_starter_brief -q`

Expected: pass.

## Task 3: Onboarding Completion Integration

**Files:**
- Modify: `src/agents/flyer/onboarding.py`
- Test: `tests/test_flyer_onboarding.py`

- [ ] **Step 1: Write failing onboarding completion tests**

Add a test where free-trial onboarding completes for a digital marketing agency without a trailing flyer request. Assert the completion reply includes the starter brief and does not create a project.

- [ ] **Step 2: Run focused test and verify RED**

Run: `python -m pytest tests/test_flyer_onboarding.py::test_trial_completion_suggests_business_category_starter_brief -q`

Expected: fail because completion copy currently gives a generic ready prompt.

- [ ] **Step 3: Append starter brief to completion copy**

Change `_reply_for_session` so the `trial` branch can access the actual `FlyerCustomerProfile`, not only `customer_id`. One acceptable shape is:

```python
customer = next((c for c in store.customers if c.customer_id == customer_id), None)
return _trial_active_reply(customer_id, creation_mode=session.creation_mode, language=session.preferred_language, customer=customer)
```

Then update `_trial_active_reply` to accept `customer: FlyerCustomerProfile | None = None` and `include_starter_brief: bool = True`, and append `starter_brief_message(customer.business_category, business_name=customer.business_name)` only when appropriate.

Append the starter brief only when:

- The customer is `trial` or `active`.
- The reply is a ready/setup completion prompt.

Do not append starter briefs to payment-pending replies.

Compound-confirm suppression is handled in `cf-router`: when `_try_flyer_onboarding_intercept` detects trailing flyer text after `CONFIRM`, it must send a short ready acknowledgement without starter-brief text before routing the trailing request to `_try_flyer_primary_intercept`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_flyer_onboarding.py::test_trial_completion_suggests_business_category_starter_brief -q`

Expected: pass.

## Task 4: cf-router Vague Active-Customer Integration

**Files:**
- Modify: `src/plugins/cf-router/hooks.py`
- Modify: `src/plugins/cf-router/actions.py`
- Test: `tests/test_cf_router_flyer_routing.py`

- [ ] **Step 1: Write failing cf-router regression test**

Add a test proving an active/trial customer with `business_category="digital marketing agency"` who sends `Create flyer` receives the category starter brief and does not call `trigger_create_flyer_project`.

Use monkeypatches matching nearby `_try_flyer_primary_intercept` tests:

```python
created = {"called": False}
monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {
    "customer_id": "CUST0001",
    "business_name": "Spark Growth",
    "business_category": "digital marketing agency",
    "status": "trial",
})
monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kwargs: created.update(called=True) or (True, "", {}))
monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text: (True, "starter-mid", ""))

result = hooks.pre_gateway_dispatch({"chat_id": "17329837841@s.whatsapp.net", "text": "Create flyer", "id": "m1"})

assert result == {"action": "skip", "reason": "cf-router flyer starter brief sent"}
assert created["called"] is False
```

Also assert the sent text contains `Here is a starter flyer request`.

Add parameterized negative tests for customers with `status` equal to `payment_pending`, `suspended`, and `cancelled`; those must not receive starter brief text.

Add a compound-confirm test for `CONFIRM. Create ...`; assert project creation happens and the onboarding acknowledgement sent before project creation does not contain `Here is a starter flyer request`.

- [ ] **Step 2: Run focused cf-router test and verify RED**

Run: `python -m pytest tests/test_cf_router_flyer_routing.py::test_vague_flyer_start_for_active_customer_sends_starter_brief -q`

Expected: fail because current code calls `_try_flyer_primary_intercept`.

- [ ] **Step 3: Implement cf-router starter branch**

In the existing `actions.is_vague_flyer_start(...)` block, when `customer` exists, `customer.get("status") in {"trial", "active"}`, and role is not owner:

```python
reply = actions.flyer_starter_brief_reply(customer)
ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
actions.audit_intercepted(...)
return {"action": "skip", "reason": "cf-router flyer starter brief sent"}
```

Do not create a project until the customer sends the edited brief back.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_cf_router_flyer_routing.py::test_vague_flyer_start_for_active_customer_sends_starter_brief -q`

Expected: pass.

## Task 5: Deploy and Smoke Wiring

**Files:**
- Modify: `src/agents/shift/scripts/shift-agent-deploy.sh`
- Modify: `src/agents/shift/scripts/shift-agent-smoke-test.sh`
- Test: `tests/test_flyer_scripts_static.py`

- [ ] **Step 1: Write failing static deploy test**

Extend the static test to assert deploy installs `src/agents/flyer/starter_briefs.py` to `/opt/shift-agent/flyer_starter_briefs.py` and smoke imports `flyer_starter_briefs`.

- [ ] **Step 2: Run focused static test and verify RED**

Run: `python -m pytest tests/test_flyer_scripts_static.py -q`

Expected: fail on missing deploy/smoke references.

- [ ] **Step 3: Add deploy/smoke wiring**

Follow the existing install pattern used for `render.py`, `workflow.py`, `onboarding.py`, `intake.py`, `account.py`, and `guest_order.py`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_flyer_scripts_static.py -q`

Expected: pass.

## Task 6: Focused Verification and PR Prep

**Files:**
- Modify: `tasks/todo.md`
- No production code unless review findings require it.

- [ ] **Step 1: Run focused Flyer verification**

Run:

```powershell
python -m pytest tests/test_flyer_starter_briefs.py tests/test_flyer_onboarding.py tests/test_flyer_create_project.py tests/test_flyer_renderer.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run syntax checks**

Run:

```powershell
python -m py_compile src\agents\flyer\starter_briefs.py src\agents\flyer\intake.py src\agents\flyer\onboarding.py src\plugins\cf-router\actions.py src\plugins\cf-router\hooks.py src\platform\schemas.py
```

Expected: exit code 0.

- [ ] **Step 3: Run diff hygiene**

Run: `git diff --check`

Expected: exit code 0, except for any pre-existing baseline CRLF warning not introduced by this branch.

- [ ] **Step 4: Update task record**

Record plan review, design review, implementation, verification, PR, and final review outcomes in `tasks/todo.md`.

## Review Plan

- Plan review: dispatch 2 parallel reviewers before writing design.
- Design review: dispatch 2 parallel reviewers before implementation.
- PR review: dispatch 3 parallel reviewers after PR creation, with orthogonal lenses:
  - Hermes/drift and scope reviewer.
  - Code/test/state-machine reviewer.
  - Product/UX and customer-copy reviewer.

## Self-Review

- Spec coverage: the plan covers catalog, intake/onboarding integration, deploy/smoke wiring, tests, task tracking, and final review.
- Placeholder scan: no open placeholder sections remain.
- Type consistency: new API is `StarterBrief`, `starter_brief_for_category`, `starter_brief_message`, and all call sites use those names.
