# PR Review 2/5 — Silent Failures (pr-review-toolkit:silent-failure-hunter)

**Verdict:** 8 CRITICALs, 7 HIGHs.

## CRITICALs

1. `send-coverage-message:267` — JID built from `candidate.phone` without nil check → "@s.whatsapp.net" if empty. → TODO: guard; _revert_everything on empty.
2. `bridge_post` — 2xx with unparseable body returned as success → HTML/garbage accepted as sent. → fixed in commit f1806f0
3. `tail-logger extract_message_id` — same ts+chat+msg hash collides on identical-second messages → 2nd silently deduped. → TODO: include byte-offset or counter in hash.
4. `tail-logger seen.remember` called after try/except → exception path also marks seen → message invisible forever. → fixed in commit f1806f0
5. `send-coverage-message` write ordering: dump_model(pending) vs _log_entry(OutboundSent) → crash window can leave log=sent + pending=reconciling. TODO: reorder or add fsck invariant.
6. `send-coverage-message._notify_owner` bare `except: pass` → Pushover down silences all alerts. → fixed in commit f1806f0
7. `shift-agent-backup.sh` `systemctl start || true` in cleanup → tail-logger restart failure silent → no-inbound window. TODO: alert on restart failure.
8. `backup.sh grep -c` required_count substring match → sparse archives pass. TODO: per-file grep -Fxq.

## HIGHs

9. `assert_local_disk` stat failure silently permits NFS → TODO: log health_check_failure + continue.
10. `safe_load_json` OSError on rename-to-corrupt silently permits re-read → fixed (returns distinct "corrupt_unrenamed" status)
11. `reconcile._proposal_has_attempt_in_log` OSError → returns False → double-send risk. TODO: raise.
12. `reconcile` TimeoutExpired only alerts; proposal stays "approved" → boot-loop. TODO: transition to send_failed on timeout.
13. `health-check.sh` jq absent → bridge status check silently skipped. TODO: require jq or python fallback.
14. `health-check.sh` Python heredoc `|| echo 0` → corrupt pending.json → 0 stale proposals reported → no alert. TODO: echo ERROR, treat non-integer as failure.
15. `notify-owner:67` using `quote` not `urlencode` for form body → `&`/`=` in values break form. → fixed in commit f1806f0 (switched to urlencode)

## Fixed in commit f1806f0
Items 2, 4, 6, 10, 15.

## Still open (follow-up patches)
Items 1, 3, 5, 7, 8, 9, 11, 12, 13, 14.
