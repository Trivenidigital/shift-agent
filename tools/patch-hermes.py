#!/usr/bin/env python3
"""Idempotent Hermes patcher for the sender-id-context feature.

Applies (or re-verifies) the patches in:
  - /root/.hermes/hermes-agent/gateway/platforms/whatsapp.py
  - /root/.hermes/hermes-agent/gateway/run.py
  - /root/.hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js

Idempotent: if BEGIN/END markers are already present, we skip and just
verify. If the anchor symbols are missing, exit 1 (fail-closed) so a
Hermes upgrade can't silently break the integration.

This is the implementation of §9 in 02-DESIGN-v2.md, with all the
Hermes-side code blocks documented there.
"""
from __future__ import annotations
import ast
import os
import re
import sys
from pathlib import Path


class PatchError(Exception):
    """A required anchor / expected prior state was not found. Raised by the pure
    text->text transforms so the all-or-nothing orchestrator can abort BEFORE any
    target file is written (fail-closed: a Hermes upgrade that moved an anchor
    leaves every target byte-identical rather than half-patched)."""


H = Path(os.environ.get("HERMES_HOME", "/root/.hermes/hermes-agent"))
RUN = H / "gateway" / "run.py"
WA = H / "gateway" / "platforms" / "whatsapp.py"
BR = H / "scripts" / "whatsapp-bridge" / "bridge.js"

MARK_BEGIN = "BEGIN shift-agent-sender-id"
MARK_END = "END shift-agent-sender-id"
CTA_MARK_BEGIN = "BEGIN shift-agent-cta-buttons"
BUTTON_RESPONSE_MARK_BEGIN = "BEGIN shift-agent-button-response-body"

# ─── Front-brain Phase-1 outbound-send screen (2026-07-12) ─────────────────
# LLM free-form replies exit via WhatsAppAdapter.send() (POST to the bridge
# /send endpoint) — NOT via safe_io.bridge_post — so the P0-3a outbound
# chokepoint never sees them. This patch wraps the composed `content` at the top
# of send(), immediately before format/chunk/relay, routing it through
# safe_io.front_brain_screen_gateway_send (per-chat/day budget + free-form
# enforcement + latency bound; DORMANT and byte-identical unless the chat is
# admitted by FRONT_BRAIN_OUTBOUND_ENFORCE + its allowlist).
#
# Authored/verified against Hermes checkout HEAD
#   1e71b7180e5b4e84905b9a3086cf9cecca139562  (== the pinned baseline commit)
# and unpatched gateway/platforms/whatsapp.py sha256
#   d59426d5cdb18a09bed91b07521cd6d7e190a59a9222e2ed235d6a1b23513663
# Stable anchors (symbol names, not line numbers): the module-level helper is
# inserted before `class WhatsAppAdapter(BasePlatformAdapter):`; the one-line
# call is injected immediately before `formatted = self.format_message(content)`
# inside `async def send(`. check-shift-agent-patch.sh fail-closes on both
# markers + anchor proximity so a Hermes upgrade that moves/removes them cannot
# silently ship un-screened LLM replies.
FB_SEND_MARK_BEGIN = "BEGIN shift-agent-front-brain-send"
FB_SEND_MARK_END = "END shift-agent-front-brain-send"
# edit_message() (whatsapp.py:996) is the SECOND text-egress path: Hermes core
# stream_consumer.py delivers streamed drafts AND the finalized answer
# (finalize=True) via adapter.edit_message. Config-proof coverage requires
# screening it too (the live box has split streaming config). Same helper, same
# fail-open-loudly semantics; a distinct marker so the deploy gate checks it
# independently. Progressive drafts screen but do NOT reserve budget (finalize
# gates the single per-reply budget unit).
FB_EDIT_MARK_BEGIN = "BEGIN shift-agent-front-brain-edit"
FB_EDIT_MARK_END = "END shift-agent-front-brain-edit"

# ─── Per-INBOUND-TURN outbound-send budget (2026-07-22, marker split 2026-07-24) ─
# The #641 gateway throttle (safe_io.front_brain_screen_gateway_send) can only
# SUBSTITUTE a safe template + page — never SUPPRESS a send — because its contract
# is `-> str` (always returns a sendable string) and the send wrapper below
# unconditionally relays that string. This companion adds the TRUE volume cap the
# reviewer ruled: a per-inbound-turn send-COUNT cap enforced WHERE sends can be
# suppressed. run.py sets a fresh per-turn budget in a safe_io ContextVar at the
# inbound-turn boundary (the _prepare_inbound_message_text inject, same site as
# the sender-id block, its OWN marker below); the adapter seam consults
# safe_io.turn_send_budget_gate and, once the turn is exhausted, yields the module-
# level `_SHIFT_DROP_SEND` sentinel so send()/edit_message() relay NOTHING and
# return a well-formed no-op. Fail-CLOSED (the opposite of #641's fail-open): a
# missing/corrupt turn context suppresses. Default-OFF
# (GATEWAY_TURN_SEND_BUDGET_ENABLED) → byte-identical.
#
# INSTALLER-CORRECTNESS (2026-07-24): the adapter-side pieces (the _SHIFT_DROP_SEND
# sentinel + budget screen, the send() drop-check, the edit_message() drop-check)
# each carry their OWN marker, INDEPENDENT of shift-agent-front-brain-send. The
# earlier #643 shape bundled them inside the front-brain-send marker block, so on a
# tree that ALREADY carried the front-brain screen (= production) the "already
# patched" skip suppressed the whole block and the volume cap never installed. Each
# piece is now idempotent + fail-closed on ITS OWN marker so it installs regardless
# of whether front-brain-send is present. The run.py boundary keeps its existing
# shift-agent-turn-send-budget marker (already independent).
TURN_BUDGET_MARK_BEGIN = "BEGIN shift-agent-turn-send-budget"
TURN_BUDGET_MARK_END = "END shift-agent-turn-send-budget"
# Adapter-side markers (whatsapp.py) — each independently installable + verified.
TURN_BUDGET_SENTINEL_MARK_BEGIN = "BEGIN shift-agent-turn-budget-sentinel"
TURN_BUDGET_SENTINEL_MARK_END = "END shift-agent-turn-budget-sentinel"
TURN_BUDGET_SEND_DROP_MARK_BEGIN = "BEGIN shift-agent-turn-budget-send-drop"
TURN_BUDGET_SEND_DROP_MARK_END = "END shift-agent-turn-budget-send-drop"
TURN_BUDGET_EDIT_DROP_MARK_BEGIN = "BEGIN shift-agent-turn-budget-edit-drop"
TURN_BUDGET_EDIT_DROP_MARK_END = "END shift-agent-turn-budget-edit-drop"


# ─── Patch payloads (Python files) ─────────────────────────────────────

WHATSAPP_PY_HELPERS = '''
# BEGIN shift-agent-sender-id
import re as _shift_re
import unicodedata as _shift_unicodedata

_SHIFT_VALID_LID = _shift_re.compile(r"^\\d{6,20}@lid$")
_SHIFT_VALID_PJID = _shift_re.compile(r"^\\d{6,20}@s\\.whatsapp\\.net$")
_SHIFT_VALID_E164 = _shift_re.compile(r"^\\+\\d{10,15}$")
_SHIFT_INVISIBLES = _shift_re.compile(
    "[\\u200b\\u200c\\u200d\\u200e\\u200f"
    "\\u202a\\u202b\\u202c\\u202d\\u202e"
    "\\u2060\\u2061\\u2062\\u2063\\u2064\\u2065\\u2066\\u2067\\u2068\\u2069"
    "\\ufeff]"
)
_SHIFT_PRE_BLOCK = _shift_re.compile(r"\\[shift-agent-sender", flags=_shift_re.IGNORECASE)


def _resolve_sender_context(event: dict) -> dict:
    """Pure helper. See src/sender_context.py for the canonical implementation
    and tests/test_sender_context.py for behaviour spec."""
    out = {"platform": "whatsapp", "phone": None, "lid": None,
           "fromMe": bool(event.get("fromMe", False)), "chat_id": None}
    sid = event.get("senderId") or ""
    sid_clean = _shift_re.sub(r":\\d+(?=@)", "", sid)
    if _SHIFT_VALID_PJID.match(sid_clean):
        out["phone"] = "+" + sid_clean.split("@")[0]
    elif _SHIFT_VALID_LID.match(sid_clean):
        out["lid"] = sid_clean
    if out["phone"] is None:
        sp = event.get("senderPhone") or ""
        if _SHIFT_VALID_E164.match(sp):
            out["phone"] = sp
    if out["lid"] is None:
        sl = event.get("senderLid") or ""
        if _SHIFT_VALID_LID.match(sl):
            out["lid"] = sl
    cid = event.get("chatId") or ""
    cid_clean = _shift_re.sub(r":\\d+(?=@)", "", cid)
    if _SHIFT_VALID_LID.match(cid_clean) or _SHIFT_VALID_PJID.match(cid_clean):
        out["chat_id"] = cid_clean
    return out


def _q_quoted(v):
    if v is None:
        return "null"
    return '"' + str(v).replace("\\\\", "\\\\\\\\").replace('"', '\\\\"') + '"'


def _render_sender_context_block(ctx: dict) -> str:
    return (
        f'[shift-agent-sender v=1 platform={ctx["platform"]} '
        f'phone={_q_quoted(ctx["phone"])} lid={_q_quoted(ctx["lid"])} '
        f'fromMe={"true" if ctx["fromMe"] else "false"} '
        f'chat_id={_q_quoted(ctx["chat_id"])}]'
    )


def _sanitize_user_body(body: str) -> str:
    if not body:
        return body
    body = _shift_unicodedata.normalize("NFKC", body)
    body = _SHIFT_INVISIBLES.sub("", body)
    return _SHIFT_PRE_BLOCK.sub("[shift-agent-sender-stripped", body)
# END shift-agent-sender-id
'''

RUN_PY_FLAG_BLOCK = '''
# BEGIN shift-agent-sender-id
_INJECT_SENDER_CONTEXT = (
    os.environ.get("HERMES_INJECT_SENDER_CONTEXT", "0") == "1"
)
# END shift-agent-sender-id
'''

# In _prepare_inbound_message_text, prepend the sender block BEFORE the
# existing `[{user_name}]` prefix so the sender block is unconditionally line 1.
RUN_PY_INJECT_BLOCK = '''
        # BEGIN shift-agent-sender-id
        if _INJECT_SENDER_CONTEXT and isinstance(getattr(event, "raw_message", None), dict):
            try:
                from gateway.platforms.whatsapp import (
                    _resolve_sender_context, _render_sender_context_block,
                    _sanitize_user_body,
                )
                _ctx = _resolve_sender_context(event.raw_message)
                _block = _render_sender_context_block(_ctx)
                message_text = f"{_block}\\n{_sanitize_user_body(message_text)}"
            except Exception as _e:
                logger.warning("shift-agent: sender context inject failed: %s", _e)
                # Fail closed — no partial block, no spoofing window.
        # END shift-agent-sender-id
'''

# Module-level flag for the per-inbound-turn send budget. Default OFF → the
# inject block below is skipped entirely (byte-identical). Distinct env var from
# the #641 gateway throttle.
RUN_PY_TURN_BUDGET_FLAG_BLOCK = '''
# BEGIN shift-agent-turn-send-budget
_TURN_SEND_BUDGET_INJECT = (
    os.environ.get("GATEWAY_TURN_SEND_BUDGET_ENABLED", "0") == "1"
)
# END shift-agent-turn-send-budget
'''

# In _prepare_inbound_message_text — the once-per-inbound-message prep that is
# awaited inside the message-handler task, upstream of the agent loop — set a
# FRESH per-turn send budget in the safe_io ContextVar. Every send()/edit_message()
# of this inbound turn shares that one counter (the ContextVar carries the same
# object by reference into child tasks the agent loop spawns); a fresh inbound turn
# calls begin_* again and resets it. Independent of the sender-id flag; guarded by
# _TURN_SEND_BUDGET_INJECT so the whole block is byte-identical when the feature is
# OFF. If begin fails, the turn has NO budget context → the adapter wrapper
# FAILS CLOSED (suppresses) rather than flooding.
RUN_PY_TURN_BUDGET_INJECT_BLOCK = '''
        # BEGIN shift-agent-turn-send-budget
        if _TURN_SEND_BUDGET_INJECT:
            try:
                import sys as _shift_sys
                if "/opt/shift-agent" not in _shift_sys.path:
                    _shift_sys.path.insert(0, "/opt/shift-agent")
                import safe_io as _shift_safe_io
                _shift_safe_io.begin_inbound_turn_send_budget()
            except Exception as _e:
                logger.warning("shift-agent: turn-send-budget begin failed: %s", _e)
                # Fail toward NO context: a send with no per-turn budget context
                # FAILS CLOSED (suppressed) in the adapter wrapper — never floods.
        # END shift-agent-turn-send-budget
'''


# ─── Patch payloads (per-inbound-turn send-budget adapter seam) ────────────

# Module-level sentinel + budget screen, inserted before `class WhatsAppAdapter(...)`
# under its OWN marker (shift-agent-turn-budget-sentinel) so it installs even when
# the front-brain-send screen is already present. `_shift_turn_send_budget_screen`
# is the TRUE volume cap: it returns the `_SHIFT_DROP_SEND` sentinel when the per-
# inbound-turn budget is exhausted (or the turn context is missing/corrupt while
# the feature is enabled — FAIL-CLOSED, the deliberate opposite of the #641 content
# screen's fail-open). It NEVER raises; a total safe_io-unavailable deploy fault
# passes content through, because the default-OFF budget feature cannot coherently
# be "on" in that state (the front-brain screen's own §12b fail-open-loudly covers
# deploy-fault visibility). `safe_io.turn_send_budget_gate` is consulted via getattr
# so an older/partial safe_io without the gate is byte-identical (budget absent).
WHATSAPP_TURN_BUDGET_SENTINEL = '''# BEGIN shift-agent-turn-budget-sentinel
class _ShiftDropSend:
    """Not-send sentinel (per-inbound-turn send budget). When the budget screen
    returns this singleton, send()/edit_message() relay NOTHING and return a well-
    formed no-op result — a TRUE suppression the #641 content screen (contractually
    `-> str`) cannot express. Identity-compared, never sent. Defined under its OWN
    marker so it installs independently of the front-brain-send screen."""
    __slots__ = ()

    def __repr__(self):  # pragma: no cover - debug aid only
        return "<shift-agent DROP_SEND>"


_SHIFT_DROP_SEND = _ShiftDropSend()


def _shift_turn_send_budget_screen(chat_id, content, reserve_budget=True):
    """Per-INBOUND-TURN send budget (default-OFF, GATEWAY_TURN_SEND_BUDGET_*) — a
    TRUE volume cap enforced WHERE sends can be suppressed. `safe_io.turn_send_budget_gate`
    returns None (feature off → passthrough), True (admitted), or False (SUPPRESS →
    return the `_SHIFT_DROP_SEND` sentinel; the caller relays NOTHING). FAIL-CLOSED
    when enabled: the gate itself suppresses on a missing/corrupt turn context. This
    wrapper only passes content through on a TOTAL safe_io-unavailable deploy fault,
    where the default-OFF budget feature cannot coherently be "on". Consulted via
    getattr so an older/partial safe_io without the gate is byte-identical (budget
    absent). `reserve_budget` is False for progressive streamed edit drafts (a
    streamed reply costs ONE budget unit, reserved only on the finalized edit) and
    is threaded through to the gate."""
    try:
        import sys as _sys
        if "/opt/shift-agent" not in _sys.path:
            _sys.path.insert(0, "/opt/shift-agent")
        import safe_io as _sio
        _gate = getattr(_sio, "turn_send_budget_gate", None)
        if _gate is not None and _gate(chat_id, content, reserve_budget=reserve_budget) is False:
            return _SHIFT_DROP_SEND
    except Exception:
        # Total safe_io-unavailable deploy fault: pass content through. The default-
        # OFF budget feature cannot coherently be "on" here, and the front-brain
        # content screen's own §12b fail-open-loudly (below) emits the disarm line.
        pass
    return content
# END shift-agent-turn-budget-sentinel'''

# Module-level content screen, inserted before `class WhatsAppAdapter(...)`. Lazily
# imports safe_io from /opt/shift-agent (the flat platform install) and returns
# the SAFE text to relay. Fail-OPEN to the ORIGINAL content if safe_io is
# unimportable: LLM replies exit through send() UN-screened TODAY, so failing
# open here is exactly today's behavior — it does NOT regress safety, it only
# means the NEW control is absent, which the tier's default-off posture already
# assumes. It must never crash the send path. Contractually `-> str`: it can
# SUBSTITUTE a safe template but never SUPPRESS a send (that is the turn-budget
# screen's job, applied BEFORE this call). The real gate lives INSIDE safe_io
# (FRONT_BRAIN_OUTBOUND_ENFORCE + allowlist), so this wrapper is byte-identical
# when the tier is off.
WHATSAPP_FB_SEND_HELPER = '''# BEGIN shift-agent-front-brain-send
def _shift_front_brain_screen_outbound(chat_id, content, reserve_budget=True):
    """Screen a composed outbound reply through the #641 content screen
    (front_brain_screen_gateway_send) and return the SAFE text to relay. It can
    substitute a safe template but is contractually `-> str` — never a suppression;
    the per-inbound-turn budget screen (applied BEFORE this call) owns suppression.
    `reserve_budget` is threaded through for progressive streamed edit drafts."""
    try:
        import sys as _sys
        if "/opt/shift-agent" not in _sys.path:
            _sys.path.insert(0, "/opt/shift-agent")
        import safe_io as _sio
        return _sio.front_brain_screen_gateway_send(chat_id, content, reserve_budget=reserve_budget)
    except Exception as _e:
        # §12b: screen-disarm must NEVER be silent. Fail-open (return the ORIGINAL
        # text == today's un-screened behavior) so a deploy-integrity fault never
        # crashes the send path; emit a structured stderr line so the operator can
        # see the screen was bypassed.
        try:
            import sys as _sys2
            _sys2.stderr.write(
                "front_brain_screen_disarmed reason=%s:%s\\n" % (type(_e).__name__, str(_e)[:120])
            )
        except Exception:
            pass
        return content
# END shift-agent-front-brain-send'''

# Content-screen call injected at the top of send(), immediately before the
# format/chunk/relay begins (anchor: the format_message assignment). 12-space
# indent = method-try-block body. The turn-budget send-drop (below) is inserted
# BEFORE this block, so budget suppression is checked first.
WHATSAPP_FB_SEND_INJECT = '''            # BEGIN shift-agent-front-brain-send
            content = _shift_front_brain_screen_outbound(chat_id, content)
            # END shift-agent-front-brain-send
'''

# Turn-budget drop-check for send(), injected IMMEDIATELY BEFORE the front-brain-
# send block (so the volume cap is consulted before the content screen). Its OWN
# marker (shift-agent-turn-budget-send-drop) — independent of front-brain-send. When
# the per-turn budget is exhausted the screen returns `_SHIFT_DROP_SEND` and send()
# returns None — a well-formed not-sent result (send()'s own empty-content guard
# upstream already returns None, so the caller tolerates it) — relaying NOTHING, so
# the transport is never hit.
WHATSAPP_TURN_BUDGET_SEND_DROP = '''            # BEGIN shift-agent-turn-budget-send-drop
            content = _shift_turn_send_budget_screen(chat_id, content)
            if content is _SHIFT_DROP_SEND:
                return None
            # END shift-agent-turn-budget-send-drop
'''

# Content-screen call injected at the top of edit_message(), immediately after
# `import aiohttp` and before the bridge /edit relay. Screens EVERY edit
# (progressive drafts are customer-visible); reserve_budget=finalize so a whole
# streamed reply costs ONE budget unit (reserved on the finalized edit only).
WHATSAPP_FB_EDIT_INJECT = '''            # BEGIN shift-agent-front-brain-edit
            content = _shift_front_brain_screen_outbound(chat_id, content, reserve_budget=finalize)
            # END shift-agent-front-brain-edit
'''

# Turn-budget drop-check for edit_message(), injected IMMEDIATELY BEFORE the front-
# brain-edit block. Its OWN marker (shift-agent-turn-budget-edit-drop). reserve_budget
# =finalize so a whole streamed reply costs ONE budget unit. On an exhausted turn the
# screen returns `_SHIFT_DROP_SEND` and edit_message() returns None (relays NOTHING)
# — the same TRUE suppression as send().
WHATSAPP_TURN_BUDGET_EDIT_DROP = '''            # BEGIN shift-agent-turn-budget-edit-drop
            content = _shift_turn_send_budget_screen(chat_id, content, reserve_budget=finalize)
            if content is _SHIFT_DROP_SEND:
                return None
            # END shift-agent-turn-budget-edit-drop
'''


# ─── Patch payload (bridge.js) ─────────────────────────────────────────

BRIDGE_JS_HELPERS = '''
// BEGIN shift-agent-sender-id
const _SHIFT_LID = /^\\d{6,20}@lid$/;
const _SHIFT_PJID = /^\\d{6,20}@s\\.whatsapp\\.net$/;

function _shiftResolveSender(msg, sock, lidToPhoneMap) {
  const fromMe = !!(msg && msg.key && msg.key.fromMe);
  let senderId = '';
  if (fromMe && sock && sock.user && sock.user.id) {
    senderId = sock.user.id;
  } else if (msg && msg.key) {
    senderId = msg.key.participant || msg.key.remoteJid || '';
  }
  // Strip baileys device suffix ":N"
  senderId = senderId.replace(/:\\d+(?=@)/, '');
  let senderPhone = null, senderLid = null;
  if (_SHIFT_PJID.test(senderId)) {
    senderPhone = '+' + senderId.split('@')[0];
  } else if (_SHIFT_LID.test(senderId)) {
    senderLid = senderId;
    if (lidToPhoneMap && lidToPhoneMap[senderId]) {
      const m = lidToPhoneMap[senderId].replace(/:\\d+(?=@)/, '');
      if (_SHIFT_PJID.test(m)) senderPhone = '+' + m.split('@')[0];
    }
  }
  return { senderId, senderPhone, senderLid, fromMe };
}

const _SHIFT_LID_CACHE_PATH = '/opt/shift-agent/state/lid-cache.json';
const _SHIFT_LID_CACHE_ENABLED = ['1','true','yes','on'].includes(
  String(process.env.WHATSAPP_LID_CACHE_WRITE || '').toLowerCase()
);
let _shiftLidCacheChain = Promise.resolve();

async function _shiftWriteLidCacheImpl(phone, lid) {
  if (!_SHIFT_LID_CACHE_ENABLED || !phone || !lid) return;
  const fs = (await import('fs')).promises;
  let cur = { schema_version: 1, pairs: [] };
  try {
    const raw = await fs.readFile(_SHIFT_LID_CACHE_PATH, 'utf-8');
    if (raw && raw.trim()) {
      const parsed = JSON.parse(raw);
      if (parsed.schema_version === 1) cur = parsed;
    }
  } catch (e) { /* ENOENT or parse error → start fresh */ }
  if (cur.pairs.some(p => p.phone === phone && p.lid === lid)) return;
  cur.pairs = cur.pairs.filter(p => p.phone !== phone);
  cur.pairs.push({ phone, lid, learned_ts: new Date().toISOString() });
  const tmp = _SHIFT_LID_CACHE_PATH + '.tmp-' + process.pid + '-' + Date.now();
  const fh = await fs.open(tmp, 'w');
  try {
    await fh.writeFile(JSON.stringify(cur, null, 2));
    await fh.sync();
  } finally {
    await fh.close();
  }
  await fs.rename(tmp, _SHIFT_LID_CACHE_PATH);
}

function _shiftWriteLidCacheEntry(phone, lid) {
  _shiftLidCacheChain = _shiftLidCacheChain.then(
    () => _shiftWriteLidCacheImpl(phone, lid).catch(e => console.error('[lid-cache] write failed:', e))
  );
  return _shiftLidCacheChain;
}
// END shift-agent-sender-id
'''


BRIDGE_CTA_ENDPOINT = '''
// BEGIN shift-agent-cta-buttons
// Send an interactive quick-reply message. Used by Flyer Studio outreach so
// customers can tap "Start Free Trial" or "Act Now" and send the matching
// intent back into the WhatsApp chat without opening a URL dialog.
function _shiftCtaPrivacyModeTs() {
  return String(Math.floor(Date.now() / 1000) - 77980457);
}

function _shiftCtaBizNode() {
  return {
    tag: 'biz',
    attrs: {
      actual_actors: '2',
      host_storage: '2',
      privacy_mode_ts: _shiftCtaPrivacyModeTs(),
    },
    content: [
      {
        tag: 'interactive',
        attrs: { type: 'native_flow', v: '1' },
        content: [{ tag: 'native_flow', attrs: { v: '9', name: 'mixed' } }],
      },
      { tag: 'quality_control', attrs: { source_type: 'third_party' } },
    ],
  };
}

app.post('/send-cta', async (req, res) => {
  if (!sock || connectionState !== 'connected') {
    return res.status(503).json({ error: 'Not connected to WhatsApp' });
  }

  const { chatId, body, buttons, footer } = req.body;
  if (!chatId || !body || !Array.isArray(buttons) || buttons.length === 0) {
    return res.status(400).json({ error: 'chatId, body, and at least one button are required' });
  }

  const nativeFlowButtons = buttons.slice(0, 3).map((button) => ({
    name: 'quick_reply',
    buttonParamsJson: JSON.stringify({
      display_text: String(button.label || '').slice(0, 60),
      id: String(button.message || '').slice(0, 300),
    }),
  }));
  if (nativeFlowButtons.some((button) => {
    const params = JSON.parse(button.buttonParamsJson);
    return !params.display_text || !params.id;
  })) {
    return res.status(400).json({ error: 'each CTA button requires label and message' });
  }

  try {
    const interactiveMessage = proto.Message.InteractiveMessage.create({
      body: proto.Message.InteractiveMessage.Body.create({ text: formatOutgoingMessage(body) }),
      footer: proto.Message.InteractiveMessage.Footer.create({ text: footer || 'Flyer Studio' }),
      nativeFlowMessage: proto.Message.InteractiveMessage.NativeFlowMessage.create({
        buttons: nativeFlowButtons.map((button) =>
          proto.Message.InteractiveMessage.NativeFlowMessage.NativeFlowButton.create(button),
        ),
        messageParamsJson: '{}',
        messageVersion: 1,
      }),
    });

    const userJid = normalizeWhatsAppId(sock.user?.id || '');
    const waMessage = generateWAMessageFromContent(chatId, { interactiveMessage }, { userJid });
    const botNode = { tag: 'bot', attrs: { biz_bot: '1' } };
    const additionalNodes = chatId.endsWith('@g.us')
      ? [_shiftCtaBizNode()]
      : [botNode, _shiftCtaBizNode()];

    await sock.relayMessage(chatId, waMessage.message, {
      messageId: waMessage.key.id,
      additionalNodes,
    });

    if (waMessage?.key?.id) {
      recentlySentIds.add(waMessage.key.id);
      if (recentlySentIds.size > MAX_RECENT_IDS) {
        recentlySentIds.delete(recentlySentIds.values().next().value);
      }
    }
    res.json({ success: true, messageId: waMessage?.key?.id });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});
// END shift-agent-cta-buttons
'''


BRIDGE_BUTTON_RESPONSE_EXTRACT = '''
      // BEGIN shift-agent-button-response-body
      } else if (messageContent.buttonsResponseMessage?.selectedButtonId || messageContent.buttonsResponseMessage?.selectedDisplayText) {
        body = messageContent.buttonsResponseMessage.selectedButtonId
          || messageContent.buttonsResponseMessage.selectedDisplayText
          || '';
      } else if (messageContent.templateButtonReplyMessage?.selectedId || messageContent.templateButtonReplyMessage?.selectedDisplayText) {
        body = messageContent.templateButtonReplyMessage.selectedId
          || messageContent.templateButtonReplyMessage.selectedDisplayText
          || '';
      } else if (messageContent.interactiveResponseMessage?.nativeFlowResponseMessage?.paramsJson) {
        try {
          const params = JSON.parse(messageContent.interactiveResponseMessage.nativeFlowResponseMessage.paramsJson || '{}');
          body = String(params.id || params.response || params.display_text || '').trim();
        } catch (err) {
          body = messageContent.interactiveResponseMessage?.body?.text || '';
        }
      // END shift-agent-button-response-body
'''


def _has_marker(p: Path) -> bool:
    return p.exists() and (MARK_BEGIN in p.read_text(encoding="utf-8"))


# ─── Pure text->text transforms (no I/O; raise PatchError, never sys.exit) ──────
#
# Each transform is idempotent on ITS OWN marker and fail-closed (raises
# PatchError) on a missing anchor. The all-or-nothing orchestrator composes them,
# validates the in-memory result, then writes every target atomically — so a
# missing anchor or a mid-apply fault leaves EVERY target byte-identical.

def _apply_wa_sender_id(text: str) -> str:
    """Insert the sender-id helpers before the first `class ...Platform...:`."""
    if MARK_BEGIN in text:
        return text
    anchor = re.search(r"^class .*Platform.*:", text, re.MULTILINE)
    if not anchor:
        raise PatchError(f"cannot locate class anchor in {WA}")
    insert_at = anchor.start()
    return text[:insert_at] + WHATSAPP_PY_HELPERS + "\n\n" + text[insert_at:]


def _apply_wa_front_brain(text: str) -> str:
    """Insert the front-brain CONTENT screen (module-level helper + send() call +
    edit_message() call) — NO suppression logic; that is the turn-budget adapter's
    job. Two idempotent edits keyed on their own markers (shift-agent-front-brain-
    send / -edit), fail-closed on a missing anchor."""
    # 1. Module-level content-screen helper + send() screen call.
    if FB_SEND_MARK_BEGIN not in text:
        class_anchor = "class WhatsAppAdapter(BasePlatformAdapter):"
        if class_anchor not in text:
            raise PatchError(
                f"cannot locate '{class_anchor}' in {WA} — Hermes may have renamed "
                f"the adapter class."
            )
        text = text.replace(
            class_anchor, WHATSAPP_FB_SEND_HELPER + "\n\n\n" + class_anchor, 1
        )
        send_def_re = re.compile(r"^    async def send\(", re.MULTILINE)
        send_m = send_def_re.search(text)
        if not send_m:
            raise PatchError(f"cannot find `async def send(` def in {WA}")
        fmt_re = re.compile(
            r"^            formatted = self\.format_message\(content\)", re.MULTILINE
        )
        fmt_m = fmt_re.search(text, pos=send_m.end())
        if not fmt_m:
            raise PatchError(
                f"`formatted = self.format_message(content)` not found inside send() "
                f"in {WA} — Hermes may have refactored the send path."
            )
        line_diff = text.count("\n", send_m.start(), fmt_m.start())
        if line_diff > 40:
            raise PatchError(
                f"format_message anchor is {line_diff} lines from `async def send(` "
                f"in {WA} — likely matched a different method's line."
            )
        text = text[:fmt_m.start()] + WHATSAPP_FB_SEND_INJECT.lstrip("\n") + text[fmt_m.start():]

    # 2. edit_message() screen call (shift-agent-front-brain-edit marker).
    if FB_EDIT_MARK_BEGIN not in text:
        edit_def_re = re.compile(r"^    async def edit_message\(", re.MULTILINE)
        edit_m = edit_def_re.search(text)
        if not edit_m:
            raise PatchError(f"cannot find `async def edit_message(` def in {WA}")
        aiohttp_re = re.compile(r"^            import aiohttp$", re.MULTILINE)
        aiohttp_m = aiohttp_re.search(text, pos=edit_m.end())
        if not aiohttp_m:
            raise PatchError(
                f"`import aiohttp` not found inside edit_message() in {WA} — Hermes "
                f"may have refactored the edit path."
            )
        line_diff = text.count("\n", edit_m.start(), aiohttp_m.start())
        if line_diff > 20:
            raise PatchError(
                f"`import aiohttp` is {line_diff} lines from `async def edit_message(` "
                f"in {WA} — likely matched a different method's line."
            )
        insert_at = aiohttp_m.end() + 1  # just past the newline after `import aiohttp`
        text = text[:insert_at] + WHATSAPP_FB_EDIT_INJECT + text[insert_at:]

    return text


def _apply_wa_turn_budget_adapter(text: str) -> str:
    """Insert the per-inbound-turn send-budget ADAPTER pieces, each under its OWN
    marker so the volume cap installs even on a tree that ALREADY carries the
    front-brain-send screen (= production — the #643 defect this fixes). Three
    idempotent, individually-keyed edits, fail-closed on a missing anchor:
      1. sentinel + budget screen fn, module-level, before `class WhatsAppAdapter`.
      2. send() drop-check, immediately before the in-method front-brain-send block.
      3. edit_message() drop-check, immediately before the front-brain-edit block.
    (2)/(3) anchor on the in-method front-brain markers, so this MUST run AFTER
    _apply_wa_front_brain (guaranteed by _transform_whatsapp_py and the back-compat
    wrapper). Placing the drop-check BEFORE the content-screen block makes budget
    suppression the first thing checked in each egress path."""
    # 1. Sentinel + budget screen, module-level.
    if TURN_BUDGET_SENTINEL_MARK_BEGIN not in text:
        class_anchor = "class WhatsAppAdapter(BasePlatformAdapter):"
        if class_anchor not in text:
            raise PatchError(
                f"cannot locate '{class_anchor}' in {WA} for the turn-budget sentinel."
            )
        text = text.replace(
            class_anchor, WHATSAPP_TURN_BUDGET_SENTINEL + "\n\n\n" + class_anchor, 1
        )

    # 2. send() drop-check, before the in-method front-brain-send block. The 12-space
    # indent distinguishes the in-method marker from the module-level helper marker.
    if TURN_BUDGET_SEND_DROP_MARK_BEGIN not in text:
        send_fb_re = re.compile(
            r"^            # BEGIN shift-agent-front-brain-send$", re.MULTILINE
        )
        m = send_fb_re.search(text)
        if not m:
            raise PatchError(
                f"cannot locate the in-method front-brain-send marker in {WA} for the "
                f"turn-budget send drop-check (front-brain patch must run first)."
            )
        text = text[:m.start()] + WHATSAPP_TURN_BUDGET_SEND_DROP.lstrip("\n") + text[m.start():]

    # 3. edit_message() drop-check, before the front-brain-edit block.
    if TURN_BUDGET_EDIT_DROP_MARK_BEGIN not in text:
        edit_fb_re = re.compile(
            r"^            # BEGIN shift-agent-front-brain-edit$", re.MULTILINE
        )
        m = edit_fb_re.search(text)
        if not m:
            raise PatchError(
                f"cannot locate the front-brain-edit marker in {WA} for the turn-budget "
                f"edit drop-check (front-brain patch must run first)."
            )
        text = text[:m.start()] + WHATSAPP_TURN_BUDGET_EDIT_DROP.lstrip("\n") + text[m.start():]

    return text


def _transform_whatsapp_py(text: str) -> str:
    """Fully patch whatsapp.py: sender-id + front-brain content screen + turn-budget
    adapter. Order matters — the turn-budget adapter anchors on the front-brain
    markers, so front-brain runs first."""
    text = _apply_wa_sender_id(text)
    text = _apply_wa_front_brain(text)
    text = _apply_wa_turn_budget_adapter(text)
    return text


def _apply_run_sender_id(text: str) -> str:
    """Insert the sender-id flag block (after `import os`) + inject (before the
    `if _is_shared_multi_user` user-name prefix). Fail-closed on a missing anchor."""
    if MARK_BEGIN in text:
        return text
    anchor_re = re.compile(r"^import os$", re.MULTILINE)
    if not anchor_re.search(text):
        raise PatchError(f"cannot locate standalone `import os` line in {RUN}")
    text = anchor_re.sub("import os" + RUN_PY_FLAG_BLOCK, text, count=1)

    func_def_re = re.compile(r"^    async def _prepare_inbound_message_text\b", re.MULTILINE)
    func_match = func_def_re.search(text)
    if not func_match:
        raise PatchError(f"cannot find _prepare_inbound_message_text def in {RUN}")
    anchor_re2 = re.compile(r"^        if _is_shared_multi_user.*$", re.MULTILINE)
    anchor_match = anchor_re2.search(text, pos=func_match.end())
    if not anchor_match:
        raise PatchError(
            f"`if _is_shared_multi_user` line not found within {RUN} after function "
            f"def — Hermes may have refactored the function."
        )
    line_diff = text.count("\n", func_match.start(), anchor_match.start())
    if line_diff > 50:
        raise PatchError(
            f"anchor `if _is_shared_multi_user` is {line_diff} lines from function def "
            f"in {RUN} — likely matched a different function's line."
        )
    return text[:anchor_match.start()] + RUN_PY_INJECT_BLOCK.lstrip("\n") + text[anchor_match.start():]


def _apply_run_turn_budget_boundary(text: str) -> str:
    """Insert the per-inbound-turn send-budget BOUNDARY: flag block after `import os`
    + begin() call before the `if _is_shared_multi_user` prefix (same site as the
    sender-id inject; independent flag). Keeps its existing independent marker
    shift-agent-turn-send-budget. Fail-closed on a missing anchor."""
    if TURN_BUDGET_MARK_BEGIN in text:
        return text
    anchor_re = re.compile(r"^import os$", re.MULTILINE)
    if not anchor_re.search(text):
        raise PatchError(
            f"cannot locate standalone `import os` line in {RUN} for the "
            f"turn-send-budget flag block."
        )
    text = anchor_re.sub("import os" + RUN_PY_TURN_BUDGET_FLAG_BLOCK, text, count=1)

    func_def_re = re.compile(r"^    async def _prepare_inbound_message_text\b", re.MULTILINE)
    func_match = func_def_re.search(text)
    if not func_match:
        raise PatchError(
            f"cannot find _prepare_inbound_message_text def in {RUN} for the "
            f"turn-send-budget inject."
        )
    anchor_re2 = re.compile(r"^        if _is_shared_multi_user.*$", re.MULTILINE)
    anchor_match = anchor_re2.search(text, pos=func_match.end())
    if not anchor_match:
        raise PatchError(
            f"`if _is_shared_multi_user` line not found in {RUN} after function def "
            f"for turn-send-budget — Hermes may have refactored the function."
        )
    line_diff = text.count("\n", func_match.start(), anchor_match.start())
    if line_diff > 60:
        raise PatchError(
            f"anchor `if _is_shared_multi_user` is {line_diff} lines from function def "
            f"(turn-send-budget) in {RUN} — likely matched a different function."
        )
    return text[:anchor_match.start()] + RUN_PY_TURN_BUDGET_INJECT_BLOCK.lstrip("\n") + text[anchor_match.start():]


def _transform_run_py(text: str) -> str:
    """Fully patch run.py: sender-id block + turn-send-budget boundary."""
    text = _apply_run_sender_id(text)
    text = _apply_run_turn_budget_boundary(text)
    return text


# ─── All-or-nothing apply (staging + validate + atomic write + rollback) ────────

def _write_text_atomic(path: Path, text: str) -> None:
    """Write via a temp file + atomic rename on the same filesystem. Isolated so a
    test can monkeypatch it to simulate a mid-apply write fault on one target."""
    tmp = path.with_name(path.name + f".patchtmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _validate_patched(wa_new: str, run_new: str) -> None:
    """(c) — syntax + required-marker validation on the IN-MEMORY outputs, before
    any write. ast.parse is the py_compile equivalent (no bytecode side effect)."""
    for label, src in (("whatsapp.py", wa_new), ("run.py", run_new)):
        try:
            ast.parse(src)
        except SyntaxError as exc:
            raise PatchError(f"patched {label} fails to parse: {exc}")
    required_wa = (
        MARK_BEGIN, FB_SEND_MARK_BEGIN, FB_EDIT_MARK_BEGIN,
        TURN_BUDGET_SENTINEL_MARK_BEGIN, TURN_BUDGET_SEND_DROP_MARK_BEGIN,
        TURN_BUDGET_EDIT_DROP_MARK_BEGIN,
    )
    for mk in required_wa:
        if mk not in wa_new:
            raise PatchError(f"patched whatsapp.py missing required marker: {mk!r}")
    for mk in (MARK_BEGIN, TURN_BUDGET_MARK_BEGIN):
        if mk not in run_new:
            raise PatchError(f"patched run.py missing required marker: {mk!r}")


def _apply_wa_run(write: bool = True) -> tuple[str, str]:
    """All-or-nothing apply of whatsapp.py + run.py.

    (a) read both originals; (b) construct both patched outputs in memory;
    (c) validate syntax + every required marker on those outputs; (d) ONLY THEN
    write both via temp+atomic-rename; (e) if ANY step fails, restore/retain the
    ORIGINAL bytes for EVERY target — a validation fault writes nothing, and a
    write fault on the second file rolls the first back to its captured bytes.
    Returns the (wa_new, run_new) outputs (also usable for write=False dry-runs)."""
    wa_orig = WA.read_text(encoding="utf-8")
    run_orig = RUN.read_text(encoding="utf-8")

    # (a)+(b) construct patched outputs in memory (raises PatchError on any anchor).
    wa_new = _transform_whatsapp_py(wa_orig)
    run_new = _transform_run_py(run_orig)

    # (c) validate the in-memory outputs BEFORE touching any file.
    _validate_patched(wa_new, run_new)

    if not write:
        return wa_new, run_new

    # (d)+(e) write both atomically; roll back everything on any failure.
    written: list[tuple[Path, str]] = []
    try:
        for path, original, new_text in (
            (WA, wa_orig, wa_new),
            (RUN, run_orig, run_new),
        ):
            _write_text_atomic(path, new_text)
            written.append((path, original))
    except Exception:
        # A later write failed after an earlier one succeeded → restore the captured
        # original bytes for every already-written target (all-or-nothing).
        for path, original in written:
            try:
                path.write_text(original, encoding="utf-8")
            except Exception:
                pass
        raise

    print(f"  ✓ patched {WA} + {RUN} (atomic: sender-id + front-brain + turn-budget)")
    return wa_new, run_new


# ─── Back-compat single-file entry points (tests/test_turn_send_budget.py) ──────
#
# These write ONE file each for the focused patch-shape / end-to-end adapter tests.
# main() uses the all-or-nothing _apply_wa_run() instead. They share the pure
# transforms above, so their output is identical to the atomic path.

def _patch_whatsapp_py_front_brain_send() -> None:
    """Apply the front-brain content screen AND the turn-budget adapter to whatsapp.py
    (both, so the patched adapter carries the sentinel + both drop-checks). Fail-
    closed (exit 1) on a missing anchor."""
    text = WA.read_text(encoding="utf-8")
    try:
        new = _apply_wa_turn_budget_adapter(_apply_wa_front_brain(text))
    except PatchError as exc:
        sys.stderr.write(f"FAIL: {exc}\n")
        sys.exit(1)
    if new != text:
        WA.write_text(new, encoding="utf-8")
        print(f"  ✓ patched {WA} (front-brain screen + turn-budget adapter)")
    else:
        print(f"  ✓ {WA} front-brain + turn-budget adapter already patched")


def _patch_run_py_turn_send_budget() -> None:
    """Apply the per-inbound-turn send-budget boundary to run.py. Fail-closed
    (exit 1) on a missing anchor."""
    text = RUN.read_text(encoding="utf-8")
    try:
        new = _apply_run_turn_budget_boundary(text)
    except PatchError as exc:
        sys.stderr.write(f"FAIL: {exc}\n")
        sys.exit(1)
    if new != text:
        RUN.write_text(new, encoding="utf-8")
        print(f"  ✓ patched {RUN} (per-inbound-turn send budget boundary)")
    else:
        print(f"  ✓ {RUN} turn-send-budget already patched")


def _patch_bridge_js():
    if _has_marker(BR):
        print(f"  ✓ {BR} sender-id already patched")
        _patch_bridge_cta_js()
        return
    text = BR.read_text(encoding="utf-8")
    text = _ensure_bridge_cta_imports(text)

    # Anchor 1: insert helpers after express.json() init.
    anchor = re.search(r"^app\.use\(express\.json\(\)\);", text, re.MULTILINE)
    if not anchor:
        sys.stderr.write(f"FAIL: cannot locate express.json anchor in {BR}\n")
        sys.exit(1)
    insert_at = anchor.end()
    text = text[:insert_at] + "\n" + BRIDGE_JS_HELPERS + "\n" + text[insert_at:]

    # Anchor 2: wire _shiftResolveSender into the message-build site so
    # the queued event carries fromMe/senderPhone/senderLid.
    # We look for `messageQueue.push(event);` and inject a resolver call
    # immediately before it that mutates `event` in place. If that call
    # site cannot be located, we MUST fail loudly (not silently): the
    # earlier "helpers inserted; event-shape extension manual" was a
    # silent feature-off bug.
    push_re = re.compile(r"^(\s*)messageQueue\.push\(event\);", re.MULTILINE)
    m = push_re.search(text)
    if not m:
        sys.stderr.write(
            f"FAIL: cannot locate `messageQueue.push(event)` in {BR}\n"
            f"      The event-shape extension cannot be applied automatically.\n"
            f"      Manually wire `_shiftResolveSender` into the event object.\n"
        )
        sys.exit(1)
    indent = m.group(1)
    wire = (
        f'{indent}// BEGIN shift-agent-sender-id (event-shape extension)\n'
        f'{indent}try {{\n'
        f'{indent}  const _s = _shiftResolveSender(msg, sock, '
        f'(typeof lidToPhone !== "undefined" ? lidToPhone : null));\n'
        f'{indent}  event.fromMe = _s.fromMe;\n'
        f'{indent}  event.senderPhone = _s.senderPhone;\n'
        f'{indent}  event.senderLid = _s.senderLid;\n'
        f'{indent}  _shiftWriteLidCacheEntry(_s.senderPhone, _s.senderLid);\n'
        f'{indent}}} catch (_e) {{ console.error("[shift-agent] resolve failed:", _e); }}\n'
        f'{indent}// END shift-agent-sender-id (event-shape extension)\n'
    )
    text = text[:m.start()] + wire + text[m.start():]

    BR.write_text(text, encoding="utf-8")
    print(f"  ✓ patched {BR} (helpers + event-shape wiring)")
    _patch_bridge_cta_js()


def _patch_bridge_cta_js():
    text = BR.read_text(encoding="utf-8")
    text = _ensure_bridge_cta_imports(text)
    text = _patch_bridge_button_response_body(text)
    if CTA_MARK_BEGIN in text:
        text = re.sub(
            r"// BEGIN shift-agent-cta-buttons.*?// END shift-agent-cta-buttons\n?",
            BRIDGE_CTA_ENDPOINT + "\n",
            text,
            count=1,
            flags=re.DOTALL,
        )
    else:
        anchor = re.search(r"^// Typing indicator", text, re.MULTILINE)
        if not anchor:
            sys.stderr.write(f"FAIL: cannot locate typing endpoint anchor in {BR}\n")
            sys.exit(1)
        text = text[:anchor.start()] + BRIDGE_CTA_ENDPOINT + "\n" + text[anchor.start():]
    BR.write_text(text, encoding="utf-8")
    print(f"  ✓ patched {BR} (CTA native-flow endpoint)")


def _patch_bridge_button_response_body(text: str) -> str:
    if BUTTON_RESPONSE_MARK_BEGIN in text:
        return re.sub(
            r"\s*// BEGIN shift-agent-button-response-body.*?// END shift-agent-button-response-body\n?",
            "\n" + BRIDGE_BUTTON_RESPONSE_EXTRACT.rstrip("\n") + "\n",
            text,
            count=1,
            flags=re.DOTALL,
        )
    anchor = "      } else if (messageContent.imageMessage) {"
    if anchor not in text:
        sys.stderr.write(f"FAIL: cannot locate image message body anchor in {BR}\n")
        sys.exit(1)
    return text.replace(anchor, BRIDGE_BUTTON_RESPONSE_EXTRACT + anchor, 1)


def _ensure_bridge_cta_imports(text: str) -> str:
    if "generateWAMessageFromContent" in text and "proto" in text:
        return text
    old = (
        "import { makeWASocket, useMultiFileAuthState, DisconnectReason, "
        "fetchLatestBaileysVersion, downloadMediaMessage } from "
        "'@whiskeysockets/baileys';"
    )
    new = (
        "import { makeWASocket, useMultiFileAuthState, DisconnectReason, "
        "fetchLatestBaileysVersion, downloadMediaMessage, proto, "
        "generateWAMessageFromContent } from '@whiskeysockets/baileys';"
    )
    if old not in text:
        sys.stderr.write(f"FAIL: cannot locate Baileys import anchor in {BR}\n")
        sys.exit(1)
    return text.replace(old, new, 1)


def main() -> int:
    print("Applying shift-agent patches:")
    # whatsapp.py + run.py (sender-id + front-brain screen + turn-budget) apply
    # all-or-nothing: staged in memory, validated, then written atomically with
    # rollback — a missing anchor or mid-apply fault leaves both byte-identical.
    try:
        _apply_wa_run()
    except PatchError as exc:
        sys.stderr.write(f"FAIL: {exc}\n")
        return 1
    # bridge.js (sender-id + CTA) is a separate JS target with its own patcher.
    _patch_bridge_js()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
