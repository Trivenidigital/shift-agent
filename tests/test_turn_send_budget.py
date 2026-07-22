"""Per-INBOUND-TURN outbound send budget (2026-07-22 — the TRUE volume cap the
#641 gateway throttle could not provide).

The #641 gateway throttle can only SUBSTITUTE a safe template + page: its screen is
contractually ``-> str`` and the injected adapter wrapper unconditionally relays
that string, so a 28-send spiral still delivers 28 (templated) messages. This
companion enforces a per-inbound-turn send-COUNT cap WHERE sends can be suppressed
— the adapter send()/edit_message() — via a not-send sentinel the wrapper returns
and the inject honors with a well-formed no-op. It is a gate BEFORE/AROUND the
#641 content screen, not a replacement.

Three layers are exercised:
  1. ``_TurnSendBudget.reserve`` — the SYNCHRONOUS atomic counter (Windows-safe).
  2. ``turn_send_budget_gate`` + ``begin_inbound_turn_send_budget`` — the safe_io
     seam decision (admit / suppress / fail-closed), audit rows, page-once.
  3. End-to-end: the REAL tools/patch-hermes.py patch applied to a synthetic
     adapter with a COUNTABLE relay, driven under asyncio — proves the transport
     is hit exactly LIMIT times and never after exhaustion, incl. concurrent sends.

fcntl is stubbed (ensure_fcntl_stub) so the module imports off-Linux;
front_brain_screen_gateway_send needs REPO/src on the path (flyer policy import)
but the content screen is left DISABLED here (FRONT_BRAIN_OUTBOUND_ENFORCE unset)
so it is a transparent passthrough and only the budget is under test.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.machinery
import importlib.util
import itertools
import json
import os
import sys
import types
from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform", REPO / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import safe_io  # noqa: E402
import schemas  # noqa: E402

PATCH_HERMES = REPO / "tools" / "patch-hermes.py"

CHAT = "17329837841@c.us"
_counter = itertools.count()


@pytest.fixture(autouse=True)
def _live_modules():
    """Bind the LIVE safe_io / schemas from sys.modules (mirrors the gateway
    throttle test) so module-level references resolve to the same objects the
    patched adapter's ``import safe_io`` also resolves — order-determinism against
    test_cf_router_plugin's pop+reload."""
    live = sys.modules.get("safe_io") or safe_io
    live_schemas = sys.modules.get("schemas") or schemas
    globals()["safe_io"] = live
    globals()["schemas"] = live_schemas
    # Reset the per-turn ContextVar so a begin() in one main-context test can't
    # leak a budget into the next (asyncio.run harness tests set it in a COPIED
    # context, so those never leak — but the direct-call gate tests do).
    live._TURN_SEND_BUDGET.set(None)
    # Reset the config-warning dedup set so the once-per-value warning test is
    # isolated from other tests that trip malformed-config fallbacks.
    live._TURN_SEND_BUDGET_LIMIT_WARNED.clear()
    return live


@pytest.fixture
def budget_on(monkeypatch):
    """Enable the per-turn budget (limit 5) and stub the operator page so no
    subprocess fires. Returns the recorded page-call list."""
    monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", "1")
    monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "5")
    pages: list = []
    monkeypatch.setattr(
        safe_io, "notify_owner_with_fallback",
        lambda *a, **k: pages.append((a, k)) or True,
    )
    return pages


# ── Layer 1: the synchronous atomic counter ───────────────────────────────────

class TestReserveIsAtomicCounter:
    def test_finalized_sends_cap_at_limit(self):
        b = safe_io._TurnSendBudget("t", 3, 30)
        assert [b.reserve(True) for _ in range(5)] == [True, True, True, False, False]
        assert b.count == 3  # never ratchets past the cap

    def test_progressive_drafts_admit_without_consuming(self):
        b = safe_io._TurnSendBudget("t", 2, 10)
        # drafts (consume=False) admit and DON'T increment the finalized counter
        assert b.reserve(False) is True and b.count == 0 and b.draft_count == 1
        assert b.reserve(False) is True and b.count == 0 and b.draft_count == 2
        # a finalized send consumes the finalized slot
        assert b.reserve(True) is True and b.count == 1

    def test_every_send_drops_after_exhaustion_drafts_included(self):
        b = safe_io._TurnSendBudget("t", 1, 10)
        assert b.reserve(True) is True  # count -> 1 (== limit)
        assert b.reserve(True) is False   # finalized dropped
        assert b.reserve(False) is False  # draft ALSO dropped after finalized exhaustion

    def test_drafts_bounded_by_separate_ceiling_before_finalized_exhaustion(self):
        # high finalized cap, low draft ceiling: drafts drop on THEIR OWN bound even
        # though the finalized counter still has room (the transport-spam guard).
        b = safe_io._TurnSendBudget("t", 100, 3)
        assert [b.reserve(False) for _ in range(5)] == [True, True, True, False, False]
        assert b.draft_count == 3
        assert b.count == 0  # drafts never touched the finalized counter

    def test_reserve_has_no_await_point(self):
        # Atomicity rests on reserve being purely synchronous (no await in the
        # critical section) — assert structurally there is no Await node, and the
        # def is a plain (non-async) function.
        fn = next(
            n for n in ast.walk(ast.parse(Path(safe_io.__file__).read_text(encoding="utf-8")))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "reserve"
        )
        assert isinstance(fn, ast.FunctionDef)  # not AsyncFunctionDef
        assert not any(isinstance(node, ast.Await) for node in ast.walk(fn))


# ── Layer 2: the safe_io gate (admit / suppress / fail-closed / page) ──────────

class TestGateDecision:
    def test_flag_off_returns_none_no_suppression(self, monkeypatch):
        monkeypatch.delenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", raising=False)
        # No begin, feature off → None (byte-identical passthrough), never False.
        assert safe_io.turn_send_budget_gate(CHAT, "hi") is None

    def test_28_finalized_sends_admit_exactly_limit(self, budget_on, monkeypatch):
        emit = []
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: emit.append((t, f)))
        turn_id = safe_io.begin_inbound_turn_send_budget()
        assert turn_id  # a budget was set (feature on)

        decisions = [safe_io.turn_send_budget_gate(CHAT, "reply") for _ in range(28)]
        assert decisions.count(True) == 5          # exactly LIMIT admitted
        assert decisions.count(False) == 23        # 28 - LIMIT suppressed
        # every suppressed send recorded a metadata-only send_budget_exhausted row
        rows = [f for t, f in emit if t == "send_budget_exhausted"]
        assert len(rows) == 23
        assert all(r["reason"] == "exhausted" for r in rows)
        assert all(r["turn_id"] == turn_id and r["limit"] == 5 for r in rows)
        assert all("message" not in r and "reply_text" not in r for r in rows)  # no content
        # operator paged EXACTLY once for the exhausted turn
        assert len(budget_on) == 1

    def test_progressive_drafts_do_not_consume_then_drop_after_exhaustion(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", "1")
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "1")
        monkeypatch.setattr(safe_io, "notify_owner_with_fallback", lambda *a, **k: True)
        safe_io.begin_inbound_turn_send_budget()
        # 10 drafts admit and consume nothing
        assert all(safe_io.turn_send_budget_gate(CHAT, "d", reserve_budget=False) is True for _ in range(10))
        # the single finalized send consumes the one unit
        assert safe_io.turn_send_budget_gate(CHAT, "f") is True
        # the next finalized send is exhausted → suppress
        assert safe_io.turn_send_budget_gate(CHAT, "f2") is False
        # a draft AFTER exhaustion is ALSO suppressed (every send stops)
        assert safe_io.turn_send_budget_gate(CHAT, "d2", reserve_budget=False) is False

    def test_missing_context_fails_closed_without_paging(self, budget_on, monkeypatch):
        emit = []
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: emit.append((t, f)))
        # feature ON but NO begin() → no turn context. FAIL CLOSED (suppress).
        assert safe_io.turn_send_budget_gate(CHAT, "x") is False
        assert safe_io.turn_send_budget_gate(CHAT, "y") is False
        rows = [f for t, f in emit if t == "send_budget_exhausted"]
        assert len(rows) == 2
        assert all(r["reason"] == "missing_turn_context" for r in rows)
        # missing-context path audits but does NOT page (no per-turn dedup object →
        # per-send paging would itself flood §12b)
        assert len(budget_on) == 0

    def test_budget_machinery_fault_fails_closed(self, budget_on):
        # A budget object whose reserve() raises (corrupt counter) → the gate's
        # top-level guard FAILS CLOSED (suppress), never fail-open.
        class _Boom(safe_io._TurnSendBudget):
            def reserve(self, consume):
                raise RuntimeError("counter corrupt")
        safe_io._TURN_SEND_BUDGET.set(_Boom("t", 5, 50))
        assert safe_io.turn_send_budget_gate(CHAT, "x") is False

    def test_fresh_turn_resets_the_budget(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", "1")
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "1")
        monkeypatch.setattr(safe_io, "notify_owner_with_fallback", lambda *a, **k: True)
        # Turn A exhausts its single unit.
        a = safe_io.begin_inbound_turn_send_budget()
        assert safe_io.turn_send_budget_gate(CHAT, "a") is True
        assert safe_io.turn_send_budget_gate(CHAT, "a2") is False
        # Turn B is a FRESH budget — A's exhaustion doesn't bleed in.
        b = safe_io.begin_inbound_turn_send_budget()
        assert b != a
        assert safe_io.turn_send_budget_gate(CHAT, "b") is True


# ── Reviewer decisive-evidence gates (2 / 6 / 7 / 8 + config) ─────────────────

class TestReviewerGates:
    """Dedicated proofs the reviewer enumerated: nested re-entry cannot reset the
    per-turn budget; exactly ONE operator page per exhausted turn; the page neither
    consumes nor re-enters the customer-send budget; the drop is silent to the
    customer; and invalid CONFIG fails toward a bounded enforced cap (never
    unbounded), the config analogue of the missing-state / machinery-fault gates."""

    def test_nested_reentry_within_one_turn_cannot_reset_the_budget(self, budget_on):
        # A nested tool/model re-entry WITHIN one turn must NOT reset/reinit the
        # per-turn contextvar (which would hand a spiraling turn a fresh cap). The
        # budget is set ONCE at the inbound boundary; nested execution runs inside
        # that context (same task) and child tasks copy it, so the ONE counter
        # persists across re-entry.
        async def _turn():
            turn_id = safe_io.begin_inbound_turn_send_budget()
            budget = safe_io._TURN_SEND_BUDGET.get()
            assert safe_io.turn_send_budget_gate(CHAT, "x") is True  # outer 1
            assert safe_io.turn_send_budget_gate(CHAT, "x") is True  # outer 2

            seen = {}

            async def _nested_tool_reentry():
                # tool result re-enters the model: same turn id + the ALREADY-
                # consumed counter — NOT a fresh cap.
                b = safe_io._TURN_SEND_BUDGET.get()
                seen["turn_id"] = b.turn_id
                seen["count_before"] = b.count
                assert safe_io.turn_send_budget_gate(CHAT, "x") is True  # nested 3

            await _nested_tool_reentry()

            async def _child_task():
                # the agent loop's create_task copies the context → SAME object
                return safe_io.turn_send_budget_gate(CHAT, "x")  # child 4

            assert await asyncio.create_task(_child_task()) is True
            return turn_id, budget, seen

        turn_id, budget, seen = asyncio.run(_turn())
        assert seen["turn_id"] == turn_id      # nested re-entry saw the SAME turn
        assert seen["count_before"] == 2       # sees consumed count, not reset to 0
        assert budget.count == 4               # 2 outer + 1 nested + 1 child, ONE shared counter

    def test_draft_relay_bound_cannot_be_reset_through_nested_reentry(self, budget_on):
        # The SEPARATE draft-relay bound lives on the SAME frozen budget object as
        # the finalized counter, so a nested tool/model re-entry within one turn
        # sees the already-consumed draft_count — NEVER a fresh draft cap. (Default
        # draft ceiling = limit x 10 = 50 here, so all 4 drafts admit; the property
        # under test is persistence of draft_count across re-entry, not exhaustion.)
        async def _turn():
            safe_io.begin_inbound_turn_send_budget()
            budget = safe_io._TURN_SEND_BUDGET.get()
            assert safe_io.turn_send_budget_gate(CHAT, "d", reserve_budget=False) is True  # draft 1
            assert safe_io.turn_send_budget_gate(CHAT, "d", reserve_budget=False) is True  # draft 2

            seen = {}

            async def _nested_tool_reentry():
                b = safe_io._TURN_SEND_BUDGET.get()
                seen["draft_count_before"] = b.draft_count
                assert safe_io.turn_send_budget_gate(CHAT, "d", reserve_budget=False) is True  # draft 3

            await _nested_tool_reentry()

            async def _child_task():
                return safe_io.turn_send_budget_gate(CHAT, "d", reserve_budget=False)  # draft 4

            assert await asyncio.create_task(_child_task()) is True
            return budget, seen

        budget, seen = asyncio.run(_turn())
        assert seen["draft_count_before"] == 2   # nested re-entry saw consumed draft budget, not 0
        assert budget.draft_count == 4           # ONE shared draft counter across re-entry
        assert budget.count == 0                 # drafts never touched the finalized counter

    def test_exactly_one_page_per_exhausted_turn_then_resets(self, budget_on):
        # 28 dropped sends in one exhausted turn → operator paged EXACTLY once (not
        # 23×). budget_on spies the §12b page helper.
        safe_io.begin_inbound_turn_send_budget()
        for _ in range(5 + 28):  # 5 admits, 28 drops
            safe_io.turn_send_budget_gate(CHAT, "x")
        assert len(budget_on) == 1
        # A SECOND exhausted turn pages once more — the per-turn `paged` flag resets.
        safe_io.begin_inbound_turn_send_budget()
        for _ in range(5 + 3):
            safe_io.turn_send_budget_gate(CHAT, "x")
        assert len(budget_on) == 2

    def test_paging_does_not_consume_or_reenter_the_send_budget(self, budget_on, monkeypatch):
        # The §12b page must not consume a customer-send reservation nor recurse
        # into the send-budget gate; it routes to the OWNER-ALERT transport only.
        orig_reserve = safe_io._TurnSendBudget.reserve
        calls = {"reserve": 0}

        def _spy_reserve(self, consume):
            calls["reserve"] += 1
            return orig_reserve(self, consume)

        monkeypatch.setattr(safe_io._TurnSendBudget, "reserve", _spy_reserve)
        srcs = []
        monkeypatch.setattr(
            safe_io, "notify_owner_with_fallback",
            lambda *a, **k: srcs.append(k.get("source")) or True,
        )
        safe_io.begin_inbound_turn_send_budget()
        budget = safe_io._TURN_SEND_BUDGET.get()
        for _ in range(6):  # 5 admits + 1 exhausting drop (pages)
            safe_io.turn_send_budget_gate(CHAT, "x")
        # reserve invoked EXACTLY once per gate call — the page did NOT re-enter the
        # gate/reserve (no recursion into the customer-send budget).
        assert calls["reserve"] == 6
        # the page routed to the owner-alert transport, NOT the customer send().
        assert srcs == ["gateway_turn_send_budget"]
        # paging consumed NO customer reservation — the count stays at the cap.
        assert budget.count == 5

    def test_drop_is_silent_to_customer_no_warning(self, budget_on, monkeypatch):
        # After exhaustion the drop is silent to the customer: no warning text is
        # composed or sent; ONLY the metadata suppression row is emitted.
        emit = []
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: emit.append((t, f)))
        safe_io.begin_inbound_turn_send_budget()
        for _ in range(5):
            assert safe_io.turn_send_budget_gate(CHAT, "reply") is True
        for _ in range(3):
            # a drop returns False (suppression) — NEVER a customer warning string
            assert safe_io.turn_send_budget_gate(CHAT, "you hit a limit?") is False
        drops = [f for t, f in emit if t == "send_budget_exhausted"]
        assert len(drops) == 3
        # NO customer-facing composed/sent row for the drops (no "you hit a limit").
        assert not any(t == "front_brain_reply_composed" for t, _ in emit)
        # and NO emitted row carries any message content (metadata-only telemetry).
        assert all(not ({"reply_text", "message", "message_preview"} & set(f)) for _, f in emit)

    def test_invalid_config_fails_to_bounded_default_cap_not_unbounded(self, monkeypatch):
        # Invalid CONFIG must never silently DISABLE the cap. A malformed / negative
        # / zero / empty limit falls back to the bounded DEFAULT cap (never
        # unbounded, never a 0-ceiling-suppress-all — matching the #641 sibling's
        # "never 0" rule). The cap stays enforced: the config analogue of the
        # missing-state + machinery-fault fail-closed gates.
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", "1")
        monkeypatch.setattr(safe_io, "notify_owner_with_fallback", lambda *a, **k: True)
        default = safe_io.DEFAULT_TURN_SEND_BUDGET_LIMIT
        for bad in ("abc", "-1", "0", "", "3.5"):
            monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", bad)
            assert safe_io.turn_send_budget_limit() == default  # bounded fallback
            safe_io.begin_inbound_turn_send_budget()  # fresh budget at the default cap
            admits = sum(
                1 for _ in range(default + 5)
                if safe_io.turn_send_budget_gate(CHAT, "x") is True
            )
            # exactly DEFAULT admitted, the rest suppressed — the cap is ENFORCED,
            # not disabled, under every malformed config value.
            assert admits == default

    def test_hard_max_ceiling_rejects_cap_disabling_value(self, monkeypatch, capsys):
        # ITEM 1: a huge configured limit (which would effectively DISABLE the cap)
        # falls back to the bounded default, same as malformed/<1; a value at/below
        # the hard max is honored; the rejection warns ONCE across repeated calls.
        default = safe_io.DEFAULT_TURN_SEND_BUDGET_LIMIT
        hard_max = safe_io.MAX_TURN_SEND_BUDGET_LIMIT
        # a cap-disabling value → bounded default
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "500000")
        assert safe_io.turn_send_budget_limit() == default
        # a value AT the hard max is honored (not clamped away)
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", str(hard_max))
        assert safe_io.turn_send_budget_limit() == hard_max
        # a value one above the hard max → bounded default
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", str(hard_max + 1))
        assert safe_io.turn_send_budget_limit() == default

    def test_invalid_limit_warning_is_deduplicated(self, monkeypatch, capsys):
        # ITEM 1: the metadata-only warning fires ONCE per offending value across
        # repeated calls (a config typo is a standing condition, not per-call noise),
        # and carries only the bad value + fallback — never message content.
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "500000")
        for _ in range(10):
            safe_io.turn_send_budget_limit()
        err = capsys.readouterr().err
        assert err.count("turn_send_budget_limit invalid config") == 1
        assert "500000" in err
        # a DIFFERENT bad value warns once more (dedup is keyed per value)
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "abc")
        for _ in range(5):
            safe_io.turn_send_budget_limit()
        assert capsys.readouterr().err.count("turn_send_budget_limit invalid config") == 1

    def test_config_read_exception_fails_closed_at_gate(self, monkeypatch):
        # ITEM 3: a config-read EXCEPTION (enabled-flag machinery throws) must
        # SUPPRESS, not passthrough-unlimited. No frozen budget + flag read raises →
        # fail closed + config_failed audit (metadata only).
        emit = []
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: emit.append((t, f)))
        monkeypatch.setattr(safe_io, "notify_owner_with_fallback", lambda *a, **k: True)

        def _boom():
            raise RuntimeError("config store unreadable")

        monkeypatch.setattr(safe_io, "turn_send_budget_enabled", _boom)
        assert safe_io.turn_send_budget_gate(CHAT, "x") is False  # suppressed, NOT None
        rows = [f for t, f in emit if t == "send_budget_exhausted"]
        assert rows and rows[0]["reason"] == "config_failed"
        assert all("message" not in f and "reply_text" not in f for f in rows)

    def test_config_read_exception_at_boundary_freezes_failed_turn(self, monkeypatch):
        # ITEM 3: if the config read throws at begin() (the turn boundary), the turn
        # is frozen CONFIG-FAILED → EVERY send that turn is suppressed, paged once.
        pages = []
        monkeypatch.setattr(
            safe_io, "notify_owner_with_fallback",
            lambda *a, **k: pages.append(k.get("source")) or True,
        )
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: None)

        def _boom():
            raise RuntimeError("config frozen unreadable")

        monkeypatch.setattr(safe_io, "turn_send_budget_enabled", _boom)
        tid = safe_io.begin_inbound_turn_send_budget()
        assert tid  # a (config-failed) turn id was frozen
        assert safe_io.turn_send_budget_gate(CHAT, "a") is False
        assert safe_io.turn_send_budget_gate(CHAT, "b") is False  # every send suppressed
        assert pages == ["gateway_turn_send_budget"]  # paged EXACTLY once

    def test_clean_off_frozen_at_boundary_is_passthrough(self, monkeypatch):
        # ITEM 3 contrast: a CLEAN read of OFF at the boundary is the one passthrough
        # — begin sets no budget and the gate returns None (byte-identical), NOT a
        # suppression.
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", "0")
        assert safe_io.begin_inbound_turn_send_budget() is None
        assert safe_io.turn_send_budget_gate(CHAT, "x") is None


# ── Layer 3: end-to-end through the REAL patch (countable transport) ──────────

_HARNESS_WA = '''"""synthetic adapter for the turn-send-budget end-to-end patch test."""


class BasePlatformAdapter:
    pass


class WhatsAppAdapter(BasePlatformAdapter):
    def __init__(self):
        self.relayed = []

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        if not content or not content.strip():
            return None
        try:
            import aiohttp
            formatted = self.format_message(content)
            self.relayed.append(("send", formatted))
            return {"status": "sent", "id": len(self.relayed)}
        except Exception as e:
            return str(e)

    async def edit_message(self, chat_id, message_id, content, *, finalize=False):
        try:
            import aiohttp
            _url = "http://127.0.0.1:9/edit"  # /edit" relay anchor
            self.relayed.append(("edit", content, finalize))
            return {"status": "edited", "url": _url}
        except Exception as e:
            return str(e)

    def format_message(self, content):
        return content

    def truncate_message(self, formatted, limit):
        return [formatted]
'''


def _load_ph(home: Path):
    os.environ["HERMES_HOME"] = str(home)
    name = f"ph_tsb_{next(_counter)}"
    loader = importlib.machinery.SourceFileLoader(name, str(PATCH_HERMES))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def _build_patched_adapter(tmp_path):
    """Write the synthetic adapter, apply the REAL front-brain patch, exec the
    patched source, and return a fresh WhatsAppAdapter instance whose send()/
    edit_message() carry the sentinel drop-check."""
    wa = tmp_path / "gateway" / "platforms" / "whatsapp.py"
    wa.parent.mkdir(parents=True, exist_ok=True)
    wa.write_text(_HARNESS_WA, encoding="utf-8")
    _load_ph(tmp_path)._patch_whatsapp_py_front_brain_send()
    src = wa.read_text(encoding="utf-8")
    ast.parse(src)  # must stay importable
    ns: dict = {}
    exec(compile(src, str(wa), "exec"), ns)
    return ns["WhatsAppAdapter"]()


@pytest.fixture
def e2e_env(monkeypatch, budget_on):
    """End-to-end env: budget ON (limit 5), a stub aiohttp so the adapter's
    `import aiohttp` succeeds, and the #641 content screen left DISABLED so it is a
    transparent passthrough (only the budget is under test)."""
    monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
    monkeypatch.delenv("FRONT_BRAIN_OUTBOUND_ENFORCE", raising=False)
    return budget_on


class TestEndToEndAdapterSuppression:
    def test_28_sends_hit_transport_exactly_limit_times(self, tmp_path, e2e_env, monkeypatch):
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: None)
        adapter = _build_patched_adapter(tmp_path)

        async def _run():
            safe_io.begin_inbound_turn_send_budget()
            return [await adapter.send(CHAT, "reply") for _ in range(28)]

        results = asyncio.run(_run())
        # transport (relay) hit EXACTLY LIMIT times; the rest returned a no-op None
        assert len(adapter.relayed) == 5
        assert results.count(None) == 23
        assert sum(1 for r in results if r is not None) == 5

    def test_no_transport_side_effect_after_exhaustion(self, tmp_path, e2e_env, monkeypatch):
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: None)
        adapter = _build_patched_adapter(tmp_path)

        async def _run():
            safe_io.begin_inbound_turn_send_budget()
            first5 = [await adapter.send(CHAT, "reply") for _ in range(5)]
            after = [await adapter.send(CHAT, "reply") for _ in range(10)]
            return first5, after

        first5, after = asyncio.run(_run())
        assert all(r is not None for r in first5)   # first LIMIT relayed
        assert all(r is None for r in after)        # every post-exhaustion send suppressed
        assert len(adapter.relayed) == 5            # transport never touched after exhaustion

    def test_dropped_send_is_not_retried(self, tmp_path, e2e_env, monkeypatch):
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: None)
        adapter = _build_patched_adapter(tmp_path)

        async def _run():
            safe_io.begin_inbound_turn_send_budget()
            for _ in range(5):
                await adapter.send(CHAT, "reply")
            return await adapter.send(CHAT, "reply")  # the 6th — dropped

        dropped = asyncio.run(_run())
        # a drop is a single no-op return; NO relay, NO re-enqueue (relay stays 5)
        assert dropped is None
        assert len(adapter.relayed) == 5

    def test_edit_finalize_consumes_drafts_do_not(self, tmp_path, e2e_env, monkeypatch):
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "2")
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: None)
        adapter = _build_patched_adapter(tmp_path)

        async def _run():
            safe_io.begin_inbound_turn_send_budget()
            r = []
            r.append(await adapter.edit_message(CHAT, "m", "d1", finalize=False))  # draft
            r.append(await adapter.edit_message(CHAT, "m", "d2", finalize=False))  # draft
            r.append(await adapter.edit_message(CHAT, "m", "f1", finalize=True))   # consume->1
            r.append(await adapter.edit_message(CHAT, "m", "f2", finalize=True))   # consume->2
            r.append(await adapter.edit_message(CHAT, "m", "f3", finalize=True))   # exhausted->drop
            r.append(await adapter.edit_message(CHAT, "m", "d3", finalize=False))  # drop
            return r

        r = asyncio.run(_run())
        # drafts d1/d2 + finalized f1/f2 relayed (4); f3 + d3 suppressed
        assert len(adapter.relayed) == 4
        assert r[4] is None and r[5] is None

    def test_concurrent_sends_cannot_both_exceed_cap(self, tmp_path, e2e_env, monkeypatch):
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: None)
        adapter = _build_patched_adapter(tmp_path)

        async def _run():
            safe_io.begin_inbound_turn_send_budget()

            async def _one():
                await asyncio.sleep(0)  # yield so all 28 are in-flight before any reserve
                return await adapter.send(CHAT, "reply")

            return await asyncio.gather(*[_one() for _ in range(28)])

        results = asyncio.run(_run())
        # even fully concurrent, the synchronous reserve admits EXACTLY LIMIT
        assert sum(1 for r in results if r is not None) == 5
        assert len(adapter.relayed) == 5

    def test_concurrent_draft_edits_cannot_overshoot_draft_bound_at_transport(
        self, tmp_path, e2e_env, monkeypatch
    ):
        # ITEM 2 parallel proof: the draft-relay ceiling bounds ACTUAL transport
        # calls, not merely logical increments. Launch draft_limit + K concurrent
        # progressive edits in one turn; the synchronous reserve (no await in the
        # critical section) admits EXACTLY draft_limit — the real relay total
        # (adapter.relayed) never overshoots. Finalized cap set high so only the
        # draft ceiling is in play.
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "20")
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_DRAFT_LIMIT", "5")
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: None)
        adapter = _build_patched_adapter(tmp_path)

        async def _run():
            safe_io.begin_inbound_turn_send_budget()

            async def _one():
                await asyncio.sleep(0)  # yield so all 28 are in-flight before any reserve
                return await adapter.edit_message(CHAT, "m", "draft", finalize=False)

            return await asyncio.gather(*[_one() for _ in range(28)])

        results = asyncio.run(_run())
        # ACTUAL transport relays — not logical counters — bounded to EXACTLY the
        # draft ceiling, never draft_limit + overshoot.
        draft_relays = [r for r in adapter.relayed if r[0] == "edit" and r[2] is False]
        assert len(draft_relays) == 5
        assert len(adapter.relayed) == 5
        assert sum(1 for r in results if r is not None) == 5
        assert results.count(None) == 23

    def test_28_progressive_edits_are_bounded_not_28(self, tmp_path, e2e_env, monkeypatch):
        # ITEM 2: progressive draft edits RELAY to transport; without a bound, 28
        # draft edits = 28 relays bypassing the finalized cap. The separate draft
        # ceiling bounds the transport count.
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "5")
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_DRAFT_LIMIT", "10")
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: None)
        adapter = _build_patched_adapter(tmp_path)

        async def _run():
            safe_io.begin_inbound_turn_send_budget()
            return [
                await adapter.edit_message(CHAT, "m", "draft", finalize=False)
                for _ in range(28)
            ]

        results = asyncio.run(_run())
        assert len(adapter.relayed) == 10   # transport BOUNDED to the draft ceiling, not 28
        assert results.count(None) == 18    # the excess drafts were dropped

    def test_mixed_sends_and_edits_combined_bound_holds(self, tmp_path, e2e_env, monkeypatch):
        # ITEM 2: both bounds enforced in one turn — finalized sends capped by
        # `limit`, progressive drafts capped by the separate draft ceiling.
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "3")
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_DRAFT_LIMIT", "4")
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: None)
        adapter = _build_patched_adapter(tmp_path)

        async def _run():
            safe_io.begin_inbound_turn_send_budget()
            # drafts first (finalized cap not yet exhausted) → bounded by draft ceiling
            for _ in range(10):
                await adapter.edit_message(CHAT, "m", "draft", finalize=False)
            # then finalized sends → bounded by the finalized cap
            for _ in range(10):
                await adapter.send(CHAT, "final")

        asyncio.run(_run())
        sends = [r for r in adapter.relayed if r[0] == "send"]
        drafts = [r for r in adapter.relayed if r[0] == "edit" and r[2] is False]
        assert len(sends) == 3    # finalized cap
        assert len(drafts) == 4   # separate draft ceiling
        assert len(adapter.relayed) == 7  # combined transport bounded

    def test_multiple_streams_each_finalize_one_slot_drafts_bounded(self, tmp_path, e2e_env, monkeypatch):
        # ITEM 2: multiple streams (finalize=True sequences) in one turn — each
        # finalize consumes ONE finalized slot (total <= limit); drafts across all
        # streams share the one bounded draft ceiling.
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "3")
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_DRAFT_LIMIT", "6")
        monkeypatch.setattr(safe_io, "_emit_audit_row", lambda t, f: None)
        adapter = _build_patched_adapter(tmp_path)

        async def _run():
            safe_io.begin_inbound_turn_send_budget()
            for _ in range(5):  # 5 streams, each = 3 drafts then 1 finalize
                for _ in range(3):
                    await adapter.edit_message(CHAT, "m", "draft", finalize=False)
                await adapter.edit_message(CHAT, "m", "final", finalize=True)

        asyncio.run(_run())
        finals = [r for r in adapter.relayed if r[0] == "edit" and r[2] is True]
        drafts = [r for r in adapter.relayed if r[0] == "edit" and r[2] is False]
        assert len(finals) == 3   # each finalize = one slot; total finalized <= limit
        assert len(drafts) == 6   # drafts bounded by the separate ceiling

    def test_flag_off_is_byte_identical_all_sends_relay(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
        monkeypatch.delenv("FRONT_BRAIN_OUTBOUND_ENFORCE", raising=False)
        monkeypatch.delenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", raising=False)
        monkeypatch.setattr(safe_io, "notify_owner_with_fallback", lambda *a, **k: True)
        adapter = _build_patched_adapter(tmp_path)

        async def _run():
            # begin is a no-op when off; no context is set
            assert safe_io.begin_inbound_turn_send_budget() is None
            return [await adapter.send(CHAT, "reply") for _ in range(8)]

        results = asyncio.run(_run())
        assert all(r is not None for r in results)   # nothing suppressed
        assert len(adapter.relayed) == 8


# ── run.py patch application (marker shape) ───────────────────────────────────

_RUN_STUB = '''"""stub gateway run.py for turn-send-budget patch tests."""
import os


class _Gateway:
    async def _prepare_inbound_message_text(self, event, source, message_text):
        _is_shared_multi_user = False
        if _is_shared_multi_user and source.user_name:
            message_text = f"[{source.user_name}] {message_text}"
        return message_text
'''


def _write_run(home: Path, content: str) -> Path:
    run = home / "gateway" / "run.py"
    run.parent.mkdir(parents=True, exist_ok=True)
    run.write_text(content, encoding="utf-8")
    return run


def test_run_patch_lands_markers_and_parses(tmp_path):
    run = _write_run(tmp_path, _RUN_STUB)
    ph = _load_ph(tmp_path)
    ph._patch_run_py_turn_send_budget()
    ph._patch_run_py_turn_send_budget()  # idempotent
    src = run.read_text(encoding="utf-8")
    ast.parse(src)
    assert src.count("BEGIN shift-agent-turn-send-budget") == 2  # flag block + inject
    assert "_TURN_SEND_BUDGET_INJECT = (" in src
    assert "begin_inbound_turn_send_budget()" in src
    # flag block sits at module scope after `import os`; inject sits inside the fn
    assert src.index("_TURN_SEND_BUDGET_INJECT = (") < src.index(
        "if _TURN_SEND_BUDGET_INJECT:"
    )


def test_run_patch_failclosed_on_missing_prepare_anchor(tmp_path):
    _write_run(tmp_path, _RUN_STUB.replace("_is_shared_multi_user", "_something_else"))
    with pytest.raises(SystemExit) as e:
        _load_ph(tmp_path)._patch_run_py_turn_send_budget()
    assert e.value.code == 1


# ── schema: send_budget_exhausted variant ─────────────────────────────────────

class TestSendBudgetExhaustedSchema:
    def test_literal_is_a_known_log_entry_type(self):
        assert "send_budget_exhausted" in schemas._KNOWN_LOG_ENTRY_TYPES

    def test_row_validates_through_the_real_adapter(self):
        safe_io._emit_audit_row(
            "send_budget_exhausted",
            {
                "jid": CHAT,
                "turn_id": "abc123",
                "reason": "exhausted",
                "count": 5,
                "limit": 5,
            },
        )
        log_path = Path(safe_io._decisions_log_path())
        rows = [json.loads(ln) for ln in log_path.read_text().splitlines() if ln.strip()]
        assert any(r["type"] == "send_budget_exhausted" for r in rows)

    def test_bad_reason_literal_is_rejected(self):
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            safe_io._emit_audit_row(
                "send_budget_exhausted",
                {"jid": CHAT, "turn_id": "x", "reason": "not_a_reason", "count": 0, "limit": 5},
            )
