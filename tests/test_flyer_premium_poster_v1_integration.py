"""Premium Poster v1 — integration (bare-path guarded routing into _render_model).

Adapters (generator / OCR / critique) are INJECTED so these run deterministically
+ network-free; the real adapters are exercised only in the C2B/C3/best-of-N shadow
runs + the gated live test. PIL-dependent -> test_flyer_* (excluded from send-path-ci).
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("PIL")

from agents.flyer import render  # noqa: E402
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "premium_poster_v1" / "textless_food_scene.png"
ALLOW = "+17329837841"
SIZE = (1080, 1350)


def _F(i, v, req=False):
    return FlyerLockedFact(fact_id=i, label=i, value=v, source="customer_profile", required=req)


def _food_facts():
    return [
        _F("business_name", "Lakshmi's Kitchen", True),
        _F("campaign_title", "Weekend Snack Specials"),
        _F("pricing_structure", "Any 2 snacks $9.99"),
        _F("item:0:name", "Punugulu"), _F("item:1:name", "Egg Bonda"), _F("item:2:name", "Aloo Bonda"),
        _F("item:3:name", "Veg Lollipop"), _F("item:4:name", "Cut Mirchi"), _F("item:5:name", "Onion Pakora"),
        _F("item:6:name", "Punjabi Samosa"),
        _F("schedule", "Saturday & Sunday"), _F("location", "90 Brybar Dr St Johns FL"), _F("contact_phone", ALLOW),
    ]


def _project(phone=ALLOW, facts=None, raw="Weekend snack specials menu for my restaurant"):
    return FlyerProject(
        project_id="F0001", status="generating_concepts", customer_phone=phone,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        original_message_id="wamid.1", raw_request=raw,
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen", event_date="2026-07-04",
            event_time="11:00 AM", venue_or_location="90 Brybar Dr St Johns FL",
            contact_info=ALLOW, style_preference="premium",
        ),
        locked_facts=_food_facts() if facts is None else facts,
    )


def _gen_ok(_p):
    return str(FIXTURE)


def _ocr_textless(_im):
    return True


def _scorer(_p, _b=""):
    return {"axes": {"appetite_appeal": {"score": 8, "critique": "x"}}, "composite": 8.0, "overall_critique": "ok"}


@pytest.fixture(autouse=True)
def _clean_env():
    keys = ("FLYER_PREMIUM_POSTER_V1", "FLYER_PREMIUM_POSTER_V1_ALLOWLIST",
            "FLYER_PREMIUM_POSTER_V1_N", "FLYER_PREMIUM_POSTER_V1_TIMEOUT_SEC")
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v


def _arm():
    os.environ["FLYER_PREMIUM_POSTER_V1"] = "1"
    os.environ["FLYER_PREMIUM_POSTER_V1_ALLOWLIST"] = ALLOW


def _render_ppv1(project, target, **kw):
    return render.render_premium_poster_v1(
        project, target, concept_id="C1", output_format="concept_preview", size=SIZE,
        model="google/gemini-3.1-flash-image-preview", quality="medium", **kw)


# ── gate helpers: flag / allowlist (scoped-rollout guard) / N / eligibility ──

def test_flag_off_not_armed():
    assert render._premium_poster_v1_armed(_project()) is False


def test_scoped_rollout_guard_empty_allowlist_disables():
    os.environ["FLYER_PREMIUM_POSTER_V1"] = "1"  # flag on, NO allowlist
    assert render._premium_poster_v1_armed(_project()) is False  # empty allowlist => DISABLED (not global)


def test_armed_only_for_allowlisted_phone():
    _arm()
    assert render._premium_poster_v1_armed(_project(phone=ALLOW)) is True
    assert render._premium_poster_v1_armed(_project(phone="+19998887777")) is False  # only the allowlisted number


def test_n_default_one_and_clamped():
    assert render._premium_poster_v1_n() == 1
    os.environ["FLYER_PREMIUM_POSTER_V1_N"] = "2"
    assert render._premium_poster_v1_n() == 2
    os.environ["FLYER_PREMIUM_POSTER_V1_N"] = "99"
    assert render._premium_poster_v1_n() == 3   # clamp 1..3
    os.environ["FLYER_PREMIUM_POSTER_V1_N"] = "garbage"
    assert render._premium_poster_v1_n() == 1


def test_eligible_food_with_required_facts():
    assert render._is_food_or_grocery_project(_project()) is True
    assert render._premium_poster_v1_eligible(_project()) is True


def test_non_food_not_eligible():
    proj = _project(raw="tax filing and bookkeeping services", facts=[
        _F("business_name", "Sharma Tax", True), _F("pricing_structure", "Returns from $99"),
        _F("item:0:name", "1040 Filing"), _F("item:1:name", "Bookkeeping"), _F("item:2:name", "Payroll")])
    assert render._premium_poster_v1_eligible(proj) is False


def test_missing_required_facts_not_eligible():
    facts = [f for f in _food_facts() if f.fact_id != "pricing_structure"]  # no offer/price
    assert render._premium_poster_v1_eligible(_project(facts=facts)) is False
    too_few = [f for f in _food_facts() if not f.fact_id.startswith("item:")]
    too_few += [_F("item:0:name", "Punugulu"), _F("item:1:name", "Bonda")]  # only 2 items
    assert render._premium_poster_v1_eligible(_project(facts=too_few)) is False


# ── composer-unfit pre-checks: never burn N generations on a brief the composer
#    refuses fail-closed (multi-price / dense menu / regional script) ──────────

def test_multi_price_offer_not_eligible():
    facts = [f for f in _food_facts() if f.fact_id != "pricing_structure"]
    facts.append(_F("pricing_structure", "Was $12.99 now $8.99"))
    assert render._premium_poster_v1_eligible(_project(facts=facts)) is False
    # single price stays eligible
    assert render._premium_poster_v1_eligible(_project()) is True


def test_dense_menu_beyond_item_cap_not_eligible():
    facts = [f for f in _food_facts() if not f.fact_id.startswith("item:")]
    facts += [_F(f"item:{i}:name", f"Snack Item {i}") for i in range(13)]  # cap is 12
    assert render._premium_poster_v1_eligible(_project(facts=facts)) is False
    at_cap = [f for f in _food_facts() if not f.fact_id.startswith("item:")]
    at_cap += [_F(f"item:{i}:name", f"Item {i}") for i in range(12)]
    assert render._premium_poster_v1_eligible(_project(facts=at_cap)) is True


def test_regional_script_facts_not_eligible():
    # The vendored poster fonts are Latin-only; regional-script facts would render
    # tofu boxes and fail QA every time. Excluded at eligibility, not after N gens.
    facts = [f for f in _food_facts() if f.fact_id != "item:0:name"]
    facts.append(_F("item:0:name", "పునుగులు"))  # Telugu
    assert render._premium_poster_v1_eligible(_project(facts=facts)) is False


# ── render_premium_poster_v1: delivers on a food win, falls through otherwise ─

def test_delivers_on_food_win(tmp_path):
    target = tmp_path / "C1.png"
    outcome = _render_ppv1(_project(), target, generator=_gen_ok, textless_ocr=_ocr_textless, critique_scorer=_scorer)
    assert outcome.delivered is True and outcome.status == "delivered"
    assert target.exists()
    from PIL import Image
    with Image.open(target) as im:
        assert im.size == SIZE   # the composed deterministic poster (text from facts)


def test_generation_failure_falls_through(tmp_path):
    target = tmp_path / "C1.png"
    outcome = _render_ppv1(_project(), target, generator=lambda p: None, textless_ocr=_ocr_textless, critique_scorer=_scorer)
    assert outcome.delivered is False and outcome.status == "fallback"
    assert not target.exists()   # nothing written -> caller uses the existing path


def test_ocr_reject_falls_through(tmp_path):
    target = tmp_path / "C1.png"
    outcome = _render_ppv1(_project(), target, generator=_gen_ok, textless_ocr=lambda im: False, critique_scorer=_scorer)
    assert outcome.delivered is False   # text-bearing image rejected; no food winner -> fall through
    assert not target.exists()


def test_ocr_error_falls_through(tmp_path):
    def _boom(_im):
        raise RuntimeError("vision outage")
    target = tmp_path / "C1.png"
    outcome = _render_ppv1(_project(), target, generator=_gen_ok, textless_ocr=_boom, critique_scorer=_scorer)
    assert outcome.delivered is False   # OCR outage => candidate dropped (never trusted) -> fall through
    assert not target.exists()


def test_critique_unavailable_still_delivers(tmp_path):
    target = tmp_path / "C1.png"
    outcome = _render_ppv1(_project(), target, generator=_gen_ok, textless_ocr=_ocr_textless, critique_scorer=lambda *a: None)
    assert outcome.delivered is True   # critique unavailable -> first accepted food candidate wins
    assert target.exists()


def test_compose_error_falls_through(tmp_path):
    def _boom_compose(*a, **k):
        raise RuntimeError("compose blew up")
    target = tmp_path / "C1.png"
    outcome = _render_ppv1(_project(), target, generator=_gen_ok, textless_ocr=_ocr_textless,
                           critique_scorer=_scorer, compose=_boom_compose)
    assert outcome.delivered is False and outcome.reason.startswith("exception:")
    assert not target.exists()


def test_unsupported_size_skips(tmp_path):
    target = tmp_path / "pdf.png"
    outcome = render.render_premium_poster_v1(
        _project(), target, concept_id="C1", output_format="printable_pdf", size=None,
        model="m", quality="low", generator=_gen_ok, textless_ocr=_ocr_textless, critique_scorer=_scorer)
    assert outcome.delivered is False and outcome.status == "skipped" and outcome.reason == "unsupported_size"
    assert not target.exists()


def test_default_generator_respects_deadline():
    # the timeout mechanism: a past deadline -> raises TimeoutError WITHOUT a network
    # call. Raising (not returning None) keeps budget exhaustion a DISTINCT fallback
    # reason: the director records generator_error:TimeoutError instead of collapsing
    # it into generator_returned_none (2026-07-02 review SF-2/FM-1).
    gen = render._ppv1_default_generator(_project(), model="m", quality="low", size=SIZE,
                                         deadline=time.monotonic() - 1)
    with pytest.raises(TimeoutError):
        gen("any prompt")


def test_generator_timeout_becomes_distinct_fallback_reason(tmp_path):
    # End-to-end: budget exhaustion must be distinguishable in the outcome reason.
    target = tmp_path / "C1.png"
    gen = render._ppv1_default_generator(_project(), model="m", quality="low", size=SIZE,
                                         deadline=time.monotonic() - 1)
    outcome = _render_ppv1(_project(), target, generator=gen,
                           textless_ocr=_ocr_textless, critique_scorer=_scorer)
    assert outcome.delivered is False
    assert "generation_failed" in outcome.reason


def test_fallback_reason_carries_candidate_status_summary(tmp_path):
    # OCR outage vs model-painted-text must be distinguishable from the reason
    # string alone (2026-07-02 review SF-2/PR-H1).
    def _ocr_boom(_im):
        raise RuntimeError("vision outage")
    target = tmp_path / "C1.png"
    outcome = _render_ppv1(_project(), target, generator=_gen_ok, textless_ocr=_ocr_boom, critique_scorer=_scorer)
    assert outcome.delivered is False
    assert "check_error" in outcome.reason
    outcome2 = _render_ppv1(_project(), target, generator=_gen_ok, textless_ocr=lambda im: False, critique_scorer=_scorer)
    assert outcome2.delivered is False
    assert "image_has_text" in outcome2.reason
    assert outcome.reason != outcome2.reason


def test_injected_generator_files_never_deleted(tmp_path):
    # Temp cleanup applies ONLY to files the DEFAULT generator creates. An injected
    # generator owns its paths — the orchestrator must never unlink them (the test
    # fixture itself would vanish).
    target = tmp_path / "C1.png"
    _render_ppv1(_project(), target, generator=_gen_ok, textless_ocr=_ocr_textless, critique_scorer=_scorer)
    assert FIXTURE.exists()


def test_delivered_poster_is_fact_safe(tmp_path):
    # the delivered winner is composed by compose_premium_poster_v1 (text from facts only).
    # Prove the composed image is the right poster size (no model text path involved).
    target = tmp_path / "C1.png"
    outcome = _render_ppv1(_project(), target, generator=_gen_ok, textless_ocr=_ocr_textless, critique_scorer=_scorer)
    assert outcome.delivered is True
    from PIL import Image
    with Image.open(target) as im:
        assert im.size == SIZE


# ── the _render_model hook: bare opt-in + flag/allowlist gate ────────────────

def test_hook_not_entered_when_flag_off(tmp_path, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    # opt-in ON but flag OFF -> branch not entered (armed False) -> deterministic render runs
    with render.premium_poster_v1_bare_path():
        render._render_model(_project(), tmp_path / "out.png", concept_id="C1",
                             output_format="concept_preview", size=SIZE,
                             model="deterministic-renderer", quality="low")
    assert calls["n"] == 0
    assert (tmp_path / "out.png").exists()   # existing deterministic path produced output


def test_hook_not_entered_without_bare_opt_in(tmp_path, monkeypatch):
    _arm()
    calls = {"n": 0}
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    # armed + eligible but NO bare opt-in (managed/studio path) -> branch not entered
    render._render_model(_project(), tmp_path / "out.png", concept_id="C1",
                         output_format="concept_preview", size=SIZE,
                         model="deterministic-renderer", quality="low")
    assert calls["n"] == 0   # D1: managed path byte-identical
    assert (tmp_path / "out.png").exists()


def test_hook_enters_premium_when_armed_and_opted_in(tmp_path, monkeypatch):
    _arm()

    def _stub(project, target, **k):
        Path(target).write_bytes(b"\x89PNG-premium-stub")
        return render.PremiumPosterV1Outcome(True, "delivered", "none", 1, 0, 8.0, k.get("output_format", ""))

    monkeypatch.setattr(render, "render_premium_poster_v1", _stub)
    out = tmp_path / "out.png"
    # non-deterministic model: if the premium branch did NOT fire+return, _render_model
    # would call OpenRouter (network). The stub delivering -> early return -> no network.
    with render.premium_poster_v1_bare_path():
        render._render_model(_project(), out, concept_id="C1", output_format="concept_preview",
                             size=SIZE, model="google/gemini-3.1-flash-image-preview", quality="low")
    assert out.read_bytes() == b"\x89PNG-premium-stub"   # premium path delivered, returned early


def test_hook_falls_through_on_premium_miss(tmp_path, monkeypatch):
    _arm()
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda project, target, **k: render.PremiumPosterV1Outcome(False, "fallback", "no_food_winner", 1, -1, None, ""))
    out = tmp_path / "out.png"
    # premium misses (not delivered) -> fall through to the existing deterministic render
    with render.premium_poster_v1_bare_path():
        render._render_model(_project(), out, concept_id="C1", output_format="concept_preview",
                             size=SIZE, model="deterministic-renderer", quality="low")
    assert out.exists()   # existing path produced the output after the miss


def test_no_routing_premium_branch_skipped_during_recovery(tmp_path, monkeypatch):
    _arm()
    calls = {"n": 0}
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    # force_background_only (the deterministic-recovery rung) -> premium branch skipped
    with render.premium_poster_v1_bare_path():
        render._render_model(_project(), tmp_path / "out.png", concept_id="C1",
                             output_format="concept_preview", size=SIZE,
                             model="deterministic-renderer", quality="low", force_background_only=True)
    assert calls["n"] == 0


# ── PR-S3 guard gates (2026-07-02 review PR-B1 / FA-3 / FM-7) ─────────────────

def test_repair_instruction_render_never_enters_premium(tmp_path, monkeypatch):
    # A strict-note / revision-feedback render must NEVER enter premium: the
    # composer works from stored locked facts and would silently DROP the
    # instruction (and QA validates against the same stored facts).
    _arm()
    calls = {"n": 0}
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    with render.premium_poster_v1_bare_path():
        render._render_model(_project(), tmp_path / "out.png", concept_id="C1",
                             output_format="concept_preview", size=SIZE,
                             model="deterministic-renderer", quality="low",
                             repair_instruction="CRITICAL: fix the price of X")
    assert calls["n"] == 0
    assert (tmp_path / "out.png").exists()  # existing path still rendered


def test_deterministic_model_never_enters_premium(tmp_path, monkeypatch):
    # Kill-switch totality (FA-3): with a deterministic draft model (the
    # FLYER_INTEGRATED_KILLSWITCH panic path), the render must make ZERO
    # generative calls — the premium branch must not even be entered.
    _arm()
    calls = {"n": 0}
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    with render.premium_poster_v1_bare_path():
        render._render_model(_project(), tmp_path / "out.png", concept_id="C1",
                             output_format="concept_preview", size=SIZE,
                             model="deterministic-renderer", quality="low")
    assert calls["n"] == 0
    assert (tmp_path / "out.png").exists()


def test_non_c1_concept_never_enters_premium(tmp_path, monkeypatch):
    # concept_count > 1 (FM-7/PR-M1): each concept would burn its own N gens +
    # budget and the emitter would record only the LAST outcome. Premium is a
    # one-shot primary attempt on C1 only.
    _arm()
    calls = {"n": 0}
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    with render.premium_poster_v1_bare_path():
        render._render_model(_project(), tmp_path / "out.png", concept_id="C2",
                             output_format="concept_preview", size=SIZE,
                             model="deterministic-renderer", quality="low")
    assert calls["n"] == 0


def test_vision_text_missing_extracted_text_key_is_unavailable(monkeypatch, tmp_path):
    # OCR schema-drift fail-closed (FM-6): a valid-JSON response WITHOUT the
    # extracted_text key must be an OUTAGE (source="unavailable" -> the premium
    # textless gate raises -> check_error -> candidate dropped), never a
    # "textless" certification. Present-and-empty stays textless.
    import io
    import json as _json
    import urllib.request

    from agents.flyer import visual_qa

    target = tmp_path / "img.png"
    from PIL import Image
    Image.new("RGB", (32, 32)).save(target)
    monkeypatch.setattr(visual_qa, "_openrouter_key", lambda: "sk-test-1234567890")

    def _fake_urlopen(req, timeout=None):
        body = _json.dumps({"choices": [{"message": {"content": _json.dumps({"quality_notes": []})}}]})
        class _Resp(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return _Resp(body.encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    text, source, kind, notes = visual_qa._vision_text(target)
    assert source == "unavailable"
    assert any("missing extracted_text" in n for n in notes)

    def _fake_urlopen_empty(req, timeout=None):
        body = _json.dumps({"choices": [{"message": {"content": _json.dumps({"extracted_text": "", "quality_notes": []})}}]})
        class _Resp(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return _Resp(body.encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_empty)
    text, source, kind, notes = visual_qa._vision_text(target)
    assert source == "openrouter" and text == ""  # genuinely-textless stays textless


# ── PR-S4 finals fidelity (2026-07-02 review ST-1/FM-3/FA-1/FA-2/CF-1) ───────

def test_delivery_unlinks_stale_raw_and_writes_provenance(tmp_path):
    # A premium delivery must (1) remove any stale legacy .raw.png sibling so
    # render_final_package can never rebuild finals from an unrelated earlier
    # background, and (2) persist the ppv1 provenance sidecar + the OCR-verified
    # winner background for per-format recomposition.
    target = tmp_path / "F0001-C1-preview.png"
    stale_raw = render._raw_background_path(target)
    stale_raw.write_bytes(b"\x89PNG-stale-raw")
    outcome = _render_ppv1(_project(), target, generator=_gen_ok,
                           textless_ocr=_ocr_textless, critique_scorer=_scorer)
    assert outcome.delivered is True
    assert not stale_raw.exists()                                # write-time invariant
    assert render._ppv1_provenance_path(target).exists()         # provenance sidecar
    assert render._ppv1_background_path(target).exists()         # persisted winner bg
    assert FIXTURE.exists()                                      # source copied, not moved
    import json as _json
    prov = _json.loads(render._ppv1_provenance_path(target).read_text(encoding="utf-8"))
    assert prov["schema"] == 1 and prov["n"] == 1


def test_fallback_writes_no_provenance(tmp_path):
    target = tmp_path / "F0001-C1-preview.png"
    outcome = _render_ppv1(_project(), target, generator=_gen_ok,
                           textless_ocr=lambda im: False, critique_scorer=_scorer)
    assert outcome.delivered is False
    assert not render._ppv1_provenance_path(target).exists()
    assert not render._ppv1_background_path(target).exists()


def test_ppv1_final_fixed_size_recomposes_story_from_saved_background(tmp_path):
    # instagram_story (1080x1920): the composer recomposes the SAME deterministic
    # poster at the target aspect from the saved background — brand band + footer
    # survive (center-crop destroyed both).
    preview = tmp_path / "F0001-C1-preview.png"
    outcome = _render_ppv1(_project(), preview, generator=_gen_ok,
                           textless_ocr=_ocr_textless, critique_scorer=_scorer)
    assert outcome.delivered is True
    out = tmp_path / "F0001-instagram_story.png"
    route = render._ppv1_final_fixed_size(_project(), preview, out, size=(1080, 1920))
    assert route == "recomposed"
    from PIL import Image
    with Image.open(out) as im:
        assert im.size == (1080, 1920)


def test_ppv1_final_fixed_size_letterboxes_when_compose_refuses(tmp_path):
    # A dense menu that fits 4:5 can overflow the SQUARE readable zone — the
    # composer refuses fail-closed and the final letterboxes the approved preview
    # (every fact visible; never a partial or cropped poster).
    facts = [f for f in _food_facts() if not f.fact_id.startswith("item:")]
    facts += [_F(f"item:{i}:name", f"Very Long Snack Item Name Number {i}") for i in range(12)]
    proj = _project(facts=facts)
    preview = tmp_path / "F0001-C1-preview.png"
    outcome = _render_ppv1(proj, preview, generator=_gen_ok,
                           textless_ocr=_ocr_textless, critique_scorer=_scorer)
    assert outcome.delivered is True
    out = tmp_path / "F0001-instagram_post.png"
    route = render._ppv1_final_fixed_size(proj, preview, out, size=(1080, 1080))
    assert route in ("recomposed", "letterboxed")  # composer decides; both fact-safe
    from PIL import Image
    with Image.open(out) as im:
        assert im.size == (1080, 1080)


def test_ppv1_final_fixed_size_letterboxes_without_saved_background(tmp_path):
    # Missing bg sidecar (e.g. provenance write half-failed): letterbox floor.
    preview = tmp_path / "F0001-C1-preview.png"
    from PIL import Image
    Image.new("RGB", (1080, 1350), (40, 20, 10)).save(preview)
    out = tmp_path / "F0001-instagram_post.png"
    route = render._ppv1_final_fixed_size(_project(), preview, out, size=(1080, 1080))
    assert route == "letterboxed"
    with Image.open(out) as im:
        assert im.size == (1080, 1080)


def test_final_package_premium_provenance_avoids_center_crop(tmp_path, monkeypatch):
    # End-to-end: a premium-provenance preview must derive instagram formats via
    # _ppv1_final_fixed_size (recompose-or-letterbox), and whatsapp_image stays a
    # DIRECT export of the exact approved artifact.
    from datetime import datetime, timezone

    from schemas import FlyerAsset, FlyerConcept

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    preview = asset_dir / "F0001-C1-preview.png"
    outcome = _render_ppv1(_project(), preview, generator=_gen_ok,
                           textless_ocr=_ocr_textless, critique_scorer=_scorer)
    assert outcome.delivered is True

    import hashlib
    sha = hashlib.sha256(preview.read_bytes()).hexdigest()
    proj = _project().model_copy(update={
        "assets": [FlyerAsset(asset_id="A0001", kind="concept_preview", source="rendered",
                              path=str(preview), mime_type="image/png", sha256=sha,
                              original_message_id="wamid.1",
                              received_at=datetime.now(timezone.utc))],
        "concepts": [FlyerConcept(concept_id="C1", title="Best Design",
                                  style_summary="Premium poster", preview_asset_id="A0001",
                                  prompt="", created_at=datetime.now(timezone.utc))],
        "selected_concept_id": "C1",
    })

    routes = []
    real = render._ppv1_final_fixed_size

    def _spy(project, preview_p, path, *, size):
        r = real(project, preview_p, path, size=size)
        routes.append((size, r))
        return r

    monkeypatch.setattr(render, "_ppv1_final_fixed_size", _spy)
    specs = render.render_final_package(proj, tmp_path / "finals")
    by_format = {s.output_format: s for s in specs}
    assert set(by_format) == {"whatsapp_image", "instagram_post", "instagram_story", "printable_pdf"}
    # instagram formats went through the premium derivation, not center-crop
    assert {sz for sz, _ in routes} == {(1080, 1080), (1080, 1920)}
    # whatsapp_image is byte-identical in content to the approved preview export path
    from PIL import Image
    with Image.open(by_format["whatsapp_image"].path) as wa, Image.open(preview) as ap:
        assert wa.size == (1080, 1350)
        assert wa.getpixel((540, 675)) == ap.getpixel((540, 675))


def test_legacy_rerender_clears_stale_premium_provenance(tmp_path, monkeypatch):
    # A premium delivery leaves provenance sidecars; if a LATER render of the same
    # path goes legacy (premium fell through / disarmed), those sidecars are stale
    # and must be removed — otherwise render_final_package would treat the new
    # legacy preview as premium and recompose finals from an unrelated background.
    _arm()
    target = tmp_path / "F0001-C1-preview.png"
    outcome = _render_ppv1(_project(), target, generator=_gen_ok,
                           textless_ocr=_ocr_textless, critique_scorer=_scorer)
    assert outcome.delivered is True
    assert render._ppv1_provenance_path(target).exists()
    # Second render at the same path: premium misses -> legacy deterministic render
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda project, t, **k: render.PremiumPosterV1Outcome(
                            False, "fallback", "no_food_winner:image_has_text=1", 1, -1, None, ""))
    with render.premium_poster_v1_bare_path():
        render._render_model(_project(), target, concept_id="C1",
                             output_format="concept_preview", size=SIZE,
                             model="deterministic-renderer", quality="low")
    assert target.exists()
    assert not render._ppv1_provenance_path(target).exists()  # stale provenance cleared
    assert not render._ppv1_background_path(target).exists()
