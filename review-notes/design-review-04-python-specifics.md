# Design Review 4/5 — Python Implementation Specifics (general-purpose)

**Verdict:** 3 BLOCKERs, 5 MAJORs, 3 MINORs

## BLOCKERS

### B1. fcntl.flock is Linux-only; unreliable on NFS
On Ubuntu local disk this is fine, but DESIGN must explicitly forbid NFS-mounted `/opt/shift-agent/`. BSD locks silently broken on some NFS kernels. **Fix:** script startup check: `os.statvfs("/opt/shift-agent").f_fsid` OR document "local disk only."

### B2. fsync missing in atomic-rename pattern (§4.2)
"temp-file + rename" insufficient on ext4 with `data=writeback` or XFS. Correct pattern: `write → fd.flush() → os.fsync(fd) → os.replace(tmp, target) → dirfd.fsync()`. Without directory fsync, crash between rename and dentry flush loses the rename. **Also:** `os.replace()` must be explicit (atomic on POSIX AND Windows); `os.rename()` is not atomic across filesystems.

### B3. NDJSON PIPE_BUF atomicity claim is wrong (§4.5)
PIPE_BUF (4096) governs pipes/FIFOs, NOT regular files. For regular files with O_APPEND, Linux provides atomicity per write() syscall for any size under ~2GB, BUT Python's `open(...).write()` may do multiple syscalls if buffer flushes mid-string. **The real atomicity guarantee comes from the explicit fcntl.flock already specified — the 4KB claim is misleading and should be removed.** Keep the flock; drop the 4KB claim.

## MAJOR

### M1. Shell injection risk in subprocess calls (§5.1)
If `identify-sender` ever invoked via `subprocess.run(..., shell=True)` or bash wrapper string, `+`/`$`/backticks in a JID become attack surface. **Fix:** mandate `shell=False` + list-form args everywhere. Also sanitize phone: `^[+\d@.\w-]+$` regex check before subprocess.

### M2. JSON parse resilience unspecified
Every `json.load()` must handle JSONDecodeError, FileNotFoundError, empty file. Need helper `safe_load_json(path, default)` that catches, logs `state_file_corrupt` to decisions.log, falls back to backup or default. §12 says "fcntl prevents corruption" but flock doesn't prevent partial writes if writer is SIGKILL'd mid-rename.

### M3. datetime timezone handling mix-prone
Config has `timezone: "America/New_York"` but `datetime.now()` is naive, `datetime.now(ZoneInfo("America/New_York"))` is aware. Comparing raises TypeError. **Fix:** mandate `from zoneinfo import ZoneInfo` everywhere; `datetime.now(tz=ZoneInfo(config.timezone))`. Parse stored ts with `datetime.fromisoformat()` (aware). "Reset counter on new day" (§4.3) needs local tz, not UTC.

### M4. SIGKILL orphan temp files (§4.2)
`fcntl.flock` auto-releases on exit (good). But temp files from atomic-rename pattern persist. **Fix:** periodic sweep of `pending.json.tmp.*` older than 5min.

### M5. Config reload race with vim-style in-place edits (§3)
Admin edits `config.yaml` with vim → writes in-place, not atomic → partial parse mid-read → script crash or wrong values. Same issue for roster.json (expected to be owner-edited). **Fix:** runbook mandates "edit via editor → mv", OR wrap reads in flock, OR tolerate + retry on parse error.

## MINOR

### m1. Sick-call regex is fragile (§5.4)
Given regex `sick|can\'t come|fever|leave|off tomorrow|absent|nenu rava|aaj nahi|jwaram` — no `IGNORECASE`, "SICK"/"Sick" miss. "leave" false-positives on "I'll leave at 5pm". Escape `can\'t` should be `can't` in raw string. Transliteration variance for Telugu/Hindi. **Fix:** `re.IGNORECASE | re.UNICODE` + word boundaries + expanded patterns. Accept false positives (skill re-classifies); minimize false negatives.

### m2. seen_ids=10k sizing OK; reconciler linear scan acceptable
45 emp × 5 msg/day × 30 days ≈ 6.75k (under cap). decisions.log linear scan O(N) per boot — at 10k proposals/year, acceptable. Flag for Phase 1 re-index.

### m3. Exit code convention inconsistent
§5.2 uses 2/3/4; other scripts unspecified. **Fix:** shared `exit_codes.py` (EXIT_DISABLED=2, EXIT_CAP=3, EXIT_NOT_FOUND=4, etc.). Verify Hermes forwards these to LLM via stderr/exit-code mapping (open question).

## Single highest-ROI fix
Review 5's recommendation: **one `schemas.py` with Pydantic models** used by every script. Addresses parse-resilience, type safety, validation, and invariant enforcement in one pass. ~1 day work; fits 48h budget.
