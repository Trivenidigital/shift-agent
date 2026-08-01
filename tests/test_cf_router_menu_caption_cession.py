"""Menu-caption routing precedence — the owner/employee menu photo must reach
the `update_catering_menu` SKILL, not Flyer Studio.

Live defect (2026-08-01T17:04Z): the owner sent a menu photo captioned "Update
menu" to start the menu→pricebook pipeline. The cf-router flyer PRIMARY arm
(`should_start_new_flyer_over_active`, which returns True for any media message
whose caption contains "menu") claimed it first — created project F0226,
attached the photo as a `menu_reference` asset, ran concept generation, failed
(`concept_generation_failed` / vision `low_confidence`) and told the owner
"I couldn't finish this automatically". The Hermes dispatcher never ran, so the
catering SKILL (documented trigger: owner/verified-employee photo captioned
"update menu" / "new menu" / "menu" — `update_catering_menu/SKILL.md` lines 3
and 56) never fired. Audit rows: `cf_router_intercepted` reason=
flyer_primary_failed detail="project_id=F0226; sender_role=employee; ...".

These are DISPATCH-level cells (`_pre_gateway_dispatch_impl`) because the defect
is one of routing precedence: the unit under test is which arm claims the
inbound, not what any arm does once it has it. The loader is the non-evicting
one from tests/test_cf_router_qualification_loop.py so co-resident flyer suites
keep their own `schemas` / `safe_io` bindings.

What is pinned:
  * the exact live inbound (owner/employee + image + "Update menu") cedes: no
    flyer project is created, dispatch returns None (the Hermes dispatcher
    routes it), and a `menu_caption_ceded_to_dispatcher` marker is written
  * the other documented SKILL captions ("update menu please", bare "menu",
    "new menu", "menu update") cede the same way
  * a genuine flyer brief that merely mentions the word menu ("weekend flyer
    using our menu") + a reference image still goes to flyer — no cession
  * a CUSTOMER sending the same photo + caption is unchanged (the SKILL is
    owner/verified-employee only)
  * a media-less "update menu" text is unchanged (the SKILL needs a source file)
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

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
DOC_MEDIA = "/opt/shift-agent/.hermes/document_cache/doc_deadbeef_menu.pdf"

# The verbatim live caption (2026-08-01 F0226 incident).
INCIDENT_CAPTION = "Update menu"
# The remaining captions the SKILL frontmatter documents as triggers.
SKILL_CAPTIONS = ["update menu please", "menu", "new menu", "menu update", "Menu."]
# A genuine flyer brief that merely contains the word "menu".
FLYER_BRIEF = "weekend flyer using our menu"


def _load_plugin():
    """Load hooks + actions as submodules of a synthetic package (the plugin dir
    name has a hyphen). Non-evicting: `schemas` / `safe_io` are left alone so
    co-resident suites in the same process keep their bindings."""
    pkg = "cf_router_menu_caption_pkg"
    for mod_name in list(sys.modules):
        if mod_name == pkg or mod_name.startswith(pkg + "."):
            del sys.modules[mod_name]

    pkg_spec = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg] = importlib.util.module_from_spec(pkg_spec)

    loaded = {}
    for name in ("actions", "hooks"):
        full = f"{pkg}.{name}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{name}.py"))
        spec = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        loader.exec_module(mod)
        loaded[name] = mod
    return loaded["hooks"], loaded["actions"]


class _Spies:
    def __init__(self):
        self.audits: list[dict] = []
        self.primary_calls: list[dict] = []
        self.f7_calls: list[dict] = []

    @property
    def reasons(self) -> list[str]:
        return [a["reason"] for a in self.audits]


# Every hook that sits between dispatch entry and the flyer-primary arm. Each is
# a no-op here so the ONE decision under test — which arm claims the inbound —
# is not masked by an unrelated intercept reading state off disk.
_NEUTRALIZED_HOOKS = (
    "_try_f8_intercept",
    "_try_automation_control",
    "_try_revenue_route_clarification_choice",
    "_try_flyer_campaign_cta_intercept",
    "_try_flyer_quote_echo_choice",
    "_try_flyer_account_intercept",
    "_try_flyer_sample_prompt_request_intercept",
    "_try_flyer_regulated_account_guard",
    "_try_flyer_quote_echo_guard",
    "_try_flyer_intake_intercept",
    "_try_flyer_reference_scope_choice_intercept",
    "_try_flyer_source_vs_new_choice_intercept",
    "_try_flyer_reference_scope_authorization_intercept",
    "_try_flyer_brand_asset_intercept",
    "_try_flyer_existing_onboarding_intercept",
    "_try_amendment_conflict_intercept",
    "_try_flyer_active_project_intercept",
    "_try_flyer_delivery_state_guard",
    "_try_flyer_onboarding_intercept",
    "_try_catering_acceptance_intercept",
    "_try_revenue_route_clarification_start",
)


def _wire(monkeypatch, hooks_mod, actions_mod, *, role="employee"):
    """Arm the flyer path exactly as the live box had it and spy the two arms
    that can claim a menu photo: the flyer primary intercept and F7."""
    s = _Spies()

    for name in _NEUTRALIZED_HOOKS:
        monkeypatch.setattr(hooks_mod, name, lambda *_a, **_kw: None)

    monkeypatch.setattr(actions_mod, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions_mod, "is_flyer_workflow_enabled", lambda: True)
    monkeypatch.setattr(actions_mod, "begin_flyer_intent_shadow", lambda **_kw: None)
    monkeypatch.setattr(actions_mod, "finalize_flyer_intent_shadow", lambda **_kw: None)
    monkeypatch.setattr(actions_mod, "reset_flyer_intent_shadow", lambda _t: None)
    monkeypatch.setattr(actions_mod, "finalize_flyer_intake_bypass_shadow", lambda **_kw: None)
    monkeypatch.setattr(actions_mod, "consume_pending_flyer_intake_bypass_token", lambda: None)
    monkeypatch.setattr(actions_mod, "reset_flyer_intake_bypass_shadow", lambda _t: None)
    monkeypatch.setattr(actions_mod, "mark_cf_router_inbound_seen", lambda *_a, **_kw: False)
    monkeypatch.setattr(actions_mod, "audit_raw_body", lambda *_a, **_kw: None)
    monkeypatch.setattr(actions_mod, "is_owner_chat", lambda _cid: role == "owner")
    monkeypatch.setattr(actions_mod, "is_verified_employee_chat", lambda _cid: False)
    monkeypatch.setattr(actions_mod, "front_brain_converse_admits", lambda _cid: False)
    monkeypatch.setattr(actions_mod, "flyer_campaign_cta_text", lambda _t: "")
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender", lambda _cid: (PHONE, role))
    monkeypatch.setattr(actions_mod, "find_paid_flyer_guest_order", lambda _p, _c: None)
    # No live flyer project / catering lead: the escape gate (#644) runs REAL and
    # falls through, which is what the live box did on this inbound.
    monkeypatch.setattr(actions_mod, "find_active_flyer_project_by_sender", lambda _p, _c: None)
    monkeypatch.setattr(actions_mod, "find_active_catering_lead_by_sender", lambda _p, _c: None)
    monkeypatch.setattr(actions_mod, "find_flyer_customer_by_sender", lambda _p, _c: {
        "customer_id": "CUST0001", "status": "active", "business_name": "Triveni",
    })
    monkeypatch.setattr(actions_mod, "recent_bare_flyer_for_chat", lambda _cid: False)
    monkeypatch.setattr(actions_mod, "audit_intercepted",
                        lambda **kw: s.audits.append(kw))
    monkeypatch.setattr(hooks_mod, "_sender_has_qualifying_lead", lambda _cid: False)

    def _primary(text, chat_id, event, **kw):
        s.primary_calls.append({"text": text, "chat_id": chat_id,
                                "media_path": kw.get("media_path"),
                                "force_new": kw.get("force_new")})
        return {"action": "skip", "reason": "cf-router flyer primary: project F0226 created"}

    def _f7(text, chat_id, event, **kw):
        s.f7_calls.append({"text": text, **kw})
        return {"action": "skip", "reason": "cf-router F7"}

    monkeypatch.setattr(hooks_mod, "_try_flyer_primary_intercept", _primary)
    monkeypatch.setattr(hooks_mod, "_try_f7_primary_intercept", _f7)
    return s


def _dispatch(hooks_mod, text, *, media_path=MEDIA):
    return hooks_mod._pre_gateway_dispatch_impl(SimpleNamespace(
        text=text, chat_id=CHAT, message_id="wamid.MENU0801",
        media_path=media_path,
    ))


# ── The live incident, as the regression cell ────────────────────────────────
@pytest.mark.parametrize("role", ["owner", "employee"])
def test_menu_photo_caption_cedes_to_dispatcher_instead_of_creating_a_flyer(
        monkeypatch, role):
    """The exact 2026-08-01 inbound. Pre-fix this created flyer project F0226 and
    ingested the menu photo as a `menu_reference` asset; the dispatcher — and
    therefore `update_catering_menu` — never ran. It must now fall through."""
    hooks_mod, actions_mod = _load_plugin()
    # Precondition: the flyer arm that claimed it live still admits this inbound,
    # so the cession is what changes the outcome (not a classifier drift).
    assert actions_mod.should_start_new_flyer_over_active(
        INCIDENT_CAPTION, has_media=True) is True
    s = _wire(monkeypatch, hooks_mod, actions_mod, role=role)

    result = _dispatch(hooks_mod, INCIDENT_CAPTION)

    assert result is None, "the inbound must reach the Hermes dispatcher"
    assert s.primary_calls == [], "no flyer project may be created or asset ingested"
    assert s.f7_calls == [], "the menu pipeline is a SKILL, not an F7 catering lead"
    assert s.reasons == ["menu_caption_ceded_to_dispatcher"]
    assert f"sender_role={role}" in s.audits[0]["detail"]


@pytest.mark.parametrize("caption", SKILL_CAPTIONS)
def test_every_documented_skill_caption_cedes(monkeypatch, caption):
    """The SKILL frontmatter documents "update menu", "new menu", and "just
    menu" as triggers; the live pipeline also accepts "menu update"."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod)

    assert _dispatch(hooks_mod, caption) is None
    assert s.primary_calls == []
    assert s.reasons == ["menu_caption_ceded_to_dispatcher"]


def test_pdf_menu_document_cedes_too(monkeypatch):
    """The SKILL accepts `mediaType=document` (PDF) as well as an image, and
    `_extract_media_path` surfaces both through the same `media_path`."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")

    assert _dispatch(hooks_mod, INCIDENT_CAPTION, media_path=DOC_MEDIA) is None
    assert s.primary_calls == []
    assert s.reasons == ["menu_caption_ceded_to_dispatcher"]


# ── Guardrails: what must NOT change ─────────────────────────────────────────
def test_flyer_brief_mentioning_menu_still_goes_to_flyer(monkeypatch):
    """"weekend flyer using our menu" + a reference image is a genuine flyer
    brief. The word "menu" appearing inside it must never cede — the cession is
    anchored on the SKILL's caption tokens, not on the substring "menu"."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")

    result = _dispatch(hooks_mod, FLYER_BRIEF)

    assert result == {"action": "skip",
                      "reason": "cf-router flyer primary: project F0226 created"}
    assert len(s.primary_calls) == 1
    assert s.primary_calls[0]["media_path"] == MEDIA
    assert "menu_caption_ceded_to_dispatcher" not in s.reasons


@pytest.mark.parametrize("caption", [INCIDENT_CAPTION, "menu", "new menu"])
@pytest.mark.parametrize("role", ["customer", "guest", "unknown", ""])
def test_unauthorized_identities_cannot_enter_the_menu_update_path(
        monkeypatch, role, caption):
    """`update_catering_menu` is owner / verified-employee ONLY (SKILL hard
    rule) — the menu is the business's price source of truth, so an ordinary
    customer photographing a menu must not be able to stage an update to it. The
    role allowlist is positive, so an unrecognized or empty role is refused too,
    and every such sender keeps the exact prior routing."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role=role)

    result = _dispatch(hooks_mod, caption)

    assert result == {"action": "skip",
                      "reason": "cf-router flyer primary: project F0226 created"}
    assert len(s.primary_calls) == 1
    assert "menu_caption_ceded_to_dispatcher" not in s.reasons


@pytest.mark.parametrize("role", ["owner", "employee"])
def test_only_owner_and_employee_are_authorized(monkeypatch, role):
    """The positive half of the same rule, so the allowlist cannot be narrowed to
    owner-only without a failing cell: the live sender resolved as `employee` via
    roster mapping, and the SKILL admits owner OR verified employee."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role=role)

    assert _dispatch(hooks_mod, "menu") is None
    assert s.primary_calls == []
    assert s.reasons == ["menu_caption_ceded_to_dispatcher"]


def test_media_less_menu_text_is_unchanged(monkeypatch):
    """The SKILL needs a source image/PDF. A bare "update menu" text carries no
    menu to extract, so the cession must not fire — and with no media the flyer
    primary arm does not admit it either (both arms stay off it)."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")

    _dispatch(hooks_mod, "update menu", media_path=None)

    assert s.primary_calls == []
    assert "menu_caption_ceded_to_dispatcher" not in s.reasons


def test_cession_reason_literal_is_an_enum_member():
    from typing import get_args
    import schemas
    allowed = set(get_args(schemas.CfRouterIntercepted.model_fields["reason"].annotation))
    assert "menu_caption_ceded_to_dispatcher" in allowed


# ── Caption boundary (the conservative half of the fix) ──────────────────────
@pytest.mark.parametrize("caption", [
    INCIDENT_CAPTION, "update menu please", "please update menu",
    "menu", "Menu.", "MENU", "new menu", "menu update",
])
def test_caption_triggers_match(caption):
    _, actions_mod = _load_plugin()
    assert actions_mod.is_menu_update_caption(caption) is True


# A WhatsApp caption is typed on a phone: mixed case and stray whitespace are the
# NORMAL shape, not the edge case. Every one of these must reach the SKILL.
@pytest.mark.parametrize("caption", [
    "  Update Menu ", "UPDATE MENU", "Update  Menu", "uPdAtE mEnU",
    "MENU", " menu ", "  Menu  ", "\tmenu\n",
    "new  menu", "New Menu", "NEW MENU", "Menu  Update", "MENU UPDATE",
])
def test_caption_triggers_survive_capitalization_and_whitespace(caption):
    """The caption predicate normalizes whitespace and folds case, so an owner
    who types "  Update Menu " gets the menu pipeline, not a flyer."""
    _, actions_mod = _load_plugin()
    assert actions_mod.is_menu_update_caption(caption) is True


@pytest.mark.parametrize("caption", [
    "  Update Menu ", "MENU", "new  menu", "New Menu",
])
def test_capitalization_and_whitespace_variants_cede_end_to_end(monkeypatch, caption):
    """The predicate is not the product — pin the same variants through the real
    dispatch so a normalization change cannot pass the unit cell while the live
    route still hands the photo to Flyer Studio."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")

    assert _dispatch(hooks_mod, caption) is None
    assert s.primary_calls == []
    assert s.reasons == ["menu_caption_ceded_to_dispatcher"]


# ── "menu flyer" is a FLYER job, not a menu update ───────────────────────────
@pytest.mark.parametrize("caption", [
    "create a menu flyer",
    "design a menu flyer",
    "make a menu flyer",
    "Create a menu flyer for the weekend",
    "design a new menu flyer",
    "create a new menu flyer for Saturday",
])
def test_menu_flyer_requests_stay_in_flyer_studio(monkeypatch, caption):
    """"Create/design/make a menu flyer" is Flyer Studio work that happens to
    contain the word menu. Two of these ("a NEW MENU flyer") even carry the
    literal trigger phrase — the explicit-flyer veto is what keeps them on the
    flyer path, which is why they are pinned through the real dispatch."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")

    result = _dispatch(hooks_mod, caption)

    assert result == {"action": "skip",
                      "reason": "cf-router flyer primary: project F0226 created"}
    assert len(s.primary_calls) == 1
    assert "menu_caption_ceded_to_dispatcher" not in s.reasons


def test_the_flyer_veto_is_what_saves_new_menu_flyer():
    """Non-vacuity guard for the cell above: "design a new menu flyer" DOES match
    the caption trigger, so if the explicit-flyer veto were ever dropped this
    would silently start ceding real flyer work to the menu pipeline."""
    _, actions_mod = _load_plugin()
    assert actions_mod.is_menu_update_caption("design a new menu flyer") is True
    assert actions_mod.classify_flyer_intent("design a new menu flyer")[0] is True


@pytest.mark.parametrize("caption", [
    FLYER_BRIEF,
    "weekend flyer with our menu items",
    "put the menu on a poster",
    "our menu is attached, make a banner",
    "add the dosa menu prices",
    "",
])
def test_caption_non_triggers_do_not_match(caption):
    """The standing rule against keyword whitelists for LLM-classifiable intent:
    the bare substring "menu" is what let the flyer arm swallow the live photo,
    so it must not be what un-swallows it either. Only the SKILL's documented
    trigger phrases match inside a sentence; a lone "menu" matches only as the
    whole caption."""
    _, actions_mod = _load_plugin()
    assert actions_mod.is_menu_update_caption(caption) is False


def test_explicit_flyer_edit_naming_a_flyer_still_wins(monkeypatch):
    """"update menu prices on this flyer" carries the trigger AND an explicit
    flyer signal. The same deterministic exclusion the P1-1 escape gate uses
    keeps it on the flyer path — the cession never over-claims a flyer edit."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")

    result = _dispatch(hooks_mod, "update menu prices on this flyer")

    assert result == {"action": "skip",
                      "reason": "cf-router flyer primary: project F0226 created"}
    assert "menu_caption_ceded_to_dispatcher" not in s.reasons


# ── Static placement proof (runs on every platform) ─────────────────────────
def test_cession_sits_between_the_r2b1_gate_and_every_flyer_claim():
    """The cession is only a fix if nothing claims the inbound before it. Pins:
    R2B-1 amendment precedence is preserved AHEAD of it, and it runs BEFORE both
    the P1-1 escape gate (which itself precedes the flyer active-project arm) and
    the `should_start_new_flyer_over_active` admission that created F0226."""
    import ast
    src = (PLUGIN_DIR / "hooks.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    dispatch = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_pre_gateway_dispatch_impl")
    r2b1 = cession = escape = None
    start_new_lines = []
    for node in ast.walk(dispatch):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            if node.func.id == "_try_amendment_conflict_intercept" and r2b1 is None:
                r2b1 = node.lineno
            if node.func.id == "_menu_caption_cedes_to_dispatcher" and cession is None:
                cession = node.lineno
            if node.func.id == "_try_flyer_catering_escape_gate" and escape is None:
                escape = node.lineno
        elif isinstance(node.func, ast.Attribute) and \
                node.func.attr == "should_start_new_flyer_over_active":
            start_new_lines.append(node.lineno)

    assert cession is not None, "menu-caption cession not wired into dispatch"
    assert r2b1 is not None and escape is not None and start_new_lines
    assert r2b1 < cession, (
        "the R2B-1 amendment gate MUST keep precedence ahead of the cession")
    assert cession < escape, (
        f"the cession (line {cession}) MUST precede the P1-1 escape gate "
        f"(line {escape}), which in turn precedes the flyer active-project arm")
    assert all(cession < sl for sl in start_new_lines), (
        f"the cession (line {cession}) MUST precede the "
        f"should_start_new_flyer_over_active admission (lines {sorted(start_new_lines)}) "
        f"— the arm that created F0226 live")
