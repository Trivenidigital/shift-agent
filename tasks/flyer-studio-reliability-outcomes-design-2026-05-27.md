**Drift-check tag:** extends-Hermes

# Flyer Studio Reliability Outcomes Design - 2026-05-27

## Plan Link

Plan: `tasks/flyer-studio-reliability-outcomes-plan-2026-05-27.md`

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Message ingress and customer identity | yes - Hermes gateway + cf-router + `identify-sender` | reuse; no new router or identity resolver |
| Flyer rendering | yes - existing Flyer render module and system-Pillow fallback convention | extend the existing fallback to exact identity overlay |
| Recovery and worker drafts | yes - existing `flyer-recovery-watchdog` modes | reuse states and transition points |
| Operator alerting | yes - `safe_io.notify_owner_with_fallback` | reuse transport; add Flyer-specific typed audit result |
| Semantic brief/product direction | partial - existing `flyer_semantic_brief` and Hermes LLM substrate | no new classifier in this PR; record follow-up only |

Awesome Hermes ecosystem check: no upstream Hermes skill replaces these project-specific reliability contracts. The design extends existing Hermes/Flyer substrate rather than adding a parallel engine.

## Design Decisions

### D1 - Exact Identity Overlay Uses Existing System-Pillow Fallback

`src/agents/flyer/render.py::apply_exact_identity_overlay` currently raises `FlyerRenderError("Pillow is required for exact identity overlay")` when the Hermes venv lacks `PIL`. Other render paths already fall back to `/usr/bin/python3` and its system Pillow install.

Change:

- Add `_exact_identity_overlay_payload(project, source, target, size)` to collect only serializable overlay inputs: paths, dimensions, business, schedule, location, contact.
- Add `EXACT_IDENTITY_OVERLAY_RENDERER` subprocess script string using `/usr/bin/python3`, `PIL.Image`, `ImageDraw`, and `ImageFont`.
- `apply_exact_identity_overlay(...)` keeps the current local-Pillow implementation when `_load_pillow()` succeeds.
- If local Pillow is absent, call `_apply_exact_identity_overlay_with_system_pillow(...)`.
- If `/usr/bin/python3` is missing or the subprocess fails, raise `FlyerRenderError("Pillow is unavailable for exact identity overlay: ...")`.

Rationale: this honors the existing render-module contract and avoids mutating the externally owned Hermes venv from app deploy code.

### D2 - Dependency Missing Is a Typed Manual-Review Reason

`src/platform/schemas.py`:

- Add `dependency_missing` to `FlyerManualReviewReason`.

`src/agents/flyer/scripts/generate-flyer-concepts`:

- Add `_draft_error_is_dependency_missing(lower: str) -> bool` matching deterministic dependency/import failures:
  - `pillow is required`
  - `pillow is unavailable`
  - `modulenotfounderror`
  - `no module named`
  - `importerror`
- Source-edit and draft-render failure mapping order becomes:
  1. visual QA
  2. dependency missing
  3. provider unavailable for provider/key/endpoint/manual-review errors
  4. provider timeout fallback

This keeps transient provider/network issues separate from deterministic runtime misconfiguration.

### D3 - FlyerProject Persists Sender Origin

`src/platform/schemas.py::FlyerProject`:

- Add `customer_id: str = Field(default="", max_length=40)`.
- Add `chat_id: str = Field(default="", max_length=200)`.

`src/agents/flyer/scripts/create-flyer-project`:

- It already parses `--chat-id` and resolves `customer = _find_customer_for_sender(...)`.
- Populate `chat_id=args.chat_id` and `customer_id=customer.customer_id if customer else ""`.
- Existing rows parse because both fields default to empty strings.

No sender data is inferred from text. The only source is the existing customer store lookup.

### D4 - Stale Manual Recovery Uses Project Origin Evidence

`src/agents/flyer/recovery.py::classify_stale_manual_project`:

- Set `chat_id` from `project["chat_id"]` when present.
- Use `evidence_quality="strong"` when project has both a nonblank `chat_id` and nonblank `original_message_id`; otherwise keep `weak`.
- Keep `provider_message_id` from `original_message_id`.

This makes durable project state usable as recovery evidence when recent audit rows have aged out.

### D5 - Owner Alerts Are Transition-Scoped and Audited

Add schema:

```python
class FlyerRecoveryOwnerAlert(_BaseEntry):
    type: Literal["flyer_recovery_owner_alert"]
    incident_id: str
    project_id: str = ""
    trigger: Literal["customer_ack_suppressed", "operator_action_required"]
    outcome: Literal["sent", "failed"]
    reason: str = ""
    notify_source: str = "flyer-recovery-watchdog"
```

Add it to `LogEntry`.

Incident state tracks alert delivery separately:

```json
"owner_alert": {
  "trigger": "operator_action_required",
  "status": "sent|failed",
  "last_attempted_at": "...",
  "attempt_count": 1
}
```

`src/agents/flyer/scripts/flyer-recovery-watchdog`:

- Import `notify_owner_with_fallback` from `safe_io`.
- Add `_alert_owner_for_recovery(...) -> bool`, returning the bool from `notify_owner_with_fallback`.
- Add `_append_owner_alert_audit(...)` to write `FlyerRecoveryOwnerAlert`.
- Alert only inside existing write sites that already transition state:
  - customer ack `none -> suppressed` with reason `missing_strong_customer_origin_evidence`
  - incident `open -> operator_action_required`
- Do not alert in `--dry-run`.
- Do not alert for terminal/no-send reasons such as `mode:worker_draft`; those are expected non-ack modes.
- If the alert succeeds, persist `owner_alert.status="sent"` and do not alert again for that trigger.
- If the alert fails, persist `owner_alert.status="failed"` and retry on later watchdog scans with a bounded cooldown equal to `operator_escalation_stale_minutes`. This keeps transient Pushover/notify outages from becoming silent one-shot failures.
- The retry path only runs for incidents already in `operator_action_required` or ack `suppressed` with the missing-origin reason; it does not create new customer sends.

### D6 - Smoke Gate Exercises the Real Overlay Path

`src/agents/shift/scripts/shift-agent-smoke-test.sh`:

- Extend the existing Flyer smoke area with a small Hermes-venv Python snippet that:
  - imports `flyer_render`, `schemas.FlyerProject`, and related models from deployed paths
  - writes a tiny source PNG using whichever path is available:
    - local Pillow if available in Hermes venv
    - otherwise `/usr/bin/python3` with system Pillow
  - calls `flyer_render.apply_exact_identity_overlay(...)`
  - asserts the target PNG exists and is nonempty

This catches the F0105 failure class without requiring `import PIL` to succeed in the Hermes venv.

### D7 - F0105 Runbook Note

Add `tasks/runbooks/f0105-post-deploy-recovery-2026-05-27.md`:

- state that the PR does not send from dev/local
- after merge/deploy authorization, run the smoke gate, then recover F0105 by rerunning generation or operator repair
- verify one of:
  - `flyer_assets_delivered` or `flyer_closure_customer_notified` for F0105
  - explicit operator handoff if the customer should not receive a generated asset

## Tests

- `tests/test_flyer_renderer.py`
  - system fallback test for exact identity overlay when local `_load_pillow()` returns `None`; on Windows, monkeypatch `Path("/usr/bin/python3").exists` and `subprocess.run` instead of requiring a POSIX path.
  - local+system fallback failure raises a dependency-shaped `FlyerRenderError`, and `generate-flyer-concepts` maps that path to `dependency_missing`.
- `tests/test_flyer_generate_concepts.py`
  - draft render dependency error queues `dependency_missing`.
  - source-edit dependency error queues `dependency_missing`.
- `tests/test_flyer_create_project.py`
  - project creation persists `customer_id` and `chat_id` for a resolved customer.
  - old minimal project row without those fields validates through `FlyerProjectStore`.
- `tests/test_flyer_recovery_watchdog.py`
  - stale manual project signal with persisted chat has strong evidence and nonblank chat hash.
  - `worker_draft` operator-action transition sends one owner alert and writes `flyer_recovery_owner_alert`.
  - rerun after successful alert does not re-alert.
  - failed owner alert is retried only after bounded cooldown and writes attempted/failed audit evidence.
  - dry-run reports escalation without alerting or mutating.
  - customer-ack suppression for missing strong evidence is tested only in `customer_ack` mode, because deployed `worker_draft` never processes customer acks.
- Smoke shell syntax check for `shift-agent-smoke-test.sh`.

## Verification Commands

```powershell
python -m pytest tests/test_flyer_renderer.py tests/test_flyer_generate_concepts.py tests/test_flyer_create_project.py tests/test_flyer_recovery_watchdog.py -q
python -m py_compile src/agents/flyer/render.py src/platform/schemas.py
python -m py_compile src/agents/flyer/recovery.py
python -m py_compile src/agents/flyer/scripts/generate-flyer-concepts src/agents/flyer/scripts/create-flyer-project src/agents/flyer/scripts/flyer-recovery-watchdog
git diff --check origin/main...HEAD
```

On Linux/VPS before deploy:

```bash
bash -n /usr/local/bin/shift-agent-smoke-test
/usr/local/bin/shift-agent-smoke-test
```

## Non-Goals

- No merge/deploy in this PR step.
- No live F0105 re-render from dev.
- No Hermes-venv package mutation.
- No new semantic classifier or replacement of existing `flyer_semantic_brief`.
- No ARE auto-PR/deploy promotion.

## Design Review Fold-In

- Implementation reviewer MAJOR: renderer fallback unit tests must not depend on `/usr/bin/python3` existing on Windows. Resolution: tests monkeypatch path existence and subprocess behavior; VPS smoke covers the real Linux binary.
- Implementation reviewer MAJOR: customer-ack suppression is not part of deployed `worker_draft`. Resolution: suppression alert tests stay in `customer_ack`; F0105 deployed-mode behavior is covered through `operator_action_required`.
- Operational reviewer MAJOR: failed owner alerts cannot be one-shot deduped. Resolution: incident `owner_alert` state records sent/failed, suppresses only sent alerts, and retries failed alerts after the configured stale interval.
- Operational reviewer MAJOR: exact-overlay local+system failure must map to `dependency_missing`. Resolution: tests explicitly cover that render failure shape through `generate-flyer-concepts`.
