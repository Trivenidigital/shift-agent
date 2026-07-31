"""Canonical catering state paths — ONE definition, imported everywhere.

Before M2 the catering menu path literal was copy-pasted into six scripts
(apply-menu-update, finalize-catering-menu, select-catering-proposal,
create-catering-proposal-options, create-catering-lead, catering-pattern-report).
Each copy was an independent chance to drift. This module is the single
definition; the scripts keep their module-level `MENU_PATH = ...` name (tests
monkeypatch that attribute) but bind it from here.

Deployed FLAT to /opt/shift-agent/ so scripts and the cf-router plugin can
`from catering_paths import ...` — same layout contract as catering_amendments /
catering_quote_ledger.

Values are plain constants, NOT env-overridable: the deployed tree is
single-tenant and tests override the consuming module's attribute (see
tests/test_catering_finalize_menu.py::_run_script), which keeps this module
free of import-time environment coupling.
"""
from __future__ import annotations

from pathlib import Path

STATE_DIR = Path("/opt/shift-agent/state")

# ── Menu (culinary catalog) ──────────────────────────────────────────────────
CATERING_MENU_PATH = STATE_DIR / "catering-menu.json"
CATERING_MENU_LOCK = Path(str(CATERING_MENU_PATH) + ".lock")
CATERING_MENU_ARCHIVE_DIR = STATE_DIR / "catering-menu-archive"

# ── Pricebook (commercial source of truth, M2) ───────────────────────────────
CATERING_PRICEBOOK_PATH = STATE_DIR / "catering-pricebook.json"
CATERING_PRICEBOOK_LOCK = Path(str(CATERING_PRICEBOOK_PATH) + ".lock")
CATERING_PRICEBOOK_ARCHIVE_DIR = STATE_DIR / "catering-pricebook-archive"

__all__ = [
    "STATE_DIR",
    "CATERING_MENU_PATH",
    "CATERING_MENU_LOCK",
    "CATERING_MENU_ARCHIVE_DIR",
    "CATERING_PRICEBOOK_PATH",
    "CATERING_PRICEBOOK_LOCK",
    "CATERING_PRICEBOOK_ARCHIVE_DIR",
]
