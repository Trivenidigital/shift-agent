# Design v2 — Catering Comprehensive Fix

**Drift-check tag:** extends-Hermes
**Branch:** `fix/catering-comprehensive`
**Source:** Design v1 (`tasks/catering-comprehensive-fix-design.md`) + 5 design-review synthesis (this doc).
**Status:** Final design. Proceeds directly to Build.

This v2 resolves **34 design-review concerns** across 5 reviewer lenses (schema-migration, build-sequence, silent-failure, deploy-ops, test-coverage). Where v1 had errors (status names, ordering bugs, deploy chicken-and-egg), v2 corrects them.

---

## 1. Critical corrections from v1

### 1.1 CATERING_TRANSITIONS uses correct Literal values

v1 referenced `EXPIRED`, `FAILED`, `CUSTOMER_REPLIED`, `REJECTED` — none of which exist in the actual `CateringLeadStatus` Literal at `schemas.py:276-287`.

**Actual statuses**: `NEW`, `EXTRACTING`, `NOT_CATERING`, `AWAITING_OWNER_APPROVAL`, `OWNER_APPROVED`, `OWNER_EDITED`, `OWNER_REJECTED`, `SENT_TO_CUSTOMER`, `CLOSED`, `STALE`.

**Corrected table** (covers EVERY actual status):

```python
CATERING_TRANSITIONS: dict[CateringLeadStatus, set[CateringLeadStatus]] = {
    "NEW": {"EXTRACTING", "NOT_CATERING"},
    "EXTRACTING": {"AWAITING_OWNER_APPROVAL", "NOT_CATERING"},
    "NOT_CATERING": set(),                                                                        # terminal
    "AWAITING_OWNER_APPROVAL": {"OWNER_APPROVED", "OWNER_EDITED", "OWNER_REJECTED", "STALE"},
    "OWNER_EDITED": {"AWAITING_OWNER_APPROVAL", "OWNER_REJECTED"},
    "OWNER_APPROVED": {"SENT_TO_CUSTOMER", "AWAITING_OWNER_APPROVAL"},                            # AWAITING for v0.3 retry-from-failure
    "SENT_TO_CUSTOMER": {"CLOSED", "STALE"},
    "OWNER_REJECTED": set(),                                                                      # terminal
    "CLOSED": set(),                                                                              # terminal
    "STALE": set(),                                                                               # terminal
}

def is_catering_transition_allowed(from_s: str, to_s: str) -> bool:
    """Returns True only for allowed transitions. False for unknown statuses."""
    return to_s in CATERING_TRANSITIONS.get(from_s, set())  # type: ignore[arg-type]
```

This intentionally does NOT add `EXPIRED` or `FAILED` (would require Literal extension + on-disk migration). Existing terminal statuses (`STALE`) cover the lifecycle.

### 1.2 No module-level asserts

v1 had `assert WHATSAPP_OFF_MENU_BUDGET >= 50` at module level + `assert_rejection_reason_complete(...)` at module import. **Both raise `AssertionError` on import**, which is stripped by `python -O` AND produces unstructured tracebacks.

**v2**: move both to a `_validate_module_invariants()` function called at the start of `main()`. On failure: structured stderr + `EXIT_SCHEMA_VIOLATION (4)`. Replace `assert` with `if not …: raise RuntimeError(...)`.

### 1.3 country_code via Pydantic context, NOT field-validator path

v1 added `country_code` parameter to `E164Phone.from_any()` and assumed it would flow into Pydantic `model_validate`. **It cannot** — Pydantic field validators don't have access to runtime config.

**v2** approach:
- `from_any(raw, *, country_code=None)` for **explicit script call sites** (create-script, lookup-script). They have `cfg.customer.country_code` available.
- The schema-side validator (when reading `customer_phone` from disk via `model_validate`) does NOT use `from_any`. Instead, it accepts any `+\d{10,15}` form (including the historical-bug shape `+9045551234`) so reads never crash.
- The **migration script** (run pre-deploy) ensures all stored phones are in the canonical `+1XXXXXXXXXX` form before deploy. After migration, validator sees only valid forms.

### 1.4 `cfg.customer.country_code` field added in Commit 1, not Commit 5

v1 placed this field add in Commit 5; build-sequence reviewer caught that it must land with the schema in Commit 1.

**v2**: extend `CustomerConfig` (line 210-220 in schemas.py) in Commit 1:

```python
class CustomerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    location_id: str
    timezone: str
    languages: list[str] = []
    country_code: Optional[str] = Field(default=None, pattern=r"^[A-Z]{2}$")  # NEW v0.3
    # ... existing valid_tz validator unchanged ...
```

### 1.5 Migration tool deploy sequence

v1 had chicken-and-egg: migration script needs new schema, but VPS has old schema until tarball extracted.

**v2 sequence**:
1. Build tarball locally (includes `tools/catering-state-migrate.py`)
2. scp tarball to VPS at `/tmp/`
3. `ssh main-vps 'sudo tar xzf /tmp/...tgz -C /opt/shift-agent/staging-new/'`
4. **NEW step**: `ssh main-vps 'cd /opt/shift-agent/staging-new && sudo -u shift-agent /opt/shift-agent/venv/bin/python tools/catering-state-migrate.py --leads-path /opt/shift-agent/state/catering-leads.json --backup'`
5. **NEW step**: `tools/run-catering-staging-tests.sh` — pytest against staging-new src
6. `ssh main-vps 'sudo /usr/local/bin/shift-agent-deploy.sh'` — runs install_artifacts + smoke + service restart
7. 20-min soak

The migration runs AFTER the tarball is on disk (so it can import the new schema from `staging-new/src/platform/`) but BEFORE `shift-agent-deploy.sh` overwrites the live `/opt/shift-agent/`.

### 1.6 Pre-deploy gate uses NEW schema (correctly placed)

v1 §5.1 SSH gate imported old `/opt/shift-agent/schemas.py`. **v2**: gate runs AFTER step 3 (tarball extracted) using staging-new's `schemas.py`:

```bash
ssh main-vps 'cd /opt/shift-agent/staging-new && /opt/shift-agent/venv/bin/python -c "
import sys, json, re, pathlib
sys.path.insert(0, \"src/platform\")
from schemas import CateringLeadStore  # NEW schema from staging-new
issues = []
leads_p = pathlib.Path(\"/opt/shift-agent/state/catering-leads.json\")
if leads_p.exists():
    raw = json.loads(leads_p.read_text())
    for lead_dict in raw.get(\"leads\", []):
        # phone-shape check
        p = lead_dict.get(\"customer_phone\", \"\")
        if re.fullmatch(r\"^\+\d{10}$\", p):
            issues.append(f\"{lead_dict.get(\\\"lead_id\\\")}: bare-10-digit phone {p!r} — historical L0 bug\")
        # quote_text shape check (mode=before will backfill, but flag for visibility)
        if lead_dict.get(\"status\") in (\"AWAITING_OWNER_APPROVAL\",\"OWNER_APPROVED\",\"OWNER_EDITED\",\"SENT_TO_CUSTOMER\"):
            if not (lead_dict.get(\"quote_text\") or \"\").strip():
                issues.append(f\"{lead_dict.get(\\\"lead_id\\\")}: status={lead_dict.get(\\\"status\\\")} quote_text empty — will be backfilled\")
    # Also exercise model_validate to confirm shim works:
    CateringLeadStore.model_validate(raw)  # tests mode=before backfill
if issues:
    print(\"PRE-DEPLOY ISSUES (non-fatal — backfill shim handles):\")
    for i in issues: print(f\"  - {i}\")
print(\"OK\")
"'
```

### 1.7 LogEntry rollback safety

v1 risk register acknowledged forward-compat (new code reading old log) but missed BACKWARD (old code reading new audit-entry types after rollback).

**v2** mitigation: rollback is acceptable because (a) the live VPS has tooling that reads decisions.log via `LogEntry` discriminated union only in cockpit/dispatcher tools (currently dormant), (b) the deployed `shift-agent` services do not parse historical audit entries, only append. Rollback recovery is still POSSIBLE; subsequent reads of new-typed entries fail closed — but that surfaces as `ValidationError` not silent corruption. Document in §6.

If rollback occurs, operator follow-up: truncate log entries past last known-good marker, OR redeploy forward (recommended). Add to runbook §5.4.

---

## 2. New scope additions from review

### 2.1 Add `CateringOwnerDecision` `mode="before"` shim

Same pattern as `_backfill_legacy_quote_text`: legacy entries with `decision="edit"` and `edit_text=""` could exist in audit log. The new `mode="after"` validator would block their replay.

```python
class CateringOwnerDecision(_BaseEntry):
    # ... existing fields ...

    @model_validator(mode="before")
    @classmethod
    def _backfill_legacy_edit_text(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("decision") == "edit" and not (data.get("edit_text") or "").strip():
            data["edit_text"] = "<legacy-pre-v0.3-no-edit-text-recorded>"
        return data

    @model_validator(mode="after")
    def _edit_text_required_for_edit(self) -> "CateringOwnerDecision":
        if self.decision == "edit" and not self.edit_text.strip():
            raise ValueError("decision='edit' requires non-empty edit_text")
        return self
```

### 2.2 `assert_rejection_reason_complete` uses bidirectional ==

v1 used subset check. **v2** uses equality:

```python
def assert_rejection_reason_complete(reason_dict: dict) -> None:
    schema_reasons = set(get_args(CateringLeadRejected.model_fields["reason"].annotation))
    runtime_reasons = set(reason_dict.keys())
    if runtime_reasons != schema_reasons:
        missing_in_dict = schema_reasons - runtime_reasons
        missing_in_schema = runtime_reasons - schema_reasons
        raise RuntimeError(
            f"REASON drift: missing in dict {missing_in_dict}, missing in schema {missing_in_schema}"
        )
```

### 2.3 `from_any` `rstrip` bug fix

v1 had `raw.rstrip("@s.whatsapp.net")` which strips chars not substring. **v2**:

```python
@classmethod
def from_any(cls, raw: str, *, country_code: Optional[str] = None) -> "E164Phone":
    cleaned = re.sub(r"[\s().-]", "", raw)
    cleaned = cleaned.split("@", 1)[0]  # CORRECT: substring removal
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    # ... rest of canonicalization ...
```

### 2.4 Migration script: backup + idempotency

```python
# tools/catering-state-migrate.py — Commit 1
def main():
    backup_path = LEADS_PATH.with_suffix(f".json.pre-migrate-{int(time.time())}.bak")
    shutil.copy2(LEADS_PATH, backup_path)
    print(f"backup: {backup_path}")

    data = json.loads(LEADS_PATH.read_text())
    for lead in data["leads"]:
        # Phone canon — IDEMPOTENT (only fix bare-10-digit-with-plus)
        p = lead.get("customer_phone", "")
        if re.fullmatch(r"^\+\d{10}$", p):  # 10 digits with +
            lead["customer_phone"] = "+1" + p[1:]  # prepend 1
            print(f"migrated phone: {p} -> {lead['customer_phone']}")
        # quote_text backfill for post-AWAITING leads — re-render from extracted
        if lead["status"] in {"AWAITING_OWNER_APPROVAL", "OWNER_APPROVED",
                              "OWNER_EDITED", "SENT_TO_CUSTOMER"}:
            if not (lead.get("quote_text") or "").strip():
                lead["quote_text"] = _render_quote_for_legacy_lead(lead)
                print(f"backfilled quote_text for {lead['lead_id']}")
    # Atomic write via safe_io
    atomic_write_json(LEADS_PATH, data)
```

### 2.5 Smoke-test self-test for transitions

Per test-coverage reviewer #10, add to smoke-test extension:

```python
from schemas import is_catering_transition_allowed
assert not is_catering_transition_allowed("CLOSED", "NEW"), "CLOSED is terminal"
assert is_catering_transition_allowed("NEW", "EXTRACTING"), "NEW->EXTRACTING is allowed"
assert is_catering_transition_allowed("AWAITING_OWNER_APPROVAL", "OWNER_APPROVED"), "happy path"
print("✓ catering transition table self-test passed")
```

### 2.6 `_b1_helpers.mk_lead` quote_text default

Per test-coverage #8, post-NEW statuses need non-empty quote_text or the new validator rejects. **v2**:

```python
def mk_lead(*, lead_id, phone, status="AWAITING_OWNER_APPROVAL",
            created_at, event_date=None, dietary=None,
            quote_text: Optional[str] = None) -> dict:
    """Construct a minimal CateringLead dict matching the schema."""
    if quote_text is None:
        # v0.3 invariant: post-NEW statuses require non-empty quote_text.
        quote_text = "" if status in {"NEW", "EXTRACTING", "NOT_CATERING"} else "<test-quote-content>"
    return {
        "lead_id": lead_id,
        # ... existing fields ...
        "quote_text": quote_text,
        # ...
    }
```

### 2.7 A8 customer message — multi-tag handling

v2: when ANY tag in `unknown_tags` (non-empty), append to quote regardless of filtered count:

```
{rendered_menu_section}

Note: we don't currently classify items by '{', '.join(unknown_tags)}'. If that's a hard requirement, please confirm and we'll send custom options.
```

When `unknown_tags` non-empty AND filtered empty: full message becomes the "Note" only (no menu). When mixed (some matched, some unknown): both appear.

### 2.8 A6 decline-message idempotency anchor

Symmetric to A1: emit `CateringDeclineAttempted` audit row in same lock as state-write BEFORE bridge POST.

```python
class CateringDeclineAttempted(_BaseEntry):
    type: Literal["catering_decline_attempted"] = "catering_decline_attempted"
    lead_id: str = Field(min_length=1)
    original_message_id: str = Field(min_length=1)
    code: str = Field(pattern=_CODE_FULL_PATTERN)
```

### 2.9 A7 rollback Pushover on triple-failure

```python
try:
    write_audit(...)
except Exception as audit_exc:
    try:
        atomic_write_json(LEADS_PATH, prior_store)  # rollback
    except Exception as rollback_exc:
        # Triple failure — alert operator immediately
        try:
            _send_pushover(
                title="CATERING TRIPLE FAILURE",
                message=f"audit-write failed: {audit_exc}; rollback also failed: {rollback_exc}; lead {lead.lead_id} may be in inconsistent state. MANUAL INTERVENTION REQUIRED.",
                priority=2,  # emergency
            )
        except Exception:
            pass  # Pushover also dead — at this point operator MUST be paged via journald-log monitoring
        raise SystemExit(EXIT_INTERNAL)  # 99
    sys.exit(EXIT_INTERNAL)
```

### 2.10 mode="before" shim WARNs include audit row

Per silent-failure #M6, add structured InvariantViolation audit on first occurrence per lead:

```python
@model_validator(mode="before")
@classmethod
def _backfill_legacy_quote_text(cls, data):
    # ... shim body ...
    if backfill_needed:
        data["quote_text"] = "<legacy-pre-v0.3-no-quote-persisted>"
        # NOTE: audit row written here would require SafeIO + path config —
        # not available in schema layer. Instead: stderr WARN consumed by
        # journald, plus migration tool catches and fixes. The pre-deploy
        # gate (§1.6) tests this shim explicitly.
    return data
```

Because schema validators don't have audit-writer access, we rely on:
1. Pre-deploy gate flags backfilled leads BEFORE deploy
2. Migration script fixes them
3. After migration, shim should never fire — if it does, it's a regression flag

### 2.11 PM2 country_code misconfig — accept-and-flag

If `cfg.customer.country_code` is wrong (e.g., set to "US" for a Mexican customer), bare 10-digit input gets `+1` prepended. **v2** mitigation:

- Document constraint: `country_code` must match the operator's ACTUAL country, not aspirational
- Add startup check: at script start, log `country_code in use` for journald visibility
- Add ONE-TIME WARN per phone via simple in-memory dedup set (process-local; can't be persistent)
- For now, accept misconfig as operator's responsibility. Future: derive from WhatsApp JID country prefix.

### 2.12 Run-catering-staging-tests.sh test glob

Per test-coverage #9: glob `test_catering_*.py` doesn't include new `test_parse_menu_photo.py`. **v2**:

```bash
scp tests/test_catering_*.py tests/test_lookup_prior_leads.py tests/test_parse_menu_photo.py tests/_b1_helpers.py tests/conftest.py main-vps:/tmp/catering_e2e/tests/
```

OR rename to `test_catering_parse_menu_photo.py` for glob consistency. **Choose rename** for maintainability.

### 2.13 Manual rollback runbook (§5.4)

```bash
# If smoke passes but issues emerge during 20-min soak:
ssh main-vps '/usr/local/bin/shift-agent-deploy.sh list'  # show available tags
ssh main-vps '/usr/local/bin/shift-agent-deploy.sh rollback <prev-deploy-tag>'
# verify
ssh main-vps 'systemctl status hermes-gateway shift-agent-cockpit'
ssh main-vps 'jq .schema_version /opt/shift-agent/state/catering-leads.json'  # 1 if old, missing if very-old
# state restoration if needed
ssh main-vps 'cp /opt/shift-agent/state/catering-leads.json.pre-migrate-<ts>.bak /opt/shift-agent/state/catering-leads.json'
```

### 2.14 20-min soak monitoring command

Per deploy-ops M1:

```bash
# In one terminal, run for 20 min:
ssh main-vps 'tail -F /opt/shift-agent/logs/decisions.log | grep -E "invariant_violation|catering.*failed|ValidationError"'

# Pass criterion: zero entries matching above for 20 minutes.
# Fail criterion: any entry → manual rollback per §5.4
```

### 2.15 KEEP_TARBALLS during PR cycle

Bump `KEEP_TARBALLS=8` for this PR's deploy iterations (set as env var in deploy script invocation). Restore default after merge.

### 2.16 Q1 test count expanded 2 → 5

Per test-coverage #1:
- T1: quote_text persists after approve
- T2: persisted quote_text byte-matches rendered output
- T3: quote_text persists after edit + re-approve
- T4: replay of approve doesn't double-write
- T5: idempotent replay returns same quote_text

### 2.17 A1+A2 anchor tests expanded 4 → 8

Per test-coverage #2:
- T1: first approve writes attempted-audit BEFORE state
- T2: retry with attempted-audit present → idempotent return
- T3: crash between attempted-audit and bridge → resume sends
- T4: crash between bridge POST success and second-lock state → resume detects via attempted
- T5: crash mid-bridge → next retry inspects bridge response idempotency (no anchor) → assumes failure
- T6: attempted-audit-write failure → no state mutation (atomic guarantee)
- T7: state-write failure after attempted-audit → rollback attempt logged
- T8: replay across multiple message_ids on same lead → distinct attempted entries

### 2.18 SourceFileLoader port plan

Per test-coverage #13: explicit line-by-line port plan for resurrected v02/lookup files. Each test file's `_run_*` helper(s) get the same surgery as `_b1_helpers.run_create`:

```python
# OLD (broken):
spec = importlib.util.spec_from_file_location("ccl", str(CREATE))
mod = importlib.util.module_from_spec(spec)  # AttributeError when spec=None

# NEW (working):
loader = importlib.machinery.SourceFileLoader("ccl", str(CREATE))
spec = importlib.util.spec_from_file_location("ccl", str(CREATE), loader=loader)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
# Override paths AFTER exec_module
mod.CONFIG_PATH = ...
mod.LEADS_PATH = ...
sys.exit(mod.main())
```

Locations to fix:
- `tests/test_catering_v02_scripts.py:135` (CCL wrapper)
- `tests/test_catering_v02_scripts.py:185` (ACOD wrapper)
- `tests/test_lookup_prior_leads.py:40` (lookup helper)
- `tests/test_lookup_prior_leads.py:270` (CLI wrapper)
- `tests/test_lookup_prior_leads.py:543` (CLI wrapper)

Approach: rather than patching each file's helpers, **route through `_b1_helpers.run_create / run_apply / lookup_prior_leads_by_phone_helper`**. This eliminates 5 broken patterns by replacing them with 3 working ones.

---

## 3. Final build sequence (8 commits, was 6)

Per build-sequence reviewer H-4 (1500 LOC test code in one commit unreviewable), split Commit 6 into 6a/6b/6c.

| # | Commit | Files | LOC est | Tests added |
|---|---|---|---|---|
| 1 | **Schema layer + smoke + migration tool + country_code in CustomerConfig + status table corrections** | `src/platform/schemas.py` (incl `_CODE_FULL_PATTERN`, `CATERING_TRANSITIONS`, all new audit classes, all `mode="before"` shims, `country_code` field), `src/agents/shift/scripts/shift-agent-smoke-test.sh`, `tools/catering-state-migrate.py` | ~600 | 60 schema + 22 transition table |
| 2 | **`create-catering-lead`** | `src/agents/catering/scripts/create-catering-lead`, `src/platform/safe_io.py` (lock-order comment) | ~280 | (in 6b) |
| 3 | **`apply-catering-owner-decision` + new templates** | `src/agents/catering/scripts/apply-catering-owner-decision`, `src/agents/catering/templates/catering_quote_to_customer.txt`, NEW `src/agents/catering/templates/catering_decline_to_customer.txt` | ~420 | (in 6b) |
| 4 | **Menu scripts** | `parse-menu-photo`, `apply-menu-update` | ~230 | (in 6b) |
| 5 | **Lookup script** (uses `country_code` already added in C1) | `src/agents/catering/scripts/lookup-prior-leads-by-phone` | ~80 | (in 6b) |
| 6a | **T1 test resurrection** — port v02 + lookup files to `_b1_helpers` SourceFileLoader pattern | `tests/_b1_helpers.py` (extend run_create with customer_tz + path_overrides; add now_override to run_apply; add quote_text default in mk_lead), `tests/test_catering_v02_scripts.py`, `tests/test_lookup_prior_leads.py` | ~200 | 63 tests resurrected |
| 6b | **New per-fix tests** (from §3.10 in v1 design + reviewer additions) | `tests/test_catering_schemas.py` (extend), `tests/test_catering_v02_scripts.py` (new tests for fixes), NEW `tests/test_catering_parse_menu_photo.py` | ~1000 | ~80 new |
| 6c | **`tools/run-catering-staging-tests.sh`** + scripts/docs | NEW `tools/run-catering-staging-tests.sh`, README updates | ~50 | — |

**Per-commit pytest gate**:
- After Commit 1: `mode="before"` shims allow legacy data through; new schema tests + transition tests pass
- After Commits 2–5: Existing tests still pass (helpers untouched until 6a; old tests still skip on Windows / fail on Linux due to broken pattern)
- After Commit 6a: `tests/test_catering_v02_scripts.py` and `tests/test_lookup_prior_leads.py` actually run on Linux. Possibly surface bugs in scripts modified in Commits 2-5 — those are real bugs to fix in subsequent commits 7+.
- After Commit 6b: Full test suite passes
- After Commit 6c: ready for PR

**T1 surfaced bugs**: hard cap = 5 additional commits. If exceeded, peel into next PR.

---

## 4. Final deploy sequence (corrected)

1. Local pytest passes against the local code (Windows: most catering tests skip; can't validate end-to-end on Windows)
2. `bash tools/build-deploy-tarball.sh` → builds tgz, runs local pytest, includes `tools/`
3. `scp shift-agent-deploy.tgz main-vps:/tmp/`
4. `ssh main-vps 'sudo tar xzf /tmp/shift-agent-deploy.tgz -C /opt/shift-agent/staging-new/'`
5. **Pre-deploy gate** (uses NEW schema from staging-new — see §1.6) — flags any data needing migration
6. **Run migration**: `ssh main-vps 'cd /opt/shift-agent/staging-new && sudo -u shift-agent /opt/shift-agent/venv/bin/python tools/catering-state-migrate.py --leads-path /opt/shift-agent/state/catering-leads.json --backup'`
7. **Run staging tests**: `bash tools/run-catering-staging-tests.sh` — pytest against staging-new
8. **Deploy**: `ssh main-vps 'sudo /usr/local/bin/shift-agent-deploy.sh'` — install_artifacts + smoke (now with catering schema + transition self-test) + auto-rollback on smoke fail
9. **20-min soak**: `tail -F` per §2.14
10. On any failure during soak: manual rollback per §5.4 (§2.13 above)

Deploy timing: weekday 21:00 CT (low Triveni traffic). Service unavailability window: ~5–10s (per measured PR #28 deploy timing).

---

## 5. Risk register (final)

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Mode=before shim doesn't backfill all legacy quote_text | Low | High | Pre-deploy gate flags + migration script fixes before deploy |
| Migration script idempotency: double-prepend +1 | Low | Medium | Idempotent regex check (`^\+\d{10}$` only triggers once) |
| Migration script corrupts data | Low | Critical | `.bak` backup written first; rollback procedure documented (§5.4) |
| LogEntry rollback breaks audit replay | Low | Low | Audit replay tooling is dormant; live services append-only |
| Status-machine table mis-mapped (NEW vs CORRECTED) | Low | High | Smoke test self-test (§2.5); 22 explicit transition tests |
| 20-min soak misses a slow regression | Medium | Medium | Concrete monitoring command (§2.14); auto-rollback on Pushover-triggering events |
| KEEP_TARBALLS=5 rotates baseline out during iteration | Medium | Low | Bump to 8 for this PR cycle (§2.15) |
| In-flight customer message during install_artifacts swap (~1-2s) | Low | Low | Hermes-gateway restart provides clean cut; messages queued by WhatsApp |
| Phone canon misconfig (country_code wrong) | Medium | Medium | Documentation + startup log; future: WhatsApp JID-based derivation |
| New audit classes not in `LogEntry` union | Low | High | All 6 added in same Commit 1 atomically (5 from v1 + new `CateringDeclineAttempted`) |
| T1 resurrection surfaces 5+ new bugs | Medium | Medium | Hard cap 5 fix commits in same PR; >5 → peel into next PR |
| Migration script can't import schemas before deploy | Resolved | — | v2 sequence runs migration AFTER tarball extraction (§1.5) |
| Module-level asserts crash imports | Resolved | — | Moved to main() entry (§1.2) |

---

## 6. Pipeline status

- ✅ Plan (`tasks/catering-comprehensive-fix-plan.md`)
- ✅ 5 plan reviews
- ✅ Design v1 (`tasks/catering-comprehensive-fix-design.md`)
- ✅ 5 design reviews
- ✅ **Design v2 (this doc)** — addresses 34 review concerns
- ⏳ Build (8 commits — Commit 1 starts now)
- ⏳ PR + 5 code reviews
- ⏳ Apply review fixes
- ⏳ Pre-merge VPS validation
- ⏳ Merge + deploy + 20-min soak
- ⏳ (Then: same exercise for Shift Agent — overnight task #2)
