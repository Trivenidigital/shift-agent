"""M2 acceptance guard — every price-bearing catering surface labels its price.

Binding ruling: a customer or owner must never receive a number without being
told how firm it is. This is a grep-style static guard (no subprocess, no box)
so it runs everywhere and fails the moment someone adds a priced template or a
priced render path without the label.

The generic rule catches FUTURE templates: any catering template that renders a
currency amount must carry the marker. The explicit list pins the surfaces that
exist today so a rename cannot quietly drop one out of scope.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO / "src" / "agents" / "catering" / "templates"
SCRIPT_DIR = REPO / "src" / "agents" / "catering" / "scripts"
_PLATFORM_DIR = REPO / "src" / "platform"
if str(_PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_DIR))

from catering_pricing import PRICE_STATUS_LABELS, PRICE_STATUS_PREFIX  # noqa: E402

# The template placeholder the render paths substitute the label into.
LABEL_SLOT = "{price_status_line}"
# A rendered currency amount: '$' followed by a digit or a substitution slot.
PRICE_TOKEN = re.compile(r"\$\s*(?:\d|\{)")

# Surfaces that exist today and MUST carry the label.
REQUIRED_TEMPLATES = (
    "catering_finalized_menu_to_owner.txt",   # owner approval card (priced)
    "catering_quote_to_customer.txt",         # customer-facing quote
)
# Render paths whose INLINE FALLBACK text must also carry it — the fallback is
# what actually sends when a template file is missing on a fresh box.
REQUIRED_INLINE_FALLBACKS = (
    "finalize-catering-menu",
    "create-catering-lead",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_template_dir_exists_with_templates():
    """Fail-closed: an empty scan would make every other cell vacuously green."""
    assert TEMPLATE_DIR.is_dir()
    assert list(TEMPLATE_DIR.glob("*.txt")), "no catering templates found to check"


@pytest.mark.parametrize("name", REQUIRED_TEMPLATES)
def test_required_template_carries_the_price_status_slot(name):
    path = TEMPLATE_DIR / name
    assert path.is_file(), f"{name} is missing — did it get renamed?"
    assert LABEL_SLOT in _read(path), (
        f"{name} must contain {LABEL_SLOT} so every rendered copy states how "
        "firm the price is"
    )


@pytest.mark.parametrize("script", REQUIRED_INLINE_FALLBACKS)
def test_render_path_inline_fallback_carries_the_label(script):
    text = _read(SCRIPT_DIR / script)
    assert "price_status_line" in text, (
        f"{script}'s inline fallback must emit the price-status label — the "
        "fallback is what sends when the template file is absent"
    )


def test_every_price_bearing_template_carries_the_label():
    """The forward-looking rule: a new priced template cannot ship unlabelled."""
    offenders = []
    for path in sorted(TEMPLATE_DIR.glob("*.txt")):
        text = _read(path)
        if PRICE_TOKEN.search(text) and LABEL_SLOT not in text:
            offenders.append(path.name)
    assert not offenders, (
        "catering template(s) render a price but carry no price-status label; add "
        f"{LABEL_SLOT} (and populate it in the render path): {offenders}"
    )


def test_kernel_render_emits_the_label_for_every_status():
    """The label vocabulary itself is complete and grep-shaped."""
    from catering_pricing import price_status_line
    for status in ("exact", "estimated", "pending_owner_review"):
        line = price_status_line(status)
        assert line.startswith(PRICE_STATUS_PREFIX)
        assert PRICE_STATUS_LABELS[status] in line


def test_estimated_label_says_pending_owner_validation():
    """The wording is contractual — operators grep logs for it."""
    assert PRICE_STATUS_LABELS["estimated"].startswith("estimated")
    assert "pending owner validation" in PRICE_STATUS_LABELS["estimated"]


def test_guard_is_not_vacuous_negative_selftest(tmp_path):
    """A priced template with no label must be detected by the same rule."""
    bad = tmp_path / "priced_no_label.txt"
    bad.write_text("Total: ${quote_total_usd}\n", encoding="utf-8")
    text = _read(bad)
    assert PRICE_TOKEN.search(text) and LABEL_SLOT not in text

    good = tmp_path / "priced_with_label.txt"
    good.write_text("Total: ${quote_total_usd}\n{price_status_line}\n", encoding="utf-8")
    text = _read(good)
    assert PRICE_TOKEN.search(text) and LABEL_SLOT in text
