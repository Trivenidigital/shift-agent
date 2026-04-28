# Plan v2: Sender Identity Context Injection (post-review)

This supersedes `01-PLAN.md`. All Critical and High review findings from
`01-PLAN-REVIEW-NOTES.md` are incorporated. Items not adopted are listed at
the end of the review-notes file with rationale.

## Problem (unchanged)

Hermes does not pass per-message sender metadata to the LLM. Skills cannot
reliably resolve the sender to a roster employee. Free-form messages
(`"fever cant come tomorrow"`) fall through. WhatsApp's modern LID-based
chat IDs are not in the roster's lookup space.

## Architecture

Three pieces, each independently shippable, each behind a feature flag:

```
┌────────────────────┐    inbound JSON   ┌─────────────────────────┐
│ bridge.js (Node)   │ ────────────────► │ Hermes Python gateway   │
│                    │                   │                          │
│ + writes           │                   │ _prepare_inbound_message_│
│   lid-cache.json   │                   │   text  (line ~3908)     │
│   (atomic write)   │                   │                          │
└────────────────────┘                   │  PATCH: prepend          │
                                         │   [shift-agent-sender …] │
                                         └─────────────────────────┘
                                                    │
┌────────────────────┐    cron 5min      ┌──────────▼──────────────┐
│ lid-cache.json     │ ────────────────► │ shift-agent-lid-learn   │
└────────────────────┘                   │ updates roster.json     │
                                         └─────────────────────────┘
```

### Piece 1 — Hermes patch in `_prepare_inbound_message_text`

**File:** `/root/.hermes/hermes-agent/gateway/run.py` lines ~3886-3920.
**Pattern source:** Hermes already prepends `[{user_name}] {message_text}`
for shared multi-user sessions on line 3908. We extend this with the
sender block.

**Helper:** `_resolve_sender_context(event, config) -> dict` placed in
`gateway/platforms/whatsapp.py` (returns a dict for clean shape; `_render_sender_context_block(d) -> str` is a thin renderer that converts it to the
text block we prepend). This separation is a deliberate
upstream-contribution shape (L1 in review notes).

**Block format (v=1, validated):**
```
[shift-agent-sender v=1 platform=whatsapp phone="+17329837841" lid="201975216009469@lid" fromMe=true chat_id="918522041562@s.whatsapp.net"]
<original message body>
```

- `v=1` first key, mandatory. Skills assert this.
- All values quoted. The renderer validates each value against an explicit
  regex before formatting; if invalid, that key is omitted (e.g., LID
  unknown → `lid=null`).
- Helper sanitizes the user body: any pre-existing occurrence of
  `[shift-agent-sender ` is replaced with `[shift-agent-sender-stripped `
  before the trusted block is prepended. Closes prompt-injection vector
  (C2).
- Helper is a no-op when `HERMES_INJECT_SENDER_CONTEXT=0` or unset.

**Patch markers:** every modified region wrapped in
`# BEGIN shift-agent-sender-id` / `# END shift-agent-sender-id`. A
`tools/check-shift-agent-patch.sh` script:
1. Verifies the markers are present at expected files.
2. Verifies the marker line is within ±10 lines of an anchor symbol
   (`_prepare_inbound_message_text` for `run.py`, `_build_message_event`
   for `whatsapp.py`).
3. Stores the expected Hermes `__version__` in
   `tools/hermes-patch-baseline.txt`. Compares on run; warns on drift.
4. **Exits non-zero on any failure**. Hooked into `deploy.sh` as a
   precondition.

### Piece 2 — `identify-sender` accepts LID

**File:** `src/scripts/identify-sender`.

Input dispatcher: explicit, never coincidental.

```
input ends with "@lid"  → parse as LID
input ends with "@s.whatsapp.net" → strip suffix; parse as phone
input matches /^\+/ → parse as phone
else → exit 2 (invalid input)
```

LID lookup: the employee whose `roster.json:employees[*].lid == <input>`.
Owner LID lookup: `config.yaml:owner.lid`.

If LID is structurally valid but no roster match, exit 0 with
`{"role":"unknown", "phone_normalized": null, "lid": "<input>"}`.

Output schema preserved + extended with optional `lid`:
```json
{
  "role": "owner|employee|unknown",
  "name": "...",                 // when known
  "employee_id": "e004",         // when employee
  "phone_normalized": "+...",    // when known
  "lid": "<digits>@lid"          // when known
}
```

### Piece 3 — Auto-learn LID via bridge → cache → cron

**File 1:** `bridge.js`. On every inbound (and on `creds.update` for the
paired account), write/refresh `lid-cache.json`. Atomic via tmp+rename
(no flock dance with Python). Gated by `WHATSAPP_LID_CACHE_WRITE=1`.

**File 2:** `lid-cache.json` (state file at
`/opt/shift-agent/state/lid-cache.json`).

```json
{
  "schema_version": 1,
  "pairs": [
    {"phone": "+17329837841", "lid": "201975216009469@lid", "learned_ts": "2026-04-28T03:05:00+00:00"}
  ]
}
```

**File 3:** `src/scripts/shift-agent-lid-learn`. Cron-driven (5min).
- Reads cache, validates `schema_version=1`. Mismatch → exit 5, alert.
- Acquires the **same lock target** as `roster_session()` (i.e., the roster
  file path itself, per `safe_io.flock(roster_path)`).
- For each pair: find employee whose `phone == pair.phone` OR
  `pair.phone in phone_history`. If found:
  - if `employee.lid` is null or differs from `pair.lid`, update.
  - log `LidLearned {employee_id, phone, old_lid, new_lid, ts}` to
    `decisions.log` (NDJSON).
- Owner: same logic against `config.owner.phone`. Updates
  `config.yaml:owner.lid` under the existing `roster_session`/
  `save_config` lock pattern.
- Idempotent: rewrites the same value as a no-op (no log spam).
- After successful application: optionally trim or reset `lid-cache.json`
  to retain only entries that didn't apply (we keep them around so
  out-of-order writes still resolve).

**File 4:** `web/deploy/jobs/shift-agent-lid-learn.cron`:
```
SHELL=/bin/sh
PATH=/usr/bin:/bin
PYTHONPATH=/opt/shift-agent
MAILTO=root
*/5 * * * * shift-agent /opt/shift-agent/venv/bin/python3 /usr/local/bin/shift-agent-lid-learn >> /opt/shift-agent/logs/lid-learn.log 2>&1
```

**Schema additions** (one PR-level migration):
- `Employee.lid: str | None = None` (and `Employee.model_config = ConfigDict(extra="ignore")` for safe rollback)
- `OwnerConfig.lid: str | None = None`
- `RawInbound.sender_phone: Optional[E164Phone] = None`
  + `RawInbound.sender_lid: Optional[str] = None`
  + `model_validator(mode="after")` requiring at least one set.

## Phased rollout (correct this time)

### Phase A — code lands, all flags off

- Ship Pieces 1, 2, 3 with feature flags:
  - Hermes inject: `HERMES_INJECT_SENDER_CONTEXT` default 0
  - Bridge cache write: `WHATSAPP_LID_CACHE_WRITE` default 0
  - lid-learn cron: not installed yet
- Schema additions ARE active (forward-compatible: `lid` optional).
- `identify-sender` accepts LID inputs but no roster has `lid` populated yet,
  so all LID inputs return `role=unknown`. No behavior regression.
- pytest E2E suite runs unchanged.

### Phase B — turn on Shift Agent

In `/root/.hermes/.env` and `/opt/shift-agent/.env`:
- `HERMES_INJECT_SENDER_CONTEXT=1`
- `WHATSAPP_LID_CACHE_WRITE=1`

Update `dispatch_shift_agent/SKILL.md` and `handle_sick_call/SKILL.md`
**atomically** with the env flag flip (`deploy.sh` does both in one
transaction):
- Dispatcher parses the v=1 block; if absent or v unsupported, treat as
  unknown sender (fail-closed).
- Dispatcher: phone or LID resolves to employee/owner via identify-sender.
- handle_sick_call greets by `name` from identify-sender (NOT WhatsApp
  display name).

Restart hermes-gateway. Validate with autonomous E2E test.

### Phase C — install cron (auto-learn LIDs)

After Phase B has run for one verified inbound:
- Install `lid-learn.cron` to `/etc/cron.d/`.
- First cron tick reads the lid-cache populated by the bridge since the env
  flag flipped, applies to roster.
- Subsequent inbounds resolve via roster lookup directly.

## Test plan (expanded)

### New unit / pytest tests

- `test_identify_sender_lid_input.py`
  - Roster has `lid` set → LID input resolves.
  - Roster has `lid` unset → LID input returns `unknown`.
  - Phone input still works (regression).
  - Garbage like `srini` returns exit 2.
  - Phone with `@s.whatsapp.net` suffix is stripped and resolved.
- `test_inject_sender_context_format.py`
  - Helper produces v=1 block with quoted values.
  - Pre-existing `[shift-agent-sender ` in user body is replaced with
    `[shift-agent-sender-stripped `.
  - Both phone and LID null → block emitted with both `null`; the dispatcher
    test ensures unknown-sender path is taken.
- `test_lid_learn_roster_update.py`
  - Cache → roster: lid populated.
  - Cache with conflicting LID for same phone (re-pair) → roster updated,
    audit log entry written.
  - Cache for unknown phone → no roster mutation.
  - schema_version mismatch → exit 5, no mutation.
- `test_e2e_sender_id_context.py`
  - Full lifecycle: synthetic inbound w/ LID-only sender → identify →
    create-proposal → owner DM → coverage send → accept.
- Existing `test_e2e_proposal_lifecycle.py` and `test_safe_io.py` /
  `test_schemas.py` continue to pass unchanged.

### Live autonomous test (Phase D)

After deploy, the autonomous test injects a synthetic inbound matching real
bridge JSON shape (with `fromMe=false`, `senderId=<lid>`, `senderPhone=null`)
and asserts:

- A new proposal P-id is created with `absent_employee_id=e004`.
- Owner DM is sent to owner JID.
- Anjali (the simulated sender) receives ONE outbound message addressing
  her by `name=Anjali Iyer` (per identify-sender), not WhatsApp profile name.
- Outbound filter is clean (no leaks of code/IDs).
- Audit log has `LidLearned` entry, `proposal_created` entry,
  `outbound_sent` entry — no errors.
- Cleanup: cancel the synthetic proposal.

## Rollback plan (corrected)

For each piece, in reverse order:

1. **Phase C off:** remove `/etc/cron.d/shift-agent-lid-learn.cron`.
2. **Phase B off:** set `HERMES_INJECT_SENDER_CONTEXT=0` and
   `WHATSAPP_LID_CACHE_WRITE=0` in env files. Restart hermes-gateway.
3. **Phase A revert:**
   - **Step 3a (REQUIRED before reverting schemas.py):**
     ```
     jq 'del(.employees[].lid)' roster.json > roster.json.tmp
     mv roster.json.tmp roster.json
     yq -i 'del(.owner.lid)' /opt/shift-agent/config.yaml
     ```
   - Remove `Employee.lid`, `OwnerConfig.lid`, `RawInbound.sender_lid` from
     `schemas.py`. Restore `RawInbound.sender_phone` to required. Keep
     `Employee.model_config = ConfigDict(extra="ignore")` if changed —
     even after revert, that's a safer default.
   - Revert `identify-sender` to phone-only.
   - Revert `bridge.js` to pre-cache version (or just leave with flag off
     — the write is gated).
   - Revert `gateway/run.py` and `gateway/platforms/whatsapp.py` patches by
     deleting the BEGIN/END marked blocks.

## File-by-file change list

```
NEW   docs/sender-id-context/01-PLAN-v2.md          this file
NEW   docs/sender-id-context/01-PLAN-REVIEW-NOTES.md review consolidation
NEW   docs/sender-id-context/02-DESIGN.md           phase 2
NEW   docs/sender-id-context/RUNBOOK.md             ops cheat sheet
NEW   tools/check-shift-agent-patch.sh              patch verification
NEW   tools/hermes-patch-baseline.txt               expected Hermes __version__

MOD   src/schemas.py
        + Employee.lid (Optional[str])
        + Employee.model_config extra=ignore
        + OwnerConfig.lid (Optional[str])
        + RawInbound.sender_phone Optional + sender_lid Optional + validator

MOD   src/scripts/identify-sender                   LID input dispatch + lid output

NEW   src/scripts/shift-agent-lid-learn             cache → roster apply

MOD   /root/.hermes/hermes-agent/gateway/platforms/whatsapp.py
        + _resolve_sender_context()
        + _render_sender_context_block()
        BEGIN/END markers

MOD   /root/.hermes/hermes-agent/gateway/run.py
        + extend _prepare_inbound_message_text (line ~3908)
        BEGIN/END markers, feature-flag gated

MOD   /root/.hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js
        + queued event includes fromMe, senderPhone, senderLid
        + writes lid-cache.json (gated by WHATSAPP_LID_CACHE_WRITE)
        BEGIN/END markers

MOD   skills/dispatch_shift_agent/SKILL.md
        parse v=1 block (fail-closed if absent)
        cross-check phone == owner.phone for fromMe (anti-spoof)
        pass phone/LID to handlers as named inputs

MOD   skills/handle_sick_call/SKILL.md
        use phone/LID from dispatch, never message text
        greet by identify-sender name

NEW   web/deploy/jobs/shift-agent-lid-learn.cron    cron entry
MOD   web/deploy/deploy.sh                          install lid-learn + cron + run patch verifier

NEW   tests/test_identify_sender_lid_input.py
NEW   tests/test_inject_sender_context_format.py
NEW   tests/test_lid_learn_roster_update.py
NEW   tests/test_e2e_sender_id_context.py
```

## Acceptance checklist (binding)

- [ ] All 4 new pytest tests pass.
- [ ] All existing pytest tests pass unchanged.
- [ ] `tools/check-shift-agent-patch.sh` exits 0 in clean state and exits
      non-zero when markers are missing or anchor drifts.
- [ ] Live autonomous E2E test passes from a fresh roster (no cached LID
      initially), then re-runs and the second pass uses the cached LID
      with no Kimi clarification.
- [ ] WHATSAPP_OUTBOUND_FILTER continues to pass v3 cases.
- [ ] Cockpit dashboard test (17/17) continues to pass.
- [ ] No new sudo / capability requirements for the cockpit unit.
- [ ] Rollback runbook is committed and dry-run-tested.
- [ ] All schema migrations are forward+backward compatible (rollback step
      strips lid before revert).
