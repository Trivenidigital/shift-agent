# Flyer Architecture A — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make integrated gemini-3.1 generation the primary flyer path for food/grocery, gated by a hardened referee with retry→deterministic-fallback, so the gen.png→F0150 quality jump ships without any flyer going out unverified or worse than today.

**Architecture:** Narrow migration of the existing flyer agent. Point the image model at gemini-3.1; widen `_integrated_poster_eligible`; add fabricated-offer/price detection to `visual_qa`; wrap render in a retry/fallback orchestration inside `scripts/generate-flyer-concepts`; reuse the deterministic renderer as the verified fallback and the `repair_instruction` primitive for corrective retries. Kill-switch forces byte-identical deterministic output.

**Tech Stack:** Python 3, Pydantic v2 (`schemas.py`), urllib (OpenRouter), pytest.

**Spec:** `docs/superpowers/specs/2026-06-14-flyer-architecture-A-slice1-design.md`

**Pre-flight (execution-time, before Task 1):** confirm the *current* production render config on main-vps (`/opt/shift-agent/config.yaml` `flyer.draft_image_model`/`final_image_model`) — this defines "today's output" for the kill-switch byte-identical test. Record the value; the deterministic fallback + kill-switch must reproduce it exactly.

---

### Task 1: Fabricated offer/price detection in the referee

**Files:**
- Modify: `src/agents/flyer/visual_qa.py` (add helper near other `_*_blockers`; add 2 patterns to `_BLOCK_TIER_PATTERNS` at line ~1219; call helper in `run_visual_qa` at line ~1576)
- Test: `tests/test_flyer_visual_qa_fabrication.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_flyer_visual_qa_fabrication.py
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "platform"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "agents" / "flyer"))
from schemas import FlyerProject, FlyerLockedFact
import visual_qa

def _proj(facts):
    return FlyerProject(project_id="F9001", customer_phone="+17329837841",
                        locked_facts=[FlyerLockedFact(fact_id=f[0], label=f[1], value=f[2], required=True) for f in facts])

def test_unauthorized_dollar_price_blocks():
    p = _proj([("item:0:name","Item","Punugulu"),("item:0:price","Price","$6.99")])
    b = visual_qa._fabricated_offer_price_blockers(p, "Punugulu $6.99\n6 OFFERS $3.99 | $4.99")
    assert any(x.startswith("fabricated price visible: ") and "$3.99" in x for x in b)

def test_locked_price_does_not_block():
    p = _proj([("item:0:name","Item","Punugulu"),("item:0:price","Price","$6.99")])
    assert visual_qa._fabricated_offer_price_blockers(p, "Punugulu $6.99") == []

def test_nondollar_promo_claim_blocks_when_no_offer():
    p = _proj([("item:0:name","Item","Masala Dosa")])
    b = visual_qa._fabricated_offer_price_blockers(p, "Masala Dosa\nLimited Time Deal!\nSpecial Combo")
    assert any(x.startswith("fabricated offer claim visible: ") for x in b)

def test_nondollar_promo_passes_when_offer_fact_exists():
    p = _proj([("offer:0","Offer","Special Combo any 2 for $9.99")])
    assert visual_qa._fabricated_offer_price_blockers(p, "Special Combo any 2 for $9.99") == []

def test_fabrication_is_block_tier():
    p = _proj([("business_name","Business","Lakshmi's Kitchen")])
    sev = visual_qa.classify_qa_severity(["fabricated price visible: $3.99"], project=p)
    assert sev == "block"
```

- [ ] **Step 2: Run, verify fail** — `pytest tests/test_flyer_visual_qa_fabrication.py -v` → FAIL (`_fabricated_offer_price_blockers` undefined).

- [ ] **Step 3: Implement helper** (add to `visual_qa.py`, near `_unexpected_phone_blockers`)

```python
_PRICE_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?")
_PROMO_PHRASE_RE = re.compile(
    r"\b(limited[\s-]?time(?:\s+deal)?|today\s+only|special\s+combo|special\s+deal|"
    r"lunch\s+offer|dinner\s+offer|grand\s+sale|flat\s+\d*\s*off|buy\s+\d+\s+get|"
    r"\d+\s*%\s*off|\bfree\b)\b", re.IGNORECASE)

def _norm_price(tok: str) -> str:
    return re.sub(r"\s+", "", tok).replace(",", "")

def _locked_price_set(project) -> set[str]:
    out: set[str] = set()
    for fact in project.locked_facts:
        for m in _PRICE_RE.findall(fact.value or ""):
            out.add(_norm_price(m))
    return out

def _has_offer_fact(project) -> bool:
    for fact in project.locked_facts:
        fid = fact.fact_id.lower()
        if fid.startswith("offer") or "offer" in (fact.label or "").lower() \
           or fid in {"pricing_structure", "promotion_end"} or "pric" in fid:
            return True
    return False

def _fabricated_offer_price_blockers(project, extracted_text: str) -> list[str]:
    """Slice 1: flag $-prices and promo claims in OCR not backed by locked_facts.
    Anchored on numbers (low false-positive); reworded legit offers with a locked
    price still pass. Non-dollar promo wording blocks only when NO offer fact exists."""
    blockers: list[str] = []
    locked = _locked_price_set(project)
    for tok in _PRICE_RE.findall(extracted_text or ""):
        if _norm_price(tok) not in locked:
            blockers.append(f"fabricated price visible: {tok.strip()}")
    if not _has_offer_fact(project):
        for m in _PROMO_PHRASE_RE.finditer(extracted_text or ""):
            blockers.append(f"fabricated offer claim visible: {m.group(0).strip()}")
    return blockers
```

- [ ] **Step 4: Register block-tier patterns** — add to `_BLOCK_TIER_PATTERNS` (after line 1219, before the trailing `quality_note_corruption` entry):

```python
    (re.compile(r"^fabricated price visible: "), "fabricated_price"),
    (re.compile(r"^fabricated offer claim visible: "), "fabricated_offer"),
```

- [ ] **Step 5: Wire into `run_visual_qa`** — after `blockers.extend(_unexpected_phone_blockers(project, extracted_text))` (line ~1576):

```python
    blockers.extend(_fabricated_offer_price_blockers(project, extracted_text))
```

- [ ] **Step 6: Run tests** — `pytest tests/test_flyer_visual_qa_fabrication.py -v` → PASS. Then full QA suite: `pytest tests/ -k visual_qa -q` → no regressions.

- [ ] **Step 7: Commit** — `git add src/agents/flyer/visual_qa.py tests/test_flyer_visual_qa_fabrication.py && git commit -m "feat(flyer): referee detects fabricated offers/prices (block-tier)"`

---

### Task 2: Widen integrated-poster eligibility

**Files:**
- Modify: `src/agents/flyer/render.py:1102-1150` (`_integrated_poster_eligible`)
- Test: `tests/test_flyer_integrated_eligibility.py` (new)

- [ ] **Step 1: Write failing tests** — English 8-item food project → eligible; Telugu food project → eligible; source-edit project → not eligible; reference-extraction-pending → not eligible; non-food → not eligible. (Construct `FlyerProject` fixtures mirroring `tests/test_flyer_renderer.py` helpers; set `FLYER_ALLOW_INTEGRATED_POSTER=1` via monkeypatch.)

```python
def test_english_8_items_eligible(monkeypatch):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER","1")
    p = _food_project(items=8, language="en")
    assert render._integrated_poster_eligible(p) is True

def test_telugu_food_now_eligible(monkeypatch):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER","1")
    p = _food_project(items=5, language="te", telugu=True)
    assert render._integrated_poster_eligible(p) is True

def test_source_edit_not_eligible(monkeypatch):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER","1")
    assert render._integrated_poster_eligible(_source_edit_project()) is False

def test_reference_extraction_pending_not_eligible(monkeypatch):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER","1")
    assert render._integrated_poster_eligible(_reference_extraction_project()) is False
```

- [ ] **Step 2: Run, verify fail** (Telugu currently returns False; 8-item currently returns False).

- [ ] **Step 3: Edit `_integrated_poster_eligible`** — remove the regional-script exclusion block (lines 1123-1127) and the item-count caps (lines 1139-1146). Keep the `_needs_reference_extraction`, `_is_source_edit_project`, `_is_food_or_grocery_project`, and reference-image-without-menu guards. Result:

```python
    if _needs_reference_extraction(project):
        return False
    if _is_source_edit_project(project):
        return False
    if not _is_food_or_grocery_project(project):
        return False
    reference_menu = _style_only_reference_requested(project) and _has_materialized_reference_menu_facts(project)
    has_reference_image = any(getattr(a, "kind", "") == "reference_image" for a in _project_reference_assets(project))
    if has_reference_image and not reference_menu:
        return False
    plan = _poster_copy_plan(project)
    if not (plan.items or plan.detail_lines or plan.title):
        return False
    return True
```

(Language no longer gates; the deterministic fallback handles Telugu glyph failures per the referee loop in Task 4.)

- [ ] **Step 4: Run tests** — `pytest tests/test_flyer_integrated_eligibility.py -v` → PASS; `pytest tests/ -k "renderer or eligib" -q` → no regressions.

- [ ] **Step 5: Commit** — `git commit -am "feat(flyer): widen integrated-poster eligibility (drop item cap, allow regional script)"`

---

### Task 3: Two-sided repair instruction builder

**Files:**
- Create: `src/agents/flyer/repair.py` (small, focused — turns referee blockers into a corrective render instruction)
- Test: `tests/test_flyer_repair_instruction.py` (new)

- [ ] **Step 1: Write failing tests**

```python
from agents.flyer.repair import build_repair_instruction
def test_includes_missing_and_removes_fabricated():
    blockers = ["missing required visible fact: contact_phone",
                "fabricated price visible: $3.99",
                "fabricated offer claim visible: Limited Time Deal"]
    locked = ["Lakshmi's Kitchen","+1 732-983-7841","Punugulu $6.99"]
    instr = build_repair_instruction(blockers, locked)
    assert "+1 732-983-7841" in instr           # include side
    assert "remove" in instr.lower()             # remove side
    assert "$3.99" in instr and "Limited Time Deal" in instr
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

```python
# src/agents/flyer/repair.py
def build_repair_instruction(blockers: list[str], locked_values: list[str]) -> str:
    fabricated = [b.split(": ", 1)[1] for b in blockers
                  if b.startswith(("fabricated price visible: ", "fabricated offer claim visible: "))]
    missing = [b.split(": ", 1)[1] for b in blockers if b.startswith("missing required visible fact: ")]
    parts = ["Render ONLY these exact facts and NOTHING else: " + " | ".join(locked_values) + "."]
    if missing:
        parts.append("Ensure these are clearly visible: " + ", ".join(missing) + ".")
    parts.append("Remove any claim, price, offer, discount, badge, or label that is not in the list above"
                 + (": " + ", ".join(fabricated) + "." if fabricated else "."))
    return " ".join(parts)
```

- [ ] **Step 4: Run tests** → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(flyer): two-sided repair instruction (include locked + remove fabricated)"`

---

### Task 4: Retry → deterministic-fallback → manual_review orchestration

**Files:**
- Modify: `src/agents/flyer/scripts/generate-flyer-concepts` (wrap render with the referee loop)
- Test: `tests/test_flyer_generate_orchestration.py` (new; subprocess-invoke + monkeypatched render/QA, mirroring `tests/test_catering_v02_scripts.py`)

- [ ] **Step 1: Write failing test** — drive the script with a stubbed `render_concept_previews` and `run_visual_qa` (via an injected test module path or env-selected fakes) asserting: (a) referee block twice then pass → integrated kept; (b) block ×3 → deterministic fallback invoked; (c) deterministic also blocks → project status `manual_edit_required`; (d) referee `provider_unavailable` → deterministic fallback + QA note `integrated_referee_unavailable_fallback` present in `project.qa_reports`.

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement orchestration** in `generate-flyer-concepts` (replace the single `render_concept_previews(...)` call in the non-source-edit branch, lines 67-74, with the loop):

```python
from agents.flyer.repair import build_repair_instruction  # noqa
KILL = os.environ.get("FLYER_INTEGRATED_KILLSWITCH") == "1"
DET = "deterministic-renderer"

def _render_and_check(model, repair=""):
    specs = render_concept_previews(project, asset_dir, model=model,
                                    quality=cfg.flyer.draft_image_quality,
                                    concept_count=cfg.flyer.concept_count,
                                    repair_instruction=repair)
    report = run_visual_qa(project, specs[0].path, output_format="concept_preview")
    return specs, report

if KILL:
    specs, report = _render_and_check(DET)
    outcome = "killswitch_deterministic"
else:
    model = cfg.flyer.draft_image_model
    locked = [f.value for f in project.locked_facts if f.required]
    specs, report = _render_and_check(model)
    attempts = 0
    while report.severity == "block" and report.status != "provider_unavailable" and attempts < 2:
        attempts += 1
        specs, report = _render_and_check(model, build_repair_instruction(report.blockers, locked))
    referee_unavailable = report.status == "provider_unavailable"
    if report.severity == "block" or referee_unavailable:
        specs, report = _render_and_check(DET)   # verified deterministic fallback
        outcome = "fell_back_deterministic"
        if referee_unavailable:
            project = project.model_copy(update={"qa_reports": [*project.qa_reports,
                report.model_copy(update={"blockers": [*report.blockers, "integrated_referee_unavailable_fallback"]})]})
    else:
        outcome = "integrated_passed" if attempts == 0 else "integrated_passed_after_retry"

manual = report.severity == "block"   # deterministic also failed
print(json.dumps({"project_id": args.project_id, "flyer_integrated_outcome": outcome,
                  "attempts": locals().get("attempts", 0), "manual_edit_required": manual}))
```

Then in the state-write block (lines 115-122), set `status` to `"manual_edit_required"` when `manual` is True, else the existing `awaiting_final_approval`/`awaiting_concept_selection`. Append `report` to `qa_reports`.

- [ ] **Step 4: Run tests** → PASS; `pytest tests/ -k "flyer" -q` → no regressions.

- [ ] **Step 5: Commit** — `git commit -am "feat(flyer): integrated render with retry/fallback/manual + referee-unavailable QA note"`

---

### Task 5: Kill-switch byte-identical regression test

**Files:**
- Test: `tests/test_flyer_killswitch_identical.py` (new)

- [ ] **Step 1: Write test** — render a fixture project twice: once with `FLYER_INTEGRATED_KILLSWITCH=1` through the orchestration path, once via the direct deterministic `render_concept_previews(model="deterministic-renderer")`. Assert the two output PNG bytes (or sha256) are identical.

```python
def test_killswitch_matches_deterministic(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH","1")
    a = render.render_concept_previews(_food_project(items=4), tmp_path/"a", model="deterministic-renderer", concept_count=1)
    b = render.render_concept_previews(_food_project(items=4), tmp_path/"b", model="deterministic-renderer", concept_count=1)
    assert sha256(a[0].path) == sha256(b[0].path)
```

- [ ] **Step 2-4: Run → PASS; commit** — `git commit -am "test(flyer): kill-switch deterministic output is byte-identical"`

---

### Task 6: Telugu regional referee model + bias-to-fallback

**Files:**
- Modify: `src/agents/flyer/visual_qa.py` (regional OCR uses a stronger model when the project is regional-script)
- Test: extend `tests/test_flyer_visual_qa_fabrication.py`

- [ ] **Step 1: Write test** — for a regional (`preferred_language="te"`) project, `_vision_text` is called with `FLYER_REGIONAL_QA_MODEL` (default `google/gemini-2.5-flash`) rather than the default mini model (assert via monkeypatched capture of the model arg).

- [ ] **Step 2-4: Implement** — add `FLYER_REGIONAL_QA_MODEL` env (default `"google/gemini-2.5-flash"`); in `run_visual_qa`, when the project is regional-script, pass that model to `_vision_text` (thread a `model` param into `_vision_text`, defaulting to `VISION_QA_MODEL`). When regional OCR yields a Telugu glyph mismatch on a required fact, it already surfaces as `missing required visible fact` → correctable block → Task 4 loop biases to deterministic fallback (which has perfect Noto Telugu). Run → PASS; commit.

---

### Task 7: QR/barcode deterministic-composite guardrail

**Files:**
- Modify: `src/agents/flyer/render.py` (post-generation hook)
- Test: `tests/test_flyer_qr_guard.py` (new)

- [ ] **Step 1: Write test** — a project with a `qr`/`barcode` fact_id: assert the integrated path raises or routes so the machine-read element is composited deterministically (Slice 1: assert `_has_machine_read_fact(project)` forces deterministic-composite path; no-op assert when absent).

- [ ] **Step 2-4: Implement** minimal `_has_machine_read_fact(project)` (fact_id in {"qr","qr_code","barcode"}); when true, after generation composite the code deterministically (reuse overlay compositor). Run → PASS; commit.

---

### Task 8: Live smoke + rollout

- [ ] **Step 1:** Deploy to main-vps (tarball pattern); set `/opt/shift-agent/config.yaml` `flyer.draft_image_model` + `final_image_model` = `google/gemini-3.1-flash-image-preview`; ensure `FLYER_ALLOW_INTEGRATED_POSTER=1`; `FLYER_INTEGRATED_KILLSWITCH` unset.
- [ ] **Step 2:** One live WhatsApp flyer from the test sender (+17329837841) end-to-end; confirm integrated render delivered + `decisions.log` shows `flyer_integrated_outcome`.
- [ ] **Step 3:** Verify kill-switch: set `FLYER_INTEGRATED_KILLSWITCH=1`, regenerate, confirm deterministic output; unset.
- [ ] **Step 4:** Telugu spot-check: generate one Telugu menu; eyeball glyphs; confirm fallback fires if wrong.
- [ ] **Step 5:** Monitor `flyer_integrated_*` for the first week (fallback-rate, manual-review-rate, referee-unavailable count).

---

## Self-review

- **Spec coverage:** config+kill-switch (T5/T8) ✓; eligibility widen (T2) ✓; fabrication incl. non-dollar (T1) ✓; two-sided repair (T3) ✓; retry→fallback→manual + referee-unavailable QA note (T4) ✓; Telugu stronger-model + bias-fallback (T6) ✓; QR guard (T7) ✓; observability metrics (T4 print + T8 monitor) ✓; out-of-scope respected (no source-edit/reference-extraction changes) ✓.
- **Placeholder scan:** none — all code concrete; pre-flight names the one execution-time value to confirm (current prod model).
- **Type consistency:** `_fabricated_offer_price_blockers(project, extracted_text)` signature matches the `run_visual_qa` call site; blocker strings (`fabricated price visible: `, `fabricated offer claim visible: `) match the `_BLOCK_TIER_PATTERNS` regexes and the repair builder's prefixes; `build_repair_instruction(blockers, locked_values)` matches the orchestration call.
- **Risk note:** the orchestration code in `generate-flyer-concepts` is the highest-risk task — the script currently has a version-snapshot guard; preserve it (render outside the state lock, write inside). T4's test must cover the snapshot-mismatch path stays intact.
