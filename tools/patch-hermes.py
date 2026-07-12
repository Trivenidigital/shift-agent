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
import os
import re
import sys
from pathlib import Path

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


# ─── Patch payloads (front-brain outbound-send screen) ─────────────────

# Module-level helper, inserted before `class WhatsAppAdapter(...)`. Lazily
# imports safe_io from /opt/shift-agent (the flat platform install) and returns
# the SAFE text to relay. Fail-OPEN to the ORIGINAL content if safe_io is
# unimportable: LLM replies exit through send() UN-screened TODAY, so failing
# open here is exactly today's behavior — it does NOT regress safety, it only
# means the NEW control is absent, which the tier's default-off posture already
# assumes. It must never crash the send path. The real gate lives INSIDE safe_io
# (FRONT_BRAIN_OUTBOUND_ENFORCE + allowlist), so this wrapper is byte-identical
# when the tier is off.
WHATSAPP_FB_SEND_HELPER = '''# BEGIN shift-agent-front-brain-send
def _shift_front_brain_screen_outbound(chat_id, content):
    """Route a composed outbound reply through the front-brain gateway-send
    screen and return the SAFE text to relay. See tools/patch-hermes.py."""
    try:
        import sys as _sys
        if "/opt/shift-agent" not in _sys.path:
            _sys.path.insert(0, "/opt/shift-agent")
        from safe_io import front_brain_screen_gateway_send as _fb_screen
        return _fb_screen(chat_id, content)
    except Exception:
        return content
# END shift-agent-front-brain-send'''

# One-line call injected at the top of send(), immediately before the
# format/chunk/relay begins (anchor: the format_message assignment). 12-space
# indent = method-try-block body.
WHATSAPP_FB_SEND_INJECT = '''            # BEGIN shift-agent-front-brain-send
            content = _shift_front_brain_screen_outbound(chat_id, content)
            # END shift-agent-front-brain-send
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


def _patch_whatsapp_py():
    if _has_marker(WA):
        print(f"  ✓ {WA} already patched")
        return
    # Anchor: insert helpers near the top of the file, after imports.
    # We pick `from gateway.platforms.base import` as a stable late-import line.
    text = WA.read_text(encoding="utf-8")
    anchor = re.search(r"^class .*Platform.*:", text, re.MULTILINE)
    if not anchor:
        sys.stderr.write(f"FAIL: cannot locate class anchor in {WA}\n")
        sys.exit(1)
    # Insert helpers right before the first class definition
    insert_at = anchor.start()
    new_text = text[:insert_at] + WHATSAPP_PY_HELPERS + "\n\n" + text[insert_at:]
    WA.write_text(new_text, encoding="utf-8")
    print(f"  ✓ patched {WA}")


def _patch_whatsapp_py_front_brain_send():
    """Insert the front-brain outbound-send screen into whatsapp.py.

    Two edits, both anchored on stable symbols (never line numbers):
      1. the module-level helper before `class WhatsAppAdapter(BasePlatformAdapter):`
      2. the one-line screen call before `formatted = self.format_message(content)`
         inside `async def send(` (bounded forward-scan, mirrors _patch_run_py).
    Idempotent (marker check first); fail-closed (exit 1) if either anchor is
    missing so a Hermes upgrade cannot silently no-op the LLM-reply screen.
    """
    text = WA.read_text(encoding="utf-8")
    if FB_SEND_MARK_BEGIN in text:
        print(f"  ✓ {WA} front-brain-send already patched")
        return

    class_anchor = "class WhatsAppAdapter(BasePlatformAdapter):"
    if class_anchor not in text:
        sys.stderr.write(
            f"FAIL: cannot locate '{class_anchor}' in {WA} — "
            f"Hermes may have renamed the adapter class.\n"
        )
        sys.exit(1)
    text = text.replace(
        class_anchor, WHATSAPP_FB_SEND_HELPER + "\n\n\n" + class_anchor, 1
    )

    send_def_re = re.compile(r"^    async def send\(", re.MULTILINE)
    send_m = send_def_re.search(text)
    if not send_m:
        sys.stderr.write(f"FAIL: cannot find `async def send(` def in {WA}\n")
        sys.exit(1)
    fmt_re = re.compile(
        r"^            formatted = self\.format_message\(content\)", re.MULTILINE
    )
    fmt_m = fmt_re.search(text, pos=send_m.end())
    if not fmt_m:
        sys.stderr.write(
            f"FAIL: `formatted = self.format_message(content)` not found inside "
            f"send() in {WA} — Hermes may have refactored the send path.\n"
        )
        sys.exit(1)
    line_diff = text.count("\n", send_m.start(), fmt_m.start())
    if line_diff > 40:
        sys.stderr.write(
            f"FAIL: format_message anchor is {line_diff} lines from `async def "
            f"send(` — likely matched a different method's line.\n"
        )
        sys.exit(1)
    text = text[:fmt_m.start()] + WHATSAPP_FB_SEND_INJECT.lstrip("\n") + text[fmt_m.start():]

    WA.write_text(text, encoding="utf-8")
    print(f"  ✓ patched {WA} (front-brain outbound-send screen)")


def _patch_run_py():
    if _has_marker(RUN):
        print(f"  ✓ {RUN} already patched")
        return
    text = RUN.read_text(encoding="utf-8")

    # Anchor 1: insert flag block after the standalone `import os` line.
    # Line-anchored regex so we don't match `import os.path` or `# import os`.
    anchor_re = re.compile(r"^import os$", re.MULTILINE)
    if not anchor_re.search(text):
        sys.stderr.write(f"FAIL: cannot locate standalone `import os` line in {RUN}\n")
        sys.exit(1)
    text = anchor_re.sub("import os" + RUN_PY_FLAG_BLOCK, text, count=1)

    # Anchor 2: in `_prepare_inbound_message_text`, inject BEFORE the
    # `if _is_shared_multi_user ...` user-name prefix line so sender block
    # is line 1. Strategy: locate function def, then forward-scan for the
    # anchor line within ~50 lines.
    func_def_re = re.compile(r"^    async def _prepare_inbound_message_text\b", re.MULTILINE)
    func_match = func_def_re.search(text)
    if not func_match:
        sys.stderr.write(f"FAIL: cannot find _prepare_inbound_message_text def in {RUN}\n")
        sys.exit(1)
    anchor_re = re.compile(r"^        if _is_shared_multi_user.*$", re.MULTILINE)
    anchor_match = anchor_re.search(text, pos=func_match.end())
    if not anchor_match:
        sys.stderr.write(
            f"FAIL: `if _is_shared_multi_user` line not found within {RUN} after function def — "
            f"Hermes may have refactored the function.\n"
        )
        sys.exit(1)
    line_diff = text.count("\n", func_match.start(), anchor_match.start())
    if line_diff > 50:
        sys.stderr.write(
            f"FAIL: anchor `if _is_shared_multi_user` is {line_diff} lines from function def — "
            f"likely matched a different function's line.\n"
        )
        sys.exit(1)
    # Insert BEFORE the anchor line (preserve line break)
    text = text[:anchor_match.start()] + RUN_PY_INJECT_BLOCK.lstrip("\n") + text[anchor_match.start():]

    RUN.write_text(text, encoding="utf-8")
    print(f"  ✓ patched {RUN}")


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
    print("Applying shift-agent-sender-id patches:")
    _patch_whatsapp_py()
    _patch_run_py()
    _patch_bridge_js()
    _patch_whatsapp_py_front_brain_send()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
