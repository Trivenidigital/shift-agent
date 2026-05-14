from __future__ import annotations

import ast
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
NOTIFY_SCRIPT = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-notify-owner"


def _whatsapp_fallback_json_keys() -> set[str]:
    tree = ast.parse(NOTIFY_SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "whatsapp_fallback":
            for call in ast.walk(node):
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "dumps"
                ):
                    continue
                if not call.args or not isinstance(call.args[0], ast.Dict):
                    continue
                keys = call.args[0].keys
                if all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in keys):
                    return {k.value for k in keys}  # type: ignore[union-attr]
    raise AssertionError("could not find json.dumps payload in whatsapp_fallback")


def test_whatsapp_fallback_uses_bridge_send_payload_contract():
    """Bridge /send requires {"chatId": ..., "message": ...}.

    The old {"jid": ..., "text": ...} shape returns HTTP 400
    "chatId and message are required", which disables the last-resort
    operator alert path when Pushover is unavailable.
    """
    assert _whatsapp_fallback_json_keys() == {"chatId", "message"}
