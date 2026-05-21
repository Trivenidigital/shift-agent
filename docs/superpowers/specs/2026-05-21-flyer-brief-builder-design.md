# Flyer Brief Builder Design

**Drift-check tag:** extends-Hermes

**New primitives introduced:** Flyer intake brief-preview statuses, compact starter idea choices, pending approved-brief provenance on `FlyerIntakeSession`.

## Goal

Make Flyer Studio easy for low-effort customers: they can pick an idea, answer guided questions, or type a short request, then approve a clear brief before the system spends image-generation work.

This design intentionally does not change provider routing, source-edit handling, payment gates, dashboard UI, or production deployment behavior.

## Drift Check

| Existing primitive | Current behavior | Design decision |
|---|---|---|
| `FlyerIntakeSession` | Stores language/mode/guided answers and reference media | Extend it; do not add a new JSON file or table |
| `starter_briefs.py` | Full editable category prompt sent once per account | Keep; add compact idea choices alongside it |
| `create-flyer-project` | Extracts `FlyerRequestFields`, hydrates saved profile, builds locked facts | Approved brief remains raw request text for this parser |
| `facts.py` | Creates profile/customer/source locked facts | Reuse; do not duplicate fact logic in intake |
| cf-router route order | Protects onboarding, active project, fresh intent, and source-edit branches | Add only explicit pending-intake exceptions |
| guest order path | Gates one-time orders through existing order/payment state | Preserve; do not create generation projects from unpaid quick-flyer intake |

## Hermes-first Analysis

| Step | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp input/output | yes - Hermes gateway/cf-router substrate | Reuse |
| Sender identity | yes - existing Hermes/Shift identity helpers | Reuse |
| Language-capable chat | yes - Hermes can converse in customer languages | Store selected language; deterministic v0.1 copy |
| Structured extraction | yes - Hermes can do schema extraction, but Flyer has a deployed parser | Reuse deployed Flyer parser for v0.1 |
| Media/OCR | yes - Hermes vision/OCR substrate | Not in this PR |
| Approval substrate | yes - Hermes has approval patterns | Use simple Flyer `APPROVE` gate, not cross-agent approval code |
| State/audit substrate | yes - JSON state + decisions.log patterns | Extend current store and cf-router audit |
| Flyer product policy | none found | Build locally |

awesome-hermes-agent ecosystem check: no purpose-built Flyer Studio customer brief-builder skill found. Verdict: custom Flyer policy on Hermes substrate.

## State Machine

Existing statuses remain. New statuses:

```text
choosing_sample_idea
text_awaiting_brief
brief_pending_approval
```

New `FlyerIntakeSession` fields:

```python
brief_raw_request: str = ""
brief_display_request: str = ""
mode_prompt_version: str = ""
brief_source: Literal["", "sample", "guided", "text"] = ""
brief_approved_at: Optional[datetime] = None
brief_approved_message_id: str = ""
```

The approved brief is intentionally stored as raw request text. `create-flyer-project` remains responsible for `FlyerRequestFields`, profile hydration, locked facts, source contracts, QA, and project status.

The `handle-flyer-intake` CLI result must also return `brief_source`, `brief_approved_at`, and `brief_approved_message_id` when approval happens so cf-router can write those values into the existing audit detail before the intake session is discarded. Approval state is cleared only after cf-router successfully hands the approved brief to project creation. If project creation fails, the pending brief remains saved and the customer receives a retry/update message instead of falling through silently.

## Route Priority

cf-router must evaluate active pending intake before active-project revision only for these statuses:

```text
choosing_sample_idea
text_awaiting_brief
guided_collecting_goal
guided_collecting_schedule
guided_collecting_items
guided_collecting_location
guided_collecting_assets
brief_pending_approval
```

The early language/mode statuses deliberately do not override an explicit active/trial flyer request. If the customer abandons setup and later sends a complete flyer request, #150 fresh-intent and active-project protections should win. The old stale-session guard remains: active/trial customers with no matching active intake session, or with a completed/discarded stale session, still route through #150 protections.

## Customer Routes

### Pick an Idea

Mode prompt:

```text
1. Pick an idea
2. Guide me
3. I'll type
```

Selecting `1` stores `choosing_sample_idea` and sends two compact choices.

Selecting an idea stores `brief_raw_request` and sends the brief preview. No project is created before approval.

For active/trial customers, the direct sample-idea path must resolve the saved profile by phone or by LID/chat id so low-typing customers do not get sent back to language selection.

For new free-trial customers who choose `Pick an idea` before onboarding, onboarding completion must continue into `choosing_sample_idea`; it must not fall back to generic text mode or the full starter brief.

For an existing active/trial customer who sends a vague opener like `Create flyer`, cf-router should skip language/mode friction and start directly at `choosing_sample_idea` using the saved customer language/profile. This preserves the “customers do not want to type” goal.

### Guide Me

Selecting `2` starts existing guided collection.

The final guided answer no longer returns `create_project` immediately. It stores the synthesized raw request, keeps `reference_media_path`, sends the preview, and waits for approval.

### I'll Type

Selecting `3` stores `text_awaiting_brief`.

The next typed flyer request is converted into:

```text
Create a professional flyer for <saved business>.
Customer request: <visible customer request>.
Use saved business name, address, phone, and logo.
Preferred flyer language: <selected language>.
```

This parser-facing raw request is later passed to `create-flyer-project`. The WhatsApp preview uses `brief_display_request`, a customer-friendly summary that hides parser/audit scaffolding such as `Brief source`, `Preferred flyer language`, saved-profile instructions, project ids, provider names, and reason codes.

### In-flight Prompt Compatibility

New mode prompts use `1=Pick an idea`, `2=Guide me`, `3=I'll type`. Existing live `choosing_mode` sessions created before this PR have no `mode_prompt_version`; those sessions preserve the old interpretation (`1=guided`, `2=text`) so customer replies to already-sent prompts are not misrouted.

### One-Time

`quick_flyer` remains gated by existing guest order/payment readiness. This PR may document the guard with tests, but should not move payment gating.

## Approval and Edit Semantics

Approval accepts:

```text
APPROVE
Approve
approve.
yes create it
yes start
go ahead
```

Sender-block-wrapped replies must use visible customer text.

Editing a pending brief:

- schedule/time/date cues append an instruction to `brief_raw_request`
- item/add/remove/include cues append an instruction
- language cues append or update the preferred language instruction
- unknown edits append as `Customer update before generation: ...`

The system re-sends the preview after edits. It does not silently generate.

Cancel semantics:

```text
cancel
stop
never mind
```

discard the intake session and send a short cancellation confirmation.

## Brief Preview Copy

```text
Flyer Studio
------------
I will create this flyer:

Business: <saved business or this business>
Request: <brief summary/raw request>
Language: <language label>

Reply APPROVE to start, or tell me what to change.
```

Forbidden in preview copy:

```text
Project F
provider
reason_code
manual_edit_required
operator
```

## Starter Idea Localization

v0.1 localization is curated shell text plus usable examples. The internal raw request remains English-ish where needed for current parser reliability, but the customer-facing choice prompt should respect selected language for labels/instructions.

Supported initial shells:
- English
- Telugu
- Hindi

Other languages fall back to English shell plus selected-language label.

All localized shells must keep numeric replies and `APPROVE` visibly present, because v0.1 parsing remains numeric/English-command based. Localized approval words are deferred until tests cover them.

## Provenance

During pending preview:

- `brief_source` records `sample`, `guided`, or `text`.
- `last_message_id` records the latest customer message.
- `brief_approved_at` and `brief_approved_message_id` are populated only after approval.

The `create_project` action returns the approved raw request and reference media path. This PR does not add project-level brief provenance; that is deferred until self-eval uses it. The core evidence lives long enough in intake tests and cf-router audit for this behavior change.

cf-router audit detail for approved brief handoff must include:

```text
brief_source=<sample|guided|text>; brief_approved_message_id=<message id>
```

This is not a new audit variant; it is provenance in the existing `flyer_project_created`/intake handoff trail.

## Test Strategy

Schema:
- new statuses/fields accepted
- unknown fields rejected

Starter ideas:
- compact choices per category
- initial Telugu/Hindi shells
- no internal terms

Intake:
- text mode persists `text_awaiting_brief`
- typed request returns `brief_preview`
- approval variants return `create_project`
- edits re-preview
- cancel discards
- guided preserves media through approval
- sample choice previews before generation
- approved raw request round-trips through `create-flyer-project` and produces required saved-profile fields

cf-router:
- pending brief status outranks active project/fresh-intent routing
- no project creation before approval
- approved brief reaches `_try_flyer_primary_intercept`
- stale sessions do not swallow new work
- #150 evening-snacks bypass remains green
- #155 replay-style pre-gateway fixture covers text -> preview -> approve with sender-block-shaped input
- #157 visible text revision tests stay in focused verification

One-time:
- unpaid quick flyer remains guest-order/payment gated
- no provider/payment mutation
- trial/free-trial quota is consumed only at approved generation reservation, not preview

Replay:
- add one transcript-style guard for text -> preview -> approve.

## Non-goals

- self-eval/operator brief incidents
- dashboard changes
- source-edit brief builder
- LLM prompt rewriting
- WhatsApp buttons/list UI
- provider/model routing
- deploy
