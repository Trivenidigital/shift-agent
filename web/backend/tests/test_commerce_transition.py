"""Slice-C Commerce transition route tests (web/backend/app/routers/commerce.py).

Functional transitions call the async handler directly (bypassing the auth
Depends, like the Slice-B read tests); the auth gate itself is exercised via
TestClient. Orders are seeded through the commerce primitives so the store is
always a valid CommerceOrderStore.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

pytest.importorskip("fastapi")


class _Req:
    """Minimal stand-in for a Starlette Request — enough for client_ip/ua."""
    def __init__(self) -> None:
        self.client = type("C", (), {"host": "127.0.0.1"})()
        self.headers = {"user-agent": "pytest"}


def _set_paths(tmp_path: Path):
    from app import audit as audit_mod
    from app.routers import commerce
    s = commerce.get_settings()
    s.state_dir = tmp_path / "state"
    s.decisions_path = tmp_path / "logs" / "decisions.log"
    s.config_path = tmp_path / "config.yaml"
    s.cockpit_audit_log = tmp_path / "logs" / "cockpit-audit.log"
    # audit.py captured its own settings ref at import; point it at the tmp log.
    audit_mod.settings.cockpit_audit_log = s.cockpit_audit_log
    return commerce, s


def _seed(commerce, s, *, status: str) -> str:
    """Create an order via the primitives and advance it to `status`."""
    from commerce import cart as commerce_cart
    from commerce import order_state
    cart = commerce_cart.add_item(
        state_path=s.state_dir / "commerce" / "carts.json",
        decisions_log_path=s.decisions_path,
        sender_phone="+15551234567", sender_lid=None, chat_id="chat",
        sku="A", display_name="Samosa", quantity=2, unit="each", unit_price_cents=300,
    ).cart
    oid = order_state.create(
        state_path=commerce._orders_path(), decisions_log_path=s.decisions_path, cart=cart,
    ).order.order_id
    # walk forward to the requested seed status
    path = ["pending_payment", "paid", "preparing", "ready", "out_for_delivery", "completed"]
    if status in path:
        for nxt in path[1: path.index(status) + 1]:
            actor = "webhook" if nxt == "paid" else "operator"
            order_state.transition(
                state_path=commerce._orders_path(), decisions_log_path=s.decisions_path,
                order_id=oid, to_status=nxt, actor=actor, cause="seed",
            )
    return oid


def _audit_types(s) -> list[str]:
    if not s.decisions_path.exists():
        return []
    return [json.loads(line)["type"]
            for line in s.decisions_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _post(commerce, order_id, **body):
    return asyncio.run(commerce.transition_order(
        order_id, commerce.OrderTransitionBody(**body), _Req(), _=None))


# ── allowed progress transitions ────────────────────────────────────────────

@pytest.mark.parametrize("seed_status,to_status", [
    ("paid", "preparing"),
    ("preparing", "ready"),
    ("ready", "out_for_delivery"),
    ("out_for_delivery", "completed"),
    ("ready", "completed"),
])
def test_allowed_progress_transition_succeeds_and_audits(tmp_path, seed_status, to_status):
    commerce, s = _set_paths(tmp_path)
    oid = _seed(commerce, s, status=seed_status)
    res = _post(commerce, oid, to_status=to_status, expected_from_status=seed_status, cause="")
    assert res["order"]["status"] == to_status
    assert "commerce_order_status_change" in _audit_types(s)


def test_pre_payment_cancel_succeeds_and_requires_reason(tmp_path):
    commerce, s = _set_paths(tmp_path)
    oid = _seed(commerce, s, status="pending_payment")
    # missing reason → 422
    with pytest.raises(HTTPException) as ei:
        _post(commerce, oid, to_status="cancelled", expected_from_status="pending_payment", cause="  ")
    assert ei.value.status_code == 422
    # with reason → cancelled + commerce_order_cancelled audit
    res = _post(commerce, oid, to_status="cancelled",
                expected_from_status="pending_payment", cause="customer changed mind")
    assert res["order"]["status"] == "cancelled"
    assert "commerce_order_cancelled" in _audit_types(s)


# ── deferred / out-of-scope transitions are refused ─────────────────────────

@pytest.mark.parametrize("seed_status,to_status,expected_from", [
    ("paid", "refunded", "paid"),            # money/provider — deferred
    ("pending_payment", "paid", "pending_payment"),  # manual mark-paid — deferred
    ("preparing", "cancelled", "preparing"),  # post-payment cancel — excluded
])
def test_out_of_slice_transition_refused_409_and_audited(tmp_path, seed_status, to_status, expected_from):
    commerce, s = _set_paths(tmp_path)
    oid = _seed(commerce, s, status=seed_status)
    with pytest.raises(HTTPException) as ei:
        _post(commerce, oid, to_status=to_status, expected_from_status=expected_from, cause="x")
    assert ei.value.status_code == 409
    refused = [json.loads(line) for line in s.decisions_path.read_text().splitlines()
               if "commerce_order_action_refused" in line]
    assert refused and refused[-1]["reason"] == "not_allowed_in_slice_c"


def test_stale_expected_status_refused_409(tmp_path):
    commerce, s = _set_paths(tmp_path)
    oid = _seed(commerce, s, status="paid")
    before = commerce._orders_path().read_bytes()
    with pytest.raises(HTTPException) as ei:
        # allowlisted pair, but the order is actually `paid`, not `preparing`
        _post(commerce, oid, to_status="ready", expected_from_status="preparing", cause="")
    assert ei.value.status_code == 409
    assert commerce._orders_path().read_bytes() == before  # no write
    refused = [json.loads(line) for line in s.decisions_path.read_text().splitlines()
               if "commerce_order_action_refused" in line]
    assert refused[-1]["reason"] == "stale_expected_status"


def test_illegal_transition_maps_to_409_refused(tmp_path, monkeypatch):
    """Defense-in-depth branch: if the primitive raises IllegalCommerceTransition
    for an otherwise-allowlisted, status-matching request, the route returns 409
    and audits reason='illegal_transition'. Forced by emptying LEGAL_TRANSITIONS
    (unreachable in normal operation — the Slice-C allowlist is a subset of it)."""
    commerce, s = _set_paths(tmp_path)
    oid = _seed(commerce, s, status="paid")
    from commerce import order_state
    monkeypatch.setattr(order_state, "LEGAL_TRANSITIONS", frozenset())
    with pytest.raises(HTTPException) as ei:
        _post(commerce, oid, to_status="preparing", expected_from_status="paid", cause="")
    assert ei.value.status_code == 409
    refused = [json.loads(line) for line in s.decisions_path.read_text().splitlines()
               if "commerce_order_action_refused" in line]
    assert refused[-1]["reason"] == "illegal_transition"


def test_unknown_order_404_and_audited(tmp_path):
    commerce, s = _set_paths(tmp_path)
    _seed(commerce, s, status="paid")  # some other order exists
    with pytest.raises(HTTPException) as ei:
        _post(commerce, "CO99999", to_status="preparing", expected_from_status="paid", cause="")
    assert ei.value.status_code == 404
    refused = [json.loads(line) for line in s.decisions_path.read_text().splitlines()
               if "commerce_order_action_refused" in line]
    assert refused[-1]["reason"] == "order_not_found"


def test_extra_field_in_body_rejected(tmp_path):
    commerce, _ = _set_paths(tmp_path)
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        commerce.OrderTransitionBody(
            to_status="preparing", expected_from_status="paid", cause="", surprise="x")


# ── auth gate (TestClient) ──────────────────────────────────────────────────

def test_transition_requires_fresh_otp(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    from fastapi.testclient import TestClient
    from jose import jwt
    from app import auth as auth_mod
    from app.main import app

    commerce, s = _set_paths(tmp_path)
    oid = _seed(commerce, s, status="paid")
    body = {"to_status": "preparing", "expected_from_status": "paid", "cause": ""}

    with TestClient(app) as client:
        unauth = client.post(f"/commerce/orders/{oid}/transition", json=body)
        assert unauth.status_code == 401

        stale_claims = {"sub": "+19045550100", "iat": 1_700_000_000,
                        "exp": 1_800_000_000, "jti": "stale", "auth_method": "pushover"}
        token = jwt.encode(stale_claims, auth_mod.settings.jwt_secret,
                           algorithm=auth_mod.settings.jwt_algo)
        client.cookies.set(auth_mod.settings.cookie_name, token)
        monkeypatch.setattr(auth_mod, "_now", lambda: 1_700_000_400)
        stale = client.post(f"/commerce/orders/{oid}/transition", json=body)
        assert stale.status_code == 403
