"""Tests for the commerce webhook-subscription deploy gate.

In-process (cross-platform): the gate logic module imports only stdlib + PyYAML
+ Pydantic `CommerceConfig`, none of which need fcntl, so these tests run on
Windows too. The `hermes webhook list` call is injected via `list_runner` so no
real Hermes CLI is required.

Mirrors the deploy-gate intent in
tasks/commerce-slice3.5-webhook-subscription-gate-design.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

SRC_PLATFORM = Path(__file__).resolve().parent.parent / "src" / "platform"
if str(SRC_PLATFORM) not in sys.path:
    sys.path.insert(0, str(SRC_PLATFORM))

import commerce_webhook_gate as gate  # noqa: E402

SUB = "stripe-commerce-payments"
PRESENT_OUTPUT = f"Dynamic subscriptions:\n  - {SUB}  route=/webhooks/stripe  active\n"
NOT_ENABLED_OUTPUT = (
    "\n  Webhook platform is not enabled. To set it up:\n"
    "  1. Run the gateway setup wizard:\n     hermes gateway setup\n"
)


def _no_call(_bin):
    raise AssertionError("hermes webhook list must NOT be called on the skip path")


def _write_config(tmp_path, commerce):
    doc = {"customer": {"timezone": "America/New_York"}}
    if commerce is not None:
        doc["commerce"] = commerce
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(p)


def test_dormant_no_commerce_section_skips(tmp_path):
    cfg = _write_config(tmp_path, None)
    code, out, err = gate.run(cfg, "hermes", list_runner=_no_call)
    assert code == 0
    assert "not applicable" in out
    assert err == ""


def test_enabled_but_placeholder_provider_skips(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "placeholder"})
    code, out, err = gate.run(cfg, "hermes", list_runner=_no_call)
    assert code == 0
    assert "not applicable" in out


def test_stripe_but_disabled_skips(tmp_path):
    # provider=stripe but enabled=False is still dormant (no traffic flows).
    cfg = _write_config(tmp_path, {"enabled": False, "provider": "stripe"})
    code, out, err = gate.run(cfg, "hermes", list_runner=_no_call)
    assert code == 0
    assert "not applicable" in out


def test_active_stripe_subscription_present_passes(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    code, out, err = gate.run(cfg, "hermes", list_runner=lambda b: PRESENT_OUTPUT)
    assert code == 0
    assert SUB in out
    assert err == ""


def test_active_stripe_subscription_absent_fails_closed(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    code, out, err = gate.run(cfg, "hermes", list_runner=lambda b: "  - other-hook  active\n")
    assert code == 1
    assert out == ""
    # Error names the exact subscription + the command shape, without secrets.
    assert SUB in err
    assert f"hermes webhook subscribe {SUB}" in err
    assert "secret" in err.lower()  # reminds not to paste secrets


def test_active_stripe_platform_not_enabled_fails_closed(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    code, out, err = gate.run(cfg, "hermes", list_runner=lambda b: NOT_ENABLED_OUTPUT)
    assert code == 1
    assert SUB in err


def test_custom_subscription_name_respected(tmp_path):
    cfg = _write_config(
        tmp_path,
        {"enabled": True, "provider": "stripe", "webhook_subscription_name": "my-custom-hook"},
    )
    code, out, err = gate.run(cfg, "hermes", list_runner=lambda b: "  - my-custom-hook  active\n")
    assert code == 0
    assert "my-custom-hook" in out


def test_missing_config_exit_2(tmp_path):
    code, out, err = gate.run(str(tmp_path / "nope.yaml"), "hermes", list_runner=lambda b: PRESENT_OUTPUT)
    assert code == 2
    assert "ERROR" in err


def test_malformed_commerce_section_exit_2(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("commerce: [not, a, mapping]\n", encoding="utf-8")
    code, out, err = gate.run(str(p), "hermes", list_runner=lambda b: PRESENT_OUTPUT)
    assert code == 2


def test_invalid_provider_value_exit_2(tmp_path):
    # provider outside the Literal set -> pydantic ValidationError -> config error.
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "bogus-provider"})
    code, out, err = gate.run(cfg, "hermes", list_runner=lambda b: PRESENT_OUTPUT)
    assert code == 2


def test_hermes_bin_missing_exit_2(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})

    def _raise(_bin):
        raise FileNotFoundError("no such file: hermes")

    code, out, err = gate.run(cfg, "hermes", list_runner=_raise)
    assert code == 2
    assert "ERROR" in err
