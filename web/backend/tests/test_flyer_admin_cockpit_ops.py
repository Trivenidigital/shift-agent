"""P0-1/P0-2/P0-3 cockpit-ops slice tests.

Covers the new manual-queue detail endpoint, operator-uploads endpoint
(multipart + MIME/size/path validation), and the project asset
media-serve endpoint used for thumbnail rendering in the cockpit drawer.
"""
from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────
# Test helpers (lightweight; mirror test_flyer_admin.py conventions)
# ─────────────────────────────────────────────────────────────────


def _write_json(path: Path, data: dict) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _queued_project(
    project_id: str,
    *,
    phone: str = "+19045550104",
    reason_code: str = "source_edit_provider_unavailable",
    updated_at: str = "2026-05-18T20:00:00Z",
) -> dict:
    return {
        "project_id": project_id,
        "status": "manual_edit_required",
        "customer_phone": phone,
        "created_at": "2026-05-18T19:00:00Z",
        "updated_at": updated_at,
        "original_message_id": f"msg-{project_id}",
        "raw_request": "Authorized flyer/source artwork update. Replace phone number.",
        "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": phone},
        "manual_review": {
            "status": "queued",
            "reason": reason_code,
            "reason_code": reason_code,
            "detail": "queued for designer review",
            "queued_at": updated_at,
        },
        "assets": [],
        "concepts": [],
        "selected_concept_id": None,
        "revisions": [],
        "version": 1,
        "final_asset_ids": [],
        "approved_message_id": "",
    }


def _seed_queue(tmp_path: Path, projects: list[dict]) -> None:
    from app.routers import flyer
    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {"schema_version": 1, "next_sequence": len(projects) + 1, "projects": projects},
    )


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _UploadFileStub:
    """Minimal UploadFile shim for direct unit testing of the upload helper.

    `_validate_and_persist_operator_upload` only consults `content_type`,
    `filename`, and `await read(n)`. Mirror that surface so we can drive
    the helper without spinning up the full HTTP stack."""
    def __init__(self, *, content: bytes, content_type: str, filename: str = "test.png"):
        self.content_type = content_type
        self.filename = filename
        self._buf = io.BytesIO(content)

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


def _build_test_client():
    """FastAPI TestClient with require_auth/require_fresh_otp bypassed.

    NOTE: dependency-override fns must NOT take any unannotated params.
    Any untyped param becomes a required query parameter, producing 422
    on every call. Easiest is a zero-arg override since we don't use the
    request in tests."""
    from fastapi.testclient import TestClient
    from app import auth as auth_mod
    from app.main import app
    from app.routers import flyer as flyer_router

    async def _bypass_auth():
        return {"sub": "test-operator", "iat": 9_999_999_999}

    async def _bypass_fresh():
        return {"sub": "test-operator", "iat": 9_999_999_999}

    app.dependency_overrides[auth_mod.require_auth] = _bypass_auth
    app.dependency_overrides[auth_mod.require_fresh_otp] = _bypass_fresh
    app.dependency_overrides[flyer_router._require_auth_dep] = _bypass_auth
    app.dependency_overrides[flyer_router._require_fresh_otp_dep] = _bypass_fresh

    class _Ctx:
        def __enter__(self):
            self.client = TestClient(app)
            return self.client
        def __exit__(self, *args):
            self.client.close()
            app.dependency_overrides.clear()
    return _Ctx()


# ─────────────────────────────────────────────────────────────────
# POST /flyer/operator-uploads
# ─────────────────────────────────────────────────────────────────


def test_operator_upload_persists_under_operator_uploads_root(tmp_path):
    from app.routers import flyer
    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    (settings.state_dir / "flyer").mkdir(parents=True, exist_ok=True)

    payload = b"\x89PNG\r\n\x1a\nfake-bytes"
    upload = _UploadFileStub(content=payload, content_type="image/png", filename="customer-flyer.png")
    target, mime, size = _run_async(flyer._validate_and_persist_operator_upload(upload))

    assert target.is_file()
    expected_root = (settings.state_dir / "flyer" / "operator-uploads").resolve()
    assert target.parent == expected_root
    assert mime == "image/png"
    assert size == len(payload)
    # Filename is server-generated; operator cannot influence path.
    assert flyer._OPERATOR_UPLOAD_NAME_RE.match(target.name), target.name
    assert "customer-flyer.png" not in target.name


def test_operator_upload_rejects_disallowed_mime(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer
    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"

    upload = _UploadFileStub(content=b"#!/bin/sh\necho hi", content_type="text/x-shellscript", filename="x.sh")
    with pytest.raises(HTTPException) as exc:
        _run_async(flyer._validate_and_persist_operator_upload(upload))
    assert exc.value.status_code == 415
    upload_dir = settings.state_dir / "flyer" / "operator-uploads"
    if upload_dir.exists():
        assert not list(upload_dir.iterdir()), "disallowed MIME must not write to disk"


def test_operator_upload_rejects_oversize(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from app.routers import flyer
    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"

    monkeypatch.setattr(flyer, "_OPERATOR_UPLOAD_MAX_BYTES", 128)
    upload = _UploadFileStub(content=b"A" * 200, content_type="image/png")
    with pytest.raises(HTTPException) as exc:
        _run_async(flyer._validate_and_persist_operator_upload(upload))
    assert exc.value.status_code == 413


def test_operator_upload_rejects_empty_body(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer
    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"

    upload = _UploadFileStub(content=b"", content_type="image/png")
    with pytest.raises(HTTPException) as exc:
        _run_async(flyer._validate_and_persist_operator_upload(upload))
    assert exc.value.status_code == 422


def test_operator_upload_target_pattern_rejects_traversal_and_arbitrary_names(tmp_path):
    """Pin the regex behavior protecting the GET media-serve endpoint:
    only server-generated filenames may be resolved. Anything operator-
    supplied or path-traversal-shaped must be rejected."""
    from fastapi import HTTPException
    from app.routers import flyer
    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"

    for bad in [
        "../passwd",
        "..\\etc\\passwd",
        "20260519T120000Z-abc.png",
        "20260519T120000Z-" + "f" * 17 + ".png",
        "20260519T120000Z-" + "f" * 16 + ".exe",
        "20260519T120000Z-XYZQRSTUFGABCDEF0.png",
        "Flyer.png",
        "/etc/passwd",
        "",
    ]:
        with pytest.raises(HTTPException) as exc:
            flyer._safe_operator_upload_target(bad)
        assert exc.value.status_code == 422, (bad, exc.value.status_code, exc.value.detail)


def test_operator_upload_generated_filename_matches_pattern():
    from app.routers import flyer
    for mime in ("image/png", "image/jpeg", "image/webp", "application/pdf"):
        name = flyer._generate_operator_upload_filename(mime)
        assert flyer._OPERATOR_UPLOAD_NAME_RE.match(name), (mime, name)


# ─────────────────────────────────────────────────────────────────
# GET /flyer/manual-queue/{project_id}/detail
# ─────────────────────────────────────────────────────────────────


def test_manual_queue_detail_returns_rich_context_for_queued_project(tmp_path):
    from app.routers import flyer

    flyer_dir = tmp_path / "state" / "flyer"
    flyer_dir.mkdir(parents=True, exist_ok=True)
    asset_path = flyer_dir / "assets" / "F0058-ref.jpg"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(b"\xff\xd8fake-jpeg")
    queued = _queued_project("F0058", phone="+19045550104")
    queued["assets"] = [{
        "asset_id": "A0001",
        "kind": "reference_image",
        "source": "whatsapp",
        "path": str(asset_path),
        "mime_type": "image/jpeg",
        "sha256": "a" * 64,
        "original_message_id": "msg-ref",
        "received_at": "2026-05-19T21:04:04Z",
        "delivery_status": "pending",
        "outbound_message_id": "",
        "delivered_at": None,
        "delivery_attempt_count": 0,
        "delivery_error": "",
    }]
    os.environ["FLYER_STATE_ROOT"] = str(flyer_dir)
    try:
        _seed_queue(tmp_path, [queued])
        detail = flyer.manual_queue_detail_action("F0058")
    finally:
        os.environ.pop("FLYER_STATE_ROOT", None)

    assert detail["project_id"] == "F0058"
    assert detail["customer_phone"] == "+19045550104"
    assert detail["status"] == "manual_edit_required"
    assert "Authorized flyer" in detail["raw_request"]
    assert detail["manual_review"]["reason_code"] == "source_edit_provider_unavailable"
    assert detail["manual_review"]["status"] == "queued"
    assert len(detail["assets"]) == 1
    asset = detail["assets"][0]
    assert asset["asset_id"] == "A0001"
    # Detail returns the frontend-facing URL (proxied via /api by the cockpit shell).
    assert asset["media_url"] == "/api/flyer/projects/F0058/assets/A0001"
    assert "path" not in asset, "raw VPS path must not leak to the cockpit"
    events = [row["event"] for row in detail["timeline"]]
    assert "project_created" in events
    assert "manual_review_queued" in events


def test_manual_queue_detail_404_for_unknown_project(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer
    _seed_queue(tmp_path, [])
    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_detail_action("F9999")
    assert exc.value.status_code == 404


def test_manual_queue_detail_isolation_does_not_leak_other_project(tmp_path):
    """Querying F0058's detail must NOT return F0050's data even when both
    rows live in the same store."""
    from app.routers import flyer
    _seed_queue(tmp_path, [
        _queued_project("F0058", phone="+19045550104"),
        _queued_project("F0050", phone="+19048626362"),
    ])
    detail = flyer.manual_queue_detail_action("F0058")
    assert detail["project_id"] == "F0058"
    assert detail["customer_phone"] == "+19045550104"
    assert "+19048626362" not in str(detail)


# ─────────────────────────────────────────────────────────────────
# GET /flyer/projects/{project_id}/assets/{asset_id} (media-serve)
# ─────────────────────────────────────────────────────────────────


def test_project_asset_media_serves_owned_asset(tmp_path):
    flyer_dir = tmp_path / "state" / "flyer"
    (flyer_dir / "assets").mkdir(parents=True, exist_ok=True)
    asset_bytes = b"\xff\xd8\xff\xe0fake-jpeg"
    asset_path = flyer_dir / "assets" / "F0058-ref.jpg"
    asset_path.write_bytes(asset_bytes)
    queued = _queued_project("F0058", phone="+19045550104")
    queued["assets"] = [{
        "asset_id": "A0001",
        "kind": "reference_image",
        "source": "whatsapp",
        "path": str(asset_path),
        "mime_type": "image/jpeg",
        "sha256": "b" * 64,
        "received_at": "2026-05-19T21:04:04Z",
        "delivery_status": "pending",
    }]
    os.environ["FLYER_STATE_ROOT"] = str(flyer_dir)
    try:
        _seed_queue(tmp_path, [queued])
        with _build_test_client() as client:
            resp = client.get("/flyer/projects/F0058/assets/A0001")
    finally:
        os.environ.pop("FLYER_STATE_ROOT", None)

    assert resp.status_code == 200, resp.text
    assert resp.content == asset_bytes
    assert resp.headers.get("content-type", "").startswith("image/jpeg")


def test_project_asset_media_404_for_unknown_asset(tmp_path):
    _seed_queue(tmp_path, [_queued_project("F0058", phone="+19045550104")])
    with _build_test_client() as client:
        resp = client.get("/flyer/projects/F0058/assets/A9999")
    assert resp.status_code == 404


def test_project_asset_media_404_for_unknown_project(tmp_path):
    _seed_queue(tmp_path, [])
    with _build_test_client() as client:
        resp = client.get("/flyer/projects/F0058/assets/A0001")
    assert resp.status_code == 404


def test_project_asset_media_rejects_cross_project_asset_id(tmp_path):
    """asset_ids are per-project (NOT globally unique). F0058/A0001 must
    not serve F0050's A0001 even when both exist in the same store."""
    flyer_dir = tmp_path / "state" / "flyer"
    (flyer_dir / "assets").mkdir(parents=True, exist_ok=True)
    asset_a = flyer_dir / "assets" / "F0050-ref.jpg"
    asset_a.write_bytes(b"customer-A-bytes")
    proj_a = _queued_project("F0050", phone="+19048626362")
    proj_a["assets"] = [{
        "asset_id": "A0001",
        "kind": "reference_image",
        "source": "whatsapp",
        "path": str(asset_a),
        "mime_type": "image/jpeg",
        "sha256": "c" * 64,
        "received_at": "2026-05-19T21:04:04Z",
        "delivery_status": "pending",
    }]
    proj_b = _queued_project("F0058", phone="+19045550104")
    os.environ["FLYER_STATE_ROOT"] = str(flyer_dir)
    try:
        _seed_queue(tmp_path, [proj_a, proj_b])
        with _build_test_client() as client:
            resp = client.get("/flyer/projects/F0058/assets/A0001")
    finally:
        os.environ.pop("FLYER_STATE_ROOT", None)
    assert resp.status_code == 404, "must not serve F0050's A0001 under F0058's URL"


def test_project_asset_media_rejects_malformed_ids(tmp_path):
    _seed_queue(tmp_path, [_queued_project("F0058", phone="+19045550104")])
    with _build_test_client() as client:
        for bad in [
            "/flyer/projects/notanid/assets/A0001",
            "/flyer/projects/F0058/assets/notanid",
            "/flyer/projects/F58/assets/A0001",
        ]:
            resp = client.get(bad)
            assert resp.status_code == 422, (bad, resp.status_code)


# ─────────────────────────────────────────────────────────────────
# GET /flyer/operator-uploads/{filename} (uploaded preview)
# ─────────────────────────────────────────────────────────────────


def test_operator_upload_media_serves_well_named_file(tmp_path):
    from app.routers import flyer
    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    upload_dir = settings.state_dir / "flyer" / "operator-uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    name = flyer._generate_operator_upload_filename("image/png")
    payload = b"\x89PNG\r\n\x1a\noperator-preview"
    (upload_dir / name).write_bytes(payload)
    with _build_test_client() as client:
        resp = client.get(f"/flyer/operator-uploads/{name}")
    assert resp.status_code == 200
    assert resp.content == payload


def test_operator_upload_media_rejects_bad_filenames(tmp_path):
    from app.routers import flyer
    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    (settings.state_dir / "flyer" / "operator-uploads").mkdir(parents=True, exist_ok=True)
    with _build_test_client() as client:
        for bad_name in [
            "random.png",
            "projects.json",
            "20260519T120000Z-XYZQRSTUFGABCDEF0.png",  # non-hex random suffix
        ]:
            resp = client.get(f"/flyer/operator-uploads/{bad_name}")
            assert resp.status_code in (422, 404), (bad_name, resp.status_code)
