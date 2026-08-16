"""A catering deposit may only be minted on a box that has catering turned ON.

THE BUG THIS FILE PINS
----------------------
`_should_mint_deposit` gated on `cfg.catering.deposit_pct > 0` alone, and both
the schema default (`CateringConfig.deposit_pct = 0.25`) and the provisioning
template (`config.yaml.template`) shipped that percentage ARMED. So the only
thing standing between a freshly provisioned customer VPS and a real deposit
link was an operator remembering to write `deposit_pct: 0` by hand —
`catering: {enabled: false}` did NOT stop the mint. Deposits move money and are
not reversible from our side, so the arming must be affirmative on both axes.

The invariant: mint only when `catering.enabled` AND `deposit_pct > 0` AND the
existing eligibility checks pass.

The template assertion reads the shipped file itself (not a copy), so a future
template edit that drops the explicit `deposit_pct: 0` fails here rather than on
someone's live box.

Cross-platform: pure-function + file read, no safe_io / fcntl. Runs on Windows.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Ensure src/agents/catering on path so `from deposit import ...` works
_REPO_ROOT = Path(__file__).resolve().parent.parent
_CATERING_DIR = _REPO_ROOT / "src" / "agents" / "catering"
if str(_CATERING_DIR) not in sys.path:
    sys.path.insert(0, str(_CATERING_DIR))

from schemas import (  # noqa: E402
    CateringLead,
    CateringLeadExtractedFields,
    CateringConfig,
    CommerceConfig,
    Config,
    CustomerConfig,
    OwnerConfig,
    LimitsConfig,
    AlertingConfig,
    BackupConfig,
)

from deposit import _should_mint_deposit  # noqa: E402

TEMPLATE_PATH = _REPO_ROOT / "src" / "agents" / "shift" / "config.yaml.template"

TS = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

_UNSET = object()


def _cfg(enabled: bool, deposit_pct=_UNSET) -> Config:
    """Config with a catering block. Omitting deposit_pct exercises the SCHEMA
    DEFAULT, which is the shape a config.yaml that never mentions the key takes."""
    catering_kwargs = {"enabled": enabled, "deposit_threshold_guests": 50}
    if deposit_pct is not _UNSET:
        catering_kwargs["deposit_pct"] = deposit_pct
    return Config(
        schema_version=1,
        customer=CustomerConfig(
            name="Test", location_id="loc_test", timezone="America/New_York", languages=["en"],
        ),
        owner=OwnerConfig(name="Owner", phone="+15551234567", self_chat_jid=""),
        limits=LimitsConfig(
            max_outbound_per_day=2, max_outbound_per_minute=30,
            pending_proposal_ttl_hours=4, per_message_timeout_sec=120,
            send_failure_retry_count=1,
        ),
        alerting=AlertingConfig(
            pushover_user_key="test_user", pushover_app_token="test_token",
            healthchecks_io_url="", email="",
        ),
        backup=BackupConfig(gpg_recipient_email="test@example.com", s3_bucket="", retention_days=30),
        catering=CateringConfig(**catering_kwargs),
        commerce=CommerceConfig(minimum_deposit_cents=500),
    )


def _eligible_lead() -> CateringLead:
    """A lead that passes every OTHER eligibility check: 100 guests (>= 50),
    $600 quote, $6/guest (>= the 3.0 floor), never minted."""
    return CateringLead(
        lead_id="L0007",
        status="SENT_TO_CUSTOMER",
        customer_phone="+15551234567",
        customer_name="Lakshmi",
        raw_inquiry="x",
        original_message_id="m",
        created_at=TS,
        updated_at=TS,
        quote_text="x",
        quote_total_usd=600,
        extracted=CateringLeadExtractedFields(headcount=100, event_date="2026-09-15"),
        deposit_payment_intent_id="",
    )


# ─────────────────────────────────────────────────────────────────
# The five arming combinations
# ─────────────────────────────────────────────────────────────────

def test_disabled_with_armed_pct_does_not_mint():
    """The defect: catering off but deposit_pct left at the old armed default."""
    assert _should_mint_deposit(_cfg(enabled=False, deposit_pct=0.25), _eligible_lead()) is False


def test_disabled_with_zero_pct_does_not_mint():
    assert _should_mint_deposit(_cfg(enabled=False, deposit_pct=0.0), _eligible_lead()) is False


def test_enabled_with_zero_pct_does_not_mint():
    """Kill switch still works when catering itself is on."""
    assert _should_mint_deposit(_cfg(enabled=True, deposit_pct=0.0), _eligible_lead()) is False


def test_enabled_with_pct_omitted_does_not_mint():
    """A config that never mentions deposit_pct must be disarmed by default."""
    assert _should_mint_deposit(_cfg(enabled=True), _eligible_lead()) is False


def test_enabled_and_armed_and_eligible_mints():
    """Both switches on + eligibility satisfied → the existing path is unchanged."""
    assert _should_mint_deposit(_cfg(enabled=True, deposit_pct=0.25), _eligible_lead()) is True


def test_schema_default_pct_is_disarmed():
    assert CateringConfig().deposit_pct == 0.0


# ─────────────────────────────────────────────────────────────────
# Provisioning template
# ─────────────────────────────────────────────────────────────────

def test_shipped_template_disarms_deposits_explicitly():
    """The template must carry `deposit_pct: 0` in writing, not lean on the
    schema default: an operator copies this file, and a rollback to a release
    whose schema default is still 0.25 must not re-arm the box."""
    template = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
    shipped_pct = template["catering"]["deposit_pct"]
    # `== 0` alone would also accept `deposit_pct: false`, since bool is a subclass
    # of int in Python. A template carrying a bool would then pass the very test
    # whose job is proving the shipped value disarms deposits — and it would reach
    # CateringConfig's float coercion as 0.0 today, so nothing downstream would
    # catch it either. Demand a real number.
    assert not isinstance(shipped_pct, bool), f"deposit_pct must be numeric, got {shipped_pct!r}"
    assert isinstance(shipped_pct, (int, float))
    assert shipped_pct == 0
    assert template["catering"]["enabled"] is False
