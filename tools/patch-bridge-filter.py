#!/usr/bin/env python3
"""Patch bridge.js to bypass the chatter filter for template-rendered messages.
Idempotent (uses BEGIN/END markers). Run as root on VPS."""
import sys

BRIDGE = "/root/.hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js"

NEEDLE = """  if (FILTER_OWNER_JID && chatId === FILTER_OWNER_JID) {
    return { text: message, redacted: false, drop: false, reason: 'owner_bypass' };
  }"""

ADDITION = """
  // BEGIN shift-agent-template-bypass
  // Template-rendered messages start with the medical-staff emoji + bold
  // agent name + horizontal rule, e.g.:
  //   "⚕ *Catering Agent*
  //   ────────────
  //   ..."
  // Those messages come from deterministic agent scripts (create-catering-lead,
  // send-coverage-message, send-daily-brief, apply-catering-owner-decision)
  // — single-purpose, owner-approved content. The chatter filter was tuned
  // for unprefixed Hermes LLM responses; bypass for template-rendered.
  if (/^\\u2695 \\*[A-Za-z][A-Za-z ]*\\*\\n[\\u2500\\-]+\\n/.test(message)) {
    return { text: message, redacted: false, drop: false, reason: "template_bypass" };
  }
  // END shift-agent-template-bypass"""


def main() -> int:
    text = open(BRIDGE).read()
    if "shift-agent-template-bypass" in text:
        print("ALREADY PATCHED — no-op")
        return 0
    n = text.count(NEEDLE)
    if n == 0:
        # Hermes >= 0.12.0 removed the chatter filter entirely; the anchor
        # `owner_bypass` no longer exists in bridge.js. The template-bypass
        # patch is OBSOLETE for these versions — exit 0 so the deploy script
        # treats it as a successful no-op rather than a failure. The
        # check-shift-agent-patch.sh marker check is correspondingly skipped
        # when the chatter-filter symbols are absent.
        print("SKIP: chatter-filter anchor absent (Hermes >=0.12.0); patch is obsolete — no-op")
        return 0
    if n != 1:
        print(f"FAIL: anchor 'owner_bypass' found {n} times — refusing patch", file=sys.stderr)
        return 1
    text = text.replace(NEEDLE, NEEDLE + ADDITION, 1)
    open(BRIDGE, "w").write(text)
    print("PATCHED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
