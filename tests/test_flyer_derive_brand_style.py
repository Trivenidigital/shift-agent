"""Contracts for the derive-flyer-brand-style CLI (Workstream A, 2026-07-11).

In-process SourceFileLoader style (mirrors test_flyer_set_brand_asset_state.py):
safe_io is stubbed with real-file-backed FileLock/atomic_write_text/flock/
ndjson_append so the state mutation and the audit rows are actually written and
can be asserted — works on Windows (real safe_io imports fcntl, Linux-only). The
vision provider (`_derive_style_raw`) is monkeypatched so no network call fires.
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
SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "derive-flyer-brand-style"
PLATFORM = REPO / "src" / "platform"

SHA = "a" * 64


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
    module_name = "derive_flyer_brand_style_under_test"
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
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        if spec.get("write_file", True):
            asset_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake" * 4)
        row = {
            "asset_id": spec["asset_id"],
            "kind": spec.get("kind", "template"),
            "path": str(asset_path),
            "mime_type": "image/png",
            "sha256": SHA,
            "original_message_id": f"m-{spec['asset_id']}",
            "received_at": now,
            "active": spec.get("active", True),
            "notes": spec.get("notes", ""),
        }
        if "derived_style" in spec:
            row["derived_style"] = spec["derived_style"]
        brand_assets.append(row)
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


def _argv(*extra):
    return ["derive-flyer-brand-style", *extra]


def _read_audit_rows(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _bright_style() -> dict:
    return {
        "palette": ["warm cream", "tricolor green", "saffron orange"],
        "typography": "brush-script-headline",
        "energy": "busy",
        "motifs": ["marigold border", "food-photo strip"],
        "base_register": "festive-vernacular",
    }


def _run(module, monkeypatch, state_path, log_path, *extra, provider=None):
    if provider is not None:
        monkeypatch.setattr(module, "_derive_style_raw", provider)
    monkeypatch.setattr(sys, "argv", _argv(
        *extra, "--state-path", str(state_path), "--log-path", str(log_path)))
    return module.main()


def test_asset_id_derives_persists_and_audits_ok(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path, assets=[{"asset_id": "B0008", "active": True}])

    rc = _run(module, monkeypatch, state_path, log_path, "--asset-id", "B0008",
              provider=lambda *a, **k: _bright_style())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["derived"] == 1 and out["attempted"] == 1

    store = json.loads(state_path.read_text(encoding="utf-8"))
    ds = store["customers"][0]["brand_assets"][0]["derived_style"]
    assert ds is not None
    assert ds["typography"] == "brush-script-headline"
    assert ds["energy"] == "busy"
    assert ds["base_register"] == "festive-vernacular"
    assert ds["source_sha256"] and len(ds["source_sha256"]) == 64

    rows = _read_audit_rows(log_path)
    assert len(rows) == 1
    assert rows[0]["type"] == "flyer_brand_style_derived"
    assert rows[0]["ok"] is True
    assert rows[0]["asset_id"] == "B0008"
    assert rows[0]["screen_hits"] == []


def test_identity_import_screen_fails_closed_with_audit(tmp_path, monkeypatch, capsys):
    """A derived field that reuses the template's business identity is rejected —
    no derived_style persisted, audit row records the screen hit."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path, assets=[{"asset_id": "B0008", "active": True}])

    leaky = dict(_bright_style())
    leaky["motifs"] = ["Lakshmi's Kitchen masthead ribbon"]  # imports business name + org suffix

    rc = _run(module, monkeypatch, state_path, log_path, "--asset-id", "B0008",
              provider=lambda *a, **k: leaky)
    assert rc == 0
    store = json.loads(state_path.read_text(encoding="utf-8"))
    assert store["customers"][0]["brand_assets"][0].get("derived_style") is None

    rows = _read_audit_rows(log_path)
    assert len(rows) == 1 and rows[0]["ok"] is False
    assert any(h.startswith("identity_import:") for h in rows[0]["screen_hits"])


def test_no_fact_law_screen_fails_closed_with_audit(tmp_path, monkeypatch, capsys):
    """Derived STYLE carrying a digit/price/percent is rejected (facts belong to
    the locked-facts layer)."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path, assets=[{"asset_id": "B0008", "active": True}])

    facty = dict(_bright_style())
    facty["palette"] = ["warm cream", "50% off red banner"]  # price/percent leak

    rc = _run(module, monkeypatch, state_path, log_path, "--asset-id", "B0008",
              provider=lambda *a, **k: facty)
    assert rc == 0
    store = json.loads(state_path.read_text(encoding="utf-8"))
    assert store["customers"][0]["brand_assets"][0].get("derived_style") is None

    rows = _read_audit_rows(log_path)
    assert rows[0]["ok"] is False
    assert any(h.startswith("no_fact_law:") for h in rows[0]["screen_hits"])


def test_provider_unavailable_fails_open_no_persist(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path, assets=[{"asset_id": "B0008", "active": True}])

    rc = _run(module, monkeypatch, state_path, log_path, "--asset-id", "B0008",
              provider=lambda *a, **k: None)
    assert rc == 0
    store = json.loads(state_path.read_text(encoding="utf-8"))
    assert store["customers"][0]["brand_assets"][0].get("derived_style") is None
    rows = _read_audit_rows(log_path)
    assert rows[0]["ok"] is False
    assert rows[0]["screen_hits"] == ["provider_unavailable"]


def test_asset_id_rejects_logo(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path,
                 assets=[{"asset_id": "B0009", "kind": "logo", "active": True}])

    rc = _run(module, monkeypatch, state_path, log_path, "--asset-id", "B0009",
              provider=lambda *a, **k: _bright_style())
    assert rc != 0
    assert "not a template" in capsys.readouterr().err.lower()
    assert _read_audit_rows(log_path) == []


def test_asset_id_unknown_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path, assets=[{"asset_id": "B0008", "active": True}])

    rc = _run(module, monkeypatch, state_path, log_path, "--asset-id", "B9999",
              provider=lambda *a, **k: _bright_style())
    assert rc == 3
    assert "unknown asset" in capsys.readouterr().err.lower()


def test_backfill_all_skips_already_derived_and_logos(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    already = {
        "palette": ["cream"], "typography": "grotesque", "energy": "balanced",
        "motifs": [], "base_register": "", "derived_at": "2026-06-15T00:00:00+00:00",
        "source_sha256": SHA, "model": "old",
    }
    _write_store(state_path, root=tmp_path, assets=[
        {"asset_id": "B0006", "kind": "template", "active": True, "derived_style": already},
        {"asset_id": "B0007", "kind": "logo", "active": True},
        {"asset_id": "B0008", "kind": "template", "active": True},
    ])

    calls = {"n": 0}

    def _provider(*a, **k):
        calls["n"] += 1
        return _bright_style()

    rc = _run(module, monkeypatch, state_path, log_path, "--backfill-all", provider=_provider)
    assert rc == 0
    # Only B0008 (active template, no derived_style) should be derived.
    assert calls["n"] == 1
    out = json.loads(capsys.readouterr().out)
    assert out["derived"] == 1 and out["attempted"] == 1

    store = json.loads(state_path.read_text(encoding="utf-8"))
    by_id = {a["asset_id"]: a for a in store["customers"][0]["brand_assets"]}
    assert by_id["B0008"]["derived_style"]["typography"] == "brush-script-headline"
    assert by_id["B0006"]["derived_style"]["model"] == "old"  # untouched
    assert by_id["B0007"].get("derived_style") is None        # logo untouched


def test_customer_id_scopes_derivation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path, assets=[{"asset_id": "B0008", "active": True}])

    rc = _run(module, monkeypatch, state_path, log_path, "--customer-id", "CUST0001",
              provider=lambda *a, **k: _bright_style())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["derived"] == 1

    # An unrelated customer id derives nothing.
    _write_store(state_path, root=tmp_path, assets=[{"asset_id": "B0008", "active": True}])
    rc = _run(module, monkeypatch, state_path, log_path, "--customer-id", "CUST9999",
              provider=lambda *a, **k: _bright_style())
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["attempted"] == 0


def test_source_file_missing_audits_and_fails_open(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    module = _load_script(monkeypatch)
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    _write_store(state_path, root=tmp_path,
                 assets=[{"asset_id": "B0008", "active": True, "write_file": False}])

    rc = _run(module, monkeypatch, state_path, log_path, "--asset-id", "B0008",
              provider=lambda *a, **k: _bright_style())
    assert rc == 0
    rows = _read_audit_rows(log_path)
    assert rows[0]["ok"] is False and rows[0]["screen_hits"] == ["source_missing"]


def test_screen_and_build_units(tmp_path, monkeypatch):
    module = _load_script(monkeypatch)
    # clean input → no hits (generic lowercase style words, incl. "food-photo strip")
    assert module._screen_derived(_bright_style(), business_name="Lakshmi's Kitchen") == []
    # a lowercase generic suffix word is NOT a leak ("open-kitchen imagery" is style)
    assert module._screen_derived({"motifs": ["open kitchen imagery"]}, business_name="X") == []
    # a capitalized MASTHEAD phrase (proper noun + org suffix) IS an identity leak
    hits = module._screen_derived({"motifs": ["Taj Palace Restaurant banner"]}, business_name="X")
    assert any("org_suffix" in h for h in hits)
    # digit leak
    hits = module._screen_derived({"typography": "bold 3d letters"}, business_name="X")
    assert any(h.startswith("no_fact_law:") for h in hits)
    # energy coercion + sha carry-through
    now = datetime(2026, 7, 11, tzinfo=timezone.utc)
    ds = module._build_derived_style({"energy": "loud", "palette": ["cream"]},
                                     source_sha256=SHA, model="m", now=now)
    assert ds.energy == "balanced"
    assert ds.source_sha256 == SHA
