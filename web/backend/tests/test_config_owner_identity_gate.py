"""The owner-identity config mutation gate.

`identify-sender._match_owner_identity` grants owner capability on a match
against `owner.phone`, `owner.lid`, OR any `owner.authorized_identities`
entry. Writing any of them therefore decides WHO holds owner authority.

Before 2026-09-01 only `owner.phone` was in `sensitive_config_fields`, so a
plain authenticated session could append an authorized identity -- gaining
owner authority outright -- while merely changing `owner.phone` demanded a
Pushover step-up. This pins the corrected gate.

Two things these tests deliberately assert beyond the status code:

* the config file on disk is BYTE-UNCHANGED after a rejected patch, proving
  the refusal happens before persistence rather than after a partial write;
* the 403 body names the specific sensitive path, so a 403 raised for an
  unrelated reason (bad auth, validation) cannot be mistaken for the gate
  firing. A bare `status_code != 200` would pass on a 500.
"""
from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("fastapi")

OWNER_IDENTITY_FIELDS = [
    "owner.phone",
    "owner.lid",
    "owner.self_chat_jid",
    "owner.authorized_identities",
]


def _authed_client(monkeypatch, *, fresh: bool = True, method: str = "pushover"):
    """TestClient carrying an owner JWT. Mirrors test_catering_router."""
    from fastapi.testclient import TestClient
    from jose import jwt

    from app import auth as auth_mod
    from app.main import app

    issued_at = 1_700_000_000
    token = jwt.encode(
        {"sub": "+19045550100", "iat": issued_at, "exp": 1_800_000_000,
         "jti": "test", "auth_method": method},
        auth_mod.settings.jwt_secret,
        algorithm=auth_mod.settings.jwt_algo,
    )
    client = TestClient(app)
    client.cookies.set(auth_mod.settings.cookie_name, token)
    monkeypatch.setattr(auth_mod, "_now", lambda: issued_at + (60 if fresh else 400))
    return client


def _config_sha(settings) -> str:
    return hashlib.sha256(settings.config_path.read_bytes()).hexdigest()


@pytest.fixture
def env(monkeypatch):
    """Settings pointed at a temp config.yaml holding a realistic owner block."""
    import yaml

    from app import config as cfg_mod
    cfg_mod.get_settings.cache_clear()  # type: ignore[attr-defined]
    settings = cfg_mod.get_settings()
    doc = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc_jax_01",
                     "timezone": "America/New_York"},
        "owner": {
            "name": "Owner",
            "phone": "+918522041562",
            "self_chat_jid": "918522041562@s.whatsapp.net",
            "lid": "211390371475536@lid",
            "authorized_identities": [{"phone": "+17329837841"}],
        },
        "limits": {"max_outbound_per_day": 100},
        "alerting": {"pushover_user_key": "k" * 30, "pushover_app_token": "t" * 30},
        "backup": {"gpg_recipient_email": "o@example.com"},
    }
    settings.config_path.parent.mkdir(parents=True, exist_ok=True)
    settings.config_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return settings


# ── the gate fires, and nothing is persisted ─────────────────────────────────

@pytest.mark.parametrize("field", OWNER_IDENTITY_FIELDS)
def test_owner_identity_field_is_rejected_without_step_up(env, monkeypatch, field):
    client = _authed_client(monkeypatch)
    before = _config_sha(env)

    value = [{"phone": "+15125550199"}] if field.endswith("identities") else "x"
    r = client.patch("/config", json={"fields": {field: value}})

    assert r.status_code == 403, r.text
    # WITNESS: the gate named THIS field, so an unrelated 403 cannot pass.
    assert field in r.text, r.text
    # WITNESS: refused BEFORE persistence.
    assert _config_sha(env) == before, "config was written despite the refusal"


def test_ordinary_field_still_patches(env, monkeypatch):
    """The control. Without it, a gate that rejected everything would pass."""
    client = _authed_client(monkeypatch)
    before = _config_sha(env)

    r = client.patch("/config", json={"fields": {"customer.name": "Triveni Foods"}})

    assert r.status_code == 200, r.text
    assert _config_sha(env) != before, "an accepted patch did not persist"


def test_a_non_identity_field_inside_owner_still_patches(env, monkeypatch):
    """The sharper control: the gate must not blanket-block the owner block.

    `owner.name` is display text, not an identifier `_match_owner_identity`
    ever consults. If this 403s, the ancestor check has become "anything
    under owner", which would over-block and make the bypass tests pass for
    the wrong reason.
    """
    client = _authed_client(monkeypatch)
    before = _config_sha(env)

    r = client.patch("/config", json={"fields": {"owner.name": "New Display Name"}})

    assert r.status_code == 200, r.text
    assert _config_sha(env) != before


def test_ancestor_key_cannot_bypass_the_gate(env, monkeypatch):
    """`_set_dotted` writes at whatever depth is named.

    A key of `"owner"` replaces the whole owner block -- phone, lid and
    authorized_identities together -- while the original
    `sensitive_config_fields & keys()` intersection saw no match, because
    `"owner"` is not the string `"owner.phone"`. That bypassed the step-up for
    `owner.phone` too, and predates the identity fields added with it.
    """
    client = _authed_client(monkeypatch)
    before = _config_sha(env)

    r = client.patch("/config", json={"fields": {"owner": {
        "name": "Attacker", "phone": "+15125550199",
        "authorized_identities": [{"phone": "+15125550199"}]}}})

    assert r.status_code == 403, r.text
    assert _config_sha(env) == before, "whole-owner overwrite persisted"


def test_descendant_key_cannot_bypass_the_gate(env, monkeypatch):
    """The mirror: addressing a leaf INSIDE a sensitive subtree."""
    client = _authed_client(monkeypatch)
    before = _config_sha(env)

    r = client.patch(
        "/config",
        json={"fields": {"owner.authorized_identities.0.phone": "+15125550199"}},
    )

    assert r.status_code == 403, r.text
    assert _config_sha(env) == before


def test_segment_boundaries_are_respected(env, monkeypatch):
    """`owner_backup` must NOT collide with `owner`.

    Proves the ancestor check matches dotted SEGMENTS rather than doing a
    sloppy string prefix, which would over-block unrelated keys and make the
    two bypass tests above pass for the wrong reason.
    """
    from app.routers.config import _sensitive_touched

    assert _sensitive_touched(["owner_backup.phone"]) == []
    assert _sensitive_touched(["ownership"]) == []
    assert _sensitive_touched(["customer.name"]) == []
    # display text under `owner` is NOT an identifier -- must stay reachable
    assert _sensitive_touched(["owner.name"]) == []
    # ...while an ancestor key that would rewrite the identifiers IS caught
    assert _sensitive_touched(["owner"]) != []
    assert _sensitive_touched(["owner.lid"]) == ["owner.lid"]
    # `limits.max_outbound_per_day` was already sensitive before this change;
    # asserting it is NOT would have been asserting a regression.
    assert _sensitive_touched(["limits.max_outbound_per_day"]) == [
        "limits.max_outbound_per_day"
    ]


# ── the step-up path still works ─────────────────────────────────────────────

@pytest.mark.parametrize("field", OWNER_IDENTITY_FIELDS)
def test_sensitive_endpoint_accepts_with_fresh_pushover_otp(env, monkeypatch, field):
    """Gating must not make these unreachable -- only harder to reach.

    Uses values that keep the config schema-valid, so a rejection here would
    mean the GATE refused, not that the document failed validation.
    """
    client = _authed_client(monkeypatch, fresh=True, method="pushover")
    valid = {
        "owner.phone": "+918522041562",
        "owner.lid": "211390371475536@lid",
        "owner.self_chat_jid": "918522041562@s.whatsapp.net",
        "owner.authorized_identities": [{"phone": "+17329837841"}],
    }[field]

    r = client.patch("/config/sensitive", json={"fields": {field: valid}})

    assert r.status_code == 200, r.text


@pytest.mark.parametrize("field", OWNER_IDENTITY_FIELDS)
def test_sensitive_endpoint_rejects_totp_only_login(env, monkeypatch, field):
    """Self-recovery prevention: a TOTP-only compromise must not reach these."""
    client = _authed_client(monkeypatch, fresh=True, method="totp")
    before = _config_sha(env)

    r = client.patch("/config/sensitive", json={"fields": {field: "x"}})

    assert r.status_code == 403, r.text
    assert _config_sha(env) == before


@pytest.mark.parametrize("field", OWNER_IDENTITY_FIELDS)
def test_sensitive_endpoint_rejects_stale_otp(env, monkeypatch, field):
    client = _authed_client(monkeypatch, fresh=False, method="pushover")
    before = _config_sha(env)

    r = client.patch("/config/sensitive", json={"fields": {field: "x"}})

    assert r.status_code == 403, r.text
    assert _config_sha(env) == before
