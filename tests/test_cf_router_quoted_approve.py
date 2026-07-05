"""Quoted-APPROVE binding (2026-07-05).

Probe evidence (cf_router_raw_body rows, 2026-07-05): the bridge delivers
swipe-replies with a CLEAN body and quote metadata in `event.raw_message`
(dict): hasQuotedMessage=True, quotedMessageId=<id of the quoted message>,
quotedParticipant=<lid of the quoted sender>. When the quoted mid matches a
project's known outbound mids (preview media / APPROVE CTA / finals), the
approve/revision flow binds to THAT project instead of the newest-updated
fallback. Separately, one legacy shape (F0211) flattened the quoted TEXT into
the body — the quote-echo guard suppresses that instead of creating a
duplicate project.

Linux-only — cf-router actions/hooks import safe_io (fcntl-only) on the
persist + audit paths; runs on Linux CI (flyer-premium-ci).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys as _sys

import pytest

pytestmark = pytest.mark.skipif(
    _sys.platform == "win32",
    reason="cf-router actions/hooks import safe_io (fcntl-only); runs on Linux CI",
)

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
PLATFORM_DIR = REPO / "src" / "platform"
sys.path.insert(0, str(PLATFORM_DIR))

spec = importlib.util.spec_from_file_location(
    "cf_actions_quoted", PLUGIN_DIR / "actions.py")
cf_actions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cf_actions)


def _load_plugin_modules():
    """Synthetic-package loader so hooks.py's `from . import actions`
    resolves (mirrors tests/test_cf_router_plugin.py — the plugin dir name
    `cf-router` contains a hyphen so it can't be imported by name)."""
    pkg_name = "cf_router_pkg_quoted_approve"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]
    for mod_name in ("schemas", "safe_io"):
        sys.modules.pop(mod_name, None)

    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg_name] = pkg_mod

    actions_full = f"{pkg_name}.actions"
    actions_loader = importlib.machinery.SourceFileLoader(
        actions_full, str(PLUGIN_DIR / "actions.py"))
    actions_spec = importlib.util.spec_from_loader(actions_full, actions_loader)
    actions_mod = importlib.util.module_from_spec(actions_spec)
    sys.modules[actions_full] = actions_mod
    actions_loader.exec_module(actions_mod)

    hooks_full = f"{pkg_name}.hooks"
    hooks_loader = importlib.machinery.SourceFileLoader(
        hooks_full, str(PLUGIN_DIR / "hooks.py"))
    hooks_spec = importlib.util.spec_from_loader(hooks_full, hooks_loader)
    hooks_mod = importlib.util.module_from_spec(hooks_spec)
    sys.modules[hooks_full] = hooks_mod
    hooks_loader.exec_module(hooks_mod)
    return hooks_mod, actions_mod


PHONE = "+19045550104"
CHAT = "201975216009469@lid"


def _now_iso(delta_hours: float = 0.0) -> str:
    ts = datetime.now(timezone.utc) - timedelta(hours=delta_hours)
    return ts.isoformat().replace("+00:00", "Z")


def _two_project_store(tmp_path, monkeypatch, mod, *, newer_status="awaiting_final_approval"):
    """Older F0100 (owns the quoted mids) + newer F0111 (newest-updated pick)."""
    projects = [
        {
            "project_id": "F0100",
            "customer_phone": PHONE,
            "status": "awaiting_final_approval",
            "created_at": _now_iso(30),
            "updated_at": _now_iso(20),
            "raw_request": "Diwali sweets flyer",
            "preview_message_ids": ["wamid.CTA100"],
            "assets": [{"asset_id": "A1", "outbound_message_id": "wamid.MEDIA100"}],
        },
        {
            "project_id": "F0111",
            "customer_phone": PHONE,
            "status": newer_status,
            "created_at": _now_iso(3),
            "updated_at": _now_iso(1),
            "raw_request": "Grand opening flyer for the new store",
        },
    ]
    store_path = tmp_path / "projects.json"
    store_path.write_text(json.dumps({"projects": projects}), encoding="utf-8")
    monkeypatch.setattr(mod, "FLYER_PROJECTS_PATH", store_path)
    monkeypatch.setattr(mod, "find_flyer_customer_by_sender", lambda _p, _c: None)
    return store_path


def _quoting_event(quoted_mid: str):
    return SimpleNamespace(raw_message={
        "hasQuotedMessage": True,
        "quotedMessageId": quoted_mid,
        "quotedParticipant": "94871987654321@lid",
    })


# ---------------------------------------------------------------------------
# extract_quoted_message_id — defensive raw_message shapes
# ---------------------------------------------------------------------------


def test_extract_quoted_mid_from_dict():
    assert cf_actions.extract_quoted_message_id(_quoting_event("wamid.PREV")) == "wamid.PREV"


def test_extract_quoted_mid_from_json_string():
    event = SimpleNamespace(raw_message=json.dumps(
        {"hasQuotedMessage": True, "quotedMessageId": "wamid.STR"}))
    assert cf_actions.extract_quoted_message_id(event) == "wamid.STR"


def test_extract_quoted_mid_from_source_attr():
    event = SimpleNamespace(source=SimpleNamespace(raw_message={
        "hasQuotedMessage": True, "quotedMessageId": "wamid.SRC"}))
    assert cf_actions.extract_quoted_message_id(event) == "wamid.SRC"


@pytest.mark.parametrize("event", [
    SimpleNamespace(),                                              # missing
    SimpleNamespace(raw_message=None),
    SimpleNamespace(raw_message="not json {"),                      # str garbage
    SimpleNamespace(raw_message=12345),                             # wrong type
    SimpleNamespace(raw_message=["hasQuotedMessage"]),              # wrong type
    SimpleNamespace(raw_message={"hasQuotedMessage": False,
                                 "quotedMessageId": "wamid.X"}),    # not a quote
    SimpleNamespace(raw_message={"hasQuotedMessage": True}),        # no mid
    SimpleNamespace(raw_message={"hasQuotedMessage": True,
                                 "quotedMessageId": 7}),            # non-str mid
    SimpleNamespace(raw_message={"hasQuotedMessage": True,
                                 "quotedMessageId": "   "}),        # blank mid
    None,
])
def test_extract_quoted_mid_defensive_shapes(event):
    assert cf_actions.extract_quoted_message_id(event) == ""


def test_extract_quoted_mid_never_raises_on_hostile_event():
    class Hostile:
        def __getattr__(self, name):
            raise RuntimeError("boom")
    assert cf_actions.extract_quoted_message_id(Hostile()) == ""


# ---------------------------------------------------------------------------
# Binding: quoted mid -> that project; no match -> newest-updated fallback
# ---------------------------------------------------------------------------


def test_quoted_cta_mid_binds_older_project(tmp_path, monkeypatch):
    _two_project_store(tmp_path, monkeypatch, cf_actions)
    active = cf_actions.find_active_flyer_project_by_sender(PHONE, CHAT)
    assert active["project_id"] == "F0111"  # newest-updated baseline
    bound, source = cf_actions.resolve_flyer_binding_project(
        active, PHONE, CHAT, _quoting_event("wamid.CTA100"))
    assert bound["project_id"] == "F0100"
    assert source == "quoted_message_id"


def test_quoted_asset_mid_binds_older_project(tmp_path, monkeypatch):
    _two_project_store(tmp_path, monkeypatch, cf_actions)
    active = cf_actions.find_active_flyer_project_by_sender(PHONE, CHAT)
    bound, source = cf_actions.resolve_flyer_binding_project(
        active, PHONE, CHAT, _quoting_event("wamid.MEDIA100"))
    assert bound["project_id"] == "F0100"
    assert source == "quoted_message_id"


def test_unknown_quoted_mid_falls_back_to_newest(tmp_path, monkeypatch):
    _two_project_store(tmp_path, monkeypatch, cf_actions)
    active = cf_actions.find_active_flyer_project_by_sender(PHONE, CHAT)
    bound, source = cf_actions.resolve_flyer_binding_project(
        active, PHONE, CHAT, _quoting_event("wamid.NOBODY"))
    assert bound["project_id"] == "F0111"
    assert source == "newest_updated"


def test_no_quote_metadata_falls_back_to_newest(tmp_path, monkeypatch):
    _two_project_store(tmp_path, monkeypatch, cf_actions)
    active = cf_actions.find_active_flyer_project_by_sender(PHONE, CHAT)
    bound, source = cf_actions.resolve_flyer_binding_project(
        active, PHONE, CHAT, SimpleNamespace(text="APPROVE"))
    assert bound["project_id"] == "F0111"
    assert source == "newest_updated"


def test_binding_with_no_active_project_stays_none(tmp_path, monkeypatch):
    _two_project_store(tmp_path, monkeypatch, cf_actions)
    bound, source = cf_actions.resolve_flyer_binding_project(
        None, PHONE, CHAT, _quoting_event("wamid.CTA100"))
    assert bound is None
    assert source == "newest_updated"


def test_quoted_mid_never_binds_across_customers(tmp_path, monkeypatch):
    """The quoted project belongs to ANOTHER customer's account — the
    account-scoped candidate set must refuse the bind and fall back."""
    projects = [
        {
            "project_id": "F0200",
            "customer_phone": "+15555550100",  # different account
            "status": "awaiting_final_approval",
            "created_at": _now_iso(10),
            "updated_at": _now_iso(9),
            "raw_request": "Other customer's flyer",
            "preview_message_ids": ["wamid.FOREIGN"],
        },
        {
            "project_id": "F0111",
            "customer_phone": PHONE,
            "status": "awaiting_final_approval",
            "created_at": _now_iso(3),
            "updated_at": _now_iso(1),
            "raw_request": "Grand opening flyer for the new store",
        },
    ]
    store_path = tmp_path / "projects.json"
    store_path.write_text(json.dumps({"projects": projects}), encoding="utf-8")
    monkeypatch.setattr(cf_actions, "FLYER_PROJECTS_PATH", store_path)
    monkeypatch.setattr(cf_actions, "find_flyer_customer_by_sender", lambda _p, _c: None)
    active = cf_actions.find_active_flyer_project_by_sender(PHONE, CHAT)
    bound, source = cf_actions.resolve_flyer_binding_project(
        active, PHONE, CHAT, _quoting_event("wamid.FOREIGN"))
    assert bound["project_id"] == "F0111"
    assert source == "newest_updated"


# ---------------------------------------------------------------------------
# Flattened quote-echo guard (F0211 class)
# ---------------------------------------------------------------------------

LONG_BRIEF = (
    "Graduation is here and time to celebrate our kids. We take customized "
    "orders - Desserts. Mango custard 40 count tray - $40. Rasmalai cups 25 "
    "count - $50. Gulab jamun tray 50 count - $35."
)


def _echo_store(tmp_path, monkeypatch, *, raw_request=LONG_BRIEF, updated_hours=2.0):
    projects = [{
        "project_id": "F0211",
        "customer_phone": PHONE,
        "status": "awaiting_final_approval",
        "created_at": _now_iso(updated_hours + 1),
        "updated_at": _now_iso(updated_hours),
        "raw_request": raw_request,
    }]
    store_path = tmp_path / "projects.json"
    store_path.write_text(json.dumps({"projects": projects}), encoding="utf-8")
    monkeypatch.setattr(cf_actions, "FLYER_PROJECTS_PATH", store_path)
    monkeypatch.setattr(cf_actions, "find_flyer_customer_by_sender", lambda _p, _c: None)


def test_quote_echo_exact_match(tmp_path, monkeypatch):
    _echo_store(tmp_path, monkeypatch)
    row = cf_actions.find_flyer_quote_echo_project(PHONE, CHAT, LONG_BRIEF)
    assert row is not None and row["project_id"] == "F0211"


def test_quote_echo_prefix_match_long_brief(tmp_path, monkeypatch):
    _echo_store(tmp_path, monkeypatch)
    body = LONG_BRIEF + "\nAPPROVE"
    row = cf_actions.find_flyer_quote_echo_project(PHONE, CHAT, body)
    assert row is not None and row["project_id"] == "F0211"


def test_quote_echo_no_prefix_match_for_short_brief(tmp_path, monkeypatch):
    _echo_store(tmp_path, monkeypatch, raw_request="Diwali sweets flyer")
    assert cf_actions.find_flyer_quote_echo_project(
        PHONE, CHAT, "Diwali sweets flyer with extra text appended") is None
    # exact equality still matches for short briefs
    row = cf_actions.find_flyer_quote_echo_project(PHONE, CHAT, "Diwali sweets flyer")
    assert row is not None and row["project_id"] == "F0211"


def test_quote_echo_genuinely_new_brief_unaffected(tmp_path, monkeypatch):
    _echo_store(tmp_path, monkeypatch)
    assert cf_actions.find_flyer_quote_echo_project(
        PHONE, CHAT, "Create a flyer for our July 4th cookout specials") is None


def test_quote_echo_stale_project_ignored(tmp_path, monkeypatch):
    _echo_store(tmp_path, monkeypatch, updated_hours=24 * 30)  # 30 days old
    assert cf_actions.find_flyer_quote_echo_project(PHONE, CHAT, LONG_BRIEF) is None


# ---------------------------------------------------------------------------
# Preview-mid index persistence + schema round-trip
# ---------------------------------------------------------------------------


def _valid_project_row(project_id="F0100", **overrides):
    row = {
        "project_id": project_id,
        "status": "awaiting_final_approval",
        "customer_phone": PHONE,
        "created_at": _now_iso(2),
        "updated_at": _now_iso(1),
        "original_message_id": "wamid.ORIG",
        "raw_request": "Diwali sweets flyer",
    }
    row.update(overrides)
    return row


def test_record_preview_message_ids_appends_dedupes_caps(tmp_path, monkeypatch):
    store_path = tmp_path / "projects.json"
    store_path.write_text(
        json.dumps({"projects": [_valid_project_row()]}), encoding="utf-8")
    monkeypatch.setattr(cf_actions, "FLYER_PROJECTS_PATH", store_path)

    cf_actions._record_flyer_preview_message_ids("F0100", ["wamid.M1", "wamid.M2", "wamid.CTA"])
    cf_actions._record_flyer_preview_message_ids("F0100", ["wamid.M2", "wamid.CTA2"])
    stored = json.loads(store_path.read_text(encoding="utf-8"))["projects"][0]
    assert stored["preview_message_ids"] == ["wamid.M1", "wamid.M2", "wamid.CTA", "wamid.CTA2"]

    # Cap: newest 10 survive (schema max_length=10)
    cf_actions._record_flyer_preview_message_ids(
        "F0100", [f"wamid.X{i}" for i in range(12)])
    stored = json.loads(store_path.read_text(encoding="utf-8"))["projects"][0]
    assert len(stored["preview_message_ids"]) == 10
    assert stored["preview_message_ids"][-1] == "wamid.X11"

    # The mutated store still validates against the deployed schema
    from schemas import FlyerProjectStore
    validated = FlyerProjectStore.model_validate(
        json.loads(store_path.read_text(encoding="utf-8")))
    assert validated.projects[0].preview_message_ids[-1] == "wamid.X11"


def test_flyer_project_schema_roundtrip_old_and_new_rows():
    from schemas import FlyerProject
    old_row = FlyerProject.model_validate(_valid_project_row())
    assert old_row.preview_message_ids == []  # additive default: old rows validate
    new_row = FlyerProject.model_validate(
        _valid_project_row(preview_message_ids=["wamid.CTA"]))
    assert "wamid.CTA" in json.loads(new_row.model_dump_json())["preview_message_ids"]


def test_cf_router_intercepted_binding_source_roundtrip():
    from pydantic import TypeAdapter

    from schemas import LogEntry
    adapter = TypeAdapter(LogEntry)
    base = {
        "type": "cf_router_intercepted",
        "ts": _now_iso(),
        "reason": "flyer_primary_project_created",
        "chat_id": CHAT,
    }
    entry = adapter.validate_python({**base, "binding_source": "quoted_message_id"})
    assert entry.binding_source == "quoted_message_id"
    assert adapter.validate_python(base).binding_source == ""  # pre-field rows
    echo = adapter.validate_python({**base, "reason": "flyer_quote_echo_suppressed"})
    assert echo.reason == "flyer_quote_echo_suppressed"


# ---------------------------------------------------------------------------
# Hooks-level: approve binds to the quoted project end-to-end
# ---------------------------------------------------------------------------


def _wire_intercept_mocks(hooks_mod, actions_mod, monkeypatch):
    calls = {"finalized": [], "audits": [], "sends": []}
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender",
                        lambda _c: (PHONE, "customer"))
    monkeypatch.setattr(actions_mod, "flyer_business_scope_block_message",
                        lambda _c, _b: "")

    def fake_finalize(chat_id, project_id, message_id):
        calls["finalized"].append(project_id)
        return True, "ok"
    monkeypatch.setattr(actions_mod, "finalize_and_send_flyer", fake_finalize)

    def fake_send(chat_id, message, *, action_context, allow_duplicate=False):
        calls["sends"].append(message)
        return True, "wamid.ACK", ""
    monkeypatch.setattr(actions_mod, "send_flyer_text", fake_send)

    def fake_audit(reason, chat_id, code=None, subprocess_rc=None, detail="",
                   binding_source=""):
        calls["audits"].append({"reason": reason, "detail": detail,
                                "binding_source": binding_source})
    monkeypatch.setattr(actions_mod, "audit_intercepted", fake_audit)
    return calls


def test_hooks_approve_finalizes_quoted_project(tmp_path, monkeypatch):
    hooks_mod, actions_mod = _load_plugin_modules()
    _two_project_store(tmp_path, monkeypatch, actions_mod)
    calls = _wire_intercept_mocks(hooks_mod, actions_mod, monkeypatch)

    result = hooks_mod._try_flyer_active_project_intercept(
        "APPROVE", CHAT, _quoting_event("wamid.CTA100"))
    assert result is not None and "F0100" in result["reason"]
    assert calls["finalized"] == ["F0100"]  # quoted project, not newest F0111
    approve_audits = [row for row in calls["audits"] if "approve=true" in row["detail"]]
    assert approve_audits and approve_audits[0]["binding_source"] == "quoted_message_id"


def test_hooks_approve_without_quote_uses_newest(tmp_path, monkeypatch):
    hooks_mod, actions_mod = _load_plugin_modules()
    _two_project_store(tmp_path, monkeypatch, actions_mod)
    calls = _wire_intercept_mocks(hooks_mod, actions_mod, monkeypatch)

    result = hooks_mod._try_flyer_active_project_intercept(
        "APPROVE", CHAT, SimpleNamespace(text="APPROVE"))
    assert result is not None and "F0111" in result["reason"]
    assert calls["finalized"] == ["F0111"]
    approve_audits = [row for row in calls["audits"] if "approve=true" in row["detail"]]
    assert approve_audits and approve_audits[0]["binding_source"] == "newest_updated"


# ---------------------------------------------------------------------------
# Hooks-level: quote-echo guard
# ---------------------------------------------------------------------------


def test_hooks_quote_echo_guard_suppresses_echo(tmp_path, monkeypatch):
    hooks_mod, actions_mod = _load_plugin_modules()
    calls = {"audits": [], "sends": []}
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender",
                        lambda _c: (PHONE, "customer"))
    echo_row = {"project_id": "F0211", "status": "awaiting_final_approval"}
    monkeypatch.setattr(actions_mod, "find_flyer_quote_echo_project",
                        lambda _p, _c, _b: echo_row)
    monkeypatch.setattr(actions_mod, "flyer_project_status_reply", lambda _p: "STATUS")

    def fake_send(chat_id, message, *, action_context, allow_duplicate=False):
        calls["sends"].append(message)
        return True, "wamid.ACK", ""
    monkeypatch.setattr(actions_mod, "send_flyer_text", fake_send)

    def fake_audit(reason, chat_id, code=None, subprocess_rc=None, detail="",
                   binding_source=""):
        calls["audits"].append(reason)
    monkeypatch.setattr(actions_mod, "audit_intercepted", fake_audit)

    result = hooks_mod._try_flyer_quote_echo_guard(LONG_BRIEF, CHAT, SimpleNamespace())
    assert result is not None and result["action"] == "skip"
    assert "F0211" in result["reason"]
    assert calls["audits"] == ["flyer_quote_echo_suppressed"]
    assert calls["sends"] and "reply APPROVE" in calls["sends"][0]


def test_hooks_quote_echo_guard_passes_new_brief(tmp_path, monkeypatch):
    hooks_mod, actions_mod = _load_plugin_modules()
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender",
                        lambda _c: (PHONE, "customer"))
    monkeypatch.setattr(actions_mod, "find_flyer_quote_echo_project",
                        lambda _p, _c, _b: None)
    assert hooks_mod._try_flyer_quote_echo_guard(
        "Create a flyer for July 4th specials", CHAT, SimpleNamespace()) is None


def test_hooks_quote_echo_guard_skips_media_and_owner(tmp_path, monkeypatch):
    hooks_mod, actions_mod = _load_plugin_modules()
    # media inbound: never a quote echo
    assert hooks_mod._try_flyer_quote_echo_guard(
        LONG_BRIEF, CHAT, SimpleNamespace(), media_path="/tmp/img.jpg") is None
    # owner sender: guard does not apply
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender",
                        lambda _c: (PHONE, "owner"))
    called = {"finder": False}

    def finder(_p, _c, _b):
        called["finder"] = True
        return None
    monkeypatch.setattr(actions_mod, "find_flyer_quote_echo_project", finder)
    assert hooks_mod._try_flyer_quote_echo_guard(LONG_BRIEF, CHAT, SimpleNamespace()) is None
    assert called["finder"] is False
