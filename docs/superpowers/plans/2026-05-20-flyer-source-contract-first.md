# Flyer Source Contract First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent source-flyer requests like F0061 from being downgraded into generic generated posters by extracting, locking, and QA-checking a source contract before generation.

**Architecture:** Match the proven in-tree Catering pattern: source media plus customer text becomes strict structured JSON, validated by Pydantic, stored as locked facts with provenance, then generation/QA acts against that contract. This is not a broad Hermes platform migration; reuse existing Hermes WhatsApp/media/state/audit substrate and add only Flyer-specific contract policy.

**Tech Stack:** Python 3, Pydantic v2 schemas in `src/platform/schemas.py`, JSON state via `safe_io`, existing cf-router plugin hooks/actions, OpenRouter vision via existing Flyer/Catering call pattern, pytest.

---

**Drift-check tag:** extends-Hermes

**New primitives introduced:** Flyer source contract model, source-contract extraction helper, exact-edit/reference policy guard, source-contract locked facts, two new `LogEntry` audit variants, `forbidden_substrings` QA gate.

## Hermes-First Analysis

| Domain | Hermes/in-tree capability found? | Decision |
|---|---|---|
| WhatsApp ingress/media cache | yes - Hermes gateway/cf-router and `/opt/shift-agent/.hermes/image_cache` | use existing path |
| Sender identity/account lookup | yes - sender block, `identify-sender`, Flyer customer store | use existing path |
| Source media OCR/vision | partial - in-tree Catering `parse-menu-photo` uses direct OpenRouter vision with strict schema; Flyer has three direct vision call sites | match Catering pattern now; **defer the `vision_client.py` chokepoint as a follow-up backlog item — NOT load-bearing for F0061** |
| Structured extraction schema | yes - Pydantic v2 patterns in `schemas.py`; Catering `MenuItem` extraction precedent | add Flyer-specific source contract schema (`extra="forbid"` on contract; LLM raw output parsed permissively then projected into the strict shape) |
| Approval / confirmation token alphabet | yes - `#XXXXX` 5-char codes via `generate_unique_code` for **money/state-mutating approvals** (Catering `MenuPendingUpdate`, Shift proposals); Flyer also has deterministic pending JSON state for **branch-choice** follow-ups (path 1 / path 2 / option 1 / option 2) | **Constrained exception (not drift):** keep deterministic word tokens (`SOURCE`/`NEW`) for the binary follow-up branch because (a) the customer is answering a branching question against an already-saved pending row, not minting a new approval ID; (b) the existing path-1/path-2 dialog at `_try_flyer_reference_scope_choice_intercept` already uses plain-word/numeric tokens without `#XXXXX`; (c) no money/external state changes on this reply; (d) the saved pending row is the audit anchor — confirmation does not need its own opaque token. Matches existing scope-choice convention. |
| State/audit | yes - JSON-on-disk, `FileLock`, `atomic_write_text/json`, `LogEntry` union | reuse; **add two new `_BaseEntry` subclasses to the LogEntry union**: `FlyerSourceContractExtracted` (Task 3) and `FlyerSourceVsNewChosen` (Task 5) |
| Image generation | partial - existing Flyer OpenRouter generation and OpenAI source-edit path | do not expand provider surface in this PR; fail closed when source edit provider unavailable |
| Visual/OCR QA | partial - `visual_qa.py` checks required locked facts via presence | extend to source-contract facts; add forbidden-substring negative check |

Awesome Hermes Agent ecosystem check: no turnkey Flyer Studio source-edit contract skill was found. Verdict: reuse Hermes/in-tree substrate and build the narrow Flyer-specific source-contract layer.

## Read First

- `AGENTS.md`
- `tasks/lessons.md` (especially 2026-05-19 entries on reference-scope continuation + queued-edit status check-ins)
- `docs/hermes-alignment.md`
- `src/agents/catering/scripts/parse-menu-photo`
- `src/agents/catering/skills/update_catering_menu/SKILL.md`
- `src/agents/flyer/reference_extract.py`
- `src/agents/flyer/facts.py`
- `src/agents/flyer/visual_qa.py`
- `src/agents/flyer/render.py`
- `src/plugins/cf-router/hooks.py`
- `src/plugins/cf-router/actions.py`
- `src/platform/schemas.py` (find existing `class _BaseEntry` and the `LogEntry` discriminated union)

## Scope Guard

In scope:

- F0061-class source-contract failure.
- Exact-edit vs new-reference policy + persisted `original_intent`.
- Source-flyer fact extraction/locking.
- QA against source-derived required facts AND forbidden-substring negative assertions.
- Two new `LogEntry` audit variants (`FlyerSourceContractExtracted`, `FlyerSourceVsNewChosen`).
- Focused parser/style poisoning fixes directly related to F0061 (final isolated commit; can be reverted without regressing the core fix).
- Golden regression coverage including the lessons.md 2026-05-19 "queue-status check-in" follow-up shape.

Out of scope:

- Dashboard/Cockpit UI.
- New marketing/onboarding features.
- Deploy/merge.
- Broad Hermes provider migration.
- Full manual queue product redesign.
- **Vision client chokepoint** (`src/platform/vision_client.py`) — deferred to follow-up backlog item; not load-bearing for F0061.

## File Ownership

- Modify: `src/platform/schemas.py` (FlyerSourceContract* + 2 new LogEntry variants + add union members)
- Modify: `src/agents/flyer/reference_extract.py`
- Modify: `src/agents/flyer/facts.py`
- Modify: `src/agents/flyer/visual_qa.py`
- Modify: `src/agents/flyer/render.py`
- Modify: `src/agents/flyer/scripts/create-flyer-project`
- Modify: `src/plugins/cf-router/hooks.py`
- Modify: `src/plugins/cf-router/actions.py`
- Test: `tests/test_flyer_schemas.py`
- Test: `tests/test_flyer_reference_extract.py`
- Test: `tests/test_flyer_golden_scenarios.py`
- Test: `tests/test_cf_router_flyer_routing.py`
- Test: `tests/test_flyer_visual_qa.py`
- Test: `tests/test_flyer_create_project.py`
- Test: `tests/test_flyer_source_edit_preflight.py`

## Task 1: Pin F0061 As A Failing Contract Regression

**Files:**
- Modify: `tests/fixtures/flyer_golden/live_customer_message_shapes.json`
- Modify: `tests/test_flyer_golden_scenarios.py`
- Modify: `tests/test_cf_router_flyer_routing.py`

- [ ] Add a red test for the exact F0061 sequence:
  - Original request says source flyer, do not change anything else.
  - Attached reference visibly belongs to Triveni Express.
  - Customer later replies `use as reference`.
  - Expected result: no immediate generic generation. System must ask explicit `NEW` vs `SOURCE`, or route to source-edit/manual queue.

Mocking surface (match existing tests in `tests/test_cf_router_flyer_routing.py`):

- Monkeypatch `actions.save_flyer_reference_scope_pending` to capture inputs (verify `original_intent="exact_source_edit"`).
- Monkeypatch `actions.consume_flyer_reference_scope_choice` to return a pending row with `original_intent="exact_source_edit"`.
- Monkeypatch `actions.trigger_create_flyer_project` (assert NOT called on the bare "use as reference" reply; assert IS called when reply is explicit "NEW").
- Monkeypatch `actions.send_flyer_text` to capture clarification text containing both "SOURCE" and "NEW".
- Monkeypatch `actions.lid_to_phone_via_identify_sender` + `actions.find_flyer_customer_by_sender` as the existing tests already do.

Suggested assertion shape:

```python
def test_exact_edit_request_cannot_be_downgraded_by_use_as_reference(monkeypatch, tmp_path):
    # 1. Seed scope-pending with original_intent="exact_source_edit" and the F0061 raw_request.
    # 2. Invoke _try_flyer_reference_scope_choice_intercept with text="use as reference".
    # 3. Assert send_flyer_text was called with body containing both "SOURCE" and "NEW".
    # 4. Assert trigger_create_flyer_project NOT called.
    # 5. Send follow-up "NEW" → trigger_create_flyer_project IS called with raw_request
    #    containing "Create a new original".
    # 6. Send follow-up "SOURCE" instead → trigger_create_flyer_project IS called with
    #    manual_edit_required=True and raw_request prefixed "Edit uploaded flyer/source artwork".
```

Also add a third scenario from `tasks/lessons.md` (2026-05-19 entry on queue-status check-ins):

```python
def test_queued_source_edit_status_checkin_does_not_reenter_clarification(monkeypatch):
    # After SOURCE was chosen and the edit is queued for manual review, a follow-up
    # "any update?" / "is it ready?" / "what's the status" must NOT re-enter the
    # SOURCE/NEW clarification path and must NOT call trigger_create_flyer_project.
```

- [ ] Add a fixture entry named `live_f0061_exact_edit_use_reference_downgrade`.

Fixture facts to include:

```json
{
  "original_request": "I'd like you use this flyer for Lakshmi's Kitchen. Do not change anything else in the flyer, except the changes asked explicitly. Changes I want. 1. Replace Triveni Express with Lakshmi's Kitchen branding. 2. Replace phone number to +17329837841. 3. Veg Thali Special, replace Rice with Jeera Rice. 4. Change address to 90 Brybar Dr, Saint Johns, FL.",
  "reply": "use as reference",
  "expected_policy": "requires_explicit_new_or_source_choice"
}
```

- [ ] Run the focused red tests.

Run:

```powershell
python -m pytest tests/test_flyer_golden_scenarios.py tests/test_cf_router_flyer_routing.py -q
```

Expected now: at least one new test fails on the current downgrade behavior.

## Task 2: Add Source Contract Schema + Audit Variants

**Files:**
- Modify: `src/platform/schemas.py`
- Test: `tests/test_flyer_schemas.py`

- [ ] Add Pydantic models near the existing Flyer models.

Required fields:

```python
class FlyerSourceContractSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(default="", max_length=160)
    items: list[str] = Field(default_factory=list, max_length=50)


class FlyerSourceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_business_names: list[str] = Field(default_factory=list, max_length=10)
    target_business_name: str = Field(default="", max_length=160)
    required_headings: list[str] = Field(default_factory=list, max_length=20)
    required_text: list[str] = Field(default_factory=list, max_length=100)
    sections: list[FlyerSourceContractSection] = Field(default_factory=list, max_length=20)
    requested_replacements: dict[str, str] = Field(default_factory=dict, max_length=50)
    forbidden_substrings: list[str] = Field(default_factory=list, max_length=50)
    preserve_layout: bool = False
    preserve_unmentioned_text: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = Field(default="", max_length=1000)
```

Field semantics (intentional):

- `preserve_layout` — geometric/visual structure (margins, positions, masthead). True for F0061's "do not change anything else".
- `preserve_unmentioned_text` — textual content not enumerated in `requested_replacements`. Also True for F0061.
- Distinct because future asks (e.g., "redesign the layout but keep menu prices") map to `preserve_layout=False, preserve_unmentioned_text=True`.
- `forbidden_substrings` — values that must NOT appear in the rendered output (the "old" side of brand/phone/address replacements where the customer asked to replace the previous value). Wired into QA in Task 6.

- [ ] Add optional field to `FlyerReferenceExtraction` (existing model is at `schemas.py:1468`, `extra="forbid"`):

```python
source_contract: Optional[FlyerSourceContract] = None
```

Backward compatibility: defaulting to `None` is compatible with reads of older sidecar JSON because the field is simply absent.

- [ ] Add two new `_BaseEntry` subclasses to the `LogEntry` discriminated union (locate union near `class _BaseEntry` in `src/platform/schemas.py`):

```python
class FlyerSourceContractExtracted(_BaseEntry):
    type: Literal["flyer_source_contract_extracted"] = "flyer_source_contract_extracted"
    project_id: str = Field(min_length=1, max_length=40)
    asset_id: str = Field(default="", max_length=40)
    asset_sha256: str = Field(default="", max_length=64)
    role: FlyerReferenceRole
    status: FlyerReferenceExtractionStatus
    headings_count: int = 0
    sections_count: int = 0
    replacements_count: int = 0
    forbidden_substrings_count: int = 0
    confidence: float = 0.0
    provider: str = Field(default="", max_length=120)


class FlyerSourceVsNewChosen(_BaseEntry):
    type: Literal["flyer_source_vs_new_chosen"] = "flyer_source_vs_new_chosen"
    chat_id: str = Field(default="", max_length=80)
    sender_phone: str = Field(default="", max_length=32)
    customer_id: str = Field(default="", max_length=40)
    original_intent: Literal["exact_source_edit", "generic_reference", "unknown"]
    choice: Literal["source", "new", "clarification_sent"]
    pending_age_sec: int = 0
```

Then append both to the `LogEntry = Union[...]` definition AND update the `_UnknownLogEntry` fallback validators if any. Mirror exactly how existing variants are registered.

- [ ] Add schema tests:
  - rejects unknown fields on `FlyerSourceContract`.
  - accepts section item names without prices.
  - supports requested replacement `{"Rice": "Jeera Rice"}` (dict key not a substring of value — verify QA Task 6 forbidden-substring guard does not blanket-ban the old key here).
  - both new `LogEntry` variants round-trip through the union and reject unknown `type` values.

Run:

```powershell
python -m pytest tests/test_flyer_schemas.py -q
```

Expected: schema tests pass.

## Task 3: Implement Source Contract Extraction

**Files:**
- Modify: `src/agents/flyer/reference_extract.py`
- Test: `tests/test_flyer_reference_extract.py`

- [ ] Add a source-contract prompt modeled after Catering `parse-menu-photo`: strict JSON only, never guess, preserve exact text.

Extraction output must include:

- visible source business names, e.g. `Triveni Express`
- visible headings, e.g. `Monday Thali Specials`, `Veg Thali Specials`, `Chicken Thali Specials`, `Goat Thali Specials`
- visible item names even without prices, e.g. `Rice`, `Dal`, `Pakora`, `Chicken Curry`
- requested replacements from customer text, e.g. `Triveni Express -> Lakshmi's Kitchen`, `Rice -> Jeera Rice`, old phone/address -> new phone/address where available
- preservation flags when customer says `do not change anything else`, `same layout`, `preserve`

- [ ] Add deterministic helper for typed customer replacement extraction so the common F0061 substitutions do not depend only on vision.

Suggested function:

```python
def extract_requested_replacements_from_text(raw_request: str) -> dict[str, str]:
    replacements: dict[str, str] = {}
    # replace X with Y / replace X to Y
    for match in re.finditer(
        r"\breplace\s+(?P<old>.+?)\s+(?:with|to)\s+(?P<new>.+?)(?=\.|\n|\d+\.\s|$)",
        raw_request,
        flags=re.IGNORECASE,
    ):
        old = " ".join(match.group("old").strip(" .,:;").split())
        new = " ".join(match.group("new").strip(" .,:;").split())
        if old and new:
            replacements[old] = new
    return replacements
```

- [ ] For `source_edit_template`, run source-contract extraction instead of returning `not_run`.

Policy:

- `source_edit_template` with provider unavailable should still create a `FlyerReferenceExtraction` with role `source_edit_template`, status `provider_unavailable`, and no false success.
- If sidecar/test provider is enabled, it should produce `source_contract` and extracted locked facts.

- [ ] When extraction succeeds (or fails with provider_unavailable), append a `FlyerSourceContractExtracted` log entry via the existing `log-decision-direct` chokepoint. Mirror how `parse-menu-photo` writes `MenuUpdateProposed` (via `ndjson_append`).

- [ ] Add tests:
  - **pin classify_reference_role for F0061 text:** assert that `classify_reference_role("I'd like you use this flyer for Lakshmi's Kitchen. Do not change anything else in the flyer, except the changes asked explicitly. Changes I want. 1. Replace Triveni Express with Lakshmi's Kitchen branding...", asset)` returns `"source_edit_template"`.
  - source-edit role no longer returns `status="not_run"` with detail "reference extraction not required".
  - text-only replacement parser extracts `{"Rice": "Jeera Rice"}`, `{"Triveni Express": "Lakshmi's Kitchen"}`, and an old/new phone pair from the F0061 text.
  - source contract supports item names without prices.
  - low-confidence contract routes manual review.
  - audit row `FlyerSourceContractExtracted` is appended once per extraction run with correct counts.

Run:

```powershell
python -m pytest tests/test_flyer_reference_extract.py -q
```

Expected: new tests pass.

## Task 4: Convert Source Contract Into Required Locked Facts

**Files:**
- Modify: `src/agents/flyer/facts.py`
- Modify: `src/agents/flyer/scripts/create-flyer-project`
- Test: `tests/test_flyer_create_project.py`

- [ ] Add function:

```python
def source_contract_locked_facts(contract: FlyerSourceContract, *, asset: FlyerAsset, message_id: str = "") -> list[FlyerLockedFact]:
    facts: list[FlyerLockedFact] = []
    for idx, heading in enumerate(contract.required_headings):
        fact = _fact(
            f"source_heading:{idx}",
            "Source heading",
            heading,
            "reference_vision",
            required=contract.preserve_layout or contract.preserve_unmentioned_text,
            message_id=message_id,
        )
        if fact:
            facts.append(fact.model_copy(update={"source_asset_id": asset.asset_id, "source_sha256": asset.sha256}))
    for section_idx, section in enumerate(contract.sections):
        heading_fact = _fact(
            f"source_section:{section_idx}:heading",
            "Source section",
            section.heading,
            "reference_vision",
            required=contract.preserve_layout or contract.preserve_unmentioned_text,
            message_id=message_id,
        )
        if heading_fact:
            facts.append(heading_fact.model_copy(update={"source_asset_id": asset.asset_id, "source_sha256": asset.sha256}))
        for item_idx, item in enumerate(section.items):
            item_fact = _fact(
                f"source_section:{section_idx}:item:{item_idx}",
                "Source item",
                item,
                "reference_vision",
                required=contract.preserve_layout or contract.preserve_unmentioned_text,
                message_id=message_id,
            )
            if item_fact:
                facts.append(item_fact.model_copy(update={"source_asset_id": asset.asset_id, "source_sha256": asset.sha256}))
    for repl_idx, (old, new) in enumerate(contract.requested_replacements.items()):
        for suffix, label, value, required in [
            ("old", "Replaced source text", old, False),
            ("new", "Required replacement text", new, True),
        ]:
            fact = _fact(
                f"replacement:{repl_idx}:{suffix}",
                label,
                value,
                "customer_text",
                required=required,
                message_id=message_id,
            )
            if fact:
                facts.append(fact.model_copy(update={"source_asset_id": asset.asset_id, "source_sha256": asset.sha256}))
    return facts
```

Fact ID convention:

- `source_heading:0`
- `source_section:0:heading`
- `source_section:0:item:0`
- `replacement:0:old`
- `replacement:0:new`

Rules:

- Replacement new values are required.
- Source headings/items are required when `preserve_unmentioned_text=True` or `preserve_layout=True`.
- Source old business names are not required in output if they are intentionally replaced.
- Target business name, phone, and address should use existing customer/new typed facts where available.

- [ ] In `create-flyer-project`, merge `extraction.extracted_facts` and source-contract-derived facts when extraction succeeds.

- [ ] Update missing-required behavior so `source_edit_template` with a missing/failed source contract queues manual review, not generation.

- [ ] Add tests:
  - F0061-style source contract creates locked facts for `Monday Thali Specials` and `Jeera Rice`.
  - Missing provider queues manual review.
  - Source old brand `Triveni Express` is not required to remain when replacement maps it to `Lakshmi's Kitchen`.

Run:

```powershell
python -m pytest tests/test_flyer_create_project.py tests/test_flyer_reference_extract.py -q
```

Expected: tests pass.

## Task 5: Fix Exact-Edit vs Reference Policy Gate (load-bearing)

**Files:**
- Modify: `src/plugins/cf-router/actions.py`
- Modify: `src/plugins/cf-router/hooks.py`
- Test: `tests/test_cf_router_flyer_routing.py`

### 5.1 Persist `original_intent` in `reference_scope_pending.json`

This is the load-bearing wiring. Three edits required, in order:

- [ ] **Extend `save_flyer_reference_scope_pending` signature** (`actions.py:2246-2257`): add kwarg `original_intent: str = "unknown"`. Add the field to the dict appended at `actions.py:2273-2287`:

  ```python
  pending.append({
      ...
      "original_intent": original_intent,
      ...
  })
  ```

- [ ] **Compute `original_intent` at the caller before save** in `hooks.py`. Today scope-check returns at line 527 *before* line 528 (`is_exact_reference_edit_request`) is evaluated. Edit `hooks.py` so that immediately before `save_flyer_reference_scope_pending(...)` (currently at line 508) the value is computed:

  ```python
  original_intent = (
      "exact_source_edit"
      if media_path and actions.is_exact_reference_edit_request(text, has_media=True)
      else "generic_reference"
  )
  actions.save_flyer_reference_scope_pending(
      chat_id=chat_id,
      sender_phone=phone,
      customer=customer,
      raw_request=raw_request,
      media_path=media_path,
      scope=scope or {},
      original_intent=original_intent,
  )
  ```

- [ ] **Propagate `original_intent` through `consume_flyer_reference_scope_choice`** so the returned pending dict contains it for `_try_flyer_reference_scope_choice_intercept` to branch on.

### 5.2 Clarification path on `use as reference`

- [ ] In `_try_flyer_reference_scope_choice_intercept` (`hooks.py:701-815`), when `choice == "use_reference"` AND `pending["original_intent"] == "exact_source_edit"`:

  - DO NOT call `trigger_create_flyer_project`.
  - Update the pending row status to `awaiting_source_vs_new_choice`, preserve `raw_request` / `media_path` / `customer` / `original_intent`.
  - Send the clarification (text below).
  - Append a `FlyerSourceVsNewChosen` audit row with `choice="clarification_sent"`.
  - Return `{"action": "skip", "reason": "cf-router flyer source-vs-new clarification sent"}`.

  Clarification text:

  ```text
  Flyer Studio
  ------------
  I can do this two ways:

  Reply SOURCE to keep the same flyer design and make only your requested changes.
  Reply NEW to create a new flyer inspired by this one. It will not preserve the exact layout.
  ```

### 5.3 Deterministic parsing of the follow-up

- [ ] Add `_source_contract_followup_choice` in `actions.py`:

  ```python
  def _source_contract_followup_choice(text: str) -> str:
      body = " ".join(flyer_visible_message_text(text).split()).lower().strip(" .!,:;-")
      if body in {"source", "keep source", "same flyer", "exact edit", "option 1", "1"}:
          return "source"
      if body in {"new", "new flyer", "inspired", "inspired by", "option 2", "2"}:
          return "new"
      return ""
  ```

  Plus `consume_flyer_source_vs_new_choice(text, *, chat_id, sender_phone) -> Optional[dict]` that:
  - holds `_reference_scope_state_lock()` for the full read-modify-write transaction;
  - matches a pending row with `status == "awaiting_source_vs_new_choice"` and `sender_phone`/`chat_id` match;
  - returns the row (with `choice` attached) only when `_source_contract_followup_choice(text)` is non-empty;
  - removes the row from the state file on consumption (matches existing `consume_flyer_reference_scope_choice` pattern).

### 5.4 SOURCE branch — wire into existing exact-edit flow (NOT a re-implementation)

- [ ] Add a new intercept `_try_flyer_source_vs_new_choice_intercept` ordered immediately after `_try_flyer_reference_scope_choice_intercept` in `hooks.py`. Behavior:

  - On `choice == "source"`: rebuild `raw_request` with the marker the source-edit renderer requires (matches `hooks.py:531`):

    ```python
    visible_request = " ".join(actions.flyer_visible_message_text(pending["raw_request"]).split())
    raw_request = f"Edit uploaded flyer/source artwork. Customer requested: {visible_request}"
    ```

    Then call `actions.trigger_create_flyer_project(..., manual_edit_required=True)`. This routes through the existing exact-edit handler at `hooks.py:566-657` (preflight → manual-review queue when `OPENAI_API_KEY` is PLACEHOLDER, generation when present). Append `FlyerSourceVsNewChosen` audit row with `choice="source"`.

  - On `choice == "new"`: call `actions.trigger_create_flyer_project(...)` WITHOUT `manual_edit_required`, with `raw_request` built the same way the current `use_reference` path builds it (existing `hooks.py:735-740` text). Append `FlyerSourceVsNewChosen` audit row with `choice="new"`.

### 5.5 Generic-reference customers keep the current behavior

- [ ] When `pending["original_intent"] == "generic_reference"`, `use as reference` continues through the existing path (no clarification, no extra round-trip). Only `exact_source_edit` triggers the SOURCE/NEW question.

### 5.6 Tests

- [ ] `use as reference` after exact-edit + scope-clarify → clarification sent, no `trigger_create_flyer_project` call.
- [ ] `NEW` after clarification → `trigger_create_flyer_project` called WITHOUT `manual_edit_required`, raw_request contains "Create a new original".
- [ ] `SOURCE` after clarification → `trigger_create_flyer_project` called WITH `manual_edit_required=True`, raw_request prefixed `Edit uploaded flyer/source artwork`.
- [ ] Generic reference requests (`original_intent="generic_reference"`) still use `use as reference` without the extra step.
- [ ] After SOURCE is chosen and the project is queued, a follow-up `any update?` / `is it ready?` / `what's the status` does NOT re-enter clarification, does NOT call `trigger_create_flyer_project`, and routes to the existing queue-status handler (matches `tasks/lessons.md` 2026-05-19 entry).
- [ ] State-lock test: `consume_flyer_source_vs_new_choice` holds the lock across the full read-modify-write (mirror existing `test_reference_scope_choice_transaction_holds_state_lock`).
- [ ] Both audit variants serialize/deserialize through `LogEntry`.

Run:

```powershell
python -m pytest tests/test_cf_router_flyer_routing.py -q
```

Expected: tests pass.

## Task 6: Make QA Source-Contract Aware

**Files:**
- Modify: `src/agents/flyer/visual_qa.py`
- Modify: `src/agents/flyer/render.py`
- Test: `tests/test_flyer_visual_qa.py`

### 6.1 Positive presence checks (existing pattern)

- [ ] Ensure source-contract locked facts (`source_heading:N`, `source_section:N:heading`, `source_section:N:item:N`, `replacement:N:new`) flow into `project.locked_facts` with `required=True` for the F0061 case (`preserve_layout=True` or `preserve_unmentioned_text=True`). The existing `run_visual_qa` loop at `visual_qa.py:240-247` then enforces them via `_value_present_in`. No change to that loop is needed.

### 6.2 Forbidden-substring negative check (new)

The plan's negative-assertion surface is `FlyerSourceContract.forbidden_substrings: list[str]` (added in Task 2). At project creation, populate it from the customer's explicit replacement pairs where the OLD value is a brand/phone/address (not a menu item — the rule below):

- [ ] In `create-flyer-project` after building locked facts, populate `forbidden_substrings` by walking `contract.requested_replacements`:
  - If the OLD value matches a brand-name heuristic (length ≥ 3, contains an uppercased word, NOT in `contract.sections.items`), add it to `forbidden_substrings`.
  - If the OLD value looks like a phone (≥ 10 digits) or a US address (street + city pattern), add it.
  - Menu-item swaps (e.g. `Rice → Jeera Rice`) are NOT added — both old and new can legitimately co-exist in the rendered flyer.

  The rule rationale: customers replacing brand identity expect the old brand gone; customers swapping menu items often want the substitution shown, not the original erased.

- [ ] Persist `forbidden_substrings` on the project (carry through `FlyerReferenceExtraction.source_contract` → reachable from `FlyerProject` via `reference_extractions[0].source_contract`). `run_visual_qa` resolves at QA time via `project.reference_extractions`.

- [ ] Extend `run_visual_qa` (`visual_qa.py:205+`) with a second loop after the locked-fact loop:

  ```python
  for contract in (
      ext.source_contract
      for ext in project.reference_extractions
      if ext.source_contract
  ):
      for forbidden in contract.forbidden_substrings:
          normalized_forbidden = _normalize_text_for_match(forbidden)
          if _text_value_present_in(normalized, normalized_forbidden):
              blockers.append(f"replaced source text still visible: {forbidden}")
  ```

  Reuses the existing word-boundary-aware `_text_value_present_in` so the check matches the positive-presence semantics.

### 6.3 Text manifest honesty

- [ ] In `render.py:write_text_manifest`, audit the language around `verification_mode="declared_render_facts"`. The current code at `render.py:661-662` does `rendered = list(expected)` — making "rendered" a tautological copy of "expected". Either:
  - Rename `rendered_facts` to `declared_facts` in the manifest schema AND update `validate_text_manifest_file` consumers, OR
  - Keep field names for backward compatibility but add an explicit field `is_rendered_proof: bool = False` and a comment that this manifest is NOT image-pixel verification.

  Pick the second (additive) approach to keep this PR scoped — schema rename can come in a follow-up.

- [ ] Keep `_instruction_leak_blockers` as a useful template-leakage lint; do not remove.

### 6.4 Blocker examples

```python
"missing required visible fact: source_heading:0"
"missing required visible fact: source_section:0:item:2"
"missing required visible fact: replacement:0:new"
"replaced source text still visible: Triveni Express"
```

### 6.5 Tests

- [ ] Generic poster OCR text without `Monday Thali Specials` fails QA when the source contract includes that heading.
- [ ] OCR text containing `Jeera Rice` passes the `replacement:0:new` presence check.
- [ ] OCR text still containing `Triveni Express` fails when `forbidden_substrings=["Triveni Express"]`.
- [ ] Menu-item replacement (`Rice → Jeera Rice`) does NOT add `Rice` to `forbidden_substrings`; OCR text containing `Rice` (e.g. as part of `Brown Rice`) passes.
- [ ] Brand replacement DOES add the old brand to `forbidden_substrings`.
- [ ] Phone replacement adds the old phone digits to `forbidden_substrings`.

Run:

```powershell
python -m pytest tests/test_flyer_visual_qa.py -q
```

Expected: tests pass.

## Task 7: Clean Parser/Style Poisoning In The F0061 Path (isolated, last commit)

**Files:**
- Modify: `src/agents/flyer/scripts/create-flyer-project`
- Modify: `src/agents/flyer/render.py`
- Test: `tests/test_flyer_create_project.py`
- Test: `tests/test_flyer_renderer.py`

> **Scope discipline:** This task is RELATED to F0061 but not on the load-bearing path (Tasks 1-6 already block the F0061 downgrade independently). Land it as the **final isolated commit** in the PR so reviewers can revert it without regressing the paradigm change.

- [ ] Remove bare `brand` / `branding` from `_is_product_or_brand_promo` unless paired with explicit promo/product terms.

Rule:

- `replace Triveni branding with Lakshmi's Kitchen branding` is an edit instruction, not a product-promo style request.
- `brand-forward product promotion` can still be product-promo.

- [ ] Replace substring category matching with phrase/word matching. The bug today is at `src/agents/flyer/render.py:879-880`:

  ```python
  def _context_has(context: str, terms: set[str]) -> bool:
      return any(term in context for term in terms)  # substring — matches "spa" inside "space"
  ```

  Replace with the word-boundary-aware version:

  ```python
  def _context_has(context: str, terms: set[str]) -> bool:
      for term in terms:
          if " " in term or "-" in term:
              if term in context:
                  return True
          elif re.search(rf"\b{re.escape(term)}\b", context):
              return True
      return False
  ```

  This fixes three call sites simultaneously: `_is_food_or_grocery_project` (line 885), `_design_direction` SALON/TAX/CLEANING/MARKETING gates (lines 890-896), and the body-rendering check at line 430.

- [ ] Tests:
  - `clean space for address` does not match salon `spa`.
  - `replace branding` does not trigger grocery product style.
  - Food/kitchen still routes to food/grocery direction.
  - `transparent`, `wraparound`, `Hispanic` do not match `spa`.

Run:

```powershell
python -m pytest tests/test_flyer_create_project.py tests/test_flyer_renderer.py -q
```

Expected: tests pass.

## Task 8: Provider Posture — Regression-Pin Existing Behavior

**Files:**
- Possibly modify: `src/plugins/cf-router/actions.py` (only if SOURCE branch from Task 5 needs to wire into the existing preflight more explicitly)
- Test: `tests/test_flyer_source_edit_preflight.py`
- Test: `tests/test_cf_router_flyer_routing.py`

> **Scope note:** Most of this task is *regression-pinning* the existing fail-closed behavior at `flyer_source_edit_preflight` and `hooks.py:566-657`, not new code. Adding the SOURCE branch in Task 5 just routes more requests through the same preflight; we lock down that the route does not silently fall back to generic generation.

- [ ] Keep current source-edit preflight behavior: no `OPENAI_API_KEY` (or `PLACEHOLDER`) means source edit unavailable → manual review queue. Confirm at `actions.py:flyer_source_edit_preflight` (~line 2071) and `workflow.py:source_edit_provider_ready` (~line 298).

- [ ] Ensure the SOURCE branch added in Task 5 reaches `flyer_source_edit_preflight` and queues manual review when provider unavailable. Customer copy MUST come from `send_flyer_manual_edit_ack` (existing), not invent new strings.

Expected customer-safe behavior (already exists in `MANUAL_REVIEW_REASON_LINES["source_edit_provider_unavailable"]`):

```text
Your edit is queued for a designer to apply by hand. I have the requested changes and the saved account details.
```

- [ ] Tests (regression-pin shape, not new logic):
  - missing OpenAI key + customer chooses SOURCE → `--queue-manual-review` with `--manual-reason-code source_edit_provider_unavailable`; no `trigger_generate_flyer_concepts` call.
  - missing OpenAI key + customer chooses NEW → existing generic generation path runs (this is the intentional escape hatch for the customer; do not block).
  - customer-facing message contains no provider name or internal queue jargon.

Run:

```powershell
python -m pytest tests/test_flyer_source_edit_preflight.py tests/test_cf_router_flyer_routing.py -q
```

Expected: tests pass.

## Task 9: ~~Vision Client Chokepoint~~ — DEFERRED

This task is intentionally **not** in scope for this PR. It is documented in the Deferred Items section below.

Rationale: `src/platform/vision_client.py` extraction is hygienic refactoring of six parallel OpenRouter call sites (Flyer reference_extract, Flyer visual_qa, Flyer check-flyer-reference-scope, Catering parse-menu-photo, Catering vision-auth-smoke, Expense Bookkeeper extract-receipt). It is *not* load-bearing for F0061 — even after the vision-client refactor, the contract-extraction + policy-gate work in Tasks 1-8 is still required. Combining them violates the PR-B2 anti-pattern (8 commits / 476 LOC where only 2 commits / 125 LOC were the actual paradigm change).

Open a follow-up backlog item with the design sketch from the prior plan revision.

## Task 10: Full Focused Verification

Run:

```powershell
python -m pytest tests/test_flyer_reference_extract.py tests/test_flyer_create_project.py tests/test_flyer_visual_qa.py tests/test_cf_router_flyer_routing.py tests/test_flyer_golden_scenarios.py tests/test_flyer_source_edit_preflight.py -q
```

Run py_compile/compileall for changed Python:

```powershell
python -m compileall -q src\agents\flyer src\plugins\cf-router src\platform
```

Run diff whitespace check:

```powershell
git diff --check
```

If frontend is untouched, do not run frontend build. If `web/` changes accidentally appear in diff, stop and remove them unless explicitly intended.

## Task 11: PR And Handoff

- [ ] Confirm no unrelated dirty files were touched.

Run:

```powershell
git status --short
git diff --stat
```

- [ ] Commit in logical pieces (Task 7 LAST so the parser-style fix can be reverted independently):

Suggested commits:

```text
test(flyer): pin F0061 source-contract downgrade regression  (Task 1)
feat(schemas): FlyerSourceContract + 2 LogEntry audit variants  (Task 2)
feat(flyer): source-contract extraction for source_edit_template role  (Task 3)
feat(flyer): source-contract locked facts in create-flyer-project  (Task 4)
fix(cf-router): persist original_intent + SOURCE/NEW clarification  (Task 5)
feat(flyer): forbidden-substring negative QA + source-contract presence  (Task 6)
test(flyer): regression-pin source-edit provider posture  (Task 8)
fix(flyer): word-boundary _context_has + brand/branding edit semantics  (Task 7 — isolated, last)
```

- [ ] Open PR for review. Do not merge or deploy.

PR summary must include:

- Files changed (broken out by commit).
- Tests run.
- Risks.
- Deferred items (with backlog links).
- Explicit note: exact source edits remain manual/provider-blocked unless `OPENAI_API_KEY` is configured.

## Acceptance Criteria

- **F0061 three-way interaction:** When scope check fires AND original raw_request matched `is_exact_reference_edit_request`, a `use as reference` reply MUST send the SOURCE/NEW clarification, never call `trigger_create_flyer_project`. (This is the load-bearing regression check.)
- F0061-class request cannot generate a generic poster without explicit `NEW`.
- Customer reply `SOURCE` routes into the existing exact-edit/manual-review path; never silently falls back to generic generation when provider is unavailable.
- After SOURCE-chosen project is queued for manual review, follow-up `any update?` / `is it ready?` / `what's the status` does NOT re-enter SOURCE/NEW clarification and does NOT create a new project (matches `tasks/lessons.md` 2026-05-19 entry).
- Exact-edit requests fail closed/manual when source-edit provider is unavailable; customer copy reuses existing `MANUAL_REVIEW_REASON_LINES["source_edit_provider_unavailable"]` text.
- Source flyer headings/items/replacements become required locked facts when `preserve_layout=True` or `preserve_unmentioned_text=True`.
- QA fails when required source-contract text is missing.
- QA fails when a forbidden substring (replaced brand/phone/address) is still visible.
- Menu-item replacements (e.g. `Rice → Jeera Rice`) do NOT auto-populate `forbidden_substrings`; both old and new can co-exist.
- `branding` edit text does not poison style into grocery/product-promo.
- `spa` no longer matches unrelated words like `space`/`transparent`/`Hispanic`.
- Both new `LogEntry` variants (`FlyerSourceContractExtracted`, `FlyerSourceVsNewChosen`) round-trip through the discriminated union.
- Golden suite includes the F0061 downgrade scenario.
- Focused tests and compile checks pass.

## Deferred Items

- **`src/platform/vision_client.py` chokepoint** (Task 9 deferred). Open new backlog item: refactor six parallel OpenRouter vision call sites (Flyer `reference_extract`, Flyer `visual_qa`, Flyer `check-flyer-reference-scope`, Catering `parse-menu-photo`, Catering `vision-auth-smoke`, Expense Bookkeeper `extract-receipt`). Carry forward the API sketch (`openrouter_vision_json`, typed exceptions) from the prior plan revision.
- **Text manifest schema rename** (`rendered_facts` → `declared_facts`) — additive field added in Task 6.3; structural rename deferred.
- Cockpit/dashboard improvements.
- Production provider decision: configure OpenAI source-edit path or productize exact edits as designer-assisted only. (Tracked in `tasks/flyer-source-edit-provider-posture-2026-05-20.md` if that file exists; otherwise open new tracking doc.)
