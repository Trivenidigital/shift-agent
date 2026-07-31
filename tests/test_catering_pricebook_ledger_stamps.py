"""M2 — quote-ledger provenance stamps (menu_version / pricebook_version /
price_status) on CateringQuoteLedgerRecord.

Split by platform need:
  * schema cells run everywhere (pure Pydantic) — including the backward-compat
    cell proving records written before M2 still load.
  * append cells need safe_io's fcntl locking, so they are Linux-only.
"""
from __future__ import annotations

import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_PLATFORM_DIR = REPO / "src" / "platform"
if str(_PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_DIR))

from schemas import CateringQuoteLedgerRecord  # noqa: E402

TS = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def _record_kwargs(**over) -> dict:
    base = dict(
        ledger_entry_id="Q0001", lead_id="CAT-0001", version=1,
        quote_text="Total: $900", quote_total_usd=900,
        selected_items=[{"name": "Samosa", "qty": 4, "price_usd": 3}],
        source="customer_finalize", created_at=TS,
    )
    base.update(over)
    return base


# ── schema (runs everywhere) ─────────────────────────────────────────────────
def test_pre_m2_record_without_stamps_still_validates():
    """THE backward-compat assertion: every version committed before the pricing
    kernel landed must keep loading, carrying None."""
    rec = CateringQuoteLedgerRecord(**_record_kwargs())
    assert rec.menu_version is None
    assert rec.pricebook_version is None
    assert rec.price_status is None


def test_stamped_record_round_trips_through_json():
    rec = CateringQuoteLedgerRecord(**_record_kwargs(
        menu_version=7, pricebook_version=4, price_status="exact"))
    reloaded = CateringQuoteLedgerRecord.model_validate_json(rec.model_dump_json())
    assert (reloaded.menu_version, reloaded.pricebook_version,
            reloaded.price_status) == (7, 4, "exact")


@pytest.mark.parametrize("status", ["exact", "estimated", "pending_owner_review"])
def test_every_price_status_is_accepted(status):
    assert CateringQuoteLedgerRecord(
        **_record_kwargs(price_status=status)).price_status == status


def test_unknown_price_status_is_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CateringQuoteLedgerRecord(**_record_kwargs(price_status="probably_fine"))


def test_version_stamps_must_be_positive():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CateringQuoteLedgerRecord(**_record_kwargs(menu_version=0))
    with pytest.raises(ValidationError):
        CateringQuoteLedgerRecord(**_record_kwargs(pricebook_version=0))


# ── append path (Linux-only: safe_io needs fcntl) ────────────────────────────
linux_only = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="catering_quote_ledger.append_version uses safe_io (fcntl, Linux only)",
)


def _runner_ids():
    if os.name == "posix":
        import grp
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
    return ("shift-agent", "shift-agent")


@linux_only
def test_append_version_persists_the_stamps(tmp_path):
    import catering_quote_ledger as ql
    owner, group = _runner_ids()
    data_path = tmp_path / "catering-quote-ledger.json"

    res = ql.append_version(
        lead_id="CAT-0001", quote_text="Total: $900", quote_total_usd=900,
        selected_items=[{"name": "Samosa", "qty": 4, "price_usd": 3}],
        source="customer_finalize", created_at=TS, data_path=data_path,
        menu_version=7, pricebook_version=4, price_status="exact",
        expected_owner=owner, expected_group=group, emit_audit=False,
    )
    assert res.ok, res.reason
    stored = json.loads(data_path.read_text(encoding="utf-8"))["records"][0]
    assert stored["menu_version"] == 7
    assert stored["pricebook_version"] == 4
    assert stored["price_status"] == "exact"


@linux_only
def test_append_version_without_stamps_stores_nulls(tmp_path):
    """A caller that has no pricebook must still be able to append."""
    import catering_quote_ledger as ql
    owner, group = _runner_ids()
    data_path = tmp_path / "catering-quote-ledger.json"

    res = ql.append_version(
        lead_id="CAT-0002", quote_text="", quote_total_usd=120,
        selected_items=[], source="owner_edit", created_at=TS,
        data_path=data_path, expected_owner=owner, expected_group=group,
        emit_audit=False,
    )
    assert res.ok, res.reason
    stored = json.loads(data_path.read_text(encoding="utf-8"))["records"][0]
    assert stored["menu_version"] is None
    assert stored["pricebook_version"] is None
    assert stored["price_status"] is None


@linux_only
def test_stamps_do_not_disturb_version_monotonicity(tmp_path):
    import catering_quote_ledger as ql
    owner, group = _runner_ids()
    data_path = tmp_path / "catering-quote-ledger.json"
    for n, status in enumerate(("estimated", "exact", "pending_owner_review"), start=1):
        res = ql.append_version(
            lead_id="CAT-0003", quote_text=f"v{n}", quote_total_usd=100 * n,
            selected_items=[], source="customer_finalize", created_at=TS,
            data_path=data_path, menu_version=n, pricebook_version=n,
            price_status=status, expected_owner=owner, expected_group=group,
            emit_audit=False,
        )
        assert res.ok and res.version == n, res.reason
    history = ql.history("CAT-0003", data_path=data_path)
    assert [r["version"] for r in history] == [1, 2, 3]
    assert [r["price_status"] for r in history] == [
        "estimated", "exact", "pending_owner_review"]
