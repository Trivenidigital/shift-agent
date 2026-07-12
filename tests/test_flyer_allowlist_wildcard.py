"""Wildcard (`*`) graduation for every flyer allowlist gate.

Incident F0217 (2026-07-11): a second onboarded customer (CUST0007) silently
ran the raw, unprotected pipeline because every flyer quality/safety feature was
gated by a per-number env allowlist and nothing graduated a validated feature to
ALL customers. Onboarding cannot safely edit env files at runtime, so the box now
sets the validated lists to the literal wildcard `*`.

Fix under test: an explicit `*` entry in ANY scoped-gate allowlist enables that
gate for EVERY sender — WITHOUT touching the sacred empty=DISABLED fail-closed
convention (the premium_overlay empty=global-on bug is a ledgered gotcha; `*` is
an EXPLICIT opt-in, never an implicit empty-list flip).

Semantics asserted per gate (identical everywhere):
  - flag on + unset/empty allowlist   -> DISABLED (unchanged, fail-closed)
  - flag on + member                  -> enabled
  - flag on + non-member              -> disabled
  - flag on + allowlist "*"           -> enabled for ANY sender
  - flag on + allowlist "*,+1..."     -> `*` composes harmlessly (both enabled)

INVARIANT META-TEST (standing rule 2026-07-07 — every documented invariant gets
a test that fails if violated): the meta-test greps the flyer source for every
`*_ALLOWLIST`-reading gate (plus the shadow `_CHATS` gate) and asserts each one
is registered here AND honors `*` — so a future gate cannot ship without wildcard
support. Runs in-process (src/platform + src/agents/flyer on sys.path, the way the
flat VPS modules import), plus the cf-router plugin loaded standalone.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
for _p in (_SRC / "platform", _SRC / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bare_render as B  # noqa: E402
import render as R  # noqa: E402
import style_registers as S  # noqa: E402

PHONE = "+17329837841"       # the historical validation-era number
OTHER = "+19998887777"       # a second listed number
STRANGER = "+15550009999"    # never listed — the CUST0007 stand-in


def _load_actions():
    path = _REPO / "src" / "plugins" / "cf-router" / "actions.py"
    spec = importlib.util.spec_from_file_location("cf_router_actions_wildcard_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ACTIONS = _load_actions()


def _proj(sender: str):
    """Minimal stand-in for a FlyerProject — the render gates only read
    ``.customer_phone``."""
    return types.SimpleNamespace(customer_phone=sender)


# Every scoped flyer allowlist gate, keyed by the allowlist env it reads.
#   flags : master flag env(s) set to "1" to arm the gate
#   allow : the allowlist env var (the wildcard lands here)
#   arm   : optional module-constant arming (gates whose flag is read at import)
#   call  : invoke the gate with a candidate sender -> bool
def _registry():
    return [
        dict(id="premium_repair", flags=["FLYER_PREMIUM_REPAIR"],
             allow="FLYER_PREMIUM_REPAIR_ALLOWLIST",
             call=lambda s: R._premium_repair_enabled(_proj(s))),
        dict(id="premium_overlay", flags=["FLYER_PREMIUM_OVERLAY"],
             allow="FLYER_PREMIUM_OVERLAY_ALLOWLIST",
             call=lambda s: R._premium_overlay_enabled(_proj(s))),
        dict(id="deterministic_recovery", flags=["FLYER_DETERMINISTIC_RECOVERY"],
             allow="FLYER_PREMIUM_OVERLAY_ALLOWLIST",
             call=lambda s: R._deterministic_recovery_enabled(_proj(s))),
        dict(id="deterministic_first", flags=["FLYER_DETERMINISTIC_FIRST"],
             allow="FLYER_PREMIUM_OVERLAY_ALLOWLIST",
             call=lambda s: R._deterministic_first_enabled(_proj(s))),
        dict(id="creative_director_v2", flags=["FLYER_CREATIVE_DIRECTOR_V2"],
             allow="FLYER_PREMIUM_OVERLAY_ALLOWLIST",
             call=lambda s: R._creative_director_v2_enabled(_proj(s))),
        dict(id="premium_poster_v1", flags=["FLYER_PREMIUM_POSTER_V1"],
             allow="FLYER_PREMIUM_POSTER_V1_ALLOWLIST",
             call=lambda s: R._premium_poster_v1_armed(_proj(s))),
        dict(id="creative_director_v1", flags=["FLYER_CREATIVE_DIRECTOR_ENABLED"],
             allow="FLYER_CREATIVE_DIRECTOR_ALLOWLIST",
             call=lambda s: B._creative_director_armed(s)),
        dict(id="skill_driven_scene", flags=["FLYER_SKILL_DRIVEN_SCENE"],
             allow="FLYER_SKILL_DRIVEN_SCENE_ALLOWLIST",
             call=lambda s: B._skill_driven_scene_armed(s)),
        dict(id="bare_iteration", flags=[],
             allow="FLYER_BARE_ITERATION_ALLOWLIST",
             arm=lambda mp: mp.setattr(B, "ITERATION_ENABLED", True),
             call=lambda s: B._iteration_armed(s)),
        dict(id="visible_contract", flags=["FLYER_VISIBLE_CONTRACT"],
             allow="FLYER_VISIBLE_CONTRACT_ALLOWLIST",
             call=lambda s: B._visible_contract_armed(_proj(s))),
        dict(id="style_registers", flags=["FLYER_STYLE_REGISTERS"],
             allow="FLYER_STYLE_REGISTERS_ALLOWLIST",
             call=lambda s: S.style_registers_enabled(s)),
        # cf-router B1 shadow classifier gate: the allowlisted() helper carries
        # no internal flag (the master flag is checked at the call site), and the
        # env is *_CHATS, not *_ALLOWLIST — but it IS a sender-scoped allowlist gate.
        dict(id="intent_shadow_llm", flags=[],
             allow="FLYER_INTENT_SHADOW_LLM_CHATS",
             call=lambda s: ACTIONS._flyer_intent_shadow_llm_allowlisted(s)),
    ]


REGISTRY = _registry()
_ALL_ENVS = sorted({e["allow"] for e in REGISTRY} |
                   {f for e in REGISTRY for f in e["flags"]} |
                   {"FLYER_BARE_ITERATION"})


def _arm(monkeypatch, entry, allow_value):
    """Reset every gate env, arm this gate's flag(s), set its allowlist."""
    for env in _ALL_ENVS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(B, "ITERATION_ENABLED", False)  # default; entry may re-arm
    if entry.get("arm"):
        entry["arm"](monkeypatch)
    for flag in entry["flags"]:
        monkeypatch.setenv(flag, "1")
    if allow_value is not None:
        monkeypatch.setenv(entry["allow"], allow_value)


_IDS = [e["id"] for e in REGISTRY]


@pytest.mark.parametrize("entry", REGISTRY, ids=_IDS)
def test_empty_allowlist_is_disabled(monkeypatch, entry):
    # Fail-closed convention stays sacred: unset AND empty both DISABLE.
    _arm(monkeypatch, entry, None)
    assert entry["call"](PHONE) is False, f"{entry['id']}: unset allowlist must be DISABLED"
    _arm(monkeypatch, entry, "")
    assert entry["call"](PHONE) is False, f"{entry['id']}: empty allowlist must be DISABLED"
    _arm(monkeypatch, entry, " , , ")
    assert entry["call"](PHONE) is False, f"{entry['id']}: whitespace-only allowlist must be DISABLED"


@pytest.mark.parametrize("entry", REGISTRY, ids=_IDS)
def test_member_and_nonmember(monkeypatch, entry):
    _arm(monkeypatch, entry, PHONE)
    assert entry["call"](PHONE) is True, f"{entry['id']}: listed member must be enabled"
    assert entry["call"](STRANGER) is False, f"{entry['id']}: non-member must be disabled"


@pytest.mark.parametrize("entry", REGISTRY, ids=_IDS)
def test_wildcard_enables_for_anyone(monkeypatch, entry):
    _arm(monkeypatch, entry, "*")
    assert entry["call"](PHONE) is True, f"{entry['id']}: `*` must enable the historical number"
    assert entry["call"](STRANGER) is True, f"{entry['id']}: `*` must enable a never-listed sender"
    assert entry["call"]("447700900123@lid") is True, f"{entry['id']}: `*` must enable a LID sender"


@pytest.mark.parametrize("entry", REGISTRY, ids=_IDS)
def test_wildcard_composes_with_numbers(monkeypatch, entry):
    # `*` alongside explicit entries stays global; order-independent.
    _arm(monkeypatch, entry, f"*,{OTHER}")
    assert entry["call"](OTHER) is True, f"{entry['id']}: listed number enabled under `*,+num`"
    assert entry["call"](STRANGER) is True, f"{entry['id']}: `*,+num` still global"
    _arm(monkeypatch, entry, f"{OTHER},*")
    assert entry["call"](STRANGER) is True, f"{entry['id']}: `+num,*` still global (order-independent)"


@pytest.mark.parametrize("entry", REGISTRY, ids=_IDS)
def test_master_flag_still_governs(monkeypatch, entry):
    # Wildcard support MUST NOT bypass the master kill-switch: with the flag(s)
    # off, even `*` stays disabled. (Gates with no separable in-function flag —
    # cf-router allowlisted(), which is flag-gated at its call site — are skipped.)
    if not entry["flags"] and not entry.get("arm"):
        pytest.skip(f"{entry['id']}: flag enforced at call site, not in this helper")
    _arm(monkeypatch, entry, "*")
    for flag in entry["flags"]:
        monkeypatch.delenv(flag, raising=False)
    if entry.get("arm"):
        monkeypatch.setattr(B, "ITERATION_ENABLED", False)
    assert entry["call"](PHONE) is False, f"{entry['id']}: master flag off must disable even under `*`"


def test_invariant_every_source_allowlist_gate_is_registered_and_honors_wildcard(monkeypatch):
    """Discovery guard: grep the flyer source for every `*_ALLOWLIST` env var
    (plus the shadow `_CHATS` gate) and prove each is covered by this file's
    registry AND honors `*`. A future gate that ships a new allowlist env without
    wildcard support makes this test go RED."""
    sources = [
        _SRC / "agents" / "flyer" / "render.py",
        _SRC / "agents" / "flyer" / "bare_render.py",
        _SRC / "agents" / "flyer" / "style_registers.py",
    ]
    discovered: set[str] = set()
    for path in sources:
        discovered |= set(re.findall(r"FLYER_[A-Z0-9_]+_ALLOWLIST", path.read_text(encoding="utf-8")))
    assert discovered, "source grep found no *_ALLOWLIST gates — grep or paths are wrong"

    registered = {e["allow"] for e in REGISTRY}
    missing = discovered - registered
    assert not missing, (
        f"new *_ALLOWLIST flyer gate(s) not registered for wildcard coverage: {sorted(missing)}. "
        "Add the gate to REGISTRY in this file (and give it `*` support) — a validated "
        "feature must graduate to all customers via the `*` wildcard."
    )
    # The B1 shadow gate reads *_CHATS (not *_ALLOWLIST) but is a sender-scoped
    # allowlist gate; pin it explicitly so it can't silently drop out.
    assert "FLYER_INTENT_SHADOW_LLM_CHATS" in registered

    # And every registered gate must actually honor `*` for a never-listed sender.
    for entry in REGISTRY:
        _arm(monkeypatch, entry, "*")
        assert entry["call"](STRANGER) is True, f"{entry['id']}: registered gate does not honor `*`"
