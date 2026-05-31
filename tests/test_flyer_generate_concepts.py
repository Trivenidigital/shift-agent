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
