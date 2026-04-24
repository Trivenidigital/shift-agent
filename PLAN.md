# Shift Agent — First Customer Rollout Plan (v2)

**Version:** 2.0 (post 5-agent review, reviewer feedback incorporated)
**Deadline:** First customer live within 48 hours (by 2026-04-26)
**Author:** Claude (Opus 4.7) for srinivas.yalavarthi@gmail.com

---

## 0. What changed since v1

v1 went through 5 parallel agent reviews. Findings: 7 BLOCKERs + ~10 MAJORs. This v2 incorporates all of them + user's 6 scoping decisions:

**User decisions adopted:**
- Proposal codes (`#A3F2`) for owner approval — YES
- Daily outbound cap is configurable per-customer (default: `max(2, min(50, ceil(roster_size × 0.10)))`), this customer = 6/day — YES
- Customer will notify employees pre-go-live — YES
- Migrate to non-root `shift-agent` service user — YES
- Reason stored as free-text (owners get confused by codes) — YES (trade some compliance tidiness for owner UX)
- 24/7 continuous operation BUT no formal uptime SLA committed; runbook frames as "continuous best-effort + fail-soft dead-man switch" — YES

**Structural changes from v1:**
- Added `pending.json` as authoritative approval-tracker state (replaces "LLM reads decisions.log for state")
- Added `config.yaml` per-customer config file (limits, timezone, languages, owner identity)
- Added `send-coverage-message` signature: takes `(proposal_id)` single arg — script loads pending.json + roster.json to re-resolve everything (updated per DESIGN v2 review — 1-arg canonical)
- Added template-based outbound message body (LLM selects employee_id; message is rendered from template, not LLM free-text)
- Added fcntl locking + NDJSON append convention for all state files
- Added dispatcher JID verification (not just `fromMe: true`)
- Added nightly backup of roster/decisions/baileys_auth to 2nd path
- Added dead-man switch: gateway-down → WhatsApp owner directly
- Added prompt-injection sanitization + OpenRouter spending cap + dedicated key + file-mode 600
- Added correlation ID (`message_id`) threaded through all logs
- Added tail-logger `seen_ids` dedup guard
- Added log rotation (logrotate daily, keep 30)
- Added customer sign-off requirements (Baileys ToS, audit immutability, employee notification)
- Added explicit self-chat round-trip test as build completion criterion

---

## 1. Context

**Phase 0 status:** Complete 2026-04-24. Full end-to-end verified (Ravi scenario, Priya conflict, Suresh zero-coverage). Helper-script log mechanism works. Model swapped to `moonshotai/kimi-k2-thinking`.

**Customer:** SMB ethnic grocery/restaurant, 45 employees, owner communicates in English with staff also speaking Telugu/Hindi/Tamil/Gujarati.

---

## 2. Scope

### In scope for 48h rollout

| Component | Purpose |
|---|---|
| Deterministic tail-logger (with seen_ids dedup) | 100% audit coverage regardless of LLM behavior |
| Phone→roster identity resolver | Maps sender phone → employee / owner / unknown |
| `pending.json` proposal state store | Authoritative state for approval tracking (replaces flat-log matching) |
| Proposal code generator | 4-char alphanumeric (`#A3F2`); included in every proposal message |
| SKILL.md redesign (3 skills) | Dispatcher + handle_sick_call + handle_owner_command |
| Outbound coverage sender (hardened) | `(employee_id, proposal_id, text)` signature; re-resolves phone from roster; enforces daily cap |
| Template-based outbound message body | Rendered from roster fields, not LLM free-text (injection defense) |
| Monitoring cron + dead-man switch | Systemd timer: gateway health + WA socket liveness + OpenRouter health + pending-proposal aging |
| Nightly backup cron | roster.json, decisions.log, pending.json, baileys_auth → 2nd path (gpg-encrypted tarball) |
| Kill switch (`shift-agent-disable`) | Stops agent + notifies owner via dead-man channel |
| Runbook (customer-facing) | How to operate, edit roster, approve, kill-switch, troubleshoot |
| Per-customer `config.yaml` | Limits, timezone, languages, owner identity (makes us multi-customer-ready) |
| Non-root `shift-agent` service user | systemd runs as non-root; deploy key ≠ runtime key |
| Correlation IDs in logs | `message_id` threaded through inbound → skill → outbound for traceability |
| Log rotation (logrotate) | Daily rotation, keep 30 compressed |
| Smoke-test script | End-to-end test post-deploy |
| Customer sign-off doc | Baileys ToS, audit immutability, employee notification — written acknowledgment |

### Explicitly out of scope (Phase 1+)

- Multi-location within one customer (adds location_id scoping complexity)
- Web UI for roster management (customer edits roster.json via runbook)
- Scenarios beyond sick-call (time-off, swap, quit, voice notes)
- Encryption at rest for roster/decisions
- Hash-chain tamper-evident audit log
- Secondary model fallback if OpenRouter degrades
- Multi-VPS redundancy / HA

---

## 3. Architecture

### Data flow (revised for full-auto)

```
         Customer's WhatsApp (linked device)
                    │
      ┌─────────────┴─────────────┐
      │                           │
  Employee msg              Owner msg (fromMe=true
  (allowlisted phone)        AND dest=self-chat)
      │                           │
      ▼                           ▼
  Hermes Gateway → Bridge (Node/Baileys)
      │                           │
      ▼                           ▼
  Dispatcher (skill router, reads identify-sender)
      │                           │
      ▼                           ▼
  handle_sick_call          handle_owner_command
      │                           │
      ├─► roster_lookup          ├─► read pending.json
      ├─► check pending.json     ├─► match proposal code (#A3F2)
      │     for conflicts         ├─► call send-coverage-message
      ├─► generate proposal_id   │     (employee_id, proposal_id)
      ├─► render template        ├─► update decisions.log +
      │     (not LLM free-text) │       pending.json status
      ├─► write pending.json    └─► send owner confirmation
      │     (flock-guarded)
      ├─► POST to owner self-chat
      └─► /usr/local/bin/log-decision
                                            
  Meanwhile, deterministic spine runs independently:
  ┌────────────────────────────────────────────┐
  │ tail-logger (systemd timer, 30s):           │
  │   • tails agent.log                         │
  │   • dedup via .seen_ids.json                │
  │   • writes raw entry for every sick-call    │
  │     inbound to decisions.log                │
  └────────────────────────────────────────────┘
  ┌────────────────────────────────────────────┐
  │ health-check (systemd timer, 5min):         │
  │   • gateway active?                         │
  │   • WA socket alive (last send < 10min)?    │
  │   • OpenRouter reachable + credits > $5?    │
  │   • pending proposals aged > TTL?           │
  │   • disk free > 5GB?                        │
  │   • tail-logger timer active?               │
  │   → unhealthy: WhatsApp owner "AGENT DOWN"  │
  │   → also Pushover/email if configured       │
  └────────────────────────────────────────────┘
  ┌────────────────────────────────────────────┐
  │ nightly-backup (systemd timer, 02:00 local):│
  │   • tar + gpg: roster, decisions, pending,  │
  │     config, baileys_auth                    │
  │   • rotate to secondary path (S3 optional)  │
  └────────────────────────────────────────────┘
```

### Key architectural decisions (v2)

1. **Approval state lives in `pending.json`, not decisions.log.** `pending.json` is the authoritative list of awaiting-approval proposals. Each has a `proposal_id` (e.g. `P0042`) + a short approval `code` (e.g. `#A3F2`). All read-modify-writes to `pending.json` use `fcntl.flock`.

2. **Owner approves by code, not "yes."** Dispatcher sees owner message, extracts `#XXXX` pattern, looks up in `pending.json`. No code = ignore or "I need a code — reply #A3F2 etc." Ambiguity eliminated.

3. **Outbound message is templated, not LLM free-text.** Template: `"Hi {candidate_name}, {absent_employee_name} is out {when} ({reason}). Can you cover the {shift} {role} shift? Reply YES or NO. Thanks! — {owner_name}"`. LLM picks the candidate and the reason-summary string; the wrapping message is roster-fields-rendered. Prompt injection can't reshape the outbound.

4. **`send-coverage-message(proposal_id)` signature (DESIGN v2 canonical).** Single-arg: script loads `pending.json` by proposal_id, verifies status == `approved`, re-resolves candidate phone from `roster.json` via candidate_employee_id in the proposal, enforces daily cap under flock, writes `outbound_attempted` to decisions.log BEFORE POST for idempotency, posts to WA. LLM can't invent phones or send without an approved proposal.

5. **Dispatcher trusts phone identity + JID.** `fromMe: true` alone is insufficient — owner sending to their spouse would trigger handle_owner_command. Dispatcher verifies: `fromMe AND destination_jid == self_chat_jid` → owner command. Otherwise → employee handler (if sender in roster) or ignore (if unknown).

6. **Per-customer config in `/opt/shift-agent/config.yaml`.** Contains limits (daily cap, per-minute rate), timezone, languages, owner identity, customer name. Not checked into code. Default-heuristic for missing limits.

7. **Service runs as `shift-agent` user, not root.** systemd unit has `User=shift-agent`. Deploy key stays mine; runtime key is the service user's. Root still administers.

8. **No database.** JSON-on-disk with file locking. Simpler for 48h + easier to audit + easier to roll back + matches our state volume (dozens of proposals, hundreds of log lines).

9. **Baileys supply chain accepted with customer disclosure.** Not fixable in 48h; customer signs off explicitly.

10. **24/7 continuous operation, no SLA committed.** Dead-man switch fills the reliability gap via owner-facing notification instead of an on-call rotation.

---

## 4. Components (revised list)

### 4.1 Deterministic tail-logger
**File:** `/usr/local/bin/shift-agent-tail-logger.py`
**Unit:** `/etc/systemd/system/shift-agent-tail-logger.timer` (every 30s)
**State:** `/opt/shift-agent/state/tail-logger-seen.json` (message_ids already processed)
**Function:** Tail agent.log since last offset; for each inbound-message line that matches sick-call classifier + sender phone is in roster, write raw audit entry to decisions.log (NDJSON append) with `message_id`, `ts`, `sender_phone`, `employee_id` (from roster), `input_message`, `status: raw_inbound`. Use `flock` on decisions.log while writing.
**Invariant:** every inbound sick-call produces exactly one raw entry regardless of LLM behavior.

### 4.2 Phone→roster identity resolver
**File:** `/usr/local/bin/identify-sender`
**Usage:** `identify-sender +19045550101` → JSON `{"role":"employee","employee_id":"e001","name":"Ravi Kumar"}` / `{"role":"owner","name":"..."}` / `{"role":"unknown"}`
**Called by:** dispatcher skill, tail-logger, approval tracker.

### 4.3 Outbound coverage sender (hardened)
**File:** `/usr/local/bin/send-coverage-message`
**Usage:** `send-coverage-message <proposal_id>` (see DESIGN v2 §6.2 for full flow)
**Flow:**
1. Read `config.yaml` for daily cap + rate limit
2. Read `roster.json` → get candidate phone, name
3. Read `pending.json` → verify proposal_id exists + status == `approved`
4. Read `send-counter.json` → verify today's count < cap
5. Render template from roster fields + proposal fields (never LLM free-text)
6. POST to Hermes gateway outbound API
7. Atomically increment send-counter
8. Append to decisions.log: `{proposal_id, outbound_ts, outbound_recipient_employee_id, status: sent}`
9. Update pending.json: proposal status `approved` → `sent`

Refuses + logs `status: cap_exceeded` / `status: proposal_not_approved` / `status: candidate_not_found` if any check fails.

### 4.4 Pending proposals state + approval tracker
**File:** `/opt/shift-agent/state/pending.json`
**Schema:**
```json
{
  "proposals": {
    "P0042": {
      "proposal_id": "P0042",
      "code": "#A3F2",
      "created_ts": "...",
      "absent_employee_id": "e001",
      "date": "2026-04-25",
      "shift": "09:00-17:00",
      "role": "cashier",
      "candidate_employee_id": "e004",
      "proposed_message": "<rendered template>",
      "status": "awaiting_owner_approval",
      "last_updated_ts": "..."
    }
  }
}
```
**Status transitions:** `awaiting_owner_approval` → `approved` → `sent` → `accepted` / `declined` / `no_response_timeout`. Also `denied_by_owner`, `expired`, `cancelled`.
**Approval tracker (integrated in `handle_owner_command` skill):**
1. Parse owner message for `#XXXX` pattern
2. flock + read pending.json → look up code
3. If found + awaiting_owner_approval → set status `approved`, invoke `send-coverage-message`
4. If found + wrong status → reply "this proposal is already {status}"
5. If not found → reply "I don't recognize that code. Your pending proposals: {list}"

### 4.5 SKILL.md files
- `/root/.hermes/skills/dispatch_shift_agent/SKILL.md` — dispatcher, routes to one of the three based on identify-sender + JID check
- `/root/.hermes/skills/handle_sick_call/SKILL.md` — employee inbound → proposal flow
- `/root/.hermes/skills/handle_owner_command/SKILL.md` — owner approval/query flow
- `/root/.hermes/skills/roster_lookup/SKILL.md` — helper (unchanged)

Each skill uses prompt-injection sanitizer: strip `<...>` patterns, "SYSTEM:", "IGNORE PREVIOUS", "Disregard" from employee message text before interpolating into prompts.

### 4.6 Monitoring cron + dead-man switch
**File:** `/usr/local/bin/shift-agent-health-check.sh`
**Unit:** `/etc/systemd/system/shift-agent-health.timer` (every 5 min)
**Checks:**
- `systemctl is-active hermes-gateway`
- `systemctl is-active shift-agent-tail-logger.timer`
- Bridge port 3000 open
- Last successful outbound timestamp < 30min (WA socket liveness proxy)
- OpenRouter `/api/v1/auth/key` responds + credits > $5
- pending.json: any proposals aged > `pending_proposal_ttl_hours`
- decisions.log writable + < rotation threshold
- Disk free on `/opt` > 5GB

**Alert path:** 
1. Write `/opt/shift-agent/state/health.log` (structured)
2. If unhealthy + last-alert > 30min: send WhatsApp message to owner via outbound sender (template: "AGENT STATUS: {summary}. Handle manually until cleared.")
3. If configured: also hit Pushover / email endpoint

### 4.7 Nightly backup cron
**File:** `/usr/local/bin/shift-agent-backup.sh`
**Unit:** `/etc/systemd/system/shift-agent-backup.timer` (02:00 local)
**Function:** tar roster.json + decisions.log + pending.json + config.yaml + `/root/.hermes/whatsapp/session/` (baileys_auth) → gpg-encrypted file in `/opt/shift-agent/backups/YYYY-MM-DD.tar.gz.gpg`. Retain 30 days. Optional: rsync to S3 if `BACKUP_S3_BUCKET` set in config.

### 4.8 Kill switch + enable
**Files:** `/usr/local/bin/shift-agent-disable` + `/usr/local/bin/shift-agent-enable`
**Disable flow:** systemctl stop hermes-gateway, send owner "AGENT OFFLINE — manual mode" via last-known-good WA route (or fallback Pushover if configured), touch `/opt/shift-agent/state/disabled.flag`.
**Enable flow:** remove flag, systemctl start, send owner "AGENT ONLINE".

### 4.9 Runbook
**File:** `/opt/shift-agent/runbook.md` (copy delivered to customer separately)
**Sections:** 
- Getting started (first-day pairing walkthrough)
- Daily use (approving proposals via codes)
- Editing roster + schedule
- Monitoring + what alerts mean
- Kill-switch + emergency procedures
- Troubleshooting common issues
- What the 24/7-no-SLA framing means
- Customer sign-off page (Baileys ToS, audit immutability, employee notification)

### 4.10 Deploy + smoke-test
**File:** `/usr/local/bin/shift-agent-smoke-test.sh`
**Function:** Run after every deploy. Simulates one inbound via bridge API, verifies: identify-sender correctly classifies, dispatcher routes, handle_sick_call produces a proposal with a code, pending.json updates, tail-logger captures the raw inbound. 10 lines max. Exits non-zero on any failure.

### 4.11 Per-customer config
**File:** `/opt/shift-agent/config.yaml`
**Schema:**
```yaml
customer:
  name: "..."
  timezone: "America/New_York"
  languages: ["en", "te", "hi"]

owner:
  name: "..."
  phone: "+1-..."

limits:
  max_outbound_per_day: 6
  max_outbound_per_minute: 30
  pending_proposal_ttl_hours: 4

alerting:
  pushover_user_key: ""
  pushover_app_token: ""
  email: ""

backup:
  s3_bucket: ""
```

---

## 5. Work partition (unchanged from v1, slightly expanded hours)

### Claude-side (~14-18h)
All code + SKILL.md + systemd units + runbook draft + local git repo.

### User + offshore (~10h)
- Customer roster data (Day 1 AM)
- Owner phone + customer name/config values
- Customer expectation alignment + sign-off doc
- Employee notification sent BEFORE go-live
- Rehearsal with actual roster (Day 2 AM)
- Final go/no-go

### Customer (~2h)
- Fill in roster questionnaire
- Send employees the pre-go-live notice
- Link Hermes as linked device on Day 2
- Read runbook + sign the 3-disclosure form

---

## 6. Timeline (slightly revised)

### Day 1 (2026-04-25)
- 00:00-06:00 Claude: finish design phase, design review launched
- 06:00-14:00 Claude: build all components in parallel with design-review feedback incorporated
- 06:00-12:00 (parallel) User/offshore: roster data collected, config.yaml populated, employee notice sent
- 14:00-18:00 Claude: PR packaging + 5-agent PR review
- 18:00-22:00 Claude: address PR feedback, smoke tests pass locally
- 22:00-24:00 User: rehearsal with customer's real roster (simulated inbounds, no actual outbound yet)

### Day 2 (2026-04-26)
- 00:00-06:00 buffer/sleep
- 06:00-10:00 customer pairs Hermes, smoke test with 2 test employees, outbound unblocked
- 10:00-12:00 final dry-run of full cycle (including a real sent outbound to a staff member who's in on the test)
- 12:00 GO LIVE (first real employee sick-call processed)
- 12:00-24:00 active watch, respond to issues

Critical path: roster data → build → rehearsal → go-live. Dead on roster delay >Day 1 noon = slips to Day 3.

---

## 7. Risks + mitigations (revised)

| Risk | Severity | Mitigation |
|---|---|---|
| Owner approval matched to wrong proposal | High (was BLOCKER) | RESOLVED: proposal codes (#A3F2); pending.json state; dispatcher requires code match |
| fromMe misclassified as owner command | High (was MAJOR) | RESOLVED: dispatcher checks destination_jid == self_chat_jid |
| Self-chat routing doesn't work as expected | High (was MAJOR) | Explicit round-trip test in smoke-test.sh; if it fails, go-live slips |
| LLM hallucinates employee_id → wrong outbound recipient | High (was MAJOR) | RESOLVED: send-coverage-message re-resolves phone from roster.json, refuses unknown employee_id |
| Prompt injection via employee message | Medium | Sanitizer regex strips known patterns; template-based outbound can't be reshaped by LLM |
| Outbound runaway (loop bug, injection) | High (was BLOCKER) | RESOLVED: daily cap enforced in script, not just skill logic |
| Audit log gap due to LLM skip | High | RESOLVED: tail-logger writes raw entry regardless of LLM (seen_ids prevents dups) |
| Concurrent write corruption | Medium (was MAJOR) | RESOLVED: fcntl.flock on pending.json; NDJSON append on decisions.log |
| Gateway down, silent failure during business hours | High (was BLOCKER) | RESOLVED: dead-man switch → owner gets "AGENT DOWN" WhatsApp within 5 min |
| Data loss from bad edit / disk issue | High (was BLOCKER) | RESOLVED: nightly gpg-encrypted tar; baileys_auth included |
| Employee consent / GDPR exposure | High (was BLOCKER) | RESOLVED: customer sends pre-go-live notification; signed acknowledgment |
| WhatsApp rate-limit / ban of customer's number | Medium | Baileys max-per-min ceiling; warm-up (don't blast 20 outbounds in first hour); kill-switch ready |
| OpenRouter outage mid-proposal | Medium | Dead-man fires; owner handles manually; proposal stays `awaiting` in pending.json, resumable when OR comes back |
| Roster drift (employee quit, not removed) | Medium | Runbook teaches editing; agent politely declines unknown sender → owner gets notified; weekly prompt in owner's self-chat "please confirm roster is current" |
| Side-channel: employee asks candidate directly before owner approves | Medium | Proposal includes "If you've already asked someone, reply CANCEL #A3F2"; UX guidance in runbook |
| Timezone confusion ("tomorrow" at 11pm) | Medium | `customer.timezone` in config.yaml; skill computes target date in that tz; ambiguous times (<02:00) → skill asks "do you mean the shift that starts X?" |
| SIM swap / device compromise | Low | Disclosed; runbook covers "unlink immediately" |
| Baileys ToS / WhatsApp number ban | Low-Medium | Disclosed; kill-switch ready; warm-up messaging pattern |
| Audit log tampering | Low | Disclosed (checksum-only immutability); owner gets monthly backup digest email |
| OpenRouter key theft | Low | Spending cap + dedicated key + file-mode 600 under service user |

---

## 8. Success criteria — go/no-go

### Must pass before go-live
- [ ] Self-chat round-trip test passes: owner sends to self-chat → gateway receives → response delivers back
- [ ] Tail-logger captures 100% of 10 simulated sick-call inbounds (dedup verified with duplicate submission)
- [ ] Identity helper resolves every roster phone + owner phone + rejects unknown
- [ ] End-to-end cycle with REAL outbound to a staff member: employee msg → proposal+code to owner → owner replies `#XXXX` → coverage msg sent to candidate → candidate replies YES → owner gets confirmation. Every step logged. No human intervention past owner approval.
- [ ] Daily outbound cap honored (attempt 7 on a 6/day cap → refused + logged)
- [ ] Ambiguous owner "yes" without code → polite "please reply with code"
- [ ] `fromMe` to non-self-chat JID → ignored by dispatcher (tested by owner sending WA to another person)
- [ ] Kill-switch stops + notifies owner within 5s
- [ ] Health check fires correctly when gateway stopped (verified by stopping + waiting)
- [ ] Nightly backup ran once + backup file exists + gpg-decryptable + contents match live files
- [ ] Customer has signed 3-disclosure doc
- [ ] Employees have received pre-go-live notification
- [ ] Runbook is in customer's hands + they've acknowledged the business-hours-supervised-beta framing

### Nice to have (not blocking)
- Pushover / email alerting wired
- S3 backup bucket configured
- logrotate verified rotating after first week

---

## 9. Rollback (unchanged from v1)

- **Soft:** `shift-agent-disable` — data preserved, systemd stopped, owner notified
- **Hard:** disable + unlink device from customer's WA Linked Devices + (optional) `rm -rf /opt/shift-agent /root/.hermes/skills/{dispatch_shift_agent,handle_sick_call,handle_owner_command}`
- Customer returns to manual. decisions.log preserved for any dispute review.

---

## 10. Runbook requirements (new section)

Runbook MUST cover (these drive customer sign-off + onboarding):

1. **First-day pairing walkthrough** (screenshots of WA Linked Devices flow)
2. **How to approve a proposal** (the `#A3F2` code UX)
3. **How to add / remove an employee** (edit roster.json; where the file is; validation step)
4. **How to edit this week's schedule** (edit roster.json schedule section)
5. **What each health-check alert means** and what to do
6. **Kill-switch** — exact command or a "reply KILL to the self-chat" alternative
7. **What "24/7 best-effort, no SLA" means** in practice
8. **Three sign-off acknowledgments:**
   - I understand my WhatsApp number uses an unofficial client (Baileys); Meta may restrict it
   - I understand the audit log is checksum-protected but not cryptographically immutable
   - I have sent my employees the pre-go-live notification and will keep the roster current
9. **How to reach the developer** (during beta period)
10. **What data is stored and where** (for employee questions)

---

## 11. Open questions resolved during v2

From v1 §10:
1. Hermes outbound-send API confirmed (bridge port 3000; design phase will spec the exact endpoint from Hermes source)
2. Self-chat routing — deferred to smoke-test; must pass before go-live
3. K2-thinking behavior re. allowlist + dispatcher — verify in build smoke test
4. WhatsApp rate limit — Baileys ~60/min; config max_outbound_per_minute=30 gives safety margin

New open questions for design phase (§9 in next doc):
- Exact Hermes gateway outbound API endpoint + auth (need to inspect bridge.js)
- self-chat JID format on different WhatsApp versions (iOS vs Android may differ)
- Whether Hermes supports non-root `User=` in systemd without gateway breaking (file path permissions)
- Whether K2-thinking's tool-use reliably populates proposal_id in the right spot

---

**End of PLAN v2. Ready for design phase.**
