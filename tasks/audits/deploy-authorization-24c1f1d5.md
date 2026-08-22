# Deploy authorization — 24c1f1d5 (behavioural batch)

**Date:** 2026-08-22 · **Tag:** `deploy-20260822-205012-24c1f1d5`
**Authorized by:** standing autonomous completion mandate.
**Box before:** `6809bd07` · **Rollback:** `deploy-20260822-201150-6809bd07.tgz`
**Artifact sha256:** `f9ff29be51325f0c193ee31769669c8856a5057c24d08d791f36b4b633aa1cde` (matched on box)

Deliberately separated from the earlier additive deploy because this batch
changes behaviour a real person experiences.

## What it arms, and what that cost was predicted to be

| PR | effect at deploy | predicted |
|---|---|---|
| #729 flyer TTL escalation | pages the owner about parked projects | exactly 2 Pushover **priority-2 emergency** alerts on tick 1, 0 after |
| #734 shift owner-approve | arms the coverage return leg | nothing — box had 1 proposal, status `accepted`, none `awaiting_owner_approval` |
| #735 daily brief | owner's morning brief content | no new outbound; the brief already sends daily |

Priority 2 repeats until acknowledged and bypasses quiet hours, so deploy timing
does not soften it. That was accepted rather than avoided: pre-closing F0217 and
F0222 first would have forfeited the live positive control this change exists to
produce, and disposed of two real customer projects by CLI without anyone
looking at them — which is the disposition decision the pages are meant to prompt.

## Pre-deploy state (captured)

- 226 flyer projects: 145 `completed`, 78 `closed_no_send`, **2 `awaiting_final_approval`
  (F0217 `2026-07-11`, F0222 `2026-07-12`)**, 1 `manual_edit_required`.
- 1 shift proposal, status `accepted`.
- Escalation rows in the live audit log: **0**.

## Post-deploy runtime verification — the live positive control

Deploy exit 0, all smoke checks passed. Box `.commit-hash` = `24c1f1d5…`.

**Tick 1** (watchdog runs every 5 min, fired 20:52:01 UTC):

```
flyer_recovery_stale_project_escalated : 2
  F0217  ttl_hours=168  stale_hours=1005
  F0222  ttl_hours=168  stale_hours=985
flyer_recovery_owner_alert             : 2   both outcome="sent"
```

**Tick 2:** escalations still 2, owner alerts still 2 — **zero new**.

That is both #729 blockers proven fixed against production data, not fixtures:
the delivery-equality swallow (these two projects sat exactly on the `>` guard
with `delivered_at == updated_at` to the microsecond) and the hourly-repeat
fingerprint. Two real customer projects invisibly parked for 42 and 41 days are
now in front of the owner for the first time.

All three changes confirmed present in the installed copies: the shift consent
gate (`_is_unqualified_shift_consent`), the brief's "Awaiting your decision"
line, and the `last_activity.isoformat()` term in `canonical_source`. Gateway
and cockpit active; bridge connected, queue 0.

## Not verified

- Whether Hermes surfaces the `shift_agent_read` tools in a live turn's
  `tool_search`. Needs a real inbound; not attempted.
- The shift coverage loop end-to-end. Intake has never fired in production
  (zero `dispatcher_routed` rows all-time, and cf-router writes that row on the
  sick-call path itself), so the newly-armed return leg has had nothing to act
  on. Kill switch without a deploy: `shift-agent-disable` — `send-coverage-message`
  checks `disabled.flag` and exits 2 before any POST.
