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

Match the existing Flyer audit-variant field convention (`sender_phone` + `customer_id`, NO `chat_id`; see schemas.py:4157-4166 and existing `FlyerProjectCreated`/`FlyerStatusChange` shapes).

```python
class FlyerSourceVsNewChosen(_BaseEntry):
    type: Literal["flyer_source_vs_new_chosen"] = "flyer_source_vs_new_chosen"
    sender_phone: str = Field(default="", max_length=32)
    customer_id: str = Field(default="", max_length=40)
    original_intent: Literal["exact_source_edit", "generic_reference", "unknown"]
    choice: Literal["source", "new", "clarification_sent", "clarification_resent", "expired"]
    pending_age_sec: int = 0
    customer_followup_instruction: str = Field(default="", max_length=500)
```

`choice` Literal values:

- `"clarification_sent"` — the SOURCE/NEW prompt was first issued.
- `"clarification_resent"` — customer sent a status check-in (`any update?`) while awaiting; prompt re-issued (idempotent).
- `"source"` — customer chose SOURCE; row consumed; project queued via `manual_edit_required=True`.
- `"new"` — customer chose NEW; row consumed; project created without `manual_edit_required`.
- `"expired"` — TTL prune found the row unconsumed; row removed; operator visibility.

`customer_followup_instruction` carries any text after the SOURCE/NEW token (see §"Compound-reply parser" below).

### `LogEntry` union update

The deployed pattern is `Annotated[Model, Tag("...")]` inside `Annotated[Union[...], Discriminator(_pick_log_entry_tag)]` (schemas.py:4037-4170), NOT bare `Union[Model, ...]`. Add both new variants in the Flyer Studio cluster (currently ends at line 4166 just before the `_UnknownLogEntry` sentinel):

```python
LogEntry = Annotated[
    Union[
        ...
        # Hermes Flyer Studio
        Annotated[FlyerProjectCreated, Tag("flyer_project_created")],
        ...
        Annotated[FlyerClosureCustomerNotified, Tag("flyer_closure_customer_notified")],
        # NEW — source-contract observability
        Annotated[FlyerSourceContractExtracted, Tag("flyer_source_contract_extracted")],
        Annotated[FlyerSourceVsNewChosen, Tag("flyer_source_vs_new_chosen")],
        # PR-D1 forward-compat shim — UNKNOWN tags route here
        Annotated[_UnknownLogEntry, Tag("_unknown_")],
    ],
    Discriminator(_pick_log_entry_tag),
]
```

`_build_known_log_entry_types()` at schemas.py:4174 introspects the union and auto-includes new tags — no manual edit needed. `_UnknownLogEntry` keeps the forward-compat fallback for older deserializers receiving new tags they don't yet know (`extra="allow"` swallows new fields without dropping the row).

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

Deterministic helper. The `new` value is post-trimmed to drop trailing role nouns (`branding`, `name`, `details`, `info`) so `replace Triveni Express with Lakshmi's Kitchen branding` resolves to `{"Triveni Express": "Lakshmi's Kitchen"}` not `{"Triveni Express": "Lakshmi's Kitchen branding"}`:

```python
_REPLACEMENT_TRAILING_ROLE_NOUNS = re.compile(
    r"\s+\b(?:branding|brand|name|info|information|details|address|phone)\b\s*$",
    flags=re.IGNORECASE,
)

def extract_requested_replacements_from_text(raw_request: str) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for match in re.finditer(
        r"\breplace\s+(?P<old>.+?)\s+(?:with|to)\s+(?P<new>.+?)(?=\.|\n|\d+\.\s|$)",
        raw_request,
        flags=re.IGNORECASE,
    ):
        old = " ".join(match.group("old").strip(" .,:;").split())
        new = " ".join(match.group("new").strip(" .,:;").split())
        new = _REPLACEMENT_TRAILING_ROLE_NOUNS.sub("", new).strip()
        if old and new and len(old) <= 80 and len(new) <= 80:
            replacements[old] = new
    return replacements
```

Audit emission: after the extraction returns (success or provider_unavailable), the caller in `create-flyer-project` emits `FlyerSourceContractExtracted` via direct `ndjson_append(LOG_PATH, ...)` call. This mirrors `parse-menu-photo:249` and `parse-menu-photo:341` exactly — `ndjson_append` IS the chokepoint; the legacy `log-decision-direct` SKILL is a separate wrapper that calls the same primitive. Spell out the direct call in the implementation note to avoid the misleading phrasing.

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

In `create-flyer-project` immediately after building `source_contract_locked_facts`, walk `contract.requested_replacements`. Three independent backstops protect against false positives (which would block legitimate flyers):

1. Skip if `old.lower()` appears in extracted section items (vision-extracted menu).
2. Skip if `new.lower().startswith(old.lower())` — covers `Rice → Jeera Rice` (new is a *variant* of old) even when vision missed the source-flyer item.
3. Skip if `old` is a single word AND not phone/address-shaped — single-word brand names are too risky to auto-forbid; require multi-word brands (`Triveni Express`, `Acme Restaurants`) for the brand branch.

```python
def _populate_forbidden_substrings(contract: FlyerSourceContract) -> None:
    section_items = {item.lower() for section in contract.sections for item in section.items}
    for old, new in contract.requested_replacements.items():
        if not old or len(old) < 3:
            continue
        # Backstop 1: vision-confirmed menu item
        if old.lower() in section_items:
            continue
        # Backstop 2: new is a variant/extension of old (covers Rice → Jeera Rice when
        # vision missed Rice from the section items)
        if new and new.lower().startswith(old.lower()):
            continue
        digits = re.sub(r"\D", "", old)
        if len(digits) >= 10:
            # Phone-shaped — forbid the digits-only run
            if digits not in contract.forbidden_substrings:
                contract.forbidden_substrings.append(digits)
            continue
        if re.search(r"\d", old) and any(t in old.lower() for t in (" st", " dr", " ave", " rd", " blvd", " ln", " way", " ct", " pkwy")):
            # US address-shaped
            contract.forbidden_substrings.append(old)
            continue
        # Backstop 3: single-word "brands" are too risky (could be "Rice", "Coffee")
        if len(old.split()) < 2:
            continue
        # Multi-word brand-name heuristic: at least one uppercase-leading word
        if any(word and word[0].isupper() for word in old.split()):
            contract.forbidden_substrings.append(old)
```

False-positive cost: blocks a legitimate flyer at QA → operator manual review. False-negative cost: rendered flyer shows old brand alongside new brand → operator/customer catches it visually. The asymmetry justifies the conservative posture (more backstops, fewer false positives).

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

**Computation scope:** `original_intent` is computed and persisted ONLY inside the `if decision in {"block", "clarify"}:` branch (currently hooks.py:499) — i.e., only when a pending row is actually being saved. When scope_ok is True we skip this entirely because the existing exact-edit branch at line 528 still runs.

```python
# Inside hooks.py _try_flyer_primary_intercept, INSIDE the if decision in {"block", "clarify"}: block:
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
    original_intent=original_intent,  # NEW kwarg
)
```

### Step 3 — `consume_flyer_reference_scope_choice` (existing)

Already returns the full pending dict including unknown fields (it uses `item.get(...)` patterns). Verify the returned dict surfaces `original_intent` — add a unit test to pin the wire.

### Step 4 — `_try_flyer_reference_scope_choice_intercept` (existing, modify)

**Race-safe state transition.** Today `consume_flyer_reference_scope_choice` *removes* the row inside the lock. If we then re-save under a new status outside the lock, there's a brief window where the file has no pending row for this sender. To eliminate that, extend the existing consumer to accept a `transition_to_status: Optional[str] = None` kwarg: when set, it rewrites the row's `status` field in-place under the same lock rather than removing it.

After `choice = str(pending.get("choice") or "")` (~hooks.py:710), early-return on the exact-edit branch. (Note: the consumer is called with `transition_to_status="awaiting_source_vs_new_choice"` for this branch — see Step 6 below):

```python
if choice == "use_reference" and pending.get("original_intent") == "exact_source_edit":
    # Pending row already transitioned to status="awaiting_source_vs_new_choice"
    # atomically by consume_flyer_reference_scope_choice(..., transition_to_status=...)
    clarification = (
        "Flyer Studio\n"
        "------------\n"
        "I can do this two ways:\n\n"
        "Reply SOURCE to keep this same flyer and apply only the changes you asked for.\n"
        "Reply NEW to create a brand-new flyer inspired by this one (different layout)."
    )
    ack_ok, mid, err = actions.send_flyer_text(chat_id, clarification)
    actions.audit_source_vs_new(
        sender_phone=phone,
        customer_id=str((pending.get("customer") or {}).get("customer_id") or ""),
        original_intent="exact_source_edit",
        choice="clarification_sent",
        pending_age_sec=int(time.time() - (pending.get("created_at") or time.time())),
    )
    actions.audit_intercepted(
        reason="flyer_reference_scope_blocked",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=f"source_vs_new_clarification_sent; ack_message_id={mid}; ack_error={err[:300]}",
    )
    return {"action": "skip", "reason": "cf-router flyer source-vs-new clarification sent"}
```

Customer copy notes:

- "preserve" replaced with "keep" (SMB-owner clear, per lessons.md preferences).
- Parallel verb structure ("keep this same flyer" / "create a brand-new flyer").
- No internal jargon (no "queue", "provider", "designer", "manual").
- Parenthetical `(different layout)` is the only minor hint at the tradeoff.

### Step 5 — new intercept `_try_flyer_source_vs_new_choice_intercept`

Insert in `hooks.py` immediately after `_try_flyer_reference_scope_choice_intercept`. Order in the main dispatcher: AFTER existing scope-choice intercept (line 161) but BEFORE the scope-authorization intercept (line 164). Branches:

1. **Compound-reply parsing:** `_source_contract_followup_choice(text)` returns `(choice, trailing)`. Trailing text is merged into the consumed pending row's `raw_request` as customer follow-up.
2. **Status check-in pre-claim:** if parser returns `("", "")` (no SOURCE/NEW token) AND a row for this sender exists with status `awaiting_source_vs_new_choice` AND `flyer_is_status_checkin(text)` matches, re-send the clarification verbatim and audit `clarification_resent`. Do NOT consume the row.
3. **SOURCE branch:** consume row, merge trailing instruction, call `trigger_create_flyer_project(..., manual_edit_required=True)`.
4. **NEW branch:** consume row, call `trigger_create_flyer_project(...)` mirroring the existing use_reference path.
5. **Idempotent retry:** if `consume_flyer_source_vs_new_choice` returns None (row already consumed) AND parser returned SOURCE or NEW AND the most recent flyer project for this customer was created within the last 60 seconds AND its `status="manual_edit_required"`, re-send the appropriate ack and return skip.

```python
def _try_flyer_source_vs_new_choice_intercept(text, chat_id, event):
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if not phone:
        return None

    choice_token, trailing = actions.parse_source_vs_new_followup(text)

    # Branch 2: status check-in re-send (lessons.md 2026-05-19)
    if not choice_token:
        existing = actions.peek_flyer_source_vs_new_pending(chat_id=chat_id, sender_phone=phone)
        if existing and actions.flyer_is_status_checkin(text):
            # Re-send clarification verbatim; do NOT consume; audit clarification_resent.
            ...
            return {"action": "skip", "reason": "cf-router flyer source-vs-new status check-in"}
        return None

    pending = actions.consume_flyer_source_vs_new_choice(
        choice_token, trailing, chat_id=chat_id, sender_phone=phone,
    )
    if not pending:
        # Branch 5: idempotent retry — same customer just chose; bonus check.
        recent = actions.find_recent_flyer_manual_edit_project(phone, window_sec=60)
        if recent and choice_token == "source":
            ack_ok, mid, err = actions.send_flyer_manual_edit_ack(
                chat_id, recent["project_id"], pending and pending.get("raw_request") or "",
                reason="source_edit_provider_unavailable",
            )
            return {"action": "skip", "reason": "cf-router flyer source-vs-new retry idempotent"}
        return None

    customer = pending.get("customer") or {}
    business_name = str(customer.get("business_name") or "this business")
    raw_request = pending.get("raw_request") or ""
    trailing = pending.get("customer_followup_instruction") or ""

    if pending.get("choice") == "source":
        visible = " ".join(actions.flyer_visible_message_text(raw_request).split())
        if trailing:
            visible = f"{visible}. Also: {trailing}"
        new_raw_request = f"Edit uploaded flyer/source artwork. Customer requested: {visible}"
        ok, detail, project = actions.trigger_create_flyer_project(
            customer_phone=phone,
            raw_request=new_raw_request,
            message_id=message_id,
            reference_media_path=str(pending.get("media_path") or ""),
            manual_edit_required=True,
        )
        # Routes through existing exact-edit handler at hooks.py:566-657 → preflight →
        # manual-review queue when OPENAI_API_KEY is PLACEHOLDER. Audit choice="source".
        ...
        return {"action": "skip", "reason": f"cf-router flyer source-edit chosen: project {project_id}"}

    if pending.get("choice") == "new":
        source = str(pending.get("source_organization") or "the source flyer")
        new_raw_request = (
            f"{raw_request}\n\n"
            f"Customer chose path 2: use {source} only as a reference/inspiration. "
            f"Create a new original {business_name} flyer with a similar menu/content structure. "
            f"Do not copy {source} branding/layout exactly."
            + (f"\n\nAdditional customer instruction: {trailing}" if trailing else "")
        ).strip()
        # Mirror existing use_reference branch from _try_flyer_reference_scope_choice_intercept.
        # Audit choice="new".
        ...
        return {"action": "skip", "reason": f"cf-router flyer new-from-source chosen: project {project_id}"}

    return None
```

### Step 5.1 — Compound-reply parser

```python
_SOURCE_TOKEN = re.compile(
    r"^\s*(?P<token>source|keep\s+source|same\s+flyer|exact\s+edit|option\s*1|1)\b[\s.,:;!\-—]*(?P<trailing>.*)$",
    flags=re.IGNORECASE | re.DOTALL,
)
_NEW_TOKEN = re.compile(
    r"^\s*(?P<token>new|new\s+flyer|inspired(?:\s+by)?|option\s*2|2)\b[\s.,:;!\-—]*(?P<trailing>.*)$",
    flags=re.IGNORECASE | re.DOTALL,
)

def parse_source_vs_new_followup(text: str) -> tuple[str, str]:
    """Return ("source"|"new"|"", trailing_text). Normalizes sender block first."""
    body = " ".join(flyer_visible_message_text(text).split())
    for choice, pattern in (("source", _SOURCE_TOKEN), ("new", _NEW_TOKEN)):
        match = pattern.match(body)
        if match:
            trailing = " ".join(match.group("trailing").strip(" .,:;-—").split())
            return choice, trailing[:500]
    return "", ""
```

Behavior:

- "SOURCE" → `("source", "")`.
- "Source." → `("source", "")`.
- "SOURCE, also change date to Saturday" → `("source", "also change date to Saturday")`.
- "1" → `("source", "")`.
- "Option 2 — please use cursive font" → `("new", "please use cursive font")`.
- "any update?" → `("", "")` (no match; falls to status check-in branch).
- "[shift-agent-sender ...]\nSource" → `("source", "")` (sender block normalized first via `flyer_visible_message_text`).

### Step 5.2 — Status check-in helper

```python
_STATUS_CHECKIN = re.compile(
    r"^(?:any\s+update|is\s+it\s+ready|what'?s?\s+(?:the\s+)?status|update\??|status\??|ready\??)\??$",
    flags=re.IGNORECASE,
)

def flyer_is_status_checkin(text: str) -> bool:
    body = " ".join(flyer_visible_message_text(text).split()).strip(" .!,:;-—")
    return bool(_STATUS_CHECKIN.match(body))
```

Already-deployed nearby pattern: `is_flyer_approval_text` at `actions.py:830-833`. Mirror style + tests.

### Step 6 — state-transition + consumer + peek + expiry-prune

Five new functions in `actions.py` near `save_flyer_reference_scope_pending` / `consume_flyer_reference_scope_choice`:

- **Modify `consume_flyer_reference_scope_choice`** to accept `transition_to_status: Optional[str] = None` kwarg. When set AND the matched row would otherwise be removed AND `choice == "use_reference"` AND `original_intent == "exact_source_edit"`, instead atomically rewrite that row's `status` and return it. This keeps the row present (no race window) while signaling the caller it can proceed with the next step.

- `consume_flyer_source_vs_new_choice(choice_token: str, trailing: str, *, chat_id: str, sender_phone: str) -> Optional[dict]` — finds the row with `status="awaiting_source_vs_new_choice"` matching `(chat_id, sender_phone)`, removes it from the state file under `_reference_scope_state_lock()`, attaches `choice` and `customer_followup_instruction=trailing` to the returned dict.

- `peek_flyer_source_vs_new_pending(*, chat_id: str, sender_phone: str) -> Optional[dict]` — read-only lookup (does not consume) used by the status check-in branch. Acquires the lock briefly to read; does not write.

- `parse_source_vs_new_followup(text: str) -> tuple[str, str]` — pure helper (no state I/O). Spelled out in Step 5.1.

- `flyer_is_status_checkin(text: str) -> bool` — pure helper. Spelled out in Step 5.2.

- `find_recent_flyer_manual_edit_project(customer_phone: str, *, window_sec: int = 60) -> Optional[dict]` — reads `projects.json`, returns the most recent project for `customer_phone` whose `status == "manual_edit_required"` AND was created within the last `window_sec` seconds. Used by the idempotent-retry branch.

**Prune-on-expiry audit:**

- Extend `_read_reference_scope_state` (`actions.py` ~line 2200) so when it drops expired rows (existing TTL behavior), for any dropped row with `original_intent="exact_source_edit"` it appends a `FlyerSourceVsNewChosen` audit entry with `choice="expired"`. This gives operators visibility into customer-abandonment without depending on a new daemon. Reuses the existing `ndjson_append` chokepoint.

All functions follow the existing pattern at `actions.py:2317-2406` (lock surface, atomic write, error-tolerant for absent files).

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
- `original_intent` ONLY computed when `decision in {"block", "clarify"}` (not on scope-ok path).
- `use as reference` reply on exact-edit pending → clarification sent (no `trigger_create_flyer_project`), pending row stays under `awaiting_source_vs_new_choice` status (atomic transition, not consume+resave).
- `NEW` after clarification → `trigger_create_flyer_project` WITHOUT `manual_edit_required`.
- `SOURCE` after clarification → `trigger_create_flyer_project` WITH `manual_edit_required=True`, raw_request prefixed `Edit uploaded flyer/source artwork`.
- Compound reply `SOURCE - also change date to Saturday` → `customer_followup_instruction="also change date to Saturday"` carried into project's raw_request.
- Compound reply `Option 2, please use cursive font` → NEW branch + trailing carried into NEW path's raw_request.
- `consume_flyer_source_vs_new_choice` holds lock across read-modify-write (mirror existing test pattern at line 233-275).
- After SOURCE-chosen project is queued manual, follow-up `any update?` re-sends the SAME clarification verbatim, audits `clarification_resent`, does NOT call `trigger_create_flyer_project`.
- After SOURCE chosen, `_consume_flyer_reference_authorization_reply_locked` does NOT claim `awaiting_source_vs_new_choice` rows (status isolation).
- Customer replies SOURCE twice (idempotent retry): second reply finds no pending row but recent (≤60s) manual-edit project; re-sends manual-edit ack without re-creating project.
- TTL expiry on `exact_source_edit` pending row emits `FlyerSourceVsNewChosen` audit with `choice="expired"`.
- Media path missing on disk at SOURCE-branch time → customer-safe "please resend" reply, pending row removed.
- Generic-reference customer still completes `use as reference` flow without the extra step.
- Sender-block-prefixed inbound (`[shift-agent-sender ...]\nSource`) parses as `("source", "")` after `flyer_visible_message_text` normalization.

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
| Customer wrote ambiguous text; vision says `preserve_layout=False` but customer meant True. | Deterministic text parser extracts replacements separately; merged into the contract. SOURCE/NEW clarification gives the customer an explicit override. |
| `forbidden_substrings` over-bans a legitimate word. | Three independent backstops: skip if vision-confirmed section item; skip if new is a variant/extension of old (`Jeera Rice` startswith `Rice`); skip single-word brands. False positive must clear all three. |
| `LogEntry` discriminator silently drops new variants in older deserializers. | New `Annotated[..., Tag(...)]` entries added; `_UnknownLogEntry` (`extra="allow"`) handles forward-compat. Test pins round-trip both ways. |
| Pending row TTL (1800s) expires before customer replies SOURCE/NEW. | Existing TTL prunes on next read. New behavior: dropped row with `original_intent="exact_source_edit"` emits `FlyerSourceVsNewChosen` audit row with `choice="expired"` so operators see abandons. |
| Customer media path GC'd by Hermes between scope-clarify and SOURCE/NEW reply. | `media_path` on disk: `is_file()` check before SOURCE-branch `trigger_create_flyer_project`. If missing, fall back to a customer-safe "could not find original flyer, please resend" reply + remove pending row. |
| Multiple worktrees / concurrent sessions touch the same `reference_scope_pending.json`. | Existing `FileLock` + atomic-write already covers this; new functions reuse the same lock surface. State transitions use the consumer's atomic rewrite path, not consume-then-resave. |
| Cf-router routing-order regression — new intercept could swallow non-source-edit follow-ups. | (a) New consumer matches ONLY pending rows with `status="awaiting_source_vs_new_choice"`. (b) Existing scope-choice consumer requires `status="awaiting_choice"`; existing auth consumer requires `status="awaiting_authorization_details"`. No cross-status claim. (c) Tests pin each consumer's status filter. |
| Two-sender business (lessons.md 2026-05-15): sender A initiates scope-pending, sender B replies SOURCE. | Consumer matches on `(chat_id, sender_phone)`. Sender B's reply does not match → falls through to LLM. Mirrors existing scope-choice behavior; documented but not fixed in this PR (cross-sender resumption is a follow-up). |
| Customer replies "SOURCE" twice (network retry, accidental double-send). | Idempotent-retry branch: if consume returns None AND parser matched SOURCE/NEW AND a recent manual-edit project (≤60s) exists for this customer, re-send the same ack. |
| Compound reply "SOURCE, also change date to Saturday" loses the trailing instruction. | Parser returns `(token, trailing)`; trailing merged into the consumed row's `raw_request` as customer follow-up before `trigger_create_flyer_project`. |
| Status check-in "any update?" arrives while pending is `awaiting_source_vs_new_choice`. | Pre-claim branch: re-send the same clarification verbatim (no consume), audit `clarification_resent`. Matches lessons.md 2026-05-19 entry. |

## Out of scope (for cross-reference)

See plan §"Deferred Items". This design does NOT cover: `vision_client.py` chokepoint, text-manifest schema rename, Cockpit/dashboard, OpenAI provider productization.
