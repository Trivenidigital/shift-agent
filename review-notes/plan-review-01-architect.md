# Plan Review 1/5 — Architecture Soundness (feature-dev:code-architect)

**Verdict:** 1 BLOCKER, 3 MAJOR, 3 MINOR

## BLOCKER

### B1. Concurrent sick-calls with ambiguous "yes" approval when LLM reads flat log as state
**Plan ref:** §4.4 (Approval tracker)
**Issue:** Matching owner's "yes" reply "by recency + keyword + active proposals" is undefined behavior when two sick-calls arrive within the same shift window. LLM reading flat `decisions.log` to determine state is not reliable state management. Classic "state-in-unstructured-text" foot-gun.
**Recommendation:** Promote `pending.json` from vague mention in §3 to authoritative state store. Give each proposal a short numeric ID (`P001`, `P002`). Owner types "yes P001" or agent asks "Reply 1 or 2." Approval tracker reads only `pending.json`, not `decisions.log`.

## MAJOR

### M1. Tail-logger duplicate entries without seen_ids guard
**Plan ref:** §4.1
**Issue:** Regex matching partial log lines that repeat (e.g. retry-on-failure lines) + messages crossing timer windows = dup entries. `message_id` reconciliation only works if Hermes actually emits message_id in agent.log (needs verification).
**Fix:** Add persisted `.tail-logger-state.json` with `seen_ids` set. Check before writing. ~10 lines of Python.

### M2. `fromMe: true` check is insufficient for owner identification
**Plan ref:** §3 (architectural decision #4), §4.5
**Issue:** `fromMe: true` is set for ANY outbound message from the linked device, not just self-chat. If owner sends a WhatsApp to anyone while agent runs, it'll be interpreted as a potential approval command.
**Fix:** Dispatcher must ALSO verify destination JID matches self-chat JID (owner's own number), not just `fromMe`. Test WhatsApp self-chat delivery reliability on Android explicitly before go-live — it's the entire owner control channel.

### M3. JSON-on-disk concurrent write corruption
**Plan ref:** §3 (decision #5), §4.1+4.5 writers
**Issue:** Three concurrent writers to `decisions.log` (tail-logger timer, handle_sick_call, handle_owner_command). Single-line appends are atomic only under PIPE_BUF (4096 bytes); LLM-enriched JSON entries exceed this → interleaved partial writes. `pending.json` as structured JSON is worse — read-modify-write without locking corrupts it.
**Fix:** `fcntl.flock` on `pending.json` for RMW. Enforce NDJSON convention for `decisions.log` + keep entries compact. Open with `O_APPEND`.

## MINOR

### m1. No log rotation in required deliverables
`decisions.log` append-only; §4.6 monitors file size but has no automated action. At 20/day with 2-5KB entries, hits 10MB in weeks not months. Add weekly logrotate config as required deliverable.

### m2. No Hermes-restart recovery path for "approved but not yet sent" state
If Hermes restarts after owner approval arrives but before `send-coverage-message` executes, `pending.json` left in "approved" limbo with no retry. Runbook must document this.

### m3. Kill-switch writing to decisions.log adds unneeded writer
Use separate `agent-state.json` for operational state, not audit log.

## Sound-as-designed callouts

- Phone-identity dispatch is correct in concept (roster phone → employee, cryptographically authenticated `fromMe` → owner)
- Helper-script-per-action pattern avoids the `-c` flag hook issue nicely
- Tail-logger architecture (deterministic audit independent of LLM) is the right shape

## Open question raised

Does Hermes actually emit `message_id` in agent.log, or just `ts`+message text? If only the latter, reconciliation key degrades to `ts+hash` which collides on identical messages sent within same second.
