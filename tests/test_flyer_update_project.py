"""State-file contracts for Flyer Studio project updates."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "update-flyer-project"
PLATFORM = REPO / "src" / "platform"
SRC = REPO / "src"


class _NoopFileLock:
    def __init__(self, _path: Path) -> None:
        pass

    def __enter__(self) -> "_NoopFileLock":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _passed_preview_qa(
    *,
    extracted_text: str,
    project_version: int = 3,
    asset_id: str = "A0002",
    artifact_sha256: str = "b" * 64,
    output_format: str = "concept_preview",
    qa_source: str = "ocr_vision",
) -> dict:
    return {
        "project_id": "F9001",
        "asset_id": asset_id,
        "artifact_path": "C:/tmp/F9001-C1.png",
        "artifact_sha256": artifact_sha256,
        "project_version": project_version,
        "output_format": output_format,
        "provider": "test",
        "qa_source": qa_source,
        "status": "passed",
        "blockers": [],
        "warnings": [],
        "extracted_text": extracted_text,
        "checked_at": "2026-05-18T12:02:00Z",
        "severity": "pass",
    }


def _load_script(monkeypatch: pytest.MonkeyPatch):
    fake_safe_io = types.ModuleType("safe_io")
    fake_safe_io.FileLock = _NoopFileLock
    fake_safe_io.atomic_write_text = lambda path, text: Path(path).write_text(text, encoding="utf-8")
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)
    sys.path.insert(0, str(SRC))
    sys.path.insert(0, str(PLATFORM))
    module_name = "update_flyer_project_under_test"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(SCRIPT))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def _project_store_json(tmp_path: Path, *, status: str, raw_request: str = "Weekend breakfast specials") -> str:
    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    reference = tmp_path / "incoming" / "reference.png"
    preview = tmp_path / "previews" / "F9001-C1.png"
    final = tmp_path / "final" / "F9001.png"
    for path in (reference, preview, final):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-image-bytes")
    return json.dumps({
        "schema_version": 1,
        "next_sequence": 9002,
        "projects": [{
            "project_id": "F9001",
            "status": status,
            "customer_phone": "+17329837841",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "original_message_id": "m-original",
            "raw_request": raw_request,
            "fields": {
                "business_name": "Lakshmis Kitchen",
                "title": "Weekend Breakfast Specials",
                "schedule": "8 AM to 11 AM",
                "items": ["Idli $4.99", "Dosa $8.99"],
                "venue_or_location": "90 Brybar Dr",
                "contact_info": "+17329837841",
                "notes": "Reference flyer provided.",
            },
            "assets": [
                {
                    "asset_id": "A0001",
                    "kind": "reference_image",
                    "source": "whatsapp",
                    "path": str(reference),
                    "mime_type": "image/png",
                    "sha256": "a" * 64,
                    "original_message_id": "m-reference",
                    "received_at": now.isoformat(),
                },
                {
                    "asset_id": "A0002",
                    "kind": "concept_preview",
                    "source": "generated",
                    "path": str(preview),
                    "mime_type": "image/png",
                    "sha256": "b" * 64,
                    "original_message_id": "m-preview",
                    "received_at": now.isoformat(),
                },
                {
                    "asset_id": "A0003",
                    "kind": "final_whatsapp_image",
                    "source": "rendered",
                    "path": str(final),
                    "mime_type": "image/png",
                    "sha256": "c" * 64,
                    "original_message_id": "m-final",
                    "received_at": now.isoformat(),
                },
            ],
            "concepts": [{
                "concept_id": "C1",
                "title": "Best Design",
                "style_summary": "Premium Indian breakfast flyer",
                "preview_asset_id": "A0002",
                "created_at": now.isoformat(),
            }],
            "selected_concept_id": "C1",
            "revisions": [],
            "version": 3,
            "final_asset_ids": ["A0003"],
            "approved_message_id": "m-approve",
        }],
    })


def test_noop_revision_preserves_existing_project_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    original = _project_store_json(tmp_path, status="delivered")
    state_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", "The flyer is still not right.",
        "--message-id", "m-noop",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is True
    assert json.loads(state_path.read_text(encoding="utf-8")) == json.loads(original)


def test_visible_time_text_revision_does_not_request_clarification(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    state_path.write_text(
        _project_store_json(
            tmp_path,
            status="awaiting_final_approval",
            raw_request="Evening snacks flyer from 4 PM to 7 PM.",
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", "Time: 16:00 is duplicated. I'd like you to remove this.",
        "--message-id", "m-visible-time",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False
    assert 'Remove duplicate/extra time text "16:00"' in payload["revision_patch"]["notes_update"]

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "revising_design"
    assert persisted["selected_concept_id"] is None
    assert persisted["concepts"] == []
    assert persisted["revisions"][0]["request_text"] == "Time: 16:00 is duplicated. I'd like you to remove this."


def test_offer_text_revision_with_price_delta_rerenders_generated_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="awaiting_final_approval",
        raw_request="Create a flyer for Dosa specials. Pick Any 3    Dosa for $20.",
    ))
    store["projects"][0]["fields"]["notes"] = (
        "Pick Any 3 Dosa for $20. "
        "Items: Masala Dosa $8.99, Onion Dosa $8.99, Rava Dosa $8.99."
    )
    store["projects"][0]["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis Kitchen", "source": "customer_profile", "required": True},
        {"fact_id": "contact_phone", "label": "Contact", "value": "+17329837841", "source": "customer_profile", "required": True},
        {"fact_id": "location", "label": "Location", "value": "90 Brybar Dr", "source": "customer_profile", "required": True},
        {"fact_id": "offer:0", "label": "Offer", "value": "Pick Any 3 Dosa for $20", "source": "customer_text", "required": True},
    ]
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1.",
        "--message-id", "m-offer-price-edit",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "revising_design"
    assert persisted["concepts"] == []
    assert persisted["selected_concept_id"] is None
    assert persisted["final_asset_ids"] == []
    assert "Pick Any 4 Dosa for $21" in persisted["fields"]["notes"]
    assert "Pick Any 3 Dosa" not in persisted["fields"]["notes"]
    assert "Pick Any 4 Dosa for $21" in persisted["raw_request"]
    assert "Pick Any 3 Dosa" not in persisted["raw_request"]
    locked_values = [fact["value"] for fact in persisted["locked_facts"]]
    assert "Pick Any 4 Dosa for $21" in locked_values
    assert "Pick Any 3 Dosa for $20" not in locked_values
    assert persisted["revisions"][0]["request_text"] == "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1."


def test_contact_and_location_revision_updates_render_locked_facts(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="awaiting_final_approval",
        raw_request="Create a flyer for Dosa specials. Use saved contact and address.",
    ))
    store["projects"][0]["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis Kitchen", "source": "customer_profile", "required": True},
        {"fact_id": "contact_phone", "label": "Contact", "value": "+1 732 983 7841", "source": "customer_profile", "required": True},
        {"fact_id": "location", "label": "Location", "value": "90 Brybar Dr", "source": "customer_profile", "required": True},
    ]
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", "Change phone number to +1 980 200 5022. Change location to Lakshmi Hall.",
        "--message-id", "m-contact-location-edit",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["fields"]["contact_info"] == "+1 980 200 5022"
    assert persisted["fields"]["venue_or_location"] == "Lakshmi Hall"
    facts = {fact["fact_id"]: fact for fact in persisted["locked_facts"]}
    assert facts["contact_phone"]["value"] == "+1 980 200 5022"
    assert facts["contact_phone"]["source"] == "customer_text"
    assert facts["contact_phone"]["source_message_id"] == "m-contact-location-edit"
    assert facts["location"]["value"] == "Lakshmi Hall"
    assert facts["location"]["source"] == "customer_text"
    assert facts["location"]["source_message_id"] == "m-contact-location-edit"
    from schemas import FlyerProject
    from agents.flyer.render import collect_text_facts
    rendered_facts = {fact.fact_id: fact.text for fact in collect_text_facts(FlyerProject.model_validate(persisted))}
    assert rendered_facts["contact"] == "+1 980 200 5022"
    assert rendered_facts["location"] == "Lakshmi Hall"
    assert persisted["status"] == "revising_design"
    assert persisted["concepts"] == []


def test_pending_contact_revision_apply_updates_render_locked_fact(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="awaiting_final_approval",
        raw_request="Create a flyer for Dosa specials. Use saved contact.",
    ))
    now = "2026-05-18T12:00:00Z"
    store["projects"][0]["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis Kitchen", "source": "customer_profile", "required": True},
        {"fact_id": "contact_phone", "label": "Contact", "value": "+1 732 983 7841", "source": "customer_profile", "required": True},
    ]
    store["projects"][0]["revisions"] = [{
        "revision_id": "R001",
        "message_id": "m-pending-contact",
        "requested_at": now,
        "request_text": "Change phone number to +1 980 200 5022.",
        "applied": False,
    }]
    store["projects"][0]["pending_revision_confirmation"] = {
        "revision_id": "R001",
        "created_at": now,
        "expires_at": "2026-12-31T16:00:00Z",
        "request_message_id": "m-pending-contact",
        "request_text": "Change phone number to +1 980 200 5022.",
        "proposal_summary": "Applied: Contact '+1 732 983 7841' -> '+1 980 200 5022'.",
        "patch": {
            "field_updates": {"contact_info": "+1 980 200 5022"},
            "changed": True,
        },
    }
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", "APPLY R001",
        "--message-id", "m-apply-contact",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["fields"]["contact_info"] == "+1 980 200 5022"
    facts = {fact["fact_id"]: fact for fact in persisted["locked_facts"]}
    assert facts["contact_phone"]["value"] == "+1 980 200 5022"
    assert facts["contact_phone"]["source"] == "customer_text"
    assert facts["contact_phone"]["source_message_id"] == "m-pending-contact"
    from schemas import FlyerProject
    from agents.flyer.render import collect_text_facts
    rendered_facts = {fact.fact_id: fact.text for fact in collect_text_facts(FlyerProject.model_validate(persisted))}
    assert rendered_facts["contact"] == "+1 980 200 5022"
    assert persisted["pending_revision_confirmation"] is None
    assert persisted["revisions"][0]["applied"] is True


def test_offer_text_revision_updates_locked_offer_without_embedded_price(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="awaiting_final_approval",
        raw_request="Create a flyer for Dosa specials. Pick Any 3 Dosa for $20.",
    ))
    store["projects"][0]["fields"]["notes"] = "Pick Any 3 Dosa for $20."
    store["projects"][0]["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis Kitchen", "source": "customer_profile", "required": True},
        {"fact_id": "offer:0", "label": "Offer", "value": "Pick Any 3 Dosa", "source": "customer_text", "required": True},
    ]
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1.",
        "--message-id", "m-offer-no-price-edit",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    locked_values = [fact["value"] for fact in persisted["locked_facts"]]
    assert "Pick Any 4 Dosa" in locked_values
    assert "Pick Any 3 Dosa" not in locked_values
    assert "Pick Any 4 Dosa for $21" in persisted["fields"]["notes"]


def test_offer_text_revision_updates_whitespace_variant_locked_offer(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="awaiting_final_approval",
        raw_request="Create a flyer for Dosa specials. Pick Any 3 Dosa for $20.",
    ))
    store["projects"][0]["fields"]["notes"] = "Pick Any 3 Dosa for $20."
    store["projects"][0]["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis Kitchen", "source": "customer_profile", "required": True},
        {"fact_id": "offer:0", "label": "Offer", "value": "Pick Any 3    Dosa for $20", "source": "customer_text", "required": True},
    ]
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1.",
        "--message-id", "m-offer-space-edit",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    capsys.readouterr()

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    locked_values = [fact["value"] for fact in persisted["locked_facts"]]
    assert "Pick Any 4 Dosa for $21" in locked_values
    assert "Pick Any 3    Dosa for $20" not in locked_values


def test_text_revision_updates_short_whitespace_locked_fact_when_new_contains_old_wording(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="awaiting_final_approval",
        raw_request="Create a flyer for Happy Hour.",
    ))
    store["projects"][0]["fields"]["notes"] = "Happy Hour."
    store["projects"][0]["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis Kitchen", "source": "customer_profile", "required": True},
        {"fact_id": "offer:0", "label": "Offer", "value": "Happy    Hour", "source": "customer_text", "required": True},
    ]
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", 'Replace "Happy Hour" with "Happy Hour Special".',
        "--message-id", "m-happy-hour-edit",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    capsys.readouterr()

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    locked_values = [fact["value"] for fact in persisted["locked_facts"]]
    assert "Happy Hour Special" in locked_values
    assert "Happy    Hour" not in locked_values


def test_text_revision_fails_closed_when_locked_fact_has_repeated_old_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="awaiting_final_approval",
        raw_request="Create a flyer for Happy Hour.",
    ))
    store["projects"][0]["fields"]["notes"] = "Happy Hour."
    store["projects"][0]["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis Kitchen", "source": "customer_profile", "required": True},
        {"fact_id": "offer:0", "label": "Offer", "value": "Happy    Hour and Happy    Hour", "source": "customer_text", "required": True},
    ]
    original = json.dumps(store)
    state_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", 'Replace "Happy Hour" with "Happy Hour Special".',
        "--message-id", "m-happy-hour-repeat-edit",
        "--state-path", str(state_path),
    ])

    with pytest.raises(SystemExit, match="stale locked fact"):
        module.main()
    capsys.readouterr()

    assert json.loads(state_path.read_text(encoding="utf-8")) == json.loads(original)


def test_already_applied_text_revision_with_stale_visible_preview_regenerates(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="awaiting_final_approval",
        raw_request="Create a flyer for Happy Hour Special.",
    ))
    store["projects"][0]["fields"]["notes"] = "Happy Hour Special."
    store["projects"][0]["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis Kitchen", "source": "customer_profile", "required": True},
        {"fact_id": "offer:0", "label": "Offer", "value": "Happy Hour", "source": "customer_text", "required": True},
    ]
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", 'Replace "Happy Hour" with "Happy Hour Special".',
        "--message-id", "m-happy-hour-idempotent",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False
    assert payload["revision_patch"]["already_applied"] is True

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "revising_design"
    assert persisted["concepts"] == []
    assert persisted["selected_concept_id"] is None
    assert persisted["final_asset_ids"] == []
    assert persisted["version"] == 4
    locked_values = [fact["value"] for fact in persisted["locked_facts"]]
    assert "Happy Hour Special" in locked_values
    assert "Happy Hour" not in locked_values


def test_already_applied_text_revision_suppresses_regeneration_only_with_current_visible_qa(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="awaiting_final_approval",
        raw_request="Create a flyer for Happy Hour Special.",
    ))
    store["projects"][0]["fields"]["notes"] = "Happy Hour Special."
    store["projects"][0]["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis Kitchen", "source": "customer_profile", "required": True},
        {"fact_id": "offer:0", "label": "Offer", "value": "Happy Hour Special", "source": "customer_text", "required": True},
    ]
    store["projects"][0]["qa_reports"] = [
        _passed_preview_qa(extracted_text="Lakshmis Kitchen Happy Hour Special 90 Brybar Dr")
    ]
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", 'Replace "Happy Hour" with "Happy Hour Special".',
        "--message-id", "m-happy-hour-idempotent-current",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False
    assert payload["revision_patch"]["already_applied"] is True

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "awaiting_final_approval"
    assert persisted["concepts"][0]["concept_id"] == "C1"
    locked_values = [fact["value"] for fact in persisted["locked_facts"]]
    assert locked_values.count("Happy Hour Special") == 1
    assert "Happy Hour" not in locked_values


@pytest.mark.parametrize(
    "qa_override",
    [
        {"artifact_sha256": "d" * 64},
        {"output_format": "final_whatsapp_image"},
        {"qa_source": "sidecar_test"},
    ],
)
def test_already_applied_text_revision_ignores_invalid_visible_qa_proof(tmp_path, monkeypatch, capsys, qa_override):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="awaiting_final_approval",
        raw_request="Create a flyer for Happy Hour Special.",
    ))
    store["projects"][0]["fields"]["notes"] = "Happy Hour Special."
    store["projects"][0]["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis Kitchen", "source": "customer_profile", "required": True},
        {"fact_id": "offer:0", "label": "Offer", "value": "Happy Hour", "source": "customer_text", "required": True},
    ]
    store["projects"][0]["qa_reports"] = [
        _passed_preview_qa(
            extracted_text="Lakshmis Kitchen Happy Hour Special 90 Brybar Dr",
            **qa_override,
        )
    ]
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", 'Replace "Happy Hour" with "Happy Hour Special".',
        "--message-id", "m-happy-hour-invalid-visible-proof",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False
    assert payload["revision_patch"]["already_applied"] is True

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "revising_design"
    assert persisted["concepts"] == []
    assert persisted["selected_concept_id"] is None
    assert persisted["final_asset_ids"] == []
    assert persisted["version"] == 4
    locked_values = [fact["value"] for fact in persisted["locked_facts"]]
    assert "Happy Hour Special" in locked_values
    assert "Happy Hour" not in locked_values


def test_already_applied_text_revision_with_current_visible_qa_refreshes_stale_locked_fact_without_regeneration(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="awaiting_final_approval",
        raw_request="Create a flyer for Happy Hour Special.",
    ))
    store["projects"][0]["fields"]["notes"] = "Happy Hour Special."
    store["projects"][0]["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis Kitchen", "source": "customer_profile", "required": True},
        {"fact_id": "offer:0", "label": "Offer", "value": "Happy Hour", "source": "customer_text", "required": True},
    ]
    store["projects"][0]["qa_reports"] = [
        _passed_preview_qa(extracted_text="Lakshmis Kitchen Happy Hour Special 90 Brybar Dr")
    ]
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", 'Replace "Happy Hour" with "Happy Hour Special".',
        "--message-id", "m-happy-hour-idempotent-visible-current",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False
    assert payload["revision_patch"]["already_applied"] is True

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "awaiting_final_approval"
    assert persisted["concepts"][0]["concept_id"] == "C1"
    assert persisted["selected_concept_id"] == "C1"
    assert persisted["final_asset_ids"] == ["A0003"]
    locked_values = [fact["value"] for fact in persisted["locked_facts"]]
    assert "Happy Hour Special" in locked_values
    assert "Happy Hour" not in locked_values


def test_day_range_revision_does_not_corrupt_existing_notes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    state_path.write_text(
        _project_store_json(
            tmp_path,
            status="awaiting_final_approval",
            raw_request=(
                "Create a professional flyer for MK kitchen. Evening snacks from 4 PM to 7 PM, "
                "Wednesday to Saturday. Include samosa, mirchi bajji, punugulu, masala vada, and tea."
            ),
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", (
            "Can you add the prices and make changes to the backdrop. "
            "Also change it to Tuesday to Sunday"
        ),
        "--message-id", "m-day-range",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    text = persisted["raw_request"] + " " + persisted["fields"]["notes"]
    assert "Use schedule Tuesday to Sunday" in text
    assert "Do not use Wednesday to Saturday" in text
    assert "MK kitchen" in text
    assert "kTuesday to Sundaychen" not in text
    assert persisted["fields"]["venue_or_location"] == "90 Brybar Dr"


def test_source_artwork_followup_stays_in_manual_edit_queue(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    state_path.write_text(
        _project_store_json(
            tmp_path,
            status="manual_edit_required",
            raw_request="Edit uploaded flyer/source artwork. Preserve the source flyer.",
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", "Remove extra 08:00 and add Any Item for $9.99.",
        "--message-id", "m-followup",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["selected_concept_id"] == "C1"
    assert persisted["concepts"][0]["concept_id"] == "C1"
    assert persisted["final_asset_ids"] == ["A0003"]
    assert "Remove extra 08:00" in persisted["raw_request"]
    assert persisted["revisions"][0]["request_text"] == "Remove extra 08:00 and add Any Item for $9.99."


def test_offer_text_revision_on_manual_source_edit_stays_in_manual_queue(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="manual_edit_required",
        raw_request="Edit uploaded flyer/source artwork. Preserve the source flyer.",
    ))
    store["projects"][0]["fields"]["notes"] = "Pick Any 3 Dosa for $20."
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1.",
        "--message-id", "m-source-offer-price-edit",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["selected_concept_id"] == "C1"
    assert persisted["concepts"][0]["concept_id"] == "C1"
    assert persisted["final_asset_ids"] == ["A0003"]
    assert persisted["fields"]["notes"] == "Pick Any 3 Dosa for $20."
    assert "Pick any 3 Dosa -> Pick Any 4 Dosa" in persisted["raw_request"]
    assert persisted["revisions"][0]["request_text"] == "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1."


def test_offer_text_revision_on_non_source_manual_row_revises_deterministically(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(
        tmp_path,
        status="manual_edit_required",
        raw_request="Create a Dosa special flyer for Lakshmis Kitchen.",
    ))
    project = store["projects"][0]
    project["fields"]["notes"] = "Pick Any 3 Dosa for $20."
    project["manual_review"] = {
        "status": "queued",
        "reason": "visual_qa_failed",
        "reason_code": "visual_qa_failed",
        "detail": "required fact missing in previous render",
        "queued_at": "2026-05-18T12:05:00Z",
    }
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1.",
        "--message-id", "m-non-source-manual-offer-price-edit",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "revising_design"
    assert persisted["concepts"] == []
    assert persisted["selected_concept_id"] is None
    assert persisted["final_asset_ids"] == []
    assert persisted["fields"]["notes"] == "Pick Any 4 Dosa for $21."
    assert "Latest correction:" not in persisted["raw_request"]
    assert persisted["revisions"][0]["request_text"] == "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1."


def test_queue_manual_review_marks_manual_project_completable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    state_path.write_text(
        _project_store_json(
            tmp_path,
            status="manual_edit_required",
            raw_request="Edit uploaded flyer/source artwork. Preserve the source flyer.",
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--queue-manual-review",
        "--manual-reason", "source_edit_provider_unavailable",
        "--manual-detail", "source edit provider is not configured",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    capsys.readouterr()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["manual_review"]["status"] == "queued"
    assert persisted["manual_review"]["reason"] == "source_edit_provider_unavailable"
    assert persisted["manual_review"]["detail"] == "source edit provider is not configured"


def test_source_artwork_followup_after_preview_requires_regeneration(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    state_path.write_text(
        _project_store_json(
            tmp_path,
            status="awaiting_final_approval",
            raw_request="Edit uploaded flyer/source artwork. Preserve the source flyer.",
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", "Remove extra 08:00 and add Any Item for $9.99.",
        "--message-id", "m-followup-after-preview",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "revising_design"
    assert persisted["concepts"] == []
    assert persisted["selected_concept_id"] is None
    assert persisted["final_asset_ids"] == []
    assert "Remove extra 08:00" in persisted["raw_request"]
    assert persisted["revisions"][0]["applied"] is False


def test_approval_applies_superseded_revisions_but_preserves_pending_boundaries(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(tmp_path, status="awaiting_final_approval"))
    project = store["projects"][0]
    project["version"] = 3
    project["revisions"] = [
        {
            "revision_id": "R001",
            "message_id": "m-edit-1",
            "requested_at": "2026-05-27T11:20:30.674256Z",
            "request_text": "show me some template ideas",
            "applied": False,
            "resulting_version": 2,
        },
        {
            "revision_id": "R002",
            "message_id": "m-edit-2",
            "requested_at": "2026-05-27T11:21:14.928670Z",
            "request_text": "show me some template ideas",
            "applied": False,
            "resulting_version": 3,
        },
        {
            "revision_id": "R003",
            "message_id": "m-edit-3",
            "requested_at": "2026-05-27T11:22:14.928670Z",
            "request_text": "change footer before regenerating",
            "applied": False,
            "resulting_version": None,
        },
        {
            "revision_id": "R004",
            "message_id": "m-edit-4",
            "requested_at": "2026-05-27T11:23:14.928670Z",
            "request_text": "future concurrent revision",
            "applied": False,
            "resulting_version": 4,
        },
    ]
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--approve-message-id", "m-approve-retry",
        "--state-path", str(state_path),
    ])

    with pytest.raises(SystemExit, match="cannot approve with unapplied revisions: R003,R004"):
        module.main()
    capsys.readouterr()

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    revisions = {revision["revision_id"]: revision for revision in persisted["revisions"]}
    assert persisted["status"] == "awaiting_final_approval"
    assert revisions["R001"]["applied"] is True
    assert revisions["R002"]["applied"] is True
    assert revisions["R003"]["applied"] is False
    assert revisions["R004"]["applied"] is False


def test_approval_succeeds_after_only_superseded_revisions_remain(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    store = json.loads(_project_store_json(tmp_path, status="awaiting_final_approval"))
    project = store["projects"][0]
    project["version"] = 3
    project["revisions"] = [
        {
            "revision_id": "R001",
            "message_id": "m-edit-1",
            "requested_at": "2026-05-27T11:20:30.674256Z",
            "request_text": "show me some template ideas",
            "applied": False,
            "resulting_version": 2,
        },
        {
            "revision_id": "R002",
            "message_id": "m-edit-2",
            "requested_at": "2026-05-27T11:21:14.928670Z",
            "request_text": "show me some template ideas",
            "applied": False,
            "resulting_version": 3,
        },
    ]
    state_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--approve-message-id", "m-approve-retry",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    revisions = {revision["revision_id"]: revision for revision in persisted["revisions"]}

    assert payload["project_id"] == "F9001"
    assert persisted["status"] == "finalizing_assets"
    assert persisted["approved_message_id"] == "m-approve-retry"
    assert revisions["R001"]["applied"] is True
    assert revisions["R002"]["applied"] is True


def test_source_artwork_followup_keeps_raw_request_within_schema_limit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    long_raw = "Edit uploaded flyer/source artwork. Preserve the source flyer. " + ("Original text. " * 130)
    state_path.write_text(
        _project_store_json(
            tmp_path,
            status="manual_edit_required",
            raw_request=long_raw[:1995],
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", [
        "update-flyer-project",
        "--project-id", "F9001",
        "--revision-text", "Remove extra 08:00 and add Any Item for $9.99.",
        "--message-id", "m-long-followup",
        "--state-path", str(state_path),
    ])

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["revision_requires_clarification"] is False

    reloaded = module.FlyerProjectStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert len(reloaded.projects[0].raw_request) <= 2000
    assert "Latest correction:" in reloaded.projects[0].raw_request
