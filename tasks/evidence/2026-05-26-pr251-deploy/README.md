# PR #251 (PR-α) deploy evidence — 2026-05-26

Audit trail for the PR-α deploy to `main-vps`. Captured during the live deploy session 2026-05-26 01:45–01:55 UTC. Preserved here per operator direction to maintain auditability without scratch clutter.

## Deploy summary

| | |
|---|---|
| Deploy tag | `deploy-20260526-014612-6e0ffeb6` |
| Source commit | `6e0ffeb` (PR #251 squash on `origin/main`) |
| Previous rollback target | `deploy-20260525-234746-e4e5489f` (pre-PR-251) |
| Build method | `tools/build-deploy-tarball.sh --skip-pytest` from clean detached-HEAD worktree at origin/main |
| Pre-deploy sweep | 187 passed, 132 skipped (Linux-only fcntl tests), 0 failed |
| Smoke result | All gates green; pilot readiness 16/16 |
| Auto-rollback | Did NOT fire (clean deploy) |
| Runtime classifier check | 10/10 PR-α phrases classified correctly |

## File inventory

| File | Captures | Source |
|---|---|---|
| `deploy_pr251_out.txt` | Full output of `shift-agent-deploy.sh` on main-vps — Hermes pin gate, config.yaml shape gate, credential-minimized foundation gate, state-file migration, env symlink integrity, install, service restart, smoke checks, pilot readiness | SSH redirect captured via two-step pattern |
| `scp_verify.txt` | Confirmation tarball landed on main-vps at `/tmp/shift-agent-deploy.tgz` with byte count match (3,879,284 bytes) | `ssh main-vps 'ls -la /tmp/shift-agent-deploy.tgz'` |
| `runtime_verify_pr_alpha.txt` | File-level grep of deployed `/root/.hermes/plugins/cf-router/{actions,hooks}.py` confirming PR-α markers + regex patterns + new helper function physically present in the runtime | SSH grep |
| `find_python.txt` | Probe locating Python interpreters available on main-vps (shift-agent venv absent, Hermes venv at `/usr/local/lib/hermes-agent/venv/bin/python`) | SSH find |
| `runtime_classifier_check.txt` | First attempt at inline classifier eval — failed because `/opt/shift-agent/venv/bin/python` does not exist | SSH stderr |
| `runtime_classifier_check2.txt` | Successful inline no-send classifier evaluation using the Hermes venv Python. 10/10 PR-α phrase assertions match expected outcomes including 2 false-positive guards on flyer briefs | SSH Python inline |

## Verification matrix (from runtime_classifier_check2.txt)

| Phrase | Expected | Actual | Status |
|---|---|---|---|
| `I paid` | regulated_account_intent=True | True | OK |
| `I have paid` | True | True | OK |
| `mark paid` | True | True | OK |
| `cancel my plan` | True | True | OK |
| `change phone` | True | True | OK |
| `change my phone number` | True | True | OK |
| `change address` | True | True | OK |
| `Upgrade to Growth` (PR-α-preserved) | True | True | OK |
| `Create a flyer with our phone number 555-1234` (false-positive guard) | False | False | OK |
| `Make a poster showing our address` (false-positive guard) | False | False | OK |
| `update this flyer, change the phone number` (active-project yield helper) | `flyer_text_targets_revision_field=True` | True | OK |
| `I paid` (active-project yield helper) | `flyer_text_targets_revision_field=False` | False | OK |

## Rollback path (if ever needed)

```
sudo /usr/local/bin/shift-agent-deploy.sh rollback deploy-20260525-234746-e4e5489f
```

The previous deploy tag exists at `/opt/shift-agent/deploys/deploy-20260525-234746-e4e5489f.tgz` per the deploy script's tarball-retention policy (KEEP_TARBALLS=5).

## Cross-references

- PR #251: https://github.com/Trivenidigital/shift-agent/pull/251 (merged 2026-05-26T01:08:41Z, squash commit `6e0ffeb`)
- PR #250 (closed superseded): https://github.com/Trivenidigital/shift-agent/pull/250
- Gap-fill sequence doc: `tasks/regulated-intent-gap-fill-pr-sequence-2026-05-26.md`
- Architecture doc: `tasks/regulated-intent-control-layer-architecture-2026-05-25.md`
