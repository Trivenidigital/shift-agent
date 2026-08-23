"""OPERATOR TRUTH — the menu-approval alert must describe the MEASURED state.

Incident (2026-08-23, update MU-FIDELITY-20260823): applying a menu correction on
a deployment with NO pricebook paged the owner priority-1 with

    "Catering menu applied, pricebook NOT updated"
    "Menu v3 is live, but the pricebook could not be activated and stays at its
     previous version. Quotes still use the old prices."

Both claims were false, in opposite directions. `catering-pricebook.json` did not
exist and never had, so nothing "stayed at its previous version"; and quoting
falls back to the menu, which HAD just been corrected, so quotes did not use old
prices — they used the new ones. One generic failure string was being reused
across materially different outcomes.

WHAT THIS FILE PINS. Each test ties ONE message to (a) the state transition that
produced it and (b) the price-source behaviour it claims, asserted against the
real pricing code rather than restated. A test that only checked "a message was
produced" would have passed against the buggy string, so every assertion here
either names a claim the generic message could not make or forbids a claim the
generic message did make.

Three price sources exist, and they are not interchangeable:
  menu_only  — no pricebook file. finalize prices each line off the menu index.
  pricebook  — one is live. item_price_overrides beat the menu, per item.
  blocked    — the file is present but will not load; finalize exits 2 instead
               of quoting, so no NEW quote can be finalized at all.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from fixtures_fleet import ensure_fcntl_stub, load_script, read_log_rows

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLATFORM = SRC / "platform"
SCRIPTS = SRC / "agents" / "catering" / "scripts"
PRICEBOOK_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "catering_pricebook_valid.json"
for _p in (str(PLATFORM), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import catering_pricing  # noqa: E402

OWNER_PHONE = "+19045550100"
CODE = "#A3F2X"
UPDATE_ID = "MU-TRUTH-0001"

# The corrected menu the owner just approved. "Masala Dosa" is ALSO carried as a
# pricebook override in the valid fixture, so the two price sources disagree on
# it by construction — which is what makes the per-source claims falsifiable.
CORRECTED_ITEMS = [
    {"name": "Masala Dosa", "price_usd": 12.00, "category": "main",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 1},
    {"name": "Gulab Jamun", "price_usd": 6.00, "category": "dessert",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 2},
]

# The two sentences the incident alert put in front of the owner. Neither is a
# fact this script can know a priori, so neither may appear unconditionally.
INCIDENT_LIES = ("stays at its previous version", "still use the old prices")


class _Box:
    """One sandbox per test — these are independent states, not a transcript."""

    def __init__(self, root: Path):
        self.root = root
        self.state = root / "state"
        self.logs = root / "logs"
        self.state.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.config = root / "config.yaml"
        self.menu = self.state / "catering-menu.json"
        self.menu_pending = self.state / "catering-menu-pending.json"
        self.menu_archive = self.state / "catering-menu-archive"
        self.pricebook = self.state / "catering-pricebook.json"
        self.log = self.logs / "decisions.log"
        self.log.write_text("", encoding="utf-8")
        self.config.write_text(yaml.safe_dump({
            "schema_version": 1,
            "customer": {"name": "Truth Box", "location_id": "loc_truth",
                         "timezone": "America/New_York"},
            "owner": {"name": "Owner", "phone": OWNER_PHONE,
                      "self_chat_jid": "19045550100@s.whatsapp.net"},
            "limits": {},
            "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
            "backup": {"gpg_recipient_email": "x@y"},
            "catering": {"enabled": True},
        }), encoding="utf-8")

    def stage_pending(self, *, fingerprint: str | None) -> None:
        """A pending update awaiting the owner's `yes`. `fingerprint=None`
        reproduces a proposal created before pricebook scope existed."""
        doc = {
            "update_id": UPDATE_ID,
            "proposed_at": datetime.now(timezone.utc).isoformat(),
            "source_image_id": "wamid.TRUTH",
            "extracted_items": CORRECTED_ITEMS,
            "confirmation_code": CODE,
            "parser_notes": "",
        }
        if fingerprint is not None:
            doc["pricebook_fingerprint"] = fingerprint
        self.menu_pending.write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture
def box(tmp_path, monkeypatch) -> _Box:
    b = _Box(tmp_path / "box")
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(b.config))
    monkeypatch.setenv("SHIFT_AGENT_STATE_DIR", str(b.state))
    monkeypatch.setenv("SHIFT_AGENT_DECISIONS_LOG_PATH", str(b.log))
    monkeypatch.setenv("SHIFT_AGENT_LOG_PATH", str(b.log))
    monkeypatch.setenv("SHIFT_AGENT_PRICEBOOK_PATH", str(b.pricebook))
    monkeypatch.setenv("SHIFT_AGENT_NOTIFY_DEDUP", "0")
    return b


_seq = [0]


def _approve(box: _Box) -> tuple[int, dict, list[dict]]:
    """Run the REAL apply-menu-update `yes` path against the sandbox.

    Returns (rc, stdout payload, captured owner alerts). The notify chokepoint is
    replaced rather than stubbed at the transport: what this file is about is the
    (title, body) pair handed to it, so that pair is exactly what is captured.
    """
    _seq[0] += 1
    mod = load_script(f"truth_apply_menu_update_{_seq[0]}", SCRIPTS / "apply-menu-update")
    for name, value in {
        "CONFIG_PATH": box.config,
        "MENU_PATH": box.menu,
        "MENU_LOCK": Path(str(box.menu) + ".lock"),
        "PENDING_PATH": box.menu_pending,
        "PENDING_LOCK": Path(str(box.menu_pending) + ".lock"),
        "ARCHIVE_DIR": box.menu_archive,
        "PRICEBOOK_PATH": box.pricebook,
        "LOG_PATH": box.log,
        "IMPORT_PRICEBOOK_BIN": SCRIPTS / "import-catering-pricebook",
        "PYTHON_BIN": Path(sys.executable),
    }.items():
        if hasattr(mod, name):
            setattr(mod, name, value)

    alerts: list[dict] = []

    def _capture(title, message, priority=1, *, source="unknown", **kw):  # noqa: ARG001
        alerts.append({"title": title, "message": message,
                       "priority": priority, "source": source})
        return True

    mod.notify_owner_with_fallback = _capture

    old_argv = sys.argv
    sys.argv = ["apply-menu-update", "--code", CODE, "--decision", "yes",
                "--sender-role", "owner"]
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = old_argv

    payload: dict = {}
    for line in reversed([ln for ln in out.getvalue().splitlines() if ln.strip()]):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    return rc, payload, alerts


def _seed_valid_pricebook(box: _Box) -> dict:
    doc = json.loads(PRICEBOOK_FIXTURE.read_text(encoding="utf-8"))
    box.pricebook.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def _fingerprint_now(box: _Box) -> str:
    return catering_pricing.pricebook_fingerprint(
        catering_pricing.load_pricebook(box.pricebook))


# ════════════════════════════════════════════════════════════════════════════
# 1. THE INCIDENT — no pricebook exists at all
# ════════════════════════════════════════════════════════════════════════════
def test_no_pricebook_alert_says_menu_only_and_makes_no_previous_version_claim(box):
    """The reproduced incident. The owner must be told the corrected menu is
    what prices quotes — not that a pricebook they do not have froze."""
    box.stage_pending(fingerprint=None)
    assert not box.pricebook.exists(), "this deployment has no pricebook, by setup"

    rc, payload, alerts = _approve(box)
    assert rc == 0, payload
    assert payload["pricebook_activated"] is False

    # The MESSAGE is asserted before the JSON shape on purpose. Checking the new
    # `price_source` key first would make this test red against the pre-fix
    # script for a missing-field reason and say nothing about what the owner was
    # told — pinning an API, not the truth. Fail on the prose first.
    assert len(alerts) == 1, alerts
    alert = alerts[0]

    # The two claims the incident alert made, neither of which was true here.
    for lie in INCIDENT_LIES:
        assert lie not in alert["message"], (
            f"the alert still asserts {lie!r} on a deployment with no pricebook")
    assert "NOT updated" not in alert["title"], (
        "nothing was 'not updated' — there is no pricebook to update")

    # What IS true, stated: no pricebook, so the corrected menu prices quotes.
    assert "no pricebook" in alert["message"].lower()
    assert "menu v1" in alert["message"].lower(), alert["message"]
    assert "Quotes already finalized are unchanged." in alert["message"]
    # And WHY, named with the same token the audit row carries.
    assert "proposal_predates_pricebook_scope" in alert["message"]
    assert read_log_rows(box.log)[-1]["reason"] == "proposal_predates_pricebook_scope"

    # ...and only then the machine-readable state the SKILL branches on.
    assert payload["price_source"] == "menu_only"
    assert payload["live_pricebook_version"] is None


def test_no_pricebook_claim_matches_what_actually_prices_a_quote(box):
    """Pins the CLAIM to the MECHANISM: with no pricebook, load_pricebook returns
    None (so finalize takes the legacy branch) and the menu index carries the
    corrected price. If that ever stops being true, the sentence the owner is
    sent becomes a lie and this test is the thing that says so."""
    box.stage_pending(fingerprint=None)
    rc, payload, alerts = _approve(box)
    assert rc == 0

    assert catering_pricing.load_pricebook(box.pricebook) is None, (
        "the alert promises menu-based pricing, which holds only while the "
        "pricebook loader reports 'no pricebook' for an absent file")

    menu = json.loads(box.menu.read_text(encoding="utf-8"))
    index = {it["name"]: int(round(it["price_usd"])) for it in menu["items"]
             if it["available"] and it["price_usd"] is not None}
    # finalize-catering-menu:241 — current_price = menu_index[item.name].
    assert index["Masala Dosa"] == 12, (
        "the corrected menu price is what the legacy quote path charges")
    assert "corrected menu" in alerts[0]["message"]


# ════════════════════════════════════════════════════════════════════════════
# 2. A pricebook DOES exist and stays put
# ════════════════════════════════════════════════════════════════════════════
def test_live_pricebook_alert_names_the_version_that_stays_and_the_menu_fallback(box):
    """Materially different state, materially different message: here a
    pricebook really does stay where it is, and saying so is correct — but only
    for the items it prices."""
    doc = _seed_valid_pricebook(box)
    box.stage_pending(fingerprint=None)  # predates scope; a real pricebook is live

    rc, payload, alerts = _approve(box)
    assert rc == 0, payload
    assert payload["pricebook_activated"] is False
    assert payload["price_source"] == "pricebook"
    assert payload["live_pricebook_version"] == doc["version"]

    message = alerts[0]["message"]
    assert f"stays at v{doc['version']}" in message, message
    # The generic string claimed ALL quotes kept old prices. Only overridden
    # items do; the rest follow the corrected menu, and the alert must say both.
    assert "items it prices keep their existing prices" in message
    assert "corrected menu v1" in message
    assert "still use the old prices" not in message


def test_live_pricebook_claim_matches_the_kernel_override_precedence(box):
    """Pins the CLAIM to the MECHANISM: compute_quote resolves
    item_price_overrides BEFORE the menu, so an overridden item keeps the
    pricebook price while an un-overridden one takes the corrected menu price."""
    doc = _seed_valid_pricebook(box)
    box.stage_pending(fingerprint=None)
    rc, _payload, _alerts = _approve(box)
    assert rc == 0

    from schemas import Menu

    menu = Menu.model_validate(json.loads(box.menu.read_text(encoding="utf-8")))
    pricebook = catering_pricing.load_pricebook(box.pricebook)
    overrides = pricebook.item_price_overrides
    overridden = next(n for n in overrides if n in {i["name"] for i in CORRECTED_ITEMS})
    plain = next(i["name"] for i in CORRECTED_ITEMS if i["name"] not in overrides)

    qc = catering_pricing.compute_quote(
        10, None, [(overridden, 1), (plain, 1)], None, pricebook, menu)
    by_name = {ln.name: ln for ln in qc.lines}
    assert by_name[overridden].source == "override", (
        "the alert promises overridden items keep the pricebook price")
    assert by_name[overridden].unit_cents == overrides[overridden]
    assert by_name[plain].source == "menu", (
        "the alert promises un-overridden items follow the corrected menu")
    assert by_name[plain].unit_cents == catering_pricing.usd_to_cents(
        next(i["price_usd"] for i in CORRECTED_ITEMS if i["name"] == plain))


# ════════════════════════════════════════════════════════════════════════════
# 3. The pricebook file is present but will not load
# ════════════════════════════════════════════════════════════════════════════
def test_unloadable_pricebook_alert_says_quoting_is_blocked_not_old_prices(box):
    """The worst state got the mildest message. With an unloadable pricebook no
    new quote can be finalized at ALL — telling the owner "quotes still use the
    old prices" describes a functioning system that is in fact refusing."""
    box.pricebook.write_text('{"version": 1, "this": ', encoding="utf-8")
    box.stage_pending(fingerprint="v1@2026-01-01T00:00:00+00:00")

    rc, payload, alerts = _approve(box)
    assert rc == 0, payload
    assert payload["price_source"] == "blocked"

    alert = alerts[0]
    assert "BLOCKED" in alert["title"], alert["title"]
    assert "No new quote can be finalized" in alert["message"]
    for lie in INCIDENT_LIES:
        assert lie not in alert["message"]
    assert "active_pricebook_unreadable" in alert["message"]


def test_unloadable_pricebook_claim_matches_finalize_refusing_to_quote(box):
    """Pins the CLAIM to the MECHANISM: finalize-catering-menu._load_pricebook
    exits 2 on a present-but-unloadable file rather than quoting at menu
    prices, which is exactly the 'quoting is blocked' the alert asserts."""
    box.pricebook.write_text('{"version": 1, "this": ', encoding="utf-8")

    with pytest.raises(catering_pricing.PricingError):
        catering_pricing.load_pricebook(box.pricebook)

    mod = load_script("truth_finalize_probe", SCRIPTS / "finalize-catering-menu")
    mod.PRICEBOOK_PATH = box.pricebook
    err = io.StringIO()
    with pytest.raises(SystemExit) as exc, redirect_stderr(err):
        mod._load_pricebook()
    assert exc.value.code == 2, "finalize must refuse, not degrade to menu prices"
    assert "refusing to finalize" in err.getvalue()


# ════════════════════════════════════════════════════════════════════════════
# 4. The whole point: different states, different messages
# ════════════════════════════════════════════════════════════════════════════
def test_the_three_price_sources_produce_three_distinguishable_messages(box):
    """The defect was ONE string across materially different outcomes. Assert
    the states are actually distinguishable to the person reading the alert —
    a message that is merely 'present' pins nothing."""
    mod = load_script("truth_alert_builder", SCRIPTS / "apply-menu-update")
    built = {
        kind: mod._owner_alert_for_failed_activation(
            3, "proposal_predates_pricebook_scope",
            "proposal_predates_pricebook_scope: static prose", source)
        for kind, source in (
            ("menu_only", mod.PriceSource("menu_only")),
            ("pricebook", mod.PriceSource("pricebook", 7)),
            ("blocked", mod.PriceSource("blocked")),
        )
    }
    titles = [t for t, _b in built.values()]
    bodies = [b for _t, b in built.values()]
    assert len(set(titles)) == 3, titles
    assert len(set(bodies)) == 3, bodies

    # Each names its own state and no other's. The WHY is shared here (same
    # reason for all three), so any difference must come from the measured
    # price source alone — which is exactly the axis the bug collapsed.
    assert "no pricebook on this deployment" in built["menu_only"][1]
    assert "stays at v7" in built["pricebook"][1]
    assert "no pricebook" not in built["pricebook"][1].lower(), (
        "a live pricebook must never be described as absent")
    assert "stays at v" not in built["menu_only"][1], (
        "the incident: claiming a version froze when no pricebook exists")
    assert "BLOCKED" in built["blocked"][0]
    assert "No new quote can be finalized" in built["blocked"][1]


def test_a_different_reason_in_the_same_price_source_still_reads_differently(box):
    """Two failures, one price source: the WHY must still differ, or the owner
    cannot tell a by-design decline from an importer crash."""
    mod = load_script("truth_alert_reasons", SCRIPTS / "apply-menu-update")
    source = mod.PriceSource("pricebook", 4)
    predates = mod._owner_alert_for_failed_activation(
        2, "proposal_predates_pricebook_scope", "proposal_predates_pricebook_scope: x",
        source)[1]
    crashed = mod._owner_alert_for_failed_activation(
        2, "import_exit_5", "import_exit_5: ValidationError on tax_rate_bps", source)[1]
    assert predates != crashed
    assert "predates pricebook activation" in predates
    assert "import_exit_5" in crashed
    # The dynamic remainder is triage data and must survive...
    assert "ValidationError on tax_rate_bps" in crashed
    # ...while a canned reason's fixed prose must NOT be appended a second time
    # after the headline already said it.
    assert predates.endswith("Reason: proposal_predates_pricebook_scope"), predates


def test_the_alert_body_is_plain_text_within_the_pushover_cap(box):
    """House rule (§12b): system-health alerts are plain text — the reason
    tokens carry underscores and a Markdown parse would eat them and hand the
    owner a mangled message that Telegram/Pushover still return 200 for. Also
    stay inside the 1024-char cap shift-agent-notify-owner truncates at."""
    mod = load_script("truth_alert_plain", SCRIPTS / "apply-menu-update")
    _title, body = mod._owner_alert_for_failed_activation(
        3, "import_exit_5", "import_exit_5: " + "x" * 500,
        mod.PriceSource("pricebook", 9))
    assert len(body) <= 1024, len(body)
    # The body carries no Markdown of its own, so nothing is LOST by sending it
    # as plain text...
    for delimiter in ("*", "`", "[", "]", "~"):
        assert delimiter not in body, (delimiter, body)
    # ...and it DOES carry underscores (the reason token), so something IS lost
    # by sending it as Markdown. That asymmetry is the rule.
    assert "import_exit_5" in body and "_" in body


def test_success_reports_the_activated_version_as_the_live_price_source(box):
    """The truthful-state contract is not failure-only: on a real activation the
    JSON must say the pricebook is what prices quotes, at the version that
    actually landed."""
    _seed_valid_pricebook(box)
    box.stage_pending(fingerprint=_fingerprint_now(box))

    rc, payload, alerts = _approve(box)
    assert rc == 0, payload
    assert payload["pricebook_activated"] is True, payload
    assert payload["price_source"] == "pricebook"
    activated = catering_pricing.load_pricebook(box.pricebook).version
    assert payload["live_pricebook_version"] == activated, (
        "the reported live version must be the one the importer actually wrote")
    assert f"v{activated}" in payload["pricebook_effect"]
    assert alerts == [], "a successful activation must not page the owner"
