# Design v1 — Catering Comprehensive Fix

> ⚠️ **SUPERSEDED** — see `tasks/catering-comprehensive-fix-design-v2.md`
> for the final design (post 5-design-review synthesis). v2 corrects the
> CATERING_TRANSITIONS status names, fixes the rstrip bug, moves
> module-level asserts, and addresses 34 reviewer concerns. This v1 is
> kept for the pipeline trail.

**Drift-check tag:** extends-Hermes (no Hermes-internal change)
**Branch:** `fix/catering-comprehensive`
**Source:** Plan at `tasks/catering-comprehensive-fix-plan.md` + 5-parallel plan-review synthesis (this doc) + pre-deploy live audit (`tasks/catering-e2e-report.md`)

This design is a **revision of the plan** that resolves all 28 plan-review concerns. Pipeline: this doc → 5 design reviews → build (~6 commits) → PR → 5 code reviews → merge → deploy.

---

## 1. New findings discovered during plan review (added to scope)

### Q1 (CRITICAL) — `apply-catering-owner-decision` never persists `quote_text`

**Discovered by:** pre-deploy audit during plan-review phase
**Evidence:** all 9 production leads (5 AWAITING + 4 SENT_TO_CUSTOMER) have `quote_text == ""`
**Fix:** in `apply-catering-owner-decision`, after rendering, set `lead.quote_text = rendered_text` BEFORE persisting. This is mandatory for S1 invariant viability AND for replay/audit.

### L0 (CRITICAL) — Phone canonicalization regression on READ

**Discovered by:** deploy-risk plan reviewer
**Evidence:** `CateringLeadStore.model_validate` reads existing leads on every script start. If any lead's `customer_phone` was stored as `+9045551234` (10-digit-with-plus, the buggy form), the new strict `from_any` will reject it on READ → all scripts crash.
**Fix:** add explicit pre-deploy migration path; verify via SSH audit; design `from_any` to accept STORED form (already-canonical including buggy 10-digit), but reject NEW input with helpful error. See §3.6.

---

## 2. Resolved plan-review issues

| Plan-review finding | Resolution in design |
|---|---|
| S1 will crash on AWAITING leads with empty quote_text | (A) Q1 fix backfills quote_text in apply-script. (B) S1 validator uses `mode="before"` shim that **fills** with sentinel `"<missing-quote-pre-v0.3>"` for legacy data + emits one-time `InvariantViolation`. (C) Pre-deploy migration script `tools/catering-state-migrate.py` populates real quote_text from re-rendering for current 5 AWAITING leads using existing extracted fields. |
| S3 ts-naive on _BaseEntry | Use `field_validator(mode="before")` that auto-converts naive→UTC with one-time WARN. **Audit log is append-only on Hermes path; rarely read back through schema** — but cockpit may read in future, so the shim covers both. |
| `schema_version` rollback hostile | Add `model_config = ConfigDict(extra="ignore")` to `CateringLeadStore` (currently `forbid`). Old code dropping the field on rollback is OK; we lose the version marker but no crash. |
| New audit classes not in `LogEntry` discriminated union | All 4 new classes (`CateringQuoteAttempted`, `CateringOwnerEdited`, `CateringOwnerApprovalCardFailed`, `CateringOwnerApprovalCardSkipped`, `CateringOwnerApprovalCardAttempted`) added to `LogEntry` union AND `__all__` export in same Commit 1 atomically. |
| Status-machine SoT split | Put `CATERING_TRANSITIONS` table EXCLUSIVELY in `src/platform/schemas.py`. Helper functions `is_catering_transition_allowed(from_s, to_s) -> bool` live alongside. No separate `catering_status_machine.py` module. |
| A3 refuses approve when MENU absent breaks new-customer onboarding | Three-way return: `_load_menu_filtered() -> tuple[items, total, error_kind]` where `error_kind ∈ {"ok","absent","corrupt","io_error"}`. Approve proceeds on `ok` AND `absent` (empty menu section is valid for first-run customers). Refuses only on `corrupt`/`io_error`. |
| C2 phone-mismatch hard-rejects legitimate retries | **Softer**: compare via `E164Phone.from_any(args.customer_phone) == existing.customer_phone`. If from_any raises (PM2 fix), reject input. Mismatch → emit `InvariantViolation(check="catering_idempotency_phone_mismatch")` audit + WARN, but RETURN existing lead's id+code (idempotent semantics preserved). User-facing impact: same as today — replay returns existing lead. |
| C3 bridge-retry without idempotency | Mirror A1+A2 pattern: emit `CateringOwnerApprovalCardAttempted` audit row in the SAME lock as state-write, BEFORE bridge POST. On retry, check for existing `CateringOwnerApprovalCardAttempted{lead_id, original_message_id}` audit row → skip POST, return idempotent. |
| PM2 hard-rejects 10-digit US phones | Default-prepend `+1` for bare 10-digit input WHEN `cfg.customer.country_code == "US"` (add field to Config). Emit `InvariantViolation(check="phone_canonicalization_assumed_us")` once per phone. Reject ONLY if input is bare 10-digit AND `cfg.customer.country_code` is not "US". |
| PM1 TTL clock-skew silent first-photo rejection | Add `now < proposed_at + 1min_grace` sanity check. If `now < proposed_at`, refuse to compute TTL → emit `InvariantViolation(check="clock_skew", detail={...})` and abort the parse-menu-photo run. |
| A1 attempted-row order ambiguous | Strict order documented + tested: `with FileLock: write_attempted_audit(); write_state(SENT_TO_CUSTOMER); release_lock; bridge_POST; if fail: log_failure_audit`. State is committed BEFORE POST; attempted-audit is committed BEFORE state. On retry, attempted-audit gates POST. |
| A4 "loudly" undefined | Define: `InvariantViolation` audit + `_send_pushover(reason="catering_template_format_error")` + `EXIT_DEPENDENCY_DOWN`. NO inline fallback. |
| A8 customer-facing wrong on unknown tags | When `unknown_tags` non-empty AND filtered result empty: customer message says `"We didn't recognize the dietary preference '<tag>'; please clarify if you meant veg, vegan, jain, etc."` Set `_format_menu_section` to handle this case explicitly. |
| C5 JID-empty exits 0 silently | Change to `EXIT_DEPENDENCY_DOWN` (rc=6) with structured stdout `{"card_sent": false, "reason": "self_chat_jid_empty"}`. Audit `CateringOwnerApprovalCardSkipped(reason="self_chat_jid_empty")`. SKILL caller sees rc≠0 and routes to Pushover. |
| M7-CL `extra="allow"` introduces silent drift | **Keep `extra="ignore"`** but add a contract-drift test in `tests/test_catering_schemas.py` that compares the SKILL's expected fields (parsed from SKILL.md frontmatter or comments) against the schema. Loud failure if SKILL adds new fields without schema update. |
| S6 regex unification breaks back-compat for L-codes | Pre-deploy scan: `tools/catering-state-migrate.py` checks `catering-menu-pending.json` for L-bearing `confirmation_code`. If found: emit warning, owner must re-photograph menu. If absent (verified empty on VPS): proceed safely. |
| L4 contradiction (deferred vs. included) | **Included** in this PR. Trivial fix in Commit 2. Plan section 1 corrected. |
| L2 `BRIDGE_URL` not configurable | Promoted to MEDIUM. Add `BRIDGE_URL = os.getenv("SHIFT_BRIDGE_URL", "http://127.0.0.1:3000/send")` in each script in Commit 2. |
| S16 NEEDS-VERIFY (3000-char notes) | **Verified resolved**: `CateringLeadExtractedFields.notes` has no max_length cap (intentional for LLM extraction). Schema-side fine; renderer has its own truncation. No fix needed. Documented in design. |
| `_b1_helpers.run_create` missing `customer_tz` | Helper API extension in Commit 6: add `customer_tz: Optional[str] = None` and `path_overrides: dict[str, Path]` knobs. |
| Test count under-estimated (60-75 not 30) | Final test target: 19 (B1) + 30 (resurrected v02 — strict count after dedup with B1) + 33 (lookup) + 60 new (per-fix) + 30 baseline schemas = ~172 collected. After dedup (~5 duplicates removed): ~165. |
| Pre-merge VPS validation undocumented | New script: `tools/run-catering-staging-tests.sh` — scp tests/, set PYTHONPATH, pytest, exit non-zero on failure. Pin in PR description. |
| Smoke test has zero catering coverage | Extend `shift-agent-smoke-test.sh`: `python3 -c "from schemas import CateringLeadStore; import json; CateringLeadStore.model_validate(json.load(open('/opt/shift-agent/state/catering-leads.json')))"`. Catches S1, S6, L0 at smoke-time → triggers auto-rollback. |
| Pre-deploy audit gate missing | Hard SSH gate: see §6.2. |
| `low-traffic window` undefined | Document: deploy after 9pm CT weekdays; ~7-10s service unavailability window. |
| C20 prompt-injection test flakiness with M7-CL | M7-CL kept as `extra="ignore"` (per fix above) — no flakiness. |
| L2-S off_menu_items test misattribution | Move attribution to `test_catering_v02_scripts.py:657` (where it actually lives). Add NEW write-only-contract test in resurrected v02 file. |
| Commit-1 not independently testable | Commit 1 schema validators use `mode="before"` shims (lenient on read, strict on write). Tests can pass independently. Commit 6 introduces `mode="after"` strict re-enable AFTER all scripts are aligned. |

---

## 3. File-by-file design

### 3.1 `src/platform/schemas.py` — Commit 1 (~350 LOC)

**New module-level constants:**

```python
# Single source of truth for code-generation alphabet.
# Excludes I, O, 0, 1, L (visually confusing chars).
_CODE_BODY_PATTERN = r"[A-HJKMNPQR-Z2-9]{5}"
_CODE_FULL_PATTERN = rf"^#{_CODE_BODY_PATTERN}$"

# Catering status-machine transition table (SoT).
# Forbidden: any transition not listed here.
CATERING_TRANSITIONS: dict[CateringLeadStatus, set[CateringLeadStatus]] = {
    "NEW": {"AWAITING_OWNER_APPROVAL", "REJECTED"},
    "AWAITING_OWNER_APPROVAL": {"OWNER_APPROVED", "OWNER_EDITED", "OWNER_REJECTED", "EXPIRED"},
    "OWNER_EDITED": {"AWAITING_OWNER_APPROVAL", "OWNER_REJECTED"},
    "OWNER_APPROVED": {"SENT_TO_CUSTOMER", "FAILED"},  # FAILED is new
    "SENT_TO_CUSTOMER": {"CUSTOMER_REPLIED", "CLOSED"},
    "CUSTOMER_REPLIED": {"CLOSED"},
    "OWNER_REJECTED": {"CLOSED"},
    "EXPIRED": {"CLOSED"},
    "FAILED": {"CLOSED", "AWAITING_OWNER_APPROVAL"},  # retry from FAILED
    "REJECTED": {"CLOSED"},
    "CLOSED": set(),  # terminal
}

def is_catering_transition_allowed(from_s: CateringLeadStatus, to_s: CateringLeadStatus) -> bool:
    return to_s in CATERING_TRANSITIONS.get(from_s, set())

# REASON_TO_ERR_PREFIX runtime check helper — for create-catering-lead startup.
def assert_rejection_reason_complete(reason_dict: dict) -> None:
    """At create-catering-lead startup, assert the runtime dict matches the schema Literal."""
    from typing import get_args
    schema_reasons = set(get_args(CateringLeadRejected.model_fields["reason"].annotation))
    runtime_reasons = set(reason_dict.keys())
    if not runtime_reasons.issubset(schema_reasons):
        missing = runtime_reasons - schema_reasons
        raise AssertionError(f"REASON_TO_ERR_PREFIX has reasons not in schema Literal: {missing}")
```

**`CateringLead` validator additions:**

```python
class CateringLead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # ... existing fields unchanged ...

    @model_validator(mode="before")
    @classmethod
    def _backfill_legacy_quote_text(cls, data: Any) -> Any:
        """v0.3 introduces non-empty quote_text invariant for post-AWAITING statuses.
        Legacy leads had empty quote_text. Backfill on READ with sentinel + WARN
        (allows existing leads to survive re-validation; new writes hit the strict
        validator below)."""
        if not isinstance(data, dict):
            return data
        status = data.get("status")
        post_awaiting = {"AWAITING_OWNER_APPROVAL", "OWNER_APPROVED", "OWNER_EDITED",
                         "SENT_TO_CUSTOMER"}
        if status in post_awaiting and not (data.get("quote_text", "") or "").strip():
            # Backfill — emit one-time stderr WARN for ops awareness.
            import sys as _sys
            _sys.stderr.write(
                f"WARN: legacy quote_text=empty on lead_id={data.get('lead_id')!r} "
                f"status={status!r}; backfilling with sentinel.\n"
            )
            data["quote_text"] = "<legacy-pre-v0.3-no-quote-persisted>"
        return data

    @model_validator(mode="after")
    def _quote_required_post_awaiting(self) -> "CateringLead":
        """v0.3 strict: post-AWAITING statuses must have quote_text. Legacy data
        already backfilled by mode='before' validator above."""
        post_awaiting = {"AWAITING_OWNER_APPROVAL", "OWNER_APPROVED", "OWNER_EDITED",
                         "SENT_TO_CUSTOMER"}
        if self.status in post_awaiting and not self.quote_text.strip():
            raise ValueError(
                f"status={self.status!r} requires non-empty quote_text"
            )
        return self
```

**`CateringLeadExtractedFields` event_date validator:**

```python
@field_validator("event_date")
@classmethod
def _validate_calendar_date(cls, v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    try:
        datetime.fromisoformat(v).date()  # Rejects 2026-13-99 etc.
    except ValueError as e:
        raise ValueError(f"event_date must be valid ISO date: {e}") from e
    return v
```

**`CateringLeadStore` additions:**

```python
class CateringLeadStore(BaseModel):
    model_config = ConfigDict(extra="ignore")  # CHANGED from forbid for rollback safety
    schema_version: int = Field(default=1, ge=1)
    leads: list[CateringLead] = Field(default_factory=list)
```

**`MenuPendingUpdate.confirmation_code` regex change:**

```python
# Before: ^#[A-HJ-NP-Z2-9]{5}$  (accepts L)
# After:  uses _CODE_FULL_PATTERN (rejects L)
confirmation_code: str = Field(pattern=_CODE_FULL_PATTERN)
```

**`Menu.updated_by` validator:**

```python
@field_validator("updated_by")
@classmethod
def _validate_updated_by(cls, v: str) -> str:
    if v == "" or v in ("photo-ocr", "manual"):
        return v
    # Otherwise expect E.164 phone
    if not re.match(r"^\+\d{10,15}$", v):
        raise ValueError(f"updated_by must be 'photo-ocr', 'manual', or E.164 phone: {v!r}")
    return v
```

**`_BaseEntry.ts` validator:**

```python
class _BaseEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ts: datetime
    type: str

    @field_validator("ts", mode="before")
    @classmethod
    def _ensure_tz_aware(cls, v: Any) -> Any:
        """v0.3 requires tz-aware timestamps. Naive datetimes auto-converted to UTC
        with WARN — preserves backward compat for any historic naive entries while
        new writes are tz-aware."""
        if isinstance(v, datetime):
            if v.tzinfo is None:
                import sys as _sys
                _sys.stderr.write(f"WARN: naive ts {v.isoformat()!r} auto-converted to UTC\n")
                return v.replace(tzinfo=timezone.utc)
        elif isinstance(v, str):
            try:
                parsed = datetime.fromisoformat(v)
                if parsed.tzinfo is None:
                    import sys as _sys
                    _sys.stderr.write(f"WARN: naive ts string {v!r} auto-converted to UTC\n")
                    return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                pass  # Pydantic will raise its own clear error
        return v
```

**`CateringOwnerDecision` validator:**

```python
@model_validator(mode="after")
def _edit_text_required_for_edit(self) -> "CateringOwnerDecision":
    if self.decision == "edit" and not self.edit_text.strip():
        raise ValueError("decision='edit' requires non-empty edit_text")
    return self
```

**5 New audit classes** (added to `LogEntry` union + `__all__`):

```python
class CateringQuoteAttempted(_BaseEntry):
    """Idempotency anchor: written BEFORE bridge POST inside same lock as state mutation.
    Detects on retry → skip duplicate send."""
    type: Literal["catering_quote_attempted"] = "catering_quote_attempted"
    lead_id: str = Field(min_length=1)
    original_message_id: str = Field(min_length=1)
    code: str = Field(pattern=_CODE_FULL_PATTERN)

class CateringOwnerApprovalCardAttempted(_BaseEntry):
    """Idempotency anchor for owner-approval card send (mirror of CateringQuoteAttempted)."""
    type: Literal["catering_owner_approval_card_attempted"] = "catering_owner_approval_card_attempted"
    lead_id: str = Field(min_length=1)
    original_message_id: str = Field(min_length=1)

class CateringOwnerApprovalCardFailed(_BaseEntry):
    type: Literal["catering_owner_approval_card_failed"] = "catering_owner_approval_card_failed"
    lead_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    bridge_error: str = Field(default="", max_length=2000)

class CateringOwnerApprovalCardSkipped(_BaseEntry):
    type: Literal["catering_owner_approval_card_skipped"] = "catering_owner_approval_card_skipped"
    lead_id: str = Field(min_length=1)
    reason: Literal["self_chat_jid_empty", "config_disabled"]

class CateringOwnerEdited(_BaseEntry):
    type: Literal["catering_owner_edited"] = "catering_owner_edited"
    lead_id: str = Field(min_length=1)
    edit_text: str = Field(min_length=1, max_length=2000)
```

**`CateringLeadRejected.reason` — extend Literal** with `"message_id_phone_mismatch"`. Atomic update of `REASON_TO_ERR_PREFIX` in Commit 2.

**`MenuUpdateProposed.extraction_dropped_count` — new field:**

```python
extraction_dropped_count: int = Field(default=0, ge=0)
```

**LogEntry discriminated union — extend with all new classes.**
**`__all__` export — extend.**

### 3.2 `src/agents/catering/scripts/create-catering-lead` — Commit 2 (~280 LOC)

**Bug fixes mapped to plan IDs:**

| ID | Diff |
|---|---|
| C1 | `try: lead = CateringLead(...) except ValidationError as e: stderr.write(...); return EXIT_INVALID_INPUT` |
| C2 | In dedup branch, compute `incoming_canon = E164Phone.from_any(args.customer_phone)`. If `existing.customer_phone != incoming_canon`: emit `CateringLeadRejected(reason="message_id_phone_mismatch")` audit + `InvariantViolation(check="catering_idempotency_phone_mismatch")`, but RETURN existing lead's id+code (idempotent — don't crash UX). |
| C3 | Refactor `_bridge_post` to support retry. **Mirror A1+A2 pattern:** emit `CateringOwnerApprovalCardAttempted` audit row INSIDE state-lock BEFORE bridge POST. On retry, check for existing attempted-audit; if found → return idempotent without POSTing. |
| C4 | On bridge timeout/failure: emit `CateringOwnerApprovalCardFailed(reason, bridge_error)` audit. |
| C5 | JID-empty path: change `return EXIT_OK` → `return EXIT_DEPENDENCY_DOWN`. Emit `CateringOwnerApprovalCardSkipped(reason="self_chat_jid_empty")` audit. |
| C6 | Off-menu loop: `running += len(item) + (2 if i > 0 else 0)` |
| C7 | Add module-level `assert WHATSAPP_OFF_MENU_BUDGET >= 50` |
| M1-CL | Split config-load except: narrow to `(FileNotFoundError, PermissionError, OSError) → EXIT_DEPENDENCY_DOWN`; `(yaml.YAMLError, ValidationError) → EXIT_SCHEMA_VIOLATION` |
| M2-CL | Remove `model_validate(model_dump())` round-trip |
| M3-CL | Wrap `_generate_unique_code` call in try/except RuntimeError → JSON error + `EXIT_INTERNAL` (new exit code 99) |
| M4-CL | `_next_lead_id`: use `L%05d` format. Emit `InvariantViolation` if `last >= 99999`. |
| M6-CL | Add lock-ordering comment in `safe_io.py`: `LEADS_LOCK → LOG_LOCK` invariant. |
| M7-CL | Keep `extra="ignore"` on `CateringLeadExtractedFields`. Add SKILL-contract test in test_catering_schemas.py. (NOT changed in script.) |
| L4-CL | `args.message_id = args.message_id.strip()`; `args.customer_phone = args.customer_phone.strip()` |
| L2-CL | `BRIDGE_URL = os.getenv("SHIFT_BRIDGE_URL", "http://127.0.0.1:3000/send")` |
| S2-runtime | At every status change, call `is_catering_transition_allowed(...)`. Refuse if not allowed → `EXIT_ILLEGAL_TRANSITION`. |
| REASON | Update `REASON_TO_ERR_PREFIX` dict to add `message_id_phone_mismatch`; call `assert_rejection_reason_complete(REASON_TO_ERR_PREFIX)` at module import. |

### 3.3 `src/agents/catering/scripts/apply-catering-owner-decision` — Commit 3 (~400 LOC)

**Bug fixes:**

| ID | Diff |
|---|---|
| **Q1** (NEW) | After `rendered = _render_quote(lead, menu_section)`, set `lead.quote_text = rendered` BEFORE `atomic_write_json`. This is the single most important fix. |
| A1 | Order: `with FileLock(LEADS_LOCK):` → `_log(CateringQuoteAttempted(lead_id, original_message_id, code))` → `lead.quote_text = rendered; lead.status = "OWNER_APPROVED"; atomic_write_json(LEADS_PATH, store)` → release lock → `bridge_post(rendered)` → on success, second lock + state→SENT_TO_CUSTOMER. On retry, check `CateringQuoteAttempted` exists for (lead_id, msg_id) → skip POST + return `idempotent_replay: true`. |
| A2 | Same as A1 (idempotency anchor handles both). |
| A3 | `_load_menu_filtered() -> tuple[items, total, error_kind]`. error_kind values: `"ok"`, `"absent"` (file missing), `"corrupt"` (RuntimeError), `"io_error"`. Refuse approve only on `corrupt`/`io_error` (emit `InvariantViolation` + Pushover + `EXIT_DEPENDENCY_DOWN`). `absent` proceeds with empty menu section. |
| A4 | Replace `except (KeyError, OSError): pass` with: emit `InvariantViolation(check="catering_template_format")` + `_send_pushover(...)` + `EXIT_DEPENDENCY_DOWN`. NO inline fallback. |
| A5 | Add `off_menu_items` to `_render_quote` substitution dict. Update template (§3.7). |
| A6 | If `args.decision == "reject"` AND `args.reason` non-empty: render decline message via new template `catering_decline_to_customer.txt`, send via bridge POST. Emit `CateringQuoteSent(decline=True)` audit (or new `CateringDeclineSent`). |
| A7 | Wrap state-write + audit in shared try/except. On audit-write failure: attempt `atomic_write_json(LEADS_PATH, prior_store)` rollback + EXIT_INTERNAL. |
| A8 | When unknown_tags non-empty AND filtered empty: customer message uses new template branch `"We didn't recognize the dietary preference '{tag}'..."`. Emit `InvariantViolation(check="catering_unknown_dietary_tag", detail={"tags": unknown_tags})`. |
| A9 | Code-collision: emit `InvariantViolation(check="catering_code_collision", detail={"code": code, "lead_ids": [...]})` + Pushover. |
| M1-A | Validate code via `re.fullmatch(_CODE_FULL_PATTERN, args.code.upper())`; if missing `#`, prepend; lowercase normalized. |
| M2-A | If `len(args.edit_text) > 2000`: `stderr.write("warning: edit_text truncated to 2000 chars")`. Schema cap = 2000. |
| M3-A | Compute `_now = customer_now(...)` once at lock entry, reuse. |
| M4-A | `reason=""` for approve/reject; only populate for edit (use first 100 chars of edit_text). |
| L1-A | Use `lead.customer_phone[1:]` instead of `lstrip('+')`. |
| L3-A | Resume from OWNER_APPROVED: A1 idempotency anchor handles this naturally. Add explicit branch: if status=="OWNER_APPROVED" AND attempted-audit exists for code → idempotent return; if attempted-audit missing → resume by completing second-lock work. |
| L2-A | `BRIDGE_URL = os.getenv("SHIFT_BRIDGE_URL", "http://127.0.0.1:3000/send")` |
| S2-runtime | `is_catering_transition_allowed(...)` check at every status change. |

### 3.4 `src/agents/catering/scripts/parse-menu-photo` — Commit 4 (~150 LOC)

| ID | Diff |
|---|---|
| PM1 | Under PENDING_LOCK: read existing pending. If present and `now < proposed_at + ttl`: refuse with EXIT_ILLEGAL_TRANSITION + diagnostic stdout `{"existing_code": ..., "expires_at": ...}`. If `now < proposed_at` (clock skew): emit `InvariantViolation(check="clock_skew")` and abort. If TTL expired: emit synthetic `MenuUpdateRejected(reason="ttl_expired")` for prior + proceed. |
| M2-PM | Extract validation errors: include total + dropped count in `MenuUpdateProposed.extraction_dropped_count`. Display first 5 errors but log full count. |
| M3-PM | Wrap `_next_update_id` in `FileLock(counter.parent / "counter.lock")`. |
| M4-PM | Add `OSError` to except clause for `image_path.read_bytes()`. |
| L3-PM | If `len(items) == 0`: refuse with EXIT_OK + `{"status": "no_items", "preview": "..."}`. Skip pending write + counter increment + audit. |
| L2-PM | `BRIDGE_URL = os.getenv("SHIFT_BRIDGE_URL", "http://127.0.0.1:3000/send")` |

### 3.5 `src/agents/catering/scripts/apply-menu-update` — Commit 4 (~80 LOC)

| ID | Diff |
|---|---|
| M1 | `try: existing, _ = load_model(...) except RuntimeError as e: # corrupt-after-quarantine; safe_io already renamed: existing = None # OK to proceed except (FileNotFoundError, PermissionError, OSError, ValidationError) as e: stderr.write(...); return EXIT_SCHEMA_VIOLATION`. ALWAYS `mkdir -p` archive dir + `shutil.copy2` raw bytes BEFORE attempting validation. |
| PM3 | `re.fullmatch(_CODE_FULL_PATTERN, args.code.upper())` |
| M1-AM | Add comment documenting PENDING_LOCK invariant. |
| L2-AM | `BRIDGE_URL = os.getenv("SHIFT_BRIDGE_URL", "http://127.0.0.1:3000/send")` |

### 3.6 `src/platform/schemas.py` `E164Phone.from_any` — Commit 5 (~50 LOC)

```python
@classmethod
def from_any(cls, raw: str, *, country_code: Optional[str] = None) -> "E164Phone":
    """Canonicalize phone input to E.164. Backward-compatible:
    - +1XXXXXXXXXX (already E.164): returned as-is
    - 1XXXXXXXXXX (11-digit with leading 1): prepend +
    - XXXXXXXXXX (bare 10-digit): if country_code='US', prepend +1; else raise
    - +XXXXXXXXXX (10-digit with +, NO country prefix): RAISE — this is the historical bug
    - jid suffix `@s.whatsapp.net`: stripped
    Reads canonical form from store should NEVER raise (validated at write time).
    """
    cleaned = re.sub(r"[\s().-]", "", raw).rstrip("@s.whatsapp.net")
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]

    if cleaned.startswith("+"):
        if not re.fullmatch(r"\+\d{10,15}", cleaned):
            raise ValueError(f"invalid phone: {raw!r}")
        # Reject the historical-bug shape: +XXXXXXXXXX (10 digits, no country prefix)
        # — but ONLY at write time. Read of stored values uses raw bytes.
        return cls(cleaned)

    if cleaned.isdigit():
        if len(cleaned) == 10:
            if country_code == "US":
                return cls("+1" + cleaned)
            raise ValueError(
                f"phone {raw!r} is bare 10-digit without country code. "
                f"Provide country code or set cfg.customer.country_code='US'"
            )
        if len(cleaned) == 11 and cleaned.startswith("1"):
            return cls("+" + cleaned)  # US 11-digit
        if 10 <= len(cleaned) <= 15:
            return cls("+" + cleaned)  # International with country code

    raise ValueError(f"invalid phone: {raw!r}")
```

**Update `Config.customer.country_code: Optional[str] = Field(default=None, pattern=r"^[A-Z]{2}$")`** in CustomerConfig.

**lookup-prior-leads-by-phone:** pass `country_code=cfg.customer.country_code` to `from_any()`. Catch `ValueError` and return `{"lookup_status": "invalid_phone", ...}`.

### 3.7 Templates — Commit 3 + Commit 4

**`catering_quote_to_customer.txt`** — add slot:
```
... (existing content) ...
{off_menu_items_section}
... (existing content) ...
```

**`catering_decline_to_customer.txt`** (NEW):
```
Hi {customer_name},

Thanks so much for thinking of us for your catering needs! Unfortunately we won't be able to take this one on:

{decline_reason}

We'd love to help with future events — please reach out anytime.

— {owner_name}, {customer_business_name}
```

### 3.8 New file `tools/catering-state-migrate.py` — Commit 1

Pre-deploy migration script. Usage: `python tools/catering-state-migrate.py --leads-path /opt/shift-agent/state/catering-leads.json --pending-path /opt/shift-agent/state/catering-menu-pending.json [--dry-run]`. Performs:
1. Load leads.json → check for AWAITING / OWNER_APPROVED / SENT leads with empty quote_text. If found: backfill via `_render_quote_for_legacy_lead(lead)` (re-uses apply-script's renderer).
2. Check pending.json for L-bearing confirmation_code → emit warning, no auto-fix (owner must re-photograph).
3. Phone canonicalization: scan all customer_phone values; if any is bare 10-digit-with-plus (the historical-bug shape `+9045551234`): prepend `1` → `+19045551234`. Verified safe because `from_any` of any input that *should* have been `+1XXX...` will produce that form.
4. Write atomic + emit audit `MigrationApplied(version=v0.3, ...)`.

### 3.9 Helpers — Commit 6

**`tests/_b1_helpers.py`** — extend:
```python
def run_create(env_dir, bridge_port, fields, *,
               customer_phone="+15551234567", customer_name="Priya",
               raw="...", message_id="msg_1",
               now_override: Optional[datetime] = None,
               customer_tz: Optional[str] = None,           # NEW
               path_overrides: Optional[dict[str, Path]] = None) -> CompletedProcess:  # NEW
    # ...
```

Same for `run_apply`.

### 3.10 Test additions — Commit 6 (~80 new tests)

| Coverage | Test count | File |
|---|---|---|
| S1 quote_text invariant (4 statuses × write/read paths) | 8 | test_catering_schemas.py |
| S2 transition table (~22 forbidden + ~10 allowed) | 32 | test_catering_schemas.py |
| S3 tz validator (naive write rejected, naive read auto-converted) | 4 | test_catering_schemas.py |
| S4 CateringOwnerEdited + edit_text validator | 4 | test_catering_schemas.py |
| S6 regex unification | 4 | test_catering_schemas.py |
| L1-S calendar regex | 3 | test_catering_schemas.py |
| New audit classes (5 × instantiation + missing fields) | 10 | test_catering_schemas.py |
| Q1 quote_text persistence in apply | 2 | resurrected v02 |
| A1+A2 idempotency anchor | 4 | resurrected v02 |
| A3 narrow except (3 error kinds) | 6 | resurrected v02 |
| A4 template format → Pushover | 2 | resurrected v02 |
| A5 off_menu in customer quote | 2 | resurrected v02 |
| A6 reject sends customer message | 2 | resurrected v02 |
| A7 audit-write rollback | 2 | resurrected v02 |
| A8 unknown dietary tag prose | 2 | resurrected v02 |
| C1+C2 idempotency edge cases | 6 | resurrected v02 |
| PM1 pending overwrite + clock-skew | 4 | new file `test_parse_menu_photo.py` |
| PM2 phone canonicalization (10/11/intl/edge) | 8 | resurrected lookup |
| L0 phone read backward-compat | 4 | resurrected lookup |
| SKILL-contract drift test (M7-CL) | 1 | test_catering_schemas.py |

**Total ~110 new tests.** Final pytest count target: ~165 collected (after dedup with B1 file).

### 3.11 New file `tools/run-catering-staging-tests.sh` — Commit 6

```bash
#!/usr/bin/env bash
# Pre-merge VPS staging test runner. Tests live outside tarball; this script
# scp's them into a sandbox, runs pytest, exits non-zero on any failure.
set -euo pipefail
ssh main-vps 'mkdir -p /tmp/catering_e2e/tests && ln -sfn /opt/shift-agent/staging-new/src /tmp/catering_e2e/src'
scp tests/test_catering_*.py tests/test_lookup_prior_leads.py tests/_b1_helpers.py tests/conftest.py main-vps:/tmp/catering_e2e/tests/
ssh main-vps 'cd /tmp/catering_e2e && /opt/shift-agent/venv/bin/python -m pytest tests/ -q'
```

### 3.12 Smoke-test extension — Commit 1

Edit `src/agents/shift/scripts/shift-agent-smoke-test.sh`:

```bash
# After existing checks, add:
echo "✓ catering schema validation"
sudo -u shift-agent /opt/shift-agent/venv/bin/python -c "
import json, sys
sys.path.insert(0, '/opt/shift-agent')
from schemas import CateringLeadStore, MenuPendingUpdate
import pathlib
leads_p = pathlib.Path('/opt/shift-agent/state/catering-leads.json')
pending_p = pathlib.Path('/opt/shift-agent/state/catering-menu-pending.json')
if leads_p.exists():
    CateringLeadStore.model_validate(json.loads(leads_p.read_text()))
if pending_p.exists():
    MenuPendingUpdate.model_validate(json.loads(pending_p.read_text()))
print('catering schema validation passed')
"
```

This catches S1, S6, L0 at smoke-time. If validation fails, deploy auto-rolls back.

---

## 4. Build sequence (final — 6 commits)

| # | Commit | Files | LOC | Tests |
|---|---|---|---|---|
| 1 | **Schema layer + smoke-test extension + migration tool** | `src/platform/schemas.py`, `src/agents/shift/scripts/shift-agent-smoke-test.sh`, `tools/catering-state-migrate.py` | ~500 | ~60 schema tests |
| 2 | **`create-catering-lead`** + safe_io.py lock-ordering comment | `src/agents/catering/scripts/create-catering-lead`, `src/platform/safe_io.py` | ~280 | (tests in commit 6) |
| 3 | **`apply-catering-owner-decision`** + `catering_quote_to_customer.txt` + `catering_decline_to_customer.txt` | `src/agents/catering/scripts/apply-catering-owner-decision`, 2 templates | ~400 | (tests in commit 6) |
| 4 | **Menu scripts** | `parse-menu-photo`, `apply-menu-update` | ~230 | (tests in commit 6) |
| 5 | **lookup + phone canonicalization** | `lookup-prior-leads-by-phone`, `src/platform/schemas.py` (E164Phone.from_any extension), CustomerConfig.country_code | ~120 | (tests in commit 6) |
| 6 | **Test resurrection + extensions + helper API** | `tests/_b1_helpers.py`, `tests/test_catering_v02_scripts.py`, `tests/test_lookup_prior_leads.py`, `tests/test_catering_schemas.py`, NEW `tests/test_parse_menu_photo.py`, NEW `tools/run-catering-staging-tests.sh` | ~50 src + ~1500 tests | All new tests pass |

**Per-commit pytest gate**:
- After Commit 1: existing tests + new schema tests pass (mode="before" shims keep legacy data working)
- After Commit 2-5: existing tests still pass (script changes don't break tests yet — they're only re-enabled in Commit 6)
- After Commit 6: full ~165 tests pass on VPS Linux via `tools/run-catering-staging-tests.sh`

---

## 5. Deploy sequence

### 5.1 Pre-deploy hard gate (NEW — was missing in plan)

```bash
# Run BEFORE building tarball.
ssh main-vps 'python3 -c "
import json, sys, re
sys.path.insert(0, \"/opt/shift-agent\")
from schemas import CateringLeadStore  # uses NEW schema
data = json.load(open(\"/opt/shift-agent/state/catering-leads.json\"))
issues = []
for l in data[\"leads\"]:
    # check 1: phone shape (post-PM2 canon)
    p = l[\"customer_phone\"]
    if re.fullmatch(r\"^\+\d{10}$\", p):
        issues.append(f\"{l[\\\"lead_id\\\"]}: phone {p!r} is bare 10-digit-with-plus (historical bug)\")
    # check 2: quote_text shape
    if l[\"status\"] in (\"AWAITING_OWNER_APPROVAL\",\"OWNER_APPROVED\",\"OWNER_EDITED\",\"SENT_TO_CUSTOMER\"):
        if not (l.get(\"quote_text\") or \"\").strip():
            issues.append(f\"{l[\\\"lead_id\\\"]}: status={l[\\\"status\\\"]} but quote_text empty\")
if issues:
    print(\"PRE-DEPLOY ISSUES:\")
    for i in issues: print(\"  -\", i)
    print(\"\\nRun migration: tools/catering-state-migrate.py\")
    sys.exit(1)
print(\"pre-deploy gate OK\")
"'
```

If issues found → run `tools/catering-state-migrate.py` on VPS first, then re-run gate.

### 5.2 Deploy procedure

1. Local pytest passes (162 + new = ~165 tests on VPS-equivalent path)
2. Pre-deploy gate (§5.1) on VPS — must pass clean
3. `bash tools/build-deploy-tarball.sh`
4. `scp /c/projects/SME-Agents/shift-agent-deploy.tgz main-vps:/tmp/`
5. `ssh main-vps 'sudo tar xzf /tmp/shift-agent-deploy.tgz -C /opt/shift-agent/staging-new/'`
6. `ssh main-vps 'sudo /usr/local/bin/shift-agent-deploy.sh'` — runs smoke test (NOW with catering coverage). On failure: auto-rollback.
7. `tools/run-catering-staging-tests.sh` — VPS Linux pytest validation (post-deploy, optional safety net)
8. **20-min soak**: monitor `/opt/shift-agent/logs/decisions.log` for any `invariant_violation` entries; monitor `journalctl -u hermes-gateway` for ValidationError tracebacks

### 5.3 Deploy timing

**Recommended window**: weekday after 21:00 CT, low message volume. Service unavailability ~7-10s during hermes-gateway restart. Auto-rollback safe net.

---

## 6. Risk register (updated)

| Risk | Mitigation |
|---|---|
| 9 in-flight leads load fail | mode="before" shim + Q1 fix + migration script + smoke-test gate |
| Audit log read-back of new entry types crashes | LogEntry union updated atomically in Commit 1 |
| schema_version rollback hostile | extra="ignore" on CateringLeadStore |
| L-bearing confirmation_code on disk | Pre-deploy scan (no L codes found in current pending.json — empty file) |
| Phone canonicalization on read | from_any preserves existing canonical form; only NEW writes use strict shape |
| New customer (no menu) approve flow blocks | A3 three-way return; "absent" proceeds with empty section |
| Bridge retry → duplicate quote | `CateringQuoteAttempted` + `CateringOwnerApprovalCardAttempted` idempotency anchors |
| TTL clock skew false rejection | `now < proposed_at` sanity check |
| T1 resurrection scope explosion | Pre-resurrect tests in Commit 6 only AFTER all script-side fixes land. Bugs surfaced may be triaged: critical → fixed in same PR; minor → filed as follow-up. **Hard cap: if >5 new bugs require new commits, peel into next PR.** |
| Deploy rollback corrupts new schema_version | Acceptable: schema_version field dropped on rollback; no functional break |

---

## 7. What this design is NOT

- Not a Decimal money migration (deferred, cost > benefit at v0.2)
- Not Hermes-side SKILL prompt changes (out of scope — schema/script-only)
- Not a refactor to packaged Python modules (defer until 2nd customer)
- Not addressing M5 markdown-fence regex (gpt-4o-mini stable, defer until model deprecation)
- Not addressing 401/403 distinct exit code (defer to next OpenRouter pass)

---

## 8. Pipeline status

- ✅ Plan written + 5 plan reviews completed
- ✅ Design v1 written (this doc) — addresses all 28 plan-review concerns
- ⏳ 5 design reviews
- ⏳ Build (6 commits)
- ⏳ PR + 5 code reviews
- ⏳ Apply review fixes
- ⏳ Pre-merge: VPS gate + run-catering-staging-tests.sh
- ⏳ Merge + deploy + 20-min soak
