#!/usr/bin/env python3
"""Port the minimum Shift Agent WhatsApp patch delta onto Hermes v0.19.1.

Ports exactly four things (see the operator brief):
  1. Front-brain outbound screening   -> plugins/platforms/whatsapp/adapter.py
  2. Button / interactive inbound     -> scripts/whatsapp-bridge/bridge_helpers.js
  3. CTA outbound POST /send-cta      -> scripts/whatsapp-bridge/bridge.js
  4. Sender-context injection         -> gateway/run.py + gateway/platforms/whatsapp_common.py

Retired (NOT ported): LID-cache writer, transport-evidence probe, turn-send-budget.

Every insertion lives inside a BEGIN/END shift-agent-* marker block. Idempotent:
a file already carrying the marker is skipped. Anchors are asserted to occur
exactly once; a missing/ambiguous anchor aborts with a non-zero exit and no
partial write.
"""
import hashlib
import sys

ROOT = "/usr/local/lib/hermes-agent"

ADAPTER = f"{ROOT}/plugins/platforms/whatsapp/adapter.py"
COMMON = f"{ROOT}/gateway/platforms/whatsapp_common.py"
RUNPY = f"{ROOT}/gateway/run.py"
BRIDGE = f"{ROOT}/scripts/whatsapp-bridge/bridge.js"
HELPERS = f"{ROOT}/scripts/whatsapp-bridge/bridge_helpers.js"

edits = []  # (path, marker, anchor, replacement)


# ─────────────────────────────────────────────────────────────────────────
# (4a) sender-context helpers — new home is whatsapp_common.py, because
#      gateway/platforms/whatsapp.py no longer exists at v0.19.1.
# ─────────────────────────────────────────────────────────────────────────
COMMON_ANCHOR = '''logger = logging.getLogger(__name__)


class WhatsAppBehaviorMixin:'''

COMMON_BLOCK = '''logger = logging.getLogger(__name__)


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


class WhatsAppBehaviorMixin:'''

edits.append((COMMON, "BEGIN shift-agent-sender-id", COMMON_ANCHOR, COMMON_BLOCK))


# ─────────────────────────────────────────────────────────────────────────
# (1) front-brain outbound screening — helper + two call sites in the
#     v0.19.1 plugin adapter.
# ─────────────────────────────────────────────────────────────────────────
ADAPTER_HELPER_ANCHOR = '''class WhatsAppAdapter(WhatsAppBehaviorMixin, BasePlatformAdapter):'''

ADAPTER_HELPER_BLOCK = '''# BEGIN shift-agent-front-brain-send
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
# END shift-agent-front-brain-send


class WhatsAppAdapter(WhatsAppBehaviorMixin, BasePlatformAdapter):'''

edits.append((ADAPTER, "BEGIN shift-agent-front-brain-send",
              ADAPTER_HELPER_ANCHOR, ADAPTER_HELPER_BLOCK))

ADAPTER_SEND_ANCHOR = '''            # Format and chunk the message
            formatted = self.format_message(content)'''

ADAPTER_SEND_BLOCK = '''            # BEGIN shift-agent-front-brain-send
            content = _shift_front_brain_screen_outbound(chat_id, content)
            # END shift-agent-front-brain-send
            # Format and chunk the message
            formatted = self.format_message(content)'''

edits.append((ADAPTER, "# BEGIN shift-agent-front-brain-send\n            content",
              ADAPTER_SEND_ANCHOR, ADAPTER_SEND_BLOCK))

ADAPTER_EDIT_ANCHOR = '''        try:
            import aiohttp
            async with self._http_session.post(
                f"http://127.0.0.1:{self._bridge_port}/edit",'''

ADAPTER_EDIT_BLOCK = '''        try:
            import aiohttp
            # BEGIN shift-agent-front-brain-edit
            content = _shift_front_brain_screen_outbound(chat_id, content, reserve_budget=finalize)
            # END shift-agent-front-brain-edit
            async with self._http_session.post(
                f"http://127.0.0.1:{self._bridge_port}/edit",'''

edits.append((ADAPTER, "BEGIN shift-agent-front-brain-edit",
              ADAPTER_EDIT_ANCHOR, ADAPTER_EDIT_BLOCK))


# ─────────────────────────────────────────────────────────────────────────
# (4b) sender-context injection in run.py — flag + injection site.
#      Import target updated: gateway.platforms.whatsapp -> whatsapp_common.
# ─────────────────────────────────────────────────────────────────────────
RUN_FLAG_ANCHOR = '''import logging
import os
import queue'''

RUN_FLAG_BLOCK = '''import logging
import os
# BEGIN shift-agent-sender-id
_INJECT_SENDER_CONTEXT = (
    os.environ.get("HERMES_INJECT_SENDER_CONTEXT", "0") == "1"
)
# END shift-agent-sender-id
import queue'''

edits.append((RUNPY, "BEGIN shift-agent-sender-id", RUN_FLAG_ANCHOR, RUN_FLAG_BLOCK))

RUN_INJECT_ANCHOR = '''            thread_sessions_per_user=_thread_sessions_per_user,
        )
        if _is_shared_multi_user and source.user_name:'''

RUN_INJECT_BLOCK = '''            thread_sessions_per_user=_thread_sessions_per_user,
        )
        # BEGIN shift-agent-sender-id
        if _INJECT_SENDER_CONTEXT and isinstance(getattr(event, "raw_message", None), dict):
            try:
                from gateway.platforms.whatsapp_common import (
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
        if _is_shared_multi_user and source.user_name:'''

edits.append((RUNPY, "BEGIN shift-agent-sender-id\n        if _INJECT_SENDER_CONTEXT",
              RUN_INJECT_ANCHOR, RUN_INJECT_BLOCK))


# ─────────────────────────────────────────────────────────────────────────
# (2) button / interactive inbound replies — extractBridgeEvent branches.
# ─────────────────────────────────────────────────────────────────────────
HELPERS_ANCHOR = '''  } else if (messageContent.imageMessage) {
    const item = messageContent.imageMessage;'''

HELPERS_BLOCK = '''  // BEGIN shift-agent-button-response-body
  } else if (messageContent.buttonsResponseMessage?.selectedButtonId || messageContent.buttonsResponseMessage?.selectedDisplayText) {
    body = messageContent.buttonsResponseMessage.selectedButtonId
      || messageContent.buttonsResponseMessage.selectedDisplayText
      || '';
    nativeType = 'buttonsResponseMessage';
  } else if (messageContent.templateButtonReplyMessage?.selectedId || messageContent.templateButtonReplyMessage?.selectedDisplayText) {
    body = messageContent.templateButtonReplyMessage.selectedId
      || messageContent.templateButtonReplyMessage.selectedDisplayText
      || '';
    nativeType = 'templateButtonReplyMessage';
  } else if (messageContent.interactiveResponseMessage?.nativeFlowResponseMessage?.paramsJson) {
    try {
      const params = JSON.parse(messageContent.interactiveResponseMessage.nativeFlowResponseMessage.paramsJson || '{}');
      body = String(params.id || params.response || params.display_text || '').trim();
    } catch (err) {
      body = messageContent.interactiveResponseMessage?.body?.text || '';
    }
    nativeType = 'interactiveResponseMessage';
  // END shift-agent-button-response-body
  } else if (messageContent.imageMessage) {
    const item = messageContent.imageMessage;'''

edits.append((HELPERS, "BEGIN shift-agent-button-response-body",
              HELPERS_ANCHOR, HELPERS_BLOCK))


# ─────────────────────────────────────────────────────────────────────────
# (3) CTA outbound POST /send-cta + (4c) sender-id event-shape extension.
# ─────────────────────────────────────────────────────────────────────────
BRIDGE_IMPORT_ANCHOR = """import { matchesAllowedUser, parseAllowedUsers } from './allowlist.js';"""

BRIDGE_IMPORT_BLOCK = """// BEGIN shift-agent-cta-buttons (baileys proto imports)
import { proto, generateWAMessageFromContent } from '@whiskeysockets/baileys';
// END shift-agent-cta-buttons (baileys proto imports)
import { matchesAllowedUser, parseAllowedUsers } from './allowlist.js';"""

edits.append((BRIDGE, "BEGIN shift-agent-cta-buttons (baileys proto imports)",
              BRIDGE_IMPORT_ANCHOR, BRIDGE_IMPORT_BLOCK))

BRIDGE_RESOLVER_ANCHOR = '''function trackSentMessageId(sent) {
  rememberSentId(sent?.key?.id);
}'''

BRIDGE_RESOLVER_BLOCK = r'''function trackSentMessageId(sent) {
  rememberSentId(sent?.key?.id);
}

// BEGIN shift-agent-sender-id
// Resolve the true speaker of an inbound message. The bridge's own
// `senderId` is `msg.key.participant || chatId`, which for a fromMe DM is the
// CUSTOMER's jid rather than the owner's — this recovers the owner identity
// from sock.user.id so the gateway's sender-context block is accurate.
// The LID -> phone cache backfill was retired 2026-08-01 (proven never to have
// fired: buildLidMap keyed bare LID digits, lookups used "<lid>@lid").
const _SHIFT_LID = /^\d{6,20}@lid$/;
const _SHIFT_PJID = /^\d{6,20}@s\.whatsapp\.net$/;

function _shiftResolveSender(msg, sock) {
  const fromMe = !!(msg && msg.key && msg.key.fromMe);
  let senderId = '';
  if (fromMe && sock && sock.user && sock.user.id) {
    senderId = sock.user.id;
  } else if (msg && msg.key) {
    senderId = msg.key.participant || msg.key.remoteJid || '';
  }
  // Strip baileys device suffix ":N"
  senderId = senderId.replace(/:\d+(?=@)/, '');
  let senderPhone = null, senderLid = null;
  if (_SHIFT_PJID.test(senderId)) {
    senderPhone = '+' + senderId.split('@')[0];
  } else if (_SHIFT_LID.test(senderId)) {
    senderLid = senderId;
  }
  return { senderId, senderPhone, senderLid, fromMe };
}
// END shift-agent-sender-id'''

edits.append((BRIDGE, "BEGIN shift-agent-sender-id",
              BRIDGE_RESOLVER_ANCHOR, BRIDGE_RESOLVER_BLOCK))

BRIDGE_EVENT_ANCHOR = '''      event.fromOwner = fromOwner;'''

BRIDGE_EVENT_BLOCK = '''      event.fromOwner = fromOwner;

      // BEGIN shift-agent-sender-id (event-shape extension)
      try {
        const _s = _shiftResolveSender(msg, sock);
        event.fromMe = _s.fromMe;
        event.senderPhone = _s.senderPhone;
        event.senderLid = _s.senderLid;
      } catch (_e) { console.error("[shift-agent] resolve failed:", _e); }
      // END shift-agent-sender-id (event-shape extension)'''

edits.append((BRIDGE, "BEGIN shift-agent-sender-id (event-shape extension)",
              BRIDGE_EVENT_ANCHOR, BRIDGE_EVENT_BLOCK))

BRIDGE_CTA_ANCHOR = '''// Send poll primitive. Approval UX is intentionally not wired here; gateway'''

BRIDGE_CTA_BLOCK = '''// BEGIN shift-agent-cta-buttons
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

    // v0.19.1 dedup API: createOutboundIdTracker exposes .remember()/.has()
    // only — the old .add/.size/.delete/MAX_RECENT_IDS eviction dance is gone.
    trackSentMessageId(waMessage);
    res.json({ success: true, messageId: waMessage?.key?.id });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});
// END shift-agent-cta-buttons

// Send poll primitive. Approval UX is intentionally not wired here; gateway'''

edits.append((BRIDGE, "BEGIN shift-agent-cta-buttons\n// Send an interactive",
              BRIDGE_CTA_ANCHOR, BRIDGE_CTA_BLOCK))


# ─────────────────────────────────────────────────────────────────────────
def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main():
    paths = []
    for path, _, _, _ in edits:
        if path not in paths:
            paths.append(path)

    print("=== BEFORE ===")
    before = {}
    for p in paths:
        before[p] = sha256(p)
        print(f"{before[p]}  {p}")

    # Read every file once, apply all its edits in memory, write once.
    bodies = {p: open(p, "r", encoding="utf-8", newline="").read() for p in paths}
    applied, skipped = [], []

    for path, marker, anchor, replacement in edits:
        text = bodies[path]
        if marker in text:
            skipped.append(f"{path} :: {marker.splitlines()[0]} (already present)")
            continue
        n = text.count(anchor)
        if n != 1:
            sys.stderr.write(
                f"ABORT: anchor for {marker.splitlines()[0]} in {path} "
                f"occurs {n} times (expected exactly 1). No files written.\n"
                f"Anchor was:\n{anchor}\n"
            )
            return 2
        bodies[path] = text.replace(anchor, replacement, 1)
        applied.append(f"{path} :: {marker.splitlines()[0]}")

    for p in paths:
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(bodies[p])

    print("\n=== APPLIED ===")
    for a in applied:
        print("  +", a)
    if skipped:
        print("\n=== SKIPPED (idempotent) ===")
        for s in skipped:
            print("  =", s)

    print("\n=== AFTER ===")
    for p in paths:
        after = sha256(p)
        print(f"{after}  {p}   (was {before[p][:16]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
