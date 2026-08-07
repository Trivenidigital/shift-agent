#!/usr/bin/env python3
"""Round 2: make the front-brain screen FAIL-CLOSED, add a startup refusal, and
put /send-cta on the bridge send queue.

Operator acceptance gate: "screening exception or timeout blocks delivery".
The round-1 port carried the original fail-OPEN wrapper verbatim; this replaces
it with a sentinel-based fail-closed screen that also enforces a timeout and
runs the (synchronous, lock-taking) screen off the event loop.

Exact-match replacement; aborts without writing if any anchor is missing or
ambiguous.
"""
import hashlib
import sys

ROOT = "/usr/local/lib/hermes-agent"
ADAPTER = f"{ROOT}/plugins/platforms/whatsapp/adapter.py"
BRIDGE = f"{ROOT}/scripts/whatsapp-bridge/bridge.js"

edits = []

# ─────────────────────────────────────────────────────────────────────────
# 1a. Replace the fail-OPEN helper with a fail-CLOSED one.
# ─────────────────────────────────────────────────────────────────────────
OLD_HELPER = '''# BEGIN shift-agent-front-brain-send
def _shift_front_brain_screen_outbound(chat_id, content, reserve_budget=True):
    """Route a composed outbound reply through the front-brain gateway-send
    screen and return the SAFE text to relay. `reserve_budget` is False for
    progressive streamed edit drafts (a streamed reply costs ONE budget unit,
    reserved only on the finalized edit). See tools/patch-hermes.py."""
    try:
        import sys as _sys
        if "/opt/shift-agent" not in _sys.path:
            _sys.path.insert(0, "/opt/shift-agent")
        from safe_io import front_brain_screen_gateway_send as _fb_screen
        return _fb_screen(chat_id, content, reserve_budget=reserve_budget)
    except Exception as _e:
        # §12b: screen-disarm must NEVER be silent. Keep fail-open (return the
        # ORIGINAL text == today's un-screened behavior) but emit a structured
        # stderr line so the operator can see the screen was bypassed.
        try:
            import sys as _sys2
            _sys2.stderr.write(
                "front_brain_screen_disarmed reason=%s:%s\\n" % (type(_e).__name__, str(_e)[:120])
            )
        except Exception:
            pass
        return content
# END shift-agent-front-brain-send'''

NEW_HELPER = '''# BEGIN shift-agent-front-brain-send
class _ShiftBlockSend:
    """Fail-closed sentinel for the outbound screen.

    Returned when the screen could NOT be consulted (unimportable, raised, or
    timed out). The send/edit call sites compare by IDENTITY and relay nothing.
    The operator acceptance gate requires a screening exception or timeout to
    BLOCK delivery -- never to fall through with unscreened model output."""
    __slots__ = ()

    def __repr__(self):  # pragma: no cover - debug aid only
        return "<shift-agent BLOCK_SEND>"


_SHIFT_BLOCK_SEND = _ShiftBlockSend()

# Wall-clock ceiling for one screening call. front_brain_screen_gateway_send is
# synchronous and takes file locks, so it runs in a worker thread: a slow screen
# must neither block the gateway event loop nor stall a reply indefinitely.
_SHIFT_FB_TIMEOUT_SEC = float(os.environ.get("SHIFT_FRONT_BRAIN_TIMEOUT_SEC", "20"))


def _shift_front_brain_import():
    """Return safe_io.front_brain_screen_gateway_send; raise if unavailable.

    Called once at adapter construction (startup refusal) and again on every
    send, so a screen that disappears at runtime still fails closed."""
    import sys as _sys
    if "/opt/shift-agent" not in _sys.path:
        _sys.path.insert(0, "/opt/shift-agent")
    from safe_io import front_brain_screen_gateway_send as _fb_screen
    return _fb_screen


async def _shift_front_brain_screen_outbound(chat_id, content, reserve_budget=True):
    """Screen one composed outbound reply. FAIL-CLOSED.

    Returns the SAFE text to relay (possibly a substituted refusal), or the
    _SHIFT_BLOCK_SEND sentinel when the screen could not be consulted. Callers
    MUST compare against the sentinel by identity and abort the send.

    `reserve_budget` is False for progressive streamed edit drafts (a streamed
    reply costs ONE budget unit, reserved only on the finalized edit)."""
    try:
        _fb_screen = _shift_front_brain_import()
        return await asyncio.wait_for(
            asyncio.to_thread(_fb_screen, chat_id, content, reserve_budget=reserve_budget),
            timeout=_SHIFT_FB_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.error(
            "front_brain_screen_blocked reason=timeout timeout_sec=%s chat=%s "
            "-- delivery BLOCKED (fail-closed)",
            _SHIFT_FB_TIMEOUT_SEC, chat_id,
        )
        return _SHIFT_BLOCK_SEND
    except Exception as _e:
        logger.error(
            "front_brain_screen_blocked reason=%s:%s chat=%s "
            "-- delivery BLOCKED (fail-closed)",
            type(_e).__name__, str(_e)[:200], chat_id, exc_info=True,
        )
        return _SHIFT_BLOCK_SEND
# END shift-agent-front-brain-send'''

edits.append((ADAPTER, "fail-closed helper", OLD_HELPER, NEW_HELPER))

# ─────────────────────────────────────────────────────────────────────────
# 1b. Startup refusal when the screen is unimportable.
# ─────────────────────────────────────────────────────────────────────────
OLD_INIT = '''    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.WHATSAPP)'''

NEW_INIT = '''    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.WHATSAPP)
        # BEGIN shift-agent-front-brain-startup-check
        # Refuse to construct the adapter when the outbound screen is
        # unimportable. The screen is fail-closed, so a broken import would
        # otherwise block EVERY reply at runtime and be discovered only as
        # silence; failing here surfaces it as a loud unit-start failure.
        try:
            _shift_front_brain_import()
        except Exception as _fb_exc:
            logger.error(
                "front_brain_screen_unavailable_at_startup reason=%s:%s "
                "-- refusing to start the WhatsApp adapter",
                type(_fb_exc).__name__, str(_fb_exc)[:200], exc_info=True,
            )
            raise RuntimeError(
                "shift-agent: front-brain outbound screen is unimportable "
                "(/opt/shift-agent/safe_io.py); refusing to start the WhatsApp "
                "adapter rather than relay unscreened model output."
            ) from _fb_exc
        # END shift-agent-front-brain-startup-check'''

edits.append((ADAPTER, "startup refusal", OLD_INIT, NEW_INIT))

# ─────────────────────────────────────────────────────────────────────────
# 1c. Call sites: await + honour the sentinel (send AND edit).
# ─────────────────────────────────────────────────────────────────────────
OLD_SEND_CALL = '''            # BEGIN shift-agent-front-brain-send
            content = _shift_front_brain_screen_outbound(chat_id, content)
            # END shift-agent-front-brain-send'''

NEW_SEND_CALL = '''            # BEGIN shift-agent-front-brain-send
            content = await _shift_front_brain_screen_outbound(chat_id, content)
            if content is _SHIFT_BLOCK_SEND:
                return SendResult(success=False, error="front_brain_screen_unavailable")
            # END shift-agent-front-brain-send'''

edits.append((ADAPTER, "send call site", OLD_SEND_CALL, NEW_SEND_CALL))

OLD_EDIT_CALL = '''            # BEGIN shift-agent-front-brain-edit
            content = _shift_front_brain_screen_outbound(chat_id, content, reserve_budget=finalize)
            # END shift-agent-front-brain-edit'''

NEW_EDIT_CALL = '''            # BEGIN shift-agent-front-brain-edit
            content = await _shift_front_brain_screen_outbound(chat_id, content, reserve_budget=finalize)
            if content is _SHIFT_BLOCK_SEND:
                return SendResult(success=False, error="front_brain_screen_unavailable")
            # END shift-agent-front-brain-edit'''

edits.append((ADAPTER, "edit call site", OLD_EDIT_CALL, NEW_EDIT_CALL))

# ─────────────────────────────────────────────────────────────────────────
# 2. CTA on the bridge send queue.
# ─────────────────────────────────────────────────────────────────────────
OLD_CTA = '''    await sock.relayMessage(chatId, waMessage.message, {
      messageId: waMessage.key.id,
      additionalNodes,
    });'''

NEW_CTA = '''    // Serialise through the shared send queue. v0.19.1 added enqueueSend()
    // because overlapping sends on one Baileys socket caused cross-chat
    // contamination (#33360) -- a CTA must never race a normal send.
    await enqueueSend(() => sock.relayMessage(chatId, waMessage.message, {
      messageId: waMessage.key.id,
      additionalNodes,
    }));'''

edits.append((BRIDGE, "cta send queue", OLD_CTA, NEW_CTA))


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main():
    paths = []
    for path, _, _, _ in edits:
        if path not in paths:
            paths.append(path)

    print("=== BEFORE ===")
    before = {p: sha256(p) for p in paths}
    for p in paths:
        print(f"{before[p]}  {p}")

    bodies = {p: open(p, "r", encoding="utf-8", newline="").read() for p in paths}
    applied = []

    for path, label, old, new in edits:
        text = bodies[path]
        if new in text:
            print(f"SKIP (already applied): {label}")
            continue
        n = text.count(old)
        if n != 1:
            sys.stderr.write(
                f"ABORT: anchor {label!r} in {path} occurs {n} times "
                f"(expected 1). No files written.\n"
            )
            return 2
        bodies[path] = text.replace(old, new, 1)
        applied.append(f"{path} :: {label}")

    for p in paths:
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(bodies[p])

    print("\n=== APPLIED ===")
    for a in applied:
        print("  +", a)

    print("\n=== AFTER ===")
    for p in paths:
        print(f"{sha256(p)}  {p}   (was {before[p][:16]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
