#!/usr/bin/env python3
"""Front-brain fail-closed proof — PLUGIN implementation.

Same 14 checks as the core-patch version, re-pointed at
hermes_plugins.shift_agent_policy. The adapter under test is built by the
REGISTERED platform factory after real plugin discovery, so if the override
stops winning these checks exercise the stock adapter and fail.

Run:  SHIFT_FRONT_BRAIN_TIMEOUT_SEC=1 <gateway-python> probe_front_brain.py
"""
import asyncio
import os
import sys
import types

os.environ.setdefault("SHIFT_FRONT_BRAIN_TIMEOUT_SEC", "1")
sys.path.insert(0, "/usr/local/lib/hermes-agent")

from hermes_cli.plugins import discover_plugins  # noqa: E402
from gateway.config import PlatformConfig  # noqa: E402
from gateway.platform_registry import platform_registry  # noqa: E402

discover_plugins(force=True)
ENTRY = platform_registry.get("whatsapp")
assert ENTRY is not None, "no whatsapp platform registered"
POLICY = sys.modules["hermes_plugins.shift_agent_policy.policy"]

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


class FakeResp:
    status = 200

    async def json(self):
        return {"messageId": "MID-1", "success": True}

    async def text(self):
        return ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False


class FakeSession:
    closed = False

    def __init__(self):
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append({"url": url, "json": json})
        return FakeResp()


def make_adapter():
    cfg = PlatformConfig()
    if not hasattr(cfg, "extra") or cfg.extra is None:
        cfg.extra = {}
    a = ENTRY.adapter_factory(cfg)      # the REAL registered factory
    a._running = True
    a._http_session = FakeSession()
    a._bridge_port = 3000

    async def _no_exit():
        return None

    a._check_managed_bridge_exit = _no_exit
    a.format_message = lambda c: c
    a.truncate_message = lambda c, limit: [c]
    a._outgoing_chunk_limit = lambda: 4096
    return a


class ctl:
    _saved = None

    @classmethod
    def install(cls, fn):
        cls._saved = sys.modules.get("safe_io")
        m = types.ModuleType("safe_io")
        m.front_brain_screen_gateway_send = fn
        sys.modules["safe_io"] = m

    @classmethod
    def install_broken(cls):
        cls._saved = sys.modules.get("safe_io")
        sys.modules["safe_io"] = types.ModuleType("safe_io")

    @classmethod
    def restore(cls):
        if cls._saved is not None:
            sys.modules["safe_io"] = cls._saved
        else:
            sys.modules.pop("safe_io", None)


ORIGINAL = "here is the free-form model answer"
SUBSTITUTE = "[refused by policy - safe template]"


def screen_allow(j, m, reserve_budget=True):
    return m


def screen_deny(j, m, reserve_budget=True):
    return SUBSTITUTE


def screen_raise(j, m, reserve_budget=True):
    raise RuntimeError("screen exploded")


def screen_hang(j, m, reserve_budget=True):
    import time
    time.sleep(5)
    return m


async def run_send(fn):
    a = make_adapter()          # built while the REAL safe_io is importable
    ctl.install(fn) if fn else ctl.install_broken()
    try:
        return await a.send("+17329837841", ORIGINAL), a._http_session.posts
    finally:
        ctl.restore()


async def run_edit(fn):
    a = make_adapter()
    ctl.install(fn) if fn else ctl.install_broken()
    try:
        res = await a.edit_message("+17329837841", "MID-0", ORIGINAL, finalize=True)
        return res, a._http_session.posts
    finally:
        ctl.restore()


def body(posts):
    return (posts[0]["json"] or {}).get("message") if posts else None


async def main():
    print("=== SEND path ===")
    r, p = await run_send(screen_allow)
    check("a/send  allowed response reaches bridge unchanged",
          r.success and len(p) == 1 and body(p) == ORIGINAL,
          f"success={r.success} posts={len(p)} body={body(p)!r}")

    r, p = await run_send(screen_deny)
    check("b/send  denied response never reaches bridge (substitute sent)",
          r.success and len(p) == 1 and body(p) == SUBSTITUTE and ORIGINAL not in (body(p) or ""),
          f"body={body(p)!r}")

    r, p = await run_send(screen_raise)
    check("c/send  screening EXCEPTION blocks delivery",
          (not r.success) and len(p) == 0,
          f"success={r.success} error={r.error!r} posts={len(p)}")

    r, p = await run_send(screen_hang)
    check("d/send  screening TIMEOUT blocks delivery",
          (not r.success) and len(p) == 0,
          f"success={r.success} error={r.error!r} posts={len(p)}")

    r, p = await run_send(None)
    check("c2/send unimportable screen blocks delivery",
          (not r.success) and len(p) == 0,
          f"success={r.success} error={r.error!r} posts={len(p)}")

    print("\n=== EDIT path ===")
    r, p = await run_edit(screen_allow)
    check("a/edit  allowed response reaches bridge unchanged",
          r.success and len(p) == 1 and body(p) == ORIGINAL,
          f"success={r.success} posts={len(p)} body={body(p)!r}")

    r, p = await run_edit(screen_deny)
    check("b/edit  denied response never reaches bridge (substitute sent)",
          r.success and len(p) == 1 and body(p) == SUBSTITUTE and ORIGINAL not in (body(p) or ""),
          f"body={body(p)!r}")

    r, p = await run_edit(screen_raise)
    check("c/edit  screening EXCEPTION blocks delivery",
          (not r.success) and len(p) == 0,
          f"success={r.success} error={r.error!r} posts={len(p)}")

    r, p = await run_edit(screen_hang)
    check("d/edit  screening TIMEOUT blocks delivery",
          (not r.success) and len(p) == 0,
          f"success={r.success} error={r.error!r} posts={len(p)}")

    r, p = await run_edit(None)
    check("c2/edit unimportable screen blocks delivery",
          (not r.success) and len(p) == 0,
          f"success={r.success} error={r.error!r} posts={len(p)}")

    print("\n=== sentinel + wrapper contract ===")
    ctl.install(screen_raise)
    try:
        check("wrapper returns BLOCK sentinel on exception",
              await POLICY.screen_outbound("+1732", "x") is POLICY.BLOCK_SEND)
    finally:
        ctl.restore()
    ctl.install(screen_hang)
    try:
        check("wrapper returns BLOCK sentinel on timeout",
              await POLICY.screen_outbound("+1732", "x") is POLICY.BLOCK_SEND)
    finally:
        ctl.restore()

    print("\n=== startup refusal (adapter construction) ===")
    ctl.install_broken()
    try:
        raised = None
        try:
            make_adapter()
        except Exception as e:
            raised = f"{type(e).__name__}: {e}"
        check("f  unimportable screen REFUSES adapter construction",
              raised is not None, (raised or "no exception")[:110])
    finally:
        ctl.restore()

    got_past = True
    try:
        make_adapter()
    except RuntimeError as e:
        if "refusing" in str(e).lower():
            got_past = False
    except Exception:
        pass
    check("f2 real safe_io present -> construction passes the screen gate", got_past)

    print()
    failed = [n for n, ok in RESULTS if not ok]
    print(f"TOTAL {len(RESULTS)} checks, {len(RESULTS) - len(failed)} pass, {len(failed)} fail")
    if failed:
        for n in failed:
            print("  FAILED:", n)
        return 1
    print("ALL FRONT-BRAIN FAIL-CLOSED CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
