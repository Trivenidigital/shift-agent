# Flyer Premium Overlay — Flat-Degrade Fix + Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the premium flyer overlay actually render in the live (PIL-less) gateway venv via the same `/usr/bin/python3` subprocess escape hatch the flat path already uses, and make every premium-vs-flat outcome explicit in `decisions.log` (alerting only on unrecovered unexpected failures).

**Architecture:** `_apply_critical_text_overlay` tries `render_premium_overlay` in-process; on any import/runtime failure it re-renders premium in a `/usr/bin/python3` subprocess (project serialized as JSON). Every premium-enabled render records a `PremiumOverlayOutcome` on a `ContextVar`; the `generate-flyer-concepts` chokepoint consumes it, writes a `flyer_premium_overlay_outcome` audit row, and fires an operator alert only when the outcome is `premium_overlay_failed_unexpected`. A deploy smoke gate renders premium under the venv interpreter and fails the deploy unless the outcome is `delivered`.

**Tech Stack:** Python 3, Pydantic v2, pytest; Pillow lives only in system `/usr/bin/python3` (NOT the hermes venv); audit via `safe_io.ndjson_append`; alerts via `shift-agent-notify-owner`.

**Design doc:** `docs/superpowers/specs/2026-06-19-flyer-premium-flat-degrade-observability-design.md`
**Drift-check tag:** `extends-Hermes`.
**Authoritative source:** `origin/main` (`0fc1bac`). All line numbers below are from `origin/main`.
**Test command (Windows):** `PYTHONPATH="src;src/platform" python -m pytest <path> -v`

**Invariants (preserve):** fail-closed unchanged; flat fallback still allowed on any premium failure; flag-off (`FLYER_PREMIUM_OVERLAY` unset) byte-identical; no new flag; no schema migration (additive `LogEntry` variant only); `premium_overlay.py` drawing logic, W1 prompt, recovery rung, referee/QA all UNCHANGED.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/platform/schemas.py` | `FlyerPremiumOverlayOutcome` LogEntry variant + union registration | additive |
| `src/agents/flyer/render.py` | `PremiumOverlayOutcome` dataclass, outcome ContextVar + accessors, `PREMIUM_OVERLAY_RENDERER` subprocess string, premium-with-fallback render, rewired `_apply_critical_text_overlay` | modify |
| `src/agents/flyer/scripts/generate-flyer-concepts` | consume outcome → emit audit event + conditional alert, after each render | modify |
| `src/agents/shift/scripts/shift-agent-smoke-test.sh` | deploy gate: render premium under venv path, assert `delivered` | additive |
| `tests/test_flyer_schemas.py` | variant validation/dispatch | additive tests |
| `tests/test_flyer_renderer.py` | dataclass/ContextVar/fallback mapping/flag-off | additive tests |
| `tests/test_flyer_premium_outcome_script.py` | script glue (emit + alert) | new file |

---

## Task 1: `FlyerPremiumOverlayOutcome` LogEntry variant

**Files:**
- Modify: `src/platform/schemas.py` (add class after `FlyerPremiumRepairSucceeded` ~line 4544; add union member after line 5960)
- Test: `tests/test_flyer_schemas.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_flyer_schemas.py`)

```python
def test_flyer_premium_overlay_outcome_dispatches_through_log_entry():
    from datetime import datetime, timezone
    from pydantic import TypeAdapter, ValidationError
    from schemas import LogEntry, FlyerPremiumOverlayOutcome

    now = datetime.now(timezone.utc).isoformat()
    delivered = {
        "type": "flyer_premium_overlay_outcome", "ts": now,
        "project_id": "F0179", "project_version": 2,
        "status": "premium_overlay_delivered", "reason_class": "none",
        "reason_detail": "ModuleNotFoundError: No module named 'PIL'",
        "render_path": "subprocess", "output_format": "concept_preview",
    }
    adapter = TypeAdapter(LogEntry)
    parsed = adapter.validate_python(delivered)
    assert isinstance(parsed, FlyerPremiumOverlayOutcome)
    assert parsed.status == "premium_overlay_delivered"
    assert parsed.render_path == "subprocess"

    # extra="forbid" rejects unknown fields
    with pytest.raises(ValidationError):
        adapter.validate_python({**delivered, "bogus": 1})
    # invalid status rejected
    with pytest.raises(ValidationError):
        adapter.validate_python({**delivered, "status": "premium_overlay_maybe"})
    # defaults: minimal payload validates
    minimal = {
        "type": "flyer_premium_overlay_outcome", "ts": now,
        "project_id": "F0179", "project_version": 1,
        "status": "premium_overlay_failed_unexpected", "reason_class": "subprocess_failure",
    }
    assert adapter.validate_python(minimal).render_path == "none"
```

(If `pytest` / `datetime` are already imported at the top of `test_flyer_schemas.py`, do not re-import them at module level; the in-function imports above are self-contained.)

- [ ] **Step 2: Run — expect FAIL** (`ImportError: cannot import name 'FlyerPremiumOverlayOutcome'`)

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_schemas.py -k premium_overlay_outcome -v`
Expected: FAIL (name does not exist).

- [ ] **Step 3: Add the class** in `src/platform/schemas.py`, immediately after the `FlyerPremiumRepairSucceeded` class (after ~line 4544):

```python
class FlyerPremiumOverlayOutcome(_BaseEntry):
    """Flat-degrade observability (2026-06-19). Emitted once per premium-enabled
    render recording whether the deterministic PREMIUM overlay actually shipped,
    degraded to the flat overlay, or failed unexpectedly — and WHY. Ends the
    silent premium->flat downgrade (premium raised in the PIL-less gateway venv
    and was swallowed). `status` is the delivered-asset type; `reason_class`
    drives alerting (only `premium_overlay_failed_unexpected` pages the
    operator); `render_path` says how premium rendered (in_process vs the
    /usr/bin/python3 subprocess escape hatch)."""
    type: Literal["flyer_premium_overlay_outcome"] = "flyer_premium_overlay_outcome"
    project_id: str = Field(min_length=1, max_length=40)
    project_version: int = Field(ge=1)
    status: Literal[
        "premium_overlay_delivered",
        "premium_overlay_degraded_to_flat",
        "premium_overlay_failed_unexpected",
    ]
    reason_class: Literal[
        "none",
        "fit", "coverage", "overflow",
        "missing_pil", "import_error", "subprocess_failure",
        "runtime_exception", "serialization_error",
    ] = "none"
    reason_detail: str = Field(default="", max_length=300)
    render_path: Literal["in_process", "subprocess", "none"] = "none"
    output_format: str = Field(default="", max_length=40)
```

- [ ] **Step 4: Register the union member** in `src/platform/schemas.py` in the `LogEntry = Annotated[Union[ ... ]]` block, immediately after the `FlyerPremiumRepairSkipped` line (after line 5960):

```python
        Annotated[FlyerPremiumOverlayOutcome, Tag("flyer_premium_overlay_outcome")],
```

(No change to `_pick_log_entry_tag` / `_build_known_log_entry_types()` — variants auto-register via `Tag(...)`.)

- [ ] **Step 5: Run — expect PASS**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_schemas.py -k premium_overlay_outcome -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/platform/schemas.py tests/test_flyer_schemas.py
git commit -m "feat(flyer): add FlyerPremiumOverlayOutcome LogEntry variant"
```

---

## Task 2: Outcome dataclass, ContextVar, and accessors (render.py)

**Files:**
- Modify: `src/agents/flyer/render.py` (after `_FORCE_BACKGROUND_ONLY` at line 148)
- Test: `tests/test_flyer_renderer.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_flyer_renderer.py`)

```python
def test_premium_overlay_outcome_contextvar_consume_and_alert():
    from agents.flyer import render as r
    # initial state: nothing recorded
    assert r.consume_premium_overlay_outcome() is None
    out = r.PremiumOverlayOutcome(
        status="premium_overlay_failed_unexpected", reason_class="subprocess_failure",
        reason_detail="RuntimeError: boom", render_path="none", output_format="concept_preview",
    )
    r._PREMIUM_OVERLAY_OUTCOME.set(out)
    got = r.consume_premium_overlay_outcome()
    assert got is out
    # consume resets so a later non-premium render is not mislabeled
    assert r.consume_premium_overlay_outcome() is None
    # alert policy: only failed_unexpected pages
    assert r.premium_outcome_should_alert(out) is True
    assert r.premium_outcome_should_alert(
        r.PremiumOverlayOutcome("premium_overlay_delivered", "none", "", "subprocess", "concept_preview")
    ) is False
    assert r.premium_outcome_should_alert(
        r.PremiumOverlayOutcome("premium_overlay_degraded_to_flat", "fit", "", "none", "concept_preview")
    ) is False
    assert r.premium_outcome_should_alert(None) is False
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: module ... has no attribute 'PremiumOverlayOutcome'`)

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k premium_overlay_outcome_contextvar -v`
Expected: FAIL.

- [ ] **Step 3: Implement** in `src/agents/flyer/render.py`, immediately after the `_FORCE_BACKGROUND_ONLY` ContextVar (after line 148). First confirm `import sys` exists near the top of the file; if absent, add it with the other stdlib imports.

```python
@dataclass
class PremiumOverlayOutcome:
    """How a single premium-enabled render resolved (set on a ContextVar, read
    by the generate-flyer-concepts chokepoint). status/reason_class map 1:1 to
    the FlyerPremiumOverlayOutcome audit variant."""
    status: str          # premium_overlay_delivered | premium_overlay_degraded_to_flat | premium_overlay_failed_unexpected
    reason_class: str    # none|fit|coverage|overflow|missing_pil|import_error|subprocess_failure|runtime_exception|serialization_error
    reason_detail: str
    render_path: str     # in_process | subprocess | none
    output_format: str


_PREMIUM_OVERLAY_OUTCOME: contextvars.ContextVar[PremiumOverlayOutcome | None] = contextvars.ContextVar(
    "flyer_premium_overlay_outcome", default=None
)


def consume_premium_overlay_outcome() -> PremiumOverlayOutcome | None:
    """Return the most-recent premium render outcome and clear it, so a later
    render that does NOT run the premium overlay cannot inherit a stale value."""
    outcome = _PREMIUM_OVERLAY_OUTCOME.get()
    _PREMIUM_OVERLAY_OUTCOME.set(None)
    return outcome


def premium_outcome_should_alert(outcome: PremiumOverlayOutcome | None) -> bool:
    """Page the operator only for unrecovered, unexpected premium failures.
    Intentional fail-closed (fit/coverage/overflow) is normal product behavior."""
    return bool(outcome) and outcome.status == "premium_overlay_failed_unexpected"
```

- [ ] **Step 4: Run — expect PASS**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k premium_overlay_outcome_contextvar -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/flyer/render.py tests/test_flyer_renderer.py
git commit -m "feat(flyer): premium overlay outcome dataclass + contextvar accessors"
```

---

## Task 3: `PREMIUM_OVERLAY_RENDERER` subprocess string + fail-closed classifier (render.py)

**Files:**
- Modify: `src/agents/flyer/render.py` (add near the `OVERLAY_RENDERER` constant at line 2662)
- Test: `tests/test_flyer_renderer.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_flyer_renderer.py`)

```python
def test_premium_overlay_renderer_string_and_classifier():
    from agents.flyer import render as r
    src = r.PREMIUM_OVERLAY_RENDERER
    # reconstructs the project and calls the real premium renderer
    assert "model_validate_json" in src
    assert "render_premium_overlay" in src
    # honors caller-provided import roots (works in box + repo layouts)
    assert "sys_path" in src
    # fail-closed -> exit 3 ; unexpected -> exit 1
    assert "sys.exit(3" in src
    assert "sys.exit(1" in src
    # fail-closed reason classifier
    assert r._classify_fail_closed_reason("required fact missing: schedule") == "coverage"
    assert r._classify_fail_closed_reason("offer seal overflow") == "overflow"
    assert r._classify_fail_closed_reason("text cannot fit the panel") == "fit"
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: ... 'PREMIUM_OVERLAY_RENDERER'`)

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k premium_overlay_renderer_string -v`
Expected: FAIL.

- [ ] **Step 3: Implement** in `src/agents/flyer/render.py`, immediately after the `OVERLAY_RENDERER` constant block (the constant starts at line 2662; add this after its closing `'''`):

```python
PREMIUM_OVERLAY_RENDERER = r'''
import json, sys, traceback
from pathlib import Path
spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for _p in reversed(spec.get("sys_path") or []):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)
try:
    from schemas import FlyerProject
    try:
        import flyer_premium_overlay as premium_overlay  # box (flat layout)
    except ImportError:
        from agents.flyer import premium_overlay          # repo / tests
    project = FlyerProject.model_validate_json(spec["project_json"])
except Exception as e:
    sys.stderr.write(f"{type(e).__name__}: {e}")
    sys.exit(1)  # import/serialization error -> unexpected
try:
    premium_overlay.render_premium_overlay(
        project, Path(spec["source"]), Path(spec["target"]),
        size=tuple(spec["size"]), output_format=spec["output_format"],
    )
except Exception as e:
    # FlyerRenderError = intentional fit/coverage fail-closed -> exit 3 ;
    # anything else = unexpected renderer crash -> exit 1.
    sys.stderr.write(f"{type(e).__name__}: {e}")
    sys.exit(3 if type(e).__name__ == "FlyerRenderError" else 1)
sys.exit(0)
'''


def _classify_fail_closed_reason(message: str) -> str:
    """Best-effort reason_class for an intentional FlyerRenderError fail-closed.
    Telemetry only — the alert decision is by status, never by reason_class."""
    low = (message or "").lower()
    if "overflow" in low:
        return "overflow"
    if any(k in low for k in ("cover", "required fact", "missing", "not present")):
        return "coverage"
    return "fit"
```

- [ ] **Step 4: Run — expect PASS**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k premium_overlay_renderer_string -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/flyer/render.py tests/test_flyer_renderer.py
git commit -m "feat(flyer): premium overlay subprocess renderer string + fail-closed classifier"
```

---

## Task 4: Premium-with-fallback render + rewire `_apply_critical_text_overlay` (render.py)

**Files:**
- Modify: `src/agents/flyer/render.py` (`_apply_critical_text_overlay` lines 2898–2950; add a helper above it)
- Test: `tests/test_flyer_renderer.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_flyer_renderer.py`). These mock the premium renderer + `subprocess.run` (mirroring the existing `monkeypatch.setattr(render_module.subprocess, "run", ...)` pattern). They reuse whatever minimal `FlyerProject` fixture builder the file already uses for food projects (e.g. `_f0174_integrated_project()` if present); if no such helper exists, build a minimal food `FlyerProject` inline the same way other tests in this file do.

```python
class _FakeProc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode; self.stdout = stdout; self.stderr = stderr


def _premium_food_project():
    # Reuse the file's existing minimal food-project builder if present:
    if "_f0174_integrated_project" in globals():
        return _f0174_integrated_project()
    return FlyerProject(  # minimal valid food project
        project_id="F0179", version=1, customer_id="CUST0001",
        status="rendering", raw_request="weekend specials",
        fields=FlyerRequestFields(business_type="restaurant"),
    )


def _enable_premium(monkeypatch):
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)  # global ON for the test
    from agents.flyer import render as r
    monkeypatch.setattr(r, "_is_food_or_grocery_project", lambda p: True)


def test_apply_premium_in_process_success(monkeypatch, tmp_path):
    from agents.flyer import render as r
    from agents.flyer import premium_overlay as po
    _enable_premium(monkeypatch)
    monkeypatch.setattr(po, "render_premium_overlay", lambda *a, **k: None)
    called = {"flat": False, "sub": False}
    monkeypatch.setattr(r, "apply_critical_text_overlay", lambda *a, **k: called.__setitem__("flat", True))
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: called.__setitem__("sub", True) or _FakeProc(0))
    r._apply_critical_text_overlay(_premium_food_project(), tmp_path / "s.png", tmp_path / "t.png", size=(1080, 1350), output_format="concept_preview")
    out = r.consume_premium_overlay_outcome()
    assert out.status == "premium_overlay_delivered" and out.render_path == "in_process"
    assert called["flat"] is False and called["sub"] is False


def test_apply_premium_in_process_failclosed_degrades_flat(monkeypatch, tmp_path):
    from agents.flyer import render as r
    from agents.flyer import premium_overlay as po
    _enable_premium(monkeypatch)
    def _raise(*a, **k):
        raise r.FlyerRenderError("required fact missing: schedule")
    monkeypatch.setattr(po, "render_premium_overlay", _raise)
    flat = {"called": False}
    monkeypatch.setattr(r, "apply_critical_text_overlay", lambda *a, **k: flat.__setitem__("called", True))
    r._apply_critical_text_overlay(_premium_food_project(), tmp_path / "s.png", tmp_path / "t.png", size=(1080, 1350), output_format="concept_preview")
    out = r.consume_premium_overlay_outcome()
    assert out.status == "premium_overlay_degraded_to_flat" and out.reason_class == "coverage"
    assert flat["called"] is True


def test_apply_premium_subprocess_recovers_on_import_error(monkeypatch, tmp_path):
    from agents.flyer import render as r
    from agents.flyer import premium_overlay as po
    _enable_premium(monkeypatch)
    def _no_pil(*a, **k):
        raise ModuleNotFoundError("No module named 'PIL'")
    monkeypatch.setattr(po, "render_premium_overlay", _no_pil)
    monkeypatch.setattr(r.Path, "exists", lambda self: True)  # /usr/bin/python3 present
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: _FakeProc(0))
    flat = {"called": False}
    monkeypatch.setattr(r, "apply_critical_text_overlay", lambda *a, **k: flat.__setitem__("called", True))
    r._apply_critical_text_overlay(_premium_food_project(), tmp_path / "s.png", tmp_path / "t.png", size=(1080, 1350), output_format="concept_preview")
    out = r.consume_premium_overlay_outcome()
    assert out.status == "premium_overlay_delivered" and out.render_path == "subprocess"
    assert "PIL" in out.reason_detail            # in-process error preserved as telemetry
    assert flat["called"] is False               # delivered -> no flat


def test_apply_premium_subprocess_failclosed_exit3(monkeypatch, tmp_path):
    from agents.flyer import render as r
    from agents.flyer import premium_overlay as po
    _enable_premium(monkeypatch)
    monkeypatch.setattr(po, "render_premium_overlay", lambda *a, **k: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'PIL'")))
    monkeypatch.setattr(r.Path, "exists", lambda self: True)
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: _FakeProc(3, stderr="FlyerRenderError: text cannot fit"))
    monkeypatch.setattr(r, "apply_critical_text_overlay", lambda *a, **k: None)
    r._apply_critical_text_overlay(_premium_food_project(), tmp_path / "s.png", tmp_path / "t.png", size=(1080, 1350), output_format="concept_preview")
    out = r.consume_premium_overlay_outcome()
    assert out.status == "premium_overlay_degraded_to_flat" and out.reason_class == "fit"


def test_apply_premium_subprocess_unexpected_exit1(monkeypatch, tmp_path):
    from agents.flyer import render as r
    from agents.flyer import premium_overlay as po
    _enable_premium(monkeypatch)
    monkeypatch.setattr(po, "render_premium_overlay", lambda *a, **k: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'PIL'")))
    monkeypatch.setattr(r.Path, "exists", lambda self: True)
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: _FakeProc(1, stderr="RuntimeError: boom"))
    monkeypatch.setattr(r, "apply_critical_text_overlay", lambda *a, **k: None)
    r._apply_critical_text_overlay(_premium_food_project(), tmp_path / "s.png", tmp_path / "t.png", size=(1080, 1350), output_format="concept_preview")
    out = r.consume_premium_overlay_outcome()
    assert out.status == "premium_overlay_failed_unexpected" and out.reason_class == "subprocess_failure"


def test_apply_premium_serialization_error(monkeypatch, tmp_path):
    from agents.flyer import render as r
    from agents.flyer import premium_overlay as po
    _enable_premium(monkeypatch)
    monkeypatch.setattr(po, "render_premium_overlay", lambda *a, **k: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'PIL'")))
    monkeypatch.setattr(r.Path, "exists", lambda self: True)
    proj = _premium_food_project()
    monkeypatch.setattr(type(proj), "model_dump_json", lambda self, *a, **k: (_ for _ in ()).throw(ValueError("nope")))
    monkeypatch.setattr(r, "apply_critical_text_overlay", lambda *a, **k: None)
    r._apply_critical_text_overlay(proj, tmp_path / "s.png", tmp_path / "t.png", size=(1080, 1350), output_format="concept_preview")
    out = r.consume_premium_overlay_outcome()
    assert out.status == "premium_overlay_failed_unexpected" and out.reason_class == "serialization_error"


def test_apply_flag_off_byte_identical(monkeypatch, tmp_path):
    from agents.flyer import render as r
    from agents.flyer import premium_overlay as po
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY", raising=False)
    premium_called = {"v": False}
    monkeypatch.setattr(po, "render_premium_overlay", lambda *a, **k: premium_called.__setitem__("v", True))
    flat = {"called": False}
    monkeypatch.setattr(r, "apply_critical_text_overlay", lambda *a, **k: flat.__setitem__("called", True))
    r._apply_critical_text_overlay(_premium_food_project(), tmp_path / "s.png", tmp_path / "t.png", size=(1080, 1350), output_format="concept_preview")
    assert premium_called["v"] is False        # premium block skipped entirely
    assert flat["called"] is True
    assert r.consume_premium_overlay_outcome() is None   # nothing recorded
```

- [ ] **Step 2: Run — expect FAIL** (new branch behavior + helper not present)

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k "apply_premium or apply_flag_off" -v`
Expected: FAIL.

- [ ] **Step 3: Implement.** In `src/agents/flyer/render.py`, add the helper directly ABOVE `_apply_critical_text_overlay` (before line 2898):

```python
def _render_premium_overlay_with_fallback(project: FlyerProject, source: Path | str, target: Path | str, *, size: tuple[int, int], output_format: str) -> PremiumOverlayOutcome:
    """Render the premium overlay in-process; on any import/runtime failure
    (the PIL-less gateway venv) re-render in a /usr/bin/python3 subprocess that
    HAS Pillow. Returns a PremiumOverlayOutcome; never raises (the caller owns
    the flat fallback)."""
    # 1) In-process attempt (the path tests + system-python contexts use).
    try:
        try:
            import flyer_premium_overlay as premium_overlay  # box (flat layout)
        except ImportError:
            from agents.flyer import premium_overlay          # repo / tests
        premium_overlay.render_premium_overlay(project, source, target, size=size, output_format=output_format)
        return PremiumOverlayOutcome("premium_overlay_delivered", "none", "", "in_process", output_format)
    except FlyerRenderError as e:
        msg = str(e)
        return PremiumOverlayOutcome("premium_overlay_degraded_to_flat", _classify_fail_closed_reason(msg), msg[:300], "none", output_format)
    except Exception as e:
        in_process_detail = f"{type(e).__name__}: {e}"  # expected on the box: ModuleNotFoundError: No module named 'PIL'

    # 2) Subprocess recovery via /usr/bin/python3 (PIL-capable), mirrors the flat OVERLAY_RENDERER path.
    if not Path("/usr/bin/python3").exists():
        return PremiumOverlayOutcome("premium_overlay_failed_unexpected", "missing_pil", in_process_detail[:300], "none", output_format)
    try:
        project_json = project.model_dump_json()
    except Exception as e:
        return PremiumOverlayOutcome("premium_overlay_failed_unexpected", "serialization_error", f"{type(e).__name__}: {e}"[:300], "none", output_format)
    spec = {
        "project_json": project_json,
        "source": str(source),
        "target": str(target),
        "size": list(size),
        "output_format": output_format,
        "sys_path": [p for p in sys.path if p],
    }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as fh:
        json.dump(spec, fh)
        spec_path = fh.name
    try:
        proc = subprocess.run(["/usr/bin/python3", "-c", PREMIUM_OVERLAY_RENDERER, spec_path], capture_output=True, text=True, timeout=60)
    except Exception as e:
        return PremiumOverlayOutcome("premium_overlay_failed_unexpected", "subprocess_failure", f"{type(e).__name__}: {e}"[:300], "none", output_format)
    finally:
        Path(spec_path).unlink(missing_ok=True)
    if proc.returncode == 0:
        # delivered via the escape hatch; keep the in-process error as telemetry (no alert).
        return PremiumOverlayOutcome("premium_overlay_delivered", "none", in_process_detail[:300], "subprocess", output_format)
    detail = (proc.stderr or proc.stdout or "").strip()
    if proc.returncode == 3:
        return PremiumOverlayOutcome("premium_overlay_degraded_to_flat", _classify_fail_closed_reason(detail), detail[:300], "none", output_format)
    return PremiumOverlayOutcome("premium_overlay_failed_unexpected", "subprocess_failure", detail[:300], "none", output_format)
```

Then REPLACE the premium block at the top of `_apply_critical_text_overlay` (lines 2899–2926, i.e. the `if _premium_overlay_enabled(...)` try/except that currently calls `render_premium_overlay` in-process and logs on failure) with:

```python
    if _premium_overlay_enabled(project) and _is_food_or_grocery_project(project):
        outcome = _render_premium_overlay_with_fallback(project, source, target, size=size, output_format=output_format)
        _PREMIUM_OVERLAY_OUTCOME.set(outcome)
        if outcome.status == "premium_overlay_delivered":
            return
        if outcome.status == "premium_overlay_failed_unexpected":
            logging.getLogger(__name__).error(
                "premium overlay failed unexpectedly (%s); degrading to flat overlay: %s",
                outcome.reason_class, outcome.reason_detail,
            )
        # delivered -> returned above; degraded_to_flat / failed_unexpected -> fall through to the flat path below.
```

Leave the rest of `_apply_critical_text_overlay` (the flat `apply_critical_text_overlay` call, the `"Pillow is required"` branch, and the `/usr/bin/python3 -c OVERLAY_RENDERER` flat subprocess at lines 2927–2950) UNCHANGED.

- [ ] **Step 4: Run — expect PASS** (the new tests, then the whole renderer file for no regression)

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k "apply_premium or apply_flag_off" -v`
Expected: PASS.
Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -q`
Expected: all pass (no regression — in-process path keeps existing behavior where PIL is present).

- [ ] **Step 5: Commit**

```bash
git add src/agents/flyer/render.py tests/test_flyer_renderer.py
git commit -m "feat(flyer): premium overlay subprocess fallback + outcome recording in _apply_critical_text_overlay"
```

---

## Task 5: Chokepoint — emit audit event + conditional alert (generate-flyer-concepts)

**Files:**
- Modify: `src/agents/flyer/scripts/generate-flyer-concepts` (import block 19–44; render imports line 63; helpers near `_audit_append` line 428; call sites after lines 884 and 926)
- Test: `tests/test_flyer_premium_outcome_script.py` (new)

- [ ] **Step 1: Write the failing test** (create `tests/test_flyer_premium_outcome_script.py`). It loads the script as a module via `importlib` (the script guards its CLI under `if __name__ == "__main__":`, so import only defines functions), then drives the helper with mocked dependencies.

```python
"""Chokepoint glue: premium overlay outcome -> audit event + conditional alert."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "platform"))
sys.path.insert(0, str(ROOT / "src" / "agents" / "flyer"))  # flat-name imports inside the script


def _load_script():
    path = ROOT / "src" / "agents" / "flyer" / "scripts" / "generate-flyer-concepts"
    spec = importlib.util.spec_from_loader("genflyer_mod", loader=None, origin=str(path))
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(path)
    code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
    exec(code, mod.__dict__)
    return mod


def _outcome(status, reason_class="none", render_path="subprocess"):
    return SimpleNamespace(status=status, reason_class=reason_class, reason_detail="d",
                           render_path=render_path, output_format="concept_preview")


def _project():
    return SimpleNamespace(project_id="F0179", version=2)


def test_emit_delivered_records_event_no_alert(monkeypatch, tmp_path):
    mod = _load_script()
    rows = []
    alerts = []
    monkeypatch.setattr(mod, "consume_premium_overlay_outcome", lambda: _outcome("premium_overlay_delivered"))
    monkeypatch.setattr(mod, "premium_outcome_should_alert", lambda o: False)
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    monkeypatch.setattr(mod, "_alert_owner", lambda msg: alerts.append(msg))
    mod._emit_premium_overlay_outcome(tmp_path / "decisions.log", _project())
    assert len(rows) == 1
    assert rows[0].type == "flyer_premium_overlay_outcome"
    assert rows[0].status == "premium_overlay_delivered"
    assert rows[0].render_path == "subprocess"
    assert alerts == []


def test_emit_failed_unexpected_records_and_alerts(monkeypatch, tmp_path):
    mod = _load_script()
    rows, alerts = [], []
    monkeypatch.setattr(mod, "consume_premium_overlay_outcome", lambda: _outcome("premium_overlay_failed_unexpected", "subprocess_failure", "none"))
    monkeypatch.setattr(mod, "premium_outcome_should_alert", lambda o: True)
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    monkeypatch.setattr(mod, "_alert_owner", lambda msg: alerts.append(msg))
    mod._emit_premium_overlay_outcome(tmp_path / "decisions.log", _project())
    assert rows[0].status == "premium_overlay_failed_unexpected"
    assert len(alerts) == 1 and "F0179" in alerts[0]


def test_emit_degraded_records_no_alert(monkeypatch, tmp_path):
    mod = _load_script()
    rows, alerts = [], []
    monkeypatch.setattr(mod, "consume_premium_overlay_outcome", lambda: _outcome("premium_overlay_degraded_to_flat", "fit", "none"))
    monkeypatch.setattr(mod, "premium_outcome_should_alert", lambda o: False)
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    monkeypatch.setattr(mod, "_alert_owner", lambda msg: alerts.append(msg))
    mod._emit_premium_overlay_outcome(tmp_path / "decisions.log", _project())
    assert rows[0].status == "premium_overlay_degraded_to_flat"
    assert alerts == []


def test_emit_none_does_nothing(monkeypatch, tmp_path):
    mod = _load_script()
    rows, alerts = [], []
    monkeypatch.setattr(mod, "consume_premium_overlay_outcome", lambda: None)
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    monkeypatch.setattr(mod, "_alert_owner", lambda msg: alerts.append(msg))
    mod._emit_premium_overlay_outcome(tmp_path / "decisions.log", _project())
    assert rows == [] and alerts == []
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: ... '_emit_premium_overlay_outcome'`)

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_premium_outcome_script.py -v`
Expected: FAIL.

- [ ] **Step 3a: Add the schema import** in `src/agents/flyer/scripts/generate-flyer-concepts` — inside the `from schemas import (` block (lines 19–44), add a line:

```python
    FlyerPremiumOverlayOutcome,
```

- [ ] **Step 3b: Add the render-side imports** to the render import line (line 63 — the `from flyer_render import ...` list). Append:

```python
    consume_premium_overlay_outcome, premium_outcome_should_alert,
```

(Insert these names into the existing comma-separated import list; keep the `# noqa` at the end.)

- [ ] **Step 3c: Add the helpers** immediately after `_audit_append` (after line 436). Confirm `subprocess` is imported at the top of the script; if not, add `import subprocess` with the other stdlib imports.

```python
def _alert_owner(message: str) -> None:
    """Best-effort plain-text operator alert (Telegram-primary chokepoint).
    Never blocks rendering/delivery; plain text avoids MarkdownV1 underscore
    mangling of status tokens."""
    try:
        subprocess.run(
            ["/usr/local/bin/shift-agent-notify-owner", "--priority", "1", message],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return


def _emit_premium_overlay_outcome(audit_log_path: Path, project) -> None:
    """Consume the premium overlay outcome recorded during the last render and
    make it explicit: one decisions.log row always; an operator alert ONLY for
    unrecovered unexpected failures. No-op when premium did not run."""
    outcome = consume_premium_overlay_outcome()
    if outcome is None:
        return
    _audit_append(audit_log_path, FlyerPremiumOverlayOutcome(
        ts=datetime.now(timezone.utc),
        project_id=project.project_id,
        project_version=project.version,
        status=outcome.status,
        reason_class=outcome.reason_class,
        reason_detail=outcome.reason_detail,
        render_path=outcome.render_path,
        output_format=outcome.output_format,
    ))
    if premium_outcome_should_alert(outcome):
        _alert_owner(
            (f"Flyer premium overlay FAILED unexpectedly ({project.project_id}): "
             f"{outcome.reason_class} - premium shipped FLAT. Detail: {outcome.reason_detail}")[:900]
        )
```

- [ ] **Step 3d: Call the helper after each render.** Immediately after the primary `render_concept_previews(...)` call that ends at line 884, add:

```python
                    _emit_premium_overlay_outcome(audit_log_path, project)
```

And immediately after the recovery `render_concept_previews(...)` call that ends at line 926 (inside the same `try`, after the assignment to `specs`, before/around the existing `FlyerIntegratedFellBackDeterministic` emit), add:

```python
                            _emit_premium_overlay_outcome(audit_log_path, project)
```

(Match the surrounding indentation exactly. The helper is a no-op when premium did not run, so calling it after both render sites is safe and DRY.)

- [ ] **Step 4: Run — expect PASS**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_premium_outcome_script.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/flyer/scripts/generate-flyer-concepts tests/test_flyer_premium_outcome_script.py
git commit -m "feat(flyer): emit flyer_premium_overlay_outcome + alert on unexpected failure"
```

---

## Task 6: Deploy smoke gate — render premium under the gateway venv path

**Files:**
- Modify: `src/agents/shift/scripts/shift-agent-smoke-test.sh` (append after the existing premium-overlay gate, which ends ~line 208)

- [ ] **Step 1: Add the smoke gate.** After the existing `✓ Fix C premium overlay imports flat + fonts present + load under $RENDER_PY` block (after line 208), add a check that renders premium under `$PY` (the PIL-less venv = the gateway interpreter class) and asserts a `delivered` outcome. This is the gate that would have caught the silent flat-degrade.

```bash
# 2.0b Fix C premium overlay — RENDER gate under the gateway venv interpreter.
# The gateway runs the flyer pipeline under a venv WITHOUT Pillow. After the
# flat-degrade fix, the premium overlay must still RENDER (via the /usr/bin/python3
# subprocess escape hatch) and report `premium_overlay_delivered` — NOT silently
# fall back to flat. This builds a tiny textless background with the PIL-capable
# render python, then drives _apply_critical_text_overlay under $PY and asserts
# the recorded outcome is `delivered`.
if "$RENDER_PY" -c "import PIL" 2>/dev/null; then
    SMOKE_DIR="$(mktemp -d)"
    BG="$SMOKE_DIR/bg.png"; OUT="$SMOKE_DIR/out.png"
    "$RENDER_PY" -c "
from PIL import Image
Image.new('RGB', (1080, 1350), (20, 18, 16)).save('$BG')
" > /dev/null 2>&1
    if ! FLYER_PREMIUM_OVERLAY=1 "$PY" -c "
import sys
sys.path.insert(0, '/opt/shift-agent')
import flyer_render as r
from schemas import FlyerProject, FlyerRequestFields
proj = FlyerProject(project_id='S0001', version=1, customer_id='CUST0001',
                    status='rendering', raw_request='weekend specials any item \$7.99',
                    fields=FlyerRequestFields(business_type='restaurant'))
r._is_food_or_grocery_project = lambda p: True   # smoke: force food path
r._apply_critical_text_overlay(proj, '$BG', '$OUT', size=(1080, 1350), output_format='concept_preview')
out = r.consume_premium_overlay_outcome()
assert out is not None, 'no premium outcome recorded (premium path did not run)'
assert out.status == 'premium_overlay_delivered', f'premium did NOT render under gateway venv: {out.status} ({out.reason_class}: {out.reason_detail})'
import os
assert os.path.getsize('$OUT') > 0, 'premium render produced an empty file'
print('premium renders premium under', sys.executable, 'via', out.render_path)
" > /dev/null; then
        echo "FAIL: premium overlay does NOT render premium under the gateway venv ($PY) — would silently ship FLAT"
        rm -rf "$SMOKE_DIR"
        exit 1
    fi
    rm -rf "$SMOKE_DIR"
    echo "✓ premium overlay renders premium under gateway venv path ($PY via subprocess)"
else
    echo "⚠  Pillow absent under $RENDER_PY — premium RENDER gate skipped (subprocess escape hatch unverifiable here)"
fi
```

(If the minimal `FlyerProject(...)` construction above fails the model's required fields on the box, adjust to include whatever fields the deployed `FlyerProject` requires — match the fields used by the renderer's own test fixtures. The contract being asserted is unchanged: `consume_premium_overlay_outcome().status == 'premium_overlay_delivered'`.)

- [ ] **Step 2: Verify the script parses.**

Run: `bash -n src/agents/shift/scripts/shift-agent-smoke-test.sh`
Expected: no output (syntax OK).

- [ ] **Step 3: Commit**

```bash
git add src/agents/shift/scripts/shift-agent-smoke-test.sh
git commit -m "feat(flyer): smoke gate asserts premium renders under gateway venv path"
```

---

## Task 7: Full suite + Codex review

- [ ] **Step 1: Full build suite**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/ -q`
Expected: 0 failed (additive variant + additive tests; flag-off byte-identical; in-process path unchanged).

- [ ] **Step 2: Codex review** the branch diff. Focus: (a) premium subprocess escape hatch mirrors the flat `OVERLAY_RENDERER` pattern and serializes the project losslessly; (b) outcome taxonomy + alert policy match the design (alert ONLY on `premium_overlay_failed_unexpected`; never on fit/coverage/overflow; missing-PIL-recovered = `delivered`, no alert); (c) fail-closed preserved (coverage/fit still degrade to flat); (d) flag-off byte-identical; (e) `FlyerPremiumOverlayOutcome` additive, auto-registered via `Tag`; (f) audit + alert are best-effort and never block delivery; (g) smoke gate asserts `delivered` under the venv interpreter.

- [ ] **Step 3: Fix any BLOCKER/MAJOR; re-review to CLEAN.**

---

## Self-Review (writing-plans)

**Spec coverage:**
- Render fix (subprocess escape hatch) → Task 3 (renderer string) + Task 4 (`_render_premium_overlay_with_fallback`, in-process→subprocess). ✓
- Outcome ContextVar → Task 2. ✓
- `flyer_premium_overlay_outcome` decisions.log event → Task 1 (schema) + Task 5 (emit). ✓
- Alert only on unrecovered unexpected failure → Task 2 (`premium_outcome_should_alert`) + Task 5 (`_emit_premium_overlay_outcome` + `_alert_owner`); tests `test_emit_failed_unexpected_records_and_alerts`, `test_emit_degraded_records_no_alert`. ✓
- No alert for fit/coverage fail-closed → Task 2/4 (`degraded_to_flat`) + Task 5 test. ✓
- Smoke gate rendering premium under gateway venv path → Task 6. ✓
- Tests: in-process success (`test_apply_premium_in_process_success`), subprocess success (`test_apply_premium_subprocess_recovers_on_import_error`), expected degrade (`test_apply_premium_in_process_failclosed_degrades_flat`, `test_apply_premium_subprocess_failclosed_exit3`), unexpected failure (`test_apply_premium_subprocess_unexpected_exit1`, serialization), flag-off byte-identical (`test_apply_flag_off_byte_identical`). ✓

**Placeholder scan:** all steps contain concrete code + exact commands. The two parenthetical "adjust if the deployed model requires more fields" notes (Task 4 fixture, Task 6 smoke) are fallback guidance, not placeholders — the primary code is concrete and the asserted contract is fixed. ✓

**Type consistency:** `PremiumOverlayOutcome(status, reason_class, reason_detail, render_path, output_format)` is constructed positionally in render.py and consumed by attribute in the script; `FlyerPremiumOverlayOutcome` field names (`status`, `reason_class`, `reason_detail`, `render_path`, `output_format`, `project_id`, `project_version`) match between schema (Task 1), emit (Task 5), and tests. `consume_premium_overlay_outcome` / `premium_outcome_should_alert` names consistent across Tasks 2, 5. ✓

## Out of scope (deferred)
Premium visual design, W1 background, recovery-rung routing, referee/QA matching, flag/allowlist changes, finals re-overlay event (preview event is the representative signal), multi-concept outcome keying (concept_count=1 on the scoped recovery path), alert dedup/rate-limit, broadening beyond `+17329837841`, combo near-duplicate quirk, Slice 2 cleanup.

## Post-build (operator-gated)
PR → CI → Codex → merge → deploy (scoped flags already on for `+17329837841`). The deploy runs the new smoke gate; if premium cannot render under the venv path, the deploy auto-rolls-back. Then operator re-sends a live brief → expect `flyer_premium_overlay_outcome` `status=premium_overlay_delivered render_path=subprocess` in `decisions.log` and a premium (editorial) flyer delivered. Rollback = `FLYER_PREMIUM_OVERLAY` off + restart → flat.
