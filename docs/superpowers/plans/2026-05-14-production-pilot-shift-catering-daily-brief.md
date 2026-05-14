# Production Pilot: Shift + Catering + Daily Brief Implementation Plan

**Drift-check tag:** extends-Hermes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Shift Agent, Catering Agent, and Daily Brief Agent production-ready for a 3-4 day WhatsApp-first pilot.

**Architecture:** Keep Hermes as the substrate: one WhatsApp gateway, one `dispatch_shift_agent` front door, existing JSON-on-disk state, systemd timers, and existing SKILL/script chokepoints. Add a deterministic production-readiness gate and focused hardening only where drift or runtime state proves a gap.

**Tech Stack:** Hermes Agent v0.13.0, WhatsApp bridge, Python/Pydantic v2, JSON/YAML state, systemd timers, pytest, existing `cf-router` plugin.

---

## Hermes-First Analysis

| Domain | Hermes skill/capability found? | Decision |
|---|---|---|
| WhatsApp ingest/reply | yes - Hermes messaging gateway + deployed bridge | Use it; no new channel code. |
| Routing among agents | yes - `dispatch_shift_agent` SKILL + `cf-router` plugin | Use and harden existing matrix/audit behavior. |
| Catering inquiry/proposals | yes - `catering_dispatcher`, `parse_catering_inquiry`, `creative_catering_proposals`, proposal scripts | Use existing flow; test and close gaps only. |
| Menu image/PDF updates | yes - `update_catering_menu` + `parse-menu-photo` + `productivity/ocr-and-documents` installed live | Use existing preview-confirm flow; clarify authority model. |
| Shift sick calls | yes - `handle_sick_call`, roster schema, proposal scripts, coverage send path | Use existing state machine; test onboarding/readiness. |
| Daily owner summary | yes - `send-daily-brief.timer` + `send-daily-brief` | Use as pilot control tower. |
| Identity learning | yes - `shift-agent-lid-learn` + `lid_learned` audit | Use for phone/LID drift; do not add a parallel identity store. |
| Safe self-evolution | yes - Hermes skills/memory + Self-Evolution Kit exists | Runtime learning may update state/memory; code/SKILL evolution goes through tests + PR + deploy. |
| External integrations | `productivity/google-workspace`, `airtable`, `notion`, `maps`, `mcp/native-mcp` installed live | Defer external OAuth writes for this pilot; local/no-key mode first. |

Awesome-Hermes-Agent ecosystem check: useful discovery surface, but no known external skill replaces this repo's SMB-specific WhatsApp routing, catering approval, and shift coverage state machines. Use Hermes ecosystem substrate; keep business policy local.

## Runtime-State Grounding

- `main-vps` is on commit `f4ce14db72af9a1b16bbbb054a3bb7610f151f74`.
- `hermes-gateway` is active and the WhatsApp bridge reports connected.
- `cf-router` plugin is installed/enabled.
- Timers are active for health, tail logger, daily brief, EOD, catering pattern report, backup, fsck, compliance, and routing summary.
- Live config is still rehearsal-shaped: owner is set, catering is enabled, but `customer.name` and `customer.location_id` are placeholders and `shift`, `daily_brief`, and `multi_location` blocks are absent/defaulted.

## Task 1: Production Readiness Gate

**Files:**
- Create: `src/agents/shift/scripts/pilot-readiness-check`
- Create: `tests/test_pilot_readiness_check.py`
- Modify: `tasks/todo.md`

- [ ] **Step 1: Write failing tests**

Test these behaviors with temp config/state fixtures:

```python
def test_ready_fixture_passes_for_three_agent_pilot(tmp_path):
    result = run_check(tmp_path, ready_config=True, ready_roster=True, ready_menu=True)
    assert result.returncode == 0
    assert '"status": "ready"' in result.stdout

def test_placeholder_customer_blocks_production(tmp_path):
    result = run_check(tmp_path, ready_config=False, ready_roster=True, ready_menu=True)
    assert result.returncode == 1
    assert "customer.name is placeholder" in result.stdout

def test_missing_roster_blocks_shift_agent(tmp_path):
    result = run_check(tmp_path, ready_config=True, ready_roster=False, ready_menu=True)
    assert result.returncode == 1
    assert "roster.json missing" in result.stdout

def test_missing_menu_blocks_catering_agent(tmp_path):
    result = run_check(tmp_path, ready_config=True, ready_roster=True, ready_menu=False)
    assert result.returncode == 1
    assert "catering-menu.json missing" in result.stdout
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
python -m pytest tests/test_pilot_readiness_check.py -q
```

Expected: fail because `pilot-readiness-check` does not exist.

- [ ] **Step 3: Implement the gate**

The script must:

- Load `config.yaml` with `yaml.safe_load` and validate `Config`.
- Load `roster.json` and validate `Roster`.
- Load `state/catering-menu.json` and validate `Menu` when `cfg.catering.enabled` is true.
- Require non-placeholder `customer.name` and `customer.location_id`.
- Require `owner.phone` and `owner.self_chat_jid`.
- Require at least one active employee and at least one schedule entry.
- Require `daily_brief.enabled`.
- Emit JSON summary by default and text summary with `--text`.
- Exit `0` only when all P0 checks pass; exit `1` when any P0 check fails.

- [ ] **Step 4: Verify focused tests pass**

Run:

```bash
python -m pytest tests/test_pilot_readiness_check.py -q
```

Expected: all tests pass.

## Task 2: Menu Update Authority Consistency

**Files:**
- Modify: `src/agents/catering/skills/update_catering_menu/SKILL.md`
- Modify: `tests/test_catering_skill_md.py`

- [ ] **Step 1: Write failing static tests**

Pin that verified employees may submit a menu source only if the owner still applies it, or tighten dispatcher to owner-only. The selected pilot policy is: verified owner or employee may upload the source; only owner can apply the pending update.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
python -m pytest tests/test_catering_skill_md.py -q
```

- [ ] **Step 3: Update the SKILL contract**

Replace the current "ONLY owner can update the menu" wording with "owner or verified employee can submit a source image/PDF; only the owner can apply it using the confirmation code." Keep the hard ban on direct application by the LLM.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
python -m pytest tests/test_catering_skill_md.py -q
```

## Task 3: Pilot Smoke Runbook

**Files:**
- Create: `docs/runbooks/production-pilot-shift-catering-daily-brief.md`
- Modify: `tasks/todo.md`

- [ ] **Step 1: Document the WhatsApp acceptance script**

Include exact smoke cases:

1. Owner sends menu image/PDF with caption `new menu`.
2. Owner applies menu with `#CODE yes`.
3. Customer sends catering inquiry.
4. Customer asks for two proposals.
5. Customer selects option number.
6. Owner approves quote.
7. Employee sends sick-call.
8. Owner approves coverage proposal.
9. Candidate accepts/declines.
10. Owner force-runs daily brief.
11. Run `pilot-readiness-check`.
12. Run `dispatcher-accuracy-report --days 1`.

- [ ] **Step 2: Include pass/fail evidence**

For each case, list expected `decisions.log` entry types and expected owner/customer WhatsApp message.

## Task 4: Verification And Deploy

**Files:**
- Modify only if verification reveals a concrete gap.

- [ ] **Step 1: Local verification**

Run:

```bash
python -m pytest tests/test_pilot_readiness_check.py tests/test_catering_skill_md.py tests/test_cf_router_plugin.py tests/test_daily_brief_schemas.py -q
python -m py_compile src/platform/credential_readiness.py
```

- [ ] **Step 2: Tarball deploy to `main-vps`**

Use existing tarball deploy flow. Never rely on a VPS git checkout.

- [ ] **Step 3: Runtime verification**

Run via the required Windows SSH two-step pattern:

```bash
pilot-readiness-check --text
systemctl list-timers --all
curl -fsS http://127.0.0.1:3000/health
```

Expected before real customer onboarding: readiness may fail on placeholder config/roster. That is a correct failure, not a script failure.

## Completion Criteria

- `pilot-readiness-check` exists locally and on VPS.
- The gate tells us exactly what blocks a real customer pilot.
- Catering menu source/update authority is internally consistent.
- The runbook covers the three-agent WhatsApp smoke path.
- No live self-evolution mutates code or SKILLs without test + PR + deploy.
