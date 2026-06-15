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
