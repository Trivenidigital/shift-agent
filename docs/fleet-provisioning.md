**Drift-check tag:** `extends-Hermes` — extends today's per-VPS tarball deploy flow (`build-deploy-tarball.sh` + `shift-agent-deploy.sh`) with a fleet provisioning lifecycle. Reuses the existing Hermes-pin gate + auto-rollback + smoke patterns. Adds operator-VPS automation around them.

# Fleet Provisioning — SMB-Agents

**Status:** v1, 2026-05-03 — sketch + script contracts. Not yet implemented; this doc is the spec to execute against when fleet build prioritizes (post-Triveni-9 bring-up, before customer #5).

**Companion doc:** `docs/multi-tenant-architecture.md` (the why + boundary-of-concerns).

## Read-deployed-code commitment

Before drafting, I read:
- `tools/build-deploy-tarball.sh` — current local-side packaging (src/ + tools/ + .commit-hash → shift-agent-deploy.tgz)
- `docs/deploy.md` §"Customer-VPS bring-up: migration is step-0" — existing manual bring-up sequence (extract tarball → check-env-drift.sh → migrate-env-to-symlink.sh → restart services → run deploy)
- `tools/check-env-drift.sh` + `tools/migrate-env-to-symlink.sh` — existing env-symlink discipline
- `tools/check-shift-agent-patch.sh` + `tools/hermes-patch-baseline.txt` — Hermes pin gate
- The catering-finalize-flow PRs CF1/CF2 deploy to srilu-vps as an existing precedent for "staging VPS" pattern

The lifecycle below is what each new customer-location goes through to land in the fleet. Most steps already have manual-runbook equivalents in `docs/deploy.md`; this doc gives them automation contracts so they're scriptable end-to-end.

## Hermes-first checklist

| Step | `[Hermes]` / `[net-new]` |
|---|---|
| Hetzner VPS provision (cloud API call) | `[net-new]` (operator runbook + Hetzner CLI/Terraform) |
| OS bootstrap (apt deps, user, dirs) | `[net-new]` (cloud-init template) |
| Hermes install on the VPS | `[Hermes]` (existing `hermes setup` flow) |
| `/opt/shift-agent/` install | `[Hermes]` (existing `shift-agent-deploy.sh`) |
| Env file population from customer template | `[net-new]` (templating + secret injection) |
| WhatsApp number pairing (Baileys QR) | `[Hermes]` (existing pair flow) + `[net-new]` (operator-driven automation) |
| Fleet registry update (operator VPS knows about new VPS) | `[net-new]` (registry write) |
| First deploy + smoke + go-live | `[Hermes]` (existing `shift-agent-deploy.sh`) |
| Per-location VPS health monitoring | `[net-new]` (operator-side polling) |
| Decommission / reassign WhatsApp number | `[net-new]` (operator runbook) |

**Net-new tally: 7 surfaces.** Most are operator-VPS scripts/services. Per-VPS deploy semantics stay unchanged — fleet automation calls into the existing single-VPS flow N times.

## Lifecycle — 7 stages from "customer wants Plano location" to "agent live"

```
   Day 0          Day 0           Day 0           Day 0
   ┌──────┐      ┌──────┐        ┌──────┐        ┌──────┐
   │ 1.   │ ───► │ 2.   │ ────► │ 3.   │ ────► │ 4.   │
   │Provi-│      │Boot- │        │Pair  │        │Regis-│
   │sion  │      │strap │        │WA #  │        │ter   │
   └──────┘      └──────┘        └──────┘        └──────┘
        ~5min          ~10min         ~5min (operator)  ~1min
                                      + customer scans QR
                                       ~2min wall-clock

   Day 0          Day 1+          ...
   ┌──────┐      ┌──────┐
   │ 5.   │ ───► │ 6.   │ ─── ongoing ───► [ 7. Decommission, if ever ]
   │First │      │Health│
   │deploy│      │watch │
   └──────┘      └──────┘
```

### Stage 1 — Provision

**Goal:** A running Hetzner VPS at known IP, accessible by SSH.

**Script:** `tools/fleet/provision-vps.sh` (NEW)

**Inputs:**
- `--customer-id` (e.g. `triveni`)
- `--location-id` (e.g. `tx-plano`)
- `--region` (Hetzner location: `hel1` / `nbg1` / `fsn1`; default round-robins for IP-diversity per multi-tenant-architecture.md §Risks)
- `--server-type` (default `cx22` ≈ CCX13 ~$7/mo)

**Behavior:**
1. Validate `<customer-id>--<location-id>` is unique in fleet registry.
2. Hetzner Cloud CLI: `hcloud server create --name <customer>--<location> --type <type> --image ubuntu-24.04 --location <region> --ssh-key <fleet-key>`.
3. Capture IPv4 + IPv6.
4. Append SSH config entry locally + on operator VPS: `Host <customer>--<location>-vps  HostName <ipv4>  ...`.
5. Wait for SSH up.
6. Emit JSON: `{"customer_id":..., "location_id":..., "region":..., "ipv4":..., "created_at":...}`.

**Exit codes:** 0 OK, 2 invalid input, 4 customer/location collision, 6 Hetzner API failure.

**Idempotency:** YES. Re-run with same args returns existing VPS metadata if it exists.

### Stage 2 — Bootstrap

**Goal:** VPS has Hermes + shift-agent dependencies installed; ready for tarball deploy.

**Script:** `tools/fleet/bootstrap-vps.sh` (NEW)

**Inputs:**
- `--vps` (SSH alias, e.g. `triveni--tx-plano-vps`)

**Behavior:**
1. SSH in, run apt updates + install Python 3.12 + Node + system deps.
2. Create `shift-agent` user + group.
3. `mkdir -p /opt/shift-agent/{state,logs,deploys,templates}` with proper perms.
4. Run `curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash` (Hermes' canonical install).
5. Verify Hermes commit matches `tools/hermes-patch-baseline.txt` `HERMES_COMMIT`. If different, refuse-or-override per pin gate semantics.
6. Apply `tools/patch-bridge-filter.py` (re-applies the `shift-agent-sender-id` + `shift-agent-template-bypass` patches).
7. Run `tools/check-shift-agent-patch.sh` — fail-closed if patches didn't apply cleanly.
8. Run `tools/migrate-env-to-symlink.sh` (existing) — sets up the `/opt/shift-agent/.env → /root/.hermes/.env` symlink.
9. Populate `/root/.hermes/.env` from customer-config template + secrets vault (Pushover keys, OPENROUTER_API_KEY, customer-specific overrides).
10. Populate `/opt/shift-agent/config.yaml` from per-customer template substituting customer/location identity.

**Exit codes:** 0 OK, 2 invalid input, 5 Hermes install failure, 6 patch apply failure, 7 env-template missing.

**Idempotency:** Mostly YES. Re-running on a partially-bootstrapped VPS should be safe (apt installs are idempotent; `hermes setup` is idempotent; symlink migration is idempotent per `migrate-env-to-symlink.sh`).

### Stage 3 — WhatsApp pairing

**Goal:** Hermes' Baileys session is logged in to this location's phone number.

**Script:** `tools/fleet/pair-whatsapp.sh` (NEW)

**Inputs:**
- `--vps` (SSH alias)
- `--phone-number` (E.164, e.g. `+19725550100`)

**Behavior:**
1. SSH start `hermes gateway start --pair-only` on the VPS — outputs QR code to terminal.
2. Capture QR data; render to operator-readable image (terminal QR).
3. Operator/customer scans QR with the location's WhatsApp account.
4. Wait for "Bridge ready (status: connected)" log line in `/root/.hermes/logs/agent.log`.
5. Verify session bound to expected phone number (Baileys exposes `session.creds.me.id`).
6. `systemctl enable --now hermes-gateway`.
7. Send a test ping from operator's WhatsApp test number → verify inbound is received.

**Exit codes:** 0 OK, 2 invalid input, 6 QR scan timed out (5 min), 7 phone-number mismatch (scanned wrong account), 8 inbound test ping failed.

**Idempotency:** PARTIAL. Re-pairing replaces the prior session. Operator must intend this — semi-destructive (prior session in-flight messages may be lost).

**The slowest step in the lifecycle.** Customer involvement required (someone must scan the QR with the staff WhatsApp). Plan UX so this is a 2-minute call/screen-share, not a multi-day async dance.

### Stage 4 — Register

**Goal:** Operator VPS knows about this new VPS so fleet ops + aggregation include it.

**Script:** `tools/fleet/register-vps.sh` (NEW; runs on operator VPS)

**Inputs:**
- `--customer-id`
- `--location-id`
- `--vps` (SSH alias)
- `--phone-number`
- `--enabled-agents` (comma-separated, e.g. `shift,catering,daily_brief,eod`)

**Behavior:**
1. Append to `/opt/operator/fleet-registry.json`:
   ```json
   {
     "customer_id": "triveni",
     "location_id": "tx-plano",
     "vps_alias": "triveni--tx-plano-vps",
     "ipv4": "...",
     "phone_number": "+19725550100",
     "enabled_agents": ["shift", "catering", "daily_brief", "eod"],
     "registered_at": "2026-05-03T...",
     "status": "active"
   }
   ```
2. Configure mission-control to include this VPS in deploy targets.
3. Spin up (or reconfigure) per-customer aggregator service to include this location's audit-log polling.
4. Emit `fleet_vps_registered` log entry to operator-VPS audit chain.

**Exit codes:** 0 OK, 2 invalid input, 4 customer not registered yet, 9 mission-control update failed.

**Idempotency:** YES. Re-register updates the row; doesn't duplicate.

### Stage 5 — First deploy

**Goal:** Latest known-good shift-agent commit is running on this VPS, all smoke checks pass.

**Script:** uses existing `tools/build-deploy-tarball.sh` + `shift-agent-deploy.sh` + adds fleet wrapper.

**Wrapper:** `tools/fleet/deploy-vps.sh` (NEW)

**Inputs:**
- `--vps` (SSH alias)
- `--commit-or-tag` (default: latest main)

**Behavior:**
1. Build tarball (skip pytest if not on the build host — assume CI built it).
2. SCP tarball to VPS `/tmp/`.
3. Run remote `shift-agent-deploy.sh deploy` — same script as today, with Hermes pin gate + **PR-CF5 state-file migration gate** + snapshot + install_artifacts + smoke + auto-rollback.
4. If smoke fails → auto-rollback fired; emit `fleet_deploy_failed` audit; alert operator.
5. If smoke passes → emit `fleet_deploy_succeeded` audit.

**Exit codes:** 0 OK, mirrors `shift-agent-deploy.sh` exit codes 1-10.

**Idempotency:** YES (existing deploy.sh is idempotent).

**State-file migration (PR-CF5)**: First-deploy on a VPS that has carried legacy state-file shapes (e.g., post-Phase-0 hand-bootstrapped Triveni VPSes) will hit the migration gate and auto-upgrade `send-counter.json`/`seen-ids.json` to current Pydantic schemas. Migration emits `state_file_migrated` audit row + writes `<file>.pre-migrate-<epoch>` backup. See `tasks/runbook-state-migration.md` for the operator recovery scenarios. Bootstrap-friendly: the gate skips with WARN if migrator script absent (pre-CF5 tarball rollback compat).

This stage is **already implemented at the per-VPS layer**. Fleet wrapper just calls it once. At fleet-wide rollout (deploy commit X to N VPSes), `mission-control` provides canary + rolling + auto-stop-on-N-failures.

### Stage 6 — Health monitoring

**Goal:** Operator notices when a per-location VPS goes silent or unhealthy, before customers notice.

**Service:** `operator/services/fleet-health-monitor.py` (NEW; runs on operator VPS)

**Behavior (every 5 min):**
1. For each registered VPS in `fleet-registry.json`:
   - SSH-ping (does the box answer?).
   - Read tail of `/opt/shift-agent/logs/decisions.log` — was there activity in last hour?
   - Check `systemctl is-active hermes-gateway` — is the gateway running?
   - Optional: send a synthetic `expense disabled?` ping from a test number, verify response.
2. If any check fails → escalate per severity:
   - Soft (no inbound activity but gateway active): operator dashboard yellow
   - Hard (gateway down OR SSH unreachable): Pushover-page operator + dashboard red
   - WhatsApp-ban suspect (no activity for >24h on a number that was active): page + auto-investigate
3. Emit `fleet_health_check_*` audit entries to operator-VPS audit chain.

**Already-deployed pattern:** `shift-agent-health.timer` runs per-VPS for self-health. The fleet-wide variant aggregates across N VPSes from the operator side.

### Stage 7 — Decommission

**Goal:** Customer-location offboarded, WhatsApp number released, VPS safely destroyed.

**Script:** `tools/fleet/decommission-vps.sh` (NEW)

**Inputs:**
- `--vps` (SSH alias)
- `--reason` (audit context)
- `--archive-state` (default true; rsync state files to operator-VPS for post-mortem retention)

**Behavior:**
1. Send "service ending" notice via the location's WhatsApp (if customer wants it).
2. Stop hermes-gateway + all timers.
3. Rsync `/opt/shift-agent/state/` + `/opt/shift-agent/logs/decisions.log` to `<operator-vps>:/opt/operator/archive/<customer>--<location>/<timestamp>/`.
4. Remove from fleet-registry.json (mark `status: decommissioned`).
5. Reconfigure mission-control to exclude this VPS.
6. Reconfigure per-customer aggregator to exclude this location.
7. `hcloud server delete <vps-name>`.
8. Emit `fleet_vps_decommissioned` audit.

**Exit codes:** 0 OK, mirrors prior stages.

**Idempotency:** YES (re-run is no-op once status=decommissioned).

## What exists today vs net-new

| Stage | Today | Net-new for fleet |
|---|---|---|
| 1 — Provision | Manual: log into Hetzner, click create-server | `provision-vps.sh` wrapping Hetzner CLI |
| 2 — Bootstrap | Manual: SSH in, run a checklist of installs | `bootstrap-vps.sh` automating the checklist; existing `migrate-env-to-symlink.sh` reused |
| 3 — Pair WhatsApp | Manual: scan QR via `hermes gateway start` | `pair-whatsapp.sh` orchestrating the QR flow |
| 4 — Register | Doesn't exist (no fleet registry yet) | `register-vps.sh` + `fleet-registry.json` schema |
| 5 — First deploy | EXISTS: `build-deploy-tarball.sh` + `shift-agent-deploy.sh` | Thin wrapper `deploy-vps.sh`; mission-control adoption later |
| 6 — Health monitor | Per-VPS only (`shift-agent-health.timer`) | Cross-fleet `fleet-health-monitor.py` on operator |
| 7 — Decommission | Doesn't exist | `decommission-vps.sh` + state archival |

## Phasing — what to build first

### Phase 0 — Triveni-9 manual bring-up (NOW)

Before any fleet automation lands, Triveni's 9 locations come up via:
- Manual Hetzner provisioning (operator clicks 9× create-server)
- `bootstrap-vps.sh` exists OR equivalent hand-rolled checklist runbook
- Manual WhatsApp pairing (operator + Triveni staff coordinated)
- Existing `shift-agent-deploy.sh` per VPS for first deploy

Result: 9 location-VPSes for Triveni, the proven manual pattern. Captures the runbook gaps that automation should close.

### Phase 1 — Provision + Bootstrap automation (BEFORE customer #5)

Build:
- `tools/fleet/provision-vps.sh` (Hetzner CLI wrapper)
- `tools/fleet/bootstrap-vps.sh` (consolidates the manual checklist)
- `tools/fleet/pair-whatsapp.sh` (orchestrates QR flow)
- `tools/fleet/register-vps.sh` + minimal `fleet-registry.json` schema

Sufficient for "operator runs 4 commands, new location is live." Customer onboarding becomes a 30-minute call (most of which is the QR pair).

### Phase 2 — Health monitoring + Per-customer aggregator (BEFORE customer #10)

Build:
- `operator/services/fleet-health-monitor.py`
- Per-customer aggregator service skeleton (one-instance-per-customer reads N location VPSes, produces consolidated Daily Brief)
- Mission-control evaluation + adoption decision

This is the inflection point where per-customer aggregation pays for itself — Triveni's owner can stop reading 9 separate Daily Briefs and start getting one rollup.

### Phase 3 — Mission-control adoption + Skill evolution + Decommission (BEFORE customer #25)

- Mission-control deployed as fleet substrate (replacing per-VPS deploy wrapper for fleet rollouts)
- Cross-customer skill evolution (DSPy+GEPA OR SkillClaw, per multi-tenant-architecture.md §"What runs where") with anonymization layer
- `decommission-vps.sh` formalized (some customers WILL churn; have the runbook)

By customer #25, the fleet is operationally mature.

### Phase 4 — Self-healing + Auto-scaling (BEFORE customer #50)

- WhatsApp number ban detector + auto-paging
- Per-customer auto-scaling (if a location's traffic exceeds CCX13 baseline, auto-bump server-type)
- Cross-region failover (operator can re-point a banned number to a fresh VPS in different DC)

This is the "no longer breaks the operator's sleep" tier.

## Risks

### High — Phase-1 effort underestimated

QR-pairing automation has UX edge cases: customer scans wrong account, takes >5 min to find phone, scans during expired QR. Manual operator-on-call probably stays in the loop through Phase 2 at minimum.

### High — `fleet-registry.json` becomes a SPOF

If the registry file corrupts, fleet ops loses the source of truth. **Mitigations:**
- `safe_io.atomic_write_json` on operator VPS (mirror the pattern from per-VPS state files)
- Git-versioned (the registry is a config artifact; commit + push to a private repo on every change)
- Regenerable from `hcloud server list` + per-VPS `cat config.yaml` SSH calls

### Medium — Mission-control bus factor

Adopting `mission-control` (3.7k stars, builderz-labs) ties our fleet ops to an external project. Have a "build minimal in-house alternative" backup plan documented before adopting (see multi-tenant-architecture.md §Risks).

### Medium — Bootstrap drift

Each new bootstrap install picks up the latest Ubuntu / Hermes / Python versions. After 6 months of customer onboardings, customer #1 and customer #50 may be running materially different substrate stacks. **Mitigation:** pin everything (Hermes commit, Ubuntu LTS, Python 3.12), document the pin in `tools/hermes-patch-baseline.txt` + parallel files. Schedule a fleet-wide upgrade quarterly, not ad-hoc.

### Low — IP-diversity tradeoff

Spreading VPSes across multiple Hetzner regions for WhatsApp anti-ban (per multi-tenant-architecture.md) means latency between operator-VPS and per-location VPSes varies. Aggregation polling needs to handle 50-200ms RTTs gracefully (already does, since polls are async + best-effort).

## Open questions

1. **WhatsApp Business API parallel track** — at what customer count do we add Business API for outbound owner-cards? Phase 1? Phase 3? Per-customer opt-in?
2. **Customer-self-service portal vs operator-driven onboarding** — should new customers be able to provision their own location-VPSes via a web portal? Or is operator-driven a feature ("we set it up for you")?
3. **Bootstrap-time secret injection mechanism** — secrets vault choice. Options: HashiCorp Vault, AWS Secrets Manager, plain-old encrypted git repo of config templates. Tradeoff: complexity vs auditability.
4. **Per-customer-aggregator placement** — one big monolithic service per operator, or one process-per-customer (sandboxed)? The latter scales better but multiplies process count on operator VPS.
5. **Decommission state retention** — how long does archived state stay on operator VPS? GDPR + customer agreements drive this; not in scope for this doc.

---

*Companion docs: `docs/multi-tenant-architecture.md` for the architectural rationale + agent-to-VPS placement matrix. `tasks/skills-roadmap.md` for per-agent Hermes ecosystem coverage (5 install-now skills, Phase 0-1 work). `tasks/runbook-state-migration.md` for PR-CF5 first-deploy migration gate failure modes.*
