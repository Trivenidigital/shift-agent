# Deploy authorization — 6809bd07

**Date:** 2026-08-22
**Authorized by:** standing autonomous completion mandate (ordinary production fix
within the established runtime architecture; no destructive migration, no money
capability, no new external irreversible capability, rollback known).
**Target:** `6809bd075d1bac41e40878b9880273b269e42926` (== `origin/main` at deploy)
**Box before:** `40064b1a` · **Deploy tag:** `deploy-20260822-201150-6809bd07`

## What changed on the runtime surface

| path | change |
|---|---|
| `src/plugins/shift-agent-read/catering_approvals_tool.py` | NEW — owner-only read |
| `src/plugins/shift-agent-read/roster_tool.py` | NEW — owner-only read |
| `src/plugins/shift-agent-read/__init__.py`, `plugin.yaml` | register the two |
| `src/agents/shift/scripts/shift-agent-deploy.sh` | **mode only** 100644 → 100755, content byte-identical |

Deliberately sequenced ahead of the behavioural PRs (#729 emergency owner pages,
#734 staff outbound). This batch adds no outbound, no writes and no scheduled
work — it is the safest change available and it buys a runtime proof.

## Pre-deploy verification

| check | result |
|---|---|
| Built from clean detached worktree at `origin/main` | `git status` empty |
| Artifact sha256 | `a1f0afb20931b0266e110e475398bea53a7a000cb081fd53c8e17a3012b145dc` |
| Checksum on box after scp | identical |
| skills-manifest lockfile | OK |
| deploy-script diff | `old mode 100644 / new mode 100755` and nothing else |
| Rollback target | `deploy-20260818-005157-40064b1a.tgz` present |
| Pre-deploy tools registered | 3 |

Run with `--skip-pytest`: every constituent PR was CI-green on Linux, and a
Windows run skips the POSIX-only tests, so it is a strictly weaker signal. The
skills-manifest gate did run.

## Post-deploy runtime verification

- Deploy exit 0, **all smoke checks passed**, gateway active (restarted 20:12:13 UTC).
- Box `.commit-hash` = `6809bd07…`.
- Both new modules present on the box (19,483 B and 17,076 B) and `plugin.yaml`
  lists all five tools.
- **`register()` executed against the DEPLOYED tree returns 5 tools**, all under
  toolset `shift_agent_read`:
  `find_nearest_location`, `get_compliance_deadlines`, `get_equipment_maintenance_due`,
  `get_pending_catering_approvals`, `get_roster_capabilities`.
  This closes the open caveat in `agent-reachability-matrix-2026-08-22.md` §7,
  which could not confirm registration against the live environment.
- **Behavioural check against the real store**, without invoking identity
  resolution (which appends audit rows): the deployed tool derives
  `pending_statuses() = {AWAITING_OWNER_APPROVAL, CUSTOMER_FINALIZED}` and
  `awaiting_redraft_statuses() = {OWNER_EDITED}`. Live census is
  `AWAITING_OWNER_APPROVAL 3, CUSTOMER_FINALIZED 2, OWNER_REJECTED 8, CLOSED 4,
  SENT_TO_CUSTOMER 3` → **5 open owner decisions, 0 awaiting redraft.**
  Five is the correct figure; the brief that commissioned this tool said three.

## Not verified

Whether Hermes surfaces the new tools in a live turn's `tool_search`. That needs
a real inbound message and was not attempted — a `/health` or a registration
probe is not the same claim, and it is not being made here.
