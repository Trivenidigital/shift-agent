# Flyer Premium Overlay — Flat-Degrade Fix + Observability — Design

**Date:** 2026-06-19
**Status:** Design for review (no implementation until the plan is approved).
**Drift-check tag:** `extends-Hermes` — adds a `/usr/bin/python3` subprocess render path (mirrors the flat overlay's existing escape hatch), one additive `LogEntry` variant, one `ContextVar`, and one operator-alert call through the existing notify-owner chokepoint. No new storage, no schema migration, no new flag.

---

## 1. Problem (root cause, pinned)

The live premium flyer overlay **never reaches the customer**. Every flyer for the scoped number ships the **flat** coupon-style fallback even though the premium editorial renderer is fully built and verified. This is not a design/background/quality problem — it is a single, 100%-reproducible **environment mismatch**:

| Evidence | Finding |
|---|---|
| `systemctl cat hermes-gateway` → `ExecStart` | Gateway runs under `/root/.hermes/hermes-agent/venv/bin/python` |
| `venv/bin/python -c "import PIL"` | **`ModuleNotFoundError: No module named 'PIL'`** |
| `/usr/bin/python3 -c "import PIL"` | `PIL OK` (system python has Pillow) |
| `flyer_premium_overlay.py:388` | `render_premium_overlay` does an **in-process `from PIL import Image, ImageDraw`** |

The flyer pipeline (`generate-flyer-concepts`) runs inside the gateway venv, which has no Pillow. So `render_premium_overlay` raises `ModuleNotFoundError` on **every** flyer.

**Why flat survives but premium does not:** `_apply_critical_text_overlay` (`render.py`) already anticipates the no-PIL interpreter for the *flat* path — when `apply_critical_text_overlay` raises `"Pillow is required"`, it **shells out to `/usr/bin/python3`** (which has PIL) via the `OVERLAY_RENDERER` subprocess. The *premium* path has **no equivalent escape hatch**: it calls `render_premium_overlay` in-process and dies.

**Why it ships silently (four layers):**
1. `except Exception` in `_apply_critical_text_overlay` swallows the error and falls to flat.
2. It logs only via Python `logging.exception(...)` → **stderr**, never the `decisions.log` audit chokepoint.
3. The subprocess exits 0 (`cf_router_intercepted … subprocess_rc=0`) — looks like success.
4. QA passes on the flat output (all text present, `blockers=[]`).

Net: `decisions.log` records the *integrated→deterministic* hop (`flyer_integrated_fell_back_deterministic`) but **nothing** about premium→flat. The audit trail cannot distinguish a premium ship from a flat ship. That silence destroyed product learning.

## 2. Goals & scope

Two coupled outcomes (operator-approved):

1. **Render fix** — make the premium overlay render in a PIL-capable environment using the **same `/usr/bin/python3` subprocess escape hatch the flat path already uses**. (Explicitly NOT: install Pillow into the hermes venv.)
2. **Observability** — make premium-vs-flat **explicit and measurable** in `decisions.log`, with the exact reason; and **alert the operator on unexpected failures only**.

**In scope:** `_apply_critical_text_overlay` (render path + outcome surfacing) in `render.py`; a `PREMIUM_OVERLAY_RENDERER` subprocess string; one `ContextVar` for the outcome; one additive `LogEntry` variant `FlyerPremiumOverlayOutcome` in `schemas.py`; the audit-emit + alert at the `generate-flyer-concepts` chokepoint; one deploy smoke gate in `shift-agent-smoke-test.sh`.

**Out of scope / unchanged:** the premium overlay's visual design (`premium_overlay.py` drawing logic), the W1 background prompt, the integrated→deterministic recovery rung, the referee/QA matching, the `FLYER_PREMIUM_OVERLAY` / `FLYER_DETERMINISTIC_RECOVERY` flags + allowlist, the combo near-duplicate quirk, Slice 2 cleanup. **No background/overlay/typography/prompt work** (operator directive). Flag-off byte-identical.

## 3. The render fix — premium subprocess escape hatch

Mirror the flat path exactly. In `_apply_critical_text_overlay`, when the premium overlay is enabled and the project is food/grocery:

```
attempt in-process render_premium_overlay(project, source, target, size, output_format):
    success                       → outcome = delivered (render_path=in_process); return
    FlyerRenderError(e)           → outcome = degraded_to_flat (reason_class = fit|coverage|overflow); → flat
    Exception(e)  (ImportError / ModuleNotFound / any runtime error):
        render premium via /usr/bin/python3 subprocess (PREMIUM_OVERLAY_RENDERER):
            rc == 0               → outcome = delivered (render_path=subprocess); return
            rc == 3 (FAILCLOSED)  → outcome = degraded_to_flat (reason_class = fit|coverage); → flat
            rc == other / crash   → outcome = failed_unexpected (reason_class = subprocess_failure|serialization_error|runtime_exception); → flat
        serialization of project failed → outcome = failed_unexpected (reason_class = serialization_error); → flat
```

Key points:
- **In-process first** keeps repo/test behavior byte-identical (PIL is present there → in-process succeeds; the subprocess path is never exercised in tests except where explicitly tested). On the box venv, in-process fast-fails at the PIL import → subprocess.
- **`PREMIUM_OVERLAY_RENDERER`** is a `-c` python source string (like `OVERLAY_RENDERER`). It: `sys.path.insert(0, "/opt/shift-agent")`, reads a temp JSON spec `{project_json, source, target, size, output_format}`, reconstructs the project with `FlyerProject.model_validate_json(project_json)` (pydantic is present under both interpreters), imports `flyer_premium_overlay`, and calls `render_premium_overlay(...)`. It maps `FlyerRenderError → exit 3` (print message to stdout) and any other exception → traceback to stderr + `exit 1`. The parent reads stdout/stderr for `reason_detail`.
- **Serialization:** the project is passed as `project.model_dump_json()` (lossless pydantic round-trip). `source`/`target` are path strings, `size` a list, `output_format` a string.
- **Timeout:** `timeout=60`, matching the flat subprocess.
- **Cost:** premium now spawns one `/usr/bin/python3` per render (preview + finals re-overlay per size). Low-volume + scoped to one number; flat already spawns for its fallback today. Acceptable.

After the fix, the normal box path is: venv in-process attempt → `ModuleNotFoundError` (expected) → subprocess renders premium → **`delivered (render_path=subprocess)`**. This is a success, **not** a failure, and does **not** alert.

## 4. Observability — outcome taxonomy, audit event, alert

### 4.1 Outcome surfacing (render.py → chokepoint)

A new module-level `ContextVar` `_PREMIUM_OVERLAY_OUTCOME` (default `None`), mirroring the existing `_FORCE_BACKGROUND_ONLY` pattern. `_apply_critical_text_overlay` sets it to a small dataclass/dict per render: `{status, reason_class, reason_detail, render_path, output_format}`. The chokepoint (`generate-flyer-concepts`) reads it after the render call and (a) emits the audit event, (b) fires the alert when warranted. `render.py` stays free of audit/alert coupling; the script owns side effects (consistent with the codebase).

### 4.2 `LogEntry` variant (additive)

```python
class FlyerPremiumOverlayOutcome(_BaseEntry):
    type: Literal["flyer_premium_overlay_outcome"] = "flyer_premium_overlay_outcome"
    project_id: str = Field(min_length=1, max_length=40)
    project_version: int = Field(ge=1)
    status: Literal[
        "premium_overlay_delivered",
        "premium_overlay_degraded_to_flat",
        "premium_overlay_failed_unexpected",
    ]
    reason_class: Literal[
        "none",            # delivered
        "fit", "coverage", "overflow",          # expected fail-closed → flat, NO alert
        "missing_pil", "import_error", "subprocess_failure",
        "runtime_exception", "serialization_error",  # unexpected → flat, ALERT
    ] = "none"
    reason_detail: str = Field(default="", max_length=300)  # exact message, e.g. "No module named 'PIL'"
    render_path: Literal["in_process", "subprocess", "none"] = "none"
    output_format: str = Field(default="", max_length=40)
```

Registered into the `LogEntry` union as `Annotated[FlyerPremiumOverlayOutcome, Tag("flyer_premium_overlay_outcome")]`, next to the `FlyerPremiumRepair*` Slice-2 variants. Emitted via the existing `_audit_append(audit_log_path, FlyerPremiumOverlayOutcome(...))` chokepoint.

**Status semantics (operator-specified):**
- `premium_overlay_delivered` — premium rendered (in-process or via the subprocess escape hatch). `reason_class=none`.
- `premium_overlay_degraded_to_flat` — premium could not render *safely* (fit / coverage / overflow). Expected fail-closed; flat is the correct safe outcome. **No alert.**
- `premium_overlay_failed_unexpected` — premium failed for an operational reason (subprocess crash/non-zero, runtime exception, serialization error, **or missing PIL the subprocess could not recover**). **Alert.**

**Important refinement on "missing PIL" (resolves the operator's alert list):** missing PIL *in the gateway venv* is the **expected, recovered** case — the in-process attempt raises `ModuleNotFoundError`, the `/usr/bin/python3` subprocess renders premium, and the outcome is **`premium_overlay_delivered` (render_path=subprocess)** with the in-process import error preserved in `reason_detail` for telemetry. This is the **normal box path after the fix and must NOT alert** (alerting here would fire on every flyer = exactly the noise we are removing). Missing PIL only escalates to `premium_overlay_failed_unexpected` (alert) when the subprocess recovery *itself* fails — e.g., `/usr/bin/python3` is absent or the subprocess crashes. So the operator's "alert on missing PIL" is honored precisely where it matters (recovery impossible) and suppressed where premium actually shipped.

### 4.3 Operator alert (unexpected only — §12b)

When `status == premium_overlay_failed_unexpected`, the chokepoint fires a **plain-text** operator alert via `/usr/local/bin/shift-agent-notify-owner --priority 1 "<msg>"` (subprocess; Telegram-primary chokepoint). The body names the project, status, `reason_class`, and a trimmed `reason_detail`, e.g.:

```
Flyer premium overlay FAILED unexpectedly (F0179): subprocess_failure — premium shipped FLAT. Detail: <...>
```

- **Plain text only** (`parse_mode=None` is the notify-owner default) — avoids the MarkdownV1 underscore-mangling failure mode (§12b). Signal/status tokens contain underscores.
- Alert dispatch is best-effort and **never blocks** rendering/delivery (wrapped like `_audit_append`). The decisions.log event is the durable record; the alert is the push notification on top.
- **No alert** for `delivered` or `degraded_to_flat` — intentional fail-closed is normal product behavior, alerting on it would be noise.

## 5. Deploy smoke gate (operator-required)

Add to `shift-agent-smoke-test.sh`, after the existing premium import/font gate. The new check renders premium **under the gateway interpreter path** and asserts a *delivered* (non-flat) outcome — this is the gate that would have caught the original bug:

- Build a minimal in-memory test `FlyerProject` + a tiny solid-color textless background PNG (rendered with `/usr/bin/python3` so it exists regardless of venv PIL).
- Under the **venv python `$PY`** (the gateway interpreter), with `FLYER_PREMIUM_OVERLAY=1` for the test project, invoke the premium render path (the same in-process-then-`/usr/bin/python3`-subprocess logic) and assert the resulting `_PREMIUM_OVERLAY_OUTCOME.status == premium_overlay_delivered` AND the output PNG exists and is non-empty.
- `FAIL` (exit 1 → auto-rollback) if the outcome is `degraded_to_flat`/`failed_unexpected` or the file is missing. Mirror the existing `if ! "$PY" -c "…"; then echo "FAIL: …"; exit 1; fi` structure; print `✓ premium overlay renders premium under gateway venv path`.

This converts "premium silently degrades to flat" from an invisible runtime condition into a **deploy-blocking** assertion.

## 6. Why this preserves safety

- **Fail-closed intact:** `render_premium_overlay`'s coverage/fit checks are unchanged and still run (now inside the subprocess on the box). A genuine fit/coverage failure still degrades to flat (`degraded_to_flat`, no alert) exactly as today — never an unsafe ship, never manual-worse.
- **Flat fallback still allowed:** any premium failure (expected or unexpected) still degrades to the known-good flat overlay; the customer always gets a correct-text flyer.
- **No silent downgrade:** every premium-enabled render now emits a `decisions.log` event recording premium-vs-flat + reason; unexpected failures additionally alert.
- **Flag-off byte-identical:** the entire block is gated by `_premium_overlay_enabled(project)`; with the flag off, no subprocess, no ContextVar write, no event — identical to today.
- **Repo/test behavior unchanged:** PIL is present in tests → in-process path → existing renderer behavior byte-identical; subprocess + outcome paths covered by their own tests.

## 7. Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Deterministic image (Pillow) rendering | none — Hermes owns ingestion/extraction/dispatch, not pixel rendering | build (our rendering internals) |
| Inter-interpreter subprocess execution | none | build — reuse the in-tree `OVERLAY_RENDERER`/`subprocess.run(["/usr/bin/python3", …])` pattern already in `render.py` |
| Audit/observability event | n/a (platform `LogEntry`/`decisions.log` chokepoint) | reuse `_audit_append` + `ndjson_append`; add one `LogEntry` variant |
| Operator alerting | n/a (platform `shift-agent-notify-owner`, Telegram-primary) | reuse the existing chokepoint CLI |

awesome-hermes-agent ecosystem check: rendering, audit, and alerting here are platform-internal concerns with no Hermes/ecosystem skill overlap. Verdict: no Hermes substrate applies; all work reuses existing in-tree primitives (subprocess render pattern, LogEntry union, audit chokepoint, notify-owner) → `extends-Hermes`, not net-new infrastructure.

## 8. Deployed-pattern compliance (drift checklist)

- **Audit:** new `LogEntry` subclass of `_BaseEntry`, `type: Literal[...]`, `Tag(...)` registration; emitted via `_audit_append` → `FileLock` + `ndjson_append`. ✓
- **No new storage / SQLite / migration:** additive union member only (existing rows keep loading). ✓
- **ContextVar precedent:** mirrors `_FORCE_BACKGROUND_ONLY`. ✓
- **Subprocess render precedent:** mirrors `OVERLAY_RENDERER` + `subprocess.run(["/usr/bin/python3", "-c", …, spec_path], timeout=60)`. ✓
- **Alerting:** reuses `shift-agent-notify-owner` (plain text, §12b). ✓
- **Smoke gate:** mirrors the existing `if ! "$PY" -c "…"; then echo FAIL; exit 1; fi` structure in `shift-agent-smoke-test.sh`. ✓
- **Flags/allowlist:** unchanged; gating via existing `_premium_overlay_enabled`. ✓

## 9. Testing strategy

- **Schema:** `FlyerPremiumOverlayOutcome` validates; `model_dump_json` round-trips; `extra="forbid"` rejects unknown fields; bad `status`/`reason_class` rejected.
- **Render mapping (in-process, PIL present):** success → `delivered/in_process`; `FlyerRenderError` → `degraded_to_flat` with `reason_class∈{fit,coverage,overflow}`, no-alert; unexpected `Exception` → triggers subprocess attempt.
- **Subprocess mapping (mock `subprocess.run`):** rc 0 → `delivered/subprocess`; rc 3 → `degraded_to_flat` (fit/coverage); other rc/crash → `failed_unexpected (subprocess_failure)`; serialization error → `failed_unexpected (serialization_error)`.
- **ContextVar:** `_apply_critical_text_overlay` sets the outcome per branch and resets cleanly; multiple renders (preview + finals) each record.
- **Chokepoint emit + alert:** given each outcome, the script emits the correct `FlyerPremiumOverlayOutcome`; fires the notify-owner subprocess **only** for `failed_unexpected` (mock the CLI); never for `delivered`/`degraded_to_flat`; alert failure does not block.
- **Flag-off byte-identical:** premium disabled → no outcome/event/alert; flat exactly as today.
- **Integration (guarded, runs only where `/usr/bin/python3`+PIL exist, like the smoke font-load check):** `PREMIUM_OVERLAY_RENDERER` over a real textless bg + serialized project → premium PNG produced; an uncoverable-fact project → subprocess exit 3 → `degraded_to_flat`.
- **Codex review** at the renderer change and final; full suite green; flag-off byte-identical asserted.

## 10. Residual risks

- **Subprocess latency/throughput:** 1 spawn per render; acceptable at flyer volumes; `timeout=60` bounds it. If volume grows, batch finals (future).
- **Serialization fidelity:** relies on `FlyerProject` pydantic round-trip; covered by a round-trip test; any failure surfaces as `failed_unexpected (serialization_error)` + alert (loud, not silent).
- **Outcome attribution (§9c):** the audit event is emitted at the rung's preview render (where `flyer_integrated_fell_back_deterministic` is already emitted); finals re-overlay follow the same decision. The plan must verify the ContextVar is read on the exact path that produced the delivered asset, and is reset between renders so a stale value can't mislabel a later one.
- **Alert volume:** if premium regresses for all flyers, every flyer alerts — but that is the intended loudness; scoped to one number keeps it bounded. Dedup/rate-limit deferred.

## 11. Out of scope (deferred)

Premium visual design, W1 background, recovery-rung routing, referee/QA matching, flag/allowlist changes, combo near-duplicate quirk, Slice 2 cleanup, broadening beyond `+17329837841`, alert dedup/rate-limit.
