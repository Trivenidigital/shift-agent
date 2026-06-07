"""Slice 3 — bare iteration handler (re-roll / specific_revision / style_reuse / unclear).

Binding regressions (operator spec 2026-06-07):
  1. "I don't like this design, generate again" -> reroll, NOT resend-full-details
  2. "redesign this, more graduation themed, two students turning back" -> specific_revision; saved
     facts preserved EXACTLY; skill-updated visual_direction only
  3. "Use this design, create a weekend breakfast flyer" -> style_reuse; NEW facts, old facts NOT copied
  4. missing/stale session OR skill can't -> ONE concise question, not a generic failure
  5. flag off -> REVISION_NEEDED (caller keeps today's resend reply)
  6. facts are never re-extracted from a revision
"""
from __future__ import annotations

import json
import types
from datetime import datetime, timezone
from pathlib import Path
import sys

_SRC = Path(__file__).resolve().parent.parent / "src"
for _p in (_SRC / "platform", _SRC / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bare_render as br  # noqa: E402
from flyer_brief import VisualDirection  # noqa: E402
from schemas import FlyerProject, FlyerLockedFact, FlyerRequestFields  # noqa: E402

CHAT = "201975216009469@lid"
SENDER = "+17329837841"
_VD = VisualDirection(theme_family="Graduation celebration",
                      visual_subjects=["two students in caps facing away"], motifs=["caps"],
                      palette=["royal blue", "gold"])


class _Cust:
    status = "active"
    business_name = "Lakshmi's Kitchen"
    customer_id = "CUST0001"
    business_whatsapp_number = "+17325550104"
    languages = ["en"]
    preferred_language = "en"


def _grad_session(*, sent_at=None):
    now = datetime(2026, 6, 7, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0300", status="awaiting_final_approval", customer_phone="+17325550104",
        created_at=now, updated_at=now, original_message_id="m",
        raw_request="Create a graduation flyer. 2026 graduation parties. 10% off.",
        fields=FlyerRequestFields(event_or_business_name="Lakshmi's Kitchen"),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen",
                            source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="2026 Graduation Parties",
                            source="customer_text", required=True),
            FlyerLockedFact(fact_id="pricing_structure", label="Pricing", value="10% off on entire order",
                            source="customer_text", required=True),
        ],
    )
    return {"chat_id": CHAT, "sent_at": sent_at or datetime.now(timezone.utc).isoformat(),
            "brief": "Create a graduation flyer. 2026 graduation parties. 10% off.",
            "project": json.loads(project.model_dump_json()), "raw_background_path": "",
            "model": "google/gemini-2.5-flash-image", "output_size": [1080, 1350]}


def _install(monkeypatch, *, session=..., advise_vd=None, qa=(True, [])):
    cap = {"poster": [], "wrote": [], "advise_calls": []}
    monkeypatch.setattr(br, "ITERATION_ENABLED", True)
    monkeypatch.setattr(br, "REVISION_APPLY_ENABLED", True)
    sess = _grad_session() if session is ... else session
    monkeypatch.setattr(br, "_load_session", lambda chat_id: sess)
    monkeypatch.setattr(br, "resolve_customer", lambda *a, **k: _Cust())
    monkeypatch.setattr(br, "run_visual_qa", lambda png, project: qa)
    monkeypatch.setattr(br, "_write_session", lambda *a, **k: cap["wrote"].append((a, k)))

    def _poster(project, *, strict_note="", raw_bg_dest=None, scene_direction=None):
        cap["poster"].append({
            "facts": [f.fact_id for f in project.locked_facts],
            "fact_values": {f.fact_id: f.value for f in project.locked_facts},
            "scene": scene_direction,
        })
        return b"ITER_PNG"
    monkeypatch.setattr(br, "_generate_poster", _poster)

    def _advise(req, facts, customer):
        cap["advise_calls"].append(req)
        return advise_vd
    monkeypatch.setattr(br, "_context_builder",
                        lambda: types.SimpleNamespace(advise_scene_direction=_advise))
    return cap


def _install_new_facts(monkeypatch, title="Weekend Breakfast"):
    new_facts = [
        FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen",
                        source="customer_profile", required=True),
        FlyerLockedFact(fact_id="campaign_title", label="Campaign", value=title,
                        source="customer_text", required=True),
    ]
    fake_fields = FlyerRequestFields(event_or_business_name="Lakshmi's Kitchen")
    monkeypatch.setattr(br, "_intake_fields",
                        lambda: types.SimpleNamespace(_extract_fields=lambda *a, **k: fake_fields))
    monkeypatch.setattr(br, "_build_locked_facts", lambda *a, **k: list(new_facts))
    monkeypatch.setattr(br, "_load_flyer_cfg", lambda: None)
    return new_facts


# ── (1) design-flavored re-roll ──────────────────────────────────────────────
def test_design_flavored_reroll_routes_to_reroll(monkeypatch):
    cap = _install(monkeypatch)
    status, payload = br.render_iteration(CHAT, "I don't like this design, generate again", sender_phone=SENDER)
    assert status == br.REROLL
    assert payload == b"ITER_PNG"
    assert cap["poster"][0]["facts"] == ["business_name", "campaign_title", "pricing_structure"]
    assert cap["poster"][0]["scene"] is None    # pure re-roll: no skill scene
    assert cap["advise_calls"] == []            # no skill call for a pure re-roll


# ── (2) specific_revision: facts preserved, skill scene applied ──────────────
def test_specific_revision_preserves_facts_and_applies_scene(monkeypatch):
    cap = _install(monkeypatch, advise_vd=_VD)
    status, _ = br.render_iteration(
        CHAT, "redesign this, make it more graduation themed with two students turning back", sender_phone=SENDER)
    assert status == br.ITERATION_REVISED
    p = cap["poster"][0]
    assert p["fact_values"]["business_name"] == "Lakshmi's Kitchen"
    assert p["fact_values"]["campaign_title"] == "2026 Graduation Parties"   # preserved EXACTLY
    assert p["fact_values"]["pricing_structure"] == "10% off on entire order"
    assert p["scene"] is _VD                                                 # skill-updated direction
    assert any("two students turning back" in c for c in cap["advise_calls"])


def test_specific_revision_no_skill_direction_asks_one_question(monkeypatch):
    cap = _install(monkeypatch, advise_vd=None)    # skill couldn't produce direction
    status, payload = br.render_iteration(CHAT, "make it nicer", sender_phone=SENDER)
    assert status == br.ITERATION_UNCLEAR
    assert payload is None
    assert cap["poster"] == []                     # no no-op render


# ── (3) style_reuse: new facts, old not copied ──────────────────────────────
def test_style_reuse_extracts_new_facts_not_old(monkeypatch):
    cap = _install(monkeypatch, advise_vd=_VD)
    _install_new_facts(monkeypatch, title="Weekend Breakfast")
    status, _ = br.render_iteration(
        CHAT, "Use this design, same theme, create a weekend breakfast flyer for Lakshmi's",
        sender_phone=SENDER, message_id="m2")
    assert status == br.ITERATION_STYLE_REUSE
    vals = cap["poster"][0]["fact_values"]
    assert vals["campaign_title"] == "Weekend Breakfast"          # NEW facts
    assert vals["campaign_title"] != "2026 Graduation Parties"    # old graduation facts NOT copied
    assert "pricing_structure" not in vals                        # old offer NOT carried over


# ── (4) no/stale session -> one question ────────────────────────────────────
def test_no_session_asks_one_question(monkeypatch):
    cap = _install(monkeypatch, session=None)
    status, payload = br.render_iteration(CHAT, "make it more festive", sender_phone=SENDER)
    assert status == br.ITERATION_UNCLEAR
    assert payload is None and cap["poster"] == []


def test_stale_session_asks_one_question(monkeypatch):
    from datetime import timedelta
    old = (datetime.now(timezone.utc) - timedelta(hours=br._REROLL_MAX_AGE_HOURS + 1)).isoformat()
    cap = _install(monkeypatch, session=_grad_session(sent_at=old))
    status, _ = br.render_iteration(CHAT, "use blue and gold", sender_phone=SENDER)
    assert status == br.ITERATION_UNCLEAR and cap["poster"] == []


# ── (5) flag off -> REVISION_NEEDED ─────────────────────────────────────────
def test_flag_off_returns_revision_needed(monkeypatch):
    cap = _install(monkeypatch)
    monkeypatch.setattr(br, "ITERATION_ENABLED", False)
    status, _ = br.render_iteration(CHAT, "make it more festive", sender_phone=SENDER)
    assert status == br.REVISION_NEEDED            # caller keeps today's resend reply
    assert cap["poster"] == []


# ── classifier + copy units ─────────────────────────────────────────────────
def test_style_reuse_detector():
    assert br._is_style_reuse("Use this design, create a weekend breakfast flyer") is True
    assert br._is_style_reuse("use the same theme to make a combo flyer") is True
    assert br._is_style_reuse("redesign this, two students turning back") is False
    assert br._is_style_reuse("make it more graduation themed") is False
    assert br._is_style_reuse("generate again") is False


def test_unclear_reply_is_a_question_not_resend_full_details():
    assert "resend the full" not in br.ITERATION_UNCLEAR_REPLY.lower()
    assert "generate again" in br.ITERATION_UNCLEAR_REPLY


def test_render_failure_failcloses(monkeypatch):
    cap = _install(monkeypatch, advise_vd=_VD)

    def _boom(project, *, strict_note="", raw_bg_dest=None, scene_direction=None):
        raise br._render_mod().FlyerRenderError("boom")
    monkeypatch.setattr(br, "_generate_poster", _boom)
    status, payload = br.render_iteration(CHAT, "use blue and gold accents", sender_phone=SENDER)
    assert status == br.FAILCLOSED
    assert "revision_render_error:FlyerRenderError" in " ".join(payload)


# ── campaign-title safety (operator point 5/6 — keep these regressions) ───────
def test_campaign_title_safety_rejects_degenerate_keeps_real():
    from facts import _normalize_campaign_title as norm
    # extraction garbage -> no title (renderer falls back), NEVER a bare "A"
    for junk in ("A", "a", "the", "this", "create", "make", "flyer", "A flyer", "the poster"):
        assert norm(junk) == "", junk
    # real titles survive on-theme (graduation / breakfast / combo / occasion)
    assert norm("2026 Graduation Parties") == "2026 Graduation Parties"
    assert norm("Weekend Breakfast") == "Weekend Breakfast"
    assert norm("Veg & Non-Veg Combo") == "Veg & Non-Veg Combo"
    assert norm("Memorial Day Sale") == "Memorial Day Sale"
    # trailing-"flyer" strip still works for a real title
    assert norm("2026 Graduation Parties flyer") == "2026 Graduation Parties"
