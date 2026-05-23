**Drift-check tag:** extends-Hermes

# Hermes Fleet Normalization Checklist — 2026-05-20

## Purpose

Before adding execute-mode Hermes upgrades, make the three VPSes comparable enough that Srilu can be a real canary and Main/VPIN can be promoted by evidence rather than hope.

This is an ops-normalization artifact. It does not change Hermes, install skills, or alter customer state.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Runtime updates | Yes — `hermes update --check` / update flow exists in Hermes docs. | Use check/update semantics as signal, but keep production execution behind Shift Agent gates. |
| Gateway/bridge health | Hermes owns gateway and WhatsApp bridge runtime. | Probe existing services; do not create a parallel service model. |
| Shift Agent patch gate | In-tree custom extension: `tools/check-shift-agent-patch.sh`. | Treat this as the hard gate for patched production. |
| Env/config posture | Existing repo gates: env symlink and config shape checks. | Normalize fleet posture to the deployed Main shape or explicitly document intentional differences. |
| Fleet orchestration | No Hermes-native fleet promotion policy for these three VPSes. | Use repo-local `tools/hermes-fleet-upgrade.py` reports. |

Verdict: **extends-Hermes**. Hermes owns the runtime; this checklist aligns host posture around the existing Shift Agent production contract.

## Current Reference Shape

Main is the current reference because it is the live Shift Agent/Flyer production host:

- `hermes-gateway`: active.
- WhatsApp bridge: listening on `127.0.0.1:3000`.
- Cockpit: active.
- `/opt/shift-agent/.env`: symlink to `/root/.hermes/.env`.
- Latest Shift Agent deploy tag present under `/opt/shift-agent/deploys/`.
- Hermes patch gate exists, but the standalone `/usr/local/bin/check-shift-agent-patch.sh` lacks its adjacent baseline file; deploy-time staging still gates correctly. This should be normalized before execute-mode upgrades.

## First Live Fleet-Check Gaps

### Srilu

- WhatsApp bridge was not listening on `:3000`.
- `/opt/shift-agent/.env` was not the canonical symlink.
- Shift Agent deploy marker was absent.
- Patch gate/baseline was not available in a runnable persistent location.
- Cockpit was not installed or not expected on that host.

### VPIN

- `/opt/shift-agent/.env` was not the canonical symlink.
- Shift Agent deploy marker was absent.
- Patch gate/baseline was not available in a runnable persistent location.
- Cockpit was not installed or not expected on that host.

## Normalization Decisions Needed

1. Decide whether Srilu and VPIN should run the full Shift Agent stack or only Hermes gateway/skills.
2. If they run Shift Agent, install/align:
   - `/opt/shift-agent/.env -> /root/.hermes/.env`
   - `/opt/shift-agent/deploys/deploy-*.tgz` marker convention
   - patch gate plus adjacent `hermes-patch-baseline.txt`
   - bridge port expectation on `127.0.0.1:3000`
   - cockpit expectation, or document `cockpit_status=missing` as intentional
3. If they do not run Shift Agent, exclude them from automated promotion waves until they have a host-specific smoke contract.

## Validation Commands

From this repo:

```powershell
python tools\hermes-fleet-upgrade.py check --format markdown --timeout 15
python tools\hermes-fleet-upgrade.py skill-sync-report --timeout 15
```

For the v0.1 normalization contract report, feed a saved/offline snapshot payload. This mode is intentionally read-only and does not SSH-probe hosts:

```powershell
python tools\hermes-fleet-upgrade.py normalization-report --format markdown --snapshots-json tests\fixtures\hermes_fleet_normalization\blocked_snapshots.json
```

Expected before execute-mode upgrade work:

- Main: green or yellow only for upstream drift.
- Srilu: no red blockers if selected as canary.
- VPIN: no red blockers before it enters a promotion wave.
- Any host that is intentionally non-WhatsApp must be configured/documented with that expectation before bridge status stops being a blocker.

## Stop Conditions

- Unknown Hermes commit.
- Gateway inactive.
- WhatsApp bridge missing where the host is expected to serve WhatsApp traffic.
- Env symlink mismatch on a Shift Agent host.
- Patch gate failed or unavailable on a host expected to run patched production Hermes.
- Missing deploy marker on a Shift Agent host.

## Follow-Up

After normalization is green:

1. Add canary upgrade dry-run for Srilu.
2. Add guarded `--execute --host srilu --candidate SHA --reason ...`.
3. Promote to Main and VPIN only after Srilu evidence is green.
