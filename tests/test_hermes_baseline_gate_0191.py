"""Focused tests for the 0.19.1 patch-baseline + deploy-gate repair.

Pins the CURRENT invariants only. Deliberately does NOT duplicate the policy
preflight's internal checks (A/B/C/D) -- the gate reuses that script rather than
reimplementing it, and re-asserting its logic here would fork the source of truth.

Static assertions on the real tracked artifacts; no VPS, no network, no fork.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "tools" / "hermes-patch-baseline.txt"
GATE = REPO / "tools" / "check-shift-agent-patch.sh"
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
PREFLIGHT = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-policy-preflight"
POLICY = REPO / "src" / "plugins" / "shift-agent-policy" / "policy.py"

BASELINE_TEXT = BASELINE.read_text(encoding="utf-8")
GATE_TEXT = GATE.read_text(encoding="utf-8")
DEPLOY_TEXT = DEPLOY.read_text(encoding="utf-8")

ATTESTED_COMMIT = "cc4cab2f592e60a197e796506de9168f74baf3ea"
ATTESTED_VERSION = "0.19.1"
ATTESTED_BRIDGE_SHA = "f8bdb2abc2a2a5bc8f80b9eb6373fa67dc78a23793db92fd7e5552d11724bb0d"


def _pin(key: str) -> str:
    m = re.search(rf"^{key}=(.+)$", BASELINE_TEXT, re.MULTILINE)
    assert m, f"baseline missing {key}"
    return m.group(1).strip()


# ── the attested 0.19.1 state ──────────────────────────────────────────────
def test_baseline_attests_the_verified_0191_state():
    assert _pin("HERMES_COMMIT") == ATTESTED_COMMIT
    assert _pin("HERMES_VERSION") == ATTESTED_VERSION
    assert _pin("BRIDGE_POST_PATCH_SHA256") == ATTESTED_BRIDGE_SHA


def test_baseline_no_longer_attests_the_superseded_014_state():
    # The stale pin made the gate unpassable without HERMES_PIN_OVERRIDE.
    assert _pin("HERMES_COMMIT") != "1e71b7180e5b4e84905b9a3086cf9cecca139562"
    assert _pin("HERMES_VERSION") != "0.14.0"
    assert _pin("BRIDGE_POST_PATCH_SHA256") != (
        "94a13a926798d5c2e6e69dd4f227ef940f88e3076bab60333d1b92fa55680913"
    )


def test_baseline_records_the_bridge_reproduction_inputs():
    # The attestation is only meaningful if its provenance is recorded with it.
    assert "9e1c4745" in BASELINE_TEXT, "pristine bridge input not recorded"
    assert "patch1_port_v0191.py" in BASELINE_TEXT
    assert "patch2_failclosed.py" in BASELINE_TEXT
    # ...and the honest caveat, so nobody later reads it as "ran as-is".
    assert "ROOT" in BASELINE_TEXT and "one line" in BASELINE_TEXT.lower()


# ── the gate verifies the CURRENT architecture ─────────────────────────────
def test_gate_reuses_the_preflight_instead_of_duplicating_it():
    assert "/usr/local/bin/shift-agent-policy-preflight" in GATE_TEXT
    # It must FAIL when the preflight fails, and when it is missing.
    assert re.search(r"fail .*preflight FAILED", GATE_TEXT), "gate must fail closed on preflight failure"
    assert re.search(r"fail .*missing or not executable", GATE_TEXT), "gate must fail closed on a missing preflight"
    # It must NOT re-implement the preflight's own A/B/C/D checks.
    assert "discover_plugins" not in GATE_TEXT, "gate must not duplicate preflight logic"


def test_gate_requires_the_canonical_policy_implementation():
    for needle in (
        "src/plugins/shift-agent-policy",
        "class ScreenedWhatsAppAdapter",
        "front_brain_screen_gateway_send",
        "pre_gateway_dispatch",
    ):
        assert needle in GATE_TEXT, f"gate no longer requires {needle!r}"


def test_gate_retains_bridge_markers_and_sha_check():
    assert 'grep -q "BEGIN shift-agent-sender-id" "$BR"' in GATE_TEXT
    assert 'grep -q "BEGIN shift-agent-cta-buttons" "$BR"' in GATE_TEXT
    assert "PINNED_BRIDGE_SHA" in GATE_TEXT and "ACTUAL_BRIDGE_SHA" in GATE_TEXT
    assert "bridge.js sha256 drift detected" in GATE_TEXT


def test_gate_still_pins_installed_commit_and_version():
    assert "PINNED_COMMIT" in GATE_TEXT and "CURRENT_COMMIT" in GATE_TEXT
    assert "PINNED_VERSION" in GATE_TEXT


# ── the obsolete architecture is no longer asserted ────────────────────────
def test_absence_of_gateway_platforms_whatsapp_is_not_itself_a_failure():
    """The file was REMOVED upstream in 0.19.1 (platform relocated). Asserting
    markers in it could never pass again, which is why the old gate was broken."""
    live_wa_assertions = [
        ln for ln in GATE_TEXT.splitlines()
        if '"$WA"' in ln and ln.strip().startswith(("grep", "[ -f", "[ -n"))
    ]
    assert not live_wa_assertions, f"gate still asserts on $WA: {live_wa_assertions}"


def test_obsolete_runpy_patch_markers_are_not_asserted():
    for obsolete in (
        'grep -q "BEGIN shift-agent-sender-id" "$RUN"',
        'grep -q "BEGIN shift-agent-turn-send-budget" "$RUN"',
    ):
        assert obsolete not in GATE_TEXT, f"gate still asserts obsolete marker: {obsolete}"


def test_runpy_hook_surface_check_is_retained():
    """run.py still EXISTS on 0.19.1 and still hosts the hook surface both
    cf-router and shift-agent-policy depend on, so this check stays."""
    assert 'grep -q "pre_gateway_dispatch" "$RUN"' in GATE_TEXT


# ── deploy wiring ──────────────────────────────────────────────────────────
def test_deploy_explicitly_installs_the_canonical_preflight():
    assert (
        "install -m 755 src/agents/shift/scripts/shift-agent-policy-preflight "
        "/usr/local/bin/shift-agent-policy-preflight" in DEPLOY_TEXT
    ), "deploy must name the preflight install explicitly, not rely on a glob"


def test_deploy_fails_loudly_when_the_preflight_is_absent():
    idx = DEPLOY_TEXT.index("shift-agent-policy-preflight missing from this tree")
    assert "exit 1" in DEPLOY_TEXT[idx: idx + 200], "missing preflight must abort the deploy"


def test_no_broad_tools_glob_was_introduced():
    """R4-H-2 invariant: never bulk-install tools/ into /usr/local/bin."""
    for forbidden in ("install -m 755 tools/*", "install -m 644 tools/*", "tools/* /usr/local/bin"):
        assert forbidden not in DEPLOY_TEXT, f"broad glob introduced: {forbidden}"


# ── the imported preflight itself ──────────────────────────────────────────
def test_preflight_is_tracked_executable_and_fail_closed():
    assert PREFLIGHT.is_file(), "canonical preflight must be tracked in-repo"
    text = PREFLIGHT.read_text(encoding="utf-8")
    assert text.startswith("#!"), "preflight must carry a shebang"
    assert "sys.exit(1)" in text, "preflight must exit non-zero on failure"
    assert "ScreenedWhatsAppAdapter" in text


def test_policy_plugin_is_tracked():
    assert POLICY.is_file(), "the screening plugin must be tracked (rsync --delete would remove it otherwise)"
