"""Contracts for the sanctioned Flyer brand-asset activate/deactivate CLI.

§12b P1 pair (2026-07-11): manual edits reversing customer-applied brand-asset
state must leave an audit row. The 2026-06-17 wrong-brand deactivation was a
hand-edit to customers.json with ZERO audit rows — this script closes that gap.

In-process SourceFileLoader style (mirrors tests/test_flyer_create_project.py):
safe_io is stubbed with real-file-backed FileLock/atomic_write_text/flock/
ndjson_append so the state mutation and the audit row are actually written and
can be asserted — works on Windows (real safe_io imports fcntl, Linux-only).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import types
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "set-flyer-brand-asset-state"
PLATFORM = REPO / "src" / "platform"


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

    @contextmanager
    def _noop_flock(_path):
        yield

    def _append(path, line):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    fake_safe_io.flock = _noop_flock
    fake_safe_io.ndjson_append = _append
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)
    sys.path.insert(0, str(PLATFORM))
    module_name = "set_flyer_brand_asset_state_under_test"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(SCRIPT))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def _write_store(state_path: Path, *, root: Path, assets: list[dict]) -> None:
    now = datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()
    brand_assets = []
    for spec in assets:
        asset_path = root / "brand_assets" / f"{spec['asset_id']}.png"
        brand_assets.append({
            "asset_id": spec["asset_id"],
            "kind": spec.get("kind", "template"),
            "path": str(asset_path),
            "mime_type": "image/png",
            "sha256": "a" * 64,
            "original_message_id": f"m-{spec['asset_id']}",
            "received_at": now,
            "active": spec["active"],
            "notes": spec.get("notes", ""),
        })
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 2,
        "next_brand_asset_sequence": len(assets) + 1,
        "customers": [{
            "customer_id": "CUST0001",
            "business_name": "Lakshmi's Kitchen",
            "business_address": "90 Brybar Dr St Johns FL",
            "primary_chat_id": "17329837841@s.whatsapp.net",
            "onboarded_by_phone": "+17329837841",
            "public_phone": "+17329837841",
            "business_whatsapp_number": "+17329837841",
            "authorized_request_numbers": ["+17329837841"],
            "business_category": "Indian Restaurant",
            "preferred_language": "en",
            "plan_id": "trial",
            "status": "trial",
            "created_at": now,
            "updated_at": now,
            "activated_at": now,
            "monthly_flyers_used": 0,
            "billing_provider": "manual",
            "payment_currency": "USD",
            "brand_assets": brand_assets,
        }],
        "onboarding_sessions": [],
    }), encoding="utf-8")


def _argv(*, asset_id, action, actor, reason, state_path, log_path):
    return [
        "set-flyer-brand-asset-state",
        "--asset-id", asset_id,
        action,
        "--actor", actor,
        "--reason", reason,
        "--state-path", str(state_path),
        "--log-path", str(log_path),
    ]


def _read_audit_rows(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_deactivate_flips_active_persists_and_audits(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path, assets=[{"asset_id": "B0006", "active": True}])

    monkeypatch.setattr(sys, "argv", _argv(
        asset_id="B0006", action="--deactivate", actor="operator",
        reason="wrong-brand fix", state_path=state_path, log_path=log_path,
    ))
    assert module.main() == 0

    out = json.loads(capsys.readouterr().out)
    assert out == {
        "asset_id": "B0006",
        "customer_id": "CUST0001",
        "prior_active": True,
        "new_active": False,
    }

    # State file mutated: the asset is now inactive, other fields preserved.
    store = json.loads(state_path.read_text(encoding="utf-8"))
    asset = store["customers"][0]["brand_assets"][0]
    assert asset["active"] is False
    assert asset["sha256"] == "a" * 64
    assert asset["kind"] == "template"

    # Audit row written + validates against the new LogEntry variant.
    from schemas import FlyerBrandAssetStateChanged, LogEntry  # noqa: E402
    from pydantic import TypeAdapter  # noqa: E402

    rows = _read_audit_rows(log_path)
    assert len(rows) == 1
    parsed = TypeAdapter(LogEntry).validate_python(rows[0])
    assert parsed.__class__ is FlyerBrandAssetStateChanged
    assert parsed.type == "flyer_brand_asset_state_changed"
    assert parsed.asset_id == "B0006"
    assert parsed.customer_id == "CUST0001"
    assert parsed.prior_active is True
    assert parsed.new_active is False
    assert parsed.applied_by == "operator"
    assert parsed.reason == "wrong-brand fix"


def test_activate_flips_inactive_asset_and_audits(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path, assets=[{"asset_id": "B0006", "active": False}])

    monkeypatch.setattr(sys, "argv", _argv(
        asset_id="B0006", action="--activate", actor="system:pr1-reactivate",
        reason="scoped reactivation for live test", state_path=state_path, log_path=log_path,
    ))
    assert module.main() == 0

    out = json.loads(capsys.readouterr().out)
    assert out["prior_active"] is False
    assert out["new_active"] is True

    store = json.loads(state_path.read_text(encoding="utf-8"))
    assert store["customers"][0]["brand_assets"][0]["active"] is True

    rows = _read_audit_rows(log_path)
    assert len(rows) == 1
    assert rows[0]["type"] == "flyer_brand_asset_state_changed"
    assert rows[0]["applied_by"] == "system:pr1-reactivate"
    assert rows[0]["new_active"] is True


def test_already_in_requested_state_is_noop_with_notice(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path, assets=[{"asset_id": "B0006", "active": True}])
    before = state_path.read_text(encoding="utf-8")

    monkeypatch.setattr(sys, "argv", _argv(
        asset_id="B0006", action="--activate", actor="operator",
        reason="ensure active", state_path=state_path, log_path=log_path,
    ))
    assert module.main() == 0

    out = json.loads(capsys.readouterr().out)
    assert out["noop"] is True
    assert "already active" in out["notice"]
    assert out["prior_active"] is True
    assert out["new_active"] is True

    # No state mutation, no audit row on a no-op.
    assert state_path.read_text(encoding="utf-8") == before
    assert _read_audit_rows(log_path) == []


def test_unknown_asset_id_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path, assets=[{"asset_id": "B0006", "active": True}])

    monkeypatch.setattr(sys, "argv", _argv(
        asset_id="B9999", action="--deactivate", actor="operator",
        reason="typo", state_path=state_path, log_path=log_path,
    ))
    rc = module.main()
    assert rc != 0

    captured = capsys.readouterr()
    assert "B9999" in captured.err
    assert "unknown asset" in captured.err.lower()
    # Unknown asset touches nothing.
    assert _read_audit_rows(log_path) == []


def test_audit_append_failure_is_loud_never_silent(tmp_path, monkeypatch, capsys):
    """§12b core invariant: if the audit append fails, the script exits nonzero
    and says so — it never completes a state reversal silently."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path, assets=[{"asset_id": "B0006", "active": True}])

    def _boom(_path, _line):
        raise OSError("disk full")

    monkeypatch.setattr(module, "ndjson_append", _boom)
    monkeypatch.setattr(sys, "argv", _argv(
        asset_id="B0006", action="--deactivate", actor="operator",
        reason="wrong-brand fix", state_path=state_path, log_path=log_path,
    ))
    rc = module.main()
    assert rc != 0
    assert "audit" in capsys.readouterr().err.lower()


def test_flyer_brand_asset_state_changed_round_trips_through_log_entry():
    from schemas import FlyerBrandAssetStateChanged, LogEntry  # noqa: E402
    from pydantic import TypeAdapter  # noqa: E402

    now = datetime.now(timezone.utc)
    entry = FlyerBrandAssetStateChanged(
        ts=now,
        asset_id="B0007",
        customer_id="CUST0001",
        prior_active=True,
        new_active=False,
        applied_by="operator",
        reason="foreign masthead deactivation",
    )
    parsed = TypeAdapter(LogEntry).validate_python(entry.model_dump())
    # Must route to the typed variant, NOT _UnknownLogEntry.
    assert parsed.__class__ is FlyerBrandAssetStateChanged
    assert parsed.type == "flyer_brand_asset_state_changed"
    assert parsed.asset_id == "B0007"
    assert parsed.prior_active is True
    assert parsed.new_active is False
    assert parsed.applied_by == "operator"
