import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "agents" / "flyer"))
from repair import build_premium_repair_instruction, build_repair_instruction

def test_includes_missing_and_removes_fabricated():
    blockers = ["missing required visible fact: contact_phone",
                "fabricated price visible: $3.99",
                "fabricated offer claim visible: Limited Time Deal"]
    locked = {"business_name": "Lakshmi's Kitchen",
              "contact_phone": "+1 732-983-7841",
              "item_0": "Punugulu $6.99"}
    instr = build_repair_instruction(blockers, locked)
    # include side
    assert "+1 732-983-7841" in instr
    assert "Punugulu $6.99" in instr
    # remove side
    assert "remove" in instr.lower()
    assert "$3.99" in instr
    assert "Limited Time Deal" in instr

def test_no_fabrication_only_include():
    blockers = ["missing required visible fact: contact_phone"]
    locked = {"business_name": "Lakshmi's Kitchen",
              "contact_phone": "+1 732-983-7841"}
    instr = build_repair_instruction(blockers, locked)
    assert "+1 732-983-7841" in instr
    # remove-clause still present but with no specific fabricated items
    assert "remove" in instr.lower()

def test_empty_blockers_still_returns_constraint():
    instr = build_repair_instruction([], {"business_name": "Lakshmi's Kitchen",
                                          "item_0": "$6.99"})
    assert "Lakshmi's Kitchen" in instr  # always re-states the locked contract

def test_missing_clause_resolves_to_value():
    blockers = ["missing required visible fact: contact_phone"]
    locked = {"business_name": "Lakshmi's Kitchen",
              "contact_phone": "+1 732-983-7841"}
    instr = build_repair_instruction(blockers, locked)
    # the value (not the fact_id) must appear inside the ENSURE clause
    ensure_idx = instr.find("clearly visible: ")
    assert ensure_idx != -1
    assert "+1 732-983-7841" in instr[ensure_idx:]
    assert "contact_phone" not in instr[ensure_idx:]

def test_empty_locked_is_clean():
    instr = build_repair_instruction(
        ["missing required visible fact: contact_phone"], {})
    # no malformed "...: ." fragment from joining an empty value set
    assert ": ." not in instr
    # fallback phrase present
    assert "customer's original brief" in instr

def test_fabricated_only():
    blockers = ["fabricated price visible: $3.99",
                "fabricated offer claim visible: Limited Time Deal"]
    locked = {"business_name": "Lakshmi's Kitchen"}
    instr = build_repair_instruction(blockers, locked)
    # remove clause names the fabricated items
    assert "$3.99" in instr
    assert "Limited Time Deal" in instr
    assert "remove" in instr.lower()
    # no ensure clause since nothing is missing
    assert "clearly visible:" not in instr

def test_colon_in_fabricated_value():
    blockers = ["fabricated offer claim visible: Buy 2: Get 1 Free"]
    locked = {"business_name": "Lakshmi's Kitchen"}
    instr = build_repair_instruction(blockers, locked)
    # split(": ", 1) keeps the full value including the embedded colon
    assert "Buy 2: Get 1 Free" in instr


# --- Slice 2 Task 3: build_premium_repair_instruction (minimal-edit) ----------

PREMIUM_PREAMBLE = "Edit this exact flyer. Change ONLY:"
PREMIUM_KEEP = (
    "Keep every other element identical — layout, colours, photography, fonts, "
    "and all other text. Do not recompose or restyle."
)


def test_premium_instruction_has_minimal_edit_envelope():
    blockers = ["missing required visible fact: item:0:name"]
    locked = {"item:0:name": "Punugulu"}
    instr = build_premium_repair_instruction(blockers, locked)
    assert instr.startswith(PREMIUM_PREAMBLE)
    assert PREMIUM_KEEP in instr


def test_premium_instruction_adds_missing_item_with_locked_price():
    blockers = ["missing required visible fact: item:0:name"]
    locked = {"item:0:name": "Vada", "item:0:price": "$7.99"}
    instr = build_premium_repair_instruction(blockers, locked)
    assert "add the menu item 'Vada — $7.99'" in instr


def test_premium_instruction_adds_missing_item_without_price():
    blockers = ["missing required visible fact: item:1:name"]
    locked = {"item:1:name": "Egg Bonda"}
    instr = build_premium_repair_instruction(blockers, locked)
    assert "add the menu item 'Egg Bonda'" in instr
    assert "—" not in instr.split(PREMIUM_KEEP)[0].split("Egg Bonda")[1]


def test_premium_instruction_handles_inferred_item_not_rendered():
    blockers = ["inferred item not rendered: Aloo Bonda"]
    locked = {"item:2:name": "Aloo Bonda", "item:2:price": "$6.99"}
    instr = build_premium_repair_instruction(blockers, locked)
    assert "add the menu item 'Aloo Bonda — $6.99'" in instr


def test_premium_instruction_fixes_spelling_to_locked_names():
    blockers = ["visible text defect reported by QA: item 'Uttapoo' appears misspelled"]
    locked = {"item:0:name": "Uttapam", "item:1:name": "Dosa"}
    instr = build_premium_repair_instruction(blockers, locked)
    assert "fix the spelling" in instr.lower()
    # ONLY locked names appear as the correct targets.
    assert "Uttapam" in instr
    assert "Dosa" in instr
    # NEVER echoes the (possibly wrong) value out of the free-form QA note.
    assert "Uttapoo" not in instr


def test_premium_instruction_adds_business_name_as_brand_header():
    blockers = ["missing required visible fact: business_name"]
    locked = {"business_name": "Lakshmi's Kitchen"}
    instr = build_premium_repair_instruction(blockers, locked)
    assert "add the business name 'Lakshmi's Kitchen' as the brand header" in instr


def test_premium_instruction_adds_schedule():
    blockers = ["missing required visible fact: schedule"]
    locked = {"schedule": "Saturday & Sunday 11am-3pm"}
    instr = build_premium_repair_instruction(blockers, locked)
    assert "add the schedule 'Saturday & Sunday 11am-3pm'" in instr


def test_premium_instruction_omits_unknown_or_dangerous_prefixes():
    # Dangerous prefixes (fabrication / unverified phone) must never produce a
    # clause — they never reach here, but defensively they are dropped.
    blockers = [
        "fabricated price visible: $3.99",
        "unverified phone number visible: 614-956-1099",
        "some unrecognized blocker shape",
    ]
    locked = {"business_name": "Lakshmi's Kitchen", "contact_phone": "+17329837841"}
    instr = build_premium_repair_instruction(blockers, locked)
    # No fabricated/phone value leaks into the instruction.
    assert "$3.99" not in instr
    assert "614-956-1099" not in instr
    # With no recoverable clauses, returns an empty/no-op string.
    assert instr == ""


def test_premium_instruction_never_emits_value_absent_from_locked():
    # A missing item whose name is NOT in locked → no fabricated guess emitted.
    blockers = ["missing required visible fact: item:5:name"]
    locked = {"business_name": "Lakshmi's Kitchen"}
    instr = build_premium_repair_instruction(blockers, locked)
    assert instr == ""


def test_premium_instruction_empty_blockers_is_noop():
    assert build_premium_repair_instruction([], {"business_name": "X"}) == ""
