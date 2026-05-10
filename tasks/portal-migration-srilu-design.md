# Portal migration to srilu (design v2 — option-A standalone-script pivot)

**Drift-check tag:** `extends-Hermes`

Design for plan at `tasks/portal-migration-srilu-plan.md`, updated per
plan-review (R1-M1 split into separate `tools/deploy-portal.sh` + R2-B1
fixed `ReadWritePaths` + R2-M1 `ProtectHome=read-only` + R2-M4 `/opt/triveni`
ownership).

**`/hermes-check` receipt:** `tasks/.hermes-check-receipts/portal-migration-srilu-design.json`
(timestamp 2026-05-10T16:16:19Z, drift-tag = extends-Hermes, 8 [Hermes] / 4 [net-new]).

---

## Hermes-first per-step checklist

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | Operator runs `tools/deploy-portal.sh` | `[Hermes]` | bash + SSH already deployed |
| 2 | Pre-deploy file checks | `[Hermes]` | bash `[ -f ... ]` |
| 3 | `scp` index.html + service file | `[Hermes]` | already-deployed SSH |
| 4 | **`ssh` heredoc: install + daemon-reload + enable** | **`[net-new]`** | new bash chain (~25 LOC) |
| 5 | systemd starts `python3 -m http.server 8080 --directory /opt/triveni/portal` | `[Hermes]` | Python 3.12 stdlib + systemd |
| 6 | **Internal smoke verify** (`ssh srilu 'curl localhost:8080'`) | **`[net-new]`** | ~5 LOC |
| 7 | **External smoke verify** (`curl 89.167.116.187:8080`) | **`[net-new]`** | ~5 LOC |
| 8 | End-user opens URL → portal renders | `[Hermes]` | browser + Python stdlib |
| 9 | Old main-vps portal continues unchanged | `[Hermes]` | no action |
| 10 | **Schema: `triveni-portal.service` systemd unit** | **`[net-new]`** | ~25 LOC |

8/10 `[Hermes]`, 4/10 `[net-new]` (counting the systemd unit definition as a
separate net-new item). Below 50% threshold.

---

## Drift-rule self-checks

Per CLAUDE.md drift rules — new-script-proposal + closest-similar systemd unit:

- ✅ Read `src/platform/systemd/hermes-gateway.service` (full, 36 lines) — long-running daemon template: `Type=simple`, `Restart=on-failure`, `RestartSec=30`, security hardening (NoNewPrivileges, ProtectSystem=strict, ReadWritePaths, ProtectHome=read-only, PrivateTmp, RuntimeDirectory). Pattern to mirror.
- ✅ Read `src/agents/daily_brief/systemd/send-daily-brief.service` (full, 27 lines) — `Type=oneshot` (different shape; portal uses `Type=simple`). Confirms `ProtectHome=read-only` is the deployed convention (R2-M1 fix).
- ✅ Read `web/portal/index.html` line 1 (already known: 467-line static HTML, no external deps, single file).
- ✅ Read `tools/build-deploy-tarball.sh` lines 58-63 — confirmed `web/` NOT in tarball (only `src/ tools/ .commit-hash`). This validates the decoupled-deploy decision: portal is independent of agent deploy.
- ✅ Reconnaissance via SSH on srilu: nginx absent, ufw inactive, port 8080 free, `python3 --version` = 3.12.3, port 8000 occupied by uvicorn (other tenant — non-conflicting).

**Deployed-pattern compliance:**
- systemd unit shape: `Type=simple` + `User=shift-agent` + security hardening ✓ (matches hermes-gateway.service)
- `ReadWritePaths=/opt/shift-agent/logs` for log-write capability ✓ (R2-B1 fix; without this, ProtectSystem=strict prevents log writes)
- `ProtectHome=read-only` ✓ (R2-M1 fix; matches deployed convention from hermes-gateway.service comment "ProtectHome=true would hide /root even with ReadWritePaths exception — avoid")
- `/opt/triveni/` + `/opt/triveni/portal/` owned by `shift-agent:shift-agent` ✓ (R2-M4 fix; matches existing `/opt/shift-agent/` ownership)
- Standalone deploy script (R1-M1 fix): portal HTML edits do NOT require full agent-tarball + deploy gauntlet (Hermes pin + state migration + vision-auth + smoke). Portal redeploys are seconds, not minutes.

---

## Code-level design

### 1. `src/platform/systemd/triveni-portal.service` (NEW, ~25 LOC)

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
StartLimitBurst=3
StandardOutput=append:/opt/shift-agent/logs/triveni-portal.log
StandardError=append:/opt/shift-agent/logs/triveni-portal.log

# Security hardening (mirrors hermes-gateway.service convention).
# R2-B1 fix: ReadWritePaths required for StandardOutput=append target.
# Without it, ProtectSystem=strict mounts /opt read-only and the service
# fails at startup with EROFS on triveni-portal.log.
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/shift-agent/logs
# R2-M1 fix: read-only matches deployed convention; ProtectHome=true would
# hide /root even with explicit ReadWritePaths.
ProtectHome=read-only
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

**Why ReadWritePaths is `/opt/shift-agent/logs` (not `/opt/triveni/portal`)**:
the service writes ONLY logs; the HTML directory is read-only by design. Listing
it as `ReadWritePaths` would weaken hardening unnecessarily.

### 2. `tools/deploy-portal.sh` (NEW, ~50 LOC)

```bash
#!/usr/bin/env bash
# tools/deploy-portal.sh — standalone deployer for the SMB-Agents portfolio portal.
#
# Decoupled from shift-agent-deploy.sh: portal HTML edits should NOT trigger the
# full agent-deploy gauntlet (Hermes pin + state migration + vision-auth smoke +
# auto-rollback). This script does ONE thing: scp the static HTML + systemd unit
# to srilu, install + enable, and smoke-verify.
#
# Usage:
#   bash tools/deploy-portal.sh                   # default target: root@srilu-vps
#   bash tools/deploy-portal.sh root@example.vps  # custom target
#
# Exit codes:
#   0 — deploy + smoke verify both passed
#   1 — local pre-flight failed (missing files)
#   2 — remote install failed
#   3 — internal smoke failed (curl localhost on srilu)
#   4 — external smoke failed (curl from local to srilu's public IP)

set -euo pipefail

TARGET="${1:-root@srilu-vps}"
PORT="${PORT:-8080}"
EXTERNAL_IP="${EXTERNAL_IP:-89.167.116.187}"

cd "$(dirname "$0")/.."
HTML_LOCAL="web/portal/index.html"
SVC_LOCAL="src/platform/systemd/triveni-portal.service"

echo "=== Pre-flight ==="
[ -f "$HTML_LOCAL" ] || { echo "FATAL: $HTML_LOCAL missing"; exit 1; }
[ -f "$SVC_LOCAL"  ] || { echo "FATAL: $SVC_LOCAL missing"; exit 1; }
echo "✓ local files present"

echo "=== scp to $TARGET ==="
scp -q "$HTML_LOCAL" "$TARGET:/tmp/triveni-portal-index.html"
scp -q "$SVC_LOCAL"  "$TARGET:/tmp/triveni-portal.service"
echo "✓ files staged at /tmp on remote"

echo "=== Install + enable ==="
ssh "$TARGET" 'bash -se' <<'REMOTE'
set -euo pipefail
install -d -o shift-agent -g shift-agent /opt/triveni /opt/triveni/portal
install -m 644 -o shift-agent -g shift-agent \
    /tmp/triveni-portal-index.html /opt/triveni/portal/index.html
install -m 644 /tmp/triveni-portal.service /etc/systemd/system/triveni-portal.service
systemctl daemon-reload
systemctl enable --now triveni-portal.service
rm -f /tmp/triveni-portal-index.html /tmp/triveni-portal.service
echo "✓ installed; service status: $(systemctl is-active triveni-portal.service)"
REMOTE

echo "=== Internal smoke (ssh + curl localhost) ==="
COUNT=$(ssh "$TARGET" "curl -s http://localhost:$PORT/ | grep -c 'SMB-Agents' || true")
if [ "${COUNT:-0}" -lt 1 ]; then
    echo "FATAL: internal smoke failed; service may have failed to start"
    ssh "$TARGET" "systemctl status triveni-portal.service --no-pager -l | tail -20"
    exit 3
fi
echo "✓ internal smoke: $COUNT 'SMB-Agents' hits"

echo "=== External smoke (local curl to public IP) ==="
EXT=$(curl -s --max-time 5 "http://$EXTERNAL_IP:$PORT/" | grep -c "SMB-Agents" || true)
if [ "${EXT:-0}" -lt 1 ]; then
    echo "FATAL: external smoke failed; check VPS firewall / public reachability"
    exit 4
fi
echo "✓ external smoke: $EXT 'SMB-Agents' hits"

echo ""
echo "Portal live at: http://$EXTERNAL_IP:$PORT/"
```

### 3. Verification

- Run `bash tools/deploy-portal.sh` from local
- Pass criteria:
  - Internal smoke (`ssh srilu 'curl localhost:8080'`) returns ≥1 `SMB-Agents` hit
  - External smoke (`curl http://89.167.116.187:8080/` from local) returns ≥1 `SMB-Agents` hit
  - `systemctl is-active triveni-portal.service` = `active` on srilu
  - main-vps portal at `46.62.206.192:8080/portal/` still serving (slowly-decommission preserved)

---

## Risks identified at design time

| Risk | Mitigation |
|---|---|
| Port 8080 collision with future tenant | ufw inactive; if collision occurs, systemd `Restart=on-failure` + `StartLimitBurst=3` will surface it via journald within ~90s. Operator can `systemctl status` to diagnose. |
| `ReadWritePaths` insufficient for future logging needs | Currently ONLY `/opt/shift-agent/logs` writable. If Python's http.server ever wants to write elsewhere (tempfiles?), `PrivateTmp=true` covers it. |
| Network bandwidth contention with other tenants | Portal is static HTML (~33KB); load is negligible. Public-internet exposure is intentional. |
| Old main-vps portal drifts vs new srilu portal | Slowly-decommission per `memory/project_srilu_canonical_state.md`. Portal updates go to srilu via `tools/deploy-portal.sh`; main-vps is read-only after this PR. Future PR retires main-vps after ≥7-day soak. |
| Operator forgets `--bind 0.0.0.0` semantics → service binds 127.0.0.1 only | systemd unit hardcodes `--bind 0.0.0.0`. Operator can override `PORT` env var but not bind. |
| Service-user `shift-agent` shouldn't really own portal data | Acceptable for v0.1 (non-secret static page). Plan defers dedicated `triveni-portal` user. |

---

## Verification + commit shape

- Single commit, ~75 LOC, message: `feat(portal): migrate to srilu via standalone deploy-portal.sh + python http.server systemd unit`
- Files: 2 new (`triveni-portal.service`, `tools/deploy-portal.sh`)
- No agent-code changes. No `shift-agent-deploy.sh` patch. No `build-deploy-tarball.sh` patch.
- Deploy: `bash tools/deploy-portal.sh` from local — ~10 seconds, fully self-contained
- Post-merge: P-E retro at `tasks/audits/portal-migration-retro.md`

---

## Approval needed

Design reviewers must approve before build. Specific decisions to challenge:

1. **`ReadWritePaths=/opt/shift-agent/logs`** vs broader `/opt/shift-agent` — narrower is better; reviewers can flip if there's a reason to widen.
2. **`StartLimitBurst=3`** — explicit cap on restart-loops. Default would also work; making it explicit signals operator intent.
3. **`PORT` env var override** in deploy script — useful for testing on alternative ports without editing the systemd unit. Reviewers can simplify by hardcoding 8080.
4. **No HTTPS** — internal-stakeholder portal; HTTP is acceptable. Reviewers can flag if external-facing.
5. **No path prefix** — main-vps used `/portal/`; srilu uses `/`. Operators learn the new URL. Reviewers can require `--directory /opt/triveni/` (not `/opt/triveni/portal/`) to preserve `/portal/` URL — but then the service exposes more files unintentionally.
