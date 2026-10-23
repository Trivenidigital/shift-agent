"""Fleet conformance: structural invariants every agent under src/agents/* must hold.

Parametrized over the agent directories discovered at collection time (18 on
2026-10-23), so a new agent is covered without editing this file:

  a. every ``skills/*/SKILL.md`` opens with YAML frontmatter that parses and
     carries ``name`` + ``description`` (same glob skills_manifest ships);
  b. ``schemas.Config`` has a config block for the agent (explicit, reasoned
     exceptions in ``_CONFIG_FIELD_EXCEPTIONS``);
  c. every audit ``type`` literal an agent script writes is a registered
     LogEntry type — an unregistered tag silently degrades to
     ``_UnknownLogEntry`` on replay;
  d. every literal ``reason`` passed to cf-router ``audit_intercepted(`` is a
     member of ``CfRouterIntercepted.reason`` — anything else raises inside the
     best-effort emitter and the row is lost.

schemas.py imports no fcntl, so this runs cross-platform.
"""
from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest
import yaml

from fleet_harness import intercepted_reason_literals
from schemas import Config, CfRouterIntercepted, _KNOWN_LOG_ENTRY_TYPES

REPO = Path(__file__).resolve().parent.parent
AGENTS_ROOT = REPO / "src" / "agents"
CF_ROUTER = REPO / "src" / "plugins" / "cf-router"

AGENT_DIRS = sorted(
    p.name for p in AGENTS_ROOT.iterdir()
    if p.is_dir() and not p.name.startswith(("_", "."))
)

# Agent dir -> Config field(s) that carry its configuration, where the dir name
# is not itself a Config field. Every entry needs a reason.
_CONFIG_FIELD_EXCEPTIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "eod_reconcile": (("eod",), "EOD reconcile agent's block is named `eod` (EodConfig)."),
    "shift": (
        ("owner", "limits", "operations"),
        "Shift is the base agent: it reads the top-level owner/limits/operations "
        "blocks rather than a dedicated sub-block.",
    ),
}

# `"type": "<x>"` / `type="<x>"` / `--type <x>` literals in agent scripts that
# are NOT decisions.log rows. Every entry needs a reason.
_NON_AUDIT_TYPE_LITERALS: dict[str, str] = {
    "text": "OpenRouter/OpenAI chat content-part type (vision prompts)",
    "image_url": "OpenRouter/OpenAI chat content-part type (vision prompts)",
    "json_object": "OpenRouter/OpenAI response_format type",
    "notify": "Baileys bridge event type inside a bridge-log regex",
}

# type= requires a QUOTED value so argparse `type=int` is never matched.
_TYPE_LITERAL = re.compile(
    r"""(?:["']type["']\s*:\s*["']|\btype\s*=\s*["']|--type[ =]+["']?)([a-z][a-z0-9_]*)"""
)


def test_agent_discovery_is_not_empty():
    assert len(AGENT_DIRS) >= 18, AGENT_DIRS


# ── a. SKILL.md frontmatter ─────────────────────────────────────────────────
def _frontmatter(text: str) -> dict:
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "SKILL.md must open with '---'"
    end = next((i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---"), None)
    assert end is not None, "SKILL.md frontmatter has no closing '---'"
    data = yaml.safe_load("\n".join(lines[1:end]))
    assert isinstance(data, dict), f"frontmatter is not a mapping: {data!r}"
    return data


@pytest.mark.parametrize("agent", AGENT_DIRS)
def test_skill_frontmatter_has_name_and_description(agent):
    problems = []
    for skill_md in sorted((AGENTS_ROOT / agent).glob("skills/*/SKILL.md")):
        try:
            fm = _frontmatter(skill_md.read_text(encoding="utf-8"))
        except (AssertionError, yaml.YAMLError) as exc:
            problems.append(f"{skill_md.relative_to(REPO)}: {exc}")
            continue
        for key in ("name", "description"):
            if not (isinstance(fm.get(key), str) and fm[key].strip()):
                problems.append(f"{skill_md.relative_to(REPO)}: missing/empty `{key}`")
    assert not problems, "\n".join(problems)


# ── b. Config block per agent ───────────────────────────────────────────────
def test_config_exceptions_only_name_real_agents():
    stale = sorted(set(_CONFIG_FIELD_EXCEPTIONS) - set(AGENT_DIRS))
    assert not stale, f"_CONFIG_FIELD_EXCEPTIONS names agents that no longer exist: {stale}"


@pytest.mark.parametrize("agent", AGENT_DIRS)
def test_config_has_field_for_agent(agent):
    fields, _reason = _CONFIG_FIELD_EXCEPTIONS.get(agent, ((agent,), ""))
    missing = [f for f in fields if f not in Config.model_fields]
    assert not missing, (
        f"schemas.Config has no field {missing} for agent dir {agent!r}; add the "
        f"block or a reasoned entry in _CONFIG_FIELD_EXCEPTIONS"
    )


# ── c. audit type literals in agent scripts ────────────────────────────────
def unknown_audit_types(text: str) -> list[tuple[int, str]]:
    """(lineno, literal) for every type literal not registered in LogEntry."""
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in _TYPE_LITERAL.finditer(line):
            lit = m.group(1)
            if lit not in _KNOWN_LOG_ENTRY_TYPES and lit not in _NON_AUDIT_TYPE_LITERALS:
                out.append((lineno, lit))
    return out


def test_type_scan_catches_unregistered_literal():
    """The scan is a real guard: an injected bogus tag in each shape is flagged."""
    sample = (
        'entry = X(type="fleet_mutant_one")\n'
        'row = {"type": "fleet_mutant_two"}\n'
        'log-decision-direct --type fleet_mutant_three\n'
        'ap.add_argument("--n", type=int)\n'
        'ok = X(type="raw_inbound")\n'
    )
    assert [lit for _, lit in unknown_audit_types(sample)] == [
        "fleet_mutant_one", "fleet_mutant_two", "fleet_mutant_three",
    ]


def test_non_audit_allowlist_does_not_shadow_real_types():
    clash = sorted(set(_NON_AUDIT_TYPE_LITERALS) & _KNOWN_LOG_ENTRY_TYPES)
    assert not clash, f"allowlisted non-audit literals are real LogEntry types: {clash}"


@pytest.mark.parametrize("agent", AGENT_DIRS)
def test_script_audit_types_are_registered(agent):
    bad = []
    for script in sorted((AGENTS_ROOT / agent).glob("scripts/*")):
        if not script.is_file():
            continue
        try:
            text = script.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary asset
        bad += [f"{script.relative_to(REPO)}:{n}: {lit}" for n, lit in unknown_audit_types(text)]
    assert not bad, (
        "audit type literal(s) not in schemas._KNOWN_LOG_ENTRY_TYPES "
        "(register the LogEntry variant, or allowlist a non-audit literal with a reason):\n"
        + "\n".join(bad)
    )


# ── d. cf-router audit_intercepted reasons ─────────────────────────────────
# Scanner: fleet_harness.intercepted_reason_literals (shared with
# test_flyer_intent_layer — one scanner, not one per suite).
_ALLOWED_REASONS = frozenset(
    typing.get_args(CfRouterIntercepted.model_fields["reason"].annotation)
)


def test_reason_scan_catches_unregistered_literal():
    """Every reachable shape is scanned: keyword, ternary, positional, a reason
    routed through a local variable or a module-level constant; f-string
    fragments are not."""
    sample = (
        "_MUTANT_REASON = 'fleet_mutant_module_const'\n"
        "_OK_REASON = 'f8_owner_reject'\n"
        "def m(actions, c):\n"
        "    actions.audit_intercepted(reason=_MUTANT_REASON, chat_id=c)\n"
        "    actions.audit_intercepted(reason=_OK_REASON, chat_id=c)\n"
        "def a(actions, c, x):\n"
        "    actions.audit_intercepted(reason='f8_owner_approve', chat_id=c)\n"
        "    actions.audit_intercepted(\n"
        "        reason=('fleet_mutant_ternary' if x else 'f8_menu_yes'), chat_id=c)\n"
        "    audit_intercepted('fleet_mutant_positional', c)\n"
        "def b(actions, c, x):\n"
        "    reason = 'fleet_mutant_variable' if x else 'f8_menu_no'\n"
        "    actions.audit_intercepted(reason=reason, chat_id=c, detail=f'n={x}')\n"
        "def other(c):\n"
        "    reason = 'not_reaching_any_call'\n"
    )
    bad = [lit for _, lit in intercepted_reason_literals(sample) if lit not in _ALLOWED_REASONS]
    assert sorted(bad) == ["fleet_mutant_module_const", "fleet_mutant_positional",
                           "fleet_mutant_ternary", "fleet_mutant_variable"]


@pytest.mark.parametrize("module", ["hooks.py", "actions.py"])
def test_cf_router_intercepted_reasons_are_schema_members(module):
    assert _ALLOWED_REASONS, "CfRouterIntercepted.reason is no longer a Literal"
    literals = intercepted_reason_literals((CF_ROUTER / module).read_text(encoding="utf-8"))
    if module == "hooks.py":
        assert literals, "scan found no audit_intercepted reason literals in hooks.py"
    bad = [f"{module}:{n}: {lit}" for n, lit in literals if lit not in _ALLOWED_REASONS]
    assert not bad, (
        "audit_intercepted reason(s) not in CfRouterIntercepted.reason — the row "
        "would raise ValidationError inside the best-effort emitter and be lost:\n"
        + "\n".join(bad)
    )
