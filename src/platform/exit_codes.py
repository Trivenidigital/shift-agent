"""
Shift Agent — shared exit codes.

All scripts use these constants. Hermes surfaces non-zero exits to the LLM via stderr,
which the skill can read and branch on.
"""

# 0 — success
EXIT_OK = 0

# 1 — generic failure
EXIT_GENERIC_ERROR = 1

# 2 — agent disabled OR input/state file invalid (caller should not retry)
EXIT_DISABLED = 2
EXIT_INVALID_INPUT = 2

# 3 — rate limit / daily cap exceeded (caller can propose owner raise cap)
EXIT_CAP_EXCEEDED = 3

# 4 — resource not found (unknown proposal_id, unknown employee_id, etc.)
EXIT_NOT_FOUND = 4

# 5 — Pydantic validation failure (schema mismatch; indicates bad data on disk)
EXIT_SCHEMA_VIOLATION = 5

# 6 — external dependency unavailable (OpenRouter, WA bridge, Pushover)
EXIT_DEPENDENCY_DOWN = 6

# 7 — state file corrupt beyond recovery (caller should alert + escalate)
EXIT_STATE_CORRUPT = 7

# 8 — concurrency conflict (failed to acquire lock within timeout)
EXIT_LOCK_TIMEOUT = 8

# 9 — caller attempted an illegal state transition
EXIT_ILLEGAL_TRANSITION = 9

# 10 — environment problem (NFS detected, missing binary, etc.)
EXIT_ENVIRONMENT = 10

# 11 — PR-B v0.4: truth-guard rejected the LLM-drafted text on stdin
# (headcount integer or ISO event_date missing from the draft). Distinct from
# EXIT_DEPENDENCY_DOWN (=6, "bridge unreachable") so PR-D2's retry-state-machine
# does NOT re-attempt the bridge POST — the lead needs a re-DRAFT, which only
# the SKILL can do. Caller must NOT auto-retry; the lead stays at
# AWAITING_OWNER_APPROVAL pending a fresh owner-reply.
EXIT_TRUTH_GUARD_FAILED = 11
