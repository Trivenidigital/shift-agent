# Solid 17 — Portfolio Consolidation Plan

**Status:** AWAITING APPROVAL — no code changes until owner confirms.

**Decided:** Option A + P&L Anomaly tweak ("Solid 17") per session 2026-04-29.

## Open questions before execution

1. **Scope of scaffolding this session:**
   - (a) Scaffold **only Expense Bookkeeper** now; leave P&L Anomaly Detective as a paper entry in portfolio.md until a customer asks.
   - (b) Scaffold **both** Expense Bookkeeper and P&L Anomaly Detective now (mirror existing Tier-2 stub pattern).
   - **Default unless told otherwise: (b)** — keeps the doc consistent with code; a P&L stub is ~30 lines.

2. **Agent numbering:**
   - (a) **Keep historical 1–20** with reframings noted in place (A11, A17) and new agents as #21, #22. Less churn — no SKILL.md or commit-message references break.
   - (b) Renumber actively to a clean 1–17 list. Cosmetic only; risks confusion when reading older commits.
   - **Default unless told otherwise: (a).**

## Phase 1 — `docs/portfolio.md` update

- [ ] Replace "Portfolio summary" section (lines 694–714) with **"Consolidated Portfolio (Solid 17, 2026-04-29)"**
- [ ] Add 2-line note under each reframed agent's title:
  - [ ] A11 Festival & Event Outreach → "Reframed 2026-04-29 as **Festival & Peak Prep** (ops-side: staffing/inventory/menu signals 3–5 days out, not customer marketing)"
  - [ ] A17 Unit Economics → "Reframed 2026-04-29 as **P&L Anomaly Detective** (light): flag anomalies from POS + cost data, no recipe modeling"
- [ ] Add sub-skill bullets to existing agent specs (additive only, no rewrite):
  - [ ] A1 Shift: `predict_no_show` (from N1)
  - [ ] A2 Catering Lead: `send_deposit_link` (from N11)
  - [ ] A3 Multi-Location: note QuickBooks transfer sync as Phase-2 add to existing `propose_inter_location_transfer`
  - [ ] A4 Daily Brief: `forecast_demand` (from N10), `weekly_owner_load_summary` (from N20)
  - [ ] A6 Inventory: `suggest_use_today_recipe` (from N8)
  - [ ] A7 Supplier: `detect_price_drift` (from N7)
  - [ ] A12 Hiring: `deliver_training_curriculum`, `quiz_via_whatsapp` (from N2)
  - [ ] A13 Compliance: `prefill_servsafe_log` (from N17)
- [ ] Mark explicitly **dropped**: A18 Customer Complaint (folded into A9 + A4), A20 Owner Wellbeing (folded into A4 + platform quiet-hours)
- [ ] Mark explicitly **backlog** (build only on customer demand): A8 Receiving & QA, A19 Equipment & Maintenance, N12 Order Status & Pickup (POS-gated), N14 Upsell, N15 Third-party delivery
- [ ] Add new agent specs at end of doc:
  - [ ] **Agent #21 — Expense Bookkeeper** (full spec block: purpose, skills, data deps, gates, integrations, risks, complexity)
  - [ ] **Agent #22 — P&L Anomaly Detective** (light spec; references retired A17)
- [ ] Update top-of-doc intro to reflect "17 agents in build commitment + backlog of 5"

## Phase 2 — Scaffold Expense Bookkeeper (priority: highest-ROI new agent)

- [ ] `src/platform/schemas.py`:
  - [ ] Add `ExpenseBookkeeperConfig` class (mirror `CashArConfig` shape)
    - `enabled: bool = False`
    - `auto_categorize_threshold: float = 0.85` (LLM confidence floor for auto-tag)
    - `require_owner_approval_for_personal_flag: bool = True`
  - [ ] Register `expense_bookkeeper: ExpenseBookkeeperConfig = Field(default_factory=ExpenseBookkeeperConfig)` in `Config` class
- [ ] `tests/test_tier2_schemas.py`:
  - [ ] Extend "all Tier-2 disabled by default" assertion to include `c.expense_bookkeeper.enabled is False`
- [ ] `src/agents/expense_bookkeeper/__init__.py` (empty module marker)
- [ ] `src/agents/expense_bookkeeper/skills/expense_bookkeeper_dispatcher/SKILL.md`:
  - [ ] Frontmatter: `name`, `description` (one-liner: receipt photo → categorize → QuickBooks)
  - [ ] Phase 0 stub: `cfg.expense_bookkeeper.enabled = False`. Self-declines.
  - [ ] Phase 1 reference: portfolio.md §Agent #21
  - [ ] Hard rules: never auto-categorize personal-vs-business without owner approval; never push to QuickBooks without explicit confirmation in Phase 0–1
- [ ] Run `pytest tests/test_tier2_schemas.py -v` — must be green

## Phase 3 — Scaffold P&L Anomaly Detective (Tier-2 stub) [conditional on Q1]

- [ ] `src/platform/schemas.py`:
  - [ ] Add `PnlAnomalyConfig` class
    - `enabled: bool = False`
    - `margin_drop_alert_pct: float = 0.05` (5% margin drop triggers)
    - `location_underperform_alert_pct: float = 0.15` (15% below baseline)
    - `evaluation_window_days: int = 7`
  - [ ] Register `pnl_anomaly: PnlAnomalyConfig = Field(default_factory=PnlAnomalyConfig)` in `Config`
- [ ] `tests/test_tier2_schemas.py`: extend assertion
- [ ] `src/agents/pnl_anomaly/__init__.py`
- [ ] `src/agents/pnl_anomaly/skills/pnl_anomaly_dispatcher/SKILL.md`:
  - [ ] Phase 0 stub: requires POS + cost data, declines until both configured
  - [ ] Note: replaces never-coded A17 Unit Economics; uses fresh config key `pnl_anomaly`
- [ ] Run `pytest tests/test_tier2_schemas.py -v` — must be green

## Phase 4 — Verification

- [ ] `pytest tests/test_tier2_schemas.py -v` (all green)
- [ ] `pytest tests/` (full suite — sanity, no broken cross-tests)
- [ ] `git status` — only intended files in diff
- [ ] `git diff --stat` — sanity-check line counts (portfolio.md grows ~150 lines; each new agent ~20 lines)
- [ ] Draft commit message:
  > `docs(portfolio): consolidate to Solid 17; scaffold expense_bookkeeper [+ pnl_anomaly]`

## Out of scope for this session

- Phase 1 / 0.2 builds for the new agents (LLM extractor, QuickBooks integration, POS hookup)
- Touching shipped agents' Phase 0 code — sub-skill additions are spec-only in this pass
- Updating `MEMORY.md` portfolio status — do after this lands and tests pass

## Review notes

Append after execution:
- What changed
- What was harder than expected
- Any deviation from this plan
