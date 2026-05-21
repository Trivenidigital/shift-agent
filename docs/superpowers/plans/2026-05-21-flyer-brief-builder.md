# Flyer Brief Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Drift-check tag:** extends-Hermes

**Goal:** Build a Flyer Brief Builder that lets customers pick an idea, answer guided questions, or type a request, then approve a compact brief before flyer generation starts.

**Architecture:** Keep Hermes and cf-router as the WhatsApp, identity, audit, and delivery substrate. Extend the existing Flyer intake state machine with a persisted awaiting-text/sample/brief-preview state, then send approved briefs through the current project creation path. Do not change provider routing, dashboard UI, source-edit behavior, payment behavior, or live VPS state.

**Tech Stack:** Python, Pydantic v2, existing Flyer JSON state, `safe_io.atomic_write_text`, cf-router hooks/actions, pytest.

---

## Plan Review Fold

Two plan reviewers found the first draft was directionally right but structurally unsafe. This revision folds the findings:

- Text Mode must persist a `text_awaiting_brief` state; it cannot discard the intake session after "Text Mode is ready."
- Pending brief states must outrank active-project and fresh-intent routing, while stale inactive intake sessions and early language/mode sessions must still not swallow explicit new flyer work.
- Pending brief evidence must be durable enough to debug generation provenance, but this PR will not add self-eval/operator-brief incidents until that evidence lands and stabilizes.
- The brief must reuse existing Flyer extraction/fact substrate instead of creating a parallel product schema.
- The current vague starter path must be connected to intake/sample choices, not leave customers replying `2` into generic routing.
- One-time orders must keep the existing payment/guest-order gate.
- Approval parsing must handle real WhatsApp variants such as `Approve`, `approve.`, `yes create it`, and sender-block-wrapped text.
- Existing active/trial customers with saved profile/language must get compact sample ideas immediately on vague `Create flyer`; do not force language/mode again.
- Sample/guided/text approved raw requests must round-trip through the existing project parser in tests, not only assert customer-copy fragments.
- Build review fixes folded: LID-only customers resolve by chat id, preview copy hides parser/audit scaffolding, old in-flight mode prompts keep their old numbering, approved brief state survives until project creation succeeds, free-trial sample mode continues into the compact idea picker after onboarding, and the sample picker is limited to two examples.

## Drift Check

| Area | Existing evidence | Decision |
|---|---|---|
| Category starter prompts | `src/agents/flyer/starter_briefs.py` has category prompts and opt-out hints | Reuse; add compact idea choices without removing full prompts |
| Language/mode intake | `src/agents/flyer/intake.py` asks language and guided/text mode | Extend this state machine; no second intake store |
| Project parser | `src/agents/flyer/scripts/create-flyer-project` extracts `FlyerRequestFields` and hydrates customer defaults | Approved brief remains raw request text shaped for existing parser |
| Locked facts | `src/agents/flyer/facts.py` derives source/profile/customer locked facts | Do not duplicate fact logic in intake |
| Active project bypass | PR #150/#155 logic protects fresh flyer requests from stale projects | Preserve; pending brief continuations become an explicit higher-priority exception |
| Current text mode | Text mode currently discards intake session after ready reply | Change to persist `text_awaiting_brief` |
| Current vague starter route | cf-router currently sends a starter brief and returns | Change only this route to start/continue sample idea intake |
| One-time orders | Quick flyer uses guest-order/payment path | Do not generate or collect final brief before existing payment readiness |

No full-functionality in-tree primitive already provides approved brief preview across sample, guided, and typed routes.

## Hermes-first Analysis

Official docs checked:
- Hermes skills and extension model: https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills
- Hermes feature overview: https://hermes-agent.nousresearch.com/docs/user-guide/features/overview/
- Hermes vision/image capability: https://hermes-agent.nousresearch.com/docs/user-guide/features/vision/
- Bundled skills catalog: https://hermes-agent.nousresearch.com/docs/reference/skills-catalog

| Step | Owner | Decision |
|---|---|---|
| Receive WhatsApp text/media | [Hermes] | Reuse gateway/cf-router event path |
| Resolve sender identity | [Hermes] | Reuse existing LID/phone helpers |
| Store customer/intake state | [Hermes pattern] | Reuse JSON-on-disk and existing `FlyerCustomerStore` |
| Ask language and route choice | [Flyer] | Existing Flyer intake, extended with one more route |
| Generate localized sample ideas | [Flyer] | Product content; add deterministic catalog helper |
| Parse typed/guided answers into approved generation text | [Flyer over Hermes substrate] | Keep deterministic v0.1; approved text goes to existing parser |
| Extract project fields/locked facts | [Flyer existing] | Reuse `create-flyer-project` and `facts.py` |
| Send preview and wait for approval | [Hermes substrate + Flyer policy] | Reuse WhatsApp send path; policy is Flyer-specific |
| Generate image assets | [Flyer existing] | No provider/model changes |
| Audit/customer copy | [Hermes pattern + Flyer copy] | Reuse existing cf-router audit and copy helpers |

awesome-hermes-agent ecosystem check: no purpose-built Flyer Studio brief-builder/customer prompt approval skill was found in prior Flyer audits or current Hermes skill search. Verdict: extend Flyer Studio on Hermes substrate.

## Customer Product Contract

The visible route choices become:

```text
Flyer Studio
------------
How would you like to create your flyer?

1. Pick an idea
2. Guide me
3. I'll type

Reply 1, 2, or 3.
```

Compatibility:
- Accept text aliases: `sample`, `idea`, `guided`, `guide me`, `text`, `type`.
- Numeric mapping intentionally changes because the product now has three routes.
- Tests must update the old 1/2 mode expectations.

The brief preview format:

```text
Flyer Studio
------------
I will create this flyer:

Business: Lakshmi's Kitchen
Request: Create an evening snacks flyer from 4 PM to 7 PM, Wednesday to Saturday. Include 5 top South Indian snack items. Use saved address, phone, and logo.
Language: English

Reply APPROVE to start, or tell me what to change.
```

The preview may show a compact `Request:` instead of duplicating `FlyerRequestFields`. This intentionally avoids a parallel parser. The existing create-project parser remains the source of truth for final fields, locked facts, and QA.

## Files

Modify:
- `src/platform/schemas.py`
- `src/agents/flyer/intake.py`
- `src/agents/flyer/starter_briefs.py`
- `src/plugins/cf-router/hooks.py`
- `src/plugins/cf-router/actions.py` only if a tiny helper is needed
- `tests/test_flyer_schemas.py`
- `tests/test_flyer_starter_briefs.py`
- `tests/test_flyer_onboarding.py`
- `tests/test_cf_router_flyer_routing.py`
- `tests/test_flyer_incident_replay.py`
- `tests/test_flyer_guest_order.py`
- `tasks/todo.md`

Create:
- `docs/superpowers/specs/2026-05-21-flyer-brief-builder-design.md`

Deferred from this PR:
- `tools/flyer-self-evaluation.py`
- `tools/operator-brief.py`
- dashboard UI
- provider/model/source-edit behavior

## Task 1: Schema states for brief builder

**Files:**
- Modify: `src/platform/schemas.py`
- Modify: `tests/test_flyer_schemas.py`

- [ ] **Step 1: Write failing schema tests**

Add tests proving `FlyerIntakeSession` accepts these statuses and fields:

```python
status="text_awaiting_brief"
status="choosing_sample_idea"
status="brief_pending_approval"
brief_raw_request="Create an evening snacks flyer..."
brief_source="text"
brief_approved_at=None
brief_approved_message_id=""
```

Also assert unknown fields are still rejected.

- [ ] **Step 2: Run RED**

Run:

```powershell
python -m pytest tests/test_flyer_schemas.py -k "intake_session" -q
```

Expected: fail because statuses/fields do not exist.

- [ ] **Step 3: Implement schema**

Add the statuses to `FlyerIntakeStatus`. Add bounded fields:

```python
brief_raw_request: str = Field(default="", max_length=3000)
brief_source: Literal["", "sample", "guided", "text"] = ""
brief_approved_at: Optional[datetime] = None
brief_approved_message_id: str = Field(default="", max_length=200)
```

- [ ] **Step 4: Run GREEN**

Run the same test and expect pass.

## Task 2: Compact sample idea choices

**Files:**
- Modify: `src/agents/flyer/starter_briefs.py`
- Modify: `tests/test_flyer_starter_briefs.py`

- [ ] **Step 1: Write failing tests**

Tests:
- restaurant category returns three compact ideas including breakfast/evening/weekend-style examples.
- Telugu/Hindi selected language changes the customer-facing shell and keeps examples usable.
- ideas do not contain internal terms: `project`, `provider`, `reason_code`, `manual_edit_required`, `operator`.
- each restaurant idea can become an approved raw request that `create-flyer-project` accepts with saved profile fields and no missing required fields.

- [ ] **Step 2: Implement helpers**

Add deterministic helpers:

```python
def starter_idea_choices(category: str, *, business_name: str = "", language: str = "en") -> list[str]:
    ...

def starter_idea_choices_message(category: str, *, business_name: str = "", language: str = "en") -> str:
    ...
```

Keep `starter_brief_message()` unchanged for backward compatibility.

## Task 3: Mode prompt and text-awaiting state

**Files:**
- Modify: `src/agents/flyer/intake.py`
- Modify: `tests/test_flyer_onboarding.py`

- [ ] **Step 1: Write failing tests**

Test active/trial customer flow:
- start intake
- choose English
- choose `3` / text
- result is `text_ready`
- store still has one intake session with `status="text_awaiting_brief"`
- next typed request returns `brief_preview`, not `create_project`

- [ ] **Step 2: Implement**

Change `_mode_prompt()` to show three choices. Change text mode to persist `text_awaiting_brief` instead of discarding the session.

## Task 4: Brief preview and approval for text route

**Files:**
- Modify: `src/agents/flyer/intake.py`
- Modify: `tests/test_flyer_onboarding.py`

- [ ] **Step 1: Write failing tests**

Use:

```text
Create Flyer for breakfast specials from 8-11 AM Monday to Thursday
```

Expected:
- reply contains `I will create this flyer`
- reply contains saved business name
- reply contains the request text
- reply says `Reply APPROVE to start`
- state is `brief_pending_approval`
- `APPROVE`, `Approve`, `approve.`, `yes create it`, `yes start`, `go ahead`, and sender-block-wrapped `APPROVE` return `create_project`
- approved raw request contains the original typed request plus selected language and saved-profile instruction
- approved raw request round-trips through `create-flyer-project` with saved business/contact/location fields and no missing-info status

- [ ] **Step 2: Implement**

Add helpers:

```python
def _is_approve_reply(text: str) -> bool: ...
def _visible_reply_text(text: str) -> str: ...
def _build_pending_brief_request(session, customer, request_text: str, source: str) -> str: ...
def _brief_preview_reply(session, customer) -> str: ...
```

Keep raw request as the canonical data that feeds `create-flyer-project`.

## Task 5: Guided route preview and media preservation

**Files:**
- Modify: `src/agents/flyer/intake.py`
- Modify: `tests/test_flyer_onboarding.py`

- [ ] **Step 1: Write failing tests**

Update guided tests so the final answer returns `brief_preview`, then `APPROVE` returns `create_project`.

Add a reference-media test proving `reference_media_path` survives:
- media attached during guided answer
- preview shown
- approval returns same `reference_media_path`

- [ ] **Step 2: Implement**

Change final guided answer to store `brief_raw_request` and `brief_source="guided"` with `status="brief_pending_approval"` instead of returning `create_project` immediately.

## Task 6: Sample idea route and current vague-start bridge

**Files:**
- Modify: `src/agents/flyer/intake.py`
- Modify: `src/plugins/cf-router/hooks.py`
- Modify: `tests/test_flyer_onboarding.py`
- Modify: `tests/test_cf_router_flyer_routing.py`

- [ ] **Step 1: Write failing tests**

Tests:
- selecting `1` at mode prompt sends compact idea choices and stores `choosing_sample_idea`.
- replying `2` stores selected idea as `brief_pending_approval` and sends preview.
- current vague active/trial `Create flyer` with a saved customer profile sends compact idea choices immediately, stores `choosing_sample_idea`, and does not ask language/mode again.
- opted-out starter prompt accounts still receive a short clarification and do not enter sample idea spam.

- [ ] **Step 2: Implement**

Add a narrow intake start path for saved active/trial customers that initializes `choosing_sample_idea` directly with the customer's saved language/profile. Do not create projects from sample selection until approval.

## Task 7: cf-router priority and stale-session protection

**Files:**
- Modify: `src/plugins/cf-router/hooks.py`
- Modify: `tests/test_cf_router_flyer_routing.py`
- Modify: `tests/test_flyer_incident_replay.py`

- [ ] **Step 1: Write failing tests**

Cases:
- pending brief receives `make it a Diwali flyer Friday 5 PM` and re-previews, not new project/revision.
- pending brief receives `create this one in Telugu` and updates/re-previews.
- stale inactive intake session still does not swallow explicit fresh project creation.
- #150 fresh evening-snacks old-active-project bypass still passes.

- [ ] **Step 2: Implement**

Change `_try_flyer_intake_intercept()` skip logic so active/trial flyer-intent messages bypass intake only when no active intake session or when session status is not one of:
- `choosing_language`
- `choosing_mode`
- `text_awaiting_brief`
- `choosing_sample_idea`
- `brief_pending_approval`
- guided collection statuses

## Task 8: One-time/guest order guard

**Files:**
- Modify: `tests/test_flyer_guest_order.py` or `tests/test_flyer_onboarding.py`
- Modify: `src/agents/flyer/intake.py` only if needed

- [ ] **Step 1: Write tests**

Tests:
- unpaid `quick_flyer` still returns `start_guest_order` and cannot reach preview/approval generation.
- paid guest order route can proceed only through the existing paid guest order branch and does not mutate account state beyond existing project reservation behavior.
- trial/free-trial consumes quota only at approved generation reservation, not at preview.
- active paid customers do not receive trial or guest-order copy in the brief-builder flow.

- [ ] **Step 2: Implement only if tests fail**

Prefer documenting current behavior with tests if it is already correct.

## Task 9: Backlog and design doc

**Files:**
- Create: `docs/superpowers/specs/2026-05-21-flyer-brief-builder-design.md`
- Modify: `tasks/todo.md`

- [ ] **Step 1: Write design doc**

Must include:
- Drift-check tag.
- Hermes-first per-step table.
- State machine.
- Route priority.
- Approval evidence.
- Deferred self-eval/operator-brief follow-up.

- [ ] **Step 2: Update todo**

Add active item with plan/design/review/build/verification checkboxes.

## Task 10: Verification and PR

- [ ] **Step 1: Run focused tests**

```powershell
python -m pytest tests/test_flyer_starter_briefs.py tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tests/test_flyer_incident_replay.py tests/test_flyer_guest_order.py tests/test_flyer_schemas.py -q
python -m pytest tests/test_flyer_workflow.py tests/test_flyer_update_project.py -q
```

- [ ] **Step 2: Run py_compile**

```powershell
python -m py_compile src\agents\flyer\starter_briefs.py src\agents\flyer\intake.py src\agents\flyer\onboarding.py src\plugins\cf-router\actions.py src\plugins\cf-router\hooks.py src\platform\schemas.py
```

- [ ] **Step 3: Run diff check**

```powershell
git diff --check
```

- [ ] **Step 4: Request final PR reviewers**

Reviewers:
- Live behavior reviewer: sample/guided/text/one-time route trace.
- Hermes/runtime reviewer: no duplicate substrate, no production mutation, no provider/payment/dashboard drift.

- [ ] **Step 5: Open PR**

PR summary must include files changed, tests run, risks, deferred items, and `No deploy performed.`

## Deferred Items

- Self-eval/operator brief incidents for brief approval anomalies.
- LLM/Hermes structured extraction of arbitrary typed text into richer `FlyerRequestFields` preview.
- WhatsApp interactive buttons/list messages.
- Dashboard view of pending brief drafts.
- Source-edit exact-edit brief builder.
- Provider/model routing.
- Automatic generation without approval for high-confidence paid users.

## Self-review

- Spec coverage: sample, guided, text, and one-time guard are included.
- Review findings: both plan-review passes folded.
- Hermes-first: per-step owner table added.
- Drift: existing parser/facts/project creation are reused; no parallel fact schema.
- Scope: self-eval/operator brief deferred to avoid mixing control-plane observability with product-flow behavior.
