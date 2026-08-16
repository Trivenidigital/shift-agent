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


# `classify_reference_role`'s `menu_reference` branch fires on the SINGULAR noun
# "flyer" with no menu or price semantics anywhere in the caption
# (`reference_extract.py:135`: `(?:sample|reference|attached|uploaded|this)
# .{0,30}(?:flyer|menu|price\s*list)`). Vetoing on the role alone therefore
# dropped six realistic template uploads that the pre-fix code did capture — the
# false-negative class. `\bflyer\b` does not match "flyers", which is why a
# plural-only positive cell hid this; both forms are pinned below.
FLYER_TEMPLATE_CAPTIONS = [
    "this is the flyer template we use",
    "use this flyer as our template going forward",
    "sample flyer we like - use this style for our brand",
    "attached is the flyer template for our brand",
    "this flyer template is what we always use",
    "please save this as our flyer template",
]


@pytest.mark.parametrize("caption", FLYER_TEMPLATE_CAPTIONS)
def test_flyer_template_upload_is_a_brand_asset(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


@pytest.mark.parametrize("caption", [
    "Please use this template for all our flyers",   # plural
    "please use this template for our flyer",        # singular — the failing form
    "here is our old flyer, use it as reference artwork",   # singular
    "here are our old flyers, use them as reference artwork",  # plural
])
def test_template_and_reference_art_captured_in_both_number_forms(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions)

    result = _intercept(hooks, caption)

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


# Real captions from `state/flyer/customers.json` on the box (B0004, B0006,
# B0008 — B0008 is the ONLY brand asset active in production today). A durable,
# forward-looking styling instruction names no logo and no template, so a
# noun-only evidence rule silently drops the operator's actual brand uploads.
# The durability is the evidence, and it is caption evidence, not project
# context — an active project is still never sufficient.
@pytest.mark.parametrize("caption", [
    "I'd like you to use the same theme and style for all flyers going forward for Lakshmi's Kitchen",
    "For all Lakshmi's kitchen flyers use this theme going forward.",
    "I want you to change theme of my fliers going forward, can you follow theme like this",
])
def test_durable_brand_styling_instruction_is_a_brand_asset(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


# A durable styling caption is only a donation when it POINTS AT the attachment.
# "use this theme going forward" hands over the attached art; "always use a
# bigger font going forward" and "our fonts always look bad" describe a desired
# or defective property and donate nothing. The distinction is a deictic bound
# to the styling noun — a donation says *this one*, a directive says *do it this
# way* — which is why "in future the design should not be this dark" declines
# even though it contains the word "this".
DURABLE_NON_DONATIONS = [
    # durable COMPLAINTS about styling
    "the colors are always wrong on these flyers",
    "our fonts always look bad",
    "the design always comes out too dark",
    "the theme is always off",
    "our flyers always look too busy, fix the style",
    "the style is always wrong, correct it going forward",
    "going forward the palette needs to be warmer",
    "from now on the style should be less busy",
    "in future the design should not be this dark",
    "the look came out wrong again, always check the colors",
    "always fix the colours before sending",
    "the theme was too dark, from now on lighten it",
    # durable DIRECTIVES donating no artifact at all
    "always use a bigger font going forward",
    "from now on use our red and gold colors",
    "going forward keep the design minimal",
    "always match the style of our website",
    # imperative-durable non-donations that already held
    "always put our phone number on the flyer",
    "from now on make them brighter",
    "use this layout for every store",
]


@pytest.mark.parametrize("caption", DURABLE_NON_DONATIONS)
def test_durable_caption_that_donates_nothing_is_not_a_brand_asset(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert w.store_calls == []
    assert result is None
    assert w.sends == []


@pytest.mark.parametrize("caption", [
    "this flyer has no logo",
    "do not use our logo on this",
    "the logo is missing from this poster",
])
def test_negated_logo_caption_is_not_a_brand_asset(monkeypatch, caption):
    """The classifier's `logo` branch has no negation handling, so these reached
    `role_logo` and walked straight past the defect-frame guard added for
    exactly this shape. The guard now runs first."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert w.store_calls == []
    assert result is None
    assert w.sends == []


# A complaint points at the attachment just as readily as a donation does, so
# the deictic discriminates nothing on its own. Every one of these binds a
# deictic to a styling noun and still donates nothing — and the defect frame
# misses most of them ("comes out" vs "came out", "fixing" vs "fix", "ends up",
# "print badly", "disappoints", "renders poorly"). That list has no end, which
# is the point: durability-only evidence now requires an AFFIRMATIVE offer
# rather than the mere absence of a complaint, restoring the closable-side rule
# the rest of the gate already follows.
DURABLE_DEICTIC_COMPLAINTS = [
    "this design always comes out too dark",
    "this style always ends up too busy",
    "this theme always needs fixing",
    "these colours always print badly",
    "this look always disappoints going forward",
    "this design always renders poorly",
]


@pytest.mark.parametrize("caption", DURABLE_DEICTIC_COMPLAINTS)
def test_durable_complaint_pointing_at_the_attachment_still_declines(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert w.store_calls == []
    assert result is None
    assert w.sends == []


# The mirror of the rows above: same shape, an offer instead of a complaint.
# Added deliberately as VARIANTS of existing rows — the previous corpus was
# uniformly deictic-free, so it proved only half the class.
@pytest.mark.parametrize("caption", [
    "use this design going forward",
    "please use these colours from now on",
    "keep this theme going forward",
])
def test_durable_offer_pointing_at_the_attachment_is_captured(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


# Three descriptive adjectives is ordinary phrasing. B0004 survived only by
# saying "the same theme" with zero words in between.
@pytest.mark.parametrize("caption", [
    "use the same clean modern minimal theme going forward",
    "please follow this bright festive traditional style going forward",
    "from now on use the same warm earthy south indian palette",
])
def test_durable_donation_with_adjectives_before_the_noun_is_captured(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


# A possessive appears in complaints as freely as in donations, so the
# supplied-artifact rule is bound to a donating context: a substitution verb
# acting on a DEICTIC object. "replace this with our logo" donates; "the flyer
# with our logo is wrong" merely mentions one.
@pytest.mark.parametrize("caption", [
    "the flyer with our logo is wrong",
    "the banner with our watermark came out blurry",
    "replace the date with our template date",
])
def test_possessive_artifact_in_a_complaint_is_not_an_offer(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert w.store_calls == []
    assert result is None


# Regression guard: a negation elsewhere in a genuine donation must not
# suppress the offer.
@pytest.mark.parametrize("caption", [
    "here is our logo, do not stretch it",
    "use this template going forward, never change the colors",
    "save our letterhead, don't crop it",
])
def test_negation_elsewhere_does_not_suppress_a_genuine_offer(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


def test_durable_style_near_miss_decline_is_countable(monkeypatch):
    """A durable styling caption that fails to qualify used to decline as plain
    `no_brand_asset_evidence` — silent and uncountable, which is the exact
    property that made the first false-negative class expensive to find."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "this design always comes out too dark")

    assert w.store_calls == []
    assert result is None
    rows = [r for r in w.audits
            if r.get("reason") == "flyer_brand_asset_declined_over_evidence"]
    assert len(rows) == 1
    assert "durable_style_without_donation" in rows[0]["detail"]


def test_durable_style_capture_is_audited(monkeypatch):
    """Defense in depth. Complaint phrasing is open-ended, so a fourth leak
    shape is possible; auditing the CAPTURES of this one class is what makes a
    residual leak detectable in production instead of silent."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions)

    result = _intercept(hooks, "use this theme going forward")

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"
    saved = [r for r in w.audits if r.get("reason") == "flyer_brand_asset_saved"]
    assert saved, "capture must still emit its saved row"
    assert any("durable_style_donation=true" in r.get("detail", "") for r in saved)


def test_non_durable_capture_is_not_marked_durable(monkeypatch):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions)

    _intercept(hooks, "Here is our new logo")

    saved = [r for r in w.audits if r.get("reason") == "flyer_brand_asset_saved"]
    assert saved
    assert not any("durable_style_donation=true" in r.get("detail", "") for r in saved)


def test_supplied_artifact_survives_its_edit_verb(monkeypatch):
    """"replace this with our logo" reads as an edit, but the edit verb acts on
    something else and the artifact is what is being handed over. Ordering the
    defect-frame check above `role_logo` broke this — it is pinned by a
    pre-existing regression guard in the menu-cession suite, and now here too so
    the interaction is visible where the rule lives."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "replace this with our logo")

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


def test_edit_that_supplies_nothing_still_declines(monkeypatch):
    """The other side of it — an edit naming the artifact but supplying no
    replacement is not a donation."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "replace the logo with a bigger one")

    assert w.store_calls == []
    assert result is None


def test_durable_donation_pointing_at_the_attachment_is_captured(monkeypatch):
    """The other side of the same rule — a durable instruction that DOES point at
    the attached art stays captured."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "this is the design we always use for our flyers")

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


def test_one_off_style_request_is_not_a_brand_asset(monkeypatch):
    """B0007's real caption. A style reference for THIS flyer carries no durable
    scope, so it is ambiguous media and belongs to normal routing — it was
    filed as a `logo` in production, which it plainly is not."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "Can you redesign in this style")

    assert w.store_calls == []
    assert result is None


# A caption that NAMES a brand artifact is not the same as one that OFFERS it.
# "remove the watermark" and "the existing flyer has a typo" name one and
# complain about it; capturing those recreates the B0009 shape, and it is not
# inert — `_brand_asset_kind` (`onboarding.py:1155`) files anything containing
# "flyer" as `template`, and an active template steers `render.py` on every
# later render (the F0217 wrong-brand vector when style transfer is off).
@pytest.mark.parametrize("caption", [
    "remove the watermark",
    "the watermark is too dark",
    "the existing flyer has a typo",
    "our old flyer looks bad, make a new one",
    "the previous poster had the wrong price",
    "our brand colors are wrong on this flyer",
])
def test_complaint_about_a_brand_artifact_is_not_an_upload(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert w.store_calls == []
    assert result is None
    assert w.sends == []


def test_defect_frame_decline_that_carried_evidence_is_audited(monkeypatch):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "the existing flyer has a typo")

    assert w.store_calls == []
    assert result is None
    rows = [r for r in w.audits
            if r.get("reason") == "flyer_brand_asset_declined_over_evidence"]
    assert len(rows) == 1


def test_durable_instruction_survives_its_own_edit_verb(monkeypatch):
    """B0008's real caption uses "change". A defect frame must not veto a
    caption that also offers the media for durable use, or the production store
    loses the asset it is actually made of.

    Pinned VERBATIM. An earlier version of this cell used a truncation that
    stopped before "follow theme like this" — which is precisely the clause that
    points at the attachment, so the truncation is a durable DIRECTIVE and now
    correctly declines. Paraphrasing a production caption changes what it is."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(
        hooks,
        "I want you to change theme of my fliers going forward, "
        "can you follow theme like this")

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


def test_durable_directive_without_the_deictic_declines(monkeypatch):
    """The other half of the pair above — same caption, deictic clause removed.
    It asks for a change without donating the attachment, so it routes normally
    rather than writing a durable template."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(
        hooks, "I want you to change theme of my fliers going forward")

    assert w.store_calls == []
    assert result is None


# `\btemplates?\b` is bare, so it matched menu media too. Menu/price semantics
# now decline regardless of which role the classifier assigned.
@pytest.mark.parametrize("caption", [
    "our menu template",
    "template of our menu",
    "price list template",
])
def test_menu_sense_template_is_not_a_brand_asset(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert w.store_calls == []
    assert result is None


# The adjective-position family: every one of these authorized under the old
# six-substring rule. Keeping them declined is load-bearing.
@pytest.mark.parametrize("caption", [
    "brand new signage",
    "our brand ambassador photo",
    "brand awareness campaign pic",
    "we are rebranding",
    "our branded cups",
    "sample of the dosa",
    "reference photo of the shop",
])
def test_adjective_position_and_bare_word_collisions_stay_declined(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert w.store_calls == []
    assert result is None


@pytest.mark.parametrize("caption", [
    "this is our branding",
    "our poster template",
    "save this template",
    "use this template for our flyers",   # plural
    "use this template for our flyer",    # singular — the FN class
])
def test_genuine_offers_are_captured_in_both_number_forms(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert len(w.store_calls) == 1
    assert result is not None and result["action"] == "skip"


# The menu/price side of the veto must hold exactly as firmly as the positives.
@pytest.mark.parametrize("caption", [
    "sample menu template",
    "this menu template - extract the prices",
    "extract the items from this menu",
    "our new price list attached",
    "take the items from this price list",
    "extract prices from this attached menu",
    "use this as a reference",
    "sample",
    "brand",
    "always add our phone number to the flyer",
])
def test_menu_price_and_bare_word_captions_are_not_brand_assets(monkeypatch, caption):
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, caption)

    assert w.store_calls == []
    assert result is None


def test_brand_asset_with_active_project_still_regenerates(monkeypatch):
    """Authorization changed; what happens once authorized did not."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "Here is our new logo")

    assert len(w.store_calls) == 1
    assert w.updates  # the open project picks the new asset up
    assert result is not None and result["action"] == "skip"


def test_pdf_media_does_not_veto_an_explicit_brand_artifact_request(monkeypatch):
    """Scope: cf-router AUTHORIZATION only — this asserts the arm reaches the
    store, not that a PDF is storable.

    `classify_reference_role` returns `unsupported` for a non-image because it
    cannot run REFERENCE EXTRACTION on one; that verdict must not be read as
    "this is not a brand asset", since the caption explicitly names a template.
    Production still refuses the file one layer down: `_sniff_brand_asset_media`
    (`onboarding.py:1201`) accepts only JPEG/PNG/WEBP magic and raises on
    `%PDF`, so the store exits non-zero and the arm takes its
    `flyer_brand_asset_failed` branch."""
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

def test_decline_that_overrode_brand_evidence_is_audited(monkeypatch):
    """"sample menu template" names a menu AND a template. The menu veto wins —
    per the ruling, genuinely ambiguous media goes to normal routing, not to the
    store — but a decline that overrode explicit brand evidence is the
    false-negative class, so production must be able to count it."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "sample menu template")

    assert w.store_calls == []
    assert result is None
    assert w.sends == []
    rows = [r for r in w.audits
            if r.get("reason") == "flyer_brand_asset_declined_over_evidence"]
    assert len(rows) == 1
    assert "menu_price_semantics_over_evidence" in rows[0]["detail"]


def test_ordinary_no_evidence_decline_is_not_audited(monkeypatch):
    """The common case stays silent, or the countable class drowns in it."""
    hooks, actions = _load_plugin_modules()
    w = _wire(monkeypatch, hooks, actions, active_project=ACTIVE_PROJECT)

    result = _intercept(hooks, "Here's the picture I took yesterday")

    assert w.store_calls == []
    assert result is None
    assert w.audits == []


def test_classify_reference_role_reads_only_mime_type():
    """Pin the classifier's attribute surface.

    cf-router hands it a MIME-only stand-in rather than a real `FlyerAsset`, to
    avoid sha256-ing the media on the inbound path. The classifier is shared
    with `create-flyer-project`, which DOES pass a real `FlyerAsset` — so a
    future field read there is invisible in that call site and becomes an
    AttributeError on the live inbound path. This fails the moment that happens.
    """
    from agents.flyer.reference_extract import classify_reference_role

    class _AttributeProbe:
        def __init__(self, mime_type):
            object.__setattr__(self, "touched", set())
            object.__setattr__(self, "_mime_type", mime_type)

        def __getattr__(self, name):
            self.touched.add(name)
            if name == "mime_type":
                return self._mime_type
            raise AttributeError(name)

    # One caption per branch of the classifier, so every path is walked.
    captions = [
        "Here is our new logo",
        "Remove that extra 08:00 from this uploaded flyer",
        "extract items from this attached menu",
        "use this as a reference",
        "Here's the picture I took yesterday",
        "",
    ]
    touched: set[str] = set()
    for caption in captions:
        for mime in ("image/jpeg", "application/pdf", ""):
            probe = _AttributeProbe(mime)
            classify_reference_role(caption, probe)
            touched |= probe.touched

    assert touched == {"mime_type"}, f"classifier now reads {sorted(touched)}"


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
