"""Focused tests for the `get_equipment_maintenance_due` plugin tool.

Scope mirrors tests/test_shift_agent_read_compliance_tool.py: the handler
contract and its authorization, with `gateway.session_context` stubbed so no
gateway is needed. The Hermes bridge itself was proven against the pinned 0.19.1
runtime before that tool was written and is not recreated here.

Authorization is driven by monkeypatching `identity.resolve_identity` rather than
the compliance file's `#!/usr/bin/env python3` fixture, so the roles[] checks —
including the dual-role principal — run on every host, Windows included. Tests
that reach the store need safe_io/fcntl and stay `linux_only`.
"""
from __future__ import annotations

import json
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
    reason="needs POSIX: loading the store goes through safe_io, which uses fcntl",
)

OWNER = "19045550100@s.whatsapp.net"
EMPLOYEE = "19045550200@s.whatsapp.net"
DUAL_ROLE = "19045550300@s.whatsapp.net"

# What identify-sender returns per principal. DUAL_ROLE is the B1 bug class: a
# genuine owner who is also on the roster resolves scalar `employee` by LID, so
# a scalar check refuses them while roles[] does not.
IDENTITIES = {
    OWNER: {"role": "owner", "roles": ["owner"]},
    EMPLOYEE: {"role": "employee", "roles": ["employee"]},
    DUAL_ROLE: {"role": "employee", "roles": ["employee", "owner"]},
}


def _install_session_stub(principal: str) -> None:
    """Stand in for the gateway ContextVar with an explicit, exact value."""
    mod = types.ModuleType("gateway.session_context")
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
    """Config + a store path that is deliberately absent to begin with."""
    (tmp_path / "state").mkdir()
    cfg = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "equipment_maintenance": {"enabled": True},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("SHIFT_AGENT_EQUIPMENT_ITEMS_PATH",
                       str(tmp_path / "state" / "equipment-items.json"))
    monkeypatch.setenv("SHIFT_AGENT_NOW_OVERRIDE", "2026-08-08T09:00:00-04:00")
    monkeypatch.syspath_prepend(str(PLATFORM_DIR))
    return tmp_path


# A dedicated alias so this file's package copy — and the identity module it
# monkeypatches — never collides with the compliance test file's copy.
PKG = "shift_agent_read_pkg_equipment"


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


def _pkg(principal=OWNER):
    """(Re)load the plugin package with a session stub already in place."""
    _install_session_stub(principal)
    for name in list(sys.modules):
        if name == PKG or name.startswith(PKG + "."):
            del sys.modules[name]
    return _load_package()


def _tool(monkeypatch=None, principal=OWNER):
    """The equipment tool, with identify-sender resolution stubbed in-process."""
    pkg = _pkg(principal)
    if monkeypatch is not None:
        monkeypatch.setattr(pkg.identity, "resolve_identity",
                            lambda p: IDENTITIES.get(p))
    return pkg.equipment_tool


def _write_cfg(env, equipment_block=...):
    """Rewrite config.yaml. Pass equipment_block=None to omit the block entirely."""
    cfg = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
    }
    if equipment_block is ...:
        equipment_block = {"enabled": True}
    if equipment_block is not None:
        cfg["equipment_maintenance"] = equipment_block
    (env / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _seed(env, items):
    (env / "state" / "equipment-items.json").write_text(
        json.dumps({"schema_version": 1, "items": items}), encoding="utf-8")


def _item(item_id, next_service_date, **kw):
    base = {"id": item_id, "name": item_id.replace("_", " ").title(),
            "category": "refrigeration", "next_service_date": next_service_date,
            "interval_days": 90}
    base.update(kw)
    return base


class _Binder:
    """Records bind attempts; can be made to fail."""

    def __init__(self, succeed=True):
        self.succeed = succeed
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return self.succeed


def _unbound(monkeypatch, tool, succeed=True):
    """Replace the real turn binder so store-free paths run off-Linux too."""
    b = _Binder(succeed=succeed)
    monkeypatch.setattr(tool, "_bind_outbound", b)
    return b


# ── registration contract ──────────────────────────────────────────────────

def test_schema_is_the_inner_function_object_with_a_description(env):
    """Double-wrapping empties function.description and removes the tool's line
    from the deferred catalog, making it undiscoverable. Pin the shape."""
    t = _tool()
    assert set(t.SCHEMA) == {"name", "description", "parameters"}
    assert "type" not in t.SCHEMA and "function" not in t.SCHEMA
    assert t.SCHEMA["name"] == "get_equipment_maintenance_due"
    assert len(t.SCHEMA["description"]) > 80
    assert "compliance" in t.SCHEMA["description"].lower(), (
        "description should distinguish this capability from the compliance calendar"
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


def test_register_puts_the_tool_in_the_surviving_toolset(env):
    """`agent.disabled_toolsets` suppresses `skills` and `terminal` by name on
    the live gateway; `shift_agent_read` is what makes this path reachable."""
    registered = []

    class FakeCtx:
        def register_tool(self, **kw):
            registered.append(kw)

    _pkg().register(FakeCtx())
    by_name = {r["name"]: r for r in registered}
    assert "get_equipment_maintenance_due" in by_name
    captured = by_name["get_equipment_maintenance_due"]
    assert captured["toolset"] == "shift_agent_read"
    assert callable(captured["handler"])
    assert captured["schema"]["name"] == "get_equipment_maintenance_due"
    assert captured["description"] == captured["schema"]["description"]
    assert {r["toolset"] for r in registered} == {"shift_agent_read"}
    assert len(by_name) == len(registered), "duplicate tool name registered"
    assert str(PLUGIN_DIR) not in sys.path, (
        "importing the plugin must not put its own directory on sys.path"
    )


# ── authorization ──────────────────────────────────────────────────────────

@linux_only
def test_owner_succeeds(env, monkeypatch):
    _seed(env, [_item("walkin_cooler", "2026-09-01")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["ok"] is True and out["source_status"] == "populated"


def test_non_owner_refuses(env, monkeypatch):
    t = _tool(monkeypatch, EMPLOYEE)
    assert json.loads(t.handler({})) == {"ok": False, "refused": "not_owner"}


def test_dual_role_principal_is_allowed(env, monkeypatch):
    """THE B1 bug class: a principal whose roles[] include owner but whose
    scalar `role` resolves `employee` is a genuine owner and must be served."""
    t = _tool(monkeypatch, DUAL_ROLE)
    _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["ok"] is True, f"dual-role owner was refused: {out}"
    assert out["source_status"] == "missing"


def test_unbound_session_refuses(env, monkeypatch):
    t = _tool(monkeypatch, "")
    assert json.loads(t.handler({})) == {"ok": False, "refused": "unbound_session"}


def test_unresolved_identity_refuses(env, monkeypatch):
    t = _tool(monkeypatch, "19045559999@s.whatsapp.net")
    assert json.loads(t.handler({})) == {"ok": False, "refused": "identity_unresolved"}


def test_refusals_carry_no_items_key(env, monkeypatch):
    """A refusal must never be readable as an authoritative 'nothing due'."""
    for principal in ("", EMPLOYEE):
        out = json.loads(_tool(monkeypatch, principal).handler({}))
        assert out["ok"] is False
        assert "items" not in out and "in_window" not in out
        assert "tracked_total" not in out


def test_model_supplied_identity_arguments_cannot_grant_owner(env, monkeypatch):
    spoof = {"owner": True, "role": "owner", "phone": "+19045550100"}
    out = json.loads(_tool(monkeypatch, EMPLOYEE).handler(spoof))
    assert out == {"ok": False, "refused": "not_owner"}


# ── the config enable gate ─────────────────────────────────────────────────
#
# cfg.equipment_maintenance.enabled defaults False ("Default OFF (opt-in)" in
# config.yaml.template), and an absent block validates to that default. An
# un-onboarded business must hear that tracking is off, never "nothing due".


def test_disabled_agent_reports_disabled_not_nothing_due(env, monkeypatch):
    _write_cfg(env, {"enabled": False})
    _seed(env, [_item("walkin_cooler", "2026-08-12")])   # would be due
    t = _tool(monkeypatch)
    b = _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "disabled"
    assert out["coverage_status"] == "not_enabled"
    assert b.calls == [t.TPL_DISABLED]
    # No zero-shaped fields and no leak of the store that was never consulted.
    for absent in ("tracked_total", "in_window", "items", "window_days"):
        assert absent not in out, f"{absent!r} must not appear on a disabled agent"
    assert "walkin_cooler" not in json.dumps(out)


def test_disabled_agent_does_not_read_the_store(env, monkeypatch):
    """Falsifiable: the store is corrupt, so any read would surface
    state_unreadable. Getting `disabled` proves the gate returned first."""
    _write_cfg(env, {"enabled": False})
    (env / "state" / "equipment-items.json").write_text("{not json", encoding="utf-8")
    t = _tool(monkeypatch)
    _unbound(monkeypatch, t)
    assert json.loads(t.handler({}))["source_status"] == "disabled"


def test_absent_config_block_is_treated_as_disabled(env, monkeypatch):
    """The block defaults to enabled=False, so an unconfigured box is OFF."""
    _write_cfg(env, None)
    _seed(env, [_item("walkin_cooler", "2026-08-12")])
    t = _tool(monkeypatch)
    b = _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "disabled"
    assert b.calls == [t.TPL_DISABLED]


def test_bind_failure_suppresses_the_disabled_payload(env, monkeypatch):
    _write_cfg(env, {"enabled": False})
    # Seeded in-window asset: without the gate the handler would reach the
    # populated path and return ok=True, so this test fails if the gate is gone.
    _seed(env, [_item("walkin_cooler", "2026-08-12")])
    t = _tool(monkeypatch)
    _unbound(monkeypatch, t, succeed=False)
    out = json.loads(t.handler({}))
    assert out["ok"] is False
    assert out["refused"] == "outbound_truthfulness_guard_unavailable"
    assert "source_status" not in out and "coverage_status" not in out


def test_unreadable_config_fails_closed_rather_than_claiming_disabled(env, monkeypatch):
    """"Not enabled" is a claim about configuration. If the config could not be
    read, that claim is unproven — and so is any answer from the store."""
    (env / "config.yaml").write_text("customer: [unclosed\n", encoding="utf-8")
    _seed(env, [_item("walkin_cooler", "2026-08-12")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["ok"] is False
    assert out["error"] == "config_unavailable"
    for absent in ("items", "tracked_total", "in_window", "source_status"):
        assert absent not in out


@linux_only
def test_enabled_agent_reads_the_store_normally(env, monkeypatch):
    _write_cfg(env, {"enabled": True})
    _seed(env, [_item("walkin_cooler", "2026-08-12")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["source_status"] == "populated" and out["in_window"] == 1


# ── the three states ───────────────────────────────────────────────────────

def test_missing_state_is_distinct(env, monkeypatch):
    assert not (env / "state" / "equipment-items.json").exists()
    t = _tool(monkeypatch)
    _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "missing"
    assert out["coverage_status"] == "not_configured"
    # Coverage is UNKNOWN, not zero: no zero-shaped authoritative fields.
    for absent in ("tracked_total", "in_window", "items", "window_days"):
        assert absent not in out, f"{absent!r} must not appear on a missing source"


@linux_only
def test_empty_state_is_distinct(env, monkeypatch):
    _seed(env, [])
    t = _tool(monkeypatch)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "empty" and out["tracked_total"] == 0


@linux_only
def test_populated_rows_are_sorted_soonest_first(env, monkeypatch):
    _seed(env, [_item("walkin_cooler", "2026-08-25"),
                _item("hood_suppression", "2026-08-12", category="fire_safety"),
                _item("delivery_van", "2026-08-18", category="vehicle")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["source_status"] == "populated"
    assert [r["id"] for r in out["items"]] == [
        "hood_suppression", "delivery_van", "walkin_cooler"]
    assert out["items"][0]["days_until"] == 4


@linux_only
def test_overdue_assets_lead_the_list(env, monkeypatch):
    """Overdue is negative days_until, and negatives are inside every window."""
    _seed(env, [_item("walkin_cooler", "2026-08-20"),
                _item("fryer", "2026-07-01", category="cooking")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert [r["id"] for r in out["items"]] == ["fryer", "walkin_cooler"]
    assert out["items"][0]["days_until"] == -38


@linux_only
def test_window_defaults_to_thirty_days_and_narrows_on_request(env, monkeypatch):
    _seed(env, [_item("soon", "2026-08-20"), _item("later", "2026-10-15")])
    t = _tool(monkeypatch)
    default = json.loads(t.handler({}))
    assert default["window_days"] == 30 and default["in_window"] == 1
    assert json.loads(t.handler({"window_days": 120}))["in_window"] == 2
    assert json.loads(t.handler({"window_days": 9999}))["window_days"] == 365
    assert json.loads(t.handler({"window_days": 0}))["window_days"] == 1
    assert json.loads(t.handler({"window_days": "abc"}))["window_days"] == 30


@linux_only
def test_populated_with_zero_rows_in_window_is_not_empty(env, monkeypatch):
    """THE distinction: assets exist, none are due soon. Not 'nothing tracked'."""
    _seed(env, [_item("far_off", "2027-06-01")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["source_status"] == "populated"
    assert out["tracked_total"] == 1 and out["in_window"] == 0 and out["items"] == []


# ── result contract ────────────────────────────────────────────────────────

@linux_only
def test_vendor_phone_and_serial_are_withheld(env, monkeypatch):
    """Both are in the store and neither belongs in a routine 'what's due?'
    answer. The owner can ask for the vendor by name and follow up out of band."""
    _seed(env, [_item("walkin_cooler", "2026-08-20", vendor_name="Hobart Service",
                      vendor_phone="+18005551234", serial="WC-99120")])
    out = json.loads(_tool(monkeypatch).handler({}))
    row = out["items"][0]
    assert row["vendor_name"] == "Hobart Service"
    assert "vendor_phone" not in row and "serial" not in row
    assert "+18005551234" not in json.dumps(out) and "WC-99120" not in json.dumps(out)

@linux_only
def test_handler_returns_json_text_not_a_dict(env, monkeypatch):
    _seed(env, [_item("walkin_cooler", "2026-08-20")])
    res = _tool(monkeypatch).handler({})
    assert isinstance(res, str)
    assert isinstance(json.loads(res), dict)


@linux_only
def test_result_contains_no_prose(env, monkeypatch):
    """The handler owns facts; Hermes owns wording."""
    _seed(env, [_item("walkin_cooler", "2026-08-20", vendor_name="Hobart Service")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert set(out) == {"ok", "source_status", "window_days",
                        "tracked_total", "in_window", "items"}
    assert set(out["items"][0]) == {"id", "name", "category", "next_service_date",
                                    "days_until", "vendor_name", "location_id"}


# ── fail closed: never an authoritative empty ──────────────────────────────

@linux_only
def test_unreadable_state_fails_closed(env, monkeypatch):
    """A corrupt/schema-invalid store is NOT an authoritative empty result."""
    (env / "state" / "equipment-items.json").write_text(
        json.dumps({"schema_version": 1, "items": [{"id": "no_name_field"}]}),
        encoding="utf-8")
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["ok"] is False
    assert out["error"] == "state_unreadable"
    for absent in ("items", "tracked_total", "in_window", "source_status"):
        assert absent not in out, f"{absent!r} must not appear in a failure result"


@linux_only
def test_unestablishable_today_fails_closed(env, monkeypatch):
    """No silent fallback: days_until must never come from a guessed date.

    Driven through an unparseable SHIFT_AGENT_NOW_OVERRIDE rather than a bad
    timezone, because CustomerConfig.valid_tz rejects a bogus zone at config
    validation and the enable gate now surfaces that as config_unavailable
    before this path is reached.
    """
    monkeypatch.setenv("SHIFT_AGENT_NOW_OVERRIDE", "not-a-timestamp")
    _seed(env, [_item("walkin_cooler", "2026-09-01")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["ok"] is False
    assert out["error"] == "customer_timezone_unavailable"
    for absent in ("items", "tracked_total", "in_window", "source_status"):
        assert absent not in out


# ── turn-bound outbound binding (fail-closed) ──────────────────────────────
#
# Every zero state must bind its deterministic reply BEFORE returning the
# authoritative payload. There must be no execution path that emits a zero
# result whose outbound qualification was not bound.


def test_missing_binds_exact_template_before_returning(env, monkeypatch):
    t = _tool(monkeypatch)
    b = _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["ok"] is True and out["source_status"] == "missing"
    assert b.calls == [t.TPL_MISSING]


@linux_only
def test_empty_binds_exact_template(env, monkeypatch):
    _seed(env, [])
    t = _tool(monkeypatch)
    b = _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "empty"
    assert b.calls == [t.TPL_EMPTY]


@linux_only
def test_populated_zero_binds_template_with_actual_window(env, monkeypatch):
    _seed(env, [_item("far_off", "2027-06-01")])
    t = _tool(monkeypatch)
    b = _unbound(monkeypatch, t)
    out = json.loads(t.handler({"window_days": 60}))
    assert out["tracked_total"] == 1 and out["in_window"] == 0
    assert b.calls == [t.TPL_POPULATED_ZERO.format(window_days=60)]


@linux_only
def test_positive_rows_bind_nothing(env, monkeypatch):
    """Hermes keeps presentation ownership when there is something real to say."""
    _seed(env, [_item("soon", "2026-08-20")])
    t = _tool(monkeypatch)
    b = _unbound(monkeypatch, t)
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
    t = _tool(monkeypatch)
    _unbound(monkeypatch, t, succeed=False)
    out = json.loads(t.handler({}))
    assert out["ok"] is False
    assert out["refused"] == "outbound_truthfulness_guard_unavailable"
    for absent in ("tracked_total", "in_window", "items",
                   "window_days", "source_status", "coverage_status"):
        assert absent not in out, f"{absent!r} leaked on a guard-unavailable refusal"


# ── description pins ───────────────────────────────────────────────────────

def test_description_carries_the_scope_rules(env):
    """These sentences are what keep a tracked-list read from being reported as
    a statement about the equipment itself; an edit must not drop them silently."""
    d = _tool().DESCRIPTION
    assert "TRACKED" in d
    assert "This is NOT a statement that nothing is due" in d
    assert "does NOT establish that nothing needs service" in d
    assert "NOT that the equipment is up to date" in d
    assert "Do not generalize beyond the tracked list" in d
    assert "OMIT window_days" in d and "30-day default" in d


def test_description_carries_the_hard_rules(env):
    """#680's Hard Rules, migrated from the SKILL that is not being carried over.
    Nothing else enforces them once the SKILL body is gone."""
    d = _tool().DESCRIPTION
    assert "never invent an asset or a service date" in d
    assert "Never contact a vendor" in d
    assert "not a code violation" in d
    assert "READ-ONLY" in d and "not wired up yet" in d


def test_window_days_description_pins_explicit_timeframe_only(env):
    w = _tool().SCHEMA["parameters"]["properties"]["window_days"]["description"]
    assert "ONLY" in w and "explicit" in w and "omit" in w.lower()
