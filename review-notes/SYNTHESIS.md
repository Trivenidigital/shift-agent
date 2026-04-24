# Plan Review Synthesis — 5 Parallel Agents

**5 reviewers. 9 BLOCKERS across them. Heavy overlap on the worst risks → strong signal.**

## Consensus BLOCKERS (flagged by 2+ reviewers)

| # | Issue | Reviewers | Fix |
|---|---|---|---|
| C1 | Approval tracker ambiguity — flat-log matching breaks with concurrent sick-calls | Architect B1 + Devil R1 | `pending.json` + proposal codes (`#A3F2`). Owner approves by code, not "yes." |
| C2 | `fromMe: true` scope — any outbound from linked device triggers owner-command flow, not just self-chat | Architect M2 + Devil R2 | Dispatcher MUST also verify destination JID = self-chat JID |
| C3 | Self-chat routing unvalidated | Architect open Q + Devil R3 | Explicit test BEFORE build declares complete — self-chat JID round-trip test |

## Single-reviewer BLOCKERS (still block go-live)

| # | Issue | Reviewer | Fix |
|---|---|---|---|
| S1 | No on-call / overnight failure path | SRE B1 | Dead-man's switch: gateway down + business hours → WhatsApp owner "AGENT DOWN, manual mode." 1h |
| S2 | No backups of roster / decisions / WA session | SRE B2, B3 | Nightly `rsync` + `tar \| gpg` to 2nd path or S3. Include `baileys_auth`. 30m |
| S3 | Uncapped outbound send in full-auto | Security B1 | Daily hard cap (10/day for 6-person roster) + template-based message body (not LLM free-text). 1-2h |
| S4 | Employee consent / health data in logs | Security B2 | Customer sends staff "we use an AI assistant" notice pre-go-live + reason codes (health/personal/schedule) instead of raw text. Non-negotiable. |

## Consensus MAJORs

- **Concurrent write corruption** (Architect M3): fcntl.flock on pending.json, NDJSON for decisions.log
- **LLM hallucinates employee_id → outbound to wrong phone** (Devil R7 + Alt recommendation): `send-coverage-message` takes `(employee_id, proposal_id)`, re-resolves phone from roster, verifies proposal pending in log. **Highest ROI hardening per alt-arch review.**
- **Prompt injection into LLM context** (Security M3): regex-strip injection patterns + template-based outbound
- **Plaintext PII + health data at rest** (Security M1): migrate to `/opt/shift-agent/` + non-root service user
- **Tail-logger dup risk** (Architect M1): seen_ids guard in state file

## Consensus MINORs (accept + document in runbook)

- Log rotation (Architect m1, SRE H3)
- Timezone ambiguity (Devil R8)
- Side-channel coordination — employee texts candidate directly (Devil R10)
- Fraud/prank identity challenge (Devil R9) — Phase 1
- SHA-256 chain on audit log (Security m1)
- Baileys ToS disclosure (Security m2)
- SIM swap disclosure (Security m3)

## Alternative architecture verdict (unanimous)

Keep current architecture. Adopt **two hardening changes**:
1. `send-coverage-message(employee_id, proposal_id, text)` — script re-validates, not LLM
2. Explicit proposal codes (`#A3F2`) for owner approval

Reject: LLM-as-language-only (NLU tax), Twilio/Supabase/Modal (Meta verification blocks deadline), full rearchitect.

## Well-defended areas (where plan was solid)

- Tail-logger as deterministic audit
- Kill-switch + rollback hygiene
- Default-deny on unknown senders
- Helper-script-per-action to avoid -c-flag hooks

## Go/no-go posture

**Ship-able in 48h ONLY if:**
1. All 7 BLOCKERs resolved (most are 30m-2h fixes each; ~10-12h total)
2. Customer provides employee notification pre-go-live (S4)
3. Customer signs off on three disclosures: Baileys ToS risk, audit log checksum-only immutability, employee notification requirement
4. Framed as **business-hours-supervised beta**, not always-on 24/7 agent

**Total revised build effort:** ~14-18h. Still fits 48h with testing buffer.

## Cross-cutting decisions needed from user BEFORE plan v2

1. **Adopt proposal-code approval UX?** (Owner replies `#A3F2` instead of `yes`.) Strong reviewer consensus says yes.
2. **Daily outbound cap value?** Security recommends 10/day for a 6-person roster. Higher if customer is larger.
3. **Employee notification willingness from customer?** If no, we cannot go live ethically/legally. This is THE hard gate.
4. **Migrate to non-root service user?** Adds ~1h build time; reduces blast radius significantly. Recommend yes.
5. **Reason codes vs raw text?** Codes = compliant + less rich audit. Raw text = more useful for owner context + compliance risk. Recommend codes-with-optional-note-field (best of both).
6. **Business-hours-only framing?** SRE recommends not committing 24/7 SLA. Customer must accept this expectation.
