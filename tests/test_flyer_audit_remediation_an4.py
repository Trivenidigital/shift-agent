"""AN-4 remediation: same-kind brand-asset re-upload must leave an audit row.

Finding AN-4 (flyer E2E adversarial audit 2026-07-13): re-uploading a logo or
template silently flipped the prior active same-kind asset to ``active=False``
with ZERO audit rows, even though the sanctioned ``set-flyer-brand-asset-state``
path already audits exactly that reversal (the 2026-06-17 wrong-brand incident).
Per §12b an AUTOMATED reversal of owner-applied brand-asset state must be audited
at the write site through the canonical decisions.log chokepoint, reusing the
existing ``FlyerBrandAssetStateChanged`` LogEntry variant.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.onboarding import handle_onboarding_message, store_brand_asset  # noqa: E402
from schemas import FlyerBrandAsset, FlyerCustomerStore, FlyerOnboardingSession  # noqa: E402


def _active_customer_store(state_path: Path, now: datetime) -> FlyerCustomerStore:
    store = FlyerCustomerStore()
    store.customers.append(store.new_customer(
        business_name="Triveni",
        business_address="300 S Polk St",
        public_phone="+17043243322",
        business_whatsapp_number="+17043243322",
        authorized_request_number="+19045550104",
        business_category="restaurant",
        preferred_language="en",
        plan_id="starter",
        now=now,
    ).model_copy(update={"status": "active"}))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")
    return store


def _brand_asset_rows(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    rows = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [r for r in rows if r.get("type") == "flyer_brand_asset_state_changed"]


def test_same_kind_logo_reupload_writes_deactivation_audit_row(tmp_path, monkeypatch):
    """Site 1 — a registered owner re-uploading a logo silently flipped the prior
    logo to inactive with no audit row. Assert the reversal now writes one
    FlyerBrandAssetStateChanged row through the decisions.log chokepoint."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    _active_customer_store(state_path, now)

    first_logo = tmp_path / "logo1.png"
    first_logo.write_bytes(b"first")
    second_logo = tmp_path / "logo2.png"
    second_logo.write_bytes(b"second")

    store_brand_asset(
        state_path=state_path,
        chat_id="17043243322@s.whatsapp.net",
        sender_phone="+17043243322",
        message_id="logo1",
        media_path=first_logo,
        text="logo",
        now=now,
        audit_log_path=log_path,
    )
    # First upload has no prior active same-kind asset → nothing is reversed yet.
    assert _brand_asset_rows(log_path) == []

    result = store_brand_asset(
        state_path=state_path,
        chat_id="17043243322@s.whatsapp.net",
        sender_phone="+17043243322",
        message_id="logo2",
        media_path=second_logo,
        text="replace logo",
        now=now,
        audit_log_path=log_path,
    )
    assert result.next_status == "brand_asset_saved"

    updated = FlyerCustomerStore.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    logos = [a for a in updated.customers[0].brand_assets if a.kind == "logo"]
    assert [a.active for a in logos] == [False, True]
    deactivated = next(a for a in logos if not a.active)
    new_active = next(a for a in logos if a.active)
    customer_id = updated.customers[0].customer_id

    rows = _brand_asset_rows(log_path)
    assert len(rows) == 1, f"expected exactly one deactivation audit row, got {rows}"
    row = rows[0]
    assert row["asset_id"] == deactivated.asset_id
    assert row["customer_id"] == customer_id
    assert row["prior_active"] is True
    assert row["new_active"] is False
    # applied_by must mark this as an AUTOMATED same-kind-reupload reversal so it
    # is distinguishable from an operator-initiated set-flyer-brand-asset-state flip.
    assert "same_kind_reupload" in row["applied_by"]
    # The row names the replacement so an operator can trace what superseded it.
    assert new_active.asset_id in row["reason"]


def test_recovered_sender_merge_audits_same_kind_deactivation(tmp_path, monkeypatch):
    """Site 3 — a second sender recovering an existing business merges a pending
    logo that supersedes the customer's active logo. The committed logo flipped
    to inactive silently; assert that merge-path reversal is now audited too."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "customers.json"
    log_path = tmp_path / "decisions.log"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)

    store = FlyerCustomerStore()
    existing = store.new_customer(
        business_name="Triveni Cafe",
        business_address="300 S Polk St, Dallas TX",
        public_phone="+17329837841",
        business_whatsapp_number="+17329837841",
        authorized_request_number="+17329837841",
        business_category="restaurant",
        preferred_language="en",
        plan_id="trial",
        now=now,
        primary_chat_id="17329837841@s.whatsapp.net",
        onboarded_by_phone="+17329837841",
    )
    existing = existing.model_copy(update={
        "status": "trial",
        "brand_assets": [FlyerBrandAsset(
            asset_id="B0001",
            kind="logo",
            path=str(tmp_path / "b0001.png"),
            mime_type="image/png",
            sha256="a" * 64,
            original_message_id="m0",
            received_at=now,
            active=True,
        )],
    })
    store.customers = [existing]
    store.next_brand_asset_sequence = 3
    store.onboarding_sessions = [FlyerOnboardingSession(
        chat_id="19045550199@s.whatsapp.net",
        sender_phone="+19045550199",
        status="confirming_summary",
        started_at=now,
        updated_at=now,
        last_message_id="summary",
        business_name="Triveni Cafe",
        business_address="100 Main St, Dallas TX",
        public_phone="+19045550199",
        business_whatsapp_number="+17329837841",
        authorized_request_number="+19045550199",
        business_category="restaurant",
        preferred_language="en",
        plan_id="trial",
        pending_brand_assets=[FlyerBrandAsset(
            asset_id="B0002",
            kind="logo",
            path=str(tmp_path / "b0002.png"),
            mime_type="image/png",
            sha256="b" * 64,
            original_message_id="m1",
            received_at=now,
            active=True,
        )],
    )]
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="19045550199@s.whatsapp.net",
        sender_phone="+19045550199",
        message_id="confirm-recover",
        text="CONFIRM",
        now=now,
        audit_log_path=log_path,
    )
    assert result.handled is True

    updated = FlyerCustomerStore.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    customer = updated.customers[0]
    logos = {a.asset_id: a.active for a in customer.brand_assets if a.kind == "logo"}
    assert logos.get("B0001") is False, f"expected B0001 flipped inactive, got {logos}"
    assert logos.get("B0002") is True

    rows = _brand_asset_rows(log_path)
    assert len(rows) == 1, f"expected one recovery-merge deactivation row, got {rows}"
    row = rows[0]
    assert row["asset_id"] == "B0001"
    assert row["customer_id"] == customer.customer_id
    assert row["prior_active"] is True
    assert row["new_active"] is False
    assert "same_kind_reupload" in row["applied_by"]
    assert "B0002" in row["reason"]
