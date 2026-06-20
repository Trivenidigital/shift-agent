# Flyer Deterministic-First Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route fact-dense flyers (menus, multi-item lists, exact price lists, combos, schedule+price) directly to mode 2 (textless imagery + deterministic premium overlay), skipping the unreliable integrated model-text path; keep integrated for sparse creative flyers. Scoped to `+17329837841` behind a new flag.

**Architecture:** A deterministic content classifier `_is_fact_dense(project)` (counts structured `locked_facts`) plus a new allowlist-scoped flag gate `_deterministic_first_enabled(project)` are added as ONE early `return False` in the existing `_integrated_poster_eligible(project)`. When the flag is on for the project and the project is fact-dense (and food), integrated becomes ineligible → the existing `_background_only_eligible` path renders the deterministic premium overlay on the primary pass. No new render machinery; everything downstream (overlay, referee, finals, fail-closed) is unchanged.

**Tech Stack:** Python 3, Pydantic v2, pytest. File: `src/agents/flyer/render.py`; tests: `tests/test_flyer_renderer.py`; deploy gate: `src/agents/shift/scripts/shift-agent-smoke-test.sh`.

**Design doc:** `docs/superpowers/specs/2026-06-20-flyer-deterministic-first-routing-design.md`
**Drift-check tag:** `extends-Hermes`.
**Authoritative source:** `origin/main` `5ec293f`. All line numbers below are from origin/main.
**Test command (Windows):** `PYTHONPATH="src;src/platform" python -m pytest <path> -v`

**Invariants (preserve):** flag-off (`FLYER_DETERMINISTIC_FIRST` unset) byte-identical; sparse flyers keep integrated; referee/fail-closed unchanged; no schema migration; no change to `_qa_failed_exact_text_recoverable` (the F0179 classifier patch is superseded, not expanded); scoped to `+17329837841` via the shared `FLYER_PREMIUM_OVERLAY_ALLOWLIST`.

---

## File Structure

| File | Change |
|---|---|
| `src/agents/flyer/render.py` | add `_is_fact_dense` + fact-pattern regexes (near `_integrated_poster_eligible` ~line 1154); add `FLYER_DETERMINISTIC_FIRST` env const + `_deterministic_first_enabled` (after `_deterministic_recovery_enabled` ~line 3365); add ONE gate line inside `_integrated_poster_eligible` after the food gate |
| `tests/test_flyer_renderer.py` | fixtures (dense/sparse) + classifier, flag, and routing tests |
| `src/agents/shift/scripts/shift-agent-smoke-test.sh` | deploy gate: router active under flag / no-op when off |

---

## Task 1: `_is_fact_dense(project)` content classifier

**Files:**
- Modify: `src/agents/flyer/render.py` (add regexes + function just ABOVE `def _integrated_poster_eligible` at line 1154)
- Test: `tests/test_flyer_renderer.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_flyer_renderer.py`; the file already does `import agents.flyer.render as render_module` and imports `FlyerProject`). Add these fixtures + tests:

```python
def _df_project(facts, *, biz="Lakshmi's Kitchen", phone="+17329837841", notes=""):
    """Build a food FlyerProject with the given locked_facts (dicts)."""
    return FlyerProject.model_validate({
        "project_id": "F9100",
        "status": "generating_concepts",
        "customer_phone": phone,
        "customer_id": "CUST0001",
        "created_at": "2026-06-20T00:00:00Z",
        "updated_at": "2026-06-20T00:00:00Z",
        "original_message_id": "wamid.df",
        "raw_request": notes or biz,
        "fields": {"event_or_business_name": biz, "preferred_language": "en", "notes": notes},
        "locked_facts": facts,
    })


def _fact(fid, value, label="F"):
    return {"fact_id": fid, "label": label, "value": value, "source": "customer_text", "required": True}


def _weekend_project():  # F0179 Weekend Specials — dense (>=2 items + currency pricing_structure)
    facts = [
        _fact("business_name", "Lakshmi's Kitchen"),
        _fact("campaign_title", "Weekend Specials"),
        _fact("contact_phone", "+17329837841"),
        _fact("location", "90 Brybar Dr St Johns FL"),
        _fact("pricing_structure", "Any item $7.99"),
        _fact("schedule", "Saturday & Sunday, 4 PM-8 PM"),
    ]
    for i, n in enumerate(["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar"]):
        facts.append(_fact(f"item:{i}:name", n))
    return _df_project(facts, notes="Weekend Specials menu, any item $7.99")


def _combo_project():  # Veg/Non-Veg Combo — dense (>=2 offers)
    facts = [
        _fact("business_name", "Lakshmi's Kitchen"),
        _fact("contact_phone", "+17329837841"),
        _fact("offer:0", "Veg Combo $12.99"),
        _fact("offer:1", "Non-Veg Combo $15.99"),
    ]
    return _df_project(facts, notes="Veg and Non-Veg combo meals")


def _dessert_project():  # Festival Dessert — dense (>=2 items + >=2 item prices)
    facts = [
        _fact("business_name", "Lakshmi's Kitchen"),
        _fact("contact_phone", "+17329837841"),
        _fact("item:0:name", "Gulab Jamun"), _fact("item:0:price", "$5.99"),
        _fact("item:1:name", "Kaju Katli"), _fact("item:1:price", "$6.99"),
        _fact("item:2:name", "Rasmalai"), _fact("item:2:price", "$5.99"),
    ]
    return _df_project(facts, notes="Diwali festival sweets and desserts")


def _sparse_project():  # sparse control — single creative flyer, no list/price
    facts = [
        _fact("business_name", "Lakshmi's Kitchen"),
        _fact("campaign_title", "Now Hiring"),
        _fact("contact_phone", "+17329837841"),
    ]
    return _df_project(facts, notes="Now hiring kitchen staff, apply in store")


def test_is_fact_dense_weekend_specials():
    assert render_module._is_fact_dense(_weekend_project()) is True


def test_is_fact_dense_combo():
    assert render_module._is_fact_dense(_combo_project()) is True


def test_is_fact_dense_dessert():
    assert render_module._is_fact_dense(_dessert_project()) is True


def test_is_fact_dense_sparse_control():
    assert render_module._is_fact_dense(_sparse_project()) is False


def test_is_fact_dense_schedule_plus_currency_price():
    p = _df_project([
        _fact("business_name", "Lakshmi's Kitchen"),
        _fact("pricing_structure", "Lunch Buffet $11.99"),
        _fact("schedule", "Mon-Fri 11-3"),
    ], notes="Lunch buffet weekday hours")
    assert render_module._is_fact_dense(p) is True


def test_is_fact_dense_single_percent_offer_is_sparse():
    p = _df_project([
        _fact("business_name", "Lakshmi's Kitchen"),
        _fact("offer:0", "10% off everything this weekend"),
    ], notes="grand opening 10% off")
    assert render_module._is_fact_dense(p) is False
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: ... '_is_fact_dense'`)

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k is_fact_dense -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — add directly ABOVE `def _integrated_poster_eligible(project: FlyerProject) -> bool:` (line 1154). `re` and `os` are already imported at the top of render.py.

```python
# Deterministic-first content classifier (2026-06-20). Fact-dense flyers (menus,
# price lists, combos, schedule+price) carry exact text the image model garbles
# (~24% first-try success live); they render reliably via the deterministic
# overlay instead. Pure heuristic over structured locked_facts — no model call.
_FACT_ITEM_NAME_RE = re.compile(r"^item:\d+:name$")
_FACT_ITEM_PRICE_RE = re.compile(r"^item:\d+:price$")
_FACT_OFFER_RE = re.compile(r"^offer:\d+$")
_FACT_CURRENCY_RE = re.compile(r"[$₹]\s*\d")  # $ or rupee followed by a digit


def _is_fact_dense(project: FlyerProject) -> bool:
    """True when the project carries fact-dense exact text (menu / multi-item /
    price list / combo / schedule+price). Deterministic over locked_facts."""
    facts = list(getattr(project, "locked_facts", []) or [])

    def _fid(f):
        return (getattr(f, "fact_id", "") or "")

    def _has_currency(f):
        return bool(f) and bool(_FACT_CURRENCY_RE.search(getattr(f, "value", "") or ""))

    item_names = {_fid(f) for f in facts if _FACT_ITEM_NAME_RE.match(_fid(f))}
    item_prices = [f for f in facts if _FACT_ITEM_PRICE_RE.match(_fid(f))]
    offers = [f for f in facts if _FACT_OFFER_RE.match(_fid(f))]
    pricing_structure = next((f for f in facts if _fid(f) == "pricing_structure"), None)
    has_schedule = any(_fid(f) == "schedule" for f in facts)

    # (a) >=2 distinct menu items
    if len(item_names) >= 2:
        return True
    # (b) >=2 item prices
    if len(item_prices) >= 2:
        return True
    # (c) a currency-amount pricing structure (not a % discount)
    if _has_currency(pricing_structure):
        return True
    # (d) >=2 offers (combo / multi-offer)
    if len(offers) >= 2:
        return True
    # (e) recurring schedule + any currency-amount price fact
    if has_schedule and (
        _has_currency(pricing_structure)
        or any(_has_currency(f) for f in item_prices)
        or any(_has_currency(f) for f in offers)
    ):
        return True
    return False
```

- [ ] **Step 4: Run — expect PASS**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k is_fact_dense -v`
Expected: all 6 pass.

- [ ] **Step 5: Commit**

```bash
git add src/agents/flyer/render.py tests/test_flyer_renderer.py
git commit -m "feat(flyer): _is_fact_dense content classifier for deterministic-first routing"
```

---

## Task 2: `FLYER_DETERMINISTIC_FIRST` flag gate

**Files:**
- Modify: `src/agents/flyer/render.py` (add after `_deterministic_recovery_enabled`, which ends ~line 3365)
- Test: `tests/test_flyer_renderer.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_flyer_renderer.py`; reuses `_weekend_project` from Task 1)

```python
def test_deterministic_first_enabled_flag_off(monkeypatch):
    monkeypatch.delenv("FLYER_DETERMINISTIC_FIRST", raising=False)
    assert render_module._deterministic_first_enabled(_weekend_project()) is False


def test_deterministic_first_enabled_global_when_no_allowlist(monkeypatch):
    monkeypatch.setenv("FLYER_DETERMINISTIC_FIRST", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)
    assert render_module._deterministic_first_enabled(_weekend_project()) is True


def test_deterministic_first_enabled_allowlist_scoped(monkeypatch):
    monkeypatch.setenv("FLYER_DETERMINISTIC_FIRST", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    assert render_module._deterministic_first_enabled(_weekend_project()) is True
    other = _weekend_project().model_copy(update={"customer_phone": "+19998887777"})
    assert render_module._deterministic_first_enabled(other) is False
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: ... '_deterministic_first_enabled'`)

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k deterministic_first_enabled -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — add immediately AFTER the `_deterministic_recovery_enabled` function (after line 3365). Mirrors it exactly, reusing `_premium_overlay_allowlist` + `_normalize_sender` and the shared allowlist:

```python
DETERMINISTIC_FIRST_ENV = "FLYER_DETERMINISTIC_FIRST"


def _deterministic_first_enabled(project: FlyerProject) -> bool:
    """Routing gate for deterministic-first: fact-dense flyers skip integrated
    model text and render via the deterministic overlay. Flag
    FLYER_DETERMINISTIC_FIRST == "1" AND (the shared FLYER_PREMIUM_OVERLAY_ALLOWLIST
    is empty => global, else project.customer_phone is in it). Independent of
    FLYER_PREMIUM_OVERLAY / FLYER_DETERMINISTIC_RECOVERY. Mirrors
    _deterministic_recovery_enabled exactly."""
    if os.environ.get(DETERMINISTIC_FIRST_ENV) != "1":
        return False
    allow = _premium_overlay_allowlist()
    if not allow:
        return True
    return _normalize_sender(getattr(project, "customer_phone", "") or "") in allow
```

- [ ] **Step 4: Run — expect PASS**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k deterministic_first_enabled -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/flyer/render.py tests/test_flyer_renderer.py
git commit -m "feat(flyer): FLYER_DETERMINISTIC_FIRST allowlist-scoped flag gate"
```

---

## Task 3: Wire the gate into `_integrated_poster_eligible`

**Files:**
- Modify: `src/agents/flyer/render.py` (`_integrated_poster_eligible`, line 1154–1203)
- Test: `tests/test_flyer_renderer.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_flyer_renderer.py`). These set `FLYER_ALLOW_INTEGRATED_POSTER=1` so the existing integrated path is otherwise enabled; the fixtures are food (Lakshmi's Kitchen).

```python
def _enable_integrated(monkeypatch):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)


def test_dense_flag_on_routes_to_mode2(monkeypatch):
    _enable_integrated(monkeypatch)
    monkeypatch.setenv("FLYER_DETERMINISTIC_FIRST", "1")
    p = _weekend_project()
    assert render_module._integrated_poster_eligible(p) is False     # not integrated
    assert render_module._background_only_eligible(p) is True        # -> mode 2


def test_sparse_flag_on_stays_integrated(monkeypatch):
    _enable_integrated(monkeypatch)
    monkeypatch.setenv("FLYER_DETERMINISTIC_FIRST", "1")
    assert render_module._integrated_poster_eligible(_sparse_project()) is True


def test_dense_flag_off_current_behavior(monkeypatch):
    _enable_integrated(monkeypatch)
    monkeypatch.delenv("FLYER_DETERMINISTIC_FIRST", raising=False)
    assert render_module._integrated_poster_eligible(_weekend_project()) is True


def test_flag_off_byte_identical_dense_and_sparse(monkeypatch):
    # Flag unset: BOTH dense and sparse food projects are integrated-eligible,
    # i.e. exactly the pre-change behavior (the gate is a no-op).
    _enable_integrated(monkeypatch)
    monkeypatch.delenv("FLYER_DETERMINISTIC_FIRST", raising=False)
    assert render_module._integrated_poster_eligible(_weekend_project()) is True
    assert render_module._integrated_poster_eligible(_sparse_project()) is True


def test_dense_flag_on_premium_overlay_interaction(monkeypatch):
    # dense + det-first -> mode 2; with premium overlay ON, mode 2 renders premium.
    _enable_integrated(monkeypatch)
    monkeypatch.setenv("FLYER_DETERMINISTIC_FIRST", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    p = _weekend_project()
    assert render_module._integrated_poster_eligible(p) is False
    assert render_module._premium_overlay_enabled(p) is True


def test_dense_flag_on_scoped_other_number_unaffected(monkeypatch):
    # scoped allowlist: a different number's dense flyer is NOT diverted.
    _enable_integrated(monkeypatch)
    monkeypatch.setenv("FLYER_DETERMINISTIC_FIRST", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    other = _weekend_project().model_copy(update={"customer_phone": "+19998887777"})
    assert render_module._integrated_poster_eligible(other) is True   # still integrated
```

- [ ] **Step 2: Run — expect FAIL** (the dense+flag-on tests fail; the gate isn't wired yet)

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k "flag_on or flag_off_byte or premium_overlay_interaction or scoped_other" -v`
Expected: `test_dense_flag_on_routes_to_mode2`, `test_dense_flag_on_premium_overlay_interaction` FAIL (currently return True); others pass.

- [ ] **Step 3: Implement** — in `_integrated_poster_eligible`, immediately AFTER the food gate:
```python
    if not _is_food_or_grocery_project(project):
        return False
```
insert:
```python
    # Deterministic-first routing (2026-06-20): fact-dense food flyers (menus,
    # price lists, combos, schedule+price) skip integrated model-rendered text and
    # render via the deterministic premium overlay (mode 2). Gated + allowlist-scoped
    # via FLYER_DETERMINISTIC_FIRST; flag-off short-circuits -> byte-identical.
    if _deterministic_first_enabled(project) and _is_fact_dense(project):
        return False
```
Leave the rest of the function unchanged.

- [ ] **Step 4: Run — expect PASS** (the routing tests, then the whole file)

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k "flag_on or flag_off_byte or premium_overlay_interaction or scoped_other" -v`
Expected: PASS.
Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -q`
Expected: all pass (no regression; flag-off paths unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/agents/flyer/render.py tests/test_flyer_renderer.py
git commit -m "feat(flyer): deterministic-first gate in _integrated_poster_eligible (fact-dense -> mode 2)"
```

---

## Task 4: Deploy smoke gate — router active under flag, no-op when off

**Files:**
- Modify: `src/agents/shift/scripts/shift-agent-smoke-test.sh` (append after the premium-overlay render gate "2.0b", which ends ~line 273)

- [ ] **Step 1: Add the smoke gate.** Pure routing logic under `$PY` (no rendering); deterministic + fast. Mirror the existing `if ! "$PY" -c "..."; then echo FAIL; exit 1; fi` structure.

```bash
# 2.0c Deterministic-first routing gate. With FLYER_DETERMINISTIC_FIRST=1 a
# fact-dense food flyer must become integrated-INELIGIBLE (routes to the
# deterministic mode-2 overlay); with the flag unset it must stay eligible
# (byte-identical). Pure eligibility logic — no model call, no render.
if ! FLYER_ALLOW_INTEGRATED_POSTER=1 "$PY" -c "
import sys
sys.path.insert(0, '/opt/shift-agent')
import os
import flyer_render as r
from schemas import FlyerProject
facts = [
    {'fact_id':'business_name','label':'B','value':\"Lakshmi's Kitchen\",'required':True,'source':'customer_text'},
    {'fact_id':'pricing_structure','label':'P','value':'Any item \$7.99','required':True,'source':'customer_text'},
]
for i, n in enumerate(['Idli','Dosa','Vada','Uttapam','Pongal','Sambar']):
    facts.append({'fact_id':f'item:{i}:name','label':'I','value':n,'required':True,'source':'customer_text'})
proj = FlyerProject.model_validate({
    'project_id':'S0002','status':'generating_concepts','customer_phone':'+17329837841',
    'customer_id':'CUST0001','created_at':'2026-06-20T00:00:00Z','updated_at':'2026-06-20T00:00:00Z',
    'original_message_id':'wamid.S0002','raw_request':'Weekend Specials menu any item \$7.99',
    'fields':{'event_or_business_name':'Weekend Specials','preferred_language':'en','notes':'menu'},
    'locked_facts':facts,
})
assert r._is_fact_dense(proj) is True, 'fact-dense classifier failed on a menu'
os.environ.pop('FLYER_DETERMINISTIC_FIRST', None)
os.environ.pop('FLYER_PREMIUM_OVERLAY_ALLOWLIST', None)
assert r._integrated_poster_eligible(proj) is True, 'flag-off should be byte-identical (integrated-eligible)'
os.environ['FLYER_DETERMINISTIC_FIRST'] = '1'
assert r._integrated_poster_eligible(proj) is False, 'flag-on dense should route to mode 2 (ineligible for integrated)'
print('deterministic-first routing OK: dense+flag-on -> mode 2; flag-off unchanged')
" > /dev/null; then
    echo \"FAIL: deterministic-first routing gate — dense flyer not routed to mode 2 under FLYER_DETERMINISTIC_FIRST, or flag-off not byte-identical\"
    exit 1
fi
echo \"✓ deterministic-first routing: fact-dense -> mode 2 under flag; no-op when off\"
```

- [ ] **Step 2: Verify the script parses.**

Run: `bash -n src/agents/shift/scripts/shift-agent-smoke-test.sh`
Expected: no output. Also extract the embedded `$PY` snippet and `python -c "import ast; ast.parse(open('snippet.py').read())"` to confirm it parses.

- [ ] **Step 3: Commit**

```bash
git add src/agents/shift/scripts/shift-agent-smoke-test.sh
git commit -m "feat(flyer): smoke gate for deterministic-first routing (dense->mode2, off=no-op)"
```

---

## Task 5: Full suite + Codex review

- [ ] **Step 1: Full build suite**

Run: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py tests/test_flyer_schemas.py tests/test_flyer_premium_overlay.py tests/test_flyer_premium_outcome_script.py -q`
Expected: 0 failed. (Full `tests/` suite runs on Linux/CI — Windows lacks `fcntl`.)

- [ ] **Step 2: Codex review** the branch diff. Focus: (a) `_is_fact_dense` matches the design criteria (≥2 items / ≥2 item prices / currency pricing_structure / ≥2 offers / schedule+currency-price; % discount and lone offer/event = sparse); (b) `_deterministic_first_enabled` mirrors `_deterministic_recovery_enabled` exactly (shared allowlist); (c) the gate is one early `return False` after the food gate; (d) flag-off byte-identical (short-circuit); (e) sparse stays integrated; (f) no change to `_qa_failed_exact_text_recoverable`, the overlay, the referee, or any schema; (g) the gate diverts ONLY food fact-dense projects for the scoped number.

- [ ] **Step 3: Fix any BLOCKER/MAJOR; re-review to CLEAN.**

---

## Self-Review (writing-plans)

**Spec coverage:** content-class router → Task 1 (`_is_fact_dense`); fact-dense/sparse criteria → Task 1 (the 5 branches + sparse tests); examples (F0179/combo/dessert/sparse) → Task 1 fixtures + Task 3 routing; fallback → unchanged (reuses existing mode-2 ladder, no code); flag interaction → Task 2 + Task 3 (`FLYER_PREMIUM_OVERLAY` interaction test); QA/referee → unchanged (no code); manual-rate impact → measured post-deploy (§ design); rollout scoped → flag + allowlist (Task 2) + smoke (Task 4) + post-build; success metrics → post-deploy. ✓
**Validation cases:** F0179 Weekend Specials (`_weekend_project`), Veg/Non-Veg Combo (`_combo_project`), Festival Dessert (`_dessert_project`), sparse control (`_sparse_project`) — all in Task 1 + Task 3. ✓
**Placeholder scan:** concrete code + commands throughout. ✓
**Type consistency:** `_is_fact_dense`, `_deterministic_first_enabled`, `DETERMINISTIC_FIRST_ENV`, `_premium_overlay_allowlist`, `_normalize_sender`, `_integrated_poster_eligible`, `_background_only_eligible`, `_premium_overlay_enabled` used consistently across tasks. ✓

## Out of scope (deferred)
Expanding `_qa_failed_exact_text_recoverable` for F0179 (superseded); LLM content classification; deterministic-everywhere (sparse keeps integrated); broadening beyond `+17329837841`; overlay visual redesign; threshold tuning (revisit after soak).

## Post-build (operator-gated)
PR → CI → Codex → merge → deploy dormant (flag off ⇒ byte-identical; smoke gate 2.0c verifies the no-op) → operator-gated scoped activation `FLYER_DETERMINISTIC_FIRST=1` (+ restart) → operator sends F0179 Weekend Specials / Veg-NonVeg Combo / Festival Dessert (expect mode-2 premium delivered, no integrated attempt, `flyer_premium_overlay_outcome status=premium_overlay_delivered render_path=subprocess`, dangerous-leak=0) + one sparse control (expect integrated path unchanged) → soak + review success metrics before any broadening. Rollback = `FLYER_DETERMINISTIC_FIRST` off + restart → integrated-primary returns.
