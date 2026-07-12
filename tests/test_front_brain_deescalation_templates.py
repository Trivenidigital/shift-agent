"""P0-3b — curated de-escalation template set + selector + multilingual fixtures.

Two things under test:
  1. The template set + fail-closed selector (like style_registers): every abuse
     class maps to a warm, non-argumentative, one-line reply that itself passes
     the outbound enforcement screen.
  2. The MULTILINGUAL fixture corpus is classifiable-IN-PRINCIPLE (every fixture's
     abuse class resolves to a template), AND it documents the evidence line for
     why classification must be LLM-side: a pure English word-list is BLIND to
     romanized Hindi / romanized Telugu / Spanish / much code-mixed abuse, and to
     abuse that carries no profanity token at all (threats, spam). Classification
     wiring lands in Phase 1 — this file only proves the corpus is tractable and
     the word-list is not.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "platform"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deescalation_templates import (  # noqa: E402
    ABUSE_CLASSES,
    DEFAULT_DEESCALATION_CLASS,
    DEESCALATION_REPLIES,
    select_deescalation_reply,
)
from agents.flyer.customer_copy_policy import enforce_free_form_text  # noqa: E402


# ── Multilingual abuse fixture corpus (TEST DATA, mandatory per plan) ────────
# Fields: (phrase, language, abuse_class, catchable_by_english_wordlist)
# `catchable_by_english_wordlist=False` marks phrases a pure English word-list
# CANNOT flag — the evidence that classification is LLM-side. Content is kept
# mild-but-clearly-negative (no slurs / no graphic threats).
MULTILINGUAL_ABUSE_FIXTURES: tuple[tuple[str, str, str, bool], ...] = (
    # English — obvious profanity/hostility tokens (word-list CAN catch)
    ("You people are idiots, this whole thing is garbage.", "en", "hostile", True),
    ("This is complete crap, absolute trash of an app.", "en", "profane", True),
    # English — abuse with NO profanity token (word-list is blind)
    ("I am going to make you regret this. You will hear from me.", "en", "threatening", False),
    ("CLICK HERE to WIN a FREE prize now, just visit our link.", "en", "spam_offtopic", False),
    # Romanized Hindi (word-list blind — non-English abuse vocabulary)
    ("Tumhara app bilkul bekaar hai, ghatiya service.", "hi-rom", "hostile", False),
    ("Bakwaas hai ye sab kuch, mera time waste kiya.", "hi-rom", "hostile", False),
    # Romanized Telugu (word-list blind)
    ("Mee service chala chetha ga undi.", "te-rom", "hostile", False),
    ("Mee app matu panikiraadu, asalu pnichadu.", "te-rom", "profane", False),
    # Spanish (word-list blind)
    ("Su servicio es una basura, no sirve para nada.", "es", "hostile", False),
    ("Voy a reportarlos, esto es un fraude total.", "es", "threatening", False),
    # Code-mixed — English profanity token leaks through (partial catch)
    ("Ye app bilkul trash hai, worst service ever.", "codemix", "hostile", True),
    ("Bhai ye kya bakwaas hai, totally useless app.", "codemix", "profane", True),
)

# A naive English abuse/profanity word-list — deliberately the SHAPE of a
# "deterministic tone lint" the plan rejects. It is blind to everything not in
# English and to abuse that carries no profanity token.
_ENGLISH_ABUSE_WORDS = frozenset({
    "idiot", "idiots", "stupid", "hate", "useless", "trash", "garbage",
    "crap", "worst", "suck", "sucks", "rubbish",
})


def _english_wordlist_flags(phrase: str) -> bool:
    tokens = "".join(c.lower() if (c.isalnum() or c.isspace()) else " " for c in phrase).split()
    return any(t in _ENGLISH_ABUSE_WORDS for t in tokens)


# ── template set + selector ─────────────────────────────────────────────────

def test_every_abuse_class_has_a_template():
    for cls in ABUSE_CLASSES:
        reply = select_deescalation_reply(cls)
        assert reply and reply == DEESCALATION_REPLIES[cls]


def test_selector_fails_closed_to_default():
    for unknown in ("", "   ", "sarcastic", "unknown_class", None):  # type: ignore[arg-type]
        assert select_deescalation_reply(unknown) == DEESCALATION_REPLIES[DEFAULT_DEESCALATION_CLASS]


def test_selector_is_case_insensitive():
    assert select_deescalation_reply("HOSTILE") == DEESCALATION_REPLIES["hostile"]


def test_all_templates_pass_outbound_enforcement():
    # A de-escalation reply that itself tripped the enforcement screen would be
    # refused and replaced — self-defeating. Lock that every curated reply is
    # sendable as-is.
    for cls, reply in DEESCALATION_REPLIES.items():
        result = enforce_free_form_text(reply)
        assert result.passed, f"template {cls!r} tripped {result.hit_classes}: {reply!r}"


def test_templates_do_not_match_energy():
    # Warm/forward tone: no argument words, no ALL-CAPS shouting.
    for reply in DEESCALATION_REPLIES.values():
        assert "help" in reply.lower()
        # not SHOUTING back
        letters = [c for c in reply if c.isalpha()]
        upper_ratio = sum(c.isupper() for c in letters) / max(1, len(letters))
        assert upper_ratio < 0.3


# ── multilingual corpus is classifiable-in-principle ────────────────────────

def test_every_fixture_class_is_resolvable():
    for phrase, lang, abuse_class, _catchable in MULTILINGUAL_ABUSE_FIXTURES:
        assert abuse_class in ABUSE_CLASSES, f"{lang}: {abuse_class} not a known class"
        reply = select_deescalation_reply(abuse_class)
        assert reply, f"no template for {abuse_class} ({lang})"


# ── EVIDENCE: a pure word-list cannot catch the corpus (why LLM-side) ───────

def test_word_list_sanity_catches_obvious_english():
    for phrase, lang, _cls, catchable in MULTILINGUAL_ABUSE_FIXTURES:
        if catchable:
            assert _english_wordlist_flags(phrase), (
                f"expected English word-list to flag {lang!r} fixture: {phrase!r}"
            )


def test_word_list_misses_non_english_and_tokenless_abuse():
    missed = [
        (phrase, lang) for phrase, lang, _cls, catchable in MULTILINGUAL_ABUSE_FIXTURES
        if not catchable
    ]
    # Each "not catchable" fixture is genuinely missed by the word-list.
    for phrase, lang in missed:
        assert not _english_wordlist_flags(phrase), (
            f"word-list unexpectedly caught {lang!r}: {phrase!r} — recheck the fixture"
        )
    # The evidence line: the word-list is blind across MULTIPLE languages +
    # tokenless English abuse — a real, multilingual customer base defeats it.
    missed_langs = {lang for _p, lang in missed}
    assert {"hi-rom", "te-rom", "es"}.issubset(missed_langs)
    assert "en" in missed_langs  # even English threats/spam evade an abuse list
    assert len(missed) >= 6
