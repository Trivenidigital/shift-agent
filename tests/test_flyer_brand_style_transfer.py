"""Render wiring + precedence for brand style-transfer (Workstream A/C, 2026-07-11).

Invariants pinned (plan Verification section):
- Template assets are NEVER attached as generation inputs when style-transfer is
  enabled (the load-bearing invariant); logos still attach.
- Exactly ONE style voice in the assembled prompt (register XOR derived style),
  asserted on the real prompt string.
- Flag-off: prompt + attachments byte-identical (the derived_style field + all
  gating is inert when the flag is off).
- Precedence: customer derived style > FLYER_STYLE_REGISTER_OVERRIDE >
  DEFAULT_REGISTER (O1 — customer wins; the flag+allowlist is the kill switch).
- Occasion/intensity still compose on top of the derived style.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from agents.flyer import render as R
from agents.flyer.render import build_image_generation_prompt
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields

PHONE = "+17329837841"

TEMPLATE_BYTES = b"\x89PNG\r\n\x1a\nTEMPLATE-UNIQUE-BYTES-0001"
LOGO_BYTES = b"\x89PNG\r\n\x1a\nLOGO-UNIQUE-BYTES-0002"


def _F(fid, value):
    return FlyerLockedFact(fact_id=fid, label=fid, value=value,
                           source="customer_text", required=True)


def _project(occasion=None):
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    facts = [
        _F("business_name", "Lakshmi's Kitchen"),
        _F("campaign_title", "Weekend Special"),
        _F("pricing_structure", "Any tiffin $6.99"),
        _F("schedule", "Friday to Sunday"),
        _F("item:0:name", "Idli"), _F("item:0:price", "$6.99"),
        _F("item:1:name", "Medu Vada"), _F("item:1:price", "$6.99"),
        _F("contact_phone", PHONE),
        _F("location", "90 Brybar Dr St Johns FL"),
    ]
    proj = FlyerProject(
        project_id="F9601", status="generating_concepts", customer_phone=PHONE,
        created_at=now, updated_at=now, original_message_id="m-bst",
        raw_request="Create a flyer for Weekend Special. Any tiffin $6.99. Idli, Medu Vada. Friday to Sunday.",
        fields=FlyerRequestFields(), locked_facts=facts,
    )
    if occasion:
        proj = proj.model_copy(update={"occasion": occasion})
    return proj


def _bright_derived():
    return {
        "palette": ["warm cream", "tricolor green", "saffron orange"],
        "typography": "brush-script-headline",
        "energy": "busy",
        "motifs": ["marigold border", "food-photo strip"],
        "base_register": "festive-vernacular",
        "derived_at": "2026-07-11T00:00:00+00:00",
        "source_sha256": "a" * 64,
        "model": "openai/gpt-4o-mini",
    }


def _write_customers(tmp_path, *, template_derived=True, include_logo=False,
                     with_template=True):
    """Write a customers.json + on-disk asset files under tmp_path (FLYER_STATE_ROOT)."""
    root = tmp_path
    (root / "brand_assets").mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()
    assets = []
    if with_template:
        tpath = root / "brand_assets" / "B0008.png"
        tpath.write_bytes(TEMPLATE_BYTES)
        row = {
            "asset_id": "B0008", "kind": "template", "path": str(tpath),
            "mime_type": "image/png", "sha256": "b" * 64,
            "original_message_id": "m-b0008", "received_at": now, "active": True,
            "notes": "make mine look like this",
        }
        if template_derived:
            row["derived_style"] = _bright_derived()
        assets.append(row)
    if include_logo:
        lpath = root / "brand_assets" / "B0009.png"
        lpath.write_bytes(LOGO_BYTES)
        assets.append({
            "asset_id": "B0009", "kind": "logo", "path": str(lpath),
            "mime_type": "image/png", "sha256": "c" * 64,
            "original_message_id": "m-b0009", "received_at": now, "active": True,
            "notes": "our logo",
        })
    state_path = root / "customers.json"
    state_path.write_text(json.dumps({
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
    return state_path


def _registers_on(monkeypatch, tmp_path, *, transfer=True):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", PHONE)
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    if transfer:
        monkeypatch.setenv("FLYER_BRAND_STYLE_TRANSFER", "1")
        monkeypatch.setenv("FLYER_BRAND_STYLE_TRANSFER_ALLOWLIST", PHONE)
    else:
        monkeypatch.delenv("FLYER_BRAND_STYLE_TRANSFER", raising=False)
        monkeypatch.delenv("FLYER_BRAND_STYLE_TRANSFER_ALLOWLIST", raising=False)


def _prompt(project):
    return build_image_generation_prompt(
        project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))


# ── One style voice (register XOR derived) ──────────────────────────────────

def test_derived_style_replaces_register_one_voice(monkeypatch, tmp_path):
    _write_customers(tmp_path)
    _registers_on(monkeypatch, tmp_path, transfer=True)
    p = _prompt(_project())
    assert "DERIVED BRAND STYLE" in p                 # derived voice present
    assert "brush-script-headline" in p               # derived typography carried
    assert "warm cream" in p                           # derived palette carried
    assert "FESTIVE PREMIUM" not in p                  # default register voice absent
    # facts still typeset by the (voice-independent) copy section
    assert "TEXT TO RENDER" in p and "Weekend Special" in p


def test_register_voice_when_transfer_off(monkeypatch, tmp_path):
    _write_customers(tmp_path)
    _registers_on(monkeypatch, tmp_path, transfer=False)
    p = _prompt(_project())
    assert "FESTIVE PREMIUM" in p                       # register voice present
    assert "DERIVED BRAND STYLE" not in p               # derived voice absent


# ── Precedence: customer derived style > override > default (O1) ─────────────

def test_precedence_derived_absent_override_unset_uses_default(monkeypatch, tmp_path):
    _write_customers(tmp_path, template_derived=False)   # no derived_style
    _registers_on(monkeypatch, tmp_path, transfer=True)
    monkeypatch.delenv("FLYER_STYLE_REGISTER_OVERRIDE", raising=False)
    p = _prompt(_project())
    assert "FESTIVE PREMIUM" in p and "DERIVED BRAND STYLE" not in p


def test_precedence_derived_absent_override_set_uses_override(monkeypatch, tmp_path):
    _write_customers(tmp_path, template_derived=False)
    _registers_on(monkeypatch, tmp_path, transfer=True)
    monkeypatch.setenv("FLYER_STYLE_REGISTER_OVERRIDE", "premium-dark")
    p = _prompt(_project())
    assert "PREMIUM DARK" in p and "DERIVED BRAND STYLE" not in p


def test_precedence_derived_present_override_unset_customer_wins(monkeypatch, tmp_path):
    _write_customers(tmp_path, template_derived=True)
    _registers_on(monkeypatch, tmp_path, transfer=True)
    monkeypatch.delenv("FLYER_STYLE_REGISTER_OVERRIDE", raising=False)
    p = _prompt(_project())
    assert "DERIVED BRAND STYLE" in p and "FESTIVE PREMIUM" not in p


def test_precedence_derived_present_override_set_customer_still_wins(monkeypatch, tmp_path):
    # O1: customer derived style OUTRANKS FLYER_STYLE_REGISTER_OVERRIDE.
    _write_customers(tmp_path, template_derived=True)
    _registers_on(monkeypatch, tmp_path, transfer=True)
    monkeypatch.setenv("FLYER_STYLE_REGISTER_OVERRIDE", "premium-dark")
    p = _prompt(_project())
    assert "DERIVED BRAND STYLE" in p
    assert "PREMIUM DARK" not in p           # override did not win
    assert "FESTIVE PREMIUM" not in p


def test_flag_off_is_kill_switch_override_wins(monkeypatch, tmp_path):
    # Kill switch: with the transfer flag off, a derived_style in the store has NO
    # effect — the override register wins (customer style is fully disabled).
    _write_customers(tmp_path, template_derived=True)
    _registers_on(monkeypatch, tmp_path, transfer=False)
    monkeypatch.setenv("FLYER_STYLE_REGISTER_OVERRIDE", "premium-dark")
    p = _prompt(_project())
    assert "PREMIUM DARK" in p and "DERIVED BRAND STYLE" not in p


# ── Occasion/intensity compose on top of derived style ──────────────────────

def test_occasion_composes_on_top_of_derived_style(monkeypatch, tmp_path):
    _write_customers(tmp_path, template_derived=True)
    _registers_on(monkeypatch, tmp_path, transfer=True)
    p = _prompt(_project(occasion="diwali"))
    assert "DERIVED BRAND STYLE" in p
    assert "OCCASION THEME - DIWALI" in p       # occasion theme still composes


# ── Template never attached (load-bearing invariant) ────────────────────────

def test_template_excluded_from_generation_assets_when_transfer_on(monkeypatch, tmp_path):
    _write_customers(tmp_path, template_derived=True, include_logo=True)
    _registers_on(monkeypatch, tmp_path, transfer=True)
    proj = _project()
    kinds = [a.kind for a in R._generation_brand_assets(proj)]
    assert "template" not in kinds
    assert "logo" in kinds                       # logo keeps identity mode


def test_template_included_when_transfer_off(monkeypatch, tmp_path):
    _write_customers(tmp_path, template_derived=True, include_logo=True)
    _registers_on(monkeypatch, tmp_path, transfer=False)
    proj = _project()
    kinds = [a.kind for a in R._generation_brand_assets(proj)]
    assert "template" in kinds                   # legacy: template attached


def test_template_bytes_never_in_attachments_when_transfer_on(monkeypatch, tmp_path):
    # End-to-end: the actual attachment builder must not carry the template image.
    _write_customers(tmp_path, template_derived=True, include_logo=True)
    _registers_on(monkeypatch, tmp_path, transfer=True)
    proj = _project()
    parts = R._image_message_content(
        proj, concept_id="C1", output_format="concept_preview", size=(1080, 1350))
    assert isinstance(parts, list)
    template_b64 = base64.b64encode(TEMPLATE_BYTES).decode("ascii")
    logo_b64 = base64.b64encode(LOGO_BYTES).decode("ascii")
    image_urls = [pt["image_url"]["url"] for pt in parts if pt.get("type") == "image_url"]
    joined = "\n".join(image_urls)
    assert template_b64 not in joined            # template NEVER attached
    assert logo_b64 in joined                     # logo IS attached


def test_honor_line_narrows_to_logo_when_transfer_on(monkeypatch, tmp_path):
    _write_customers(tmp_path, template_derived=True, include_logo=True)
    _registers_on(monkeypatch, tmp_path, transfer=True)
    p = _prompt(_project())
    honor = p.split("Customer brand assets to honor:", 1)[1].split("Revision notes", 1)[0]
    assert "saved template reference" not in honor    # template not honored as identity
    assert "saved logo reference" in honor            # logo still honored
    assert "derived separately from the customer's saved template" in p


# ── Flag-off byte-identical (derived_style field is inert) ──────────────────

def test_flag_off_prompt_and_attachments_byte_identical(monkeypatch, tmp_path):
    # With the flag OFF, whether the template carries a derived_style or not must
    # produce a byte-identical prompt AND byte-identical attachments — the schema
    # field + all gating is inert.
    from pathlib import Path

    tmp_ds = tmp_path / "ds"
    tmp_no = tmp_path / "no"
    tmp_ds.mkdir(); tmp_no.mkdir()
    _write_customers(tmp_ds, template_derived=True, include_logo=True)
    _write_customers(tmp_no, template_derived=False, include_logo=True)

    def _render(root):
        monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
        monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
        monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", PHONE)
        monkeypatch.setenv("FLYER_STATE_ROOT", str(root))
        monkeypatch.setenv("FLYER_CUSTOMERS_PATH", str(root / "customers.json"))
        monkeypatch.delenv("FLYER_BRAND_STYLE_TRANSFER", raising=False)
        monkeypatch.delenv("FLYER_BRAND_STYLE_TRANSFER_ALLOWLIST", raising=False)
        proj = _project()
        prompt = _prompt(proj)
        parts = R._image_message_content(
            proj, concept_id="C1", output_format="concept_preview", size=(1080, 1350))
        urls = tuple(pt["image_url"]["url"] for pt in parts if pt.get("type") == "image_url")
        return prompt, urls

    prompt_ds, urls_ds = _render(tmp_ds)
    prompt_no, urls_no = _render(tmp_no)
    assert prompt_ds == prompt_no                 # derived_style field inert on prompt
    assert urls_ds == urls_no                      # ...and on attachments
    # And legacy behavior holds: the template IS attached when the flag is off.
    template_b64 = base64.b64encode(TEMPLATE_BYTES).decode("ascii")
    assert any(template_b64 in u for u in urls_ds)


# ── Gate semantics: empty allowlist disabled, wildcard admits ───────────────

def test_transfer_gate_empty_allowlist_disabled_and_wildcard(monkeypatch, tmp_path):
    _write_customers(tmp_path, template_derived=True)
    proj = _project()
    for var in ("FLYER_BRAND_STYLE_TRANSFER", "FLYER_BRAND_STYLE_TRANSFER_ALLOWLIST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_CUSTOMERS_PATH", str(tmp_path / "customers.json"))
    monkeypatch.setenv("FLYER_BRAND_STYLE_TRANSFER", "1")
    assert R._brand_style_transfer_enabled(proj) is False   # empty allowlist = disabled
    monkeypatch.setenv("FLYER_BRAND_STYLE_TRANSFER_ALLOWLIST", PHONE)
    assert R._brand_style_transfer_enabled(proj) is True
    monkeypatch.setenv("FLYER_BRAND_STYLE_TRANSFER_ALLOWLIST", "+19998887777")
    assert R._brand_style_transfer_enabled(proj) is False   # non-member disabled
    monkeypatch.setenv("FLYER_BRAND_STYLE_TRANSFER_ALLOWLIST", "*")
    assert R._brand_style_transfer_enabled(proj) is True    # wildcard graduation (#612)
