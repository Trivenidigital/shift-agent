"""Premium Poster v1 — managed/studio path integration (the generalized opt-in +
the managed hook). Adapters injected -> deterministic + network-free. PIL-dependent
-> test_flyer_* (runs locally + deploy smoke; send-path-ci excludes test_flyer*)."""
from __future__ import annotations

import os
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
        _F("business_name", "Lakshmi's Kitchen", True), _F("campaign_title", "Weekend Snack Specials"),
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
            event_or_business_name="Lakshmi's Kitchen", event_date="2026-07-04", event_time="11:00 AM",
            venue_or_location="90 Brybar Dr St Johns FL", contact_info=ALLOW, style_preference="premium"),
        locked_facts=_food_facts() if facts is None else facts,
    )


def _gen_ok(_p):
    return str(FIXTURE)


def _ocr_ok(_im):
    return True


def _scorer(_p, _b=""):
    return {"axes": {"appetite_appeal": {"score": 8, "critique": "x"}}, "composite": 8.0, "overall_critique": "ok"}


@pytest.fixture(autouse=True)
def _clean_env():
    keys = ("FLYER_PREMIUM_POSTER_V1", "FLYER_PREMIUM_POSTER_V1_ALLOWLIST", "FLYER_PREMIUM_POSTER_V1_N")
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


# ── the generalized opt-in carries path identity ────────────────────────────

def test_opt_in_default_none():
    assert render._premium_poster_v1_opt_in() is None


def test_managed_context_sets_managed():
    with render.premium_poster_v1_managed_path():
        assert render._premium_poster_v1_opt_in() == "managed"
    assert render._premium_poster_v1_opt_in() is None   # reset on exit


def test_bare_context_sets_bare():
    with render.premium_poster_v1_bare_path():
        assert render._premium_poster_v1_opt_in() == "bare"
        assert render._premium_poster_v1_bare_opt_in() is True
    assert render._premium_poster_v1_opt_in() is None


# ── render_premium_poster_v1 records the path in its outcome ─────────────────

def test_render_records_managed_path(tmp_path):
    _arm()
    out = render.render_premium_poster_v1(
        _project(), tmp_path / "c.png", concept_id="C1", output_format="concept_preview", size=SIZE,
        model="m", quality="low", generator=_gen_ok, textless_ocr=_ocr_ok, critique_scorer=_scorer, path="managed")
    assert out.delivered is True and out.path == "managed"
    assert (tmp_path / "c.png").exists()


def test_premium_failure_falls_through_managed(tmp_path):
    out = render.render_premium_poster_v1(
        _project(), tmp_path / "c.png", concept_id="C1", output_format="concept_preview", size=SIZE,
        model="m", quality="low", generator=lambda p: None, textless_ocr=_ocr_ok, critique_scorer=_scorer, path="managed")
    assert out.delivered is False and out.path == "managed"   # path preserved on fallback
    assert not (tmp_path / "c.png").exists()


# ── the _render_model hook fires for the MANAGED opt-in (armed + eligible) ───

def _hook_calls(monkeypatch):
    calls = {"n": 0, "path": None}
    def _stub(project, target, **k):
        calls["n"] += 1
        calls["path"] = k.get("path")
        Path(target).write_bytes(b"\x89PNG-managed-stub")
        return render.PremiumPosterV1Outcome(True, "delivered", "none", 1, 0, 8.0, k.get("output_format", ""), k.get("path", ""))
    monkeypatch.setattr(render, "render_premium_poster_v1", _stub)
    return calls


def test_managed_hook_fires_when_armed_eligible(tmp_path, monkeypatch):
    _arm()
    calls = _hook_calls(monkeypatch)
    out = tmp_path / "out.png"
    with render.premium_poster_v1_managed_path():
        render._render_model(_project(), out, concept_id="C1", output_format="concept_preview",
                             size=SIZE, model="google/gemini-3.1-flash-image-preview", quality="low")
    assert calls["n"] == 1 and calls["path"] == "managed"     # managed hook fired with path identity
    assert out.read_bytes() == b"\x89PNG-managed-stub"


def test_flag_off_managed_unchanged(tmp_path, monkeypatch):
    calls = _hook_calls(monkeypatch)   # flag OFF (not armed)
    with render.premium_poster_v1_managed_path():
        render._render_model(_project(), tmp_path / "out.png", concept_id="C1", output_format="concept_preview",
                             size=SIZE, model="deterministic-renderer", quality="low")
    assert calls["n"] == 0 and (tmp_path / "out.png").exists()   # existing deterministic render ran


def test_flag_on_not_allowlisted_managed_unchanged(tmp_path, monkeypatch):
    _arm()
    calls = _hook_calls(monkeypatch)
    with render.premium_poster_v1_managed_path():
        render._render_model(_project(phone="+19998887777"), tmp_path / "out.png", concept_id="C1",
                             output_format="concept_preview", size=SIZE, model="deterministic-renderer", quality="low")
    assert calls["n"] == 0 and (tmp_path / "out.png").exists()


def test_non_food_managed_unchanged(tmp_path, monkeypatch):
    _arm()
    calls = _hook_calls(monkeypatch)
    proj = _project(raw="tax filing and bookkeeping services", facts=[
        _F("business_name", "Sharma Tax", True), _F("pricing_structure", "Returns from $99"),
        _F("item:0:name", "1040 Filing"), _F("item:1:name", "Bookkeeping"), _F("item:2:name", "Payroll")])
    with render.premium_poster_v1_managed_path():
        render._render_model(proj, tmp_path / "out.png", concept_id="C1", output_format="concept_preview",
                             size=SIZE, model="deterministic-renderer", quality="low")
    assert calls["n"] == 0 and (tmp_path / "out.png").exists()


def test_missing_facts_managed_unchanged(tmp_path, monkeypatch):
    _arm()
    calls = _hook_calls(monkeypatch)
    facts = [f for f in _food_facts() if f.fact_id != "pricing_structure"]  # no offer
    with render.premium_poster_v1_managed_path():
        render._render_model(_project(facts=facts), tmp_path / "out.png", concept_id="C1",
                             output_format="concept_preview", size=SIZE, model="deterministic-renderer", quality="low")
    assert calls["n"] == 0 and (tmp_path / "out.png").exists()


def test_recovery_render_not_wrapped(tmp_path, monkeypatch):
    _arm()
    calls = _hook_calls(monkeypatch)
    # even under the managed opt-in, force_background_only (recovery rung) skips premium
    with render.premium_poster_v1_managed_path():
        render._render_model(_project(), tmp_path / "out.png", concept_id="C1", output_format="concept_preview",
                             size=SIZE, model="deterministic-renderer", quality="low", force_background_only=True)
    assert calls["n"] == 0


# ── bare path has no regression ─────────────────────────────────────────────

def test_bare_path_still_fires(tmp_path, monkeypatch):
    _arm()
    calls = _hook_calls(monkeypatch)
    out = tmp_path / "out.png"
    with render.premium_poster_v1_bare_path():
        render._render_model(_project(), out, concept_id="C1", output_format="concept_preview",
                             size=SIZE, model="google/gemini-3.1-flash-image-preview", quality="low")
    assert calls["n"] == 1 and calls["path"] == "bare"        # bare still works, path-distinguished


# ── outcome does not leak / go stale across renders ─────────────────────────

def test_outcome_reset_per_render_no_stale(tmp_path):
    _arm()
    # render 1: managed premium delivers (real injected adapters via a stub is complex;
    # instead assert the hook RESETS the outcome each render so a non-premium render
    # never inherits a prior value).
    render._PREMIUM_POSTER_V1_OUTCOME.set(
        render.PremiumPosterV1Outcome(True, "delivered", "none", 1, 0, 8.0, "concept_preview", "managed"))
    # a render with NO opt-in (not armed path) resets the outcome to None at the top
    render._render_model(_project(), tmp_path / "out.png", concept_id="C1", output_format="concept_preview",
                         size=SIZE, model="deterministic-renderer", quality="low")
    assert render.consume_premium_poster_v1_outcome() is None   # stale value cleared, not inherited


# ── generate-flyer-concepts wiring (structural) ─────────────────────────────

def test_generate_concepts_wraps_primary_render_and_emits():
    src = (REPO / "src" / "agents" / "flyer" / "scripts" / "generate-flyer-concepts").read_text(encoding="utf-8")
    # the managed opt-in wraps the primary render + emits the managed outcome
    assert "with premium_poster_v1_managed_path():" in src
    # emitted on BOTH the success path AND the exception path (so a raised primary
    # render doesn't lose the managed audit row) — idempotent consume-and-clear.
    assert src.count("_emit_premium_poster_v1_managed_outcome(audit_log_path, project)") >= 2
    # NOT wrapped around the recovery/rung re-renders (only one managed wrap)
    assert src.count("with premium_poster_v1_managed_path():") == 1
    # consumes the outcome (closes the #523 never-consumed gap)
    assert "consume_premium_poster_v1_outcome()" in src
