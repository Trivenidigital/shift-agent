# Catering Studio — Dark-Deploy Preflight & Verification (P0-3)

**Scope:** deploying `dba6ee3` (Catering Studio MVP) to main-vps with every
customer-facing catering behavior dormant. Companion to
`docs/runbooks/catering-rollback.md` (P0-2) and
`tasks/catering-studio-mvp-completion.md` (flag inventory).

**Release identity:** merge `dba6ee3c15b0eb48a4b332ba7254df42f1f87f02`;
tree `a7b5912f750a343043ef512004ce7045223135dc` — byte-identical to CI head
`1da6798` (Linux send-path 4630 passed / 34 skipped / 0 failed, run
30664265688), so CI evidence transfers to the deploy tree by tree identity.

## 1. Pre-restart environment (exact code-defined names)

Set BEFORE restarting hermes-gateway. Names are read call-time from the
process environment (source anchors in parentheses). Edit the **target** of
the `.env` symlink (`/root/.hermes/.env`) — never `sed -i` the symlink.

| Variable | Dark value | Default if unset | Anchor |
|---|---|---|---|
| `CATERING_QUALIFICATION_GATE` | `0` **(MUST set — default is ON)** | `1` = ON | cf-router hooks `CATERING_QUALIFICATION_GATE_ENV`, default `"1"` |
| `CATERING_ACCEPTANCE_ARM` | unset or `0` | OFF | hooks `CATERING_ACCEPTANCE_ARM_ENV`, default `"0"` |
| `CATERING_FOLLOWUP_ENABLED` | unset | OFF | catering-followup-sweep |
| `CATERING_FOLLOWUP_ALLOWLIST` | unset/empty | admits nothing | catering-followup-sweep / catering_followups |
| `CATERING_FOLLOWUP_AUTOSEND` | unset | OFF | catering-followup-sweep |
| `CATERING_PROPOSAL_SWEEP_ENABLED` | unset | OFF (also: no timer exists) | catering-proposal-expiry-sweep |
| `CATERING_LEAD_TTL_SWEEP_ENABLED` | unset | OFF (pre-existing) | catering-lead-ttl-sweep |
| `CATERING_AUTOMATION_CONTROL_ENABLED` | unset | OFF (pre-existing PR#653) | automation_control |
| `CATERING_AUTOMATION_CONTROL_ALLOWLIST` | unset/empty | disabled fail-closed | automation_control |
| `CATERING_STOP_ENABLED` / `CATERING_TAKEOVER_ENABLED` | unset | OFF | automation_control |
| `CATERING_AMENDMENT_DISCRIMINATOR` | unset | OFF (pre-existing) | cf-router actions |
| `GATEWAY_TURN_SEND_BUDGET_ENABLED` | unset | OFF (pre-existing) | safe_io |
| `GATEWAY_TRANSPORT_EVIDENCE_ENABLED` | unset | OFF — socket never bound (pre-existing) | transport_evidence |
| `FRONT_BRAIN_OUTBOUND_ENFORCE` (+ allowlist) | leave as currently armed | scoped | safe_io (pre-existing, unchanged) |

Config (`config.yaml` on box, not env):
- `catering.deposit_pct: 0` — **verify from effective runtime config**, not the
  template (the mint chokepoint admission is new in this release; 12 mint
  guards remain but the operator ruling requires 0 until pilot).
- `catering_followup.enabled` — absent/false (schema default false).

Systemd units: `catering-followup-sweep.timer` installs but is **not
enabled** by deploy — verify `systemctl is-enabled catering-followup-sweep.timer`
returns `disabled`/`not-found` after deploy.

## 2. What dark mode does and does not guarantee

With the table above applied:
- No qualification questions are emitted (gate OFF → the pre-MVP F14
  sample-menu ack behavior is byte-identical to `dc7a81a2`).
- No acceptance bookings, no follow-up scheduling/cards/sends, no proposal
  expiry, no automation-control acks, no transport budget, no evidence socket.
- Flyer / Shift / commerce paths untouched (their flags unchanged).

**NOT guaranteed — read carefully:** F7 primary-mode lead creation is
pre-existing LIVE behavior. An organic catering inquiry after restart will
write a lead, and the new release serializes the 12 new (default-valued)
fields onto every lead it writes. That first write is exactly the P0-2
rollback hazard. Mitigations, in force together:
1. `catering-state-downgrade` (P0-2, tested) restores old-schema readability;
   procedure in `docs/runbooks/catering-rollback.md`.
2. Snapshot before restart (step 3) gives an atomic pre-deploy restore point.
If a zero-lead-write dark window is required, it can only be achieved by
containment (Stage A allowlist) — which is its own human-gated step — not by
any flag in this release.

## 3. Deploy sequence (operator)

1. Snapshot state (atomic restore point, cheap):
   `tar czf /root/pre-dba6ee3-state-$(date +%s).tgz -C /opt/shift-agent state logs/decisions.log`
2. Apply §1 environment to the `.env` target; `systemctl show-environment`
   sanity if using set-environment.
3. Standard tarball deploy of `dba6ee3` (build → scp → `shift-agent-deploy.sh`;
   all existing gates — Hermes pin, venv import smoke, env symlink — apply).
4. Post-restart verification (§4). Rollback anchor: current
   `deploy-20260729-021058-dc7a81a2` + the snapshot from step 1.

## 4. Post-restart dark verification (read-only)

Run on-box; expected results in parentheses:

1. `catering-control-status` (kernel flags all OFF/unset; no held leads;
   disabled.flag absent; per-conversation modes empty or unchanged).
2. `python3 -c 'import os; print(os.environ.get("CATERING_QUALIFICATION_GATE"))'`
   inside the gateway service environment (`systemctl show hermes-gateway -p Environment`)
   → `0`.
3. Send **nothing**. Passively observe the next organic non-catering inbound
   in `/opt/shift-agent/logs/hermes-gateway.log` — routing unchanged
   (dispatcher_routed rows normal). Note this is a FILE, not journald: the unit
   sets `StandardError=append` there, so `journalctl -u hermes-gateway` shows
   systemd lifecycle only and would read as "no inbound arrived".
4. `tail decisions.log`: no `catering_lead_qualification_updated`,
   `catering_followup_*`, `catering_lead_acceptance_recorded`, or
   `catering_automation_control_changed` rows post-restart.
5. `systemctl is-enabled catering-followup-sweep.timer` → not enabled;
   `test -S /run/shift-agent/transport-evidence.sock` → absent.
6. Cockpit `/api/catering/dashboard` (via Studio) loads with readiness flags
   showing pricebook absent/placeholder and kernel disarmed — proves the
   read path without any write.
7. If an organic catering inquiry arrives during observation: confirm the
   customer received the F14-style ack (no questions), the lead row exists,
   and note that the P0-2 hazard window is now open (snapshot + downgrade
   both available).

## 5. Activation order (post-dark, each its own explicit authorization)

Per the activation-closure ruling: real pricebook import + verification →
PR #644 ruling/merge → Stage A identities + containment → arm
`CATERING_QUALIFICATION_GATE=1` scoped to the pilot →
observe → `CATERING_ACCEPTANCE_ARM=1` → follow-up flags (requires the §12a
followups-store watchdog from the deferred backlog first).
