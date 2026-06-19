"""Chokepoint glue: premium overlay outcome -> audit event + conditional alert."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "platform"))
sys.path.insert(0, str(ROOT / "src" / "agents" / "flyer"))

# Windows test hosts lack the Unix-only `fcntl` module that safe_io imports at
# top-level for file locking. These unit tests monkeypatch `_audit_append`, so
# the lock path is never exercised; stub `fcntl` so the script (which imports
# safe_io) can be loaded in-process. Production runs on Linux with real fcntl.
if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ModuleNotFoundError:
        import types as _types

        _fcntl_stub = _types.ModuleType("fcntl")
        _fcntl_stub.LOCK_EX = 2
        _fcntl_stub.LOCK_UN = 8
        _fcntl_stub.LOCK_NB = 4
        _fcntl_stub.flock = lambda *_a, **_k: None
        sys.modules["fcntl"] = _fcntl_stub


def _load_script():
    path = ROOT / "src" / "agents" / "flyer" / "scripts" / "generate-flyer-concepts"
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("genflyer_mod", loader=None, origin=str(path)))
    mod.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    return mod


def _outcome(status, reason_class="none", render_path="subprocess"):
    return SimpleNamespace(status=status, reason_class=reason_class, reason_detail="d",
                           render_path=render_path, output_format="concept_preview")


def _project():
    return SimpleNamespace(project_id="F0179", version=2)


def test_emit_delivered_records_event_no_alert(monkeypatch, tmp_path):
    mod = _load_script()
    rows, alerts = [], []
    monkeypatch.setattr(mod, "consume_premium_overlay_outcome", lambda: _outcome("premium_overlay_delivered"))
    monkeypatch.setattr(mod, "premium_outcome_should_alert", lambda o: False)
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    monkeypatch.setattr(mod, "_alert_owner", lambda msg: alerts.append(msg))
    mod._emit_premium_overlay_outcome(tmp_path / "decisions.log", _project())
    assert len(rows) == 1
    assert rows[0].type == "flyer_premium_overlay_outcome"
    assert rows[0].status == "premium_overlay_delivered"
    assert rows[0].render_path == "subprocess"
    assert alerts == []


def test_emit_failed_unexpected_records_and_alerts(monkeypatch, tmp_path):
    mod = _load_script()
    rows, alerts = [], []
    monkeypatch.setattr(mod, "consume_premium_overlay_outcome", lambda: _outcome("premium_overlay_failed_unexpected", "subprocess_failure", "none"))
    monkeypatch.setattr(mod, "premium_outcome_should_alert", lambda o: True)
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    monkeypatch.setattr(mod, "_alert_owner", lambda msg: alerts.append(msg))
    mod._emit_premium_overlay_outcome(tmp_path / "decisions.log", _project())
    assert rows[0].status == "premium_overlay_failed_unexpected"
    assert len(alerts) == 1 and "F0179" in alerts[0]


def test_emit_degraded_records_no_alert(monkeypatch, tmp_path):
    mod = _load_script()
    rows, alerts = [], []
    monkeypatch.setattr(mod, "consume_premium_overlay_outcome", lambda: _outcome("premium_overlay_degraded_to_flat", "fit", "none"))
    monkeypatch.setattr(mod, "premium_outcome_should_alert", lambda o: False)
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    monkeypatch.setattr(mod, "_alert_owner", lambda msg: alerts.append(msg))
    mod._emit_premium_overlay_outcome(tmp_path / "decisions.log", _project())
    assert rows[0].status == "premium_overlay_degraded_to_flat"
    assert alerts == []


def test_emit_none_does_nothing(monkeypatch, tmp_path):
    mod = _load_script()
    rows, alerts = [], []
    monkeypatch.setattr(mod, "consume_premium_overlay_outcome", lambda: None)
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    monkeypatch.setattr(mod, "_alert_owner", lambda msg: alerts.append(msg))
    mod._emit_premium_overlay_outcome(tmp_path / "decisions.log", _project())
    assert rows == [] and alerts == []
