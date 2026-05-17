"""Contracts for Flyer Studio project creation."""
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
SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "create-flyer-project"
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
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)
    sys.path.insert(0, str(PLATFORM))
    module_name = "create_flyer_project_under_test"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(SCRIPT))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def test_create_project_hydrates_missing_contact_from_trial_customer(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 2,
        "customers": [{
            "customer_id": "CUST0001",
            "business_name": "Lakshmi Kitchen",
            "business_address": "90 Bry Bar",
            "primary_chat_id": "84593927557152@lid",
            "onboarded_by_phone": "+19802005022",
            "public_phone": "+19802005022",
            "business_whatsapp_number": "+19802005022",
            "authorized_request_numbers": ["+19802005022"],
            "business_category": "Indian restaurant",
            "preferred_language": "te",
            "plan_id": "trial",
            "status": "trial",
            "created_at": datetime(2026, 5, 17, tzinfo=timezone.utc).isoformat(),
            "updated_at": datetime(2026, 5, 17, tzinfo=timezone.utc).isoformat(),
            "activated_at": datetime(2026, 5, 17, tzinfo=timezone.utc).isoformat(),
            "monthly_flyers_used": 0,
            "billing_provider": "manual",
            "payment_currency": "USD",
        }],
        "onboarding_sessions": [],
    }), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+19802005022",
        "--message-id", "m-breakfast",
        "--raw-request", (
            "Create a flyer for breakfast menu Idli-$1each Dosa-$2each "
            "Upma-5plate Gaarelu-$1each Morning 8am to 10am, Monday to Friday"
        ),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)

    assert project["fields"]["contact_info"] == "+19802005022"
    assert project["fields"]["venue_or_location"] == "90 Bry Bar"
    assert project["fields"]["notes"].startswith("Create a flyer for breakfast menu")
    assert module.FlyerProject.model_validate(project).fields.missing_required_fields() == []
    assert project["fields"]["contact_info"] in projects_path.read_text(encoding="utf-8")
