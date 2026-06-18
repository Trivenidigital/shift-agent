# Flyer Deterministic-Recovery Routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an integrated gemini render fails visual_qa with *only recoverable text-fidelity defects* (and the facts are locked), re-render deterministically (textless background + Fix C overlay — correct text by construction) and re-verify, before the existing hard-stop→manual ladder; ship premium/flat on QA pass, manual only when deterministic recovery also fails or the defect is genuinely dangerous.

**Architecture:** A new flag-gated recovery rung in `generate-flyer-concepts`, placed after the (OFF) Slice 2 premium-repair rung and before the legacy autorepair ladder. It reuses the existing `_qa_failed_exact_text_recoverable` partition (extended so an *own-brand spelling variant* is recoverable while a *different business* stays dangerous, via `visual_qa._is_brand_typo`) and forces the existing mode-2 render path (`force_background_only`) so an integrated-eligible project gets the deterministic Fix C overlay.

**Tech Stack:** Python 3, Pydantic v2 (`src/platform/schemas.py`), Pillow (deterministic overlay), pytest. Repo layout: `src/agents/flyer/`, tests under `tests/`. Authoritative source = `origin/main` (= deployed box `1166d16`).

**Design doc:** `docs/superpowers/specs/2026-06-18-flyer-deterministic-recovery-routing-design.md`
**Drift-check tag:** `extends-Hermes`.

**Conventions for every test command (Windows git-bash):**
`PYTHONPATH="src;src/platform" python -m pytest <path> -v`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/platform/schemas.py` | `FlyerIntegratedFellBackDeterministic.reason` Literal | add `"qa_text_fidelity"` value |
| `src/agents/flyer/visual_qa.py` | brand-typo discriminator | add public `is_own_brand_variant()` + `brand_blocker_name()` (wrap existing `_is_brand_typo` / `_project_business_name`) |
| `src/agents/flyer/render.py` | render-mode dispatch + scope gate | add `_deterministic_recovery_enabled()`; thread `force_background_only` through `render_concept_previews` → `_render_model` → `build_image_generation_prompt` |
| `src/agents/flyer/scripts/generate-flyer-concepts` | recovery orchestration | extend `_qa_failed_exact_text_recoverable(project=...)`; add the deterministic-recovery rung |
| `src/agents/shift/scripts/shift-agent-smoke-test.sh` | deploy smoke | add a one-line import/flag-default assertion |
| `tests/test_flyer_visual_qa.py` | brand-variant unit tests | new tests |
| `tests/test_flyer_generate_concepts.py` | partition + rung tests | new tests |
| `tests/test_flyer_renderer.py` | force-background-only unit tests | new tests |

---

## Task 1: Schema — add the `qa_text_fidelity` audit reason

**Files:**
- Modify: `src/platform/schemas.py:4492`
- Test: `tests/test_flyer_generate_concepts.py`

- [ ] **Step 1: Write the failing test** (append near the other recoverable tests, ~line 2965)

```python
def test_integrated_fell_back_deterministic_accepts_qa_text_fidelity_reason():
    from schemas import FlyerIntegratedFellBackDeterministic
    from datetime import datetime, timezone
    entry = FlyerIntegratedFellBackDeterministic(
        ts=datetime(2026, 6, 18, tzinfo=timezone.utc),
        project_id="F0174",
        project_version=1,
        reason="qa_text_fidelity",
    )
    assert entry.reason == "qa_text_fidelity"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_generate_concepts.py::test_integrated_fell_back_deterministic_accepts_qa_text_fidelity_reason -v`
Expected: FAIL — `ValidationError: Input should be 'retries_exhausted', 'referee_unavailable', 'generation_error' or 'fabrication'`.

- [ ] **Step 3: Add the Literal value** at `src/platform/schemas.py:4492`

```python
    reason: Literal["retries_exhausted", "referee_unavailable", "generation_error", "fabrication", "qa_text_fidelity"]
```

Also update the class docstring (lines ~4480-4486) to add the line:
```
      - qa_text_fidelity     : integrated render had only recoverable text-fidelity
                               defects; re-rendered deterministically and shipped
```

- [ ] **Step 4: Run it to verify it passes**

Run: same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/platform/schemas.py tests/test_flyer_generate_concepts.py
git commit -m "feat(flyer): add qa_text_fidelity reason to FlyerIntegratedFellBackDeterministic"
```

---

## Task 2: visual_qa — public own-brand-variant discriminator

Reuse the existing `_is_brand_typo` (visual_qa.py:1410) + `_project_business_name` (visual_qa.py:1440) + the brand-blocker regex used by `classify_qa_severity` (`^visible wrong business/brand: (?P<name>.+)$`). Expose two public helpers so the script can decide recoverability without reaching into private functions.

**Files:**
- Modify: `src/agents/flyer/visual_qa.py` (add after `_project_business_name`, ~line 1448)
- Test: `tests/test_flyer_visual_qa.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_brand_blocker_name_parses_live_format():
    from agents.flyer.visual_qa import brand_blocker_name
    assert brand_blocker_name("visible wrong business/brand: Laksmi'S Kitchen") == "Laksmi'S Kitchen"
    assert brand_blocker_name("missing required visible fact: business_name") is None


def _project_with_brand(name: str):
    from schemas import FlyerProject, FlyerLockedFact
    from datetime import datetime, timezone
    return FlyerProject(
        project_id="F0174", status="intake_started", customer_phone="+17329837841",
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        original_message_id="m-F0174", raw_request="x",
        locked_facts=[FlyerLockedFact(fact_id="business_name", label="Business", value=name, source="customer_profile")],
    )


def test_is_own_brand_variant_true_for_own_brand_typo():
    from agents.flyer.visual_qa import is_own_brand_variant
    assert is_own_brand_variant("Laksmi'S Kitchen", _project_with_brand("Lakshmi's Kitchen")) is True


def test_is_own_brand_variant_false_for_different_business():
    from agents.flyer.visual_qa import is_own_brand_variant
    assert is_own_brand_variant("Triveni Indian Cafe & Bakery", _project_with_brand("Lakshmi's Kitchen")) is False


def test_is_own_brand_variant_false_when_no_registered_brand():
    from agents.flyer.visual_qa import is_own_brand_variant
    from schemas import FlyerProject
    from datetime import datetime, timezone
    proj = FlyerProject(
        project_id="F0174", status="intake_started", customer_phone="+17329837841",
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        original_message_id="m", raw_request="x", locked_facts=[],
    )
    assert is_own_brand_variant("Anything", proj) is False
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_visual_qa.py -k "brand_variant or brand_blocker_name" -v`
Expected: FAIL — `ImportError: cannot import name 'is_own_brand_variant'`.

- [ ] **Step 3: Add the public helpers** in `src/agents/flyer/visual_qa.py` after `_project_business_name`

```python
_BRAND_VARIANT_BLOCKER_RE = re.compile(r"^visible wrong business/brand: (?P<name>.+)$")


def brand_blocker_name(blocker: str) -> str | None:
    """Return the rendered brand text from a 'visible wrong business/brand: X'
    blocker, or None if the blocker is not a brand-variant blocker. Mirrors the
    regex used by classify_qa_severity's brand_variant spec."""
    m = _BRAND_VARIANT_BLOCKER_RE.match(blocker or "")
    return m.group("name") if m else None


def is_own_brand_variant(extracted_name: str, project: FlyerProject) -> bool:
    """True when extracted_name is an own-brand spelling variant of the project's
    registered business_name (a recoverable text-fidelity defect — the
    deterministic overlay redraws the registered name). False when it is a
    structurally different business (hard-block) or no brand is known. Wraps the
    brand-typo gate used by classify_qa_severity (operator decision 2026-05-28)."""
    return _is_brand_typo(extracted_name, _project_business_name(project))
```

(If `re` is not already imported at module top, it is — `_is_brand_typo`/the WARN patterns use it.)

- [ ] **Step 4: Run them to verify they pass**

Run: same as Step 2. Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agents/flyer/visual_qa.py tests/test_flyer_visual_qa.py
git commit -m "feat(flyer): public is_own_brand_variant + brand_blocker_name helpers"
```

---

## Task 3: Extend `_qa_failed_exact_text_recoverable` with brand-typo recoverability

The function lives in the `generate-flyer-concepts` script (~line 260). Add a `project=None` keyword; when a `visible wrong business/brand: X` blocker is present, it is recoverable iff `is_own_brand_variant(X, project)` — otherwise dangerous (poisons the set). Existing callers pass no `project` → brand blocker stays dangerous (unchanged behavior).

**Files:**
- Modify: `src/agents/flyer/scripts/generate-flyer-concepts` (`_qa_failed_exact_text_recoverable`, ~line 260; imports ~line 67-69)
- Test: `tests/test_flyer_generate_concepts.py`

- [ ] **Step 1: Write the failing tests** (append near line 2965; reuse the existing `_load_script` + `_qa_report` helpers)

```python
def _project_lakshmi(monkeypatch_module, locked_fact_ids):
    from schemas import FlyerProject, FlyerLockedFact
    from datetime import datetime, timezone
    facts = [FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile")]
    for fid in locked_fact_ids:
        if fid == "business_name":
            continue
        facts.append(FlyerLockedFact(fact_id=fid, label=fid, value="x", source="customer_text"))
    return FlyerProject(
        project_id="F0174", status="intake_started", customer_phone="+17329837841",
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        original_message_id="m-F0174", raw_request="Any item $7.99", locked_facts=facts,
    )


def test_qa_recoverable_true_for_own_brand_typo_with_project(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=["visible wrong business/brand: Laksmi'S Kitchen"])
    proj = _project_lakshmi(module, {"business_name"})
    assert module._qa_failed_exact_text_recoverable([report], project=proj) is True


def test_qa_recoverable_false_for_different_business_with_project(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=["visible wrong business/brand: Triveni Indian Cafe & Bakery"])
    proj = _project_lakshmi(module, {"business_name"})
    assert module._qa_failed_exact_text_recoverable([report], project=proj) is False


def test_qa_recoverable_brand_blocker_dangerous_when_no_project(monkeypatch):
    # Backward-compat: existing callers pass no project → brand blocker is dangerous.
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=["visible wrong business/brand: Laksmi'S Kitchen"])
    assert module._qa_failed_exact_text_recoverable([report]) is False


def test_qa_recoverable_f0174_replay_blocker_set(monkeypatch):
    # Exact live F0174 blockers + locked fact ids (item:0..5:name/price all locked).
    module = _load_script(monkeypatch)
    blockers = [
        "visible wrong business/brand: Laksmi'S Kitchen",
        "missing required visible fact: business_name",
        "item price mismatch: item:1 expected Dosa $7.99",
        "item price mismatch: item:3 expected Uttapam $7.99",
        "item price mismatch: item:4 expected Pongal $7.99",
        "item price mismatch: item:5 expected Sambar $7.99",
    ]
    locked = {"business_name", "contact_phone", "location", "campaign_title",
              "pricing_structure", "schedule"}
    for i in range(6):
        locked.add(f"item:{i}:name")
        locked.add(f"item:{i}:price")
    report = _qa_report(module, blockers=blockers)
    proj = _project_lakshmi(module, locked)
    assert module._qa_failed_exact_text_recoverable([report], locked_fact_ids=locked, project=proj) is True


def test_qa_recoverable_f0174_with_fabrication_added_is_dangerous(monkeypatch):
    module = _load_script(monkeypatch)
    blockers = [
        "visible wrong business/brand: Laksmi'S Kitchen",
        "fabricated price visible: $3.99",
    ]
    proj = _project_lakshmi(module, {"business_name"})
    assert module._qa_failed_exact_text_recoverable([report := _qa_report(module, blockers=blockers)], project=proj) is False
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_generate_concepts.py -k "own_brand or different_business or f0174_replay or brand_blocker_dangerous or f0174_with_fabrication" -v`
Expected: FAIL — `_qa_failed_exact_text_recoverable() got an unexpected keyword argument 'project'`.

- [ ] **Step 3: Implement the extension**

First, extend the import (script line ~67-69) to bring in the helpers:
```python
    from flyer_visual_qa import run_visual_qa, write_visual_qa_report, is_own_brand_variant, brand_blocker_name  # type: ignore  # noqa: E402
```
and the `agents.flyer.visual_qa` fallback line:
```python
    from agents.flyer.visual_qa import run_visual_qa, write_visual_qa_report, is_own_brand_variant, brand_blocker_name  # noqa: E402
```

Then change the signature + add the brand branch inside the per-blocker loop of `_qa_failed_exact_text_recoverable`:

```python
def _qa_failed_exact_text_recoverable(reports, *, locked_fact_ids=None, project=None) -> bool:
    ...
    for blocker in blockers:
        if blocker.startswith(recoverable_prefixes):
            continue
        # own-brand spelling variant (e.g. "Laksmi'S Kitchen" vs registered
        # "Lakshmi's Kitchen") is a text-fidelity defect the deterministic overlay
        # fixes by construction — it redraws the REGISTERED name. A structurally
        # different business stays DANGEROUS (hard-block). Requires `project` to
        # resolve the registered brand; without it, a brand blocker is dangerous.
        brand_name = brand_blocker_name(blocker)
        if brand_name is not None:
            if project is not None and is_own_brand_variant(brand_name, project):
                continue
            return False
        match = _ITEM_PRICE_MISMATCH_RE.match(blocker)
        if match and f"item:{match.group('index')}:price" in locked:
            continue
        return False
    return True
```
(Update the docstring's RECOVERABLE list to mention own-brand spelling variant; keep the DANGEROUS list noting "truly different business".)

- [ ] **Step 4: Run them to verify they pass**

Run: same as Step 2. Expected: PASS (5 tests). Then run the FULL existing recoverable suite to prove no regression:
`PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_generate_concepts.py -k "recoverable or fabrication" -v`
Expected: all PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add src/agents/flyer/scripts/generate-flyer-concepts tests/test_flyer_generate_concepts.py
git commit -m "feat(flyer): own-brand-variant recoverable in _qa_failed_exact_text_recoverable"
```

---

## Task 4: render.py — `force_background_only` (mode-2 forcing) + scope gate

Two additions in `src/agents/flyer/render.py`:
(a) `_deterministic_recovery_enabled(project)` mirroring the existing `_premium_overlay_enabled` (reuse `_premium_overlay_allowlist()`), gated on `FLYER_DETERMINISTIC_RECOVERY`.
(b) `force_background_only` threaded through `render_concept_previews` → `_render_model` → `build_image_generation_prompt`, so an integrated-eligible project renders mode 2 (textless bg + `_apply_critical_text_overlay`).

**Files:**
- Modify: `src/agents/flyer/render.py` (`_premium_overlay_enabled` ~line 3110; `render_concept_previews` ~3906; `_render_model` ~3861; `build_image_generation_prompt`)
- Test: `tests/test_flyer_renderer.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_deterministic_recovery_enabled_respects_flag_and_allowlist(monkeypatch):
    from agents.flyer import render as r
    from schemas import FlyerProject
    from datetime import datetime, timezone
    proj = FlyerProject(
        project_id="F0174", status="intake_started", customer_phone="+17329837841",
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        original_message_id="m", raw_request="x", locked_facts=[],
    )
    monkeypatch.delenv("FLYER_DETERMINISTIC_RECOVERY", raising=False)
    assert r._deterministic_recovery_enabled(proj) is False
    monkeypatch.setenv("FLYER_DETERMINISTIC_RECOVERY", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    assert r._deterministic_recovery_enabled(proj) is True
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+19999999999")
    assert r._deterministic_recovery_enabled(proj) is False


def test_force_background_only_uses_overlay_for_integrated_eligible(monkeypatch, tmp_path):
    from agents.flyer import render as r
    from schemas import FlyerProject, FlyerLockedFact
    from datetime import datetime, timezone
    proj = FlyerProject(
        project_id="F0174", status="intake_started", customer_phone="+17329837841",
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        original_message_id="m", raw_request="Any item $7.99",
        locked_facts=[FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile")],
    )
    monkeypatch.setattr(r, "_integrated_poster_eligible", lambda p: True)  # would be mode 1
    monkeypatch.setattr(r, "_openrouter_image_bytes", lambda *a, **k: b"fakebgbytes")
    monkeypatch.setattr(r, "_write_generated_image", lambda raw, path, *, size: __import__("pathlib").Path(path).write_bytes(raw))
    calls = {"overlay": 0}
    def fake_overlay(project, source, target, *, size, output_format):
        calls["overlay"] += 1
        __import__("pathlib").Path(target).write_bytes(b"overlaid")
    monkeypatch.setattr(r, "_apply_critical_text_overlay", fake_overlay)
    target = tmp_path / "F0174-C1.png"
    r._render_model(proj, target, concept_id="C1", output_format="concept_preview",
                    size=(1080, 1350), model="google/gemini-3.1-flash-image-preview",
                    quality="high", force_background_only=True)
    assert calls["overlay"] == 1  # mode 2 taken, not the mode-1 early return
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k "deterministic_recovery_enabled or force_background_only" -v`
Expected: FAIL — `_deterministic_recovery_enabled` missing / `_render_model() got an unexpected keyword argument 'force_background_only'`.

- [ ] **Step 3a: Add the scope gate** near `_premium_overlay_enabled` (~line 3160)

```python
PREMIUM_DETERMINISTIC_RECOVERY_ENV = "FLYER_DETERMINISTIC_RECOVERY"


def _deterministic_recovery_enabled(project: FlyerProject) -> bool:
    """Routing gate for integrated-fail → deterministic recovery. Flag
    FLYER_DETERMINISTIC_RECOVERY == "1" AND (the shared FLYER_PREMIUM_OVERLAY_ALLOWLIST
    is empty ⇒ global, else project.customer_phone is in it). Independent of
    FLYER_PREMIUM_OVERLAY (which separately controls premium-vs-flat overlay)."""
    if os.environ.get(PREMIUM_DETERMINISTIC_RECOVERY_ENV) != "1":
        return False
    allow = _premium_overlay_allowlist()
    if not allow:
        return True
    return _normalize_sender(getattr(project, "customer_phone", "") or "") in allow
```

- [ ] **Step 3b: Thread `force_background_only`** through the three functions (keyword-only, default `False`):

In `render_concept_previews(... , force_background_only: bool = False)` — pass it into the `_render_model(...)` call(s) it makes.

In `_render_model(project, path, *, ..., force_background_only: bool = False)` change the eligibility branch so the force overrides mode 1:
```python
    if model.strip().lower() in DETERMINISTIC_MODEL_NAMES:
        _render(project, path, concept_id=concept_id, size=size)
        return
    raw = _openrouter_image_bytes(project, concept_id=concept_id, output_format=output_format,
                                  size=size, model=model, quality=quality,
                                  repair_instruction=repair_instruction, scene_direction=scene_direction,
                                  force_background_only=force_background_only)
    raw_path = _raw_background_path(path)
    if _integrated_poster_eligible(project) and not force_background_only:
        _write_generated_image(raw, path, size=size)
        return
    if not _background_only_eligible(project) and not force_background_only:
        # (existing reference/identity-overlay branch unchanged)
        ...
    # background-only (or forced): textless bg + deterministic overlay
    _write_generated_image(raw, raw_path, size=size)
    _apply_critical_text_overlay(project, raw_path, path, size=size, output_format=output_format)
```

In `build_image_generation_prompt(project, *, ..., force_background_only: bool = False)` and `_openrouter_image_bytes(..., force_background_only: bool = False)`: where the prompt branches on `_background_only_eligible(project)` (render.py ~lines 1004, 1940), use `(_background_only_eligible(project) or force_background_only)` so the **textless-background** contract is emitted under force. `_openrouter_image_bytes` passes `force_background_only` into `build_image_generation_prompt`.

> Implementer note: read render.py lines 1004, 1940-1971 and 2908-3000 to apply the `or force_background_only` at each `_background_only_eligible(project)` site that selects the textless-vs-integrated PROMPT. Do not change any site that is purely an output-format/size decision.

- [ ] **Step 4: Run them to verify they pass**

Run: same as Step 2. Expected: PASS (2 tests). Then prove default-False is unchanged:
`PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -v`
Expected: all PASS (no regression).

- [ ] **Step 5: Commit**

```bash
git add src/agents/flyer/render.py tests/test_flyer_renderer.py
git commit -m "feat(flyer): force_background_only mode-2 + _deterministic_recovery_enabled gate"
```

---

## Task 5: The deterministic-recovery rung in `generate-flyer-concepts`

Insert the rung **after** the Slice 2 premium-repair block + its skip-logging block (after ~line 1290 on origin/main) and **before** the legacy autorepair-classify ladder (~line 1315). Uses the enclosing scope: `current`, `asset_dir`, `draft_model`, `draft_provider`, `cfg`, `audit_log_path`, `integrated_path_attempted`, `source_edit_requested`.

**Files:**
- Modify: `src/agents/flyer/scripts/generate-flyer-concepts` (imports ~63-65 add `_deterministic_recovery_enabled`; insert rung ~line 1291)
- Test: `tests/test_flyer_generate_concepts.py`

- [ ] **Step 1: Write the failing tests** (script-level, mirroring `_load_script` + state-file + monkeypatched `render_concept_previews`/`run_visual_qa` + `module.main()`)

```python
def _f0174_state(tmp_path):
    import json
    from datetime import datetime, timezone
    now = datetime(2026, 6, 18, tzinfo=timezone.utc).isoformat()
    facts = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmi's Kitchen", "source": "customer_profile"},
        {"fact_id": "contact_phone", "label": "Contact", "value": "+17329837841", "source": "customer_profile"},
        {"fact_id": "location", "label": "Location", "value": "90 Brybar Dr St Johns FL", "source": "customer_profile"},
        {"fact_id": "campaign_title", "label": "Campaign", "value": "Weekend Specials", "source": "customer_text"},
        {"fact_id": "pricing_structure", "label": "Pricing", "value": "Any item $7.99", "source": "customer_text"},
        {"fact_id": "schedule", "label": "Schedule", "value": "Saturday & Sunday, 4 PM-8 PM", "source": "customer_text"},
    ]
    names = ["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar"]
    for i, nm in enumerate(names):
        facts.append({"fact_id": f"item:{i}:name", "label": f"Item {i}", "value": nm, "source": "customer_text"})
        facts.append({"fact_id": f"item:{i}:price", "label": f"Price {i}", "value": "$7.99", "source": "customer_text"})
    state_path = tmp_path / "projects.json"
    state_path.write_text(json.dumps({"schema_version": 1, "next_sequence": 175, "projects": [{
        "project_id": "F0174", "status": "intake_started", "customer_phone": "+17329837841",
        "created_at": now, "updated_at": now, "original_message_id": "m-F0174",
        "raw_request": "Weekend Specials. Any item $7.99. Idli, Dosa, Vada, Uttapam, Pongal, Sambar.",
        "locked_facts": facts,
    }]}), encoding="utf-8")
    return state_path


_F0174_BLOCKERS = [
    "visible wrong business/brand: Laksmi'S Kitchen",
    "missing required visible fact: business_name",
    "item price mismatch: item:1 expected Dosa $7.99",
]


def test_rung_recovers_f0174_ships_on_qa_pass(monkeypatch, tmp_path, capsys):
    import sys, types, json
    module = _load_script(monkeypatch)
    state_path = _f0174_state(tmp_path)
    asset_dir = tmp_path / "assets"; asset_dir.mkdir()
    monkeypatch.setenv("FLYER_DETERMINISTIC_RECOVERY", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")

    state = {"n": 0}
    def fake_render(project, _dir, **kwargs):
        state["n"] += 1
        p = asset_dir / f"{project.project_id}-C1.png"; p.write_bytes(b"x")
        # record whether the forced re-render happened
        if kwargs.get("force_background_only"):
            state["forced"] = True
        return [types.SimpleNamespace(path=p, kind="concept_preview", output_format="concept_preview", width=1080, height=1350, concept_id="C1")]
    def fake_qa(project, path, *, output_format, asset_id="A0001"):
        from schemas import FlyerVisualQAReport
        from datetime import datetime, timezone
        # first call (integrated) fails with F0174 blockers; second (forced) passes
        failed = state["n"] <= 1
        return FlyerVisualQAReport(
            project_id="F0174", asset_id=asset_id, artifact_path=str(path), artifact_sha256="a"*64,
            project_version=1, output_format=output_format, provider="test", qa_source="ocr_vision",
            status="failed" if failed else "passed", blockers=list(_F0174_BLOCKERS) if failed else [],
            checked_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        )
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *a, **k: None)
    monkeypatch.setattr(module, "build_asset_manifest", lambda specs, **k: [types.SimpleNamespace(asset_id="A0001")])
    monkeypatch.setattr(sys, "argv", ["generate-flyer-concepts", "--project-id", "F0174",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml")])
    rc = module.main()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert state.get("forced") is True            # mode-2 re-render happened
    assert persisted["status"] != "manual_edit_required"   # shipped, not manual
    assert rc == 0


def test_rung_qa_fail_falls_through_to_manual(monkeypatch, tmp_path):
    import sys, types, json
    module = _load_script(monkeypatch)
    state_path = _f0174_state(tmp_path)
    asset_dir = tmp_path / "assets"; asset_dir.mkdir()
    monkeypatch.setenv("FLYER_DETERMINISTIC_RECOVERY", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    def fake_render(project, _dir, **kwargs):
        p = asset_dir / f"{project.project_id}-C1.png"; p.write_bytes(b"x")
        return [types.SimpleNamespace(path=p, kind="concept_preview", output_format="concept_preview", width=1080, height=1350, concept_id="C1")]
    def fake_qa(project, path, *, output_format, asset_id="A0001"):
        from schemas import FlyerVisualQAReport
        from datetime import datetime, timezone
        return FlyerVisualQAReport(  # always fails (even the deterministic re-render)
            project_id="F0174", asset_id=asset_id, artifact_path=str(path), artifact_sha256="a"*64,
            project_version=1, output_format=output_format, provider="test", qa_source="ocr_vision",
            status="failed", blockers=list(_F0174_BLOCKERS),
            checked_at=datetime(2026, 6, 18, tzinfo=timezone.utc))
    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *a, **k: None)
    monkeypatch.setattr(module, "build_asset_manifest", lambda specs, **k: [types.SimpleNamespace(asset_id="A0001")])
    monkeypatch.setattr(sys, "argv", ["generate-flyer-concepts", "--project-id", "F0174",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml")])
    rc = module.main()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert rc == 2


def test_rung_flag_off_is_byte_identical_manual(monkeypatch, tmp_path):
    # FLAG OFF: F0174 must behave exactly as today → straight to manual,
    # deterministic re-render NEVER attempted.
    import sys, types, json
    module = _load_script(monkeypatch)
    state_path = _f0174_state(tmp_path)
    asset_dir = tmp_path / "assets"; asset_dir.mkdir()
    monkeypatch.delenv("FLYER_DETERMINISTIC_RECOVERY", raising=False)
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    seen = {"forced": False}
    def fake_render(project, _dir, **kwargs):
        if kwargs.get("force_background_only"):
            seen["forced"] = True
        p = asset_dir / f"{project.project_id}-C1.png"; p.write_bytes(b"x")
        return [types.SimpleNamespace(path=p, kind="concept_preview", output_format="concept_preview", width=1080, height=1350, concept_id="C1")]
    def fake_qa(project, path, *, output_format, asset_id="A0001"):
        from schemas import FlyerVisualQAReport
        from datetime import datetime, timezone
        return FlyerVisualQAReport(project_id="F0174", asset_id=asset_id, artifact_path=str(path),
            artifact_sha256="a"*64, project_version=1, output_format=output_format, provider="test",
            qa_source="ocr_vision", status="failed", blockers=list(_F0174_BLOCKERS),
            checked_at=datetime(2026, 6, 18, tzinfo=timezone.utc))
    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *a, **k: None)
    monkeypatch.setattr(module, "build_asset_manifest", lambda specs, **k: [types.SimpleNamespace(asset_id="A0001")])
    monkeypatch.setattr(sys, "argv", ["generate-flyer-concepts", "--project-id", "F0174",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml")])
    rc = module.main()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert seen["forced"] is False                 # rung skipped
    assert persisted["status"] == "manual_edit_required"
    assert rc == 2
```

> Implementer note: the config fixture is provided by `_load_script`'s fake `safe_io.load_yaml_model` (it ignores the `--config-path` value). If an existing script test needs a real `config.yaml` on disk, mirror that test's setup. Adapt `build_asset_manifest`/`run_visual_qa` mock signatures to the real ones if they differ (see lines 985, 1006).

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_generate_concepts.py -k "rung_recovers or rung_qa_fail or rung_flag_off" -v`
Expected: FAIL — all three currently route F0174 to manual (no rung), so `test_rung_recovers_f0174_ships_on_qa_pass` fails on `forced`/status.

- [ ] **Step 3: Add the import + the rung**

Import (script line ~63-65, both branches) add `_deterministic_recovery_enabled`:
```python
    from flyer_render import FlyerRenderError, _premium_repair_enabled, _deterministic_recovery_enabled, build_asset_manifest, ...  # type: ignore
    from agents.flyer.render import FlyerRenderError, _premium_repair_enabled, _deterministic_recovery_enabled, build_asset_manifest, ...
```

Insert the rung after the Slice 2 skip-logging block (~line 1291), before the autorepair-classify block (~line 1315):
```python
        # === Deterministic-recovery rung (FLYER_DETERMINISTIC_RECOVERY, scoped) ===
        # When the integrated render fails QA with ONLY recoverable text-fidelity
        # defects (missing/dropped/misspelled facts, locked-price mismatch, OWN-brand
        # spelling variant), re-render in MODE 2 (textless background + deterministic
        # Fix C overlay — exact text by construction) and re-verify, BEFORE the legacy
        # classify→hard_stop→manual ladder. Genuinely dangerous defects (fabrication,
        # unverified phone, truly different business) are EXCLUDED and fall through.
        # Flag OFF ⇒ skipped ⇒ byte-identical. Independent of FLYER_PREMIUM_OVERLAY
        # (which separately controls premium-vs-flat overlay inside mode 2).
        if (
            failed_qa
            and not source_edit_requested
            and integrated_path_attempted
            and _deterministic_recovery_enabled(current)
            and not _qa_failed_has_fabrication(failed_qa)
            and _qa_failed_exact_text_recoverable(
                failed_qa,
                locked_fact_ids={f.fact_id for f in current.locked_facts},
                project=current,
            )
        ):
            det_specs = []
            try:
                det_specs = render_concept_previews(
                    current,
                    asset_dir,
                    model=draft_model,
                    quality=draft_provider.quality,
                    concept_count=cfg.flyer.concept_count,
                    force_background_only=True,
                )
            except Exception:
                det_specs = []
            if det_specs:
                det_assets = build_asset_manifest(
                    det_specs,
                    first_asset_number=next_asset_number(current),
                    source="rendered",
                    original_message_id=current.original_message_id,
                )
                det_qa_reports = []
                for asset, spec in zip(det_assets, det_specs):
                    _write_sidecar_visual_qa_fixture(current, spec.path)
                    report = run_visual_qa(current, spec.path, output_format=spec.output_format, asset_id=asset.asset_id)
                    write_visual_qa_report(report, spec.path)
                    det_qa_reports.append(report)
                det_failed_qa = [r for r in det_qa_reports if r.status != "passed"]
                specs = det_specs
                assets = det_assets
                qa_reports = det_qa_reports
                failed_qa = det_failed_qa
                if not det_failed_qa:
                    _audit_append(audit_log_path, FlyerIntegratedFellBackDeterministic(
                        ts=datetime.now(timezone.utc),
                        project_id=current.project_id,
                        project_version=current.version,
                        reason="qa_text_fidelity",
                    ))
                # else: deterministic recovery ALSO failed → failed_qa carries the
                # new blockers → existing ladder below routes to manual.
```

> Implementer note: confirm `assets`, `_write_sidecar_visual_qa_fixture`, `next_asset_number`, `audit_log_path` names against the enclosing scope (they appear at lines 1001-1025). Place the rung so `specs`/`assets`/`qa_reports`/`failed_qa` reassignments are visible to the downstream persist + ladder code.

- [ ] **Step 4: Run them to verify they pass**

Run: same as Step 2. Expected: PASS (3 tests). Then the whole file:
`PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_generate_concepts.py -v`
Expected: all PASS (no regression).

- [ ] **Step 5: Commit**

```bash
git add src/agents/flyer/scripts/generate-flyer-concepts tests/test_flyer_generate_concepts.py
git commit -m "feat(flyer): deterministic-recovery rung routes recoverable integrated failures to mode-2"
```

---

## Task 6: Smoke gate + full suite + final review

**Files:**
- Modify: `src/agents/shift/scripts/shift-agent-smoke-test.sh` (flyer python import block)

- [ ] **Step 1: Add an import/flag-default assertion** to the existing flyer smoke python block (so the deploy gate proves the new symbols exist and the flag defaults OFF):

```python
import flyer_render, flyer_visual_qa
assert hasattr(flyer_render, "_deterministic_recovery_enabled")
assert hasattr(flyer_visual_qa, "is_own_brand_variant")
import os
assert os.environ.get("FLYER_DETERMINISTIC_RECOVERY") in (None, "", "0", "1")
```

- [ ] **Step 2: Run the full flyer-relevant suite**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_generate_concepts.py tests/test_flyer_visual_qa.py tests/test_flyer_renderer.py tests/test_flyer_autorepair.py tests/test_flyer_premium_overlay.py -q`
Expected: all PASS (1 skip needs a live key).

- [ ] **Step 3: Run the full build gate**

Run: `python -m pytest tests/ -q`
Expected: 0 failed.

- [ ] **Step 4: Final review** — dispatch a code reviewer over the whole branch diff vs `origin/main` (security/correctness/silent-failure/flag-off byte-identical). Address findings.

- [ ] **Step 5: Commit**

```bash
git add src/agents/shift/scripts/shift-agent-smoke-test.sh
git commit -m "chore(flyer): smoke gate asserts deterministic-recovery symbols + flag default"
```

---

## Tests required by the operator — coverage map

| Operator-required test | Task / test name |
|---|---|
| F0174 replay blocker set | Task 3 `test_qa_recoverable_f0174_replay_blocker_set` |
| own-brand typo recoverable | Task 2 `test_is_own_brand_variant_true_for_own_brand_typo`; Task 3 `test_qa_recoverable_true_for_own_brand_typo_with_project` |
| different business not recoverable | Task 2 `test_is_own_brand_variant_false_for_different_business`; Task 3 `test_qa_recoverable_false_for_different_business_with_project` |
| fabricated price/offer still hard-block | Task 3 `test_qa_recoverable_f0174_with_fabrication_added_is_dangerous` (+ existing `test_qa_recoverable_false_for_fabrication_*`) |
| unverified phone still hard-block | existing `test_qa_recoverable_false_for_unverified_phone` (unchanged-pass proves no regression) |
| item price mismatch recoverable only when locked price exists | existing `test_qa_recoverable_true/false_for_item_price_mismatch_when_price_locked/not_locked` (unchanged-pass) |
| flag OFF byte-identical | Task 5 `test_rung_flag_off_is_byte_identical_manual` |
| forced mode-2 render path | Task 4 `test_force_background_only_uses_overlay_for_integrated_eligible` |
| QA pass ships; QA fail → manual | Task 5 `test_rung_recovers_f0174_ships_on_qa_pass` / `test_rung_qa_fail_falls_through_to_manual` |

---

## Self-Review (writing-plans)

**Spec coverage:** every design §5 component maps to a task (rung→T5, brand-typo→T2/T3, force-background-only→T4, flag/scope→T4, schema reason→T1, smoke→T6). ✓
**Placeholder scan:** no TBD/"handle edge cases"; every code step shows code; "implementer notes" point at exact existing line numbers for scope-variable confirmation (the code exists — not a placeholder). ✓
**Type consistency:** `force_background_only` (kw, default False) consistent T4/T5; `_deterministic_recovery_enabled` consistent T4/T5/T6; `is_own_brand_variant`/`brand_blocker_name` consistent T2/T3; `reason="qa_text_fidelity"` consistent T1/T5. ✓

## Rollout (post-merge, operator-gated — NOT in this plan's code)
Deploy dormant (both flags OFF) → verify byte-identical → operator sets `FLYER_DETERMINISTIC_RECOVERY=1` (+ existing `FLYER_PREMIUM_OVERLAY_ALLOWLIST=+17329837841`) + restart → re-send the 3 validation briefs (F0174 first). Rollback = unset `FLYER_DETERMINISTIC_RECOVERY` + restart.
