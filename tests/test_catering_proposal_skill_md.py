from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "src" / "agents" / "catering" / "skills" / "creative_catering_proposals" / "SKILL.md"
DISPATCHER = REPO / "src" / "agents" / "catering" / "skills" / "catering_dispatcher" / "SKILL.md"
SHIFT = REPO / "src" / "agents" / "shift" / "skills" / "dispatch_shift_agent" / "SKILL.md"


def test_creative_skill_forbids_customer_pricing_and_send_message():
    text = SKILL.read_text(encoding="utf-8")
    assert "create-catering-proposal-options" in text
    assert "NEVER call send_message" in text
    assert "NEVER include prices" in text
    assert "payment" in text.lower()


def test_creative_skill_invokes_proposal_script_with_required_flags():
    text = SKILL.read_text(encoding="utf-8")
    assert "--lead-id" in text
    assert "--customer-jid" in text
    assert "--source-message-id" in text
    assert "--request-text" in text
    assert "--options-json -" in text


def test_catering_dispatcher_has_proposal_decision_matrix():
    text = DISPATCHER.read_text(encoding="utf-8")
    assert "creative_catering_proposals" in text
    assert "select-catering-proposal" in text
    assert "Owner reply path" in text


def test_shift_dispatcher_uses_active_lead_condition_not_global_option_keyword():
    text = SHIFT.read_text(encoding="utf-8")
    assert "active non-terminal catering lead" in text
    assert "proposal-selection" in text
    keyword_line = next(line for line in text.splitlines() if line.startswith("Catering keywords"))
    assert "`option`" not in keyword_line
    assert "`proposal`" not in keyword_line
