# Catering + Flyer Studio — production-readiness continuation (2026-10-08)

**Drift-check tag:** extends-Hermes — repairs existing chokepoints and watchdogs; no new store, router, workflow engine, scheduler, state machine, approval mechanism or notification system.
**New primitives introduced:** None.
**Predecessor:** [catering-flyer-launch-plan.md](catering-flyer-launch-plan.md), [catering-flyer-launch-release.md](catering-flyer-launch-release.md) (PRs #796/#797, deployed `50a1daa0` 2026-10-03).

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Outbound message screening for the owner's chat | None — the front-brain screen is repo-owned (`src/platform/safe_io.py`); Hermes has no outbound policy hook beyond the adapter override already in `shift-agent-policy` | Fix the existing chokepoint (owner exemption); no new mechanism |
| Provider-credit / key-cap monitoring | None in the Hermes skill hub (https://hermes-agent.nousresearch.com/docs/skills/) covers OpenRouter key caps; the repo's `check-openrouter-balance` timer already exists | Extend the existing watchdog |
| Reference-image menu extraction | Existing `openrouter_vision` provider in `src/agents/flyer/reference_extract.py` | Keep; document the price-less-menu limitation (no code change in this pass) |

awesome-hermes-agent (https://github.com/0xNyk/awesome-hermes-agent) checked: nothing replaces repo-specific chokepoint repairs. Verdict: **extend existing kernels; no custom subsystem.**

## Session bootstrap (from the repository)

```
Universal directive: docs/governance/engineering-directive.md v1.2.0 blob 31d7706d42b5a9477ce236f4dcb3841223b74a6f
Project registry:    docs/governance/project-registry.yaml blob 207ad4183e4b0fd37b74eeec29e7dc215546c4cd
Affected projects:   shift-platform (src/platform/safe_io.py), shift-agent (src/agents/shift/scripts/check-openrouter-balance), catering-studio, flyer-studio (runtime/state actions only)
Applicable directives:
  docs/governance/projects/catering-studio.md v1.0.0 blob 858bd9f29d591e023b895ff5cbd2f3804f146ead
  docs/governance/projects/flyer-studio.md    v1.0.0 blob fe45ce8094eb666057e8393be1e989ec64f7c324
  docs/governance/shared-platform-directive.md v1.1.0 blob 9c6ce25607d59ac3dc3918ab4c908c77a65bde5f
Shared-platform impact: YES — safe_io.front_brain_outbound_enforce_enabled gains an owner-directed exemption (see map below)
New subsystem proposed: no
Architecture exception: none
```

### Capability Reuse Map — shift-platform (safe_io owner exemption)
- Requested outcome: owner-destined sends (daily brief, catering owner cards, compliance, STOP acks) are no longer replaced by the generic front-brain ack now that the owner identity sits in `FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST`.
- Affected projects: shift-platform (owner); consumers: daily-brief, catering-studio, flyer-studio, compliance, expense-bookkeeper, shift-agent (all route owner sends through `bridge_post`).
- Applicable directives: engineering-directive §4/§7, shared-platform §2/§3.
- Existing platform/model capabilities reused: existing screen + lint classes untouched.
- Existing deterministic kernels reused: `front_brain_outbound_enforce_enabled`, `_front_brain_normalize_chat_key`, config.yaml owner block (same identities `identify-sender` uses).
- Existing stores/workflows reused: none new.
- Thin adapters: one predicate (`_front_brain_owner_directed`) reading `owner.*` from config.yaml.
- Custom runtime code genuinely unavoidable: ~25 lines in safe_io.py.
- New subsystem: none.
- Evidence existing capabilities were insufficient: decisions.log 2026-10-05..08 — four `front_brain_outbound_refused` rows on the Daily Brief with `template_fallback_used: true`, each followed by `brief_sent` (false success).
- Architecture exception: none.
- Shared-platform impact: default behaviour unchanged for every non-owner chat; OFF path byte-identical; activation posture: production on deploy (no flag), rollback = redeploy prior tarball.
- Other agents affected: daily-brief (restored brief), catering-studio (owner cards no longer substitutable), compliance/expense (same). Per-agent test: enforcement test file covers the predicate used by all of them.
- Vertical E2E proof: next morning's `brief_sent` row must be preceded by NO `front_brain_outbound_refused` row; catering owner card during the live rehearsal arrives with its `#CODE`.

### Capability Reuse Map — shift-agent (OpenRouter key-cap alert)
- Requested outcome: operator is paged when the gateway key's spend cap is near/at exhaustion (today: `limit 30`, `limit_remaining 0`, every model call HTTP 403).
- Affected projects: shift-agent (script owner); beneficiaries flyer-studio, catering-studio (all model calls).
- Existing capabilities reused: `check-openrouter-balance` timer, `shift-agent-notify-owner` chokepoint, §12b dispatched/delivered event pairs.
- Thin adapters: one extra GET to `/api/v1/auth/key`.
- New subsystem: none. Architecture exception: none. Shared-platform impact: none (per-agent script).
- Vertical E2E proof: timer run on the box emits `openrouter_key_limit_alert_dispatched/delivered` while the cap is exhausted; `openrouter_key_limit_ok` after the operator raises it.

### Catering Studio / Flyer Studio
No source change proposed in this pass. Runtime/state actions only (listed under "Proposed runtime actions"); each reuses an existing CLI (`flyer-manual-queue --close`, systemd drop-in removal, `.env` edit via the existing symlink target, `chown` of service-owned state).

## Priority 1 — current truth (verified on main-vps 2026-10-08 13:33Z, read-only probes)

| Item | State |
|---|---|
| Deployed receipt | `50a1daa09cfebd7a6e4f34d1d2645dc75d19e51d` installed 2026-10-03T01:31:24Z = `origin/main` HEAD; deploy artifact `deploy-20261003-013123-50a1daa0.tgz`; rollback `deploy-20261003-004544-0ea5af38.tgz`, `deploy-20260902-015029-a98431f5.tgz` present |
| Services | hermes-gateway / shift-agent-cockpit / catering-owner-action-watchdog active since 2026-10-03 02:08Z; 0 failed units; nginx 127.0.0.1:8080 → cockpit 8081 (`/health` 200) |
| Bridge | connected, queueLength 0, uptime ~5.5 d, `sendReadReceipts:false` |
| Runtime | `/usr/local/lib/hermes-agent/venv` Python 3.11.17 / SQLite 3.53.1; Hermes 0.19.1; rollback venv retained |
| Service account | uid 999 / gid 988 `shift-agent`; gateway ExecStart as shift-agent |
| Readiness | `pilot-readiness-check --text` as shift-agent with gateway env: READY 17/0 |
| Identities | config owner `+17329837841` / `17329837841@s.whatsapp.net` / LID `201975216009469@lid`, authorized_identities []; `identify-sender`: +1732 → roles [employee, owner] (e008); +19802005023 → unknown; bot +918522041562 / `211390371475536@lid` → unknown (no owner grant) ✔ |
| Effective env | `FRONT_BRAIN_OUTBOUND_ENFORCE=1`, `..._ALLOWLIST=+17329837841,201975216009469@lid` (**owner**), `FRONT_BRAIN_CONVERSE=1` with empty `CONVERSE_CHATS`; `CATERING_AUTOMATION_CONTROL_ENABLED=1`, allowlist `17329837841,201975216009469@lid,19802005023`, STOP/TAKEOVER on; `CATERING_ACCEPTANCE_ARM=0`; `CATERING_QUALIFICATION_GATE=0`; `FLYER_STYLE_REGISTERS_ALLOWLIST=+19802005023`; `WHATSAPP_OWNER_JID=918522041562@s.whatsapp.net` (bot; no repo consumer — Hermes-level), `WHATSAPP_HOME_CHANNEL=17329837841@s.whatsapp.net` |
| Catering state | 20 leads (L0017–L0019 AWAITING_OWNER_APPROVAL from +1732, empty items); pricebook v2 `updated_by: manual`, `placeholder:false`, two active packages described "Pilot package for gate test. Owner will replace rates."; menu v3 77 items, **0 serving facts** |
| Flyer state | 228 projects; `manual_edit_required`: F0226 (since 2026-08-01) and **F0228** (2026-10-03 02:39Z, a GENUINE handset request from the owner's LID: "Create similar flyer for Lakshmi's kitchen, same exact items" + menu photo); test customer +19802005023 already owns trial account **CUST0005** ("Petshop St. john's", primary_chat_id `269612545511591@lid`, 1 of 3 trial flyers used, period ended 2026-06-19) |
| Inbound traffic | zero inbound from +19802005023 ever; last genuine inbound = F0228 on 10-03 |

### Findings (mismatches)

| # | Finding | Evidence | Class |
|---|---|---|---|
| F1 | **Owner's daily brief replaced by the generic ack every day since ≥10-05**; `brief_sent` recorded anyway. Cause: owner identities in `FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST` after the 10-03 swap; `_front_brain_outbound_enforce` substitutes and `bridge_post` still returns `sent`. Same path would strip `#CODE` from catering owner cards whenever inquiry text contains a completion verb. | decisions.log 10-05/06/07/08 `front_brain_outbound_refused` hit_values ["scheduled","sent"] + `brief_sent`; `safe_io.py:1331-1348, 1505-1529, 3463, 3522-3537` | runtime config + code (C1) |
| F2 | **OpenRouter key cap exhausted**: `/auth/key` `limit 30, limit_remaining 0`; text and vision completions → HTTP 403 "Key limit exceeded". Account balance $2.56 (watchdog has paged daily since 09-08; a $-0.18 → $2.56 top-up happened ~10-07). No watchdog covers the key cap. | probe7 2026-10-08 | **operator action** + code (C2) |
| F3 | Flyer SLA watchdog pages the owner via Pushover ~every 30 min indefinitely for the two stale manual rows (525 `owner_alert_dispatched` in 8 days). By design until rows close. | `flyer-source-edit-sla-watchdog:172-185`, unit `--repeat-minutes 60` | operator disposition (F0226/F0228) |
| F4 | `flyer-recovery-watchdog` runs as **root** via stale drop-in `/etc/systemd/system/flyer-recovery-watchdog.service.d/10-codex-worker-root.conf` (2026-05-24, Codex worker); repo unit says `User=shift-agent`. Leaves `recovery_incidents.json`, recovery bundles/queue root-owned. `catering-leads.json` also root-owned (0644, dir shift-agent → atomic replace still works). | probe4 | runtime hygiene |
| F5 | F0228/F0226 reference extraction: the uploaded menus carry **no prices**; `reference_extract.py:736` empties facts for `menu_reference` without a pricing fact → `low_confidence` → manual queue. Both genuine "flyer from my menu photo" requests failed on this rule. (Today the same call fails earlier with provider 403 — F2.) | images inspected; `reference_extract.py:730-753` | product limitation (documented, not fixed in this pass) |
| F6 | Catering WhatsApp-only path cannot reach a priced quote: `select-catering-proposal` sizes with `require_confirmed=True` and every item has `serves: null` → "needs restaurant review of serving sizes"; typed tray counts are not extracted. Operator-driven `finalize-catering-menu --selected-items-json` is the supported explicit-quantity route. | subagent read of `select-catering-proposal`, `catering_pricing.quantity_for_guests` | business facts + operator step |
| F7 | `WHATSAPP_OWNER_JID` still = bot JID. No consumer in repo source (only two disabled codex scripts); owner routing uses config.yaml. Not a blocker. | grep | info |
| F8 | `health_check_failure` ×603 (09-30 → 10-05, "tail-logger timer not active") — resolved; health OK since. | probe4 | historical |

## Proposed runtime actions (need operator go — each reversible, backed up, audited)

| # | Action | Why | Rollback |
|---|---|---|---|
| R1 | Raise the OpenRouter **key limit** (openrouter.ai/settings/keys, key `sk-or-v1-9e3…a84`) and top up account credits (≥ $25 for the rehearsal) | F2 — nothing model-backed works until then | n/a (operator account) |
| ~~R2~~ | **Dropped after review.** Emptying `FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST` would also unscreen the owner's LLM chat (gateway seam: throttle, budget, wording screen). The deployed code fix C1 exempts only scripted `bridge_post` sends to the primary owner and keeps the gateway seam screened. | F1 | — |
| R3 | Close F0226 and F0228 via `runuser -u shift-agent -- flyer-manual-queue --close <id> --reason "<text>" --no-notify` (both are the owner's own test projects; `--no-notify` because their customer phone is now the owner) | F3 — stops the alert storm without faking delivery | rows keep history; status `closed_no_send` |
| R4 | Remove `10-codex-worker-root.conf`, `systemctl daemon-reload`; `chown shift-agent:shift-agent` the root-owned files (`state/flyer/recovery_incidents.json`, `recovery_bundles/FRI20261003-*.json`, `recovery_worker_queue/FRI20261003-*.json`, `state/catering-leads.json`, 3 root-owned logs) preserving modes/mtimes | F4 — watchdog must run as the service account | re-create the drop-in |
| R5 | Owner confirms (a) that the two pricebook packages are test rates to keep **active** for the TEST-ONLY quote or should be set inactive; (b) per-tray serving counts for a small pilot subset (or we keep automatic sizing held and use explicit quantities only) | F6 | pricebook import is versioned |

## Code changes (in this worktree)

- **C1** `src/platform/safe_io.py`: owner-directed exemption at the scripted seam only (`_front_brain_outbound_enforce`, called by `bridge_post`), primary owner identities only (`owner.self_chat_jid` / `phone` / `lid`; `authorized_identities` stay screened per their authorization-only contract, `schemas.py:309`); the gateway LLM-reply seam and cf-router CONVERSE admission are untouched. Tests in `tests/test_front_brain_outbound_enforcement.py` (Linux-only; run in Docker). Review findings that shaped this: authorization lens HIGH (gateway seam) + MEDIUM (authorized_identities); runtime lens MEDIUM ×3 on the runtime script (fixed: R2 dropped, R4 quarantines `*.conf` with an abort guard, ownership restored before any watchdog run, no manual start, no synthetic `agent_state_change` rows).
- **C2** `src/agents/shift/scripts/check-openrouter-balance`: `/auth/key` cap check with §12b event pairs + tests in `tests/test_openrouter_balance_alert.py`.
- Deferred (documented): price-less `menu_reference` rule (F5); `bridge_post` returning `sent` after substitution (status widening is rollback-uncovered — see memory `reference_rollback_coverage_four_categories`).

## Evidence record
- Probe outputs: scratchpad `probe1..8.out` (read-only; no sends, no restarts, no state writes; two diagnostic OpenRouter calls returned 403 and cost nothing).
- Images inspected: `F0228-reference.jpg` (Triveni Express weekend specials — item names only), `F0226-reference.jpg` (Om Indian Bistro list — no prices).

## Checklist
- [x] P1 current truth established (above)
- [ ] C1 implemented + red/green + reviewed
- [ ] C2 implemented + red/green + reviewed
- [ ] Operator decisions R1–R5
- [ ] Runtime actions R2–R4 executed with backups + audit rows
- [ ] PR, CI, governance, canonical tarball, deploy, receipt + service-account verification
- [ ] P2 flyer genuine journey (CUST0005 from +19802005023)
- [ ] P3 catering genuine journey (explicit quantities; owner `#CODE approve` from +17329837841)
- [ ] P4 monitoring/rollback verification; runbook
