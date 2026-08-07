"""Shift Agent WhatsApp policy layer.

Application/security policy that used to live as patches inside Hermes core
(``plugins/platforms/whatsapp/adapter.py`` and ``gateway/run.py``). It now runs
entirely through supported plugin seams so the Hermes checkout stays stock:

* OUTBOUND -- ``ScreenedWhatsAppAdapter`` subclasses the bundled adapter and
  routes every ``send()`` / ``edit_message()`` through the front-brain screen.
  Registered under the ``whatsapp`` platform name; ``platform_registry.register``
  is last-writer-wins and user plugins load after bundled ones, so this factory
  replaces the stock one.
* INBOUND -- ``pre_gateway_dispatch`` prepends an authenticated sender-context
  block and sanitises the untrusted body before the agent ever sees it.

Both are FAIL-CLOSED: if the screen cannot be consulted, nothing is relayed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import unicodedata
from typing import Any, Optional

logger = logging.getLogger(__name__)

SHIFT_AGENT_PATH = "/opt/shift-agent"

# Wall-clock ceiling for ONE screening call. front_brain_screen_gateway_send is
# synchronous and takes file locks, so it runs in a worker thread: a slow screen
# must neither block the gateway event loop nor stall a reply forever.
FRONT_BRAIN_TIMEOUT_SEC = float(os.environ.get("SHIFT_FRONT_BRAIN_TIMEOUT_SEC", "20"))

INJECT_SENDER_CONTEXT = os.environ.get("HERMES_INJECT_SENDER_CONTEXT", "0") == "1"


# ─────────────────────────────────────────────────────────────────────────
# Front-brain outbound screen
# ─────────────────────────────────────────────────────────────────────────
class _BlockSend:
    """Fail-closed sentinel. Returned when the screen could NOT be consulted
    (unimportable, raised, or timed out). Callers compare by IDENTITY and relay
    nothing -- a screening exception or timeout must BLOCK delivery, never fall
    through with unscreened model output."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<shift-agent BLOCK_SEND>"


BLOCK_SEND = _BlockSend()


def front_brain_screen():
    """Return ``safe_io.front_brain_screen_gateway_send``; raise if unavailable."""
    if SHIFT_AGENT_PATH not in sys.path:
        sys.path.insert(0, SHIFT_AGENT_PATH)
    from safe_io import front_brain_screen_gateway_send

    return front_brain_screen_gateway_send


async def screen_outbound(chat_id: str, content: str, reserve_budget: bool = True):
    """Screen one composed outbound reply. FAIL-CLOSED.

    Returns the SAFE text to relay (possibly a substituted refusal template), or
    ``BLOCK_SEND`` when the screen could not be consulted.

    ``reserve_budget`` is False for progressive streamed edit drafts -- a
    streamed reply costs ONE budget unit, reserved only on the finalized edit.
    """
    try:
        screen = front_brain_screen()
        return await asyncio.wait_for(
            asyncio.to_thread(screen, chat_id, content, reserve_budget=reserve_budget),
            timeout=FRONT_BRAIN_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.error(
            "front_brain_screen_blocked reason=timeout timeout_sec=%s chat=%s "
            "-- delivery BLOCKED (fail-closed)",
            FRONT_BRAIN_TIMEOUT_SEC,
            chat_id,
        )
        return BLOCK_SEND
    except Exception as exc:
        logger.error(
            "front_brain_screen_blocked reason=%s:%s chat=%s "
            "-- delivery BLOCKED (fail-closed)",
            type(exc).__name__,
            str(exc)[:200],
            chat_id,
            exc_info=True,
        )
        return BLOCK_SEND


# ─────────────────────────────────────────────────────────────────────────
# Sender-context prompt-injection defence
# ─────────────────────────────────────────────────────────────────────────
_VALID_LID = re.compile(r"^\d{6,20}@lid$")
_VALID_PJID = re.compile(r"^\d{6,20}@s\.whatsapp\.net$")
_VALID_E164 = re.compile(r"^\+\d{10,15}$")
_INVISIBLES = re.compile(
    "[​‌‍‎‏"
    "‪‫‬‭‮"
    "⁠⁡⁢⁣⁤⁥⁦⁧⁨⁩"
    "﻿]"
)
_PRE_BLOCK = re.compile(r"\[shift-agent-sender", flags=re.IGNORECASE)


def resolve_sender_context(event: dict) -> dict:
    """Derive the true speaker identity from a bridge event. Pure helper."""
    out = {
        "platform": "whatsapp",
        "phone": None,
        "lid": None,
        "fromMe": bool(event.get("fromMe", False)),
        "chat_id": None,
    }
    sid = event.get("senderId") or ""
    sid_clean = re.sub(r":\d+(?=@)", "", sid)
    if _VALID_PJID.match(sid_clean):
        out["phone"] = "+" + sid_clean.split("@")[0]
    elif _VALID_LID.match(sid_clean):
        out["lid"] = sid_clean
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


def _q(v) -> str:
    if v is None:
        return "null"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_sender_context_block(ctx: dict) -> str:
    return (
        f'[shift-agent-sender v=1 platform={ctx["platform"]} '
        f'phone={_q(ctx["phone"])} lid={_q(ctx["lid"])} '
        f'fromMe={"true" if ctx["fromMe"] else "false"} '
        f'chat_id={_q(ctx["chat_id"])}]'
    )


def sanitize_user_body(body: str) -> str:
    """Neutralise a hostile body: NFKC-normalise, strip invisibles, and rename
    any spoofed ``[shift-agent-sender`` block so only OUR block is authentic."""
    if not body:
        return body
    body = unicodedata.normalize("NFKC", body)
    body = _INVISIBLES.sub("", body)
    return _PRE_BLOCK.sub("[shift-agent-sender-stripped", body)


def pre_gateway_dispatch(**kwargs: Any) -> Optional[dict]:
    """Hook: stamp an authenticated sender block onto every inbound WhatsApp
    message and sanitise the untrusted body.

    Fires once per inbound MessageEvent BEFORE auth/pairing and agent dispatch.
    Media captions and interactive button replies arrive as ``event.text`` just
    like plain messages, so one callback covers all three shapes identically.

    Fail-closed: any error leaves the text untouched (no partial block, no
    spoofing window) rather than emitting a half-formed header.
    """
    if not INJECT_SENDER_CONTEXT:
        return None
    event = kwargs.get("event")
    if event is None:
        return None
    try:
        from gateway.config import Platform

        source = getattr(event, "source", None)
        if source is None or getattr(source, "platform", None) != Platform.WHATSAPP:
            return None
        raw = getattr(event, "raw_message", None)
        if not isinstance(raw, dict):
            return None
        text = getattr(event, "text", None) or ""
        block = render_sender_context_block(resolve_sender_context(raw))
        return {"action": "rewrite", "text": f"{block}\n{sanitize_user_body(text)}"}
    except Exception as exc:
        logger.warning("shift-agent: sender context inject failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────
# Screening adapter
# ─────────────────────────────────────────────────────────────────────────
def assert_registry_override(cls) -> None:
    """Refuse if the registry no longer resolves ``whatsapp`` to *cls*.

    Guards the load-order dependency the whole design rests on: registration is
    last-writer-wins, so a plugin registering AFTER us would silently displace
    the screening adapter and screening would vanish with no error. The winning
    factory is tagged with the class it builds, so this compares identities
    rather than trusting that "our __init__ ran, therefore we won".
    """
    from gateway.platform_registry import platform_registry

    entry = platform_registry.get("whatsapp")
    if entry is None:
        raise RuntimeError(
            "shift-agent-policy: no 'whatsapp' platform registered; refusing to "
            "construct a screening adapter that the gateway would not use."
        )
    winner = getattr(entry.adapter_factory, "_shift_agent_screened_cls", None)
    if winner is not cls:
        raise RuntimeError(
            "shift-agent-policy: the registered 'whatsapp' factory builds "
            f"{getattr(winner, '__name__', winner)!r}, not {cls.__name__!r} — the "
            "screening override was displaced. Refusing to construct rather than "
            "let unscreened output reach live chats."
        )


def build_screened_adapter_class():
    """Build the screening subclass lazily (importing the bundled adapter pulls
    in the whole WhatsApp transport, so keep it out of module import)."""
    from gateway.platforms.base import SendResult
    from gateway.whatsapp_identity import to_whatsapp_jid
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

    class ScreenedWhatsAppAdapter(WhatsAppAdapter):
        """WhatsApp transport with the Shift Agent outbound screen in front of
        every free-form egress path (``send`` and streamed ``edit_message``)."""

        def __init__(self, config):
            # Fail closed at construction: an adapter that cannot consult the
            # screen must never exist, let alone relay.
            front_brain_screen()
            # ...and one that is not the registry's winner must not exist either.
            assert_registry_override(type(self))
            super().__init__(config)

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            if content and content.strip():
                jid = to_whatsapp_jid(chat_id)
                screened = await screen_outbound(jid, content)
                if screened is BLOCK_SEND:
                    return SendResult(
                        success=False, error="front_brain_screen_unavailable"
                    )
                content = screened
            return await super().send(
                chat_id, content, reply_to=reply_to, metadata=metadata
            )

        async def edit_message(self, chat_id, message_id, content, *, finalize=False):
            jid = to_whatsapp_jid(chat_id)
            screened = await screen_outbound(jid, content, reserve_budget=finalize)
            if screened is BLOCK_SEND:
                return SendResult(success=False, error="front_brain_screen_unavailable")
            return await super().edit_message(
                chat_id, message_id, screened, finalize=finalize
            )

    return ScreenedWhatsAppAdapter


# ─────────────────────────────────────────────────────────────────────────
# Plugin entry point
# ─────────────────────────────────────────────────────────────────────────
def register(ctx) -> None:
    """Register the inbound hook and the screening WhatsApp adapter.

    The platform kwargs mirror the bundled registration exactly so nothing the
    stock adapter advertises (setup, cron delivery, standalone send, ...)
    regresses when this factory replaces it.
    """
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)

    from plugins.platforms.whatsapp.adapter import (
        _apply_yaml_config,
        _is_connected,
        _standalone_send,
        check_whatsapp_requirements,
        interactive_setup,
    )

    screened_cls = build_screened_adapter_class()

    def _factory(cfg, _cls=screened_cls):
        return _cls(cfg)

    # Tag the factory so assert_registry_override() can identify the winning
    # class WITHOUT constructing an adapter (which would recurse).
    _factory._shift_agent_screened_cls = screened_cls

    ctx.register_platform(
        name="whatsapp",
        label="WhatsApp",
        adapter_factory=_factory,
        check_fn=check_whatsapp_requirements,
        is_connected=_is_connected,
        required_env=["WHATSAPP_ENABLED"],
        install_hint=(
            "WhatsApp requires a Node.js bridge — see the WhatsApp messaging docs"
        ),
        setup_fn=interactive_setup,
        apply_yaml_config_fn=_apply_yaml_config,
        allowed_users_env="WHATSAPP_ALLOWED_USERS",
        allow_all_env="WHATSAPP_ALLOW_ALL_USERS",
        cron_deliver_env_var="WHATSAPP_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        max_message_length=4096,
        emoji="💬",
        allow_update_command=True,
    )
    logger.info(
        "shift-agent-policy: registered screened WhatsApp adapter + "
        "pre_gateway_dispatch sender-context hook"
    )
