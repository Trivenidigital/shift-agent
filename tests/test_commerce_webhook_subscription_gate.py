"""Tests for the commerce webhook-subscription deploy gate.

In-process (cross-platform): the gate logic module imports only stdlib + PyYAML
+ Pydantic `CommerceConfig`, none of which need fcntl, so these tests run on
Windows too. The `hermes webhook list` call is injected via `list_runner`
(returns ``(returncode, combined_output)``) so no real Hermes CLI is required.

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


def _ok(output):
    """Build a list_runner that returns exit 0 + the given output."""
    return lambda _bin: (0, output)


def _write_config(tmp_path, commerce):
    doc = {"customer": {"timezone": "America/New_York"}}
    if commerce is not None:
        doc["commerce"] = commerce
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(p)


# ── dormant-safe skip paths ────────────────────────────────────────────────

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
    cfg = _write_config(tmp_path, {"enabled": False, "provider": "stripe"})
    code, out, err = gate.run(cfg, "hermes", list_runner=_no_call)
    assert code == 0
    assert "not applicable" in out


# ── active Stripe: pass / fail-closed ──────────────────────────────────────

def test_active_stripe_subscription_present_passes(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    code, out, err = gate.run(cfg, "hermes", list_runner=_ok(PRESENT_OUTPUT))
    assert code == 0
    assert SUB in out
    assert err == ""


def test_active_stripe_subscription_absent_fails_closed(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    code, out, err = gate.run(cfg, "hermes", list_runner=_ok("  - other-hook  active\n"))
    assert code == 1
    assert out == ""
    assert SUB in err
    assert f"hermes webhook subscribe {SUB}" in err
    assert "secret" in err.lower()


def test_active_stripe_platform_not_enabled_fails_closed(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    code, out, err = gate.run(cfg, "hermes", list_runner=_ok(NOT_ENABLED_OUTPUT))
    assert code == 1
    assert SUB in err


def test_custom_subscription_name_respected(tmp_path):
    cfg = _write_config(
        tmp_path,
        {"enabled": True, "provider": "stripe", "webhook_subscription_name": "my-custom-hook"},
    )
    code, out, err = gate.run(cfg, "hermes", list_runner=_ok("  - my-custom-hook  active\n"))
    assert code == 0
    assert "my-custom-hook" in out


# ── BLOCKER-1 regression: precise token match, no substring false positives ──

def test_longer_name_superstring_does_not_falsely_pass(tmp_path):
    # A DIFFERENT subscription whose name merely contains ours as a prefix must
    # NOT be treated as our subscription — money-safety fail-closed.
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    out_text = f"Dynamic subscriptions:\n  - {SUB}-v2  route=/x  active\n"
    code, out, err = gate.run(cfg, "hermes", list_runner=_ok(out_text))
    assert code == 1
    assert SUB in err


def test_name_appearing_only_in_freeform_text_does_not_pass(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    # The token appears glued inside an unrelated word — not a standalone record.
    out_text = "note: legacystripe-commerce-paymentsXYZ was removed\n"
    code, out, err = gate.run(cfg, "hermes", list_runner=_ok(out_text))
    assert code == 1


def test_subscription_with_trailing_punctuation_passes(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    out_text = f"  - {SUB}: route=/webhooks/stripe\n"
    code, out, err = gate.run(cfg, "hermes", list_runner=_ok(out_text))
    assert code == 0


def test_name_as_value_after_key_passes(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    out_text = f"  name={SUB}  status=active\n"
    code, out, err = gate.run(cfg, "hermes", list_runner=_ok(out_text))
    assert code == 0


# ── BLOCKER-2 regression: non-zero return code is a gate/runtime error ───────

def test_nonzero_returncode_is_config_error_even_if_token_present(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    # Command failed (rc=3) but happened to print the token — must NOT pass.
    code, out, err = gate.run(cfg, "hermes", list_runner=lambda b: (3, PRESENT_OUTPUT))
    assert code == 2
    assert "3" in err
    assert out == ""


def test_nonzero_returncode_without_token_is_config_error_not_missing(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    code, out, err = gate.run(cfg, "hermes", list_runner=lambda b: (1, "boom: backend error\n"))
    # Runtime/gate error (exit 2), NOT "subscription missing" (exit 1).
    assert code == 2


# ── config / runtime error paths ────────────────────────────────────────────

def test_missing_config_exit_2(tmp_path):
    code, out, err = gate.run(str(tmp_path / "nope.yaml"), "hermes", list_runner=_ok(PRESENT_OUTPUT))
    assert code == 2
    assert "ERROR" in err


def test_malformed_commerce_section_exit_2(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("commerce: [not, a, mapping]\n", encoding="utf-8")
    code, out, err = gate.run(str(p), "hermes", list_runner=_ok(PRESENT_OUTPUT))
    assert code == 2


def test_invalid_provider_value_exit_2(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "bogus-provider"})
    code, out, err = gate.run(cfg, "hermes", list_runner=_ok(PRESENT_OUTPUT))
    assert code == 2


def test_hermes_bin_missing_exit_2(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})

    def _raise(_bin):
        raise FileNotFoundError("no such file: hermes")

    code, out, err = gate.run(cfg, "hermes", list_runner=_raise)
    assert code == 2
    assert "ERROR" in err
