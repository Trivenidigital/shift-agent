# PR-ζ.1b — known blockers + regression requirements (tracked from PR-ζ.1a deploy verification)

**Created:** 2026-05-26 19:25 UTC, post-ζ.1a deploy.
**Authority:** operator directive 2026-05-26: bundle the allowlist-basename-mismatch finding into PR-ζ.1b rather than shipping a separate hotfix; non-live smoke/test traffic so urgency drops; track explicitly.

This is an evidence/finding doc tracking issues to bundle into the eventual PR-ζ.1b re-plan. NOT a plan/spec itself.

---

## Blocker 1 — `SAFE_IO_NULL_CONTEXT_ALLOWLIST` source-basename vs deployed-flat-module mismatch

**Root cause:** PR-ζ's `SAFE_IO_NULL_CONTEXT_ALLOWLIST` uses dev-tree basenames. The deploy script `shift-agent-deploy.sh` renames some source files with a `flyer_` prefix on install. The allowlist match (basename of `inspect.stack()` frame) fails at runtime when a renamed file calls `bridge_post`.

**Specific affected entry:**

| Allowlist entry | Source path | Deployed path | Match at runtime? |
|---|---|---|---|
| `"manual_queue.py"` | `src/agents/flyer/manual_queue.py` | `/opt/shift-agent/flyer_manual_queue.py` | ❌ NO — `inspect.stack()` returns `flyer_manual_queue.py` |

**Evidence (kept here per operator directive):**

```
{"ts":"2026-05-26T18:41:45.143822Z","type":"regulated_send_missing_action_context",
 "caller_script":"flyer_manual_queue.py","jid":"201975216009469@lid",
 "message_preview":"Flyer Studio\n------------\nThis flyer project was closed
                    without delivering — the generated flyer didn't pass our quality"}
{"ts":"2026-05-26T18:41:45.637574Z","type":"regulated_send_missing_action_context",
 "caller_script":"flyer_manual_queue.py","jid":"201975216009469@lid",
 "message_preview":"Flyer Studio\n------------\nThis flyer project was closed
                    without delivering — the generated flyer didn't pass our quality"}
```

Both rows fired between PR-ζ deploy (`deploy-20260526-180329-d807b0cc`) and PR-ζ.1a deploy (`deploy-20260526-192336-369c7ffb`). The JID `201975216009469@lid` is non-live smoke/test traffic on a development VPS — confirmed by operator 2026-05-26. **No customer repair action needed.**

**Other allowlist entries audited (no other affected entries today):**

| Entry | Type | Rename risk? |
|---|---|---|
| `shift-agent-health-check.sh`, `shift-agent-notify-owner`, `shift-agent-tail-logger.py`, `shift-agent-fsck.py` | scripts → `/usr/local/bin/` | No (verbatim) |
| `send-daily-brief`, `eod-reconcile`, `check-compliance-deadlines.py` | scripts | No |
| `flyer-recovery-watchdog`, `flyer-source-edit-sla-watchdog` | scripts | No |
| `send-flyer-package`, `send-flyer-campaign` | scripts | No |
| **`manual_queue.py`** | Python module under `src/agents/flyer/` | **YES — renamed to `flyer_manual_queue.py`** ❌ |
| catering + expense scripts | scripts | No |
| `send-coverage-message` | script | No |
| `actions.py`, `hooks.py` | cf-router plugin → `/root/.hermes/plugins/cf-router/` | No (rsync, basename preserved) |

**Fix scope in ζ.1b:** add `"flyer_manual_queue.py"` to the allowlist (single string addition). After the regression test below lands, remove `"manual_queue.py"` since it never matches at runtime.

---

## Regression requirement — flat-module allowlist name verification

**The structural class of bug:** any allowlist entry that resolves to a source file installed under a different basename will silently fail at runtime. The PR-ζ pre-deploy SSH check verified `customer_copy_policy.py` import resolution (via the #270 hotfix) but did NOT audit the full allowlist for the flat-rename pattern.

**Regression test to add in PR-ζ.1b:**

Test sketch — parse `shift-agent-deploy.sh` for `install -m 644 src/agents/flyer/X.py /opt/shift-agent/flyer_X.py` patterns; build a `RENAMES: dict[str, str]` map. Load `SAFE_IO_NULL_CONTEXT_ALLOWLIST`. For every `.py` entry, fail if it appears in `RENAMES.keys()` (which would mean it's the SOURCE name but the deployed name is in `RENAMES.values()`).

Catches this class of bug for any future flyer module added to the allowlist (e.g. a future `intent.py` allowlist entry should land as `flyer_intent.py`).

Plus extend the SSH pre-deploy check pattern to exercise the allowlist runtime resolution for at least one renamed module: assert that `inspect.stack()` from inside the renamed module's deployed path resolves to the deployed basename.

---

## Bundle into PR-ζ.1b scope

When ζ.1b is re-planned (per operator directive — paused awaiting go):

- ADD: blocker fix (`"flyer_manual_queue.py"` allowlist entry) — 1 LOC + remove the stale `"manual_queue.py"` entry once the regression test confirms it's dead
- ADD: regression test above — ~60 LOC
- ADD: pre-deploy SSH check that exercises the allowlist runtime resolution for at least one renamed module
- KEEP: the 80-callsite migration scope already enumerated by PR-ζ.1 reviewers (52 in hooks.py + 7 direct in actions.py + 22 internal sites)
- KEEP: all PR-ζ.1 reviewer findings (default-context pattern is unsafe; helper goes in `action_registry.py` not duplicated; parametrized tests need full enumeration; F7 must also reject `action_context=None` literal; `mutation_class` is premature; etc.)

---

## Why no separate ζ.1a.1 hotfix

Operator decision 2026-05-26: non-live smoke/test traffic; urgency drops; bundling into ζ.1b avoids hotfix-of-hotfix churn. PR-ζ.1a closed the live customer-facing exposure (cancelled-status forbidden verb); the residual `manual_queue.py` allowlist bug doesn't affect real customers on the dev VPS.

**Re-evaluate IF a NEW audit-row pair appears post-ζ.1a-deploy that affects real customers** (different JID, production-shaped traffic). Watch the audit log for new `regulated_send_missing_action_context` rows with `caller_script: flyer_manual_queue.py` (or any other unmapped flat-module name).
