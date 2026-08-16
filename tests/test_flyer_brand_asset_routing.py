"""Brand-asset capture authorization — an active Flyer project is CONTEXT, not
EVIDENCE that an attached image is a brand asset.

The defect these cells pin (`hooks.py` brand-asset arm, pre-fix):

    explicit_asset_words = any(word in lower for word in
        ("logo", "template", "sample", "reference", "brand", "replace"))
    is_brand_asset = active_project is not None or explicit_asset_words

Two independent ways that authorized a durable write to the customer's
brand-asset store:

  * **active project alone.** Any photo a customer sent while a flyer project
    was open — an uncaptioned image, a food photo, a picture of the storefront,
    a receipt — was copied into the brand-asset store and attached to every
    later generation prompt. No pixels inspected, no caption required.
  * **six context-dependent substrings.** "replace chicken with paneer" is not
    a logo upload; "sample menu" and "brand new menu" are menu photos, not
    brand art.

The fix reuses the deployed classifier, `reference_extract.classify_reference_role`
— which had zero cf-router call sites — for the routing judgment, and keeps a
deterministic sufficiency gate in cf-router for the decision to MUTATE the
store. The decision shape asserted here:

    receipt candidate      -> receipt cession (unchanged)
    menu candidate         -> menu cession (unchanged)
    explicit flyer edit    -> flyer edit/reference path (unchanged)
    clear brand asset      -> brand asset store
    ambiguous media        -> normal routing (return None; no mutation)
    classifier failure     -> NO brand mutation, and no reply from this arm
    active project only    -> NEVER sufficient

`should_start_new_flyer_over_active` is stubbed False throughout on purpose:
these cells are about the brand-asset authorization decision itself, so the
upstream new-work-order yield must not mask it. Where the real function would
also have declined the turn, the cell says so.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLUGIN_DIR = SRC / "plugins" / "cf-router"
for _p in (SRC, SRC / "platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

PHONE = "+15550100001"
CHAT = "15550100001@lid"
MEDIA = "/opt/shift-agent/.hermes/image_cache/img_deadbeef.jpg"
PDF_MEDIA = "/opt/shift-agent/.hermes/document_cache/doc_deadbeef.pdf"

ACTIVE_PROJECT = {"project_id": "F0048", "status": "awaiting_concept_selection"}


def _load_plugin_modules():
    """Load hooks + actions as submodules of a synthetic package (mirrors the
    loader in tests/test_cf_router_flyer_routing.py so co-resident flyer suites
    keep their own `schemas` / `safe_io` bindings)."""
    pkg_name = "cf_router_brand_asset_pkg_under_test"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]

    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg_name] = importlib.util.module_from_spec(pkg_spec)

    def _load(name):
        full = f"{pkg_name}.{name}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{name}.py"))
        spec = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        loader.exec_module(mod)
        return mod

    actions_mod = _load("actions")
    hooks_mod = _load("hooks")
    return hooks_mod, actions_mod


class _Wiring:
    def __init__(self):
        self.store_calls: list[dict] = []
        self.sends: list[str] = []
        self.audits: list[dict] = []
        self.updates: list[tuple] = []


def _wire(monkeypatch, hooks, actions, *, active_project=None, role="customer") -> _Wiring:
    w = _Wiring()
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: (PHONE, role))
    # Isolated on purpose — see module docstring.
    monkeypatch.setattr(actions, "should_start_new_flyer_over_active",
                        lambda _t, has_media=False: False)
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender",
                        lambda _p, _c: active_project)
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender",
                        lambda _p, _c: {"customer_id": "CUST0001", "status": "trial"})

    def _store(**kwargs):
        w.store_calls.append(kwargs)
        return True, "ok", {
            "handled": True,
            "reply_text": "Flyer Studio\n------------\nLogo saved.",
            "next_status": "brand_asset_saved",
            "customer_id": "CUST0001",
        }

    monkeypatch.setattr(actions, "trigger_store_flyer_brand_asset", _store)
    monkeypatch.setattr(actions, "send_flyer_text",
                        lambda _c, msg, **_kw: (w.sends.append(msg), (True, "ack-mid", ""))[1])
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: w.audits.append(kwargs))
    monkeypatch.setattr(actions, "invoke_update_flyer_project",
                        lambda *args: (w.updates.append(args), (True, "{}"))[1])
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts",
                        lambda project_id: (True, f"generated {project_id}"))
    monkeypatch.setattr(actions, "_dispatch_concept_preview_send",
                        lambda _c, project_id: (True, "preview-mid", ""))
    monkeypatch.delenv("FLYER_BRAND_STYLE_TRANSFER", raising=False)
    return w


def _intercept(hooks, text, *, media_path=MEDIA, owner_receipt_candidate=False,
               menu_caption_candidate=False):
    return hooks._try_flyer_brand_asset_intercept(
        text, CHAT, {"message_id": "m-brand-asset"}, media_path,
        owner_receipt_candidate=owner_receipt_candidate,
        menu_caption_candidate=menu_caption_candidate,
    )


# ---------------------------------------------------------------------------
# An active project is context, never evidence.
# ---------------------------------------------------------------------------

def test_active_project_plus_arbitrary_customer_photo_is_not_a_brand_asset(monkeypatch):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "Here's the picture I took yesterday")

    assert w.store_calls == []
    assert result is None  # falls through to normal flyer routing
    assert w.sends == []


def test_active_project_plus_food_or_event_photo_is_not_a_brand_asset(monkeypatch):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "This is our Diwali sweets counter last weekend")

    assert w.store_calls == []
    assert result is None
    assert w.sends == []


def test_active_project_plus_uncaptioned_image_is_not_silently_a_brand_asset(monkeypatch):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "")

    assert w.store_calls == []
    assert result is None
    assert w.sends == []


def test_active_project_plus_source_preserving_edit_stays_on_the_edit_path(monkeypatch):
    """An exact-edit request against the attached flyer belongs to the flyer
    edit/reference path. Pre-fix the active project alone captured it as brand
    art before that path was ever reached."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(
        hooks,
        "Remove that extra 08:00 from this uploaded flyer. Do not change anything else.",
    )

    assert w.store_calls == []
    assert result is None
    assert w.sends == []


# ---------------------------------------------------------------------------
# Context-dependent words are not standalone evidence.
# ---------------------------------------------------------------------------

def test_replace_x_with_y_is_not_a_brand_asset(monkeypatch):
    """`replace` was one of the six substrings. "replace chicken with paneer" is
    a menu edit, not a logo upload."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions)

    result = _intercept(hooks, "Replace chicken with paneer")

    assert w.store_calls == []
    assert result is None


def test_sample_menu_is_not_a_brand_asset(monkeypatch):
    """`sample` was one of the six substrings; a sample menu is menu media."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions)

    result = _intercept(hooks, "Here's a sample menu")

    assert w.store_calls == []
    assert result is None


def test_brand_new_menu_is_not_a_brand_asset(monkeypatch):
    """`brand` was one of the six substrings, and it matched "brand new"."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions)

    result = _intercept(hooks, "Here's our brand new menu")

    assert w.store_calls == []
    assert result is None


# ---------------------------------------------------------------------------
# A clear brand asset is still captured.
# ---------------------------------------------------------------------------

def test_explicit_logo_upload_is_a_brand_asset(monkeypatch):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions)

    result = _intercept(hooks, "Here is our new logo")

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"
    assert w.sends  # the customer is acknowledged


def test_explicit_template_request_is_a_brand_asset(monkeypatch):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions)

    result = _intercept(hooks, "Please use this template for all our flyers")

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


def test_explicit_reference_art_request_is_a_brand_asset(monkeypatch):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions)

    result = _intercept(
        hooks, "Here is our old flyer — please use it as the reference artwork for our brand")

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


def test_brand_asset_with_active_project_still_regenerates(monkeypatch):
    """Authorization changed; what happens once authorized did not."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "Here is our new logo")

    assert len(w.store_calls) == 1
    assert w.updates  # the open project picks the new asset up
    assert result is not None and result["action"] == "skip"


def test_pdf_letterhead_template_is_still_a_brand_asset(monkeypatch):
    """A non-image brand asset is unsupported for REFERENCE EXTRACTION but is a
    perfectly good stored template — the classifier's media verdict must not
    veto an explicit brand-artifact request."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions)

    result = _intercept(hooks, "Our new letterhead template", media_path=PDF_MEDIA)

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


# ---------------------------------------------------------------------------
# Cessions and identity cases that prior incidents produced stay correct.
# ---------------------------------------------------------------------------

def test_menu_caption_candidate_still_cedes(monkeypatch):
    """F0226 class — the menu photo belongs to `update_catering_menu`."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT, role="employee")

    result = _intercept(hooks, "new menu with our logo", menu_caption_candidate=True)

    assert w.store_calls == []
    assert result is None


def test_owner_receipt_candidate_still_cedes(monkeypatch):
    """B0009/B0010 class — the owner's receipt belongs to the expense path."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT, role="employee")

    result = _intercept(hooks, "expense receipt", owner_receipt_candidate=True)

    assert w.store_calls == []
    assert result is None


def test_b0009_live_inbound_is_no_longer_captured_as_a_logo(monkeypatch):
    """The verbatim 2026-08-10 inbound. The sender resolved as employee `e008`
    with no owner capability, so the receipt cession refused it (that gap is
    separate and still open) — but the brand-asset arm captured it anyway on the
    strength of the open project alone, filed it as `kind="logo"`, and told the
    sender "Logo saved and will be used for future flyers."

    The receipt still does not reach the expense path here; what changed is that
    it is no longer written into the brand-asset store on the way past.
    """
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT, role="employee")

    result = _intercept(hooks, "Expense receipt review this")

    assert w.store_calls == []
    assert result is None
    assert w.sends == []


def test_dual_role_principal_can_still_upload_a_logo(monkeypatch):
    """The legacy scalar is frozen at `employee` for a principal holding both
    memberships. Neither cession claims this turn, so a genuine logo upload from
    that principal must still be captured."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, role="employee")

    result = _intercept(hooks, "Here is our new logo")

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


def test_owner_scalar_is_still_exempt(monkeypatch):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT, role="owner")

    result = _intercept(hooks, "Here is our new logo")

    assert w.store_calls == []
    assert result is None


# ---------------------------------------------------------------------------
# Fail closed.
# ---------------------------------------------------------------------------

def test_classifier_failure_makes_no_brand_mutation_and_no_reply(monkeypatch):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    def _boom(_text, _media_path):
        raise RuntimeError("classifier unavailable")

    monkeypatch.setattr(hooks, "_classify_flyer_reference_role", _boom)

    result = _intercept(hooks, "Here is our new logo")

    assert w.store_calls == []
    assert w.sends == []  # no reply from this arm — no double reply downstream
    assert result is None
    assert any(row.get("reason") == "flyer_brand_asset_classifier_unavailable"
               for row in w.audits)
