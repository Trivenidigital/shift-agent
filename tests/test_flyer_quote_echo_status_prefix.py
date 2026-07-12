"""Fix 2 (2026-07-12) — quote-echo duplicate-guard must NOT prepend the echo
project's past-tense status line.

Incident: a customer sends a NEW brief that flat-echoes a DELIVERED project's
raw_request. `_try_flyer_quote_echo_guard` prepended the echo project's status
reply — for a delivered project that is "The final flyer files have been
delivered." — before the NEW/APPROVE choice. The customer reads that as THIS
turn's outcome (files "have been delivered" for a request that produced nothing).
The guard now sends only the Flyer Studio banner + the NEW-choice line, which
already names "your current flyer".

cf-router actions/hooks import safe_io (fcntl-only) -> Docker/Linux, not Windows.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import platform
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="cf-router actions/hooks import safe_io (fcntl-only); runs on Linux CI",
)

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
PLATFORM_DIR = REPO / "src" / "platform"
CHAT = "17329837841@c.us"
PHONE = "+17329837841"


def _load():
    sys.path.insert(0, str(PLATFORM_DIR))
    pkg = "cf_router_quote_echo_prefix_pkg"
    for m in list(sys.modules):
        if m == pkg or m.startswith(pkg + "."):
            del sys.modules[m]
    pkg_spec = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg] = importlib.util.module_from_spec(pkg_spec)
    for name in ("actions", "hooks"):
        full = f"{pkg}.{name}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{name}.py"))
        spec = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        loader.exec_module(mod)
    return sys.modules[f"{pkg}.hooks"], sys.modules[f"{pkg}.actions"]


@pytest.fixture
def wired(monkeypatch):
    hooks, actions = _load()
    rec = {"sends": []}

    def set_a(name, fn):
        monkeypatch.setattr(actions, name, fn, raising=False)

    set_a("flyer_visible_message_text", lambda t: t)
    set_a("lid_to_phone_via_identify_sender", lambda _c: (PHONE, "customer"))
    set_a("save_flyer_quote_echo_pending", lambda **k: None)
    set_a("audit_intercepted", lambda **k: None)
    set_a("send_flyer_text", lambda _c, _m, **k: rec["sends"].append(_m) or (True, "mid", ""))

    def run(status):
        set_a(
            "find_flyer_quote_echo_project",
            lambda *_a, **_k: {"project_id": "F0100", "status": status},
        )
        ev = SimpleNamespace(text="Diwali weekend special samosas", chat_id=CHAT, message_id="m-1")
        return hooks._try_flyer_quote_echo_guard("Diwali weekend special samosas", CHAT, ev)

    return rec, run


def test_delivered_echo_does_not_state_this_turn_delivered(wired):
    rec, run = wired
    result = run("delivered")
    assert isinstance(result, dict) and result["action"] == "skip"
    assert len(rec["sends"]) == 1
    reply = rec["sends"][0]
    # The misleading past-tense delivery/status line must NOT be prepended.
    assert "have been delivered" not in reply
    assert "delivered" not in reply.lower()
    # The NEW choice line (referring to the EXISTING flyer) is preserved.
    assert "Reply NEW" in reply
    assert "your current flyer" in reply


def test_awaiting_approval_echo_keeps_approve_choice_without_status_prefix(wired):
    rec, run = wired
    run("awaiting_final_approval")
    reply = rec["sends"][0]
    assert "have been delivered" not in reply
    # APPROVE branch choice line survives for the pre-delivery statuses.
    assert "Reply NEW" in reply
    assert "APPROVE" in reply
