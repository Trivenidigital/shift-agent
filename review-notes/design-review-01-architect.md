# Design Review 1/5 — Implementation Architecture (feature-dev:code-architect)

**Verdict:** 3 BLOCKERs, 6 MAJORs, 5 MINORs

## BLOCKERS

### B1. Startup reconciler has unclosed race it claims to close (§12.1)
Reconciler reads pending.json, sees `approved`, releases lock, calls `send-coverage-message` — but concurrent in-flight skill-path call can also be mid-send. Both pass status check → both send. **Fix:** add `reconciling` intermediate status written BEFORE releasing lock, not after.

### B2. `send-coverage-message` signature contradiction between PLAN v2 and DESIGN v1
- DESIGN §5.2: `send-coverage-message <proposal_id>` (1 arg)
- PLAN §4.3: `send-coverage-message <employee_id> <proposal_id>` (2 arg)
- handle_owner_command (§6.3): calls with 1 arg
Every caller would fork at this seam. **Fix:** declare DESIGN's 1-arg form canonical (cleaner — everything's in pending.json); update PLAN.

### B3. `approved → send_failed` is a terminal dead-end
State machine §9 has no transition OUT of `send_failed`. Health check fires dead-man but no recovery transition. Flaky send permanently orphans proposal. **Fix:** add owner command `RETRY #XXXX` transition, OR document explicitly as "owner creates new proposal."

## MAJOR

### M1. seen-ids.json filename contradicts between docs (§4.4 vs PLAN §4.1)
DESIGN: `seen-ids.json`. PLAN: `tail-logger-seen.json`. Must align.

### M2. Candidate response handler completely unspecified (§9)
State transitions `sent → accepted/declined` described but NO component is defined that: (a) matches inbound YES/NO to open sent proposal by candidate's phone, (b) updates pending.json, (c) notifies owner via template 8.3. Entire path missing. **Fix:** new skill `handle_candidate_response` OR extend dispatcher to route candidate replies.

### M3. "Last send <30min" health check will false-alarm during quiet periods (§5.5)
45-person store may legitimately send zero for hours. Dead-man fires constantly. **Fix:** use bridge `/health` endpoint for socket liveness, not send-timestamp proxy. Socket-alive ≠ last-send-recent.

### M4. `log-decision-direct-append` referenced but never defined (§5.7)
Kill-switch calls this script; it's not in file layout (§2), build order (§14), or component specs (§5). Build will hit missing dependency.

### M5. Config hot-reload window in `send-coverage-message` (§5.2, §3)
Config read step 1, counter check step 4, update step 9. Admin edits config.yaml between 4 and 9 → cap check passes on old value. **Fix:** explicitly hold config snapshot through entire transaction.

### M6. `ProtectHome=true` conflicts with `ReadWritePaths=/root/.hermes` (§10.1)
systemd known gotcha: ProtectHome=true hides /root entirely; ReadWritePaths exceptions don't reach into protected namespace. Hermes fails to read session. **Fix:** `ProtectHome=read-only` or `ProtectHome=no` with comment.

## MINOR

- **m1.** `next_code_seq` in pending.json is unused dead field (§4.2) — remove
- **m2.** `send-counter.json` has no flock spec (§4.3, §5.2) — add `send-counter.json.lock`
- **m3.** Build order places `shift-agent-notify-owner` at step 8 but callers at 6 and 12 — move to step 5
- **m4.** `cancelled` status in §4.2 has no state-machine trigger — define (owner `CANCEL #XXXX`) or remove
- **m5.** fsync missing before atomic rename on pending.json (§4.2) — acknowledge or add

## Top 2 most dangerous for 48h build
1. **B2** — signature contradiction forks implementation at most-called interface
2. **M2** — candidate-response path entirely unspecified, requires new component absent from layout + build order
