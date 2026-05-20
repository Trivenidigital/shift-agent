**Drift-check tag:** extends-Hermes

# Hermes Fleet Upgrade Train Plan — 2026-05-20

## Goal

Set up a safe Hermes upgrade train across the three production VPSes:

- Srilu
- Main
- VPIN

The cadence is:

- **Daily check:** observe current Hermes/runtime posture on every VPS and compare it with upstream.
- **Weekly promotion:** prepare a reviewed promotion plan that advances one canary first, then promotes by wave only after smoke evidence.

This must not auto-pull Hermes `main` into production. The tool should make drift visible, generate operator-safe promotion instructions, and preserve the existing tarball/deploy gates.

## Drift Check

Existing in-tree primitives:

| Surface | Evidence | Decision |
|---|---|---|
| Hermes patch/pin gate | `tools/check-shift-agent-patch.sh` reads `tools/hermes-patch-baseline.txt` and fails closed on live Hermes drift. | Reuse as authoritative gate; do not bypass. |
| Hermes patch application | `tools/patch-hermes.py` applies Shift Agent gateway/WhatsApp bridge patches to Hermes. | Weekly promotion runbook must require patching/verifying the candidate before baseline update. |
| Tarball deploy | `tools/build-deploy-tarball.sh` and `src/agents/shift/scripts/shift-agent-deploy.sh`. | Keep this deploy path; no git checkout on VPS. |
| Canary-style bulk deploy | `tools/canary-bulk-deploy.sh` exists for staggered halt-on-failure deploys. | Reuse the wave idea, but this feature is three-host Hermes-focused rather than broad bulk deploy. |
| Config/env gates | `tools/check-hermes-config-yaml.sh`, `tools/check-env-drift.sh`, deploy symlink gate. | Daily check should report these posture facts where possible. |

Residual gap: there is no fleet-level daily report or weekly Hermes promotion planner for Srilu/Main/VPIN. Operators currently discover Hermes drift only when deploy gates fail.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Skills/plugin discovery | Yes — Hermes Skills Hub documents hundreds of skills and categories at https://hermes-agent.nousresearch.com/docs/skills/. | Use upstream as the source of candidate improvements; daily report should surface upstream drift, not install automatically. |
| Hermes runtime source | Yes — official source is https://github.com/NousResearch/hermes-agent. | Compare deployed commits with upstream HEAD or a provided candidate SHA. |
| Production gateway/bridge integration | In-tree Shift Agent patches already extend Hermes gateway/WhatsApp behavior. | Preserve existing patch gate and baseline file. |
| Fleet orchestration across customer VPSes | No turnkey Hermes skill found for this repo's three-VPS promotion policy. | Build small repo-local ops tooling. |
| Awesome Hermes ecosystem | Checked the community ecosystem reference. | No replacement for this repo-specific deploy gate/promotion train; continue with custom wrapper. |

Verdict: this is **extends-Hermes**. Hermes remains the runtime and skills substrate. The net-new work is fleet reporting and promotion discipline around the existing Shift Agent patch/deploy contract.

## Design

### New CLI

Add `tools/hermes-fleet-upgrade.py` with two safe subcommands:

1. `check`
   - Default hosts: `srilu`, `main-vps`, `vpin`.
   - Collects read-only runtime facts over SSH.
   - Compares each host to upstream HEAD or `--upstream-commit`.
   - Fetches upstream metadata in the Hermes checkout and classifies changed paths by risk.
   - Emits Markdown or JSON.

2. `promotion-plan`
   - Requires `--candidate <sha>`.
   - Emits a weekly promotion checklist in fixed order: Srilu → Main → VPIN.
   - Includes required preflight gates, patch-baseline update reminder, smoke commands, and stop conditions.
   - Does not mutate prod state.

3. `skill-sync-report`
   - Reports installed skills/plugins per host.
   - Surfaces upstream skill-path changes.
   - Marks potentially relevant Flyer/Catering/Shift skill changes as `review-before-install`.
   - Does not install skills.

4. `normalization-report`
   - Compares Srilu/VPIN posture to Main's current reference shape.
   - Lists env symlink, bridge, patch gate, deploy marker, and cockpit gaps before execute-mode upgrades.

### Daily Check Fields

Per host:

- Hermes commit and branch/tag if discoverable.
- Upstream comparison: current, ahead/behind/unknown.
- Gateway service health.
- Cockpit service health where installed.
- WhatsApp bridge port health.
- Env symlink posture.
- Latest Shift Agent deploy tag.
- Skills/plugins counts and examples.
- Patch/baseline posture, when the live gate is available.
- Upstream changed paths and update-risk class.

### Weekly Promotion Rules

- Promote only a reviewed candidate SHA, not an unpinned branch.
- Canary first on Srilu.
- Main second only after Srilu smoke is green.
- VPIN last only after Main smoke is green.
- Update `tools/hermes-patch-baseline.txt` in a PR after the candidate is verified.
- Run `tools/patch-hermes.py` against the candidate before trusting the gateway/bridge path.
- Run `shift-agent-deploy.sh` smoke and pilot readiness after each promotion.
- Stop immediately on missing env symlink, failed patch markers, gateway inactive, bridge disconnected, failed smoke, or unknown commit.

## Tests

Add `tests/test_hermes_fleet_upgrade.py` covering:

- Default fleet order is Srilu → Main → VPIN.
- Health classification marks all-green hosts green.
- Missing commit or inactive gateway is red.
- Missing optional Cockpit on a host is yellow, not red.
- Markdown report includes all hosts, upstream commit, drift, and stop conditions.
- JSON report redacts secrets and is machine readable.
- Promotion plan refuses missing candidate.
- Promotion plan emits the fixed promotion order and review/smoke gates.
- High-risk path diffing marks gateway/bridge/provider/plugin changes as high risk.
- Skill-only upstream changes render as medium/review-before-install.
- Normalization report uses Main as reference and surfaces Srilu/VPIN gaps.

Tests will not SSH. They will exercise pure parsing, classification, and rendering.

## Implementation Notes

- Use only standard library Python.
- Keep SSH probing read-only.
- Avoid printing environment values; report only presence/source.
- The CLI may use normal subprocess capture for local operator use, but documentation must retain the project rule for manual SSH debugging: redirect SSH output to a file and read it separately on Windows.
- Do not edit production state, Hermes checkouts, or VPS configs in this PR.

## Acceptance

- `python -m pytest tests/test_hermes_fleet_upgrade.py -q` passes.
- `python -m py_compile tools/hermes-fleet-upgrade.py` passes.
- `python tools/hermes-fleet-upgrade.py promotion-plan --candidate <40-char-sha> --format markdown` produces a usable weekly plan.
- `git diff --check` passes.
