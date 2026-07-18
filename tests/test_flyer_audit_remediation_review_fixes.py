"""Post-review fixes for the flyer audit remediation (review pass 2026-07-18).

Each finding (F1–F8, F10) was verified by execution against the remediation
branch HEAD; these tests pin the corrected behavior:

  F1  price truncation/drop on trailing quantities ("Samosa $5.99 2pc")
  F2  phantom items from color/adjective lines ("Green"/"Gold")
  F3  ordinal false-positives ("no one likes it" -> sample 1)
  F4  food-word competitor mastheads excused ("Bombay Thali")
  F5  template brand-asset wrong-brand vector missed by the masthead backstop
  F6  §12b audit row emitted BEFORE the state persist
  F7  bare 🙏 counted as a final approval
  F8  "redo it" not accepted by the quote-echo NEW classifier
  F10 numeric concept-selection reply must not trip the AN-1 early-approval gate
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.flyer import facts as facts_module
from agents.flyer.facts import _item_name_facts, _item_price_facts, price_conflict_signals
from agents.flyer.intake import _parse_sample_choice
from agents.flyer.semantic_brief import (
    _project_ingested_external_reference,
    visible_wrong_brand_blockers,
)
from schemas import FlyerRequestFields

PHONE = "+17329837841"
REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"


def _load_cf_router():
    """Load cf-router actions+hooks as a throwaway package (mirrors AN-1 harness)."""
    pkg_name = "cf_router_review_fixes_pkg_under_test"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]
    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg_name] = importlib.util.module_from_spec(pkg_spec)
    for sub in ("actions", "hooks"):
        full = f"{pkg_name}.{sub}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{sub}.py"))
        spec = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        loader.exec_module(mod)
    return sys.modules[f"{pkg_name}.actions"], sys.modules[f"{pkg_name}.hooks"]


def _item_probe(raw, monkeypatch):
    monkeypatch.setattr(facts_module, "build_hermes_semantic_brief_provider", lambda: None)
    facts = facts_module.extract_text_facts(FlyerRequestFields(notes=raw), raw, message_id="m")
    names, prices = {}, {}
    for f in facts:
        if f.fact_id.endswith(":name"):
            names[f.fact_id.split(":")[1]] = f.value
        elif f.fact_id.endswith(":price"):
            prices[f.fact_id.split(":")[1]] = f.value
    return {names[i]: prices.get(i) for i in names}


# ───────────────────────────── F1 price truncation ──────────────────────────

@pytest.mark.parametrize(
    ("brief", "name", "price"),
    [
        ("Samosa $5.99 2pc", "Samosa", "$5.99"),
        ("Idli $4.99 3pc box", "Idli", "$4.99"),
        ("Platter $10 30 pieces", "Platter", "$10"),
        ("Biryani $12 4 people", "Biryani", "$12"),
    ],
)
def test_f1_symbol_price_keeps_full_value_before_trailing_quantity(brief, name, price, monkeypatch):
    m = _item_probe(brief, monkeypatch)
    assert m.get(name) == price, m


@pytest.mark.parametrize("brief", ["Everything 20% off today", "Menu 15.00% discount"])
def test_f1_percent_still_rejected(brief):
    m = {f.fact_id: f.value for f in _item_price_facts(brief, message_id="m")
         if f.fact_id.startswith("item:")}
    assert m == {}, m


# ───────────────────────────── F2 color/adjective phantoms ──────────────────

@pytest.mark.parametrize("brief", ["Make a weekend flyer\nGreen\nGold", "green, gold"])
def test_f2_color_lines_never_become_items(brief, monkeypatch):
    assert _item_probe(brief, monkeypatch) == {}


def test_f2_legit_bare_dish_lists_still_extract():
    newline = [f.value for f in _item_name_facts("Idli\nDosa\nMasala Dosa", message_id="m")
               if f.fact_id.endswith(":name")]
    comma = [f.value for f in _item_name_facts("Idli, Dosa, Vada, Pongal", message_id="m")
             if f.fact_id.endswith(":name")]
    assert newline == ["Idli", "Dosa", "Masala Dosa"], newline
    assert comma == ["Idli", "Dosa", "Vada", "Pongal"], comma


# ───────────────────────────── F3 ordinal false-positives ───────────────────

@pytest.mark.parametrize(
    "reply",
    ["no one likes it", "one moment please", "give me one more", "two of my friends said no"],
)
def test_f3_free_floating_cardinal_in_prose_is_not_a_selection(reply):
    assert _parse_sample_choice(reply) is None


@pytest.mark.parametrize(
    ("reply", "expected"),
    [("first", 0), ("2", 1), ("option 2", 1), ("the second one", 1),
     ("one", 0), ("two", 1), ("number two", 1)],
)
def test_f3_genuine_selections_still_map(reply, expected):
    assert _parse_sample_choice(reply) == expected


# ───────────────────────────── F4/F5 wrong-brand masthead ───────────────────

def _wrong_brand_project(**updates):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    base = {
        "project_id": "F9001", "status": "intake_started", "customer_phone": PHONE,
        "created_at": now, "updated_at": now, "original_message_id": "m-1",
        "raw_request": "weekend flyer",
        "fields": {"event_or_business_name": "Lakshmis Kitchen",
                   "venue_or_location": "90 Brybar Dr St Johns FL",
                   "contact_info": PHONE, "notes": ""},
        "locked_facts": [{"fact_id": "business_name", "label": "Business",
                          "value": "Lakshmis Kitchen", "source": "customer_profile",
                          "required": True}],
    }
    base.update(updates)
    from schemas import FlyerProject
    return FlyerProject.model_validate(base)


_WITH_REFERENCE = {"reference_extractions": [{"asset_id": "A0001", "role": "inspiration"}]}


@pytest.mark.parametrize("masthead", ["Bombay Thali", "Madras Meals", "Sunday Brunch"])
def test_f4_food_word_competitor_masthead_now_blocks(masthead):
    assert visible_wrong_brand_blockers(_wrong_brand_project(**_WITH_REFERENCE), masthead)


@pytest.mark.parametrize("line", ["Weekend Special", "Family Combo Feast", "Fresh Daily Breakfast"])
def test_f4_genuine_promo_headline_still_excused(line):
    assert visible_wrong_brand_blockers(_wrong_brand_project(**_WITH_REFERENCE), line) == []


def _write_customer_store_with_template(tmp_path, *, with_template: bool):
    (tmp_path / "brand_assets").mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()
    assets = []
    if with_template:
        tpath = tmp_path / "brand_assets" / "B0008.png"
        tpath.write_bytes(b"\x89PNGTEMPLATE")
        assets.append({
            "asset_id": "B0008", "kind": "template", "path": str(tpath),
            "mime_type": "image/png", "sha256": "b" * 64,
            "original_message_id": "m-b0008", "received_at": now, "active": True,
            "notes": "make mine look like this",
        })
    customers_path = tmp_path / "customers.json"
    customers_path.write_text(json.dumps({
        "schema_version": 1, "next_customer_sequence": 2, "next_brand_asset_sequence": 10,
        "customers": [{
            "customer_id": "CUST0001", "business_name": "Lakshmi's Kitchen",
            "business_address": "90 Brybar Dr St Johns FL",
            "primary_chat_id": "17329837841@s.whatsapp.net",
            "onboarded_by_phone": PHONE, "public_phone": PHONE,
            "business_whatsapp_number": PHONE, "authorized_request_numbers": [PHONE],
            "business_category": "Indian Restaurant", "preferred_language": "en",
            "plan_id": "trial", "status": "trial", "created_at": now, "updated_at": now,
            "activated_at": now, "monthly_flyers_used": 0, "billing_provider": "manual",
            "payment_currency": "USD", "brand_assets": assets,
        }],
        "onboarding_sessions": [],
    }), encoding="utf-8")
    return customers_path


def test_f5_active_template_brand_asset_is_external_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path = _write_customer_store_with_template(tmp_path, with_template=True)
    monkeypatch.setenv("FLYER_CUSTOMERS_PATH", str(customers_path))
    project = _wrong_brand_project()  # no reference_extractions, no reference_image asset
    assert _project_ingested_external_reference(project) is True
    # The suffix-less masthead backstop now fires in the template-asset threat context.
    assert visible_wrong_brand_blockers(project, "Saravana Bhavan")


def test_f5_no_template_asset_keeps_backstop_quiet(tmp_path, monkeypatch):
    customers_path = _write_customer_store_with_template(tmp_path, with_template=False)
    monkeypatch.setenv("FLYER_CUSTOMERS_PATH", str(customers_path))
    project = _wrong_brand_project()
    assert _project_ingested_external_reference(project) is False
    # No ingested external asset -> a 2-word owner tagline is NOT read as a competitor.
    assert visible_wrong_brand_blockers(project, "Saravana Bhavan") == []


def test_f5_missing_customer_returns_false_gracefully(tmp_path, monkeypatch):
    # Store path that does not exist -> read is graceful, gate stays False.
    monkeypatch.setenv("FLYER_CUSTOMERS_PATH", str(tmp_path / "nope.json"))
    assert _project_ingested_external_reference(_wrong_brand_project()) is False


# ───────────────────────────── F6 §12b audit ordering ───────────────────────

def _brand_asset_rows(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("type") == "flyer_brand_asset_state_changed"]


def _active_logo_customer_store(state_path: Path, now: datetime):
    from schemas import FlyerCustomerStore
    store = FlyerCustomerStore()
    store.customers.append(store.new_customer(
        business_name="Triveni", business_address="300 S Polk St",
        public_phone="+17043243322", business_whatsapp_number="+17043243322",
        authorized_request_number="+19045550104", business_category="restaurant",
        preferred_language="en", plan_id="starter", now=now,
    ).model_copy(update={"status": "active"}))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")


def test_f6_site1_no_audit_row_when_persist_fails(tmp_path, monkeypatch):
    """Re-upload deactivation must NOT emit its §12b audit row if the store write
    that makes the deactivation durable fails first."""
    from agents.flyer import onboarding
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    _active_logo_customer_store(state_path, now)

    first = tmp_path / "logo1.png"; first.write_bytes(b"first")
    second = tmp_path / "logo2.png"; second.write_bytes(b"second")
    onboarding.store_brand_asset(
        state_path=state_path, chat_id="17043243322@s.whatsapp.net",
        sender_phone="+17043243322", message_id="logo1", media_path=first,
        text="logo", now=now, audit_log_path=log_path)
    assert _brand_asset_rows(log_path) == []  # first upload reverses nothing

    def _boom(*_a, **_k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(onboarding, "write_customer_store", _boom)
    with pytest.raises(RuntimeError):
        onboarding.store_brand_asset(
            state_path=state_path, chat_id="17043243322@s.whatsapp.net",
            sender_phone="+17043243322", message_id="logo2", media_path=second,
            text="replace logo", now=now, audit_log_path=log_path)
    assert _brand_asset_rows(log_path) == [], "audit row emitted despite failed persist"


def test_f6_site2_connect_recovered_sender_defers_audit_emission(tmp_path, monkeypatch):
    """_connect_recovered_sender must NOT emit the merge deactivation audit itself;
    it returns the (deactivated, replacement) flip for the caller to emit AFTER its
    write_customer_store lands."""
    from agents.flyer.onboarding import _connect_recovered_sender
    from schemas import FlyerBrandAsset, FlyerCustomerStore, FlyerOnboardingSession
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    log_path = tmp_path / "decisions.log"

    store = FlyerCustomerStore()
    existing = store.new_customer(
        business_name="Triveni Cafe", business_address="300 S Polk St, Dallas TX",
        public_phone=PHONE, business_whatsapp_number=PHONE, authorized_request_number=PHONE,
        business_category="restaurant", preferred_language="en", plan_id="trial",
        now=now, primary_chat_id="17329837841@s.whatsapp.net", onboarded_by_phone=PHONE,
    ).model_copy(update={
        "status": "trial",
        "brand_assets": [FlyerBrandAsset(
            asset_id="B0001", kind="logo", path=str(tmp_path / "b1.png"),
            mime_type="image/png", sha256="a" * 64, original_message_id="m0",
            received_at=now, active=True)],
    })
    store.customers = [existing]
    session = FlyerOnboardingSession(
        chat_id="19045550199@s.whatsapp.net", sender_phone="+19045550199",
        status="confirming_summary", started_at=now, updated_at=now, last_message_id="s",
        pending_brand_assets=[FlyerBrandAsset(
            asset_id="B0002", kind="logo", path=str(tmp_path / "b2.png"),
            mime_type="image/png", sha256="b" * 64, original_message_id="m1",
            received_at=now, active=True)],
    )

    deferred = _connect_recovered_sender(
        store=store, customer=existing, session=session,
        sender_phone="+19045550199", now=now, audit_log_path=log_path)

    assert _brand_asset_rows(log_path) == [], "audit emitted inside the function, before persist"
    assert len(deferred) == 1
    deactivated, replacement = deferred[0]
    assert deactivated.asset_id == "B0001"
    assert replacement.asset_id == "B0002"


# ───────────────────────────── F7 bare-emoji approval ───────────────────────

def test_f7_bare_folded_hands_is_not_an_approval():
    actions, _ = _load_cf_router()
    assert actions.is_flyer_approval_text("\U0001F64F") is False  # 🙏 alone
    assert actions.is_flyer_approval_text("\U0001F44D") is True   # 👍 alone
    assert actions.is_flyer_approval_text("\U0001F44D approved") is True
    assert actions.is_flyer_approval_text("\U0001F64F approve") is True  # text path still approves


# ───────────────────────────── F8 quote-echo "redo it" ──────────────────────

@pytest.mark.parametrize("reply", ["redo", "redo it", "regenerate it", "make a new one", "again"])
def test_f8_redo_it_classifies_as_new(reply):
    actions, _ = _load_cf_router()
    assert actions.classify_flyer_quote_echo_choice(reply) == "new"


def test_f8_unrelated_edit_is_not_new():
    actions, _ = _load_cf_router()
    assert actions.classify_flyer_quote_echo_choice("change the logo") is None


# ───────────────────────────── F10 numeric selection vs AN-1 ────────────────

def test_f10_numeric_reply_routes_to_concept_selection_not_an1():
    """A numeric "1" in awaiting_concept_selection resolves to a concept (routes to
    selection) and is NOT approval text — so the AN-1 early-approval progress reply
    (which is gated behind is_flyer_approval_text) can never intercept it."""
    actions, hooks = _load_cf_router()
    project = {"concepts": [{"concept_id": "C1", "title": "A"},
                            {"concept_id": "C2", "title": "B"},
                            {"concept_id": "C3", "title": "C"}]}
    assert hooks._resolve_flyer_concept_selection("1", project) == "C1"
    assert actions.is_flyer_approval_text("1") is False
    # The AN-1 reply exists for the status, but its send gate is is_flyer_approval_text,
    # which "1" fails — proving the numeric selection cannot trip the early-approval path.
    assert hooks._flyer_early_approval_progress_reply("awaiting_concept_selection") is not None
