"""shift-agent-read-preflight — the read tools must prove they registered.

`hermes_cli.plugins._load_plugin` swallows every exception a plugin's
`register()` raises, so a plugin that fails to load is silent. The 2026-08-22
reachability audit found the consequence: five tools shipped on registration
inferred from source, and the gateway log carries nothing from the gateway
process at all — no plugin-load line, no tool name, ever. A plugin that stopped
registering would be invisible until someone asked why the owner got no answer.

Two properties this file exists to hold:

* **The check is real.** Both drift directions fail — a declared tool that never
  registered, and a registered tool nobody declared. And registration alone is
  not enough: a toolset that is disabled by name, or absent from the platform's
  loadout, is unreachable no matter how correctly its tools registered.
* **The check never blocks the boot.** These are READ tools. Refusing to start
  the gateway over them would trade a capability outage for a total messaging
  outage on a live customer's WhatsApp. Fail-closed is right for authority
  (`shift-agent-policy-preflight` does refuse); fail-loud is right for
  capability. The exit code is pinned below, and so is the `-` prefix in the
  unit that makes the property structural rather than a promise.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PREFLIGHT = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-read-preflight"
GATEWAY_UNIT = REPO / "src" / "platform" / "systemd" / "hermes-gateway.service"
PLUGIN_YAML = REPO / "src" / "plugins" / "shift-agent-read" / "plugin.yaml"
PLUGIN_INIT = REPO / "src" / "plugins" / "shift-agent-read" / "__init__.py"
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"


def _load():
    """Import the preflight as a module. Safe on any OS: module scope only sets
    constants and inserts a path, and every Hermes import is inside a function."""
    loader = importlib.machinery.SourceFileLoader("shift_agent_read_preflight",
                                                  str(PREFLIGHT))
    spec = importlib.util.spec_from_file_location(
        "shift_agent_read_preflight", str(PREFLIGHT), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["shift_agent_read_preflight"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def pf():
    return _load()


def _mapping(d):
    """toolset_of from a dict."""
    return lambda name: d.get(name)


def _in_toolset(d):
    """tools_in_toolset derived from the same dict, so the fixture is consistent."""
    return lambda ts: [n for n, t in d.items() if t == ts]


# ── check C: declared vs registered, both directions ─────────────────────

def test_all_declared_tools_registered_is_clean(pf):
    reg = {"a": "shift_agent_read", "b": "shift_agent_read"}
    toolset, problems = pf.evaluate_tools(["a", "b"], _mapping(reg), _in_toolset(reg))
    assert toolset == "shift_agent_read"
    assert problems == []


def test_a_declared_tool_that_never_registered_fails(pf):
    """The live failure mode: register() raised and Hermes swallowed it."""
    reg = {"a": "shift_agent_read"}
    toolset, problems = pf.evaluate_tools(["a", "b"], _mapping(reg), _in_toolset(reg))
    assert any(code == "C" and "NOT registered" in msg and "b" in msg
               for code, msg in problems), problems
    assert toolset == "shift_agent_read"  # still resolvable from the tool that did


def test_a_registered_tool_nobody_declared_fails(pf):
    """The other drift direction — plugin.yaml fell behind the code."""
    reg = {"a": "shift_agent_read", "sneaky": "shift_agent_read"}
    toolset, problems = pf.evaluate_tools(["a"], _mapping(reg), _in_toolset(reg))
    assert any(code == "C" and "UNDECLARED" in msg and "sneaky" in msg
               for code, msg in problems), problems


def test_tools_split_across_toolsets_fails_and_resolves_no_toolset(pf):
    """A split has no single answer to hand to the reachability check."""
    reg = {"a": "shift_agent_read", "b": "something_else"}
    toolset, problems = pf.evaluate_tools(["a", "b"], _mapping(reg), _in_toolset(reg))
    assert toolset is None
    assert any("split across toolsets" in msg for _, msg in problems), problems


def test_empty_declared_list_is_a_failure_not_a_pass(pf):
    """A plugin.yaml that lost its declarations must not pass vacuously.

    Without this, every other check succeeds over an empty set and the preflight
    reports OK while zero tools exist.
    """
    toolset, problems = pf.evaluate_tools([], _mapping({}), _in_toolset({}))
    assert toolset is None
    assert problems, "an empty declaration set must never be clean"


def test_nothing_registered_at_all_fails(pf):
    """Whole-plugin load failure — the case this preflight was written for."""
    toolset, problems = pf.evaluate_tools(["a", "b"], _mapping({}), _in_toolset({}))
    assert toolset is None
    assert any("NOT registered" in msg for _, msg in problems), problems


# ── check D: registration is not reach ───────────────────────────────────

def test_reachable_toolset_is_clean(pf):
    assert pf.evaluate_reachability(
        "shift_agent_read", disabled=["skills", "terminal"],
        platform_sets=["hermes-whatsapp", "shift_agent_read"]) == []


def test_disabled_toolset_fails_even_though_tools_registered(pf):
    """`agent.disabled_toolsets` suppresses by NAME and is applied last."""
    problems = pf.evaluate_reachability(
        "shift_agent_read", disabled=["skills", "shift_agent_read"],
        platform_sets=["hermes-whatsapp", "shift_agent_read"])
    assert any(code == "D" and "disabled_toolsets" in msg for code, msg in problems)


def test_toolset_missing_from_the_platform_loadout_fails(pf):
    """Registered, not disabled, and still never offered to WhatsApp."""
    problems = pf.evaluate_reachability(
        "shift_agent_read", disabled=[], platform_sets=["hermes-whatsapp"])
    assert any(code == "D" and "platform_toolsets" in msg for code, msg in problems)


# ── the non-blocking property ────────────────────────────────────────────

def test_main_returns_zero_even_when_every_check_fails(pf, monkeypatch):
    """The whole point. A read-capability outage must never become a messaging
    outage, so main() returns 0 on total failure."""
    recorded, alerted = [], []
    monkeypatch.setattr(pf, "_record", lambda **kw: recorded.append(kw))
    monkeypatch.setattr(pf, "_alert", lambda msg: alerted.append(msg))
    # No Hermes on the test runner, so discovery raises and every check fails.
    assert pf.main() == 0
    assert pf.FAILURES, "expected failures with no Hermes present"
    assert recorded, "a failure must be recorded to the audit chokepoint"
    assert alerted, "a failure must alert the owner (12b)"


def test_failure_is_recorded_to_the_audit_chokepoint_not_only_stderr(pf, monkeypatch):
    """stderr here lands in a gateway log nothing reads — recording the finding
    there would reproduce the invisibility this preflight exists to end."""
    recorded = []
    monkeypatch.setattr(pf, "_record", lambda **kw: recorded.append(kw))
    monkeypatch.setattr(pf, "_alert", lambda msg: None)
    pf.main()
    assert len(recorded) == 1
    assert recorded[0]["check"] == "shift-agent-read-preflight"
    assert recorded[0]["detail"], "the detail must name what failed"


def test_the_owner_alert_carries_no_markdown_hazard(pf, monkeypatch):
    """Underscored names render as garbage under Markdown parsing and the owner
    stops recognising the message as an alert (the 12b house rule)."""
    alerted = []
    monkeypatch.setattr(pf, "_record", lambda **kw: None)
    monkeypatch.setattr(pf, "_alert", lambda msg: alerted.append(msg))
    pf.main()
    body = alerted[0]
    assert "_" not in body, f"underscore in alert body would be mangled: {body!r}"
    assert "*" not in body


# ── the wiring, which is where this kind of change usually dies ──────────

def test_the_unit_wires_the_preflight_with_a_leading_dash(pf):
    """The `-` makes the non-blocking property structural: systemd ignores the
    exit status, so even an unhandled traceback cannot stop the gateway."""
    unit = GATEWAY_UNIT.read_text(encoding="utf-8")
    assert "ExecStartPre=-/usr/local/bin/shift-agent-read-preflight" in unit, (
        "the read preflight must be wired with a leading '-' so it can never "
        "block gateway startup")


def test_the_policy_preflight_is_still_fail_closed(pf):
    """Negative control for the asymmetry. If someone ever 'harmonises' the two
    by adding a dash to the policy gate, screening stops being enforced."""
    policy = (REPO / "src" / "agents" / "shift" / "scripts"
              / "shift-agent-policy-preflight").read_text(encoding="utf-8")
    assert "sys.exit(1)" in policy, (
        "shift-agent-policy-preflight must still refuse to start the gateway — "
        "it gates a safety control, not a capability")


def test_the_deploy_installs_the_preflight(pf):
    """A check that never installs is the defect class this whole audit found.
    The shift scripts glob covers it; this fails if that glob is ever narrowed."""
    deploy = DEPLOY.read_text(encoding="utf-8")
    assert "install -m 755 src/agents/shift/scripts/* /usr/local/bin/" in deploy


def test_the_preflight_is_executable_and_has_the_hermes_shebang(pf):
    """It must run under the Hermes venv — the plugin manager is not importable
    from the system python."""
    first = PREFLIGHT.read_text(encoding="utf-8").splitlines()[0]
    assert first == "#!/root/.hermes/hermes-agent/venv/bin/python", first


# ── the derivation must not rot ──────────────────────────────────────────

def test_expected_tools_are_not_hardcoded_in_the_preflight(pf):
    """The list is derived from the manifest Hermes parsed. A literal here would
    be wrong the day a sixth tool lands, and would only prove this file agrees
    with itself."""
    src = PREFLIGHT.read_text(encoding="utf-8")
    for name in ("get_compliance_deadlines", "get_roster_capabilities",
                 "find_nearest_location", "get_pending_catering_approvals",
                 "get_equipment_maintenance_due"):
        assert name not in src, (
            f"{name} is hardcoded in the preflight; derive it from "
            f"manifest.provides_tools instead")


def test_plugin_yaml_declares_exactly_what_register_iterates():
    """The two sources the preflight compares at runtime must already agree in
    the repo, or the check ships red."""
    import re
    yaml_txt = PLUGIN_YAML.read_text(encoding="utf-8")
    declared = set(re.findall(r"^\s+-\s+(\w+)$", yaml_txt, re.M))

    init = PLUGIN_INIT.read_text(encoding="utf-8")
    # Only the tuple register() actually iterates — not every *_tool token in
    # the file, which would sweep in `register_tool` and prose from the docstring.
    tup = re.search(r"for tool in \(([^)]*)\)", init, re.S)
    assert tup, "register() no longer iterates a tuple of tool modules"
    modules = set(re.findall(r"(\w+_tool)", tup.group(1)))

    assert declared, "plugin.yaml declares no tools"
    assert len(declared) == len(modules), (
        f"plugin.yaml declares {len(declared)} tools {sorted(declared)} but "
        f"register() iterates {len(modules)} modules {sorted(modules)}")
