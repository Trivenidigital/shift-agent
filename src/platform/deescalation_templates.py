"""Curated de-escalation reply set — data, not machinery (P0-3b).

Portfolio-wide (all 25+ agents, not flyer-specific): when the front-brain
classifies an inbound as abusive / off-topic, the reply is drawn from THIS
operator-reviewable set instead of composed free-form. The plan's rationale
(§Lint realism): a deterministic tone-appropriateness lint is not achievable and
free-form-plus-word-list gives false assurance on clean-but-dismissive replies —
curated templates collapse abuse handling to a solved problem.

Every template is warm, professional, a one-line de-escalation + an offer to
help. NO argument, NO matching energy. Each passes
customer_copy_policy.enforce_free_form_text (no operational promise, no
completion claim) — verified by tests.

Design mirrors style_registers.py: CLOSED vocabulary + fail-closed selector.
The abuse-class LABEL is produced by the Phase-1 classification wiring (LLM-side,
lands in Phase 1); this module only owns the set + the selector.
"""
from __future__ import annotations

# Abuse classes the selector understands. Anything else fails closed to DEFAULT.
ABUSE_CLASSES: tuple[str, ...] = ("hostile", "profane", "threatening", "spam_offtopic")

DEFAULT_DEESCALATION_CLASS = "default"

# One curated reply per class. Kept short, calm, and forward-looking. None make
# an operational promise or a completion claim, so they all clear the outbound
# enforcement screen (self-consistency test).
DEESCALATION_REPLIES: dict[str, str] = {
    "hostile": (
        "I'm sorry this has been frustrating. I'm here to help — tell me what "
        "you need and I'll do my best to make it right."
    ),
    "profane": (
        "I do want to help you get this resolved. Let me know what you're "
        "looking for and I'll take it from there."
    ),
    "threatening": (
        "I hear that you're upset, and I want to help. Please tell me what you "
        "need and I'll do what I can for you."
    ),
    "spam_offtopic": (
        "Thanks for reaching out! I can help with flyers, menus, and your "
        "account — what can I do for you today?"
    ),
    DEFAULT_DEESCALATION_CLASS: (
        "I'm here to help. Tell me what you need and I'll do my best for you."
    ),
}


def select_deescalation_reply(abuse_class: str) -> str:
    """Return the curated de-escalation reply for `abuse_class`. Fail-closed:
    an unknown / empty class returns the DEFAULT reply (never an argument, never
    a guess), mirroring style_registers' unknown->default discipline."""
    key = (abuse_class or "").strip().lower()
    return DEESCALATION_REPLIES.get(key, DEESCALATION_REPLIES[DEFAULT_DEESCALATION_CLASS])
