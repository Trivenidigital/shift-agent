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
ROUTER = "_effective_render_model"
CONST = "_DETERMINISTIC_RENDERER_MODEL"

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
    """Structural form of the body: comments, formatting and the docstring
    stripped, logic kept.

    The docstring has to go or this compares prose: the two
    `_effective_render_model` copies document themselves differently while
    executing identically, and that is not a drift anyone needs paging about.

    Deliberately still strict about everything else — a purely local rename
    (`val` -> `raw`) DOES trip this even though behaviour is unchanged. That is
    the intended trade: this test asks "did anyone touch this body", and the
    behavioural matrix below is what asks "did the meaning change". A rename
    fails loudly with the fail-safe tests still green, which tells the author
    immediately that only structure moved.
    """
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(getattr(body[0], "value", None), ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    assert body, f"{node.name} has no body beyond its docstring"
    return ast.dump(ast.parse(ast.unparse(ast.Module(body=body, type_ignores=[]))), annotate_fields=True)


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


# ─── the switch is only half of it: where it ROUTES must match too ──────────
#
# The tests above prove both copies of the predicate agree on whether to engage.
# They say nothing about what engaging DOES. `_effective_render_model` and the
# `_DETERMINISTIC_RENDERER_MODEL` constant it returns are ALSO duplicated across
# the same two files, and the parity tests above stay green if the constant
# drifts. That failure is worse than a predicate drift: the switch engages on
# both paths, the operator sees it engage, and one path routes to a renderer
# name that does not exist.


def _extract_named(path: Path, name: str):
    """Same trick as `_extract`, for an arbitrary top-level function."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None
    )
    assert node is not None, f"{name} not found in {path} -- renamed or removed"
    return node


def _extract_constant(path: Path, name: str) -> str:
    """Read a module-level string constant without importing the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in stmt.targets
        ):
            return ast.literal_eval(stmt.value)
    raise AssertionError(f"{name} not found in {path} -- renamed or removed")


def _router(path: Path):
    """`_effective_render_model` wired to its OWN file's predicate and constant.

    Deliberately not cross-wired: the question is what each file does on its
    own, so a drifted constant shows up as a behavioural difference.
    """
    namespace: dict = {"os": os, CONST: _extract_constant(path, CONST)}
    exec(compile(ast.Module(body=[_extract_named(path, FUNC)], type_ignores=[]), str(path), "exec"), namespace)
    exec(compile(ast.Module(body=[_extract_named(path, ROUTER)], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[ROUTER]


def test_deterministic_renderer_constant_is_identical_in_both_files():
    bare = _extract_constant(BARE_RENDER, CONST)
    concepts = _extract_constant(CONCEPTS, CONST)
    assert bare == concepts, (
        f"the panic switch routes to {bare!r} in bare_render and {concepts!r} in "
        "generate-flyer-concepts -- it would engage on both paths and only one "
        "would reach a real renderer"
    )


def test_both_effective_render_model_implementations_are_structurally_identical():
    assert _normalized_body(_extract_named(BARE_RENDER, ROUTER)) == _normalized_body(
        _extract_named(CONCEPTS, ROUTER)
    )


def test_engaged_switch_routes_both_files_to_the_same_renderer(monkeypatch):
    """Behavioural parity, and the positive control for it: engaging must
    actually CHANGE the model on both sides, not merely agree."""
    bare, concepts = _router(BARE_RENDER), _router(CONCEPTS)
    configured = "openai/gpt-5.4-image-2"

    monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", "1")
    assert bare(configured) == concepts(configured), "engaged switch routes to different renderers"
    assert bare(configured) != configured, "engaged switch left the generative model in place"
    assert bare(configured) == _extract_constant(BARE_RENDER, CONST)

    monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", "off")
    assert bare(configured) == concepts(configured) == configured, (
        "a disengaged switch must pass the configured model through untouched"
    )


def test_router_agrees_across_the_full_operator_input_matrix(monkeypatch):
    bare, concepts = _router(BARE_RENDER), _router(CONCEPTS)
    for value in _ENGAGE + _STAY_OFF:
        monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", value)
        for model in ("openai/gpt-5.4-image-2", "gpt-image-1", "deterministic-renderer", ""):
            assert bare(model) == concepts(model), f"disagree on env={value!r} model={model!r}"
