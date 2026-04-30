# PR-D3 — Absorbing shim for v0.4 forward-compat fields

**Drift-check tag:** `extends-Hermes`

**Pipeline position:** PR-D3 (this) → 24h soak → PR-B1 → 24h soak → PR-B2 → 90-min canary → bulk deploy.

**Why this PR exists:** PR-B 5-agent design review (Reviewer 2 / schema lens, 2026-04-29) found that plan v2 §B-1's "PR-B1 has no writers" rationale is structurally wrong. `safe_io.atomic_write_json` calls `model_dump_json()` (no `exclude_defaults`/`exclude_none`). Every PR-B1 read→write at five callsites (`apply-catering-owner-decision:442/497/685`, `catering-lead-reconcile:151`, `create-catering-lead:462`) round-trips the full store, materializing the new fields even when the touching script never set them. PR-D2-binary then crashes on read (`CateringLead.extra="forbid"` at schemas.py:515; `CustomerConfig.extra="forbid"` at schemas.py:317).

**Resolution:** ship a `mode='before'` validator on `CateringLead` and `CustomerConfig` that strips the four known-future-keys on read. Lands on PR-D2-line. After 24h soak, PR-B1 writes are absorbed cleanly by any rollback to PR-D3.

This is the same ladder shape as PR-D1 (schema infra) → PR-D2 (writers): infra first, writers second.

---

## Read-deployed-code evidence

Line numbers below are from the pre-PR-D3 (main HEAD) state of the file.
Post-PR-D3, line numbers shift down ~30-40 lines due to the constants /
helper / shim insertions.

| File | Confirmed |
|---|---|
| `src/platform/schemas.py:515` (pre-shim) / `:553` (post-shim) | `CateringLead.model_config = ConfigDict(extra="forbid")` |
| `src/platform/schemas.py:317` (pre-shim) / `:343` (post-shim) | `CustomerConfig.model_config = ConfigDict(extra="forbid")` |
| `src/platform/schemas.py:540` (pre-shim) / `:593` (post-shim) | `_backfill_legacy_quote_text` `mode='before'` validator — exact precedent |
| `src/platform/safe_io.py:226-229` | `atomic_write_json` calls `obj.model_dump_json(indent=2)` — no exclude_defaults |
| `src/agents/catering/scripts/apply-catering-owner-decision:442,497,685` | three `atomic_write_json(LEADS_PATH, store)` callsites |
| `src/agents/catering/scripts/catering-lead-reconcile:151` | round-trips store on reconcile |
| `src/agents/catering/scripts/create-catering-lead:462` | round-trips store on create |

---

## Design (single commit)

```python
# src/platform/schemas.py — additions

# Top-level constant near LEGACY_QUOTE_TEXT_SENTINEL declaration:
_PR_B_RESERVED_LEAD_KEYS = frozenset({"voice_quality", "quote_source"})
_PR_B_RESERVED_CONFIG_KEYS = frozenset({"tone_profile", "tone_examples"})


# Inside CateringLead, alongside _backfill_legacy_quote_text:
@model_validator(mode="before")
@classmethod
def _strip_pr_b_reserved_keys(cls, data: Any) -> Any:
    """PR-D3: forward-compat absorption of v0.4 PR-B1 fields.
    Strips reserved keys on read so a future PR-B1 binary's writes
    round-trip safely on this PR-D3-line binary. Logs at WARN once
    per key per process (not per-call) to flag silent data loss
    during a rollback window.
    """
    if not isinstance(data, dict):
        return data
    for key in _PR_B_RESERVED_LEAD_KEYS:
        if key in data:
            _warn_pr_b_reserved_key_once("CateringLead", key)
            data.pop(key, None)
    return data


# Inside CustomerConfig, near top of class body:
@model_validator(mode="before")
@classmethod
def _strip_pr_b_reserved_keys(cls, data: Any) -> Any:
    """PR-D3: forward-compat absorption — see CateringLead docstring."""
    if not isinstance(data, dict):
        return data
    for key in _PR_B_RESERVED_CONFIG_KEYS:
        if key in data:
            _warn_pr_b_reserved_key_once("CustomerConfig", key)
            data.pop(key, None)
    return data


# Module-level helper (top of schemas.py, near other state):
_PR_B_WARNED: set[tuple[str, str]] = set()
def _warn_pr_b_reserved_key_once(model_name: str, key: str) -> None:
    pair = (model_name, key)
    if pair in _PR_B_WARNED:
        return
    _PR_B_WARNED.add(pair)
    sys.stderr.write(
        f"WARN: PR-D3 absorbing-shim stripped {key!r} from {model_name} on read "
        f"(rollback window from PR-B1+ to PR-D3). Once-per-process; subsequent "
        f"strips silent.\n"
    )
```

**Behavior:**
- PR-D3 binary reading PR-D2-written data: no reserved keys present → shim no-op. Existing 433 tests pass unchanged.
- PR-D3 binary reading future PR-B1-written data (after rollback): reserved keys stripped silently after first WARN per key.
- PR-D3 binary writing data: never sets the reserved keys (PR-D3 has no writer logic for them) → on-disk JSON unchanged from PR-D2 shape.

**Why a once-per-process WARN, not per-call:** every read after rollback would otherwise spam stderr. Once is enough to alert the operator; subsequent strips are observed via `decisions.log` ratio of pre/post-rollback writes.

---

## Test plan (synced with shipped tests: 21 total)

Note: shipped count grew from the original 6-case skeleton to 21 cases
after PR review feedback (R2 HIGH gaps + R2 MEDIUMs). All counted in
`tests/test_pr_d3_absorbing_shim.py`.

**Core strip behavior (9 cases):**
- 3 CateringLead: strip voice_quality + strip quote_source + strip both at once
- 3 CustomerConfig: strip tone_profile + strip tone_examples + (round-trip idempotent)
- 3 round-trip / extra-forbid: lead round-trip idempotent, lead extra-forbid, config extra-forbid

**Once-per-process WARN behavior (4 cases):**
- WARN once per (model, key): single-key, separate-key, separate-model, across different lead instances

**End-to-end + post-strip integration (2 cases):**
- post-strip idempotency: load → dump → re-load emits no second WARN
- e2e via `safe_io.atomic_write_json`: round-trip writes a clean JSON file with no reserved keys

**Shape & format pinning (4 cases):**
- value-shape coverage (parameterized: None, "", 42, nested-dict, list)
- case-sensitivity: `VoiceQuality` is NOT stripped (extra-forbid still rejects)
- WARN format greppable: literal "PR-D3 absorbing-shim stripped" in stderr
- WARN goes to stderr only (stdout empty)

**Fixture-drift sentinel (1 case):**
- minimum dicts produce valid models (catches future required-field additions)

**No-mutation contract (3 cases, review #38 MEDIUM):**
- CateringLead validator does not mutate caller's input dict
- CustomerConfig validator does not mutate caller's input dict
- Clean (no-reserved-key) input takes the fast-path and is left untouched

**Regression suite:** full pytest passes with these tests added (was 433 baseline + the new shim tests + the no-mutation pins), zero regressions.

**Test-import convention.** Tests use `from schemas import (...)` directly (bare). The conftest.py at `tests/conftest.py:18-22` puts `src/platform/` on `sys.path`, so this is the canonical project pattern (matches `tests/test_catering_schemas.py:8`). The pseudo-code below shows `from src.platform.schemas import ...` for cross-package readability; the shipped tests use the bare-import canonical form.

(legacy section header retained below for diff readability)

### Original 6-case skeleton (superseded by above)

`tests/test_pr_d3_absorbing_shim.py` (new file):

```python
import pytest
from src.platform.schemas import CateringLead, CustomerConfig

# CateringLead — 3 cases
def test_lead_strips_voice_quality_on_read():
    """Future PR-B1 binary wrote voice_quality; PR-D3 strips it cleanly."""
    raw = {<minimum-valid-CateringLead-fields>, "voice_quality": "good"}
    lead = CateringLead.model_validate(raw)
    assert not hasattr(lead, "voice_quality")
    assert "voice_quality" not in lead.model_dump()

def test_lead_strips_quote_source_on_read():
    raw = {<minimum-valid-CateringLead-fields>, "quote_source": "llm"}
    lead = CateringLead.model_validate(raw)
    assert "quote_source" not in lead.model_dump()

def test_lead_round_trip_idempotent_no_reserved_keys():
    """Reading + writing a clean lead is byte-identical (no shim impact)."""
    raw = {<minimum-valid-CateringLead-fields>}
    lead = CateringLead.model_validate(raw)
    dumped = lead.model_dump(mode="json")
    re_loaded = CateringLead.model_validate(dumped)
    assert re_loaded.model_dump(mode="json") == dumped

# CustomerConfig — 3 cases
def test_config_strips_tone_profile_on_read():
    raw = {<minimum-valid-CustomerConfig-fields>, "tone_profile": {"formality": "casual"}}
    cfg = CustomerConfig.model_validate(raw)
    assert "tone_profile" not in cfg.model_dump()

def test_config_strips_tone_examples_on_read():
    raw = {<minimum-valid-CustomerConfig-fields>, "tone_examples": ["hello"]}
    cfg = CustomerConfig.model_validate(raw)
    assert "tone_examples" not in cfg.model_dump()

def test_config_round_trip_idempotent_no_reserved_keys():
    raw = {<minimum-valid-CustomerConfig-fields>}
    cfg = CustomerConfig.model_validate(raw)
    dumped = cfg.model_dump(mode="json")
    re_loaded = CustomerConfig.model_validate(dumped)
    assert re_loaded.model_dump(mode="json") == dumped
```

Plus 1 integration regression check: full pytest suite (433 existing tests) passes unchanged.

---

## Build sequence (1 commit)

| # | Commit subject | LOC |
|---|---|---|
| 1 | `feat(schemas): PR-D3 absorbing shim — strip v0.4 PR-B reserved keys on read for rollback safety` | ~30 src + 6 tests |

---

## Branching

- **Branch:** `fix/catering-pr-d3-absorbing-shim` cut from main HEAD (`5cfebf6`).
- PR-B branch (`feat/catering-v04-llm-quote`) will rebase onto PR-D3-merged main once it lands.

---

## Deploy plan

1. Build tarball + scp + `shift-agent-deploy.sh` on canary VPS.
2. Pre-restart import gate runs (PR-D1 infrastructure).
3. **24h soak.** Watch for:
   - Pydantic `ValidationError` on `CateringLead` or `CustomerConfig` reads:
     ```bash
     ssh <canary> 'journalctl -u hermes-gateway --since "1 hour ago"' > .soak.txt 2>&1
     # then locally:
     grep -E "ValidationError.*(CateringLead|CustomerConfig)" .soak.txt | wc -l
     # expected: 0
     ```
   - WARN line `"PR-D3 absorbing-shim stripped"` — should NOT appear during soak (no PR-B1 writes exist yet); appearance = unexpected data shape.
4. Bulk-deploy 8 non-canary VPS via `tools/canary-bulk-deploy.sh` after canary clears.

### Observability honest-statement (review #38 LOW)

A previous draft of this doc claimed "subsequent strips are observed via `decisions.log` ratio of pre/post-rollback writes." That is **not actually backed by the code** — the shim writes only `sys.stderr` (one WARN per `(model, key)` pair per process). No `_BaseEntry` LogEntry variant is emitted on strip. Adding one would mean writing an audit row from a `mode='before'` Pydantic validator that runs on every model load — high-cardinality and invasive for an absorbing shim that should be quiet.

**Correct observability statement:** strip events are visible **only** in per-VPS journald via the WARN line `"PR-D3 absorbing-shim stripped"`. After the first WARN per `(model, key)` per process, subsequent strips are silent within that process; new processes (e.g., separate script invocations) re-emit the first WARN. Operator detection on rollback = grep journald on each VPS, not central audit-log query.

**Future improvement (out of scope for PR-D3):** if operator detection across the fleet becomes load-bearing, add a `PrBReservedKeyAbsorbed` LogEntry variant emitted at most once per process via the same memo set. Defer until a real operational need; the soak window is the only relevant detection moment, and per-VPS journald grep is sufficient there.

---

## Removal plan (review #38 LOW)

The shim is by nature temporary. After PR-B1 + PR-B2 ship and PR-B2 has soaked cleanly across the fleet, the shim becomes dead code that:
1. Silently strips legitimately-named future fields if anyone reuses these key names
2. Adds two `mode='before'` passes on every model load forever (negligible cost, but unnecessary)

**Removal trigger:** **after PR-B2 has soaked 7 days post-bulk-deploy across all 9 Triveni VPS**, with zero `"PR-D3 absorbing-shim stripped"` WARN lines in journald during the 7-day window (rollback window has closed). At that point: open `tasks/pr-d4-shim-removal.md` (single commit, deletes the two validators + the four module-level constants + the WARN helper + the absorbing-shim tests; full regression test).

**Tracking:** create a calendar reminder when PR-B2 bulk-deploy completes ("7 days from today: open PR-D4 to remove the PR-D3 absorbing shim"). The `/schedule` tooling can carry this; alternatively land a `# TODO(remove-after-pr-b2-soak-7d)` comment next to the helper to surface during code review until removed.

**Why a removal PR rather than leaving the shim:** the steady-state cost is small but the strip-on-read silently absorbing legitimately-named future fields is a real footgun for a future contributor who reuses any of `voice_quality`, `quote_source`, `tone_profile`, `tone_examples` for a new purpose. Removing the shim once it's no longer load-bearing eliminates that surface.

---

## Self-review

- [x] Drift-tag at top.
- [x] Mirrors deployed `_backfill_legacy_quote_text` precedent (schemas.py:540 pre-shim / :593 post-shim).
- [x] No new dependencies.
- [x] No behavior change for clean reads (shim is no-op on PR-D2 data).
- [x] Once-per-process WARN avoids log spam after rollback.
- [x] No `extra="ignore"` flip on `CateringLead`/`CustomerConfig` (would weaken forward-validation; shim is more surgical).
- [x] PR-D3 itself has no writers for reserved keys; on-disk JSON unchanged.

## Status: DESIGN-DRAFTED, ready for build

Skip 5-agent design review for PR-D3: scope is ~30 LOC mirroring a single deployed precedent, no novel architecture. PR review on the open PR provides the gate.
