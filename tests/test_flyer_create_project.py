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
SRC = REPO / "src"
CF_ACTIONS = REPO / "src" / "plugins" / "cf-router" / "actions.py"


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


def _load_cf_actions(monkeypatch: pytest.MonkeyPatch):
    fake_safe_io = types.ModuleType("safe_io")
    fake_safe_io.FileLock = _NoopFileLock
    fake_safe_io.atomic_write_text = lambda path, text: Path(path).write_text(text, encoding="utf-8")
    fake_safe_io.load_yaml_model = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)
    sys.path.insert(0, str(PLATFORM))
    module_name = "cf_router_actions_for_flyer_create_test"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(CF_ACTIONS))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def _write_customer(customers_path: Path, *, category: str, phone: str = "+19802005022") -> None:
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 2,
        "customers": [{
            "customer_id": "CUST0001",
            "business_name": "Demo Business",
            "business_address": "90 Bry Bar",
            "primary_chat_id": "84593927557152@lid",
            "onboarded_by_phone": phone,
            "public_phone": phone,
            "business_whatsapp_number": phone,
            "authorized_request_numbers": [phone],
            "business_category": category,
            "preferred_language": "en",
            "plan_id": "trial",
            "status": "trial",
            "created_at": datetime(2026, 5, 18, tzinfo=timezone.utc).isoformat(),
            "updated_at": datetime(2026, 5, 18, tzinfo=timezone.utc).isoformat(),
            "activated_at": datetime(2026, 5, 18, tzinfo=timezone.utc).isoformat(),
            "monthly_flyers_used": 0,
            "billing_provider": "manual",
            "payment_currency": "USD",
        }],
        "onboarding_sessions": [],
    }), encoding="utf-8")


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


def test_all_starter_briefs_create_required_flyer_projects(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(SRC))
    from agents.flyer.starter_briefs import all_starter_briefs  # noqa: E402

    module = _load_script(monkeypatch)
    actions = _load_cf_actions(monkeypatch)

    for index, brief in enumerate(all_starter_briefs(), start=1):
        customers_path = tmp_path / f"customers-{brief.category_id}.json"
        projects_path = tmp_path / f"projects-{brief.category_id}.json"
        _write_customer(customers_path, category=brief.label)
        monkeypatch.setattr(sys, "argv", [
            "create-flyer-project",
            "--customer-phone", "+19802005022",
            "--message-id", f"starter-{index}",
            "--raw-request", brief.body,
            "--state-path", str(projects_path),
            "--customer-state-path", str(customers_path),
        ])

        assert module.main() == 0
        project = json.loads(capsys.readouterr().out)

        assert module.FlyerProject.model_validate(project).fields.missing_required_fields() == [], brief.category_id
        assert actions.flyer_project_has_required_fields(project), brief.category_id


def test_create_project_names_recurring_breakfast_specials_cleanly(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 2,
        "customers": [{
            "customer_id": "CUST0001",
            "business_name": "Lakshmis Kitchn",
            "business_address": "90 Brybar Dr St Johns FL",
            "primary_chat_id": "201975216009469@lid",
            "onboarded_by_phone": "+19045550104",
            "public_phone": "+17329837841",
            "business_whatsapp_number": "+17329837841",
            "authorized_request_numbers": ["+17329837841", "+19045550104"],
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
        "--customer-phone", "+19045550104",
        "--message-id", "m-breakfast-specials",
        "--raw-request", (
            'Create a breakfast flyer with these items "Poori with Chicken $14.99, '
            'Kheema Dosa $12.99". Timings 8 AM to 11 AM. Thursday to Sunday.'
        ),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)

    assert project["fields"]["event_or_business_name"] == "Weekend Breakfast Specials"
    assert project["fields"]["contact_info"] == "+17329837841"


def test_create_project_parses_chloe_salon_service_request_without_prompt_leak(monkeypatch):
    module = _load_script(monkeypatch)
    fields = module._extract_fields(
        (
            "Create flyer for Chloe Hair Studio promoting the $20 men haircut, "
            "$80 perms, and other hair services. Location: Virginia Beach, VA. "
            "Contact: +1 757 555 0199"
        ),
        now=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )

    assert fields.event_or_business_name == "Chloe Hair Studio"
    assert fields.venue_or_location == "Virginia Beach, VA"
    assert fields.contact_info == "+1 757 555 0199"
    assert "food" not in fields.style_preference.lower()
    assert "menu" not in fields.style_preference.lower()
    assert "salon" in fields.style_preference.lower()


def test_create_project_can_queue_exact_reference_edit_without_template_title(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    reference = tmp_path / "source.jpg"
    reference.write_bytes(b"fake image bytes")
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 2,
        "customers": [{
            "customer_id": "CUST0001",
            "business_name": "Lakshmis Kitchen",
            "business_address": "90 Brybar Dr St Johns FL",
            "primary_chat_id": "201975216009469@lid",
            "onboarded_by_phone": "+19045550104",
            "public_phone": "+17329837841",
            "business_whatsapp_number": "+17329837841",
            "authorized_request_numbers": ["+17329837841", "+19045550104"],
            "business_category": "Indian restaurant",
            "preferred_language": "en",
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
        "--customer-phone", "+19045550104",
        "--message-id", "m-exact-edit",
        "--raw-request", (
            "Edit uploaded flyer/source artwork. Customer requested: "
            "I'd like you to Remove that extra 08:00. Add Any Item for $9.99."
        ),
        "--reference-media-path", str(reference),
        "--manual-edit-required",
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(tmp_path / "assets"),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)

    assert project["status"] == "manual_edit_required"
    assert project["fields"]["event_or_business_name"] == "Lakshmis Kitchen"
    assert project["assets"][0]["kind"] == "reference_image"
    assert "Uploaded Flyer Template" not in project["fields"]["event_or_business_name"]
