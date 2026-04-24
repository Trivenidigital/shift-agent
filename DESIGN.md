# Shift Agent — Detailed Design (v2)

**Status:** Post-review (5-agent design review incorporated)
**Author:** Claude (Opus 4.7)
**Context:** Implements PLAN v2. Addresses 12 BLOCKERs + high-impact MAJORs surfaced in design review.

---

## 0. Changes from v1

v1 went through 5 parallel reviews. This v2 resolves every BLOCKER. Structural changes:

- **NEW `/opt/shift-agent/schemas.py`** — Pydantic models as single source of truth for every data file. Used by every script. Lifts type safety + invariant enforcement across the entire system.
- **NEW `handle_candidate_response` skill** — handles covering employee's YES/NO reply to an outbound coverage message. Previously missing; state machine transitions had no implementer.
- **Signature fix:** `send-coverage-message <proposal_id>` (1-arg, canonical). PLAN updated separately to match.
- **Reconciler with `reconciling` intermediate status** — closes double-send race on startup.
- **`outbound_attempted` log entry before POST** — prevents reconciler from retrying a successful-but-unrecorded send.
- **Cap-check ↔ counter-increment made atomic** under flock.
- **Pushover required** (not optional) for out-of-band alerts. Agent refuses to start if Pushover unconfigured.
- **Health-check watchdog** (second-tier timer) alerts if health-check itself stops firing.
- **logrotate correct config** (`create` mode, not copytruncate).
- **NEW `deploy.sh`** with git-tag per deploy + rollback-via-checkout.
- **fsync + os.replace pattern** mandated for all atomic writes.
- **5-char proposal codes** (28.6M combinations, negligible collision).
- **`send_failed` has `RETRY #XXXX` transition** (not dead-end).
- **NEW `shift-agent-fsck` script** — nightly cross-file invariant check.
- **Employee active/terminated status + phone_history**.
- **Canonical phone type** — eliminates dashed/E164/@-JID mismatches.
- **healthchecks.io ping** for external heartbeat.
- **ExecStartPre chown** for Hermes dir (idempotent guard against mid-session root-writes).
- **Backup script stops tail-logger timer** before tar (avoids partial-line snapshots).
- **cp-then-tar for baileys_auth** (avoids live-session corruption).
- **Pubkey-only GPG** (private key never on VPS).
- **Smoke-test auto-rollback** on failure.
- **`zoneinfo` mandated** everywhere; naive datetimes forbidden.
- **`safe_load_json` helper** — handles corruption/missing/empty gracefully.
- **Exit codes in `/opt/shift-agent/exit_codes.py`** — one source of truth.
- **systemd hardening:** PrivateTmp, RuntimeDirectory, ProtectHome=read-only.

Total scope expansion: ~15-20h additional work. Plan estimated 32-42h total; user accepted Path A.

---

## 1. Architecture overview

Unchanged principle: LLM reasons, scripts enforce invariants.
Strengthened: every data file is validated on read + validated on write via Pydantic. Every side-effect is atomic-or-loud. Every failure has a detectable signal + an out-of-band alert path.

```
                      WhatsApp (linked device)
                            │
                   Baileys bridge (Node)
                            │
                   Hermes gateway (Python)
                            │
                    dispatch_shift_agent
                    (routes by phone+JID)
                            │
      ┌─────────────────────┼─────────────────────┐
      ▼                     ▼                     ▼
  handle_sick_call  handle_owner_command  handle_candidate_response
      │                     │                     │
      ▼                     ▼                     ▼
  scripts (schemas.py → Pydantic validation everywhere):
    identify-sender, create-proposal, send-coverage-message,
    render-coverage-template, log-decision, update-proposal-status

  State (JSON + flock + fsync):
    config.yaml, roster.json, state/pending.json,
    state/send-counter.json, state/seen-ids.json,
    logs/decisions.log (NDJSON)

  Out-of-band timers:
    tail-logger (30s)        → raw_inbound audit
    health-check (5m)        → dead-man via Pushover + healthchecks.io ping
    health-watchdog (15m)    → alerts if health-check stops firing
    nightly-backup (02:00)   → pubkey-gpg tarball (tail-logger stopped during)
    fsck (03:00)             → cross-file invariant check
```

---

## 2. File layout

All state under `/opt/shift-agent/`. Owned by `shift-agent:shift-agent`.

```
/opt/shift-agent/
├── config.yaml                 # per-customer config
├── roster.json                 # employees + schedule
├── .env                        # secrets (mode 600)
├── schemas.py                  # NEW: Pydantic models, used by all scripts
├── exit_codes.py               # NEW: shared exit-code constants
├── safe_io.py                  # NEW: safe_load_json, atomic_write, flock helpers
├── state/
│   ├── pending.json            # proposal state (Pydantic-validated)
│   ├── send-counter.json       # daily counter
│   ├── seen-ids.json           # tail-logger dedup + offset
│   ├── disabled.flag           # kill-switch marker
│   ├── health.log              # last health-check output
│   └── last-health-check-ts    # updated on green health check; watchdog reads
├── logs/
│   └── decisions.log           # NDJSON audit (current file only; rotations in /var/log/shift-agent-archive/)
├── backups/
│   └── YYYY-MM-DD.tar.gz.gpg
└── templates/
    ├── proposal_to_owner.txt
    ├── coverage_message_to_candidate.txt
    ├── owner_confirmation_after_accept.txt
    └── dead_man_alert.txt

/usr/local/bin/
├── identify-sender
├── send-coverage-message
├── create-proposal
├── render-coverage-template
├── update-proposal-status
├── log-decision                # extends Phase 0
├── log-decision-direct         # NEW: append-only, no Hermes roundtrip (for kill-switch)
├── shift-agent-notify-owner    # NEW: single chokepoint for outbound via Pushover + WA-if-available
├── shift-agent-tail-logger.py
├── shift-agent-health-check.sh
├── shift-agent-health-watchdog.sh
├── shift-agent-backup.sh
├── shift-agent-fsck.py         # NEW: cross-file invariant check
├── shift-agent-reconcile.py    # NEW: startup reconciler with reconciling status
├── shift-agent-disable
├── shift-agent-enable
├── shift-agent-smoke-test.sh
└── shift-agent-deploy.sh       # NEW: git-tag + install + daemon-reload + rollback

/etc/systemd/system/
├── hermes-gateway.service      # updated: User=shift-agent, PrivateTmp, ExecStartPre chown
├── shift-agent-tail-logger.{service,timer}
├── shift-agent-health.{service,timer}
├── shift-agent-health-watchdog.{service,timer}
├── shift-agent-backup.{service,timer}
├── shift-agent-fsck.{service,timer}
└── shift-agent-reconcile.service  # oneshot, runs at boot

/etc/logrotate.d/
└── shift-agent                 # create-mode rotation (no copytruncate)

/var/log/shift-agent-archive/   # rotated decisions.log.gz
```

---

## 3. Configuration schema (`config.yaml`)

```yaml
schema_version: 1    # bump on incompatible changes

customer:
  name: "Triveni Jacksonville"
  location_id: "loc_jax_01"
  timezone: "America/New_York"   # IANA; validated via zoneinfo.ZoneInfo
  languages: ["en", "te", "hi", "ta", "gu"]

owner:
  name: "..."
  phone: "+19045550100"          # E.164 canonical (no dashes); Pydantic rejects otherwise
  self_chat_jid: ""              # auto-populated on first owner inbound; flock'd update

limits:
  max_outbound_per_day: 6
  max_outbound_per_minute: 30
  pending_proposal_ttl_hours: 4
  per_message_timeout_sec: 120
  send_failure_retry_count: 1

alerting:
  pushover_user_key: "..."       # REQUIRED — startup refuses if empty
  pushover_app_token: "..."      # REQUIRED
  healthchecks_io_url: ""        # optional; if set, pinged on every green health check
  email: ""                      # optional secondary channel

backup:
  gpg_recipient_email: "srinivas.yalavarthi@gmail.com"  # PUBKEY only; private key NEVER on VPS
  s3_bucket: ""                  # optional
  retention_days: 30

operations:
  business_hours_local: "08:00-22:00"  # labels alert urgency; agent runs 24/7
```

`Config` Pydantic model validates this at load. Missing required fields → `ConfigError` + immediate exit + Pushover alert to owner. (Pushover-on-config-error is chicken-and-egg only if Pushover itself misconfigured; runbook flags this.)

---

## 4. schemas.py (NEW — central types)

Single Pydantic file imported by every script. ~250 lines. Key excerpts:

```python
# /opt/shift-agent/schemas.py
from pydantic import BaseModel, Field, constr, ConfigDict, model_validator
from typing import Literal, Annotated, Union, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
import re

# ─── Phone canonicalization (review #5 top-4) ───
PHONE_RE = re.compile(r"^\+\d{10,15}$")
PHONE_ANY_RE = re.compile(r"[\+\d]")

class E164Phone(str):
    @classmethod
    def __get_validators__(cls): yield cls.validate
    @classmethod
    def validate(cls, v):
        if not isinstance(v, str): raise TypeError("string required")
        canonical = cls.from_any(v)
        if not PHONE_RE.match(canonical): raise ValueError(f"invalid E.164: {v}")
        return canonical
    @classmethod
    def from_any(cls, raw: str) -> str:
        # handles "+1-904-555-0100", "19045550100@s.whatsapp.net", "+19045550100", etc.
        s = raw.split("@")[0]
        s = re.sub(r"[^\d+]", "", s)
        if not s.startswith("+"):
            s = "+" + s
        return s

# ─── Roles ───
Role = Literal["cashier","bakery","meat_counter","sweets","floor","prep","cook"]

# ─── Employee / Roster ───
EmployeeId = constr(regex=r"^e\d{3,}$")

class Employee(BaseModel):
    id: EmployeeId
    name: str
    nickname: Optional[str] = None
    role: Role
    phone: E164Phone
    languages: list[str]
    can_cover_roles: frozenset[Role]
    status: Literal["active","inactive","terminated"] = "active"
    phone_history: list[dict] = []   # {phone, effective_from, effective_to}
    restrictions: Optional[dict] = None

class ScheduleEntry(BaseModel):
    employee_id: EmployeeId
    shift: constr(regex=r"^\d{2}:\d{2}-\d{2}:\d{2}$")
    role: Role

class Roster(BaseModel):
    location: dict
    employees: list[Employee]
    schedule: dict[str, list[ScheduleEntry]]   # date-string → entries
    _meta: Optional[dict] = None

    @model_validator(mode="after")
    def check_referential_integrity(self):
        ids = {e.id for e in self.employees}
        for date, entries in self.schedule.items():
            for entry in entries:
                if entry.employee_id not in ids:
                    raise ValueError(f"schedule references unknown employee {entry.employee_id} on {date}")
        return self

# ─── Proposal (discriminated union on status) ───
ProposalId = constr(regex=r"^P\d{4,}$")
ProposalCode = constr(regex=r"^#[A-HJ-NPR-Z2-9]{5}$")   # 5 char, excludes 0/O/1/I/L

class _BaseProp(BaseModel):
    proposal_id: ProposalId
    code: ProposalCode
    created_ts: datetime
    last_updated_ts: datetime
    absent_employee_id: EmployeeId
    absent_date: str
    absent_shift: str
    absent_role: Role
    absent_reason: str
    input_message: constr(max_length=4000)
    message_id: str
    candidate_employee_id: Optional[EmployeeId] = None
    candidate_name: Optional[str] = None
    proposed_message_rendered: Optional[str] = None
    status_history: list[dict] = []   # {from, to, ts, cause, actor, event_ref}

class AwaitingProposal(_BaseProp):
    status: Literal["awaiting_owner_approval"]

class ApprovedProposal(_BaseProp):
    status: Literal["approved"]
    approved_ts: datetime
    owner_input: str

class ReconcilingProposal(_BaseProp):
    status: Literal["reconciling"]                  # transient during send
    reconciling_started_ts: datetime
    reconciling_pid: int

class SentProposal(_BaseProp):
    status: Literal["sent"]
    sent_ts: datetime
    outbound_message_id: Optional[str] = None

class SendFailedProposal(_BaseProp):
    status: Literal["send_failed"]
    last_error: str
    retry_count: int
    failed_ts: datetime

class AcceptedProposal(_BaseProp):
    status: Literal["accepted"]
    response_ts: datetime
    response_message: str

class DeclinedProposal(_BaseProp):
    status: Literal["declined"]
    response_ts: datetime
    response_message: str

class DeniedByOwnerProposal(_BaseProp):
    status: Literal["denied_by_owner"]
    denied_ts: datetime
    owner_input: str

class ExpiredProposal(_BaseProp):
    status: Literal["expired"]
    expired_ts: datetime

class CancelledProposal(_BaseProp):
    status: Literal["cancelled"]
    cancelled_ts: datetime
    cancel_reason: str

class NoResponseTimeoutProposal(_BaseProp):
    status: Literal["no_response_timeout"]
    timeout_ts: datetime

Proposal = Annotated[
    Union[AwaitingProposal, ApprovedProposal, ReconcilingProposal, SentProposal,
          SendFailedProposal, AcceptedProposal, DeclinedProposal, DeniedByOwnerProposal,
          ExpiredProposal, CancelledProposal, NoResponseTimeoutProposal],
    Field(discriminator="status")
]

TERMINAL_STATUSES = {"accepted","declined","denied_by_owner","expired","cancelled","no_response_timeout"}
def is_terminal(p: Proposal) -> bool:
    return p.status in TERMINAL_STATUSES

class PendingStore(BaseModel):
    proposals: dict[ProposalId, Proposal]
    next_proposal_seq: int

# ─── send-counter ───
class SendCounter(BaseModel):
    day: str                            # YYYY-MM-DD in customer tz
    count: int
    last_send_ts: Optional[datetime] = None

# ─── seen-ids (tail-logger) ───
class SeenIds(BaseModel):
    seen_message_ids: list[str]
    max_size: int = 10000
    last_offset_bytes: int
    agent_log_inode: int                # NEW: detects rotation

# ─── decisions.log entries (discriminated union on type) ───
class RawInbound(BaseModel):
    type: Literal["raw_inbound"]
    ts: datetime
    message_id: str
    sender_phone: E164Phone
    employee_id: Optional[EmployeeId]
    input_message: str

class ProposalCreated(BaseModel):
    type: Literal["proposal_created"]
    ts: datetime
    proposal_id: ProposalId
    code: ProposalCode
    absent_employee_id: EmployeeId
    candidate_employee_id: Optional[EmployeeId]

class ProposalStatusChange(BaseModel):
    type: Literal["proposal_status_change"]
    ts: datetime
    proposal_id: ProposalId
    from_status: str
    to_status: str
    cause: str
    actor: str                          # "owner" / "agent" / "timer" / "reconciler"

class OutboundAttempted(BaseModel):       # NEW: written BEFORE POST
    type: Literal["outbound_attempted"]
    ts: datetime
    proposal_id: ProposalId
    recipient_employee_id: EmployeeId
    attempt_id: str                     # unique per attempt for idempotency

class OutboundSent(BaseModel):
    type: Literal["outbound_sent"]
    ts: datetime
    proposal_id: ProposalId
    recipient_employee_id: EmployeeId
    outbound_message_id: str
    rendered: str

class OutboundResponse(BaseModel):
    type: Literal["outbound_response"]
    ts: datetime
    proposal_id: ProposalId
    from_employee_id: EmployeeId
    response: Literal["yes","no","unknown"]
    response_message: str

class OutboundCapExceeded(BaseModel):
    type: Literal["outbound_cap_exceeded"]
    ts: datetime
    proposal_id: ProposalId
    reason: str

class AgentStateChange(BaseModel):
    type: Literal["agent_state_change"]
    ts: datetime
    to_state: Literal["enabled","disabled"]
    reason: str

class InvariantViolation(BaseModel):
    type: Literal["invariant_violation"]
    ts: datetime
    check: str
    detail: str

# ... (additional entry types for errors) ...

LogEntry = Annotated[
    Union[RawInbound, ProposalCreated, ProposalStatusChange, OutboundAttempted,
          OutboundSent, OutboundResponse, OutboundCapExceeded, AgentStateChange,
          InvariantViolation],
    Field(discriminator="type")
]
```

Every writer: `entry_instance.model_dump_json()` + `\n`. Every reader: iterate lines, `parse_line(line) -> LogEntry`.

---

## 5. safe_io.py (NEW — shared helpers)

```python
# /opt/shift-agent/safe_io.py
import fcntl, os, json, tempfile, time
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime

LOCAL_FS_CHECK_DONE = False

def assert_local_disk(path: Path):
    """fcntl.flock unreliable on NFS. Abort if state path is remote."""
    global LOCAL_FS_CHECK_DONE
    if LOCAL_FS_CHECK_DONE: return
    st = os.stat(path)
    # st_dev < some threshold is a heuristic; better: check /proc/mounts for the fs type
    # (real impl: parse mount info, fail on nfs/cifs/fuse)
    mnt = subprocess.check_output(["stat","-f","-c","%T",str(path)], text=True).strip()
    if mnt in ("nfs","cifs","fuse.sshfs"):
        raise RuntimeError(f"state path {path} on remote fs {mnt}; flock unreliable")
    LOCAL_FS_CHECK_DONE = True

class FileLock:
    def __init__(self, lockpath: Path):
        self.lockpath = Path(lockpath)
        self.fd = None
    def __enter__(self):
        self.fd = os.open(str(self.lockpath), os.O_RDWR | os.O_CREAT, 0o640)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self
    def __exit__(self, *args):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)

def safe_load_json(path: Path, default=None):
    """Handles missing, empty, and corrupt files with explicit signaling."""
    try:
        if not path.exists():
            return default, "missing"
        raw = path.read_text()
        if not raw.strip():
            return default, "empty"
        return json.loads(raw), "ok"
    except json.JSONDecodeError as e:
        # Rename to .corrupt-<ts>, log, return default
        corrupt = path.with_suffix(path.suffix + f".corrupt-{int(time.time())}")
        path.rename(corrupt)
        return default, f"corrupt:{e}"
    except OSError as e:
        return default, f"oserror:{e}"

def atomic_write_json(path: Path, obj) -> None:
    """fsync + os.replace pattern. Durable even across panics on ext4/xfs."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        os.write(fd, json.dumps(obj, indent=2).encode() if hasattr(obj,"__dict__") else obj.encode() if isinstance(obj,str) else json.dumps(obj).encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    # fsync directory
    dfd = os.open(str(path.parent), os.O_RDONLY)
    try: os.fsync(dfd)
    finally: os.close(dfd)

def ndjson_append(path: Path, entry_json_str: str, lock: FileLock):
    """Single-line append with explicit flock. Assumes caller holds lock already."""
    with open(path, "a") as f:
        f.write(entry_json_str + "\n")
        f.flush()
        os.fsync(f.fileno())

def customer_now(tz_name: str) -> datetime:
    return datetime.now(tz=ZoneInfo(tz_name))

def sweep_orphan_temps(state_dir: Path, max_age_sec: int = 300):
    """Remove .tmp-<pid> files older than max_age_sec."""
    now = time.time()
    for p in state_dir.glob("*.tmp-*"):
        if now - p.stat().st_mtime > max_age_sec:
            p.unlink()
```

Every state-touching script: `from safe_io import FileLock, safe_load_json, atomic_write_json, ndjson_append, customer_now, assert_local_disk`.

---

## 6. Component interfaces (updated)

### 6.1 `identify-sender`
Unchanged semantics. Load-failure distinction:
```
exit 0 + json with role=employee/owner → resolved
exit 0 + json with role=unknown → sender not in roster
exit 2 + json with error="roster_load_failed:<msg>" → input/file problem; dispatcher triggers dead-man
```

### 6.2 `send-coverage-message <proposal_id>` (1-arg canonical)
Flow (with all BLOCKER fixes):
```
1. assert_local_disk(state)
2. Load Config (Pydantic validates)
3. Check disabled.flag → if present: exit 2 + log outbound_refused_disabled
4. With FileLock(send-counter.lock) AND FileLock(pending.lock) held simultaneously:
   a. Load pending.json, parse with Pydantic, find proposal_id
   b. Require proposal.status == "approved" (not "reconciling", not "sent")
   c. Transition proposal → "reconciling" with pid=os.getpid(), atomic_write pending.json
   d. Load SendCounter; if stale day, reset; if count+1 > limit, revert to "approved", exit 3 + log outbound_cap_exceeded
   e. Reserve counter: increment count, atomic_write send-counter.json
5. Release locks (reservations persisted)
6. Render template: load Roster, get candidate phone by employee_id, render via render-coverage-template
7. Generate attempt_id (uuid4); append decisions.log OutboundAttempted BEFORE POST
8. POST http://127.0.0.1:3000/send with {jid, text} (Hermes outbound API)
9. On success:
   a. With FileLock(pending.lock): transition proposal → "sent" with outbound_message_id
   b. Append decisions.log OutboundSent
   c. Exit 0
10. On failure (POST returned non-2xx or timeout):
    a. Retry once after 5s
    b. If still fails:
       - With FileLock(pending.lock): transition proposal → "send_failed" with last_error
       - Decrement counter (atomic_write send-counter): refund the reservation
       - Append decisions.log (custom type: outbound_send_failed)
       - Trigger Pushover alert via shift-agent-notify-owner
       - Exit 4
```

The "reconciling" intermediate status + "outbound_attempted" log entry are the two BLOCKER fixes. If script dies between steps 7 and 9, the reconciler sees status=reconciling + an OutboundAttempted with no corresponding OutboundSent → manual intervention required (no auto-retry).

### 6.3 `create-proposal`
NEW helper script. Called by handle_sick_call skill.
```
usage: create-proposal <absent_employee_id> <absent_date> <absent_shift> <absent_role> <absent_reason> <input_message> <message_id> [candidate_employee_id]
```
Generates next proposal_id + 5-char unique code, assembles AwaitingProposal (Pydantic validates), writes to pending.json under flock, appends ProposalCreated to decisions.log.

### 6.4 `update-proposal-status`
NEW helper script. Called by handle_owner_command / handle_candidate_response skills.
```
usage: update-proposal-status <proposal_id> <new_status> [--cause <str>] [--actor <str>] [--owner-input <str>] [--response-message <str>]
```
Loads pending.json, validates transition is legal (via TRANSITIONS table), updates proposal variant, atomic_write pending.json, appends ProposalStatusChange to decisions.log.

### 6.5 `render-coverage-template`
```
usage: render-coverage-template <template_name> [--fields-json '<json>']
```
Loads template file, interpolates ONLY fields from `--fields-json`. LLM-supplied text goes through `reason_short` (max 60 chars, regex-filtered to alphanumeric + basic punct) before interpolation. Output to stdout.

### 6.6 `log-decision` (existing) + `log-decision-direct` (new)
- `log-decision` (existing): called by skills via Hermes terminal tool. Passes JSON arg, appends to decisions.log.
- `log-decision-direct`: called by scripts directly (NOT via Hermes). Used by kill-switch, reconciler, health-check. Same behavior minus Hermes involvement.

### 6.7 `shift-agent-notify-owner <message>`
Out-of-band notifier. Tries Pushover FIRST (always-available). On success, done. If Pushover fails AND bridge is alive, falls back to WhatsApp self-chat. If both fail: writes to `state/notify-failed.log` + exit non-zero.

**Required for startup:** health-check refuses to start if Pushover unconfigured. This closes the dead-man bootstrap paradox (C3).

### 6.8 `shift-agent-tail-logger.py`
Updated:
- `assert_local_disk` at startup
- Load SeenIds via safe_load_json; on `corrupt` status, rename + restart with empty + fire Pushover alert
- **Inode detection (M2 fix):** stat agent.log; if inode != stored OR size < stored_offset → reset offset=0, log rotation event
- message_id hash includes sender phone (M9 fix)
- On sick-call classification: call identify-sender; on role=employee, write RawInbound to decisions.log
- Use `re.IGNORECASE | re.UNICODE` + expanded patterns + word boundaries for sick-call regex
- All JSON loads via safe_load_json

### 6.9 `shift-agent-health-check.sh`
Updated:
- `set -euo pipefail` (all bash scripts)
- touch `state/last-health-check-ts` on green (watchdog reads this)
- On ANY failure: call `shift-agent-notify-owner` (Pushover primary)
- On green: curl healthchecks.io URL if configured
- WA socket liveness via bridge `/health` endpoint (NOT send-timestamp proxy; M3 fix)
- OpenRouter check validates response is JSON + has data.usage field
- Every check has explicit success check, no silent passes

### 6.10 `shift-agent-health-watchdog.sh` (NEW)
Separate timer every 15 min. Checks `state/last-health-check-ts` age. If > 10 min, fires Pushover alert "HEALTH CHECK ITSELF STOPPED". Independent of health-check.

### 6.11 `shift-agent-backup.sh`
Updated:
- `set -euo pipefail`
- systemctl stop shift-agent-tail-logger.timer BEFORE tar (M3 fix)
- `cp -a /root/.hermes/whatsapp/session /tmp/shift-session-$$/` then tar the copy (M4 fix)
- gpg --recipient only (pubkey mode; M5 fix)
- Round-trip test: `gpg --decrypt` → diff against source → only delete plaintext if match
- `aws s3 sync` conditional on `command -v aws`
- systemctl start shift-agent-tail-logger.timer at end (with trap for failure cases)

### 6.12 `shift-agent-fsck.py` (NEW)
Nightly 03:00 timer (after backup 02:00). Asserts cross-file invariants:
1. Every proposal_id in decisions.log exists (or existed) in pending.json
2. Every `proposal_created` has an eventual terminal status entry
3. SendCounter.count == count of OutboundSent entries in decisions.log for day
4. RawInbound.employee_id → resolvable via roster at that time
5. Code uniqueness among awaiting_owner_approval
6. seen-ids.last_offset_bytes ≤ stat(agent.log).st_size
7. No proposals stuck in `reconciling` older than 10 min (indicates crashed send)

Each violation → append InvariantViolation to decisions.log + Pushover alert to owner.

### 6.13 `shift-agent-reconcile.py` (NEW; boot-time oneshot)
Runs at systemd boot via oneshot unit. For each proposal in pending.json:
- If status == `reconciling` AND age > 5 min: **DO NOT auto-retry.** Leave in reconciling, alert owner via Pushover, require manual resolution. Safer than retry risk.
- If status == `approved` AND no matching OutboundAttempted for this proposal_id in decisions.log: invoke send-coverage-message.
- If status == `approved` AND OutboundAttempted exists but no OutboundSent: leave as reconciling, alert.

Fixes the reconciler-double-send race explicitly.

### 6.14 `shift-agent-disable` / `shift-agent-enable`
Updated:
- `set -euo pipefail` (no `|| true`)
- disable: touch disabled.flag → systemctl stop hermes-gateway + shift-agent-tail-logger.timer → log via log-decision-direct → `shift-agent-notify-owner` (fails loudly if it fails)
- enable: rm disabled.flag → systemctl start gateway + timer → notify owner

### 6.15 `shift-agent-smoke-test.sh`
Updated:
- Pre-deploy test: run against a dry-run endpoint
- Post-deploy test: real end-to-end
- On failure: call `shift-agent-deploy.sh --rollback` + Pushover alert

### 6.16 `shift-agent-deploy.sh` (NEW)
```bash
#!/usr/bin/env bash
set -euo pipefail
ACTION="${1:-deploy}"
REPO=/opt/shift-agent/.git   # actual repo location
TAG_PREFIX="deploy-"

case "$ACTION" in
  deploy)
    cd /opt/shift-agent
    PREV=$(git describe --tags --abbrev=0 --match "${TAG_PREFIX}*" 2>/dev/null || echo "none")
    git pull --ff-only origin main
    NEW_TAG="${TAG_PREFIX}$(date +%Y%m%d-%H%M%S)"
    git tag "$NEW_TAG"
    # install artifacts
    install -m 755 scripts/*.sh /usr/local/bin/
    install -m 755 scripts/*.py /usr/local/bin/
    cp -a skills/* /root/.hermes/skills/
    systemctl daemon-reload
    systemctl restart hermes-gateway
    # smoke test
    if ! shift-agent-smoke-test.sh; then
      echo "SMOKE TEST FAILED — rolling back"
      "$0" rollback "$PREV"
      shift-agent-notify-owner "Deploy $NEW_TAG rolled back — smoke test failed"
      exit 1
    fi
    shift-agent-notify-owner "Deploy $NEW_TAG succeeded"
    ;;
  rollback)
    TARGET="${2:?need target tag}"
    cd /opt/shift-agent
    git checkout "$TARGET"
    install -m 755 scripts/*.sh /usr/local/bin/
    install -m 755 scripts/*.py /usr/local/bin/
    cp -a skills/* /root/.hermes/skills/
    systemctl daemon-reload
    systemctl restart hermes-gateway
    shift-agent-notify-owner "Rolled back to $TARGET"
    ;;
esac
```

---

## 7. Skills (updated)

### 7.1 `dispatch_shift_agent/SKILL.md`
Unchanged routing decision table, but adds candidate branch:
- `fromMe` AND dest=self-chat-JID → `handle_owner_command`
- not `fromMe` AND sender in roster as employee → ROUTE BY: if there's a matching `SentProposal` with `candidate_employee_id` == sender.id AND it's awaiting response → `handle_candidate_response`. Else → `handle_sick_call`.
- unknown → decline

### 7.2 `handle_sick_call/SKILL.md`
Rewritten steps:
1. Acknowledge employee
2. Sanitize input message (strip injection patterns)
3. Call `identify-sender` to confirm claimed identity; ask if mismatch
4. Read pending.json for conflict detection
5. Generate proposal via `create-proposal` script
6. Render owner-facing message via `render-coverage-template proposal_to_owner --fields '{...}'`
7. POST to owner self-chat via Hermes outbound
8. Log via `log-decision` (Pydantic-validated type: ProposalCreated)

### 7.3 `handle_owner_command/SKILL.md`
Parse patterns:
- `#XXXXX` → approve: `update-proposal-status <id> approved` then invoke `send-coverage-message <id>`. Reply confirmation.
- `DENY #XXXXX` → `update-proposal-status <id> denied_by_owner`. Reply confirmation.
- `RETRY #XXXXX` → for send_failed status, transition back to approved; invoke send-coverage-message.
- `CANCEL #XXXXX` → transition to cancelled.
- `STATUS` → list all pending + recent sent.
- `KILL` → invoke `shift-agent-disable`.
- else → help message listing commands.

### 7.4 `handle_candidate_response/SKILL.md` (NEW)
Parse candidate's reply (YES/NO or natural language equivalents) to an outbound coverage message:
1. Load pending.json; find proposal with `candidate_employee_id == sender.id` AND `status == sent`
2. Classify reply: YES / NO / ambiguous
3. If YES: `update-proposal-status <id> accepted`; send owner confirmation via template
4. If NO: `update-proposal-status <id> declined`; notify owner; owner may create new proposal
5. If ambiguous: ask candidate to clarify (YES or NO); do not transition state yet

### 7.5 `roster_lookup/SKILL.md`
Minor update: reads path from config.yaml (default /opt/shift-agent/roster.json).

---

## 8. Dispatcher routing (updated table)

| fromMe | Dest JID | Sender in roster as | Matching sent proposal where candidate_id == sender? | → Handler |
|---|---|---|---|---|
| true | owner self-chat | n/a | n/a | handle_owner_command |
| true | ≠ self-chat | n/a | n/a | IGNORE |
| false | n/a | employee | YES | handle_candidate_response |
| false | n/a | employee | no | handle_sick_call |
| false | n/a | owner (from other device) | n/a | handle_owner_command |
| false | n/a | unknown | n/a | unknown_sender_declined |

---

## 9. Proposal state machine (revised)

```
            [created]
                │
                ▼
    awaiting_owner_approval
     │        │          │
  approved   denied_    expired
     │       by_owner   (ttl timer)
     ▼       (terminal) (terminal)
  reconciling (transient; held briefly by send-coverage-message)
     │
     ▼
    sent ─────────────────────────────┐
     │         │          │           │
  accepted  declined   no_response_   send_failed
 (terminal) (terminal)   timeout       │
                       (terminal)      │
                                       ▼
                                 RETRY #XXXXX
                                 → approved (back)

  cancelled ← CANCEL #XXXXX from owner (any non-terminal → cancelled)
```

Transitions validated by update-proposal-status before write. Illegal transitions rejected + logged.

---

## 10. systemd units (revised)

### 10.1 hermes-gateway.service (full revision)
```ini
[Unit]
Description=Hermes Agent Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=shift-agent
Group=shift-agent
Environment="HOME=/opt/shift-agent"
Environment="HERMES_HOME=/root/.hermes"
EnvironmentFile=/opt/shift-agent/.env
# Idempotent ownership guard for mid-session Baileys writes (review M2)
ExecStartPre=/bin/chown -R shift-agent:shift-agent /root/.hermes
ExecStart=/root/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --replace
Restart=on-failure
RestartSec=30
StandardOutput=append:/opt/shift-agent/logs/hermes-gateway.log
StandardError=append:/opt/shift-agent/logs/hermes-gateway.log

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/shift-agent /root/.hermes
ProtectHome=read-only                   # NOT true — would block /root
PrivateTmp=true                         # per-service /tmp
RuntimeDirectory=shift-agent            # /run/shift-agent for lock files

[Install]
WantedBy=multi-user.target
```

### 10.2 tail-logger timer (same as v1 + assert_local_disk check in script)

### 10.3 health-check timer (every 5 min)

### 10.4 health-watchdog timer (every 15 min, runs shift-agent-health-watchdog.sh)

### 10.5 backup timer (OnCalendar=02:00 local)

### 10.6 fsck timer (OnCalendar=03:00 local)

### 10.7 reconcile service (oneshot, ExecStart=shift-agent-reconcile.py, runs at boot via multi-user.target.wants/)

---

## 11. logrotate config

```
/opt/shift-agent/logs/decisions.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 shift-agent shift-agent
    olddir /var/log/shift-agent-archive
    dateext
    # NO copytruncate — lossy for NDJSON
    # Works because our writers open-append-close per invocation, never cache fd
}

/opt/shift-agent/logs/hermes-gateway.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 shift-agent shift-agent
    copytruncate   # acceptable for this log; it's Hermes stdout
}
```

---

## 12. Security posture (updated)

- `/opt/shift-agent/` owned `shift-agent:shift-agent`, mode 750
- `.env` mode 600, EnvironmentFile= in systemd (not world-readable)
- OpenRouter spending cap SET ON PROVIDER DASHBOARD pre-go-live ($20/mo hard limit)
- Dedicated OpenRouter key (not shared with personal projects)
- Monthly key rotation cron reminder
- GPG pubkey-only mode (private key OFF the VPS)
- Prompt injection sanitizer: regex-strips `<`, `>`, `SYSTEM:`, `USER:`, `IGNORE PREVIOUS`, `DISREGARD`, `OVERRIDE` — applied to all inbound message text before LLM interpolation. Raw text preserved in `input_message` field of logs only.
- Audit log tamper-evidence: SHA-256 chain on `decisions.log.sha256` sidecar (appended per entry).
- Pushover creds in `.env`; compromised VPS = attacker can spam your Pushover — accept this; rotate app_token if seen.

---

## 13. Error paths + recovery (updated)

Added to v1 table:

| Failure | Detection | Recovery |
|---|---|---|
| Pushover down during dead-man | notify-owner returns non-zero | writes to `state/notify-failed.log`; next 5-min health check retries |
| `reconciling` status stale (pid crashed mid-send) | fsck or reconciler timer | alert owner, do NOT auto-retry, manual resolution |
| Schema violation in config/roster | Pydantic ValidationError at load | script exits 5 + Pushover alert with error details |
| Agent log inode changed (rotation) | tail-logger inode check | reset offset=0 + log rotation event |
| SIGKILL orphan temp file | next safe_io call calls sweep_orphan_temps | automatic cleanup |
| Invariant violation (fsck) | nightly fsck | InvariantViolation logged + Pushover alert |
| Vim in-place edit of config/roster mid-read | safe_load_json detects parse error | retry once after 500ms; if still corrupt, fail + alert |

---

## 14. Build order (updated for v2)

Must-build-in-order dependency chain:

1. shift-agent user + /opt/shift-agent/ tree + permissions
2. `schemas.py`
3. `safe_io.py`
4. `exit_codes.py`
5. `/opt/shift-agent/config.yaml` (template, populated with customer data)
6. `identify-sender`
7. `render-coverage-template`
8. `shift-agent-notify-owner` (Pushover; REQUIRED before anything else uses it)
9. `log-decision-direct`, `log-decision` (Phase 0 extension)
10. `create-proposal`
11. `update-proposal-status`
12. `send-coverage-message`
13. `shift-agent-tail-logger.py`
14. `shift-agent-health-check.sh` + `shift-agent-health-watchdog.sh`
15. `shift-agent-backup.sh`
16. `shift-agent-fsck.py`
17. `shift-agent-reconcile.py`
18. `shift-agent-disable` + `shift-agent-enable`
19. `shift-agent-smoke-test.sh`
20. `shift-agent-deploy.sh`
21. Skills: dispatch_shift_agent, handle_sick_call (rewrite), handle_owner_command (new), handle_candidate_response (NEW), roster_lookup (tweak)
22. systemd unit files (all)
23. logrotate config
24. Templates
25. Runbook
26. Local git repo init for PR packaging

---

## 15. Testing approach (updated)

Same stages as v1 + added:

**Stage 0 (pre-build, smoke): Pydantic schemas parse example data without error**
- `schemas.py` → `Config.model_validate(yaml.safe_load(config.yaml))` at script startup
- Same for Roster, PendingStore, SendCounter, SeenIds

**Stage 2 (end-to-end dry-run) additions:**
- Send Ravi + Priya + Suresh scenarios; verify fsck passes all 6 invariants
- Kill send-coverage-message mid-POST (simulate crash); verify reconciler does NOT auto-retry
- Corrupt pending.json manually; verify safe_load_json detects + alerts
- logrotate decisions.log; send one more inbound; verify tail-logger picks up via inode check

**Stage 3 (customer roster) additions:**
- Pushover alert fires + reaches phone (send test)
- healthchecks.io ping received + alert on missed ping (simulate)

**Stage 4 (failure modes) additions:**
- Kill hermes-gateway entirely → dead-man via Pushover within 5 min
- Kill health-check timer → watchdog fires within 15 min
- Stop Baileys bridge (kill node process) → health check detects via /health endpoint → dead-man fires

---

## 16. Open questions (reduced from v1)

All structural open questions resolved in v2 except:
1. Hermes outbound API endpoint + auth — verify in build (will read bridge.js)
2. self-chat JID format — test empirically on customer pair

---

**End of DESIGN v2. Ready for build phase.**
