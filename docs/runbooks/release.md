# Flyer-Studio — Release Modes Runbook

Last updated: 2026-06-29

Every customer-facing change advances through these modes in order; never skip straight to production. Each mode is flag-gated and kill-switchable, and thresholds are pre-registered before any gate flips.

| Mode | Who sees it | Customer impact | Gate posture | Promote when |
|---|---|---|---|---|
| **dormant** | nobody | none | code/schema present but not wired; no model calls, no gate | wiring is approved |
| **shadow** | operator (logs / traces only) | none | computes + records to the trace/log; never gates or alters output | metrics collected; false-positive rate acceptable |
| **internal** | operator only, allowlist `+17329837841` | none (operator-driven) | runs on allowlisted traffic; output reviewed by the operator, not shipped to real customers | internal review clean |
| **canary** | a small scoped set of real customers (allowlist) | yes, scoped | live + measured, kill-switch armed | pre-registered metrics clear on the canary |
| **production** | all customers | yes | full | explicit operator approval after measured canary results |

## Rules
- Activation stays **allowlist-scoped first** (initial allowlist `+17329837841`).
- Every gate is **kill-switchable** (`FLYER_*_KILLSWITCH` or a scoped flag).
- **Pre-register thresholds** before flipping a gate; no full production rollout without measured results + explicit operator approval.
- **Subjective vision-LLM quality scoring** runs shadow / sampled-allowlist only and must NOT block output until reliability is measured on real Flyer-Studio outputs. Deterministic checks may run more broadly only if cheap and safe.
- No release changes the **Hermes version** (pinned 0.14), **migrates WhatsApp**, or **installs community skills**.
- No release **weakens locked-fact enforcement** or **removes the deterministic fallback**, and customer-supplied QR codes are never regenerated.
- Agents do **not** self-merge or self-promote; the operator gates merge + each mode transition.
