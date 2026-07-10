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


# Each generator must consult at least one SIBLING agent's #XXXXX store (cross-pool), not
# just its own — the dispatcher disambiguates a cross-pool collision only by state-file
# priority, so an own-pool-only generator can mint a code that silently shadows a sibling's.
# BL-SEC-04 (catering) / BL-SHIFT-13 (shift) closed the last two own-pool-only generators.
_CROSS_POOL_SIBLING_REF = {
    "create-proposal": "catering-leads.json",   # shift must also consult catering leads
    "create-catering-lead": "pending.json",      # catering must also consult shift proposals
    "parse-menu-photo": "pending.json",          # catering must also consult shift proposals
    "extract-receipt": "catering-leads.json",    # expense must also consult a sibling pool
}


def test_all_generators_check_cross_pool():
    """Every generator must reference at least one SIBLING agent's code store, closing the
    S2-6 cross-pool gap. A future edit that drops the sibling scan (reverting a generator to
    own-pool-only) trips this test.

    Text-grep proxy (like the sibling test above): asserts the sibling store PATH is referenced,
    not that it is behaviorally consulted — a full behavioral test needs the fcntl-dependent
    scripts (Linux-only). Still strong enough to catch a dropped sibling scan.
    """
    for path in GENERATOR_FILES:
        text = path.read_text(encoding="utf-8")
        sibling = _CROSS_POOL_SIBLING_REF[path.name]
        assert sibling in text, (
            f"{path.name} does not reference sibling pool store {sibling!r} — it can mint a "
            f"code colliding with a live code in a sibling agent's pool (S2-6 cross-pool gap, "
            f"BL-SEC-04/BL-SHIFT-13)"
        )
