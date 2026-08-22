"""Agent #3 — `find_nearest_location`, the plugin-tool reachability path.

The old `customer_location_query` SKILL is unreachable: it needs `skill_view`
(the `skills` toolset) and inline `jq` / `log-decision-direct` (the `terminal`
toolset), both globally disabled on the gateway. This tool exposes the same
deterministic kernel through the live `shift_agent_read` toolset instead.

Two properties carry the safety weight and are pinned hardest here:

  * a WRONG address, phone or set of hours reaching a customer is HIGH under the
    multi-location directive, so the successful reply is deterministic — the
    model's wording cannot alter it;
  * `not_configured` and `no_usable_locations` share customer wording (the
    customer cannot act on the difference) but MUST stay distinct in the
    structured result, because the operator can.

The kernel subprocess is stubbed by exit code + stdout. Its real behaviour is
already covered by tests/test_agent_3_multi_location.py, and re-running geocoding
in CI would test Nominatim, not us. The Hermes adapter harness is NOT recreated
here — that layer was proven in the Wave-1 A1–A4 runtime proofs.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "shift-agent-read"
PLATFORM_DIR = REPO / "src" / "platform"

linux_only = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="binding goes through safe_io, which imports fcntl (Linux only)",
)

PKG = "shift_agent_read_loc_pkg"


def _install_session_stub(bound: bool = True) -> None:
    mod = types.ModuleType("gateway.session_context")
    values = ({"HERMES_SESSION_ID": "sess-loc",
               "HERMES_SESSION_MESSAGE_ID": "msg-loc",
               "HERMES_SESSION_CHAT_ID": "15555550123@s.whatsapp.net"}
              if bound else {})
    mod.get_session_env = lambda name, default="": values.get(name, default)
    pkg = sys.modules.get("gateway") or types.ModuleType("gateway")
    pkg.session_context = mod
    sys.modules["gateway"] = pkg
    sys.modules["gateway.session_context"] = mod


def _load(bound: bool = True):
    """Import the plugin as a package so relative imports resolve."""
    import importlib.util
    _install_session_stub(bound)
    for name in list(sys.modules):
        if name == PKG or name.startswith(PKG + "."):
            del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        PKG, PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.syspath_prepend(str(PLATFORM_DIR))
    pkg = _load()
    try:
        import safe_io
        safe_io._turn_overrides.clear()
    except Exception:
        pass
    return pkg.location_tool


RANKED_STDOUT = json.dumps({
    "source": "haversine_fallback",
    "results": [
        {"location_id": "loc_dal_01", "name": "Dallas", "address_short": "Dallas, TX",
         "phone": "+12145551000", "hours": "9am-9pm", "drive_minutes": 12.4,
         "distance_km": 4.8},
        {"location_id": "loc_jax_01", "name": "Jacksonville",
         "address_short": "Jacksonville, FL", "phone": "+19045551000",
         "hours": "10am-8pm", "drive_minutes": 51.0, "distance_km": 19.6},
    ],
    "customer_input": {"address_provided": True},
    "n_locations_total": 9, "n_returned": 2, "errors": [],
})


def _stub_kernel(monkeypatch, tool, rc, stdout=""):
    calls = {}

    def fake_run(argv, **kw):
        calls["argv"] = argv
        calls["kwargs"] = kw
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr="")

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    return calls


def _bind_spy(monkeypatch, tool, succeed=True):
    calls = []
    monkeypatch.setattr(tool, "_bind_outbound",
                        lambda text: (calls.append(text), succeed)[1])
    return calls


def _audit_spy(monkeypatch, tool):
    calls = []
    monkeypatch.setattr(tool, "_audit",
                        lambda source, results, detail="":
                        calls.append({"source": source, "n": len(results),
                                      "detail": detail}))
    return calls


# ── tool contract ──────────────────────────────────────────────────────────

def test_schema_is_the_inner_function_object(tool):
    assert set(tool.SCHEMA) == {"name", "description", "parameters"}
    assert "type" not in tool.SCHEMA and "function" not in tool.SCHEMA
    assert tool.SCHEMA["name"] == "find_nearest_location"
    assert tool.TOOLSET == "shift_agent_read"


def test_description_guides_top_n_and_forbids_guessing_a_location(tool):
    d = tool.DESCRIPTION
    assert "closest" in d.lower() and "nearest" in d.lower()
    assert "do not guess" in d.lower()
    assert "1 when the customer asks for THE closest" in d


def test_arguments_are_bounded_and_identity_free(tool):
    props = tool.SCHEMA["parameters"]["properties"]
    assert set(props) == {"address", "top_n"}
    assert props["address"]["minLength"] == 1
    assert props["address"]["maxLength"] == 300
    assert props["top_n"]["minimum"] == 1 and props["top_n"]["maximum"] == 5
    assert tool.SCHEMA["parameters"]["required"] == ["address"]
    forbidden = {"owner", "role", "phone", "user", "user_id", "sender",
                 "identity", "chat_id", "source", "location_id"}
    assert not (set(props) & forbidden)


# ── argument validation: reject, never silently clamp ──────────────────────

@pytest.mark.parametrize("bad", [
    {"address": ""},
    {"address": "   "},
    {"address": "x" * 301},
    {},
    {"address": "75001", "top_n": 0},
    {"address": "75001", "top_n": 6},
    {"address": "75001", "top_n": "many"},
])
def test_invalid_arguments_are_rejected(tool, monkeypatch, bad):
    """A hidden clamp would answer a question the customer never asked."""
    ran = _stub_kernel(monkeypatch, tool, 0, RANKED_STDOUT)
    out = json.loads(tool.handler(bad))
    assert out == {"ok": False, "refused": "invalid_arguments"}
    assert "argv" not in ran, "kernel must not run on invalid arguments"


@linux_only
@pytest.mark.parametrize("n", [1, 5])
def test_top_n_bounds_are_accepted(tool, monkeypatch, n):
    calls = _stub_kernel(monkeypatch, tool, 0, RANKED_STDOUT)
    _bind_spy(monkeypatch, tool); _audit_spy(monkeypatch, tool)
    assert json.loads(tool.handler({"address": "75001", "top_n": n}))["ok"] is True
    assert calls["argv"][-1] == str(n)


@linux_only
def test_kernel_invoked_by_exact_argv_never_a_shell(tool, monkeypatch):
    calls = _stub_kernel(monkeypatch, tool, 0, RANKED_STDOUT)
    _bind_spy(monkeypatch, tool); _audit_spy(monkeypatch, tool)
    tool.handler({"address": "Cary, NC"})
    argv = calls["argv"]
    assert isinstance(argv, list)
    assert "--address" in argv and "Cary, NC" in argv
    assert calls["kwargs"].get("shell") in (None, False)


# ── ranked ─────────────────────────────────────────────────────────────────

@linux_only
def test_ranked_returns_verbatim_facts_and_preserves_source(tool, monkeypatch):
    _stub_kernel(monkeypatch, tool, 0, RANKED_STDOUT)
    _bind_spy(monkeypatch, tool); _audit_spy(monkeypatch, tool)
    out = json.loads(tool.handler({"address": "75001"}))
    assert out["status"] == "ranked"
    assert out["source"] == "haversine_fallback"
    assert out["estimate"] is True
    assert out["n_locations_total"] == 9 and out["n_returned"] == 2
    first = out["locations"][0]
    assert first["address_short"] == "Dallas, TX"
    assert first["phone"] == "+12145551000"
    assert first["hours"] == "9am-9pm"


@linux_only
def test_fallback_wording_does_not_imply_live_driving_routes(tool, monkeypatch):
    """The kernel's osrm_distance() returns None unconditionally, so calling
    these 'driving times' would be a factual claim we cannot support."""
    _stub_kernel(monkeypatch, tool, 0, RANKED_STDOUT)
    bound = _bind_spy(monkeypatch, tool); _audit_spy(monkeypatch, tool)
    tool.handler({"address": "75001"})
    text = bound[0]
    assert "not live driving routes" in text
    assert "approx." in text
    assert "driving time" not in text.lower()
    for fact in ("Dallas, TX", "+12145551000", "9am-9pm"):
        assert fact in text
    assert text.rstrip().endswith("Anything else?")


@linux_only
def test_osrm_source_switches_to_live_routing_wording(tool, monkeypatch):
    """Phrasing keys off the returned source, not a hard-coded assumption."""
    payload = json.loads(RANKED_STDOUT); payload["source"] = "osrm"
    _stub_kernel(monkeypatch, tool, 0, json.dumps(payload))
    bound = _bind_spy(monkeypatch, tool); _audit_spy(monkeypatch, tool)
    out = json.loads(tool.handler({"address": "75001"}))
    assert out["estimate"] is False and out["source"] == "osrm"
    assert "not live driving routes" not in bound[0]
    assert "min drive" in bound[0]


# ── the three failure states stay distinct ─────────────────────────────────

@linux_only
def test_not_configured_is_distinct_and_audited_as_such(tool, monkeypatch):
    _stub_kernel(monkeypatch, tool, 2, json.dumps({"source": "not_configured"}))
    bound = _bind_spy(monkeypatch, tool); audits = _audit_spy(monkeypatch, tool)
    out = json.loads(tool.handler({"address": "75001"}))
    assert out["status"] == "not_configured"
    assert bound == [tool.TPL_UNAVAILABLE]
    assert audits == [{"source": "not_configured", "n": 0, "detail": ""}]


@linux_only
def test_input_unresolved_is_distinct_and_never_audited_as_not_configured(
        tool, monkeypatch):
    """Fabricating a `source` to satisfy the schema would put a false
    operational fact in the durable log."""
    _stub_kernel(monkeypatch, tool, 1, "")
    bound = _bind_spy(monkeypatch, tool); audits = _audit_spy(monkeypatch, tool)
    out = json.loads(tool.handler({"address": "zzzz"}))
    assert out["status"] == "input_unresolved"
    assert bound == [tool.TPL_INPUT_UNRESOLVED]
    assert audits == [], "an unresolved input must not be audited at all"


@linux_only
def test_no_usable_locations_is_distinct_and_preserves_kernel_source(
        tool, monkeypatch):
    _stub_kernel(monkeypatch, tool, 3, json.dumps(
        {"source": "haversine_fallback", "results": [], "n_locations_total": 4}))
    bound = _bind_spy(monkeypatch, tool); audits = _audit_spy(monkeypatch, tool)
    out = json.loads(tool.handler({"address": "75001"}))
    assert out["status"] == "no_usable_locations"
    assert out["source"] == "haversine_fallback"
    assert bound == [tool.TPL_UNAVAILABLE]
    assert audits[0]["source"] == "haversine_fallback" and audits[0]["n"] == 0


@linux_only
def test_shared_wording_does_not_collapse_the_structured_states(tool, monkeypatch):
    """Same customer sentence, different operator meaning."""
    _stub_kernel(monkeypatch, tool, 2, json.dumps({"source": "not_configured"}))
    _bind_spy(monkeypatch, tool); _audit_spy(monkeypatch, tool)
    a = json.loads(tool.handler({"address": "75001"}))["status"]
    _stub_kernel(monkeypatch, tool, 3, json.dumps({"source": "haversine_fallback",
                                                   "results": []}))
    _bind_spy(monkeypatch, tool); _audit_spy(monkeypatch, tool)
    b = json.loads(tool.handler({"address": "75001"}))["status"]
    assert a == "not_configured" and b == "no_usable_locations" and a != b


@linux_only
def test_kernel_timeout_becomes_no_usable_locations_not_an_empty_list(
        tool, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="closest-location.py", timeout=30)
    monkeypatch.setattr(tool.subprocess, "run", boom)
    bound = _bind_spy(monkeypatch, tool); _audit_spy(monkeypatch, tool)
    out = json.loads(tool.handler({"address": "75001"}))
    assert out["status"] == "no_usable_locations"
    assert bound == [tool.TPL_UNAVAILABLE]


# ── outbound guard ─────────────────────────────────────────────────────────

@linux_only
@pytest.mark.parametrize("rc,stdout", [
    (0, RANKED_STDOUT),
    (2, json.dumps({"source": "not_configured"})),
    (1, ""),
    (3, json.dumps({"source": "haversine_fallback", "results": []})),
])
def test_bind_failure_suppresses_every_store_fact(tool, monkeypatch, rc, stdout):
    _stub_kernel(monkeypatch, tool, rc, stdout)
    _bind_spy(monkeypatch, tool, succeed=False); _audit_spy(monkeypatch, tool)
    out = json.loads(tool.handler({"address": "75001"}))
    assert out == {"ok": False, "refused": "outbound_truthfulness_guard_unavailable"}
    for absent in ("locations", "status", "source", "n_returned",
                   "n_locations_total", "estimate"):
        assert absent not in out


@linux_only
def test_model_wording_cannot_alter_the_deterministic_store_reply(tool, monkeypatch):
    """Compound-question case: whatever the model composes, egress is the bound
    text. The delivery half of 'closest store and do you deliver?' is dropped —
    accepted deliberately; a dropped follow-up is recoverable, a wrong address
    is not."""
    import safe_io
    _stub_kernel(monkeypatch, tool, 0, RANKED_STDOUT)
    _audit_spy(monkeypatch, tool)
    assert json.loads(tool.handler({"address": "75001"}))["ok"] is True
    bound = safe_io.lookup_turn_outbound_override()
    assert bound is not None and "Dallas, TX" in bound

    monkeypatch.setattr(safe_io, "front_brain_outbound_enforce_enabled",
                        lambda jid: False)
    monkeypatch.setattr(safe_io, "_agent_disabled", lambda: False)
    monkeypatch.setattr(safe_io, "_automation_control_suppressed_mode",
                        lambda jid: None)
    for composed in ("Our Austin store at 99 Fake St is closest!",
                     "Yes we deliver everywhere.", ""):
        assert safe_io.front_brain_screen_gateway_send(
            "15555550123@s.whatsapp.net", composed) == bound


@linux_only
def test_unconstrained_unrelated_turn_still_passes_through_unchanged(
        tool, monkeypatch):
    import safe_io
    _install_session_stub(True)
    safe_io._turn_overrides.clear()          # no location lookup this turn
    monkeypatch.setattr(safe_io, "front_brain_outbound_enforce_enabled",
                        lambda jid: False)
    monkeypatch.setattr(safe_io, "_agent_disabled", lambda: False)
    monkeypatch.setattr(safe_io, "_automation_control_suppressed_mode",
                        lambda jid: None)
    original = "Sure — we open at 9am tomorrow."
    assert safe_io.front_brain_screen_gateway_send(
        "15555550123@s.whatsapp.net", original) == original


# ── public access + isolation ──────────────────────────────────────────────

@linux_only
def test_public_caller_succeeds_without_owner_authorization(tool, monkeypatch):
    """Store locations are published information; no owner gate, and
    identify-sender must never be consulted."""
    called = []
    monkeypatch.setattr(tool.subprocess, "run",
                        lambda argv, **k: (called.append(argv),
                                           types.SimpleNamespace(
                                               returncode=0, stdout=RANKED_STDOUT,
                                               stderr=""))[1])
    _bind_spy(monkeypatch, tool); _audit_spy(monkeypatch, tool)
    out = json.loads(tool.handler({"address": "75001"}))
    assert out["ok"] is True and out["status"] == "ranked"
    assert not any("identify-sender" in str(a) for a in called)


def test_tool_module_never_calls_identify_sender_or_require_owner():
    """Checks EXECUTABLE code, not prose: the module docstring explains why
    identify-sender is deliberately absent, and a naive substring match would
    fail on that explanation."""
    import ast
    src = (PLUGIN_DIR / "location_tool.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names, strings = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value)
    assert "require_owner" not in names
    # Docstrings are expressions, so exclude them before scanning literals.
    # clean=False so the raw literal matches the Constant node exactly.
    docstrings = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))}
    live = [s for s in strings if s not in docstrings]
    assert not any("identify-sender" in s for s in live)


def test_compliance_tool_remains_owner_only(tool):
    """The public tool must not have relaxed the owner gate next door."""
    src = (PLUGIN_DIR / "compliance_tool.py").read_text(encoding="utf-8")
    assert "require_owner()" in src


@linux_only
def test_result_exposes_no_roster_schedule_or_customer_data(tool, monkeypatch):
    _stub_kernel(monkeypatch, tool, 0, RANKED_STDOUT)
    _bind_spy(monkeypatch, tool); _audit_spy(monkeypatch, tool)
    out = json.loads(tool.handler({"address": "75001"}))
    blob = json.dumps(out).lower()
    for leaked in ("employee", "roster", "schedule", "pending", "proposal",
                   "lead", "catering", "shift"):
        assert leaked not in blob
    assert set(out["locations"][0]) == {
        "location_id", "name", "address_short", "phone", "hours",
        "drive_minutes", "distance_km"}


# ── privacy ────────────────────────────────────────────────────────────────

@linux_only
def test_raw_address_never_reaches_the_result_or_the_audit(tool, monkeypatch):
    secret = "742 Evergreen Terrace, Springfield"
    _stub_kernel(monkeypatch, tool, 0, RANKED_STDOUT)
    bound = _bind_spy(monkeypatch, tool)
    audits = _audit_spy(monkeypatch, tool)
    out = tool.handler({"address": secret})
    assert secret not in out
    assert secret not in json.dumps(audits)
    assert secret not in "\n".join(bound)


@linux_only
def test_audit_row_carries_no_customer_coordinates(tool, monkeypatch):
    """The variant's customer_lat/lon stay unset: this path geocodes from address
    text, and the schema docstring is explicit that geocoded coordinates are not
    persisted."""
    import safe_io  # noqa: F401  (ensures platform path is primed)
    _stub_kernel(monkeypatch, tool, 0, RANKED_STDOUT)
    _bind_spy(monkeypatch, tool)
    captured = {}
    monkeypatch.setattr(tool, "_audit",
                        lambda source, results, detail="":
                        captured.update({"source": source, "results": results}))
    tool.handler({"address": "75001"})
    assert "customer_lat" not in captured and "customer_lon" not in captured


@linux_only
def test_audit_payload_validates_as_the_existing_log_entry_variant(tool, monkeypatch):
    """No schemas.py change: the deployed variant already fits."""
    from pydantic import TypeAdapter
    from schemas import LogEntry, MultiLocationClosestLookup
    row = {
        "ts": "2026-08-09T02:00:00Z",
        "type": "multi_location_closest_lookup",
        "chat_id": "15555550123@s.whatsapp.net",
        "nearest_location_id": "loc_dal_01",
        "nearest_drive_minutes": 12.4,
        "n_locations_returned": 2,
        "source": "haversine_fallback",
        "detail": "",
    }
    parsed = TypeAdapter(LogEntry).validate_python(row)
    assert isinstance(parsed, MultiLocationClosestLookup)


# ── registration ───────────────────────────────────────────────────────────

def test_plugin_registers_every_tool_under_shift_agent_read(monkeypatch):
    monkeypatch.syspath_prepend(str(PLATFORM_DIR))
    pkg = _load()
    registered = []

    class FakeCtx:
        def register_tool(self, **kw):
            registered.append(kw)

    pkg.register(FakeCtx())
    names = {r["name"] for r in registered}
    # Exhaustive on purpose: a stray or duplicate registration is exactly what
    # this catches. Update the set when a tool lands, do not loosen it.
    assert names == {"get_compliance_deadlines", "find_nearest_location",
                     "get_equipment_maintenance_due",
                     "get_pending_catering_approvals", "get_catering_menu_items"}
    assert {r["toolset"] for r in registered} == {"shift_agent_read"}
    for r in registered:
        assert r["description"] == r["schema"]["description"]
    assert str(PLUGIN_DIR) not in sys.path


def test_plugin_metadata_no_longer_claims_owner_only():
    """It stopped being true when a public tool landed here."""
    y = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    assert "find_nearest_location" in y
    assert "Owner Reads" not in y
    assert "own authorization policy" in y
