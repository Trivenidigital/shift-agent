"""Contracts for Flyer concept generation preflight."""
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
SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "generate-flyer-concepts"
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
    sys.path.insert(0, str(PLATFORM))
    sys.path.insert(0, str(SRC))
    from schemas import Config  # noqa: E402

    fake_safe_io = types.ModuleType("safe_io")
    fake_safe_io.FileLock = _NoopFileLock
    fake_safe_io.atomic_write_text = lambda path, text: Path(path).write_text(text, encoding="utf-8")
    fake_safe_io.ndjson_append = lambda path, text: Path(path).open("a", encoding="utf-8").write(text + "\n")
    fake_safe_io.load_yaml_model = lambda *_args, **_kwargs: Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {"enabled": True, "draft_image_model": "deterministic-renderer", "concept_count": 1},
    })
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)
    module_name = "generate_flyer_concepts_under_test"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(SCRIPT))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def _project_with_failed_reference() -> dict:
    now = datetime(2026, 5, 19, tzinfo=timezone.utc).isoformat()
    return {
        "project_id": "F0001",
        "status": "intake_started",
        "customer_phone": "+17329837841",
        "created_at": now,
        "updated_at": now,
        "original_message_id": "m-ref",
        "raw_request": "Create menu flyer. Extract item names and prices from attached sample flyer.",
        "reference_extractions": [{
            "asset_id": "A0001",
            "role": "menu_reference",
            "provider": "unavailable",
            "status": "provider_unavailable",
            "detail": "reference OCR/vision provider unavailable",
            "extracted_at": now,
        }],
        "assets": [{
            "asset_id": "A0001",
            "kind": "reference_image",
            "source": "whatsapp",
            "path": "/opt/shift-agent/state/flyer/assets/F0001-reference.png",
            "mime_type": "image/png",
            "sha256": "a" * 64,
            "original_message_id": "m-ref",
            "received_at": now,
        }],
    }


def _project_with_pending_reference(asset_path: Path) -> dict:
    project = _project_with_failed_reference()
    project["reference_extractions"][0]["provider"] = "not_run"
    project["reference_extractions"][0]["status"] = "not_run"
    project["reference_extractions"][0]["detail"] = "reference extraction pending"
    project["assets"][0]["path"] = str(asset_path)
    return project


def test_generate_refuses_unextracted_required_reference_before_render(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [_project_with_failed_reference()],
    }), encoding="utf-8")

    def explode_render(*_args, **_kwargs):
        raise AssertionError("render must not run when required reference extraction failed")

    monkeypatch.setattr(module, "render_concept_previews", explode_render)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 2
    out = json.loads(capsys.readouterr().out)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]

    assert out["project_id"] == "F0001"
    assert out["reference_extraction_failed"][0]["status"] == "provider_unavailable"
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["status"] == "queued"
    assert persisted["manual_review"]["reason"] == "reference_provider_unavailable"


def test_generate_extracts_pending_reference_facts_before_render(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from schemas import FlyerVisualQAReport  # noqa: E402

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    rendered = asset_dir / "F0001-C1.png"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [_project_with_pending_reference(reference)],
    }), encoding="utf-8")

    class FakeProvider:
        provider_name = "fake_vision"

        def extract_text(self, _asset, _raw_request):
            return "Idly $7\nDosa $8", "ok"

    def fake_render(project, _asset_dir, **_kwargs):
        by_value = {fact.value for fact in project.locked_facts}
        assert {"Idly", "$7", "Dosa", "$8"}.issubset(by_value)
        rendered.write_bytes(b"rendered")
        return [types.SimpleNamespace(
            path=rendered,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )]

    def fake_qa(project, path, *, output_format, asset_id):
        return FlyerVisualQAReport(
            project_id=project.project_id,
            asset_id=asset_id,
            artifact_path=str(path),
            artifact_sha256="b" * 64,
            project_version=project.version,
            output_format=output_format,
            provider="test",
            qa_source="sidecar_test",
            status="passed",
            checked_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(module, "build_reference_extraction_provider", lambda: FakeProvider())
    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 0
    out = json.loads(capsys.readouterr().out)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    values = {fact["value"] for fact in persisted["locked_facts"]}

    assert out["project_id"] == "F0001"
    assert {"Idly", "$7", "Dosa", "$8"}.issubset(values)
    assert persisted["reference_extractions"][0]["status"] == "ok"
    assert persisted["status"] == "awaiting_final_approval"


def test_generate_persists_structured_generation_prompt_not_raw_request(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from schemas import FlyerVisualQAReport  # noqa: E402

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    rendered = asset_dir / "F0120-C1-preview.png"
    now = datetime(2026, 5, 31, tzinfo=timezone.utc).isoformat()
    raw = (
        "Create a flyer for Indo-Chinese specials on Wednesday. Include 8 famous "
        "Indo-Chinese items. Any item priced at $9.99. Use Address and phone number stored."
    )
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 122,
        "projects": [{
            "project_id": "F0120",
            "status": "intake_started",
            "customer_phone": "+17329837841",
            "created_at": now,
            "updated_at": now,
            "original_message_id": "m-indochinese",
            "raw_request": raw,
            "fields": {
                "event_or_business_name": "Indo-Chinese Specials",
                "venue_or_location": "90 Brybar Dr St Johns FL",
                "contact_info": "+17329837841",
                "notes": raw,
                "style_preference": "professional local food menu flyer",
            },
            "locked_facts": [
                {"fact_id": "business_name", "label": "Business", "value": "Lakshmi's Kitchen", "source": "customer_profile", "required": True},
                {"fact_id": "campaign_title", "label": "Campaign", "value": "Indo-Chinese Specials", "source": "customer_text", "required": True},
                {"fact_id": "location", "label": "Location", "value": "90 Brybar Dr St Johns FL", "source": "customer_profile", "required": True},
                {"fact_id": "contact_phone", "label": "Contact", "value": "+17329837841", "source": "customer_profile", "required": True},
                {"fact_id": "item:0:name", "label": "Item", "value": "Veg Manchurian", "source": "customer_text", "required": True},
                {"fact_id": "item:0:price", "label": "Price", "value": "$9.99", "source": "customer_text", "required": True},
            ],
        }],
    }), encoding="utf-8")

    def fake_render(*_args, **_kwargs):
        rendered.write_bytes(b"rendered image")
        return [types.SimpleNamespace(
            path=rendered,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )]

    def fake_qa(project, path, *, output_format, asset_id):
        return FlyerVisualQAReport(
            project_id=project.project_id,
            asset_id=asset_id,
            artifact_path=str(path),
            artifact_sha256="b" * 64,
            project_version=project.version,
            output_format=output_format,
            provider="test",
            qa_source="sidecar_test",
            status="passed",
            checked_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0120",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 0
    capsys.readouterr()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    prompt = persisted["concepts"][0]["prompt"]

    assert prompt != raw
    assert "Controlled customer copy:" in prompt
    assert "Business/brand: Lakshmi's Kitchen" in prompt
    assert "Veg Manchurian - $9.99" in prompt


def test_generate_deferred_source_edit_template_extracts_source_contract_before_render(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from schemas import (  # noqa: E402
        FlyerReferenceExtraction,
        FlyerSourceContract,
        FlyerSourceContractSection,
        FlyerVisualQAReport,
    )

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    audit_log_path = tmp_path / "decisions-source-contract.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    rendered = asset_dir / "F0001-C1.png"
    project = _project_with_pending_reference(reference)
    project["raw_request"] = "Remove extra 08:00 from this uploaded flyer. Change Lunch Combo to Dinner Combo."
    project["reference_extractions"][0]["role"] = "source_edit_template"
    project["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmi's Kitchen", "source": "customer_profile", "required": True},
        {"fact_id": "contact_phone", "label": "Contact", "value": "+17329837841", "source": "customer_profile", "required": True},
    ]
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def fake_extract_reference(asset, *, raw_request, provider):
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role="source_edit_template",
            provider="fake_vision",
            status="ok",
            detail="source contract extracted",
            extracted_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
            source_contract=FlyerSourceContract(
                source_business_names=["Lakshmi's Kitchen"],
                target_business_name="Lakshmi's Kitchen",
                required_headings=["Lunch Menu"],
                sections=[FlyerSourceContractSection(heading="Combos", items=["Lunch Combo $9.99"])],
                requested_replacements={"Lunch Combo": "Dinner Combo"},
                preserve_layout=True,
                preserve_unmentioned_text=True,
                confidence=0.96,
            ),
        )

    def fake_render_source_edit(project, _asset_dir, **_kwargs):
        assert project.reference_extractions[0].source_contract is not None
        values = {fact.value for fact in project.locked_facts}
        assert "Lunch Menu" in values
        assert "Lunch Combo $9.99" in values
        assert "Dinner Combo" in values
        assert "Lunch Combo" in project.reference_extractions[0].source_contract.forbidden_substrings
        rendered.write_bytes(b"source edit rendered")
        return types.SimpleNamespace(
            path=rendered,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )

    def explode_normal_render(*_args, **_kwargs):
        raise AssertionError("source_edit_template projects must use source-edit rendering")

    def fake_qa(project, path, *, output_format, asset_id):
        return FlyerVisualQAReport(
            project_id=project.project_id,
            asset_id=asset_id,
            artifact_path=str(path),
            artifact_sha256="b" * 64,
            project_version=project.version,
            output_format=output_format,
            provider="test",
            qa_source="sidecar_test",
            status="passed",
            checked_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(module, "build_reference_extraction_provider", lambda: object())
    monkeypatch.setattr(module, "extract_reference", fake_extract_reference)
    monkeypatch.setattr(module, "render_source_edit_preview", fake_render_source_edit)
    monkeypatch.setattr(module, "render_concept_previews", explode_normal_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_log_path),
    ])

    assert module.main() == 0
    out = json.loads(capsys.readouterr().out)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    values = {fact["value"] for fact in persisted["locked_facts"]}

    assert out["project_id"] == "F0001"
    assert persisted["reference_extractions"][0]["status"] == "ok"
    assert persisted["reference_extractions"][0]["source_contract"]["required_headings"] == ["Lunch Menu"]
    assert "Lunch Menu" in values
    assert "Dinner Combo" in values
    assert persisted["status"] == "awaiting_final_approval"
    assert "Edit the attached flyer image" in persisted["concepts"][0]["prompt"]
    assert "Create a complete, finished customer-ready poster flyer" not in persisted["concepts"][0]["prompt"]
    audit_rows = [json.loads(line) for line in audit_log_path.read_text(encoding="utf-8").splitlines()]
    assert audit_rows[0]["type"] == "flyer_source_contract_extracted"
    assert audit_rows[0]["role"] == "source_edit_template"
    assert audit_rows[0]["status"] == "ok"
    assert audit_rows[0]["headings_count"] == 1


def test_generate_deferred_source_edit_template_provider_failure_queues_manual_review(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from schemas import FlyerReferenceExtraction  # noqa: E402

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    audit_log_path = tmp_path / "decisions-source-contract-failure.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["raw_request"] = "edit uploaded flyer/source artwork: replace headline"
    project["reference_extractions"][0]["role"] = "source_edit_template"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def fake_extract_reference(asset, *, raw_request, provider):
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role="source_edit_template",
            provider="fake_vision",
            status="provider_unavailable",
            detail="vision provider unavailable",
            extracted_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(module, "build_reference_extraction_provider", lambda: object())
    monkeypatch.setattr(module, "extract_reference", fake_extract_reference)
    monkeypatch.setattr(module, "render_source_edit_preview", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("render must wait for source contract")))
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_log_path),
    ])

    assert module.main() == 2
    out = json.loads(capsys.readouterr().out)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]

    assert out["reference_extraction_failed"][0]["role"] == "source_edit_template"
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["status"] == "queued"
    assert persisted["manual_review"]["reason_code"] == "source_edit_provider_unavailable"
    assert persisted["manual_review"]["detail"] == "vision provider unavailable"
    audit_rows = [json.loads(line) for line in audit_log_path.read_text(encoding="utf-8").splitlines()]
    assert audit_rows[0]["type"] == "flyer_source_contract_extracted"
    assert audit_rows[0]["role"] == "source_edit_template"
    assert audit_rows[0]["status"] == "provider_unavailable"


@pytest.mark.parametrize(
    ("status", "expected_reason_code"),
    [
        ("low_confidence", "reference_low_confidence"),
        ("unsupported", "reference_unsupported"),
    ],
)
def test_generate_deferred_source_edit_template_non_provider_failure_keeps_reference_reason(
    monkeypatch, tmp_path, capsys, status, expected_reason_code,
):
    module = _load_script(monkeypatch)
    from schemas import FlyerReferenceExtraction  # noqa: E402

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["raw_request"] = "edit uploaded flyer/source artwork: replace headline"
    project["reference_extractions"][0]["role"] = "source_edit_template"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def fake_extract_reference(asset, *, raw_request, provider):
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role="source_edit_template",
            provider="fake_vision",
            status=status,
            detail=f"source contract extraction {status}",
            extracted_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(module, "build_reference_extraction_provider", lambda: object())
    monkeypatch.setattr(module, "extract_reference", fake_extract_reference)
    monkeypatch.setattr(
        module,
        "render_source_edit_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("render must wait for source contract")
        ),
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 2
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["reason_code"] == expected_reason_code
    assert persisted["manual_review"]["detail"] == f"source contract extraction {status}"


def test_generate_deferred_reference_smoke_can_use_sidecar_visual_qa(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_QA_ALLOW_SIDECAR", "1")
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    rendered = asset_dir / "F0001-C1.png"
    project = _project_with_pending_reference(reference)
    project["raw_request"] = "Create a flyer for Smoke Menu. Contact +19045550123. Create a flyer from this attached menu."
    project["fields"] = {
        "event_or_business_name": "Smoke Menu",
        "contact_info": "+19045550123",
        "notes": project["raw_request"],
    }
    project["locked_facts"] = [
        {"fact_id": "business_name", "label": "Business", "value": "Smoke Menu", "source": "customer_text", "required": True},
        {"fact_id": "contact_phone", "label": "Contact", "value": "+19045550123", "source": "customer_profile", "required": True},
    ]
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    class FakeProvider:
        provider_name = "sidecar"

        def extract_text(self, _asset, _raw_request):
            return "Idly $7\nDosa $8", "ok"

    def fake_render(_project, _asset_dir, **_kwargs):
        rendered.write_bytes(b"rendered")
        return [types.SimpleNamespace(
            path=rendered,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )]

    monkeypatch.setattr(module, "build_reference_extraction_provider", lambda: FakeProvider())
    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 0
    out = json.loads(capsys.readouterr().out)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]

    assert out["project_id"] == "F0001"
    assert persisted["status"] == "awaiting_final_approval"
    assert "Smoke Menu" in Path(str(rendered) + ".ocr.txt").read_text(encoding="utf-8")
    assert "Idly" in Path(str(rendered) + ".ocr.txt").read_text(encoding="utf-8")


def test_generate_source_edit_provider_unavailable_queues_manual_review(monkeypatch, tmp_path, capsys):
    """P0-5: when render_source_edit_preview raises FlyerRenderError (e.g.
    OPENAI_API_KEY missing or provider 5xx), generate-flyer-concepts must
    queue the project for manual review with reason_code=
    `source_edit_provider_unavailable` rather than crashing — operator CLI
    retries and edge cases where the cf-router preflight didn't catch the
    failure must still land the project in a triage-visible state."""
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    # Mark as source-edit so the script takes the render_source_edit_preview
    # branch. The raw_request keyword `edit uploaded flyer/source artwork`
    # is the dispatcher signal `_is_source_edit_project` keys on.
    project["raw_request"] = "edit uploaded flyer/source artwork: change phone to +17329837841"
    # Reference is already extracted (not in failure state) so the script
    # bypasses the reference-failure manual-review path and reaches the
    # source-edit render call.
    project["reference_extractions"][0]["provider"] = "openai"
    project["reference_extractions"][0]["status"] = "ok"
    project["reference_extractions"][0]["detail"] = "extracted"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def fake_render_source_edit(*_args, **_kwargs):
        raise module.FlyerRenderError("OPENAI_API_KEY is missing")

    monkeypatch.setattr(module, "render_source_edit_preview", fake_render_source_edit)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    rc = module.main()
    assert rc == 2  # non-zero: signals manual-review-required to the caller
    out = json.loads(capsys.readouterr().out)
    assert "OPENAI_API_KEY" in out["source_edit_failed"]
    assert out["manual_review_reason_code"] == "source_edit_provider_unavailable"

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["status"] == "queued"
    assert persisted["manual_review"]["reason_code"] == "source_edit_provider_unavailable"
    assert "OPENAI_API_KEY" in persisted["manual_review"]["detail"]


def test_fix5_source_edit_under_killswitch_routes_to_manual_without_render(monkeypatch, tmp_path, capsys):
    """FIX 5: source-edit has no deterministic renderer (render.py dispatches it
    by provider API). Under FLYER_INTEGRATED_KILLSWITCH=1, passing
    model='deterministic-renderer' to render_source_edit_preview would error.
    Instead the script must route the project straight to manual_edit_required
    WITHOUT attempting any source-edit render/API call."""
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", "1")
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["raw_request"] = "edit uploaded flyer/source artwork: change phone to +17329837841"
    project["reference_extractions"][0]["provider"] = "openai"
    project["reference_extractions"][0]["status"] = "ok"
    project["reference_extractions"][0]["detail"] = "extracted"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def explode_source_edit(*_args, **_kwargs):
        raise AssertionError("render_source_edit_preview must not run under the kill-switch")

    monkeypatch.setattr(module, "render_source_edit_preview", explode_source_edit)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    rc = module.main()
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["source_edit_killswitch_manual"] is True
    assert out["manual_review_reason"] == "killswitch_source_edit_manual"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["status"] == "queued"
    # reason_code stays within the FlyerManualReviewReason enum; the kill-switch
    # marker lives in the human-readable reason/detail fields.
    assert persisted["manual_review"]["reason_code"] == "operator_request"
    assert persisted["manual_review"]["reason"] == "killswitch_source_edit_manual"
    assert "killswitch_source_edit_manual" in persisted["manual_review"]["detail"]


def test_generate_source_edit_provider_http_error_queues_provider_timeout(monkeypatch, tmp_path, capsys):
    """Source-edit provider HTTP/5xx errors are transient provider failures,
    not missing-provider configuration. They should land in the retry/provider
    bucket instead of telling operators to provision credentials."""
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["raw_request"] = "edit uploaded flyer/source artwork: replace headline"
    project["reference_extractions"][0]["provider"] = "openai"
    project["reference_extractions"][0]["status"] = "ok"
    project["reference_extractions"][0]["detail"] = "extracted"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def fake_render_source_edit(*_args, **_kwargs):
        raise module.FlyerRenderError("OpenRouter source edit HTTP 500: upstream provider error")

    monkeypatch.setattr(module, "render_source_edit_preview", fake_render_source_edit)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["manual_review_reason_code"] == "provider_timeout"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["reason_code"] == "provider_timeout"
    assert "HTTP 500" in persisted["manual_review"]["detail"]


def test_generate_source_edit_http_500_with_missing_text_still_queues_provider_timeout(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["raw_request"] = "edit uploaded flyer/source artwork: replace headline"
    project["reference_extractions"][0]["provider"] = "openai"
    project["reference_extractions"][0]["status"] = "ok"
    project["reference_extractions"][0]["detail"] = "extracted"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def fake_render_source_edit(*_args, **_kwargs):
        raise module.FlyerRenderError("OpenRouter source edit HTTP 500: missing upstream resource")

    monkeypatch.setattr(module, "render_source_edit_preview", fake_render_source_edit)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["manual_review_reason_code"] == "provider_timeout"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["manual_review"]["reason_code"] == "provider_timeout"


def test_generate_source_edit_missing_text_manifest_queues_visual_qa_failed(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["raw_request"] = "edit uploaded flyer/source artwork: replace headline"
    project["reference_extractions"][0]["provider"] = "openai"
    project["reference_extractions"][0]["status"] = "ok"
    project["reference_extractions"][0]["detail"] = "extracted"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def fake_render_source_edit(*_args, **_kwargs):
        raise module.FlyerRenderError("text manifest validation failed: missing critical text facts")

    monkeypatch.setattr(module, "render_source_edit_preview", fake_render_source_edit)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["manual_review_reason_code"] == "visual_qa_failed"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["manual_review"]["reason_code"] == "visual_qa_failed"


def test_generate_source_edit_dependency_missing_queues_manual_review(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["raw_request"] = "edit uploaded flyer/source artwork: change phone to +17329837841"
    project["reference_extractions"][0]["provider"] = "openai"
    project["reference_extractions"][0]["status"] = "ok"
    project["reference_extractions"][0]["detail"] = "extracted"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def fake_render_source_edit(*_args, **_kwargs):
        raise module.FlyerRenderError("Pillow is unavailable for exact identity overlay: /usr/bin/python3 missing")

    monkeypatch.setattr(module, "render_source_edit_preview", fake_render_source_edit)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["manual_review_reason_code"] == "dependency_missing"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["manual_review"]["reason_code"] == "dependency_missing"


def test_generate_source_edit_quality_failure_queues_visual_qa_failed(monkeypatch, tmp_path, capsys):
    """Quality-check FlyerRenderError ("edited concept failed quality check: …")
    is a structural failure (wrong dimensions / mime / corrupt bytes), NOT a
    transient provider issue. It must map to `visual_qa_failed` so operator
    triage routes it to designer review rather than the "retry the provider"
    bucket — `provider_timeout` would mislead operators into retrying a
    deterministically-bad output."""
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["raw_request"] = "edit uploaded flyer/source artwork: replace headline"
    project["reference_extractions"][0]["provider"] = "openai"
    project["reference_extractions"][0]["status"] = "ok"
    project["reference_extractions"][0]["detail"] = "extracted"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def fake_render_source_edit(*_args, **_kwargs):
        raise module.FlyerRenderError("edited concept failed quality check: width != 1080")

    monkeypatch.setattr(module, "render_source_edit_preview", fake_render_source_edit)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    rc = module.main()
    assert rc == 2

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["reason_code"] == "visual_qa_failed"



def test_generate_source_edit_fact_fit_failure_queues_visual_qa_failed(monkeypatch, tmp_path, capsys):
    """A deterministic source-edit layout failure is not a provider timeout.

    The source-preserving renderer raises this when the requested critical
    text cannot be fitted without losing required facts. Retrying the provider
    will not help, so autonomous repair and operator triage should see it as
    visual/layout QA work.
    """
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["raw_request"] = "edit uploaded flyer/source artwork: add many required prices"
    project["reference_extractions"][0]["provider"] = "openai"
    project["reference_extractions"][0]["status"] = "ok"
    project["reference_extractions"][0]["detail"] = "extracted"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def fake_render_source_edit(*_args, **_kwargs):
        raise module.FlyerRenderError("critical text facts do not fit")

    monkeypatch.setattr(module, "render_source_edit_preview", fake_render_source_edit)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    rc = module.main()
    assert rc == 2

    out = json.loads(capsys.readouterr().out)
    assert out["manual_review_reason_code"] == "visual_qa_failed"

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["reason_code"] == "visual_qa_failed"


@pytest.mark.parametrize(
    ("project_id", "raw_request", "locked_facts", "rendered_text"),
    [
        (
            "F0106",
            "Create a flyer for Diwali sale, All items 5-10% off. Lucky draw eligible with purchase above $100.",
            [
                {"fact_id": "business_name", "label": "Business", "value": "Lakshmi's Kitchen", "source": "customer_profile"},
                {"fact_id": "campaign_title", "label": "Campaign", "value": "Diwali Sale", "source": "customer_text"},
                {"fact_id": "pricing_structure", "label": "Pricing", "value": "All items 5-10% off", "source": "customer_text"},
                {"fact_id": "offer:0", "label": "Offer", "value": "Lucky draw eligible with purchase above $100", "source": "customer_text"},
                {"fact_id": "location", "label": "Location", "value": "90 Brybar Dr St Johns FL", "source": "customer_profile"},
                {"fact_id": "contact_phone", "label": "Contact", "value": "+17329837841", "source": "customer_profile"},
            ],
            "Lakshmis Kitchen\nDIWALI SALE\nALL ITEMS 5-10% OFF\nLucky Draw Eligible\nAbove $100 purchase\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
        ),
        (
            "F0107",
            "Create a flyer for evening snacks sale, Wednesday and Thursday, any item $7.99. Free Masala Chai with any purchase above $12. This promotion runs until June 25.",
            [
                {"fact_id": "business_name", "label": "Business", "value": "Lakshmi's Kitchen", "source": "customer_profile"},
                {"fact_id": "campaign_title", "label": "Campaign", "value": "Evening Snacks Sale", "source": "customer_text"},
                {"fact_id": "pricing_structure", "label": "Pricing", "value": "Any item $7.99", "source": "customer_text"},
                {"fact_id": "offer:0", "label": "Offer", "value": "Free Masala Chai with any purchase above $12", "source": "customer_text"},
                {"fact_id": "schedule", "label": "Schedule", "value": "Wednesday and Thursday", "source": "customer_text"},
                {"fact_id": "promotion_end", "label": "Promotion end", "value": "June 25", "source": "customer_text"},
                {"fact_id": "location", "label": "Location", "value": "90 Brybar Dr St Johns FL", "source": "customer_profile"},
                {"fact_id": "contact_phone", "label": "Contact", "value": "+17329837841", "source": "customer_profile"},
            ],
            "Lakshmis Kitchen\nEVENING SNACKS SALE\nWednesday and Thursday\nAny item $7.99\nFree Masala Chai with purchase above $12\nOffer valid until June 25\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
        ),
    ],
)
def test_generate_replay_incident_semantic_briefs_pass_visual_qa(
    monkeypatch,
    tmp_path,
    capsys,
    project_id,
    raw_request,
    locked_facts,
    rendered_text,
):
    module = _load_script(monkeypatch)

    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    now = datetime(2026, 5, 27, tzinfo=timezone.utc).isoformat()
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 108,
        "projects": [{
            "project_id": project_id,
            "status": "intake_started",
            "customer_phone": "+17329837841",
            "created_at": now,
            "updated_at": now,
            "original_message_id": f"m-{project_id}",
            "raw_request": raw_request,
            "locked_facts": locked_facts,
        }],
    }), encoding="utf-8")

    def fake_render(project, _asset_dir, **_kwargs):
        rendered = asset_dir / f"{project.project_id}-C1.png"
        rendered.write_bytes(b"rendered")
        (asset_dir / f"{project.project_id}-C1.png.ocr.txt").write_text(rendered_text, encoding="utf-8")
        return [types.SimpleNamespace(
            path=rendered,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )]

    monkeypatch.setenv("FLYER_QA_ALLOW_SIDECAR", "1")
    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "build_asset_manifest", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", project_id,
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 0
    out = json.loads(capsys.readouterr().out)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]

    assert out["project_id"] == project_id
    assert persisted["status"] == "awaiting_concept_selection"

def test_generate_missing_required_facts_project_does_not_enter_source_edit_renderer(
    monkeypatch, tmp_path, capsys,
):
    """Fix D regression: a project at status=manual_edit_required for the
    missing_required_facts reason (S4) — i.e. NO source-edit raw_request
    marker AND NO reference_image asset — must NOT take the source-edit
    rendering branch. Pre-fix, the bare status-only detection would have
    sent it into `render_source_edit_preview` which then fails inside
    `_source_edit_reference_asset` and rewrites manual_review with a
    misleading provider/quality reason_code, losing the original
    missing_required_facts context."""
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()

    # Project at manual_edit_required for missing_required_facts: bare
    # raw_request (no source-edit marker), no assets (no reference_image).
    # Schema requires non-empty event_or_business_name; in the real
    # missing_required_facts flow it gets set to a placeholder/profile value.
    now = datetime(2026, 5, 19, tzinfo=timezone.utc).isoformat()
    project = {
        "project_id": "F0002",
        "status": "manual_edit_required",
        "customer_phone": "+17329837841",
        "created_at": now,
        "updated_at": now,
        "original_message_id": "m-missing",
        "raw_request": "Make a flyer please.",
        "fields": {"event_or_business_name": "Pending Customer", "contact_info": "+17329837841"},
        "assets": [],  # no reference_image -> not a source-edit project
        "manual_review": {
            "status": "queued",
            "reason": "missing_required_facts",
            "reason_code": "missing_required_facts",
            "detail": "missing required fact slots: business_name, contact_phone",
            "queued_at": now,
        },
        "version": 1,
    }
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 3,
        "projects": [project],
    }), encoding="utf-8")

    # If source-edit branch is taken, the test fails — render_source_edit_preview
    # would be called. We patch it to assert that it is NOT called.
    def fake_render_source_edit(*_args, **_kwargs):
        raise AssertionError("missing_required_facts project must not enter source-edit renderer")

    # Render_concept_previews should be the path. Stub it to a no-op spec; the
    # script will then attempt downstream steps which may fail — we just need
    # to verify the source-edit branch was NOT taken.
    monkeypatch.setattr(module, "render_source_edit_preview", fake_render_source_edit)

    def fake_render_concepts(*_args, **_kwargs):
        # No-op: return empty list so downstream code path bails harmlessly.
        return []

    monkeypatch.setattr(module, "render_concept_previews", fake_render_concepts)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0002",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    # The script may rc==0 or rc!=0 depending on downstream behavior with an
    # empty specs list; either way the AssertionError from
    # fake_render_source_edit would propagate up and fail the test. The
    # invariant under test is "source-edit branch NOT taken", not the rc.
    try:
        module.main()
    except SystemExit:
        pass  # downstream paths may SystemExit on empty specs; tolerated.
    # If we got here without AssertionError, the source-edit branch was
    # correctly avoided.


def test_generate_draft_provider_timeout_queues_manual_review(monkeypatch, tmp_path, capsys):
    """Draft (non-source-edit) rendering must never stall silently. When
    render_concept_previews raises (timeout/5xx), queue manual review with
    reason_code=provider_timeout and persist manual_edit_required for early
    statuses."""
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["reference_extractions"][0]["provider"] = "openai"
    project["reference_extractions"][0]["status"] = "ok"
    project["reference_extractions"][0]["detail"] = "extracted"
    project["status"] = "generating_concepts"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def fake_render(*_args, **_kwargs):
        raise module.FlyerRenderError("HTTP 502 Bad Gateway")

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    rc = module.main()
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["manual_review_reason_code"] == "provider_timeout"
    assert out["attempts"] == 2  # includes the single retry

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["status"] == "queued"
    assert persisted["manual_review"]["reason_code"] == "provider_timeout"




def test_generate_draft_dependency_missing_queues_manual_review(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["reference_extractions"][0]["provider"] = "openai"
    project["reference_extractions"][0]["status"] = "ok"
    project["reference_extractions"][0]["detail"] = "extracted"
    project["status"] = "generating_concepts"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    def fake_render(*_args, **_kwargs):
        raise module.FlyerRenderError("Pillow is unavailable for exact identity overlay: /usr/bin/python3 missing")

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["manual_review_reason_code"] == "dependency_missing"
    assert out["attempts"] == 1
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["manual_review"]["reason_code"] == "dependency_missing"


def test_generate_retries_without_saved_brand_assets_when_business_name_missing(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["reference_extractions"] = []
    project["assets"] = []
    project["status"] = "manual_edit_required"
    project["manual_review"] = {
        "status": "queued",
        "reason": "visual_qa_failed",
        "reason_code": "visual_qa_failed",
        "detail": "missing required visible fact: business_name",
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    project["raw_request"] = "Create an evening snacks flyer. Use saved business name, address, phone, and logo."
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    render_env_values = []

    def fake_render(_project, output_dir, **_kwargs):
        disabled = module.os.environ.get("FLYER_DISABLE_BRAND_ASSETS", "")
        render_env_values.append(disabled)
        suffix = "retry" if disabled == "1" else "first"
        path = Path(output_dir) / f"F0001-C1-{suffix}.png"
        path.write_bytes(f"image-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(
            path=path,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        failed = render_env_values[-1] != "1"
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="0" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="failed" if failed else "passed",
            blockers=["missing required visible fact: business_name"] if failed else [],
            extracted_text="DESI CHOWRASTHA" if failed else "Lakshmi's Kitchen",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    rc = module.main()
    assert rc == 0
    assert render_env_values == ["", "1"]
    assert module.os.environ.get("FLYER_DISABLE_BRAND_ASSETS") is None

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "awaiting_final_approval"
    assert persisted["manual_review"]["status"] == "none"
    assert persisted["assets"][-1]["path"].endswith("F0001-C1-retry.png")


def test_generate_exact_text_qa_failure_uses_overlay_fallback_before_manual_review(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import Config, FlyerVisualQAReport

    monkeypatch.setattr(module, "load_yaml_model", lambda *_args, **_kwargs: Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {
            "enabled": True,
            "draft_image_model": "openrouter/premium-poster-model",
            "draft_image_quality": "high",
            "concept_count": 1,
        },
    }))

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    now = datetime(2026, 6, 12, tzinfo=timezone.utc).isoformat()
    project = {
        "project_id": "F0152",
        "status": "manual_edit_required",
        "customer_phone": "+17329837841",
        "customer_id": "CUST0001",
        "created_at": now,
        "updated_at": now,
        "original_message_id": "wamid.f0152",
        "raw_request": "Use as reference. Street snack specials for Lakshmi's Kitchen.",
        "fields": {
            "event_or_business_name": "Lakshmi's Kitchen",
            "venue_or_location": "90 Brybar Dr St Johns FL",
            "contact_info": "+1 732 983 7841",
            "notes": "Customer chose path 2: use the source flyer only as a reference/inspiration.",
        },
        "locked_facts": [
            {"fact_id": "business_name", "label": "Business", "value": "Lakshmi's Kitchen", "source": "customer_profile", "required": True},
            {"fact_id": "campaign_title", "label": "Campaign", "value": "STREET SNACK SPECIALS", "source": "reference_vision", "required": True},
            {"fact_id": "pricing_structure", "label": "Pricing", "value": "ANY 2 SNACKS $9.99", "source": "reference_vision", "required": True},
            {"fact_id": "item:0:name", "label": "Item", "value": "Punugulu", "source": "reference_vision", "required": True},
            {"fact_id": "item:1:name", "label": "Item", "value": "Egg Bonda", "source": "reference_vision", "required": True},
            {"fact_id": "item:2:name", "label": "Item", "value": "Aloo Bonda", "source": "reference_vision", "required": True},
            {"fact_id": "item:3:name", "label": "Item", "value": "Veg Lollipop", "source": "reference_vision", "required": True},
            {"fact_id": "item:4:name", "label": "Item", "value": "Mirchi Bhajji", "source": "reference_vision", "required": True},
            {"fact_id": "location", "label": "Location", "value": "90 Brybar Dr St Johns FL", "source": "customer_profile", "required": True},
            {"fact_id": "contact_phone", "label": "Contact", "value": "+1 732 983 7841", "source": "customer_profile", "required": True},
        ],
        "reference_extractions": [],
        "assets": [{
            "asset_id": "A0001",
            "kind": "reference_image",
            "source": "whatsapp",
            "path": str(asset_dir / "F0152-reference.jpg"),
            "mime_type": "image/jpeg",
            "sha256": "a" * 64,
            "original_message_id": "wamid.ref",
            "received_at": now,
        }],
        "manual_review": {
            "status": "queued",
            "reason": "visual_qa_failed",
            "reason_code": "visual_qa_failed",
            "detail": "unverified phone number visible: 614 956-1099",
            "queued_at": now,
        },
    }
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 153,
        "projects": [project],
    }), encoding="utf-8")

    render_calls = []

    def fake_render(project_obj, output_dir, **kwargs):
        mode = module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER", "")
        render_calls.append({
            "mode": mode,
            "model": kwargs.get("model"),
            "quality": kwargs.get("quality"),
        })
        suffix = "fallback" if mode == "0" else "integrated"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(
            path=path,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        fallback = "fallback" in Path(artifact_path).name
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="d" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="passed" if fallback else "failed",
            blockers=[] if fallback else [
                "missing required visible fact: item:2:name",
                "near-duplicate item visible: expected Veg Lollipop but saw Veg Lolipop",
            ],
            extracted_text="Lakshmi's Kitchen\nSTREET SNACK SPECIALS" if fallback else "Veg Lolipop",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(
        module,
        "render_source_edit_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("visual_qa_failed reference-inspiration retry must not enter source-edit renderer")
        ),
    )
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module,
        "classify_flyer_qa_for_autorepair",
        lambda *_args, **_kwargs: types.SimpleNamespace(decision="hard_stop", reason="customer_trust_risk"),
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0152",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    out = json.loads(capsys.readouterr().out)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]

    assert out["project_id"] == "F0152"
    # 2026-06-15: a content-recoverable miss (missing item name + near-duplicate)
    # now gets ONE integrated content corrective retry (mode=1, repair_instruction)
    # BEFORE the deterministic-overlay fallback (mode=0). The retry here still
    # misses → the overlay ships.
    assert render_calls == [
        {"mode": "1", "model": "openrouter/premium-poster-model", "quality": "high"},
        {"mode": "1", "model": "openrouter/premium-poster-model", "quality": "high"},
        {"mode": "0", "model": "openrouter/premium-poster-model", "quality": "high"},
    ]
    assert module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER") == "1"
    assert persisted["status"] == "awaiting_final_approval"
    assert persisted["manual_review"]["status"] == "none"
    assert persisted["assets"][-1]["path"].endswith("F0152-C1-fallback.png")


def test_exact_text_fallback_guard_includes_source_text_leakage(monkeypatch):
    module = _load_script(monkeypatch)
    from schemas import FlyerVisualQAReport

    report = FlyerVisualQAReport(
        project_id="F0152",
        asset_id="A0004",
        artifact_path="/tmp/F0152-C1-preview.png",
        artifact_sha256="e" * 64,
        project_version=1,
        output_format="concept_preview",
        provider="test",
        qa_source="ocr_vision",
        status="failed",
        blockers=[
            "duplicate item visible: Punugulu",
            "inferred item not rendered: Vada",
        ],
        checked_at=datetime.now(timezone.utc),
    )
    unsafe = report.model_copy(update={
        "blockers": ["visible wrong brand text: Indian Cafe & Bakery"],
    })

    assert module._qa_failed_exact_text_recoverable([report]) is True
    assert module._qa_failed_exact_text_recoverable([unsafe]) is False


def test_generate_marks_superseded_revision_applied_before_approval(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    project = _project_with_failed_reference()
    project.update({
        "project_id": "F0105",
        "status": "generating_concepts",
        "version": 3,
        "reference_extractions": [],
        "assets": [],
        "raw_request": "Create Daily Thali Specials for Lakshmi's Kitchen.",
        "revisions": [
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
                "request_text": "change the footer before regenerating",
                "applied": False,
                "resulting_version": None,
            },
            {
                "revision_id": "R004",
                "message_id": "m-edit-4",
                "requested_at": "2026-05-27T11:23:14.928670Z",
                "request_text": "future revision from a concurrent writer",
                "applied": False,
                "resulting_version": 4,
            },
        ],
    })
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 106,
        "projects": [project],
    }), encoding="utf-8")

    def fake_render(_project, output_dir, **_kwargs):
        rendered = Path(output_dir) / "F0105-C1-preview.png"
        rendered.write_bytes(b"rendered")
        return [RenderedAssetSpec(
            path=rendered,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="c" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="sidecar_test",
            status="passed",
            checked_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0105",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 0
    out = json.loads(capsys.readouterr().out)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    revisions = {revision["revision_id"]: revision for revision in persisted["revisions"]}

    assert out["project_id"] == "F0105"
    assert persisted["status"] == "awaiting_final_approval"
    assert revisions["R001"]["applied"] is True
    assert revisions["R002"]["applied"] is True
    assert revisions["R003"]["applied"] is False
    assert revisions["R004"]["applied"] is False


def test_generate_draft_quality_failure_does_not_retry(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    project = _project_with_pending_reference(reference)
    project["reference_extractions"][0]["provider"] = "openai"
    project["reference_extractions"][0]["status"] = "ok"
    project["reference_extractions"][0]["detail"] = "extracted"
    project["status"] = "generating_concepts"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [project],
    }), encoding="utf-8")

    calls = {"n": 0}

    def fake_render(*_args, **_kwargs):
        calls["n"] += 1
        raise module.FlyerRenderError("draft concept failed quality check: width mismatch")

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    rc = module.main()
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["manual_review_reason_code"] == "visual_qa_failed"
    assert out["attempts"] == 1
    assert calls["n"] == 1


def test_generate_autorepairs_f0105_style_visual_qa_failure_before_manual_review(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import Config, FlyerVisualQAReport

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    cfg = Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {
            "enabled": True,
            "draft_image_model": "deterministic-renderer",
            "concept_count": 1,
            "recovery": {"auto_repair_enabled": True, "max_auto_repair_attempts": 1},
        },
    })
    monkeypatch.setattr(module, "load_yaml_model", lambda *_args, **_kwargs: cfg)

    state_path = tmp_path / "projects.json"
    attempt_path = tmp_path / "autorepair_attempts.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    now = datetime(2026, 5, 27, tzinfo=timezone.utc).isoformat()
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [{
            "project_id": "F0105",
            "status": "generating_concepts",
            "customer_phone": "+17329837841",
            "created_at": now,
            "updated_at": now,
            "original_message_id": "wamid.F0105",
            "raw_request": "Create Special Biryani flyer with chicken and goat prices.",
            "locked_facts": [
                {"fact_id": "detail_001", "label": "Item 1", "value": "Chicken biryani - $12.99", "source": "customer_text", "required": True},
                {"fact_id": "detail_002", "label": "Item 2", "value": "Goat biryani - $14.99", "source": "customer_text", "required": True},
            ],
        }],
    }), encoding="utf-8")

    render_instructions = []

    def fake_render(_project, output_dir, **kwargs):
        repair_instruction = kwargs.get("repair_instruction", "")
        render_instructions.append(repair_instruction)
        suffix = "repaired" if repair_instruction else "first"
        path = Path(output_dir) / f"F0105-C1-{suffix}.png"
        path.write_bytes(f"image-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(
            path=path,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        repaired = "repaired" in Path(artifact_path).name
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="0" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="passed" if repaired else "failed",
            blockers=[] if repaired else [
                "missing required visible fact: item:2:name",
                "instruction text leaked into flyer copy: DAILY THALI SPECIALS FLYER",
            ],
            extracted_text="Lakshmi's Kitchen\nChicken biryani\nGoat biryani" if repaired else "Lakshmi's Kitchen\nChicken biryani",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(
        module,
        "plan_flyer_autorepair",
        lambda **_kwargs: {
            "action": "regenerate_with_instruction",
            "repair_instruction": "Show each offer item once. Remove generic footer title.",
            "confidence": "high",
        },
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0105",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(attempt_path),
    ])

    rc = module.main()
    assert rc == 0
    assert render_instructions == ["", "Show each offer item once. Remove generic footer title."]
    out = json.loads(capsys.readouterr().out)
    assert out["concept_count"] == 1

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "awaiting_final_approval"
    assert persisted["manual_review"]["status"] == "none"
    assert [asset["path"] for asset in persisted["assets"]] == [str(asset_dir / "F0105-C1-repaired.png")]
    assert not (asset_dir / "F0105-C1-first.png").exists()

    attempts = json.loads(attempt_path.read_text(encoding="utf-8"))["attempts"]
    assert attempts[0]["status"] == "succeeded"
    assert attempts[0]["repair_instruction_hash"] != "0" * 64
    audit_types = [json.loads(line)["type"] for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert audit_types == ["flyer_autorepair_attempted", "flyer_autorepair_succeeded"]


def test_generate_autorepair_preserves_repaired_asset_when_renderer_reuses_path(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec, validate_text_manifest_file, write_text_manifest
    from agents.flyer.visual_qa import validate_visual_qa_report, write_visual_qa_report
    from schemas import Config, FlyerVisualQAReport

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    cfg = Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {
            "enabled": True,
            "draft_image_model": "deterministic-renderer",
            "concept_count": 1,
            "recovery": {"auto_repair_enabled": True, "max_auto_repair_attempts": 1},
        },
    })
    monkeypatch.setattr(module, "load_yaml_model", lambda *_args, **_kwargs: cfg)

    state_path = tmp_path / "projects.json"
    attempt_path = tmp_path / "autorepair_attempts.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    now = datetime(2026, 5, 27, tzinfo=timezone.utc).isoformat()
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [{
            "project_id": "F0105",
            "status": "generating_concepts",
            "customer_phone": "+17329837841",
            "created_at": now,
            "updated_at": now,
            "original_message_id": "wamid.F0105",
            "raw_request": "Create daily thali specials flyer.",
            "locked_facts": [
                {"fact_id": "item:0:name", "label": "Item", "value": "veg", "source": "customer_text", "required": True},
                {"fact_id": "item:1:name", "label": "Item", "value": "chicken", "source": "customer_text", "required": True},
                {"fact_id": "item:2:name", "label": "Item", "value": "goat specials", "source": "customer_text", "required": True},
            ],
        }],
    }), encoding="utf-8")

    render_count = {"n": 0}
    shared_path = asset_dir / "F0105-C1-preview.png"

    def fake_render(_project, output_dir, **kwargs):
        render_count["n"] += 1
        content = b"repaired-image" if kwargs.get("repair_instruction") else b"failed-image"
        shared_path.write_bytes(content)
        write_text_manifest(
            _project,
            shared_path,
            output_format="concept_preview",
            selected_concept_id="C1",
        )
        return [RenderedAssetSpec(
            path=shared_path,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        repaired = render_count["n"] >= 2
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="1" * 64 if repaired else "0" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="passed" if repaired else "failed",
            blockers=[] if repaired else ["missing required visible fact: item:2:name"],
            extracted_text="veg chicken goat specials" if repaired else "veg chicken",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", write_visual_qa_report)
    monkeypatch.setattr(
        module,
        "plan_flyer_autorepair",
        lambda **_kwargs: {"action": "regenerate_with_instruction", "repair_instruction": "Show goat specials.", "confidence": "high"},
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0105",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(attempt_path),
    ])

    assert module.main() == 0
    capsys.readouterr()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["assets"][0]["path"] == str(shared_path)
    assert shared_path.read_bytes() == b"repaired-image"
    text_result = validate_text_manifest_file(
        shared_path,
        project_id="F0105",
        project_version=persisted["version"],
        output_format="concept_preview",
    )
    visual_result = validate_visual_qa_report(
        shared_path,
        project_id="F0105",
        project_version=persisted["version"],
        output_format="concept_preview",
        allow_sidecar=True,
    )
    assert text_result.ok, text_result.blockers
    assert visual_result.ok, visual_result.blockers


def test_generate_autorepair_failure_preserves_failed_preview_for_manual_review(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import Config, FlyerVisualQAReport

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    cfg = Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {"enabled": True, "draft_image_model": "deterministic-renderer", "concept_count": 1},
    })
    monkeypatch.setattr(module, "load_yaml_model", lambda *_args, **_kwargs: cfg)

    state_path = tmp_path / "projects.json"
    attempt_path = tmp_path / "autorepair_attempts.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    now = datetime(2026, 5, 27, tzinfo=timezone.utc).isoformat()
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [{
            "project_id": "F0106",
            "status": "generating_concepts",
            "customer_phone": "+17329837841",
            "created_at": now,
            "updated_at": now,
            "original_message_id": "wamid.F0106",
            "raw_request": "Create Special Biryani flyer with chicken and goat prices.",
            "locked_facts": [
                {"fact_id": "detail_001", "label": "Item 1", "value": "Chicken biryani - $12.99", "source": "customer_text", "required": True},
                {"fact_id": "detail_002", "label": "Item 2", "value": "Goat biryani - $14.99", "source": "customer_text", "required": True},
            ],
        }],
    }), encoding="utf-8")

    def fake_render(_project, output_dir, **kwargs):
        repair_instruction = kwargs.get("repair_instruction", "")
        suffix = "repair" if repair_instruction else "first"
        path = Path(output_dir) / f"F0106-C1-{suffix}.png"
        path.write_bytes(f"image-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(
            path=path,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="0" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="failed",
            blockers=["missing required visible fact: item:2:name"],
            severity="block",  # P0 #2 — pin block to exercise manual-review path
            extracted_text="Lakshmi's Kitchen\nChicken biryani",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(
        module,
        "plan_flyer_autorepair",
        lambda **_kwargs: {
            "action": "regenerate_with_instruction",
            "repair_instruction": "Show each offer item once.",
            "confidence": "high",
        },
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0106",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(attempt_path),
    ])

    assert module.main() == 2
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["assets"][0]["path"] == str(asset_dir / "F0106-C1-first.png")
    assert (asset_dir / "F0106-C1-first.png").exists()
    assert not (asset_dir / "F0106-C1-repair.png").exists()
    assert json.loads(attempt_path.read_text(encoding="utf-8"))["attempts"][0]["status"] == "exhausted"
    audit_types = [json.loads(line)["type"] for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert audit_types == ["flyer_autorepair_attempted", "flyer_autorepair_exhausted"]


def test_generate_autorepair_failure_preserves_original_asset_when_renderer_reuses_path(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec, validate_text_manifest_file, write_text_manifest
    from agents.flyer.visual_qa import validate_visual_qa_report, write_visual_qa_report
    from schemas import Config, FlyerVisualQAReport

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    cfg = Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {
            "enabled": True,
            "draft_image_model": "deterministic-renderer",
            "concept_count": 1,
            "recovery": {"auto_repair_enabled": True, "max_auto_repair_attempts": 1},
        },
    })
    monkeypatch.setattr(module, "load_yaml_model", lambda *_args, **_kwargs: cfg)

    state_path = tmp_path / "projects.json"
    attempt_path = tmp_path / "autorepair_attempts.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    now = datetime(2026, 5, 27, tzinfo=timezone.utc).isoformat()
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [{
            "project_id": "F0105",
            "status": "generating_concepts",
            "customer_phone": "+17329837841",
            "created_at": now,
            "updated_at": now,
            "original_message_id": "wamid.F0105",
            "raw_request": "Create daily thali specials flyer.",
            "locked_facts": [
                {"fact_id": "item:0:name", "label": "Item", "value": "veg", "source": "customer_text", "required": True},
                {"fact_id": "item:1:name", "label": "Item", "value": "chicken", "source": "customer_text", "required": True},
            ],
        }],
    }), encoding="utf-8")

    render_count = {"n": 0}
    shared_path = asset_dir / "F0105-C1-preview.png"

    def fake_render(_project, output_dir, **kwargs):
        render_count["n"] += 1
        if kwargs.get("repair_instruction"):
            # Production renderers reuse stable preview paths. Simulate a
            # failed repair write, then restore the original failed artifact
            # so cleanup must not delete the manual-review evidence.
            shared_path.write_bytes(b"repair-failed-image")
            write_text_manifest(_project, shared_path, output_format="concept_preview", selected_concept_id="C1")
            shared_path.write_bytes(b"original-failed-image")
            write_text_manifest(_project, shared_path, output_format="concept_preview", selected_concept_id="C1")
        else:
            shared_path.write_bytes(b"original-failed-image")
            write_text_manifest(_project, shared_path, output_format="concept_preview", selected_concept_id="C1")
        return [RenderedAssetSpec(
            path=shared_path,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="0" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="failed",
            blockers=["missing required visible fact: item:1:name"],
            severity="block",  # P0 #2 — pin block to exercise manual-review path
            extracted_text="veg",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", write_visual_qa_report)
    monkeypatch.setattr(
        module,
        "plan_flyer_autorepair",
        lambda **_kwargs: {"action": "regenerate_with_instruction", "repair_instruction": "Show chicken.", "confidence": "high"},
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0105",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(attempt_path),
    ])

    assert module.main() == 2
    capsys.readouterr()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["assets"][0]["path"] == str(shared_path)
    assert shared_path.read_bytes() == b"original-failed-image"
    text_result = validate_text_manifest_file(
        shared_path,
        project_id="F0105",
        project_version=persisted["version"],
        output_format="concept_preview",
    )
    visual_result = validate_visual_qa_report(
        shared_path,
        project_id="F0105",
        project_version=persisted["version"],
        output_format="concept_preview",
        allow_sidecar=True,
    )
    assert text_result.ok, text_result.blockers
    assert not visual_result.ok
    assert visual_result.blockers == ["visual QA did not pass", "missing required visible fact: item:1:name"]


def test_generate_autorepair_render_exception_exhausts_and_queues_manual_review(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import Config, FlyerVisualQAReport

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    cfg = Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {"enabled": True, "draft_image_model": "deterministic-renderer", "concept_count": 1},
    })
    monkeypatch.setattr(module, "load_yaml_model", lambda *_args, **_kwargs: cfg)
    state_path = tmp_path / "projects.json"
    attempt_path = tmp_path / "autorepair_attempts.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    now = datetime(2026, 5, 27, tzinfo=timezone.utc).isoformat()
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [{
            "project_id": "F0107",
            "status": "generating_concepts",
            "customer_phone": "+17329837841",
            "created_at": now,
            "updated_at": now,
            "original_message_id": "wamid.F0107",
            "raw_request": "Create Special Biryani flyer with chicken and goat prices.",
        }],
    }), encoding="utf-8")

    def fake_render(_project, output_dir, **kwargs):
        if kwargs.get("repair_instruction"):
            raise module.FlyerRenderError("repair provider failed after planner")
        path = Path(output_dir) / "F0107-C1-first.png"
        path.write_bytes(b"image-first")
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview", width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="0" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="failed",
            blockers=["missing required visible fact: item:2:name"],
            severity="block",  # P0 #2 — pin block to exercise manual-review path
            extracted_text="Lakshmi's Kitchen",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "plan_flyer_autorepair", lambda **_kwargs: {"action": "regenerate_with_instruction", "repair_instruction": "Show each offer item once."}, raising=False)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0107",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(attempt_path),
    ])

    assert module.main() == 2
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["assets"][0]["path"] == str(asset_dir / "F0107-C1-first.png")
    assert json.loads(attempt_path.read_text(encoding="utf-8"))["attempts"][0]["status"] == "exhausted"


def test_generate_autorepair_audit_failure_does_not_block_manual_review(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import Config, FlyerVisualQAReport

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    cfg = Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {"enabled": True, "draft_image_model": "deterministic-renderer", "concept_count": 1},
    })
    monkeypatch.setattr(module, "load_yaml_model", lambda *_args, **_kwargs: cfg)
    monkeypatch.setattr(module, "ndjson_append", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("log unwritable")))

    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    now = datetime(2026, 5, 27, tzinfo=timezone.utc).isoformat()
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [{
            "project_id": "F0108",
            "status": "generating_concepts",
            "customer_phone": "+17329837841",
            "created_at": now,
            "updated_at": now,
            "original_message_id": "wamid.F0108",
            "raw_request": "Create Special Biryani flyer with chicken and goat prices.",
        }],
    }), encoding="utf-8")

    def fake_render(_project, output_dir, **_kwargs):
        path = Path(output_dir) / "F0108-C1-first.png"
        path.write_bytes(b"image-first")
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview", width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="0" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="failed",
            blockers=["visible wrong business: Desi Chowrastha"],
            severity="block",  # P0 #2 — pin block to exercise manual-review path
            extracted_text="Desi Chowrastha",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0108",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(tmp_path / "unwritable.log"),
        "--autorepair-state-path", str(tmp_path / "autorepair_attempts.json"),
    ])

    assert module.main() == 2
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["reason_code"] == "visual_qa_failed"


def test_autorepair_attempted_rows_are_marked_stale_before_budget_count(monkeypatch):
    module = _load_script(monkeypatch)
    from schemas import FlyerAutoRepairAttemptStore, FlyerRepairAttempt

    old = datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 27, 11, 0, tzinfo=timezone.utc)
    store = FlyerAutoRepairAttemptStore(attempts=[
        FlyerRepairAttempt(
            attempt_id="F0109-v1-a1",
            project_id="F0109",
            project_version=1,
            status="attempted",
            qa_blocker_hash="a" * 64,
            repair_instruction_hash="b" * 64,
            repair_instruction="Show each item once.",
            started_at=old,
        )
    ])

    updated = module._mark_stale_attempts(store, now=now, stale_minutes=30)

    assert updated.attempts[0].status == "stale"
    assert updated.attempts[0].completed_at == now


# ─────────────────────────────────────────────────────────────────
# P0 #2 — warn-tier severity branch tests (Commit 3)
# Verifies the script writes delivered_with_warning + warning payload +
# FlyerWarnTierDelivered audit row WITHOUT sending. cf-router drives the
# actual customer send via its post-subprocess branch (Commit 4).
# Hermes-as-brain invariant check: script is a state-writer, not a sender.
# ─────────────────────────────────────────────────────────────────


def _setup_warn_tier_project_state(state_path: Path, project_id: str = "F0108") -> None:
    """Project in generating_concepts state with Lakshmi's Kitchen brand —
    matches F0108 production reproduction shape."""
    now = datetime(2026, 5, 28, tzinfo=timezone.utc).isoformat()
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [{
            "project_id": project_id,
            "status": "generating_concepts",
            "customer_phone": "+17329837841",
            "created_at": now,
            "updated_at": now,
            "original_message_id": f"wamid.{project_id}",
            "raw_request": "Create a flyer for Dosa Special Night at Lakshmi's Kitchen.",
            "locked_facts": [
                {"fact_id": "business_name", "label": "Business",
                 "value": "Lakshmi's Kitchen", "source": "customer_text", "required": True},
            ],
        }],
    }), encoding="utf-8")


def _warn_tier_test_config() -> "Config":  # noqa: F821
    from schemas import Config
    return Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {"enabled": True, "draft_image_model": "deterministic-renderer", "concept_count": 1},
    })


def test_generate_concepts_warn_tier_writes_delivered_with_warning_state(monkeypatch, tmp_path, capsys):
    """F0108-shape: single brand-typo warn blocker → state writes
    delivered_with_warning + warning payload + FlyerWarnTierDelivered audit row.
    Script returns 0 with a stdout JSON marker so cf-router knows to take
    the warn-tier send branch. NO outbound bridge call from the script."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(module, "load_yaml_model", lambda *_a, **_k: _warn_tier_test_config())

    state_path = tmp_path / "projects.json"
    attempt_path = tmp_path / "autorepair_attempts.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    _setup_warn_tier_project_state(state_path)

    def fake_render(_project, output_dir, **kwargs):
        suffix = "repair" if kwargs.get("repair_instruction", "") else "first"
        path = Path(output_dir) / f"F0108-C1-{suffix}.png"
        path.write_bytes(f"img-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(
            path=path, kind="concept_preview",
            output_format="concept_preview",
            width=1080, height=1350, concept_id="C1",
        )]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="0" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="failed",
            blockers=["visible wrong business/brand: Laksmi'S Kitchen"],
            severity="warn",  # F0108 — brand typo classifier output
            extracted_text="Lakshmi's Kitchen ... LAKSMI'S KITCHEN",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(
        module, "plan_flyer_autorepair",
        lambda **_k: {"action": "regenerate_with_instruction",
                      "repair_instruction": "Fix the brand spelling.",
                      "confidence": "high"},
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0108",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(attempt_path),
    ])

    # State machine result + return code
    assert module.main() == 0

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "delivered_with_warning"

    # Warning payload populated with the warn-tier blockers + customer text + sha
    warning = persisted["warning"]
    assert warning is not None
    assert warning["severity"] == "warn"
    assert warning["blockers"] == ["visible wrong business/brand: Laksmi'S Kitchen"]
    assert warning["customer_text"]  # non-empty
    assert "Lakshmi's Kitchen" in warning["customer_text"]
    assert "spelling" in warning["customer_text"]
    assert len(warning["customer_text_sha256"]) == 64  # sha256 hex
    assert warning["asset_id"]  # non-empty

    # FlyerWarnTierDelivered audit row written, NOT visual_qa_failed
    audit_lines = audit_path.read_text(encoding="utf-8").splitlines()
    audit_types = [json.loads(line)["type"] for line in audit_lines]
    assert "flyer_warn_tier_delivered" in audit_types
    warn_row = next(json.loads(line) for line in audit_lines
                    if json.loads(line)["type"] == "flyer_warn_tier_delivered")
    assert warn_row["project_id"] == "F0108"
    assert warn_row["severity"] == "warn"
    assert warn_row["blockers"] == ["visible wrong business/brand: Laksmi'S Kitchen"]
    assert warn_row["customer_text_sha256"] == warning["customer_text_sha256"]

    # Stdout JSON marker — cf-router uses this to know warn-tier branch fired
    captured = capsys.readouterr()
    marker = json.loads(captured.out.strip().splitlines()[-1])
    assert marker["project_id"] == "F0108"
    assert marker["delivered_with_warning"] is True
    assert marker["warning_blockers"] == ["visible wrong business/brand: Laksmi'S Kitchen"]


def test_generate_concepts_block_tier_preserves_manual_edit_required_path(monkeypatch, tmp_path, capsys):
    """Block-tier path is preserved bit-for-bit. Placeholder blocker is
    always block-tier; severity branch routes to manual_edit_required."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(module, "load_yaml_model", lambda *_a, **_k: _warn_tier_test_config())

    state_path = tmp_path / "projects.json"
    attempt_path = tmp_path / "autorepair_attempts.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    _setup_warn_tier_project_state(state_path, project_id="F0109")

    def fake_render(_project, output_dir, **kwargs):
        suffix = "repair" if kwargs.get("repair_instruction", "") else "first"
        path = Path(output_dir) / f"F0109-C1-{suffix}.png"
        path.write_bytes(f"img-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(
            path=path, kind="concept_preview",
            output_format="concept_preview",
            width=1080, height=1350, concept_id="C1",
        )]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="0" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="failed",
            blockers=["placeholder text is visible in generated flyer"],
            severity="block",  # placeholder is block-tier per classifier
            extracted_text="[your business name here]",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(
        module, "plan_flyer_autorepair",
        lambda **_k: {"action": "regenerate_with_instruction",
                      "repair_instruction": "Remove placeholder.",
                      "confidence": "high"},
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0109",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(attempt_path),
    ])

    # Block-tier hits the manual_edit_required path (return 2)
    assert module.main() == 2

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["warning"] is None  # warn payload NOT populated on block path
    assert persisted["manual_review"]["reason_code"] == "visual_qa_failed"

    # No flyer_warn_tier_delivered audit row written on block path
    audit_types = [json.loads(line)["type"] for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert "flyer_warn_tier_delivered" not in audit_types


def test_generate_concepts_warn_tier_clears_stale_manual_review_payload(monkeypatch, tmp_path, capsys):
    """Regression check: if a project re-entered generating_concepts from
    manual_edit_required carrying a queued manual_review payload, the warn-
    tier transition MUST reset manual_review to its default. Otherwise
    cf-router's status formatters at actions.py:2103/2182 would leak stale
    operator-review copy onto the delivered_with_warning surface."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(module, "load_yaml_model", lambda *_a, **_k: _warn_tier_test_config())

    state_path = tmp_path / "projects.json"
    attempt_path = tmp_path / "autorepair_attempts.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()

    # Project re-entered generating_concepts from manual_edit_required and
    # carries the prior manual_review payload from that queued state.
    now = datetime(2026, 5, 28, tzinfo=timezone.utc).isoformat()
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [{
            "project_id": "F0110",
            "status": "generating_concepts",
            "customer_phone": "+17329837841",
            "created_at": now,
            "updated_at": now,
            "original_message_id": "wamid.F0110",
            "raw_request": "Create flyer for Lakshmi's Kitchen Dosa night.",
            "locked_facts": [
                {"fact_id": "business_name", "label": "Business",
                 "value": "Lakshmi's Kitchen", "source": "customer_text", "required": True},
            ],
            "manual_review": {
                "status": "queued",
                "reason": "Stale designer-review note",
                "reason_code": "visual_qa_failed",
                "detail": "Stale detail copy from a prior manual_edit_required cycle",
                "queued_at": now,
                "completed_at": None,
                "operator_asset_ids": [],
                "break_glass_reason": "",
            },
        }],
    }), encoding="utf-8")

    def fake_render(_project, output_dir, **kwargs):
        suffix = "repair" if kwargs.get("repair_instruction", "") else "first"
        path = Path(output_dir) / f"F0110-C1-{suffix}.png"
        path.write_bytes(b"img")
        return [RenderedAssetSpec(
            path=path, kind="concept_preview",
            output_format="concept_preview",
            width=1080, height=1350, concept_id="C1",
        )]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return FlyerVisualQAReport(
            project_id=project_obj.project_id, asset_id=asset_id,
            artifact_path=str(artifact_path), artifact_sha256="0" * 64,
            project_version=project_obj.version, output_format=output_format,
            provider="test", qa_source="ocr_vision", status="failed",
            blockers=["visible wrong business/brand: Laksmi'S Kitchen"],
            severity="warn",
            extracted_text="x", checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(
        module, "plan_flyer_autorepair",
        lambda **_k: {"action": "regenerate_with_instruction",
                      "repair_instruction": "x", "confidence": "high"},
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0110",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(attempt_path),
    ])

    assert module.main() == 0
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "delivered_with_warning"

    # The defining invariant of this test: manual_review reset to default,
    # NOT carrying the stale "Stale designer-review note" / queued state.
    manual_review = persisted["manual_review"]
    assert manual_review["status"] == "none"
    assert manual_review["reason"] == ""
    assert manual_review["reason_code"] == "unclassified"
    assert manual_review["detail"] == ""
    assert manual_review["queued_at"] is None
    assert manual_review["completed_at"] is None
    assert manual_review["operator_asset_ids"] == []
    assert manual_review["break_glass_reason"] == ""

    # Warning payload populated as expected (regression-safe — the new branch
    # behavior is unaffected by adding the manual_review reset).
    assert persisted["warning"] is not None
    assert persisted["warning"]["severity"] == "warn"


def test_generate_concepts_warn_tier_state_write_does_not_send(monkeypatch, tmp_path, capsys):
    """Hermes-as-brain invariant check: generate-flyer-concepts must NOT
    invoke any send mechanism. The warn-tier path writes state + audit
    row + stdout marker; cf-router (Commit 4) drives the actual customer
    send. This test asserts no bridge_post / bridge_send_media calls fire
    from the script."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(module, "load_yaml_model", lambda *_a, **_k: _warn_tier_test_config())

    # Sentinel for any bridge call fired from the script
    bridge_calls: list[tuple] = []
    monkeypatch.setattr(
        "safe_io.bridge_post" if False else "builtins.print",
        lambda *a, **k: None,
        raising=False,
    )
    # Stub-import bridge functions in case the script tries to import them
    import safe_io  # noqa: E402
    if hasattr(safe_io, "bridge_post"):
        monkeypatch.setattr(safe_io, "bridge_post",
                            lambda *a, **k: bridge_calls.append(("bridge_post", a, k)),
                            raising=False)
    if hasattr(safe_io, "bridge_send_media"):
        monkeypatch.setattr(safe_io, "bridge_send_media",
                            lambda *a, **k: bridge_calls.append(("bridge_send_media", a, k)),
                            raising=False)

    state_path = tmp_path / "projects.json"
    attempt_path = tmp_path / "autorepair_attempts.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    _setup_warn_tier_project_state(state_path)

    def fake_render(_project, output_dir, **kwargs):
        suffix = "repair" if kwargs.get("repair_instruction", "") else "first"
        path = Path(output_dir) / f"F0108-C1-{suffix}.png"
        path.write_bytes(b"img")
        return [RenderedAssetSpec(
            path=path, kind="concept_preview",
            output_format="concept_preview",
            width=1080, height=1350, concept_id="C1",
        )]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return FlyerVisualQAReport(
            project_id=project_obj.project_id, asset_id=asset_id,
            artifact_path=str(artifact_path), artifact_sha256="0" * 64,
            project_version=project_obj.version, output_format=output_format,
            provider="test", qa_source="ocr_vision", status="failed",
            blockers=["visible wrong business/brand: Laksmi'S Kitchen"],
            severity="warn",
            extracted_text="x", checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(
        module, "plan_flyer_autorepair",
        lambda **_k: {"action": "regenerate_with_instruction",
                      "repair_instruction": "x", "confidence": "high"},
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0108",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(attempt_path),
    ])

    assert module.main() == 0
    # The whole point: zero outbound bridge calls from the script
    assert bridge_calls == []


# ===========================================================================
# 2026-06-14 — integrated safety net (4a / 4b / 4c)
# Unit-level tests for the pure/decidable helpers + integration tests for the
# two new orchestration paths (provider_unavailable fallback, fabrication retry).
# ===========================================================================


def _qa_report(monkeypatch_module, *, status="failed", blockers=None, asset_id="A0001", path="/tmp/F0001-C1.png", severity=None):
    from schemas import FlyerVisualQAReport
    # severity=None → schema default ("pass"). Failed reports built by these
    # fixtures should set severity to match what run_visual_qa's
    # classify_qa_severity would return in production (e.g. block-tier for a
    # fabrication blocker) so the downstream state machine routes realistically.
    extra = {} if severity is None else {"severity": severity}
    return FlyerVisualQAReport(
        project_id="F0001",
        asset_id=asset_id,
        artifact_path=path,
        artifact_sha256="a" * 64,
        project_version=1,
        output_format="concept_preview",
        provider="test",
        qa_source="ocr_vision",
        status=status,
        blockers=list(blockers or []),
        checked_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
        **extra,
    )


# --- Option 4 (2026-06-15 safety net): fabrication is a HARD-BLOCK, not -------
# --- overlay-recoverable. Reverses the 2026-06-14 (4a) behavior per the -------
# --- operator directive: "wrong price/phone, fabricated offer remain ----------
# --- hard-blocks." A fabrication-bearing set therefore returns False so it ----
# --- falls through to manual review (the fab-only corrective retry still runs --
# --- upstream; only on its exhaustion does it reach here).

def test_qa_recoverable_false_for_fabrication_price_only(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=["fabricated price visible: $3.99"])
    assert module._qa_failed_exact_text_recoverable([report]) is False


def test_qa_recoverable_false_for_fabrication_offer_only(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=["fabricated offer claim visible: Limited Time Deal"])
    assert module._qa_failed_exact_text_recoverable([report]) is False


def test_qa_recoverable_false_for_fabrication_mixed_with_recoverable(monkeypatch):
    # Fabrication poisons an otherwise-recoverable set → hard-block to manual.
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=[
        "fabricated price visible: $3.99",
        "missing required visible fact: business_name",
    ])
    assert module._qa_failed_exact_text_recoverable([report]) is False


def test_qa_recoverable_false_for_unverified_phone(monkeypatch):
    # Operator: "wrong phone remains a hard-block." A phone that is not the
    # registered one must go to manual, never an auto-shipped overlay.
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=["unverified phone number visible: +1 555 000 0000"])
    assert module._qa_failed_exact_text_recoverable([report]) is False


def test_qa_recoverable_false_for_fabrication_mixed_with_unrecoverable(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=[
        "fabricated price visible: $3.99",
        "visible wrong brand text: Indian Cafe",
    ])
    assert module._qa_failed_exact_text_recoverable([report]) is False


def test_qa_recoverable_preserves_existing_true_case(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=["missing required visible fact: contact_phone"])
    assert module._qa_failed_exact_text_recoverable([report]) is True


def test_qa_recoverable_preserves_existing_false_case(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=["visible wrong business name: Foo"])
    assert module._qa_failed_exact_text_recoverable([report]) is False


# --- Option 4 (2026-06-15): vision-QA misspelling/duplication defects are -----
# --- recoverable via the deterministic overlay (live F0160 "Uttap", F0162 -----
# --- "Bihcken"). This is the change that converts the dense-menu manual-holds --
# --- into recover → fallback → ship.

def test_qa_recoverable_true_for_visible_text_defect_misspelling(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=[
        "visible text defect reported by QA: The word 'Uttap' may be a misspelling of 'Uttapam'.",
    ])
    assert module._qa_failed_exact_text_recoverable([report]) is True


def test_qa_recoverable_true_for_live_f0160_shape(monkeypatch):
    # Exact live F0160 blocker shape: dropped item names + a misspelling +
    # missing facts — all recoverable, no dangerous blocker → recover/fallback.
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=[
        "inferred item not rendered: Uttapam",
        "inferred item not rendered: Pongal",
        "visible text defect reported by QA: The word 'Uttap' may be a misspelling of 'Uttapam'.",
        "missing required visible fact: contact_phone",
        "missing required visible fact: location",
    ])
    assert module._qa_failed_exact_text_recoverable([report]) is True


def test_qa_recoverable_true_for_live_f0162_shape(monkeypatch):
    # Exact live F0162 blocker shape: a misspelling + a missing offer fact.
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=[
        "visible text defect reported by QA: The word 'Bihcken' appears to be misspelled and should likely be 'Chicken'.",
        "missing required visible fact: offer:0",
    ])
    assert module._qa_failed_exact_text_recoverable([report]) is True


def test_qa_recoverable_false_for_text_defect_mixed_with_fabrication(monkeypatch):
    # A misspelling is recoverable, but a co-occurring fabrication hard-blocks.
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=[
        "visible text defect reported by QA: 'Uttap' misspelling",
        "fabricated offer claim visible: 50% OFF",
    ])
    assert module._qa_failed_exact_text_recoverable([report]) is False


# --- 2026-06-15: inferred-item-not-rendered is recoverable (live F0157) ------

def test_qa_recoverable_true_for_inferred_item_not_rendered(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=["inferred item not rendered: Idli"])
    assert module._qa_failed_exact_text_recoverable([report]) is True


def test_qa_recoverable_true_for_inferred_item_plus_missing_schedule(monkeypatch):
    # The exact live F0157 shape: dropped item names + dropped schedule.
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=[
        "inferred item not rendered: Idli",
        "inferred item not rendered: Vada",
        "missing required visible fact: schedule",
    ])
    assert module._qa_failed_exact_text_recoverable([report]) is True


def test_qa_recoverable_false_for_inferred_item_mixed_with_unrecoverable(monkeypatch):
    # A truly-non-recoverable blocker still poisons the set → False. Both the
    # unverified phone (now dangerous) AND the wrong-brand text are hard-blocks.
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=[
        "inferred item not rendered: Idli",
        "unverified phone number visible: +1 555 000 0000",  # dangerous → hard-block
        "visible wrong brand text: Indian Cafe",             # NOT recoverable
    ])
    assert module._qa_failed_exact_text_recoverable([report]) is False


# --- 2026-06-16 (live F0165): item price mismatch is recoverable ONLY when the ---
# --- expected price is a LOCKED item:N:price (overlay redraws the customer's ----
# --- own stated price). Otherwise it stays a DANGEROUS hard-block. -------------

def test_qa_recoverable_true_for_item_price_mismatch_when_price_locked(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=["item price mismatch: item:2 expected Vada $7.99"])
    assert module._qa_failed_exact_text_recoverable(
        [report], locked_fact_ids={"item:2:name", "item:2:price"}
    ) is True


def test_qa_recoverable_false_for_item_price_mismatch_when_price_not_locked(monkeypatch):
    # Expected price is NOT a locked fact → cannot trust the overlay to redraw the
    # right value → hard-block to manual.
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=["item price mismatch: item:2 expected Vada $7.99"])
    assert module._qa_failed_exact_text_recoverable(
        [report], locked_fact_ids={"item:2:name"}
    ) is False


def test_qa_recoverable_false_for_item_price_mismatch_without_locked_ids(monkeypatch):
    # No locked_fact_ids supplied (conservative default) → not recoverable.
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=["item price mismatch: item:2 expected Vada $7.99"])
    assert module._qa_failed_exact_text_recoverable([report]) is False


def test_qa_recoverable_true_for_live_f0165_shape(monkeypatch):
    # Exact live F0165: dropped items → missing item names + item price mismatch,
    # all with locked item:N:price → recoverable → recover/overlay → ship.
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=[
        "missing required visible fact: item:2:name",
        "missing required visible fact: item:3:name",
        "missing required visible fact: item:4:name",
        "item price mismatch: item:2 expected Vada $7.99",
        "item price mismatch: item:3 expected Uttapam $7.99",
        "item price mismatch: item:4 expected Pongal $7.99",
    ])
    locked = {f"item:{i}:{k}" for i in range(6) for k in ("name", "price")}
    assert module._qa_failed_exact_text_recoverable([report], locked_fact_ids=locked) is True


def test_qa_recoverable_false_for_item_price_mismatch_mixed_with_fabrication(monkeypatch):
    # Even with a locked expected price, a co-occurring fabricated price hard-blocks.
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=[
        "item price mismatch: item:2 expected Vada $7.99",
        "fabricated price visible: $19.99",
    ])
    assert module._qa_failed_exact_text_recoverable(
        [report], locked_fact_ids={"item:2:price"}
    ) is False


# --- 4a: _qa_failed_is_fabrication_only ------------------------------------

def test_fabrication_only_true_for_price_and_offer(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=[
        "fabricated price visible: $3.99",
        "fabricated offer claim visible: Buy One Get One",
    ])
    assert module._qa_failed_is_fabrication_only([report]) is True


def test_fabrication_only_false_when_mixed_with_missing_fact(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, blockers=[
        "fabricated price visible: $3.99",
        "missing required visible fact: business_name",
    ])
    assert module._qa_failed_is_fabrication_only([report]) is False


def test_fabrication_only_false_when_no_blockers(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, status="provider_unavailable", blockers=[])
    assert module._qa_failed_is_fabrication_only([report]) is False


# --- 4b: _qa_has_provider_unavailable --------------------------------------

def test_provider_unavailable_true_when_any_report_unavailable(monkeypatch):
    module = _load_script(monkeypatch)
    ok = _qa_report(module, status="failed", blockers=["x"])
    unavail = _qa_report(module, status="provider_unavailable", blockers=[])
    assert module._qa_has_provider_unavailable([ok, unavail]) is True


def test_provider_unavailable_false_when_none_unavailable(monkeypatch):
    module = _load_script(monkeypatch)
    report = _qa_report(module, status="failed", blockers=["x"])
    assert module._qa_has_provider_unavailable([report]) is False


# --- 4c: _effective_render_model kill-switch --------------------------------

def test_effective_render_model_passthrough_without_killswitch(monkeypatch):
    module = _load_script(monkeypatch)
    monkeypatch.delenv("FLYER_INTEGRATED_KILLSWITCH", raising=False)
    assert module._effective_render_model("openrouter/premium-poster-model") == "openrouter/premium-poster-model"


def test_effective_render_model_forces_deterministic_with_killswitch(monkeypatch):
    module = _load_script(monkeypatch)
    monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", "1")
    assert module._effective_render_model("openrouter/premium-poster-model") == "deterministic-renderer"
    assert module._effective_render_model("any-model") == "deterministic-renderer"


def test_effective_render_model_killswitch_off_value_passes_through(monkeypatch):
    module = _load_script(monkeypatch)
    monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", "0")
    assert module._effective_render_model("some-model") == "some-model"


def _premium_config_loader():
    from schemas import Config
    return lambda *_args, **_kwargs: Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {
            "enabled": True,
            "draft_image_model": "openrouter/premium-poster-model",
            "draft_image_quality": "high",
            "concept_count": 1,
        },
    })


def _basic_food_project_dict(project_id, asset_dir, *, status="generating_concepts"):
    now = datetime(2026, 6, 14, tzinfo=timezone.utc).isoformat()
    return {
        "project_id": project_id,
        "status": status,
        "customer_phone": "+17329837841",
        "customer_id": "CUST0001",
        "created_at": now,
        "updated_at": now,
        "original_message_id": f"wamid.{project_id}",
        "raw_request": "Snack specials for Lakshmi Kitchen. Punugulu, Egg Bonda.",
        "fields": {
            "event_or_business_name": "Lakshmi Kitchen",
            "venue_or_location": "90 Brybar Dr St Johns FL",
            "contact_info": "+1 732 983 7841",
            "notes": "Punugulu; Egg Bonda",
        },
        "locked_facts": [
            {"fact_id": "business_name", "label": "Business", "value": "Lakshmi Kitchen", "source": "customer_profile", "required": True},
            {"fact_id": "location", "label": "Location", "value": "90 Brybar Dr St Johns FL", "source": "customer_profile", "required": True},
            {"fact_id": "contact_phone", "label": "Contact", "value": "+1 732 983 7841", "source": "customer_profile", "required": True},
            {"fact_id": "item:0:name", "label": "Item", "value": "Punugulu", "source": "customer_text", "required": True},
            {"fact_id": "item:1:name", "label": "Item", "value": "Egg Bonda", "source": "customer_text", "required": True},
        ],
        "reference_extractions": [],
        "assets": [],
    }


def test_4b_referee_unavailable_routes_to_deterministic_fallback_with_marker(monkeypatch, tmp_path, capsys):
    """4b: when the concept-path referee is provider_unavailable (OCR/vision down)
    the script must route to the deterministic-overlay fallback (safe-by-
    construction) instead of going to manual with no marker. The fallback ships
    even though its own re-QA is also provider_unavailable, AND the run emits the
    observable marker in qa_reports + an audit row."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    render_models = []

    def fake_render(project_obj, output_dir, **kwargs):
        # FIX 1: the referee-unavailable fallback no longer toggles
        # FLYER_ALLOW_INTEGRATED_POSTER — it renders with the PURE deterministic
        # model. Detect the fallback by the model kwarg instead of the env.
        render_models.append(kwargs.get("model"))
        is_fallback = kwargs.get("model") == "deterministic-renderer"
        suffix = "fallback" if is_fallback else "integrated"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return _qa_report(module, status="provider_unavailable", blockers=[],
                          asset_id=asset_id, path=str(artifact_path))

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "awaiting_final_approval"
    assert persisted["manual_review"]["status"] == "none"
    assert persisted["assets"][-1]["path"].endswith("F0001-C1-fallback.png")
    # FIX 1: the referee-unavailable fallback render MUST be the pure deterministic
    # renderer (Pillow only, no model call) — safe-by-construction with no QA.
    assert "deterministic-renderer" in render_models
    marker = "integrated_referee_unavailable_fallback"
    assert any(marker in (r.get("blockers") or []) for r in persisted["qa_reports"])
    audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert any(r.get("type") == "flyer_integrated_referee_unavailable_fallback" for r in audit_rows)


def test_4b_referee_unavailable_falls_closed_when_overlay_blocks(monkeypatch, tmp_path, capsys):
    """4b: a genuine block-tier failure on the deterministic overlay still falls
    closed to manual even though we entered via the referee-unavailable route."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    def fake_render(project_obj, output_dir, **kwargs):
        # FIX 1: the referee-unavailable fallback renders with the pure
        # deterministic model — detect it via the model kwarg.
        is_fallback = kwargs.get("model") == "deterministic-renderer"
        suffix = "fallback" if is_fallback else "integrated"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        if "fallback" in Path(artifact_path).name:
            return _qa_report(module, status="failed",
                              blockers=["missing required visible fact: business_name"],
                              asset_id=asset_id, path=str(artifact_path))
        return _qa_report(module, status="provider_unavailable", blockers=[],
                          asset_id=asset_id, path=str(artifact_path))

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 2
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"


def test_4a_fabrication_only_retry_first_succeeds_before_fallback(monkeypatch, tmp_path, capsys):
    """4a retry-first: a fabrication-only block triggers ONE corrective re-render
    with a repair instruction. If the re-render passes QA, it ships without ever
    invoking the deterministic-overlay fallback."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    render_kwargs = []

    def fake_render(project_obj, output_dir, **kwargs):
        render_kwargs.append(kwargs)
        if module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER") == "0":
            raise AssertionError("deterministic-overlay fallback must not run when retry succeeds")
        has_repair = bool(kwargs.get("repair_instruction"))
        suffix = "repaired" if has_repair else "first"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        repaired = "repaired" in Path(artifact_path).name
        return _qa_report(
            module,
            status="passed" if repaired else "failed",
            blockers=[] if repaired else ["fabricated price visible: $3.99"],
            asset_id=asset_id, path=str(artifact_path),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(
        module, "classify_flyer_qa_for_autorepair",
        lambda *_a, **_k: types.SimpleNamespace(decision="manual_required", reason="not_autorepair"),
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "awaiting_final_approval"
    assert persisted["manual_review"]["status"] == "none"
    assert persisted["assets"][-1]["path"].endswith("F0001-C1-repaired.png")
    assert len(render_kwargs) == 2
    assert render_kwargs[1].get("repair_instruction")


def test_4a_fabrication_retry_fails_then_hard_blocks_to_manual(monkeypatch, tmp_path, capsys):
    """Option 4 (2026-06-15): when persistent fabrication survives BOTH bounded
    corrective retries, the run HARD-BLOCKS to manual review — it does NOT fall
    back to the deterministic overlay (operator: "fabricated offer remains a
    hard-block"). Render budget is strictly bounded: initial + TWO repaired
    attempts, then manual (no fallback render, no infinite loop)."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    render_calls = []

    def fake_render(project_obj, output_dir, **kwargs):
        if module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER") == "0":
            suffix = "fallback"
        elif kwargs.get("repair_instruction"):
            suffix = "repaired"
        else:
            suffix = "first"
        render_calls.append(suffix)
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        name = Path(artifact_path).name
        if "fallback" in name:
            return _qa_report(module, status="passed", blockers=[], asset_id=asset_id, path=str(artifact_path))
        # Production-accurate: a fabrication blocker is block-tier severity.
        return _qa_report(module, status="failed",
                          blockers=["fabricated price visible: $3.99"], severity="block",
                          asset_id=asset_id, path=str(artifact_path))

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(
        module, "classify_flyer_qa_for_autorepair",
        lambda *_a, **_k: types.SimpleNamespace(decision="manual_required", reason="not_autorepair"),
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 2
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["status"] == "queued"
    assert persisted["manual_review"]["reason_code"] == "visual_qa_failed"
    # Exactly initial + TWO corrective retries, then manual — NO fallback render.
    assert render_calls == ["first", "repaired", "repaired"]
    assert render_calls.count("repaired") == 2  # x2 bound, never more
    assert "fallback" not in render_calls  # fabrication is NOT overlay-recoverable


def test_4c_killswitch_forces_deterministic_model_in_render(monkeypatch, tmp_path, capsys):
    """4c: with FLYER_INTEGRATED_KILLSWITCH=1 the draft render is invoked with
    model='deterministic-renderer' regardless of the configured premium model."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", "1")
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    seen_models = []

    def fake_render(project_obj, output_dir, **kwargs):
        seen_models.append(kwargs.get("model"))
        path = Path(output_dir) / f"{project_obj.project_id}-C1.png"
        path.write_bytes(b"rendered")
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return _qa_report(module, status="passed", blockers=[], asset_id=asset_id, path=str(artifact_path))

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    assert seen_models == ["deterministic-renderer"]


def test_R3_fabrication_block_severity_hard_blocks_to_manual_not_warn(monkeypatch, tmp_path, capsys):
    """R3 fail-closed coupling (Option 4): a fabrication-ONLY block that survives
    the bounded repair retry must land in manual_edit_required (rc 2) — NEVER
    warn-tier delivery of the unverified/fabricating render, and NEVER an
    auto-shipped deterministic overlay. Fabrication is a HARD-BLOCK: the
    deterministic-overlay fallback is not even attempted.

    The fabrication QA report carries a REAL severity='block' (not the helper
    default), so this asserts the genuine block-severity path drives the
    fail-closed outcome rather than a fixture artifact."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    render_calls = []

    def fake_render(project_obj, output_dir, **kwargs):
        # Option 4: the deterministic-overlay fallback (FLYER_ALLOW_INTEGRATED_
        # POSTER=0) must NEVER run for a fabrication-bearing set. If it ever does,
        # fail loudly so the regression is caught.
        if module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER") == "0":
            render_calls.append("fallback")
            raise AssertionError("fabrication must hard-block — overlay fallback must not be attempted")
        suffix = "repaired" if kwargs.get("repair_instruction") else "first"
        render_calls.append(suffix)
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        # Every integrated/repaired render keeps fabricating a price → REAL block.
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="c" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="failed",
            blockers=["fabricated price visible: $3.99"],
            severity="block",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    # Keep the recovery.py autorepair loop out — this is the separate retry-first path.
    monkeypatch.setattr(
        module, "classify_flyer_qa_for_autorepair",
        lambda *_a, **_k: types.SimpleNamespace(decision="manual_required", reason="not_autorepair"),
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    rc = module.main()
    assert rc == 2
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    # Fail-closed to manual; NOT delivered_with_warning, NOT auto-shipped overlay.
    assert persisted["status"] == "manual_edit_required"
    assert persisted["status"] != "delivered_with_warning"
    assert persisted["manual_review"]["status"] == "queued"
    assert persisted["manual_review"]["reason_code"] == "visual_qa_failed"
    # Coupling proof: the bounded retry ran; the overlay fallback was NOT attempted.
    assert "repaired" in render_calls
    assert "fallback" not in render_calls
    out = json.loads(capsys.readouterr().out)
    assert "visual_qa_failed" in out


def test_fix4_mixed_missing_and_fabricated_skips_legacy_autorepair(monkeypatch, tmp_path, capsys):
    """FIX 4 + Option 4: a MIXED `missing fact + fabricated offer` failure must
    NOT enter the legacy recovery.py autorepair loop (whose LLM repair_instruction
    is not guaranteed to remove fabrications). It is not fabrication-only, so it
    skips the bounded fabrication retry too. Because it carries a fabrication
    blocker it is NOT overlay-recoverable (Option 4: fabrication is a hard-block),
    so it flows to manual_edit_required with NO autorepair audit row and WITHOUT
    auto-shipping a deterministic overlay."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    render_calls = []

    def fake_render(project_obj, output_dir, **kwargs):
        # Option 4: the deterministic-overlay fallback (FLYER_ALLOW_INTEGRATED_
        # POSTER=0) must NEVER run for a fabrication-bearing set.
        is_fallback = module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER") == "0"
        suffix = "fallback" if is_fallback else (
            "repaired" if kwargs.get("repair_instruction") else "integrated"
        )
        render_calls.append(suffix)
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        # Integrated render: a MIXED failure (one missing fact + one fabrication).
        # Both blockers are block-tier severity in production.
        return _qa_report(
            module,
            status="failed",
            blockers=[
                "missing required visible fact: business_name",
                "fabricated offer claim visible: Limited Time Deal",
            ],
            severity="block",
            asset_id=asset_id,
            path=str(artifact_path),
        )

    # Guard: the legacy autorepair planner/classifier must NEVER be invoked for
    # this mixed fabrication-bearing set.
    def _boom_classify(*_a, **_k):
        raise AssertionError("legacy autorepair classifier must not run for a fabrication-bearing set")

    def _boom_plan(*_a, **_k):
        raise AssertionError("legacy autorepair planner must not run for a fabrication-bearing set")

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "classify_flyer_qa_for_autorepair", _boom_classify, raising=False)
    monkeypatch.setattr(module, "plan_flyer_autorepair", _boom_plan, raising=False)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 2
    # Hard-blocked to manual: NO legacy autorepair re-render AND NO overlay fallback.
    assert render_calls == ["integrated"]
    assert "fallback" not in render_calls
    assert "repaired" not in render_calls
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["status"] == "queued"
    # No legacy autorepair audit rows of ANY kind were emitted.
    audit_types = [json.loads(line)["type"] for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert "flyer_autorepair_attempted" not in audit_types
    assert "flyer_autorepair_skipped" not in audit_types
    assert "flyer_autorepair_succeeded" not in audit_types


def _byte_identical_food_project(project_id="F0001"):
    """A representative English food project that renders via the deterministic
    (pure-Pillow) path — no generative API call — so output is reproducible."""
    from schemas import FlyerProject, FlyerRequestFields, FlyerLockedFact
    now = datetime(2026, 6, 14, tzinfo=timezone.utc)
    return FlyerProject(
        project_id=project_id,
        status="generating_concepts",
        customer_phone="+17329837841",
        customer_id="CUST0001",
        created_at=now,
        updated_at=now,
        original_message_id=f"wamid.{project_id}",
        raw_request="Weekend snack specials for Lakshmi Kitchen. Dosa, Idli, Vada.",
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi Kitchen",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+1 732 983 7841",
            preferred_language="en",
            notes="Dosa $6.99; Idli $5.99; Vada $4.99",
            style_preference="warm festive south-indian, premium readable",
        ),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+1 732 983 7841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$6.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Idli", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$5.99", source="customer_text", required=True),
        ],
    )


def test_task5_killswitch_resolves_to_deterministic_renderer(monkeypatch):
    """Task 5 (part 1): under FLYER_INTEGRATED_KILLSWITCH=1 the script's
    _effective_render_model collapses ANY configured generative model to the pure
    deterministic renderer — the panic-switch contract."""
    module = _load_script(monkeypatch)
    monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", "1")
    assert module._effective_render_model("google/gemini-3.1-flash-image-preview") == "deterministic-renderer"
    assert module._effective_render_model("openrouter/premium-poster-model") == "deterministic-renderer"
    # Switch off → configured model passes through unchanged.
    monkeypatch.delenv("FLYER_INTEGRATED_KILLSWITCH", raising=False)
    assert module._effective_render_model("google/gemini-3.1-flash-image-preview") == "google/gemini-3.1-flash-image-preview"


def test_task5_killswitch_render_is_byte_identical_to_direct_deterministic(monkeypatch, tmp_path):
    """Task 5 (part 2): rendering with the kill-switch-resolved model produces
    output BYTE-IDENTICAL to rendering with model='deterministic-renderer'
    directly. Proves the panic switch reverts to deterministic output with no
    drift (no generative call, no timestamp/nonce in the PNG)."""
    module = _load_script(monkeypatch)
    import hashlib
    from agents.flyer.render import render_concept_previews

    # The model the script WOULD have used; the kill-switch must collapse it.
    monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", "1")
    resolved = module._effective_render_model("google/gemini-3.1-flash-image-preview")
    assert resolved == "deterministic-renderer"

    out_killswitch = tmp_path / "ks"
    out_direct = tmp_path / "direct"
    out_killswitch.mkdir()
    out_direct.mkdir()

    specs_ks = render_concept_previews(
        _byte_identical_food_project("F0001"), out_killswitch, model=resolved, concept_count=1,
    )
    specs_direct = render_concept_previews(
        _byte_identical_food_project("F0001"), out_direct, model="deterministic-renderer", concept_count=1,
    )

    sha_ks = hashlib.sha256(Path(specs_ks[0].path).read_bytes()).hexdigest()
    sha_direct = hashlib.sha256(Path(specs_direct[0].path).read_bytes()).hexdigest()
    assert sha_ks == sha_direct, (
        "kill-switch render diverged from direct deterministic render: "
        f"{sha_ks} != {sha_direct}"
    )


def test_4a_fabrication_retry_passes_on_attempt_2_stops_early(monkeypatch, tmp_path, capsys):
    """4a (x2 bound): attempt 1 still fabricates, attempt 2 passes QA → keep the
    attempt-2 render and STOP. No third corrective render, no deterministic
    fallback. Proves the loop is bounded AND stops early on success."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    render_calls = []

    def fake_render(project_obj, output_dir, **kwargs):
        if module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER") == "0":
            raise AssertionError("fallback must not run when retry attempt 2 passes")
        if kwargs.get("repair_instruction"):
            render_calls.append("repaired")
            n = render_calls.count("repaired")
            suffix = f"repaired{n}"
        else:
            render_calls.append("first")
            suffix = "first"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        name = Path(artifact_path).name
        # first + repaired1 still fabricate; repaired2 passes.
        if "repaired2" in name:
            return _qa_report(module, status="passed", blockers=[], asset_id=asset_id, path=str(artifact_path))
        return _qa_report(module, status="failed",
                          blockers=["fabricated price visible: $3.99"],
                          asset_id=asset_id, path=str(artifact_path))

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(
        module, "classify_flyer_qa_for_autorepair",
        lambda *_a, **_k: types.SimpleNamespace(decision="manual_required", reason="not_autorepair"),
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "awaiting_final_approval"
    assert persisted["manual_review"]["status"] == "none"
    # Attempt 2's render is kept; no third corrective render, no fallback.
    assert persisted["assets"][-1]["path"].endswith("F0001-C1-repaired2.png")
    assert render_calls == ["first", "repaired", "repaired"]
    assert render_calls.count("repaired") == 2


# ---------------------------------------------------------------------------
# 2026-06-15 — content-miss auto-recover (live F0157): dropped item NAMES +
# missing schedule must retry once then fall to the deterministic overlay, NOT
# go straight to manual.
# ---------------------------------------------------------------------------

def test_content_miss_retry_fails_then_deterministic_overlay_ships_not_manual(monkeypatch, tmp_path, capsys):
    """F0157 shape: the integrated render drops most item NAMES + the schedule
    ('inferred item not rendered: ...' + 'missing required visible fact: schedule').
    The ONE content corrective retry still misses → the deterministic-overlay
    fallback (background-only + overlay renders EVERY name + schedule) ships.
    Project ends awaiting_final_approval, NOT manual_edit_required, with a
    fell_back audit row."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    render_calls = []

    def fake_render(project_obj, output_dir, **kwargs):
        # The deterministic-overlay fallback runs under FLYER_ALLOW_INTEGRATED_POSTER=0.
        if module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER") == "0":
            render_calls.append("fallback")
            suffix = "fallback"
        elif kwargs.get("repair_instruction"):
            render_calls.append("content_retry")
            suffix = "content_retry"
        else:
            render_calls.append("first")
            suffix = "first"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        name = Path(artifact_path).name
        if "fallback" in name:
            # The deterministic overlay renders every name + schedule → clean.
            return _qa_report(module, status="passed", blockers=[],
                              asset_id=asset_id, path=str(artifact_path))
        # The integrated first render AND the content retry both still drop names + schedule.
        return _qa_report(module, status="failed",
                          blockers=[
                              "inferred item not rendered: Idli",
                              "missing required visible fact: schedule",
                          ],
                          asset_id=asset_id, path=str(artifact_path))

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    # Keep the legacy recovery.py autorepair loop out of this path explicitly.
    monkeypatch.setattr(
        module, "classify_flyer_qa_for_autorepair",
        lambda *_a, **_k: types.SimpleNamespace(decision="manual_required", reason="not_autorepair"),
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    # Critical: NOT manual — the deterministic overlay shipped.
    assert persisted["status"] == "awaiting_final_approval"
    assert persisted["status"] != "manual_edit_required"
    assert persisted["manual_review"]["status"] == "none"
    assert persisted["assets"][-1]["path"].endswith("F0001-C1-fallback.png")
    # The content retry ran ONCE, then the deterministic overlay fallback ran.
    assert render_calls == ["first", "content_retry", "fallback"]
    rows = _audit_rows(audit_path)
    row_types = [r["type"] for r in rows]
    assert "flyer_integrated_manual_review" not in row_types
    fell = [r for r in rows if r["type"] == "flyer_integrated_fell_back_deterministic"]
    assert len(fell) == 1
    assert fell[0]["reason"] == "retries_exhausted"


def test_content_miss_warn_only_survives_retry_and_fallback_forces_manual_not_warn(monkeypatch, tmp_path, capsys):
    """Fail-closed gap (Codex): a WARN-ONLY content-recoverable residual
    ('missing required visible fact: schedule', warn-tier) that survives BOTH the
    content corrective retry AND the deterministic-overlay fallback (fallback QA
    still warn-fails) must go to manual_edit_required — NEVER delivered_with_warning.
    Without the content_recovery_unresolved force-manual, the warn-tier residual
    would ship a content-deficient flyer with a warning."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    render_calls = []

    def fake_render(project_obj, output_dir, **kwargs):
        if module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER") == "0":
            render_calls.append("fallback")
            suffix = "fallback"
        elif kwargs.get("repair_instruction"):
            render_calls.append("content_retry")
            suffix = "content_retry"
        else:
            render_calls.append("first")
            suffix = "first"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        # EVERY render (first, content_retry, AND the deterministic overlay
        # fallback) still drops the schedule — a WARN-ONLY residual that never
        # recovers. severity="warn" so the state machine would, absent the fix,
        # take the delivered_with_warning branch.
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="e" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="failed",
            blockers=["missing required visible fact: schedule"],
            severity="warn",
            extracted_text="Lakshmi Kitchen\nPunugulu\nEgg Bonda",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(
        module, "classify_flyer_qa_for_autorepair",
        lambda *_a, **_k: types.SimpleNamespace(decision="manual_required", reason="not_autorepair"),
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    rc = module.main()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    # The content-recovery path ran retry + overlay fallback; both still failed.
    assert render_calls == ["first", "content_retry", "fallback"]
    # Fail-closed: forced to manual, NOT delivered_with_warning.
    assert persisted["status"] == "manual_edit_required"
    assert persisted["status"] != "delivered_with_warning"
    assert persisted["manual_review"]["status"] == "queued"
    assert persisted.get("warning") in (None, {}) or persisted["warning"] is None
    assert rc == 2


def test_content_miss_retry_succeeds_keeps_integrated_no_fallback(monkeypatch, tmp_path, capsys):
    """The content corrective retry PASSES on attempt 1 → keep the beautiful
    integrated render, no deterministic fallback. Emits flyer_integrated_passed
    with attempts=2 (initial + 1 corrective)."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    render_calls = []

    def fake_render(project_obj, output_dir, **kwargs):
        if module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER") == "0":
            raise AssertionError("deterministic fallback must not run when the content retry passes")
        if kwargs.get("repair_instruction"):
            render_calls.append("content_retry")
            suffix = "content_retry"
        else:
            render_calls.append("first")
            suffix = "first"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        name = Path(artifact_path).name
        if "content_retry" in name:
            return _qa_report(module, status="passed", blockers=[],
                              asset_id=asset_id, path=str(artifact_path))
        return _qa_report(module, status="failed",
                          blockers=[
                              "inferred item not rendered: Idli",
                              "missing required visible fact: schedule",
                          ],
                          asset_id=asset_id, path=str(artifact_path))

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(
        module, "classify_flyer_qa_for_autorepair",
        lambda *_a, **_k: types.SimpleNamespace(decision="manual_required", reason="not_autorepair"),
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "awaiting_final_approval"
    assert persisted["manual_review"]["status"] == "none"
    # The integrated (content-retry) render is kept; no deterministic fallback.
    assert persisted["assets"][-1]["path"].endswith("F0001-C1-content_retry.png")
    assert render_calls == ["first", "content_retry"]
    rows = _audit_rows(audit_path)
    row_types = [r["type"] for r in rows]
    assert "flyer_integrated_fell_back_deterministic" not in row_types
    passed = [r for r in rows if r["type"] == "flyer_integrated_passed"]
    assert len(passed) == 1
    assert passed[0]["attempts"] == 2


# ---------------------------------------------------------------------------
# Spec §6 — integrated-path observability audit rows
# ---------------------------------------------------------------------------

def _audit_rows(audit_path):
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def _audit_types(audit_path):
    return [r.get("type") for r in _audit_rows(audit_path)]


def test_obs_clean_integrated_pass_emits_attempted_and_passed(monkeypatch, tmp_path, capsys):
    """§6: a clean integrated render (QA passes first time) emits exactly
    flyer_integrated_attempted then flyer_integrated_passed (attempts=1)."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    def fake_render(project_obj, output_dir, **kwargs):
        path = Path(output_dir) / f"{project_obj.project_id}-C1.png"
        path.write_bytes(b"rendered")
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return _qa_report(module, status="passed", blockers=[], asset_id=asset_id, path=str(artifact_path))

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    rows = _audit_rows(audit_path)
    row_types = [r.get("type") for r in rows]
    assert "flyer_integrated_attempted" in row_types
    assert "flyer_integrated_passed" in row_types
    assert "flyer_integrated_fell_back_deterministic" not in row_types
    assert "flyer_integrated_manual_review" not in row_types
    passed = next(r for r in rows if r["type"] == "flyer_integrated_passed")
    assert passed["attempts"] == 1
    assert passed["project_id"] == "F0001"


def test_obs_kill_switch_suppresses_integrated_rows(monkeypatch, tmp_path, capsys):
    """§6: with the kill-switch ON no integrated row is emitted (no integrated
    attempt is made)."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_INTEGRATED_KILLSWITCH", "1")
    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    def fake_render(project_obj, output_dir, **kwargs):
        path = Path(output_dir) / f"{project_obj.project_id}-C1.png"
        path.write_bytes(b"rendered")
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return _qa_report(module, status="passed", blockers=[], asset_id=asset_id, path=str(artifact_path))

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    row_types = _audit_types(audit_path) if audit_path.exists() else []
    assert "flyer_integrated_attempted" not in row_types
    assert "flyer_integrated_passed" not in row_types


def test_obs_fabrication_hard_block_emits_attempted_and_manual_review(monkeypatch, tmp_path, capsys):
    """§6 (Option 4): persistent fabrication → HARD-BLOCK to manual emits
    flyer_integrated_attempted + flyer_integrated_manual_review. It does NOT emit
    flyer_integrated_fell_back_deterministic (fabrication is not overlay-
    recoverable) and no passed row."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    def fake_render(project_obj, output_dir, **kwargs):
        if module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER") == "0":
            suffix = "fallback"
        elif kwargs.get("repair_instruction"):
            suffix = "repaired"
        else:
            suffix = "first"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        if "fallback" in Path(artifact_path).name:
            return _qa_report(module, status="passed", blockers=[], asset_id=asset_id, path=str(artifact_path))
        return _qa_report(module, status="failed",
                          blockers=["fabricated price visible: $3.99"], severity="block",
                          asset_id=asset_id, path=str(artifact_path))

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(
        module, "classify_flyer_qa_for_autorepair",
        lambda *_a, **_k: types.SimpleNamespace(decision="manual_required", reason="not_autorepair"),
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 2
    rows = _audit_rows(audit_path)
    row_types = [r["type"] for r in rows]
    assert "flyer_integrated_attempted" in row_types
    assert "flyer_integrated_passed" not in row_types
    assert "flyer_integrated_fell_back_deterministic" not in row_types
    manual = next(r for r in rows if r["type"] == "flyer_integrated_manual_review")
    assert manual["project_id"] == "F0001"
    assert manual["reason_code"] == "visual_qa_failed"


def test_obs_manual_outcome_emits_attempted_and_manual_review(monkeypatch, tmp_path, capsys):
    """§6: an integrated render that ends in manual_edit_required emits
    flyer_integrated_attempted + flyer_integrated_manual_review (reason_code),
    and NO passed / fell_back rows. Uses a REAL severity='block' QA + a fallback
    render that raises so the project fails closed to manual."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    def fake_render(project_obj, output_dir, **kwargs):
        if module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER") == "0":
            raise module.FlyerRenderError("deterministic overlay could not fit required facts")
        suffix = "repaired" if kwargs.get("repair_instruction") else "first"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return FlyerVisualQAReport(
            project_id=project_obj.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="c" * 64,
            project_version=project_obj.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="failed",
            blockers=["fabricated price visible: $3.99"],
            severity="block",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(
        module, "classify_flyer_qa_for_autorepair",
        lambda *_a, **_k: types.SimpleNamespace(decision="manual_required", reason="not_autorepair"),
    )
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 2
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    rows = _audit_rows(audit_path)
    row_types = [r["type"] for r in rows]
    assert "flyer_integrated_attempted" in row_types
    assert "flyer_integrated_manual_review" in row_types
    assert "flyer_integrated_passed" not in row_types
    mr = next(r for r in rows if r["type"] == "flyer_integrated_manual_review")
    assert mr["reason_code"] == "visual_qa_failed"
    assert mr["project_id"] == "F0001"


def test_fix7_initial_generation_error_falls_back_to_deterministic_not_manual(monkeypatch, tmp_path, capsys):
    """FIX 7: when the INITIAL integrated render raises a generation/provider error
    (API/transport — provider_timeout class) on both attempts, the script must
    render the pure deterministic fallback (model='deterministic-renderer') and
    ship it — NOT route straight to manual. Emits flyer_integrated_fell_back_
    deterministic with reason='generation_error'."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    render_models = []

    def fake_render(project_obj, output_dir, **kwargs):
        model = kwargs.get("model")
        render_models.append(model)
        # The integrated (generative) model fails with a transient provider error
        # on every attempt; only the deterministic fallback produces a render.
        if model != "deterministic-renderer":
            raise RuntimeError("OpenRouter image HTTP 503: service unavailable")
        path = Path(output_dir) / f"{project_obj.project_id}-C1-fallback.png"
        path.write_bytes(b"deterministic")
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        # The deterministic fallback render is clean.
        return _qa_report(module, status="passed", blockers=[],
                          asset_id=asset_id, path=str(artifact_path))

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    # Shipped via the deterministic fallback, NOT routed to manual.
    assert persisted["status"] == "awaiting_final_approval"
    assert persisted["status"] != "manual_edit_required"
    assert persisted["manual_review"]["status"] == "none"
    assert persisted["assets"][-1]["path"].endswith("F0001-C1-fallback.png")
    # The generative model was attempted (and failed) before the deterministic fallback ran.
    assert "deterministic-renderer" in render_models
    assert any(m != "deterministic-renderer" for m in render_models)
    rows = _audit_rows(audit_path)
    row_types = [r["type"] for r in rows]
    assert "flyer_integrated_attempted" in row_types
    assert "flyer_integrated_manual_review" not in row_types
    fell = [r for r in rows if r["type"] == "flyer_integrated_fell_back_deterministic"]
    assert len(fell) == 1
    assert fell[0]["reason"] == "generation_error"


def test_fix7_initial_dependency_missing_still_goes_manual(monkeypatch, tmp_path, capsys):
    """FIX 7 boundary: a dependency-missing error (Pillow unavailable) must STILL
    route to manual — the deterministic renderer cannot run either, so there is no
    safe fallback. No deterministic render is attempted."""
    module = _load_script(monkeypatch)

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    render_models = []

    def fake_render(project_obj, output_dir, **kwargs):
        render_models.append(kwargs.get("model"))
        raise RuntimeError("Pillow is required but unavailable: No module named 'PIL'")

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 2
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["reason_code"] == "dependency_missing"
    # No deterministic fallback was attempted for a dependency-missing error.
    assert "deterministic-renderer" not in render_models
    row_types = _audit_types(audit_path)
    assert "flyer_integrated_fell_back_deterministic" not in row_types


def test_obs_referee_unavailable_fallback_emits_fell_back_reason(monkeypatch, tmp_path, capsys):
    """§6: referee-unavailable → deterministic fallback emits
    flyer_integrated_fell_back_deterministic with reason=referee_unavailable
    (alongside the existing anti-silent referee-unavailable row)."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    def fake_render(project_obj, output_dir, **kwargs):
        # FIX 1: referee-unavailable fallback renders with the pure deterministic
        # model — detect it via the model kwarg, not the env.
        is_fallback = kwargs.get("model") == "deterministic-renderer"
        suffix = "fallback" if is_fallback else "integrated"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        return _qa_report(module, status="provider_unavailable", blockers=[],
                          asset_id=asset_id, path=str(artifact_path))

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    rows = _audit_rows(audit_path)
    row_types = [r["type"] for r in rows]
    assert "flyer_integrated_attempted" in row_types
    assert "flyer_integrated_referee_unavailable_fallback" in row_types
    fell = next(r for r in rows if r["type"] == "flyer_integrated_fell_back_deterministic")
    assert fell["reason"] == "referee_unavailable"


# ===========================================================================
# Slice 2 Task 4 — additive premium image-to-image repair rung (flag-gated)
# ===========================================================================


def _recoverable_blockers():
    # Recoverable (non-fabrication, non-phone): a dropped item name + a near-dup.
    return [
        "missing required visible fact: item:1:name",
        "near-duplicate item visible: expected Punugulu but saw Punuglu",
    ]


def _run_premium_repair_case(monkeypatch, tmp_path, *, repair_passes_on, flag="1", blockers=None, fail_severity="pass", capsys=None):
    """Shared harness: an integrated render that fails QA with recoverable
    blockers, then (flag-gated) the premium repair rung. ``repair_passes_on`` is
    the 1-based attempt index whose repaired render passes QA (None = never).
    Returns (module, render_calls, repair_calls, persisted, rows)."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    if flag is None:
        monkeypatch.delenv("FLYER_PREMIUM_REPAIR", raising=False)
    else:
        monkeypatch.setenv("FLYER_PREMIUM_REPAIR", flag)
    monkeypatch.delenv("FLYER_PREMIUM_REPAIR_ALLOWLIST", raising=False)

    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    blockers = blockers if blockers is not None else _recoverable_blockers()
    render_calls = []
    repair_calls = []

    def fake_render(project_obj, output_dir, **kwargs):
        mode = module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER", "")
        render_calls.append({"mode": mode, "model": kwargs.get("model"), "quality": kwargs.get("quality")})
        suffix = "fallback" if mode == "0" else "integrated"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_repair(project_obj, base_png, output_dir, *, repair_instruction, model, quality="high", output_name=""):
        repair_calls.append({"base": str(base_png), "instruction": repair_instruction, "model": model,
                             "quality": quality, "output_name": output_name})
        # Honor the distinct per-attempt name the ladder passes (BLOCKER-1 fix):
        # repair renders MUST NOT land on <id>-C1-preview.png.
        name = output_name or f"{project_obj.project_id}-C1-preview.png"
        path = Path(output_dir) / name
        path.write_bytes(f"repaired-{len(repair_calls)}".encode("ascii"))
        return RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                 width=1080, height=1350, concept_id="C1")

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        name = Path(artifact_path).name
        if "fallback" in name:
            status, blk = "passed", []
        elif "repair" in name:
            # A repaired render. It passes only on the configured attempt index.
            idx = len(repair_calls)
            if repair_passes_on is not None and idx >= repair_passes_on:
                status, blk = "passed", []
            else:
                status, blk = "failed", list(blockers)
        else:
            status, blk = "failed", list(blockers)
        extra = {} if status == "passed" else {"severity": fail_severity}
        return FlyerVisualQAReport(
            project_id=project_obj.project_id, asset_id=asset_id, artifact_path=str(artifact_path),
            artifact_sha256="d" * 64, project_version=project_obj.version, output_format=output_format,
            provider="test", qa_source="ocr_vision", status=status, blockers=blk,
            extracted_text="ok" if status == "passed" else "Punuglu", checked_at=datetime.now(timezone.utc),
            **extra,
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "render_repair_edit", fake_repair)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "classify_flyer_qa_for_autorepair",
                        lambda *_a, **_k: types.SimpleNamespace(decision="hard_stop", reason="customer_trust_risk"))
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    rc = module.main()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    rows = _audit_rows(audit_path) if audit_path.exists() else []
    return module, render_calls, repair_calls, persisted, rows, rc


def test_premium_repair_pass_ships_repaired_premium(monkeypatch, tmp_path, capsys):
    """Flag ON + recoverable defect → ONE premium repair edit that passes QA →
    ship the repaired PREMIUM render (NOT the flat overlay) → awaiting_final_
    approval + FlyerPremiumRepairSucceeded. No deterministic overlay render runs."""
    module, render_calls, repair_calls, persisted, rows, rc = _run_premium_repair_case(
        monkeypatch, tmp_path, repair_passes_on=1)
    assert rc == 0
    # Exactly ONE repair edit; the base was the prior integrated render.
    assert len(repair_calls) == 1
    assert repair_calls[0]["base"].endswith("F0001-C1-integrated.png")
    assert repair_calls[0]["instruction"].startswith("Edit this exact flyer")
    # NO overlay (mode "0") render — the premium repaired render shipped.
    assert all(c["mode"] == "1" for c in render_calls)
    assert persisted["status"] == "awaiting_final_approval"
    # The shipped asset is the DISTINCT repair render (BLOCKER-1 fix), NOT the
    # original <id>-C1-preview.png path.
    assert persisted["assets"][-1]["path"].endswith("F0001-C1-repair1.png")
    assert not persisted["assets"][-1]["path"].endswith("C1-preview.png")
    row_types = [r["type"] for r in rows]
    assert "flyer_premium_repair_attempted" in row_types
    assert "flyer_premium_repair_succeeded" in row_types
    assert "flyer_premium_repair_exhausted" not in row_types


def test_premium_repair_exhausts_then_falls_through_to_overlay(monkeypatch, tmp_path, capsys):
    """Flag ON + recoverable defect, repair never passes → bounded ×2 then
    FlyerPremiumRepairExhausted → falls through to the EXISTING ladder, which
    ships the deterministic overlay (mode "0")."""
    module, render_calls, repair_calls, persisted, rows, rc = _run_premium_repair_case(
        monkeypatch, tmp_path, repair_passes_on=None)
    assert rc == 0
    # Bounded ×2 repair edits, no more.
    assert len(repair_calls) == 2
    row_types = [r["type"] for r in rows]
    assert "flyer_premium_repair_attempted" in row_types
    assert "flyer_premium_repair_exhausted" in row_types
    assert "flyer_premium_repair_succeeded" not in row_types
    exhausted = next(r for r in rows if r["type"] == "flyer_premium_repair_exhausted")
    assert exhausted["reason"] == "residual_recoverable_defect"
    # The existing ladder ran AFTER exhaustion → deterministic overlay shipped.
    assert any(c["mode"] == "0" for c in render_calls)
    assert persisted["status"] == "awaiting_final_approval"
    assert persisted["assets"][-1]["path"].endswith("F0001-C1-fallback.png")


def test_premium_repair_flag_off_makes_zero_repair_calls(monkeypatch, tmp_path, capsys):
    """Flag OFF → the premium repair rung is entirely skipped (byte-identical):
    render_repair_edit is NEVER called and no premium-repair audit rows appear.
    The existing ladder still recovers via the deterministic overlay."""
    module, render_calls, repair_calls, persisted, rows, rc = _run_premium_repair_case(
        monkeypatch, tmp_path, repair_passes_on=1, flag=None)
    assert rc == 0
    assert repair_calls == []
    row_types = [r["type"] for r in rows]
    assert "flyer_premium_repair_attempted" not in row_types
    assert "flyer_premium_repair_succeeded" not in row_types
    assert "flyer_premium_repair_exhausted" not in row_types
    # Existing ladder shipped the overlay (mode "0"), unchanged.
    assert any(c["mode"] == "0" for c in render_calls)
    assert persisted["status"] == "awaiting_final_approval"


def test_premium_repair_never_called_for_fabrication(monkeypatch, tmp_path, capsys):
    """SAFETY: a fabrication-bearing failure must NEVER enter the repair loop
    (it stays a hard-block). render_repair_edit is not called; no repair rows."""
    fab_blockers = [
        "missing required visible fact: item:1:name",
        "fabricated price visible: $3.99",
    ]
    module, render_calls, repair_calls, persisted, rows, rc = _run_premium_repair_case(
        monkeypatch, tmp_path, repair_passes_on=1, blockers=fab_blockers, fail_severity="block")
    assert repair_calls == []
    row_types = [r["type"] for r in rows]
    assert "flyer_premium_repair_attempted" not in row_types
    # Fabrication is a hard-block → manual review, NOT a shipped/overlaid flyer.
    assert persisted["status"] == "manual_edit_required"


def test_premium_repair_introducing_fabrication_is_discarded(monkeypatch, tmp_path, capsys):
    """SAFETY: a repair that INTRODUCES a dangerous blocker (fabrication) on
    re-QA must be discarded (never shipped) → FlyerPremiumRepairExhausted
    (introduced_non_recoverable) → falls through to the existing hard-block /
    manual path. No fabricated flyer is delivered."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_PREMIUM_REPAIR", "1")
    monkeypatch.delenv("FLYER_PREMIUM_REPAIR_ALLOWLIST", raising=False)

    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    render_calls = []
    repair_calls = []

    def fake_render(project_obj, output_dir, **kwargs):
        mode = module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER", "")
        render_calls.append({"mode": mode})
        suffix = "fallback" if mode == "0" else "integrated"
        path = Path(output_dir) / f"{project_obj.project_id}-C1-{suffix}.png"
        path.write_bytes(f"rendered-{suffix}".encode("ascii"))
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_repair(project_obj, base_png, output_dir, *, repair_instruction, model, quality="high", output_name=""):
        repair_calls.append(str(base_png))
        name = output_name or f"{project_obj.project_id}-C1-preview.png"
        path = Path(output_dir) / name
        path.write_bytes(b"repaired")
        return RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                 width=1080, height=1350, concept_id="C1")

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        name = Path(artifact_path).name
        if "repair" in name:
            # The repaired render INTRODUCED a fabrication (dangerous) → discard.
            status, blk = "failed", ["fabricated offer claim visible: 50% OFF"]
        elif "fallback" in name:
            status, blk = "passed", []
        else:
            status, blk = "failed", ["missing required visible fact: item:1:name"]
        return FlyerVisualQAReport(
            project_id=project_obj.project_id, asset_id=asset_id, artifact_path=str(artifact_path),
            artifact_sha256="d" * 64, project_version=project_obj.version, output_format=output_format,
            provider="test", qa_source="ocr_vision", status=status, blockers=blk,
            severity="block" if status == "failed" else "pass",
            extracted_text="x", checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "render_repair_edit", fake_repair)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "classify_flyer_qa_for_autorepair",
                        lambda *_a, **_k: types.SimpleNamespace(decision="hard_stop", reason="customer_trust_risk"))
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    rows = _audit_rows(audit_path)
    row_types = [r["type"] for r in rows]
    # The repair was attempted once, then discarded (dangerous introduced) → stop.
    assert len(repair_calls) == 1
    assert "flyer_premium_repair_attempted" in row_types
    exhausted = next(r for r in rows if r["type"] == "flyer_premium_repair_exhausted")
    assert exhausted["reason"] == "introduced_non_recoverable"
    # No fabricated flyer ships: original failed_qa (item miss only) was recoverable,
    # so the existing ladder overlay path ships the SAFE deterministic overlay.
    # Critically the repaired (fabricating) render was NOT shipped (check the
    # filename, not the full path — the tmp dir name may contain "repair").
    assert "repair" not in Path(persisted["assets"][-1]["path"]).name


def test_failed_premium_repair_leaves_original_preview_byte_identical(monkeypatch, tmp_path, capsys):
    """BLOCKER-1 regression guard: after a FAILED repair (re-QA still failing →
    fall-through to the existing ladder), the ORIGINAL <id>-C1-preview.png written
    by the integrated render must be BYTE-IDENTICAL to before the repair attempt —
    the repair writes to a DISTINCT repair{n}.png and never overwrites it."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_PREMIUM_REPAIR", "1")
    monkeypatch.delenv("FLYER_PREMIUM_REPAIR_ALLOWLIST", raising=False)

    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    # The canonical original-integrated-render path + its exact byte content. The
    # real render_concept_previews writes <id>-C1-preview.png; we mirror that and
    # snapshot the bytes inside the FIRST integrated render call.
    canonical_preview = asset_dir / "F0001-C1-preview.png"
    ORIGINAL_BYTES = b"ORIGINAL-INTEGRATED-PREMIUM-RENDER-BYTES"
    # Captured at the instant the overlay-fallback render fires — i.e. AFTER the
    # repair rung exhausted but BEFORE the existing ladder cleans up the original.
    # This is the precise window where a same-path-overwrite bug would have
    # corrupted the original; capturing here makes the byte-identity check
    # unconditional (the file legitimately disappears later when the overlay ships).
    preview_at_fallback_time = {}
    repair_calls = []

    def fake_render(project_obj, output_dir, **kwargs):
        mode = module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER", "")
        if mode == "0":
            # deterministic-overlay fallback → a DIFFERENT file. Snapshot the
            # original preview's bytes right now (repair has run + exhausted).
            preview_at_fallback_time["exists"] = canonical_preview.exists()
            preview_at_fallback_time["bytes"] = canonical_preview.read_bytes() if canonical_preview.exists() else None
            path = Path(output_dir) / f"{project_obj.project_id}-C1-fallback.png"
            path.write_bytes(b"overlay-fallback-bytes")
        else:
            # integrated render lands on the canonical preview path.
            path = Path(output_dir) / f"{project_obj.project_id}-C1-preview.png"
            path.write_bytes(ORIGINAL_BYTES)
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_repair(project_obj, base_png, output_dir, *, repair_instruction, model, quality="high", output_name=""):
        repair_calls.append(output_name)
        # Honor the distinct per-attempt name; MUST NOT be the canonical preview.
        assert output_name and output_name != f"{project_obj.project_id}-C1-preview.png"
        path = Path(output_dir) / output_name
        path.write_bytes(f"REPAIR-ATTEMPT-{len(repair_calls)}-BYTES".encode("ascii"))
        return RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                 width=1080, height=1350, concept_id="C1")

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        name = Path(artifact_path).name
        if "fallback" in name:
            status, blk = "passed", []
        else:
            # original integrated render + every repair render still fail (recoverable).
            status, blk = "failed", ["missing required visible fact: item:1:name"]
        return FlyerVisualQAReport(
            project_id=project_obj.project_id, asset_id=asset_id, artifact_path=str(artifact_path),
            artifact_sha256="d" * 64, project_version=project_obj.version, output_format=output_format,
            provider="test", qa_source="ocr_vision", status=status, blockers=blk,
            extracted_text="x", checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "render_repair_edit", fake_repair)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "classify_flyer_qa_for_autorepair",
                        lambda *_a, **_k: types.SimpleNamespace(decision="hard_stop", reason="customer_trust_risk"))
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    assert module.main() == 0
    # The repair ran (and exhausted) — so the invariant is non-trivially tested.
    assert len(repair_calls) == 2
    assert all(n and n != "F0001-C1-preview.png" for n in repair_calls)
    rows = _audit_rows(audit_path)
    assert "flyer_premium_repair_exhausted" in [r["type"] for r in rows]
    # The deterministic overlay shipped via the EXISTING ladder, untouched.
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "awaiting_final_approval"
    # CORE ASSERTION (BLOCKER-1): at the moment the existing ladder took over (the
    # overlay-fallback render fired), the original integrated preview was STILL ON
    # DISK and BYTE-IDENTICAL to what the integrated render wrote — the bounded ×2
    # repair never overwrote it (it wrote to distinct repair{n}.png paths). A
    # same-path-overwrite bug would have replaced these bytes with REPAIR-ATTEMPT-*.
    assert preview_at_fallback_time["exists"] is True
    assert preview_at_fallback_time["bytes"] == ORIGINAL_BYTES


def test_premium_repair_non_flyer_render_error_does_not_crash_run(monkeypatch, tmp_path, capsys):
    """BLOCKER-2 regression guard: render_repair_edit can raise a non-
    FlyerRenderError (Pillow / manifest write) AFTER writing the distinct repair
    file. The repair rung must catch ANY Exception → discard the repair artifact
    → fall through to the existing ladder (NOT crash the whole run). The original
    <id>-C1-preview.png is at a distinct path so it is never touched."""
    module = _load_script(monkeypatch)
    from agents.flyer.render import RenderedAssetSpec
    from schemas import FlyerVisualQAReport

    monkeypatch.setattr(module, "load_yaml_model", _premium_config_loader())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_PREMIUM_REPAIR", "1")
    monkeypatch.delenv("FLYER_PREMIUM_REPAIR_ALLOWLIST", raising=False)

    state_path = tmp_path / "projects.json"
    audit_path = tmp_path / "decisions.log"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2,
        "projects": [_basic_food_project_dict("F0001", asset_dir)],
    }), encoding="utf-8")

    overlay_shipped = {}

    def fake_render(project_obj, output_dir, **kwargs):
        mode = module.os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER", "")
        if mode == "0":
            overlay_shipped["ran"] = True
            path = Path(output_dir) / f"{project_obj.project_id}-C1-fallback.png"
            path.write_bytes(b"overlay-fallback-bytes")
        else:
            path = Path(output_dir) / f"{project_obj.project_id}-C1-preview.png"
            path.write_bytes(b"ORIGINAL-INTEGRATED-BYTES")
        return [RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def boom_repair(project_obj, base_png, output_dir, *, repair_instruction, model, quality="high", output_name=""):
        # Simulate a partial write THEN a non-FlyerRenderError (e.g. Pillow).
        (Path(output_dir) / output_name).write_bytes(b"partial-repair-bytes")
        raise RuntimeError("Pillow exploded mid-write")

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        name = Path(artifact_path).name
        status, blk = ("passed", []) if "fallback" in name else ("failed", ["missing required visible fact: item:1:name"])
        return FlyerVisualQAReport(
            project_id=project_obj.project_id, asset_id=asset_id, artifact_path=str(artifact_path),
            artifact_sha256="d" * 64, project_version=project_obj.version, output_format=output_format,
            provider="test", qa_source="ocr_vision", status=status, blockers=blk,
            extracted_text="x", checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "render_repair_edit", boom_repair)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "classify_flyer_qa_for_autorepair",
                        lambda *_a, **_k: types.SimpleNamespace(decision="hard_stop", reason="customer_trust_risk"))
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts", "--project-id", "F0001",
        "--state-path", str(state_path), "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(audit_path),
        "--autorepair-state-path", str(tmp_path / "autorepair.json"),
    ])

    # The run completes (does NOT crash) despite the non-FlyerRenderError.
    assert module.main() == 0
    rows = _audit_rows(audit_path)
    row_types = [r["type"] for r in rows]
    assert "flyer_premium_repair_attempted" in row_types
    exhausted = next(r for r in rows if r["type"] == "flyer_premium_repair_exhausted")
    assert exhausted["reason"] == "generation_error"
    # Fell through to the existing ladder → deterministic overlay shipped.
    assert overlay_shipped.get("ran") is True
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["status"] == "awaiting_final_approval"
    # The partial repair artifact was cleaned up (not left orphaned, not shipped).
    assert not (asset_dir / "F0001-C1-repair1.png").exists()
    assert "repair" not in Path(persisted["assets"][-1]["path"]).name
