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
    # the timeout mechanism: a past deadline -> generator returns None WITHOUT a network call
    gen = render._ppv1_default_generator(_project(), model="m", quality="low", size=SIZE,
                                         deadline=time.monotonic() - 1)
    assert gen("any prompt") is None


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
