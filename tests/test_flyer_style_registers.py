"""Graduation commit 1 — style_registers data module (plan:
tasks/flyer-prompt-graduation-plan.md).

Contracts pinned here:
- Registers/occasions/intensities are a CLOSED vocabulary with deterministic
  selection; unknown names FAIL CLOSED (default register / no theme / accent).
- Every occasion vocabulary ships WITH its forbidden-substrings entries
  (leak law, standing rule 2026-07-04: new prompt vocabulary enters
  pre-screened at authoring time).
- Flag helper uses ppv1 semantics: FLYER_STYLE_REGISTERS=1 AND a NON-EMPTY
  allowlist AND phone membership — empty allowlist = DISABLED (the
  premium_overlay empty=global-on semantic is the ledgered gotcha this
  module must never reproduce).
- Style text NEVER contains fact-like content (no digits/prices/phones).
"""
from __future__ import annotations

import pytest

from agents.flyer.style_registers import (
    DEFAULT_REGISTER,
    INTENSITIES,
    OCCASIONS,
    REGISTERS,
    forbidden_substrings_for,
    style_prompt_block,
    style_registers_enabled,
)


def test_registry_shape():
    assert DEFAULT_REGISTER == "festive-premium"
    assert set(REGISTERS) >= {"festive-premium", "pure-festive", "festive-modern",
                              "clean-modern", "premium-dark"}
    assert set(OCCASIONS) == {"july4", "diwali", "ramadan", "thanksgiving"}
    assert set(INTENSITIES) == {"accent", "full"}


def test_selector_composes_register_occasion_intensity():
    block = style_prompt_block("festive-premium", occasion="july4", intensity="full")
    assert "FESTIVE PREMIUM" in block
    assert "JULY 4TH" in block and "FULL intensity" in block
    accent = style_prompt_block("festive-premium", occasion="july4", intensity="accent")
    assert "ACCENT intensity" in accent and accent != block


def test_unknown_names_fail_closed():
    # Unknown register -> default register; unknown occasion -> no theme;
    # unknown intensity -> accent. Never raises, never guesses a festival.
    base = style_prompt_block(DEFAULT_REGISTER)
    assert style_prompt_block("neon-vaporwave") == base
    assert style_prompt_block(DEFAULT_REGISTER, occasion="christmas") == base
    fell_back = style_prompt_block(DEFAULT_REGISTER, occasion="july4", intensity="mega")
    assert "ACCENT intensity" in fell_back


def test_no_theme_without_occasion():
    block = style_prompt_block(DEFAULT_REGISTER, occasion=None)
    assert "OCCASION THEME" not in block


def test_every_occasion_ships_forbidden_entries():
    # Leak law: the vocabulary and its screen are authored together.
    for occ in OCCASIONS:
        entries = forbidden_substrings_for(DEFAULT_REGISTER, occasion=occ)
        assert len(entries) >= 4, occ
        assert all(e == e.lower() for e in entries), "screen entries are lowercase"


def test_base_forbidden_entries_cover_style_vocabulary():
    entries = forbidden_substrings_for(DEFAULT_REGISTER)
    for word in ("beveled", "scalloped", "dimensional", "letterspaced"):
        assert word in entries, word


def test_style_text_contains_no_fact_like_content():
    import re
    for reg in REGISTERS:
        for occ in (None, *OCCASIONS):
            for lvl in INTENSITIES:
                block = style_prompt_block(reg, occasion=occ, intensity=lvl)
                assert not re.search(r"[$]\d|\d{3,}", block), (reg, occ, lvl)


def test_flag_semantics_empty_allowlist_is_off(monkeypatch):
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", "")
    assert style_registers_enabled("+17329837841") is False  # empty = DISABLED
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", "+17329837841")
    assert style_registers_enabled("+17329837841") is True
    assert style_registers_enabled("+15550000000") is False
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "0")
    assert style_registers_enabled("+17329837841") is False


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("FLYER_STYLE_REGISTERS", raising=False)
    monkeypatch.delenv("FLYER_STYLE_REGISTERS_ALLOWLIST", raising=False)
    assert style_registers_enabled("+17329837841") is False
