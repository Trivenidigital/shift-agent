# Agent #32 Special Request Memory v0.1 — read-only lookup + integration (plan)

**Drift-check tag:** `extends-Hermes`

Adds custom infrastructure (config block, schemas, lookup script, SKILL prose
patch) on top of the deployed parse_catering_inquiry Step 0 convention.
Mirrors `lookup-prior-leads-by-phone` exactly — same shape, same status enum,
same fall-open semantics. v0.1 is read-only; write/owner-command deferred to
v0.2.

**Portfolio reference:** `docs/portfolio.md` line 988 (Agent #32 spec) +
build-priority list at line 1099-1101.

**`/hermes-check` receipt:** `tasks/.hermes-check-receipts/agent-32-special-request-memory-v0-1.json`
(timestamp 2026-05-09T17:14:42Z, drift-tag = extends-Hermes, 8 [Hermes] / 4 [net-new]).

**Scope decision (revised after drift-rule survey)**: drop `SpecialRequestLookup`
audit variant from v0.1 scope. The closest-similar `lookup-prior-leads-by-phone`
emits NO audit today (`parse_catering_inquiry/SKILL.md:74-76`: "soak-monitoring
is journald-only; a `lookup_invoked` LogEntry variant is tracked as a P1.4
follow-up"). Matching deployed convention saves ~25 LOC and avoids creating
audit-chain divergence between two parallel lookup scripts. When the P1.4
follow-up adds `lookup_invoked`, BOTH lookups will gain observability
uniformly. Net effect: v0.1 net-new count reduces 4 → 3.

---

## Hermes-first per-step checklist

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | Customer WhatsApp inquiry | `[Hermes]` | Source ingestion |
| 2 | Dispatcher routing | `[Hermes]` | Already deployed |
| 3 | catering_dispatcher → parse_catering_inquiry | `[Hermes]` | Skill chaining |
| 4 | Step 0 lookup-prior-leads-by-phone call | `[Hermes]` | Already deployed |
| 5 | **Step 0 NEW parallel call to lookup-special-request** | **`[net-new]`** | SKILL prose patch (~10 LOC) |
| 6 | **Merge special-request priors into extraction priors** | **`[net-new]`** | SKILL prose addition |
| 7 | Step 1 LLM extraction | `[Hermes]` | LLM gateway capability |
| 8 | create-catering-lead state write | `[Hermes]` | Already deployed |
| 9 | **lookup-special-request CLI script** | **`[net-new]`** | New ~250 LOC mirroring lookup-prior-leads-by-phone |
| 10 | State file hand-edited by operator (v0.1) | `[Hermes]` | safe_io.atomic_write_json convention exists |
| 11 | v0.2 owner WhatsApp command | DEFERRED | Not in scope |

8/11 `[Hermes]`, 3/11 `[net-new]`. Below the 50% red-flag threshold.

**Awesome-hermes-agent ecosystem check:** N/A — per-customer preference
memory is per-customer SMB business logic. The ecosystem productivity skills
covered other domains (Maps, Airtable, Workspace) per `tasks/skills-roadmap.md`;
none provide a per-phone preference store.

---

## Drift-rule self-checks

Per CLAUDE.md Part 3 (schema work + new-script-mirroring + SKILL prose work
+ test work). Files Read this session before drafting:

- ✅ Read `src/agents/catering/scripts/lookup-prior-leads-by-phone` lines 1-180 — pattern to mirror exactly: importable+CLI shape (line 49-83), CONFIG_PATH + LEADS_PATH + LEADS_LOCK constants (lines 85-92), LOCK_RETRY_ATTEMPTS = 3 + LOCK_RETRY_SLEEP_SEC = 1.0 (lines 93-94), LOOKUP_STATUS_* enum (lines 99-104), `_empty_result(status)` shape (lines 107-115), `_canonicalize_phone(raw)` defensive ValidationError-catching (lines 118-131), `_read_leads_with_lock_timeout` lock-aware read with TOCTOU safety + corrupt/oserror status mapping (lines 134-169)
- ✅ Read `src/agents/catering/skills/parse_catering_inquiry/SKILL.md` lines 63-105 — Step 0 convention: "Hard rule" pre-Step-1 ordering, "fall-open by design" semantics, lookup_status enum table, soft-prior merge convention, "Hard rule" about priors never leaking to customer. Confirmed line 74-76 audit-omission pattern (P1.4 follow-up tracker).
- ✅ Read `src/platform/schemas.py` lines 416-437 (DailyBriefConfig precedent), lines 1230-1252 (Config plug-in area), lines 2640-2719 (LogEntry discriminated union — relevant context even though v0.1 drops audit variant)
- ✅ Read `src/platform/safe_io.py` lines 255-309 (`customer_now`, `ndjson_append`, `load_model`); confirmed `customer_now` returns aware datetime
- ✅ Read `tests/test_lookup_prior_leads.py` lines 1-50 — 22-test test pattern mirroring this script: importlib `spec_from_file_location` + `module_from_spec` + `exec_module` (line 36-43), `_seed_leads(env_dir, leads)` helper writing CateringLeadStore JSON (line 46-50). Pattern is directly transferable for `lookup-special-request` tests; will rename helper to `_seed_special_requests`.

**Deployed-pattern compliance:**
- Storage: JSON-on-disk + safe_io atomic writes ✓ (state file follows convention)
- Schemas: pydantic v2 + `extra="forbid"` ✓ (matches DailyBriefConfig / OwnerWellbeingConfig)
- Lock convention: `.lock` sibling pattern ✓ (matches LEADS_LOCK = LEADS_PATH + ".lock")
- Lookup script shape: importable+CLI, status-enum dict return, fall-open ✓ (mirrors lookup-prior-leads-by-phone)
- SKILL.md prose: "Hard rule" framing for Step 0, status-enum table, soft-prior + no-customer-leak rules ✓
- Audit chain: NO audit emit in v0.1 (matches existing lookup-prior-leads-by-phone convention; P1.4 will uniformly add lookup_invoked variant later)
- Test pattern: importlib `spec_from_file_location` + module_from_spec + exec_module + `_seed_*` helper for state file ✓ (mirrors `tests/test_lookup_prior_leads.py`)

---

## Scope boundary (anti-over-engineering)

### In scope (~480 LOC across 4 files + 1 SKILL patch)

| File | Change | LOC |
|---|---|---|
| `src/platform/schemas.py` | Add `SpecialRequestMemoryConfig` (Tier-2 scaffold pattern, default `enabled=False`) + `CustomerPreference` (per-phone preferences) + `SpecialRequestMemoryStore` (the file's outer schema with `customers: list[CustomerPreference]`) + plug into `Config` + exports. **NO audit variant. NO LogEntry union edit.** | ~40 |
| `src/agents/catering/scripts/lookup-special-request` (NEW) | Full mirror of lookup-prior-leads-by-phone: importable+CLI, lock-aware, status-enum return, defensive failure mapping, phone canonicalization | ~250 |
| `src/agents/catering/skills/parse_catering_inquiry/SKILL.md` | Step 0 patch: parallel lookup call + lookup_status table extension + merge convention prose ("merge `preferences` list into `notes` priors as soft signals only; preferences are NOT dietary_restrictions") | ~25 lines added |
| `src/agents/shift/scripts/shift-agent-deploy.sh` | NO change — existing `install -m 755 src/agents/catering/scripts/* /usr/local/bin/` glob at line 202-204 picks up the new script automatically | 0 |
| `tests/test_lookup_special_request.py` (NEW) | Mirror `tests/test_lookup_prior_leads.py` test cases against the new script + store schema | ~150-200 |

### Schema shape (locked at plan time so reviewers can challenge)

```python
class CustomerPreference(BaseModel):
    """Per-customer preference record. Keyed by E.164 phone."""
    model_config = ConfigDict(extra="forbid")
    customer_phone: E164Phone
    preferences: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=500)
    updated_at: datetime


class SpecialRequestMemoryStore(BaseModel):
    """Outer container for the JSON state file."""
    model_config = ConfigDict(extra="forbid")
    customers: list[CustomerPreference] = Field(default_factory=list)
    schema_version: int = 1


class SpecialRequestMemoryConfig(BaseModel):
    """Tier-2 scaffold; default off."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
```

**Lookup return shape** (mirrors lookup-prior-leads-by-phone's dict shape):

```python
def _empty_result(status: str) -> dict:
    return {
        "lookup_status": status,
        "preference_count": 0,
        "preferences": [],
        "notes": "",
        "last_updated_days_ago": None,
    }
```

### Explicitly out of scope (rejected at plan time)

| Considered | Decision | Reason |
|---|---|---|
| `SpecialRequestLookup` audit variant | **DROPPED from v0.1** | lookup-prior-leads-by-phone emits no audit either; matching deployed convention. P1.4 follow-up will add `lookup_invoked` variant for both lookups uniformly. |
| Write script `record-special-request` | **DEFERRED to v0.2** | v0.1 ships read-only; operator hand-edits state file via `safe_io.atomic_write_json` convention if needed. Avoids spec-creep into write-path concerns (idempotency, conflict resolution, owner WhatsApp command parsing). |
| Owner WhatsApp command (e.g. `add prefs +1xxx: no-onion`) | **DEFERRED to v0.2** | Requires new SKILL + dispatcher row + LLM-intent classification. Significant scope; v0.1 proves the read+integrate path first. |
| LLM-driven preference extraction from inquiry text | **DEFERRED to v0.3** | Tempting to auto-detect "no onions please" in catering inquiry and add to memory, but: (a) preferences should be authoritative not inferred, (b) write-path is v0.2 dependency, (c) existing dietary_restrictions extraction already exists. |
| Dict-keyed store (`customers: dict[E164Phone, CustomerPreference]`) | **REJECTED** | List-based store matches `CateringLeadStore.leads: list[CateringLead]` convention. Lookup is O(N) but N is bounded by per-customer scale (single VPS, ~hundreds of customers max). Dict-keying would diverge from deployed JSON-store conventions. |
| Per-customer expiry / TTL on preferences | **DEFERRED** | Premature; weekday filter / expiry logic would require periodic cleanup cron. v0.2+ if a customer asks. |

### Deferred (separate commits if ever needed)

- v0.2: write script + owner WhatsApp command
- v0.2 / v0.3: dispatcher integration with future Order Accuracy (#30) / Kitchen Load Balancer (#31) when those land (POS-blocked today)
- P1.4 follow-up: `lookup_invoked` audit variant for BOTH lookups uniformly
- v0.3: LLM-driven extraction-to-memory
- v0.3: TTL / cleanup cron

---

## Verification + commit shape

- **Run on srilu**: `pytest tests/test_lookup_special_request.py -v` against tarballed working tree
- **Pass criterion**: ~15-18 tests pass on first run; existing `tests/test_lookup_prior_leads.py` still 100% green
- **Commit shape**: ONE commit, message `feat(agent-32): special request memory v0.1 — read-only lookup + parse_catering_inquiry integration`, ~480 LOC across 4 files (no deploy.sh change)
- **Deploy notes**:
  - `shift-agent-deploy.sh:202-204` already runs `install -m 755 src/agents/catering/scripts/* /usr/local/bin/` — script propagates via existing glob
  - `schemas.py` propagates via existing line 42
  - SKILL.md propagates via existing rsync
  - Default config has `special_request_memory.enabled=False`, so deploy is a no-op until owner explicitly opts in AND state file is hand-created
  - Config validation must continue to pass with `special_request_memory` block absent (default_factory pattern; pre-flight test)

---

## Approval needed

Plan reviewers must explicitly approve before design phase. Specific decisions
to challenge:

1. **Dropping `SpecialRequestLookup` audit variant** — matches deployed convention but trades observability for deployed-pattern consistency. Reviewers can flip if observability is judged more important.
2. **Read-only v0.1** — operator hand-edits state file. Some operators may want WhatsApp command immediately. v0.1 ships sooner; v0.2 adds owner command.
3. **List-based store vs dict-keyed** — list matches CateringLeadStore convention; dict would be O(1) lookup but diverges. Reviewers can challenge if performance ever materially matters.
4. **Step 0 SKILL prose merge convention** — preferences merged into `notes` (free-form string) rather than `dietary_restrictions` (closed Literal enum: vegetarian/vegan/jain/halal/kosher/gluten-free). "no-onion" / "extra-spicy" don't fit the dietary enum; they're free-form. Merging into notes is the safe choice. Reviewers can challenge.
5. **Schema `preferences: list[str]` with `max_length=20`** — free-form strings vs enum. Free-form matches the variability of customer requests (e.g., "extra-spicy", "no-cilantro", "lactose-intolerant"). Bounded at 20 to prevent runaway. Reviewers can challenge if a closed enum is preferred for v0.1.
