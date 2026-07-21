"""Deterministic mix-and-match recomposition planner (PR-D turn-3 assist).

The customer asks to combine sections of already-SENT proposal options
("option 1 starters with the option 2 mains"). Rather than let an LLM compose
the item list — which on gpt-4o-mini silently drops half the request ~half the
time (prose promises a mix the payload doesn't deliver) — this module resolves
the request MECHANICALLY against the sent options' items, so the merge cannot
lie about what it contains.

Pure functions only (no I/O): `parse_section_refs` extracts (option, section)
references from the raw text; `recompose_plan` resolves them against a SENT
proposal set + the menu and returns either a deterministic merge payload or a
CLARIFY decision. Conservative by construction — anything imperfect (an option
number that wasn't sent, a section the named option lacks, fewer than two
sections named, the same section named twice) yields a clarify, NEVER a
best-guess merge and NEVER a menu re-dump.
"""
from __future__ import annotations

import re
from typing import Optional

# Section vocabulary: customer words -> menu category (subset of CATEGORY_ORDER
# in create-catering-proposal-options / MenuCategory in schemas.py).
_SECTION_SYNONYMS: dict[str, str] = {
    "starter": "appetizer", "starters": "appetizer",
    "appetizer": "appetizer", "appetizers": "appetizer",
    "app": "appetizer", "apps": "appetizer",
    "small plate": "appetizer", "small plates": "appetizer",
    "main": "main", "mains": "main", "entree": "main", "entrees": "main",
    "entree course": "main", "main course": "main", "mains course": "main",
    "dessert": "dessert", "desserts": "dessert", "sweet": "dessert", "sweets": "dessert",
    "side": "side", "sides": "side",
    "soup": "soup", "soups": "soup",
    "salad": "salad", "salads": "salad",
    "package": "package", "packages": "package", "combo": "package", "combos": "package",
    "beverage": "beverage", "beverages": "beverage", "drink": "beverage", "drinks": "beverage",
    "special": "special", "specials": "special",
}

# Render/merge order — must match create-catering-proposal-options CATEGORY_ORDER.
CATEGORY_ORDER = [
    "package", "appetizer", "soup", "salad", "main", "side", "dessert", "beverage", "special",
]

# Longest-first so multiword synonyms ("small plates") win over "plate".
_SECTION_PATTERNS = sorted(_SECTION_SYNONYMS.keys(), key=len, reverse=True)
_OPTION_RE = re.compile(r"option\s*(\d+)", re.IGNORECASE)


def _first_section(window: str) -> Optional[str]:
    """Return the category of the earliest-occurring section synonym in `window`."""
    best_pos: Optional[int] = None
    best_cat: Optional[str] = None
    for word in _SECTION_PATTERNS:
        m = re.search(r"\b" + re.escape(word) + r"\b", window)
        if m and (best_pos is None or m.start() < best_pos):
            best_pos = m.start()
            best_cat = _SECTION_SYNONYMS[word]
    return best_cat


def parse_section_refs(text: str) -> list[tuple[int, str]]:
    """Extract ordered (option_number, category) references from the request.

    Each `option N` token claims the window up to the next `option` token; the
    first section synonym in that window is its section. Returns [] when no
    `option N <section>` reference is present ("the biryani one" -> [])."""
    t = " ".join((text or "").split())
    matches = list(_OPTION_RE.finditer(t))
    refs: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        n = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(t)
        sec = _first_section(t[start:end].lower())
        if sec is not None:
            refs.append((n, sec))
    return refs


def _sent_option_sections(sent_set: dict, menu_by_name: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    """{option_id: {category: [item names in that category, order preserved]}} for a SENT set.
    Items whose name is not on the current menu are skipped (defensive; the render
    layer is the catalog-exact chokepoint)."""
    out: dict[str, dict[str, list[str]]] = {}
    for opt in sent_set.get("options", []):
        oid = str(opt.get("option_id"))
        by_cat: dict[str, list[str]] = {}
        for name in opt.get("item_names", []):
            cat = menu_by_name.get(name)
            if cat is None:
                continue
            by_cat.setdefault(cat, []).append(name)
        out[oid] = by_cat
    return out


def _available_options_phrase(option_ids: list[str]) -> str:
    ids = sorted(option_ids, key=lambda x: int(x) if x.isdigit() else 0)
    if len(ids) == 1:
        return f"option {ids[0]}"
    if len(ids) == 2:
        return f"options {ids[0]} and {ids[1]}"
    return "options " + ", ".join(ids[:-1]) + f", and {ids[-1]}"


def _section_word(category: str) -> str:
    return {"appetizer": "starters", "main": "mains", "dessert": "desserts",
            "side": "sides", "soup": "soups", "salad": "salads",
            "package": "packages", "beverage": "drinks", "special": "specials"}.get(category, category)


def _clarify(reason: str, message: str) -> dict:
    return {"kind": "clarify", "reason": reason, "message": message}


def recompose_plan(request_text: str, sent_set: Optional[dict],
                   menu_by_name: dict[str, str]) -> dict:
    """Resolve a mix-and-match request against a SENT proposal set + the menu.

    Returns one of:
      {"kind": "merge", "item_names": [...], "sections": [category, ...],
       "combination": "option 1 starters + option 2 mains"}
      {"kind": "clarify", "reason": <code>, "message": <customer-facing one-liner>}

    Conservative: any imperfection clarifies. Never a best-guess merge."""
    if not sent_set or not sent_set.get("options"):
        return _clarify("no_sent_set",
                        "I haven't sent you menu options yet — shall I put a couple together first?")

    option_sections = _sent_option_sections(sent_set, menu_by_name)
    avail = _available_options_phrase(list(option_sections.keys()))
    refs = parse_section_refs(request_text)

    # Underspecified: a genuine mix names at least two sections.
    if len(refs) < 2:
        return _clarify(
            "underspecified",
            f"Happy to mix and match — I've sent you {avail}. Tell me which option's "
            f"section you'd like from each, for example 'option 1 starters with option 2 mains'.")

    # Same section named twice is ambiguous (which option wins?).
    seen_sections: dict[str, int] = {}
    for n, sec in refs:
        if sec in seen_sections and seen_sections[sec] != n:
            return _clarify(
                "ambiguous_section",
                f"Which option's {_section_word(sec)} would you like — I saw more than one. "
                f"For example, 'option 1 starters with option 2 mains'.")
        seen_sections[sec] = n

    # Resolve every reference against the sent options.
    for n, sec in refs:
        oid = str(n)
        if oid not in option_sections:
            return _clarify(
                "unknown_option",
                f"I've only sent you {avail}. Which of those would you like to combine — "
                f"for example, option 1's starters with option 2's mains?")
        if not option_sections[oid].get(sec):
            return _clarify(
                "missing_section",
                f"Option {n} doesn't include a {_section_word(sec)} section. "
                f"Which option's {_section_word(sec)} would you like?")

    # Clean: merge verbatim, section items in CATEGORY_ORDER.
    sec_to_option = {sec: str(n) for n, sec in refs}
    ordered_sections = [c for c in CATEGORY_ORDER if c in sec_to_option]
    item_names: list[str] = []
    for cat in ordered_sections:
        for name in option_sections[sec_to_option[cat]][cat]:
            if name not in item_names:
                item_names.append(name)
    combination = " + ".join(
        f"option {sec_to_option[cat]} {_section_word(cat)}" for cat in ordered_sections)
    return {"kind": "merge", "item_names": item_names,
            "sections": ordered_sections, "combination": combination}
