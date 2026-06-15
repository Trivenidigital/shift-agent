import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "agents" / "flyer"))
from repair import build_repair_instruction

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
