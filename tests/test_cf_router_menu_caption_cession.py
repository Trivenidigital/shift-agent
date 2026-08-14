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

2026-08-08 — the cession's OUTCOME changed and these cells changed with it. It
used to `return None`, handing the inbound to the LLM dispatcher to decide
whether to invoke `update_catering_menu`; on the live 2026-08-01 owner turn the
dispatcher declined to invoke it and replied "successfully recorded" with nothing
recorded. Since the cession has already proven media + trigger + authorized role,
and the SKILL's own documented job is to call ONE script with mechanically
derivable inputs, cf-router now calls `parse-menu-photo` itself. So the routing
cells below assert a deterministic invocation and a terminal result where they
previously asserted a pure yield. The precedence guarantees are unchanged: what
the cession takes the inbound AWAY FROM is still every flyer arm.

What is pinned:
  * the exact live inbound (owner/employee + image + "Update menu") cedes: no
    flyer project is created, `parse-menu-photo` is invoked exactly once with
    inputs derived from the inbound, dispatch returns a TERMINAL result (so the
    LLM is never asked to re-select the action), and both the
    `menu_caption_ceded_to_dispatcher` and `menu_ingestion_staged` markers are
    written
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
import json
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

# What the stubbed extractor reports, and what the durable store must agree with
# before anything may tell the owner a proposal is ready.
STAGED_UPDATE_ID = "MU0007"
STAGED_CODE = "#A3F2X"
STAGED_PREVIEW = "*Appetizers*\n• Idly (3 PCS) — $6.00"


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
        # Outbound sink, as (chat_id, text, action_context). On a ceded inbound
        # the owner now gets EXACTLY ONE cf-router message — the preview card the
        # SKILL used to relay — and its action_context is the evidence the
        # chokepoint checks the claim against.
        self.sent: list[tuple] = []
        # Every parse-menu-photo invocation, with the inputs it was given.
        self.menu_calls: list[dict] = []
        self.routed: list[dict] = []
        # Every brand-asset write. The brand-asset arm is TERMINAL and runs 50+
        # lines ahead of the cession, so this list is the evidence that the menu
        # photo was not swallowed as a logo before the cession could be reached.
        self.brand_assets: list[dict] = []

    @property
    def reasons(self) -> list[str]:
        return [a["reason"] for a in self.audits]


# Every hook that sits between dispatch entry and the flyer-primary arm. Each is
# a no-op here so the ONE decision under test — which arm claims the inbound —
# is not masked by an unrelated intercept reading state off disk.
#
# `_try_flyer_brand_asset_intercept` is DELIBERATELY ABSENT and must stay absent.
# It used to be neutralized here, and that is exactly where the second defect
# hid: it is terminal and runs 50+ lines ahead of the cession, so a menu photo it
# claims never reaches the cession at all. Stubbing it to None made every cell
# below pass while the live route stored the menu as a brand logo. Its one
# mutation (`trigger_store_flyer_brand_asset`) is spied instead of stubbed away,
# so the arm runs for real and the cells can assert it did not fire.
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
    "_try_flyer_existing_onboarding_intercept",
    "_try_amendment_conflict_intercept",
    "_try_flyer_active_project_intercept",
    "_try_flyer_delivery_state_guard",
    "_try_flyer_onboarding_intercept",
    "_try_catering_acceptance_intercept",
    "_try_revenue_route_clarification_start",
)


def _wire(monkeypatch, hooks_mod, actions_mod, *, role="employee",
          owner_capability=False, flyer_generation=True):
    """Arm the flyer path exactly as the live box had it and spy the three arms
    that can claim a menu photo: the brand-asset intercept, the flyer primary
    intercept and F7.

    `role` is the LEGACY SCALAR from identify-sender; `owner_capability` is the
    separate membership answer. The dual-role principal is `role="employee"` +
    `owner_capability=True` — the shape whose scalar is deliberately frozen at
    employee, which is why the brand-asset arm's `role == "owner"` exemption
    cannot rescue it.
    """
    s = _Spies()

    for name in _NEUTRALIZED_HOOKS:
        monkeypatch.setattr(hooks_mod, name, lambda *_a, **_kw: None)

    monkeypatch.setattr(actions_mod, "is_flyer_enabled", lambda: flyer_generation)
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
    # Membership, which is a DIFFERENT question from the scalar above. Stubbed so
    # the co-resident receipt candidacy is deterministic instead of depending on
    # whether identify-sender happens to be executable on the test host.
    monkeypatch.setattr(actions_mod, "has_owner_capability", lambda _cid: owner_capability)
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
    monkeypatch.setattr(
        actions_mod, "send_flyer_text",
        lambda cid, txt, **kw: s.sent.append((cid, txt, kw.get("action_context")))
        or (True, "mid1", ""))
    monkeypatch.setattr(hooks_mod, "_sender_has_qualifying_lead", lambda _cid: False)

    # The brand-asset arm's ONE mutation. Spied, not neutralized: the arm itself
    # runs for real so these cells see the precedence the live route has.
    def _store_brand_asset(**kw):
        s.brand_assets.append(kw)
        return True, "stored", {"asset_id": "B9999", "next_status": "active",
                                "customer_id": "CUST0001",
                                "reply_text": "Flyer Studio\n------------\nBrand asset saved."}

    monkeypatch.setattr(actions_mod, "trigger_store_flyer_brand_asset", _store_brand_asset)

    # Menu-ingestion seam. The route owns WHEN parse-menu-photo runs and whether
    # its reported result is durable; stubbing both isolates the routing decision
    # from a vision call. The REAL script runs in the _e2e companion.
    def _parse_menu(*, image_path, source_image_id, sender_phone):
        s.menu_calls.append({"image_path": image_path,
                             "source_image_id": source_image_id,
                             "sender_phone": sender_phone})
        return 0, json.dumps({
            "update_id": STAGED_UPDATE_ID, "confirmation_code": STAGED_CODE,
            "item_count": 3, "preview_text": STAGED_PREVIEW,
        }), ""

    monkeypatch.setattr(actions_mod, "invoke_parse_menu_photo", _parse_menu)
    monkeypatch.setattr(
        actions_mod, "find_menu_pending_by_update_id",
        lambda uid: ({"update_id": uid, "confirmation_code": STAGED_CODE,
                      "extracted_items": [{}, {}, {}]}
                     if uid == STAGED_UPDATE_ID else None))
    monkeypatch.setattr(actions_mod, "audit_dispatcher_routed",
                        lambda **kw: s.routed.append(kw))

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


def _assert_staged(result, s, *, media_path=MEDIA, role="employee"):
    """The shared post-condition of a ceded inbound: no flyer arm touched it, the
    extractor ran ONCE against the inbound's own media, dispatch is terminal so
    the LLM is never asked to re-select the action, and the single reply the owner
    gets carries a context claiming completion ONLY because it is verified."""
    assert result is not None, (
        "dispatch must be terminal — returning None hands the action decision "
        "back to the LLM dispatcher, which is the 2026-08-01 defect")
    assert result["action"] == "skip"
    assert result["reason"].startswith(
        f"cf-router menu ingestion staged {STAGED_UPDATE_ID}"), result["reason"]
    assert s.brand_assets == [], (
        "the menu photo was captured as a flyer brand asset — the terminal "
        "brand-asset arm claimed the turn before the cession could be reached")
    assert s.primary_calls == [], "no flyer project may be created or asset ingested"
    assert s.f7_calls == [], "the menu pipeline is a SKILL, not an F7 catering lead"
    assert [c["image_path"] for c in s.menu_calls] == [media_path], (
        "parse-menu-photo runs exactly once, on the media that arrived")
    assert s.menu_calls[0]["source_image_id"] == "wamid.MENU0801"
    assert s.menu_calls[0]["sender_phone"] == PHONE
    assert s.reasons == ["menu_caption_ceded_to_dispatcher", "menu_ingestion_staged"]
    assert f"sender_role={role}" in s.audits[0]["detail"]
    assert s.audits[1]["code"] == STAGED_CODE
    # Routing-accuracy pairing still sees an LLM-bypassing arm.
    assert [r["routed_to_skill"] for r in s.routed] == ["update_catering_menu"]
    assert len(s.sent) == 1, "exactly one reply — the preview card"
    chat, body, ctx = s.sent[0]
    assert chat == CHAT
    assert STAGED_PREVIEW in body, "the script's preview passes through verbatim"
    assert f"{STAGED_CODE} yes" in body, "the owner needs the approval code"
    assert ctx.verified_action_result is True
    assert ctx.claims_action_completed is True
    assert ctx.audit_row_id is None, "no receipt id exists; none may be invented"
    assert STAGED_UPDATE_ID in ctx.action_id


# ── The live incident, as the regression cell ────────────────────────────────
@pytest.mark.parametrize("role", ["owner", "employee"])
def test_menu_photo_caption_cedes_to_dispatcher_instead_of_creating_a_flyer(
        monkeypatch, role):
    """The exact 2026-08-01 inbound. Pre-fix this created flyer project F0226 and
    ingested the menu photo as a `menu_reference` asset; the dispatcher — and
    therefore `update_catering_menu` — never ran. It must now reach menu
    ingestion, deterministically, with no second routing decision."""
    hooks_mod, actions_mod = _load_plugin()
    # Precondition: the flyer arm that claimed it live still admits this inbound,
    # so the cession is what changes the outcome (not a classifier drift).
    assert actions_mod.should_start_new_flyer_over_active(
        INCIDENT_CAPTION, has_media=True) is True
    s = _wire(monkeypatch, hooks_mod, actions_mod, role=role)

    result = _dispatch(hooks_mod, INCIDENT_CAPTION)

    _assert_staged(result, s, role=role)


@pytest.mark.parametrize("caption", SKILL_CAPTIONS)
def test_every_documented_skill_caption_cedes(monkeypatch, caption):
    """The SKILL frontmatter documents "update menu", "new menu", and "just
    menu" as triggers; the live pipeline also accepts "menu update"."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod)

    _assert_staged(_dispatch(hooks_mod, caption), s)


def test_pdf_menu_document_cedes_too(monkeypatch):
    """The SKILL accepts `mediaType=document` (PDF) as well as an image, and
    `_extract_media_path` surfaces both through the same `media_path`."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")

    result = _dispatch(hooks_mod, INCIDENT_CAPTION, media_path=DOC_MEDIA)

    _assert_staged(result, s, media_path=DOC_MEDIA, role="owner")
    assert s.routed[0]["message_shape"] == "media_other", (
        "a PDF is not an image — the routing row must not claim otherwise")


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

    _assert_staged(_dispatch(hooks_mod, "menu"), s, role=role)


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
    # An LLM-bypassing arm whose reason is not in the enum is swallowed by
    # audit_intercepted's except-and-warn, i.e. silently invisible to telemetry.
    assert "menu_ingestion_staged" in allowed
    assert "menu_ingestion_failed" in allowed


# ── The action decision is not delegated a second time ───────────────────────
def test_the_llm_dispatcher_is_never_asked_to_select_the_menu_action(monkeypatch):
    """The 2026-08-01 defect was not a classification failure — the cession
    correctly identified the turn and then handed the ACTION decision to the LLM,
    which declined to invoke `update_catering_menu`. Pin that the delegation is
    gone: dispatch is terminal on every ceded caption, so no LLM turn exists in
    which the action could be re-selected or skipped."""
    hooks_mod, actions_mod = _load_plugin()
    for caption in [INCIDENT_CAPTION, *SKILL_CAPTIONS]:
        s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")
        result = _dispatch(hooks_mod, caption)
        assert result is not None, (
            f"{caption!r} still yields the action decision to the LLM")
        assert len(s.menu_calls) == 1, f"{caption!r} did not run the extractor"


# ── Failure shapes: the receipt is the pending store, not the script's word ──
def test_script_failure_is_reported_honestly_and_claims_nothing(monkeypatch):
    """parse-menu-photo exits non-zero (its documented 2/3/5/6, or 124/127 from
    the invoker). No proposal exists, so the reply must not imply one and the
    context must not claim completion."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")
    monkeypatch.setattr(actions_mod, "invoke_parse_menu_photo",
                        lambda **kw: (6, "", "OpenRouter unreachable"))

    result = _dispatch(hooks_mod, INCIDENT_CAPTION)

    assert result["reason"].startswith("cf-router menu ingestion failed (rc=6)")
    assert s.reasons == ["menu_caption_ceded_to_dispatcher", "menu_ingestion_failed"]
    assert s.audits[1]["subprocess_rc"] == 6
    assert len(s.sent) == 1
    body, ctx = s.sent[0][1], s.sent[0][2]
    assert ctx.verified_action_result is False
    assert ctx.claims_action_completed is False, (
        "nothing completed, so nothing may be asserted as completed")
    assert STAGED_CODE not in body, "no approval code exists to offer"
    assert "could not read" in body.lower()


def test_script_success_without_matching_durable_state_is_not_a_success(monkeypatch):
    """The exact false-success shape, one layer deeper: the script reports rc=0
    and an update_id, but the durable pending store does not hold it (write lost,
    clobbered by a concurrent proposal, or never landed). The stdout claim alone
    must not be enough — otherwise the owner gets a code that cannot be approved."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")
    monkeypatch.setattr(actions_mod, "find_menu_pending_by_update_id",
                        lambda _uid: None)

    result = _dispatch(hooks_mod, INCIDENT_CAPTION)

    assert result["reason"].startswith("cf-router menu ingestion failed")
    assert s.reasons[-1] == "menu_ingestion_failed"
    assert "durable_pending=no" in s.audits[1]["detail"]
    assert s.sent[0][2].claims_action_completed is False
    assert s.sent[0][2].verified_action_result is False


def test_durable_state_holding_a_different_update_id_is_not_a_match(monkeypatch):
    """A pending store holding SOMEBODY ELSE'S proposal is not evidence for this
    one. Pins that verification is an identity check, not a presence check."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")
    monkeypatch.setattr(
        actions_mod, "find_menu_pending_by_update_id",
        lambda uid: {"update_id": "MU9999"} if uid == "MU9999" else None)

    result = _dispatch(hooks_mod, INCIDENT_CAPTION)

    assert result["reason"].startswith("cf-router menu ingestion failed")
    assert s.sent[0][2].claims_action_completed is False


def test_update_id_matching_is_exact(monkeypatch, tmp_path):
    """`find_menu_pending_by_update_id` is the verification primitive; pin its
    contract directly so a future refactor cannot loosen it to a truthiness or
    prefix test."""
    _, actions_mod = _load_plugin()
    store = {"update_id": "MU0007", "confirmation_code": STAGED_CODE}
    path = tmp_path / "catering-menu-pending.json"
    path.write_text(json.dumps(store), encoding="utf-8")
    monkeypatch.setattr(actions_mod, "MENU_PENDING_PATH", path)

    assert actions_mod.find_menu_pending_by_update_id("MU0007") == store
    assert actions_mod.find_menu_pending_by_update_id("MU000") is None
    assert actions_mod.find_menu_pending_by_update_id("MU00070") is None
    assert actions_mod.find_menu_pending_by_update_id("") is None
    assert actions_mod.find_menu_pending_by_update_id("MU9999") is None


def test_an_unexpected_error_still_never_yields_the_turn(monkeypatch):
    """The arm's whole purpose is to remove the LLM from this turn, and the outer
    plugin try/except turns an exception into `None` — which would hand the turn
    straight back to the dispatcher. Pin that an unexpected fault degrades to the
    honest failure reply, not to an LLM turn."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")

    def _boom(**_kw):
        raise RuntimeError("bridge exploded")

    monkeypatch.setattr(actions_mod, "invoke_parse_menu_photo", _boom)

    result = _dispatch(hooks_mod, INCIDENT_CAPTION)

    assert result is not None, "an exception must not become a fall-through"
    assert result["reason"].startswith("cf-router menu ingestion errored")
    assert s.reasons[-1] == "menu_ingestion_failed"
    assert "unexpected_error=RuntimeError" in s.audits[-1]["detail"]
    assert len(s.sent) == 1 and s.sent[0][2].claims_action_completed is False


def test_a_menu_item_name_containing_braces_does_not_break_the_card(monkeypatch):
    """The preview card is a format template and `preview_text` is script output.
    A menu listing "Combo {2 pcs}" must render, not raise — an exception here
    would degrade a SUCCESSFUL extraction into the failure arm."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")
    brace_preview = "*Mains*\n• Combo {2 pcs} — $9.00 {extra}"
    monkeypatch.setattr(actions_mod, "invoke_parse_menu_photo", lambda **_kw: (
        0, json.dumps({"update_id": STAGED_UPDATE_ID, "confirmation_code": STAGED_CODE,
                       "item_count": 1, "preview_text": brace_preview}), ""))

    result = _dispatch(hooks_mod, INCIDENT_CAPTION)

    assert result["reason"].startswith("cf-router menu ingestion staged")
    assert brace_preview in s.sent[0][1]


def test_missing_pending_store_verifies_nothing(monkeypatch, tmp_path):
    _, actions_mod = _load_plugin()
    monkeypatch.setattr(actions_mod, "MENU_PENDING_PATH", tmp_path / "absent.json")
    assert actions_mod.find_menu_pending_by_update_id("MU0007") is None


def test_corrupt_pending_store_verifies_nothing(monkeypatch, tmp_path):
    _, actions_mod = _load_plugin()
    bad = tmp_path / "catering-menu-pending.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(actions_mod, "MENU_PENDING_PATH", bad)
    assert actions_mod.find_menu_pending_by_update_id("MU0007") is None


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

    _assert_staged(_dispatch(hooks_mod, caption), s, role="owner")


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


# ── Natural phrasings of the same documented trigger (PR-E) ─────────────────
#
# The precedence fix made cession candidacy faithful to this predicate, which
# left the trigger VOCABULARY as the remaining gap: an owner who writes the
# obvious English form of "update menu" fell through to flyer routing.
@pytest.mark.parametrize("caption", [
    "updated menu",
    "Updated Menu",
    "menu updates",
    "our updated menu prices",
    "here is the menu",
    "here is our new menu",
    "Here are the updated menu",
    "here is my menu.",
])
def test_natural_menu_update_phrasings_match(caption):
    _, actions_mod = _load_plugin()
    assert actions_mod.is_menu_update_caption(caption) is True


# The "here is/are ... menu" arm is END-ANCHORED. Unanchored it would swallow
# every flyer brief that opens by presenting the menu as source material, which
# is a far more common message than the one the arm exists to catch.
@pytest.mark.parametrize("caption", [
    "here are our menu items for the weekend flyer",
    "here is the menu, put it on a poster",
    "here is my menu and here is the logo to go on the flyer",
])
def test_here_is_the_menu_arm_is_end_anchored(caption):
    _, actions_mod = _load_plugin()
    assert actions_mod.is_menu_update_caption(caption) is False


def test_the_flyer_veto_still_carries_trigger_phrases_used_mid_sentence():
    """Scope note for the cell above. Only the "here is/are … menu" arm is
    anchored; "updated menu" / "new menu" stay matchable mid-sentence, exactly
    as "new menu" already was. So "here is our new menu — design a banner from
    it" DOES match the predicate, and the explicit-flyer veto is what keeps it
    on the flyer path — the same division of labour as "design a new menu
    flyer". Anchoring the first alternation too would break the live incident
    caption this whole cession exists for."""
    _, actions_mod = _load_plugin()
    caption = "here is our new menu — design a banner from it"
    assert actions_mod.is_menu_update_caption(caption) is True
    assert actions_mod.classify_flyer_intent(caption)[0] is True


def test_widening_did_not_reach_for_a_bare_possessive_menu():
    """`(?:our|the)\\s+menu` was rejected deliberately: it is the shape every
    flyer brief uses to name its source material."""
    _, actions_mod = _load_plugin()
    for caption in ("weekend flyer using our menu", "put the menu on a poster",
                    "a poster from our menu", "the menu goes on the flyer"):
        assert actions_mod.is_menu_update_caption(caption) is False, caption


def test_non_ascii_menu_word_stays_unmatched():
    """The predicate matches the SKILL's documented ASCII trigger tokens only.
    "menü" is not one of them and is NOT handled here — noted so a future
    localization pass finds the gap rather than assuming coverage."""
    _, actions_mod = _load_plugin()
    assert actions_mod.is_menu_update_caption("menü") is False
    assert actions_mod.is_menu_update_caption("neue menü") is False


def test_accepted_residual_flyer_brief_captioned_exactly_here_is_the_menu():
    """ACCEPTED BY RULING, pinned so it is a known cell and not a surprise.

    An owner who attaches a flyer brief and captions it exactly "here is the
    menu" now gets menu ingestion. Nothing downstream rescues this: the flyer
    signal check needs a literal flyer/poster/banner token and the exact-edit
    check needs an edit verb plus a singular target, and this caption has
    neither. End-anchoring is the only thing bounding the arm, and it is what
    keeps the far commoner "here is the menu, put it on a poster" safe."""
    _, actions_mod = _load_plugin()
    assert actions_mod.is_menu_update_caption("here is the menu") is True
    assert actions_mod.classify_flyer_intent("here is the menu")[0] is False


@pytest.mark.parametrize("caption", ["updated menu", "menu updates",
                                     "here is our new menu"])
def test_natural_phrasings_cede_end_to_end(monkeypatch, caption):
    """The predicate is not the product. #697 made cession candidacy consume
    this predicate, so a widened vocabulary must actually reach the SKILL
    through the real dispatch, not just satisfy the unit cell."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner")

    _assert_staged(_dispatch(hooks_mod, caption), s, role="owner")


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


# ── Brand-asset precedence: the arm that runs 50+ lines BEFORE the cession ───
#
# Same class as B0009/B0010, which #694 closed for receipts and left open for
# menus. `_try_flyer_brand_asset_intercept` is TERMINAL and is invoked far ahead
# of the cession, and #694's yield inside it is gated on the RECEIPT candidate
# only — so a menu photo it claims never reaches the cession at all. The caption
# that breaks it names a brand-asset word, because that is what makes the arm
# claim the turn instead of declining.

# Both carry a documented menu trigger AND an explicit asset word. The premise
# each one rests on is pinned in test_the_brand_asset_arm_would_otherwise_claim.
MENU_CAPTIONS_NAMING_AN_ASSET = ["new menu with our logo",
                                 "update menu with our new logo"]


@pytest.mark.parametrize("caption", MENU_CAPTIONS_NAMING_AN_ASSET)
def test_menu_caption_naming_a_brand_asset_cedes_instead_of_being_captured(
        monkeypatch, caption):
    """A verified EMPLOYEE sends a menu photo captioned "new menu with our logo".

    Pre-fix: the caption is a documented `update_catering_menu` trigger, but the
    brand-asset arm got there first — the owner exemption does not apply to an
    employee, `should_start_new_flyer_over_active` declines (`_CURRENT_BRAND_UPLOAD`
    matches "logo"), and "logo" is an explicit asset word — so the menu was stored
    as a brand LOGO and attached to every later flyer prompt, and the cession was
    never reached.
    """
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="employee")

    _assert_staged(_dispatch(hooks_mod, caption), s, role="employee")


@pytest.mark.parametrize("caption", MENU_CAPTIONS_NAMING_AN_ASSET)
def test_dual_role_principal_menu_caption_cedes(monkeypatch, caption):
    """The same input from the DUAL-ROLE principal: legacy scalar frozen at
    `employee`, membership including owner. This is the identity shape both live
    receipt misroutes had, and the reason a fix written against the scalar
    exemption does not work — `role == "owner"` is False for exactly the sender
    it needs to be True for."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="employee",
              owner_capability=True)

    _assert_staged(_dispatch(hooks_mod, caption), s, role="employee")


def test_the_brand_asset_arm_would_otherwise_claim_these_captions():
    """NON-VACUITY guard for the two cells above.

    Each premise the defect rested on is pinned separately, so the cells cannot
    start passing for an unrelated reason (a caption predicate drifting, or
    `should_start_new_flyer_over_active` starting to admit the caption and
    bouncing the turn out of the brand arm before the yield is consulted)."""
    _, actions_mod = _load_plugin()
    for caption in MENU_CAPTIONS_NAMING_AN_ASSET:
        assert actions_mod.is_menu_update_caption(caption) is True, caption
        # The arm's own early exit does NOT save these — a brand-upload caption
        # is precisely what makes it decline and continue into capture.
        assert actions_mod.should_start_new_flyer_over_active(
            caption, has_media=True) is False, caption
        # ...and "logo" is one of the arm's explicit asset words, so capture is
        # what it would do next.
        assert "logo" in caption


@pytest.mark.parametrize("caption", ["here's our new logo", "our brand template",
                                     "replace this with our logo"])
def test_a_genuine_brand_asset_upload_is_still_captured(monkeypatch, caption):
    """REGRESSION GUARD — the yield is scoped to the menu candidacy predicate,
    not to the sender or to the word "menu" appearing nearby. The same employee
    uploading a real logo must still get a brand asset, exactly as before."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="employee")

    result = _dispatch(hooks_mod, caption)

    assert len(s.brand_assets) == 1, (
        f"{caption!r} lost brand-asset capture — the yield is over-broad")
    assert s.brand_assets[0]["media_path"] == MEDIA
    assert result["reason"].startswith("cf-router flyer brand asset")
    assert s.menu_calls == [], "a logo upload must not invoke the menu extractor"
    assert "menu_caption_ceded_to_dispatcher" not in s.reasons


# ── Flag asymmetry: capture and cession must be armed by the SAME flag ───────
def test_menu_cession_survives_flyer_generation_being_disabled(monkeypatch):
    """The second defect. The brand-asset arm sits under `flyer_workflow_enabled`
    while both cessions sat under `flyer_generation_enabled`, and the two are not
    equivalent — `is_flyer_workflow_enabled` is True whenever a `flyer:` block
    exists. So turning flyer generation off (the incident kill switch) disarmed
    the cessions while leaving CAPTURE armed: the menu photo would still be
    swallowed as a brand asset, with nothing left to rescue it.

    With generation off and workflow on, a menu-captioned media turn must still
    cede — and must still be terminal, so it does not fall to the LLM either.
    """
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="owner",
              flyer_generation=False)

    _assert_staged(_dispatch(hooks_mod, INCIDENT_CAPTION), s, role="owner")


def test_menu_cession_survives_generation_disabled_for_the_captured_shape(monkeypatch):
    """The two defects composed: generation off (cession was dark) AND a caption
    naming a brand asset (capture was armed). This is the cell where the flag fix
    and the yield fix each have to hold for the turn to survive."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="employee",
              owner_capability=True, flyer_generation=False)

    _assert_staged(_dispatch(hooks_mod, "new menu with our logo"), s,
                   role="employee")


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


def test_the_brand_asset_arm_runs_ahead_of_the_cession_and_is_told_about_it():
    """Why the yield exists at all, pinned structurally.

    The brand-asset arm is TERMINAL and is invoked before the cession, so the
    cession's placement guarantees above are worth nothing for any turn the arm
    claims. That ordering is not the bug and is not being changed — the arm is
    passed the turn's menu candidacy so it can decline those turns itself. If a
    refactor ever drops that argument, this fails rather than silently
    reinstating the swallow.
    """
    import ast
    src = (PLUGIN_DIR / "hooks.py").read_text(encoding="utf-8")
    dispatch = next(n for n in ast.walk(ast.parse(src))
                    if isinstance(n, ast.FunctionDef) and n.name == "_pre_gateway_dispatch_impl")
    brand = cession = None
    brand_kwargs: list[str] = []
    for node in ast.walk(dispatch):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id == "_try_flyer_brand_asset_intercept" and brand is None:
            brand = node.lineno
            brand_kwargs = [kw.arg for kw in node.keywords]
        if node.func.id == "_menu_caption_cedes_to_dispatcher" and cession is None:
            cession = node.lineno

    assert brand is not None and cession is not None
    assert brand < cession, (
        "the brand-asset arm no longer precedes the cession — re-read this cell "
        "before deleting it; the yield may no longer be load-bearing")
    assert "menu_caption_candidate" in brand_kwargs, (
        f"the terminal brand-asset arm at line {brand} is not told the turn is a "
        f"menu candidate, so it will capture the photo before the cession at "
        f"line {cession} can claim it")
