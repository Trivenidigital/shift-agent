"""Turn-scoped identity memo — one identify-sender resolution per inbound turn.

Flyer audit P1-7 / catering audit P1-4. Identity was resolved ~10-15 times per
turn through THREE independent `subprocess.run([identify-sender, ...])` call
sites (`is_owner_chat`'s LID branch, `identify_sender_metadata` which feeds
`has_owner_capability`, and `lid_to_phone_via_identify_sender`). Each is an
external process with a 10s timeout, so successive reads inside ONE turn can
disagree — the sender is `employee` at the brand-asset arm and `customer` at the
cession five lines later.

#694 and #697 froze the DERIVED booleans (`owner_receipt_candidate`,
`menu_caption_candidate`) at their decision points. That closed the two observed
misroutes but left the underlying fact unfrozen, so every other pair of arms in
the ladder still races. This pins the fix at the source: ONE memoized resolution
per turn, with the scalar view and the roles[] membership view both derived from
it, so they cannot contradict each other.

What is pinned:
  * one turn -> exactly ONE identify-sender invocation (measured at the
    subprocess seam, which is the only place a spawn can happen)
  * the scalar role and the roles[] membership read inside a turn agree, even
    when the stub is rigged to return a different answer on every call
  * the memo is turn-scoped, not global: a background thread running DURING the
    turn resolves fresh, a caller with no turn open resolves fresh, and turn N's
    answer never leaks into turn N+1
  * a turn whose identity read FAILED and which then yields to the LLM writes
    exactly ONE `identity_unresolved_turn_yielded` row. Memoizing failures buys
    within-turn consistency (consistent-unknown beats split-brain) but would
    otherwise convert a noisy partial failure into a silent total one.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLUGIN_DIR = SRC / "plugins" / "cf-router"
for _p in (SRC, SRC / "platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

CHAT = "201975216009469@lid"
PHONE = "+17329837841"
OTHER_CHAT = "100000000000002@lid"
OTHER_PHONE = "+15550100002"

# The dual-role principal (#694's live shape) and a customer. Alternating these
# is what makes an unmemoized turn visibly split-brain: the scalar says
# `employee` on one read and `customer` on the next, and roles[] flips with it.
DUAL_ROLE = {"role": "employee", "roles": ["employee", "owner"],
             "phone_normalized": PHONE}
CUSTOMER = {"role": "customer", "roles": [], "phone_normalized": None}
OTHER_PRINCIPAL = {"role": "employee", "roles": ["employee"],
                   "phone_normalized": OTHER_PHONE}


def _load_plugin():
    """Load hooks + actions as submodules of a synthetic package (the plugin dir
    name has a hyphen). Non-evicting, matching the sibling cf-router suites."""
    pkg = "cf_router_turn_identity_pkg"
    for mod_name in list(sys.modules):
        if mod_name == pkg or mod_name.startswith(pkg + "."):
            del sys.modules[mod_name]

    pkg_spec = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg] = importlib.util.module_from_spec(pkg_spec)

    loaded = {}
    for name in ("actions", "hooks"):
        full = f"{pkg}.{name}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{name}.py"))
        spec = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        loader.exec_module(mod)
        loaded[name] = mod
    return loaded["hooks"], loaded["actions"]


class _Seam:
    """Counts + scripts every identify-sender invocation.

    The subprocess call is the ONLY place a spawn can occur, so counting here
    measures the real thing rather than a proxy. `payloads` cycles, so an
    unmemoized turn sees a different identity on every read.
    """

    def __init__(self, payloads):
        self.payloads = payloads
        self.calls: list[str] = []

    def run(self, cmd, *_a, **_kw):
        if cmd and str(cmd[0]).endswith("identify-sender"):
            payload = self.payloads[len(self.calls) % len(self.payloads)]
            self.calls.append(str(cmd[1]))
            if payload is None:  # identify-sender failed for this read
                return SimpleNamespace(returncode=1, stdout="", stderr="boom")
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        # Never let a unit test actually shell out to anything else.
        return SimpleNamespace(returncode=127, stdout="", stderr="stubbed")


def _wire(monkeypatch, hooks_mod, actions_mod, *, payloads):
    """Arm the flyer path and neutralize every store read, so the ONE thing the
    cells observe is how many times identity was resolved and what it said."""
    seam = _Seam(payloads)
    audits: list[dict] = []
    probes: list = []

    monkeypatch.setattr(actions_mod.subprocess, "run", seam.run)

    monkeypatch.setattr(actions_mod, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions_mod, "is_flyer_workflow_enabled", lambda: True)
    monkeypatch.setattr(actions_mod, "mark_cf_router_inbound_seen", lambda *_a, **_k: False)
    monkeypatch.setattr(actions_mod, "front_brain_converse_admits", lambda _c: False)
    monkeypatch.setattr(actions_mod, "find_active_flyer_project_by_sender", lambda _p, _c: None)
    monkeypatch.setattr(actions_mod, "find_flyer_customer_by_sender", lambda _p, _c: None)
    monkeypatch.setattr(actions_mod, "find_paid_flyer_guest_order", lambda _p, _c: None)
    monkeypatch.setattr(actions_mod, "find_active_catering_lead_by_sender", lambda _p, _c: None)
    monkeypatch.setattr(actions_mod, "recent_bare_flyer_for_chat", lambda _c, **_k: False)
    monkeypatch.setattr(actions_mod, "trigger_store_flyer_brand_asset",
                        lambda **_k: (False, "stubbed", None))
    monkeypatch.setattr(actions_mod, "send_flyer_text", lambda *_a, **_k: (True, "mid", ""))
    monkeypatch.setattr(actions_mod, "audit_intercepted", lambda **kw: audits.append(kw))
    monkeypatch.setattr(actions_mod, "begin_flyer_intent_shadow", lambda **_k: None)
    monkeypatch.setattr(actions_mod, "finalize_flyer_intent_shadow", lambda **_k: None)
    monkeypatch.setattr(actions_mod, "reset_flyer_intent_shadow", lambda _t: None)
    monkeypatch.setattr(actions_mod, "finalize_flyer_intake_bypass_shadow", lambda **_k: None)
    monkeypatch.setattr(actions_mod, "consume_pending_flyer_intake_bypass_token", lambda: None)
    monkeypatch.setattr(actions_mod, "reset_flyer_intake_bypass_shadow", lambda _t: None)
    monkeypatch.setattr(hooks_mod, "_sender_has_qualifying_lead", lambda _c: False)
    return SimpleNamespace(seam=seam, audits=audits, probes=probes)


def _in_turn_probe(monkeypatch, actions_mod, fn):
    """Run `fn` at a point that is unambiguously INSIDE the dispatch turn.

    `audit_raw_body` is called near the top of `_pre_gateway_dispatch_impl`,
    unconditionally, after the turn token is opened — so it is the natural probe
    point for asserting what a caller sees mid-turn.
    """
    monkeypatch.setattr(actions_mod, "audit_raw_body", lambda *_a, **_k: fn())


def _dispatch(hooks_mod, *, text="here is our new logo", chat_id=CHAT,
              media="/opt/shift-agent/.hermes/image_cache/img_turn.jpg"):
    return hooks_mod.pre_gateway_dispatch(SimpleNamespace(
        text=text, chat_id=chat_id, message_id="wamid.TURN",
        media_urls=[media] if media else [],
    ))


# ── One resolution per turn ──────────────────────────────────────────────────

def test_one_turn_resolves_identity_exactly_once(monkeypatch):
    """THE regression. Measured pre-fix at 10 identify-sender spawns for this
    exact inbound — ten independent chances for the turn to change its mind
    about who is speaking."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, payloads=[DUAL_ROLE, CUSTOMER])
    monkeypatch.setattr(actions_mod, "audit_raw_body", lambda *_a, **_k: None)

    _dispatch(hooks_mod)

    assert len(s.seam.calls) == 1, (
        f"identity resolved {len(s.seam.calls)}x in one turn "
        f"({s.seam.calls}) — each extra read can contradict the last")
    assert s.seam.calls == [CHAT]


def test_scalar_and_membership_views_agree_within_a_turn(monkeypatch):
    """The split-brain itself, at the source.

    `lid_to_phone_via_identify_sender` reads the legacy SCALAR;
    `has_owner_capability` reads roles[] MEMBERSHIP through
    `identify_sender_metadata`. Pre-fix these are two separate subprocesses, so
    with the stub returning a different principal on each call the two views
    describe different people inside one turn. Memoizing them separately would
    have preserved exactly that split — which is why the memo sits at the
    subprocess boundary and both views derive from one payload.
    """
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, payloads=[DUAL_ROLE, CUSTOMER])

    def _probe():
        phone, role = actions_mod.lid_to_phone_via_identify_sender(CHAT)
        s.probes.append({
            "phone": phone,
            "role": role,
            "owner_membership": actions_mod.has_owner_capability(CHAT),
            "metadata_role": actions_mod.identify_sender_metadata(CHAT).get("role"),
            "spawns": len(s.seam.calls),
        })

    _in_turn_probe(monkeypatch, actions_mod, _probe)

    _dispatch(hooks_mod)

    assert len(s.probes) == 1
    p = s.probes[0]
    assert p["spawns"] == 1, (
        f"three views cost {p['spawns']} resolutions — they can disagree")
    # All three derive from DUAL_ROLE, the first (and only) payload.
    assert p["role"] == "employee"
    assert p["metadata_role"] == "employee", (
        "the metadata view disagrees with the scalar view inside one turn")
    assert p["owner_membership"] is True, (
        "roles[] membership disagrees with the scalar view inside one turn")
    assert p["phone"] == PHONE


def test_regulated_account_guard_resolves_identity_once(monkeypatch):
    """The explicit double-resolve: `_try_flyer_regulated_account_guard` spawned
    identify-sender twice five lines apart and discarded the first read's role."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, payloads=[DUAL_ROLE, CUSTOMER])
    monkeypatch.setattr(actions_mod, "is_flyer_regulated_account_intent", lambda _t: True)
    monkeypatch.setattr(actions_mod, "is_flyer_account_command", lambda _t: False)
    monkeypatch.setattr(actions_mod, "flyer_text_targets_revision_field", lambda _t: True)
    monkeypatch.setattr(actions_mod, "audit_raw_body", lambda *_a, **_k: None)

    _dispatch(hooks_mod, text="change the phone number on my plan", media=None)

    assert len(s.seam.calls) == 1, (
        f"the regulated-account guard resolved identity {len(s.seam.calls)}x")


# ── The memo is turn-scoped, not global ──────────────────────────────────────

def test_a_background_thread_during_the_turn_resolves_fresh(monkeypatch):
    """A `threading.Thread` starts with a fresh, empty context, so the ContextVar
    holding the memo reads its default (None) there and the memo is a NO-OP. A
    timer firing mid-turn must not be handed the turn's frozen identity, and must
    not write into it either."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, payloads=[DUAL_ROLE, CUSTOMER])

    def _probe():
        actions_mod.lid_to_phone_via_identify_sender(CHAT)  # turn read -> spawn 1
        spawns_before = len(s.seam.calls)

        def _thread_body():
            s.probes.append({
                "role": actions_mod.lid_to_phone_via_identify_sender(CHAT)[1],
                "spawns_after": len(s.seam.calls),
            })

        t = threading.Thread(target=_thread_body)
        t.start()
        t.join(timeout=10)
        s.probes.append({"spawns_before": spawns_before})

    _in_turn_probe(monkeypatch, actions_mod, _probe)

    _dispatch(hooks_mod)

    thread_result = s.probes[0]
    assert thread_result["spawns_after"] == 2, (
        "the background thread reused the turn's memo instead of resolving fresh")
    assert thread_result["role"] == "customer", (
        "the thread got the turn's frozen identity, not its own fresh read")


def test_no_turn_open_means_no_memo(monkeypatch):
    """A script import or any caller outside a dispatch turn has no token active,
    so every call resolves fresh. The memo must never become a process-global
    cache — identity changes between turns and between runs."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, payloads=[DUAL_ROLE, CUSTOMER])

    first = actions_mod.lid_to_phone_via_identify_sender(CHAT)
    second = actions_mod.lid_to_phone_via_identify_sender(CHAT)

    assert len(s.seam.calls) == 2, "a caller with no turn open was served a cache"
    assert first[1] == "employee" and second[1] == "customer"


def test_turn_memo_does_not_leak_into_the_next_turn(monkeypatch):
    """Two sequential inbounds from DIFFERENT principals. If the memo outlived
    its turn, the second sender would be routed as the first — strictly worse
    than the split-brain this replaces."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              payloads=[DUAL_ROLE, OTHER_PRINCIPAL])
    seen: list = []
    monkeypatch.setattr(
        actions_mod, "audit_raw_body",
        lambda *_a, **_k: seen.append(
            actions_mod.lid_to_phone_via_identify_sender(_a[1] if len(_a) > 1 else CHAT)))

    _dispatch(hooks_mod, chat_id=CHAT)
    _dispatch(hooks_mod, chat_id=OTHER_CHAT)

    assert len(s.seam.calls) == 2, s.seam.calls
    assert s.seam.calls == [CHAT, OTHER_CHAT]
    assert seen[0][0] == PHONE
    assert seen[1][0] == OTHER_PHONE, (
        "turn 2 was served turn 1's identity — the memo outlived its turn")


# ── Failed identity: consistent, but never silent ────────────────────────────

def test_failed_identity_turn_yielding_to_the_llm_writes_one_audit_row(monkeypatch):
    """Memoizing FAILURES is deliberate — a turn that cannot resolve identity is
    at least internally consistent about it. The cost is that one noisy partial
    failure becomes one quiet total one, so the turn must say so exactly once
    when it hands the inbound to the LLM (§12a)."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, payloads=[None])
    monkeypatch.setattr(actions_mod, "audit_raw_body", lambda *_a, **_k: None)

    result = _dispatch(hooks_mod)

    assert result is None, "this inbound must still fall through to the LLM"
    rows = [a for a in s.audits if a.get("reason") == "identity_unresolved_turn_yielded"]
    assert len(rows) == 1, (
        f"expected exactly one unresolved-identity row, got {len(rows)}: "
        f"{[a.get('reason') for a in s.audits]}")
    assert rows[0]["chat_id"] == CHAT
    assert len(s.seam.calls) == 1, "a failed read must be memoized, not retried"


def test_a_resolved_turn_writes_no_unresolved_row(monkeypatch):
    """Non-vacuity: the row is about failure, not about yielding."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, payloads=[DUAL_ROLE])
    monkeypatch.setattr(actions_mod, "audit_raw_body", lambda *_a, **_k: None)

    assert _dispatch(hooks_mod) is None
    assert not [a for a in s.audits
                if a.get("reason") == "identity_unresolved_turn_yielded"]


def test_the_unresolved_row_is_written_once_per_turn_not_once_per_read(monkeypatch):
    """Two turns, both failing, each writing exactly one row — and the second
    turn re-resolves rather than inheriting the first turn's failure."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, payloads=[None])
    monkeypatch.setattr(actions_mod, "audit_raw_body", lambda *_a, **_k: None)

    _dispatch(hooks_mod)
    _dispatch(hooks_mod)

    rows = [a for a in s.audits if a.get("reason") == "identity_unresolved_turn_yielded"]
    assert len(rows) == 2, f"one row per turn expected, got {len(rows)}"
    assert len(s.seam.calls) == 2, "each turn resolves once; failures do not persist"


def test_unresolved_reason_literal_is_an_enum_member():
    """A reason outside the enum is swallowed by `audit_intercepted`'s
    except-and-warn — i.e. the alarm this whole arm exists for would be
    invisible to telemetry."""
    from typing import get_args
    import schemas
    allowed = set(get_args(schemas.CfRouterIntercepted.model_fields["reason"].annotation))
    assert "identity_unresolved_turn_yielded" in allowed


# ── Failure SEMANTICS are unchanged ──────────────────────────────────────────

@pytest.mark.parametrize("payload,expected", [
    (None, (None, "unknown")),
    ({"role": "employee", "phone_normalized": PHONE}, (PHONE, "employee")),
    ({}, (None, "unknown")),
])
def test_scalar_accessor_failure_semantics_are_unchanged(monkeypatch, payload, expected):
    """The memo changes consistency and spawn count. It must not change what any
    accessor returns for a given identify-sender response."""
    hooks_mod, actions_mod = _load_plugin()
    _wire(monkeypatch, hooks_mod, actions_mod, payloads=[payload])

    assert actions_mod.lid_to_phone_via_identify_sender(CHAT) == expected


@pytest.mark.parametrize("payload,expected", [
    (None, False),
    ({"role": "employee", "roles": ["employee", "owner"]}, True),
    ({"role": "employee", "roles": ["employee"]}, False),
    ({"role": "owner"}, True),          # rollback window: no roles[] key
    ({"role": "customer"}, False),
])
def test_owner_membership_semantics_are_unchanged(monkeypatch, payload, expected):
    hooks_mod, actions_mod = _load_plugin()
    _wire(monkeypatch, hooks_mod, actions_mod, payloads=[payload])

    assert actions_mod.has_owner_capability(CHAT) is expected


@pytest.mark.parametrize("payload,expected_role", [
    (None, "unknown"),
    ({"role": "customer"}, "customer"),
])
def test_metadata_accessor_failure_semantics_are_unchanged(monkeypatch, payload, expected_role):
    hooks_mod, actions_mod = _load_plugin()
    _wire(monkeypatch, hooks_mod, actions_mod, payloads=[payload])

    assert actions_mod.identify_sender_metadata(CHAT).get("role") == expected_role


def test_metadata_callers_cannot_mutate_the_memo(monkeypatch):
    """`identify_sender_metadata` hands back a dict. Pre-memo each caller got a
    freshly parsed one; now they would share the memoized payload, so a caller
    mutating its copy could rewrite another arm's view of the sender."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, payloads=[DUAL_ROLE])

    def _probe():
        first = actions_mod.identify_sender_metadata(CHAT)
        first["role"] = "tampered"
        first.pop("roles", None)
        s.probes.append(actions_mod.identify_sender_metadata(CHAT))

    _in_turn_probe(monkeypatch, actions_mod, _probe)

    _dispatch(hooks_mod)

    assert s.probes[0]["role"] == "employee", "a caller's mutation reached the memo"
    assert s.probes[0]["roles"] == ["employee", "owner"]
