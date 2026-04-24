# PR Review 5/5 — Security (typescript-security-expert)

**Verdict:** 2 CRITICAL, 4 HIGH, 3 MEDIUM, 1 LOW. Python paths hardened; Bash has concrete exploitable issues. Score: 6.5/10.

## CRITICAL

### C1. `urllib.parse` not imported in shift-agent-notify-owner
- **File:** `src/scripts/shift-agent-notify-owner` line 67
- **Bug:** `urllib.parse.quote(v)` called but only `urllib.error` and `urllib.request` imported
- **Effect:** Every Pushover call raises AttributeError → silent fallback to WA → if WA also unreachable, message lands in `notify-failed.log` that nobody reads. **Dead-man goes silent from day 1.**
- **Fix:** Add `import urllib.parse` at top

### C2. Shell injection via $REASON in shift-agent-disable
- **File:** `src/scripts/shift-agent-disable` lines 10, 23-25, 32
- **Bug:** `REASON="${1:-manual_kill}"` unsanitized → interpolated into NDJSON heredoc + Pushover arg
- **Effect:** `shift-agent-disable '","reason":"injected","x":"'` produces malformed NDJSON, breaking audit. Any double-quote/newline in reason corrupts log.
- **Fix:** Allowlist strip immediately after assignment: `REASON="${REASON//[^a-zA-Z0-9_ ]/}"`. Better: pass to log-decision-direct as separate args rather than heredoc.

## HIGH

### H1. `--trust-model always` in backup.sh enables key-substitution
- **File:** `src/scripts/shift-agent-backup.sh` line 82
- **Bug:** `gpg --trust-model always` accepts any key matching `$GPG_RECIPIENT` regardless of trust level
- **Effect:** If attacker imports rogue key with matching email, backup gets encrypted to attacker's key silently.
- **Fix:** Use `--trust-model pgp` (default) with fingerprint pinning. Accept full GPG fingerprint (40 hex chars) in config instead of email.

### H2. Pushover error `detail` logged unredacted, may echo token
- **File:** `src/scripts/shift-agent-notify-owner` line 79, 113
- **Bug:** `parsed.get('errors')` from Pushover API can echo request fields in some error modes; written to `notify-failed.log` unredacted.
- **Fix:** Redact token from detail strings; log only status code + sanitized error category.

### H3. Temp file `/tmp/backup-tar-errors-$$` not in cleanup trap
- **File:** `src/scripts/shift-agent-backup.sh` line 58
- **Bug:** Cleanup trap only handles `$SESSION_COPY` and `$TAR_PATH`; if script exits early after tar succeeds, stderr file persists in /tmp (world-readable, contains directory paths).
- **Fix:** Use `mktemp`, add to cleanup trap.

### H4. GPG_RECIPIENT not validated after YAML parse
- **File:** `src/scripts/shift-agent-backup.sh` line 18
- **Bug:** `grep | sed` extraction, no format validation. A newline-containing YAML value could truncate unexpectedly.
- **Fix:** Regex check against email pattern after extraction; exit loudly if malformed.

## MEDIUM

### M1. `atomic_write_text` does not preserve existing file permissions
- **File:** `src/safe_io.py` `atomic_write_text` line 116
- **Bug:** Default mode `0o640`; if target was manually tightened to `0o600`, replace resets it.
- **Effect:** Group-readable state files during window between temp write + rename.
- **Fix:** Preserve target mode if file exists; default to `0o600` for state files anyway.

### M2. Path traversal in render-coverage-template via template_name
- **File:** `src/scripts/render-coverage-template` line 51
- **Bug:** `TEMPLATES_DIR / f"{args.template_name}.txt"` — `../../etc/passwd.txt` style escapes.
- **Fix:** `if template_path.resolve().parent != TEMPLATES_DIR.resolve(): refuse`

### M3. NDJSON log injection via U+2028/U+2029 in rendered field
- **File:** `src/safe_io.py` `ndjson_append` line 154
- **Bug:** Check is `"\n" in entry_json`; doesn't catch U+0085 (NEL), U+2028 (LINE SEPARATOR), U+2029 (PARAGRAPH SEPARATOR). Some NDJSON parsers treat these as line breaks.
- **Fix:** `if any(c in entry_json for c in ('\n', '\r', ' ', ' ', ''))`

## LOW

### L1. e.stderr from render-template subprocess leaks roster data into decisions.log
- **File:** `src/scripts/send-coverage-message` line 255
- **Bug:** Template render errors echo partial field values (employee names) into `decisions.log` via revert path.
- **Effect:** GDPR-adjacent issue if log is shipped off-host.
- **Fix:** `e.stderr[:100].replace('\n', ' ')` before inclusion.

## Well-implemented (no issues)
- Python subprocess calls correctly use argument lists, no `shell=True`
- `ndjson_append` newline guard (needs U+2028 extension but structurally correct)
- `validate_phone_input` allowlist approach

## Top priority to fix before deploy

1. C1 (urllib.parse missing) — 30 sec fix, prevents silent dead-man failure
2. C2 (shell injection in disable) — 2 min fix, prevents log corruption
3. H1 (gpg trust-model) — 5 min fix, prevents backup hijack
4. H3 (tar-errors temp file) — 2 min fix
5. M2 (template path traversal) — 2 min fix
