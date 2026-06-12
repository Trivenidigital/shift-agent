# PR #260 (PR-β.1) deploy evidence — 2026-05-26

Audit trail for the PR-β.1 deploy to `main-vps`. Captured during the live deploy session 2026-05-26 12:46–12:50 UTC. Preserved here matching the PR #251 / PR #252 evidence pattern.

This deploy shipped `origin/main` at `08ac395` — which stacks PR-β.1 on top of two intervening codex-automation merges (PR #259 stale-manual-queue triage, PR #261 self-eval CLI contract assertions). Cherry-pick path was explicitly rejected per operator direction ("creates a special runtime that no longer matches main").

## Deploy summary

| | |
|---|---|
| PR | https://github.com/Trivenidigital/shift-agent/pull/260 |
| Deploy tag | `deploy-20260526-124624-08ac3952` |
| Source commit | `08ac395` (current `origin/main` — includes PR-β.1 + PR #259 + PR #261) |
| Rollback target | `deploy-20260526-123009-ee14fdd8` (codex-automation intermediate deploy between PR-β and PR-β.1) |
| Build method | `tools/build-deploy-tarball.sh --skip-pytest` from clean detached-HEAD worktree at `origin/main` |
| Pre-deploy sweep | **267 passed, 121 skipped, 0 failed** in 3.14s — `tests/test_cf_router_flyer_routing.py tests/test_cf_router_plugin.py tests/test_flyer_self_evaluation.py tests/test_operator_brief.py -q` (broader than PR-α/PR-β sweeps because the deploy ships #259 + #261 alongside PR-β.1) |
| Smoke result | All gates green; pilot readiness 16/16; cf-router classifier sanity green; all Flyer smokes green |
| Auto-rollback | Did NOT fire (clean deploy) |
| Runtime classifier check | **12/12 phrase assertions match** expected outcomes per the operator's specified verification list (positive + negative + PR-β regression) |

## Runtime classifier verification matrix

**PR-β.1 positive cases (must classify as `is_flyer_delivery_state_intent=True` AND `is_flyer_send_now_intent=True`):**

| Phrase | `delivery_state_intent` | `send_now_intent` | Status |
|---|---|---|---|
| `send now` | True | True | ✅ |
| `please send my flyer now` | True | True | ✅ |
| `send the flyer now` | True | True | ✅ |

**PR-β.1 negative cases (must classify as False both ways — start-anchor + verb-structure defense):**

| Phrase | `delivery_state_intent` | `send_now_intent` | Status |
|---|---|---|---|
| `Create a flyer that says send now` | False | False | ✅ (start-anchor catches embedded send-now) |
| `send to customers Friday` | False | False | ✅ (no "now" word) |
| `send me ideas` | False | False | ✅ (no "now" word) |

**PR-β regression (must still match as `is_flyer_delivery_state_intent=True`):**

| Phrase | `delivery_state_intent` | Status |
|---|---|---|
| `where is my flyer` | True | ✅ |
| `did you send my flyer` | True | ✅ |
| `send my flyer` | True | ✅ |

OVERALL: **GREEN** — 12/12 phrase assertions match expected outcomes on the deployed runtime.

## File inventory

| File | Captures | Source |
|---|---|---|
| `deploy_pr260_out.txt` | Full output of `shift-agent-deploy.sh` on main-vps — Hermes pin gate, config.yaml shape gate, credential-minimized foundation, state-file migration, env symlink integrity, install, service restart, smoke checks, pilot readiness | SSH redirect via two-step Windows pattern |
| `runtime_classifier_pr260.txt` | Inline no-send classifier evaluation using the Hermes venv Python. 12/12 phrase assertions match expected outcomes (3 PR-β.1 positive × 2 classifiers + 3 negative × 2 classifiers + 3 PR-β regression) | SSH Python inline |

(No separate SCP-verify scratch file produced this session — the SCP step was inline + verified by the subsequent deploy script's tar-extract success.)

## Constraints honored

- ✅ Deployed `origin/main` at `08ac395` from clean detached-HEAD worktree (NOT the local PR-β.1 branch with its 3 commits ahead of pre-merge state)
- ✅ Cherry-pick path explicitly rejected per operator reasoning ("the deploy stack has been tracking main, and the intervening commits #259, #261 are already part of the verified current main")
- ✅ Broader pre-deploy sweep (4 test files, 267 tests) because this ships more than PR-β.1
- ✅ Post-deploy verification was no-send / classifier-only — no `bridge_post`, no state mutation, no live customer messages
- ✅ `send now` deferral REVERSED on deployed runtime (was the PR-β.1 success criterion)
- ✅ Start-anchor false-positive defense confirmed live (`Create a flyer that says send now` → False on deployed code)
- ✅ PR-β regression locked on deployed runtime
- ✅ Flyer recovery lane untouched
- ✅ No timer changes beyond standard service restart

## Rollback path (if ever needed)

```
sudo /usr/local/bin/shift-agent-deploy.sh rollback deploy-20260526-123009-ee14fdd8
```

That returns the runtime to the codex-automation intermediate deploy (between PR-β and PR-β.1). To roll back further to PR-β proper:

```
sudo /usr/local/bin/shift-agent-deploy.sh rollback deploy-20260526-024934-9bb5c4d0
```

Both targets exist in `/opt/shift-agent/deploys/` per the deploy script's KEEP_TARBALLS=5 retention.

## Cross-references

- PR #260: https://github.com/Trivenidigital/shift-agent/pull/260 (merged 2026-05-26T12:39:19Z, squash commit `08ac395`)
- PR #251 deploy evidence: `tasks/evidence/2026-05-26-pr251-deploy/README.md`
- PR #252 deploy evidence: `tasks/evidence/2026-05-26-pr252-deploy/README.md`
- Gap-fill sequence doc: `tasks/regulated-intent-gap-fill-pr-sequence-2026-05-26.md`
- Architecture doc: `tasks/regulated-intent-control-layer-architecture-2026-05-25.md`

## Audit-trail note

This evidence is committed on the orphaned local branch `fix/flyer-send-now-deterministic` (remote deleted post-merge). Per operator direction, the evidence is intended to fold into the next PR-γ rather than open a separate docs-only PR. Cherry-pick into PR-γ's branch when that work starts.
