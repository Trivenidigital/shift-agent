# Production Pilot Runbook: Shift + Catering + Daily Brief

**Drift-check tag:** extends-Hermes

This runbook validates the first production pilot bundle: Shift Agent,
Catering Agent, and Daily Brief Agent over one WhatsApp business number and one
owner self-chat.

## Preconditions

- `/usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/pilot-readiness-check --text`
  has no `FAIL` rows.
- `hermes-gateway` is active.
- WhatsApp bridge health endpoint returns `{"status":"connected"}`.
- `shift-agent-tail-logger.timer`, `shift-agent-health.timer`,
  `send-daily-brief.timer`, and `catering-pattern-report.timer` are active.
- Owner phone/LID and employee phones/LIDs are seeded or learnable.
- `cfg.catering.enabled=true`.
- `/opt/shift-agent/state/catering-menu.json` has the current menu.

## Runtime Commands

Use the Windows SSH two-step pattern from AGENTS.md. Redirect SSH output to a
file first, then read the file.

```bash
ssh main-vps '/usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/pilot-readiness-check --text' > .ssh_pilot_readiness.txt 2>&1
```

```bash
ssh main-vps 'systemctl is-active hermes-gateway; curl -fsS http://127.0.0.1:3000/health; systemctl list-timers --all --no-pager | grep -E "shift-agent|send-daily-brief|catering-pattern-report"' > .ssh_pilot_runtime.txt 2>&1
```

## Permissions (owner / employee / customer)

Identity is set by `identify-sender` (`sender_role` from roster/config metadata,
never message text — see Shift dispatcher). Catering actions are gated by role:

| Action | Owner | Employee | Customer / unknown |
|---|---|---|---|
| Submit catering inquiry (`parse_catering_inquiry`) | ✅ | ✅ | ✅ |
| Upload menu source (`update_catering_menu` / `parse-menu-photo`) | ✅ | ✅ | — |
| **Apply menu** (`apply-menu-update`) | ✅ | ❌ | ❌ |
| **Approve / edit / reject quote** (`apply-catering-owner-decision`) | ✅ | ❌ | ❌ |
| Request / select proposals, finalize own selection | via owner flow | — | ✅ (own lead) |

Owner-only operations reject non-owner senders with exit code **12
(`EXIT_PRIVILEGE_DENIED`) before any state read or lock** — verified at
`apply-catering-owner-decision:368` and `apply-menu-update:82`, covered by
`tests/test_catering_privilege_escalation.py` (32 cases). A non-owner who
forwards an owner's `#XXXXX` code cannot apply it.

## Smoke Script

### 1. Owner Or Employee Uploads Menu Source

Send from owner or verified employee WhatsApp:

```text
new menu
```

Attach the menu image or PDF in the same message.

Expected owner WhatsApp response:

```text
⚕ Catering Agent
────────────
Menu Update <id> — preview
...
To apply this menu, reply: #XXXXX yes
```

Expected audit:

- `dispatcher_routed` with `routed_to_skill="update_catering_menu"`
- `menu_update_proposed`

### 2. Owner Applies Menu

Owner replies:

```text
#XXXXX yes
```

Expected audit:

- `dispatcher_routed` with `routed_to_skill="apply_catering_menu_decision"`
- menu update applied audit row

Expected state:

- `/opt/shift-agent/state/catering-menu.json` `updated_at` moves forward
- prior menu is retained in `/opt/shift-agent/state/catering-menu-archive/`

### 3. Customer Sends Catering Inquiry

Send from customer phone:

```text
Need catering for 80 people next Friday. Mix veg and non-veg options.
```

Expected customer WhatsApp response:

- Catering Agent acknowledgment with lead reference.
- No price, deposit, payment, or booking confirmation.

Expected audit:

- `cf_router_intercepted` or `dispatcher_routed`
- `catering_lead_created`
- `catering_owner_approval_requested` or active lead status row
- `catering_customer_ack_sent`

### 4. Customer Requests Proposals

Send from the same customer phone:

```text
Please send two proposal menus: one balanced mixed veg/non-veg option and one premium option.
```

Expected customer WhatsApp response:

- Two numbered proposal options.
- Menu item names are grounded in `catering-menu.json`.
- No price, deposit, payment, or booking confirmation.

Expected audit:

- `cf_router_intercepted` with proposal-request reason
- `catering_proposals_generated`

### 5. Customer Selects Option

Send:

```text
Go with option 2
```

Expected customer WhatsApp response:

- Confirms the selected option was sent for owner review.
- Does not claim final approval or booking.

Expected audit:

- `catering_proposal_selected`
- `catering_menu_finalized`
- `catering_quote_attempted`

### 6. Owner Approves Final Quote

Owner replies with the active catering approval code:

```text
#XXXXX approve
```

Expected customer WhatsApp response:

- Final quote/owner-approved message.

Expected audit:

- `catering_owner_decision`
- `catering_quote_sent`
- `catering_lead_status_change`

### 7. Employee Sends Sick Call

Send from roster employee phone:

```text
I am sick today and cannot come for my evening shift.
```

Expected owner WhatsApp response:

- Coverage proposal with a 5-character approval code.

Expected audit:

- `cf_router_intercepted` with sick-call alert reason, or `dispatcher_routed`
- `proposal_created`

### 8. Owner Approves Coverage Proposal

Owner replies:

```text
#XXXXX approve
```

Expected candidate WhatsApp response:

- Coverage request sent to selected candidate.

Expected audit:

- proposal status changes from awaiting owner to sent/reconciling path
- outbound send audit for candidate message

### 9. Candidate Accepts Or Declines

Candidate replies:

```text
Yes, I can cover it.
```

Expected owner WhatsApp response:

- Candidate accepted/declined status update.

Expected audit:

- `dispatcher_routed` with `routed_to_skill="handle_candidate_response"`
- proposal status changed to accepted or declined

### 10. Force Daily Brief

Run:

```bash
ssh main-vps 'send-daily-brief --force --force-resend' > .ssh_daily_brief.txt 2>&1
```

Expected owner WhatsApp response:

- Daily Brief Agent message in owner self-chat.
- Includes alerts/recent activity from catering and shift flows when present.

Expected audit:

- `brief_attempted`
- `brief_sent`

### 11. Optional Catering Learning Summary

This section is opt-in. It is safe to deploy while disabled because
`daily_brief.sections` does not include `catering_learning` by default.

First generate and inspect the counts-only sidecar as the service user:

```bash
ssh main-vps 'sudo -u shift-agent /usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/catering-pattern-report --dry-run --learning-days 30' > .ssh_catering_learning_dry_run.txt 2>&1
```

If the dry-run output is safe, write the sidecar:

```bash
ssh main-vps 'sudo -u shift-agent /usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/catering-pattern-report --learning-days 30' > .ssh_catering_learning_write.txt 2>&1
```

Expected state:

- `/opt/shift-agent/state/catering-learning-summary.json` exists.
- The file contains only counts, freshness, and degraded-source names.
- It does not contain customer names, phone numbers, raw inquiry text,
  addresses, prices, payment terms, or raw off-menu item text.

Only after reviewing the dry-run output should the operator add
`catering_learning` to `daily_brief.sections`.

## Evidence Pull

After each live test, pull a narrow audit slice:

```bash
ssh main-vps 'tail -n 250 /opt/shift-agent/logs/decisions.log' > .ssh_decisions_tail.txt 2>&1
```

Then inspect `.ssh_decisions_tail.txt` locally. The audit chain must show both
the route and the state transition for every customer-facing or employee-facing
send.

## Stop Conditions

Pause the pilot and do not onboard another customer if any of these occur:

- WhatsApp bridge is disconnected.
- `pilot-readiness-check` reports a P0 failure other than an intentional
  placeholder in a rehearsal VPS.
- Customer receives pricing/payment/booking language before owner approval.
- Menu proposal contains items not present in the current menu.
- A sick-call state change occurs without a corresponding audit row.
- Daily Brief does not fire or cannot send to owner self-chat.

## Rollback

Deploys are tarball-based with a smoke gate. **On smoke-test failure the deploy
auto-rolls-back to the previous tarball** (no operator action needed); the run
exits non-zero and fires a Pushover P2.

Manual rollback (a regression that passed smoke but misbehaves live):

```bash
ssh main-vps 'sudo /usr/local/bin/shift-agent-deploy.sh list' > .ssh_deploy_list.txt 2>&1
# pick the last-known-good deploy-YYYYMMDD-HHMMSS-<hash> tag, then:
ssh main-vps 'sudo /usr/local/bin/shift-agent-deploy.sh rollback <deploy-tag>' > .ssh_rollback.txt 2>&1
```

Rollback restores that tarball's `src/`, reinstalls, restarts hermes-gateway +
cockpit, and re-runs the smoke test against the restored version. It does NOT
touch customer / menu / roster / lead state files (data, not code) — and the
catering stores are forward-compatible (`extra="ignore"`), so rolling code back
does not corrupt existing `catering-leads.json` / `catering-menu.json`. If the
rollback target itself fails smoke, the deploy stops and fires a Pushover P2 —
SSH in and triage; do not chain another rollback.
