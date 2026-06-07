"""Slice 1 — Hermes skill `visual_direction` drives the INTEGRATED renderer's scene block.

Binding tests (operator correction 5, 2026-06-07):
  1. graduation visual_direction -> scene block has graduation visual language (caps/diplomas/stage/...)
  2. graduation scene block steers AWAY from family-dinner/food-table composition
  3. food visual_direction -> food subjects are the hero (food/product-closeup composition)
  4. advise_scene_direction() returns None on disabled/unreachable/unparseable/empty -> caller falls back
  5. gate is decoupled from FLYER_CREATIVE_DIRECTOR_ENABLED; off/not-allowlisted -> no skill call, no CD
  6. _image_prompt with a scene_direction still injects the exact controlled facts (no fact loss)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

_SRC = Path(__file__).resolve().parent.parent / "src"
for _p in (_SRC / "platform", _SRC / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import render  # noqa: E402
import bare_render as br  # noqa: E402
import flyer_context_builder as CB  # noqa: E402
from flyer_brief import VisualDirection  # noqa: E402
from schemas import FlyerProject, FlyerLockedFact, FlyerRequestFields  # noqa: E402


_GRAD_VD = VisualDirection(
    theme_family="graduation celebration",
    palette=["royal blue", "gold"],
    motifs=["mortarboard caps", "diploma scrolls", "balloons"],
    visual_subjects=["graduates in caps and gowns", "celebration stage", "confetti"],
)
_FOOD_VD = VisualDirection(
    theme_family="South Indian weekend breakfast",
    palette=["warm cream", "banana-leaf green"],
    motifs=["banana leaf"],
    visual_subjects=["idli", "crispy dosa", "vada", "filter coffee"],
)


def _project(business="Lakshmi's Kitchen", title="2026 Graduation Parties"):
    now = datetime(2026, 6, 7, tzinfo=timezone.utc)
    return FlyerProject(
        project_id="F0200", status="awaiting_final_approval", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="m",
        raw_request=f"Create a flyer for {business}",
        fields=FlyerRequestFields(event_or_business_name=business),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value=business,
                            source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value=title,
                            source="customer_text", required=True),
        ],
    )


# ── (1) graduation visual language ───────────────────────────────────────────
def test_graduation_scene_block_has_graduation_visual_language():
    block = render._scene_block_from_visual_direction(_GRAD_VD).lower()
    assert "graduation" in block            # theme
    assert "cap" in block                   # mortarboard caps
    assert "diploma" in block               # diploma scrolls
    assert "stage" in block                 # celebration stage
    assert "graduates" in block             # hero subjects are graduates


# ── (2) avoids family-dinner / food-table for an occasion ────────────────────
def test_graduation_scene_block_steers_away_from_food_table():
    block = render._scene_block_from_visual_direction(_GRAD_VD).lower()
    # the prompt explicitly tells the model NOT to default to a family-dinner/food-table composition
    assert "do not fall back to a generic family dinner" in block
    assert "food-table" in block
    # the positive hero is graduation, not a dining table
    assert "graduates in caps and gowns" in block
    assert "dining table" not in block.split("do not fall back")[0]  # not described as the hero


# ── (3) food intent keeps food/product-closeup composition ───────────────────
def test_food_scene_block_makes_food_the_hero():
    block = render._scene_block_from_visual_direction(_FOOD_VD).lower()
    assert "idli" in block and "dosa" in block          # the food items
    assert "hero of the composition" in block            # food is the hero -> product-closeup
    assert "rich, appealing detail" in block             # appetizing rendering


# ── (4) advisory function never fail-closes: None on every problem ───────────
def test_advise_scene_direction_returns_none_on_problems(monkeypatch):
    monkeypatch.setattr(CB, "_build_user_message", lambda *a, **k: "USER MSG")
    monkeypatch.setattr(CB, "_skill_body", lambda: "SKILL BODY")

    # gateway unreachable / disabled
    monkeypatch.setattr(CB, "_call_gateway", lambda sp, um: None)
    assert CB.advise_scene_direction("req", [], {}) is None
    # response without a visual_direction
    monkeypatch.setattr(CB, "_call_gateway", lambda sp, um: {"request_intent": "event"})
    assert CB.advise_scene_direction("req", [], {}) is None
    # empty visual_direction (no actual direction) -> fall back
    monkeypatch.setattr(CB, "_call_gateway", lambda sp, um: {"visual_direction": {}})
    assert CB.advise_scene_direction("req", [], {}) is None
    # thin partial: a theme but NO concrete subject/motif -> fall back (Codex; a weak prompt is worse)
    monkeypatch.setattr(CB, "_call_gateway", lambda sp, um: {"visual_direction": {"theme_family": "graduation"}})
    assert CB.advise_scene_direction("req", [], {}) is None
    # thin partial: a motif but NO theme -> fall back
    monkeypatch.setattr(CB, "_call_gateway", lambda sp, um: {"visual_direction": {"motifs": ["balloons"]}})
    assert CB.advise_scene_direction("req", [], {}) is None
    # palette alone is not enough taste -> fall back
    monkeypatch.setattr(CB, "_call_gateway", lambda sp, um: {"visual_direction": {"theme_family": "x", "palette": ["blue"]}})
    assert CB.advise_scene_direction("req", [], {}) is None
    # skill body unreadable
    monkeypatch.setattr(CB, "_call_gateway", lambda sp, um: {"visual_direction": {"theme_family": "x"}})
    monkeypatch.setattr(CB, "_skill_body", lambda: "")
    assert CB.advise_scene_direction("req", [], {}) is None
    # a raising gateway must NOT propagate
    monkeypatch.setattr(CB, "_skill_body", lambda: "SKILL")
    def _boom(sp, um):
        raise RuntimeError("gateway blew up")
    monkeypatch.setattr(CB, "_call_gateway", _boom)
    assert CB.advise_scene_direction("req", [], {}) is None


def test_advise_scene_direction_returns_visual_direction_on_success(monkeypatch):
    monkeypatch.setattr(CB, "_build_user_message", lambda *a, **k: "USER MSG")
    monkeypatch.setattr(CB, "_skill_body", lambda: "SKILL")
    monkeypatch.setattr(CB, "_call_gateway", lambda sp, um: {
        "visual_direction": {"theme_family": "graduation celebration",
                             "visual_subjects": ["graduates in caps and gowns"],
                             "motifs": ["caps"], "palette": ["blue"]},
        # extra brief fields must be ignored (only visual_direction is read)
        "request_intent": "event", "offer_structure": "ignored", "background_brief": "ignored",
    })
    vd = CB.advise_scene_direction("graduation flyer", [], {})
    assert vd is not None
    assert vd.theme_family == "graduation celebration"
    assert "graduates in caps and gowns" in vd.visual_subjects


# ── (5) gate decoupled from the CD flag; off/not-allowlisted -> no skill call ─
def test_skill_driven_scene_gate_matrix(monkeypatch):
    sender = "+17329837841"
    monkeypatch.delenv(br.SKILL_DRIVEN_SCENE_ENABLED_ENV, raising=False)
    monkeypatch.delenv(br.SKILL_DRIVEN_SCENE_ALLOWLIST_ENV, raising=False)
    assert br._skill_driven_scene_armed(sender) is False           # flag unset
    monkeypatch.setenv(br.SKILL_DRIVEN_SCENE_ENABLED_ENV, "1")
    assert br._skill_driven_scene_armed(sender) is False           # on, but empty allowlist
    monkeypatch.setenv(br.SKILL_DRIVEN_SCENE_ALLOWLIST_ENV, "19998887777")
    assert br._skill_driven_scene_armed(sender) is False           # on, sender NOT allowlisted
    monkeypatch.setenv(br.SKILL_DRIVEN_SCENE_ALLOWLIST_ENV, f"+{sender.lstrip('+')}, 19998887777")
    assert br._skill_driven_scene_armed(sender) is True            # on + allowlisted


def test_advisory_scene_direction_not_armed_never_calls_skill(monkeypatch):
    sender = "+17329837841"
    monkeypatch.delenv(br.SKILL_DRIVEN_SCENE_ENABLED_ENV, raising=False)
    # decoupled from the CD flag: even with CD enabled+allowlisted, scene gate is its own
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, sender)
    called = {"n": 0}
    monkeypatch.setattr(br, "_context_builder", lambda: (_ for _ in ()).throw(AssertionError("skill called!")))
    # not armed (scene flag unset) -> None, and _context_builder must never be invoked
    assert br._advisory_scene_direction("req", [], object(), sender) is None
    # also armed-but-skill-raises -> None (advisory, never propagates)
    monkeypatch.setenv(br.SKILL_DRIVEN_SCENE_ENABLED_ENV, "1")
    monkeypatch.setenv(br.SKILL_DRIVEN_SCENE_ALLOWLIST_ENV, sender)
    import types as _types
    monkeypatch.setattr(br, "_context_builder",
                        lambda: _types.SimpleNamespace(advise_scene_direction=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))))
    assert br._advisory_scene_direction("req", [], object(), sender) is None


# ── (6) facts are still injected when a scene_direction is provided ───────────
def test_image_prompt_with_scene_direction_keeps_controlled_facts():
    project = _project()
    prompt = render._image_prompt(project, concept_id="C1", output_format="concept_preview",
                                  size=(1080, 1350), scene_direction=_GRAD_VD)
    # the skill scene block is used...
    assert "Hermes skill art direction" in prompt
    assert "graduation" in prompt.lower() and "diploma" in prompt.lower()
    # ...AND the exact controlled facts are still injected (no fact loss)
    assert "Lakshmi's Kitchen" in prompt
    assert "2026 Graduation Parties" in prompt


def test_image_prompt_without_scene_direction_uses_python_path():
    project = _project()
    prompt = render._image_prompt(project, concept_id="C1", output_format="concept_preview",
                                  size=(1080, 1350), scene_direction=None)
    # fallback path: the skill scene marker is absent; facts still present
    assert "Hermes skill art direction" not in prompt
    assert "Lakshmi's Kitchen" in prompt
