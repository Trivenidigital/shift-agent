"""Build a corrective render instruction from referee blockers (Slice 1).

Two-sided: re-state the authorized locked facts (include side) AND explicitly
list fabricated claims/prices to remove (remove side). The remove side targets
the failure mode where the image model invents promo banners/prices that are
not in the customer's facts.
"""


def build_repair_instruction(blockers: list[str], locked_values: list[str]) -> str:
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
    parts = [
        "Render ONLY these exact facts and NOTHING else: "
        + " | ".join(locked_values)
        + "."
    ]
    if missing:
        parts.append("Ensure these are clearly visible: " + ", ".join(missing) + ".")
    remove_clause = "Remove any claim, price, offer, discount, badge, or label that is not in the list above"
    if fabricated:
        remove_clause += ": " + ", ".join(fabricated)
    parts.append(remove_clause + ".")
    return " ".join(parts)
