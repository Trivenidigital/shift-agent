# Credit-burn audit — 2026-05-08

**Trigger:** Vizora burned all OpenRouter credits to HTTP 402 on 2026-05-06.
User asked SMB-Agents to check for the same compounding failures.

**Audit method:** Live runtime-state inspection on srilu-vps (per CLAUDE.md §9
runtime-state verification rule), not source-only review.

**Result:** GREEN today (low-volume + session-level caps mitigate). One HIGH
gap and one MEDIUM gap captured for future revisit (P2.5 in `tasks/todo.md`).

---

## Vizora root cause (the threat model we're auditing against)

| Mode | Mechanism | Multiplier |
|---|---|---|
| F1 | `hard_stop_enabled: false` — tool-error loops accumulate full 10K+ context per retry within one turn | 5–10× per single firing |
| F2 | `max_tokens` left at default 16,384 — model authorized to "reason" 16K tokens per turn for what should be one JSON line | $0.05–$0.20 per firing instead of $0.001 |
| F3 | 5-min cron on a skill that mostly logs "0 tickets found" heartbeats | 288 firings/day |
| Combined | F1 × F2 × F3 | Dollars/hour, silent until 402 |

---

## SMB-Agents check (live srilu-vps, 2026-05-08T13:24:50Z)

### F1 — Indefinite tool-error loop ✓ NOT PRESENT

Hermes config at `/root/.hermes/config.yaml`:
```yaml
agent:
  max_turns: 60
  max_tool_calls: 50
delegation:
  max_iterations: 50
```

Worst-case runaway session = 50 tool calls × 16K tokens × $0.60/MT = **$0.48 ceiling** (hard-bounded by session caps).

### F2 — `max_tokens` defaulting to 16K ⚠️ HIGH GAP, MITIGATED

Source: `/usr/local/lib/hermes-agent/agent/transports/chat_completions.py:270`:
```python
max_tokens = params.get("max_tokens")  # → None when config doesn't set it
```

`/root/.hermes/config.yaml` does **not** set a global `max_tokens`. Per-call, OpenRouter receives `None` and falls back to model default (~16K for gpt-4o-mini).

**Why we're not bleeding right now:** call volume is microscopic.

OpenRouter key live status:
```
usage_daily   $0.00002805
usage_weekly  $2.45938635
usage_monthly $4.91181055
total_usage   $7.77888391
```

Audit-log frequency by type (last 24h, LLM-firing types):
```
   5 brief_sent                       1×/day actual
   5 brief_attempted                  1×/day, mostly degraded-mode
   3 catering_lead_created            per inquiry
   4 menu_update_proposed             per menu image
   3 catering_owner_approval_requested per approval
```

~25 LLM fires/day × 16K output × $0.60/MT = **$0.24/day worst-case**. Actual $0.00003/day.

### F3 — 5-min cron on low-event LLM skill ✓ NOT PRESENT

| Timer | Frequency | LLM call? | Date-gated? |
|---|---|---|---|
| `shift-agent-tail-logger` | 30 sec | No (bash log tail) | n/a |
| `shift-agent-health` | 5 min | **No** (bash `shift-agent-health-check.sh`) | n/a |
| `shift-agent-health-watchdog` | 15 min | No (bash) | n/a |
| `send-daily-brief` | 15 min | Rare | **Yes** — 296 `brief_skipped` vs 5 `brief_sent` |
| `eod-reconcile` | 15 min | Rare | **Yes** — 35 `eod_skipped` vs 4 `eod_snapshot` |
| `check-compliance-deadlines` | daily 06:00 | Yes (rare) | implicit |
| `prune-expense-receipts` | daily | No | n/a |
| `send-routing-accuracy-summary` | weekly Sun 13:00 | maybe | weekly |

Vizora's "288 fires/day on a low-event LLM skill" pattern doesn't exist. Aggressive timers are local-only; LLM-calling timers are date-gated and prove they skip ~99% of fires.

---

## Latent gap — `extract-receipt` (Agent #21, dormant)

`src/agents/expense_bookkeeper/scripts/extract-receipt`, lines 320 + 360 — both OpenRouter payloads lack `max_tokens`:

```python
payload = {
    "model": VISION_MODEL,
    "messages": [...],
    "response_format": {"type": "json_object"},
    "temperature": 0.0,
    # no max_tokens → uses model default ~16K
}
```

Verification this is dormant:
- `expense_bookkeeper.enabled` not set in `/opt/shift-agent/config.yaml` (defaults `false` per pydantic)
- `decisions.log` shows zero `expense_extracted` entries — never fired in production

Convention violation: `parse-menu-photo` (catering vision) explicitly sets `"max_tokens": 8192` at line 177; `vision-auth-smoke` sets `"max_tokens": 4`. `extract-receipt` should follow the same pattern.

---

## Other observations (out of credit-burn scope)

- **Daily-brief in Python error loop** — `TypeError: can't compare offset-naive and offset-aware datetimes` at `send-daily-brief:217` → `log_source.py:91`. Each 15-min fire hits the error in `_aggregate_yesterday`, falls through to `degraded_mode: true`, sends a degraded brief (template-based, no LLM enrichment). **No credit impact** but daily brief is quietly degraded.
- **Disk free on `/opt`** at 4.3 GB and dropping. Composite health check fires every 5 min with `health_check_failure` audit. Threshold is 5 GB. Unrelated to credits but worth a separate ticket.

---

## Deferred items (re-check triggers)

Tracked in `tasks/todo.md` P2.5 "Credit-burn defense (2026-05-08 audit deferrals)":

### R1 — Add `max_tokens` cap to Hermes config

- **What:** Edit `/root/.hermes/config.yaml`'s `agent:` block to add `max_tokens: 4096`.
- **Also:** add `"max_tokens": 4096` to both payloads in `extract-receipt`.
- **Effort:** ~15 min total (config + script + deploy verify).
- **Re-check trigger** (any one):
  - Catering inquiry volume scales >10/day
  - New LLM-calling agent ships (`pnl_anomaly`, `compliance` full prod, `expense_bookkeeper` RealQBOClient)
  - `usage_daily` from OpenRouter key check exceeds $0.10/day for 3 consecutive days
  - Any Hermes upgrade past 0.12.0 (re-verify default behavior)

### R3 — OpenRouter daily-spend alarm

- **What:** Either OpenRouter dashboard email alert at daily threshold OR in-audit-chain check from `shift-agent-health-check.sh` that emits `decisions.log` warning if daily spend > $1.
- **Effort:** 5 min UI / 30 min in-audit-chain.
- **Why both R1 and R3:** R1 is a per-call cap (prevents runaway); R3 is a spend-floor alarm (catches model-pricing changes, new agents bypassing global cap, OR R1 itself getting unset by future config drift).
- **Re-check trigger:** same as R1.

---

## Why deferred (and the discipline gate)

Today's volume is orders of magnitude below the danger zone. Adding caps now is good hygiene, not urgent. The risk is **letting "low volume today" silently turn into "still no cap when traffic 10×s"** — exactly the trap that hit Vizora.

The triggers above are the discipline gate. When ANY of them fires, R1 + R3 promote from "deferred" to "do now."

---

## Memory + backlog updates

- ✅ `tasks/todo.md` P2.5 — added "Credit-burn defense (2026-05-08 audit deferrals)" subsection with R1 + R3 + triggers
- ✅ This audit doc at `tasks/audits/credit-burn-audit-2026-05-08.md`
- ✅ Memory entry at `~/.claude/projects/.../memory/project_credit_burn_audit_2026_05_08.md`
