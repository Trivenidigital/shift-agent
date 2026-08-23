"""Rehearsal credential sterility — a copied-state rehearsal MUST be unable to
produce an externally authenticated side effect (P1-A).

Motivating incident (2026-08-2x): a copied-state rehearsal of a menu mutation
copied the REAL production ``config.yaml`` — live Pushover ``pushover_user_key``
and ``pushover_app_token`` included — into a ``--network host`` docker container
running real production scripts. Nothing fired, but only because the branch that
calls ``notify_owner_with_fallback`` happened not to execute; the same scripts
DID page the owner for real minutes later in production. Copied state is not
safe merely because the files are copies, if the copied credentials still
authorise real external actions.

The invariant under test:

    A copied-state / local rehearsal must be UNABLE to produce an externally
    authenticated side effect, even when production config or state was used as
    its source.

Six tests, one per required protection layer, plus a network-level negative
control. Each names the ALTERNATE mechanism that could produce the same green
and rules it out — a "no credential survived" pass is worthless if the sanitiser
never ran, and a "no external call happened" pass is worthless if nothing ran at
all (this project has been bitten by both).

Synthetic credential values below are shaped like the real thing (30-char
alphanumeric Pushover tokens, ``sk-or-v1-`` OpenRouter keys, ``sk_live_`` Stripe
keys) but spell out that they are fake and cannot authenticate.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent
_PLATFORM_DIR = _REPO_ROOT / "src" / "platform"
if str(_PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_DIR))

import safe_io  # noqa: E402,F401  (import for sys.path side effect; see _sio)
from conftest import build_rehearsal_sandbox  # noqa: E402


def _sio():
    """Return the LIVE ``safe_io`` module object.

    Several suites in this repo call ``importlib.reload(safe_io)`` or
    ``sys.modules.pop("safe_io")`` (test_bridge_send_harness,
    test_audit_prod_isolation_guard, test_front_brain_outbound_enforcement, …).
    A module-level ``import safe_io`` binding then goes STALE: its
    ``RehearsalCredentialLeak`` is a different class object from the one the
    reloaded module raises, so ``pytest.raises`` stops matching and the test
    fails only when run after one of those suites. Resolving at call time is
    the fix; the repo's own routing-invariants suite documents the same hazard.
    """
    import importlib
    return importlib.import_module("safe_io")

NOTIFY_OWNER_SCRIPT = (
    _REPO_ROOT / "src" / "agents" / "shift" / "scripts" / "shift-agent-notify-owner"
)

# ── synthetic, production-SHAPED credentials ────────────────────────────────
# Pushover app tokens/user keys are exactly 30 alphanumeric chars. These match
# that shape (so the shape scanner has something real to catch) while spelling
# out that they are fake.
FAKE_PUSHOVER_APP_TOKEN = "aFAKEtokenDOnotUSE000000000000"   # 30 alnum
FAKE_PUSHOVER_USER_KEY = "uFAKEuserkeyDOnotUSE0000000000"    # 30 alnum
# Assembled from fragments, and kept SHORT after the provider prefix, so the
# literal never appears in the file. GitHub push protection rejected an earlier
# revision whose Stripe fixture was long enough to match the real `sk_live_`
# format — a secret scanner cannot tell "obviously fake" from "real", and it is
# right not to try. The prefix is what this suite's shape scanner keys on
# (`^(sk|rk|pk)_(live|test)_[A-Za-z0-9]`), so a short tail loses no coverage.
FAKE_OPENROUTER_KEY = "sk-or-" + "v1-" + "FAKEdoNOTuse"
FAKE_OPENAI_KEY = "sk-" + "proj-" + "FAKEdoNOTuse"
FAKE_STRIPE_KEY = "sk_" + "live_" + "FAKEdoNOTuse"
FAKE_HEALTHCHECKS_URL = "https://hc-ping.com/00000000-fake-do-not-use-000000000000"

assert len(FAKE_PUSHOVER_APP_TOKEN) == 30
assert len(FAKE_PUSHOVER_USER_KEY) == 30


def production_shaped_config() -> dict:
    """A config.yaml body shaped exactly like a real on-box one, carrying
    live-LOOKING credentials in every credential-bearing key."""
    return {
        "schema_version": 1,
        "customer": {
            "name": "Triveni Supermarket",
            "location_id": "loc_jax_01",
            "timezone": "America/New_York",
            "languages": ["en"],
        },
        "owner": {
            "name": "Real Owner",
            "phone": "+19045550999",
            "self_chat_jid": "19045550999@s.whatsapp.net",
        },
        "limits": {},
        "alerting": {
            "pushover_user_key": FAKE_PUSHOVER_USER_KEY,
            "pushover_app_token": FAKE_PUSHOVER_APP_TOKEN,
            "healthchecks_io_url": FAKE_HEALTHCHECKS_URL,
            "email": "owner@example.com",
        },
        "backup": {
            "gpg_recipient_email": "owner@example.com",
            "gpg_fingerprint": "A" * 40,
            "s3_bucket": "triveni-prod-backups",
            "retention_days": 30,
        },
        "operations": {"business_hours_local": "08:00-22:00"},
        "commerce": {
            "enabled": True,
            "provider": "stripe",
            "stripe_livemode_expected": True,
            "payment_checkout_url_template": "https://buy.stripe.com/live_FAKE_DO_NOT_USE",
        },
        "catering": {"enabled": True},
    }


def production_shaped_env() -> dict:
    """The ambient env a rehearsal would inherit from an operator shell that had
    sourced the box's .env."""
    return {
        "OPENROUTER_API_KEY": FAKE_OPENROUTER_KEY,
        "OPENAI_API_KEY": FAKE_OPENAI_KEY,
        "STRIPE_API_KEY": FAKE_STRIPE_KEY,
        "PUSHOVER_APP_TOKEN": FAKE_PUSHOVER_APP_TOKEN,
        "PUSHOVER_USER_KEY": FAKE_PUSHOVER_USER_KEY,
        "HERMES_BRIDGE_URL": "http://127.0.0.1:3000/send",
    }


@pytest.fixture
def prod_env(monkeypatch):
    """Poison the ambient environment with live-LOOKING credentials, exactly as
    an operator shell on/near the box would carry them."""
    for k, v in production_shaped_env().items():
        monkeypatch.setenv(k, v)
    return production_shaped_env()


# ═══════════════════════════════════════════════════════════════════════════
# 1. Pushover credentials cannot reach the subprocess unchanged
# ═══════════════════════════════════════════════════════════════════════════

def test_1_pushover_credentials_cannot_reach_the_subprocess(tmp_path, prod_env):
    """A production-shaped config containing live-LOOKING Pushover credentials
    cannot reach a rehearsal subprocess unchanged.

    Asserted on the BYTES the subprocess actually reads, not on the in-memory
    dict — the config is re-read from disk by the script under rehearsal, so
    disk is the only surface that matters.

    ALTERNATE mechanism that could fake this green: the sandbox wrote no config
    at all (nothing to leak because nothing exists). Ruled out by asserting the
    config file exists, parses, and still carries the non-credential production
    fields (customer name, timezone) — i.e. the copy happened and only the
    credentials were replaced.
    """
    sandbox = build_rehearsal_sandbox(tmp_path, source_config=production_shaped_config())

    raw = sandbox.config_path.read_text(encoding="utf-8")
    assert FAKE_PUSHOVER_APP_TOKEN not in raw
    assert FAKE_PUSHOVER_USER_KEY not in raw
    assert FAKE_HEALTHCHECKS_URL not in raw

    parsed = yaml.safe_load(raw)
    # the copy really happened — non-credential production fields survived
    assert parsed["customer"]["name"] == "Triveni Supermarket"
    assert parsed["customer"]["timezone"] == "America/New_York"
    # and the credential slots hold sterile sentinels, not "" (a blank would
    # fail AlertingConfig.require_pushover and mask the sterility as a crash)
    assert _sio().REHEARSAL_STERILE_PREFIX in parsed["alerting"]["pushover_app_token"]
    assert _sio().REHEARSAL_STERILE_PREFIX in parsed["alerting"]["pushover_user_key"]
    assert parsed["alerting"]["healthchecks_io_url"] == ""

    # the sanitised config still validates as a real Config (a rehearsal that
    # cannot load its config proves nothing)
    sys.path.insert(0, str(_PLATFORM_DIR))
    from schemas import Config
    Config.model_validate(parsed)

    # and the subprocess env carries no Pushover credential either
    assert FAKE_PUSHOVER_APP_TOKEN not in json.dumps(sandbox.env)
    assert FAKE_PUSHOVER_USER_KEY not in json.dumps(sandbox.env)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Model-provider credentials removed or unusable
# ═══════════════════════════════════════════════════════════════════════════

def test_2_model_provider_credentials_are_removed(tmp_path, prod_env):
    """OpenRouter / OpenAI / Stripe credentials are removed from the rehearsal
    environment AND from the .env FILES every resolver falls back to.

    Env-scrubbing alone is not enough: openrouter_env.openrouter_key() and
    render._read_env_value() fall back to /root/.hermes/.env and
    /opt/shift-agent/.env when the env var is empty. A rehearsal that scrubs
    only os.environ still authenticates from disk.

    ALTERNATE mechanism: the env dict is simply empty (nothing to leak). Ruled
    out by asserting the sandbox env still carries ordinary inherited variables
    and the rehearsal marker.
    """
    sandbox = build_rehearsal_sandbox(tmp_path, source_config=production_shaped_config())

    blob = json.dumps(sandbox.env)
    for secret in (FAKE_OPENROUTER_KEY, FAKE_OPENAI_KEY, FAKE_STRIPE_KEY):
        assert secret not in blob, f"{secret[:12]}... survived into the rehearsal env"
    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "STRIPE_API_KEY",
                 "PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY"):
        assert sandbox.env.get(name, "") == "", f"{name} not neutralised"

    # env is real, not empty
    assert sandbox.env["SHIFT_AGENT_REHEARSAL"] == "1"
    assert "PATH" in sandbox.env

    # the .env FILE fallback is closed too: both resolver paths point at a
    # sterile file that exists and carries no live key.
    for var in ("HERMES_ENV_PATH", "SHIFT_AGENT_ENV_PATH"):
        p = Path(sandbox.env[var])
        assert p.is_file(), f"{var} must point at a real sterile .env file"
        text = p.read_text(encoding="utf-8")
        for secret in (FAKE_OPENROUTER_KEY, FAKE_OPENAI_KEY, FAKE_STRIPE_KEY):
            assert secret not in text

    # positive: the resolver that reads those files returns nothing usable
    sys.path.insert(0, str(_REPO_ROOT / "src" / "agents" / "flyer"))
    import openrouter_env
    resolved = openrouter_env.read_key_from_env_file(sandbox.env["SHIFT_AGENT_ENV_PATH"])
    assert not resolved or _sio().REHEARSAL_STERILE_PREFIX in resolved


# ═══════════════════════════════════════════════════════════════════════════
# 3. Bridge destination cannot resolve to the production transport
# ═══════════════════════════════════════════════════════════════════════════

def test_3_bridge_destination_cannot_be_the_production_transport(tmp_path, prod_env):
    """The rehearsal's bridge destination is a closed local sink, and even if
    something forced the live bridge back the send chokepoint refuses.

    ALTERNATE mechanism: HERMES_BRIDGE_URL is merely absent, so the code falls
    back to its hardcoded 127.0.0.1:3000 default — the live bridge — and the
    test passes only because it checked the env var rather than the resolved
    destination. Ruled out by asserting the value is present AND is not the live
    port, and by driving the resolved-URL guard directly.
    """
    sandbox = build_rehearsal_sandbox(tmp_path, source_config=production_shaped_config())

    url = sandbox.env["HERMES_BRIDGE_URL"]
    assert url, "HERMES_BRIDGE_URL must be SET, not merely absent"
    assert not _sio()._is_live_bridge_url(url), f"{url} resolves to the live bridge"

    # the chokepoint refuses a live-bridge send under the rehearsal marker even
    # outside pytest context and even with the pytest opt-out flag set
    env = dict(sandbox.env)
    env["SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS"] = "1"
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r)\n"
         "import safe_io\n"
         "print(safe_io.bridge_send_blocked_by_test_context('http://127.0.0.1:3000/send'))\n"
         % str(_PLATFORM_DIR)],
        capture_output=True, text=True, env={**env, "PYTEST_CURRENT_TEST": ""}, timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    assert "None" not in probe.stdout.strip().splitlines()[-1], (
        "live-bridge send was NOT blocked under the rehearsal marker: "
        f"{probe.stdout!r} {probe.stderr!r}"
    )

    # and the notify-owner script's WhatsApp fallback honours the same override
    # (it hardcoded port 3000 before this change)
    src = NOTIFY_OWNER_SCRIPT.read_text(encoding="utf-8")
    assert "HERMES_BRIDGE_URL" in src, (
        "shift-agent-notify-owner still hardcodes the bridge URL — a rehearsal "
        "cannot redirect its WhatsApp fallback away from the live bridge"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 4. FAIL-CLOSED: a surviving forbidden credential aborts before business code
# ═══════════════════════════════════════════════════════════════════════════

def test_4_surviving_credential_fails_closed_before_business_code(tmp_path, prod_env):
    """Deliberately leave one forbidden credential in place — the rehearsal must
    REFUSE to start, before any business code runs.

    This is the layer that makes the other five worth anything: without it,
    every guarantee rests on the sanitiser having been remembered.

    ALTERNATE mechanism: the sandbox raised for an unrelated reason (bad path,
    missing dir) and the test read it as a sterility refusal. Ruled out by
    asserting the exception TYPE is RehearsalCredentialLeak and that its message
    names the offending key; and by proving the canary — which business code
    would have written — does not exist.
    """
    canary = tmp_path / "business-code-ran.marker"

    leaky = production_shaped_config()

    # sanitise everything EXCEPT the app token — the single-door survivor
    def leave_app_token(cfg: dict) -> dict:
        out = _sio().sanitize_config_for_rehearsal(cfg)
        out["alerting"]["pushover_app_token"] = FAKE_PUSHOVER_APP_TOKEN
        return out

    with pytest.raises(_sio().RehearsalCredentialLeak) as exc:
        build_rehearsal_sandbox(
            tmp_path / "sbx", source_config=leaky,
            _sanitizer=leave_app_token,
            _on_ready=lambda sbx: canary.write_text("ran", encoding="utf-8"),
        )
    assert "alerting.pushover_app_token" in str(exc.value)
    assert not canary.exists(), (
        "business code ran despite a surviving credential — the guard is not "
        "fail-closed"
    )

    # the same check catches a credential at a door NOT in the registry: an
    # unknown key holding a production-SHAPED secret must also refuse. A guard
    # that only knew about Pushover would repeat the incident at another door.
    unknown_door = _sio().sanitize_config_for_rehearsal(production_shaped_config())
    unknown_door["some_future_agent"] = {"provider_api_key": FAKE_STRIPE_KEY}
    with pytest.raises(_sio().RehearsalCredentialLeak) as exc2:
        _sio().assert_rehearsal_config_sterile(unknown_door, source="unit")
    assert "some_future_agent.provider_api_key" in str(exc2.value)


# ═══════════════════════════════════════════════════════════════════════════
# 5. POSITIVE CONTROL: the safe local stub is genuinely reachable
# ═══════════════════════════════════════════════════════════════════════════

def test_5_positive_control_local_stub_is_reachable(tmp_path, prod_env):
    """Prove the rehearsal actually RUNS and actually reaches its local stub.

    Without this, "no external call happened" is indistinguishable from
    "nothing ran at all" — a false green this project has hit repeatedly (a
    probe that "passed" because the target was crash-looping).

    Two proofs in one test:
      (a) the sanitiser demonstrably executed — a spy sanitiser records its call
          AND the emitted config differs from the source in exactly the
          credential slots;
      (b) a real subprocess, launched with the sandbox env, POSTs to a local
          stub HTTP server and the stub records the request.
    """
    from _b1_helpers import BridgeStub  # local import: Linux-only helpers
    from http.server import HTTPServer
    import threading

    BridgeStub.requests = []
    server = HTTPServer(("127.0.0.1", 0), BridgeStub)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        calls: list = []

        def spy(cfg):
            calls.append(cfg)
            return _sio().sanitize_config_for_rehearsal(cfg)

        sandbox = build_rehearsal_sandbox(
            tmp_path,
            source_config=production_shaped_config(),
            bridge_url=f"http://127.0.0.1:{port}/send",
            _sanitizer=spy,
        )
        # (a) the sanitiser ran, and it changed exactly the credential slots
        assert len(calls) == 1, "sanitiser did not run — sterility is unproven"
        before = calls[0]
        after = yaml.safe_load(sandbox.config_path.read_text(encoding="utf-8"))
        assert before["alerting"] != after["alerting"]
        assert before["customer"] == after["customer"]

        # (b) the stub is genuinely reachable from a subprocess using this env
        probe = subprocess.run(
            [sys.executable, "-c",
             "import os, urllib.request, json\n"
             "req = urllib.request.Request(os.environ['HERMES_BRIDGE_URL'],\n"
             "    data=json.dumps({'chatId':'x','message':'positive-control'}).encode(),\n"
             "    headers={'Content-Type':'application/json'})\n"
             "print(urllib.request.urlopen(req, timeout=5).status)\n"],
            capture_output=True, text=True, env=sandbox.env, timeout=30,
        )
        assert probe.returncode == 0, f"stub unreachable: {probe.stderr}"
        assert "200" in probe.stdout
        assert BridgeStub.requests, "stub recorded nothing — nothing actually ran"
        assert BridgeStub.requests[-1]["message"] == "positive-control"
    finally:
        server.shutdown()
        server.server_close()


# ═══════════════════════════════════════════════════════════════════════════
# 6. Ordinary production execution is UNAFFECTED
# ═══════════════════════════════════════════════════════════════════════════

def test_6_production_execution_is_unaffected(tmp_path):
    """Outside a rehearsal, every guard is inert — the real scripts behave on
    the box exactly as before.

    ALTERNATE mechanism: the guards are inert because the rehearsal marker is
    ALWAYS unset, i.e. the guard can never fire at all. Ruled out by flipping
    the marker in the same subprocess and observing the behaviour change.
    """
    probe_src = (
        "import sys, os\n"
        "sys.path.insert(0, %r)\n"
        "import safe_io\n"
        "os.environ.pop('PYTEST_CURRENT_TEST', None)\n"
        "os.environ.pop('SHIFT_AGENT_REHEARSAL', None)\n"
        "print('OFF_bridge=%%r' %% (safe_io.bridge_send_blocked_by_test_context("
        "'http://127.0.0.1:3000/send'),))\n"
        "print('OFF_rehearsal=%%r' %% (safe_io.rehearsal_mode_active(),))\n"
        "print('OFF_notify=%%r' %% (safe_io.owner_alert_blocked_by_rehearsal('probe'),))\n"
        "os.environ['SHIFT_AGENT_REHEARSAL'] = '1'\n"
        "print('ON_bridge=%%r' %% (safe_io.bridge_send_blocked_by_test_context("
        "'http://127.0.0.1:3000/send'),))\n"
        "print('ON_notify=%%r' %% (safe_io.owner_alert_blocked_by_rehearsal('probe'),))\n"
        % str(_PLATFORM_DIR)
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTEST_CURRENT_TEST", "SHIFT_AGENT_REHEARSAL")}
    probe = subprocess.run([sys.executable, "-c", probe_src],
                           capture_output=True, text=True, env=env, timeout=30)
    assert probe.returncode == 0, probe.stderr
    out = probe.stdout

    # production (no marker): every guard inert, byte-identical to pre-change
    assert "OFF_bridge=None" in out, out
    assert "OFF_rehearsal=False" in out, out
    assert "OFF_notify=None" in out, out
    # rehearsal (marker on): the same guards fire — so "inert" above is a real
    # negative, not a guard that can never trigger
    assert "ON_bridge=None" not in out, out
    assert "ON_notify=None" not in out, out

    # and notify_owner_with_fallback does not invoke its subprocess under a
    # rehearsal (it is the exact call the incident's scripts contained)
    marker = tmp_path / "pushover-bin-was-invoked"
    fake_bin = tmp_path / "fake-notify-owner"
    fake_bin.write_text(
        "#!/bin/sh\ntouch %s\nexit 0\n" % marker, encoding="utf-8")
    fake_bin.chmod(0o755)

    os.environ["SHIFT_AGENT_REHEARSAL"] = "1"
    try:
        ok = _sio().notify_owner_with_fallback(
            "t", "m", source="rehearsal-test", notify_owner_bin=str(fake_bin),
            notify_failed_log=tmp_path / "nf.log",
            dedup_state_path=tmp_path / "dd.json",
        )
    finally:
        os.environ.pop("SHIFT_AGENT_REHEARSAL", None)
    assert ok is False
    assert not marker.exists(), "Pushover bin was invoked during a rehearsal"

    # control: with the marker OFF the very same call DOES invoke the bin —
    # proving the assertion above is about the guard, not about a broken bin.
    ok2 = _sio().notify_owner_with_fallback(
        "t", "m", source="rehearsal-test", notify_owner_bin=str(fake_bin),
        notify_failed_log=tmp_path / "nf.log",
        dedup_state_path=tmp_path / "dd2.json",
    )
    assert ok2 is True
    assert marker.exists(), "control failed — the fake bin never worked at all"


# ═══════════════════════════════════════════════════════════════════════════
# Network-level negative control
# ═══════════════════════════════════════════════════════════════════════════

def test_network_negative_control_unexpected_connection_fails_loudly(tmp_path, prod_env):
    """An attempted connection to the rehearsal's default sink must FAIL, loudly
    and fast — not hang, and not silently succeed against something else.

    ALTERNATE mechanism: the connection "failed" because the URL was malformed
    and never left the process, which would also hide a real leak behind a
    different error. Ruled out by parsing the sink to a concrete host/port and
    performing a raw socket connect, asserting ConnectionRefused-class failure.
    """
    from urllib.parse import urlparse

    sandbox = build_rehearsal_sandbox(tmp_path, source_config=production_shaped_config())
    parsed = urlparse(sandbox.env["HERMES_BRIDGE_URL"])
    assert parsed.hostname == "127.0.0.1"
    assert isinstance(parsed.port, int) and parsed.port > 0

    s = socket.socket()
    s.settimeout(3)
    with pytest.raises(OSError):
        s.connect((parsed.hostname, parsed.port))
    s.close()


# ═══════════════════════════════════════════════════════════════════════════
# Sweep: the PRE-EXISTING copied-state harnesses are sterile too
# ═══════════════════════════════════════════════════════════════════════════

# Every test harness that builds a subprocess env from the ambient environment
# AND runs real production scripts. Fixing only the new helper would leave the
# incident reachable through any of these — they are where a real rehearsal
# actually happens.
_COPIED_STATE_ENV_BUILDERS = (
    ("tests/e2e/test_catering_lifecycle_deterministic.py", "_child_env"),
    ("tests/test_catering_v02_scripts.py", "_env"),
    ("tests/_b1_helpers.py", "_env_for_subprocess"),
)


def test_sweep_sterilizer_neutralises_a_poisoned_environment(tmp_path, prod_env):
    """The shared sterilizer removes every registered credential and closes the
    .env FILE fallback, given an environment poisoned exactly as an operator
    shell would be.

    ALTERNATE mechanism: the assertion passes because the input was never
    poisoned. Ruled out by asserting the pre-state contains the secrets.
    """
    from conftest import sterilize_subprocess_env

    env = dict(os.environ)
    assert FAKE_OPENROUTER_KEY in json.dumps(env), "input was not actually poisoned"

    out = sterilize_subprocess_env(
        env,
        env_file=tmp_path / ".env-sterile",
        notify_owner_bin=tmp_path / "bin" / "nope",
    )
    blob = json.dumps(out)
    for secret in (FAKE_OPENROUTER_KEY, FAKE_OPENAI_KEY, FAKE_STRIPE_KEY,
                   FAKE_PUSHOVER_APP_TOKEN, FAKE_PUSHOVER_USER_KEY):
        assert secret not in blob
    # the .env file door is closed and the Pushover BINARY cannot resolve to a
    # real installed script
    assert Path(out["HERMES_ENV_PATH"]).is_file()
    assert Path(out["SHIFT_AGENT_ENV_PATH"]).is_file()
    assert not Path(out["SHIFT_AGENT_NOTIFY_OWNER_BIN"]).exists()


@pytest.mark.parametrize("rel_path,func_name", _COPIED_STATE_ENV_BUILDERS)
def test_sweep_every_copied_state_env_builder_sterilizes(rel_path, func_name):
    """Static check: each known copied-state env builder routes through the
    shared sterilizer.

    Honest about its own strength — this reads source text, so it proves the
    call is WRITTEN, not that it executed. The behavioural proof is that the
    suites those builders belong to still pass (see the PR body's test runs);
    this exists so a future edit that drops the call fails LOUDLY here instead
    of silently re-opening the door.
    """
    src = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert f"def {func_name}(" in src, f"{rel_path}:{func_name} moved or renamed"
    assert "sterilize_subprocess_env(" in src, (
        f"{rel_path} builds a subprocess env for real production scripts but no "
        f"longer calls sterilize_subprocess_env — credentials from the ambient "
        f"environment can reach those scripts again"
    )


def test_sweep_lifecycle_e2e_child_env_is_sterile_at_runtime(tmp_path, prod_env):
    """RUNTIME proof for the highest-risk harness — the catering lifecycle e2e.

    That suite installs every catering AND shift script (shift-agent-notify-owner
    included) into /usr/local/bin and runs them for real, with an env built from
    `dict(os.environ)`. It is the closest in-repo analogue of the incident, so
    the static source check above is not good enough for it: this imports its
    actual ``_child_env`` and inspects what it produces from a poisoned
    environment.

    ALTERNATE mechanism: the env came back clean because ``_child_env`` raised
    or returned something empty. Ruled out by asserting it returned a populated
    mapping that still carries the suite's own required settings.
    """
    import importlib.util

    e2e_path = _REPO_ROOT / "tests" / "e2e" / "test_catering_lifecycle_deterministic.py"
    spec = importlib.util.spec_from_file_location("_p1a_lifecycle_probe", e2e_path)
    mod = importlib.util.module_from_spec(spec)
    # dataclass field resolution looks the module up in sys.modules during
    # exec_module, so it must be registered BEFORE the body runs.
    sys.modules["_p1a_lifecycle_probe"] = mod
    try:
        spec.loader.exec_module(mod)
        root = tmp_path / "probe-sbx"
        root.mkdir()
        sb = mod.Sandbox(root=root, bridge_url="http://127.0.0.1:9/send", sent=[])
        env = mod._child_env(sb)
    finally:
        sys.modules.pop("_p1a_lifecycle_probe", None)

    blob = json.dumps(env)
    for secret in (FAKE_OPENROUTER_KEY, FAKE_OPENAI_KEY, FAKE_STRIPE_KEY,
                   FAKE_PUSHOVER_APP_TOKEN, FAKE_PUSHOVER_USER_KEY):
        assert secret not in blob, "the lifecycle e2e still hands a live credential to real scripts"
    # the suite's own contract is intact — this is not an empty/failed env
    assert env["SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS"] == "1"
    assert env["HERMES_BRIDGE_URL"] == "http://127.0.0.1:9/send"
    assert "PATH" in env
    # and the Pushover binary cannot resolve to the real script it installed
    assert not Path(env["SHIFT_AGENT_NOTIFY_OWNER_BIN"]).exists()
