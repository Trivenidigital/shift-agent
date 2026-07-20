# Deployment-Authorization Package — main `9fb8543` (Phase-3 cleanup) — 2026-07-20

**Status:** READ-ONLY preparation. Nothing deployed. Awaiting explicit authorization.
**Sprint statement (reviewer wording, recorded):** "The production-readiness sprint's
engineering and documentation scope is complete. Remaining operational activations are
separately operator-gated."

## 1. Range + inventory
Live: `3409629` (`deploy-20260720-042204-34096295`). Target: `9fb8543` = origin/main
tip. Range = EXACTLY ONE commit (the #627 Phase-3 cleanup squash). Seven files:
DESIGN.md (tree corrected to live templates only) ·
docs/runbooks/commerce-stripe-onboarding.md (approval-gate defer section) ·
src/agents/compliance/skills/compliance_dispatcher/SKILL.md (DELETED) ·
src/agents/multi_location/skills/multi_location_query/SKILL.md (NOT_WIRED marker) ·
src/agents/shift/templates/dead_man_alert.txt (DELETED) ·
src/platform/schemas.py (ONE comment line — verified comment-only) ·
tools/skills-manifest.txt (regenerated, 32 skills).

## 2. Current on-box artifacts (verified pre-deploy, 2026-07-20)
- `/root/.hermes/skills/compliance_dispatcher/` — EXISTS (pre-cleanup deploys).
- `/opt/shift-agent/templates/dead_man_alert.txt` — EXISTS.
- `/root/.hermes/skills/multi_location_query/` — EXISTS (stays; gains the marker).

## 3. Deletion propagation mechanics (deploy-script verified)
- **Skills: deletion PROPAGATES.** The Shift skills rsync runs `--delete` against
  `/root/.hermes/skills/` FIRST (deploy.sh:265 — wiping anything not in the current
  shift set, including previously-installed per-agent skills), then per-agent
  ADDITIVE rsyncs re-install only skills present in the tarball. compliance_dispatcher
  is in no source dir → removed by the --delete pass, never re-added. (The in-script
  comment documents this ordering as load-bearing.)
- **Templates: installs are ADDITIVE globs** (`install templates/* →
  /opt/shift-agent/templates/`, no deletion pass) → without correction,
  dead_man_alert.txt would LINGER on-box as an inert stale file after deploy.
  **RESOLVED-BY the ops/retired-template-removal PR (reviewer-mandated release-
  integrity correction):** artifact-aware, **dir_fd-ANCHORED** deletion of the
  exact entry name under the exact parent. The reviewer's protected-parent contract
  FAILED on box (`/opt/shift-agent/templates` is runtime-writable: shift-agent:
  shift-agent 0755, `runuser -u shift-agent -- test -w` SUCCEEDS), so a pathname `rm`
  would be TOCTOU-exposed. The deploy-only, root-executed python3 helper instead opens
  the parent ONCE with `O_DIRECTORY | O_NOFOLLOW` (a symlinked parent → FATAL),
  validates it via `fstat` against the realpath inode (same-inode anchor) and REPORTS
  owner/group/mode into the deploy log (parent-writability is disclosed, not asserted —
  it is exactly why the anchor is required), then `lstat`/`unlink` the entry ANCHORED to
  that directory fd (immune to parent-path swaps; unlink never follows the leaf).
  Outcomes: staged-present → normal install (rollback-restore path); staged-omitted →
  absent = idempotent success; a DIRECTORY at the name = FATAL before restart (unlink
  cannot remove it, recursive removal forbidden); any other object (regular / symlink /
  FIFO / …) = unlinked (a swapped-in symlink is unlinked with its target untouched —
  any object at the retired name IS retired). A same-anchor `lstat` verification assert
  in the smoke section fails the deploy if the entry lingers. No wildcards, no
  template-dir `rsync --delete`, unrelated templates untouched. **The revised deployment
  target becomes that PR's squash SHA once merged** (superseding 9fb8543 as the deploy
  target; this package's other sections carry forward unchanged).

## 4. Zero live runtime consumers (both artifacts)
Re-verified at the merged tree: zero functional references repo-wide; dispatcher
matrix routes compliance to compliance_owner_query; no render-coverage-template
caller passes dead_man_alert; health/dead-man contracts served by
shift-agent-notify-owner + proposal-sweep.

## 5. On-box manifest transition
The tarball ships the regenerated `tools/skills-manifest.txt` (32 entries) →
installed to `/usr/local/share/shift-agent/skills-manifest.txt` by the existing
install block; the deploy's skills-manifest gate validates manifest-vs-tree
fail-closed, so a mismatch aborts before restart. Post-deploy check: the installed
manifest contains no compliance_dispatcher line and 32 skill entries.

## 6. multi_location_query: present but NOT_WIRED
The SKILL redeploys WITH its NOT_WIRED marker (still no dispatcher row — routing
behavior unchanged); activation requirements recorded in the marker itself.

## 7. schemas.py comment-only
Range diff for schemas.py = exactly one added comment line (`# NOT YET INVOKED …`);
no code-token change; zero runtime effect.

## 8. Smoke plan (skill loading + dispatcher health after removal)
Built-in smoke already covers: scripts present/executable, Python modules
importable, cf-router compile + classifier sanity, catering schema/transitions,
config gates. Post-deploy read-only additions: `/root/.hermes/skills/` listing
shows compliance_dispatcher ABSENT + multi_location_query PRESENT (with marker
line grep); installed manifest = 32 entries, no removed skill; gateway active +
journal clean (normal skill loading); compliance LIVE path intact
(check-compliance-deadlines timer + compliance_owner_query SKILL present);
dispatcher smoke = existing replay/accuracy checks in the suite, no live sends.

## 9. Rollback
`shift-agent-deploy.sh rollback deploy-20260720-042204-34096295` (tarball retained,
newest on box). Restoration is complete by construction: the prior tarball CONTAINS
both deleted files, so the shift `--delete` rsync re-installs compliance_dispatcher
and the template glob re-installs dead_man_alert.txt; the prior manifest (33 skills)
reinstalls with it. No data migration; locks and sidecar state untouched either way.

## 10. Nothing rides along
No discriminator activation, allowlist, or wildcard (env untouched — re-verified
zero CATERING_AMENDMENT vars in §2's probe run); no Pushover change; no Stripe/
commerce activation; no multi-location wiring; no R2B-2..4/R3/R4 content; no
runtime configuration or production data change. Code+docs-only tarball from clean
`9fb8543` through both build gates.

## Approvals log
- 2026-07-20: reviewer accepted #627 merge closeout; instructed this READ-ONLY
  package. Deployment: PENDING explicit authorization. All operational activations
  separately held.
