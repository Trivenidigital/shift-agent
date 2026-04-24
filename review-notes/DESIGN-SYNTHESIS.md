# Design Review Synthesis — 5 Parallel Agents

**Across 5 reviews: 12 unique BLOCKERs + ~35 MAJORs. Heavy overlap on the worst = strong signal.**

## Consensus BLOCKERs (flagged by 2+ reviewers)

| # | Issue | Reviewers | Fix |
|---|---|---|---|
| C1 | **Signature contradiction PLAN v2 vs DESIGN v1 on `send-coverage-message`** (1 arg vs 2) | Architect B2 | Declare 1-arg form canonical (cleaner), update PLAN |
| C2 | **Reconciler race on startup — can double-send** | Architect B1 + Silent-B2 | Add `reconciling` intermediate status; "outbound_attempted" log BEFORE POST |
| C3 | **Dead-man can't alert when bridge is dead** (bootstrap paradox) | Silent-B3 + Ops-M7 | Required out-of-band channel (Pushover OR healthchecks.io). Non-optional. Refuse to start if unconfigured. |
| C4 | **Candidate YES/NO response handler completely missing** | Architect M2 | New skill `handle_candidate_response` OR dispatcher extension. Not optional — state machine has transitions that depend on it. |
| C5 | **Cap-check ↔ counter-increment race** (concurrent sends both pass) | Silent-B1 | flock across check→POST→increment, OR optimistic reserve pattern |

## Single-reviewer BLOCKERs (still block, distinct issues)

| # | Issue | Reviewer | Fix |
|---|---|---|---|
| B6 | **`send_failed` is terminal dead-end, no recovery path** | Architect B3 | Add owner `RETRY #XXXX` command OR document as "create new proposal" |
| B7 | **Health-check crash has no watchdog** | Silent-B4 | Second-tier watchdog checks last-health-check-ts; alerts if aged |
| B8 | **logrotate copytruncate wrong for NDJSON** (data loss on rotation) | Ops-B1 | Use `create 0640` mode; verify writers re-open per call |
| B9 | **Deploy mechanism entirely undefined** | Ops-B2 | `deploy.sh` with git pull + install + daemon-reload + restart; git-tag per deploy; rollback via checkout |
| B10 | **fcntl.flock unreliable on NFS** | Python-B1 | Assert local-disk at script start; document |
| B11 | **fsync + os.replace correct pattern missing** | Python-B2 | `write → flush → fsync(fd) → os.replace → fsync(dirfd)`. Explicit. |
| B12 | **NDJSON PIPE_BUF atomicity claim is wrong** | Python-B3 | Remove 4KB claim; flock is what's doing the work |

## High-impact MAJORs (consensus across reviews)

- **Log rotation breaks tail-logger offset** (Silent-M2): stat inode before seek; reset on rotation
- **seen-ids.json corruption recovery** (Silent-M1): on parse error, rename to `.corrupt-$ts`, start EOF, fire dead-man
- **identify-sender load failure = silent denial** (Silent-M3): exit 2 + dead-man on load failure vs unknown
- **Backup tar silent incomplete on perm errors** (Silent-M4, Ops-M4): set -euo pipefail + verify contents
- **gpg/S3 silent failure chain** (Silent-M5, Ops-M5): check exit codes, round-trip decrypt test
- **Kill-switch `|| true` swallows notify failure** (Silent-M6): drop || true; exit non-zero on notify failure
- **Multi-step status transition non-atomic** (Silent-M8): add `approved_sending` intermediate status
- **message_id synthesis collision** (Silent-M9): include sender phone in hash; WARN when used
- **systemd ProtectHome vs ReadWritePaths conflict** (Architect-M6, Ops-M1): ProtectHome=read-only or move Hermes to /opt
- **chown /root/.hermes post-migration breaks mid-session** (Ops-M2): ExecStartPre chown as idempotent guard
- **Timer contention at 02:00 backup** (Ops-M3): stop tail-logger timer during backup
- **tar of live baileys_auth = partial snapshot** (Ops-M4): cp to /tmp then tar
- **GPG passphrase on disk = pointless encryption** (Ops-M5): pubkey-only mode
- **Smoke-test no auto-rollback** (Ops-M6): deploy.sh reverts on smoke-test non-zero
- **Zero external observability** (Ops-M7): healthchecks.io ping on every green health check
- **JSON parse resilience unspecified** (Python-M2): safe_load_json helper
- **datetime timezone mix-prone** (Python-M3): enforce ZoneInfo everywhere
- **Shell injection risk** (Python-M1): shell=False + list args mandatory
- **Config hot-reload window** (Architect-M5, Python-M5): snapshot config in-transaction; atomic edits via runbook

## Top structural recommendation (review 5)

**Create `/opt/shift-agent/schemas.py` with Pydantic models** for every data file: Config, Roster, Employee, Proposal (discriminated union on status), PendingStore, SendCounter, SeenIds, LogEntry (discriminated union on type).

Every script imports + validates on read, writes through `.model_dump()`. This single change:
- Fixes JSON parse resilience (Python-M2) — Pydantic validates
- Fixes discriminated-union concerns for Proposal + LogEntry (Types-rec-3)
- Fixes config schema validation (Types-rec-9)
- Fixes canonical phone representation (Types-rec-4) via custom type
- Gives every script consistent exception behavior

~1 day of work. Across entire review set, highest-ROI single investment.

## Other consensus tightenings

- **Proposal codes 4-char → 5-char** (31^4 = 923k, collision risk 54% at 1000 distinct; 31^5 = 28.6M, negligible) OR document "codes ephemeral"
- **Phone canonicalization at one chokepoint** (custom type, from_any constructor, forbid raw str in signatures)
- **Nightly `shift-agent-fsck`** asserting cross-file invariants; logs `invariant_violation` entries
- **Employee status field** (`active`/`inactive`/`terminated`) — roster drift mitigation
- **Exit code constants module** — consistent across scripts

## Total revised build effort estimate

Base plan (v2): ~14-18h
+ Schemas.py (review 5 top rec): +6-8h
+ handle_candidate_response skill (C4): +2-3h
+ Reconciler fix with reconciling status (C2): +1-2h
+ Out-of-band alert channel (Pushover/healthchecks.io) (C3): +1-2h
+ Cap/counter race fix (C5): +1h
+ Deploy script + rollback (B9): +2-3h
+ logrotate correct config (B8): +30m
+ fsync/os.replace pattern (B11): +1h (applied to every write)
+ systemd corrections (M6, M1): +1h
+ Miscellaneous hardening (silent-failure fixes): +3-4h

**Total: ~32-42h.** Approaching the 48h budget with little slack for testing/rehearsal.

## Recommendation for user

Given the real size of the gap, there are 3 honest paths:

### Path A: Full DESIGN v2 with all 12 BLOCKERs resolved + Pydantic schemas
~32-42h build. Fits 48h only if parallelized + offshore team actually executes.
**Pros:** production-quality for this scale. Customer gets something durable.
**Cons:** 42h leaves <6h testing buffer. Any surprise ruins the timeline.

### Path B: Reduced scope — ship assist-mode instead of full-auto
Drop full-auto send → agent drafts, owner forwards manually. Eliminates:
- Outbound cap race (C5) — still nice to have but not critical
- Reconciler race (C2) — no automated retry needed
- Dead-man send-before-notify (less critical)
- Full error-path tree for auto-send
**Revised effort:** ~18-24h. Fits 48h with real testing buffer.
**Tradeoff:** owner does more clicking. Customer may or may not accept.

### Path C: Delay customer rollout by 48h
Ship 96h from now with Path A done right. Honest answer if the customer can't accept Path B.

**My recommendation:** Path B as primary, Path A as stretch if truly 24/7 offshore execution is available. The review uncovered more than "hardening" — it uncovered a missing major component (candidate response) and multiple race conditions that would burn the first customer's trust if hit.

**Cross-cutting decisions I need from user before DESIGN v2:**
1. **Path A / B / C?** Biggest fork.
2. **Out-of-band alert channel (REQUIRED now):** Pushover ($5 one-time), healthchecks.io (free, email-only), email, SMS? Pick one — I'll wire it.
3. **5-char proposal codes or 4-char + doc as ephemeral?** Codes touch UX; 5-char is slightly more to type.
4. **Accept Pydantic as hard dependency?** Adds `pip install pydantic` to the venv. Minor; flagging.
5. **Skill vs dispatcher extension for candidate-response?** Slight preference for a separate skill (`handle_candidate_response`) for clarity.
