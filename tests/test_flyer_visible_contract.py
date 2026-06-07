"""Regressions for the post-render visible-contract referee (operator 2026-06-07).

Pure-function tests on validate_visible_contract: feed the locked facts + the
VISIBLE text the vision read-back would return, assert the concrete blockers. The
gate wiring (flag/allowlist/vision call/unverified) lives in flyer_bare_render and
is exercised separately; here we pin the referee's judgments.
"""
from __future__ import annotations

from datetime import datetime, timezone

from schemas import FlyerLockedFact, FlyerProject
from agents.flyer.visible_contract import validate_visible_contract

NOW = datetime(2026, 6, 7, tzinfo=timezone.utc)
BNAME = "Lakshmis Kitchen"


def _fact(fact_id, value, *, label="", required=True):
    return FlyerLockedFact(
        fact_id=fact_id, label=label or fact_id, value=value, source="customer_text", required=required,
    )


def _project(facts, *, raw_request="", phone="+17329837841"):
    has_name = any(getattr(f, "fact_id", "") == "business_name" for f in facts)
    if not has_name:
        facts = [_fact("business_name", BNAME, label="Business")] + facts
    return FlyerProject(
        project_id="F9100", status="awaiting_final_approval", customer_phone=phone,
        created_at=NOW, updated_at=NOW, original_message_id="m-vc",
        raw_request=raw_request or "Create a flyer.", locked_facts=facts,
    )


def _blockers(*args, **kwargs):
    return validate_visible_contract(*args, **kwargs)


def _has(blockers, needle):
    return any(needle in b for b in blockers)


# ── 1 + 2: placeholder / garbled price slots ────────────────────────────────────
def test_placeholder_price_slot_blocks_revision():
    p = _project([_fact("pricing_structure", "Any item $7.99"), _fact("item:0:price", "$2", label="Price")])
    out = _blockers(p, f"{BNAME} Any item $7.99 Tea $2 Samosa [price] Mirchi [Price]")
    assert _has(out, "placeholder/garbled slot visible")


def test_every_item_699_with_placeholder_blocks():
    p = _project([_fact("pricing_structure", "Every item $6.99"), _fact("item:0:price", "$2", label="Price")])
    out = _blockers(p, f"{BNAME} Every item [price] Tea $2 Location 90 Brybar Dr Contact +17329837841")
    assert _has(out, "placeholder/garbled slot visible")
    # $6.99 absent from a substantive read -> absence blocker too
    assert _has(out, "requested price not visible: 6.99")


def test_garbled_bracket_token_rice_blocks():
    p = _project([])  # no prices requested
    out = _blockers(p, f"{BNAME} Samosa - [rice] Mirchi bajji - [rice] Tea - [rice]")
    assert _has(out, "placeholder/garbled slot visible")


# ── 3: qualifier flip (Any -> Every) ────────────────────────────────────────────
def test_any_item_must_not_become_every_item():
    p = _project([_fact("pricing_structure", "Any item $7.99")])
    out = _blockers(p, f"{BNAME} Every item $7.99 Location 90 Brybar Dr Contact +17329837841")
    assert _has(out, "pricing qualifier changed")


def test_any_item_rendered_as_any_item_is_clean():
    p = _project([_fact("pricing_structure", "Any item $7.99")])
    out = _blockers(p, f"{BNAME} Any item $7.99 Location 90 Brybar Dr St Johns FL Contact +17329837841")
    assert not _has(out, "pricing qualifier changed")
    assert not _has(out, "unexpected price")


# ── 4: invented prices when none were requested ─────────────────────────────────
def test_invented_price_when_no_prices_requested_blocks():
    # Only unambiguous currency / "N/-" forms are prices (a bare "6.00" is not — it could
    # be a time/rating/version). The no-price flyer's leaked "[price]" slot is what fails
    # closed in the bare-decimal case (test_garbled_bracket_token_rice_blocks).
    p = _project([])
    out = _blockers(p, f"{BNAME} Punugulu $6.00 Samosa 1/- Location 90 Brybar Dr Contact +17329837841")
    assert _has(out, "invented price visible")


def test_no_price_request_with_address_phone_zip_does_not_false_positive():
    # The real MK-kitchen flyer text: address + ZIP + phone + times + "Est 2010" must NOT
    # be read as invented prices (the whole point of the strict price-token shape).
    p = _project([], phone="+15713830763")
    out = _blockers(
        p,
        "MK kitchen Location 23596 prosperity ridge pl Ashburn Va 20148 "
        "Contact +15713830763 Open 9 AM to 5 PM Est 2010",
    )
    assert out == [], f"unexpected blockers on a clean no-price flyer: {out}"


# ── 4b: locked dessert prices reject invented package pricing ──────────────────
def test_dessert_locked_prices_block_invented_package_prices():
    p = _project([
        _fact("item:0:name", "Mango tresleches - half tray", label="Item"),
        _fact("item:0:price", "$75", label="Price"),
        _fact("item:1:name", "Rasmalai tresleches - half tray", label="Item"),
        _fact("item:1:price", "$70", label="Price"),
        _fact("item:2:name", "Khalakhandh - 100 count", label="Item"),
        _fact("item:2:price", "$100", label="Price"),
    ])
    out = _blockers(
        p,
        f"{BNAME} Graduation Specials Celebration Sweet Box $29.99 "
        "Graduation Party Platter $49.99 Custom Treat Bags $12.99 each "
        "Location 90 Brybar Dr Contact +17329837841",
    )
    assert _has(out, "unexpected price visible")
    assert _has(out, "$29.99")
    assert _has(out, "$49.99")
    assert _has(out, "$12.99")


def test_dessert_locked_prices_allow_normalized_clean_render():
    p = _project([
        _fact("item:0:name", "Mango tresleches - half tray", label="Item"),
        _fact("item:0:price", "$75", label="Price"),
        _fact("item:1:name", "Rasmalai tresleches - half tray", label="Item"),
        _fact("item:1:price", "$70", label="Price"),
        _fact("item:2:name", "Khalakhandh - 100 count", label="Item"),
        _fact("item:2:price", "$100", label="Price"),
    ])
    out = _blockers(
        p,
        f"{BNAME} Graduation Dessert Specials Mango tresleches half tray $75 "
        "Rasmalai tresleches half tray $70 Khalakhandh 100 count $100 "
        "Location 90 Brybar Dr Contact +17329837841",
    )
    assert out == [], f"clean dessert price render should pass: {out}"


# ── 5: requested badges / notes must be visible (substantive read) ──────────────
def test_requested_badges_missing_blocks_when_substantive():
    p = _project(
        [_fact("offer:0", "We cater both veg and non-veg", label="Offer")],
        raw_request="Daily thali specials. We cater. Delivery available. Zelle accepted.",
    )
    out = _blockers(p, f"{BNAME} Daily Thali Specials Location 90 Brybar Dr St Johns FL Contact +17329837841")
    assert _has(out, "requested catering note not visible")
    assert _has(out, "requested delivery note not visible")
    assert _has(out, "requested payment note not visible")


def test_requested_badges_present_is_clean():
    p = _project(
        [_fact("offer:0", "We cater both veg and non-veg", label="Offer")],
        raw_request="Daily thali specials. We cater. Delivery available. Zelle accepted.",
    )
    out = _blockers(
        p,
        f"{BNAME} Daily Thali Specials We cater Delivery available Zelle accepted "
        "Location 90 Brybar Dr Contact +17329837841",
    )
    assert not _has(out, "note not visible")


def test_badge_absence_not_flagged_on_sparse_read():
    # A sparse OCR (business name not fully read, < 8 words) must NOT yield absence blockers.
    p = _project(
        [_fact("item:0:price", "$8.99", label="Price")],
        raw_request="We cater. Delivery available.",
    )
    out = _blockers(p, "Lakshmis [price]")
    assert _has(out, "placeholder/garbled slot visible")   # positive check still fires
    assert not _has(out, "note not visible")               # absence checks skipped
    assert not _has(out, "requested price not visible")


# ── 6: combo / package exact prices (no $39.99 -> $99 corruption) ───────────────
def test_combo_price_corruption_blocks():
    p = _project([
        _fact("offer:0", "Non veg combo $49.99 includes 2 non veg curries and 1 dessert", label="Offer"),
        _fact("offer:1", "Veg combo $39.99 includes 2 veg curries and 1 dessert", label="Offer"),
    ])
    out = _blockers(p, f"{BNAME} Non Veg Combo $49.99 Veg Combo $99 Location 90 Brybar Dr Contact +17329837841")
    assert _has(out, "unexpected price visible")
    assert _has(out, "$99")


def test_combo_correct_prices_is_clean():
    p = _project([
        _fact("offer:0", "Non veg combo $49.99 includes 2 non veg curries and 1 dessert", label="Offer"),
        _fact("offer:1", "Veg combo $39.99 includes 2 veg curries and 1 dessert", label="Offer"),
    ])
    out = _blockers(
        p,
        f"{BNAME} Non Veg Combo $49.99 Veg Combo $39.99 Location 90 Brybar Dr Contact +17329837841",
    )
    assert out == [], f"clean combo should pass: {out}"


# ── 7: internal asset ids must not be visible ───────────────────────────────────
def test_internal_asset_id_blocks():
    p = _project([_fact("item:0:price", "$8.99", label="Price")])
    out = _blockers(p, f"{BNAME} B0002 Veg Combo $8.99 Location 90 Brybar Dr Contact +17329837841")
    assert _has(out, "internal id visible: B0002")


# ── medium-word title leak ──────────────────────────────────────────────────────
def test_raw_medium_word_title_leak_blocks():
    p = _project([])
    out = _blockers(p, f"{BNAME} Daily Thali Specials Flyer Location 90 Brybar Dr Contact +17329837841")
    assert _has(out, "raw medium word")


# ── false-positive guards from Codex review 2026-06-07 ──────────────────────────
def test_decimal_times_measurements_dates_are_not_prices():
    # No prices requested; decimal-form times / measurements / dotted dates must NOT
    # be read as invented prices (would false-hold a legit flyer).
    p = _project([])
    out = _blockers(
        p,
        f"{BNAME} Open 9.00 AM to 5.00 PM Bag 1.50 lb Date 06.07.2026 "
        "Location 90 Brybar Dr Contact +17329837841",
    )
    assert out == [], f"decimal time/measurement/date wrongly flagged: {out}"


def test_decimal_hour_ranges_are_not_prices():
    # "4.00 - 7.00 PM" / "10.00 to 2.00 PM": the am/pm trails the SECOND time, so the
    # first decimal needs the range guard (Codex 2026-06-07).
    p = _project([])
    out = _blockers(
        p,
        f"{BNAME} Open 4.00 - 7.00 PM Hours 10.00 to 2.00 PM Contact +17329837841 Location 90 Brybar Dr",
    )
    assert out == [], f"decimal hour range wrongly flagged: {out}"


def test_legit_bracket_labels_are_not_placeholders():
    p = _project([_fact("item:0:price", "$8.99", label="Price")])
    out = _blockers(
        p,
        f"{BNAME} [Veg] Premium Thali $8.99 [Limited Time] [Weekend Special] "
        "Location 90 Brybar Dr Contact +17329837841",
    )
    assert not _has(out, "placeholder/garbled slot visible")


def test_capitalized_price_slot_still_blocks():
    p = _project([_fact("item:0:price", "$8.99", label="Price")])
    out = _blockers(p, f"{BNAME} Premium Thali [Price] Contact +17329837841")
    assert _has(out, "placeholder/garbled slot visible")


def test_missing_requested_price_blocks_on_substantive_read():
    # Brand name read back (substantive) but the requested price is absent -> the flyer
    # omitted it -> fail closed (Codex 2026-06-07: single missing price must block).
    p = _project([_fact("item:0:price", "$8.99", label="Price")])
    out = _blockers(p, f"{BNAME} Location 90 Brybar Dr St Johns FL Contact +17329837841 Open 4 PM to 7 PM")
    assert _has(out, "requested price not visible")


def test_thousands_separator_price_is_parsed_and_matched():
    # $1,299.00 must parse as one price (129900), not "$1,29" + "299.00".
    p = _project([_fact("offer:0", "Catering package $1,299.00 for 50 guests", label="Offer")])
    clean = _blockers(p, f"{BNAME} Catering Package $1,299.00 for 50 guests Contact +17329837841")
    assert clean == [], f"thousands-separator price wrongly flagged: {clean}"
    wrong = _blockers(p, f"{BNAME} Catering Package $99 for 50 guests Contact +17329837841")
    assert _has(wrong, "unexpected price visible")


def test_negated_badge_is_not_required():
    # "no delivery" must NOT be treated as a requested delivery badge.
    p = _project(
        [_fact("business_name", BNAME, label="Business")],
        raw_request="Daily thali specials. No delivery. We do not cater.",
    )
    out = _blockers(p, f"{BNAME} Daily Thali Specials Location 90 Brybar Dr Contact +17329837841")
    assert not _has(out, "delivery note not visible")
    assert not _has(out, "catering note not visible")


def test_affirmative_badge_after_negation_is_still_required():
    # "No delivery fee. Delivery available." -> delivery IS requested (a later affirmative
    # mention must not be dropped by an earlier negated one).
    p = _project(
        [_fact("business_name", BNAME, label="Business")],
        raw_request="No delivery fee. Delivery available all week. Daily thali specials.",
    )
    out = _blockers(p, f"{BNAME} Daily Thali Specials Location 90 Brybar Dr Contact +17329837841")
    assert _has(out, "delivery note not visible")


def test_decimal_percent_is_not_a_price():
    p = _project([])
    out = _blockers(p, f"{BNAME} 50.00% off everything Contact +17329837841 Location 90 Brybar Dr")
    assert not _has(out, "invented price")


def test_absence_skipped_when_business_name_not_read():
    # Business name absent from the read -> not substantive -> no absence blockers.
    p = _project(
        [_fact("item:0:price", "$8.99", label="Price")],
        raw_request="We cater. Delivery available.",
    )
    out = _blockers(p, "Some Other Header Location 90 Brybar Dr Contact +17329837841 Open 4 PM to 7 PM today")
    assert not _has(out, "requested price not visible")
    assert not _has(out, "note not visible")


# ── gate wiring in bare_render (armed flag + allowlist, vision read-back) ────────
def _arm(monkeypatch, tmp_path):
    from agents.flyer import bare_render as BR
    monkeypatch.setenv("FLYER_VISIBLE_CONTRACT", "1")
    monkeypatch.setenv("FLYER_VISIBLE_CONTRACT_ALLOWLIST", "+17329837841")
    monkeypatch.setenv("FLYER_BARE_SKIP_VISUAL_QA", "1")  # broad QA off, exactly like the box
    monkeypatch.setattr(BR, "AUDIT_LOG_PATH", tmp_path / "decisions.log")
    return BR


def _patch_vision(monkeypatch, text, notes=None):
    from agents.flyer import visual_qa as VQ
    monkeypatch.setattr(VQ, "_vision_text", lambda _p: (text, "openrouter", "ocr_vision", notes or []))


def test_gate_blocks_on_violation_when_armed(monkeypatch, tmp_path):
    BR = _arm(monkeypatch, tmp_path)
    _patch_vision(monkeypatch, f"{BNAME} Veg Combo $99 Contact +17329837841")
    p = _project([_fact("offer:0", "Veg combo $39.99 includes 2 curries", label="Offer")])
    ok, blockers = BR.run_visual_qa(b"png-bytes", p)
    assert ok is False
    assert any("unexpected price" in b for b in blockers)
    assert "blocked" in (tmp_path / "decisions.log").read_text(encoding="utf-8")


def test_gate_unverified_sends_anyway_when_read_empty(monkeypatch, tmp_path):
    BR = _arm(monkeypatch, tmp_path)
    _patch_vision(monkeypatch, "", notes=["unreadable"])
    p = _project([_fact("item:0:price", "$8.99", label="Price")])
    ok, _blk = BR.run_visual_qa(b"png-bytes", p)
    assert ok is True  # send-anyway on an unverifiable read-back (this scoped phase)
    assert "unverified" in (tmp_path / "decisions.log").read_text(encoding="utf-8")


def test_gate_passes_clean_and_logs_pass(monkeypatch, tmp_path):
    BR = _arm(monkeypatch, tmp_path)
    _patch_vision(monkeypatch, f"{BNAME} Premium Thali $8.99 Location 90 Brybar Dr Contact +17329837841 open 4 pm")
    p = _project([_fact("item:0:name", "Premium Thali", label="Item"), _fact("item:0:price", "$8.99", label="Price")])
    ok, _blk = BR.run_visual_qa(b"png-bytes", p)
    assert ok is True
    assert "pass" in (tmp_path / "decisions.log").read_text(encoding="utf-8")


def test_gate_not_invoked_when_flag_off(monkeypatch, tmp_path):
    from agents.flyer import visual_qa as VQ
    from agents.flyer import bare_render as BR
    monkeypatch.delenv("FLYER_VISIBLE_CONTRACT", raising=False)
    monkeypatch.setenv("FLYER_BARE_SKIP_VISUAL_QA", "1")
    called = {"n": 0}
    monkeypatch.setattr(VQ, "_vision_text", lambda _p: (called.__setitem__("n", called["n"] + 1), ("x", "p", "s", []))[1])
    p = _project([_fact("item:0:price", "$8.99", label="Price")])
    ok, blockers = BR.run_visual_qa(b"png-bytes", p)
    assert ok is True
    assert called["n"] == 0                    # gate never read the image
    assert blockers == ["visual_qa_disabled"]  # unchanged behavior when flag off


def test_gate_infra_error_sends_anyway(monkeypatch, tmp_path):
    # The referee module not being deployed (or any infra failure) must send-anyway +
    # log unverified, NEVER crash or hold the render (Codex 2026-06-07 BLOCKER).
    BR = _arm(monkeypatch, tmp_path)
    _patch_vision(monkeypatch, f"{BNAME} Veg Combo $99 Contact +17329837841")  # would block if reached

    def _boom():
        raise ImportError("flyer_visible_contract not deployed")

    monkeypatch.setattr(BR, "_visible_contract_mod", _boom)
    p = _project([_fact("offer:0", "Veg combo $39.99 includes 2 curries", label="Offer")])
    ok, _blk = BR.run_visual_qa(b"png-bytes", p)
    assert ok is True
    assert "unverified" in (tmp_path / "decisions.log").read_text(encoding="utf-8")


# ── 8: a fully correct flyer stays green ────────────────────────────────────────
def test_clean_flyer_passes():
    p = _project([
        _fact("item:0:name", "Premium Thali", label="Item"),
        _fact("item:0:price", "$8.99", label="Price"),
    ])
    out = _blockers(
        p,
        f"{BNAME} Premium Thali $8.99 Location 90 Brybar Dr St Johns FL Contact +17329837841 "
        "Schedule Wednesday to Saturday 4 PM to 7 PM",
    )
    assert out == [], f"clean flyer should pass: {out}"
