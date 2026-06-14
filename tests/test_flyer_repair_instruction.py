import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "agents" / "flyer"))
from repair import build_repair_instruction

def test_includes_missing_and_removes_fabricated():
    blockers = ["missing required visible fact: contact_phone",
                "fabricated price visible: $3.99",
                "fabricated offer claim visible: Limited Time Deal"]
    locked = ["Lakshmi's Kitchen", "+1 732-983-7841", "Punugulu $6.99"]
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
    locked = ["Lakshmi's Kitchen", "+1 732-983-7841"]
    instr = build_repair_instruction(blockers, locked)
    assert "+1 732-983-7841" in instr
    # remove-clause still present but with no specific fabricated items
    assert "remove" in instr.lower()

def test_empty_blockers_still_returns_constraint():
    instr = build_repair_instruction([], ["Lakshmi's Kitchen", "$6.99"])
    assert "Lakshmi's Kitchen" in instr  # always re-states the locked contract
