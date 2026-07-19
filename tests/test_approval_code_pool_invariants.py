"""Invariant tests for the shared #XXXXX approval-code pool (audit finding S2-6).

Each agent inlines its own code generator (reviewer-a HIGH A1) rather than
importing a shared helper, so nothing structurally prevents the four alphabets
— or the generator alphabet vs. the schema validator pattern — from drifting
apart. A drift means a code one generator emits can fail another agent's schema
validation, or land outside the visually-unambiguous set. These tests fail if
any generator regresses.

They are pure-text (no import of the fcntl-dependent scripts), so unlike most
of this repo's script tests they run on every platform including the Windows
dev box, not only Linux CI.

This is the "every documented invariant gets one test that fails if the
invariant is violated" template from the 2026-07 audit remediation.
"""
from __future__ import annotations

import re
import string
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"

# The four agents that mint #XXXXX approval codes into the shared pool.
GENERATOR_FILES = [
    SRC / "agents" / "shift" / "scripts" / "create-proposal",
    SRC / "agents" / "catering" / "scripts" / "create-catering-lead",
    SRC / "agents" / "catering" / "scripts" / "parse-menu-photo",
    SRC / "agents" / "expense_bookkeeper" / "scripts" / "extract-receipt",
]

_ALPHA_RE = re.compile(r'_CODE_ALPHA\s*=\s*"([^"]+)"')


def _extract_alpha(path: Path) -> str:
    m = _ALPHA_RE.search(path.read_text(encoding="utf-8"))
    assert m, f"no _CODE_ALPHA literal found in {path.name}"
    return m.group(1)


def test_all_generator_alphabets_identical():
    """All four inlined generators must share one alphabet. If they drift, a
    code minted by one agent can be rejected by another agent's #XXXXX schema
    validator, or use a visually-ambiguous glyph the others exclude."""
    alphas = {p.name: _extract_alpha(p) for p in GENERATOR_FILES}
    distinct = set(alphas.values())
    assert len(distinct) == 1, (
        f"approval-code alphabets have drifted across generators: {alphas}"
    )


def test_alphabet_matches_schema_body_pattern():
    """The generator alphabet and the Pydantic validator (_CODE_BODY_PATTERN in
    schemas.py) must accept exactly the same character set — otherwise a
    generated code can fail validation, or the validator can accept a glyph no
    generator ever emits."""
    schemas_text = (SRC / "platform" / "schemas.py").read_text(encoding="utf-8")
    m = re.search(r'_CODE_BODY_PATTERN\s*=\s*r"([^"]+)"', schemas_text)
    assert m, "could not find _CODE_BODY_PATTERN in schemas.py"
    body_pattern = m.group(1)  # e.g. [A-HJKMNPQR-Z2-9]{5}
    char_class = re.match(r"\[([^\]]+)\]", body_pattern)
    assert char_class, f"unexpected body-pattern shape: {body_pattern!r}"
    one_char = re.compile(f"[{char_class.group(1)}]$")

    alpha_set = set(_extract_alpha(GENERATOR_FILES[0]))

    # Every char the pattern accepts must be in the alphabet, and vice-versa.
    for ch in string.ascii_uppercase + string.digits:
        allowed = bool(one_char.match(ch))
        present = ch in alpha_set
        assert allowed == present, (
            f"char {ch!r}: accepted-by-_CODE_BODY_PATTERN={allowed} but "
            f"in-generator-alphabet={present} — alphabet and validator drifted"
        )

    # And the alphabet contains nothing outside A-Z0-9 (no lowercase/symbols).
    assert alpha_set <= set(string.ascii_uppercase + string.digits), (
        f"alphabet contains unexpected chars: {alpha_set - set(string.ascii_uppercase + string.digits)}"
    )


def test_no_generator_mints_without_collision_check():
    """Regression guard for the specific S2-6 defect: parse-menu-photo minted a
    code with NO collision check at all (bare secrets.choice return). Every
    generator must consult an active-code set before returning. We assert each
    file references an active/collision set near its generator, so a future
    edit that drops the check trips this test.

    SCOPE / KNOWN GAPS (be honest — this is a text-grep proxy, not behavioral):
    - It detects PRESENCE of a collision reference, not that the reference is
      actually consulted. A partial regression (helper left defined but the
      generator reverts to a bare return) would still PASS. A behavioral test
      that seeds a store with a known code and asserts avoidance would be
      stronger but requires importing the fcntl-dependent scripts (Linux-only).
    - This test asserts PRESENCE of an own-pool check only. CROSS-pool coverage
      (each generator also consulting sibling pools) is asserted separately by
      test_all_generators_check_cross_pool below. As of BL-SEC-04/BL-SHIFT-13 all
      four generators check the shared pool cross-agent (inline sibling scan per
      parse-menu-photo:121 "each agent inlines its own generator"); this test
      remains the own-pool-presence guard.
    """
    for path in GENERATOR_FILES:
        text = path.read_text(encoding="utf-8")
        # Each generator either builds `active_codes`/`active` locally or calls
        # `_collect_active_codes`; a bare `return "#" + ...choice...` with no
        # such reference is the regression we are guarding against.
        has_check = (
            "_collect_active_codes" in text
            or "active_codes" in text
            or re.search(r"\bactive\b\s*=", text) is not None
        )
        assert has_check, (
            f"{path.name} mints an approval code with no active-code collision "
            f"check — regression of audit finding S2-6"
        )


def test_all_generators_check_cross_pool():
    """Every generator must draw its exclusion set from the ONE cross-pool source
    (PR-R1: approval_code_pools.all_live_codes, which unions ALL FOUR pools),
    closing the S2-6 cross-pool gap. A future edit that reverts a generator to an
    own-pool-only scan (dropping the registry) trips this test.

    Before PR-R1 each generator inlined its own sibling scan and this test grepped
    for a specific sibling state-file path; the four inline scans are now a single
    registry call, so the assertion tracks the single source instead.

    Text-grep proxy (like the sibling tests above): asserts the registry call is
    referenced, not that it is behaviorally consulted — the behavioral proof lives
    in tests/test_routing_invariants_r1.py (per-generator planted-collision tests).
    """
    for path in GENERATOR_FILES:
        text = path.read_text(encoding="utf-8")
        assert "all_live_codes" in text, (
            f"{path.name} does not consult approval_code_pools.all_live_codes — it can mint a "
            f"code colliding with a live code in a sibling agent's pool (S2-6 cross-pool gap, "
            f"BL-SEC-04/BL-SHIFT-13; PR-R1 single-source contract)"
        )


# ── PR-R1: pool-order lives in ONE canonical source, and the dispatcher SKILL
# prose conforms to it ────────────────────────────────────────────────────────

def _load_pools_module():
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(SRC / "platform"))
    return importlib.import_module("approval_code_pools")


def test_canonical_pool_order_single_source():
    """The canonical lookup order is exported from ONE constant."""
    pools = _load_pools_module()
    assert pools.CODE_POOL_CANONICAL_ORDER == (
        "menu-pending", "catering-leads", "expense", "shift",
    )


# Map the state-file basename each jq lookup line targets -> pool name. Order in
# this dict matters: expense-bookkeeper/leads.json is checked before the bare
# "pending.json" so the expense line isn't mis-bucketed.
_FILE_TO_POOL = {
    "catering-menu-pending.json": "menu-pending",
    "catering-leads.json": "catering-leads",
    "expense-bookkeeper/leads.json": "expense",
    "pending.json": "shift",
}

_SKILL_MD = SRC / "agents" / "shift" / "skills" / "dispatch_shift_agent" / "SKILL.md"


def _parse_skill_pool_order(text: str) -> list[str]:
    """Parse the SKILL's four jq lookup lines (top-to-bottom) into an ordered
    pool list, first occurrence of each pool preserved in document order."""
    order: list[str] = []
    for line in text.splitlines():
        if "jq " not in line:
            continue
        if "$c" not in line and "$CODE" not in line:
            continue
        for needle, pool in _FILE_TO_POOL.items():
            if needle in line:
                if pool not in order:
                    order.append(pool)
                break
    return order


def test_skill_md_pool_order_and_membership_match_registry():
    """The dispatcher SKILL's documented pool-lookup block (its four jq lines,
    read top-to-bottom) must resolve pools in the SAME order AND with the SAME
    membership as the registry's CODE_POOL_CANONICAL_ORDER. This is how the prose
    'consumes' the single executable source — if either drifts, this fails."""
    pools = _load_pools_module()
    order = _parse_skill_pool_order(_SKILL_MD.read_text(encoding="utf-8"))
    # membership (set equality) + order (sequence equality)
    assert set(order) == set(pools.CODE_POOL_CANONICAL_ORDER), (
        f"SKILL.md pools {set(order)} != registry membership {set(pools.CODE_POOL_CANONICAL_ORDER)}"
    )
    assert tuple(order) == pools.CODE_POOL_CANONICAL_ORDER, (
        f"SKILL.md order {order} != registry canonical order {pools.CODE_POOL_CANONICAL_ORDER}"
    )


def test_skill_order_parser_detects_reordering():
    """Evidence the assertion actually FAILS on drift: parse a DELIBERATELY
    reordered copy of the prose (menu <-> catering jq lines swapped) and assert
    the parsed order differs from the registry tuple (so the real-file test above
    would fail if the SKILL were reordered). Runs against a mutated STRING, never
    the real file."""
    pools = _load_pools_module()
    text = _SKILL_MD.read_text(encoding="utf-8")
    lines = text.splitlines()
    jq_idx = [i for i, ln in enumerate(lines)
              if "jq " in ln and ("$c" in ln or "$CODE" in ln)]
    assert len(jq_idx) >= 2, "expected >=2 jq lookup lines to mutate"
    i0, i1 = jq_idx[0], jq_idx[1]
    lines[i0], lines[i1] = lines[i1], lines[i0]  # swap first two lookup lines
    mutated_order = _parse_skill_pool_order("\n".join(lines))
    assert tuple(mutated_order) != pools.CODE_POOL_CANONICAL_ORDER, (
        "parser failed to detect a reordered SKILL prose block — the order "
        "assertion would not catch drift"
    )
    assert mutated_order[0] == "catering-leads", mutated_order  # menu/catering swapped
