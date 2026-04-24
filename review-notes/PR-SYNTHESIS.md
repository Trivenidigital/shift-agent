# PR Review Synthesis — 5 Parallel Agents on commit efd1a5b

**9 BLOCKERs found. Several would break the system on first use. Rigor paid off.**

## Consensus BLOCKERs (multiple reviewers caught same issue)

| # | Issue | Reviewers | Severity |
|---|---|---|---|
| P1 | `urllib.parse` not imported in `shift-agent-notify-owner` → AttributeError on every Pushover call → silent fallback → dead-man goes silent day 1 | Security-C1, Code-quality-B1, Silent-failures #15 | CRITICAL — breaks THE primary alerting channel |
| P2 | `E164Phone` uses Pydantic v1 API (`__get_validators__`) on a v2 codebase → validators never run → phone canonicalization silently bypassed everywhere | Code-quality-B2, Test-coverage (via schemas analysis) | CRITICAL — all phone comparisons broken |
| P3 | `path.with_suffix(".json.corrupt-X")` / `.json.tmp-X` raises ValueError in Python → atomic writes + corrupt-file recovery both FAIL | Code-quality-M6, M7 | CRITICAL — every state write is broken |

## Single-reviewer BLOCKERs (still block — distinct high-consequence bugs)

| # | Issue | Reviewer | Fix |
|---|---|---|---|
| P4 | Shell injection via `$REASON` in `shift-agent-disable` → malformed NDJSON, audit corruption | Security-C2 | Allowlist-strip REASON |
| P5 | `send-coverage-message` write ordering: `dump_model(PENDING)` happens BEFORE `OutboundSent` log → crash between them leaves state/log inconsistent | Silent-failures #5 | Reorder: log first, then pending update |
| P6 | `bridge_post` returns `(True, "")` on unparseable body → HTML/garbage treated as success | Silent-failures #2 | Require non-empty id; parse failure = failure |
| P7 | `tail-logger` calls `seen.remember(msg_id)` AFTER try/except → exception → dedup FAILS → sick call can silently be re-processed forever, OR never recovered | Silent-failures #4 | Move remember inside success path |
| P8 | `Roster.find_by_phone`: `h.effective_to is None or True` → phone_history always matches regardless of effective_to | Code-quality-M12, Comment-accuracy #ignore | Remove `or True`; thread `now` through |
| P9 | `_notify_owner` bare `except: pass` in send-coverage-message → Pushover down swallows dead-man for every alert path | Silent-failures #6 | Append to notify-failed.log on failure |

## HIGH severity (fix if time allows before deploy)

- `log-decision` legacy path silently drops non-typed entries (Code-quality-B3)
- `create-proposal` logs `ProposalCreated` AFTER releasing pending lock → orphan risk (Code-quality-B4)
- GPG `--trust-model always` enables key-substitution attack (Security-H1)
- `backup.sh grep -c` substring match not anchored → incomplete backup passes check (Code-quality-M8, Silent-failures #8)
- `send-coverage-message` sleeps for rate-limit while holding both locks → blocks all other proposal ops up to 2s (Code-quality-M5)
- `health-check.sh` jq absent → bridge status check silently skipped (Silent-failures #13)
- `health-check.sh` Python heredoc `|| echo 0` treats corrupt pending.json as healthy (Silent-failures #14)
- `health-check.sh` OpenRouter check: `grep -q '"data"'` — substring match; 401 response body containing "data" would pass (Code-quality-M10)
- `backup.sh systemctl start` in cleanup trap has `|| true` → tail-logger restart failure silent, no-inbound window unrecoverable (Silent-failures #7)
- `safe_load_json` OSError on rename-to-corrupt silently permits re-read of corrupt file (Silent-failures #10)
- `reconciler` OSError on decisions.log scan returns False → can cause double-send (Silent-failures #11)
- `datetime.utcnow()` deprecated in Py 3.12+ (Code-quality-M11) — Pydantic schema says "all datetimes tz-aware" but utcnow returns naive
- `atomic_write_text` default mode 0o640 relaxes 0o600 on replace (Security-M1)
- `render-coverage-template` template_name path traversal (Security-M2)
- `ndjson_append` check misses U+2028/U+2029 line separators (Security-M3)
- `notify-owner` `datetime.utcnow` deprecated + returns naive (Code-quality-M11)
- YAML parsed with grep|sed instead of real parser in backup.sh (Code-quality-M9)

## Documentation / quality nits

- `create-proposal` docstring lists exit 8 (lock timeout) but FileLock has no timeout → impossible exit code (Comment-accuracy #1)
- `send-coverage-message` comment says "atomic" for what's actually "lock-protected" (Comment-accuracy #2)
- `ndjson_append` has unused `lock: Optional[FileLock]` parameter in signature (Comment-accuracy #6)
- `safe_io.py` "Durable across kernel panics" overstated — filesystem-dependent (Comment-accuracy #4)
- Dead code: `shift-agent-fsck.py:141` `if False else` (Code-quality-14, Comment-accuracy)
- Unused imports across several scripts (Code-quality-15)
- `assert_local_disk` silently permits on stat failure (Silent-failures #9)

## Test coverage verdict

Smoke test covers "does it start," not "does it work correctly under stress."
**Proposed minimum 6-8h test suite:** LEGAL_TRANSITIONS property tests, send-coverage-message E2E with mocked bridge (6 scenarios), cap race test, reconciler decision-tree test, code-regex drift test, safe_io concurrency test, render-template sanitizer tests.

Skipping full test suite for 48h rollout, but: add `max_outbound_per_day: 2` as a kill-switch for the first 48h (blow-up blast radius) + owner on high-priority Pushover + manual canary on a staging pair before customer pair.

## Recommendation

**Fix the 9 consensus BLOCKERs (P1-P9) before deploy.** Each is a targeted 1-line-to-10-line change. Total ~30 min of focused work. Without them:
- P1 alone = every alert silent
- P2 alone = phone comparisons break subtly
- P3 alone = no state write succeeds
- P4 alone = first disable corrupts log
- P5-P9 = range of race/correctness bugs

Do NOT deploy with these open.

HIGH-severity items are worth fixing but survivable if rehearsal doesn't surface them.
