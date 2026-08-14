"""Focused tests for the `get_compliance_deadlines` plugin tool.

Scope is deliberately narrow: the handler contract and its authorization.
The Hermes bridge itself (registration → deferred catalog → tool_search /
tool_describe / tool_call → registry.dispatch → handler → session identity) was
proven against the pinned 0.19.1 runtime before this code was written; that
research harness is NOT recreated here. What these tests pin is our side of the
contract, with `gateway.session_context` stubbed so they need no gateway.

Tests that need a POSIX host are marked `linux_only` — see the marker for the
two reasons. Everything that runs portably (schema shape, identity-free
arguments, unbound session, registration) stays unmarked and runs everywhere,
including the Windows build gate.
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

# Two distinct POSIX dependencies, both real:
#   1. loading the store goes through safe_io, which uses fcntl;
#   2. the fake identify-sender fixture is a `#!/usr/bin/env python3` script, so
#      any test that must reach a resolved ROLE needs a POSIX exec. On Windows
#      subprocess raises OSError, resolve_identity returns None, and the handler
#      correctly reports `identity_unresolved` — right behavior, wrong precondition
#      for those assertions. Production runs the Linux identify-sender kernel;
#      making the synthetic fixture Windows-executable is not a product need.
linux_only = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="needs POSIX: safe_io/fcntl for store loads, and an executable "
           "shebang fixture for identify-sender role resolution (Linux only)",
)


def _install_session_stub(principal: str) -> None:
    """Stand in for the gateway ContextVar with an explicit, exact value."""
    mod = types.ModuleType("gateway.session_context")
    # A bound turn: the handler binds its deterministic reply through the real
    # safe_io registry, so these tests exercise the actual guard rather than a
    # stub of it.
    values = {"HERMES_SESSION_USER_ID": principal,
              "HERMES_SESSION_ID": "sess-test",
              "HERMES_SESSION_MESSAGE_ID": "msg-test"}
    mod.get_session_env = lambda name, default="": values.get(name, default)
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
    return tmp_path


PKG = "shift_agent_read_pkg"  # the shipped dir name has a hyphen; alias for import


def _load_package():
    """Import the plugin as a PACKAGE so its relative imports resolve.

    The shipped directory is `shift-agent-read`, which is not a legal module
    name, so it is aliased. Hermes' own loader does the equivalent; the point of
    doing it here is that `from .identity import ...` must work exactly as it
    will at runtime — no sys.path insertion of the plugin directory.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        PKG, PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = mod
    spec.loader.exec_module(mod)
    return mod


def _tool(principal="19045550100@s.whatsapp.net"):
    """(Re)load the plugin package with a session stub already in place."""
    _install_session_stub(principal)
    for name in list(sys.modules):
        if name == PKG or name.startswith(PKG + "."):
            del sys.modules[name]
    return _load_package().compliance_tool


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


@linux_only  # needs the shebang fixture to resolve role=employee
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


@linux_only  # needs the shebang fixture to resolve role=employee
def test_model_supplied_identity_arguments_cannot_grant_owner(env):
    spoof = {"owner": True, "role": "owner", "phone": "+19045550100"}
    out = json.loads(_tool("19045550200@s.whatsapp.net").handler(spoof))
    assert out == {"ok": False, "refused": "not_owner"}


# ── the three states ───────────────────────────────────────────────────────

@linux_only  # owner must resolve before the store check is reached
def test_missing_state_is_distinct(env):
    assert not (env / "state" / "compliance-items.json").exists()
    out = json.loads(_tool().handler({}))
    assert out["source_status"] == "missing"
    assert out["coverage_status"] == "not_configured"
    # Coverage is UNKNOWN, not zero: no zero-shaped authoritative fields.
    for absent in ("tracked_total", "in_window", "items", "window_days"):
        assert absent not in out, f"{absent!r} must not appear on a missing source"


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


# ── fail closed: never an authoritative empty ──────────────────────────────

@linux_only
def test_unreadable_state_fails_closed(env):
    """A corrupt/schema-invalid store is NOT an authoritative empty result.

    The owner's deadlines may exist and simply be unreadable; answering
    "nothing due" here is the UNVERIFIED_ABSENCE_CLAIM failure in its purest
    form. This is that invariant in executable form.
    """
    (env / "state" / "compliance-items.json").write_text(
        json.dumps({"schema_version": 1, "items": [{"id": "no_name_field"}]}),
        encoding="utf-8")
    out = json.loads(_tool().handler({}))
    assert out["ok"] is False
    assert out["error"] == "state_unreadable"
    for absent in ("items", "tracked_total", "in_window", "source_status"):
        assert absent not in out, f"{absent!r} must not appear in a failure result"


@linux_only
def test_unestablishable_today_fails_closed(env, monkeypatch):
    """No silent fallback: days_until must never come from a guessed date.

    Driven through an unparseable SHIFT_AGENT_NOW_OVERRIDE rather than a bad
    timezone, because a malformed config now surfaces as config_unavailable at
    the enable gate before this path is reached (mirrors the equipment tool's
    test of the same invariant).
    """
    monkeypatch.setenv("SHIFT_AGENT_NOW_OVERRIDE", "not-a-timestamp")
    _write_cfg(env)
    _seed(env, [_item("health_inspect", "2026-09-01")])
    out = json.loads(_tool().handler({}))
    assert out["ok"] is False
    assert out["error"] == "customer_timezone_unavailable"
    for absent in ("items", "tracked_total", "in_window", "source_status"):
        assert absent not in out


# ── registration smoke ─────────────────────────────────────────────────────

def test_register_uses_package_relative_imports_and_registers_the_tool(env):
    """Loads the plugin as a package (no sys.path insertion of the plugin dir)
    and drives register() with a collector standing in for PluginContext."""
    _install_session_stub("19045550100@s.whatsapp.net")
    for name in list(sys.modules):
        if name == PKG or name.startswith(PKG + "."):
            del sys.modules[name]
    pkg = _load_package()

    # A list, not a dict: the package registers more than one tool, and an
    # overwriting collector would silently assert only about the last one.
    registered = []

    class FakeCtx:
        def register_tool(self, **kw):
            registered.append(kw)

    pkg.register(FakeCtx())
    by_name = {r["name"]: r for r in registered}
    assert "get_compliance_deadlines" in by_name
    captured = by_name["get_compliance_deadlines"]
    assert captured["toolset"] == "shift_agent_read"
    assert callable(captured["handler"])
    assert captured["schema"]["name"] == "get_compliance_deadlines"
    assert captured["description"] == captured["schema"]["description"]
    # Every tool this package registers must land in the dedicated toolset;
    # a stray default toolset would be suppressed by `disabled_toolsets`.
    assert {r["toolset"] for r in registered} == {"shift_agent_read"}
    assert len(by_name) == len(registered), "duplicate tool name registered"
    assert str(PLUGIN_DIR) not in sys.path, (
        "importing the plugin must not put its own directory on sys.path"
    )


@linux_only
def test_result_contains_no_prose(env):
    """The handler owns facts; Hermes owns wording."""
    _seed(env, [_item("health_inspect", "2026-08-20")])
    out = json.loads(_tool().handler({}))
    assert set(out) == {"ok", "source_status", "window_days", "tracked_total",
                        "in_window", "items"}
    assert set(out["items"][0]) == {"id", "name", "category", "renewal_date",
                                    "days_until", "agency", "location_id"}


# ── turn-bound outbound binding (fail-closed) ──────────────────────────────
#
# Every zero state must bind its deterministic reply BEFORE returning the
# authoritative payload. There must be no execution path that emits a zero
# result whose outbound qualification was not bound — that combination is what
# produced the observed false "you have no compliance deadlines" answer.


class _Binder:
    """Records bind attempts; can be made to fail."""

    def __init__(self, succeed=True):
        self.succeed = succeed
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return self.succeed


@linux_only
def test_missing_binds_exact_template_before_returning(env, monkeypatch):
    t = _tool()
    b = _Binder()
    monkeypatch.setattr(t, "_bind_outbound", b)
    out = json.loads(t.handler({}))
    assert out["ok"] is True and out["source_status"] == "missing"
    assert b.calls == [t.TPL_MISSING]


@linux_only
def test_empty_binds_exact_template(env, monkeypatch):
    _seed(env, [])
    t = _tool()
    b = _Binder()
    monkeypatch.setattr(t, "_bind_outbound", b)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "empty"
    assert b.calls == [t.TPL_EMPTY]


@linux_only
def test_populated_zero_binds_template_with_actual_window(env, monkeypatch):
    _seed(env, [_item("far_off", "2027-06-01")])
    t = _tool()
    b = _Binder()
    monkeypatch.setattr(t, "_bind_outbound", b)
    out = json.loads(t.handler({"window_days": 30}))
    assert out["tracked_total"] == 1 and out["in_window"] == 0
    assert b.calls == [t.TPL_POPULATED_ZERO.format(window_days=30)]


@linux_only
def test_positive_rows_bind_nothing(env, monkeypatch):
    """Hermes keeps presentation ownership when there is something real to say."""
    _seed(env, [_item("soon", "2026-08-20")])
    t = _tool()
    b = _Binder()
    monkeypatch.setattr(t, "_bind_outbound", b)
    out = json.loads(t.handler({}))
    assert out["in_window"] == 1
    assert b.calls == []


@linux_only
@pytest.mark.parametrize("state", ["missing", "empty", "populated_zero"])
def test_bind_failure_suppresses_every_zero_payload(env, monkeypatch, state):
    """THE load-bearing rule: no zero evidence without a bound qualification."""
    if state == "empty":
        _seed(env, [])
    elif state == "populated_zero":
        _seed(env, [_item("far_off", "2027-06-01")])
    t = _tool()
    monkeypatch.setattr(t, "_bind_outbound", _Binder(succeed=False))
    out = json.loads(t.handler({}))
    assert out["ok"] is False
    assert out["refused"] == "outbound_truthfulness_guard_unavailable"
    for absent in ("tracked_total", "in_window", "items", "window_days",
                   "source_status", "coverage_status"):
        assert absent not in out, f"{absent!r} leaked on a guard-unavailable refusal"


def test_description_carries_the_proven_rules(env):
    """These sentences are the reason the falsification passed; a future edit
    must not drop them silently."""
    d = _tool().DESCRIPTION
    assert "TRACKED" in d
    assert "does NOT establish that the owner has no deadlines" in d
    assert "NOT that there are no obligations" in d
    assert "Do not generalize beyond the tracked calendar" in d
    assert "OMIT window_days" in d
    assert "90-day default" in d


def test_window_days_description_pins_explicit_timeframe_only(env):
    w = _tool().SCHEMA["parameters"]["properties"]["window_days"]["description"]
    assert "ONLY" in w and "explicit" in w and "omit" in w.lower()


# ── the config enable gate ─────────────────────────────────────────────────
#
# cfg.compliance.enabled defaults False, and an absent block validates to that
# default, so an un-onboarded business must hear that tracking is off — never
# "nothing due". The gate was documented as the lever and never read by the
# tool; check-compliance-deadlines.py:488 has always honored it, so only the
# owner-facing read was ungated.
#
# These resolve identity by monkeypatching `resolve_identity` rather than
# through the shebang fixture the rest of this file uses, mirroring
# test_shift_agent_read_equipment_tool.py — the gate must be provable on any
# host, and a POSIX-only skip would leave it unverified where CI runs.


def _gated_tool(monkeypatch, principal="19045550100@s.whatsapp.net"):
    tool = _tool(principal)
    import importlib
    identity = importlib.import_module(PKG + ".identity")
    monkeypatch.setattr(identity, "resolve_identity",
                        lambda p: {"role": "owner", "roles": ["owner"]}
                        if p.startswith("19045550100") else None)
    return tool


def _write_cfg(env, compliance_block=...):
    """Rewrite config.yaml. Pass compliance_block=None to omit the block."""
    cfg = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
    }
    if compliance_block is ...:
        compliance_block = {"enabled": True}
    if compliance_block is not None:
        cfg["compliance"] = compliance_block
    (env / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_disabled_agent_reports_disabled_not_nothing_due(env, monkeypatch):
    _write_cfg(env, {"enabled": False})
    _seed(env, [_item("food_licence", "2026-08-12")])   # would be due
    t = _gated_tool(monkeypatch)
    b = _Binder()
    monkeypatch.setattr(t, "_bind_outbound", b)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "disabled"
    assert out["coverage_status"] == "not_enabled"
    assert b.calls == [t.TPL_DISABLED]
    # No zero-shaped fields and no leak of the store that was never consulted.
    for absent in ("tracked_total", "in_window", "items", "window_days"):
        assert absent not in out, f"{absent!r} must not appear on a disabled agent"
    assert "food_licence" not in json.dumps(out)


def test_disabled_agent_does_not_read_the_store(env, monkeypatch):
    """Falsifiable: the store is corrupt, so any read would surface
    state_unreadable. Getting `disabled` proves the gate returned first."""
    _write_cfg(env, {"enabled": False})
    (env / "state" / "compliance-items.json").write_text("{not json", encoding="utf-8")
    t = _gated_tool(monkeypatch)
    monkeypatch.setattr(t, "_bind_outbound", _Binder())
    assert json.loads(t.handler({}))["source_status"] == "disabled"


def test_absent_config_block_is_treated_as_disabled(env, monkeypatch):
    """ComplianceConfig.enabled defaults False — same as the equipment block —
    so an unconfigured box is OFF."""
    _write_cfg(env, None)
    _seed(env, [_item("food_licence", "2026-08-12")])
    t = _gated_tool(monkeypatch)
    b = _Binder()
    monkeypatch.setattr(t, "_bind_outbound", b)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "disabled"
    assert b.calls == [t.TPL_DISABLED]


def test_bind_failure_suppresses_the_disabled_payload(env, monkeypatch):
    _write_cfg(env, {"enabled": False})
    # Seeded in-window item: without the gate the handler would reach the
    # populated path and return ok=True, so this test fails if the gate is gone.
    _seed(env, [_item("food_licence", "2026-08-12")])
    t = _gated_tool(monkeypatch)
    monkeypatch.setattr(t, "_bind_outbound", _Binder(succeed=False))
    out = json.loads(t.handler({}))
    assert out["ok"] is False
    assert out["refused"] == "outbound_truthfulness_guard_unavailable"
    assert "source_status" not in out and "coverage_status" not in out


def test_unreadable_config_fails_closed_rather_than_claiming_disabled(env, monkeypatch):
    """"Not enabled" is a claim about configuration. If the config could not be
    read, that claim is unproven — and so is any answer from the store."""
    (env / "config.yaml").write_text("customer: [unclosed\n", encoding="utf-8")
    _seed(env, [_item("food_licence", "2026-08-12")])
    out = json.loads(_gated_tool(monkeypatch).handler({}))
    assert out["ok"] is False
    assert out["error"] == "config_unavailable"
    for absent in ("items", "tracked_total", "in_window", "source_status"):
        assert absent not in out


@linux_only
def test_enabled_agent_reads_the_store_normally(env, monkeypatch):
    _write_cfg(env, {"enabled": True})
    _seed(env, [_item("food_licence", "2026-08-12")])
    t = _gated_tool(monkeypatch)
    monkeypatch.setattr(t, "_bind_outbound", _Binder())
    out = json.loads(t.handler({}))
    assert out["source_status"] == "populated"
    assert out["tracked_total"] == 1
