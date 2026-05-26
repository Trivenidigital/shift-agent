#!/usr/bin/env python3
"""Run no-live-send conversation eval fixtures."""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
PLATFORM_DIR = REPO / "src" / "platform"

if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))


def _load_plugin_modules():
    pkg_name = "cf_router_conversation_eval"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]

    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg_name] = pkg_mod

    loaded = []
    for name in ("actions", "hooks"):
        full = f"{pkg_name}.{name}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{name}.py"))
        spec = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        loader.exec_module(mod)
        loaded.append(mod)
    return loaded[1], loaded[0]


def _run_flyer_fixture(path: Path) -> tuple[bool, str]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    hooks, actions = _load_plugin_modules()
    sent: list[str] = []
    audits: list[dict] = []
    customer = {
        "customer_id": "CUST-EVAL",
        "business_name": "Eval Business",
        "status": "trial",
        "plan_id": "trial",
    }

    actions.is_flyer_enabled = lambda: True
    actions.mark_cf_router_inbound_seen = lambda *_args, **_kwargs: False
    actions.flyer_campaign_cta_text = lambda _text: ""
    actions.lid_to_phone_via_identify_sender = lambda _chat_id: ("+15555550123", "customer")
    actions.find_flyer_customer_by_sender = lambda _phone, _chat_id: customer
    actions.find_active_flyer_project_by_sender = lambda _phone, _chat_id: fixture.get("active_project")
    actions.find_paid_flyer_guest_order = lambda _phone, _chat_id: None
    actions.find_flyer_intake_session_by_sender = lambda _phone, _chat_id: None
    actions.find_flyer_onboarding_session_by_sender = lambda _phone, _chat_id: None
    actions.send_flyer_text = lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "eval-mid", "")
    actions.audit_intercepted = lambda **kwargs: audits.append(kwargs)
    actions.audit_flyer_hermes_intent_decision = lambda **_kwargs: None
    actions.trigger_create_flyer_project = lambda **_kwargs: (_ for _ in ()).throw(AssertionError("fixture must not create flyer"))
    actions.trigger_flyer_onboarding = lambda **_kwargs: (_ for _ in ()).throw(AssertionError("fixture must not start onboarding"))
    actions.trigger_flyer_account_command = lambda **_kwargs: (
        True,
        "eval-account",
        {
            "handled": True,
            "reply_text": "Flyer Studio\n------------\nPlease reply CONFIRM UPDATE to apply this account change.",
            "customer_id": "CUST-EVAL",
            "status": "trial",
        },
    )

    event = SimpleNamespace(
        text=fixture["inbound_text"],
        chat_id="15555550123@s.whatsapp.net",
        message_id=fixture["id"],
    )
    result = hooks.pre_gateway_dispatch(event)
    expected_reason = fixture["expected_reason"]
    actual_reason = (result or {}).get("reason")
    if actual_reason != expected_reason:
        return False, f"{path.name}: expected reason {expected_reason!r}, got {actual_reason!r}"
    reply_must_contain = fixture.get("reply_must_contain")
    if reply_must_contain and not sent:
        return False, f"{path.name}: expected customer reply containing {reply_must_contain!r}, got no send"
    if reply_must_contain and reply_must_contain not in sent[0]:
        return False, f"{path.name}: reply missing {reply_must_contain!r}: {sent[0]!r}"
    return True, path.name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", default="all", choices=["all", "flyer"])
    args = parser.parse_args(argv)

    agents = ["flyer"] if args.agent in {"all", "flyer"} else []
    total = 0
    failed: list[str] = []
    for agent in agents:
        fixture_dir = REPO / "tests" / "conversation_evals" / "seed" / agent
        for path in sorted(fixture_dir.glob("*.json")):
            total += 1
            ok, detail = _run_flyer_fixture(path)
            if not ok:
                failed.append(detail)

    for failure in failed:
        print(f"FAIL {failure}")
    print(f"conversation_evals agents={','.join(agents)} total={total} failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
