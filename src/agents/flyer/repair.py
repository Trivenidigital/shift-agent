"""Build a corrective render instruction from referee blockers (Slice 1).

Two-sided: re-state the authorized locked facts (include side) AND explicitly
list fabricated claims/prices to remove (remove side). The remove side targets
the failure mode where the image model invents promo banners/prices that are
not in the customer's facts.

The include side resolves missing fact IDs to their human-readable values via
the ``locked`` mapping (fact_id -> value); emitting a bare fact_id like
``contact_phone`` is meaningless to an image model, which needs the actual
``+1 732-983-7841``.
"""


import re

_ITEM_NAME_FACT_RE = re.compile(r"^item:(\d+):name$")

_PREMIUM_PREAMBLE = "Edit this exact flyer. Change ONLY:"
_PREMIUM_KEEP = (
    "Keep every other element identical — layout, colours, photography, fonts, "
    "and all other text. Do not recompose or restyle."
)


def build_premium_repair_instruction(blockers: list[str], locked: dict[str, str]) -> str:
    """Slice 2 minimal-edit instruction for the image-to-image PREMIUM repair of
    the prior premium render. Unlike ``build_repair_instruction`` (which re-states
    the FULL locked contract for a text-to-image regen), this scopes the edit to
    ONLY the defective fields so the model changes just the broken text and holds
    the rest of the composition.

    Safety invariant: every clause emits ONLY values resolved from ``locked`` —
    NEVER a value parsed out of a (possibly wrong) referee blocker. A blocker
    whose locked value is unknown produces no clause (no fabricated guess), and
    dangerous/unknown prefixes (fabrication, unverified phone) are omitted
    entirely (they never reach the repair loop; this is defence-in-depth).

    Returns ``""`` when no recoverable clause can be built (empty blockers, or a
    set with no resolvable locked value) — the caller then falls through to the
    existing recovery ladder."""
    clauses: list[str] = []
    spelling_emitted = False

    def _item_with_price(name_fact_id: str, name_value: str) -> str:
        match = _ITEM_NAME_FACT_RE.match(name_fact_id)
        price = locked.get(f"item:{match.group(1)}:price") if match else ""
        if price:
            return f"add the menu item '{name_value} — {price}'"
        return f"add the menu item '{name_value}'"

    for raw in blockers:
        blocker = str(raw or "").strip()
        if not blocker:
            continue
        # Missing item NAME → add it (with the locked price when one is locked).
        m = re.match(r"^missing required visible fact: (item:\d+:name)$", blocker)
        if m:
            fact_id = m.group(1)
            value = locked.get(fact_id)
            if value:
                clauses.append(_item_with_price(fact_id, value))
            continue
        # Inferred item the model painted a photo of but never drew the name for.
        if blocker.startswith("inferred item not rendered: "):
            name = blocker.split(": ", 1)[1].strip()
            # Resolve to the LOCKED item name (so we only ever emit a locked value
            # and can attach the locked price); skip if not a locked item.
            fact_id = next(
                (fid for fid, val in locked.items()
                 if _ITEM_NAME_FACT_RE.match(fid) and val == name),
                "",
            )
            if fact_id:
                clauses.append(_item_with_price(fact_id, locked[fact_id]))
            continue
        # Missing business name → add the brand header.
        if blocker == "missing required visible fact: business_name":
            value = locked.get("business_name")
            if value:
                clauses.append(f"add the business name '{value}' as the brand header")
            continue
        # Missing schedule → add the schedule text.
        if blocker == "missing required visible fact: schedule":
            value = locked.get("schedule")
            if value:
                clauses.append(f"add the schedule '{value}'")
            continue
        # Misspelled / duplicated visible text → fix to the LOCKED item names.
        # The QA note is free-form and may quote the WRONG spelling, so we never
        # echo it; we re-state the locked item names as the correct targets.
        if blocker.startswith("visible text defect reported by QA: ") and not spelling_emitted:
            names = [
                val for fid, val in locked.items()
                if _ITEM_NAME_FACT_RE.match(fid) and val
            ]
            if names:
                clauses.append(
                    "fix the spelling of any misspelled item to its correct name: "
                    + ", ".join(names)
                )
                spelling_emitted = True
            continue
        # Unknown / dangerous prefixes → omit (defensive; never reaches here).
        continue

    if not clauses:
        return ""
    return f"{_PREMIUM_PREAMBLE} " + "; ".join(clauses) + ". " + _PREMIUM_KEEP


def build_repair_instruction(blockers: list[str], locked: dict[str, str]) -> str:
    fabricated = [
        b.split(": ", 1)[1]
        for b in blockers
        if b.startswith(("fabricated price visible: ", "fabricated offer claim visible: "))
    ]
    missing = [
        b.split(": ", 1)[1]
        for b in blockers
        if b.startswith("missing required visible fact: ")
    ]
    if locked:
        parts = [
            "Render ONLY these exact facts and NOTHING else: "
            + " | ".join(locked.values())
            + "."
        ]
    else:
        parts = [
            "Render ONLY the facts from the customer's original brief; add no "
            "prices, offers, badges, or claims that the customer did not provide."
        ]
    if missing:
        vals = [locked.get(fid, fid) for fid in missing]
        parts.append("Ensure these are clearly visible: " + ", ".join(vals) + ".")
    remove_clause = "Remove any claim, price, offer, discount, badge, or label that is not in the list above"
    if fabricated:
        remove_clause += ": " + ", ".join(fabricated)
    parts.append(remove_clause + ".")
    return " ".join(parts)
