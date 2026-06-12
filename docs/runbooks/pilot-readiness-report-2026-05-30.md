# Customer-Pilot Readiness Report — 2026-05-30

**Drift-check tag:** extends-Hermes (status snapshot of the deployed pilot
agents, which extend Hermes with custom skills/scripts/state).

**Scope:** Shift sick-call, Catering lead, Daily Brief, and the Commerce-dormant
posture (WhatsApp ordering). Point-in-time snapshot; supersedes nothing — pair
with the operator runbook `production-pilot-shift-catering-daily-brief.md`.

## Deployed version
- **Live deploy:** `deploy-20260530-030230-7e524c2e` (commit `7e524c2`).
  Verified to include every Shift/Catering/Commerce-gate change through PR #357
  (`332ef0e` is an ancestor); hermes-gateway + cockpit active, `/health` 200; the
  only failed systemd unit (`logrotate.service`) was fixed this session.
- **Merged but NOT yet deployed (runtime-affecting):** PR #358 — the
  pilot-readiness Pushover-key check. It takes effect on the **next deploy**
  (the deployed `pilot-readiness-check` does not yet include it). Doc PRs
  #359/#360 need no deploy.

## Flow status

| Flow | Status | Basis |
|---|---|---|
| **Shift sick-call** | **READY** (pending operator provisioning) | Identity (phone/LID, fail-closed), role-gated routing, roster lookup, audit (incl. the now-deployed `validate_failed` variant + pinned SKILL emit), injection/escalation resistance — all deployed + tested. |
| **Catering lead** | **READY** | Text + image/PDF intake, owner/employee/customer permission gates (`EXIT_PRIVILEGE_DENIED` before any lock), state machine, quote truth-guard, audit; deposit path fail-closed while commerce dormant. |
| **Daily Brief** | **READY** (pending owner self-chat) | Enabled + deployed; aggregate/render smoke + timer-liveness freshness checks pass; delivers to owner self-chat. |
| **WhatsApp ordering / Commerce** | **BLOCKED on operator activation** | Built + tested + **dormant** (`commerce.enabled=false`, `provider=placeholder`). Activation is operator-only; the two deployed fail-closed gates (`check-commerce-webhook-subscription`, `check-commerce-stripe-livemode`) protect it. Not a blocker for the three flows above (separate opt-in). |

## Exact operator actions

**Required before pilot (data provisioning — not code):**
1. `roster.json` — ≥2 active employees + a schedule whose `location.id`/`name`
   match `customer.location_id` (validated by `pilot-readiness-check`).
2. `owner.phone` + `owner.self_chat_jid` — real values (proposals + Daily Brief
   deliver to the owner self-chat).
3. **Real Pushover keys** — replace the `MUTED_…` rehearsal keys in
   `/root/.hermes/.env` with a real `pushover_user_key` + `pushover_app_token`,
   or owner out-of-band alerts (sick-call escalation, deploy failures) won't
   deliver. PR #358 makes `pilot-readiness-check` flag this once deployed.
4. Catering: owner uploads the real menu via WhatsApp (Step 1 of the pilot
   runbook); confirm `OPENROUTER_API_KEY` is set (it is, on main-vps) for vision.

**Optional / separate (operator-only, not required for Shift+Catering+Brief):**
5. Activate Commerce/Stripe deposits per `docs/runbooks/commerce-stripe-onboarding.md`
   (Stripe key → webhook subscribe → `stripe_livemode_expected` → provider flip).
   Until then deposits stay fail-closed ("Payment link is not configured yet").

**Operational:**
6. Deploy at the next window so PR #358 (Pushover readiness check) goes live.

## Residual risks
- The **deployed** `pilot-readiness-check` does not yet include the Pushover-key
  check (#358 merged, not deployed) — until the next deploy, a muted/placeholder
  Pushover key is surfaced only by the smoke channel-probe (which already detects
  the `MUTED_` rehearsal keys on main-vps), not by the readiness report.
- The two commerce deploy gates have run live only in **skip mode** (commerce
  dormant); their active-for-Stripe path is unit-tested + runbook-documented but
  unexercised in a live activation (expected — activation is operator-gated).
- A concurrent session deployed `7e524c2` (a Flyer PR) at 03:02 UTC after this
  session's 02:54 deploy; verified it includes all of this session's
  Shift/Catering/Commerce-gate work (no regression).
- `logrotate.service` was failing on a stale pre-session config; fixed (current
  config valid, failed state cleared, audit-log rotation chain restored).
