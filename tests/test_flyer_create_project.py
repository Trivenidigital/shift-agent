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


def test_fresh_meats_product_promo_is_ready_without_event_or_contact(tmp_path, monkeypatch):
    """Real customer regression: a product/brand promo brief with a logo
    should not be treated as an event requiring date/time/contact.

    The live loop created F0050/F0051 and asked for the full request again
    because "at the bottom" was parsed as a venue and contact/date/time were
    required even though the customer asked for a non-event product poster.
    """
    module = _load_script(monkeypatch)
    actions = _load_cf_actions(monkeypatch)
    raw_request = (
        "Design a premium organic-style flyer for *Fresh Meats* featuring a whole fresh chicken "
        "as the hero image, surrounded by herbs, garlic, and natural ingredients on an earthy green "
        "and brown background. Add bold elegant typography with the text *Premium Amish Organic Chicken* "
        "and the tagline *Clean bird. Strong life.* Include premium badges like *Fresh, Healthy, Natural, "
        "along with a green **Halal Certified** seal. Create a clean modern luxury grocery aesthetic "
        "with cinematic lighting, rustic textures, and space for address and phone number at the bottom."
    )

    fields = module._extract_fields(raw_request, now=datetime(2026, 5, 19, tzinfo=timezone.utc))
    project = {
        "raw_request": raw_request,
        "fields": json.loads(fields.model_dump_json()),
        "assets": [{"kind": "reference_image"}],
    }

    assert fields.event_or_business_name == "Fresh Meats"
    assert fields.venue_or_location is None
    assert "organic-style" in fields.style_preference
    assert fields.missing_required_fields() == []
    assert actions.flyer_project_has_required_fields(project)


def test_create_project_cleans_logo_prompt_business_and_bad_venue(tmp_path, monkeypatch, capsys):
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
            "primary_chat_id": "17329837841@s.whatsapp.net",
            "onboarded_by_phone": "+17329837841",
            "public_phone": "+17329837841",
            "business_whatsapp_number": "+17329837841",
            "authorized_request_numbers": ["+17329837841"],
            "business_category": "Indian Restaurant",
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

    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", "m-logo",
        "--raw-request", (
            "Create a premium local restaurant flyer for Lakshmis Kitchn using the attached logo. "
            "Headline: Family Combo Feast. Tagline: Fresh food. Happy family. "
            "Feature biryani, dosa, and curry as hero foods. Include badges Fresh, Homemade, Weekend Special. "
            "Use green, gold, and warm rustic textures. Include address and phone from customer profile."
        ),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)

    assert project["fields"]["event_or_business_name"] == "Lakshmis Kitchn"
    assert project["fields"]["venue_or_location"] == "90 Brybar Dr St Johns FL"
    assert project["fields"]["event_time"] is None
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}
    assert facts["business_name"]["value"] == "Lakshmis Kitchn"
    assert facts["headline"]["value"] == "Family Combo Feast"
    assert facts["tagline"]["value"] == "Fresh food. Happy family"
    assert facts["contact_phone"]["source"] == "customer_profile"


def test_create_project_records_reference_extraction_provider_failure(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    reference = tmp_path / "sample.png"
    reference.write_bytes(b"fake")
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 2,
        "customers": [{
            "customer_id": "CUST0001",
            "business_name": "Lakshmis Kitchn",
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
            "created_at": datetime(2026, 5, 18, tzinfo=timezone.utc).isoformat(),
            "updated_at": datetime(2026, 5, 18, tzinfo=timezone.utc).isoformat(),
            "activated_at": datetime(2026, 5, 18, tzinfo=timezone.utc).isoformat(),
            "monthly_flyers_used": 0,
            "billing_provider": "manual",
            "payment_currency": "USD",
        }],
        "onboarding_sessions": [],
    }), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", "m-reference",
        "--raw-request", "Create flyer. Extract item names and prices from attached sample flyer.",
        "--reference-media-path", str(reference),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(asset_dir),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)

    assert project["reference_extractions"][0]["role"] == "menu_reference"
    assert project["reference_extractions"][0]["status"] == "provider_unavailable"
    assert project["manual_review"]["status"] == "queued"
    assert project["manual_review"]["reason"] == "reference_provider_unavailable"
    assert project["status"] == "manual_edit_required"


def test_create_project_image_reference_extracts_locked_menu_facts(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    reference = tmp_path / "menu.png"
    reference.write_bytes(b"fake image bytes")
    _write_customer(customers_path, category="Indian grocery", phone="+17329837841")

    class FakeReferenceProvider:
        provider_name = "fake_vision"

        def extract_text(self, _asset, _raw_request):
            return "Lakshmis Kitchen\nIdly $7.00\nDosa $8.00\nSamosa $3.50", "ok"

    monkeypatch.setattr(
        module,
        "build_reference_extraction_provider",
        lambda: FakeReferenceProvider(),
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", "m-image-menu",
        "--raw-request", "Create a menu flyer. Extract item names and prices from attached sample flyer.",
        "--reference-media-path", str(reference),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(asset_dir),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {(fact["fact_id"], fact["value"], fact["source"]) for fact in project["locked_facts"]}

    assert project["status"] == "intake_started"
    assert project["manual_review"]["status"] == "none"
    assert project["reference_extractions"][0]["status"] == "ok"
    assert ("item:0:name", "Idly", "reference_vision") in facts
    assert ("item:0:price", "$7.00", "reference_vision") in facts
    assert ("item:1:name", "Dosa", "reference_vision") in facts
    assert ("item:1:price", "$8.00", "reference_vision") in facts


def test_create_project_typed_facts_override_reference_facts(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    reference = tmp_path / "menu.png"
    reference.write_bytes(b"fake image bytes")
    _write_customer(customers_path, category="Indian grocery", phone="+17329837841")

    class FakeReferenceProvider:
        provider_name = "fake_vision"

        def extract_text(self, _asset, _raw_request):
            return "Idly $7.00\nDosa $8.00", "ok"

    monkeypatch.setattr(
        module,
        "build_reference_extraction_provider",
        lambda: FakeReferenceProvider(),
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", "m-typed-wins",
        "--raw-request", (
            "Create a menu flyer with Idly $6.50 and Dosa $8.50. "
            "Extract item names and prices from attached sample flyer."
        ),
        "--reference-media-path", str(reference),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(tmp_path / "assets"),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}

    assert facts["item:0:name"]["value"] == "Idly"
    assert facts["item:0:price"]["value"] == "$6.50"
    assert facts["item:1:name"]["value"] == "Dosa"
    assert facts["item:1:price"]["value"] == "$8.50"


def test_create_project_logo_only_image_does_not_become_menu_facts(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    reference = tmp_path / "logo.png"
    reference.write_bytes(b"fake image bytes")
    _write_customer(customers_path, category="Indian grocery", phone="+17329837841")

    class ExplodingReferenceProvider:
        provider_name = "should_not_run"

        def extract_text(self, _asset, _raw_request):
            raise AssertionError("logo-only references should not run menu extraction")

    monkeypatch.setattr(
        module,
        "build_reference_extraction_provider",
        lambda: ExplodingReferenceProvider(),
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", "m-logo-ref",
        "--raw-request", "Create a premium flyer for Lakshmis Kitchen using this as our logo.",
        "--reference-media-path", str(reference),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(tmp_path / "assets"),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)

    assert project["reference_extractions"][0]["role"] == "logo"
    assert project["reference_extractions"][0]["status"] == "not_run"
    assert not [fact for fact in project["locked_facts"] if fact["fact_id"].startswith("item:")]


def test_create_project_pdf_reference_queues_manual_review(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    reference = tmp_path / "menu.pdf"
    reference.write_bytes(b"%PDF-1.4 fake")
    _write_customer(customers_path, category="Indian grocery", phone="+17329837841")
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", "m-pdf-menu",
        "--raw-request", "Create a menu flyer. Extract item names and prices from attached sample menu.",
        "--reference-media-path", str(reference),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(tmp_path / "assets"),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)

    assert project["status"] == "manual_edit_required"
    assert project["manual_review"]["status"] == "queued"
    assert project["manual_review"]["reason"] == "reference_unsupported"
    assert project["reference_extractions"][0]["status"] == "unsupported"
    assert "application/pdf" in project["reference_extractions"][0]["detail"]


def test_create_project_pdf_logo_queues_manual_review_not_generic_generation(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    reference = tmp_path / "logo.pdf"
    reference.write_bytes(b"%PDF-1.4 fake")
    _write_customer(customers_path, category="Indian grocery", phone="+17329837841")
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", "m-pdf-logo",
        "--raw-request", "Create a premium flyer for Lakshmis Kitchen using this as our logo.",
        "--reference-media-path", str(reference),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(tmp_path / "assets"),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)

    assert project["status"] == "manual_edit_required"
    assert project["manual_review"]["reason"] == "reference_unsupported"
    assert project["reference_extractions"][0]["role"] == "logo"
    assert project["reference_extractions"][0]["status"] == "unsupported"


def test_create_project_cleans_new_original_reference_business_name(monkeypatch):
    module = _load_script(monkeypatch)
    raw_request = (
        "Create a new original Lakshmis Kitchn flyer. Extract the visible snack item names and prices "
        "from the attached sample/reference flyer, but do not copy SAMPLE MARKET branding."
    )

    fields = module._extract_fields(raw_request, now=datetime(2026, 5, 19, tzinfo=timezone.utc))

    assert fields.event_or_business_name == "Lakshmis Kitchn"


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
            "Contact: +1 757 555 0199. Style: modern US salon and beauty studio promotion."
        ),
        now=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )

    assert fields.event_or_business_name == "Chloe Hair Studio"
    assert fields.venue_or_location == "Virginia Beach, VA"
    assert fields.contact_info == "+1 757 555 0199"
    assert "food" not in fields.style_preference.lower()
    assert "menu" not in fields.style_preference.lower()
    assert fields.style_preference == "modern US salon and beauty studio promotion"


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
