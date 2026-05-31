"""Script-level Expense Bookkeeper config-load integration tests.

These tests cover the PR #34 follow-up that unit-level `load_yaml_model`
coverage was not enough: the actual expense entry points must get past their
`config.yaml` load path. The assertions stop at the next deterministic boundary
so they never call vision, QBO, or the live WhatsApp bridge.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest
import yaml

if os.name == "nt" and "fcntl" not in sys.modules:
    _stub = types.ModuleType("fcntl")
    _stub.LOCK_EX = 2
    _stub.LOCK_UN = 8
    _stub.LOCK_NB = 4
    _stub.flock = lambda *a, **k: None
    sys.modules["fcntl"] = _stub

_REPO = Path(__file__).resolve().parent.parent
_PLATFORM = _REPO / "src" / "platform"
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

# Pin repo modules before loading deployed-style scripts that prepend
# /opt/shift-agent.
import audit_helpers  # noqa: E402,F401
import exit_codes  # noqa: E402,F401
import qbo_client  # noqa: E402,F401
import safe_io  # noqa: E402,F401
import schemas  # noqa: E402,F401

EXTRACT_SCRIPT = _REPO / "src" / "agents" / "expense_bookkeeper" / "scripts" / "extract-receipt"
APPLY_SCRIPT = _REPO / "src" / "agents" / "expense_bookkeeper" / "scripts" / "apply-expense-decision"


def _load_script(path: Path, module_name: str):
    from importlib.machinery import SourceFileLoader

    loader = SourceFileLoader(module_name, str(path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    mod = importlib.util.module_from_spec(spec)
    mod.__name__ = module_name
    loader.exec_module(mod)
    return mod


def _write_config(path: Path, *, enabled: bool = True) -> None:
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {
            "name": "Owner",
            "phone": "+19045550100",
            "self_chat_jid": "19045550100@s.whatsapp.net",
            "lid": "201975216009469@lid",
        },
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "expense_bookkeeper": {"enabled": enabled, "qbo_client_mode": "mock"},
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_extract_receipt_valid_yaml_config_reaches_image_validation(tmp_path, monkeypatch, capsys):
    mod = _load_script(EXTRACT_SCRIPT, "extract_receipt_config_load_test")
    _write_config(tmp_path / "config.yaml", enabled=True)
    (tmp_path / "state" / "expense-bookkeeper").mkdir(parents=True)
    (tmp_path / "logs").mkdir()

    monkeypatch.setattr(mod, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(mod, "LEADS_PATH", tmp_path / "state" / "expense-bookkeeper" / "leads.json")
    monkeypatch.setattr(mod, "LEADS_LOCK", tmp_path / "state" / "expense-bookkeeper" / "leads.json.lock")
    monkeypatch.setattr(mod, "RECEIPTS_DIR", tmp_path / "state" / "expense-bookkeeper" / "receipts")
    monkeypatch.setattr(mod, "LOG_PATH", tmp_path / "logs" / "decisions.log")
    monkeypatch.setattr(sys, "argv", [
        "extract-receipt",
        "--image-path", str(tmp_path / "missing.jpg"),
        "--source-image-id", "wamid.test",
        "--owner-phone", "+19045550100",
    ])

    assert mod.main() == mod.EXIT_INVALID_INPUT

    captured = capsys.readouterr()
    assert "image not found" in captured.err
    assert "config load failed" not in captured.err
    assert not list(tmp_path.glob("config.yaml.corrupt-*"))


def test_apply_expense_decision_valid_yaml_config_reaches_message_parser(tmp_path, monkeypatch, capsys):
    mod = _load_script(APPLY_SCRIPT, "apply_expense_decision_config_load_test")
    _write_config(tmp_path / "config.yaml", enabled=True)
    (tmp_path / "state" / "expense-bookkeeper").mkdir(parents=True)
    (tmp_path / "logs").mkdir()

    sends: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(mod, "LEADS_PATH", tmp_path / "state" / "expense-bookkeeper" / "leads.json")
    monkeypatch.setattr(mod, "LEADS_LOCK", tmp_path / "state" / "expense-bookkeeper" / "leads.json.lock")
    monkeypatch.setattr(mod, "LOG_PATH", tmp_path / "logs" / "decisions.log")
    monkeypatch.setattr(mod, "_bridge_post", lambda jid, msg: sends.append((jid, msg)) or (True, "dry-run"))
    monkeypatch.setattr(sys, "argv", [
        "apply-expense-decision",
        "--raw-message", "not a code",
        "--sender-phone", "+19045550100",
    ])

    assert mod.main() == mod.EXIT_INVALID_INPUT

    captured = capsys.readouterr()
    assert "config load failed" not in captured.err
    assert sends and "I didn't recognise that" in sends[0][1]
    assert not list(tmp_path.glob("config.yaml.corrupt-*"))
