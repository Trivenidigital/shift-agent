# Agent matrix, routing census, and a severity correction — 2026-09-01

**Drift-check tag:** `Hermes-native` — an audit record; no runtime code, schema,
skill or config is introduced by this document.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Readiness/routing audit record | none found — repo convention is `tasks/audits/` | use the existing convention (no code) |

Verdict: **documentation only.**

**Supersedes** the "NOT COMPLETED" section of
`session-handoff-2026-09-01.md`, which stated no matrix was produced. Three
research agents reported after that record was written.

---

## 1. SEVERITY CORRECTION — a live authentication-bypass primitive, fix merged but NOT deployed

`cockpit-backend-deploy-gap-2026-09-01.md` recorded the stale cockpit as a
routine deploy gap. **It is not routine.** Verified by reading the deployed
files on 46.62.206.192:

```
/opt/shift-agent/cockpit/backend/app/routers/config.py:52
    sensitive = settings.sensitive_config_fields & body.fields.keys()
/opt/shift-agent/cockpit/backend/app/config.py:101
    jwt_ttl_hours: int = 24
```

That is the plain set intersection — the pre-#777 version. `_set_dotted`
(deployed, lines 39-46) writes at whatever depth the caller names.

**So on the box today:** a session holding any valid `hjwt` cookie, of any age
up to 24 hours, with **no freshness requirement and no OTP**, can send

```
PATCH /config {"fields": {"owner": {...}}}
```

The key `"owner"` does not intersect the string `"owner.phone"`, so the
step-up is never demanded, and the entire owner block is replaced.

**Why that is a takeover and not just a config edit:** `is_owner_chat()`
(`cf-router/actions.py`) fast-paths on strict equality against
`owner.self_chat_jid` and grants owner authority with **no roster
cross-check at all**. Rewriting the owner block therefore hands WhatsApp-side
owner authority to an attacker-controlled chat id.

**This bug predates #777.** `owner.phone` was already in
`sensitive_config_fields`; the plain-intersection check has always missed the
ancestor case. #777's `_sensitive_touched` closes it — and #777 is merged and
not running.

**Scoping it honestly, both directions:**

- It requires an already-valid session. It is a privilege-escalation and
  step-up-bypass primitive, not an unauthenticated entry point.
- The cockpit is loopback-only: nginx `127.0.0.1:8080`, uvicorn
  `127.0.0.1:8081`, only SSH exposed. Reaching it needs an SSH tunnel.
- **`ufw` is inactive**, so that containment rests entirely on bind addresses,
  with no second layer.
- OTP *delivery* is not redirected by this: `issue_otp()` reads
  `alerting.pushover_*` for the channel, and those keys are separately gated.
  The takeover is of WhatsApp-side owner authority, not of the OTP channel.

**Recommendation: raise the cockpit deploy from "recommended" to "do this
first."** The blocker is unchanged — `web/deploy/deploy.sh` runs
`rsync -az --delete web/frontend/dist/`, which is destructive without a local
frontend build — so it remains an operator action, but the cost of waiting is
now a live bypass rather than a merged-but-idle improvement.

## 2. CORRECTION TO MY OWN BRIEF — `disabled_toolsets` does not mean what I told the agents

I briefed both research agents that a route depending on a disabled toolset is
unreachable, naming `skills` among them. **That premise was wrong**, and both
agents caught it independently. Verified myself on the box:

```
/usr/local/lib/hermes-agent/toolsets.py:193-195
    "skills": { ... "tools": ["skills_list", "skill_view", "skill_manage"] }
```

The `skills` toolset gates only those three CRUD tools. SKILL content still
injects into the prompt. Also: the disabled list has **seven** entries, not
six — `file` is disabled too, which I did not have.

**The mechanism that IS dead is `terminal`.** `dispatch_shift_agent/SKILL.md`
(read on the box) states the agent *"MUST use the `terminal` tool to execute
shell scripts in the exact order below"* and is itself invoked *"via
`skill_view`"*. Both are disabled, so the LLM-driven dispatch matrix cannot
execute — doubly.

The corrected conclusion is the same shape but for a different reason, and the
difference matters for any future fix: restoring `skill_view` alone would not
revive the matrix; `terminal` is the binding constraint. Re-enabling either is
**not authorized** and is not proposed here.

## 3. AGENT MATRIX — 18 agents, from current evidence

Two agents produced independent matrices that agree on every status. Registry
question resolved: **18 agent directories exist, not the "Solid 17"** of
`docs/portfolio.md`, which self-describes as *"a planning surface… 60-70%
accurate at best."* `tools/skills-manifest.txt` (deploy-gated sha256 list, 32
skills, byte-identical to `/root/.hermes/skills/`) was treated as ground truth
for "shipped and present."

| status | count | agents |
|---|---|---|
| PRODUCTION_READY | 2 | daily_brief, eod_reconcile |
| DEPLOYED_AWAITING_LIVE_E2E | 2 | shift, equipment_maintenance |
| PARTIAL | 6 | catering, flyer, expense_bookkeeper, compliance, multi_location/customer_location_query, (see note) |
| NOT_REACHABLE | 8 | cash_ar, employee_docs, hiring, inventory, pnl_anomaly, sales_tax, supplier, vip |
| NOT_IMPLEMENTED | 2 | catering_followup, multi_location_query (both self-declared unwired in-repo) |

**Only two routing paths actually work**, both bypassing the LLM entirely:
`cf-router`'s `pre_gateway_dispatch` (native Python, pre-LLM) and the
`shift-agent-read` plugin's five standalone tools. Anything reached by neither
is unreachable regardless of how complete its handler is.

**The traffic caveat that qualifies every number:**
`/opt/shift-agent/config.yaml:13` reads
`owner: name: "Srini (rehearsal owner)"`. **Every non-zero N traces to two or
three operator/tester identities.** No agent has been proven against a paying
customer. Two inflated-looking counts were debunked before use:
`owner_alert_dispatched`=711 is one watchdog alert repeating hourly, and
`flyer_source_edit_sla_alert`=439 is one stuck project (F0226) since Aug 14 —
neither is business traffic.

**Cheapest to genuinely promote**, agreed by both agents:

1. **compliance** — the daily cron already fires and the owner-gated tool
   already works (2 real calls at `agent.log:4496` and `:4691`, 2026-08-08).
   Blocked only by a missing config flag and zero seed data. Zero code risk.
2. **multi_location/customer_location_query** — code-complete, public,
   stateless; `cfg.multi_location` is absent from the box config entirely, so
   the only live customer has zero configured locations.
3. **equipment_maintenance** — same shape as compliance; **zero invocations
   across the full log retention back to ~2026-05-21**, not merely in-window.
4. **catering** — already live; its one on-record real event half-failed
   (`menu_update_applied`=1 paired with `catering_menu_pricebook_sync_failed`=1,
   same 2026-08-23 event). A bounded, already-occurred defect, not new wiring.

## 4. NEW ROUTING FINDING — 5 of 6 active employees cannot be identified by LID

`EmployeeIn`/`EmployeePatch` in the deployed cockpit have no `lid` field, so a
posted lid is silently dropped. `identify-sender._match_employee` resolves
LID-based identity **only** by exact match against `roster.json`'s
`Employee.lid`, with no fallback.

Live roster: **5 of 6 active employees have `lid=None`**; only 2 of 8 rows
carry one. So for those employees, `is_verified_employee_chat` /
`has_employee_capability` **fail closed to "not an employee"** whenever their
account presents by LID rather than phone-JID — which silently disables F9
sick-call and every employee-gated cf-router arm for them.

Fail-closed is the right direction, but the effect is a silently unreachable
agent for most of the roster.

**NOT DETERMINED:** whether this has actually cost a real sick-call. The
schema gap and the roster data are established; per-employee evidence of which
identifier form each account presents is not. The named next check is a
`decisions.log` grep for `identity_unresolved_turn_yielded` correlated to
those five phones.

## 5. What both censuses checked and found ABSENT

Reported so a real negative can be told from unsearched ground: classifier
precedence / arm swallowing; dead deterministic admission; message-shape
mismatch; `LogEntry` Literal silently dropping rows (87 emitted `reason=`
values diffed against 102 Literal members — zero gaps, remediated since the
historical occurrence); `safe_io` bypass (zero raw writes, 45 safe_io sites);
suppression/kill-switch bypass; skills-manifest drift (no orphan this time);
legacy scalar `role` (all three sites read `roles` first, scalar only as a
documented rollback fallback).

**Two latent items worth a note, neither live:** `cf-router`'s
`_expense_leads_path()` ignores `SHIFT_AGENT_EXPENSE_LEADS_PATH` while
`approval_code_pools.py` honours it — moot while agent #21 is not live. And
two plugins register `pre_gateway_dispatch`; cf-router wins only because
`sorted(path.iterdir())` puts it first alphabetically, with no explicit
priority field. Correct today, fragile by construction.

## 6. Remaining NOT_DETERMINED, carried honestly

- Whether the LLM-driven dispatch matrix has any non-`terminal` path to write
  its mandatory `dispatcher_routed` audit row.
- Why `cfg.compliance.enabled` was never set although the reactive tool works —
  deliberate two-tier design or oversight.
- Whether the 2026-05-03 finding that no Hermes/MCP tax-filing or supplier
  integration exists still holds; not re-run this pass.
- Roughly `hooks.py:1336-8163` was sampled, not exhaustively walked.
