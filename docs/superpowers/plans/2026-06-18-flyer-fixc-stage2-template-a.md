# Flyer Fix C — Stage 2 / Template A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat deterministic fallback renderer with a premium "Template A · Editorial" text-over-imagery renderer — the model owns the (textless) food imagery, a deterministic layer owns every fact — behind `FLYER_PREMIUM_OVERLAY`, with safety and fail-closed behavior unchanged.

**Architecture:** A new focused module `src/agents/flyer/premium_overlay.py` provides `render_premium_overlay(project, source, target, *, size, output_format)`. It reuses `render.collect_text_facts`, `render._split_item_price`, `render._wrap`, the bundled premium fonts, and raises `render.FlyerRenderError` on fit failure (same fail-closed contract as `apply_critical_text_overlay`). `render._apply_critical_text_overlay` gains a flag branch that delegates to it. The background-only prompt is extended with a fixed text-safe-zone contract. `visual_qa.run_visual_qa` is unchanged (defense-in-depth).

**Tech Stack:** Python 3.12, Pillow (already a dep), pytest. OFL fonts (Playfair Display, Cormorant Garamond, Montserrat) vendored under `src/agents/flyer/fonts/`.

**Scope (operator-approved v1):** Stage 2 only (fallback replacement, NOT primary path). Template A only (B/C deferred). Tier-1 fixed text-safe zones + gradient scrims (no saliency detection). Food/menu projects only; other categories keep current behavior.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/agents/flyer/premium_overlay.py` (new) | `render_premium_overlay` + Template A spec + `plan_premium_layout` solver + `_draw_offer_seal` + `_compose_scrims` + `_premium_font`. Imports shared helpers from `render` lazily to avoid a cycle. |
| `src/agents/flyer/fonts/` (new) | Vendored OFL TTFs + `FONTS.md` (source + license). |
| `src/agents/flyer/render.py` (modify ~2858) | `_apply_critical_text_overlay`: flag branch → `render_premium_overlay`. Extend background-only prompt with text-safe-zone contract (§ task 7). |
| `tests/test_flyer_premium_overlay.py` (new) | Unit + golden-structure + QA-integration + flag tests. |

**Why a new module:** `render.py` is ~3,800 lines; adding a premium typographic renderer there worsens an already-unwieldy file. `premium_overlay.py` is a focused unit with one entry point. It depends on `render` for shared low-level helpers; `render._apply_critical_text_overlay` imports `premium_overlay` **inside the function** (deferred import) so there is no import cycle.

---

## Task 1: Vendor premium fonts + role-based font loader

**Files:**
- Create: `src/agents/flyer/fonts/PlayfairDisplay-Bold.ttf`, `PlayfairDisplay-Black.ttf`, `CormorantGaramond-SemiBold.ttf`, `Montserrat-SemiBold.ttf`, `Montserrat-Bold.ttf`, `Montserrat-ExtraBold.ttf`, `FONTS.md`
- Create: `src/agents/flyer/premium_overlay.py`
- Test: `tests/test_flyer_premium_overlay.py`

- [ ] **Step 1: Vendor the OFL TTFs.** Download from Google Fonts (all OFL-licensed) into `src/agents/flyer/fonts/`. Record exact source URLs + "SIL OFL 1.1" in `FONTS.md`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_flyer_premium_overlay.py
from pathlib import Path
from agents.flyer import premium_overlay as po

def test_premium_font_roles_load():
    for role in ("masthead", "kicker", "title", "offer_price", "menu", "footer"):
        f = po._premium_font(role, 40)
        assert f is not None
        assert f.size == 40

def test_premium_font_falls_back_when_missing(monkeypatch):
    monkeypatch.setattr(po, "_FONT_DIR", Path("/nonexistent"))
    f = po._premium_font("title", 30)   # must not raise; falls back to PIL default/system
    assert f is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_flyer_premium_overlay.py -k font -v`
Expected: FAIL (`premium_overlay` not importable / `_premium_font` undefined).

- [ ] **Step 4: Implement the loader**

```python
# src/agents/flyer/premium_overlay.py
from __future__ import annotations
from pathlib import Path

_FONT_DIR = Path(__file__).resolve().parent / "fonts"
# role -> (preferred bundled file, bold-fallback for PIL default)
_ROLE_FILES = {
    "masthead":    "PlayfairDisplay-Bold.ttf",
    "kicker":      "Montserrat-Bold.ttf",
    "title":       "PlayfairDisplay-Black.ttf",
    "offer_price": "PlayfairDisplay-Black.ttf",
    "menu":        "CormorantGaramond-SemiBold.ttf",
    "footer":      "Montserrat-SemiBold.ttf",
}
# System fallbacks already used by render.py if the bundle is unavailable.
_SYS_FALLBACKS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

def _premium_font(role: str, size: int):
    from PIL import ImageFont
    candidates = []
    fn = _ROLE_FILES.get(role)
    if fn:
        candidates.append(_FONT_DIR / fn)
    candidates += [Path(p) for p in _SYS_FALLBACKS]
    for path in candidates:
        try:
            if path.exists():
                return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return ImageFont.load_default()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_flyer_premium_overlay.py -k font -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agents/flyer/fonts src/agents/flyer/premium_overlay.py tests/test_flyer_premium_overlay.py
git commit -m "feat(flyer): vendor OFL premium fonts + role-based loader for Fix C"
```

---

## Task 2: Layout solver (item-count → presentation, fail-closed)

**Files:**
- Modify: `src/agents/flyer/premium_overlay.py`
- Test: `tests/test_flyer_premium_overlay.py`

- [ ] **Step 1: Write the failing test**

```python
from agents.flyer.premium_overlay import plan_premium_layout, PremiumLayout

def _items(n, price="$7.99"):
    return [(f"Item{i}", price) for i in range(n)]

def test_layout_two_items_uses_combo():
    L = plan_premium_layout(_items(2), shared_price=None)
    assert L.menu_mode == "combo"

def test_layout_six_shared_price_uses_namerows_and_seal():
    L = plan_premium_layout(_items(6, ""), shared_price="$7.99")
    assert L.menu_mode == "name_rows"
    assert L.offer_mode == "seal"

def test_layout_six_distinct_prices_uses_two_col():
    L = plan_premium_layout([("Dosa","$8.99"),("Idli","$5.99"),("Vada","$5.49"),
                             ("Upma","$5.99"),("Bonda","$4.99"),("Pakora","$4.49")], shared_price=None)
    assert L.menu_mode == "two_col"

def test_layout_sixteen_items_compact_and_floor_enforced():
    L = plan_premium_layout(_items(16), shared_price=None)
    assert L.menu_mode == "two_col_compact"
    assert L.menu_font_px >= L.min_font_px   # never below the legibility floor
```

- [ ] **Step 2: Run** `python -m pytest tests/test_flyer_premium_overlay.py -k layout -v` → Expected: FAIL (undefined).

- [ ] **Step 3: Implement**

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class PremiumLayout:
    menu_mode: str        # "combo" | "name_rows" | "two_col" | "two_col_compact"
    offer_mode: str       # "seal" | "inline" | "none"
    menu_font_px: int
    min_font_px: int

def plan_premium_layout(items, *, shared_price, width: int = 1080) -> PremiumLayout:
    n = len(items)
    has_item_prices = any(p for _name, p in items)
    floor = max(20, int(width * 0.020))      # mobile legibility floor (~22px @1080)
    if n <= 2:
        mode = "combo"
    elif shared_price and not has_item_prices:
        mode = "name_rows"
    elif n <= 8:
        mode = "two_col"
    else:
        mode = "two_col_compact"
    base = {"combo": 0.040, "name_rows": 0.034, "two_col": 0.030, "two_col_compact": 0.022}[mode]
    font_px = max(floor, int(width * base))
    offer = "seal" if (shared_price and not has_item_prices) else ("inline" if has_item_prices else "none")
    return PremiumLayout(menu_mode=mode, offer_mode=offer, menu_font_px=font_px, min_font_px=floor)
```

- [ ] **Step 4: Run** the same pytest → Expected: PASS.
- [ ] **Step 5: Commit** `feat(flyer): premium layout solver (item-count → presentation, mobile floor)`

---

## Task 3: Gradient text-safe-zone scrims

**Files:** Modify `premium_overlay.py`; Test `tests/test_flyer_premium_overlay.py`

- [ ] **Step 1: Failing test**

```python
from PIL import Image
from agents.flyer.premium_overlay import compose_scrims

def test_scrims_preserve_size_and_darken_bands():
    base = Image.new("RGB", (1080, 1350), (180, 180, 180))
    out = compose_scrims(base, top_frac=0.22, bottom_frac=0.32)
    assert out.size == (1080, 1350)
    # top and bottom bands are darker than the untouched centre
    cx = out.getpixel((540, 675))
    top = out.getpixel((540, 20)); bot = out.getpixel((540, 1330))
    assert sum(top) < sum(cx) and sum(bot) < sum(cx)
```

- [ ] **Step 2: Run** `-k scrims` → FAIL.
- [ ] **Step 3: Implement**

```python
def compose_scrims(img, *, top_frac=0.22, bottom_frac=0.32):
    from PIL import Image
    img = img.convert("RGB")
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = overlay.load()
    top_h = int(h * top_frac); bot_start = int(h * (1 - bottom_frac))
    for y in range(top_h):                       # 0.82 -> 0 alpha
        a = int(209 * (1 - y / max(1, top_h)))
        for x in range(w):
            px[x, y] = (8, 4, 2, a)
    for y in range(bot_start, h):                # 0 -> 0.92 alpha
        a = int(235 * ((y - bot_start) / max(1, h - bot_start)))
        for x in range(w):
            px[x, y] = (8, 4, 2, a)
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
```

(Per-pixel loops are clear but slow at 1080×1350; if profiling shows it matters, replace with a 1-px-wide gradient `Image` stretched via `resize` — same output. Keep the simple version until measured.)

- [ ] **Step 4: Run** → PASS.  **Step 5: Commit** `feat(flyer): gradient text-safe-zone scrims`

---

## Task 4: Offer seal primitive (Template A)

**Files:** Modify `premium_overlay.py`; Test file.

- [ ] **Step 1: Failing test** — assert the seal is drawn within the offer zone and the price text is present.

```python
from PIL import Image, ImageDraw
from agents.flyer.premium_overlay import draw_offer_seal

def test_offer_seal_draws_in_zone():
    img = Image.new("RGB", (1080, 1350), (20, 20, 20))
    draw = ImageDraw.Draw(img, "RGBA")
    box = draw_offer_seal(draw, label="ANY ITEM", price="$7.99", width=1080,
                          center=(540, 760))
    assert box[0] >= 0 and box[2] <= 1080 and box[3] <= 1350
```

- [ ] **Step 2: Run** `-k offer_seal` → FAIL.
- [ ] **Step 3: Implement** a gold-bordered pill: rounded rectangle sized to the text, label (Montserrat) over price (Playfair Black), returns its bbox. (Mirror the existing badge code at `render.py:2272-2292` but with premium fonts + gold border `#ecc873`.)

```python
def draw_offer_seal(draw, *, label, price, width, center):
    lf = _premium_font("kicker", max(20, int(width * 0.022)))
    pf = _premium_font("offer_price", max(54, int(width * 0.072)))
    pl, pt, pr, pb = draw.textbbox((0, 0), price, font=pf)
    pw, ph = pr - pl, pb - pt
    pad = int(width * 0.03)
    bw, bh = pw + pad * 2, ph + int(width * 0.05)
    cx, cy = center
    x0, y0 = cx - bw // 2, cy - bh // 2
    x1, y1 = x0 + bw, y0 + bh
    draw.rounded_rectangle((x0+5, y0+5, x1+5, y1+5), radius=22, fill=(0, 0, 0, 90))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=22, fill=(20, 8, 4, 150), outline=(236, 200, 115, 255), width=3)
    ll, lt, lr, lb = draw.textbbox((0, 0), label, font=lf)
    draw.text((cx - (lr-ll)//2, y0 + int(bh*0.16)), label, font=lf, fill=(240, 226, 196, 255))
    draw.text((cx - pw//2, y1 - ph - int(bh*0.16)), price, font=pf, fill=(255, 233, 184, 255))
    return (x0, y0, x1, y1)
```

- [ ] **Step 4: Run** → PASS.  **Step 5: Commit** `feat(flyer): premium offer seal primitive`

---

## Task 5: `render_premium_overlay` — Template A end-to-end

**Files:** Modify `premium_overlay.py`; Test file.

- [ ] **Step 1: Failing tests**

```python
from pathlib import Path
from PIL import Image
from schemas import FlyerProject
from agents.flyer import premium_overlay as po, render

def _bg(tmp_path):
    p = tmp_path / "bg.png"; Image.new("RGB", (1080, 1350), (90, 50, 25)).save(p); return p

def _project6():
    facts = [{"fact_id":"business_name","label":"Business","value":"Lakshmi's Kitchen","required":True,"source":"customer_text"},
             {"fact_id":"campaign_title","label":"Campaign","value":"Weekend Specials","required":True,"source":"customer_text"},
             {"fact_id":"contact_phone","label":"Contact","value":"+17329837841","required":True,"source":"customer_text"},
             {"fact_id":"location","label":"Location","value":"90 Brybar Dr St Johns FL","required":True,"source":"customer_text"},
             {"fact_id":"pricing_structure","label":"Pricing","value":"Any item $7.99","required":True,"source":"customer_text"},
             {"fact_id":"schedule","label":"Schedule","value":"Saturday & Sunday, 4 PM-8 PM","required":True,"source":"customer_text"}]
    for i,n in enumerate(["Idli","Dosa","Vada","Uttapam","Pongal","Sambar"]):
        facts.append({"fact_id":f"item:{i}:name","label":"Item","value":n,"required":True,"source":"customer_text"})
    return FlyerProject.model_validate({"project_id":"F9001","status":"generating_concepts",
        "customer_phone":"+17329837841","customer_id":"CUST0001","created_at":"2026-06-18T00:00:00Z",
        "updated_at":"2026-06-18T00:00:00Z","original_message_id":"wamid.F9001","raw_request":"x",
        "fields":{"event_or_business_name":"Weekend Specials","preferred_language":"en"},"locked_facts":facts})

def test_render_premium_overlay_writes_image(tmp_path):
    out = tmp_path / "out.png"
    po.render_premium_overlay(_project6(), _bg(tmp_path), out, size=(1080,1350), output_format="concept_preview")
    assert out.exists()
    assert Image.open(out).size == (1080, 1350)

def test_render_premium_overlay_fail_closed_on_overflow(tmp_path, monkeypatch):
    # 40 long items can't fit above the floor -> must raise (never truncate)
    proj = _project6()
    import pytest
    monkeypatch.setattr(po, "_force_overflow_for_test", True, raising=False)
    with pytest.raises(render.FlyerRenderError):
        po.render_premium_overlay(proj, _bg(tmp_path), tmp_path/"o2.png",
                                  size=(1080,1350), output_format="concept_preview", _items_override=[("VeryLongDishNameNumber%d"%i,"$9.99") for i in range(40)])
```

- [ ] **Step 2: Run** `-k render_premium_overlay` → FAIL.
- [ ] **Step 3: Implement** `render_premium_overlay`. It: opens the background; `compose_scrims`; pulls facts via `render.collect_text_facts(project)` (brand/title/offer/menu/schedule/location/contact); computes the menu items via `render._split_item_price`; calls `plan_premium_layout`; draws — masthead (top zone, Playfair, gold rule + kicker), title (Playfair Black), offer (`draw_offer_seal` for `seal` mode), menu (mode-specific via `render._wrap` for wrapping), footer (Montserrat) — all within the scrimmed zones; **fail-closed**: if any required fact's rendered block would exceed its zone at the floor font, `raise render.FlyerRenderError("premium overlay does not fit")`. Save to `target`.

```python
def render_premium_overlay(project, source, target, *, size, output_format, _items_override=None):
    from PIL import Image, ImageDraw
    from agents.flyer import render
    W, H = size
    img = Image.open(source).convert("RGB").resize((W, H))
    img = compose_scrims(img)
    draw = ImageDraw.Draw(img, "RGBA")
    facts = {f.fact_id: f.text for f in render.collect_text_facts(project)}
    brand = facts.get("brand", ""); title = facts.get("title", "")
    items = _items_override if _items_override is not None else [
        render._split_item_price(i) for i in _menu_items(project)]
    shared = _shared_price(project)  # from pricing_structure / offer:0 when items share a price
    layout = plan_premium_layout(items, shared_price=shared, width=W)
    # --- top zone: kicker + gold rule + masthead (fail-closed wrap) ---
    drew = _draw_masthead(draw, brand, W, H)
    _draw_title(draw, title, W, H)
    if layout.offer_mode == "seal" and shared:
        draw_offer_seal(draw, label=_offer_label(project), price=shared, width=W, center=(int(W*0.5), int(H*0.62)))
    _draw_menu(draw, items, layout, W, H, shared=shared)
    _draw_footer(draw, facts, W, H)
    if not _all_required_drawn(project, drawn_regions=...):   # zone-budget check
        raise render.FlyerRenderError("premium overlay does not fit")
    img.convert("RGB").save(target)
```

(`_draw_masthead/_draw_title/_draw_menu/_draw_footer`, `_menu_items`, `_shared_price`, `_offer_label`, `_all_required_drawn` are small helpers in this module; each is a few lines mirroring the existing `apply_critical_text_overlay` regions but with premium fonts + zone math. Implement them as part of this task with their own micro-asserts: every `required=True` fact in `collect_text_facts` is drawn or the function raises.)

- [ ] **Step 4: Run** → PASS.  **Step 5: Commit** `feat(flyer): render_premium_overlay Template A (editorial), fail-closed`

---

## Task 6: Flag-gated integration into the render path

**Files:** Modify `src/agents/flyer/render.py` (`_apply_critical_text_overlay`, ~2858); Test file.

- [ ] **Step 1: Failing test**

```python
def test_flag_off_uses_legacy(monkeypatch, tmp_path):
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY", raising=False)
    calls = []
    monkeypatch.setattr("agents.flyer.premium_overlay.render_premium_overlay",
                        lambda *a, **k: calls.append("premium"))
    # legacy path still invoked, premium not called
    ...
    assert calls == []

def test_flag_on_food_project_uses_premium(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    calls = []
    monkeypatch.setattr("agents.flyer.premium_overlay.render_premium_overlay",
                        lambda *a, **k: (calls.append("premium"), Image.new("RGB",(1080,1350)).save(a[2]))[-1])
    render._apply_critical_text_overlay(_food_project(), _bg(tmp_path), tmp_path/"o.png",
                                        size=(1080,1350), output_format="concept_preview")
    assert calls == ["premium"]
```

- [ ] **Step 2: Run** `-k flag` → FAIL.
- [ ] **Step 3: Implement the branch** in `render._apply_critical_text_overlay`:

```python
def _apply_critical_text_overlay(project, source, target, *, size, output_format):
    if os.environ.get("FLYER_PREMIUM_OVERLAY") == "1" and _is_food_or_grocery_project(project):
        try:
            from agents.flyer import premium_overlay  # deferred import (no cycle)
            premium_overlay.render_premium_overlay(project, source, target, size=size, output_format=output_format)
            return
        except FlyerRenderError:
            raise                      # fail-closed -> manual (same as legacy)
    apply_critical_text_overlay(project, source, target, size=size, output_format=output_format)
```

(Flag OFF ⇒ byte-identical to today. `FlyerRenderError` still routes to manual via the existing funnel — no behavior change to safety.)

- [ ] **Step 4: Run** → PASS.  **Step 5: Commit** `feat(flyer): FLYER_PREMIUM_OVERLAY flag wires render_premium_overlay into fallback`

---

## Task 7: Background text-safe-zone prompt contract

**Files:** Modify `src/agents/flyer/render.py` (background-only branch of `_image_prompt`, ~1939); Test `tests/test_flyer_renderer.py`.

- [ ] **Step 1: Failing test** — when `FLYER_PREMIUM_OVERLAY=1`, the background-only prompt instructs explicit calm top/bottom zones.

```python
def test_background_prompt_has_textsafe_zones(monkeypatch):
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    p = render._image_prompt(_food_bg_project(), concept_id="C1", output_format="concept_preview", size=(1080,1350))
    assert "top" in p.lower() and "bottom" in p.lower()
    assert "calm" in p.lower() or "uncluttered" in p.lower()
    assert "hero" in p.lower()  # hero food centered
```

- [ ] **Step 2: Run** → FAIL (current text differs).
- [ ] **Step 3: Implement** — extend the existing background-only `text_contract_line` (render.py ~1940) to add: "Keep the top ~22% and bottom ~32% darker and visually calm/uncluttered for text overlay; place the hero food in the centre band." Gate the stronger wording on the flag so flag-off output is unchanged.
- [ ] **Step 4: Run** → PASS.  **Step 5: Commit** `feat(flyer): text-safe-zone background prompt contract under FLYER_PREMIUM_OVERLAY`

---

## Task 8: QA-integration + premium-delivery measurement

**Files:** Test `tests/test_flyer_premium_overlay.py`; reuse `slice2_pilot.py` (eval-only, not shipped).

- [ ] **Step 1: QA-integration test** — render Template A over a plain background, run the real referee, assert **0 blockers** (text is exact by construction). Uses the offline OCR only if `OPENROUTER_API_KEY` is set; otherwise `pytest.skip` (no network in CI).

```python
import os, pytest
from agents.flyer.visual_qa import run_visual_qa

@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="needs vision QA")
def test_premium_overlay_passes_referee(tmp_path):
    out = tmp_path / "qa.png"
    po.render_premium_overlay(_project6(), _bg(tmp_path), out, size=(1080,1350), output_format="concept_preview")
    rep = run_visual_qa(_project6(), out, output_format="concept_preview")
    assert rep.blockers == []
```

- [ ] **Step 2: Run** locally with the key (on the box) → PASS; CI skips.
- [ ] **Step 3: Premium-delivery measurement (manual, gated):** run `slice2_pilot.py` with `FLYER_PREMIUM_OVERLAY=1` for the treatment arm to measure premium-delivery % vs the current flat fallback on the brief set. Record results in the PR. (Spend-gated; operator authorizes.)
- [ ] **Step 4: Commit** `test(flyer): premium overlay referee-pass + premium-delivery harness hook`

---

## Task 9: Deploy artifact wiring (fonts ship to the VPS)

**Files:** Modify `src/agents/shift/scripts/shift-agent-deploy.sh` install list (and/or `tools/build-deploy-tarball.sh`) to include `src/agents/flyer/fonts/`.

- [ ] **Step 1:** Confirm `render.py`/`premium_overlay.py` deploy as `flyer_render.py`/`flyer_premium_overlay.py` and the `fonts/` dir is copied alongside (the loader resolves `_FONT_DIR` relative to the module — verify the deployed layout keeps fonts next to the module, else set an absolute deployed path).
- [ ] **Step 2:** Add a deploy smoke assertion: a font file exists at the resolved `_FONT_DIR` on the box.
- [ ] **Step 3: Commit** `build(flyer): ship vendored premium fonts in deploy artifact`

---

## Self-Review

**Spec coverage:** §1 text-safe zones → Tasks 3,7. §2 typography → Tasks 1,5. §3 dynamic layout → Task 2 (+menu draw in 5). §4 offer treatments → Task 4 (seal; inline/flash deferred with B/C). §5 mobile-first → Task 2 floor + Task 8 referee. §6 overlay×imagery → Tasks 3,5. §7 migration Stage 2 → Task 6 (flag into fallback slot only; Stage 3 primary explicitly out of scope). Safety/fail-closed → Tasks 5,6. Fonts-on-VPS → Task 9.

**Deferred (by operator scope, not gaps):** Templates B/C, inline/flash offer modes, saliency (Tier-2), Stage 3 primary-path swap, non-food categories.

**Placeholder scan:** the `...` in Task 5 (`_draw_*` helpers) and Task 6 (legacy-path assertion) are the only abbreviations — Task 5 explicitly enumerates the helpers to implement with their micro-asserts; the implementer writes them mirroring `apply_critical_text_overlay` regions. Flag this as the one place needing concrete drawing code during implementation (tuned against golden snapshots).

**Type consistency:** `PremiumLayout` fields (`menu_mode`, `offer_mode`, `menu_font_px`, `min_font_px`) used consistently in Tasks 2 and 5. `render_premium_overlay(project, source, target, *, size, output_format)` matches `_apply_critical_text_overlay`'s signature (Task 6 call site).

**Note on visual fidelity:** unit/golden tests pin *structure and safety* (all facts drawn, fail-closed, referee-clean, zones respected). The *premium look* is validated against the approved mockups via golden snapshots + the premium-delivery harness — typography/spacing constants in Tasks 4–5 will be tuned during implementation, which is expected for a visual renderer.
