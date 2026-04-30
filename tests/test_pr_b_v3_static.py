"""PR-B v3 source-only static checks (Windows-runnable).

Tests that don't load the apply-script as a module — just grep the source
to verify deletions + additions landed. Companion to
test_pr_b_v3_apply_decision_quote_text.py which exercises the helpers
runtime (Linux-only because safe_io imports fcntl).
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_APPLY = _REPO_ROOT / "src" / "agents" / "catering" / "scripts" / "apply-catering-owner-decision"


@pytest.fixture(scope="module")
def apply_src() -> str:
    return _APPLY.read_text(encoding="utf-8")


def test_quote_text_stdin_flag_present(apply_src):
    """--quote-text-stdin must be in the apply-script's argparse spec."""
    assert "--quote-text-stdin" in apply_src
    assert 'action="store_true"' in apply_src


def test_template_machinery_deleted(apply_src):
    """All five template-machinery names must be gone as ACTIVE code.

    The function defs (_render_quote, _format_menu_section,
    _load_menu_filtered) and the constants (MENU_ITEMS_IN_QUOTE,
    TEMPLATE_DIR) must not appear as definitions or assignments.
    Comment references documenting what was replaced are allowed.
    """
    # Function definitions
    for fn in ["_render_quote", "_format_menu_section", "_load_menu_filtered"]:
        assert f"def {fn}" not in apply_src, \
            f"function {fn} should be deleted in PR-B v3 commit 2"
    # Constant assignments at module level
    for const in ["MENU_ITEMS_IN_QUOTE", "TEMPLATE_DIR"]:
        assert f"\n{const} = " not in apply_src, \
            f"constant {const} should be deleted in PR-B v3 commit 2"
        # Also catch if it's declared without leading newline (start of file)
        assert not apply_src.startswith(f"{const} = "), \
            f"constant {const} should be deleted in PR-B v3 commit 2"


def test_menu_imports_dropped(apply_src):
    """Menu / MenuItem schema imports gone; only used by deleted machinery."""
    import_lines = [l for l in apply_src.splitlines() if "from schemas import" in l]
    combined = "\n".join(import_lines)
    # Bare imports of these names must not appear — substring match on word
    # boundaries (commas / newlines) avoids false-positives on doc strings.
    assert " Menu," not in combined
    assert "MenuItem," not in combined
    assert " Menu\n" not in combined
    assert " MenuItem\n" not in combined


def test_quote_skill_failed_imported(apply_src):
    """CateringQuoteSkillFailed must be imported for audit emission."""
    assert "CateringQuoteSkillFailed" in apply_src


def test_new_helpers_present(apply_src):
    """The three v3 helpers added must exist in the source."""
    for name in ["_normalize_quote_text", "_truth_guard_check",
                 "_emit_quote_skill_failed_best_effort"]:
        assert f"def {name}" in apply_src, \
            f"{name} helper missing — should be added in PR-B v3 commit 2"


def test_re_and_unicodedata_imports_added(apply_src):
    """re + unicodedata imports added for the new helpers."""
    assert "import re" in apply_src
    assert "import unicodedata" in apply_src


def test_menu_path_constant_deleted(apply_src):
    """MENU_PATH constant deleted — apply-script no longer reads the menu."""
    assert "MENU_PATH" not in apply_src


def test_strip_unicode_categories_constant_present(apply_src):
    """The Cc/Cf/Cs/Co/Cn frozenset must be defined as a module-level constant."""
    assert "_STRIP_UNICODE_CATEGORIES" in apply_src
    # All five categories should appear in the constant definition
    for cat in ['"Cc"', '"Cf"', '"Cs"', '"Co"', '"Cn"']:
        assert cat in apply_src
