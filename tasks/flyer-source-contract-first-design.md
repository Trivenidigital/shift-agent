# Flyer Source Contract First — design

**Drift-check tag:** extends-Hermes

**New primitives introduced:** `FlyerSourceContract` + `FlyerSourceContractSection` Pydantic models; two `_BaseEntry` audit subclasses (`FlyerSourceContractExtracted`, `FlyerSourceVsNewChosen`); `forbidden_substrings` QA negative-assertion mechanism; `original_intent` persisted on `reference_scope_pending.json`; `_source_contract_followup_choice` deterministic parser; `consume_flyer_source_vs_new_choice` lock-safe state consumer; `_try_flyer_source_vs_new_choice_intercept` cf-router intercept.

**Plan reference:** `docs/superpowers/plans/2026-05-20-flyer-source-contract-first.md`

## Hermes-first capability checklist

| # | Implementation step | `[Hermes]` or `[net-new]` |
|---|---|---|
| 1 | WhatsApp inbound + media cache + sender block + lid→phone | `[Hermes]` — `cf-router` + `identify-sender` |
| 2 | Source flyer image vision read | `[Hermes]` substrate; ~120 LOC of Flyer-specific prompt + JSON validation that mirrors Catering `parse-menu-photo` |
| 3 | Strict JSON validation of vision output | `[Hermes]` — Pydantic v2 `model_validate_json`; ~5 LOC |
| 4 | `FlyerSourceContract` + sections + replacements + forbidden | `[net-new]` ~50 LOC |
| 5 | `FlyerSourceContractExtracted` + `FlyerSourceVsNewChosen` audit variants in `LogEntry` union | `[net-new]` ~40 LOC |
| 6 | Locked-fact generation for source-contract slots | `[net-new]` ~60 LOC; reuses `_fact`/`merge_locked_facts` |
| 7 | `original_intent` persist + propagate through `reference_scope_pending.json` | `[net-new]` ~15 LOC (one new kwarg, one new field) |
| 8 | SOURCE/NEW clarification reply + deterministic parser | `[net-new]` ~30 LOC; reuses existing scope-choice helper pattern |
| 9 | SOURCE branch wire into existing `manual_edit_required=True` flow | `[Hermes]` — reuses `hooks.py:566-657` |
| 10 | `forbidden_substrings` populated from brand/phone/address replacements | `[net-new]` ~30 LOC |
| 11 | `run_visual_qa` second loop on `forbidden_substrings` | `[net-new]` ~25 LOC; reuses `_text_value_present_in` + `_normalize_text_for_match` |
| 12 | Word-boundary `_context_has` | `[net-new]` ~10 LOC |
| 13 | Tests | `[net-new]` ~400 LOC |

Awesome-Hermes-Agent ecosystem check: no installable skill replaces the Flyer-specific source-contract policy. The vision call itself is Hermes-style structured extraction; everything else is Flyer-specific policy that does not exist in the ecosystem.

## Drift-rule self-checks

- ✅ Read `src/agents/catering/scripts/parse-menu-photo` (vision prompt + Pydantic `MenuItem` validation + confirmation code pattern; 320 LOC). Used as structural template.
- ✅ Read `src/agents/catering/skills/update_catering_menu/SKILL.md` (preview-then-confirm UX).
- ✅ Read `src/platform/schemas.py` lines 725 (`FlyerReferenceExtractionStatus`), 1468-1476 (`FlyerReferenceExtraction`), 1583 (FlyerProject.reference_extractions), 2600 (`_BaseEntry`), 2629 (`_UnknownLogEntry`), and the `LogEntry = Union[...]` discriminator. New variants append to the union; `_UnknownLogEntry` keeps fallback for forward-compatibility.
- ✅ Read `src/platform/safe_io.py` for `FileLock`, `atomic_write_text`, `atomic_write_json`, `ndjson_append` — reuse all.
- ✅ Read `src/agents/flyer/reference_extract.py:38-60` (`classify_reference_role`) — F0061 text triggers `source_edit_template` per the `replace ... this flyer/source` regex at lines 43-46.
- ✅ Read `src/agents/flyer/facts.py:25-46` (`_fact` helper) — returns `None` for empty values; tolerable because we filter `if fact:` at callers.
- ✅ Read `src/agents/flyer/facts.py:137-204` (`merge_locked_facts`) — only `item:N:name|price` IDs go through the item-collapse logic; new prefixes flow through generic merged-by-id path with `customer_text=0 < reference_vision=4` priority (correct ordering for typed-customer-overrides-vision).
- ✅ Read `src/agents/flyer/visual_qa.py:74-101` (`_text_value_present_in`, `_value_present_in`) — word-boundary aware, will be reused for `forbidden_substrings`.
- ✅ Read `src/agents/flyer/visual_qa.py:205-261` (`run_visual_qa`) — extension point for negative-assertion loop is after the existing locked-fact loop at line 247.
- ✅ Read `src/plugins/cf-router/hooks.py:464-527` (pre-scope save), `:528-657` (exact-edit branch), `:701-815` (`_try_flyer_reference_scope_choice_intercept`), `:817-942` (authorization intercept). Mapped insertion points for new intercept.
- ✅ Read `src/plugins/cf-router/actions.py:2214-2406` (`_write_reference_scope_state`, `_reference_scope_state_lock`, `save_flyer_reference_scope_pending`, `consume_flyer_reference_scope_choice`). Wiring confirmed.
- ✅ Read `src/plugins/cf-router/actions.py:864-910` (`is_exact_reference_edit_request`) — F0061 text matches because it has `replace` verb + `branding/phone/flyer` targets + `this/source/uploaded` cues.
- ✅ Read `src/agents/flyer/render.py:879-880` (`_context_has`), :885 (`_is_food_or_grocery_project`), :888-905 (`_design_direction`), :430 (body-rendering substring use). One helper change fixes all four call sites.
- ✅ Read `tests/test_cf_router_flyer_routing.py` mocking patterns (`monkeypatch.setattr(actions, "save_flyer_reference_scope_pending", ...)`, `consume_flyer_reference_scope_choice`, `trigger_create_flyer_project`, `send_flyer_text`, `lid_to_phone_via_identify_sender`, `find_flyer_customer_by_sender`). Mirror for new intercept tests.
- ✅ Read `tests/test_cf_router_flyer_routing.py:233-275` (`test_reference_scope_choice_transaction_holds_state_lock`) — exact pattern for the new `consume_flyer_source_vs_new_choice` lock test.

## Schema details — final shapes

### `FlyerSourceContractSection` (new)

Insert near `FlyerLockedFact` (~`schemas.py:1454`). Use `extra="forbid"` because this is a state schema, not raw LLM output.

```python
class FlyerSourceContractSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(default="", max_length=160)
    items: list[str] = Field(default_factory=list, max_length=50)
```

### `FlyerSourceContract` (new)

```python
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

### `FlyerReferenceExtraction` (modify in place at `schemas.py:1468`)

Add ONE optional field at the end:

```python
class FlyerReferenceExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=40)
    role: FlyerReferenceRole
    provider: str = Field(default="", max_length=120)
    status: FlyerReferenceExtractionStatus = "not_run"
    extracted_facts: list[FlyerLockedFact] = Field(default_factory=list, max_length=100)
    detail: str = Field(default="", max_length=500)
    extracted_at: Optional[datetime] = None
    source_contract: Optional[FlyerSourceContract] = None  # NEW
```

Backward-compatibility: older sidecar JSON lacking the field deserializes successfully because `Optional[...]` with default `None` is permissive.

### `FlyerSourceContractExtracted` (new audit variant)

Insert near `DispatcherRouted` (around `schemas.py:2800`). Fields kept tight to avoid PII leak in audit:

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
```

### `FlyerSourceVsNewChosen` (new audit variant)

```python
class FlyerSourceVsNewChosen(_BaseEntry):
    type: Literal["flyer_source_vs_new_chosen"] = "flyer_source_vs_new_chosen"
    chat_id: str = Field(default="", max_length=80)
    sender_phone: str = Field(default="", max_length=32)
    customer_id: str = Field(default="", max_length=40)
    original_intent: Literal["exact_source_edit", "generic_reference", "unknown"]
    choice: Literal["source", "new", "clarification_sent"]
    pending_age_sec: int = 0
```

### `LogEntry` union update

Append both new classes to the `LogEntry = Union[...]` definition; mirror existing order alphabetically within the Flyer cluster. `_UnknownLogEntry` requires no edit — its `type` Literal exclusion is the negative-space pattern; new positive types add themselves cleanly.

## Vision prompt — final shape

Insert in `reference_extract.py` near the existing `REFERENCE_EXTRACTION_PROMPT` (~line 21). New prompt is added (not replacing) so generic OCR mode remains:

```python
SOURCE_CONTRACT_PROMPT = """Read this uploaded source flyer for an SMB Flyer Studio
exact-edit request. Extract the visible structure and the customer's stated changes.

Return STRICT JSON only:
{
  "source_business_names": ["..."],
  "target_business_name": "...",
  "required_headings": ["..."],
  "required_text": ["..."],
  "sections": [{"heading": "...", "items": ["...", "..."]}],
  "requested_replacements": {"OLD": "NEW", ...},
  "forbidden_substrings": [],
  "preserve_layout": true|false,
  "preserve_unmentioned_text": true|false,
  "confidence": "high" | "medium" | "low",
  "notes": "..."
}

Rules:
- Do not invent items, prices, or business names.
- Preserve item names exactly (case + spelling).
- "preserve_unmentioned_text" = true when the customer text contains any of:
  "do not change anything else", "only change", "same layout", "preserve", "keep the rest".
- "preserve_layout" = true when the customer text references layout, design, or look preservation.
- "forbidden_substrings" stays empty here; it is populated downstream from replacements.
- "requested_replacements" maps explicit "replace X with Y" from the customer text only.
- Return only JSON. No markdown.
"""
```

Customer text (the raw_request) is appended at call time inside `extract_reference` — same pattern as the existing `REFERENCE_EXTRACTION_PROMPT` usage.

## Extraction call shape

`reference_extract.py::extract_reference` is modified to branch on role:

```python
def extract_reference(asset, *, raw_request, provider=None):
    role = classify_reference_role(raw_request, asset)
    provider = provider or NoopReferenceExtractionProvider()
    mime = (asset.mime_type or "").lower()
    if role == "unsupported" or not mime.startswith("image/"):
        return FlyerReferenceExtraction(...status="unsupported"...)

    if role == "source_edit_template":
        return _extract_source_contract(asset, raw_request=raw_request, provider=provider)

    if role not in {"menu_reference", "old_flyer_reference"}:
        return FlyerReferenceExtraction(...status="not_run", detail="..."...)

    # existing menu/old-flyer reference path unchanged
    ...
```

`_extract_source_contract` is new; it uses the same `provider.extract_text` interface but with `SOURCE_CONTRACT_PROMPT`. Vision-output JSON is parsed with `FlyerSourceContract.model_validate_json` wrapped in a permissive try/except — on JSON parse failure, status becomes `low_confidence`; on provider unavailable, status becomes `provider_unavailable`; on success, the contract is attached AND `extract_requested_replacements_from_text(raw_request)` is *merged* (customer-text replacements override vision-extracted dict on key collision because customer text is authoritative).

Deterministic helper:

```python
def extract_requested_replacements_from_text(raw_request: str) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for match in re.finditer(
        r"\breplace\s+(?P<old>.+?)\s+(?:with|to)\s+(?P<new>.+?)(?=\.|\n|\d+\.\s|$)",
        raw_request,
        flags=re.IGNORECASE,
    ):
        old = " ".join(match.group("old").strip(" .,:;").split())
        new = " ".join(match.group("new").strip(" .,:;").split())
        if old and new and len(old) <= 80 and len(new) <= 80:
            replacements[old] = new
    return replacements
```

Audit emission: after the extraction returns (success or provider_unavailable), the caller in `create-flyer-project` emits `FlyerSourceContractExtracted` via the existing `ndjson_append` chokepoint used for `log-decision-direct`. Mirrors how `parse-menu-photo` writes `MenuUpdateProposed` after extraction.

## Locked-fact generation — exact shape

`source_contract_locked_facts(contract, *, asset, message_id)` in `facts.py`. Fact IDs and required-flags:

| Fact ID | Source | Required when |
|---|---|---|
| `source_heading:N` | `reference_vision` | `preserve_layout` OR `preserve_unmentioned_text` |
| `source_section:N:heading` | `reference_vision` | `preserve_layout` OR `preserve_unmentioned_text` |
| `source_section:N:item:M` | `reference_vision` | `preserve_layout` OR `preserve_unmentioned_text` |
| `replacement:N:old` | `customer_text` | never (informational; negative check uses `forbidden_substrings`) |
| `replacement:N:new` | `customer_text` | always |

`source_asset_id` and `source_sha256` carried on every fact via `model_copy(update={...})` so provenance survives merging.

`merge_locked_facts` analysis: new prefixes (`source_heading:`, `source_section:`, `replacement:`) do not match the `item:N:name|price` regex at `facts.py:147`, so they route through the generic `merged[fact_id]` path. With priority ordering `customer_text=0 < reference_vision=4`, customer-typed `replacement:N:new` values correctly outrank any same-id source-vision derivation.

## Forbidden-substrings population heuristic

In `create-flyer-project` immediately after building `source_contract_locked_facts`, walk `contract.requested_replacements`:

```python
def _populate_forbidden_substrings(contract: FlyerSourceContract) -> None:
    section_items = {item.lower() for section in contract.sections for item in section.items}
    for old, _new in contract.requested_replacements.items():
        if not old or len(old) < 3:
            continue
        if old.lower() in section_items:
            # Menu-item swap (Rice -> Jeera Rice); old item may legitimately stay
            continue
        digits = re.sub(r"\D", "", old)
        if len(digits) >= 10:
            if digits not in contract.forbidden_substrings:
                contract.forbidden_substrings.append(digits)
            continue
        if re.search(r"\d", old) and any(t in old.lower() for t in (" st", " dr", " ave", " rd", " blvd", " ln", " way", " ct", " pkwy")):
            contract.forbidden_substrings.append(old)
            continue
        # Brand-name heuristic
        if any(word and word[0].isupper() for word in old.split()):
            contract.forbidden_substrings.append(old)
```

This is intentionally conservative — false negatives (missing a forbidden) are recoverable by operator override; false positives (auto-banning a menu word) would block legitimate flyers.

## Cf-router intercept wiring

### Step 1 — `actions.save_flyer_reference_scope_pending`

Extend signature (`actions.py:2246`):

```python
def save_flyer_reference_scope_pending(
    *,
    chat_id: str,
    sender_phone: str,
    customer: dict,
    raw_request: str,
    media_path: str,
    scope: dict,
    ttl_sec: int = 1800,
    status: str = "awaiting_choice",
    authorization_note: str = "",
    original_intent: str = "unknown",   # NEW
) -> None:
```

Add to the dict at lines 2273-2287:

```python
pending.append({
    ...
    "original_intent": original_intent,
    ...
})
```

### Step 2 — `hooks.py` caller

Before the existing `save_flyer_reference_scope_pending(...)` call at `hooks.py:508`, compute `original_intent` once. This is the LOAD-BEARING edit. Today `line 528` (`is_exact_reference_edit_request`) is unreachable when scope clarifies (we `return` at 527 first). We move that evaluation up by ~20 lines into the pending save.

```python
# In _try_flyer_primary_intercept, immediately before the scope_ok branch:
original_intent = (
    "exact_source_edit"
    if media_path and actions.is_exact_reference_edit_request(text, has_media=True)
    else "generic_reference"
)
# ... then pass original_intent=original_intent to save_flyer_reference_scope_pending(...)
```

### Step 3 — `consume_flyer_reference_scope_choice` (existing)

Already returns the full pending dict including unknown fields (it uses `item.get(...)` patterns). Verify the returned dict surfaces `original_intent` — add a unit test to pin the wire.

### Step 4 — `_try_flyer_reference_scope_choice_intercept` (existing, modify)

After `choice = str(pending.get("choice") or "")` (~hooks.py:710), early-return on the exact-edit branch:

```python
if choice == "use_reference" and pending.get("original_intent") == "exact_source_edit":
    # Save the pending row under "awaiting_source_vs_new_choice" status.
    actions.save_flyer_source_vs_new_pending(pending)   # NEW writer
    clarification = (
        "Flyer Studio\n"
        "------------\n"
        "I can do this two ways:\n\n"
        "Reply SOURCE to keep the same flyer design and make only your requested changes.\n"
        "Reply NEW to create a new flyer inspired by this one. It will not preserve the exact layout."
    )
    ack_ok, mid, err = actions.send_flyer_text(chat_id, clarification)
    # audit row: FlyerSourceVsNewChosen choice="clarification_sent"
    actions.audit_source_vs_new(
        chat_id=chat_id, sender_phone=phone, customer_id=...,
        original_intent="exact_source_edit", choice="clarification_sent",
        pending_age_sec=int(time.time() - pending.get("created_at", 0)),
    )
    actions.audit_intercepted(
        reason="flyer_reference_scope_blocked",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=f"source_vs_new_clarification_sent; ack_message_id={mid}; ack_error={err[:300]}",
    )
    return {"action": "skip", "reason": "cf-router flyer source-vs-new clarification sent"}
```

### Step 5 — new intercept `_try_flyer_source_vs_new_choice_intercept`

Insert in `hooks.py` immediately after `_try_flyer_reference_scope_choice_intercept`. Order in the main dispatcher: AFTER existing scope-choice intercept (line 161) but BEFORE the scope-authorization intercept (line 164). Logic:

```python
def _try_flyer_source_vs_new_choice_intercept(text, chat_id, event):
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if not phone:
        return None
    pending = actions.consume_flyer_source_vs_new_choice(text, chat_id=chat_id, sender_phone=phone)
    if not pending:
        return None
    customer = pending.get("customer") or {}
    business_name = str(customer.get("business_name") or "this business")
    choice = pending.get("choice")  # "source" or "new"

    if choice == "source":
        visible = " ".join(actions.flyer_visible_message_text(pending.get("raw_request") or "").split())
        raw_request = f"Edit uploaded flyer/source artwork. Customer requested: {visible}"
        ok, detail, project = actions.trigger_create_flyer_project(
            customer_phone=phone,
            raw_request=raw_request,
            message_id=message_id,
            reference_media_path=str(pending.get("media_path") or ""),
            manual_edit_required=True,
        )
        # Routes through existing exact-edit handler at hooks.py:566-657 because
        # manual_edit_required=True → project.status="manual_edit_required" → preflight
        # → manual review queue when OPENAI_API_KEY is PLACEHOLDER.
        ...
        return {"action": "skip", "reason": f"cf-router flyer source-edit chosen: project {project_id}"}

    if choice == "new":
        source = str(pending.get("source_organization") or "the source flyer")
        raw_request = (
            f"{pending.get('raw_request') or ''}\n\n"
            f"Customer chose path 2: use {source} only as a reference/inspiration. "
            f"Create a new original {business_name} flyer with a similar menu/content structure. "
            f"Do not copy {source} branding/layout exactly."
        ).strip()
        # Mirror existing _try_flyer_reference_scope_choice_intercept use_reference branch.
        ...
        return {"action": "skip", "reason": f"cf-router flyer new-from-source chosen: project {project_id}"}

    return None
```

### Step 6 — `consume_flyer_source_vs_new_choice` writer + consumer

Two new helpers in `actions.py` near `save_flyer_reference_scope_pending` / `consume_flyer_reference_scope_choice`:

- `save_flyer_source_vs_new_pending(pending)` — updates status to `awaiting_source_vs_new_choice` and re-saves into the same `reference_scope_pending.json` (reuse `_reference_scope_state_lock`, `_write_reference_scope_state`).
- `consume_flyer_source_vs_new_choice(text, *, chat_id, sender_phone)` — parses `text` via `_source_contract_followup_choice`, looks up pending row by (chat_id, sender_phone) AND `status == "awaiting_source_vs_new_choice"`, holds the lock across the full read-modify-write, removes the row, returns the row with `choice` attached.

Both functions follow the existing pattern at `actions.py:2317-2406`.

## QA changes — exact extension

`visual_qa.py::run_visual_qa` after the existing locked-fact loop at line 247, append:

```python
for ext in project.reference_extractions:
    if not ext.source_contract:
        continue
    for forbidden in ext.source_contract.forbidden_substrings:
        normalized_forbidden = _normalize_text_for_match(forbidden)
        if not normalized_forbidden:
            continue
        if _looks_like_phone(forbidden):
            if _phone_value_present_in(normalized, forbidden):
                blockers.append(f"replaced source text still visible: {forbidden}")
        else:
            if _text_value_present_in(normalized, normalized_forbidden):
                blockers.append(f"replaced source text still visible: {forbidden}")
```

Phone vs text split mirrors the existing `_value_present_in` policy at line 88-101. Reuses the same word-boundary helper so OCR variants (`Lakshmis Kitchen` vs `Lakshmi's Kitchen`) behave consistently.

## Text manifest honesty

Additive change at `render.py:write_text_manifest` (lines 650-712). Add field to the manifest dict at line 685:

```python
"is_rendered_proof": False,  # NEW — manifest is declarative not pixel-verified
"verification_method": "declared_render_facts",  # explicit, for future renames
```

No removal; `rendered_facts` keeps its name to preserve backward compatibility with already-on-disk sidecars.

## `_context_has` word-boundary fix

Replace `render.py:879-880` (one helper, three call sites fixed):

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

## Test surface

`tests/test_flyer_schemas.py` (new file or extend existing):
- `FlyerSourceContract` accepts/rejects per `extra="forbid"`.
- Section items without prices accepted.
- `requested_replacements` dict round-trips.
- `forbidden_substrings` round-trips.
- Both new audit variants serialize through `LogEntry.model_validate({"type": ..., ...})`.

`tests/test_flyer_reference_extract.py` (extend):
- `classify_reference_role` returns `source_edit_template` for the verbatim F0061 string.
- `extract_requested_replacements_from_text` extracts F0061's three replacements (brand, phone, menu item).
- `_extract_source_contract` with sidecar provider returns a non-empty contract.
- `_extract_source_contract` with provider_unavailable returns `status="provider_unavailable"` and NO `source_contract` attached.
- Audit row `FlyerSourceContractExtracted` is appended once.

`tests/test_flyer_create_project.py` (extend):
- Source-contract for F0061 yields locked facts including `source_section:0:heading="Monday Thali Specials"` and `replacement:0:new="Lakshmi's Kitchen"`.
- `forbidden_substrings` includes `"Triveni Express"` (brand) and digits-only old phone, but NOT `"Rice"` (menu item).
- Missing-provider source-edit queues manual review with `reason_code="source_edit_provider_unavailable"`.

`tests/test_cf_router_flyer_routing.py` (extend):
- `original_intent="exact_source_edit"` persisted on scope-pending save when F0061-style text + media.
- `original_intent="generic_reference"` persisted when text lacks edit verbs.
- `use as reference` reply on exact-edit pending → clarification sent, no `trigger_create_flyer_project`.
- `NEW` after clarification → `trigger_create_flyer_project` WITHOUT `manual_edit_required`.
- `SOURCE` after clarification → `trigger_create_flyer_project` WITH `manual_edit_required=True`, raw_request prefixed `Edit uploaded flyer/source artwork`.
- `consume_flyer_source_vs_new_choice` holds lock across read-modify-write (mirror existing test pattern at line 233-275).
- After SOURCE-chosen project is queued manual, follow-up `any update?` does NOT re-enter clarification, does NOT call `trigger_create_flyer_project`.
- Generic-reference customer still completes `use as reference` flow without the extra step.

`tests/test_flyer_visual_qa.py` (extend):
- OCR text lacking `Monday Thali Specials` fails when source contract requires it.
- OCR with `Jeera Rice` passes presence check.
- OCR with `Triveni Express` fails when `forbidden_substrings=["Triveni Express"]`.
- Old phone digits in OCR triggers blocker.
- `Rice` legitimately present (e.g., `Brown Rice`) does NOT fail because `Rice` was not added to `forbidden_substrings`.

`tests/test_flyer_renderer.py` (extend):
- `_context_has("clean space for address", {"spa"})` → False.
- `_context_has("modern spa retreat", {"spa"})` → True.
- `_context_has("Hispanic restaurant", {"spa"})` → False.
- `_context_has("transparent design", {"spa"})` → False.

`tests/test_flyer_source_edit_preflight.py` (extend):
- SOURCE branch + missing OPENAI_API_KEY → `flyer_source_edit_preflight` returns `(False, ..., "source_edit_provider_unavailable")`; project queued.
- SOURCE branch + valid OPENAI_API_KEY → preflight returns `(True, "ready", "")`; concept generation triggered.

## Fail modes / risks

| Risk | Mitigation |
|---|---|
| `_extract_source_contract` returns junk JSON when vision model degrades. | Pydantic `extra="forbid"` + `model_validate_json` raises → catch → status=`low_confidence`, queue manual review. |
| Customer wrote ambiguous text; vision says `preserve_layout=False` but customer meant True. | Deterministic text parser extracts replacements separately; OR'd into the contract. Customer can still reply with corrections, and the SOURCE/NEW clarification gives them an explicit out. |
| `forbidden_substrings` over-bans a legitimate word. | Conservative heuristic (length ≥ 3, uppercase word, not a section item, phone-shape, address-shape). False negative is recoverable; false positive blocks customer work. |
| `LogEntry` discriminator silently drops new variants in older deserializers. | New `Literal[...]` types appended; `_UnknownLogEntry` fallback handles forward-compat. Test pins round-trip. |
| Pending row TTL (1800s) expires before customer replies SOURCE/NEW. | Reuse existing TTL semantics; if expired, next inbound goes through fresh primary intercept (acceptable for now). |
| Multiple worktrees / concurrent sessions touch the same `reference_scope_pending.json`. | Existing `FileLock` + atomic-write already covers this; new consumer reuses the same lock surface. |
| Cf-router routing-order regression — the new intercept could swallow non-source-edit follow-ups. | New consumer matches ONLY pending rows with `status="awaiting_source_vs_new_choice"`. No bare-text match. |

## Out of scope (for cross-reference)

See plan §"Deferred Items". This design does NOT cover: `vision_client.py` chokepoint, text-manifest schema rename, Cockpit/dashboard, OpenAI provider productization.
