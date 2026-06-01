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


def _load_script(monkeypatch: pytest.MonkeyPatch, *, with_audit: bool = False):
    fake_safe_io = types.ModuleType("safe_io")
    fake_safe_io.FileLock = _NoopFileLock
    fake_safe_io.atomic_write_text = lambda path, text: Path(path).write_text(text, encoding="utf-8")
    if with_audit:
        # Audit emission for FlyerSourceContractExtracted uses these; expose
        # them on the stubbed safe_io so the script's optional import succeeds.
        from contextlib import contextmanager

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


def _write_customer(
    customers_path: Path,
    *,
    category: str,
    phone: str = "+19802005022",
    business_name: str = "Demo Business",
    business_address: str = "90 Bry Bar",
    primary_chat_id: str = "84593927557152@lid",
) -> None:
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 2,
        "customers": [{
            "customer_id": "CUST0001",
            "business_name": business_name,
            "business_address": business_address,
            "primary_chat_id": primary_chat_id,
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


def test_evening_snacks_request_uses_profile_business_and_campaign_title(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+17329837841",
        business_name="Lakshmis Kitchn",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="17329837841@s.whatsapp.net",
    )

    raw_request = (
        "I\u2019d like you to help me with evening snacks flier from 4 PM to 7 PM. "
        "Include 5 top South Indian snack items. Its Wednesday through Saturday event"
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--chat-id", "17329837841@s.whatsapp.net",
        "--message-id", "m-evening-snacks",
        "--raw-request", raw_request,
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}

    assert project["status"] == "intake_started"
    assert project["customer_id"] == "CUST0001"
    assert project["chat_id"] == "17329837841@s.whatsapp.net"
    assert project["fields"]["event_or_business_name"] == "Evening Snacks"
    assert facts["business_name"]["value"] == "Lakshmis Kitchn"
    assert facts["business_name"]["source"] == "customer_profile"
    assert facts["contact_phone"]["value"] == "+17329837841"
    assert facts["contact_phone"]["source"] == "customer_profile"
    assert facts["location"]["value"] == "90 Brybar Dr St Johns FL"
    assert facts["location"]["source"] == "customer_profile"
    assert facts["location"]["required"] is True
    assert facts["campaign_title"]["value"] == "Evening Snacks"
    assert facts["campaign_title"]["source"] == "customer_text"
    assert facts["campaign_title"]["required"] is True
    poisoned = " ".join(fact["value"].lower() for fact in project["locked_facts"])
    assert "help me with evening snacks flier" not in poisoned
    assert "flier from" not in poisoned


def test_flyer_project_store_accepts_legacy_rows_without_origin_fields():
    from schemas import FlyerProjectStore  # noqa: E402

    now = datetime(2026, 5, 27, tzinfo=timezone.utc).isoformat()
    store = FlyerProjectStore.model_validate({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [{
            "project_id": "F0001",
            "status": "intake_started",
            "customer_phone": "+17329837841",
            "created_at": now,
            "updated_at": now,
            "original_message_id": "wamid.legacy",
            "raw_request": "Create a flyer for biryani",
        }],
    })

    assert store.projects[0].customer_id == ""
    assert store.projects[0].chat_id == ""


def test_sample_snacks_request_locks_unpriced_menu_items(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+15713830763",
        business_name="MK kitchen",
        business_address="23596 prosperity ridge pl Ashburn Va 20148",
        primary_chat_id="104805909434618@lid",
    )

    raw_request = (
        "Create a professional flyer for MK kitchen. Customer request: "
        "Create an evening snacks flyer from 4 PM to 7 PM, Wednesday to Saturday. "
        "Include samosa, mirchi bajji, punugulu, masala vada, and tea. "
        "Use saved address, phone, and logo."
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+15713830763",
        "--chat-id", "104805909434618@lid",
        "--message-id", "m-mk-snacks",
        "--raw-request", raw_request,
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}

    assert [facts[f"item:{idx}:name"]["value"] for idx in range(5)] == [
        "samosa",
        "mirchi bajji",
        "punugulu",
        "masala vada",
        "tea",
    ]
    assert all(facts[f"item:{idx}:name"]["required"] is True for idx in range(5))
    assert "item:0:price" not in facts


def test_biryani_price_for_request_locks_real_items_not_instruction_fragments(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+17329837841",
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="201975216009469@lid",
    )

    raw_request = (
        "Create a Special Biryani's Flyer with all famous south indian biryani's included, "
        "add Price as $16.99 for chicken and $18.99 for goat. "
        "This promotion runs on Wednesday and Thursday of every week. "
        "Use address and phone number stored."
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--chat-id", "201975216009469@lid",
        "--message-id", "m-biryani-prices",
        "--raw-request", raw_request,
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}

    assert facts["business_name"]["value"] == "Lakshmi's Kitchen"
    assert facts["business_name"]["source"] == "customer_profile"
    assert facts["item:0:name"]["value"] == "Chicken Biryani"
    assert facts["item:0:price"]["value"] == "$16.99"
    assert facts["item:1:name"]["value"] == "Goat Biryani"
    assert facts["item:1:price"]["value"] == "$18.99"
    assert "add price as" not in " ".join(fact["value"].lower() for fact in project["locked_facts"])


def test_indochinese_famous_items_request_expands_menu_and_uses_profile_location(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+17329837841",
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="201975216009469@lid",
    )

    raw_request = (
        "Create a flyer for Indo-Chinese specials on Wednesday. "
        "Include 8 famous Indo-Chinese items. Any item priced at $9.99. "
        "Use Address and phone number stored with this business."
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--chat-id", "201975216009469@lid",
        "--message-id", "m-indochinese",
        "--raw-request", raw_request,
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}

    assert project["status"] == "intake_started"
    assert project["fields"]["venue_or_location"] == "90 Brybar Dr St Johns FL"
    assert facts["campaign_title"]["value"] == "Indo-Chinese Specials"
    assert [facts[f"item:{idx}:name"]["value"] for idx in range(8)] == [
        "Veg Manchurian",
        "Gobi Manchurian",
        "Chili Paneer",
        "Hakka Noodles",
        "Schezwan Fried Rice",
        "Chili Garlic Noodles",
        "Manchow Soup",
        "Spring Rolls",
    ]
    assert [facts[f"item:{idx}:price"]["value"] for idx in range(8)] == ["$9.99"] * 8
    assert "$9" not in project["fields"]["venue_or_location"]


def test_lakshmi_south_indian_snack_request_reaches_integrated_menu_path(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+17329837841",
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="201975216009469@lid",
    )

    raw_request = (
        "Create a flyer for south indian snacks.Include these items. "
        "Gavvalu 1 Lb $8.99, Chekkalu 1 lb $8.99 and Arisalu 1 Lb $10.99"
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--chat-id", "201975216009469@lid",
        "--message-id", "m-lakshmi-snacks",
        "--raw-request", raw_request,
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project_doc = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project_doc["locked_facts"]}

    assert facts["business_name"]["value"] == "Lakshmi's Kitchen"
    assert facts["campaign_title"]["value"] == "South Indian Snacks"
    assert facts["item:0:name"]["value"] == "Gavvalu 1 Lb"
    assert facts["item:0:price"]["value"] == "$8.99"
    assert facts["item:1:name"]["value"] == "Chekkalu 1 lb"
    assert facts["item:1:price"]["value"] == "$8.99"
    assert facts["item:2:name"]["value"] == "Arisalu 1 Lb"
    assert facts["item:2:price"]["value"] == "$10.99"

    from schemas import FlyerProject  # noqa: E402
    from agents.flyer.render import _image_prompt, _integrated_poster_eligible  # noqa: E402

    project = FlyerProject.model_validate(project_doc)
    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))
    assert _integrated_poster_eligible(project) is True
    assert "Build a full restaurant/menu poster" in prompt
    assert "decorative BACKGROUND image only" not in prompt


def test_discount_offer_does_not_become_menu_item_prices(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+17329837841",
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="201975216009469@lid",
    )

    raw_request = (
        "Create a flyer for evening snacks sale. "
        "Include samosa and idli. All items 5-10% off. Use saved address and phone."
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--chat-id", "201975216009469@lid",
        "--message-id", "m-discount-not-price",
        "--raw-request", raw_request,
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}

    assert facts["item:0:name"]["value"] == "samosa"
    assert facts["item:1:name"]["value"] == "idli"
    assert "item:0:price" not in facts
    assert facts["pricing_structure"]["value"] == "All items 5-10% off"


def test_explicit_item_prices_outrank_famous_item_expansion(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+17329837841",
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="201975216009469@lid",
    )

    raw_request = (
        "Create a flyer for Indo-Chinese specials. Include 8 famous Indo-Chinese items. "
        "Chili Chicken $12.99, Hakka Noodles $10.99. Use saved address and phone."
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--chat-id", "201975216009469@lid",
        "--message-id", "m-explicit-indochinese",
        "--raw-request", raw_request,
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}

    assert facts["item:0:name"]["value"] == "Chili Chicken"
    assert facts["item:0:price"]["value"] == "$12.99"
    assert facts["item:1:name"]["value"] == "Hakka Noodles"
    assert facts["item:1:price"]["value"] == "$10.99"
    assert "Veg Manchurian" not in {fact["value"] for fact in project["locked_facts"]}


def test_indochinese_famous_item_expansion_supports_ten_items(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+17329837841",
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="201975216009469@lid",
    )

    raw_request = (
        "Create a flyer for Indo-Chinese specials. Include 10 famous Indo-Chinese items. "
        "Any item priced at $9.99. Use saved address and phone."
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--chat-id", "201975216009469@lid",
        "--message-id", "m-indochinese-ten",
        "--raw-request", raw_request,
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}

    assert facts["item:8:name"]["value"] == "American Chopsuey"
    assert facts["item:9:name"]["value"] == "Chili Chicken"
    assert facts["item:9:price"]["value"] == "$9.99"


def test_create_project_passes_config_to_creative_planner(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    config_path = tmp_path / "config.yaml"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+17329837841",
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="201975216009469@lid",
    )
    config_path.write_text(json.dumps({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {
            "enabled": True,
            "creative_planner": {
                "enabled": True,
                "enabled_categories": ["south indian"],
            },
        },
    }), encoding="utf-8")

    from agents.flyer import creative_planner as cp  # noqa: E402

    monkeypatch.setattr(
        cp,
        "build_creative_planner_provider",
        lambda: (lambda _fields, _raw: [
            "Idly",
            "Medu Vada",
            "Masala Dosa",
            "Mysore Bonda",
            "Upma",
            "Poori Bhaji",
            "Pongal",
            "Rava Dosa",
        ]),
    )
    raw_request = (
        "Create a weekend breakfast specials flyer for Lakshmi's Kitchen. "
        "Include 8 famous South Indian breakfast items. Any item price is at $8.99. "
        "Only available on Saturday and Sunday from 8 AM to 11 AM."
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--chat-id", "201975216009469@lid",
        "--message-id", "m-south-indian-planner",
        "--raw-request", raw_request,
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--config-path", str(config_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}

    assert [facts[f"item:{idx}:name"]["value"] for idx in range(8)] == [
        "Idly",
        "Medu Vada",
        "Masala Dosa",
        "Mysore Bonda",
        "Upma",
        "Poori Bhaji",
        "Pongal",
        "Rava Dosa",
    ]
    assert [facts[f"item:{idx}:name"]["source"] for idx in range(8)] == ["hermes_inferred"] * 8
    assert [facts[f"item:{idx}:price"]["value"] for idx in range(8)] == ["$8.99"] * 8
    assert [facts[f"item:{idx}:price"]["source"] for idx in range(8)] == ["customer_text"] * 8


def test_profile_hydration_uses_chat_id_when_phone_does_not_match(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+19045550104",
        business_name="Lakshmis Kitchn",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="17329837841@lid",
    )

    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+19999999999",
        "--chat-id", "17329837841@lid",
        "--message-id", "m-lid-profile",
        "--raw-request", "Create flyer for weekend lunch specials. Contact from customer profile.",
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}
    assert facts["business_name"]["value"] == "Lakshmis Kitchn"
    assert facts["contact_phone"]["value"] == "+19045550104"
    assert facts["location"]["value"] == "90 Brybar Dr St Johns FL"


@pytest.mark.parametrize(
    ("raw_request", "expected_business"),
    [
        ("Create flyer for lunch specials. Business name is Lakshmi's Kitchen.", "Lakshmi's Kitchen"),
        (
            "Create flyer for lunch specials. Business name is Lakshmi's Kitchen and headline is Lunch Specials.",
            "Lakshmi's Kitchen",
        ),
        (
            "Create flyer for lunch specials. Replace Lakshmis Kitchn with Lakshmi's Kitchen for this flyer.",
            "Lakshmi's Kitchen",
        ),
    ],
)
def test_explicit_business_name_override_is_allowed_and_auditable(
    tmp_path,
    monkeypatch,
    capsys,
    raw_request,
    expected_business,
):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+17329837841",
        business_name="Lakshmis Kitchn",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="17329837841@s.whatsapp.net",
    )

    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--chat-id", "17329837841@s.whatsapp.net",
        "--message-id", "m-business-override",
        "--raw-request", raw_request,
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}
    assert facts["business_name"]["value"] == expected_business
    assert facts["business_name"]["source"] == "customer_text"
    assert facts["contact_phone"]["source"] == "customer_profile"
    assert facts["location"]["source"] == "customer_profile"


def test_paid_guest_request_can_use_sane_text_business_and_contact(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    customers_path.write_text(json.dumps({"schema_version": 1, "customers": [], "onboarding_sessions": []}), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+15550101010",
        "--message-id", "m-guest",
        "--raw-request", (
            "Create flyer for River Cafe weekend brunch special. "
            "Contact: +1 555 010 1010. Location: 12 Main St. Include pancakes $9.99."
        ),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}
    assert project["status"] == "intake_started"
    assert facts["business_name"]["value"] == "River Cafe"
    assert facts["business_name"]["source"] == "customer_text"
    assert facts["contact_phone"]["value"] == "+1 555 010 1010"
    assert facts["contact_phone"]["source"] == "customer_text"


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


def test_explicit_english_only_overrides_forbidden_language_mentions(monkeypatch):
    module = _load_script(monkeypatch)
    fields = module._extract_fields(
        (
            "Create a Ganesh festival flyer. Preferred flyer language: Mixed / Other. "
            "Customer update before generation: Language: English only. "
            "Do NOT use Telugu, Hindi, or any regional Indian language."
        ),
        now=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )

    assert fields.preferred_language == "en"


def test_create_project_keeps_explicit_english_only_over_profile_language(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+17329837841",
        business_name="Lakshmis Kitchn",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="17329837841@s.whatsapp.net",
    )
    store = json.loads(customers_path.read_text(encoding="utf-8"))
    store["customers"][0]["preferred_language"] = "te"
    customers_path.write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--chat-id", "17329837841@s.whatsapp.net",
        "--message-id", "m-english-only",
        "--raw-request", (
            "Create a Ganesh festival flyer. Language: English only. "
            "Do NOT use Telugu, Hindi, or any regional Indian language."
        ),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    assert project["fields"]["preferred_language"] == "en"


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
    # Regression: --manual-edit-required must populate manual_review.reason_code,
    # not leave it at the default 'unclassified' (the F0052/F0053 prod bug).
    assert project["manual_review"]["status"] == "queued"
    assert project["manual_review"]["reason_code"] == "source_edit_provider_unavailable"
    assert project["manual_review"]["queued_at"] is not None


@pytest.mark.parametrize(
    ("status", "expected_reason_code"),
    [
        ("low_confidence", "reference_low_confidence"),
        ("unsupported", "reference_unsupported"),
    ],
)
def test_source_edit_template_reference_failure_keeps_non_provider_reason(
    tmp_path, monkeypatch, capsys, status, expected_reason_code,
):
    module = _load_script(monkeypatch)
    from schemas import FlyerReferenceExtraction

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    reference = tmp_path / "source.jpg"
    reference.write_bytes(b"fake image bytes")
    _write_customer(
        customers_path,
        category="Indian restaurant",
        phone="+19045550104",
        business_name="Lakshmi's Kitchen",
        primary_chat_id="19045550104@s.whatsapp.net",
    )

    def fake_extract_reference(asset, *, raw_request, provider):
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role="source_edit_template",
            provider="fake_vision",
            status=status,
            detail=f"source contract extraction {status}",
            extracted_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(module, "build_reference_extraction_provider", lambda: object())
    monkeypatch.setattr(module, "extract_reference", fake_extract_reference)
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+19045550104",
        "--chat-id", "19045550104@s.whatsapp.net",
        "--message-id", "m-source-edit-reference-failure",
        "--raw-request", "Edit uploaded flyer/source artwork. Change Lunch Combo to Dinner Combo.",
        "--reference-media-path", str(reference),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(tmp_path / "assets"),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    assert project["status"] == "manual_edit_required"
    assert project["manual_review"]["reason_code"] == expected_reason_code
    assert project["manual_review"]["detail"] == f"source contract extraction {status}"


def test_create_flyer_project_queues_manual_review_on_missing_required_facts(tmp_path, monkeypatch, capsys):
    """P0-2: when extraction does not surface every required fact slot
    (business_name + contact_phone by default), the project must be queued for
    manual review with reason_code='missing_required_facts' rather than
    silently entering intake_started."""
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    projects_path = tmp_path / "projects.json"
    customers_path = tmp_path / "customers.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    projects_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 1,
        "projects": [],
    }), encoding="utf-8")
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 1,
        "customers": [],
    }), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", "missing-required-msg",
        "--raw-request", "Make a flyer please.",
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(asset_dir),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)

    assert project["status"] == "manual_edit_required"
    assert project["manual_review"]["status"] == "queued"
    assert project["manual_review"]["reason_code"] == "missing_required_facts"
    detail = project["manual_review"]["detail"]
    assert "missing required fact slots" in detail
    # At least one of the required slots must be named in the detail (the bare
    # request "Make a flyer please." has neither a business name nor a phone).
    assert ("business_name" in detail) or ("contact_phone" in detail)


def test_create_flyer_project_does_not_queue_when_required_facts_present(tmp_path, monkeypatch, capsys):
    """Regression guard: when extraction DOES surface every required slot, project
    enters intake_started and manual_review stays at status='none'."""
    module = _load_script(monkeypatch)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    projects_path = tmp_path / "projects.json"
    customers_path = tmp_path / "customers.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    projects_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 1,
        "projects": [],
    }), encoding="utf-8")
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 1,
        "customers": [],
    }), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", "complete-msg",
        "--raw-request", (
            "Create flyer for Lakshmis Kitchen Thursday dinner special. "
            "Contact +17329837841. Idly $7 and Dosa $8."
        ),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(asset_dir),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)

    assert project["status"] == "intake_started"
    assert project["manual_review"]["status"] == "none"
    assert project["manual_review"]["reason_code"] == "unclassified"


# ─── Task 4: source-contract locked-fact helpers ──────────────────


def test_source_contract_locked_facts_for_f0061(tmp_path, monkeypatch):
    """F0061-style contract yields source_section, source_heading, and
    replacement:N:new locked facts. preserve_layout/preserve_unmentioned_text
    flip the required bit on source-derived facts."""
    sys.path.insert(0, str(PLATFORM))
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    from schemas import FlyerAsset, FlyerSourceContract, FlyerSourceContractSection
    from agents.flyer.facts import source_contract_locked_facts

    (tmp_path / "sample.png").write_bytes(b"x")
    asset = FlyerAsset(
        asset_id="A0001",
        kind="reference_image",
        source="whatsapp",
        path=str(tmp_path / "sample.png"),
        mime_type="image/png",
        sha256="a" * 64,
        original_message_id="m-ref",
        received_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )

    contract = FlyerSourceContract(
        required_headings=["Monday Thali Specials"],
        sections=[FlyerSourceContractSection(heading="Veg Thali Specials", items=["Rice", "Dal"])],
        requested_replacements={"Triveni Express": "Lakshmi's Kitchen", "Rice": "Jeera Rice"},
        preserve_layout=True,
        preserve_unmentioned_text=True,
    )
    facts = source_contract_locked_facts(contract, asset=asset, message_id="m-x")
    by_id = {f.fact_id: f for f in facts}
    assert "source_heading:0" in by_id
    assert by_id["source_heading:0"].value == "Monday Thali Specials"
    assert by_id["source_heading:0"].required is True
    assert by_id["source_section:0:heading"].value == "Veg Thali Specials"
    assert by_id["source_section:0:item:0"].value == "Rice"
    assert "replacement:0:new" in by_id
    assert by_id["replacement:0:new"].required is True
    # `replacement:N:old` is present but not required (it's tracked for QA negative-checks).
    assert "replacement:0:old" in by_id
    assert by_id["replacement:0:old"].required is False
    # Provenance survives on every fact.
    assert by_id["source_heading:0"].source_asset_id == "A0001"


def test_source_contract_locked_facts_includes_required_text(tmp_path, monkeypatch):
    """Required source text (e.g. tagline rows, sides rows, badges) must
    become locked facts so QA's required-fact loop can enforce them.
    Without this, `required_text` extracted from the source flyer lives
    in the schema but never reaches QA — the "do not change anything else"
    promise leaks at the QA boundary.

    Regression for PR #137 review finding 1.
    """
    sys.path.insert(0, str(PLATFORM))
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    from schemas import FlyerAsset, FlyerSourceContract
    from agents.flyer.facts import source_contract_locked_facts

    (tmp_path / "sample.png").write_bytes(b"x")
    asset = FlyerAsset(
        asset_id="A0001",
        kind="reference_image",
        source="whatsapp",
        path=str(tmp_path / "sample.png"),
        mime_type="image/png",
        sha256="a" * 64,
        original_message_id="m-ref",
        received_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )
    contract = FlyerSourceContract(
        required_text=["Sides: salad, raita, papad", "Today's special"],
        preserve_layout=True,
        preserve_unmentioned_text=True,
    )
    facts = source_contract_locked_facts(contract, asset=asset, message_id="m-x")
    by_id = {f.fact_id: f for f in facts}
    assert "source_required_text:0" in by_id
    assert by_id["source_required_text:0"].value == "Sides: salad, raita, papad"
    assert by_id["source_required_text:0"].required is True
    assert by_id["source_required_text:0"].source == "reference_vision"
    assert by_id["source_required_text:0"].source_asset_id == "A0001"
    assert by_id["source_required_text:1"].value == "Today's special"


def test_source_contract_locked_facts_not_required_when_no_preserve(tmp_path, monkeypatch):
    sys.path.insert(0, str(PLATFORM))
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    from schemas import FlyerAsset, FlyerSourceContract
    from agents.flyer.facts import source_contract_locked_facts

    (tmp_path / "sample.png").write_bytes(b"x")
    asset = FlyerAsset(
        asset_id="A0001",
        kind="reference_image",
        source="whatsapp",
        path=str(tmp_path / "sample.png"),
        mime_type="image/png",
        sha256="a" * 64,
        original_message_id="m-ref",
        received_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )
    contract = FlyerSourceContract(
        required_headings=["Some Heading"],
        preserve_layout=False,
        preserve_unmentioned_text=False,
    )
    facts = source_contract_locked_facts(contract, asset=asset)
    by_id = {f.fact_id: f for f in facts}
    assert by_id["source_heading:0"].required is False


def test_populate_forbidden_substrings_brand_phone_but_not_menu_item(tmp_path):
    sys.path.insert(0, str(PLATFORM))
    from schemas import FlyerSourceContract, FlyerSourceContractSection
    from agents.flyer.facts import _populate_forbidden_substrings

    contract = FlyerSourceContract(
        sections=[FlyerSourceContractSection(heading="Veg Thali Specials", items=["Rice"])],
        requested_replacements={
            "Triveni Express": "Lakshmi's Kitchen",
            "Rice": "Jeera Rice",
            "555-010-0100": "+17329837841",
        },
    )
    _populate_forbidden_substrings(contract)
    forbidden = contract.forbidden_substrings
    assert "Triveni Express" in forbidden, forbidden
    # Menu-item swap is NOT forbidden — both legitimately co-exist.
    assert "Rice" not in forbidden
    # Phone replacement adds digits-only run.
    assert any("5550100100" in f for f in forbidden)


def test_populate_forbidden_substrings_skips_single_word_brand(tmp_path):
    sys.path.insert(0, str(PLATFORM))
    from schemas import FlyerSourceContract
    from agents.flyer.facts import _populate_forbidden_substrings

    contract = FlyerSourceContract(
        requested_replacements={"Acme": "Bravo"},
    )
    _populate_forbidden_substrings(contract)
    assert "Acme" not in contract.forbidden_substrings


def test_populate_forbidden_substrings_skips_new_starts_with_old(tmp_path):
    """Rice -> Jeera Rice doesn't add Rice to forbidden_substrings even
    when Rice is not in the vision section items."""
    sys.path.insert(0, str(PLATFORM))
    from schemas import FlyerSourceContract
    from agents.flyer.facts import _populate_forbidden_substrings

    contract = FlyerSourceContract(
        requested_replacements={"Rice": "Jeera Rice"},
    )
    _populate_forbidden_substrings(contract)
    assert "Rice" not in contract.forbidden_substrings


# ─── Task 7: brand/branding edit semantics ────────────────────────


def test_is_product_or_brand_promo_does_not_match_bare_branding_edit(monkeypatch):
    """`replace Triveni branding with Lakshmi's Kitchen branding` is an
    edit instruction — not a product-promo request. The post-fix matcher
    requires brand keywords be paired with explicit promo/forward cues."""
    module = _load_script(monkeypatch)
    text = "Replace Triveni Express with Lakshmi's Kitchen branding"
    assert module._is_product_or_brand_promo(text) is False


def test_is_product_or_brand_promo_still_matches_explicit_brand_forward(monkeypatch):
    module = _load_script(monkeypatch)
    text = "brand-forward product promotion with premium imagery"
    assert module._is_product_or_brand_promo(text) is True


def test_is_product_or_brand_promo_matches_brand_promo_phrase(monkeypatch):
    module = _load_script(monkeypatch)
    text = "We need a brand promo for our supermarket grand opening"
    assert module._is_product_or_brand_promo(text) is True


# ─── Task 12 (PR-review follow-up): FlyerSourceContractExtracted audit emission ──


def test_create_project_emits_source_contract_extracted_audit(monkeypatch, tmp_path, capsys):
    """Regression for PR #137 review finding 2: design committed to emitting
    FlyerSourceContractExtracted after extract_reference returns, but the
    initial implementation never called the audit chokepoint. Operator
    observability into provider-availability regressions depends on this row.

    Test strategy: monkeypatch the audit helper to capture its kwargs rather
    than trust filesystem-side audit IO. Verifies the call site exists and
    is reached for source_edit_template role on a successful extraction.
    """
    sys.path.insert(0, str(PLATFORM))
    module = _load_script(monkeypatch, with_audit=True)
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    reference = tmp_path / "src.png"
    reference.write_bytes(b"fake")
    _write_customer(customers_path, category="Indian grocery", phone="+17329837841")

    captured = []

    def _capture(*, extraction, project_id, now):
        captured.append({
            "role": extraction.role,
            "status": extraction.status,
            "project_id": project_id,
            "source_contract": extraction.source_contract,
        })

    monkeypatch.setattr(
        module,
        "_emit_source_contract_extracted_audit",
        _capture,
        raising=False,
    )

    from schemas import FlyerSourceContract, FlyerSourceContractSection

    def _fake_extract_reference(asset, *, raw_request, provider):
        from schemas import FlyerReferenceExtraction
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role="source_edit_template",
            provider="fake_source_contract",
            status="ok",
            extracted_facts=[],
            detail="",
            source_contract=FlyerSourceContract(
                source_business_names=["Triveni Express"],
                target_business_name="Lakshmi's Kitchen",
                required_headings=["Monday Thali Specials"],
                sections=[FlyerSourceContractSection(heading="Veg", items=["Rice"])],
                requested_replacements={"Triveni Express": "Lakshmi's Kitchen"},
                preserve_layout=True,
                preserve_unmentioned_text=True,
                confidence=0.85,
            ),
            extracted_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(module, "extract_reference", _fake_extract_reference, raising=False)
    monkeypatch.setattr(
        module,
        "build_reference_extraction_provider",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", "m-audit",
        "--raw-request",
        # Must contain a `replace|change|edit` verb + `this/attached/source flyer`
        # cue to trip classify_reference_role into source_edit_template.
        "Replace Triveni Express with Lakshmi's Kitchen in this flyer. Do not change anything else.",
        "--reference-media-path", str(reference),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(asset_dir),
    ])

    assert module.main() == 0
    capsys.readouterr()
    assert len(captured) == 1, f"expected one audit call; got {captured!r}"
    assert captured[0]["role"] == "source_edit_template"
    assert captured[0]["status"] == "ok"
    assert captured[0]["project_id"].startswith("F")
    assert captured[0]["source_contract"] is not None
    assert "Monday Thali Specials" in captured[0]["source_contract"].required_headings


def test_create_project_emits_audit_even_on_provider_unavailable(monkeypatch, tmp_path, capsys):
    """Provider unavailable / low-confidence extraction must still trigger
    the audit emission — operator visibility into how often the provider
    is missing is part of the source-contract observability contract.
    """
    sys.path.insert(0, str(PLATFORM))
    module = _load_script(monkeypatch, with_audit=True)
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    reference = tmp_path / "src.png"
    reference.write_bytes(b"fake")
    _write_customer(customers_path, category="Indian grocery", phone="+17329837841")

    captured = []

    def _capture(*, extraction, project_id, now):
        captured.append({
            "role": extraction.role,
            "status": extraction.status,
            "source_contract": extraction.source_contract,
        })

    monkeypatch.setattr(
        module,
        "_emit_source_contract_extracted_audit",
        _capture,
        raising=False,
    )

    def _fake_extract_reference(asset, *, raw_request, provider):
        from schemas import FlyerReferenceExtraction
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role="source_edit_template",
            provider="fake",
            status="provider_unavailable",
            extracted_facts=[],
            detail="provider unavailable",
            source_contract=None,
            extracted_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(module, "extract_reference", _fake_extract_reference, raising=False)
    monkeypatch.setattr(
        module,
        "build_reference_extraction_provider",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", "m-audit-pu",
        "--raw-request",
        # Must contain a `replace|change|edit` verb + `this/attached/source flyer`
        # cue to trip classify_reference_role into source_edit_template.
        "Replace Triveni Express with Lakshmi's Kitchen in this flyer. Do not change anything else.",
        "--reference-media-path", str(reference),
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(asset_dir),
    ])
    assert module.main() == 0
    capsys.readouterr()
    assert len(captured) == 1, f"expected one audit call even on provider_unavailable; got {captured!r}"
    assert captured[0]["role"] == "source_edit_template"
    assert captured[0]["status"] == "provider_unavailable"
    assert captured[0]["source_contract"] is None


def test_diwali_sale_request_locks_semantic_campaign_pricing_and_offer(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+17329837841",
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="17329837841@s.whatsapp.net",
    )
    raw_request = (
        "Create a flyer for Diwali sale, All items 5-10% off. "
        "Lucky draw eligible with purchase above $100."
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--chat-id", "17329837841@s.whatsapp.net",
        "--message-id", "m-diwali",
        "--raw-request", raw_request,
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}

    assert facts["business_name"]["value"] == "Lakshmi's Kitchen"
    assert facts["business_name"]["source"] == "customer_profile"
    assert facts["campaign_title"]["value"] == "Diwali Sale"
    assert facts["pricing_structure"]["value"] == "All items 5-10% off"
    assert facts["offer:0"]["value"] == "Lucky draw eligible with purchase above $100"
    assert facts["campaign_title"]["value"] != "Diwali sale, All items 5-10% off"


def test_evening_snacks_sale_request_locks_price_offer_schedule_and_end(tmp_path, monkeypatch, capsys):
    module = _load_script(monkeypatch)
    customers_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    _write_customer(
        customers_path,
        category="Indian Restaurant",
        phone="+17329837841",
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar Dr St Johns FL",
        primary_chat_id="17329837841@s.whatsapp.net",
    )
    raw_request = (
        "Create a flyer for evening snacks sale, Wednesday and Thursday, any item $7.99. "
        "Free Masala Chai with any purchase above $12. This promotion runs until June 25."
    )
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--chat-id", "17329837841@s.whatsapp.net",
        "--message-id", "m-snacks",
        "--raw-request", raw_request,
        "--state-path", str(projects_path),
        "--customer-state-path", str(customers_path),
    ])

    assert module.main() == 0
    project = json.loads(capsys.readouterr().out)
    facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}

    assert facts["campaign_title"]["value"] == "Evening Snacks Sale"
    assert facts["pricing_structure"]["value"] == "Any item $7.99"
    assert facts["offer:0"]["value"] == "Free Masala Chai with any purchase above $12"
    assert facts["schedule"]["value"] == "Wednesday and Thursday"
    assert facts["promotion_end"]["value"] == "June 25"
    assert all(fact["value"].lower() != "any item" for fact in project["locked_facts"])
