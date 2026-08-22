"""The panic switch must trip BOTH generative render paths, or neither.

`_integrated_killswitch_active` is implemented twice — in
`src/agents/flyer/bare_render.py` and in
`src/agents/flyer/scripts/generate-flyer-concepts`. Both carry the comment
"Must stay in lockstep with <the other one>", and today they are identical.

A comment is not an enforcement mechanism. If the two ever diverge, the failure
is silent and lands at the worst possible moment: an operator sets
FLYER_INTEGRATED_KILLSWITCH during a live incident, one render path collapses to
the deterministic renderer as intended, and the other keeps calling the
generative provider. Nothing errors. The operator believes the switch is engaged.

`tests/test_flyer_scripts_static.py` already proves *totality* — that every
generative render routes its model through the helper. It does not compare the
two helpers to each other, so a divergence in the predicate itself passes it.
This file closes that gap.

The kill switch's fail-safe direction is inverted relative to an arming flag:
any value that is not clearly "off" must ENGAGE it, so a mistyped `true`/`on`/
`yes` still trips. The matrix below pins that direction on both copies.
"""
from __future__ import annotations

from pathlib import Path
import ast
import os

REPO = Path(__file__).resolve().parent.parent
BARE_RENDER = REPO / "src" / "agents" / "flyer" / "bare_render.py"
CONCEPTS = REPO / "src" / "agents" / "flyer" / "scripts" / "generate-flyer-concepts"

FUNC = "_integrated_killswitch_active"

# Values an operator might plausibly type, plus the off-switch vocabulary and
# the empty/unset case. Every entry that is not clearly "off" must ENGAGE.
_ENGAGE = ("1", "true", "True", "TRUE", "on", "yes", "Y", "engaged", "  1  ", "0.0", "please")
_STAY_OFF = ("", "0", "false", "False", "no", "off", "OFF", "  off  ")


def _extract(path: Path):
    """Compile just the one function out of a file, without importing it.

    `generate-flyer-concepts` is a 131KB CLI with heavy imports and module-level
    side effects; the point here is the predicate, not the program.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == FUNC), None
    )
    assert node is not None, f"{FUNC} not found in {path} -- it was renamed or removed"
    # The predicate's only dependency is `os`; supplying the real module keeps
    # the env lookup genuine rather than stubbing the thing under test.
    namespace: dict = {"os": os}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return node, namespace[FUNC]


def _normalized_body(node: ast.FunctionDef) -> str:
    """Structural form of the body: comments and formatting stripped, logic kept."""
    stripped = ast.Module(body=list(node.body), type_ignores=[])
    return ast.dump(ast.parse(ast.unparse(stripped)), annotate_fields=True)


def test_both_killswitch_implementations_are_structurally_identical():
    bare_node, _ = _extract(BARE_RENDER)
    concepts_node, _ = _extract(CONCEPTS)
    assert _normalized_body(bare_node) == _normalized_body(concepts_node), (
        "the two _integrated_killswitch_active implementations have diverged; "
        "the panic switch would trip one generative render path and silently "
        "leave the other one running"
    )


def test_both_implementations_agree_on_every_operator_input(monkeypatch):
    _, bare_fn = _extract(BARE_RENDER)
    _, concepts_fn = _extract(CONCEPTS)

    for value in _ENGAGE + _STAY_OFF:
        monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", value)
        assert bare_fn() == concepts_fn(), f"implementations disagree on {value!r}"

    monkeypatch.delenv("FLYER_INTEGRATED_KILLSWITCH", raising=False)
    assert bare_fn() == concepts_fn(), "implementations disagree when the var is unset"


def test_the_switch_fails_safe_on_anything_that_is_not_clearly_off(monkeypatch):
    """Positive control. Structural equality alone would still pass if BOTH
    copies were broken the same way, so pin the actual required direction."""
    for path in (BARE_RENDER, CONCEPTS):
        _, fn = _extract(path)
        for value in _ENGAGE:
            monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", value)
            assert fn() is True, f"{path.name}: {value!r} must ENGAGE the panic switch"
        for value in _STAY_OFF:
            monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", value)
            assert fn() is False, f"{path.name}: {value!r} must leave the switch off"
        monkeypatch.delenv("FLYER_INTEGRATED_KILLSWITCH", raising=False)
        assert fn() is False, f"{path.name}: unset must leave the switch off"
