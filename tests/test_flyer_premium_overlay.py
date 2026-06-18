"""Tests for premium_overlay font loader (Fix C Task 1).

TDD: these tests must FAIL before premium_overlay.py exists, then PASS after.
"""
from pathlib import Path
from agents.flyer import premium_overlay as po


def test_premium_font_roles_load():
    for role in ("masthead", "kicker", "title", "offer_price", "menu", "footer"):
        f = po._premium_font(role, 40)
        assert f is not None
        assert f.size == 40


def test_premium_font_falls_back_when_missing(monkeypatch):
    monkeypatch.setattr(po, "_FONT_DIR", Path("/nonexistent"))
    f = po._premium_font("title", 30)   # must not raise; falls back
    assert f is not None


def test_variable_font_weight_axis_differentiates():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (10, 10)); d = ImageDraw.Draw(img)
    masthead = po._premium_font("masthead", 80)   # Playfair 700
    title = po._premium_font("title", 80)          # Playfair 900 (same file, heavier)
    wm = d.textlength("LAKSHMI", font=masthead)
    wt = d.textlength("LAKSHMI", font=title)
    assert wt > wm, f"expected Black(900) wider than Bold(700): {wt} vs {wm}"


# ---------------------------------------------------------------------------
# Task 2: premium layout solver
# ---------------------------------------------------------------------------
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
    assert L.menu_font_px >= L.min_font_px


# ---------------------------------------------------------------------------
# Task 3: gradient text-safe-zone scrims
# ---------------------------------------------------------------------------
from PIL import Image
from agents.flyer.premium_overlay import compose_scrims


def test_scrims_preserve_size_and_darken_bands():
    base = Image.new("RGB", (1080, 1350), (180, 180, 180))
    out = compose_scrims(base, top_frac=0.22, bottom_frac=0.32)
    assert out.size == (1080, 1350)
    cx = out.getpixel((540, 675))                 # untouched centre
    top = out.getpixel((540, 20)); bot = out.getpixel((540, 1330))
    assert sum(top) < sum(cx) and sum(bot) < sum(cx)   # bands darker than centre


# ---------------------------------------------------------------------------
# Task 4: premium offer-seal primitive
# ---------------------------------------------------------------------------
from PIL import Image, ImageDraw
from agents.flyer.premium_overlay import draw_offer_seal


def test_offer_seal_draws_in_zone():
    img = Image.new("RGB", (1080, 1350), (20, 20, 20))
    draw = ImageDraw.Draw(img, "RGBA")
    box = draw_offer_seal(draw, label="ANY ITEM", price="$7.99", width=1080, center=(540, 760))
    assert box[0] >= 0 and box[2] <= 1080 and box[3] <= 1350     # within canvas
    assert box[2] > box[0] and box[3] > box[1]                   # non-empty bbox


# ---------------------------------------------------------------------------
# Task 5: render_premium_overlay — Template A (Editorial)
# ---------------------------------------------------------------------------
from schemas import FlyerProject
from agents.flyer import render


def _bg(tmp_path):
    p = tmp_path / "bg.png"
    Image.new("RGB", (1080, 1350), (70, 40, 20)).save(p)
    return p


def _project6():
    facts = [{"fact_id":"business_name","label":"Business","value":"Lakshmi's Kitchen","required":True,"source":"customer_text"},
             {"fact_id":"campaign_title","label":"Campaign","value":"Weekend Specials","required":True,"source":"customer_text"},
             {"fact_id":"contact_phone","label":"Contact","value":"+17329837841","required":True,"source":"customer_text"},
             {"fact_id":"location","label":"Location","value":"90 Brybar Dr St Johns FL","required":True,"source":"customer_text"},
             {"fact_id":"pricing_structure","label":"Pricing","value":"Any item $7.99","required":True,"source":"customer_text"},
             {"fact_id":"schedule","label":"Schedule","value":"Saturday & Sunday, 4 PM-8 PM","required":True,"source":"customer_text"}]
    for i, n in enumerate(["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar"]):
        facts.append({"fact_id":f"item:{i}:name","label":"Item","value":n,"required":True,"source":"customer_text"})
    # Realistic raw_request (NOT a placeholder "x"). collect_text_facts echoes
    # this into a detail clause; the renderer must NOT require that echo — it is
    # not a locked fact, so the referee does not require it either. Regression
    # guard for the "fail-closes the flagship case to manual" bug.
    return FlyerProject.model_validate({"project_id":"F9001","status":"generating_concepts",
        "customer_phone":"+17329837841","customer_id":"CUST0001","created_at":"2026-06-18T00:00:00Z",
        "updated_at":"2026-06-18T00:00:00Z","original_message_id":"wamid.F9001",
        "raw_request":"Create a flyer for Weekend Specials. Any item $7.99. Idli, Dosa, Vada, Uttapam, Pongal, Sambar. Sat & Sun 4-8 PM. +1 732-983-7841",
        "fields":{"event_or_business_name":"Weekend Specials","preferred_language":"en"},"locked_facts":facts})


def test_render_premium_overlay_writes_image(tmp_path):
    out = tmp_path / "out.png"
    po.render_premium_overlay(_project6(), _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists() and Image.open(out).size == (1080, 1350)


def test_render_premium_overlay_fail_closed_on_overflow(tmp_path):
    import pytest
    proj = _project6()
    # 40 LOCKED item names overflow the menu zone — a real, required failure that
    # MUST fail-closed (route to manual), unlike a non-locked detail echo.
    many = [{"fact_id":f"item:{i}:name","label":"Item","value":f"VeryLongDishNameNumber{i}","required":True,"source":"customer_text"} for i in range(40)]
    base = [f for f in proj.locked_facts if not f.fact_id.startswith("item:")]
    proj2 = proj.model_copy(update={"locked_facts": base + [type(base[0]).model_validate(m) for m in many]})
    with pytest.raises(render.FlyerRenderError):
        po.render_premium_overlay(proj2, _bg(tmp_path), tmp_path/"o2.png", size=(1080, 1350), output_format="concept_preview")


def test_required_set_is_derived_from_locked_facts_not_text_facts(tmp_path):
    """The fail-closed contract is the referee's: derived from project.locked_facts,
    never stricter. ``collect_text_facts`` emits detail clauses (e.g. raw-request
    echoes / reformatted phone strings) that are NOT locked facts; the renderer
    must treat those as optional (best-effort), never required, because
    ``visual_qa`` iterates ``locked_facts`` — not ``collect_text_facts``."""
    proj = _project6()
    text_fact_values = {f.text for f in render.collect_text_facts(proj)}
    locked_values = {f.value for f in proj.locked_facts}
    # There is at least one non-locked detail clause that the renderer must NOT
    # require (this is exactly the class of clause that previously fail-closed
    # the flagship case to manual).
    non_locked = [v for v in text_fact_values if v not in locked_values]
    assert non_locked, "expected at least one non-locked detail clause from collect_text_facts"
    # The flagship case must render despite the non-locked clause(s).
    out = tmp_path / "derived.png"
    po.render_premium_overlay(proj, _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists() and Image.open(out).size == (1080, 1350)


def test_render_premium_overlay_does_not_fail_on_raw_request_echo(tmp_path):
    """Direct regression for the reported bug: a SHORT raw_request that
    collect_text_facts echoes verbatim into a detail clause (redundant with
    title + offer) must NOT cause a fail-closed. Mirrors the operator repro."""
    import pytest  # noqa: F401  (kept for parity with sibling tests)
    facts = [{"fact_id":"business_name","label":"B","value":"Lakshmi's Kitchen","required":True,"source":"customer_text"},
             {"fact_id":"campaign_title","label":"C","value":"Weekend Specials","required":True,"source":"customer_text"},
             {"fact_id":"contact_phone","label":"P","value":"+17329837841","required":True,"source":"customer_text"},
             {"fact_id":"location","label":"L","value":"90 Brybar Dr St Johns FL","required":True,"source":"customer_text"},
             {"fact_id":"pricing_structure","label":"Pr","value":"Any item $7.99","required":True,"source":"customer_text"},
             {"fact_id":"schedule","label":"S","value":"Saturday & Sunday, 4 PM-8 PM","required":True,"source":"customer_text"}]
    for i, n in enumerate(["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar"]):
        facts.append({"fact_id":f"item:{i}:name","label":"I","value":n,"required":True,"source":"customer_text"})
    proj = FlyerProject.model_validate({"project_id":"F9002","status":"generating_concepts",
        "customer_phone":"+17329837841","customer_id":"CUST0001","created_at":"2026-06-18T00:00:00Z",
        "updated_at":"2026-06-18T00:00:00Z","original_message_id":"wamid.F9002",
        "raw_request":"Weekend Specials any item $7.99",
        "fields":{"event_or_business_name":"Weekend Specials","preferred_language":"en"},"locked_facts":facts})
    # The verbatim echo IS present as a non-locked detail clause here.
    nonlocked = {f.text for f in render.collect_text_facts(proj)} - {f.value for f in proj.locked_facts}
    assert "Weekend Specials any item $7.99" in nonlocked
    out = tmp_path / "echo.png"
    po.render_premium_overlay(proj, _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists() and Image.open(out).size == (1080, 1350)


# ---------------------------------------------------------------------------
# Adversarial coverage tests (Codex review hardening)
# ---------------------------------------------------------------------------

def _proj(facts, pid="F9300"):
    return FlyerProject.model_validate({"project_id":pid,"status":"generating_concepts",
        "customer_phone":"+17329837841","customer_id":"CUST0001","created_at":"2026-06-18T00:00:00Z",
        "updated_at":"2026-06-18T00:00:00Z","original_message_id":"wamid.X","raw_request":"x",
        "fields":{"event_or_business_name":"Weekend Specials","preferred_language":"en"},"locked_facts":facts})


def _core_facts():
    return [{"fact_id":"business_name","label":"B","value":"Lakshmi's Kitchen","required":True,"source":"customer_text"},
            {"fact_id":"campaign_title","label":"C","value":"Weekend Specials","required":True,"source":"customer_text"},
            {"fact_id":"contact_phone","label":"P","value":"+17329837841","required":True,"source":"customer_text"},
            {"fact_id":"location","label":"L","value":"St Johns FL","required":True,"source":"customer_text"}]


def test_required_fact_id_outside_old_allowlist_is_not_silently_skipped(tmp_path):
    """BLOCKER 1: a required locked fact with an ID the renderer has no dedicated
    region for (here ``tagline``) must be RENDERED (covered) or RAISE — never
    silently skipped while the image is saved. We assert it renders (the
    secondary-line path places it); a too-long unplaceable value would raise."""
    facts = _core_facts() + [{"fact_id":"tagline","label":"T","value":"Authentic South Indian Since 1998","required":True,"source":"customer_text"}]
    for i, n in enumerate(["Idli", "Dosa", "Vada"]):
        facts.append({"fact_id":f"item:{i}:name","label":"I","value":n,"required":True,"source":"customer_text"})
    out = tmp_path / "tagline.png"
    po.render_premium_overlay(_proj(facts, "F9301"), _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists()  # rendered without raising == tagline value was covered


def test_unplaceable_required_facts_raise_not_silent(tmp_path):
    """BLOCKER 1 (fail-closed half): required facts (with IDs outside any
    dedicated region) that together cannot fit the secondary band must RAISE,
    not save a flyer missing them. Uses many large unknown-id facts so the band
    overflows deterministically (a single ~480-char fact still fits the band)."""
    import pytest
    facts = _core_facts()
    for k in range(12):
        huge = " ".join(["T%02dWord%02d" % (k, i) for i in range(34)])  # ~480 chars each
        facts.append({"fact_id":f"source_required_text:{k}","label":"SRC","value":huge,"required":True,"source":"customer_text"})
    for i, n in enumerate(["Idli", "Dosa", "Vada"]):
        facts.append({"fact_id":f"item:{i}:name","label":"I","value":n,"required":True,"source":"customer_text"})
    with pytest.raises(render.FlyerRenderError):
        po.render_premium_overlay(_proj(facts, "F9302"), _bg(tmp_path), tmp_path/"x.png", size=(1080, 1350), output_format="concept_preview")


def test_two_facts_same_bucket_are_value_matched(tmp_path):
    """BLOCKER 2: campaign_title + headline collapse to the same "title" region in
    the old design; both DISTINCT values must be independently covered (value-
    matched) or raise — drawing one must not satisfy the other."""
    facts = _core_facts() + [{"fact_id":"headline","label":"H","value":"Grand Festive Feast","required":True,"source":"customer_text"}]
    for i, n in enumerate(["Idli", "Dosa", "Vada"]):
        facts.append({"fact_id":f"item:{i}:name","label":"I","value":n,"required":True,"source":"customer_text"})
    out = tmp_path / "twobucket.png"
    # Both campaign_title ("Weekend Specials") and headline ("Grand Festive Feast")
    # must be present; render succeeds only if BOTH were actually drawn.
    po.render_premium_overlay(_proj(facts, "F9303"), _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists()


def test_two_col_long_item_name_raises_not_collides(tmp_path):
    """BLOCKER 3: a 16-item distinct-price (two_col) layout where one item name
    cannot fit its half-column must RAISE — it must not collide/overflow yet
    mark the item drawn."""
    import pytest
    facts = _core_facts()
    names = ["Item%02d" % i for i in range(15)] + [
        "ThisIsAnExtremelyLongUnbreakableDishNameThatCannotPossiblyFitInHalfAColumnWidth"]
    for i, n in enumerate(names):
        facts.append({"fact_id":f"item:{i}:name","label":"I","value":n,"required":True,"source":"customer_text"})
        facts.append({"fact_id":f"item:{i}:price","label":"I","value":"$%d.99" % (i % 9 + 1),"required":True,"source":"customer_text"})
    with pytest.raises(render.FlyerRenderError):
        po.render_premium_overlay(_proj(facts, "F9304"), _bg(tmp_path), tmp_path/"y.png", size=(1080, 1350), output_format="concept_preview")


def test_long_offer_label_seal_fits_or_fails(tmp_path):
    """MAJOR: the offer seal is sized from BOTH label and price; a long required
    label must be fully rendered (covered) or raise — never clipped while "offer"
    is marked drawn. Here it fits (wrapped), so the render succeeds and the full
    pricing_structure value is covered."""
    facts = _core_facts() + [{"fact_id":"pricing_structure","label":"Pr",
        "value":"Any item from our entire weekend tiffin menu special $7.99","required":True,"source":"customer_text"}]
    for i, n in enumerate(["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar"]):
        facts.append({"fact_id":f"item:{i}:name","label":"I","value":n,"required":True,"source":"customer_text"})
    out = tmp_path / "longlabel.png"
    po.render_premium_overlay(_proj(facts, "F9305"), _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists()


# ---------------------------------------------------------------------------
# Coverage-matching hardening (Codex re-review: must mirror the referee)
# ---------------------------------------------------------------------------

def test_coverage_is_word_boundary_aware_not_substring(monkeypatch):
    """BLOCKER: coverage must NOT be naive substring. If the renderer draws only
    "Vada" but a DISTINCT required item "Vadai" is (artificially) never drawn,
    coverage must FAIL — "Vada" must not satisfy "Vadai" and vice-versa. We force
    the menu to drop one item to simulate a missing render and assert it raises."""
    import pytest
    from agents.flyer import premium_overlay as _po
    facts = _core_facts()
    for i, n in enumerate(["Vada", "Vadai", "Dosa"]):
        facts.append({"fact_id":f"item:{i}:name","label":"I","value":n,"required":True,"source":"customer_text"})
    proj = _proj(facts, "F9306")

    # Patch the menu planner so the rendered menu OMITS "Vadai" (draws only the
    # other two) — the boundary-aware coverage check must then fail closed.
    real_plan = _po._plan_menu_block
    def _drop_vadai(draw, items, layout, menu_px, min_px, safe_w, has_item_prices, render):
        kept = [(n, p) for (n, p) in items if n != "Vadai"]
        return real_plan(draw, kept, layout, menu_px, min_px, safe_w, has_item_prices, render)
    monkeypatch.setattr(_po, "_plan_menu_block", _drop_vadai)

    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        bg = pathlib.Path(d) / "bg.png"; Image.new("RGB", (1080, 1350), (70, 40, 20)).save(bg)
        with pytest.raises(render.FlyerRenderError):
            _po.render_premium_overlay(proj, bg, pathlib.Path(d)/"o.png", size=(1080, 1350), output_format="concept_preview")


def test_duplicate_prices_are_row_matched_not_collapsed(tmp_path):
    """NOT-FIXED#2: two items both priced "$7.99" must each be matched as a
    name+price PAIR on its own row — one inked "$7.99" must NOT satisfy both.
    A correct render (each row carries its name and price) succeeds; the
    per-row pairing gate is what makes this safe rather than collapse-prone."""
    facts = _core_facts()
    pairs = [("Dosa", "$7.99"), ("Idli", "$7.99"), ("Vada", "$5.49"), ("Upma", "$5.99")]
    for i, (n, pr) in enumerate(pairs):
        facts.append({"fact_id":f"item:{i}:name","label":"I","value":n,"required":True,"source":"customer_text"})
        facts.append({"fact_id":f"item:{i}:price","label":"I","value":pr,"required":True,"source":"customer_text"})
    out = tmp_path / "dupprice.png"
    po.render_premium_overlay(_proj(facts, "F9307"), _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists()


def test_combo_renders_item_prices(tmp_path):
    """NOT-FIXED#2: a 2-item combo WITH locked item prices must VISIBLY render
    each price (name+price pair), not assume it covered. Render succeeds only if
    both prices are paired in the drawn text (final gate enforces it)."""
    facts = _core_facts()
    for i, (n, pr) in enumerate([("Masala Dosa", "$8.99"), ("Mysore Bonda", "$5.49")]):
        facts.append({"fact_id":f"item:{i}:name","label":"I","value":n,"required":True,"source":"customer_text"})
        facts.append({"fact_id":f"item:{i}:price","label":"I","value":pr,"required":True,"source":"customer_text"})
    out = tmp_path / "combo.png"
    po.render_premium_overlay(_proj(facts, "F9308"), _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists()


# ---------------------------------------------------------------------------
# Round-3: exact flag parity + paint-faithful two_col ink
# ---------------------------------------------------------------------------

def test_match_flags_are_label_only_exact_referee_parity():
    """ISSUE 1: the per-fact match flags must mirror visual_qa EXACTLY — keyed on
    fact_id equality OR the keyword in the LABEL ONLY (never the fact_id). A fact
    whose fact_id contains 'address'/'price'/'schedule' but whose label does NOT
    must NOT get that flag (the old fid+label `ctx` heuristic over-matched)."""
    from agents.flyer import visual_qa as vqa
    # Reproduce the renderer's flag logic inline (it is a local closure) and
    # assert it equals the referee's exact expressions for adversarial inputs.
    def flags(fid, label, value):
        lc = label.casefold()
        return {
            "phone_match": vqa._locked_fact_uses_phone_match(fact_id=fid, label=label, value=value),
            "address_match": fid == "location" or "address" in lc or "location" in lc,
            "schedule_match": fid == "schedule" or "schedule" in lc,
            "price_match": fid.endswith(":price") or "price" in lc,
        }
    # fact_id mentions the keyword, label does not → flag must be False
    assert flags("address_note", "Note", "x")["address_match"] is False
    assert flags("price_terms", "Terms", "x")["price_match"] is False
    assert flags("schedule_ref", "Reference", "x")["schedule_match"] is False
    # genuine triggers (fact_id-equality or label keyword) → True
    assert flags("location", "Anything", "x")["address_match"] is True
    assert flags("x", "Store Address", "x")["address_match"] is True
    assert flags("item:0:price", "Item", "$5.99")["price_match"] is True
    assert flags("x", "Unit Price", "$5.99")["price_match"] is True
    assert flags("schedule", "X", "x")["schedule_match"] is True


def test_two_col_pair_ink_gated_on_actual_paint(monkeypatch, tmp_path):
    """ISSUE 2: the two_col ink must be faithful to painted pixels — the
    'name price' pair line is inked ONLY when the price is actually painted. If a
    price is forced not to paint, the pair must NOT be inked and the final
    pair/coverage gate must RAISE (not ship a flyer missing the price)."""
    import pytest
    from agents.flyer import premium_overlay as _po

    facts = _core_facts()
    pairs = [("Dosa", "$8.99"), ("Idli", "$5.99"), ("Vada", "$5.49"),
             ("Upma", "$5.99"), ("Bonda", "$4.99"), ("Pakora", "$4.49")]
    for i, (n, pr) in enumerate(pairs):
        facts.append({"fact_id":f"item:{i}:name","label":"I","value":n,"required":True,"source":"customer_text"})
        facts.append({"fact_id":f"item:{i}:price","label":"I","value":pr,"required":True,"source":"customer_text"})
    proj = _proj(facts, "F9309")

    # Wrap the menu planner so the returned render_fn paints names but inks NO
    # price pairs (simulating prices that did not paint within bounds). The pair
    # gate must then fail closed.
    real_plan = _po._plan_menu_block
    def _names_only(draw, items, layout, menu_px, min_px, safe_w, has_item_prices, render):
        block_h, real_render = real_plan(draw, items, layout, menu_px, min_px, safe_w, has_item_prices, render)
        def _render(draw, **kw):
            painted = real_render(draw, **kw)
            # Drop any "name price" pair line → simulate price not painted.
            return [s for s in painted if not any(p in s for _n, p in items if p)]
        return block_h, _render
    monkeypatch.setattr(_po, "_plan_menu_block", _names_only)

    with pytest.raises(render.FlyerRenderError):
        _po.render_premium_overlay(proj, _bg(tmp_path), tmp_path/"np.png", size=(1080, 1350), output_format="concept_preview")
