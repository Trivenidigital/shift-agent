# Plan — Isolated CI/test harness for send-path tests

**Drift-check tag:** extends-Hermes

Strengthens the existing Hermes-aligned send chokepoint (`safe_io.bridge_post` /
`bridge_send_cta` / `bridge_send_media`) with a **test-only** tripwire and adds a
test harness + CI. No production behavior change (tripwire is gated on pytest
context). The `bridge_send_blocked_by_test_context()` guard is kept and only
extended (additive), never weakened.

## Why this exists (context)

2026-05-30 incident: send-path pytest runs with `SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS=1`
on main-vps leaked outbound sends to the **live** WhatsApp bridge, because
`safe_io.BRIDGE_URL` defaults to `http://127.0.0.1:3000/send` (the live bridge) and
the tests only override the now-vestigial *script-module* `BRIDGE_URL`. No real
recipient (fixtures use reserved-fictional 555-01XX numbers) but a real hazard —
`+17329837841` (operator-owned) is a literal JID in `test_send_catering_ack`.
Root enabler: there is **no pytest CI**, so the breakage was invisible.

## Hermes-first capability checklist

| Step | Tag | Note |
|---|---|---|
| 1. Resolve send target URL + detect pytest context | `[Hermes]` | `safe_io.BRIDGE_URL` + existing `bridge_send_blocked_by_test_context()` substrate |
| 2. Tripwire: raise if pytest send targets live bridge | `[net-new]` | additive guard branch in safe_io (~15 LOC) |
| 3. conftest fake-sink autouse default | `[net-new]` | test infra (~15 LOC) |
| 4. Regression tests proving no live-bridge hit | `[net-new]` | ~120 LOC |
| 5. In-process send tests provide `action_context` + stub override | `[net-new]` | edit cta/media tests |
| 6. Linux pytest CI workflow (no live bridge) | `[net-new]` | `.github/workflows/` |

Hermes owns none of repo test tooling/CI — this is infra, correctly `[net-new]`.
`mcp/native-mcp` / ecosystem skills N/A (internal test harness).

## Drift-rule self-checks (read-deployed-code done)

- ✅ Read `src/platform/safe_io.py` (`BRIDGE_URL` from `HERMES_BRIDGE_URL` default `:3000` at line 583, `validate_bridge_url` loopback-only, `bridge_send_blocked_by_test_context()` at line 866, `_enforce_action_context_policy`, 3 send call sites) before drafting the tripwire.
- ✅ Read `src/platform/schemas.py` (`ActionExecutionContext` at line 3181 — `action_id`, `is_regulated_action`, `verified_action_result`, frozen, extra=forbid) before drafting the in-process action_context fixture.
- ✅ Read `tests/conftest.py` (4 fixtures, no autouse bridge fixture) before drafting the fake-sink autouse.
- ✅ Read `tests/test_safe_io_bridge_send_cta.py` (in-process, mocks urlopen, asserts status==sent, only overrides safe_io.BRIDGE_URL) before drafting the point-4 fix.

Deployed-pattern checklist: keeps the JSON/flock audit chokepoint untouched; no
SQLite; tripwire is gated on pytest context so the production audit/send path is
byte-for-byte unchanged.

## Design

1. **`safe_io.py` (additive, test-gated):**
   - `class LiveBridgeSendInTestError(RuntimeError)`.
   - `_is_live_bridge_url(url)` → True if URL port ∈ {3000} (canonical live bridge).
   - `bridge_send_blocked_by_test_context(target_url=None)`: on the
     `SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS=1` branch, if running under pytest AND
     `target_url` is a live bridge → **raise** `LiveBridgeSendInTestError`. All
     other branches unchanged (refuse-by-default preserved).
   - Pass the resolved URL at the 3 call sites (`BRIDGE_URL` / media `url` / cta `url`).
2. **`tests/conftest.py`:** function-autouse `_force_fake_bridge_sink` —
   `monkeypatch.setenv("HERMES_BRIDGE_URL", SINK)` + `setattr(safe_io, "BRIDGE_URL", SINK)`
   where `SINK="http://127.0.0.1:1/__fake_test_sink__"` (closed loopback). Tests that
   capture sends override `safe_io.BRIDGE_URL` to their own stub after.
3. **`tests/test_bridge_send_harness.py` (new regression coverage):** default
   BRIDGE_URL under tests is the sink not :3000; tripwire raises on :3000 (post/cta/media)
   under allow-var+pytest; guard still refuses by default (not weakened); a non-3000
   loopback stub is permitted under allow-var; an in-process send WITH a constructed
   `ActionExecutionContext` to a stub succeeds (point-4 pattern).
4. **In-process send tests:** update `test_safe_io_bridge_send_cta/media` to override
   `safe_io.BRIDGE_URL`→stub + pass `action_context` (point 4).
5. **`.github/workflows/pytest-ci.yml`:** ubuntu, install pydantic+pyyaml+pytest,
   run the harness + curated-green subset (no live bridge in CI → safe). Broader
   subprocess-test repair tracked separately.

## Verification (safe)

Verify the tripwire raises FIRST (no real send — it raises before urlopen), then run
the harness + green subset on the VPS Hermes venv (now safe: sink default + tripwire
backstop mean a misdirected send raises rather than leaking).

## Out of scope (tracked follow-up)

Full repair of the ~8 subprocess send-path test files (catering apply/finalize/proposal,
expense, daily-brief) — they need per-file `safe_io.BRIDGE_URL` stub override. This PR
makes them SAFE to run and establishes the pattern + CI; their green-up is the next batch.
