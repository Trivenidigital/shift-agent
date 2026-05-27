"""Autonomous Flyer repair policy contracts."""
from __future__ import annotations

from pathlib import Path
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.recovery import classify_flyer_qa_for_autorepair  # noqa: E402
from agents.flyer import recovery as recovery_module  # noqa: E402
from schemas import FlyerLockedFact, FlyerProject  # noqa: E402


def _project() -> FlyerProject:
    return FlyerProject(
        project_id="F0105",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        original_message_id="wamid.F0105",
        raw_request="Create Special Biryani flyer with chicken and goat prices.",
        locked_facts=[
            FlyerLockedFact(
                fact_id="business_name",
                label="Business name",
                value="Lakshmi's Kitchen",
                source="customer_profile",
                required=True,
            ),
            FlyerLockedFact(
                fact_id="detail_001",
                label="Offer 1",
                value="Chicken biryani - $12.99",
                source="customer_text",
                required=True,
            ),
            FlyerLockedFact(
                fact_id="detail_002",
                label="Offer 2",
                value="Goat biryani - $14.99",
                source="customer_text",
                required=True,
            ),
        ],
    )


def test_autorepair_classifier_accepts_current_f0105_style_visible_fact_blockers():
    decision = classify_flyer_qa_for_autorepair(
        [
            "missing rendered fact: detail_002",
            "missing required visible fact: item:2:name",
            "instruction text leaked into flyer copy: DAILY THALI SPECIALS FLYER",
        ],
        _project(),
    )

    assert decision.decision == "hermes_plan_eligible"
    assert decision.reason == "qa_visible_copy_repairable"


def test_autorepair_classifier_hard_stops_trust_risks():
    for blocker in [
        "visible wrong business: Desi Chowrastha",
        "missing required visible fact: phone",
        "price mismatch: expected $14.99 saw $19.99",
    ]:
        decision = classify_flyer_qa_for_autorepair([blocker], _project())
        assert decision.decision == "hard_stop"
        assert decision.reason == "customer_trust_risk"


def test_hermes_default_model_resolves_from_hermes_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  default: nousresearch/hermes-4\n", encoding="utf-8")

    assert recovery_module.resolve_hermes_model("default_hermes_gateway", hermes_config_path=config) == "nousresearch/hermes-4"


def test_planner_rejects_instructions_that_mutate_trust_facts():
    assert recovery_module.repair_instruction_is_safe("Show each item once. Remove the extra footer title.")
    assert not recovery_module.repair_instruction_is_safe("Change the phone number to 555-1212.")
    assert not recovery_module.repair_instruction_is_safe("Replace Lakshmi's Kitchen with Desi Chowrastha.")
