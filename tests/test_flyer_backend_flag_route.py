"""P0 #2 Commit 5 — flag_warn_tier_project_action audit-only helper.

Unit-tests the action helper that backs the POST /flyer/projects/{id}/flag
route. The helper validates project state + writes a
FlyerOperatorFlaggedWarnTier audit row WITHOUT mutating project state.

Loading the backend `flyer.py` module requires its parent package context
(web.backend.app — for `from ..audit import log` etc.). We synthesize the
parent packages with minimal stubs so the helper is reachable without
needing the full FastAPI app stack.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM = REPO / "src" / "platform"

sys.path.insert(0, str(PLATFORM))


def _stub_parent_packages(monkeypatch, tmp_path: Path) -> None:
    """Synthesize minimal parent packages so flyer.py's relative imports
    resolve. Uses monkeypatch.setitem for auto-restoration on test teardown —
    raw sys.modules[] assignment would leak fakes across tests and corrupt
    later runs (test_flyer_delivery_retry imports the real safe_io)."""

    def _set(name: str, module: types.ModuleType) -> None:
        monkeypatch.setitem(sys.modules, name, module)

    def _pkg(name: str) -> types.ModuleType:
        m = types.ModuleType(name)
        m.__path__ = []  # type: ignore[attr-defined]
        return m

    _set("web", _pkg("web"))
    _set("web.backend", _pkg("web.backend"))
    _set("web.backend.app", _pkg("web.backend.app"))

    audit_mod = types.ModuleType("web.backend.app.audit")
    audit_mod.log = lambda *a, **k: None
    _set("web.backend.app.audit", audit_mod)

    auth_mod = types.ModuleType("web.backend.app.auth")
    auth_mod.require_auth = lambda: None
    auth_mod.require_fresh_otp = lambda: None
    _set("web.backend.app.auth", auth_mod)

    config_mod = types.ModuleType("web.backend.app.config")

    class _Settings:
        decisions_path = tmp_path / "decisions.log"
        state_dir = tmp_path / "state"
    config_mod.get_settings = lambda: _Settings()
    _set("web.backend.app.config", config_mod)

    shell_mod = types.ModuleType("web.backend.app.shell")
    shell_mod.run_cli = lambda *a, **k: (0, "", "")
    _set("web.backend.app.shell", shell_mod)

    # safe_io — Unix-only (fcntl). Windows test runs need a stub.
    # Real safe_io implementations are tested elsewhere; this stub just
    # lets flyer.py import cleanly. AUTO-RESTORED via setitem so the
    # subsequent test files (which import the REAL safe_io) aren't polluted.
    safe_io_stub = types.ModuleType("safe_io")

    def _stub_ndjson_append(path, payload):
        Path(str(path)).parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "a", encoding="utf-8") as fh:
            fh.write(str(payload) + "\n")

    def _stub_load_model(path, model_cls, *, default=None):
        p = Path(str(path))
        if not p.exists():
            return default, "missing"
        data = json.loads(p.read_text(encoding="utf-8"))
        return model_cls.model_validate(data), "ok"

    def _stub_atomic_write_text(path, text):
        p = Path(str(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    class _StubFileLock:
        def __init__(self, _p): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False

    safe_io_stub.ndjson_append = _stub_ndjson_append
    safe_io_stub.load_model = _stub_load_model
    safe_io_stub.atomic_write_text = _stub_atomic_write_text
    safe_io_stub.FileLock = _StubFileLock
    safe_io_stub.bridge_post = lambda *a, **k: (True, "mid", "", "sent")
    safe_io_stub.bridge_send_media = lambda *a, **k: (True, "mid", "", "sent")
    _set("safe_io", safe_io_stub)


def _load_flyer_router(monkeypatch, tmp_path: Path):
    """Load web/backend/app/routers/flyer.py with synthesized parents."""
    _stub_parent_packages(monkeypatch, tmp_path)
    routers_pkg = types.ModuleType("web.backend.app.routers")
    routers_pkg.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "web.backend.app.routers", routers_pkg)
    monkeypatch.delitem(sys.modules, "web.backend.app.routers.flyer_under_test",
                        raising=False)
    spec = importlib.util.spec_from_file_location(
        "web.backend.app.routers.flyer_under_test",
        REPO / "web" / "backend" / "app" / "routers" / "flyer.py",
        submodule_search_locations=None,
    )
    if spec is None or spec.loader is None:
        pytest.skip("backend flyer router spec unavailable")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        pytest.skip(f"backend flyer router import failed: {type(exc).__name__}: {exc}")
    # Point projects.json at tmp_path
    flyer_state_dir = tmp_path / "state" / "flyer"
    flyer_state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(module, "_projects_path",
                        lambda: flyer_state_dir / "projects.json")
    return module


def _seed_project_state(
    state_dir: Path,
    project_id: str = "F0108",
    status: str = "delivered_with_warning",
    with_warning: bool = True,
) -> None:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc).isoformat()
    project = {
        "project_id": project_id, "status": status,
        "customer_phone": "+17329837841",
        "created_at": now, "updated_at": now,
        "original_message_id": f"wamid.{project_id}",
        "raw_request": "Create a flyer for Dosa Night.",
        "locked_facts": [
            {"fact_id": "business_name", "label": "Business",
             "value": "Lakshmi's Kitchen", "source": "customer_text", "required": True},
        ],
    }
    if with_warning:
        project["warning"] = {
            "severity": "warn",
            "blockers": ["visible wrong business/brand: Laksmi'S Kitchen"],
            "customer_text": "Here's your flyer draft. ...",
            "customer_text_sha256": "a" * 64,
            "delivered_at": now, "asset_id": "A0001",
            "classifier_version": "v1",
        }
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "projects.json").write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2, "projects": [project],
    }), encoding="utf-8")


def test_flag_warn_tier_writes_audit_row_no_state_mutation(monkeypatch, tmp_path):
    """Happy path: warn-tier project + valid note → ok=True; audit_append
    called with FlyerOperatorFlaggedWarnTier row; project state unchanged."""
    module = _load_flyer_router(monkeypatch, tmp_path)
    flyer_state_dir = tmp_path / "state" / "flyer"
    _seed_project_state(flyer_state_dir)

    captured: list[tuple] = []

    def fake_audit_append(path, payload):
        captured.append((str(path), payload))

    decisions_path = tmp_path / "decisions.log"
    result = module.flag_warn_tier_project_action(
        "F0108",
        note="Brand looks off here",
        audit_append=fake_audit_append,
        decisions_path=decisions_path,
        now_fn=lambda: datetime(2026, 5, 28, 15, 0, tzinfo=timezone.utc),
    )

    assert result["ok"] is True
    assert result["project_id"] == "F0108"
    assert result["project_status"] == "delivered_with_warning"
    audit_entry = result["audit_entry"]
    assert audit_entry["type"] == "flyer_operator_flagged_warn_tier"
    assert audit_entry["project_id"] == "F0108"
    assert audit_entry["flagged_by_operator_id"] == "cockpit"
    assert audit_entry["note"] == "Brand looks off here"
    assert len(captured) == 1
    assert captured[0][0] == str(decisions_path)
    row = json.loads(captured[0][1])
    assert row["type"] == "flyer_operator_flagged_warn_tier"
    assert row["project_id"] == "F0108"
    # Project state on disk UNCHANGED — no transition, no manual_review write
    persisted = json.loads(
        (flyer_state_dir / "projects.json").read_text(encoding="utf-8")
    )["projects"][0]
    assert persisted["status"] == "delivered_with_warning"
    assert persisted["warning"]["severity"] == "warn"


def test_flag_warn_tier_rejects_non_warn_tier_project(monkeypatch, tmp_path):
    """409-shape: project exists but isn't in delivered_with_warning state
    → raises ValueError('not_warn_tier'). Route layer maps to HTTP 409."""
    module = _load_flyer_router(monkeypatch, tmp_path)
    flyer_state_dir = tmp_path / "state" / "flyer"
    _seed_project_state(flyer_state_dir, status="awaiting_concept_selection",
                        with_warning=False)
    captured: list[tuple] = []
    with pytest.raises(ValueError) as exc:
        module.flag_warn_tier_project_action(
            "F0108", note="",
            audit_append=lambda *a: captured.append(a),
            decisions_path=tmp_path / "decisions.log",
            now_fn=lambda: datetime.now(timezone.utc),
        )
    assert str(exc.value) == "not_warn_tier"
    assert captured == []


def test_flag_warn_tier_rejects_missing_project(monkeypatch, tmp_path):
    """404-shape: project_id absent → raises ValueError('project_not_found')."""
    module = _load_flyer_router(monkeypatch, tmp_path)
    flyer_state_dir = tmp_path / "state" / "flyer"
    flyer_state_dir.mkdir(parents=True, exist_ok=True)
    (flyer_state_dir / "projects.json").write_text(
        json.dumps({"schema_version": 1, "next_sequence": 1, "projects": []}),
        encoding="utf-8",
    )
    captured: list[tuple] = []
    with pytest.raises(ValueError) as exc:
        module.flag_warn_tier_project_action(
            "F9999", note="",
            audit_append=lambda *a: captured.append(a),
            decisions_path=tmp_path / "decisions.log",
            now_fn=lambda: datetime.now(timezone.utc),
        )
    assert str(exc.value) == "project_not_found"
    assert captured == []


def test_flag_warn_tier_empty_note_accepted(monkeypatch, tmp_path):
    """Empty note is valid — the flag itself is the operator signal."""
    module = _load_flyer_router(monkeypatch, tmp_path)
    flyer_state_dir = tmp_path / "state" / "flyer"
    _seed_project_state(flyer_state_dir)
    captured: list[str] = []
    result = module.flag_warn_tier_project_action(
        "F0108", note="",
        audit_append=lambda _p, payload: captured.append(payload),
        decisions_path=tmp_path / "decisions.log",
        now_fn=lambda: datetime.now(timezone.utc),
    )
    assert result["ok"] is True
    row = json.loads(captured[0])
    assert row["note"] == ""


def test_flag_warn_tier_operator_id_override(monkeypatch, tmp_path):
    """operator_id parameter overrides the 'cockpit' default. Future operator-
    identity scheme can pass through here without a route-layer change."""
    module = _load_flyer_router(monkeypatch, tmp_path)
    flyer_state_dir = tmp_path / "state" / "flyer"
    _seed_project_state(flyer_state_dir)
    captured: list[str] = []
    module.flag_warn_tier_project_action(
        "F0108", note="x", operator_id="alice@cockpit",
        audit_append=lambda _p, payload: captured.append(payload),
        decisions_path=tmp_path / "decisions.log",
        now_fn=lambda: datetime.now(timezone.utc),
    )
    row = json.loads(captured[0])
    assert row["flagged_by_operator_id"] == "alice@cockpit"


def test_flag_warn_tier_audit_row_is_log_entry_routable(monkeypatch, tmp_path):
    """Written row deserializes through TypeAdapter(LogEntry) — confirms
    the action helper produces a row the audit-replay tooling consumes."""
    from pydantic import TypeAdapter
    from schemas import FlyerOperatorFlaggedWarnTier, LogEntry
    module = _load_flyer_router(monkeypatch, tmp_path)
    flyer_state_dir = tmp_path / "state" / "flyer"
    _seed_project_state(flyer_state_dir)
    captured: list[str] = []
    module.flag_warn_tier_project_action(
        "F0108", note="audit-replay smoke",
        audit_append=lambda _p, payload: captured.append(payload),
        decisions_path=tmp_path / "decisions.log",
        now_fn=lambda: datetime(2026, 5, 28, tzinfo=timezone.utc),
    )
    row = json.loads(captured[0])
    parsed = TypeAdapter(LogEntry).validate_python(row)
    assert isinstance(parsed, FlyerOperatorFlaggedWarnTier)
    assert parsed.note == "audit-replay smoke"
