# PR #252 (PR-β) deploy evidence — 2026-05-26

Audit trail for the PR-β deploy to `main-vps`. Captured during the live deploy session 2026-05-26 02:49–02:55 UTC. Preserved here matching the PR #251 evidence pattern.

## Deploy summary

| | |
|---|---|
| PR | https://github.com/Trivenidigital/shift-agent/pull/252 |
| Deploy tag | `deploy-20260526-024934-9bb5c4d0` |
| Source commit | `9bb5c4d` (PR #252 squash on `origin/main`) |
| Rollback target | `deploy-20260526-014612-6e0ffeb6` (PR-α deploy) |
| Build method | `tools/build-deploy-tarball.sh --skip-pytest` from clean detached-HEAD worktree at `origin/main` |
| Pre-deploy sweep | 197 passed, 132 skipped (Linux-only fcntl tests), 0 failed in 2.16s — `tests/test_cf_router_flyer_routing.py tests/test_cf_router_plugin.py tests/test_safe_io_bridge_post.py -q` |
| Smoke result | All gates green; pilot readiness 16/16 |
| Auto-rollback | Did NOT fire (clean deploy) |
| Runtime classifier check | 8/8 phrases classified correctly per the operator-specified verification list |

## Runtime classifier verification matrix

**Positive cases (must classify as `is_flyer_delivery_state_intent=True`):**

| Phrase | Expected | Actual | Status |
|---|---|---|---|
| `where is my flyer` | True | True | OK |
| `did you send my flyer` | True | True | OK |
| `send my flyer` | True | True | OK |
| `approve` | True | True | OK |
| `I approve` | True | True | OK |

**Negative cases (must classify as `is_flyer_delivery_state_intent=False`):**

| Phrase | Expected | Actual | Status |
|---|---|---|---|
| `send now` | False | False | OK (PR-β.1 deferred — locked here) |
| `approve this concept` | False | False | OK |
| `Create a flyer with our address` | False | False | OK |

OVERALL: **GREEN** — all 8 phrase assertions match expected outcomes on the deployed runtime.

## File inventory

| File | Captures | Source |
|---|---|---|
| `deploy_pr252_out.txt` | Full output of `shift-agent-deploy.sh` on main-vps — Hermes pin gate, config.yaml shape gate, credential-minimized foundation, state-file migration, env symlink integrity, install, service restart, smoke checks, pilot readiness | SSH redirect via two-step Windows pattern |
| `scp_verify_pr252.txt` | Confirmation tarball landed on main-vps at `/tmp/shift-agent-deploy.tgz` with byte count match (3,880,764 bytes) | `ssh main-vps 'ls -la /tmp/shift-agent-deploy.tgz'` |
| `runtime_classifier_pr252.txt` | Inline no-send classifier evaluation using the Hermes venv Python. 8/8 PR-β phrase assertions match expected outcomes (5 positive + 3 negative including the explicit `send now` PR-β.1 deferral assertion) | SSH Python inline |

## Constraints honored

- ✅ Deployed `origin/main` at `9bb5c4d` from clean detached-HEAD worktree (NOT any local doc commits)
- ✅ No Flyer recovery lane changes (codex/flyer-full-autonomous-recovery branch + codex-flyer-autodev-main.timer untouched)
- ✅ No timer changes beyond standard service restart
- ✅ Read-only / no-send runtime verification (classifier eval only)
- ✅ `send now` PR-β.1 deferral explicitly re-confirmed False on deployed runtime
- ✅ False-positive guards (`approve this concept`, `Create a flyer with our address`) confirmed False

## Rollback path (if ever needed)

```
sudo /usr/local/bin/shift-agent-deploy.sh rollback deploy-20260526-014612-6e0ffeb6
```

That returns the runtime to the PR-α deploy state (PR-β code rolled back, PR-α code retained).

## Cross-references

- PR #252: https://github.com/Trivenidigital/shift-agent/pull/252 (merged 2026-05-26T02:45:42Z, squash commit `9bb5c4d`)
- PR #251 deploy evidence: `tasks/evidence/2026-05-26-pr251-deploy/README.md`
- Gap-fill sequence doc: `tasks/regulated-intent-gap-fill-pr-sequence-2026-05-26.md`
- Architecture doc: `tasks/regulated-intent-control-layer-architecture-2026-05-25.md`
