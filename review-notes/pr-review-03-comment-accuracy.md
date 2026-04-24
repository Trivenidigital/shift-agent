# PR Review 3/5 — Comment / Doc Accuracy (pr-review-toolkit:comment-analyzer)

**Verdict:** 10 accuracy drift issues. Docstrings are unusually good overall (reference DESIGN sections, list exit codes, explain WHY not just WHAT), but several load-bearing claims don't match code.

## Key drift items

1. `create-proposal:30` — docstring lists exit code 8 (lock timeout) but FileLock has no timeout → impossible. TODO: remove from docstring.
2. `send-coverage-message:147` — comment says "atomic check→reconcile→counter" but dump_model runs BEFORE cap check. It's lock-protected, not atomic in the strict sense. TODO: soften.
3. `reconcile.py:16` — "safe-by-default. Never sends duplicates" true, but also guarantees NON-delivery in the attempted-but-not-confirmed window. TODO: acknowledge non-delivery guarantee.
4. `safe_io.py:111` — "Durable across kernel panics on ext4/xfs" overstated. Filesystem-dependent on mount options. → fixed in commit f1806f0 (soften to data=ordered).
5. `schemas.py:177` — "Out-of-band alerts cannot be optional" — validator enforces Pushover-only; WA fallback still optional. TODO: clarify Pushover-only requirement.
6. `safe_io.py:147` — `ndjson_append(lock: Optional[FileLock] = None)` parameter unused. → fixed in commit f1806f0 (removed).
7. `tail-logger:10` — "No sick-call inbound is lost" three gaps: hash collision, rotation-while-not-running, exception before dump_model. Partial fix in commit f1806f0; hash collision still open.
8. `fsck.py:7-8` — `counter.count == outbound_sent count for day` doesn't account for DST/tz-change boundaries. TODO: document assumption.
9. `reconcile.py:13` — "O(n) scan; acceptable at boot" is actually O(n × m). Will rot as log grows. TODO: compute attempted-pids set in one pass.
10. `backup.sh:40-42` — "let any in-flight run finish" with `sleep 2` is wishful. `systemctl stop timer` doesn't wait for a running unit. TODO: also stop the service + poll.

## Positive

- `send-coverage-message` docstring is exemplary (cross-refs DESIGN §6.2, lists flow + exit codes).
- `LEGAL_TRANSITIONS` table in schemas.py is a rare case where a data structure IS the documentation.
- `health-watchdog.sh` correctly named "second-tier watchdog".
