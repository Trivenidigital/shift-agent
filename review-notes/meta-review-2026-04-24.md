# Meta-Review: cross-reference of PR-SYNTHESIS against HEAD (2026-04-24)

**Purpose:** the PR-SYNTHESIS.md was written at 15:04 UTC against commit `efd1a5b`, before the fixes in commit `f1806f0` landed at 15:26 UTC. The individual review files (`pr-review-01` through `pr-review-04`) were subsequently updated with "→ fixed in f1806f0" annotations at 15:08-15:09, but the synthesis was never regenerated.

This doc is the authoritative cross-reference until the synthesis is regenerated.

## Consensus-BLOCKER status table

| # | Issue | Synthesis says | Actual status at HEAD (f1806f0) | Source |
|---|---|---|---|---|
| P1 | urllib.parse import in notify-owner | BLOCKER | **FIXED** | code-quality B1 annotation; verified in git show HEAD:src/scripts/shift-agent-notify-owner |
| P2 | Pydantic v1 API on v2 codebase for E164Phone | BLOCKER | **FIXED** | code-quality B2 annotation; `__get_pydantic_core_schema__` added |
| P3 | `path.with_suffix` raises on dotted suffixes | BLOCKER | **FIXED** | code-quality M6/M7 annotation; swapped to `path.with_name` in safe_io.py |
| P4 | Shell injection via $REASON in shift-agent-disable | BLOCKER | **FIXED** | Present in commit message for f1806f0; verified by reading HEAD. Security review file annotation is stale (was written against efd1a5b). |
| P5 | pending.json write vs OutboundSent log ordering window | BLOCKER | **OPEN** | silent-failures #5, no fix annotation; not listed in f1806f0 commit message |
| P6 | bridge_post unparseable 2xx body treated as success | BLOCKER | **FIXED** | silent-failures #2 annotation; requires non-empty id now |
| P7 | tail-logger seen.remember runs after log-write exception | BLOCKER | **FIXED** | silent-failures #4 annotation; moved inside `if logged:` guard |
| P8 | `h.effective_to is None or True` dead logic in find_by_phone | BLOCKER | **FIXED** | code-quality M12 annotation; now proper effective_from/to window check |
| P9 | bare except: pass in send-coverage-message._notify_owner | BLOCKER | **FIXED** | silent-failures #6 annotation; appends to notify-failed.log now |

**Summary:** 8 fixed, 1 open (P5).

## Citation bug in synthesis

Synthesis attributes P1 to "Silent-failures #15" — incorrect. Silent-failures #15 is the `urlencode` vs `quote` form-body bug in the same file (`notify-owner`), separate from the missing import. Only code-quality B1 and security C1 caught the import. Both reviewers independently flagged it; consensus status stands, but the cross-reference was rushed. (Silent-failures #15 was also fixed in f1806f0 — switched from `"&".join(...quote(v))` pattern to `urllib.parse.urlencode(data)`.)

## Elevated single-reviewer findings (worth BLOCKER treatment for customer-bound deploy)

These are flagged by only one reviewer each, so they didn't make the "consensus BLOCKER" list — but for an outbound-WhatsApp production system they carry customer-visible blast radius comparable to the consensus items.

- **silent-failures #3** — `tail-logger.extract_message_id` hashes `ts|chat|msg`. Two identical-text messages in the same wall-clock second produce the same synthetic id → second is silently deduped → never logged as raw_inbound → owner never notified of a real second sick call. **Failure mode is exactly what the tail-logger was built to prevent.** Fix: include byte-offset or monotonic counter in the hash input.
- **silent-failures #11** — `reconcile._proposal_has_attempt_in_log` catches `OSError` with `pass`, returns `False`. If decisions.log is momentarily inaccessible at boot, the reconciler classifies an already-attempted send as "no prior attempt" and retries it → duplicate WhatsApp to the candidate. **Duplicate outbound is a customer-visible trust harm.** Fix: raise on OSError; refuse to reconcile until the log is readable.
- **silent-failures #12** — `reconcile` catches `subprocess.TimeoutExpired` with only an alert; proposal remains in `approved` status. Next boot sees it again, tries again, times out again → **alert boot-loop** until manual intervention. Fix: on timeout, transition proposal to `send_failed` with `cause=reconciler_timeout` so owner RETRY is required.

## What's still good from the original synthesis

- Test-coverage gap is real; the `max_outbound_per_day: 2` kill-switch + staging canary + manual E2E on a 2-number staging pair remains the right compensating control for a 48h production rollout without the 6-8h test suite written.
- Docstring quality assessment holds (10 narrow drift items, not a deploy gate).
- The security-review HIGH/MEDIUM items (gpg trust-model, YAML grep-sed, template path traversal, U+2028 line separator) are legitimately HIGH, not BLOCKER — worth fixing in Phase 1 but survivable with the current compensating controls.

## What should actually block deploy (post-fix synthesis basis)

After P5 + silent-failures #3, #11, #12 are fixed (~20 min total):
1. Confirm `max_outbound_per_day: 2` set in `config.yaml` for first 48h
2. Run `shift-agent-smoke-test.sh` on staging
3. Manual E2E with 2-number staging pair (owner + one "employee" + one "candidate")
4. Customer sends employees the pre-go-live notification (non-negotiable; see runbook §3)
5. Customer signs the three disclosures in the runbook (Baileys ToS, audit integrity, employee notification)
6. Pair Hermes to customer's WhatsApp as linked device; populate `owner.self_chat_jid` from first observed message

## Process lesson

The stale-synthesis trap is real: once fixes land, older analysis docs become actively misleading. Two mitigations:

1. Banner the stale file loudly (done — see top of `PR-SYNTHESIS.md`).
2. Regenerate synthesis against post-fix HEAD before making deploy decisions (pending — to be done as `PR-SYNTHESIS-post-f1806f0.md`).

Don't re-run the 5 parallel agents — the cross-reference here is mechanical and doesn't need fresh analysis.
