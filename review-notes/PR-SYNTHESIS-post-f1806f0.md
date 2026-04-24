# PR Review Synthesis — Post-Fix (against upcoming HEAD)

**Status:** authoritative for deploy decisions. This supersedes `PR-SYNTHESIS.md` (which is banner-flagged stale).

**Basis:** all 5 original PR reviews (commit `efd1a5b`) + fixes in `f1806f0` + fixes in the commit being made now (replaces `f1806f0..HEAD`).

## Consensus BLOCKERs — final status

| # | Issue | Fix location | Status |
|---|---|---|---|
| P1 | `urllib.parse` not imported in notify-owner | f1806f0 | ✅ FIXED |
| P2 | `E164Phone` using Pydantic v1 API on v2 | f1806f0 | ✅ FIXED |
| P3 | `path.with_suffix` raises on dotted suffixes | f1806f0 | ✅ FIXED |
| P4 | `$REASON` shell injection in shift-agent-disable | f1806f0 | ✅ FIXED |
| P5 | pending.json/decisions.log write ordering | post-f1806f0 | ✅ FIXED — log written BEFORE state mutation on both success and failure paths; partial-failure notifies instead of hiding |
| P6 | `bridge_post` unparseable 2xx body | f1806f0 | ✅ FIXED |
| P7 | tail-logger `seen.remember` after exception | f1806f0 | ✅ FIXED |
| P8 | `h.effective_to is None or True` dead logic | f1806f0 | ✅ FIXED |
| P9 | bare `except: pass` in `_notify_owner` | f1806f0 | ✅ FIXED |

**9 of 9 consensus BLOCKERs closed.**

## Elevated single-reviewer items (closed)

| # | Issue | Fix location | Status |
|---|---|---|---|
| SF#1 | JID built from empty candidate.phone | post-f1806f0 | ✅ FIXED — empty phone guard before JID construction |
| SF#3 | tail-logger `extract_message_id` same-second hash collision | post-f1806f0 | ✅ FIXED — byte-offset included in hash input |
| SF#11 | `reconcile._proposal_has_attempt_in_log` swallows OSError | post-f1806f0 | ✅ FIXED — raises `AttemptLogUnreadable`; reconciler refuses to retry that proposal + alerts |
| SF#12 | `reconcile` TimeoutExpired boot-loop | post-f1806f0 | ✅ FIXED — on timeout, transition to send_failed so owner RETRY is required |
| SF#15 | `urlencode` vs `quote` in notify-owner form body | f1806f0 | ✅ FIXED |
| SEC-M3 | U+2028/2029/NEL NDJSON line-break injection | f1806f0 | ✅ FIXED — `ndjson_append` check broadened |
| SEC-M1 | `atomic_write_text` default 0o640 vs 0o600 + mode-preserve | f1806f0 | ✅ FIXED |
| SEC-H2 | Pushover error `detail` could echo token in logs | f1806f0 | ✅ FIXED — 30+ char hex run redaction in notify-failed logger |
| CM-4 | "Durable across kernel panics" overstated | f1806f0 | ✅ FIXED — softened to `data=ordered` mount |
| CM-6 | `ndjson_append` unused `lock` parameter | f1806f0 | ✅ FIXED — removed |
| CQ-M11 | `datetime.utcnow()` deprecated + naive | f1806f0 | ✅ FIXED |

## HIGH-severity items deferred to Phase 1 (with compensating controls)

These single-reviewer findings are real but not blocking for a friendly-design-partner beta with the compensating controls listed:

- **GPG `--trust-model always`** (security-H1): key-substitution risk. Compensating control: operator must `gpg --list-keys` during initial provisioning and verify the customer's recipient fingerprint matches the imported key. Document in runbook §3. Phase 1: switch to fingerprint-pinned `--recipient`.
- **`backup.sh` YAML parsed via grep|sed** (code-quality-M9): fragile on multi-line values. Compensating control: roster.json and config.yaml are both constrained to simple schemas the agent validates on read — no multi-line surprises. Phase 1: replace with `python3 -c 'import yaml; ...'`.
- **`backup.sh grep -c` substring match** (silent-failures #8, code-quality-M8): could pass incomplete archives. Compensating control: nightly fsck has its own cross-check (counter vs log) that detects roster/log issues independently. Phase 1: per-file `grep -Fxq` checks.
- **`health-check.sh` `jq` dependency** (silent-failures #13): if jq is missing, bridge-status check silently skips. Compensating control: jq is in the deploy prerequisites; runbook deploy step includes `apt-get install -y jq`. Phase 1: Python fallback.
- **`health-check.sh` Python heredoc `|| echo 0`** (silent-failures #14): corrupt pending.json → 0 stale proposals reported. Compensating control: nightly fsck catches this via its schema validation. Phase 1: explicit error path.
- **`backup.sh systemctl start || true` in cleanup** (silent-failures #7): tail-logger restart failure silent. Compensating control: health-watchdog (15-min timer) alerts if the tail-logger timer is inactive. Phase 1: explicit alert on restart failure.
- **Template path traversal in `render-coverage-template`** (security-M2): attack surface limited to `.txt` file reads within `/opt/shift-agent/templates/` anyway, but `../../` traversal could escape. Compensating control: the script is only invoked with hardcoded template names by internal callers. Phase 1: `resolve().parent == TEMPLATES_DIR.resolve()` guard.

None of these carry customer-visible blast radius equivalent to the consensus BLOCKERs.

## Remaining open HIGHs worth tracking (not blocking)

- `send-coverage-message._revert_everything` uses stale `proposal` var from outside lock (code-quality-M13) — re-read under lock would be safer. Low probability of actual divergence given tight timing.
- `log-decision` legacy-compat path silently writes to `decisions-legacy.log` (code-quality-B3) — acceptable as migration aid; add deprecation warning in Phase 1.
- `create-proposal` logs after pending lock release (code-quality-B4) — race window; Phase 1 fix is to move the log write inside the pending lock (note: different lock file, no deadlock risk).
- Unused imports across several files (code-quality-15) — cosmetic; run `pyflakes` in Phase 1 CI.

## Test coverage posture (unchanged)

No automated test suite. Compensating controls for 48h production:
1. `shift-agent-smoke-test.sh` run post-deploy + on every restart
2. `max_outbound_per_day: 2` in config.yaml for first 48h (blast-radius cap)
3. Manual E2E on staging pair (2 WhatsApp numbers) before customer pair
4. Nightly `shift-agent-fsck` catches cross-file invariant violations
5. Health-check + watchdog + external healthchecks.io ping for liveness

Phase 1: 6-8h unit test suite per `pr-review-04-test-coverage.md`.

## Final deploy gates

All required for go-live:

1. ✅ 9/9 consensus BLOCKERs closed (P1-P9)
2. ✅ 4/4 elevated single-reviewer items closed (SF#1, SF#3, SF#11, SF#12)
3. ⬜ Customer roster data populated in `/opt/shift-agent/roster.json`
4. ⬜ Customer Pushover keys generated + placed in `/opt/shift-agent/.env`
5. ⬜ Customer's GPG public key imported on the VPS (`gpg --import`)
6. ⬜ `shift-agent` user created; `/opt/shift-agent/` tree owned by it; Hermes dir chown'd
7. ⬜ `config.yaml` populated (customer name, timezone, owner phone, limits with `max_outbound_per_day: 2`)
8. ⬜ Customer sends employees the pre-go-live notification (runbook §3 — non-negotiable)
9. ⬜ Customer signs the three disclosures (Baileys ToS, audit integrity, employee notification)
10. ⬜ Hermes paired to customer's primary WhatsApp as linked device
11. ⬜ `shift-agent-smoke-test.sh` exits 0 on the target VPS
12. ⬜ Manual staging E2E with 2-number pair: inbound → proposal → approve → outbound → candidate YES → owner confirmation. decisions.log shows full chain.

After all 12 gates pass → flip `max_outbound_per_day` to the customer's real value (6 for 45-employee roster) and declare live.

## Process lesson captured

Stale-synthesis trap: once fixes land, pre-fix analysis docs become actively misleading. Mitigations applied:
- Banner prior synthesis loudly (`PR-SYNTHESIS.md` top)
- Write meta-review doc for the correction (`meta-review-2026-04-24.md`)
- Produce fresh synthesis against post-fix HEAD (this file)
- Do NOT re-run the 5-agent review cycle — the status check was mechanical cross-reference, not new analysis
