"""M3 — catering proposal validity window + the expiry sweep.

Before M3 a SENT proposal set never expired: a customer could reply "option 2"
months later and select-catering-proposal would claim it against prices the owner
had since changed. Three layers here:

  * `catering_proposal_sweep` pure helpers — cross-platform (mirrors
    tests/test_catering_lead_sweep.py and tests/test_proposal_sweep.py).
  * `catering-proposal-expiry-sweep` — static-invariant scans (cross-platform) +
    subprocess integration (Linux-only, fcntl) driving the real transition,
    audit chokepoint, idempotency and flag-off inertness.
  * the customer-facing "valid until" line — rendered from the SAME config field
    the stored expires_at comes from, so the customer's date and the sweep's
    deadline can never disagree.
"""
from __future__ import annotations

import json
import os
import platform
import py_compile
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from catering_proposal_sweep import (
    DEFAULT_PROPOSAL_VALIDITY_DAYS,
    expires_at_from,
    find_expired_sent_proposal_sets,
    quote_validity_line,
    validity_end_date,
)
from schemas import CateringConfig, CateringProposalSet

NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
_REPO = Path(__file__).resolve().parent.parent
_SWEEP = _REPO / "src" / "agents" / "catering" / "scripts" / "catering-proposal-expiry-sweep"
_TEMPLATE_DIR = _REPO / "src" / "agents" / "catering" / "templates"
_SCRIPTS = _REPO / "src" / "agents" / "catering" / "scripts"


def _row(status: str, expires_in_days=None, set_id="CPS-L0001-000001"):
    expires_at = NOW + timedelta(days=expires_in_days) if expires_in_days is not None else None
    return SimpleNamespace(proposal_set_id=set_id, status=status, expires_at=expires_at)


# ── pure helpers ────────────────────────────────────────────────────────────
def test_default_validity_matches_the_config_default():
    """One number, two homes — a drift here silently changes what customers are told."""
    assert DEFAULT_PROPOSAL_VALIDITY_DAYS == CateringConfig().proposal_validity_days


def test_expires_at_is_sent_at_plus_validity():
    assert expires_at_from(NOW, 14) == NOW + timedelta(days=14)


def test_validity_end_date_is_a_plain_iso_date():
    assert validity_end_date(datetime(2026, 7, 31, 23, 30, tzinfo=timezone.utc), 14) == "2026-08-14"


def test_quote_validity_line_names_the_date():
    line = quote_validity_line(NOW, 14)
    assert line == "This quote is valid until 2026-08-14."


def test_stated_date_is_never_earlier_than_the_enforced_deadline():
    """The customer reads a DATE; the sweep enforces an INSTANT. The instant must
    fall at/after the end of the stated day, or we would retire a quote while the
    customer still believes it stands."""
    sent = datetime(2026, 7, 31, 9, 15, tzinfo=timezone.utc)
    stated = datetime.fromisoformat(validity_end_date(sent, 14)).replace(tzinfo=timezone.utc)
    assert expires_at_from(sent, 14) >= stated


def test_expired_sent_set_included():
    assert find_expired_sent_proposal_sets([_row("SENT", -1)], NOW) == ["CPS-L0001-000001"]


def test_boundary_exactly_at_expiry_included():
    assert find_expired_sent_proposal_sets([_row("SENT", 0)], NOW) == ["CPS-L0001-000001"]


def test_unexpired_sent_set_excluded():
    assert find_expired_sent_proposal_sets([_row("SENT", 3)], NOW) == []


def test_sent_set_without_expires_at_is_skipped_not_expired():
    """Pre-M3 sets carry no expires_at. Nobody told those customers a deadline, so
    the sweep must not invent one retroactively."""
    assert find_expired_sent_proposal_sets([_row("SENT", None)], NOW) == []


@pytest.mark.parametrize("status", [
    "DRAFT", "SEND_FAILED", "SUPERSEDED", "SELECTING", "SELECTED",
    "SELECTED_OWNER_CARD_FAILED", "SELECT_FAILED", "EXPIRED",
])
def test_non_sent_statuses_never_expire_even_when_ancient(status):
    assert find_expired_sent_proposal_sets([_row(status, -999)], NOW) == []


def test_expired_ids_are_sorted():
    rows = [
        _row("SENT", -1, "CPS-L0001-000003"),
        _row("SENT", -5, "CPS-L0001-000001"),
        _row("SENT", 5, "CPS-L0001-000002"),   # still valid
    ]
    assert find_expired_sent_proposal_sets(rows, NOW) == [
        "CPS-L0001-000001", "CPS-L0001-000003",
    ]


def test_empty_store():
    assert find_expired_sent_proposal_sets([], NOW) == []


# ── schema: expires_at is additive ──────────────────────────────────────────
def _set_kwargs(**over):
    base = dict(
        proposal_set_id="CPS-L0001-000001", lead_id="L0001", status="DRAFT",
        created_at=NOW, source_message_id="m1",
        options=[
            {"option_id": "1", "style_key": "balanced_mixed", "tier": "balanced",
             "item_names": ["Idli"]},
            {"option_id": "2", "style_key": "premium_mixed", "tier": "premium",
             "item_names": ["Biryani"]},
        ],
    )
    base.update(over)
    return base


def test_legacy_set_without_expires_at_still_loads():
    row = CateringProposalSet(**_set_kwargs())
    assert row.expires_at is None


def test_sent_set_carries_expires_at_when_supplied():
    row = CateringProposalSet(**_set_kwargs(
        status="SENT", sent_at=NOW, outbound_message_id="wamid.1",
        expires_at=expires_at_from(NOW, 14),
    ))
    assert row.expires_at == NOW + timedelta(days=14)


def test_expired_is_a_valid_persisted_status():
    row = CateringProposalSet(**_set_kwargs(status="EXPIRED"))
    assert row.status == "EXPIRED"


# ── customer-facing validity text (cross-platform render checks) ────────────
def test_customer_quote_template_carries_the_validity_slot():
    text = (_TEMPLATE_DIR / "catering_quote_to_customer.txt").read_text(encoding="utf-8")
    assert "{quote_validity_line}" in text


@pytest.mark.parametrize("script", ["create-catering-lead", "apply-catering-owner-decision"])
def test_customer_quote_render_paths_populate_the_validity_line(script):
    text = (_SCRIPTS / script).read_text(encoding="utf-8")
    assert "quote_validity_line" in text
    assert "proposal_validity_days" in text, (
        "the validity date must come from the SAME config field expires_at does"
    )


def test_rendered_customer_quote_states_the_validity_period():
    """The line a customer actually reads, rendered through the real template."""
    tmpl = (_TEMPLATE_DIR / "catering_quote_to_customer.txt").read_text(encoding="utf-8")
    rendered = tmpl.format(
        customer_name="Asha", headcount=80, event_date="2026-09-05", event_time="18:00",
        menu_section="", price_status_line="Price status: pending owner review",
        quote_validity_line=quote_validity_line(NOW, 14), lead_id="L0001",
    )
    assert "This quote is valid until 2026-08-14." in rendered


# ── static-invariant scans of the sweep script (cross-platform) ─────────────
def test_sweep_script_compiles():
    py_compile.compile(str(_SWEEP), doraise=True)


def test_sweep_gated_off_by_default_env_flag():
    t = _SWEEP.read_text(encoding="utf-8")
    assert "CATERING_PROPOSAL_SWEEP_ENABLED" in t
    assert "def _enabled" in t
    assert "if not _enabled()" in t


def test_sweep_uses_the_transition_table_and_the_audit_chokepoint():
    t = _SWEEP.read_text(encoding="utf-8")
    assert 'TERMINAL_STATUS = "EXPIRED"' in t
    assert "is_proposal_set_transition_allowed" in t
    assert "CateringProposalExpired" in t
    assert "find_expired_sent_proposal_sets" in t
    assert "atomic_write_json" in t


def test_sweep_owner_alert_is_plain_text_and_never_reaches_the_customer():
    t = _SWEEP.read_text(encoding="utf-8")
    assert "shift-agent-notify-owner" in t
    assert "catering_proposal_expiry_alert_dispatched" in t   # §12b
    assert "catering_proposal_expiry_alert_delivered" in t
    low = t.lower()
    assert "deposit" not in low and "stripe" not in low
    assert "send-catering-ack" not in t, "the sweep alerts the OWNER, never the customer"


def test_sweep_never_fails_its_timer():
    t = _SWEEP.read_text(encoding="utf-8")
    assert "a watchdog must never fail its timer" in t


# ── subprocess integration (Linux-only, fcntl) ──────────────────────────────
_LINUX_ONLY = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="sweep depends on safe_io which uses fcntl (Linux only)",
)


def _set_row(set_id, status, *, expires_at=None, sent_at=None, lead_id="L0001"):
    row = {
        "proposal_set_id": set_id, "lead_id": lead_id, "status": status,
        "created_at": "2026-01-01T00:00:00+00:00",
        "source_message_id": f"m-{set_id}",
        "options": [
            {"option_id": "1", "style_key": "balanced_mixed", "tier": "balanced",
             "item_names": ["Idli"]},
            {"option_id": "2", "style_key": "premium_mixed", "tier": "premium",
             "item_names": ["Biryani"]},
        ],
    }
    if status == "SENT":
        row["outbound_message_id"] = f"wamid.{set_id}"
        row["sent_at"] = sent_at or "2026-01-01T00:00:00+00:00"
    if expires_at is not None:
        row["expires_at"] = expires_at
    return row


def _seed(tmp_path):
    path = tmp_path / "catering-proposals.json"
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    path.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 5,
        "sets": [
            _set_row("CPS-L0001-000001", "SENT", expires_at=past),      # expired
            _set_row("CPS-L0001-000002", "SENT", expires_at=future),    # still valid
            _set_row("CPS-L0001-000003", "SENT"),                       # pre-M3, no expiry
            _set_row("CPS-L0001-000004", "SELECTED", expires_at=past),  # already chosen
        ],
    }), encoding="utf-8")
    return path


def _run_sweep(tmp_path, *, enabled, extra_args=()):
    env = {
        **os.environ,
        "PYTHONPATH": str(_REPO / "src" / "platform"),
        "SHIFT_AGENT_CATERING_PROPOSALS_PATH": str(tmp_path / "catering-proposals.json"),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(tmp_path / "decisions.log"),
        "SHIFT_AGENT_CONFIG_PATH": str(tmp_path / "no-such-config.yaml"),  # UTC fallback
    }
    if enabled:
        env["CATERING_PROPOSAL_SWEEP_ENABLED"] = "1"
    else:
        env.pop("CATERING_PROPOSAL_SWEEP_ENABLED", None)
    return subprocess.run([sys.executable, str(_SWEEP), *extra_args],
                          capture_output=True, text=True, env=env, timeout=30)


def _statuses(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {s["proposal_set_id"]: s["status"] for s in doc["sets"]}


def _rows(tmp_path):
    log = tmp_path / "decisions.log"
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]


@_LINUX_ONLY
def test_cli_expires_only_the_past_due_sent_set(tmp_path):
    path = _seed(tmp_path)
    r = _run_sweep(tmp_path, enabled=True)
    assert r.returncode == 0, r.stderr
    statuses = _statuses(path)
    assert statuses["CPS-L0001-000001"] == "EXPIRED"
    assert statuses["CPS-L0001-000002"] == "SENT", "in-window set untouched"
    assert statuses["CPS-L0001-000003"] == "SENT", "pre-M3 set (no expires_at) untouched"
    assert statuses["CPS-L0001-000004"] == "SELECTED", "already-selected set untouched"


@_LINUX_ONLY
def test_cli_writes_one_expiry_audit_row(tmp_path):
    _seed(tmp_path)
    _run_sweep(tmp_path, enabled=True)
    expired = [x for x in _rows(tmp_path) if x.get("type") == "catering_proposal_expired"]
    assert len(expired) == 1
    assert expired[0]["proposal_set_id"] == "CPS-L0001-000001"
    assert expired[0]["lead_id"] == "L0001"
    assert expired[0]["from_status"] == "SENT"
    assert expired[0]["validity_days"] >= 1


@_LINUX_ONLY
def test_cli_idempotent_second_run_is_a_noop(tmp_path):
    _seed(tmp_path)
    _run_sweep(tmp_path, enabled=True)
    _run_sweep(tmp_path, enabled=True)
    expired = [x for x in _rows(tmp_path) if x.get("type") == "catering_proposal_expired"]
    assert len(expired) == 1, "EXPIRED is terminal with no self-edge — no re-expiry"


@_LINUX_ONLY
def test_cli_flag_off_is_inert(tmp_path):
    path = _seed(tmp_path)
    r = _run_sweep(tmp_path, enabled=False)
    assert r.returncode == 0
    assert _statuses(path)["CPS-L0001-000001"] == "SENT", "dormant unless armed"
    assert _rows(tmp_path) == []


@_LINUX_ONLY
def test_cli_dry_run_reports_without_mutating(tmp_path):
    path = _seed(tmp_path)
    r = _run_sweep(tmp_path, enabled=False, extra_args=("--dry-run",))
    assert r.returncode == 0
    assert "would_expire CPS-L0001-000001" in r.stdout
    assert "CPS-L0001-000002" not in r.stdout
    assert _statuses(path)["CPS-L0001-000001"] == "SENT"
    assert _rows(tmp_path) == []


@_LINUX_ONLY
def test_cli_dispatches_the_owner_alert_at_the_write_site(tmp_path):
    """§12b: the operator is told at the moment the state is reversed. The notify
    binary is absent on the runner, so only the dispatched/failed pair is asserted."""
    _seed(tmp_path)
    r = _run_sweep(tmp_path, enabled=True)
    assert "catering_proposal_expiry_alert_dispatched set=CPS-L0001-000001" in r.stderr


# ── in-process integration (runs EVERYWHERE, incl. Windows) ─────────────────
# The subprocess cells above are the fidelity layer but skip off Linux, which
# would leave the sweep's actual behaviour unverified on the machine most of this
# is written on. fixtures_fleet's fcntl stub exists for exactly this: advisory
# locking is irrelevant in a single test process, so driving main() in-process
# exercises the same selection / transition / audit / idempotency code paths.
from fixtures_fleet import ensure_fcntl_stub, load_script  # noqa: E402

ensure_fcntl_stub()


def _run_sweep_inprocess(tmp_path, monkeypatch, *, enabled, argv=()):
    monkeypatch.setenv("SHIFT_AGENT_CATERING_PROPOSALS_PATH",
                       str(tmp_path / "catering-proposals.json"))
    monkeypatch.setenv("SHIFT_AGENT_DECISIONS_LOG_PATH", str(tmp_path / "decisions.log"))
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(tmp_path / "no-such-config.yaml"))
    if enabled:
        monkeypatch.setenv("CATERING_PROPOSAL_SWEEP_ENABLED", "1")
    else:
        monkeypatch.delenv("CATERING_PROPOSAL_SWEEP_ENABLED", raising=False)
    monkeypatch.setattr(sys, "argv", ["catering-proposal-expiry-sweep", *argv])
    mod = load_script("catering_proposal_expiry_sweep_mod", _SWEEP)
    # The Pushover binary does not exist on a test runner; the sweep already
    # swallows that, but stubbing keeps the cell free of a process spawn.
    monkeypatch.setattr(mod, "_alert_owner", lambda *a, **k: None)
    return mod.main()


def test_inprocess_expires_only_the_past_due_sent_set(tmp_path, monkeypatch):
    path = _seed(tmp_path)
    assert _run_sweep_inprocess(tmp_path, monkeypatch, enabled=True) == 0
    statuses = _statuses(path)
    assert statuses["CPS-L0001-000001"] == "EXPIRED"
    assert statuses["CPS-L0001-000002"] == "SENT"
    assert statuses["CPS-L0001-000003"] == "SENT"
    assert statuses["CPS-L0001-000004"] == "SELECTED"


def test_inprocess_writes_one_expiry_audit_row(tmp_path, monkeypatch):
    _seed(tmp_path)
    _run_sweep_inprocess(tmp_path, monkeypatch, enabled=True)
    expired = [x for x in _rows(tmp_path) if x.get("type") == "catering_proposal_expired"]
    assert len(expired) == 1
    assert expired[0]["proposal_set_id"] == "CPS-L0001-000001"
    assert expired[0]["from_status"] == "SENT"
    assert expired[0]["validity_days"] == DEFAULT_PROPOSAL_VALIDITY_DAYS


def test_inprocess_second_run_is_idempotent(tmp_path, monkeypatch):
    _seed(tmp_path)
    _run_sweep_inprocess(tmp_path, monkeypatch, enabled=True)
    _run_sweep_inprocess(tmp_path, monkeypatch, enabled=True)
    expired = [x for x in _rows(tmp_path) if x.get("type") == "catering_proposal_expired"]
    assert len(expired) == 1


def test_inprocess_flag_off_is_inert(tmp_path, monkeypatch):
    path = _seed(tmp_path)
    assert _run_sweep_inprocess(tmp_path, monkeypatch, enabled=False) == 0
    assert _statuses(path)["CPS-L0001-000001"] == "SENT"
    assert _rows(tmp_path) == []


def test_inprocess_dry_run_reports_without_mutating(tmp_path, monkeypatch, capsys):
    path = _seed(tmp_path)
    assert _run_sweep_inprocess(tmp_path, monkeypatch, enabled=False, argv=("--dry-run",)) == 0
    out = capsys.readouterr().out
    assert "would_expire CPS-L0001-000001" in out
    assert "CPS-L0001-000002" not in out
    assert _statuses(path)["CPS-L0001-000001"] == "SENT"
    assert _rows(tmp_path) == []


def test_inprocess_alerts_the_owner_once_per_expiry(tmp_path, monkeypatch):
    """§12b: one alert per state reversal, and none on the idempotent re-run."""
    _seed(tmp_path)
    calls: list[tuple] = []
    monkeypatch.setenv("SHIFT_AGENT_CATERING_PROPOSALS_PATH",
                       str(tmp_path / "catering-proposals.json"))
    monkeypatch.setenv("SHIFT_AGENT_DECISIONS_LOG_PATH", str(tmp_path / "decisions.log"))
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(tmp_path / "no-such-config.yaml"))
    monkeypatch.setenv("CATERING_PROPOSAL_SWEEP_ENABLED", "1")
    monkeypatch.setattr(sys, "argv", ["catering-proposal-expiry-sweep"])
    mod = load_script("catering_proposal_expiry_sweep_mod", _SWEEP)
    monkeypatch.setattr(mod, "_alert_owner", lambda *a: calls.append(a))
    mod.main()
    assert [c[1] for c in calls] == ["CPS-L0001-000001"]
    mod.main()
    assert len(calls) == 1, "a terminal transition fires its alert at most once"
