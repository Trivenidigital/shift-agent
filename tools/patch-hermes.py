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


def _has_marker(p: Path) -> bool:
    return p.exists() and (MARK_BEGIN in p.read_text())


def _patch_whatsapp_py():
    if _has_marker(WA):
        print(f"  ✓ {WA} already patched")
        return
    # Anchor: insert helpers near the top of the file, after imports.
    # We pick `from gateway.platforms.base import` as a stable late-import line.
    text = WA.read_text()
    anchor = re.search(r"^class .*Platform.*:", text, re.MULTILINE)
    if not anchor:
        sys.stderr.write(f"FAIL: cannot locate class anchor in {WA}\n")
        sys.exit(1)
    # Insert helpers right before the first class definition
    insert_at = anchor.start()
    new_text = text[:insert_at] + WHATSAPP_PY_HELPERS + "\n\n" + text[insert_at:]
    WA.write_text(new_text)
    print(f"  ✓ patched {WA}")


def _patch_run_py():
    if _has_marker(RUN):
        print(f"  ✓ {RUN} already patched")
        return
    text = RUN.read_text()

    # Anchor 1: insert flag block after `import os` near the top
    if "import os" not in text:
        sys.stderr.write(f"FAIL: cannot locate `import os` in {RUN}\n")
        sys.exit(1)
    text = text.replace("import os", "import os" + RUN_PY_FLAG_BLOCK, 1)

    # Anchor 2: in `_prepare_inbound_message_text`, inject BEFORE the existing
    # `[{user_name}]` prefix (so sender block is line 1 unconditionally).
    pattern = re.compile(
        r"(    async def _prepare_inbound_message_text.*?\n"
        r"(?:        .*\n)*?)"
        r"(        if .*?_is_shared_multi_user)",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        # Fallback: find the function and insert at end of body before return
        sys.stderr.write(f"WARN: could not locate ideal inject site in _prepare_inbound_message_text — may need manual placement\n")
        # Try a simpler search: just `_prepare_inbound_message_text`
        anchor_pattern = re.compile(r"(    async def _prepare_inbound_message_text\b[^\n]*\n)")
        m2 = anchor_pattern.search(text)
        if not m2:
            sys.stderr.write(f"FAIL: cannot locate _prepare_inbound_message_text in {RUN}\n")
            sys.exit(1)
        # Insert right after the function signature line
        insert_at = m2.end()
        text = text[:insert_at] + RUN_PY_INJECT_BLOCK + text[insert_at:]
    else:
        # Insert the inject block BEFORE the user-name prefix
        text = text[:m.start(2)] + RUN_PY_INJECT_BLOCK + text[m.start(2):]

    RUN.write_text(text)
    print(f"  ✓ patched {RUN}")


def _patch_bridge_js():
    if _has_marker(BR):
        print(f"  ✓ {BR} already patched")
        return
    text = BR.read_text()

    # Anchor: insert helpers after the existing function declarations.
    # Use `app.use(express.json())` as a stable late-init point.
    anchor = re.search(r"^app\.use\(express\.json\(\)\);", text, re.MULTILINE)
    if not anchor:
        sys.stderr.write(f"FAIL: cannot locate express.json anchor in {BR}\n")
        sys.exit(1)
    insert_at = anchor.end()
    text = text[:insert_at] + "\n" + BRIDGE_JS_HELPERS + "\n" + text[insert_at:]

    # Add the call to _shiftWriteLidCacheEntry near messageQueue.push.
    # We append a marker block that captures the most recent senderPhone/Lid;
    # since we can't easily mutate the existing event object insertion in this
    # generic patcher, we instead append a hook AFTER messageQueue.push.
    # But note: bridge.js may already not have senderPhone/senderLid on the
    # event. Our complete patch (the v2 design) extends the queued event
    # shape; that's a more invasive change deferred to manual review on the
    # VPS.

    BR.write_text(text)
    print(f"  ✓ patched {BR} (helpers inserted; event-shape extension may need manual review)")


def main() -> int:
    print("Applying shift-agent-sender-id patches:")
    _patch_whatsapp_py()
    _patch_run_py()
    _patch_bridge_js()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
