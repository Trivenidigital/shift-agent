"""Regression: the 2026-06-02 stale-intake-session hijack (P0-2b).

Symptom: a customer replied to an awaiting-approval flyer asking for a change
("address & phone not visible, correct that") and got re-prompted "choose a
creation mode" instead of a revision. Root cause: a stale `choosing_mode` intake
session survived the project-create bypass, so a later non-bypass reply re-
entered the intake state machine.

Fix (close-on-handoff): when the intake intercept BYPASSES to the project-create
flow, it discards the lingering (non-protected) intake session + audits it, so no
stale session can hijack a later reply. Reconstructed here both with mocks
(Windows) and end-to-end with the real canonical finder/discard + lid-cache
(fcntl-gated), keying the session under the phone-JID while the customer messages
from their LID — the exact identity split of the incident.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
for _p in (str(REPO / "src" / "platform"), str(REPO / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_WINDOWS = platform.system() == "Windows"

PHONE = "+17329837841"
PHONE_JID = "17329837841@s.whatsapp.net"
LID = "201975216009469@lid"


def _load_plugin_modules():
    pkg_name = "cf_router_hijack_regression_pkg"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]
    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg_name] = pkg_mod
    actions_full = f"{pkg_name}.actions"
    actions_loader = importlib.machinery.SourceFileLoader(actions_full, str(PLUGIN_DIR / "actions.py"))
    actions_mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(actions_full, actions_loader))
    sys.modules[actions_full] = actions_mod
    actions_loader.exec_module(actions_mod)
    hooks_full = f"{pkg_name}.hooks"
    hooks_loader = importlib.machinery.SourceFileLoader(hooks_full, str(PLUGIN_DIR / "hooks.py"))
    hooks_mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(hooks_full, hooks_loader))
    sys.modules[hooks_full] = hooks_mod
    hooks_loader.exec_module(hooks_mod)
    return hooks_mod, actions_mod


# ── (b) bypass closes the stale session — the hijack window (Windows, mocked) ──

def test_bypass_discards_stale_intake_session(monkeypatch):
    hooks, actions = _load_plugin_modules()
    customer = {"customer_id": "CUST0001", "status": "active", "business_name": "Lakshmi's Kitchen"}
    stale = {"chat_id": PHONE_JID, "sender_phone": PHONE, "status": "choosing_mode", "source": "start_trial"}
    discards: list = []
    audits: list = []
    triggered: list = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: (PHONE, "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: customer)
    monkeypatch.setattr(actions, "find_flyer_intake_session_by_sender", lambda _p, _c: stale)
    monkeypatch.setattr(actions, "should_bypass_intake_for_clear_intent", lambda **_k: "new_flyer_text_only")
    monkeypatch.setattr(actions, "_detect_inbound_script", lambda _t: "latin")
    monkeypatch.setattr(actions, "audit_flyer_intake_bypassed", lambda **_k: None)
    monkeypatch.setattr(actions, "note_flyer_intake_bypass_active", lambda **_k: None)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))
    monkeypatch.setattr(
        actions, "discard_flyer_intake_session_by_sender",
        lambda phone, chat_id: (discards.append((phone, chat_id)) or True),
    )
    monkeypatch.setattr(
        actions, "trigger_flyer_intake",
        lambda **kw: triggered.append(kw) or (True, "", {"action": "choose_mode"}),
    )

    # Customer messages from their LID (identity split); a clear new-flyer intent bypasses intake.
    result = hooks._try_flyer_intake_intercept("Create a fresh Diwali weekend sale flyer", LID, {"message_id": "m1"})

    assert result is None                                   # bypass path
    assert discards == [(PHONE, LID)]                       # stale session CLOSED on handoff
    assert any(a.get("reason") == "flyer_intake_session_closed_on_handoff" for a in audits)
    assert triggered == []                                  # never re-entered intake (no choose_mode)


def test_revision_reply_not_hijacked_without_stale_session(monkeypatch):
    hooks, actions = _load_plugin_modules()
    customer = {"customer_id": "CUST0001", "status": "active", "business_name": "Lakshmi's Kitchen"}
    triggered: list = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: (PHONE, "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: customer)
    # Post-fix state: the stale session was already discarded on handoff.
    monkeypatch.setattr(actions, "find_flyer_intake_session_by_sender", lambda _p, _c: None)
    monkeypatch.setattr(actions, "should_bypass_intake_for_clear_intent", lambda **_k: None)
    monkeypatch.setattr(actions, "note_flyer_intake_bypass_active", lambda **_k: None)
    monkeypatch.setattr(actions, "audit_flyer_intake_bypassed", lambda **_k: None)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_k: None)
    monkeypatch.setattr(actions, "discard_flyer_intake_session_by_sender", lambda *_a: False)
    monkeypatch.setattr(
        actions, "trigger_flyer_intake",
        lambda **kw: triggered.append(kw) or (True, "", {"action": "choose_mode"}),
    )

    result = hooks._try_flyer_intake_intercept(
        "Address and phone number is not visible. I would like you to correct that",
        LID,
        {"message_id": "m2"},
    )
    # Falls through (returns None) to the active-project/revision path — NOT hijacked.
    assert result is None
    assert triggered == []


# ── end-to-end reconstruction with the real canonical finder/discard ──────────

@pytest.mark.skipif(_WINDOWS, reason="real discard write path imports safe_io.FileLock (fcntl — Linux only)")
def test_lid_reply_discards_phone_keyed_session_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    (tmp_path / "lid-cache.json").write_text(
        json.dumps({"schema_version": 1, "pairs": [{"phone": PHONE, "lid": LID}]}), encoding="utf-8"
    )
    hooks, actions = _load_plugin_modules()
    cust = tmp_path / "customers.json"
    fresh = datetime.now(timezone.utc).isoformat()
    cust.write_text(
        json.dumps({
            "schema_version": 1,
            "customers": [],
            "intake_sessions": [{
                "chat_id": PHONE_JID, "sender_phone": PHONE,   # keyed to the phone-JID
                "status": "choosing_mode", "source": "start_trial",
                "started_at": fresh, "updated_at": fresh,
            }],
        }),
        encoding="utf-8",
    )
    actions.FLYER_CUSTOMERS_PATH = cust

    # REAL find_flyer_intake_session_by_sender + discard_flyer_intake_session_by_sender.
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: (None, "unknown"))  # LID unresolved by identify-sender
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: None)
    monkeypatch.setattr(actions, "should_bypass_intake_for_clear_intent", lambda **_k: "new_flyer_text_only")
    monkeypatch.setattr(actions, "_detect_inbound_script", lambda _t: "latin")
    monkeypatch.setattr(actions, "audit_flyer_intake_bypassed", lambda **_k: None)
    monkeypatch.setattr(actions, "note_flyer_intake_bypass_active", lambda **_k: None)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_k: None)

    # Customer messages from their LID; the phone-keyed session must be found + closed.
    result = hooks._try_flyer_intake_intercept("Create a fresh Diwali weekend sale flyer", LID, {"message_id": "m3"})
    assert result is None
    remaining = json.loads(cust.read_text(encoding="utf-8"))["intake_sessions"]
    assert remaining == []   # stale phone-keyed session discarded via the LID reply
