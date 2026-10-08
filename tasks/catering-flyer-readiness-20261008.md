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

---

## Session 2 — 2026-10-08 15:00–16:00Z (operator-authorized continuation)

Operator authorization (2026-10-08): push/PR/merge after CI, canonical tarball, deploy + verify runtime/rollback; R4 service-account/drop-in repair; R3 NOT authorized (F0226/F0228 are genuine owner requests — preserve, fix the underlying failure, present disposition separately); close three product gaps through existing workflows.

### Executed
- **PR #798** (`launch/catering-flyer-readiness-20261008`, c0bb4955 + 969e4a78 doc-lint fix) — all CI checks green on 969e4a78 (first run: 6539 passed / 1 failed `test_deploy_provenance_receipt::test_nothing_points_an_operator_at_the_receipt_as_the_sole_source`, runbook cited the receipt without `.commit-hash`; fixed). Squash-merged → **main `8411b8c3`**.
- **Canonical tarball** built from a bare clone of main at 8411b8c3 inside `python:3.11-slim` (git archive-equivalent, `tools/build-deploy-tarball.sh --skip-pytest` because the full gate ran in CI): sha256 `fb86307f5046bc822eeb726da59ac001d5ce23ad355a740519415442b2a5b553`, 421 files, every byte and executable mode verified equal to Git. Shipped to `main-vps:/tmp/shift-agent-deploy.tgz`; remote checksum verified before extraction.
- **Deploy attempt `deploy-20261008-155259-8411b8c3`** (staged script via bash, no pin override needed): pin gate, skills manifest, env-symlink gate, drop-ins all OK; **FAILED at the vision-auth smoke** (`vision-auth-smoke: AUTH FAIL — HTTP 403`) because the OpenRouter per-key cap is exhausted (finding F2). The deploy script auto-rolled back to `deploy-20261003-013123-50a1daa0`; verified after rollback: `.commit-hash` and `DEPLOY_RECEIPT.json` = 50a1daa0 (installed 15:53:03Z), hermes-gateway / cockpit / catering-owner-action-watchdog / three timers active, bridge `connected` queue 0, old `safe_io.py` restored (no `exempt_owner`), 0 root-owned state files. The gate has no override (`shift-agent-deploy.sh:1507-1523`, fail-closed by design) and was not bypassed. **Redeploy 8411b8c3 is gated on the key-cap raise (human action R1).** The snapshot `deploys/deploy-20261008-155259-8411b8c3.tgz` is NOT a deploy record — it is the staging snapshot of the failed attempt.
- **R4 executed 15:19:14Z** (`runtime_actions_R3_R4.sh`, DO_R4=1): timer paused → `10-codex-worker-root.conf` moved to `/root/quarantine/codex-dropins-20261008T151914Z/` → `daemon-reload` → unit now `User=shift-agent Group=shift-agent HOME=/opt/shift-agent` → re-owned 7 root-owned files under `state/` (recovery_incidents.json, 2 recovery_bundles, 2 recovery_worker_queue, catering-menu-archive/menu-v2-1787512327.json, **catering-leads.json (root-owned since 2026-09-02)**) + 3 logs, modes preserved → gate 0 root-owned → timer re-armed. Verified: tick 15:21:10Z ran under the repaired unit, `recovery_incidents.json` rewritten at 15:21 and still `shift-agent:shift-agent`, 0 root-owned files after a further tick.
- **R3 not executed.** F0226 (2026-08-01, raw request "Create flyer from uploaded template/reference. Customer requested: Update menu") and F0228 (2026-10-03, "Create similar flyer for Lakshmi's kitchen, same exact items") belong to **CUST0001 = Lakshmi's Kitchen, +17329837841 (the owner's own flyer account, trial, period ends 2026-10-09)**, both `manual_edit_required / reference_low_confidence`, `extracted_facts: []`, 0 concepts. The SLA watchdog still pages hourly for both (`customer_update_due: true`). Disposition options for the operator: (A) keep queued and re-run them after the price-less fix (G1) deploys and the key cap is raised; (B) close with a customer notice; (C) close silently (`--no-notify`). Recommendation: A.

### C1 boundary verification (before deploy; explorer map of every `bridge_post` caller)
- Owner-directed `bridge_post` bodies are fixed templates: `send-daily-brief:1033/1039` ("no LLM in v0.1"), `check-compliance-deadlines.py:403/407`, `finalize-catering-menu:1208`, `apply-catering-owner-decision:1019`, `record-catering-acceptance:194`, `catering-followup-sweep:522`, `handle-shift-sick-call:320`, expense decisions. Owner cards embed LLM-*extracted values* in fixed templates (`create-catering-lead:888-898` customer name / raw inquiry / fields; `amend-catering-lead:438`; menu-photo preview `hooks.py:5735` with vision-read prices) — these were the false-positive victims the exemption is for.
- Model-composed replies never reach `bridge_post`: Hermes LLM replies go through `plugins/shift-agent-policy/policy.py ScreenedWhatsAppAdapter.send/edit_message` → `front_brain_screen_gateway_send` (`safe_io.py:1620`), whose enforce call (`:1811`) does not pass `exempt_owner`; cf-router's model calls (intent shadow, amendment discriminator) classify only.
- One free-text body script exists: `send-catering-ack --message-text` (`:207,:268,:273`; on the PR-ζ null-context allowlist `safe_io.py:876`). cf-router's own calls pass deterministic strings; an LLM cannot drive it because the Hermes `terminal`, `code_execution`, `skills`, `browser`, `clarify`, `delegation` toolsets are disabled on the box (`/root/.hermes/config.yaml:42-48`, verified 2026-10-08). Hardened anyway in session-2 code (G0b below).
- `bridge_post` gates still enforced for owner sends: URL validation `:3476`, test-context block `:3479`, kill switch `:3486`, automation-control `:3497`, PR-ζ/PR-γ policy `:3516-3547`, throttle `:3556`. The exemption only skips the wording screen + `front_brain_*` rows (`:1452`).
- Pre-existing residuals (unchanged by C1, recorded): `send-coverage-message:104` defines its own `bridge_post` with no safe_io gates (staff-only); `bridge_send_media` captions are never front-brain screened; `policy.py:322` registers Hermes' stock `_standalone_send` unscreened (whether cron delivery uses it is unverified — Hermes source not in repo).
- Delivered-content evidence gap: no log records the brief body (`brief_attempted`/`brief_sent` carry counts/ids; `front_brain_reply_composed.reply_text` is now skipped for owner sends; the Baileys `bridge.log` does not log bodies — 0 hits for the generic-ack text). Closed by G0a below.

### Runtime facts gathered (read-only probes 2026-10-08)
- **CUST0005** (`state/flyer/customers.json`): "Petshop St. john's", `primary_chat_id 269612545511591@lid` (exact LID match → LID sender resolves without a lid-cache pair; lid-cache has no pair), `public_phone/business_whatsapp_number/authorized +19802005023`, `status trial`, `plan_id trial`, `current_period 2026-05-19 → 2026-06-19`, `monthly_flyers_used 1` (F0049, 2026-05-19), `trial_bonus_flyers 0`, `payment_records []`. **Trial expiry is not time-based anywhere in src** — `account._roll_period` (`account.py:917-925`) advances the period for every status, so the 3-flyer trial quota resets every month until a paid activation. CUST0005 is therefore eligible for the test journey today with no account change. Legitimate trial→paid path: admin number sends `CHANGE PLAN STARTER` → replies `CONFIRM UPDATE` (sets `pending_plan_id`, audit `flyer_account_updated/plan_change_requested`) → operator `manage-flyer-account --activate-customer CUST0005 --provider manual --payment-reference <real ref> --expected-plan starter [--amount-cents 4999]` (`account.py:417-429`; a manual reference is recorded, not verified against a processor; `config.yaml` has no `flyer.plan_tiers`/checkout template → defaults: trial 3 / starter $49.99 30 flyers). The runbook's earlier command lacked the pending-plan step and `--expected-plan` (fixed in session 2).
- **Pricebook v2** (`state/catering-pricebook.json`, `updated_by manual`, effective 2026-09-03, notes "PILOT BOOK … Package rates are PILOT values the owner will replace … tax_rate_bps=0 … is NOT the Mecklenburg County prepared-food rate"): per-person packages **"Vegetarian Buffet" (`veg_buffet`) $14.99/person, min 25, active** and **"Mixed Veg and Non-Veg Buffet" (`mixed_buffet`) $19.99/person, min 25, active**; `fixed_fees []`, `approved_discounts []`, `tax_rate_bps 0`; `item_price_overrides` for all 77 menu names, identical to menu v3 `price_usd` (0 mismatches). **Menu v3** (`updated_by photo-ocr`, 2026-08-23, `source_image_id img_8752e8048d17`): 77 items (42 appetizer / 28 main / 3 side / 4 dessert), all priced, **0 `serves`**. Nothing here may be represented as approved customer pricing.
- `identify-sender`: `+17329837841` → roles [employee, owner]; `201975216009469@lid` → primary role `employee`, roles [employee, owner] (`has_owner_capability` uses `"owner" in roles`, `cf-router/actions.py:411-421`, so `#CODE approve` from the LID carries authority); `+19802005023`, `269612545511591@lid`, bot `+918522041562`, `211390371475536@lid` → unknown (correct).
- Catering leads store: 20 leads (8 OWNER_REJECTED, 4 CLOSED, 3 SENT_TO_CUSTOMER, 3 AWAITING_OWNER_APPROVAL, 2 CUSTOMER_FINALIZED), all historical, 7 under the owner's phone, none for +19802005023. `catering-automation-control.json`: +17329837841 `active` since 2026-10-03 (simulated STOP rehearsal resume).

### Gap work (session 2, branch `launch/catering-flyer-gaps-20261008`)

Hermes-first analysis for the gap work:

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Structured per-item quantity extraction from chat text | none found — Skills Hub (hermes-agent.nousresearch.com/docs/skills) is client-rendered ("Loading the catalog… 88k+ skills"); WebFetch on 2026-10-08 returned no listings, so the hub search itself was NOT performed (unverified); awesome-hermes-agent README (github.com/0xNyk/awesome-hermes-agent): no matching entry | Deterministic parser in the existing `catering_extraction` module: governance (`catering-studio.md` §probabilistic) requires the mapping to a canonical menu item and every cent to be deterministic; Hermes `skills` toolset is disabled on main-vps, so a skill could not run there anyway |
| Menu/price OCR from photos | none found — same hub caveat; awesome-hermes-agent README: no dedicated menu/price OCR entry (only generic vision-composition tooling) | Reuse existing `parse-menu-photo` / `reference_extract` (OpenRouter vision) — no change |
| Flyer rendering of price-less item lists | none found — same hub caveat; awesome-hermes-agent README: no flyer/poster rendering entry | Reuse the existing concept/final pipeline; only the extraction rule changes |

awesome-hermes-agent ecosystem check: fetched 2026-10-08 (github.com/0xNyk/awesome-hermes-agent); no entry for line-item extraction, menu OCR, or flyer rendering. Verdict: no Hermes skill replaces the deterministic kernels; the gap work is wiring inside existing modules.

- **G0a (shift-platform)** positive audit row `front_brain_owner_exempt_send` (chat_key_hash, seam, exempt_reason, message_text ≤2000) at the exempt early-return — makes delivered owner brief/card content verifiable from `decisions.log`. Readers tolerate new row types (`_UnknownLogEntry` shim).
- **G0b (shift-platform)** `bridge_post(..., exempt_owner=True)` keyword; `send-catering-ack` passes `exempt_owner=False` so a free-text body is always screened, owner or not.
- **G1 (flyer-studio)** price-less menu photos: `reference_extract.py:736` (`menu_reference` without a pricing fact → facts emptied → `low_confidence` → `manual_edit_required`) becomes: item names preserved, prices omitted, extraction `ok`; no invented prices (lint/guard on `$` amounts not in locked facts); regression tests; render verification gated on the key cap.
- **G2 (catering-studio)** explicit quantities: `extract_explicit_line_items` (deterministic; unique-token-subset menu match) → cf-router explicit arm before the proposal-selection arm (fixes the "I'll take 2 trays of Idly" → Option 2 misroute) → `compute_quote` → `finalize-catering-menu` WITHOUT `--scale-selected-to-headcount` → owner card → `#CODE approve` → frozen-cent quote. Flag `CATERING_EXPLICIT_QTY_ENABLED` (default "1") AND sender in the existing `CATERING_AUTOMATION_CONTROL_ALLOWLIST` (prod = owner + test customer). Headcount missing or kernel not deliverable → existing path. Graduation trigger: widen the allowlist after 3 genuine quotes whose `pricing_inputs` cents equal pricebook × qty with no owner edit.
- **G3 (repo-meta)** runbook corrections: trial/paid path (above), `price_usd` whole dollars (the earlier `5.99` example fails `CateringSelectedItem.price_usd: int`), explicit-quantity customer journey, `.commit-hash` preflight.

Operator decisions still open after session 2: R1 key cap + credits (two separate actions), F0226/F0228 disposition (A/B/C), package/serving facts confirmation, test-account eligibility (none needed for the trial journey), handset messages after the redeploy.

---

### Session 2 — review outcomes and commits (2026-10-08 16:00–17:10Z)

Branch `launch/catering-flyer-gaps-20261008`: 16fd2213 platform · 29ce50e3 flyer · f2112bb2 catering · 9a3769f7 plan · 0fa686b6 + fa6c7045 runbook · **24284a6d flyer review fixes** · **ce64046b catering review fixes**. Three independent reviewers on the committed range (`git archive`, Docker probes), orthogonal lenses:

- **Money/authorization lens — SHIP-WITH-FIXES → fixed in ce64046b.** HIGH M1: the quantity regex's free prefix swallowed negations ("cancel 10 Idly", "Instead of 10 Idly, 5 Masala Dosa", "not 10 Idly, 5 Idly" → 15) and finalized the opposite of the customer's words → any non-lead-in word before a quantity, or any negation/removal/addition word in the message, now makes the whole message non-explicit. HIGH M2 (= runtime F1): when the arm backed out after a match, option selection ran and "I'll take 2 trays of Idly" became Option 2 → once an item matches the arm owns the turn (canonical "with the owner" reply + handled audit row; finalize rc 1/124 pages the operator). MEDIUM M3 "Can we also add 4 Pongal?" replaced the whole order → add/also/get/more removed from lead-ins (goes to R2A capture). MEDIUM M4 re-finalize under a pending approval → finalize only from AWAITING_OWNER_APPROVAL / OWNER_EDITED. MEDIUM M5 single-token tier-3 matches ("40 veg", "3 Paneer", "60 of us want Masala Dosa") → ≥2 phrase words covering ≥½ of the item's words; headcount/dietary-only phrases never items. MEDIUM M6 (also runtime F4) arm gated by the STOP/takeover allowlist with a default-on flag → own `CATERING_EXPLICIT_QTY_ALLOWLIST` (unset = off). LOW L1 owner-card line amounts used rounded whole dollars → exact cents; L3 clarification echoed raw customer words (could trip `invented_operational_claim`) → menu-token words only. Verified by the reviewer: every cent is kernel-sourced (`price_usd` display-only; `pricing_inputs` cents; approve renders frozen cents), owner never reaches the arm, arm never books, `#CODE approve` owner-only, takeover suppression upstream.
- **Runtime/deploy/rollback lens — SHIP-WITH-FIXES → fixed in ce64046b / runbook.** Flat-install simulation of every new import (incl. `catering_pricing` from the plugin, lazy `style_registers`) imports clean; scripts run `--help` under system python3; 1234 tests across 21 files green; CI globs pick up the new test files. F1/F2 fixed as above (audit rows only for handled outcomes). F3 `--retry-extraction` parks a project in `generating_concepts` (unwatched) and a hand-run `generate-flyer-concepts` does not deliver previews → runbook §1 says so; customer re-sends for end-to-end delivery (accepted residual). F6 rollback past the gap release while `FLYER_PRICELESS_MENU_ALLOWLIST` is set would let old `visual_qa` treat price-less menus as creative latitude → runbook §4 rollback order. Rollback otherwise clean: new row tag → `_UnknownLogEntry`; `flyer_status_change` rows validate against 50a1daa0's model; no Literal widened.
- **Flyer product-correctness lens — SHIP-WITH-FIXES → fixed in 24284a6d.** HIGH F1 real dish names ("Idli (3 PCS)", "Mysore Masala Dosa.", "Kothu Parotta ₹180") failed the bullet regex and vanished silently → charset widened (parentheticals, trailing period, Indic script); "same exact items" goes low_confidence on any unparsed line. HIGH F2 footer/meta lines ("Call 904-…", "Open 7 days", "Served with sambar", "Veg / Non-Veg") became required menu items → deterministic non-item filter (+ address and ALL-CAPS header rules), prompt tightened. MEDIUM-HIGH F3 the router wrapper makes every photo caption `menu_reference`, so a Diwali poster would render as a menu → price-less acceptance requires menu intent in the customer's own words (wrapper stripped). MEDIUM F4 prompt invited estimated prices (pre-existing path to a delivered invented price) → "never estimate" prompt + drop any item price absent from the model's `visible_text`. MEDIUM F5 Rs/₹ prices inside names → split off; `_is_price_bearing_fact` covers $ ₹ € £ Rs. Accepted residuals: empty `visible_text` keeps model prices (strict reading would strip legitimate prices); "Biryani 250" keeps the bare number; `_ANY_CURRENCY_AMOUNT_RE` false-positives ("Table RS 2") cost a retry then manual review, never a wrong flyer; large menus may hit `missing_item_name` (pre-existing).

Test state after fixes (Docker python:3.11-slim): flyer 557 (7 files) / 4090 (126 files, 9 skipped); catering routing+extraction 81, `tests/test_catering_*.py tests/test_cf_router_*.py` 2745 / 11 skipped, lifecycle e2e 16 (fresh container); platform 269. Red-first on the review fixes: flyer 11, catering 37. CI-equivalent non-flyer root suite on the Windows-mounted worktree (pre-fix tree): 6555 passed, 10 failed — all in repo-invariant tests (`test_architecture_governance` ×5, `test_deploy_entrypoint_prefers_staging` ×3, `test_repo_invariants`, `test_tarball_includes_summary_artifacts`) that inspect git modes/tracked files; treated as mounted-checkout artefacts pending CI, which is the authority.

Decisions recorded: both product arms ship allowlist-gated and OFF by default (`FLYER_PRICELESS_MENU_ALLOWLIST`, `CATERING_EXPLICIT_QTY_ALLOWLIST`; pilot values `+17329837841,+19802005023` and `+19802005023`); enabling them is a separately authorized `/root/.hermes/.env` edit (symlink target, backup, gateway restart). Graduation: widen each allowlist after 3 genuine journeys with correct output (flyer: no manual-review fabrication blocker; catering: `pricing_inputs` cents = pricebook × qty, no owner edit). `--retry-extraction` is an inspection tool, not a delivery path.
