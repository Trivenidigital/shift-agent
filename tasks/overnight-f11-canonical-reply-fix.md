# F11 — Bridge canonical-reply fix (override `WHATSAPP_CANONICAL_REPLY` env)

## What it fixes

Production incident 2026-05-01 12:37 UTC. Customer at +19802005023 sent
a catering inquiry; received "Got it. Take care, we'll handle the shift."
as the only response. NO catering ack, NO catering lead.

## Root cause (smoking gun)

`/root/.hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js:67-68`:

```javascript
const CANONICAL_REPLY = process.env.WHATSAPP_CANONICAL_REPLY ||
  "Got it. Take care, we'll handle the shift.";
```

The default `CANONICAL_REPLY` is shift-themed. It's used by the bridge's
`filterOutbound()` function when an outbound message matches a
`FORBIDDEN_PATTERNS` entry (approval_code, proposal_id, internal_emp_id,
etc.) — the LLM-leaky message gets REWRITTEN to this canonical reply.

In the 12:37 trace, bridge.log shows:
```
[FILTER] Rewrote outbound to 269612545511591@lid: reason=pattern:approval_code 352->42 chars
```

The 352-char outbound was the LLM trying to send an owner card (with
`#XXXXX` code) to the customer chat — wrong target, but valid mechanic.
Bridge rewrote to 42 chars = length of the default shift canonical.

## Fix

Set `WHATSAPP_CANONICAL_REPLY` env var in `/root/.hermes/.env` to a
neutral, catering-friendly fallback:

```
WHATSAPP_CANONICAL_REPLY=Thank you — your message has been received. We will follow up shortly.
```

Bridge.js reads this at module-load (constant), so a gateway restart is
required. Done as part of F11 deploy.

## Verification

Synthetic test via `curl POST http://127.0.0.1:3000/send`:
- Input: `"Code is #ABCDE here."` (20 chars, matches approval_code regex)
- Bridge log: `[FILTER] Rewrote outbound to 15559998888@s.whatsapp.net: reason=pattern:approval_code 20->70 chars`
- 70 chars = length of new canonical reply ✅

## Followups (deferred)

- The bridge canonical-reply is global (one string for all chats). Ideally
  it would be context-aware (different per customer/employee/owner). That
  requires bridge.js per-chat role lookup — substrate change. Out of scope.
- The underlying issue — LLM trying to send owner cards to customer chats
  — is addressed by F10 (parse_catering_inquiry SKILL hard rules) +
  F8 watchdog (deterministic owner-action path).
