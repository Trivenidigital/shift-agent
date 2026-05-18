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
