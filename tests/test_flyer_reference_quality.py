from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.reference_extract import _facts_from_text  # noqa: E402
from agents.flyer.render import _background_only_eligible, _integrated_poster_eligible, _needs_reference_extraction  # noqa: E402
from agents.flyer.visual_qa import _near_duplicate_item_blockers  # noqa: E402
from schemas import (  # noqa: E402
    FlyerAsset,
    FlyerLockedFact,
    FlyerProject,
    FlyerReferenceExtraction,
    FlyerRequestFields,
)


def _asset(tmp_path: Path, monkeypatch) -> FlyerAsset:
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    path = tmp_path / "reference.png"
    path.write_bytes(b"fake image bytes")
    return FlyerAsset(
        asset_id="A0001",
        kind="reference_image",
        source="whatsapp",
        path=str(path),
        mime_type="image/png",
        sha256="a" * 64,
        original_message_id="wamid.reference",
        received_at=datetime.now(timezone.utc),
    )


def _street_snack_text() -> str:
    return "\n".join(
        [
            "Lakshmi's Kitchen",
            "STREET SNACK SPECIALS",
            "EVERY TUESDAY NIGHT",
            "ANY 2 SNACKS",
            "$9.99",
            "Snack Picks",
            "- Punugulu",
            "- Egg Bonda",
            "- Aloo Bonda",
            "- Veg Lollipop",
            "- Cut Mirchi",
            "- Onion Mirchi",
            "- Mirchi Bhajji",
            "- Onion Pakora",
            "- Onion Samosa",
            "- Punjabi Samosa",
        ]
    )


def test_reference_extraction_materializes_two_column_snack_items(tmp_path, monkeypatch):
    facts = _facts_from_text(_street_snack_text(), asset=_asset(tmp_path, monkeypatch), source="reference_vision")
    by_id = {fact.fact_id: fact.value for fact in facts}
    item_values = [fact.value for fact in facts if fact.fact_id.endswith(":name")]

    assert by_id["campaign_title"] == "STREET SNACK SPECIALS"
    assert by_id["schedule"] == "EVERY TUESDAY NIGHT"
    assert by_id["pricing_structure"] == "ANY 2 SNACKS $9.99"
    assert item_values == [
        "Punugulu",
        "Egg Bonda",
        "Aloo Bonda",
        "Veg Lollipop",
        "Cut Mirchi",
        "Onion Mirchi",
        "Mirchi Bhajji",
        "Onion Pakora",
        "Onion Samosa",
        "Punjabi Samosa",
    ]


def test_style_only_reference_menu_with_materialized_facts_uses_overlay_not_integrated_poster(tmp_path, monkeypatch):
    asset = _asset(tmp_path, monkeypatch)
    facts = _facts_from_text(_street_snack_text(), asset=asset, source="reference_vision")
    project = FlyerProject(
        project_id="F0151",
        status="generating_concepts",
        customer_phone="+17329837841",
        customer_id="CUST0001",
        chat_id="201975216009469@lid",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        original_message_id="wamid.f0151",
        raw_request="Use as reference. Same flyer for Lakshmi's Kitchen, same content, Lakshmi's Kitchen theme.",
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen Menu",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+1 732 983 7841",
            preferred_language="en",
            notes="Customer chose path 2: use the source flyer only as a reference/inspiration.",
            style_preference="Lakshmi's Kitchen theme",
        ),
        assets=[asset],
        locked_facts=facts,
        reference_extractions=[
            FlyerReferenceExtraction(
                asset_id=asset.asset_id,
                role="inspiration",
                provider="sidecar",
                status="ok",
                extracted_facts=facts,
                extracted_at=datetime.now(timezone.utc),
            )
        ],
    )

    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setattr("agents.flyer.render._project_reference_assets", lambda _project: project.assets)

    assert _needs_reference_extraction(project) is False
    assert _integrated_poster_eligible(project) is False
    assert _background_only_eligible(project) is True


def test_visual_qa_blocks_near_duplicate_snack_item_typo():
    locked_facts = [
        FlyerLockedFact(
            fact_id=f"item:{idx}:name",
            label="Item",
            value=value,
            source="reference_vision",
            required=True,
        )
        for idx, value in enumerate(["Punugulu", "Onion Samosa", "Punjabi Samosa"])
    ]
    project = FlyerProject(
        project_id="F0151",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        original_message_id="wamid.f0151",
        raw_request="Street Snack Specials",
        fields=FlyerRequestFields(event_or_business_name="Street Snack Specials"),
        locked_facts=locked_facts,
    )

    blockers = _near_duplicate_item_blockers(
        project,
        "\n".join(["Punugulu", "Onion Samosa", "Punbi Samosa", "Punjabi Samosa"]),
    )

    assert blockers == ["near-duplicate item visible: expected Punjabi Samosa but saw Punbi Samosa"]
