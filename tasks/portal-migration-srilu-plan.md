# Portal migration to srilu (plan)

**Drift-check tag:** `extends-Hermes`

Migrate the static SMB-Agents portfolio portal from main-vps (nginx
:8080 /portal/) to srilu (python `http.server` :8080). Single static
HTML file + systemd unit + 2 lines added to `shift-agent-deploy.sh`.
main-vps portal stays running until srilu deploy is verified ≥7 days
(slowly-decommission per `memory/project_srilu_canonical_state.md`).

**`/hermes-check` receipt:** `tasks/.hermes-check-receipts/portal-migration-srilu.json`
(timestamp 2026-05-10T16:11:17Z, drift-tag = extends-Hermes, 7 [Hermes] / 3 [net-new]).

**Why option A (python `http.server`) not nginx**: srilu is a multi-tenant
VPS shared with 4 other bots per `memory/project_vps_deploy_state.md`. Adding
nginx is heavier infrastructure than a single static file warrants. Python
3.12 stdlib already on srilu (verified via `python3 --version` in recon).
If a second site lands on srilu later, escalating to nginx is a separate
decision with its own evidence — rule of three applies.

---

## Hermes-first per-step checklist

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | Tarball + scp + `shift-agent-deploy.sh` invocation | `[Hermes]` | Already deployed pipeline |
| 2 | **Install `web/portal/index.html` → `/opt/triveni/portal/index.html`** | **`[net-new]`** | New `install` line in `install_artifacts()` (~3 LOC) |
| 3 | **Install `triveni-portal.service` systemd unit** | **`[net-new]`** | New service file (~25 LOC) + new `install` line (~2 LOC) |
| 4 | `systemctl daemon-reload + enable --now triveni-portal.service` | `[Hermes]` | Already-deployed pattern (deploy.sh does this for daily-brief, eod, compliance, prune-expense, routing-summary) |
| 5 | Service starts: `python3 -m http.server 8080 --directory /opt/triveni/portal --bind 0.0.0.0` | `[Hermes]` | Python 3.12 stdlib on srilu (verified) |
| 6 | systemd `User=` directive runs the server as non-root user | `[Hermes]` | Standard systemd security pattern (matches hermes-gateway.service, send-daily-brief.service) |
| 7 | End-user GETs `http://89.167.116.187:8080/` → http.server serves index.html | `[Hermes]` | Python stdlib + browser-native rendering |
| 8 | JS auto-counts agents from AGENTS array | `[Hermes]` | Already in `web/portal/index.html` |
| 9 | **Smoke verify: `curl -s http://localhost:8080/ | grep -c "SMB-Agents"`** | **`[net-new]`** | New verification step in deploy.sh (~5 LOC) OR manual one-liner |
| 10 | Old main-vps portal continues unchanged | `[Hermes]` | No action — slowly-decommission |
| 11 | (FUTURE PR) Retire main-vps portal after ≥7-day soak | DEFERRED | Out of v0.1 scope |

7/11 `[Hermes]`, 3/11 `[net-new]`. Below 50% threshold.

**Awesome-hermes-agent ecosystem check:** N/A — static-file serving is
basic infrastructure; no Hermes ecosystem skill applies.

---

## Drift-rule self-checks

Per CLAUDE.md drift rules — new-script-proposal + closest-similar systemd unit:

- ✅ Read `src/agents/shift/scripts/shift-agent-deploy.sh` lines 32-216 (`install_artifacts()`) — confirmed:
  - Line 38-39: existing `install -m 755 src/platform/scripts/* /usr/local/bin/` pattern
  - Line 105-107: existing `install -m 644 src/agents/shift/systemd/*.service /etc/systemd/system/` pattern
  - Line 312-318: existing `systemctl enable --now ...timer` pattern
  - The deploy script's structure cleanly accommodates a new section for portal install
- ✅ Read `src/platform/systemd/hermes-gateway.service` (full) — long-running daemon template: `Type=simple`, `Restart=on-failure`, `RestartSec=30`, `User=shift-agent`, security hardening (`NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths`, `ProtectHome=read-only`, `PrivateTmp`, `RuntimeDirectory`). Pattern to mirror for portal service.
- ✅ Read `src/agents/daily_brief/systemd/send-daily-brief.service` — oneshot service template (different shape: `Type=oneshot` for cron-triggered work). Confirms portal needs `Type=simple` (long-running) not oneshot.
- ✅ Read `web/portal/index.html` (467 lines) — single-file static HTML with embedded JS; no external assets, no API calls, no LLM. Just-curl-able.
- ✅ Reconnaissance via SSH on srilu confirmed: nginx absent, ufw inactive, port 8080 free, `python3 --version` = 3.12.3, port 8000 occupied by uvicorn (other tenant — non-conflicting).

**Deployed-pattern compliance:**
- systemd unit shape: `Type=simple` + `User=` + security hardening + journald logging ✓ (matches hermes-gateway.service)
- Deploy script integration: `install -m 644 ... /etc/systemd/system/` + `systemctl enable --now ...service` ✓ (matches existing per-agent systemd installs)
- Multi-tenant courtesy: non-root user, `--bind 0.0.0.0` (no privileged port), no firewall change needed (ufw inactive) ✓
- File ownership: `/opt/triveni/portal/` owned by the same `shift-agent` user used elsewhere (consistent ownership across the box) ✓

---

## Scope boundary (anti-over-engineering)

### In scope (~30 LOC across 2 files + 1 new file)

| File | Change | LOC |
|---|---|---|
| `src/platform/systemd/triveni-portal.service` (NEW) | Mirror `hermes-gateway.service` shape (Type=simple, security hardening) but with `python3 -m http.server` ExecStart and no Hermes-specific env vars | ~25 |
| `src/agents/shift/scripts/shift-agent-deploy.sh` | Add 3 lines under `install_artifacts()`: (a) `install -d /opt/triveni/portal && install -m 644 web/portal/index.html /opt/triveni/portal/index.html`, (b) `install -m 644 src/platform/systemd/triveni-portal.service /etc/systemd/system/`, (c) `systemctl enable --now triveni-portal.service` (added after the existing `systemctl enable` block at lines 312+) | ~5 |
| `tools/build-deploy-tarball.sh` | Add `web/` to the tar inclusions if not already; verify `tar` includes `web/portal/index.html` | ~1 |

### systemd unit shape (locked at plan time)

```ini
[Unit]
Description=Triveni SMB-Agents Portfolio Portal (static HTML)
After=network-online.target
Wants=network-online.target
ConditionPathExists=/opt/triveni/portal/index.html

[Service]
Type=simple
User=shift-agent
Group=shift-agent
ExecStart=/usr/bin/python3 -m http.server 8080 --directory /opt/triveni/portal --bind 0.0.0.0
Restart=on-failure
RestartSec=30
StandardOutput=append:/opt/shift-agent/logs/triveni-portal.log
StandardError=append:/opt/shift-agent/logs/triveni-portal.log

# Security hardening (mirrors hermes-gateway.service)
NoNewPrivileges=true
ProtectSystem=strict
ReadOnlyPaths=/opt/triveni/portal
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Note: **no `ReadWritePaths`** because the portal is read-only static. `ReadOnlyPaths=/opt/triveni/portal` enforces that hardening explicitly.

### Explicitly out of scope (deferred)

| Considered | Decision | Reason |
|---|---|---|
| Install nginx on srilu | **REJECTED** | Heavier than needed for a single static file on a multi-tenant box. Rule-of-three: revisit when a second site lands. |
| HTTPS / Let's Encrypt | **DEFERRED** | Portal is operator-only internal-stakeholder view. HTTPS adds cert-renewal cron + ACME setup. Do when external-facing or audit demands. |
| Reverse-proxy via existing uvicorn (port 8000) | **REJECTED** | Other tenant's process; not our infrastructure to extend. |
| Authentication | **DEFERRED** | Portal is read-only with no PII; same security posture as the existing main-vps version. v0.2 if internal-data sensitivity changes. |
| URL path prefix `/portal/` | **REJECTED** | main-vps used nginx `location /portal/` for vhost organization. python `http.server` doesn't support path prefixes. New URL is `http://89.167.116.187:8080/`. Operators learn the new URL once. |
| Retire main-vps portal in same PR | **DEFERRED** | Slowly-decommission discipline: keep old version running ≥7 days post-srilu-deploy, then a separate PR retires main-vps. |

### Deferred (separate commits if ever needed)

- v0.2: HTTPS via Let's Encrypt (when external-facing demand emerges)
- v0.2: nginx upgrade (when a second srilu-hosted site is needed)
- Future PR: retire main-vps portal after 7-day soak
- Future PR: dedicated `triveni-portal` user (currently uses shared `shift-agent` user — consistent with other services on srilu but `nobody:nogroup` would be more locked-down)

---

## Verification + commit shape

- **Deploy verification on srilu**: after `shift-agent-deploy.sh` runs, `curl -s http://localhost:8080/ | grep -c "SMB-Agents"` returns ≥1
- **External verification**: `curl http://89.167.116.187:8080/` from local returns the portal HTML
- **systemd verification**: `systemctl is-active triveni-portal.service` returns `active`
- **Pass criteria**: all three above + main-vps portal still serving its old content (slowly-decommission preserved)
- **Commit shape**: ONE commit, message `feat(portal): migrate to srilu via python http.server systemd unit`, ~30 LOC across 3 files (1 new + 2 modified)
- **Deploy via existing pipeline**: `bash tools/build-deploy-tarball.sh --skip-pytest && scp + extract + shift-agent-deploy.sh deploy`

---

## Approval needed

Plan reviewers must explicitly approve before design phase. Specific decisions
to challenge:

1. **Python `http.server` vs nginx** — option-A discipline says python; reviewers can flip if they think nginx is the right call given main-vps already runs it.
2. **Port 8080 on srilu** — same as main-vps. Reviewers can flip if a different port avoids future tenant conflicts.
3. **Run as `shift-agent` user** vs dedicated `triveni-portal` user — chose `shift-agent` for consistency with other services on srilu. Dedicated user would lock down further but adds setup work.
4. **No URL path prefix** (`http://...:8080/` not `http://...:8080/portal/`) — main-vps used `/portal/` via nginx; python http.server doesn't. Operators learn the new URL.
5. **Smoke verify scripted in deploy.sh** vs manual check — leaning manual for v0.1 (deploy is operator-driven; one curl is fine). Reviewers can challenge if deploy-gate scripted check is needed.
