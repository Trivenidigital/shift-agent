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
