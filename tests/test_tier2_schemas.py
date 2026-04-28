"""Tests for Tier 2 agent config schemas (Agents 6, 7, 9, 10, 12-16).
All Windows-runnable. Backward-compat verified for old configs missing all 9 blocks."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas import (
    Config,
    InventoryConfig, SupplierConfig, VipConfig, CateringFollowupConfig,
    HiringConfig, ComplianceConfig, EmployeeDocsConfig, CashArConfig, SalesTaxConfig,
)


def test_all_tier2_default_disabled():
    """Every Tier 2 agent defaults to enabled=False (opt-in)."""
    assert InventoryConfig().enabled is False
    assert SupplierConfig().enabled is False
    assert VipConfig().enabled is False
    assert CateringFollowupConfig().enabled is False
    assert HiringConfig().enabled is False
    assert ComplianceConfig().enabled is False
    assert EmployeeDocsConfig().enabled is False
    assert CashArConfig().enabled is False
    assert SalesTaxConfig().enabled is False


def test_all_tier2_extra_forbid():
    for cls in (InventoryConfig, SupplierConfig, VipConfig, CateringFollowupConfig,
                HiringConfig, ComplianceConfig, EmployeeDocsConfig, CashArConfig, SalesTaxConfig):
        with pytest.raises(ValidationError):
            cls(typo=True)


def test_inventory_thresholds_positive():
    with pytest.raises(ValidationError):
        InventoryConfig(low_stock_threshold_days=0)
    with pytest.raises(ValidationError):
        InventoryConfig(expiry_warning_days=0)


def test_compliance_warning_days_validator():
    # empty rejected
    with pytest.raises(ValidationError):
        ComplianceConfig(advance_warning_days=[])
    # negative rejected
    with pytest.raises(ValidationError):
        ComplianceConfig(advance_warning_days=[30, -1])
    # sorted descending + dedup
    c = ComplianceConfig(advance_warning_days=[7, 30, 30, 14, 1])
    assert c.advance_warning_days == [30, 14, 7, 1]


def test_employee_docs_warning_days_validator():
    c = EmployeeDocsConfig(advance_warning_days=[14, 30, 90, 60])
    assert c.advance_warning_days == [90, 60, 30, 14]


def test_sales_tax_warning_days_validator():
    c = SalesTaxConfig(advance_warning_days=[1, 14, 7, 3])
    assert c.advance_warning_days == [14, 7, 3, 1]


def test_cash_ar_cadence_and_threshold():
    CashArConfig(reminder_cadence_days=[7, 14, 30, 45], escalate_threshold_days=60)
    with pytest.raises(ValidationError):
        CashArConfig(escalate_threshold_days=0)


def test_supplier_followup_hours_positive():
    with pytest.raises(ValidationError):
        SupplierConfig(follow_up_after_hours=0)


def test_vip_thresholds_positive():
    with pytest.raises(ValidationError):
        VipConfig(min_orders_for_vip=0)
    with pytest.raises(ValidationError):
        VipConfig(at_risk_silent_days=0)


def test_hiring_overdue_positive():
    with pytest.raises(ValidationError):
        HiringConfig(paperwork_overdue_days=0)


def test_catering_followup_delays_positive():
    with pytest.raises(ValidationError):
        CateringFollowupConfig(thank_you_delay_hours=0)
    with pytest.raises(ValidationError):
        CateringFollowupConfig(anniversary_nudge_days_before=0)


def test_config_backward_compat_no_tier2_blocks():
    """Old config (no Tier 2 blocks at all) loads with all defaults applied."""
    old = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "l", "timezone": "America/New_York"},
        "owner": {"name": "O", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
    }
    c = Config.model_validate(old)
    # All Tier 2 disabled, defaults populated
    assert c.inventory.enabled is False
    assert c.supplier.enabled is False
    assert c.vip.enabled is False
    assert c.catering_followup.enabled is False
    assert c.hiring.enabled is False
    assert c.compliance.enabled is False
    assert c.employee_docs.enabled is False
    assert c.cash_ar.enabled is False
    assert c.sales_tax.enabled is False


def test_config_partial_tier2_overrides():
    """Owner can opt-in selectively without touching other blocks."""
    cfg_dict = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "l", "timezone": "America/New_York"},
        "owner": {"name": "O", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "compliance": {"enabled": True, "advance_warning_days": [60, 30, 7, 1]},
        "employee_docs": {"enabled": True},
    }
    c = Config.model_validate(cfg_dict)
    assert c.compliance.enabled is True
    assert c.compliance.advance_warning_days == [60, 30, 7, 1]
    assert c.employee_docs.enabled is True
    assert c.cash_ar.enabled is False  # untouched, still default
