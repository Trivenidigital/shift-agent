# Deploy authorization — 44b27021

**Date:** 2026-08-22 · **Tag:** `deploy-20260822-222919-44b27021`
**Box before:** `24c1f1d5` · **Rollback:** `deploy-20260822-205012-24c1f1d5.tgz`
**Artifact sha256:** `2aef417716b3d8f146a1336cff57f385cb19870dabfd5df9ec5ed7539db9e296` (matched on box)

## What shipped

| PR | Surface |
|---|---|
| #742 | `approval_code_pools.py` + `cf-router/hooks.py` — intra-pool collisions fail closed |
| #746 | `shift-agent-read-preflight` + an `ExecStartPre=-` on the gateway unit |
| #748 | two tracked drop-ins + an additive drop-in installer in the deploy script |

#742 changes the primitive **every `#XXXXX` approval code flows through**, so this
batch was held for its own window rather than riding along with the behavioural
deploy.

## Pre-deploy state (captured)

Box `24c1f1d5`; gateway active; **no** `IntraPoolCollisionResult` on the box;
`shift-agent-read-preflight` ABSENT; **1** `ExecStartPre` on the live unit;
drop-in SHAs `33c02036…` (policy preflight) and `46fec0a5…` (drain timeout).

## Post-deploy verification

Deploy exit 0, all smoke checks passed. Box `.commit-hash` = `44b27021…`.
Gateway and cockpit active.

**The drop-in installer behaved exactly as predicted from live SHAs** — the
prediction was made before the deploy, from the box copies, and both halves held:

```
dropin unchanged  hermes-gateway.service.d/30-shift-agent-policy-preflight.conf
dropin DIFFERS    hermes-gateway.service.d/20-drain-timeout.conf — NOT overwriting.
```

The DIFFERS case is a **CRLF-vs-LF difference only**. Line endings are
deliberately not normalised before comparing: normalising would let the deploy
claim a match while the box quietly kept Windows line endings in a config file.
It will nag once per deploy until a human resolves it. That is intended.

**Both preflights ran at gateway start**, and the new one is non-blocking by
construction (`ExecStartPre=-` plus a script that always exits 0):

```
ok  C: all 5 declared tools registered under toolset shift_agent_read
ok  D: toolset shift_agent_read is in platform_toolsets.whatsapp and not disabled
OK: shift-agent-read preflight passed (5 tools registered under shift_agent_read;
    registration proven, live discovery still unproven)
OK: shift-agent-policy preflight passed (screening is live)
```

Check D is what makes it meaningful rather than ceremonial: `disabled_toolsets`
suppresses by name and is applied last, so registration alone would not prove
reach. And the preflight's own success line states the limit of its claim —
registration is proven at every boot; **discovery still requires a live inbound
and is not claimed.**

`ExecStartPre` count on the live unit went 1 → 2. `approval_code_pools.py` on the
box now carries the intra-pool guard.

## Finding surfaced by this deploy — NOT actioned, operator decision

The gateway log warns on **every start** that the Hermes venv links **SQLite
3.50.4**, vulnerable to the WAL-reset corruption bug, for `state.db`
(`async_delegation`, **`delivery_ledger`**) and `kanban.db`. `delivery_ledger` is
Hermes's own outbound delivery record, so this is a data-integrity risk on a
money-adjacent store.

The remedy the warning names is `hermes update`. That is exactly the operation
this project pinned Hermes to avoid — a prior version bump removed the patch
anchors and broke WhatsApp. So the fix is an upgrade of a deliberately pinned
runtime, with a known history of breaking the live transport. Neither engineering
nor the operator can clear it cheaply; it needs a decision and a plan, not a
command.

## Not verified

Live `tool_search` surfacing of the five tools. The preflight proves registration
and reachability of the toolset; only a real inbound proves discovery. Stated as
a non-claim in the preflight's own output.
