# Flyer Fix C v2 — Editorial Luxury — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use `- [ ]` tracking.

**Goal:** Upgrade the existing deterministic premium overlay + the food background prompt so the Fix C path renders the approved "Editorial Luxury" flyer (mockup A) instead of the current plain output (F0175), with exact text still guaranteed.

**Architecture:** Two in-place upgrades behind the *existing* `FLYER_PREMIUM_OVERLAY` flag — (W1) the background-only image prompt requests a food-hero (no people); (W2) `render_premium_overlay`'s zone drawing is restyled to mockup A (emblem lockup, gold rules, prominent gold seal, dot-leader menu, stronger scrims). The `ink` log + required-fact coverage + fail-closed ladder are **preserved unchanged**.

**Tech Stack:** Python 3, Pillow + vendored OFL fonts (Playfair Display, Cormorant Garamond, Montserrat), pytest. Repo layout `src/agents/flyer/`. Authoritative source = `origin/main` (`25d336f`).

**Design doc:** `docs/superpowers/specs/2026-06-19-flyer-fixc-v2-editorial-luxury-design.md`
**Visual reference (committed):** `docs/superpowers/fixc-v2-reference/fixc-v2-A-editorial-luxury-approved.png` (target), `fixc-v2-mockup-generator.py` (`compose_A` = the styling reference: exact emblem/rules/seal/dot-leader constants), `f0175-current-premium-baseline.png` (what we're replacing).
**Drift-check tag:** `extends-Hermes`.

**Test command (Windows git-bash):** `PYTHONPATH="src;src/platform" python -m pytest <path> -v`

---

## Invariants every W2 task MUST preserve (do not regress)
- `render_premium_overlay` keeps building `payload = render._menu_overlay_payload(project)`, the `required_facts` ledger, the `ink` log (`_ink(text)` for EVERY drawn string), and the final `_covered(...)` fail-closed check (raise `render.FlyerRenderError` if any required fact isn't covered or can't fit ≥ `min_px`). Restyling changes *how* text is drawn, never *whether* drawn text is `_ink`'d or whether coverage runs.
- Flag off ⇒ `render_premium_overlay` never runs ⇒ flat overlay path byte-identical.
- No new flag; no schema/state change.

## File structure
| File | Change |
|---|---|
| `src/agents/flyer/render.py` | W1: food-hero directive in the background-only prompt block (`_poster_layout_requirements` / `_poster_copy_block` background-only branch). |
| `src/agents/flyer/premium_overlay.py` | W2: restyle the top/title/offer/menu/scrim drawing in `render_premium_overlay` + `draw_offer_seal` + `compose_scrims` + `_draw_gold_rule`. |
| `tests/test_flyer_premium_overlay.py` | coverage/fail-closed/dynamic-count/visual tests. |
| `tests/test_flyer_renderer.py` | W1 prompt test. |

---

## Task 1: W1 — food-hero background prompt (no people)

**Files:** Modify `src/agents/flyer/render.py` (the background-only prompt branch — `_poster_layout_requirements` ~line 1183 and/or `_poster_copy_block` ~1004, whichever emits the textless background contract); Test `tests/test_flyer_renderer.py`.

- [ ] **Step 1: Write the failing test**
```python
def test_background_only_prompt_requests_food_hero_no_people(monkeypatch):
    from agents.flyer import render as r
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    p = _f0174_integrated_project()  # existing helper in this file
    tok = r._FORCE_BACKGROUND_ONLY.set(True)
    try:
        prompt = r.build_image_generation_prompt(p, concept_id="C1", output_format="concept_preview", size=(1080, 1350), force_background_only=True)
    finally:
        r._FORCE_BACKGROUND_ONLY.reset(tok)
    low = prompt.lower()
    assert "no people" in low and ("no faces" in low or "no hands" in low)
    assert "close-up" in low or "hero" in low
    assert "do not draw any text" in low or "do not render" in low  # text guarantee retained
```

- [ ] **Step 2: Run it — expect FAIL** (`no people` / `hero` not present).
`PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py::test_background_only_prompt_requests_food_hero_no_people -v`

- [ ] **Step 3: Implement** — in the background-only branch of the prompt builder (where `_background_only_eligible(project)` is true), add a food-hero directive to the existing reserve contract. Keep the existing "do NOT draw any text…" line. Add:
```python
            "- Compose a FOOD-HERO background: one large, appetizing close-up of the dish(es) "
            "as the clear subject, with dramatic appetizing lighting and rich depth of field on an "
            "elegant surface. Do NOT show people, faces, hands, diners, a family, a buffet, or a "
            "generic restaurant scene — the food itself is the hero.\n"
```
Insert it adjacent to the existing reserved-zone line so both apply on the background-only path. (See `compose_A`'s `BG["A"]` prompt for the validated wording.)

- [ ] **Step 4: Run it — expect PASS.** Then full prompt-path no-regression: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -q`

- [ ] **Step 5: Commit** — `git add src/agents/flyer/render.py tests/test_flyer_renderer.py` / `git commit -m "feat(flyer): food-hero background directive (no people) on the background-only prompt"` (+ Co-Authored-By trailer).

---

## Task 2: W2a — brand lockup (emblem + small-caps serif)

**Files:** Modify `src/agents/flyer/premium_overlay.py` (the top-zone drawing in `render_premium_overlay`); Test `tests/test_flyer_premium_overlay.py`.

Replace the current flat-band kicker+masthead with: an emblem ring + monogram (brand initials) above the brand name in Cormorant small-caps, over the top scrim. Port the geometry from `compose_A` (the `LK` ring + `center_spaced(BRAND.upper(), Cormorant 34, +6)`), deriving the monogram from `business` initials. **`_ink(business)` must still be called** so coverage sees the brand.

- [ ] **Step 1: Write the failing test** (brand still covered + a monogram helper)
```python
def test_brand_monogram_from_business():
    from agents.flyer import premium_overlay as po
    assert po._brand_monogram("Lakshmi's Kitchen") == "LK"
    assert po._brand_monogram("Dosa") == "D"
```
- [ ] **Step 2: Run — expect FAIL** (`_brand_monogram` missing).
- [ ] **Step 3: Implement** `_brand_monogram` + restyle the top zone (emblem ring via `draw.ellipse`, monogram via `_premium_font("title", …)`, brand via Cormorant small-caps with letter-spacing). Keep `_ink(business)`.
```python
def _brand_monogram(business: str) -> str:
    words = [w for w in re.sub(r"[^A-Za-z ]", " ", business).split() if w]
    if not words:
        return (business.strip()[:1] or "·").upper()
    return "".join(w[0] for w in words[:2]).upper()
```
(Add `import re` if absent.) In `render_premium_overlay`'s top zone draw the emblem + brand; reference `compose_A` for ring radius/positions.
- [ ] **Step 4: Run the test + the existing coverage suite** to prove brand still covered + nothing regressed:
`PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_premium_overlay.py -q`
- [ ] **Step 5: Commit** `feat(flyer): editorial brand lockup (emblem + small-caps) in premium overlay`.

---

## Task 3: W2b — title with decorative gold rules

**Files:** Modify `src/agents/flyer/premium_overlay.py` (`_draw_gold_rule` + title draw in `render_premium_overlay`); Test `tests/test_flyer_premium_overlay.py`.

Draw the campaign title in Playfair with a centered gold rule + center dot flanking it (port `compose_A`'s two `d.line(...)` + center `d.ellipse(...)`). Title text still `_ink`'d.

- [ ] **Step 1: Failing test** — render to a temp file for a 6-item project over a solid test bg and assert no exception + title covered:
```python
def test_title_renders_and_is_covered(tmp_path):
    from agents.flyer import premium_overlay as po
    src = _solid_bg(tmp_path)          # helper: a 1080x1350 solid PNG
    proj = _premium_project_6item()    # helper: brand/title/schedule/contact/location + 6 items, shared $7.99
    out = tmp_path / "o.png"
    po.render_premium_overlay(proj, src, out, size=(1080,1350), output_format="concept_preview")
    assert out.exists()
```
(If `_solid_bg`/`_premium_project_6item` helpers don't exist, add them mirroring the existing premium-overlay tests' fixtures.)
- [ ] **Step 2: Run — establish current pass/behavior** (this is a characterization test; it should pass before + after — its job is to guard that the title restyle doesn't break rendering/coverage).
- [ ] **Step 3: Implement** the rules + title styling per `compose_A`.
- [ ] **Step 4: Run** `tests/test_flyer_premium_overlay.py -q` — all pass.
- [ ] **Step 5: Commit** `feat(flyer): title with decorative gold rules`.

---

## Task 4: W2c — prominent gold offer seal

**Files:** Modify `src/agents/flyer/premium_overlay.py` (`draw_offer_seal` + its placement in `render_premium_overlay`); Test `tests/test_flyer_premium_overlay.py`.

Restyle `draw_offer_seal` to the maroon+gold circular seal (`ANY ITEM` / `$7.99` / `EACH`) from `compose_A`, sized from label+price (the existing `_seal_geometry` already sizes it — keep that), placed as a real focal element. The seal text (`shared_offer_label`, `shared_offer_price`) must still be `_ink`'d so the offer fact is covered.

- [ ] **Step 1: Failing test** — offer covered after render (extend the Task-3 characterization render to assert the shared offer price is in the ink/coverage). Add an assertion via a small hook: render, then check the project's offer value is covered (reuse the render; assert no FlyerRenderError for a shared-price project — coverage failure would raise).
```python
def test_shared_offer_seal_covered(tmp_path):
    from agents.flyer import premium_overlay as po
    proj = _premium_project_6item()   # shared $7.99 offer
    po.render_premium_overlay(proj, _solid_bg(tmp_path), tmp_path/"o.png", size=(1080,1350), output_format="concept_preview")
    # no FlyerRenderError == offer (and all required facts) covered
```
- [ ] **Step 2: Run — should pass before+after** (characterization guard).
- [ ] **Step 3: Implement** the seal restyle (maroon fill + gold ring + 3 lines) per `compose_A.draw_offer_seal` geometry; keep `_ink` of label+price.
- [ ] **Step 4: Run** `tests/test_flyer_premium_overlay.py -q`.
- [ ] **Step 5: Commit** `feat(flyer): prominent gold offer seal`.

---

## Task 5: W2d — dot-leader two-column menu (no flat rows)

**Files:** Modify `src/agents/flyer/premium_overlay.py` (`_plan_menu_block` / the menu drawing in `render_premium_overlay`); Test `tests/test_flyer_premium_overlay.py`.

Restyle the menu rows to a 2-column dot-leader list (`Idli · · · · $7.99`) per `compose_A`: Cormorant name (left), Playfair gold price (right), gold dot leaders between, per column. **Each rendered row's name AND price must be `_ink`'d** (the coverage check pairs item name+price via `visual_qa._item_price_pair_blockers`, so the drawn priced row text must contain both). Preserve the existing `plan_premium_layout` mode logic (combo/name_rows/two_col/two_col_compact) — only the visual rendering changes, plus the dot leaders.

- [ ] **Step 1: Failing test** — dynamic counts render or fail-closed, and per-item coverage holds:
```python
import pytest
@pytest.mark.parametrize("n", [2, 6, 16])
def test_menu_dynamic_counts_cover_or_failclose(tmp_path, n):
    from agents.flyer import premium_overlay as po
    from agents.flyer import render as r
    proj = _premium_project_n_items(n)   # helper: n items each with locked name+price
    try:
        po.render_premium_overlay(proj, _solid_bg(tmp_path), tmp_path/f"o{n}.png", size=(1080,1350), output_format="concept_preview")
    except r.FlyerRenderError:
        # acceptable ONLY as fail-closed (e.g. 16 can't fit) — never a silent drop
        return
    # if it rendered, all items must have been covered (else it would have raised)
    assert (tmp_path/f"o{n}.png").exists()
```
- [ ] **Step 2: Run** — verify 2 & 6 render (pass), 16 either renders or raises (never drops).
- [ ] **Step 3: Implement** the dot-leader rendering (port `compose_A`'s column loop: name, right-aligned price, dot-leader fill between; `_ink(f"{name} {price}")` per row so pairing coverage sees both). Keep the min-font floor + fail-closed.
- [ ] **Step 4: Run** `tests/test_flyer_premium_overlay.py -q` — all pass.
- [ ] **Step 5: Commit** `feat(flyer): dot-leader two-column editorial menu`.

---

## Task 6: W2e — stronger scrims + composition + visual golden

**Files:** Modify `src/agents/flyer/premium_overlay.py` (`compose_scrims` top/bottom fractions + final zone spacing); Test `tests/test_flyer_premium_overlay.py` + a committed golden.

Strengthen the top/bottom gradient scrims so text reads on the food (per `compose_A`'s `scrim(...)` opacities), and tune zone spacing to the mockup A hierarchy.

- [ ] **Step 1: Generate the golden** — render the F0175 brief over the committed `docs/superpowers/fixc-v2-reference/fixc-v2-A-hero-bg.png` and save it as `tests/goldens/fixc-v2-A-render.png`. Visually confirm it matches `fixc-v2-A-editorial-luxury-approved.png` (emblem, rules, seal, dot-leader menu, food showing through). This is a manual visual gate.
- [ ] **Step 2: Add a structural golden test** (size + that it renders without fail-closed for the canonical brief):
```python
def test_fixc_v2_canonical_render(tmp_path):
    from agents.flyer import premium_overlay as po
    bg = "docs/superpowers/fixc-v2-reference/fixc-v2-A-hero-bg.png"
    proj = _premium_project_6item()
    out = tmp_path/"v2.png"
    po.render_premium_overlay(proj, bg, out, size=(1080,1350), output_format="concept_preview")
    from PIL import Image
    im = Image.open(out); assert im.size == (1080, 1350)
```
- [ ] **Step 3: Implement** scrim/spacing tuning.
- [ ] **Step 4: Run** `tests/test_flyer_premium_overlay.py -q` + re-render the golden + manual visual compare to mockup A.
- [ ] **Step 5: Commit** `feat(flyer): stronger scrims + editorial composition (matches mockup A)` (+ the golden).

---

## Task 7: Integration — end-to-end, fail-closed, flag-off, full suite

**Files:** Test-only + verification.

- [ ] **Step 1: Exact-text coverage unchanged** — run the existing v1 coverage tests (Vada≠Vadai, $7.99≠$7.999, missing-fact fail-closed, item price pairing). All must still pass: `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_premium_overlay.py -q`.
- [ ] **Step 2: Fail-closed ladder** — add/confirm a test where a required fact cannot fit (e.g. an absurdly long brand) raises `FlyerRenderError` (→ flat → manual). Never silently drops.
- [ ] **Step 3: Flag-off byte-identical** — confirm with `FLYER_PREMIUM_OVERLAY` unset the flat overlay path is unaffected (existing Task-6 flag tests in `tests/test_flyer_premium_overlay.py`).
- [ ] **Step 4: Full suite** — `PYTHONPATH="src;src/platform" python -m pytest tests/ -q` → 0 failed.
- [ ] **Step 5: Final review** — dispatch a code reviewer + run Codex over the branch diff (focus: coverage/fail-closed unchanged, flag-off byte-identical, no model-text reintroduced, visual fidelity to mockup A). Address findings.
- [ ] **Step 6: Commit** any review fixes.

---

## Self-Review (writing-plans)
**Spec coverage:** W1→T1; W2 brand→T2, title/rules→T3, seal→T4, menu→T5, scrims/composition→T6; constraints (coverage/fail-closed/flag-off/dynamic counts)→T5+T7; visual fidelity→T6 golden. ✓
**Placeholder scan:** test fixtures (`_solid_bg`, `_premium_project_6item`, `_premium_project_n_items`) are named with explicit "mirror existing fixtures" instructions; `compose_A` is the committed concrete styling reference (not a placeholder). ✓
**Type consistency:** `_brand_monogram`, `render_premium_overlay`, `draw_offer_seal`, `compose_scrims`, `_FORCE_BACKGROUND_ONLY` consistent across tasks. ✓

## Out of scope (deferred)
B (Social Promo) and C (Modern Brand) templates; template selection/owner-style/auto-pick; any model-rendered text; broader rollout (stays scoped to `+17329837841` behind the existing flag).

## Post-build (operator-gated)
PR → CI + Codex → merge → deploy (scoped flag already on) → re-send F0175 + combo + dessert → judge vs mockup A and "would an owner post this?".
