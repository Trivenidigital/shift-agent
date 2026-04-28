# Plan: Sender Identity Context Injection (Option 3)

## Problem statement

The Shift Agent runs on top of Hermes, an agent platform. When an inbound
WhatsApp message reaches Hermes, it is forwarded to the Kimi LLM as a plain
user-text turn with **no sender metadata** in the prompt. Today's symptoms:

1. **Wrong-name greeting**: An employee whose WhatsApp profile name is "Srini"
   sends a sick-call. Kimi greets them "Got it, Srini" even though their roster
   record says `Anjali Iyer`. Reason: Kimi infers the sender's name from the
   single-user persona system prompt, which mentions "Srini" repeatedly.
2. **No proposal created**: Kimi cannot reliably resolve the sender to a roster
   employee, so it short-circuits the `handle_sick_call` flow at the
   acknowledgment step. No `create-proposal` call, no owner DM, no coverage offer.
3. **LID-only chats are unidentifiable**: WhatsApp now uses LID-based JIDs
   (`<digits>@lid`) for privacy. The `identify-sender` script only accepts
   E.164 phones. There is no LID→roster lookup.

The phone/LID information **does exist** in the Baileys envelope reaching the
WhatsApp bridge — but it is dropped before Kimi sees the message.

## Root cause

Hermes was built as a **single-user agent**: one Hermes account == one human
user, the agent's persona file describes that user, and the model is expected
to know "the user" without per-message metadata.

Shift Agent inverts this: **one Hermes account serves many roster employees**
plus an owner. Each inbound DM may come from a different person. The platform
must tell the model who sent each message; the model cannot guess.

## Goal (success criteria)

After this change:

1. Kimi receives, on every inbound, a structured sender block in the user
   message context. Format is owned by Hermes (one place to fix), not by SKILL
   prose.
2. `identify-sender` accepts both phone (`+E.164`) and LID (`<digits>@lid`)
   inputs and returns the same roster record.
3. The roster auto-learns each employee's LID the first time their phone
   messages the bridge — no manual roster editing required.
4. Free-form messages with no name in body (e.g., `"fever cant come tomorrow"`)
   correctly identify the sender by phone/LID and proceed through the full
   `handle_sick_call` lifecycle.
5. Existing `identify-sender +<phone>` callers keep working; no regression in
   the pytest E2E suite or the scripted lifecycle test.
6. Survives a Hermes upstream upgrade with a clear, isolated, well-marked
   patch block (so it can be re-applied or contributed back).

## Out of scope

- Multi-language Kimi prompt rewrites.
- Schedule/Roster data-model overhaul.
- Migration of the cockpit frontend.
- General Hermes refactor.

## Architecture — three pieces, each independently small

### Piece 1 — Hermes patch: `inject_sender_context`

**File**: `/root/.hermes/hermes-agent/gateway/run.py` (and `session_context.py`
if needed for clean integration).

**What it does**: when a WhatsApp `MessageEvent` is built, attach a
deterministic `[shift-agent-sender ...]` block to the user message body. This
block carries the platform-resolved metadata — phone JID, LID, fromMe flag —
that Kimi needs in order to call `identify-sender` correctly. The block is a
single line, prefixed/suffixed with delimiters so SKILL.md can describe how to
parse it without regex tricks.

Format (plain text, ASCII only, single line):

```
[shift-agent-sender platform=whatsapp phone=+17329837841 lid=201975216009469@lid fromMe=true chat_id=918522041562@s.whatsapp.net]
<original message body>
```

**Properties**:
- Always ASCII so it never breaks log/encoding paths.
- Single line so it cannot be confused with prose.
- Distinct prefix `[shift-agent-sender ` so it is grep-able.
- Phone is the E.164-normalized form when Baileys can resolve it; LID is the
  raw `<digits>@lid` form. If only one is available, the other is `null`.
- `fromMe=true|false` so the dispatch skill can short-circuit owner/self-chat
  routing without re-deriving.
- Block is added by Hermes ONLY when `gateway_inject_sender_context: true` in
  config (or env override `HERMES_INJECT_SENDER_CONTEXT=1`), so the change is
  off by default for non-shift deployments.

**Code location**: a small helper `_inject_sender_context_prefix(event,
config) -> str | None` placed near `_build_message_event` in
`gateway/platforms/whatsapp.py`. Called once from `gateway/run.py` at the same
site that builds the user prompt for the agent (around line ~4084 where
`build_session_context_prompt` is invoked).

**Marker comment**: every modified line is wrapped in
`# BEGIN shift-agent / END shift-agent` markers so the patch can be re-applied
on Hermes upgrades, located, removed, or contributed upstream as a feature
flag.

### Piece 2 — `identify-sender` accepts LID input

**File**: `/opt/shift-agent/working/scripts/identify-sender` (deployed to
`/usr/local/bin/identify-sender`).

**What changes**:
- Add input parser: accepts `+<digits>` (E.164), `<digits>@s.whatsapp.net`
  (legacy phone-JID), and `<digits>@lid` (LID).
- For LID inputs, look up the employee whose roster record has
  `lid: "<digits>@lid"`. If no employee has that LID stored yet, fall back to
  `role=unknown` (current behavior). The new "auto-learn" piece (Piece 3)
  populates the LID, so a second message from the same employee will resolve.
- Owner LID resolution: same lookup against `config.yaml:owner.lid` (new
  optional field).
- All existing exit codes preserved (0 ok, 2 invalid input). New: exit 0 with
  `role=unknown` (not error) when the LID is structurally valid but not yet
  in roster — auto-learn handles backfill.

**Output schema preserved**: `{"role": "...", "employee_id": "...", "name":
"...", "phone_normalized": "...", "lid": "..."}` with `lid` newly populated
when known.

### Piece 3 — Roster LID auto-learn

**Where**: bridge-side, in `/root/.hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js`.

**What it does**: on every inbound, Baileys gives us BOTH the phone and the
LID for the sender (via `participant`/`participantAlt` or the in-memory
`lidMapping`). The bridge writes `(phone, lid)` pairs to a small JSON cache
file (`/opt/shift-agent/state/lid-cache.json`) — phone-keyed.

A separate small Python helper
`/usr/local/bin/shift-agent-lid-learn` runs from the bridge cache → roster
update at a steady cadence (or on-demand). It:

1. Reads `lid-cache.json`.
2. For each `(phone, lid)` pair, finds the employee whose `phone` matches
   (or whose `phone_history` contains it) and writes `lid` into the roster
   under `flock`. If no match, leaves it (unknown sender — possibly a future
   spammer).
3. Re-validates roster against schema; aborts atomically on any error.
4. Writes a row to `decisions.log` for auditability.

**Schema additions** (`schemas.py`):

```python
class Employee(BaseModel):
    ...
    phone: str
    phone_history: list[PhoneAssignment] = []
    lid: str | None = None       # NEW: <digits>@lid, auto-learned
```

**Owner** (config.yaml schema):

```python
class OwnerConfig(BaseModel):
    name: str
    phone: str
    self_chat_jid: str
    lid: str | None = None       # NEW: optional, auto-learned
```

The `lid-learn` helper handles owner LID via `config.owner.phone` match.

## Phased rollout

### Phase A — code changes (off-by-default)

- Piece 1 lands behind `gateway_inject_sender_context: false` (default).
- Piece 2 lands as a pure superset: existing phone inputs still work.
- Piece 3 lands but does nothing if config flag is off.

This phase is safe to ship on its own — zero behavior change in production.

### Phase B — turn on for Shift Agent

- Set `HERMES_INJECT_SENDER_CONTEXT=1` in `/root/.hermes/.env`.
- Update `dispatch_shift_agent/SKILL.md` and `handle_sick_call/SKILL.md` to
  parse the new sender block instead of guessing.
- Restart hermes-gateway.
- Validate via the autonomous E2E test (below).

### Phase C — auto-learn cron

- Add a cron entry that runs `shift-agent-lid-learn` every 5 minutes.
- First run after deploy seeds LIDs from any cached pairs the bridge has
  written since restart. Manual `lid` edits are preserved (the helper only
  writes when the field is `null` or differs).

## Test plan

### Unit / pytest tests (new + updated)

- `test_identify_sender_lid_input.py` — feed `<digits>@lid`, expect roster hit
  when LID is in roster, `role=unknown` when not.
- `test_identify_sender_phone_still_works.py` — regression: existing E.164
  inputs unchanged.
- `test_lid_learn_roster_update.py` — given a `lid-cache.json` and a roster
  matching by phone, `shift-agent-lid-learn` populates the `lid` field
  atomically and idempotently.
- `test_inject_sender_context_format.py` — given a fake `MessageEvent` with
  phone/LID/fromMe set, the helper emits the exact one-line block format.

### End-to-end (existing harness)

- `tests/test_e2e_proposal_lifecycle.py` runs unchanged (phone-based callers
  still work).
- A new `tests/test_e2e_sender_id_context.py` runs the full lifecycle using
  the LID-only path (so we know the new code path works on its own).

### Live autonomous test (final phase)

After deploy, an autonomous test that:

1. Starts from clean state (no in-flight proposals).
2. Synthesizes an inbound `MessageEvent` with phone + LID set, body
   `"fever cant come tomorrow"` and no name in text. Routes through Hermes
   exactly as the bridge would.
3. Asserts: P0014 (or next free P-id) is created with
   `absent_employee_id=e004`. Owner DM goes to owner JID. Outbound filter
   stays clean (no leaks). Anjali receives ONE message and it greets her by
   the roster name (`"Got it, Anjali."`), not the WhatsApp profile name.
4. Cleans up by cancelling the synthetic proposal.

## Rollback plan

- Piece 1: revert by setting `HERMES_INJECT_SENDER_CONTEXT=0` and restart
  Hermes. The patched code paths are no-ops when the flag is off.
- Piece 2: `identify-sender` accepts the new inputs as a superset; rollback
  is just reverting the script. Old callers keep working.
- Piece 3: stop the cron, revert `schemas.py`. Existing `lid` fields in
  roster are ignored by the unmodified schema (Pydantic by default ignores
  unknown fields unless `extra="forbid"` — confirm before rollback).

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Hermes upstream renames `_build_message_event` between versions | Patch blocks are wrapped in BEGIN/END markers; `tools/check-shift-agent-patch.sh` greps for the markers and warns post-upgrade. |
| Baileys' LID resolution is incomplete for new contacts | Fall back to phone-only; flag in audit log. Auto-learn picks up on subsequent messages. |
| Owner re-pairs to a different phone after LID is learned | The `lid-learn` helper detects phone change and clears the stale LID. Add explicit clear-on-mismatch step. |
| Multiple employees share a phone (test artifact) | Roster validation already forbids duplicate phones. Auto-learn is phone-keyed → deterministic. |
| Race: bridge writes lid-cache while learner reads | Both use `flock` on `lid-cache.json.lock`; learner is idempotent. |
| Privacy: LID is leaked in audit log | LID is non-sensitive (it's a public identifier on the WhatsApp wire). Phone is more sensitive and continues to use the existing redaction layer. |

## Acceptance checklist

- [ ] Hermes patch applies cleanly to current `/root/.hermes/hermes-agent/`
      with all BEGIN/END markers visible.
- [ ] `pytest tests/` passes, including the 4 new tests.
- [ ] `tests/test_e2e_proposal_lifecycle.py` continues to pass unchanged.
- [ ] Live autonomous test passes from a fresh roster (no LID cached
      initially), then re-runs and the second iteration uses the cached LID.
- [ ] `WHATSAPP_OUTBOUND_FILTER` continues to pass all 5 v3 cases.
- [ ] No new sudo / capability requirements for the cockpit unit.
- [ ] Backup plan and runbook entry are added.

## File-by-file change list (preview)

```
NEW   docs/sender-id-context/01-PLAN.md            this file
NEW   docs/sender-id-context/02-DESIGN.md          phase 2
NEW   docs/sender-id-context/RUNBOOK.md            ops cheat sheet
MOD   src/schemas.py                               +lid on Employee, +lid on OwnerConfig
MOD   src/scripts/identify-sender                  LID input handling
NEW   src/scripts/shift-agent-lid-learn            lid-cache.json -> roster.json
NEW   src/scripts/inject-sender-context-test       harness for inject helper
MOD   /root/.hermes/hermes-agent/gateway/platforms/whatsapp.py   _inject_sender_context_prefix
MOD   /root/.hermes/hermes-agent/gateway/run.py                  call site under feature flag
MOD   skills/dispatch_shift_agent/SKILL.md         parse the new block
MOD   skills/handle_sick_call/SKILL.md             use phone/LID from block, not message text
MOD   /root/.hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js  write lid-cache.json
NEW   tests/test_identify_sender_lid_input.py
NEW   tests/test_lid_learn_roster_update.py
NEW   tests/test_inject_sender_context_format.py
NEW   tests/test_e2e_sender_id_context.py
NEW   web/deploy/jobs/lid-learn.cron               5-minute cron
MOD   web/deploy/deploy.sh                         install lid-learn script + cron
```

## Timeline (autonomous overnight)

1. Plan review (5 parallel agents) → fixes — ~25 min.
2. Design (file-by-file with exact diffs) → review (5 parallel agents) →
   fixes — ~50 min.
3. Build (apply changes per design) — ~50 min.
4. PR open + automated tests → review (5 parallel agents) → fixes — ~40 min.
5. Merge → deploy → autonomous E2E — ~25 min.

Total: ~3.5 hours autonomous. No human input required after kickoff.
