# HERMES_AGENT_INVENTORY

**Evidence:** runtime-derived from the active `hermes-gateway` process on each host
(`/proc/<pid>/cmdline`, `/proc/<pid>/environ`, `systemctl show`), plus repository evidence.
**Dates:** 2026-08-01T23:xx – 2026-08-02T00:xxZ. **Read-only. Nothing modified.**

## 0. Fleet baseline — CORRECTED

All three hosts run **Hermes 0.19.1** on the **active runtime**. The fleet upgrade is complete,
as the operator stated.

| Host | Role | Active interpreter | HERMES_HOME | Version | Pkg git | Plugins |
|---|---|---|---|---|---|---|
| srilu-vps | canary | `/home/gecko-agent/.hermes/hermes-agent/venv/bin/python` | `/home/gecko-agent/.hermes` | **0.19.1** | not a checkout | none |
| main-vps | production | `/root/.hermes/hermes-agent/venv/bin/python` | `/root/.hermes` | **0.19.1** | `cc4cab2f5` | `cf-router`, `shift-agent-policy` |
| vpin-vps | production | `/usr/local/lib/hermes-agent/venv/bin/python` | `/root/.hermes` | **0.19.1** | `cc4cab2f5` | none |

**Retraction.** An earlier interim note claimed srilu-vps ran 0.14.0. That was read from
`/usr/local/lib/hermes-agent` — a **stale root-owned reference install (May 22) that the active
service does not use**. srilu's runtime lives under the `gecko-agent` user's home. The claim is
withdrawn as a `STALE_FINDING`. Two operational lessons, both now applied throughout this
inventory: **derive the interpreter from the running process**, and **set `HERMES_HOME` from that
process's environment** before running any `hermes` command — a default-home query returns a
different fleet.

**Host identity caveat:** srilu-vps and vpin-vps both report hostname `ubuntu-4gb-hel1-1`.
They are distinct machines (machine-ids `845c76e1095b` vs `14516f91c9da`). Never key fleet
automation on hostname here.

## 1. Real LLM-mediated agents

Only **main-vps runs WhatsApp** (`WHATSAPP_ENABLED=true`; the other two have the platform key
present but unset). Consequently every customer/owner-facing Hermes agent lives on main-vps, and
the absence of `shift-agent-policy` screening on the other two hosts is **correct scoping, not a
gap** — verified before asserting.

### 1.1 main-vps — 32 agent skills (the actual agent fleet)

| Family | Skills | Channel | Deterministic services called |
|---|---|---|---|
| **Shift** | `dispatch_shift_agent` (router), `handle_owner_command`, `handle_sick_call`, `handle_candidate_response`, `roster_lookup` | WhatsApp | `create-proposal`, `send-coverage-message`, `update-proposal-status`, `identify-sender`, `validate-sender-block` |
| **Catering** | `catering_dispatcher`, `parse_catering_inquiry`, `creative_catering_proposals`, `handle_catering_owner_approval`, `handle_catering_menu_finalize`, `apply_catering_menu_decision`, `update_catering_menu`, `catering_followup_dispatcher` | WhatsApp | `create-catering-lead`, `send-catering-ack`, `apply-catering-owner-decision`, `catering-mint-deposit`, `finalize-catering-menu` |
| **Flyer** | `flyer_dispatcher`, `flyer_intake`, `flyer_generation` | WhatsApp | `create-flyer-project`, `send-flyer-package`, `finalize-flyer-assets`, `visual_qa`, `creative_firewall` |
| **Expense** | `parse_receipt_photo`, `expense_bookkeeper_dispatcher`, `handle_expense_owner_approval` | WhatsApp | `extract-receipt`, `apply-expense-decision` |
| **Commerce** | `commerce_payment_confirmed` | WhatsApp | `commerce-payment-confirm`, `payment_link` |
| **Query//lookup** | `compliance_owner_query`, `customer_location_query`, `multi_location_query` (shelved) | WhatsApp | compliance + location stores |
| **Dormant / scaffolded** | `cash_ar_dispatcher`, `employee_docs_dispatcher`, `equipment_maintenance_dispatcher`, `hiring_dispatcher`, `inventory_dispatcher`, `pnl_anomaly_dispatcher`, `sales_tax_dispatcher`, `supplier_dispatcher`, `vip_dispatcher` | WhatsApp | mostly scaffold only |

**Production-status caveat:** the last row is *registered* (installed + enabled) but several have
no evidence of live use; `multi_location_query` was explicitly shelved (main `9fb8543`). Per the
brief's standard, registration ≠ active. These are marked `REGISTERED_INACTIVE` in the matrix.

### 1.2 srilu-vps — gecko-alpha domain agents

`coin_resolver`, `crypto_narrative_scanner`, `kol_watcher`, `narrative_alert_dispatcher`,
`narrative_classifier` (all `local`). Owned by the Gecko workstream; **out of scope for
Shift/Catering/Flyer adoption** but in scope for the security precedent (§3, and
`SKILL_SECURITY_REVIEW.md`).

### 1.3 vpin-vps — Vizora domain agents

`vizora-customer-lifecycle`, `vizora-shadow-operations` (local). Vizora runs on **vpin-vps**, not
a separate host. vpin also carries the ML/fine-tuning cluster (`axolotl`, `unsloth`, `outlines`,
`fine-tuning-with-trl`).

## 2. Deterministic workflows — direct vs indirect applicability

Per the correction: the timer is usually `NOT_APPLICABLE` for a skill, but its **surrounding
operator workflow** often benefits. Both columns reported.

| Workflow | Host | Direct skill applicability | Indirect (operator/agent workflow) skill applicability |
|---|---|---|---|
| `shift-agent-health-watchdog`, `shift-agent-fsck`, `check-corrupt-state`, `alert-integrity-watchdog` | main | `NOT_APPLICABLE` — deterministic checks | **HIGH** — investigation + evidence interpretation when a check fires → `org/runtime-effective-diagnosis` |
| `shift-agent-proposal-sweep` | main | `NOT_APPLICABLE` | MED — operator explanation of *why* a proposal expired |
| `send-daily-brief`, `eod-reconcile` | main (+srilu) | `NOT_APPLICABLE` — deterministic aggregation | **HIGH** — report generation / narrative summarisation of the same data |
| `catering-pattern-report` | main | `NOT_APPLICABLE` | HIGH — interpretation + follow-up recommendation |
| `flyer-source-edit-sla-watchdog` | main | `NOT_APPLICABLE` | MED — remediation planning when SLA breaches |
| `check-compliance-deadlines` | main (+srilu) | `NOT_APPLICABLE` | HIGH — operator guidance on the specific deadline |
| `prune-expense-receipts`, `shift-agent-backup` | main | `NOT_APPLICABLE` | LOW |
| gecko cron watchdogs (`source-calls-lag`, `held-position-price`, `stop-loss-fn-audit`, `revival-verdict`, `acceleration-heartbeat`, …) | srilu | `NOT_APPLICABLE` | MED — Gecko-owned; exception handling + evidence interpretation |
| `apex-agent kalshi_daily_report`, `15minuteprofitable` | vpin | `NOT_APPLICABLE` | LOW — out of scope |
| `shift-agent-cockpit` (FastAPI) | main | `NOT_APPLICABLE` — deterministic admin API | LOW |
| `codex-*` units (`auth-guard`, `autonomous-dev-srilu`, `readonly-operator-brief`) | srilu, vpin | `NOT_APPLICABLE` | MED — overlaps `subagent-driven-development` |

**Net:** ~35 deterministic workflows, **zero** of which should receive a skill directly; ~8 have
genuine indirect value, and those cluster into two org skills
(`org/runtime-effective-diagnosis`, `org/incident-evidence-report`) rather than eight separate
ones — see `CUSTOM_SKILLS_BACKLOG.md`.

## 3. Classification summary

| Class | Count | Notes |
|---|---|---|
| Real active LLM agents (main-vps) | ~20 of 32 | Shift/Catering/Flyer/Expense/Commerce/query |
| Registered but inactive | ~9 | dormant dispatchers; `multi_location_query` shelved |
| Domain agents, other workstreams | 7 | 5 gecko (srilu) + 2 vizora (vpin) |
| Deterministic services/timers/cron | ~35 | **not agents**; indirect value only |
| Same runtime identity under different names | — | `dispatch_shift_agent` is the single WhatsApp entry point; per-family "dispatchers" are skills it routes to, not separate runtimes |

**Not verified (stated, not assumed):** per-agent memory/session namespace, per-agent toolset
binding, and last-invocation evidence per skill. `hermes skills list` does not expose routing
bindings, and I did not enable any tracing. Recorded as a gap in `SKILLS_ADOPTION_PLAN.md`.
