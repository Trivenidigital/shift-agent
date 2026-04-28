"""sender_context — pure functions for resolving + rendering the
[shift-agent-sender ...] block prepended to inbound user messages.

Lives in src/ so it can be imported by tests AND by the Hermes-side patch.
The Hermes patch (in /root/.hermes/hermes-agent/gateway/platforms/whatsapp.py)
inlines these functions verbatim under BEGIN/END markers — keeping a single
source of truth here for tests + a shadow copy in Hermes for runtime.

Responsibilities:
- _resolve_sender_context(event_dict) -> dict
- _render_sender_context_block(ctx_dict) -> str
- _sanitize_user_body(body_str) -> str  (defeats homoglyph/zero-width spoofing)
- inject(event_dict, body_str) -> str   (the full pipeline)
"""
from __future__ import annotations
import re
import unicodedata
from typing import Any

# BEGIN shift-agent-sender-id (canonical regex source-of-truth — bridge.js
# mirrors these literals; check-shift-agent-patch.sh greps both files for
# string equality.)
_VALID_LID = re.compile(r"^\d{6,20}@lid$")
_VALID_PJID = re.compile(r"^\d{6,20}@s\.whatsapp\.net$")
_VALID_E164 = re.compile(r"^\+\d{10,15}$")

# Strip zero-width / bidi / BOM Unicode characters that could be used to
# bypass the prefix-strip regex via lookalikes.
_INVISIBLES = re.compile(
    "[​‌‍‎‏"
    "‪‫‬‭‮"
    "⁠⁡⁢⁣⁤⁥⁦⁧⁨⁩"
    "﻿]"
)
_PRE_BLOCK = re.compile(r"\[shift-agent-sender", flags=re.IGNORECASE)
# END shift-agent-sender-id


def _resolve_sender_context(event: dict[str, Any]) -> dict:
    """Extract structured sender info from a Baileys/bridge event dict.

    Pure: no I/O, no logging, deterministic. Validates each field against
    its expected regex; values that don't match are emitted as None
    (rendered as `null` in the block).

    Field-priority rules (DC6 from review):
    - senderId-derived phone/lid is the PRIMARY signal.
    - senderPhone / senderLid are FALLBACK only — never overwrite a value
      already set from senderId. (Defends against corrupt or inconsistent
      bridge fields silently demoting owner.)
    """
    out = {
        "platform": "whatsapp",
        "phone": None,
        "lid": None,
        "fromMe": bool(event.get("fromMe", False)),
        "chat_id": None,
    }
    sid = event.get("senderId") or ""
    # Strip Baileys' device suffix (":<digit>") if present
    sid_clean = re.sub(r":\d+(?=@)", "", sid)
    if _VALID_PJID.match(sid_clean):
        out["phone"] = "+" + sid_clean.split("@")[0]
    elif _VALID_LID.match(sid_clean):
        out["lid"] = sid_clean

    # Fallbacks only when senderId-derived value is missing
    if out["phone"] is None:
        sp = event.get("senderPhone") or ""
        if _VALID_E164.match(sp):
            out["phone"] = sp
    if out["lid"] is None:
        sl = event.get("senderLid") or ""
        if _VALID_LID.match(sl):
            out["lid"] = sl

    cid = event.get("chatId") or ""
    cid_clean = re.sub(r":\d+(?=@)", "", cid)
    if _VALID_LID.match(cid_clean) or _VALID_PJID.match(cid_clean):
        out["chat_id"] = cid_clean
    return out


def _q_quoted(v: str | None) -> str:
    """Quote a value with backslash escaping (DC4 — defends against
    attribute-injection via embedded `"` or `\\`)."""
    if v is None:
        return "null"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_sender_context_block(ctx: dict) -> str:
    """Render a v=1 single-line block from a context dict. Always emits
    every field; missing values become `null`."""
    return (
        f'[shift-agent-sender v=1 platform={ctx["platform"]} '
        f'phone={_q_quoted(ctx["phone"])} lid={_q_quoted(ctx["lid"])} '
        f'fromMe={"true" if ctx["fromMe"] else "false"} '
        f'chat_id={_q_quoted(ctx["chat_id"])}]'
    )


def _sanitize_user_body(body: str) -> str:
    """Defeat impersonation attempts where the user's message body contains
    `[shift-agent-sender ...]` (DC5).

    Steps (in order):
    1. NFKC-normalize so Unicode lookalikes (Cyrillic 's', etc.) collapse
       to their canonical form.
    2. Strip zero-width and bidi-override characters that could split the
       literal `[shift-agent-sender` prefix into invisible pieces.
    3. Replace any remaining occurrence with `[shift-agent-sender-stripped`
       so the block is no longer recognisable as a v=1 marker.
    """
    if not body:
        return body
    body = unicodedata.normalize("NFKC", body)
    body = _INVISIBLES.sub("", body)
    return _PRE_BLOCK.sub("[shift-agent-sender-stripped", body)


def inject(event: dict[str, Any], body: str) -> str:
    """Full pipeline: resolve → render → sanitize → prepend.

    Returns the new message text with the v=1 block on line 1.
    """
    ctx = _resolve_sender_context(event)
    block = _render_sender_context_block(ctx)
    safe_body = _sanitize_user_body(body or "")
    return f"{block}\n{safe_body}"
