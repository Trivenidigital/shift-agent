"""The daily brief must name the OWNER as the blocker on leads that wait on him.

Live defect this file pins (2026-08-22, tasks/audits/catering-stuck-leads-2026-08-22.md):
three leads sat in ``AWAITING_OWNER_APPROVAL`` for up to 74 days while the brief
printed them under "Awaiting customer finalize" and reported "Awaiting your
approve" from ``CUSTOMER_FINALIZED`` alone. The count was right and the
attribution was wrong, which is the one error shape that reliably produces owner
inaction: he was told to wait on someone else.

The owner-blocked set is DERIVED from ``CATERING_TRANSITIONS`` rather than written
out, so a new owner-actionable status joins the brief automatically. The derivation
is itself asserted below — a hardcoded pair is exactly how this under-reported
before.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import platform
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="send-daily-brief imports safe_io which depends on fcntl",
)

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
SEND_BRIEF = REPO / "src" / "agents" / "daily_brief" / "scripts" / "send-daily-brief"


def _load_send_brief(env_dir: Path):
    sys.path.insert(0, str(PLATFORM_DIR))
    for modname in ("schemas", "safe_io", "exit_codes", "log_source"):
        path = PLATFORM_DIR / f"{modname}.py"
        loader = importlib.machinery.SourceFileLoader(modname, str(path))
        spec = importlib.util.spec_from_file_location(modname, str(path), loader=loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)

    loader = importlib.machinery.SourceFileLoader("send_brief_owner_blocked", str(SEND_BRIEF))
    spec = importlib.util.spec_from_file_location(
        "send_brief_owner_blocked", str(SEND_BRIEF), loader=loader,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.TEMPLATES_DIR = REPO / "src" / "agents" / "daily_brief" / "templates"
    mod.CATERING_LEADS_PATH = env_dir / "state" / "catering-leads.json"
    mod.CATERING_LEARNING_SUMMARY_PATH = env_dir / "state" / "catering-learning-summary.json"
    return mod


def _write_leads(env_dir: Path, leads: list[dict]) -> None:
    path = env_dir / "state" / "catering-leads.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"leads": leads}), encoding="utf-8")


# The COMPLETE key set of a production catering lead, taken from
# /opt/shift-agent/state/catering-leads.json on 2026-08-22. Fixtures carry every
# key, not the handful this renderer happens to read today.
#
# Hand-picking "the fields that matter" is how a fixture omits exactly what the
# code fails on. Here the omission would have been load-bearing: `quote_text`,
# `raw_inquiry`, `extracted` and `selected_items` are free-text/nested fields that
# carry customer names and dollar amounts in production. A HARD-RULES test run
# against a fixture that lacks them proves nothing about whether they leak — it
# just proves absent fields cannot be rendered.
_PROD_LEAD_KEYS = (
    "created_at", "customer_finalized_at", "customer_name", "customer_phone",
    "customer_replied", "deposit_amount_cents", "deposit_commerce_order_id",
    "deposit_minted_at", "deposit_payment_intent_id", "deposit_payment_reference",
    "deposit_required", "deposit_status", "extracted", "last_finalize_message_id",
    "lead_id", "original_message_id", "owner_approval_code", "quote_text",
    "quote_total_usd", "quote_version", "raw_inquiry", "selected_items",
    "status", "updated_at",
)


def _lead(lead_id: str, status: str, code: str, age_days: float, now: datetime,
          **extra) -> dict:
    """A lead carrying the full production key set.

    Every free-text field is seeded with content that WOULD violate a HARD RULE if
    it were ever rendered — a dollar total, a customer name, a phone number — so
    the HARD-RULES tests are exercised against the real shape rather than against
    a fixture that omits the risky fields.
    """
    ts = (now - timedelta(days=age_days)).isoformat()
    lead = {
        "lead_id": lead_id,
        "status": status,
        "owner_approval_code": code,
        "created_at": ts,
        "updated_at": ts,
        "customer_finalized_at": None,
        "customer_name": "",
        "customer_phone": "+15550000000",
        "customer_replied": False,
        "deposit_amount_cents": 0,
        "deposit_commerce_order_id": "",
        "deposit_payment_intent_id": "",
        "deposit_payment_reference": "",
        "deposit_required": False,
        "deposit_status": "none",
        "deposit_minted_at": None,
        "extracted": {"headcount": 200, "event_date": "2026-09-01",
                      "contact_name": "Anjali Iyer", "budget_usd": 4820.50},
        "last_finalize_message_id": None,
        "original_message_id": "3ABA73E9EAC201BD6AE5",
        "quote_text": "Hi Anjali Iyer — your quote for 200 guests is $4,820.50.",
        "quote_total_usd": None,
        "quote_version": 0,
        "raw_inquiry": "Catering for my cousin Ravi Kumar's wedding, budget $5000",
        "selected_items": [{"name": "Idly (3 PCS)", "price_usd": 5.99, "qty": 200}],
    }
    lead.update(extra)
    missing = set(_PROD_LEAD_KEYS) - set(lead)
    assert not missing, f"fixture drifted from the production lead shape: {missing}"
    return lead


@pytest.fixture()
def now() -> datetime:
    return datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)


# ── the derivation itself ────────────────────────────────────────────────────

def test_owner_blocked_set_is_derived_from_the_transition_table(tmp_path):
    """A status is owner-blocked iff the owner can move it next.

    Pinning the derivation, not a literal: the previous fix under-reported because
    it wrote the pair out by hand. If a future status gains an OWNER_* exit it must
    appear here without anyone editing the brief.
    """
    mod = _load_send_brief(tmp_path)
    from schemas import CATERING_TRANSITIONS  # noqa: PLC0415

    expected = {
        status for status, exits in CATERING_TRANSITIONS.items()
        if exits & {"OWNER_APPROVED", "OWNER_EDITED", "OWNER_REJECTED"}
    }
    assert mod.OWNER_BLOCKED_CATERING_STATUSES == expected
    # Guards against a silently-empty derivation passing the line above.
    assert expected == {"AWAITING_OWNER_APPROVAL", "CUSTOMER_FINALIZED", "OWNER_EDITED"}


def test_owner_blocked_set_is_a_superset_of_the_watchdog_actionable_set(tmp_path):
    """The brief must never claim fewer open owner items than the watchdog will act on.

    catering-owner-action-watchdog gates its fallback on
    {AWAITING_OWNER_APPROVAL, OWNER_EDITED}. A lead it would apply an owner decision
    to, but that the brief omits, is an owner item nobody surfaces.
    """
    mod = _load_send_brief(tmp_path)
    assert {"AWAITING_OWNER_APPROVAL", "OWNER_EDITED"} <= mod.OWNER_BLOCKED_CATERING_STATUSES


# ── the live defect ──────────────────────────────────────────────────────────

def test_awaiting_owner_approval_is_not_attributed_to_the_customer(tmp_path, now):
    """RED before the fix: the three stuck leads rendered as 'Awaiting customer finalize'."""
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [
        _lead("L0017", "AWAITING_OWNER_APPROVAL", "#4SX94", 74, now),
        _lead("L0018", "AWAITING_OWNER_APPROVAL", "#8D9YG", 32, now),
        _lead("L0019", "AWAITING_OWNER_APPROVAL", "#7GCQP", 30, now),
    ])
    out = mod._render_catering(now)

    assert "Awaiting customer finalize: 3" not in out
    assert "customer finalize" not in out.lower()
    assert "Awaiting your decision" in out


def test_owner_blocked_count_includes_every_owner_blocked_status(tmp_path, now):
    """The count is the union, not one status. Live shape: 3 awaiting + 2 finalized."""
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [
        _lead("L0017", "AWAITING_OWNER_APPROVAL", "#4SX94", 74, now),
        _lead("L0018", "AWAITING_OWNER_APPROVAL", "#8D9YG", 32, now),
        _lead("L0019", "AWAITING_OWNER_APPROVAL", "#7GCQP", 30, now),
        _lead("L0020", "CUSTOMER_FINALIZED", "#KYHWU", 28, now),
        _lead("L0021", "CUSTOMER_FINALIZED", "#PQR22", 3, now),
    ])
    out = mod._render_catering(now)
    assert "Awaiting your decision" in out
    decision_line = next(l for l in out.splitlines() if "Awaiting your decision" in l)
    assert decision_line.rstrip().endswith("5"), decision_line


def test_owner_edited_is_counted_too(tmp_path, now):
    """OWNER_EDITED carries an OWNER_REJECTED exit, so it is an open owner item.

    It is also unreachable by catering-lead-ttl-sweep (which selects only
    AWAITING_OWNER_APPROVAL / QUALIFYING) AND has no STALE edge in the transition
    table, so nothing will ever retire it. The brief is the only place it can surface.
    """
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [_lead("L0030", "OWNER_EDITED", "#EDIT1", 40, now)])
    out = mod._render_catering(now)
    decision_line = next(l for l in out.splitlines() if "Awaiting your decision" in l)
    assert decision_line.rstrip().endswith("1"), decision_line
    assert "#EDIT1" in out


# ── negative control: the status that really does wait on the customer ───────

def test_customer_finalized_still_counts_as_owner_blocked(tmp_path, now):
    """Negative control for the fix's blast radius.

    CUSTOMER_FINALIZED was the ONLY status the old code counted as owner-blocked,
    and it exits to OWNER_APPROVED/EDITED/REJECTED, so it must keep counting. A fix
    that swapped one wrong set for another would trip here.
    """
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [_lead("L0020", "CUSTOMER_FINALIZED", "#KYHWU", 5, now)])
    out = mod._render_catering(now)
    decision_line = next(l for l in out.splitlines() if "Awaiting your decision" in l)
    assert decision_line.rstrip().endswith("1"), decision_line
    assert "#KYHWU" in out


def test_qualifying_is_the_customer_blocked_line(tmp_path, now):
    """QUALIFYING is the status that genuinely waits on the customer.

    Per catering_lead_sweep: 'QUALIFYING is waiting on the CUSTOMER, mid-conversation'.
    It must never be folded into the owner's count.
    """
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [
        _lead("L0040", "QUALIFYING", "#QUAL1", 2, now),
        _lead("L0017", "AWAITING_OWNER_APPROVAL", "#4SX94", 74, now),
    ])
    out = mod._render_catering(now)
    decision_line = next(l for l in out.splitlines() if "Awaiting your decision" in l)
    assert decision_line.rstrip().endswith("1"), decision_line
    assert "Awaiting customer reply: 1" in out


def test_sent_to_customer_is_not_owner_blocked(tmp_path, now):
    """SENT_TO_CUSTOMER exits only to BOOKED/CLOSED/STALE — no owner verb."""
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [_lead("L0050", "SENT_TO_CUSTOMER", "#SENT1", 9, now)])
    out = mod._render_catering(now)
    decision_line = next(l for l in out.splitlines() if "Awaiting your decision" in l)
    assert decision_line.rstrip().endswith("0"), decision_line
    assert "Quotes sent to customers: 1" in out


def test_terminal_leads_are_never_owner_blocked(tmp_path, now):
    """STALE/CLOSED/OWNER_REJECTED/NOT_CATERING have empty exit sets."""
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [
        _lead("L0060", "STALE", "#ST001", 90, now),
        _lead("L0061", "CLOSED", "#CL001", 90, now),
        _lead("L0062", "OWNER_REJECTED", "#RJ001", 90, now),
        _lead("L0063", "NOT_CATERING", "#NC001", 90, now),
    ])
    out = mod._render_catering(now)
    decision_line = next(l for l in out.splitlines() if "Awaiting your decision" in l)
    assert decision_line.rstrip().endswith("0"), decision_line


# ── the codes and the age ────────────────────────────────────────────────────

def test_codes_are_rendered_for_every_owner_blocked_lead(tmp_path, now):
    """The #XXXXX code is the token cf-router's F8 intercept acts on deterministically.

    Before the fix, codes were rendered only for CUSTOMER_FINALIZED, so the owner was
    never shown the codes for the leads that had waited longest.
    """
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [
        _lead("L0017", "AWAITING_OWNER_APPROVAL", "#4SX94", 74, now),
        _lead("L0020", "CUSTOMER_FINALIZED", "#KYHWU", 28, now),
        _lead("L0030", "OWNER_EDITED", "#EDIT1", 40, now),
    ])
    out = mod._render_catering(now)
    for code in ("#4SX94", "#KYHWU", "#EDIT1"):
        assert code in out


def test_oldest_age_is_rendered_so_74d_differs_from_1d(tmp_path, now):
    """Without an age, a 74-day-old lead reads exactly like one created yesterday."""
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [
        _lead("L0017", "AWAITING_OWNER_APPROVAL", "#4SX94", 74, now),
        _lead("L0019", "AWAITING_OWNER_APPROVAL", "#7GCQP", 1, now),
    ])
    out = mod._render_catering(now)
    assert "74d" in out

    _write_leads(tmp_path, [_lead("L0019", "AWAITING_OWNER_APPROVAL", "#7GCQP", 1, now)])
    fresh = mod._render_catering(now)
    assert "74d" not in fresh
    assert "1d" in fresh


def test_unparseable_updated_at_does_not_crash_or_invent_an_age(tmp_path, now):
    """A lead is still counted when its timestamp is junk; no age is fabricated for it."""
    mod = _load_send_brief(tmp_path)
    bad = _lead("L0070", "AWAITING_OWNER_APPROVAL", "#BAD01", 5, now)
    bad["updated_at"] = "not-a-timestamp"
    bad["created_at"] = "not-a-timestamp"
    _write_leads(tmp_path, [bad])
    out = mod._render_catering(now)
    decision_line = next(l for l in out.splitlines() if "Awaiting your decision" in l)
    assert decision_line.rstrip().endswith("1"), decision_line


# ── HARD RULES (2026-05-11 customer-name-hallucination finding) ──────────────

def test_hard_rule_no_dollar_totals_reach_the_brief(tmp_path, now):
    """Pricing is owner-only, out-of-band via the #XXXXX approve flow.

    Every fixture lead carries money in FOUR places — `quote_total_usd`,
    `quote_text`, `extracted.budget_usd`, `selected_items[].price_usd` — because
    the renderer reading any one of them would leak.
    """
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [
        _lead("L0017", "AWAITING_OWNER_APPROVAL", "#4SX94", 74, now,
              quote_total_usd=4820.50),
        _lead("L0020", "CUSTOMER_FINALIZED", "#KYHWU", 28, now,
              quote_total_usd=1299.99),
    ])
    out = mod._render_catering(now)
    assert "$" not in out
    for amount in ("4820", "1299", "5000", "5.99"):
        assert amount not in out, f"{amount!r} leaked into the brief"


def test_hard_rule_no_customer_names_reach_the_brief(tmp_path, now):
    """Cosmetic hallucination risk — the 2026-05-11 customer-name finding."""
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [
        _lead("L0017", "AWAITING_OWNER_APPROVAL", "#4SX94", 74, now,
              customer_name="Anjali Iyer"),
        _lead("L0020", "CUSTOMER_FINALIZED", "#KYHWU", 28, now,
              customer_name="Ravi Kumar"),
    ])
    out = mod._render_catering(now)
    assert "Anjali" not in out
    assert "Iyer" not in out
    assert "Ravi" not in out
    assert "Kumar" not in out


def test_hard_rule_no_customer_phone_reaches_the_brief(tmp_path, now):
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [
        _lead("L0017", "AWAITING_OWNER_APPROVAL", "#4SX94", 74, now,
              customer_phone="+17329837841"),
    ])
    out = mod._render_catering(now)
    assert "7329837841" not in out


# ── preserved behaviour ──────────────────────────────────────────────────────

def test_empty_leads_file_still_renders_nothing_without_learning(tmp_path, now):
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [])
    assert mod._render_catering(now) == ""


def test_missing_leads_file_still_renders_nothing_without_learning(tmp_path, now):
    mod = _load_send_brief(tmp_path)
    assert mod._render_catering(now) == ""


def test_new_leads_24h_window_is_unchanged(tmp_path, now):
    mod = _load_send_brief(tmp_path)
    _write_leads(tmp_path, [
        _lead("L0080", "AWAITING_OWNER_APPROVAL", "#NEW01", 0.5, now),
        _lead("L0017", "AWAITING_OWNER_APPROVAL", "#4SX94", 74, now),
    ])
    out = mod._render_catering(now)
    assert "New leads (24h): 1" in out
