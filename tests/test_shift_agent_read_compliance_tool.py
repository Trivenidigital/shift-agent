"""Focused tests for the `get_compliance_deadlines` plugin tool.

Scope is deliberately narrow: the handler contract and its authorization.
The Hermes bridge itself (registration → deferred catalog → tool_search /
tool_describe / tool_call → registry.dispatch → handler → session identity) was
proven against the pinned 0.19.1 runtime before this code was written; that
research harness is NOT recreated here. What these tests pin is our side of the
contract, with `gateway.session_context` stubbed so they need no gateway.

Linux-only: the populated paths load the store through safe_io (fcntl).
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import sys
import types
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "shift-agent-read"
PLATFORM_DIR = REPO / "src" / "platform"

linux_only = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="store load depends on safe_io which uses fcntl (Linux only)",
)


def _install_session_stub(principal: str) -> None:
    """Stand in for the gateway ContextVar with an explicit, exact value."""
    mod = types.ModuleType("gateway.session_context")
    mod.get_session_env = lambda name, default="": (
        principal if name == "HERMES_SESSION_USER_ID" else default
    )
    pkg = sys.modules.get("gateway") or types.ModuleType("gateway")
    pkg.session_context = mod
    sys.modules["gateway"] = pkg
    sys.modules["gateway.session_context"] = mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Config + roster + a fake identify-sender; store deliberately absent."""
    (tmp_path / "state").mkdir()
    cfg = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "compliance": {"enabled": True},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    fake = tmp_path / "identify-sender"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "arg = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "role = 'owner' if arg.startswith('19045550100') else "
        "('employee' if arg.startswith('19045550200') else 'unknown')\n"
        "print(json.dumps({'role': role}))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("SHIFT_AGENT_COMPLIANCE_ITEMS_PATH",
                       str(tmp_path / "state" / "compliance-items.json"))
    monkeypatch.setenv("SHIFT_AGENT_IDENTIFY_SENDER_BIN", str(fake))
    monkeypatch.setenv("SHIFT_AGENT_NOW_OVERRIDE", "2026-08-08T09:00:00-04:00")
    monkeypatch.syspath_prepend(str(PLATFORM_DIR))
    monkeypatch.syspath_prepend(str(PLUGIN_DIR))
    return tmp_path


def _tool(principal="19045550100@s.whatsapp.net"):
    """(Re)import the handler with a session stub already in place."""
    _install_session_stub(principal)
    for name in ("identity", "compliance_tool"):
        sys.modules.pop(name, None)
    return importlib.import_module("compliance_tool")


def _seed(env, items):
    (env / "state" / "compliance-items.json").write_text(
        json.dumps({"schema_version": 1, "items": items}), encoding="utf-8")


def _item(item_id, renewal_date, **kw):
    base = {"id": item_id, "name": item_id.replace("_", " ").title(),
            "category": "inspection", "renewal_date": renewal_date,
            "recurrence_days": 365}
    base.update(kw)
    return base


# ── registration contract ──────────────────────────────────────────────────

def test_schema_is_the_inner_function_object_with_a_description(env):
    """Double-wrapping empties function.description and removes the tool's line
    from the deferred catalog, making it undiscoverable. Pin the shape."""
    t = _tool()
    assert set(t.SCHEMA) == {"name", "description", "parameters"}
    assert "type" not in t.SCHEMA and "function" not in t.SCHEMA
    assert t.SCHEMA["name"] == "get_compliance_deadlines"
    assert len(t.SCHEMA["description"]) > 80
    assert "todo" in t.SCHEMA["description"].lower(), (
        "description should distinguish this capability from the todo list"
    )


def test_arguments_carry_no_identity_field(env):
    """A model must not be able to assert who it is."""
    props = _tool().SCHEMA["parameters"]["properties"]
    assert set(props) == {"window_days"}
    forbidden = {"owner", "role", "phone", "user", "user_id", "sender", "identity"}
    assert not (set(props) & forbidden)
    assert _tool().SCHEMA["parameters"]["required"] == []


def test_window_days_is_bounded_in_the_schema(env):
    w = _tool().SCHEMA["parameters"]["properties"]["window_days"]
    assert w["type"] == "integer" and w["minimum"] == 1 and w["maximum"] == 365


# ── authorization ──────────────────────────────────────────────────────────

@linux_only
def test_owner_succeeds(env):
    _seed(env, [_item("health_inspect", "2026-09-01")])
    out = json.loads(_tool().handler({}))
    assert out["ok"] is True and out["source_status"] == "populated"


def test_non_owner_refuses(env):
    out = json.loads(_tool("19045550200@s.whatsapp.net").handler({}))
    assert out == {"ok": False, "refused": "not_owner"}


def test_unbound_session_refuses(env):
    out = json.loads(_tool("").handler({}))
    assert out == {"ok": False, "refused": "unbound_session"}


def test_refusals_carry_no_items_key(env):
    """A refusal must never be readable as an authoritative 'nothing due'."""
    for principal in ("", "19045550200@s.whatsapp.net"):
        out = json.loads(_tool(principal).handler({}))
        assert out["ok"] is False
        assert "items" not in out and "in_window" not in out and "tracked_total" not in out


def test_model_supplied_identity_arguments_cannot_grant_owner(env):
    spoof = {"owner": True, "role": "owner", "phone": "+19045550100"}
    out = json.loads(_tool("19045550200@s.whatsapp.net").handler(spoof))
    assert out == {"ok": False, "refused": "not_owner"}


# ── the three states ───────────────────────────────────────────────────────

def test_missing_state_is_distinct(env):
    assert not (env / "state" / "compliance-items.json").exists()
    out = json.loads(_tool().handler({}))
    assert out["source_status"] == "missing" and out["tracked_total"] == 0


@linux_only
def test_empty_state_is_distinct(env):
    _seed(env, [])
    out = json.loads(_tool().handler({}))
    assert out["source_status"] == "empty" and out["tracked_total"] == 0


@linux_only
def test_populated_with_rows_in_window(env):
    _seed(env, [_item("health_inspect", "2026-08-20"),
                _item("tabc_permit", "2026-09-30", category="license_renewal")])
    out = json.loads(_tool().handler({}))
    assert out["source_status"] == "populated"
    assert out["tracked_total"] == 2 and out["in_window"] == 2
    assert [r["id"] for r in out["items"]] == ["health_inspect", "tabc_permit"]
    assert out["items"][0]["days_until"] == 12


@linux_only
def test_populated_with_zero_rows_in_window_is_not_empty(env):
    """THE distinction: rows exist, none are soon. Not 'nothing tracked'."""
    _seed(env, [_item("far_off", "2027-06-01")])
    out = json.loads(_tool().handler({}))
    assert out["source_status"] == "populated"
    assert out["tracked_total"] == 1 and out["in_window"] == 0 and out["items"] == []


@linux_only
def test_window_argument_narrows_and_is_clamped(env):
    _seed(env, [_item("soon", "2026-08-20"), _item("later", "2026-11-01")])
    assert json.loads(_tool().handler({"window_days": 30}))["in_window"] == 1
    assert json.loads(_tool().handler({"window_days": 9999}))["window_days"] == 365
    assert json.loads(_tool().handler({"window_days": 0}))["window_days"] == 1
    assert json.loads(_tool().handler({"window_days": "abc"}))["window_days"] == 90


# ── result contract ────────────────────────────────────────────────────────

@linux_only
def test_handler_returns_json_text_not_a_dict(env):
    _seed(env, [_item("health_inspect", "2026-09-01")])
    res = _tool().handler({})
    assert isinstance(res, str)
    assert isinstance(json.loads(res), dict)


@linux_only
def test_result_contains_no_prose(env):
    """The handler owns facts; Hermes owns wording."""
    _seed(env, [_item("health_inspect", "2026-08-20")])
    out = json.loads(_tool().handler({}))
    assert set(out) == {"ok", "source_status", "window_days", "tracked_total",
                        "in_window", "items"}
    assert set(out["items"][0]) == {"id", "name", "category", "renewal_date",
                                    "days_until", "agency", "location_id"}
