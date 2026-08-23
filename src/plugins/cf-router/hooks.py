"""cf-router hook implementations.

`pre_gateway_dispatch` runs BEFORE the LLM sees the inbound message. We use
this to bypass the LLM entirely for deterministic owner-approval and
menu-decision flows (F8 replacement), to fire alerts on detected sick-call
patterns (F9 replacement), and to schedule a 30s rescue check for missed
catering inquiries (F7 replacement, PR-CF7).

Hook signature (per Hermes gateway/run.py:4197+):
    pre_gateway_dispatch(event, gateway, session_store) -> dict | None

Return value is one of:
    {"action": "skip", "reason": "..."}        — drop message, no LLM call
    {"action": "rewrite", "text": "..."}       — replace event.text, continue
    {"action": "allow"}                         — explicit allow (same as None)
    None                                        — normal dispatch, no override

Multi-plugin: gateway iterates results; first action != "allow" wins.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

from . import actions

# PR-ζ F8 2026-05-26: pre-import ActionExecutionContext at module top so any
# sys.path / packaging quirk in the Hermes plugin load environment surfaces
# LOUD at plugin-load time, not silently at first regulated change_plan send.
# Both reviewers (§11-compatibility #5 + security/money-flow #3) flagged the
# previous lazy try/except as a silent-lint-bypass surface — if the import
# failed, the code fell back to action_ctx=None which is allowlist-matched
# (actions.py/hooks.py basenames) → lint silently skipped on a regulated
# reply. Moving the import to module top means an import failure breaks the
# plugin at load time (operator sees in journalctl), not at first regulated
# send (customer silently misses the lint protection).
actions._ensure_platform_path()  # type: ignore[attr-defined]
from schemas import ActionExecutionContext  # type: ignore  # noqa: E402
# PR-R1: centralized approval-code pool contract (canonical resolve order +
# fail-closed cross-pool collision refusal). Imported flat like schemas above so
# an import failure surfaces LOUD at plugin-load time, not at first #code send.
import approval_code_pools  # type: ignore  # noqa: E402
# PR-R2A: durable Branch-B amendment capture (sidecar store). Imported flat like
# approval_code_pools above so an import failure surfaces LOUD at plugin-load
# time, not silently at the first suppressed amendment.
import catering_amendments  # type: ignore  # noqa: E402
# M1: deterministic catering field extraction + minimum-qualification state.
# Imported flat like the modules above so an import failure surfaces LOUD at
# plugin-load time rather than at the first catering inbound. Both are pure
# stdlib (regex + dict shaping) — no IO, no LLM, no network.
import catering_extraction  # type: ignore  # noqa: E402
import catering_qualification  # type: ignore  # noqa: E402
# PR-5: deterministic per-conversation automation-control kernel (STOP/pause/
# opt-out + human takeover). Imported flat like the modules above so an import
# failure surfaces LOUD at plugin-load time. DORMANT behind
# CATERING_AUTOMATION_CONTROL_ENABLED + allowlist — byte-identical when OFF.
import automation_control  # type: ignore  # noqa: E402

# PR-ζ.1b 2026-05-26 — registry + helpers for the migrated send-path
# callsites. Deployed-flat-module fallback mirrors safe_io.py:815-818 +
# intent.py:18-21 — on the VPS the module lives at
# /opt/shift-agent/flyer_action_registry.py (flat-renamed by
# shift-agent-deploy.sh), NOT under an agents/flyer/ package.
try:
    from agents.flyer.action_registry import (  # type: ignore  # noqa: E402
        ACCOUNT_ACTIONS,
        PROJECT_ACTIONS,
        build_action_context,
        build_action_context_for_command,
    )
except ImportError:  # pragma: no cover - deployed flat-module fallback
    from flyer_action_registry import (  # type: ignore  # noqa: E402
        ACCOUNT_ACTIONS,
        PROJECT_ACTIONS,
        build_action_context,
        build_action_context_for_command,
    )

# === F7 path config (PR-CF7 → PR-CF1d primary-mode 2026-05-12) ===
#
# F7_ENABLED is the rollback hatch. To disable the F7 plugin path without
# touching F8/F9, edit this file in place and restart hermes-gateway:
#   sudo sed -i 's/^F7_ENABLED = True/F7_ENABLED = False/' \
#     /root/.hermes/plugins/cf-router/hooks.py
#   sudo systemctl restart hermes-gateway
F7_ENABLED = True

# PR-CF1d 2026-05-12: F7 is now PRIMARY-MODE. cf-router intercepts catering
# customer inquiries INSIDE pre_gateway_dispatch and invokes
# create-catering-lead directly. LLM never sees the inbound. Replaces the
# prior rescue-mode after Phase 11 adversarial test reproduced May 3 HARD
# RULES violations (Kimi composed fabricated proposals + per-person prices
# under customer pressure).
#
# F7_PRIMARY_FOLLOWUP_REPLY: controls whether Branch B (customer has active
# lead → suppress follow-up) sends a canonical UX-mitigation reply. With it
# True, the customer gets a hard-coded "your inquiry is with the owner"
# pointer. With it False, the follow-up is silently suppressed (the bot
# appears unresponsive to status questions).
F7_PRIMARY_FOLLOWUP_REPLY = True

# Task 6 proposal branch flag. Default off preserves Branch B's pinned
# suppression behavior until the proposal workflow is explicitly enabled.
F7_PROPOSAL_BRANCH_ENABLED = True

# M1 2026-07-31: minimum-qualification gate + deterministic slot-filling loop.
# Enabled, a new inquiry below catering_qualification.REQUIRED_FIELDS parks in
# QUALIFYING and the customer is asked for the gaps, instead of an owner approval
# card firing for an event whose date, size, venue and style are unknown; a reply to
# those questions is applied to the SAME lead. Disabled, every catering path behaves
# exactly as it did before M1 (create-catering-lead's gate is opt-in, so flag-off
# simply never passes --qualification-gate and never routes an answer).
#
# DEFAULT ON — this behavior is disclosed and accepted. The env var exists as a
# rollback lever that needs no source edit and no redeploy:
#   sudo systemctl set-environment CATERING_QUALIFICATION_GATE=0 \
#     && sudo systemctl restart hermes-gateway
CATERING_QUALIFICATION_GATE_ENV = "CATERING_QUALIFICATION_GATE"

# F7 ACCEPTANCE ARM (M3) — see _try_catering_acceptance_intercept for what it does.
# DEFAULT OFF: it BOOKS an event off a customer message, and no operator has armed
# it. It stays dormant (byte-identical routing) until one explicitly does:
#   sudo systemctl set-environment CATERING_ACCEPTANCE_ARM=1 \
#     && sudo systemctl restart hermes-gateway
CATERING_ACCEPTANCE_ARM_ENV = "CATERING_ACCEPTANCE_ARM"


# Read at CALL time, mirroring the automation_control kernel's flag helpers
# (stop_enabled / takeover_enabled): a module-load snapshot cannot be flipped
# without re-importing the plugin, and it makes the flag untestable from the
# environment the operator actually sets.
def f7_qualification_gate_enabled() -> bool:
    return os.environ.get(CATERING_QUALIFICATION_GATE_ENV, "1") == "1"


def f7_acceptance_arm_enabled() -> bool:
    return os.environ.get(CATERING_ACCEPTANCE_ARM_ENV, "0") == "1"

# 30s rescue window — matches the deployed F7 daemon's WATCHDOG_TIMEOUT_SECS.
# PRESERVED (not removed) for backwards-compat with TestF7DispatcherWatchdog,
# but no longer wired into pre_gateway_dispatch. f7_rescue_check() +
# _schedule_f7_rescue() remain callable directly. Cleanup deferred to a
# follow-up PR after F7 primary-mode soaks in production.
F7_WATCHDOG_TIMEOUT_SEC = 30

# Owner-approval code regex — same alphabet as ProposalCode
# (`#[A-HJKMNPQR-Z2-9]{5}`, excluding I/O/0/1/L).
# No IGNORECASE: codes are emitted uppercase by generate_unique_code; the
# dispatcher rejects lowercase, so matching lowercase here just adds surface.
_CODE_PATTERN = re.compile(r"#([A-HJKMNPQR-Z2-9]{5})")
_SAMPLE_PROMPT_REQUEST = re.compile(
    r"\b(?:sample|example|starter)\s+(?:prompt|prompts|idea|ideas)\b"
    r"|\b(?:prompt|prompts)\s+(?:example|examples)\b"
    r"|\b(?:template|templates)\s+(?:idea|ideas|example|examples)\b"
    r"|\b(?:example|sample)\s+(?:flyer|flier|poster|marketing|ad)\s+(?:text|copy|caption|captions|line|lines|template|templates)\b"
    r"|\b(?:sample|example|starter|idea|ideas|inspiration)\b.{0,40}\b(?:for|of)\b.{0,20}\b(?:flyer|flier|poster|marketing)\b"
    r"|\b(?:give|send|show|share|suggest|provide|help|need)\b.{0,50}\b(?:flyer|flier|poster|marketing|ad|ads)\b.{0,30}\b(?:idea|ideas|prompt|prompts|examples|inspiration|hook|hooks|caption|captions|copy|copies|line|lines|text|template|templates|tagline|taglines|slogan|slogans|punchline|punchlines|option|options)\b"
    r"|\b(?:give|send|show|share|suggest|provide|help)\b.{0,60}"
    r"\b(?:sample|example|starter|inspiration)?\s*(?:prompt|prompts|idea|ideas|examples|inspiration|hook|hooks|caption|captions|copy|copies|line|lines|text|template|templates|tagline|taglines|slogan|slogans|punchline|punchlines|option|options)\b.{0,60}"
    r"\b(?:flyer|flier|poster|marketing|ad|ads)\b"
    r"|\b(?:give|send|show|share|suggest|provide|need|help)\b.{0,70}"
    r"\b(?:ad|ads|promo|promotion|promotional|campaign|marketing|creative)\b.{0,40}"
    r"\b(?:idea|ideas|suggestion|suggestions|concept|concepts|example|examples|prompt|prompts|inspiration|hook|hooks|caption|captions|copy|copies|line|lines|text|template|templates|tagline|taglines|slogan|slogans|punchline|punchlines|option|options)\b.{0,40}"
    r"\b(?:business|shop|store|brand|service|offer)\b"
    r"|\b(?:help|need|suggest|share|give|show|send|provide)\b.{0,25}"
    r"\b(?:promotion|promo|campaign|marketing|weekend|offer)\b.{0,25}"
    r"\b(?:idea|ideas|prompt|prompts|example|examples|hook|hooks|caption|captions|copy|copies|line|lines|text|template|templates|tagline|taglines|slogan|slogans|punchline|punchlines|option|options)\b"
    r"|\b(?:help|need|suggest|share|give|show|send|provide)\b.{0,30}"
    r"\b(?:\d+\s+)?(?:idea|ideas|prompt|prompts|example|examples|hook|hooks|caption|captions|copy|copies|line|lines|text|template|templates|tagline|taglines|slogan|slogans|punchline|punchlines|option|options)\b.{0,40}"
    r"\b(?:for|about|on)\b.{0,30}\b(?:weekend|offer|promotion|promo|campaign|marketing|flyer|business|shop|store|poster|ad|ads)\b"
    r"|\b(?:what(?:'s| is| are)|any)\b.{0,40}\b(?:promo|promotion|marketing|campaign|flyer|flier|poster|ad|ads|business|shop|store|offer)\b.{0,40}\b(?:idea|ideas|caption|captions|copy|copies|prompt|prompts|tagline|taglines|slogan|slogans|punchline|punchlines|option|options)\b"
    r"|\bwhat\s+can\s+you\s+suggest\b.{0,60}\b(?:for|about|on)\b.{0,40}\b(?:flyer|flier|poster|ad|ads|promo|promotion|campaign|offer|business|shop|store)\b"
    r"|\bwhat\s+should\s+i\s+write\b.{0,60}\b(?:for|about|on)\b.{0,40}\b(?:flyer|flier|poster|ad|ads|promo|promotion|campaign|offer)\b"
    r"|\bsample\s+(?:flyer|flier|poster|ad|marketing)\s+request\b"
    r"|\bwhat\s+should\s+(?:be|i\s+put)\s+on\s+(?:my|the|this)\s+(?:flyer|flier|poster|ad)\b"
    r"|\bsuggest\b.{0,30}\b(?:flyer|flier|poster|ad)\b.{0,20}\b(?:wording|wordings|copy|caption|captions|text|line|lines)\b"
    r"|\bneed\b.{0,20}\bideas?\b.{0,15}\bfor\b.{0,10}\bcaptions?\b"
    r"|\bcan\s+i\s+get\b.{0,20}\b(?:flyer|flier|poster|ad)\b.{0,20}\bideas?\b",
    re.IGNORECASE,
)
_ACTIVE_PROJECT_SAMPLE_IDEA_ITERATION = re.compile(
    r"\banother\b.{0,35}"
    r"\b(?:flyer|flier|poster|design|concept|idea|version|option)\b"
    r"|\b(?:flyer|flier|poster|design|concept|idea)\b.{0,35}"
    r"\banother\b.{0,20}"
    r"\b(?:idea|design|concept|version|option)\b"
    r"|\b(?:reroll|re-roll|regenerate|generate\s+again|try\s+again|redo)\b",
    re.IGNORECASE,
)


def _flyer_request_excerpt_for_reply(text: str, *, limit: int = 140) -> str:
    excerpt = " ".join(actions.flyer_visible_message_text(text).split())
    if len(excerpt) > limit:
        excerpt = excerpt[: limit - 3].rstrip() + "..."
    return excerpt


def _sample_prompt_request_should_yield_to_active_project(body: str) -> bool:
    """Return True only for idea wording that clearly iterates an active flyer."""
    return bool(_ACTIVE_PROJECT_SAMPLE_IDEA_ITERATION.search(body or ""))


def _fb_sample_prompt_request_matches(text: str) -> bool:
    """Pure, side-effect-free predicate mirroring the top gates of
    `_try_flyer_sample_prompt_request_intercept` (regex + preference-command +
    brief-detail). Used ONLY to decide whether a front-brain yield at the
    sample-prompt site is meaningful enough to record a marker row — it never
    gates behavior, so an imperfect match just changes whether a traceability
    row is written, never whether the LLM runs."""
    try:
        if actions.is_flyer_starter_prompt_preference_command(text):
            return False
        body = " ".join(actions.flyer_visible_message_text(text).split())
        if not _SAMPLE_PROMPT_REQUEST.search(body):
            return False
        if actions.flyer_message_has_brief_detail(text):
            return False
        return True
    except Exception:
        return False


def _fb_yield_missing_info(chat_id: str, message_id: str) -> bool:
    """Front-brain Phase-1: for CONVERSE-cohort chats, yield the deterministic
    "I need a few more details" clarification (`flyer_project_missing_info_reply`)
    to Hermes so the gateway LLM + flyer_intake SKILL drive the clarifying
    conversation, instead of sending the fixed net reply.

    This is the REAL vague-brief lever on an established customer's traffic —
    the Phase-1 cold-start yields (sample-prompt / intake-followup / vague-start)
    never fire for a trial customer with an active project (incident 2026-07-12:
    "Create a flyer for Saturday" routed active-project-bypass -> primary-create
    -> deterministic missing-info reply, ZERO front_brain_yielded rows).

    Emits the `front_brain_yielded` marker so the hand-off is traceable on the
    Phase-1 review surface. Returns True when the caller should yield (return
    None to the gateway); False -> caller sends the deterministic reply unchanged.
    Fail-CLOSED via `front_brain_converse_admits` (flag off / non-cohort / any
    error -> False -> byte-identical deterministic net, test-enforced)."""
    if not actions.front_brain_converse_admits(chat_id):
        return False
    actions.audit_front_brain_yielded(
        chat_id, intercept="missing_info", message_id=message_id)
    return True


# Verb classifier — mirrors the F8 watchdog's accepted verb set so plugin
# coverage matches the watchdog it replaces. Past-tense forms ("approved",
# "rejected") are common in owner replies and were handled by the watchdog.
_VERB_APPROVE = re.compile(r"\b(approve|approved|yes|send|ok|go|send it)\b", re.IGNORECASE)
_VERB_REJECT = re.compile(r"\b(reject|rejected|no|decline|pass|cancel)\b", re.IGNORECASE)
_VERB_EDIT = re.compile(r"\b(edit|change|modify)\b", re.IGNORECASE)

# Shift coverage verbs are scoped to the POOL_SHIFT branch because the Shift
# owner card asks for a DIFFERENT grammar than the catering card does:
#
#     Reply {code} to approve + send, or "DENY {code}" to reject.
#     (Type STATUS for all pending.)
#         -- src/agents/shift/templates/proposal_to_owner.txt
#
# Two consequences, both load-bearing. `DENY` is in NO existing verb set —
# _VERB_REJECT is (reject|rejected|no|decline|pass|cancel) — so the exact word
# the card tells the owner to type matches nothing today, and a bare-code-
# approves rule without this set would read "DENY #ABCDE" as an APPROVAL and
# message a real employee. And the bare code has to mean approve, because that
# is what the card asks for; requiring a verb would leave the leg as dead as it
# is now for every owner who followed the instruction printed on the card.
_SHIFT_VERB_DENY = re.compile(
    r"\b(deny|denied|denies|reject|rejected|decline|declined|no|nope|pass)\b", re.IGNORECASE)
_SHIFT_VERB_CANCEL = re.compile(r"\b(cancel|cancelled|canceled)\b", re.IGNORECASE)
_SHIFT_VERB_RETRY = re.compile(r"\b(retry)\b", re.IGNORECASE)

# Everything that is not the code itself, once the code is removed: whitespace
# and the punctuation a phone keyboard adds around a pasted code. If anything
# else survives, the message is not a bare code and this branch will not read
# it as an approval.
_SHIFT_BARE_CODE_RESIDUE = re.compile(r"^[\s.,!:;\-–—*_\"'()\[\]]*$")

# The ONLY proposal status an owner code may approve. Deliberately not a set:
# `send_failed` is resumed with RETRY (a separate command this branch does not
# implement), and every other status is either mid-flight or terminal.
_SHIFT_APPROVABLE_STATUS = "awaiting_owner_approval"

# Mirrors exit_codes.EXIT_TRUTH_GUARD_FAILED. The plugin deliberately does not
# import the Shift platform module (it loads from the Hermes plugin dir, not
# /opt/shift-agent), so the value is restated here rather than left as a bare
# literal at the call site. TestF8OwnerApproveNonFinalizedReachable in
# tests/test_cf_router_plugin.py asserts it stays equal to the canonical value.
_EXIT_TRUTH_GUARD_FAILED = 11


def _is_unqualified_shift_consent(text: str, code: str) -> bool:
    """True when the owner said yes to THIS proposal and nothing else.

    Strips the code, then strips the approval verbs, then requires that nothing
    of substance is left. `#ABCDE`, `#ABCDE approve`, `approve #ABCDE` and
    `yes #ABCDE` all reduce to nothing and are consent.

    The verb strip is the load-bearing half, and an earlier revision got it
    wrong by testing `has_approve OR bare-code`, which let any message
    containing an approval word skip the residue check entirely. That approved
    AND SENT on `"#ABCDE yes but ask someone else"`, `"yes #ABCDE tomorrow
    instead"` and `"ok #ABCDE but only if she agrees"` — a coverage ask to the
    ORIGINAL candidate about the ORIGINAL date, which is the opposite of what
    the owner asked for in each case. It was also self-inconsistent: `"#ABCDE
    change the candidate"` was already refused by the edit verb.

    A half-agreement is not an agreement. Anything left over after the code and
    the verb are removed means the owner said something this branch cannot act
    on, and the only safe reading of that is to not send.
    """
    residue = re.sub(re.escape(code), "", text, flags=re.IGNORECASE)
    residue = _VERB_APPROVE.sub("", residue)
    return bool(_SHIFT_BARE_CODE_RESIDUE.match(residue))


# ─── candidate reply grammar ────────────────────────────────────────────────
#
# The words come from the message she is actually sent, not from taste:
#
#     "Can you cover the {absent_shift} {absent_role} shift? Reply YES or NO."
#         -- src/agents/shift/templates/coverage_message_to_candidate.txt
#
# so YES and NO must work, and everything else is a bonus that must not cost
# safety. The risk is asymmetric — reading a refusal as consent puts a real
# person on a shift she said she could not work — so anything that is not an
# unqualified answer falls through to the LLM, which is exactly today's
# behaviour and therefore not a regression.
#
# SKILL.md lists Hindi/Telugu/Tamil affirmatives too. Those are deliberately NOT
# here: this is deterministic code owning an irreversible state change, and a
# multilingual guess belongs to Hermes, which still sees every message this
# branch declines.
_CANDIDATE_YES_TOKENS = frozenset({
    "yes", "y", "yep", "yeah", "yup", "ok", "okay", "sure", "confirmed", "👍",
})
_CANDIDATE_NO_TOKENS = frozenset({
    "no", "n", "nope", "nah", "cant", "cannot", "unable", "declined", "👎",
})
# Politeness and bare pronouns/copulas that carry no answer. Stripped so
# "yes please", "no thanks" and "I can't" all reduce to a single answer token.
# Deliberately excludes "ok"/"sure" — those ARE answers and live in
# _CANDIDATE_YES_TOKENS.
_CANDIDATE_FILLER_TOKENS = frozenset({
    "please", "pls", "thanks", "thank", "ty", "sir", "maam", "madam",
    "i", "im", "ill", "id", "am", "it", "its", "that", "this",
})
# A qualified answer is not an answer. "yes if my sister can babysit" and
# "yes but only until 3" are commitments the owner cannot act on.
#
# "not" earns its place the hard way: the table caught `"not sure yet"`
# classifying as ACCEPTED, because "sure" is an affirmative token and nothing
# was cancelling it. That is precisely the refusal-read-as-consent class this
# branch exists to avoid, so a negator anywhere in the reply makes it ambiguous.
_CANDIDATE_QUALIFIER_TOKENS = frozenset({
    "if", "unless", "but", "only", "maybe", "might", "depends", "unsure",
    "probably", "perhaps", "possibly", "later", "tomorrow", "after", "before",
    "not", "dont", "doubt", "sorry",
})
# "no problem" / "no worries" mean the OPPOSITE of the refusal token they
# contain. Neutralised before tokenising so they cannot be read as a decline;
# what is left rarely reduces to a bare answer, so these land in the LLM's lap.
_CANDIDATE_NEGATION_TRAPS = re.compile(
    r"\bno\s+(?:problem|worries|prob|probs|issue|issues)\b", re.IGNORECASE)
_CANDIDATE_WORD = re.compile(r"[\w']+|[\U0001F300-\U0001FAFF]", re.UNICODE)


def _classify_candidate_reply(text: str) -> Optional[str]:
    """`"accepted"`, `"declined"`, or None when the reply is not an answer.

    None is the safe default and the common case: it changes no state and lets
    the turn continue to the LLM exactly as it does today.
    """
    body = _CANDIDATE_NEGATION_TRAPS.sub(" ", text or "")
    if "?" in body:
        # A question is a request for information, never a commitment.
        return None
    tokens = [t.lower().replace("'", "") for t in _CANDIDATE_WORD.findall(body)]
    tokens = [t for t in tokens if t and t not in _CANDIDATE_FILLER_TOKENS]
    # THE RULE: after stripping fillers, the reply must reduce to EXACTLY ONE
    # token, and that token must be an answer. Anything else is not an answer to
    # "Reply YES or NO" and is left to the LLM.
    #
    # An earlier revision instead allowed an answer token anywhere in the first
    # two tokens of a short message, with no requirement that the rest be empty.
    # That accepted `"ok im busy"`, `"ok never mind"`, `"ok ask someone else"`,
    # `"yes im busy"` and `"ok call me"` — each one a refusal recorded as
    # consent, which is the exact direction this function exists to prevent, and
    # IRREVERSIBLE: `LEGAL_TRANSITIONS["accepted"]` is empty, so no operator
    # command, sweep or fsck can undo a wrong accept. It is the same mistake
    # `_is_unqualified_shift_consent` documents on the owner side — testing for
    # the presence of a verb instead of the absence of everything else.
    if len(tokens) != 1:
        return None
    token = tokens[0]
    # Belt and braces behind the exactly-one rule: a single qualifier ("maybe")
    # is not an answer, and these also keep firing if the filler set is ever
    # widened in a way that would strip a word out of a longer reply.
    if token in _CANDIDATE_QUALIFIER_TOKENS:
        return None
    if token in _CANDIDATE_NO_TOKENS:
        return "declined"
    if token in _CANDIDATE_YES_TOKENS:
        return "accepted"
    return None


def _try_candidate_response(
    *, text: str, chat_id: str, message_id: str,
) -> Optional[dict]:
    """Record a covering employee's YES/NO against her coverage proposal.

    Closes the leg that had no code at all. `handle_candidate_response` exists
    only as a SKILL.md, and the SKILL dispatcher needs the `skills` toolset that
    is disabled on the box — so before this, her reply reached nothing and
    `shift-agent-proposal-sweep` told the owner 30 minutes later that she never
    responded. #734 made that newly reachable by wiring the owner's approval.

    Recording the reply is the whole job here. The transition out of `sent` is
    what makes the sweep's existing guard skip the proposal, which is what stops
    the false "she never replied" alert. This function sends nothing: it never
    resolves a phone number and never composes a body.

    Identity binding is `candidate_employee_id`, resolved through
    `identify_sender_metadata` — the same authority `has_pending_candidate_response`
    uses. A sender identify-sender cannot place has no employee_id, so the lookup
    returns nothing and this refuses. Deliberately NOT also gated on
    `is_verified_employee_chat`: that adds an active-roster check, and an
    employee deactivated between being asked and answering should still have her
    answer recorded rather than silently dropped. The proposal naming her id is
    the tighter binding anyway.

    Fail-closed in five places, none of which mutate anything: a sender with no
    resolvable employee_id, a reply that is not an unqualified YES or NO, a
    candidate with no `sent` proposal, a candidate with more than one, and a
    kernel that refuses the transition. Idempotence against a double reply comes
    from the same two guards the owner-side branch uses — the first reply moves
    the proposal out of `sent` so the second finds nothing, and the kernel
    refuses the illegal transition under the pending lock for the race that
    check cannot see.
    """
    verdict = _classify_candidate_reply(text)
    if verdict is None:
        return None

    proposal_ids = actions.sent_proposal_ids_for_candidate(chat_id)
    if len(proposal_ids) != 1:
        # Zero: nothing is awaiting her answer, so a stray "yes" in normal
        # conversation must not move state. More than one: her "yes" does not
        # say WHICH shift, and picking would record the wrong one as covered.
        return None
    proposal_id = proposal_ids[0]

    actions.audit_dispatcher_routed(
        message_id=message_id,
        chat_id=chat_id,
        routed_to_skill="handle_candidate_response",
        message_shape="text",
    )

    # Read the shift details BEFORE the transition — the alert names the shift
    # being covered, not the new status.
    fields = actions.proposal_notify_fields(proposal_id)

    rc = actions.invoke_update_proposal_status(
        proposal_id,
        verdict,
        cause="candidate_accepted" if verdict == "accepted" else "candidate_declined",
        actor="candidate",
        response_message=text,
    )
    if rc != 0:
        # Illegal transition, unknown proposal, or lock timeout. Nothing was
        # recorded, so do not report it as handled and do not page the owner
        # about an outcome that was not written — fall through instead.
        actions.audit_intercepted(
            reason="error",
            chat_id=chat_id,
            subprocess_rc=rc,
            detail=(f"candidate_response_transition_failed; proposal_id={proposal_id}; "
                    f"verdict={verdict}"),
        )
        return None

    _notify_owner_of_candidate_verdict(verdict, proposal_id, fields)
    return {"action": "skip",
            "reason": (f"cf-router candidate reply: invoked "
                       f"update-proposal-status {verdict} for {proposal_id} (rc=0)")}


def _notify_owner_of_candidate_verdict(
    verdict: str, proposal_id: str, fields: dict,
) -> None:
    """Tell the owner the outcome. Best-effort, AFTER the state change.

    This exists because recording the reply SUPPRESSES something. Moving the
    proposal out of `sent` is what stops the false "she never replied" page —
    and that page was also the only real-time signal that the shift still needs
    covering. Removing it without replacing it would be a net regression: a
    mis-attributed page still gets a human to arrange coverage, silence does
    not.

    Deterministic f-string, never LLM text, and fired only after rc 0 so it can
    never announce an outcome the kernel refused to write. Phrasing and priority
    mirror `_sweep_one`'s uncovered-shift alert, which is the message this one
    replaces for the decline case.

    Best-effort by construction: the state change is the primary operation, and
    `fire_pushover_alert` swallows its own failures. A Pushover outage must not
    turn a recorded reply back into an unrecorded one.
    """
    who = fields.get("candidate_name") or "The chosen replacement"
    role = fields.get("absent_role") or "the"
    date = fields.get("absent_date") or "the requested date"
    shift = fields.get("absent_shift") or ""
    when = f"{date} ({shift})" if shift else date
    try:
        if verdict == "declined":
            actions.fire_pushover_alert(
                "Coverage still needed",
                (f"{who} declined the coverage request for the {role} shift on "
                 f"{when}. That shift is still uncovered — please arrange "
                 f"coverage manually."),
                priority=1,
            )
        else:
            actions.fire_pushover_alert(
                "Coverage confirmed",
                (f"{who} accepted the coverage request for the {role} shift on "
                 f"{when}. That shift is now covered."),
                priority=0,
            )
    except Exception:  # noqa: BLE001 — never lose a recorded reply to an alert
        # `fire_pushover_alert` already swallows and logs its own failures, so
        # this outer guard is only here so a future change to that contract
        # cannot turn an already-recorded reply into a dropped turn. The state
        # change has committed by this point; there is nothing to roll back and
        # nothing worth failing the turn over.
        pass


# Sick-call regex set (employee path — F9 replacement). Mirrors the six
# absence-specific patterns from the old notifier. Broad courtesy/address
# patterns were removed because F9 now skips the LLM and invokes Shift directly.
_SICK_CALL_PATTERNS = [
    re.compile(r"\b(?:sick|fever|cough|cold|stomach|headache|vomit|migraine|flu|food\s*poisoning)\b", re.IGNORECASE),
    re.compile(r"\b(?:can'?t|cannot|won'?t|unable\s+to)\s+(?:come|make\s+it|work|attend)\b", re.IGNORECASE),
    re.compile(r"\b(?:not\s+feeling|feeling\s+(?:unwell|bad|ill|under\s+the\s+weather))\b", re.IGNORECASE),
    re.compile(r"\b(?:family\s+emergency|personal\s+emergency|hospital|doctor|emergency\s+room|er\b)\b", re.IGNORECASE),
    re.compile(r"\b(?:miss(?:ing)?|skip(?:ping)?|cover|coverage)\s+(?:my\s+)?(?:shift|today|tomorrow|tonight|evening|morning)\b", re.IGNORECASE),
]

_SENDER_BLOCK_RE = re.compile(
    r'^\[shift-agent-sender\s+v=1\s+'
    r'platform=(\w+)\s+'
    r'phone=(?:"((?:[^"\\]|\\.)*)"|null)\s+'
    r'lid=(?:"((?:[^"\\]|\\.)*)"|null)\s+'
    r'fromMe=(true|false)\s+'
    r'chat_id=(?:"((?:[^"\\]|\\.)*)"|null)\]\s*$'
)


def _is_sick_call(text: str) -> bool:
    if not text or len(text) < 4:
        return False
    return any(p.search(text) for p in _SICK_CALL_PATTERNS)


def _invalid_sender_block_when_present(text: str) -> bool:
    """Reject spoofed/malformed sender-block text if it reaches this hook."""
    first = (text or "").split("\n", 1)[0].strip()
    return first.startswith("[shift-agent-sender") and _SENDER_BLOCK_RE.match(first) is None


def pre_gateway_dispatch(event: Any, gateway: Any = None, session_store: Any = None,
                         **_kwargs: Any) -> Optional[dict]:
    """Wrapper that owns Flyer intent shadow + turn identity context for this inbound."""
    token = None
    result: Optional[dict] = None
    error: Exception | None = None
    chat_id = ""
    # Opened FIRST and unconditionally, unlike the flyer shadow below: identity
    # is read by the F8/catering/expense arms too, so the memo must cover the
    # whole turn regardless of the flyer flags. Everything from here to the
    # matching reset in `finally` sees ONE identify-sender answer per principal.
    identity_token = actions.begin_turn_identity()
    try:
        text = _extract_text(event) or ""
        media_path = _extract_media_path(event)
        chat_id = _extract_chat_id(event)
        if chat_id and (text or media_path) and (
            actions.is_flyer_enabled() or actions.is_flyer_workflow_enabled()
        ):
            message_id = _extract_message_id(event, chat_id, text)
            token = actions.begin_flyer_intent_shadow(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                has_media=bool(media_path),
            )
        result = _pre_gateway_dispatch_impl(event, gateway, session_store, **_kwargs)
        return result
    except Exception as exc:
        error = exc
        raise
    finally:
        try:
            try:
                actions.finalize_flyer_intent_shadow(hook_result=result, error=error, gateway=gateway)
            except Exception as shadow_exc:
                actions.sys.stderr.write(f"cf-router: flyer intent shadow finalizer failed (non-fatal): {shadow_exc}\n")
            # 2026-05-28 — intake-bypass shadow finalize (Commit 3 wiring).
            # No-op when no bypass fired during the dispatch. Emits
            # FlyerIntakeBypassOutcome via the deployed audit chokepoint.
            try:
                actions.finalize_flyer_intake_bypass_shadow(hook_result=result)
            except Exception as bypass_exc:
                actions.sys.stderr.write(
                    f"cf-router: flyer intake bypass shadow finalizer failed (non-fatal): {bypass_exc}\n"
                )
            # §12a companion to memoizing FAILED identity reads. A turn that
            # could not resolve who is speaking, and then handed the inbound to
            # the LLM, says so exactly once — otherwise the memo turns several
            # noisy partial failures into one silent total one. Only the
            # fall-through case: a raised error propagates and is already
            # visible, and a turn that CLAIMED the inbound made its decision on
            # a consistent (if unknown) identity, with the money and
            # authorization arms failing closed on their own.
            try:
                if result is None and error is None and actions.turn_identity_read_failed():
                    actions.audit_intercepted(
                        reason="identity_unresolved_turn_yielded",
                        chat_id=chat_id or "unknown",
                        detail=("identify-sender did not resolve this turn; "
                                "inbound yielded to the LLM with sender_role unknown"),
                    )
            except Exception as identity_exc:
                actions.sys.stderr.write(
                    f"cf-router: unresolved-identity audit failed (non-fatal): {identity_exc}\n"
                )
        finally:
            actions.reset_flyer_intent_shadow(token)
            actions.reset_flyer_intake_bypass_shadow(
                actions.consume_pending_flyer_intake_bypass_token()
            )
            actions.reset_turn_identity(identity_token)


def _pre_gateway_dispatch_impl(event: Any, gateway: Any = None, session_store: Any = None,
                               **_kwargs: Any) -> Optional[dict]:
    """Main hook — dispatched by Hermes for every user-originated inbound.

    Returns None for the common case (let LLM handle normally). Only returns
    a non-None action when we have HIGH CONFIDENCE the message matches a
    deterministic rescue path (F8 owner approval / menu decision) or
    needs an alert (F9 sick-call).

    All errors are caught and audited as `reason="error"` then return None;
    a misbehaving plugin never blocks the LLM.
    """
    try:
        text = _extract_text(event) or ""
        media_path = _extract_media_path(event)
        chat_id = _extract_chat_id(event)
        if (not text and not media_path) or not chat_id:
            return None
        if _extract_from_me(event):
            return None
        native_message_id = _extract_native_message_id(event)
        message_id = native_message_id or _extract_message_id(event, chat_id, text)
        if native_message_id and actions.mark_cf_router_inbound_seen(chat_id, native_message_id, text):
            return {"action": "skip", "reason": "cf-router duplicate inbound"}
        # Raw-body diagnostic capture (quoted-APPROVE prerequisite): record what
        # the bridge delivers — body head + quote/reply-shaped event attrs —
        # BEFORE any routing. Best-effort; never blocks the flow.
        actions.audit_raw_body(event, chat_id, message_id, text)
        # TURN-SCOPED SNAPSHOT of owner-receipt candidacy. Declared here so it is
        # in scope for both consumers, but deliberately NOT resolved here: the
        # lookup is an identify-sender subprocess, and resolving it at dispatch
        # entry would put that latency and failure surface on EVERY media inbound
        # — including turns a higher-priority intercept returns from long before
        # receipt-vs-brand arbitration. It is resolved lazily at the first
        # brand-asset decision instead, which is the earliest point the answer
        # can actually matter.
        owner_receipt_candidate = False
        # Sister snapshot for the menu cession, on the same terms and for the
        # same reason: the brand-asset arm is terminal and runs 50+ lines ahead
        # of `_menu_caption_cedes_to_dispatcher`, so a menu photo it claims can
        # never reach the cession. Also declared-not-resolved here; the answer
        # needs the sender role, and that identity read belongs inside the media
        # guard where the receipt candidate already lives.
        menu_caption_candidate = False
        # The scalar identity for this turn, resolved AT MOST ONCE (see the
        # media block below) and shared by the brand-asset arm, the menu
        # candidacy and `guest_phone`/`guest_role`. None means not yet read.
        turn_identity: Optional[tuple[Optional[str], str]] = None
        flyer_generation_enabled = actions.is_flyer_enabled()
        flyer_workflow_enabled = flyer_generation_enabled or actions.is_flyer_workflow_enabled()
        # P1-1 send-now-compound fix: ONE dispatch-scoped single-flight memo shared
        # by the line-456 send-now compound check and the escape gate so the
        # underlying classify_catering runs at most once per inbound (see
        # _make_classify_catering_memo — it caches + re-raises exceptions too).
        classify_catering_memo = _make_classify_catering_memo()

        # F8 path — owner self-chat + #XXXXX code → bypass LLM
        if actions.is_owner_chat(chat_id):
            f8_result = _try_f8_intercept(text, chat_id, message_id)
            if f8_result is not None:
                return f8_result

        # PR-5 automation-control kernel — INBOUND enforcement point (§5.4/§5.5).
        # DORMANT behind CATERING_AUTOMATION_CONTROL_ENABLED + allowlist; when OFF
        # the first line returns None with no state read (byte-identical). Runs
        # BEFORE all routing (F9/F7/flyer) so a suppressed conversation never
        # reaches an automated handler, while still processing the commands that
        # are the exceptions (owner takeover/release/resume; customer STOP/pause/
        # resume + the one deterministic ack). Placed AFTER F8 so #XXXXX approval
        # verbs keep precedence (takeover/release verbs never match F8's verbs).
        automation_control_result = _try_automation_control(
            text, chat_id, message_id, native_message_id)
        if automation_control_result is not None:
            return automation_control_result

        # F9 path — employee sender + sick-call regex -> deterministic Shift.
        #
        # Live regression 2026-06-07: F9 fired as alert-only, returned allow,
        # and the LLM continued in a stale mixed-domain session without invoking
        # dispatch_shift_agent or writing dispatcher_routed. Employee absence
        # traffic is an internal trust-zone route; once identity is verified
        # and absence intent is clear, do not let broad Catering/Flyer context
        # or session history decide the outcome.
        if (
            _is_sick_call(text)
            and not _invalid_sender_block_when_present(text)
            and actions.is_verified_employee_chat(chat_id)
        ):
            if actions.has_pending_candidate_response(chat_id):
                return None
            _try_f9_alert(text, chat_id)
            actions.audit_dispatcher_routed(
                message_id=message_id,
                chat_id=chat_id,
                routed_to_skill="handle_sick_call",
                message_shape="text",
            )
            rc, _out, _err = actions.invoke_shift_sick_call(
                chat_id=chat_id,
                text=text,
                message_id=message_id,
            )
            if rc != 0:
                actions.audit_intercepted(
                    reason="error",
                    chat_id=chat_id,
                    subprocess_rc=rc,
                    detail=(
                        "f9_shift_sick_call_failed; "
                        f"stdout={str(_out)[:300]!r}; stderr={str(_err)[:300]!r}"
                    ),
                )
            return {
                "action": "skip",
                "reason": f"cf-router F9: invoked handle-shift-sick-call (rc={rc})",
            }

        # Candidate reply path — the RETURN leg of the coverage loop.
        #
        # Before this branch existed the reply reached nothing and the
        # no-response sweep reported her to her employer as unresponsive 30
        # minutes later.
        #
        # Placed immediately AFTER F9. A message that is BOTH a sick call and a
        # refusal ("I can't come in tomorrow") is genuinely ambiguous, and F9
        # decides it above: when the sender has a pending coverage ask it
        # returns None and neither branch acts.
        #
        # KNOWN RESIDUAL, and it is not small: that F9 guard is a DOUBLE no-op,
        # so the most natural refusals — "cant come", "can't make it", "unable
        # to work", "no im sick" — still record nothing and still draw the false
        # 30-minute page. The classifier below would decline every one of them.
        # This branch therefore closes the loop for plain YES/NO replies and NOT
        # for sick-call-shaped ones. Fixing it means deciding which arm owns an
        # ambiguous message, which is a routing ruling, not a parser change.
        # Pinned by test_f9_preempts_sick_call_shaped_declines_end_to_end.
        candidate_result = _try_candidate_response(
            text=text, chat_id=chat_id, message_id=message_id)
        if candidate_result is not None:
            return candidate_result

        # F7 PRIMARY-MODE (PR-CF1d 2026-05-12) — non-owner +
        # catering classifier → intercept inside pre_gateway_dispatch and
        # bypass LLM entirely. Promoted from rescue-mode after Phase 11
        # adversarial test reproduced May 3 failure mode (Kimi violated
        # HARD RULES under customer pressure: composed proposals, fabricated
        # per-person prices, multi-lead creation, attempted skill_manage +
        # memory writes).
        #
        # Two branches inside _try_f7_primary_intercept:
        #   A — no active lead: invoke create-catering-lead deterministically
        #       with customer_name="" (kills the "Anjali Iyer" hallucination
        #       class); return skip
        #   B — active lead exists: optionally send canonical UX-mitigation
        #       reply; return skip (prevents multi-lead-creation bug)
        #
        # Rescue-mode helpers (_schedule_f7_rescue, actions.f7_rescue_check,
        # F7_WATCHDOG_TIMEOUT_SEC) remain in this module for backwards-compat
        # with the TestF7DispatcherWatchdog suite; they are NO LONGER wired
        # into pre_gateway_dispatch. Cleanup deferred to a follow-up PR.
        revenue_choice_result = _try_revenue_route_clarification_choice(
            text,
            chat_id,
            event,
            flyer_generation_enabled=flyer_generation_enabled,
            flyer_workflow_enabled=flyer_workflow_enabled,
        )
        if revenue_choice_result is not None:
            return revenue_choice_result

        if flyer_workflow_enabled:
            # Front-brain Phase-1 conversational cohort (2026-07-12, item 2).
            # When admitted, the THREE conversational flyer intercepts below —
            # sample-prompt, intake-followup, and vague-start — YIELD to the LLM
            # (return None here) instead of the deterministic net answering, and
            # a `front_brain_yielded` marker row is written so the yield is
            # traceable. EVERY money/#code/payment/delivery-state/brand-asset/
            # active-project guard in this block runs UNCHANGED. Flag off /
            # non-cohort -> fb_converse is False -> byte-identical deterministic
            # net (test-enforced). fb_phone/fb_role are resolved once, only for
            # admitted chats, so the non-cohort path adds zero work.
            fb_converse = actions.front_brain_converse_admits(chat_id)
            fb_phone, fb_role = (
                actions.lid_to_phone_via_identify_sender(chat_id)
                if fb_converse else (None, None)
            )
            campaign_cta_text = actions.flyer_campaign_cta_text(text)
            if campaign_cta_text:
                cta_result = _try_flyer_campaign_cta_intercept(campaign_cta_text, chat_id, event)
                if cta_result is not None:
                    return cta_result
                return None
            # Quote-echo NEW/APPROVE resolution must run before any intercept
            # that could swallow a bare "NEW" (intake continuation, vague-start
            # clarifications). Self-gates on pending state + 4h TTL.
            quote_echo_choice_result = _try_flyer_quote_echo_choice(
                text, chat_id, event,
                flyer_generation_enabled=flyer_generation_enabled,
                flyer_workflow_enabled=flyer_workflow_enabled,
            )
            if quote_echo_choice_result is not None:
                return quote_echo_choice_result
            account_result = _try_flyer_account_intercept(text, chat_id, event)
            if account_result is not None:
                return account_result
            if fb_converse:
                # Yield the sample-prompt menu to the LLM; record a marker only
                # when the request would actually have matched (cheap pure gate).
                if _fb_sample_prompt_request_matches(text):
                    actions.audit_front_brain_yielded(
                        chat_id, intercept="sample_prompt", message_id=message_id)
            else:
                sample_prompt_result = _try_flyer_sample_prompt_request_intercept(text, chat_id, event, media_path)
                if sample_prompt_result is not None:
                    return sample_prompt_result
            regulated_account_result = _try_flyer_regulated_account_guard(text, chat_id, event)
            if regulated_account_result is not None:
                return regulated_account_result
            # P1-1 send-now-compound fix: the whole-message approval-text arm is
            # UNCHANGED (proven safe, zero classifier involvement — it short-circuits
            # the `or`). The send-now arm now takes the early finalize path ONLY for a
            # PURE send-now; a compound "send it now + <fresh catering>" (or a
            # classifier error) is NOT early-pathed, so it falls through the normal
            # ladder → R2B-1 keeps precedence → the escape gate raises ONE clarification.
            if flyer_generation_enabled and (
                actions.is_flyer_approval_text(text)
                or _flyer_send_now_early_path_allowed(text, classify_catering_memo)
            ):
                flyer_result = _try_flyer_active_project_intercept(text, chat_id, event, media_path)
                if flyer_result is not None:
                    return flyer_result
            # Quoted-APPROVE binding (2026-07-05): flattened quote-echo guard
            # (F0211 class). Ordered BEFORE intake / new-request routing so an
            # echoed brief cannot create a duplicate project, and BEFORE the
            # unconditional active-project intercept so it cannot be captured
            # as revision text.
            quote_echo_result = _try_flyer_quote_echo_guard(text, chat_id, event, media_path)
            if quote_echo_result is not None:
                return quote_echo_result
            if fb_converse:
                # Yield an in-progress intake follow-up to the LLM; record a
                # marker only when a live intake session actually exists for this
                # customer (owner chats never had an intake session here).
                try:
                    if fb_role != "owner" and actions.find_flyer_intake_session_by_sender(fb_phone, chat_id):
                        actions.audit_front_brain_yielded(
                            chat_id, intercept="intake_followup", message_id=message_id)
                except Exception:
                    pass  # traceability only — never block the yield
            else:
                intake_result = _try_flyer_intake_intercept(text, chat_id, event, media_path=media_path)
                if intake_result is not None:
                    return intake_result
            scope_choice_result = _try_flyer_reference_scope_choice_intercept(text, chat_id, event)
            if scope_choice_result is not None:
                return scope_choice_result
            # SOURCE/NEW intercept claims rows transitioned to
            # `awaiting_source_vs_new_choice` by the choice intercept above.
            # Ordered AFTER scope-choice (which may transition the row in the
            # same pass) but BEFORE scope-authorization (status isolation:
            # source-vs-new rows are not authorization-eligible).
            source_vs_new_result = _try_flyer_source_vs_new_choice_intercept(text, chat_id, event)
            if source_vs_new_result is not None:
                return source_vs_new_result
            scope_auth_result = _try_flyer_reference_scope_authorization_intercept(text, chat_id, event)
            if scope_auth_result is not None:
                return scope_auth_result
            if media_path:
                # Resolve ONCE, here — the first point the answers are needed.
                # The two cessions below consume these same booleans and must not
                # re-resolve them: a second identity read could contradict the
                # first and drop the turn into active-project Flyer routing.
                owner_receipt_candidate = _is_owner_receipt_candidate(
                    text, chat_id, media_path=media_path)
                # The scalar read the brand-asset arm used to do for itself, and
                # that `guest_phone`/`guest_role` does below. Hoisted so the menu
                # candidacy can consume it WITHOUT adding a spawn: this one read
                # now feeds all three, so a media turn resolves the scalar the
                # same number of times as before.
                turn_identity = actions.lid_to_phone_via_identify_sender(chat_id)
                menu_caption_candidate = _is_menu_caption_candidate(
                    text, media_path=media_path, role=turn_identity[1])
                brand_result = _try_flyer_brand_asset_intercept(
                    text, chat_id, event, media_path,
                    owner_receipt_candidate=owner_receipt_candidate,
                    menu_caption_candidate=menu_caption_candidate,
                    identity=turn_identity)
                if brand_result is not None:
                    return brand_result
            onboarding_reply_result = _try_flyer_existing_onboarding_intercept(text, chat_id, event)
            if onboarding_reply_result is not None:
                return onboarding_reply_result
            # Reuse the media block's read when it happened; text-only turns
            # resolve here exactly as before.
            guest_phone, guest_role = (
                turn_identity if turn_identity is not None
                else actions.lid_to_phone_via_identify_sender(chat_id))
            if (
                flyer_generation_enabled
                and guest_role != "owner"
                and actions.find_paid_flyer_guest_order(guest_phone, chat_id)
            ):
                flyer_result = _try_flyer_primary_intercept(
                    text, chat_id, event, force_new=True, media_path=media_path,
                )
                if flyer_result is not None:
                    return flyer_result
            if flyer_generation_enabled:
                # PR-R2B-1: SINGLE hoisted amendment/flyer conflict gate — runs ONCE,
                # BEFORE the flyer active-project arm commits any revision (the canary
                # lesson: logic after the flyer terminal arm is ineffective). Fires the
                # bounded discriminator ONLY in the exactly-one-eligible-lead conflict
                # cell; dormant + byte-identical fall-through when the flag is off, the
                # sender is not allowlisted, or there is no eligible catering lead.
                amendment_conflict_result = _try_amendment_conflict_intercept(
                    text, chat_id, event, media_path)
                if amendment_conflict_result is not None:
                    return amendment_conflict_result
            # BOTH cessions sit under `flyer_workflow_enabled`, NOT under
            # `flyer_generation_enabled` — deliberately the same condition that
            # arms the brand-asset arm above. The two flags are not equivalent
            # (`is_flyer_workflow_enabled` is True whenever a `flyer:` block
            # exists), so gating capture and cession differently left a live
            # configuration — generation off as an incident kill switch, workflow
            # still on — in which a menu or receipt photo was still captured as a
            # flyer brand asset while the arms that rescue it were dark. Ordering
            # is unchanged when both flags are on, which is the normal state: the
            # R2B-1 amendment gate above keeps its precedence, and everything
            # below still runs only when generation is enabled.
            #
            # Menu-caption cession (2026-08-01 live F0226 defect): an owner /
            # verified-employee media message captioned with a documented
            # `update_catering_menu` trigger belongs to the menu→pricebook
            # SKILL, not to Flyer Studio. Released here so it precedes EVERY
            # flyer claim below — the active-project arm (which would ingest
            # the photo as a flyer reference asset) and the
            # `should_start_new_flyer_over_active` primary arm (which created
            # F0226 live, because _MEDIA_TEMPLATE_EDIT admits the bare
            # substring "menu" on any media message). No flyer project, no
            # asset ingestion, no catering lead. Placed AFTER the R2B-1 gate
            # so amendment-conflict precedence is unchanged, and it consumes
            # the turn-scoped candidacy resolved in the media block above
            # rather than spawning identify-sender again.
            #
            # 2026-08-08: this used to `return None` and let the LLM
            # dispatcher decide whether to invoke `update_catering_menu`. On
            # the live 2026-08-01 owner turn it decided not to, and answered
            # "successfully recorded" with nothing recorded. Everything
            # needed to run the action is already proven here — media path,
            # documented trigger, authorized role — and the SKILL's own job
            # is to call ONE script with mechanically derivable inputs, so
            # the second, redundant routing decision is gone: cf-router runs
            # the script and answers from its verified result.
            if _menu_caption_cedes_to_dispatcher(
                text, chat_id, role=guest_role,
                menu_caption_candidate=menu_caption_candidate,
            ):
                return _run_owner_menu_ingestion(
                    chat_id, event, text=text, media_path=media_path,
                    role=guest_role, sender_phone=guest_phone,
                )
            # Wave-2 DRAFT tier: an owner photo whose caption explicitly says
            # "expense receipt" is an expense record, not flyer artwork. Placed
            # in the same tier as the menu cession — AFTER the R2B-1 gate, so
            # amendment precedence is unchanged, and BEFORE every flyer claim,
            # so an unresolved Flyer Studio project cannot ingest the receipt
            # as a reference asset (the live F0226 failure mode). Owner-only,
            # and the caption trigger is required: bare owner images stay
            # unsupported in this wave rather than being guessed at.
            if _receipt_caption_cedes_to_dispatcher(
                text, chat_id, media_path=media_path, role=guest_role,
                owner_receipt_candidate=owner_receipt_candidate,
            ):
                return _run_owner_receipt_ingestion(
                    chat_id, event, text=text, media_path=media_path,
                    role=guest_role, sender_phone=guest_phone,
                )
            if flyer_generation_enabled:
                # P1-1: fresh-intent catering escape gate. A fresh catering
                # inquiry from a customer with a LIVE flyer project must reach
                # catering (F7), not be captured as a flyer edit/revision (the
                # F0224 `flyer_reference_exact_edit_queued` defect). ONE shared
                # gate, placed AFTER the R2B-1 gate (precedence preserved) and
                # BEFORE the active-project intercept so no terminal flyer arm
                # can claim the message first. Returns the fall-through sentinel
                # only when the inbound is not catering (byte-identical flyer
                # path); every other outcome (escape / clarify / F7-declined)
                # returns from dispatch here.
                escape_result = _try_flyer_catering_escape_gate(
                    text, chat_id, event, media_path, classify_fn=classify_catering_memo)
                if escape_result is not _GATE_FALLTHROUGH:
                    return escape_result
                flyer_result = _try_flyer_active_project_intercept(text, chat_id, event, media_path)
                if flyer_result is not None:
                    return flyer_result
            # PR-β 2026-05-26 — delivery-state guard for when no active or
            # recent flyer project resolves. Placed AFTER active-project
            # intercept so the guard only sees the no-project-resolves case.
            # Sister to PR-α's regulated_account_guard (different surface).
            delivery_state_result = _try_flyer_delivery_state_guard(text, chat_id, event)
            if delivery_state_result is not None:
                return delivery_state_result
            context_phone, context_role = actions.lid_to_phone_via_identify_sender(chat_id)
            if context_role != "owner":
                context_customer = actions.find_flyer_customer_by_sender(context_phone, chat_id)
                if (
                    context_customer
                    and context_customer.get("status") in {"trial", "active"}
                    and flyer_generation_enabled
                    and actions.is_registered_customer_contextual_flyer_brief(text)
                ):
                    flyer_result = _try_flyer_primary_intercept(
                        text, chat_id, event, force_new=True, media_path=media_path,
                    )
                    if flyer_result is not None:
                        return flyer_result
            if (
                flyer_generation_enabled
                and actions.should_start_new_flyer_over_active(text, has_media=bool(media_path))
            ):
                flyer_result = _try_flyer_primary_intercept(
                    text, chat_id, event, force_new=True, media_path=media_path,
                )
                if flyer_result is not None:
                    return flyer_result
            _fb_vague_start = actions.is_vague_flyer_start(text, has_media=bool(media_path))
            if _fb_vague_start and fb_converse:
                # Yield a vague "make me a flyer" brief to the LLM so Hermes asks
                # the clarifying questions itself (item 2). Marker records the
                # yield; the whole deterministic starter/clarify block below is
                # skipped for admitted chats.
                actions.audit_front_brain_yielded(
                    chat_id, intercept="vague_start", message_id=message_id)
            elif _fb_vague_start:
                phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
                if role != "owner":
                    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
                    if customer and customer.get("status") in {"trial", "active"}:
                        if actions.is_flyer_legacy_trial_link_followup(text):
                            return _send_flyer_active_customer_trial_link_recovery(chat_id, customer, role=role)
                        if (
                            actions.flyer_starter_prompts_enabled(customer)
                            and not actions.flyer_starter_prompt_already_sent(customer)
                            and actions.claim_flyer_starter_prompt_send(str(customer.get("customer_id") or ""))
                        ):
                            ok, detail, intake = actions.trigger_flyer_intake(
                                chat_id=chat_id,
                                sender_phone=phone,
                                message_id=_extract_message_id(event, chat_id, text),
                                text=text,
                                media_path=media_path or "",
                                start_source="sample_idea",
                                original_text=text,
                            )
                            if not ok or not intake:
                                actions.release_flyer_starter_prompt_claim(str(customer.get("customer_id") or ""))
                                actions.audit_intercepted(
                                    reason="flyer_intake_failed",
                                    chat_id=chat_id,
                                    subprocess_rc=2,
                                    detail=f"source=sample_idea; detail={detail[:450]}",
                                )
                                return None
                            reply = str(intake.get("reply_text") or "")
                            ack_ok, mid, err = actions.send_flyer_text(
                                chat_id, reply,
                                action_context=build_action_context_for_command(
                                    PROJECT_ACTIONS, "intake.acknowledged",
                                ),
                                allow_duplicate=True,
                            )
                            if not ack_ok and not mid:
                                actions.release_flyer_starter_prompt_claim(str(customer.get("customer_id") or ""))
                            actions.audit_intercepted(
                                reason="flyer_starter_ideas",
                                chat_id=chat_id,
                                subprocess_rc=0 if ack_ok else 3,
                                detail=(
                                    f"customer_id={customer.get('customer_id') or ''}; sender_role={role}; "
                                    f"action={intake.get('action') or ''}; "
                                    f"ack_message_id={mid}; ack_error={err[:300]}"
                                ),
                            )
                            return {"action": "skip", "reason": "cf-router flyer starter ideas sent"}
                        reply = actions.flyer_vague_request_clarification_reply(customer)
                        ack_ok, mid, err = actions.send_flyer_text(
                            chat_id, reply,
                            action_context=build_action_context(
                                action_id="flyer.project.vague_request_clarification",
                                is_regulated_action=False,
                            ),
                        )
                        reason = (
                            "flyer_starter_preference_off"
                            if not actions.flyer_starter_prompts_enabled(customer)
                            else "flyer_starter_already_sent"
                        )
                        actions.audit_intercepted(
                            reason=reason,
                            chat_id=chat_id,
                            subprocess_rc=0 if ack_ok else 3,
                            detail=(
                                f"customer_id={customer.get('customer_id') or ''}; sender_role={role}; "
                                f"ack_message_id={mid}; ack_error={err[:300]}"
                            ),
                        )
                        if reason == "flyer_starter_preference_off":
                            return {"action": "skip", "reason": "cf-router flyer starter preference off clarification sent"}
                        return {"action": "skip", "reason": "cf-router flyer starter already sent clarification sent"}
                    elif customer:
                        reply = actions.flyer_customer_not_active_reply(customer)
                        ack_ok, mid, err = actions.send_flyer_text(
                            chat_id, reply,
                            action_context=build_action_context(
                                action_id="flyer.account.customer_not_active",
                                is_regulated_action=False,
                            ),
                        )
                        actions.audit_intercepted(
                            reason="flyer_customer_not_active",
                            chat_id=chat_id,
                            subprocess_rc=0 if ack_ok else 3,
                            detail=(
                                f"customer_id={customer.get('customer_id') or ''}; status={customer.get('status') or ''}; "
                                f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
                            ),
                        )
                        return {"action": "skip", "reason": "cf-router flyer customer not active"}
                    else:
                        started = _start_flyer_intake(
                            text, chat_id, event, source="start_trial", original_text=text,
                            media_path=media_path,
                        )
                        if started is not None:
                            return started
            is_catering_probe, _catering_probe_signals = actions.classify_catering(text)
            is_flyer_probe, _flyer_probe_signals = actions.classify_flyer_intent(text)
            if not is_catering_probe and (is_flyer_probe or actions.is_flyer_onboarding_intent(text)):
                onboarding_result = _try_flyer_onboarding_intercept(text, chat_id, event)
                if onboarding_result is not None:
                    return onboarding_result

        # Bare-flyer mode (Approach B): deterministic async intercept, ORDERED so a follow-up
        # resolves correctly (slice 2c routing). cf-router does NOT block on the ~30s render and
        # does NOT rely on the LLM. Inert when flyer.enabled=true. The recent-gated arms (a)/(c)
        # only fire for a chat that just received a bare flyer, so they never hijack catering/other
        # messages, and the whole chain sits before F7 so a recent customer's revision/new-flyer
        # wins over catering.
        if flyer_workflow_enabled and not flyer_generation_enabled:
            recent = actions.recent_bare_flyer_for_chat(chat_id)
            # Attached flyer + exact edit OR concept/reference-adaptation text needs the
            # primary media path even while flyer.enabled=false. Exact edits use source-
            # preserving generation/manual queue; concept references use the existing
            # authorization gate and new-poster path while preserving the attachment as
            # visual inspiration.
            if media_path and (
                actions.is_exact_reference_edit_request(text, has_media=True)
                or actions.is_reference_concept_adaptation_request(text, has_media=True)
            ):
                source_edit_result = _try_flyer_primary_intercept(
                    text, chat_id, event, force_new=True, media_path=media_path)
                if source_edit_result is not None:
                    return source_edit_result
            # (a) supported price edits -> apply (re-overlay/regenerate from persisted facts)
            if recent and actions.detect_bare_price_revision_apply(text):
                actions.spawn_bare_flyer_render_and_send(
                    chat_id, text, message_id=message_id, is_revision_apply=True)
                return {"action": "skip", "reason": "cf-router bare flyer revision-apply dispatched"}
            # (b) a genuine NEW-flyer request wins over the broad revision route
            if actions.is_strong_new_flyer_request(text):
                actions.spawn_bare_flyer_render_and_send(chat_id, text, message_id=message_id)
                return {"action": "skip", "reason": "cf-router bare flyer dispatched"}
            flyer_intent = actions.classify_flyer_intent(text)[0]
            existing_flyer_edit = actions.is_flyer_edit_of_existing(text)
            # (c) explicit edits of an existing flyer must stay in Flyer before F7. The bounded
            #     apply arm above handles the supported price-layout edit; everything else gets the
            #     flyer-scoped revision fallback instead of being suppressed as an active catering
            #     lead follow-up.
            if existing_flyer_edit and (recent or flyer_intent):
                actions.spawn_bare_flyer_render_and_send(
                    chat_id, text, message_id=message_id, is_revision=True)
                return {"action": "skip", "reason": "cf-router bare flyer revision dispatched"}
            # (d) other explicit flyer intent -> fresh render.
            if flyer_intent:
                actions.spawn_bare_flyer_render_and_send(chat_id, text, message_id=message_id)
                return {"action": "skip", "reason": "cf-router bare flyer dispatched"}

        if F7_ENABLED:
            if flyer_generation_enabled:
                is_flyer, _flyer_signals = actions.classify_flyer_intent(text)
                if is_flyer:
                    flyer_result = _try_flyer_primary_intercept(text, chat_id, event)
                    if flyer_result is not None:
                        return flyer_result
                    return None

            # M3 acceptance arm, BEFORE the catering-signal admission below: a
            # customer answering "we accept" to a quote we already sent carries no
            # catering signal, so the admission check would drop it — and its lead
            # is in SENT_TO_CUSTOMER, which F7's active-lead lookup excludes. It
            # runs after the flyer-intent branch above so explicit flyer intent
            # still wins.
            acceptance_result = _try_catering_acceptance_intercept(
                text, chat_id, event,
                flyer_generation_enabled=flyer_generation_enabled,
            )
            if acceptance_result is not None:
                return acceptance_result

            is_catering, signals = actions.classify_catering(text)
            proposal_workflow = (
                actions.is_proposal_selection(text)
                or actions.is_proposal_request(text)
            )
            # M1: a reply to our own intake question carries no catering signal, so
            # the lead lookup is the last admission check — evaluated ONLY when the
            # cheap signal checks have all failed.
            if not (is_catering or _has_f7_followup_signal(signals) or proposal_workflow):
                admit = (f7_qualification_gate_enabled()
                         and _sender_has_qualifying_lead(chat_id))
            else:
                admit = True
            if admit:
                if flyer_generation_enabled:
                    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
                    # Staleness-bounded on purpose: an abandoned or orphaned
                    # flyer row used to suppress catering routing for this
                    # sender permanently. Only THIS site is bounded — the other
                    # two callers govern flyer routing and need their own review.
                    if role != "owner" and actions.has_recent_non_delivered_flyer_project_by_sender(phone, chat_id):
                        return None
                f7_result = _try_f7_primary_intercept(
                    text, chat_id, event, signals=signals,
                    allow_new_lead=is_catering,
                )
                if f7_result is not None:
                    return f7_result

        revenue_clarification_result = _try_revenue_route_clarification_start(
            text,
            chat_id,
            message_id,
            flyer_workflow_enabled=flyer_workflow_enabled,
        )
        if revenue_clarification_result is not None:
            return revenue_clarification_result

        # (e) recent customer + broad non-object revision feedback -> deterministic "resend full
        # details" (no toolless-LLM loop). Placed AFTER the F7 catering block so catering ALWAYS
        # wins first for messages that do not name a flyer object: a follow-up like "add 20
        # vegetarian meals for the party" is handled by F7 and never reaches this flyer arm.
        if (flyer_workflow_enabled
                and not flyer_generation_enabled
                and actions.recent_bare_flyer_for_chat(chat_id)
                and actions.is_flyer_revision_intent(text)):
            actions.spawn_bare_flyer_render_and_send(chat_id, text, message_id=message_id, is_revision=True)
            return {"action": "skip", "reason": "cf-router bare flyer revision dispatched"}

        return None

    except Exception as e:  # noqa: BLE001 — plugin must never crash the gateway
        try:
            actions.audit_intercepted(
                reason="error", chat_id=str(event)[:50] if event else "?",
                detail=f"{type(e).__name__}: {e}",
            )
        except Exception:
            pass
        return None  # always let LLM run on plugin error


def _clarified_text_event(*, text: str, chat_id: str, message_id: str) -> Any:
    return SimpleNamespace(text=text, chat_id=chat_id, message_id=message_id)


def _try_revenue_route_clarification_choice(
    text: str,
    chat_id: str,
    event: Any,
    *,
    flyer_generation_enabled: bool,
    flyer_workflow_enabled: bool,
) -> Optional[dict]:
    try:
        pending = actions.get_revenue_route_clarification(chat_id)
    except Exception as exc:  # noqa: BLE001 - clarification state must not preempt core routing
        try:
            actions.audit_intercepted(
                reason="error",
                chat_id=chat_id,
                detail=f"revenue_route_clarification_lookup_failed: {type(exc).__name__}: {exc}",
            )
        except Exception:
            pass
        return None
    if not pending:
        return None
    # PR-R2B-1: an amendment-conflict clarification routes DIFFERENTLY from the
    # revenue-route (new-inquiry) one — "catering" enters R2A capture on the EXISTING
    # lead, never create-catering-lead. Branch before the revenue-route flow.
    if pending.get("kind") == "amendment_conflict":
        return _handle_amendment_conflict_choice(
            text, chat_id, event, pending,
            flyer_generation_enabled=flyer_generation_enabled,
            flyer_workflow_enabled=flyer_workflow_enabled)
    choice = actions.classify_revenue_route_choice(text)
    if choice is None:
        return None

    if choice == "both":
        reply = actions.revenue_route_both_reply()
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id,
            reply,
            action_context=build_action_context(
                action_id="flyer.routing.revenue_route_clarification",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="revenue_route_clarification_sent",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=f"choice=both; pending_kept=true; ack_message_id={mid}; ack_error={err[:300]}",
        )
        return {"action": "skip", "reason": "cf-router revenue route clarification sent"}

    pending = actions.pop_revenue_route_clarification(chat_id) or pending
    original_text = str(pending.get("original_text") or "").strip()
    if not original_text:
        return None
    original_message_id = str(pending.get("message_id") or "").strip()
    if not original_message_id:
        original_message_id = _extract_message_id(event, chat_id, original_text)

    actions.audit_intercepted(
        reason="revenue_route_clarification_chosen",
        chat_id=chat_id,
        detail=f"choice={choice}; original_message_id={original_message_id}",
    )
    original_event = _clarified_text_event(
        text=original_text,
        chat_id=chat_id,
        message_id=original_message_id,
    )

    if choice == "flyer":
        if flyer_generation_enabled:
            return _try_flyer_primary_intercept(original_text, chat_id, original_event, force_new=True)
        if flyer_workflow_enabled:
            actions.spawn_bare_flyer_render_and_send(
                chat_id, original_text, message_id=original_message_id,
            )
            return {"action": "skip", "reason": "cf-router bare flyer dispatched"}
        return None

    if choice == "catering":
        phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
        if role == "owner":
            return None
        return _try_f7_primary_intercept(
            original_text,
            chat_id,
            original_event,
            signals=["revenue_route_choice:catering"],
            allow_new_lead=True,
        )

    return None


def _handle_amendment_conflict_choice(
    text: str, chat_id: str, event: Any, pending: dict, *,
    flyer_generation_enabled: bool, flyer_workflow_enabled: bool,
) -> Optional[dict]:
    """Resolve the customer's reply to an amendment-conflict clarification.

    "flyer"    → route the STORED original text to the unchanged flyer active-project
                 arm (a flyer revision, now that the customer chose flyer).
    "catering" → route the STORED text into R2A CAPTURE on the EXISTING lead
                 (source=conflict_discriminator) — NEVER create-catering-lead. When a
                 single eligible lead cannot be determined, re-ask (never silent-choose).
    No discriminator/model call happens here (one-call-max is a gate property). An
    unrecognized reply leaves the pending in place and defers to normal routing."""
    choice = actions.classify_revenue_route_choice(text)
    if choice not in ("flyer", "catering"):
        return None  # unrecognized → keep pending; let normal routing handle the reply

    pending = actions.pop_revenue_route_clarification(chat_id) or pending
    original_text = str(pending.get("original_text") or "").strip()
    if not original_text:
        return None
    original_message_id = str(pending.get("message_id") or "").strip() \
        or _extract_message_id(event, chat_id, original_text)
    original_event = _clarified_text_event(
        text=original_text, chat_id=chat_id, message_id=original_message_id)
    stored_lead_ids = [str(x) for x in (pending.get("lead_ids") or []) if x]

    if choice == "flyer":
        actions.audit_intercepted(
            reason="catering_amendment_conflict_resolved", chat_id=chat_id,
            detail=f"choice=flyer; lead_ids={','.join(stored_lead_ids)}")
        if flyer_generation_enabled:
            return _try_flyer_active_project_intercept(original_text, chat_id, original_event)
        return None

    # choice == "catering" → capture into the EXISTING lead (never create-lead).
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    eligible = actions.find_all_eligible_catering_leads_by_sender(phone, chat_id)
    target = None
    if len(eligible) == 1:
        target = eligible[0]
    elif stored_lead_ids:
        by_id = {str(l.get("lead_id")): l for l in eligible}
        still = [by_id[i] for i in stored_lead_ids if i in by_id]
        if len(still) == 1:
            target = still[0]
    if target is None:
        # Cannot pick a single lead deterministically → NEVER silently choose. Re-ask.
        return _send_amendment_conflict_clarification(
            text=original_text, chat_id=chat_id, event=original_event,
            leads=eligible or [{"lead_id": i} for i in stored_lead_ids],
            project_id="", cause="multi_lead_choice", latency_ms=0,
            reason="catering_amendment_conflict_clarify")

    lead_id = str(target.get("lead_id") or "")
    capture = catering_amendments.capture_branch_b_amendment(
        lead=target, text=original_text, chat_id=chat_id, phone=phone,
        message_id=original_message_id, source_transport=_event_transport(event),
        provider_timestamp=_event_provider_timestamp(event),
        source="conflict_discriminator")
    if capture.ok:
        if F7_PRIMARY_FOLLOWUP_REPLY:
            actions.send_canonical_followup_reply(chat_id, lead_id, original_message_id)
        actions.audit_intercepted(
            reason="catering_amendment_conflict_resolved", chat_id=chat_id,
            detail=(f"choice=catering; lead_id={lead_id}; amendment_id={capture.amendment_id}; "
                    f"{'replayed' if capture.idempotent else 'captured'}"))
        return {"action": "skip",
                "reason": f"cf-router R2B-1: amendment captured for {lead_id} via clarification"}
    if F7_PRIMARY_FOLLOWUP_REPLY:
        _send_amendment_retry_reply(chat_id, lead_id)
    actions.audit_intercepted(
        reason="catering_amendment_conflict_capture_failed", chat_id=chat_id,
        detail=f"choice=catering; lead_id={lead_id}; capture_failed reason={capture.reason}; retry requested")
    return {"action": "skip",
            "reason": f"cf-router R2B-1: amendment capture failed for {lead_id}, retry requested"}


def _try_revenue_route_clarification_start(
    text: str,
    chat_id: str,
    message_id: str,
    *,
    flyer_workflow_enabled: bool,
) -> Optional[dict]:
    if not flyer_workflow_enabled:
        return None
    is_ambiguous, signals = actions.classify_ambiguous_revenue_brief(text)
    if not is_ambiguous:
        return None
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        return None

    actions.save_revenue_route_clarification(
        chat_id=chat_id,
        original_text=text,
        message_id=message_id,
        sender_phone=phone,
        sender_role=role,
        signals=signals,
    )
    reply = actions.revenue_route_clarification_reply()
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id,
        reply,
        action_context=build_action_context(
            action_id="flyer.routing.revenue_route_clarification",
            is_regulated_action=False,
        ),
    )
    actions.audit_intercepted(
        reason="revenue_route_clarification_sent",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"signals={','.join(signals)}; sender_role={role}; "
            f"ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    return {"action": "skip", "reason": "cf-router revenue route clarification sent"}


def _try_f8_intercept(text: str, chat_id: str, message_id: str = "") -> Optional[dict]:
    """Owner sent something in self-chat. Look for #XXXXX + verb.

    Returns:
      {"action": "skip", "reason": ...} — plugin handled, LLM bypassed
      None                              — no match OR apply-script failed;
                                          let LLM dispatch normally so the
                                          owner can see/recover from the
                                          failure
    """
    code_match = _CODE_PATTERN.search(text)
    if not code_match:
        return None

    code = "#" + code_match.group(1).upper()

    # Determine verb intent
    has_approve = bool(_VERB_APPROVE.search(text))
    has_reject = bool(_VERB_REJECT.search(text))
    has_edit = bool(_VERB_EDIT.search(text))

    # PR-R1: resolve the code through the centralized pool registry, in the
    # canonical order (menu-pending -> catering-leads -> expense -> shift). A
    # code matching >=2 pools, OR >=2 rows inside ONE pool (a subclass, so
    # this single isinstance check covers both), is a fail-closed CollisionResult — we REFUSE to
    # apply any approval, record the collision (audit every time + owner alert
    # once), and fall through returning None (no new reply). Thread actions' own
    # configurable state dir so the registry reads the SAME state files this
    # plugin is configured for (tests patch actions.LEADS_PATH; prod is /opt).
    _pool_paths = approval_code_pools.pool_paths_under(actions.LEADS_PATH.parent)
    resolved = approval_code_pools.resolve_code(code, paths=_pool_paths)
    if isinstance(resolved, approval_code_pools.CollisionResult):
        approval_code_pools.record_collision_event(resolved, detected_by="f8_intercept")
        return None
    if resolved is None:
        # Code didn't match any open pool — let LLM handle (might be a stale
        # reference; LLM can tell the owner).
        return None

    pool_name, row = resolved

    if pool_name == approval_code_pools.POOL_MENU_PENDING:
        if has_approve:  # "yes" matches _VERB_APPROVE
            rc = actions.invoke_apply_menu_update(code, "yes")
            return _build_skip_or_passthrough(
                rc=rc, chat_id=chat_id, code=code,
                reason="f8_menu_yes", detail="",
                action_label=f"apply-menu-update yes for {code}",
            )
        if has_reject:  # "no" matches _VERB_REJECT
            rc = actions.invoke_apply_menu_update(code, "no")
            return _build_skip_or_passthrough(
                rc=rc, chat_id=chat_id, code=code,
                reason="f8_menu_no", detail="",
                action_label=f"apply-menu-update no for {code}",
            )
        return None

    if pool_name == approval_code_pools.POOL_CATERING_LEADS:
        lead = row
        lead_status = lead.get("status")
        # Edit needs LLM extraction — let LLM handle
        if has_edit:
            return None
        if has_approve:
            # R2.H2 2026-08-23: a non-finalized approve is REFUSED by the
            # apply-script (exit 11), which reprompts the owner itself. That is a
            # complete deterministic answer, so the turn must end here — an
            # available LLM must not double-answer it. Terminal ONLY when the
            # reprompt actually reached the owner: if the bridge refused it,
            # nobody was told, and closing the turn would claim otherwise. That
            # is the silent-failure class this repair exists to close, so in
            # that case fall through exactly as before.
            cap: dict = {}
            rc = actions.invoke_apply_owner_decision(
                code, "approve", lead=lead, message_id=message_id, capture=cap)
            payload = cap.get("payload") or {}
            if rc == _EXIT_TRUTH_GUARD_FAILED and payload.get(
                    "refusal") == "owner_approve_without_customer_finalize":
                informed = payload.get("owner_reprompt_delivered") is True
                # reason MUST be a member of CfRouterIntercepted.reason, which is
                # a Literal. A new value would fail validation inside
                # audit_intercepted's best-effort try/except and the row would be
                # SILENTLY DROPPED — so the refusal is recorded under the existing
                # f8_owner_approve value (accurate: the owner did send approve and
                # the plugin did invoke the apply-script) with the discriminating
                # facts in the free-text detail. No schema change, so no rollback
                # exposure from a widened Literal either.
                actions.audit_intercepted(
                    reason="f8_owner_approve",
                    chat_id=chat_id, code=code, subprocess_rc=rc,
                    detail=("refused: not customer-finalized; owner reprompt "
                            + ("DELIVERED" if informed else "NOT delivered")),
                )
                if informed:
                    return {"action": "skip",
                            "reason": ("cf-router F8: apply-owner-decision refused "
                                       f"{code} (not customer-finalized); owner "
                                       "reprompted deterministically (rc=11)")}
                return None
            return _build_skip_or_passthrough(
                rc=rc, chat_id=chat_id, code=code,
                reason="f8_owner_approve",
                detail=f"lead status was {lead_status}",
                action_label=f"apply-owner-decision approve for {code}",
            )
        if has_reject:
            rc = actions.invoke_apply_owner_decision(code, "reject", message_id=message_id)
            return _build_skip_or_passthrough(
                rc=rc, chat_id=chat_id, code=code,
                reason="f8_owner_reject",
                detail=f"lead status was {lead_status}",
                action_label=f"apply-owner-decision reject for {code}",
            )
        # Code matched but no clear verb — let LLM ask for clarification
        return None

    if pool_name == approval_code_pools.POOL_CATERING_FOLLOWUPS:
        # M5: the owner is answering a follow-up approval card. Without this
        # branch the code resolves to a pool F8 does not handle and falls through
        # to the LLM, which would have to guess what "#XXXXX approve" meant for a
        # message the deterministic engine already composed and stored.
        followup_status = row.get("status")
        if has_edit:
            # There is nothing to edit — the text was fixed when the card was
            # rendered. Let the LLM explain that rather than silently approving.
            return None
        if has_approve:
            rc = actions.invoke_approve_catering_followup(code, "approve")
            return _build_skip_or_passthrough(
                rc=rc, chat_id=chat_id, code=code,
                reason="f8_followup_approve",
                detail=f"followup status was {followup_status}",
                action_label=f"approve-catering-followup approve for {code}",
            )
        if has_reject:
            rc = actions.invoke_approve_catering_followup(code, "cancel")
            return _build_skip_or_passthrough(
                rc=rc, chat_id=chat_id, code=code,
                reason="f8_followup_cancel",
                detail=f"followup status was {followup_status}",
                action_label=f"approve-catering-followup cancel for {code}",
            )
        return None

    if pool_name == approval_code_pools.POOL_SHIFT:
        return _handle_shift_coverage_code(
            row=row, code=code, chat_id=chat_id, text=text,
            message_id=message_id, has_edit=has_edit,
        )

    # Expense codes are not F8's responsibility (owner self-chat handles menu,
    # catering and shift coverage here) — fall through so the LLM/dispatcher
    # routes them. The shift arm above used to share this line; the expense arm
    # still means it, and `test_expense_code_still_falls_through` pins that this
    # change did not silently widen to a pool with no wired apply path.
    return None


# ── PR-5 automation-control kernel — cf-router INBOUND enforcement (§5.4/§5.5) ─

def _try_automation_control(
    text: str, chat_id: str, message_id: str, native_message_id: str = "",
) -> Optional[dict]:
    """Deterministic STOP/pause/opt-out + human-takeover enforcement, BEFORE any
    routing. Returns a ``{"action": "skip", ...}`` dict when the kernel handled
    the inbound (a command was processed OR the conversation is suppressed), else
    None (normal routing).

    DORMANT behind CATERING_AUTOMATION_CONTROL_ENABLED + allowlist: the first
    check returns None with no state read when OFF (byte-identical). The two
    behaviors activate independently (CATERING_STOP_ENABLED /
    CATERING_TAKEOVER_ENABLED). Any kernel fault falls through to normal routing
    (fail-open at inbound — the bridge_post point still guards outbound sends;
    get_mode itself preserves the last-known suppressed mode on a read error)."""
    try:
        if not automation_control.enabled(chat_id):
            return None
    except Exception:
        return None
    try:
        nid = native_message_id or message_id or ""
        is_owner = actions.is_owner_chat(chat_id)
        # Owner takeover/release/resume (#XXXXX <verb>), via the F8 owner surface.
        if is_owner and automation_control.takeover_enabled():
            owner_cmd = automation_control.detect_owner_command(text)
            if owner_cmd is not None:
                verb, code = owner_cmd
                owner_result = _automation_control_apply_owner(chat_id, verb, code, nid)
                if owner_result is not None:
                    return owner_result
        # Customer STOP/pause/opt-out + resume (owner self-chat is not a customer
        # command surface).
        if (not is_owner) and automation_control.stop_enabled():
            cust_cmd = automation_control.detect_customer_command(text)
            if cust_cmd is not None:
                applied = _automation_control_apply_customer(chat_id, cust_cmd, nid)
                if applied is not None:
                    return applied
                # A resume token on an ACTIVE conversation is a NO-OP → fall
                # through so the message routes normally (proposal-selection etc.).
        # Suppression — a suppressed conversation's non-command inbound is dropped
        # before routing (defense in depth with the bridge_post outbound point).
        mode = automation_control.get_mode(chat_id)
        if automation_control.mode_suppresses(mode):
            automation_control.emit_send_suppressed(
                chat_id, mode, "inbound_dispatch", logical_turn_id=nid)
            return {"action": "skip", "reason": f"automation_suppressed:{mode}"}
        return None
    except Exception as exc:
        actions.sys.stderr.write(
            f"cf-router: automation-control kernel error (non-fatal): {exc}\n")
        return None


def _automation_control_resolve_customer_target(code: str) -> Optional[str]:
    """Resolve an owner ``#XXXXX`` code to the target customer's phone by scanning
    the catering-leads store for ANY lead carrying that owner_approval_code (not
    just routing-actionable ones — a takeover must reach the customer in any lead
    status). Returns the E.164 customer_phone or None (no matching lead → the
    owner command falls through so the LLM can surface a stale-code hint)."""
    try:
        with actions.LEADS_PATH.open(encoding="utf-8") as f:
            store = json.load(f)
        for lead in store.get("leads", []):
            if isinstance(lead, dict) and lead.get("owner_approval_code") == code:
                phone = lead.get("customer_phone")
                if phone:
                    return str(phone)
    except Exception:
        return None
    return None


def _automation_control_apply_owner(
    owner_chat_id: str, verb: str, code: str, nid: str,
) -> Optional[dict]:
    """Apply an owner takeover (``verb == "takeover"``), pause/hold
    (``verb == "paused"`` — M4/G3), or release/resume (``verb == "active"``)
    against the customer conversation the ``#XXXXX`` code resolves to. Idempotent
    (re-asserting rewrites the same mode + audits the no-op transition, and for
    takeover/pause that rewrite also extends the auto-expiry window). Confirms
    back to the owner self-chat (exempt — the owner key is never a suppressed
    customer key)."""
    target = _automation_control_resolve_customer_target(code)
    if not target:
        return None
    if verb == "takeover":
        to_mode, reason, audit_reason = (
            automation_control.MODE_TAKEOVER, "owner_takeover",
            "automation_control_owner_takeover")
    elif verb == automation_control.MODE_PAUSED:
        to_mode, reason, audit_reason = (
            automation_control.MODE_PAUSED, "owner_pause",
            "automation_control_owner_pause")
    else:
        to_mode, reason, audit_reason = (
            automation_control.MODE_ACTIVE, "owner_release",
            "automation_control_owner_release")
    from_mode, _, _ = automation_control.set_mode(
        target, to_mode, actor="owner", reason=reason, logical_turn_id=nid)
    automation_control.emit_control_changed(
        target, from_mode, to_mode, "owner", reason, logical_turn_id=nid)
    actions.audit_intercepted(
        reason=audit_reason, chat_id=owner_chat_id, code=code, subprocess_rc=0,
        detail=f"target_hash={automation_control.chat_key_hash(target)}; "
               f"from={from_mode}; to={to_mode}",
    )
    _automation_control_send(
        owner_chat_id, automation_control.owner_confirmation(to_mode, code), nid)
    return {"action": "skip", "reason": f"cf-router automation-control {reason}"}


def _automation_control_apply_customer(
    chat_id: str, cmd: str, nid: str,
) -> Optional[dict]:
    """Apply a customer STOP (``opted_out``), pause (``paused``), or resume
    (``active``). Sends AT MOST ONE deterministic ack per suppression episode via
    the atomic compare-and-set inside set_mode (must-fix b); the ack is exempt
    from the outbound suppression it triggers (automation_control_ack=True).

    Resume is the explicit customer re-enable and takes effect ONLY when the
    conversation is currently suppressed (BLOCKER-1a): on an ACTIVE conversation
    a resume token is a NO-OP → returns None so the message routes normally
    (never drops an active customer's message). A new inbound after opt_out never
    silently re-enables — only an explicit resume, or the owner command, does."""
    if cmd == "resume":
        from_mode = automation_control.get_mode(chat_id)
        if not automation_control.mode_suppresses(from_mode):
            return None  # active convo: resume token is a NO-OP, route normally
        automation_control.set_mode(
            chat_id, automation_control.MODE_ACTIVE, actor="customer",
            reason="customer_resume", logical_turn_id=nid)
        automation_control.emit_control_changed(
            chat_id, from_mode, automation_control.MODE_ACTIVE, "customer",
            "customer_resume", logical_turn_id=nid)
        actions.audit_intercepted(
            reason="automation_control_customer_resume", chat_id=chat_id,
            subprocess_rc=0, detail=f"from={from_mode}; to=active")
        _automation_control_send(chat_id, automation_control.RESUME_ACK, nid)
        return {"action": "skip", "reason": "cf-router automation-control customer resume"}
    if cmd == "pause":
        to_mode, reason, ack_text, audit_reason = (
            automation_control.MODE_PAUSED, "customer_pause",
            automation_control.PAUSE_ACK, "automation_control_customer_pause")
    else:
        to_mode, reason, ack_text, audit_reason = (
            automation_control.MODE_OPTED_OUT, "customer_stop",
            automation_control.OPT_OUT_ACK, "automation_control_customer_stop")
    from_mode, _, ack_flipped = automation_control.set_mode(
        chat_id, to_mode, actor="customer", reason=reason,
        logical_turn_id=nid, ack_compare_and_set=True)
    automation_control.emit_control_changed(
        chat_id, from_mode, to_mode, "customer", reason, logical_turn_id=nid)
    actions.audit_intercepted(
        reason=audit_reason, chat_id=chat_id, subprocess_rc=0,
        detail=f"from={from_mode}; to={to_mode}; ack_emitted={ack_flipped}")
    if ack_flipped:
        _automation_control_send(chat_id, ack_text, nid)
    return {"action": "skip", "reason": f"cf-router automation-control {reason}"}


def _automation_control_send(chat_id: str, text: str, nid: str) -> None:
    """Send a deterministic kernel ack/confirmation via the platform send
    chokepoint with automation_control_ack=True (exempt from the very suppression
    it may be announcing). Non-regulated context (no completion/money claim, so
    it clears the outbound content screens). Best-effort — a send failure never
    regresses the state change already committed."""
    try:
        actions._ensure_platform_path()
        from safe_io import bridge_post  # type: ignore
        ctx = build_action_context(
            action_id="catering.automation_control.ack", is_regulated_action=False)
        bridge_post(chat_id, text, action_context=ctx, automation_control_ack=True)
    except Exception as exc:
        actions.sys.stderr.write(
            f"cf-router: automation-control ack send failed (non-fatal): {exc}\n")


def _try_flyer_sample_prompt_request_intercept(text: str, chat_id: str, event: Any, media_path: Optional[str]) -> Optional[dict]:
    if actions.is_flyer_starter_prompt_preference_command(text):
        return None
    body = " ".join(actions.flyer_visible_message_text(text).split())
    if not _SAMPLE_PROMPT_REQUEST.search(body):
        return None
    # A real flyer brief / creation request outranks the sample-prompt menu, even when it says
    # "prompt"/"improvise"/"improve" (operator 2026-06-07: "real flyer creation/intake must beat the
    # sample-prompt menu"). Concrete brief content -> fall through to creation/intake, not the menu.
    if actions.flyer_message_has_brief_detail(text):
        return None

    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        return None
    # Priority #1 (operator 2026-06-07): explicit active-project iteration /
    # Slice 3 outranks the sample-prompt menu ("give me another flyer idea",
    # reroll/regenerate wording). Generic ideas/suggestions are customer-help
    # intent and must not mutate a non-terminal project just because one exists.
    if (
        actions.find_active_flyer_project_by_sender(phone, chat_id)
        and _sample_prompt_request_should_yield_to_active_project(body)
    ):
        return None
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
    if not customer:
        ok, detail, intake = actions.trigger_flyer_intake(
            chat_id=chat_id,
            sender_phone=phone,
            message_id=_extract_message_id(event, chat_id, text),
            text=text,
            media_path=media_path or "",
            start_source="sample_idea",
            original_text=text,
        )
        if not ok or not intake:
            actions.audit_intercepted(
                reason="flyer_intake_failed",
                chat_id=chat_id,
                subprocess_rc=2,
                detail=f"source=sample_idea_new_customer; detail={detail[:450]}",
            )
            return None
        reply = str(intake.get("reply_text") or "")
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, reply,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "intake.acknowledged",
            ),
            allow_duplicate=True,
        )
        actions.audit_intercepted(
            reason="flyer_sample_prompt_requested",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"customer_id=; sender_role={role}; action={intake.get('action') or ''}; "
                f"ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer sample prompts sent"}
    if customer.get("status") not in {"trial", "active"}:
        reply = actions.flyer_customer_not_active_reply(customer)
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, reply,
            action_context=build_action_context(
                action_id="flyer.account.customer_not_active",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_customer_not_active",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"customer_id={customer.get('customer_id') or ''}; status={customer.get('status') or ''}; "
                f"sender_role={role}; sample_prompt_request=true; ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer customer not active"}

    ok, detail, intake = actions.trigger_flyer_intake(
        chat_id=chat_id,
        sender_phone=phone,
        message_id=_extract_message_id(event, chat_id, text),
        text=text,
        media_path=media_path or "",
        start_source="sample_idea",
        original_text=text,
    )
    if not ok or not intake:
        actions.audit_intercepted(
            reason="flyer_intake_failed",
            chat_id=chat_id,
            subprocess_rc=2,
            detail=f"source=explicit_sample_prompt; detail={detail[:450]}",
        )
        return None
    reply = str(intake.get("reply_text") or "")
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply,
        action_context=build_action_context_for_command(
            PROJECT_ACTIONS, "intake.acknowledged",
        ),
        allow_duplicate=True,
    )
    actions.audit_intercepted(
        reason="flyer_sample_prompt_requested",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"customer_id={customer.get('customer_id') or ''}; sender_role={role}; "
            f"action={intake.get('action') or ''}; ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    return {"action": "skip", "reason": "cf-router flyer sample prompts sent"}


def _try_flyer_primary_intercept(
    text: str,
    chat_id: str,
    event: Any,
    *,
    force_new: bool = False,
    media_path: Optional[str] = None,
    brief_audit_detail: str = "",
) -> Optional[dict]:
    """Create a Flyer Studio project deterministically before LLM dispatch.

    This mirrors the Catering F7 primary-mode safety pattern: explicit flyer
    requests should not depend on the generic LLM dispatcher being able to
    call shell tools correctly.
    """
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    brief_detail = f"; {brief_audit_detail}" if brief_audit_detail else ""
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
    if not phone:
        phone = _flyer_customer_sender_phone(customer)
    if not phone:
        return None
    if actions.is_vague_flyer_start(text, has_media=bool(media_path)) and not customer:
        return _start_flyer_intake(
            text, chat_id, event, source="new_flyer", original_text=text,
            media_path=media_path,
        )
    if customer and customer.get("status") not in {"trial", "active"} and not actions.find_paid_flyer_guest_order(phone, chat_id):
        reply = actions.flyer_customer_not_active_reply(customer)
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, reply,
            action_context=build_action_context(
                action_id="flyer.account.customer_not_active",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_customer_not_active",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"customer_id={customer.get('customer_id') or ''}; status={customer.get('status') or ''}; "
                f"sender_role={role}; explicit=true; ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer customer not active"}

    scope_block = actions.flyer_business_scope_block_message(customer or {}, text)
    if scope_block:
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, scope_block,
            action_context=build_action_context(
                action_id="flyer.scope.business_scope_blocked",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_business_scope_blocked",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"customer_id={(customer or {}).get('customer_id') or ''}; sender_role={role}; "
                f"ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer business scope blocked"}

    active_project = None if force_new else actions.find_active_flyer_project_by_sender(phone, chat_id)
    if active_project is not None:
        project_id = str(active_project.get("project_id") or "")
        has_required = actions.flyer_project_has_required_fields(active_project)
        if has_required and not active_project.get("concepts"):
            access, quota_result = _reserve_flyer_access_or_reply(
                chat_id, phone, project_id, message_id,
                consume_quota=not bool(active_project.get("revisions")),
            )
            if quota_result is not None:
                return quota_result
            proc_ok, proc_mid, proc_err = actions.send_flyer_processing_ack(
                chat_id, project_id,
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "intake.processing",
                    is_regulated_action=False,
                ),
            )
            gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
            if gen_ok:
                if not active_project.get("revisions"):
                    ack_ok, outbound_message_id, ack_err = _send_preview_then_finalize_access(
                        access, chat_id, phone, project_id, message_id,
                        proc_ok=proc_ok, proc_mid=proc_mid, proc_err=proc_err,
                    )
                else:
                    ack_ok, outbound_message_id, ack_err = actions._dispatch_concept_preview_send(chat_id, project_id)
                    outbound_message_id = ",".join(x for x in [proc_mid, outbound_message_id] if x)
                    if not proc_ok:
                        ack_err = f"processing_ack_failed: {proc_err}; ack_error={ack_err}"
            else:
                if not active_project.get("revisions"):
                    _release_flyer_access(access, chat_id, phone, project_id, message_id)
                ack_ok, outbound_message_id, ack_err = _send_generation_failure_customer_update(
                    chat_id,
                    project_id,
                    text,
                    gen_detail,
                    proc_ok=proc_ok,
                    action_context=build_action_context_for_command(
                        PROJECT_ACTIONS, "generation.failed_ack",
                        is_regulated_action=False,
                    ),
                )
                outbound_message_id = ",".join(x for x in [proc_mid, outbound_message_id] if x)
                ack_err = f"concept_generation_failed: {gen_detail}; ack_error={ack_err}"
        else:
            if _fb_yield_missing_info(chat_id, message_id):
                return None
            ack_ok, outbound_message_id, ack_err = actions.send_flyer_text(
                chat_id,
                actions.flyer_project_missing_info_reply(active_project),
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "clarification.request",
                    is_regulated_action=False,
                ),
            )
        generation_failed = str(ack_err or "").startswith("concept_generation_failed:")
        actions.audit_intercepted(
            reason="flyer_primary_failed" if generation_failed or not ack_ok else "flyer_primary_project_created",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok and not generation_failed else (2 if generation_failed else 3),
            detail=(
                f"project_id={project_id}; sender_role={role}; existing=true; "
                f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
            ),
        )
        return {"action": "skip",
                "reason": f"cf-router flyer primary: project {project_id} resumed"}

    raw_request = _flyer_raw_request_with_reference(text, media_path)
    customer = customer or actions.find_flyer_customer_by_sender(phone, chat_id)
    block_message = actions.flyer_location_block_message(customer or {}, raw_request)
    if block_message:
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, block_message,
            action_context=build_action_context(
                action_id="flyer.scope.location_blocked",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_location_blocked",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}",
        )
        return {"action": "skip", "reason": "cf-router flyer location blocked"}
    if media_path and customer:
        scope_ok, scope_detail, scope = actions.trigger_check_flyer_reference_scope(
            customer=customer,
            media_path=media_path,
            raw_request=raw_request,
        )
        decision = str((scope or {}).get("decision") or "")
        if not scope_ok:
            decision = "clarify"
            business_name = str(customer.get("business_name") or "this business")
            scope = {
                "reply_text": (
                    "Flyer Studio\n"
                    "------------\n"
                    f"I could not confirm whether the attached flyer belongs to {business_name}.\n\n"
                    f"If you own or are authorized to use this flyer, reply with how it is connected to {business_name}, "
                    f"and send the {business_name} logo/details to use.\n"
                    f"If this is only a reference, reply \"use as reference\" and Flyer Studio can create a new original "
                    f"{business_name} flyer using it as inspiration without copying another business's branding/layout exactly.\n\n"
                    "For a different business or organization, please use Create One Flyer - $4 or contact support."
                ),
                "reason": scope_detail[:160],
            }
        if decision in {"block", "clarify"}:
            reply = str((scope or {}).get("reply_text") or "").strip()
            if not reply:
                reply = (
                    "Flyer Studio\n"
                    "------------\n"
                    "I could not confirm this attached flyer belongs to this business account. "
                    "Please send a related flyer/reference or use Create One Flyer - $4 for unrelated work."
                )
            # Compute `original_intent` BEFORE save so the downstream
            # SOURCE/NEW intercept can branch on exact-source-edit
            # vs generic-reference. Today line 528 evaluates
            # `is_exact_reference_edit_request` AFTER we return at 527
            # for clarify/block — that signal would be lost without this
            # capture. Computed only inside the clarify/block branch so
            # scope_ok rows don't carry stale intent metadata.
            original_intent = (
                "exact_source_edit"
                if media_path and actions.is_exact_reference_edit_request(text, has_media=True)
                else "generic_reference"
            )
            actions.save_flyer_reference_scope_pending(
                chat_id=chat_id,
                sender_phone=phone,
                customer=customer,
                raw_request=raw_request,
                media_path=media_path,
                scope=scope or {},
                original_intent=original_intent,
            )
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id, reply,
                action_context=build_action_context(
                    action_id="flyer.scope.reference_scope_blocked",
                    is_regulated_action=False,
                ),
            )
            actions.audit_intercepted(
                reason="flyer_reference_scope_blocked",
                chat_id=chat_id,
                subprocess_rc=0 if ack_ok else 3,
                detail=(
                    f"decision={decision}; sender_role={role}; "
                    f"scope_reason={(scope or {}).get('reason') or ''}; "
                    f"ack_message_id={mid}; ack_error={err[:300]}"
                ),
            )
            return {"action": "skip", "reason": f"cf-router flyer reference scope {decision}"}
    exact_reference_edit = bool(media_path and actions.is_exact_reference_edit_request(text, has_media=True))
    if exact_reference_edit:
        visible_request = " ".join(actions.flyer_visible_message_text(text).split())
        raw_request = f"Edit uploaded flyer/source artwork. Customer requested: {visible_request}"
    quota_precheck = _flyer_quota_precheck_or_reply(chat_id, phone, message_id)
    if quota_precheck is not None:
        return quota_precheck
    ok, detail, project = actions.trigger_create_flyer_project(
        customer_phone=phone,
        chat_id=chat_id,
        raw_request=raw_request,
        message_id=message_id,
        reference_media_path=media_path or "",
        manual_edit_required=exact_reference_edit,
    )
    project_id = str((project or {}).get("project_id") or "")
    if not ok or not project_id:
        actions.audit_intercepted(
            reason="flyer_primary_failed", chat_id=chat_id,
            subprocess_rc=2, detail=detail[:500],
        )
        return None
    if not exact_reference_edit and actions.flyer_project_has_manual_review_queued(project or {}):
        manual = (project or {}).get("manual_review") or {}
        ack_ok, outbound_message_id, ack_err = actions.send_flyer_manual_review_ack(
            chat_id,
            project_id,
            text,
            reason=str(manual.get("detail") or manual.get("reason") or ""),
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "manual_review.queued",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_reference_manual_review_queued",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"project_id={project_id}; sender_role={role}; "
                f"manual_reason={manual.get('reason') or ''}; "
                f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}{brief_detail}"
            ),
        )
        return {"action": "skip", "reason": f"cf-router flyer manual review queued: project {project_id}"}

    if exact_reference_edit:
        access, quota_result = _reserve_flyer_access_or_reply(chat_id, phone, project_id, message_id, consume_quota=True)
        if quota_result is not None:
            return quota_result
        ready_ok, ready_detail, ready_reason_code = actions.flyer_source_edit_preflight(project or {})
        if not ready_ok:
            # P0-5: route via --manual-reason-code (the typed enum field that the
            # cockpit triage view groups + tallies on). The preflight now returns
            # the per-failure reason_code (`source_edit_provider_unavailable` /
            # `reference_unsupported` / `reference_provider_unavailable`) so a
            # PDF reference is not mis-bucketed as a provider outage.
            queue_ok, queue_detail = actions.invoke_update_flyer_project(
                project_id,
                "--queue-manual-review",
                "--manual-reason-code", ready_reason_code,
                "--manual-reason", ready_reason_code,
                "--manual-detail", ready_detail[:500],
            )
            release_ok, release_detail = _release_flyer_access(access, chat_id, phone, project_id, message_id)
            # If the queue update silently failed (e.g. schema-rejected transition,
            # concurrent mutation), do NOT send the customer a "queued for manual
            # review" ack — that would be a lie. Skip the ack and audit the
            # failure so the operator sees the broken-state row.
            if queue_ok:
                ack_ok, outbound_message_id, ack_err = actions.send_flyer_manual_edit_ack(
                    chat_id,
                    project_id,
                    text,
                    reason=ready_detail,
                    action_context=build_action_context_for_command(
                        PROJECT_ACTIONS, "manual_edit.queued",
                        is_regulated_action=False,
                    ),
                )
            else:
                ack_ok = False
                outbound_message_id = ""
                ack_err = f"manual_edit_ack_skipped_because_queue_failed: {queue_detail[:200]}"
            actions.audit_intercepted(
                reason="flyer_reference_exact_edit_queued" if (queue_ok and ack_ok) else "flyer_primary_failed",
                chat_id=chat_id,
                subprocess_rc=0 if queue_ok and ack_ok and release_ok else 3,
                detail=(
                    f"project_id={project_id}; sender_role={role}; "
                    f"source_edit_preflight_failed={ready_detail[:250]}; "
                    f"reason_code={ready_reason_code}; access={access}; "
                    f"queue_ok={queue_ok}; queue_detail={queue_detail[:250]}; "
                f"release_ok={release_ok}; release_detail={release_detail[:250]}; "
                    f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}{brief_detail}"
                ),
            )
            return {
                "action": "skip",
                "reason": f"cf-router flyer exact edit queued: project {project_id}",
            }
        proc_ok, proc_mid, proc_err = actions.send_flyer_edit_processing_ack(
            chat_id, project_id,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "edit.processing",
                is_regulated_action=False,
            ),
        )
        gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
        if gen_ok:
            ack_ok, outbound_message_id, ack_err = _send_preview_then_finalize_access(
                access, chat_id, phone, project_id, message_id,
                proc_ok=proc_ok, proc_mid=proc_mid, proc_err=proc_err,
            )
            reason = (
                f"cf-router flyer exact edit generated: project {project_id}"
                if ack_ok else f"cf-router flyer exact edit delivery failed: project {project_id}"
            )
        else:
            if not actions.flyer_generation_queued_manual_review(gen_detail):
                actions.invoke_update_flyer_project(
                    project_id,
                    "--queue-manual-review",
                    "--manual-reason", "source_edit_generation_failed",
                    "--manual-reason-code", "provider_timeout",
                    "--manual-detail", gen_detail[:500],
                )
            ack_ok, manual_mid, ack_err = actions.send_flyer_manual_edit_ack(
                chat_id,
                project_id,
                text,
                reason=f"automatic edit generation failed: {gen_detail}",
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "manual_edit.queued",
                    is_regulated_action=False,
                ),
            )
            outbound_message_id = ",".join(x for x in [proc_mid, manual_mid] if x)
            ack_err = (
                f"edit_generation_failed: {gen_detail}; "
                "access_held_for_manual_review=true; "
                f"ack_error={ack_err}"
            )
            reason = f"cf-router flyer exact edit queued: project {project_id}"
        actions.audit_intercepted(
            reason="flyer_primary_project_created" if gen_ok else "flyer_reference_exact_edit_queued",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"project_id={project_id}; sender_role={role}; "
                f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}{brief_detail}"
            ),
        )
        return {"action": "skip", "reason": reason}

    has_required = actions.flyer_project_has_required_fields(project or {})
    if has_required:
        access, quota_result = _reserve_flyer_access_or_reply(chat_id, phone, project_id, message_id, consume_quota=True)
        if quota_result is not None:
            return quota_result
        proc_ok, proc_mid, proc_err = actions.send_flyer_processing_ack(
            chat_id, project_id,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "intake.processing",
                is_regulated_action=False,
            ),
        )
        gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
        if gen_ok:
            ack_ok, outbound_message_id, ack_err = _send_preview_then_finalize_access(
                access, chat_id, phone, project_id, message_id,
                proc_ok=proc_ok, proc_mid=proc_mid, proc_err=proc_err,
            )
        else:
            _release_flyer_access(access, chat_id, phone, project_id, message_id)
            ack_ok, outbound_message_id, ack_err = _send_generation_failure_customer_update(
                chat_id,
                project_id,
                text,
                gen_detail,
                proc_ok=proc_ok,
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "generation.failed_ack",
                    is_regulated_action=False,
                ),
            )
            outbound_message_id = ",".join(x for x in [proc_mid, outbound_message_id] if x)
            ack_err = f"concept_generation_failed: {gen_detail}; ack_error={ack_err}"
    else:
        # ORPHAN NOTE: the project above was already created deterministically.
        # Yielding here hands the clarifying turn to Hermes but leaves this
        # just-created incomplete project in state — create-flyer-project dedups
        # only on original_message_id (it does NOT reuse an active incomplete
        # project), so a later Hermes create makes a fresh id. Accepted for the
        # dormant Phase-1 pilot; the orphan is an incomplete row that never
        # renders/sends. See report + flyer_intake SKILL hand-off note.
        if _fb_yield_missing_info(chat_id, message_id):
            return None
        ack_ok, outbound_message_id, ack_err = actions.send_flyer_text(
            chat_id,
            actions.flyer_project_missing_info_reply(project or {}),
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "clarification.request",
                is_regulated_action=False,
            ),
        )
    generation_failed = str(ack_err or "").startswith("concept_generation_failed:")
    actions.audit_intercepted(
        reason="flyer_primary_failed" if generation_failed or not ack_ok else "flyer_primary_project_created",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok and not generation_failed else (2 if generation_failed else 3),
        detail=(
            f"project_id={project_id}; sender_role={role}; "
            f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}{brief_detail}"
        ),
    )
    return {"action": "skip",
            "reason": f"cf-router flyer primary: project {project_id} created"}


def _try_flyer_reference_scope_choice_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if not phone:
        return None
    # When the original raw_request was an exact-source-edit and the customer
    # replies `use as reference`, atomically transition the row to
    # `awaiting_source_vs_new_choice` rather than consume it. The SOURCE/NEW
    # intercept (below) then claims it on the customer's next reply.
    pending = actions.consume_flyer_reference_scope_choice(
        text,
        chat_id=chat_id,
        sender_phone=phone,
        transition_to_status="awaiting_source_vs_new_choice",
    )
    if not pending:
        return None

    choice = str(pending.get("choice") or "")
    business_name = str(((pending.get("customer") or {}).get("business_name")) or "this business")
    source = str(pending.get("source_organization") or "the source flyer")
    original_intent = str(pending.get("original_intent") or "unknown")

    # F0061 load-bearing branch: exact-edit + use-as-reference → ask
    # explicit SOURCE vs NEW. Pre-fix this fell through to the use_reference
    # path and silently downgraded the exact-edit request to a generic
    # poster. The pending row's status has already been atomically rewritten
    # to awaiting_source_vs_new_choice by the consumer above.
    if choice == "use_reference" and original_intent == "exact_source_edit":
        clarification = (
            "Flyer Studio\n"
            "------------\n"
            "I can do this two ways:\n\n"
            "Reply SOURCE to keep this same flyer and apply only the changes you asked for.\n"
            "Reply NEW to create a brand-new flyer inspired by this one (different layout)."
        )
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, clarification,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "clarification.request",
                is_regulated_action=False,
            ),
        )
        pending_created_at = float(pending.get("created_at") or 0)
        pending_age_sec = int(time.time() - pending_created_at) if pending_created_at else 0
        try:
            actions.audit_source_vs_new(
                sender_phone=phone,
                customer_id=str((pending.get("customer") or {}).get("customer_id") or ""),
                original_intent="exact_source_edit",
                choice="clarification_sent",
                pending_age_sec=pending_age_sec,
            )
        except Exception:
            pass  # best-effort; audit never blocks
        actions.audit_intercepted(
            reason="flyer_reference_scope_blocked",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"source_vs_new_clarification_sent; sender_role={role}; "
                f"ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer source-vs-new clarification sent"}

    if choice == "authorized":
        actions.save_flyer_reference_authorization_pending(pending)
        reply = (
            "Flyer Studio\n"
            "------------\n"
            f"Thanks. Please reply with how {source} is connected to {business_name}, "
            f"and include any {business_name} logo/details that are different from the saved account details.\n\n"
            "If the saved account details are correct, a short answer like \"co-owner\" is enough."
        )
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, reply,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "clarification.request",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_reference_scope_blocked",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=f"sender_role={role}; source={source}; ack_message_id={mid}; ack_error={err[:300]}",
        )
        return {"action": "skip", "reason": "cf-router flyer reference scope authorized path"}

    if choice != "use_reference":
        return None

    raw_request = (
        f"{pending.get('raw_request') or ''}\n\n"
        f"Customer chose path 2: use {source} only as a reference/inspiration. "
        f"Create a new original {business_name} flyer with a similar menu/content structure. "
        f"Do not copy {source} branding/layout exactly."
    ).strip()
    quota_precheck = _flyer_quota_precheck_or_reply(chat_id, phone, message_id)
    if quota_precheck is not None:
        return quota_precheck
    ok, detail, project = actions.trigger_create_flyer_project(
        customer_phone=phone,
        chat_id=chat_id,
        raw_request=raw_request,
        message_id=message_id,
        reference_media_path=str(pending.get("media_path") or ""),
    )
    project_id = str((project or {}).get("project_id") or "")
    if not ok or not project_id:
        actions.audit_intercepted(
            reason="flyer_primary_failed", chat_id=chat_id,
            subprocess_rc=2, detail=f"reference_choice=true; {detail[:450]}",
        )
        return None
    if actions.flyer_project_has_manual_review_queued(project or {}):
        manual = (project or {}).get("manual_review") or {}
        ack_ok, outbound_message_id, ack_err = actions.send_flyer_manual_review_ack(
            chat_id,
            project_id,
            raw_request,
            reason=str(manual.get("detail") or manual.get("reason") or ""),
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "manual_review.queued",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_reference_manual_review_queued",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"project_id={project_id}; sender_role={role}; source={source}; "
                f"manual_reason={manual.get('reason') or ''}; "
                f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
            ),
        )
        return {"action": "skip", "reason": f"cf-router flyer reference manual review queued: project {project_id}"}

    has_required = actions.flyer_project_has_required_fields(project or {})
    if has_required:
        access, quota_result = _reserve_flyer_access_or_reply(chat_id, phone, project_id, message_id, consume_quota=True)
        if quota_result is not None:
            return quota_result
        proc_ok, proc_mid, proc_err = actions.send_flyer_processing_ack(
            chat_id, project_id,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "intake.processing",
                is_regulated_action=False,
            ),
        )
        gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
        if gen_ok:
            ack_ok, outbound_message_id, ack_err = _send_preview_then_finalize_access(
                access, chat_id, phone, project_id, message_id,
                proc_ok=proc_ok, proc_mid=proc_mid, proc_err=proc_err,
            )
        else:
            _release_flyer_access(access, chat_id, phone, project_id, message_id)
            ack_ok, outbound_message_id, ack_err = _send_generation_failure_customer_update(
                chat_id,
                project_id,
                raw_request,
                gen_detail,
                proc_ok=proc_ok,
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "generation.failed_ack",
                    is_regulated_action=False,
                ),
            )
            outbound_message_id = ",".join(x for x in [proc_mid, outbound_message_id] if x)
            ack_err = f"concept_generation_failed: {gen_detail}; ack_error={ack_err}"
    else:
        if _fb_yield_missing_info(chat_id, message_id):
            return None
        ack_ok, outbound_message_id, ack_err = actions.send_flyer_text(
            chat_id,
            actions.flyer_project_missing_info_reply(project or {}),
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "clarification.request",
                is_regulated_action=False,
            ),
        )
    generation_failed = str(ack_err or "").startswith("concept_generation_failed:")
    actions.audit_intercepted(
        reason="flyer_primary_failed" if generation_failed or not ack_ok else "flyer_primary_project_created",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok and not generation_failed else (2 if generation_failed else 3),
        detail=(
            f"project_id={project_id}; sender_role={role}; source={source}; "
            f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
        ),
    )
    return {"action": "skip",
            "reason": f"cf-router flyer reference scope use-reference: project {project_id}"}


def _try_flyer_source_vs_new_choice_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    """Claim SOURCE/NEW replies after `_try_flyer_reference_scope_choice_intercept`
    transitioned a pending row to `awaiting_source_vs_new_choice`.

    Five branches:
      1. Compound parse: SOURCE/NEW followed by trailing instruction.
      2. Status check-in pre-claim: `any update?` re-sends the clarification
         verbatim and audits `clarification_resent` without consuming.
      3. SOURCE branch: rebuilds raw_request with the source-edit marker
         and triggers create-flyer-project with manual_edit_required=True.
      4. NEW branch: triggers create-flyer-project WITHOUT manual_edit_required.
      5. Idempotent retry: when consume returns None but a recent (<60s)
         manual_edit_required project exists for this customer, re-send the ack.
    """
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if not phone:
        return None

    choice_token, trailing = actions.parse_source_vs_new_followup(text)

    # Branch 2: status check-in re-send.
    if not choice_token:
        existing = actions.peek_flyer_source_vs_new_pending(chat_id=chat_id, sender_phone=phone)
        if existing and actions.flyer_is_status_checkin(text):
            clarification = (
                "Flyer Studio\n"
                "------------\n"
                "I can do this two ways:\n\n"
                "Reply SOURCE to keep this same flyer and apply only the changes you asked for.\n"
                "Reply NEW to create a brand-new flyer inspired by this one (different layout)."
            )
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id, clarification,
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "clarification.request",
                    is_regulated_action=False,
                ),
            )
            try:
                actions.audit_source_vs_new(
                    sender_phone=phone,
                    customer_id=str((existing.get("customer") or {}).get("customer_id") or ""),
                    original_intent="exact_source_edit",
                    choice="clarification_resent",
                )
            except Exception:
                pass
            actions.audit_intercepted(
                reason="flyer_reference_scope_blocked",
                chat_id=chat_id,
                subprocess_rc=0 if ack_ok else 3,
                detail=f"source_vs_new_status_checkin_resent; ack_message_id={mid}; ack_error={err[:300]}",
            )
            return {"action": "skip", "reason": "cf-router flyer source-vs-new status check-in"}
        return None

    pending = actions.consume_flyer_source_vs_new_choice(
        choice_token,
        trailing,
        chat_id=chat_id,
        sender_phone=phone,
    )
    if not pending:
        # Branch 5: idempotent retry. SOURCE-only by design: a SOURCE-chosen
        # project queues for manual review and the customer gets one ack, so
        # a duplicate SOURCE reply within 60s should re-send the same ack.
        # NEW-chosen projects already trigger a customer-facing concept-
        # generation ack via the existing flyer-primary-project-created path,
        # so a duplicate NEW reply does not need a second ack here — it falls
        # through to the next intercept (or to the LLM) where the active-
        # project intercept will catch any further customer text.
        recent = actions.find_recent_flyer_manual_edit_project(phone, window_sec=60)
        if recent and choice_token == "source":
            project_id = str(recent.get("project_id") or "")
            ack_ok, mid, err = actions.send_flyer_manual_edit_ack(
                chat_id,
                project_id,
                str(recent.get("raw_request") or ""),
                reason="source_edit_provider_unavailable",
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "manual_edit.queued",
                    is_regulated_action=False,
                ),
            )
            actions.audit_intercepted(
                reason="flyer_reference_exact_edit_queued",
                chat_id=chat_id,
                subprocess_rc=0 if ack_ok else 3,
                detail=f"source_vs_new_retry_idempotent; project_id={project_id}; ack_message_id={mid}; ack_error={err[:300]}",
            )
            return {"action": "skip", "reason": f"cf-router flyer source-vs-new retry idempotent: project {project_id}"}
        return None

    customer = pending.get("customer") or {}
    business_name = str(customer.get("business_name") or "this business")
    raw_request = str(pending.get("raw_request") or "")
    trailing_text = str(pending.get("customer_followup_instruction") or "")
    customer_id = str(customer.get("customer_id") or "")
    pending_created_at = float(pending.get("created_at") or 0)
    pending_age_sec = int(time.time() - pending_created_at) if pending_created_at else 0

    if pending.get("choice") == "source":
        visible = " ".join(actions.flyer_visible_message_text(raw_request).split())
        if trailing_text:
            visible = f"{visible}. Also: {trailing_text}"
        new_raw_request = f"Edit uploaded flyer/source artwork. Customer requested: {visible}"
        ok, detail, project = actions.trigger_create_flyer_project(
            customer_phone=phone,
            chat_id=chat_id,
            raw_request=new_raw_request,
            message_id=message_id,
            reference_media_path=str(pending.get("media_path") or ""),
            manual_edit_required=True,
        )
        project_id = str((project or {}).get("project_id") or "")
        try:
            actions.audit_source_vs_new(
                sender_phone=phone,
                customer_id=customer_id,
                original_intent="exact_source_edit",
                choice="source",
                pending_age_sec=pending_age_sec,
                customer_followup_instruction=trailing_text,
            )
        except Exception:
            pass
        if not ok or not project_id:
            actions.audit_intercepted(
                reason="flyer_primary_failed", chat_id=chat_id,
                subprocess_rc=2, detail=f"source_vs_new_source_failed; {detail[:450]}",
            )
            return None

        # Mirror the existing primary exact-edit handler at hooks.py:587-697:
        # reserve quota → preflight → either run generation OR queue manual review.
        # Previous shape always sent the manual-edit ack, which means a valid
        # OPENAI_API_KEY never reached generation from the SOURCE branch.
        access, quota_result = _reserve_flyer_access_or_reply(
            chat_id, phone, project_id, message_id, consume_quota=True,
        )
        if quota_result is not None:
            return quota_result
        ready_ok, ready_detail, ready_reason_code = actions.flyer_source_edit_preflight(project or {})
        if not ready_ok:
            queue_ok, queue_detail = actions.invoke_update_flyer_project(
                project_id,
                "--queue-manual-review",
                "--manual-reason-code", ready_reason_code,
                "--manual-reason", ready_reason_code,
                "--manual-detail", ready_detail[:500],
            )
            release_ok, release_detail = _release_flyer_access(
                access, chat_id, phone, project_id, message_id,
            )
            if queue_ok:
                ack_ok, outbound_message_id, ack_err = actions.send_flyer_manual_edit_ack(
                    chat_id,
                    project_id,
                    new_raw_request,
                    reason=ready_detail,
                    action_context=build_action_context_for_command(
                        PROJECT_ACTIONS, "manual_edit.queued",
                        is_regulated_action=False,
                    ),
                )
            else:
                ack_ok = False
                outbound_message_id = ""
                ack_err = f"manual_edit_ack_skipped_because_queue_failed: {queue_detail[:200]}"
            actions.audit_intercepted(
                reason="flyer_reference_exact_edit_queued" if (queue_ok and ack_ok) else "flyer_primary_failed",
                chat_id=chat_id,
                subprocess_rc=0 if queue_ok and ack_ok and release_ok else 3,
                detail=(
                    f"source_vs_new_source; project_id={project_id}; sender_role={role}; "
                    f"source_edit_preflight_failed={ready_detail[:250]}; "
                    f"reason_code={ready_reason_code}; access={access}; "
                    f"queue_ok={queue_ok}; queue_detail={queue_detail[:250]}; "
                    f"release_ok={release_ok}; release_detail={release_detail[:250]}; "
                    f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
                ),
            )
            return {
                "action": "skip",
                "reason": f"cf-router flyer source-edit queued: project {project_id}",
            }
        proc_ok, proc_mid, proc_err = actions.send_flyer_edit_processing_ack(
            chat_id, project_id,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "edit.processing",
                is_regulated_action=False,
            ),
        )
        gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
        if gen_ok:
            ack_ok, outbound_message_id, ack_err = _send_preview_then_finalize_access(
                access, chat_id, phone, project_id, message_id,
                proc_ok=proc_ok, proc_mid=proc_mid, proc_err=proc_err,
            )
            reason = (
                f"cf-router flyer source-edit generated: project {project_id}"
                if ack_ok else f"cf-router flyer source-edit delivery failed: project {project_id}"
            )
            audit_reason = "flyer_primary_project_created"
        else:
            if not actions.flyer_generation_queued_manual_review(gen_detail):
                actions.invoke_update_flyer_project(
                    project_id,
                    "--queue-manual-review",
                    "--manual-reason", "source_edit_generation_failed",
                    "--manual-reason-code", "provider_timeout",
                    "--manual-detail", gen_detail[:500],
                )
            ack_ok, manual_mid, ack_err = actions.send_flyer_manual_edit_ack(
                chat_id,
                project_id,
                new_raw_request,
                reason=f"automatic edit generation failed: {gen_detail}",
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "manual_edit.queued",
                    is_regulated_action=False,
                ),
            )
            release_ok, release_detail = _release_flyer_access(
                access, chat_id, phone, project_id, message_id,
            )
            outbound_message_id = ",".join(x for x in [proc_mid, manual_mid] if x)
            ack_err = (
                f"edit_generation_failed: {gen_detail}; "
                f"release_ok={release_ok}; release_detail={release_detail[:250]}; ack_error={ack_err}"
            )
            reason = f"cf-router flyer source-edit queued: project {project_id}"
            audit_reason = "flyer_reference_exact_edit_queued"
        actions.audit_intercepted(
            reason=audit_reason,
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"source_vs_new_source; project_id={project_id}; sender_role={role}; "
                f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
            ),
        )
        return {"action": "skip", "reason": reason}

    if pending.get("choice") == "new":
        source = str(pending.get("source_organization") or "the source flyer")
        new_raw_request = (
            f"{raw_request}\n\n"
            f"Customer chose path 2: use {source} only as a reference/inspiration. "
            f"Create a new original {business_name} flyer with a similar menu/content structure. "
            f"Do not copy {source} branding/layout exactly."
        ).strip()
        if trailing_text:
            new_raw_request = f"{new_raw_request}\n\nAdditional customer instruction: {trailing_text}"
        ok, detail, project = actions.trigger_create_flyer_project(
            customer_phone=phone,
            chat_id=chat_id,
            raw_request=new_raw_request,
            message_id=message_id,
            reference_media_path=str(pending.get("media_path") or ""),
        )
        project_id = str((project or {}).get("project_id") or "")
        try:
            actions.audit_source_vs_new(
                sender_phone=phone,
                customer_id=customer_id,
                original_intent="exact_source_edit",
                choice="new",
                pending_age_sec=pending_age_sec,
                customer_followup_instruction=trailing_text,
            )
        except Exception:
            pass
        if not ok or not project_id:
            actions.audit_intercepted(
                reason="flyer_primary_failed", chat_id=chat_id,
                subprocess_rc=2, detail=f"source_vs_new_new_failed; {detail[:450]}",
            )
            return None
        if actions.flyer_project_has_manual_review_queued(project or {}):
            manual = (project or {}).get("manual_review") or {}
            ack_ok, outbound_message_id, ack_err = actions.send_flyer_manual_review_ack(
                chat_id,
                project_id,
                new_raw_request,
                reason=str(manual.get("detail") or manual.get("reason") or ""),
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "manual_review.queued",
                    is_regulated_action=False,
                ),
            )
        elif actions.flyer_project_has_required_fields(project or {}):
            access, quota_result = _reserve_flyer_access_or_reply(
                chat_id, phone, project_id, message_id, consume_quota=True,
            )
            if quota_result is not None:
                return quota_result
            proc_ok, proc_mid, proc_err = actions.send_flyer_processing_ack(
                chat_id,
                project_id,
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "intake.processing",
                    is_regulated_action=False,
                ),
            )
            gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
            if gen_ok:
                ack_ok, outbound_message_id, ack_err = _send_preview_then_finalize_access(
                    access,
                    chat_id,
                    phone,
                    project_id,
                    message_id,
                    proc_ok=proc_ok,
                    proc_mid=proc_mid,
                    proc_err=proc_err,
                )
            else:
                _release_flyer_access(access, chat_id, phone, project_id, message_id)
                ack_ok, outbound_message_id, ack_err = _send_generation_failure_customer_update(
                    chat_id,
                    project_id,
                    new_raw_request,
                    gen_detail,
                    proc_ok=proc_ok,
                    action_context=build_action_context_for_command(
                        PROJECT_ACTIONS, "generation.failed_ack",
                        is_regulated_action=False,
                    ),
                )
                outbound_message_id = ",".join(x for x in [proc_mid, outbound_message_id] if x)
                ack_err = f"concept_generation_failed: {gen_detail}; ack_error={ack_err}"
        else:
            ack_ok, outbound_message_id, ack_err = actions.send_flyer_intake_ack(
                chat_id, project_id,
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "intake.acknowledged",
                    is_regulated_action=False,
                ),
            )
        generation_failed = str(ack_err or "").startswith("concept_generation_failed:")
        actions.audit_intercepted(
            reason="flyer_primary_failed" if generation_failed or not ack_ok else "flyer_primary_project_created",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok and not generation_failed else (2 if generation_failed else 3),
            detail=(
                f"source_vs_new_new; project_id={project_id}; sender_role={role}; "
                f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
            ),
        )
        return {"action": "skip", "reason": f"cf-router flyer new-from-source chosen: project {project_id}"}

    return None


def _try_flyer_reference_scope_authorization_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if not phone:
        return None
    pending = actions.consume_flyer_reference_authorization_reply(text, chat_id=chat_id, sender_phone=phone)
    if not pending:
        return None

    choice = str(pending.get("choice") or "")
    business_name = str(((pending.get("customer") or {}).get("business_name")) or "this business")
    source = str(pending.get("source_organization") or "the source flyer")

    if choice == "use_account_details":
        message_id = _extract_message_id(event, chat_id, text)
        original_raw_request = str(pending.get("raw_request") or "").strip()
        original_intent = str(pending.get("original_intent") or "unknown")
        if (
            original_intent == "generic_reference"
            or actions.is_reference_concept_adaptation_request(original_raw_request, has_media=True)
        ):
            auth_note = str(pending.get("authorization_note") or "Customer confirmed authorization").strip()
            raw_request = (
                f"{original_raw_request}\n\n"
                f"Customer authorization note for using the attached reference: {auth_note}.\n"
                f"Use saved {business_name} account details.\n"
                f"Use {source} only as a reference/inspiration. "
                f"Create a new original {business_name} flyer with a similar menu/content structure. "
                f"Do not copy {source} branding/layout exactly."
            ).strip()
            ok, detail, project = actions.trigger_create_flyer_project(
                customer_phone=phone,
                chat_id=chat_id,
                raw_request=raw_request,
                message_id=message_id,
                reference_media_path=str(pending.get("media_path") or ""),
            )
            project_id = str((project or {}).get("project_id") or "")
            if not ok or not project_id:
                actions.audit_intercepted(
                    reason="flyer_primary_failed", chat_id=chat_id,
                    subprocess_rc=2, detail=f"authorized_reference_concept=true; {detail[:450]}",
                )
                return None
            if actions.flyer_project_has_manual_review_queued(project or {}):
                manual = (project or {}).get("manual_review") or {}
                ack_ok, outbound_message_id, ack_err = actions.send_flyer_manual_review_ack(
                    chat_id,
                    project_id,
                    raw_request,
                    reason=str(manual.get("detail") or manual.get("reason") or ""),
                    action_context=build_action_context_for_command(
                        PROJECT_ACTIONS, "manual_review.queued",
                        is_regulated_action=False,
                    ),
                )
            elif actions.flyer_project_has_required_fields(project or {}):
                access, quota_result = _reserve_flyer_access_or_reply(
                    chat_id, phone, project_id, message_id, consume_quota=True,
                )
                if quota_result is not None:
                    return quota_result
                proc_ok, proc_mid, proc_err = actions.send_flyer_processing_ack(
                    chat_id,
                    project_id,
                    action_context=build_action_context_for_command(
                        PROJECT_ACTIONS, "intake.processing",
                        is_regulated_action=False,
                    ),
                )
                gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
                if gen_ok:
                    ack_ok, outbound_message_id, ack_err = _send_preview_then_finalize_access(
                        access,
                        chat_id,
                        phone,
                        project_id,
                        message_id,
                        proc_ok=proc_ok,
                        proc_mid=proc_mid,
                        proc_err=proc_err,
                    )
                else:
                    _release_flyer_access(access, chat_id, phone, project_id, message_id)
                    ack_ok, outbound_message_id, ack_err = _send_generation_failure_customer_update(
                        chat_id,
                        project_id,
                        raw_request,
                        gen_detail,
                        proc_ok=proc_ok,
                        action_context=build_action_context_for_command(
                            PROJECT_ACTIONS, "generation.failed_ack",
                            is_regulated_action=False,
                        ),
                    )
                    outbound_message_id = ",".join(x for x in [proc_mid, outbound_message_id] if x)
                    ack_err = f"concept_generation_failed: {gen_detail}; ack_error={ack_err}"
            else:
                if _fb_yield_missing_info(chat_id, message_id):
                    return None
                ack_ok, outbound_message_id, ack_err = actions.send_flyer_text(
                    chat_id,
                    actions.flyer_project_missing_info_reply(project or {}),
                    action_context=build_action_context_for_command(
                        PROJECT_ACTIONS, "clarification.request",
                        is_regulated_action=False,
                    ),
                )
            generation_failed = str(ack_err or "").startswith("concept_generation_failed:")
            actions.audit_intercepted(
                reason="flyer_primary_failed" if generation_failed or not ack_ok else "flyer_primary_project_created",
                chat_id=chat_id,
                subprocess_rc=0 if ack_ok and not generation_failed else (2 if generation_failed else 3),
                detail=(
                    f"authorized_reference_concept; project_id={project_id}; sender_role={role}; "
                    f"source={source}; ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
                ),
            )
            return {
                "action": "skip",
                "reason": f"cf-router flyer reference scope authorized concept: project {project_id}",
            }
        raw_request = (
            "Authorized flyer/source artwork update.\n"
            f"Authorized relationship note: {pending.get('authorization_note') or 'Customer confirmed authorization'}.\n"
            f"Use saved {business_name} account details.\n\n"
            f"Original customer request: {pending.get('raw_request') or ''}"
        ).strip()
        ok, detail, project = actions.trigger_create_flyer_project(
            customer_phone=phone,
            chat_id=chat_id,
            raw_request=raw_request,
            message_id=message_id,
            reference_media_path=str(pending.get("media_path") or ""),
            manual_edit_required=True,
        )
        project_id = str((project or {}).get("project_id") or "")
        if not ok or not project_id:
            actions.audit_intercepted(
                reason="flyer_primary_failed", chat_id=chat_id,
                subprocess_rc=2, detail=f"authorized_reference=true; {detail[:450]}",
            )
            return None

        access, quota_result = _reserve_flyer_access_or_reply(chat_id, phone, project_id, message_id, consume_quota=True)
        if quota_result is not None:
            return quota_result
        ready_ok, ready_detail, ready_reason_code = actions.flyer_source_edit_preflight(project or {})
        if not ready_ok:
            # P0-5: project state must be updated, then quota released (so a
            # customer retry can't double-reserve), then the ack sent —
            # consistent with site 1 ordering. ack is skipped if the queue
            # update silently failed so we don't lie that the edit is queued.
            queue_ok, queue_detail = actions.invoke_update_flyer_project(
                project_id,
                "--queue-manual-review",
                "--manual-reason-code", ready_reason_code,
                "--manual-reason", ready_reason_code,
                "--manual-detail", ready_detail[:500],
            )
            release_ok, release_detail = _release_flyer_access(access, chat_id, phone, project_id, message_id)
            if queue_ok:
                ack_ok, outbound_message_id, ack_err = actions.send_flyer_manual_edit_ack(
                    chat_id,
                    project_id,
                    raw_request,
                    reason=ready_detail,
                    action_context=build_action_context_for_command(
                        PROJECT_ACTIONS, "manual_edit.queued",
                        is_regulated_action=False,
                    ),
                )
            else:
                ack_ok = False
                outbound_message_id = ""
                ack_err = f"manual_edit_ack_skipped_because_queue_failed: {queue_detail[:200]}"
            actions.audit_intercepted(
                reason="flyer_reference_exact_edit_queued" if (queue_ok and ack_ok) else "flyer_primary_failed",
                chat_id=chat_id,
                subprocess_rc=0 if ack_ok and release_ok and queue_ok else 3,
                detail=(
                    f"project_id={project_id}; sender_role={role}; source={source}; "
                    f"source_edit_preflight_failed={ready_detail[:250]}; "
                    f"reason_code={ready_reason_code}; access={access}; "
                    f"release_ok={release_ok}; release_detail={release_detail[:250]}; "
                    f"queue_ok={queue_ok}; queue_detail={queue_detail[:250]}; "
                    f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
                ),
            )
            return {
                "action": "skip",
                "reason": f"cf-router flyer reference scope authorized queued: project {project_id}",
            }
        proc_ok, proc_mid, proc_err = actions.send_flyer_edit_processing_ack(
            chat_id, project_id,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "edit.processing",
                is_regulated_action=False,
            ),
        )
        gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
        if gen_ok:
            ack_ok, outbound_message_id, ack_err = _send_preview_then_finalize_access(
                access, chat_id, phone, project_id, message_id,
                proc_ok=proc_ok, proc_mid=proc_mid, proc_err=proc_err,
            )
            reason = f"cf-router flyer reference scope authorized generated: project {project_id}"
            audit_reason = "flyer_primary_project_created"
        else:
            ack_ok, manual_mid, ack_err = actions.send_flyer_manual_edit_ack(
                chat_id,
                project_id,
                raw_request,
                reason=f"automatic edit generation failed: {gen_detail}",
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "manual_edit.queued",
                    is_regulated_action=False,
                ),
            )
            release_ok, release_detail = _release_flyer_access(access, chat_id, phone, project_id, message_id)
            outbound_message_id = ",".join(x for x in [proc_mid, manual_mid] if x)
            ack_err = (
                f"edit_generation_failed: {gen_detail}; "
                f"release_ok={release_ok}; release_detail={release_detail[:250]}; ack_error={ack_err}"
            )
            reason = f"cf-router flyer reference scope authorized queued: project {project_id}"
            audit_reason = "flyer_reference_exact_edit_queued"

        actions.audit_intercepted(
            reason=audit_reason,
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"project_id={project_id}; sender_role={role}; source={source}; "
                f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
            ),
        )
        return {"action": "skip", "reason": reason}
    else:
        reply = (
            "Flyer Studio\n"
            "------------\n"
            f"Thanks. I noted that {source} is connected to {business_name}.\n\n"
            f"Please send the {business_name} logo/details to use, or reply \"use account details\" "
            "if the saved Flyer Studio account details should be used."
        )

    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply,
        action_context=build_action_context_for_command(
            PROJECT_ACTIONS, "clarification.request",
            is_regulated_action=False,
        ),
    )
    actions.audit_intercepted(
        reason="flyer_reference_scope_blocked",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"choice={choice}; sender_role={role}; source={source}; "
            f"ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    return {"action": "skip", "reason": f"cf-router flyer reference scope authorization {choice}"}


# Shared by the pre-create precheck and the reserve path, so the two cannot
# drift apart in what a blocked customer is told.
_FLYER_QUOTA_BLOCKED_FALLBACK = (
    "Flyer Studio\n------------\n"
    "Please complete payment first. Tap Create One Flyer - $4, pay, then send your flyer details here."
)


def _flyer_quota_precheck_or_reply(
    chat_id: str, phone: str, message_id: str,
) -> Optional[dict]:
    """Answer the quota question BEFORE any project row exists.

    THE DEFECT THIS CLOSES: `trigger_create_flyer_project` ran first and the
    quota wall was hit a few lines later, leaving the project row behind
    forever. Nothing releases it — both quota-block returns from
    `_reserve_flyer_access_or_reply` yield `access == ""`, and
    `_release_flyer_access` short-circuits on that with `no_access_to_release`,
    so there is no reservation to give back and "release on the block path" is
    a no-op. The orphan row then makes
    `has_non_delivered_flyer_project_by_sender` true for that sender forever,
    and the catering-admission gate hands every later catering inquiry from
    them to the LLM.

    EARLY-OUT ONLY, and only on a DEFINITE block. A paid guest order (which
    bypasses quota entirely), an allowed quota, a missing customer record, a
    subprocess failure or an unparseable result all return None so the inbound
    takes today's path unchanged and the AUTHORITATIVE reservation still
    happens after the project exists. That keeps this a pure optimisation of
    when the customer is told, never a new way to be refused.
    """
    try:
        if actions.find_paid_flyer_guest_order(phone, chat_id):
            return None
        ok, detail, result = actions.trigger_flyer_check_quota(
            customer_phone=phone, message_id=message_id,
        )
    except Exception:  # noqa: BLE001 — an optimisation must never break the route
        # Latent rather than live: both calls already return rather than raise.
        # But this runs ahead of project creation on the primary flyer path, so
        # a future raise inside either would take out flyer creation entirely
        # to save a row. Falling through costs at most one orphan row, which is
        # the exact state this branch is an optimisation against.
        return None
    if not ok or not result or result.get("quota_allowed") is not False:
        return None
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id,
        str(result.get("reply_text") or "") or _FLYER_QUOTA_BLOCKED_FALLBACK,
        action_context=build_action_context(
            action_id="flyer.quota.blocked",
            is_regulated_action=False,
        ),
    )
    actions.audit_intercepted(
        reason="flyer_quota_blocked",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 2,
        detail=(
            f"project_id=; precheck=pre_create; quota_detail={detail[:300]}; "
            f"ack_message_id={mid}; ack_error={err[:200]}"
        ),
    )
    return {"action": "skip",
            "reason": "cf-router flyer quota blocked before project creation"}


def _reserve_flyer_access_or_reply(
    chat_id: str,
    phone: str,
    project_id: str,
    message_id: str,
    *,
    consume_quota: bool,
) -> tuple[str, Optional[dict]]:
    if not consume_quota:
        return "none", None
    if actions.find_reserved_flyer_guest_order(phone, chat_id, project_id):
        return "guest", None
    paid_guest_order = actions.find_paid_flyer_guest_order(phone, chat_id)
    if paid_guest_order:
        ok_guest, detail_guest, _guest_doc = actions.trigger_reserve_flyer_guest_order(
            sender_phone=phone,
            chat_id=chat_id,
            project_id=project_id,
        )
        if ok_guest:
            return "guest", None
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id,
            (
                "Flyer Studio\n------------\n"
                "I could not reserve your paid quick-flyer order for this request. "
                "Please reply STATUS or contact support before retrying."
            ),
            action_context=build_action_context(
                action_id="flyer.guest_order.reserve_failed",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_primary_failed",
            chat_id=chat_id,
            subprocess_rc=2,
            detail=(
                f"project_id={project_id}; guest_reserve_failed={detail_guest[:300]}; "
                f"ack_message_id={mid}; ack_error={err[:200]}; ack_ok={ack_ok}"
            ),
        )
        return "", {"action": "skip", "reason": f"cf-router flyer guest order reserve blocked: {project_id}"}
    ok, detail, result = actions.trigger_flyer_reserve_quota(
        customer_phone=phone,
        project_id=project_id,
        message_id=message_id,
    )
    if ok and result and result.get("quota_allowed"):
        return "quota", None
    reply = (result or {}).get("reply_text") if result else ""
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id,
        reply or _FLYER_QUOTA_BLOCKED_FALLBACK,
        action_context=build_action_context(
            action_id="flyer.quota.blocked",
            is_regulated_action=False,
        ),
    )
    actions.audit_intercepted(
        reason="flyer_primary_failed" if not ok else "flyer_quota_blocked",
        chat_id=chat_id,
        subprocess_rc=0 if ok and ack_ok else 2,
        detail=f"project_id={project_id}; quota_detail={detail[:300]}; ack_message_id={mid}; ack_error={err[:200]}",
    )
    return "", {"action": "skip", "reason": f"cf-router flyer quota blocked: {project_id}"}


def _release_flyer_access(access: str, chat_id: str, phone: str, project_id: str, message_id: str) -> tuple[bool, str]:
    if access == "quota":
        ok, detail, _result = actions.trigger_flyer_release_quota(customer_phone=phone, project_id=project_id, message_id=message_id)
    elif access == "guest":
        ok, detail, _result = actions.trigger_release_flyer_guest_order(sender_phone=phone, chat_id=chat_id, project_id=project_id)
    else:
        return True, "no_access_to_release"
    if not ok:
        actions.audit_intercepted(
            reason="flyer_access_release_failed",
            chat_id=chat_id,
            subprocess_rc=2,
            detail=(
                f"project_id={project_id}; access={access}; message_id={message_id}; "
                f"release_detail={detail[:400]}"
            ),
        )
    return ok, detail


def _finalize_flyer_access_checked(access: str, chat_id: str, phone: str, project_id: str, message_id: str) -> tuple[bool, str]:
    if access == "quota":
        ok, detail, _result = actions.trigger_flyer_finalize_usage(customer_phone=phone, project_id=project_id, message_id=message_id)
    if access == "guest":
        ok, detail, _result = actions.trigger_consume_flyer_guest_order(sender_phone=phone, chat_id=chat_id, project_id=project_id)
    if access not in {"quota", "guest"}:
        return True, "no_access_to_finalize"
    if not ok:
        actions.audit_intercepted(
            reason="flyer_access_finalize_failed",
            chat_id=chat_id,
            subprocess_rc=2,
            detail=(
                f"project_id={project_id}; access={access}; message_id={message_id}; "
                f"finalize_detail={detail[:400]}"
            ),
        )
    return ok, detail


def _send_preview_then_finalize_access(
    access: str,
    chat_id: str,
    phone: str,
    project_id: str,
    message_id: str,
    *,
    proc_ok: bool,
    proc_mid: str,
    proc_err: str,
) -> tuple[bool, str, str]:
    preview_ok, preview_mid, preview_err = actions._dispatch_concept_preview_send(chat_id, project_id)
    outbound_message_id = ",".join(x for x in [proc_mid, preview_mid] if x)
    ack_err = preview_err
    if not proc_ok:
        ack_err = f"processing_ack_failed: {proc_err}; ack_error={ack_err}"
    if not preview_ok:
        if _preview_may_have_delivered(preview_mid, preview_err):
            access_ok, access_detail = _finalize_flyer_access_checked(access, chat_id, phone, project_id, message_id)
            if not access_ok:
                return False, outbound_message_id, f"{ack_err}; access_finalize_failed={access_detail[:250]}"
            return False, outbound_message_id, ack_err
        release_ok, release_detail = _release_flyer_access(access, chat_id, phone, project_id, message_id)
        if not release_ok:
            ack_err = f"{ack_err}; access_release_failed={release_detail[:250]}"
        return False, outbound_message_id, ack_err
    access_ok, access_detail = _finalize_flyer_access_checked(access, chat_id, phone, project_id, message_id)
    if not access_ok:
        return proc_ok and preview_ok, outbound_message_id, f"{ack_err}; access_finalize_failed={access_detail[:250]}"
    return proc_ok and preview_ok and access_ok, outbound_message_id, ack_err


def _send_generation_failure_customer_update(
    chat_id: str,
    project_id: str,
    request_text: str,
    gen_detail: str,
    *,
    proc_ok: bool,
    action_context: ActionExecutionContext,
) -> tuple[bool, str, str]:
    # PR-ζ.1b §13.E — caller threads the context (caller knows the flow:
    # intake-generation / revision-generation / manual-review-fallback).
    # The helper passes the SAME context to both internal acks so audit-row
    # attribution reflects the calling flow rather than the helper's
    # branch divergence.
    if actions.flyer_generation_queued_manual_review(gen_detail):
        return actions.send_flyer_manual_review_ack(
            chat_id,
            project_id,
            request_text,
            reason=gen_detail,
            action_context=action_context,
        )
    if proc_ok:
        # IN-3 (Flyer Studio E2E audit 2026-07-13): generation failed for an
        # UNCLASSIFIED reason (concurrency guard / unclassified crash) after the
        # customer already received a processing-ack. This branch used to return
        # silently, leaving the customer on "creating your flyer now" with no
        # closure — only an audit row + the reactive "any update?" path existed.
        # Send ONE plain-language closure so the customer knows the attempt
        # ended, mirroring the send_flyer_text mechanism the regeneration /
        # finalization failed-ack helpers use. gen_detail is deliberately NOT
        # surfaced — the copy stays customer-facing (no internal reason codes).
        return actions.send_flyer_text(
            chat_id,
            (
                "Flyer Studio\n"
                "------------\n"
                "Sorry — I couldn't finish your flyer just now. "
                "I'm looking into it and will follow up shortly."
            ),
            action_context=action_context,
        )
    return actions.send_flyer_intake_ack(
        chat_id, project_id, action_context=action_context,
    )


def _preview_may_have_delivered(outbound_message_id: str, ack_err: str) -> bool:
    err = (ack_err or "").lower()
    return bool(outbound_message_id) or "partial_delivery" in err or "send_uncertain" in err


def _send_flyer_regeneration_failed_ack(
    chat_id: str,
    project_id: str,
    *,
    action_context: ActionExecutionContext,
) -> tuple[bool, str, str]:
    # PR-ζ.1b §13.F — kwarg flip. Callers (2 invocation sites at 2921+3050)
    # pass for_command(PROJECT_ACTIONS, "generation.failed_ack",
    # is_regulated_action=False). Wrapper passes through to send_flyer_text.
    return actions.send_flyer_text(
        chat_id,
        (
            "Flyer Studio\n"
            "------------\n"
            "I could not finish the revised flyer automatically just now.\n\n"
            "I kept the edit request open instead of sending a mismatched flyer. "
            "Please check back here shortly, or send one exact correction if anything else must change."
        ),
        action_context=action_context,
    )


def _send_flyer_finalization_failed_ack(
    chat_id: str,
    project_id: str,
    *,
    action_context: ActionExecutionContext,
) -> tuple[bool, str, str]:
    # PR-ζ.1b §13.F — kwarg flip. Single caller (2969) passes
    # for_command(PROJECT_ACTIONS, "finalization.failed_ack",
    # is_regulated_action=False). Wrapper passes through.
    #
    # P0-4: this used to promise "I'll review it and send an update here" with
    # nothing queued, nothing scheduled and no timer that would ever produce
    # that message. The project stays in `finalizing_assets` — the only status
    # `send-flyer-package` accepts, and the one the retry gate keys on — so the
    # honest copy names the reply that genuinely re-enters that gate. "SEND IT"
    # is in the approval alias set; bare "SEND" is in neither alias set and
    # would be an instruction that does nothing.
    return actions.send_flyer_text(
        chat_id,
        (
            "Flyer Studio\n"
            "------------\n"
            "I could not finish preparing your final files just now, and nothing was sent.\n\n"
            "Your flyer is still here. Reply SEND IT when you want me to try again."
        ),
        action_context=action_context,
    )


def _send_flyer_final_delivery_failed_ack(
    chat_id: str,
    project_id: str,
    *,
    action_context: ActionExecutionContext,
) -> tuple[bool, str, str]:
    # P0-4, same reasoning as the finalization ack above. The one difference is
    # the truth: finalization failed BEFORE anything went out, this one failed
    # at the send itself, so only that clause differs.
    return actions.send_flyer_text(
        chat_id,
        (
            "Flyer Studio\n"
            "------------\n"
            "I could not send your final files just now.\n\n"
            "Your flyer is still here. Reply SEND IT when you want me to try again."
        ),
        action_context=action_context,
    )


def _try_flyer_account_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    if not actions.is_flyer_account_command(text):
        return None
    is_preference_command = actions.is_flyer_starter_prompt_preference_command(text)
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
    if customer is None:
        if not is_preference_command:
            return None
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id,
            "Flyer Studio\n------------\nI can change sample prompt settings after your Flyer Studio account is set up.",
            action_context=build_action_context(
                action_id="flyer.account.command_failed_fallback",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_account_customer_not_found",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"message_id={message_id}; sender_role={role}; "
                f"ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer account command customer not found"}
    ok, detail, result = actions.trigger_flyer_account_command(
        chat_id=chat_id,
        sender_phone=phone,
        sender_role=role,
        text=text,
    )
    if not ok or not result:
        actions.audit_intercepted(
            reason="flyer_account_failed",
            chat_id=chat_id,
            subprocess_rc=2,
            detail=f"message_id={message_id}; {detail[:400]}",
        )
        if not is_preference_command:
            return None
        actions.send_flyer_text(
            chat_id,
            "Flyer Studio\n------------\nI could not update that setting right now. Please try again.",
            action_context=build_action_context(
                action_id="flyer.account.command_failed_fallback",
                is_regulated_action=False,
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer account command failed"}
    if not result.get("handled"):
        if not is_preference_command:
            return None
        actions.audit_intercepted(
            reason="flyer_account_unhandled",
            chat_id=chat_id,
            subprocess_rc=2,
            detail=f"message_id={message_id}; detail={result.get('detail') or ''}",
        )
        actions.send_flyer_text(
            chat_id,
            "Flyer Studio\n------------\nI could not update that setting right now. Please try again.",
            action_context=build_action_context(
                action_id="flyer.account.command_failed_fallback",
                is_regulated_action=False,
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer account command failed"}
    # PR-ζ F8 2026-05-26 + PR-ζ.1b §6 2026-05-26: build the action_context for
    # whichever account-command branch fires. change_plan path → ACCOUNT_ACTIONS
    # ["change_plan"] (the only external_irreversible action in the portfolio).
    # Non-change_plan command_reply path → ACCOUNT_ACTIONS["command_reply"]
    # with is_regulated_action=False (dispatcher matrix already enforced the
    # regulated check; this is the reply-emission step). ζ.1b commit 10
    # removes "hooks.py" from SAFE_IO_NULL_CONTEXT_ALLOWLIST, so every send
    # path MUST carry an explicit context — no implicit fall-through allowed.
    detail = result.get("detail") or ""
    is_change_plan = "plan_change_requested" in detail
    # PR-ζ.1b 2026-05-26 §6 — replaces inline ActionExecutionContext with
    # registry helpers. change_plan path → ACCOUNT_ACTIONS["change_plan"]
    # (external_irreversible mutation_class carried via the registry entry).
    # Non-change_plan account commands → ACCOUNT_ACTIONS["command_reply"]
    # with is_regulated_action=False (dispatcher matrix already enforced the
    # regulated check; this is the reply-emission step). Required because
    # commit 10 removes "hooks.py" from SAFE_IO_NULL_CONTEXT_ALLOWLIST.
    if is_change_plan:
        action_ctx = build_action_context_for_command(ACCOUNT_ACTIONS, "change_plan")
    else:
        action_ctx = build_action_context_for_command(
            ACCOUNT_ACTIONS, "command_reply", is_regulated_action=False,
        )
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, result.get("reply_text") or "", action_context=action_ctx,
    )
    # PR-ζ BLOCKER #2 fix (security/money-flow reviewer): when the chokepoint
    # refuses on the change_plan path, the customer must NOT be left with
    # half-state (pending_plan_* fields persisted at account.py:301 BEFORE
    # the send, but no checkout URL delivered). Send an informational
    # fallback so the customer knows something went wrong + the operator
    # has been alerted. The fallback uses is_regulated_action=False because
    # it's a system meta-message about request state, not a claim about
    # the action's outcome.
    if not ack_ok and is_change_plan and err and "refused" in str(err).lower():
        # PR-ζ.1b §6 — registry helper replaces inline ctor.
        fallback_ctx = build_action_context_for_command(
            ACCOUNT_ACTIONS, "change_plan_fallback", is_regulated_action=False,
        )
        actions.send_flyer_text(
            chat_id,
            (
                "Flyer Studio\n------------\n"
                "We weren't able to set up your plan change right now. "
                "We've logged it for operator follow-up — please reply again "
                "in a few minutes or wait for an update here."
            ),
            action_context=fallback_ctx,
        )
    actions.audit_intercepted(
        reason="flyer_account_command",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"customer_id={result.get('customer_id') or ''}; status={result.get('status') or ''}; "
            f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    return {"action": "skip", "reason": "cf-router flyer account command"}


def _try_flyer_regulated_account_guard(text: str, chat_id: str, event: Any) -> Optional[dict]:
    """Fail closed for account/billing language before generic Hermes fallback."""
    if not actions.is_flyer_regulated_account_intent(text):
        return None
    # PR-α follow-up 2026-05-26: yield to _try_flyer_active_project_intercept
    # when (a) text is NOT a deterministic account command, (b) text targets a
    # flyer attribute/field via an edit verb, AND (c) sender has an active
    # flyer project. Without this yield, PR-α's extended regulated-account
    # regex would hijack legitimate flyer edits like "update this flyer,
    # change the phone number" into a fail-closed account warning. The
    # existing flyer_routing_decision_preview already routes such phrases to
    # revision when an active project exists; preserve that behavior here.
    # Pure account commands ("Upgrade to Growth", "UPDATE BUSINESS WHATSAPP")
    # never yield because is_flyer_account_command catches them earlier in
    # _try_flyer_account_intercept anyway; the early-return below is defensive.
    # ONE resolution, consumed by both the yield check below and the audited
    # sender_role further down. These were two identify-sender spawns five lines
    # apart, the first discarding its role — so a role flip between them was
    # invisible. Hoisting is spawn-neutral even before the turn memo, because
    # the second call ran unconditionally anyway.
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if not actions.is_flyer_account_command(text) and actions.flyer_text_targets_revision_field(text):
        # PR-α follow-up 2026-05-26 (LID-only blocker fix): do NOT gate on
        # phone_check truthiness before the lookup. LID-only customers
        # legitimately resolve to phone=None via identify-sender, and the
        # active-project store may still surface their project by chat_id /
        # primary_chat_id (see lessons.md line 92-95 on LID-only routing).
        # Today find_active_flyer_project_by_sender has its own phone-required
        # gate; passing phone=None through trusts that function to evolve
        # toward LID-only project lookup. Either way, this code never
        # ADDS a second phone gate.
        active_project = actions.find_active_flyer_project_by_sender(phone, chat_id)
        if active_project:
            return None
    message_id = _extract_message_id(event, chat_id, text)
    # sender_role census gap: a flyer CUSTOMER is not in roster/config, so
    # identify-sender returns role=unknown + phone=None for their LID chat.
    # Resolve the phone via the canonical identity key (lid-cache) so both the
    # customer lookup and the audited sender_role reflect a known customer
    # rather than the misleading `unknown`.
    lookup_phone = phone
    if not lookup_phone:
        canonical = actions.flyer_canonical_identity_key(chat_id)
        if canonical.startswith("+"):
            lookup_phone = canonical
    customer = actions.find_flyer_customer_by_sender(lookup_phone, chat_id)
    if customer is not None and role == "unknown":
        role = "customer"
    if customer is None:
        reply = (
            "Flyer Studio\n"
            "------------\n"
            "I can help with Flyer Studio account or billing requests after this number is set up.\n\n"
            "No account or payment change has been made."
        )
    else:
        business_name = str(customer.get("business_name") or "this business").strip() or "this business"
        reply = (
            "Flyer Studio\n"
            "------------\n"
            f"I understand this may be an account or billing request for {business_name}.\n\n"
            "No plan, payment, or account change has been made.\n\n"
            "To see plans, reply UPGRADE PLAN.\n"
            "To request a plan change, reply CHANGE PLAN STARTER, CHANGE PLAN GROWTH, or CHANGE PLAN UNLIMITED. "
            "I will ask for confirmation and payment before any plan changes."
        )
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply,
        action_context=build_action_context(
            action_id="flyer.account.regulated_account_guard",
            is_regulated_action=False,
        ),
    )
    actions.audit_intercepted(
        reason="flyer_regulated_account_guard",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"message_id={message_id}; customer_id={customer.get('customer_id') if customer else ''}; "
            f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    return {"action": "skip", "reason": "cf-router flyer regulated account guard"}


def _select_flyer_status_reply(project: dict) -> tuple[str, bool]:
    status = str(project.get("status") or "")
    manual_block = project.get("manual_review") or {}
    reason_code = manual_block.get("reason_code")
    is_exact_source_edit_status = (
        status == "manual_edit_required"
        and actions.is_source_edit_provider_unavailable_reason(reason_code)
    )
    if is_exact_source_edit_status:
        return actions.flyer_manual_edit_status_reply(project), True
    return actions.flyer_project_status_reply(project), False


def _resolve_status_project_for_reply(*, active_project: dict, body: str, phone: Optional[str], chat_id: str) -> tuple[dict, Optional[str]]:
    """Select status target row for customer status check replies."""
    mentioned_id = actions.extract_flyer_project_id_mention(body)
    if mentioned_id:
        named = actions.find_flyer_project_by_id_for_sender(phone, chat_id, mentioned_id)
        if named is not None:
            return named, mentioned_id
        return active_project, mentioned_id
    if str(active_project.get("status") or "") == "manual_edit_required":
        return active_project, None
    latest = actions.find_latest_flyer_project_for_status_by_sender(phone, chat_id)
    if latest is not None and str(latest.get("updated_at") or "") > str(active_project.get("updated_at") or ""):
        return latest, None
    return active_project, None


_CONCEPT_SELECTION_PHRASES = {
    "1": "C1", "option 1": "C1", "concept 1": "C1", "c1": "C1",
    "first": "C1", "first one": "C1", "first option": "C1", "first concept": "C1", "first design": "C1", "1st": "C1",
    "2": "C2", "option 2": "C2", "concept 2": "C2", "c2": "C2",
    "second": "C2", "second one": "C2", "second option": "C2", "second concept": "C2", "second design": "C2", "2nd": "C2",
    "3": "C3", "option 3": "C3", "concept 3": "C3", "c3": "C3",
    "third": "C3", "third one": "C3", "third option": "C3", "third concept": "C3", "third design": "C3", "3rd": "C3",
}
_CONCEPT_SELECTION_PATTERNS = (
    ("C1", re.compile(r"^(?:(?:i\s+)?(?:like|prefer|choose|select|pick|use|take|want)\s+|go\s+with\s+)?(?:the\s+)?(?:c\s*1|concept\s*1|option\s*1|1|first|1st)(?:\s+(?:one|option|concept|design))?(?:\s+(?:please|pls))?$", re.IGNORECASE)),
    ("C2", re.compile(r"^(?:(?:i\s+)?(?:like|prefer|choose|select|pick|use|take|want)\s+|go\s+with\s+)?(?:the\s+)?(?:c\s*2|concept\s*2|option\s*2|2|second|2nd)(?:\s+(?:one|option|concept|design))?(?:\s+(?:please|pls))?$", re.IGNORECASE)),
    ("C3", re.compile(r"^(?:(?:i\s+)?(?:like|prefer|choose|select|pick|use|take|want)\s+|go\s+with\s+)?(?:the\s+)?(?:c\s*3|concept\s*3|option\s*3|3|third|3rd)(?:\s+(?:one|option|concept|design))?(?:\s+(?:please|pls))?$", re.IGNORECASE)),
)
_CONCEPT_TITLE_SELECTION_PREFIX = re.compile(
    r"^(?:(?:i\s+)?(?:like|prefer|choose|select|pick|use|take|want)\s+|go\s+with\s+)(?:the\s+)?",
    re.IGNORECASE,
)
_CONCEPT_TITLE_SELECTION_SUFFIX = re.compile(r"\s+(?:please|pls)$", re.IGNORECASE)
_FLYER_ACTIVE_REVISION_HINT_PATTERN = re.compile(
    r"\b(?:make|resize|enlarge|shrink|smaller|bigger|larger|bold|brighter|darker|"
    r"change|edit|fix|correct|replace|remove|add|swap|update)\b",
    re.IGNORECASE,
)


def _concept_title_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def _concept_title_selection_key(normalized: str) -> str:
    target = _CONCEPT_TITLE_SELECTION_PREFIX.sub("", normalized, count=1).strip()
    target = _CONCEPT_TITLE_SELECTION_SUFFIX.sub("", target).strip()
    return _concept_title_key(target)


def _looks_like_active_flyer_revision(body: str) -> bool:
    normalized = " ".join(actions.flyer_visible_message_text(body).split())
    return (
        actions.is_flyer_revision_intent(normalized)
        or actions.flyer_text_targets_revision_field(normalized)
        or bool(_FLYER_ACTIVE_REVISION_HINT_PATTERN.search(normalized))
    )


def _resolve_flyer_concept_selection(body: str, project: dict) -> str:
    """Resolve bounded selection-only concept replies without using the LLM."""
    concepts = project.get("concepts") if isinstance(project.get("concepts"), list) else []
    concept_ids = {str(concept.get("concept_id") or "") for concept in concepts if isinstance(concept, dict)}
    if not concept_ids:
        return ""
    normalized = " ".join(actions.flyer_visible_message_text(body).split()).lower().strip(" .!,:;")
    candidates: set[str] = set()
    if normalized in _CONCEPT_SELECTION_PHRASES:
        candidates.add(_CONCEPT_SELECTION_PHRASES[normalized])
    for concept_id, pattern in _CONCEPT_SELECTION_PATTERNS:
        if pattern.search(normalized):
            candidates.add(concept_id)
    body_key = _concept_title_selection_key(normalized)
    if body_key:
        for concept in concepts:
            if not isinstance(concept, dict):
                continue
            title_key = _concept_title_key(str(concept.get("title") or ""))
            if title_key and title_key == body_key:
                candidates.add(str(concept.get("concept_id") or ""))
    candidates = {candidate for candidate in candidates if candidate in concept_ids}
    return next(iter(candidates)) if len(candidates) == 1 else ""


def _try_flyer_delivery_state_guard(text: str, chat_id: str, event: Any) -> Optional[dict]:
    """Fail closed for delivery-state language when no flyer project resolves.

    Runs AFTER `_try_flyer_active_project_intercept` in dispatch order. The
    active-project intercept already handles delivery-state phrases when an
    active project exists OR when a status-request surfaces a closed_no_send
    project. This guard catches the leftover case where no project resolves
    and the message would otherwise fall through to generic Hermes (which
    can claim "I sent your flyer" with no evidence).

    PR-β 2026-05-26 — sister to `_try_flyer_regulated_account_guard` (PR-α).
    No active-project yield needed because placement is AFTER the
    active-project intercept (which already handled active-project cases).
    Per the LID-only lesson (lessons.md line 92-95), do NOT add a phone-
    truthy gate before the customer lookup — the response copy works whether
    or not identify-sender resolves a phone.
    """
    if not actions.is_flyer_delivery_state_intent(text):
        return None
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
    # PR-β follow-up 2026-05-26 (delivered-project false-copy blocker fix):
    # surface the latest project's status BEFORE emitting the no-project
    # copy. The active-project intercept's status-surface branch only fires
    # for `is_flyer_project_status_request` matches; "did you send my flyer"
    # and "send my flyer" do NOT classify as status requests by that helper,
    # so a delivered project would otherwise reach this guard and trigger
    # the inaccurate "no active or recent flyer" reply. Use
    # `find_latest_flyer_project_for_status_by_sender` (includes delivered,
    # closed_no_send, and every non-completed status, max(updated_at)).
    # NO phone-truthy gate added — pass phone=None through (LID-only lesson).
    status_project = actions.find_latest_flyer_project_for_status_by_sender(phone, chat_id)
    if status_project is not None:
        sp_id = str(status_project.get("project_id") or "")
        sp_status = str(status_project.get("status") or "")
        status_reply, _is_exact_source_edit_status = _select_flyer_status_reply(status_project)
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, status_reply,
            action_context=build_action_context(
                action_id="flyer.project.status_surfaced",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_delivery_state_status_surfaced",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"message_id={message_id}; project_id={sp_id}; status={sp_status}; "
                f"customer_id={customer.get('customer_id') if customer else ''}; "
                f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer delivery state status surfaced"}
    if customer is None:
        reply = (
            "Flyer Studio\n"
            "------------\n"
            "I can help with flyer status or delivery after this number is set up.\n\n"
            "No delivery action has been taken."
        )
    else:
        business_name = str(customer.get("business_name") or "this business").strip() or "this business"
        reply = (
            "Flyer Studio\n"
            "------------\n"
            f"I don't see an active or recent flyer for {business_name} to deliver right now.\n\n"
            "No delivery action has been taken.\n\n"
            "To start a new flyer, reply with what it should promote."
        )
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply,
        action_context=build_action_context(
            action_id="flyer.delivery.delivery_state_guard",
            is_regulated_action=False,
        ),
    )
    actions.audit_intercepted(
        reason="flyer_delivery_state_guard",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"message_id={message_id}; customer_id={customer.get('customer_id') if customer else ''}; "
            f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    return {"action": "skip", "reason": "cf-router flyer delivery state guard"}


def _try_flyer_quote_echo_guard(text: str, chat_id: str, event: Any, media_path: Optional[str] = None) -> Optional[dict]:
    """Suppress flattened quote-echo bodies (F0211 class) before they become
    duplicate projects or bogus revision text.

    One legacy bridge shape flattens the QUOTED message text into the inbound
    body on swipe-reply. When the quoted text is a project brief, the echo
    re-enters intake / new-request routing and creates a duplicate project
    (or lands in the revision fallback as a nonsense instruction). Match is
    conservative — exact equality with a recent project's raw_request, or
    prefix when the brief is long (actions.find_flyer_quote_echo_project);
    genuinely new briefs never match and are unaffected. Text-only: media
    uploads are never quote echoes.
    """
    if media_path:
        return None
    body = " ".join(actions.flyer_visible_message_text(text).split())
    if not body:
        return None
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        return None
    echo_project = actions.find_flyer_quote_echo_project(phone, chat_id, body)
    if echo_project is None:
        return None
    project_id = str(echo_project.get("project_id") or "")
    status = str(echo_project.get("status") or "")
    message_id = _extract_message_id(event, chat_id, text)
    # Operator ruling 2026-07-05: an echo is ambiguity to RESOLVE, never noise
    # to drop — weekly-specials customers legitimately re-send the same brief.
    # The reply must be resolvable in one word: NEW creates a fresh project
    # from this same brief (pending state below + _try_flyer_quote_echo_choice);
    # APPROVE flows through normal approval routing on the existing project.
    #
    # 2026-07-12: do NOT prepend the echo project's status line
    # (`_select_flyer_status_reply`). For a delivered echo-project that line is
    # "The final flyer files have been delivered." — a PAST-tense delivery status
    # that a customer sending a NEW brief mis-reads as THIS turn's outcome (they
    # get told files "have been delivered" for a request that produced nothing).
    # The choice_line already names "your current flyer", so the banner + choice
    # is unambiguous and clearly refers to the EXISTING flyer without the prefix.
    if status in {"awaiting_final_approval", "revising_design", "delivered_with_warning"}:
        choice_line = (
            "This looks like the same request as your current flyer. "
            "Reply NEW to create a fresh flyer with these details, "
            "or reply APPROVE to receive the current flyer's final files."
        )
    else:
        choice_line = (
            "This looks like the same request as your current flyer. "
            "Reply NEW to create a fresh flyer with these details, "
            "or reply with any changes to the current one."
        )
    reply = f"Flyer Studio\n------------\n{choice_line}"
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply,
        action_context=build_action_context(
            action_id="flyer.project.quote_echo_clarification",
            is_regulated_action=False,
        ),
    )
    if ack_ok:
        try:
            actions.save_flyer_quote_echo_pending(
                chat_id=chat_id,
                original_text=body,
                message_id=message_id,
                project_id=project_id,
            )
        except Exception as exc:  # noqa: BLE001 - pending persist must not undo the sent reply
            err = f"{err}; pending_persist_failed: {type(exc).__name__}: {exc}"
    actions.audit_intercepted(
        reason="flyer_quote_echo_suppressed",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"project_id={project_id}; status={status}; quote_echo=true; "
            f"disambiguation_sent={'1' if ack_ok else '0'}; "
            f"body_len={len(body)}; sender_role={role}; "
            f"ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    return {"action": "skip", "reason": f"cf-router flyer quote echo suppressed for {project_id}"}


def _try_flyer_quote_echo_choice(
    text: str,
    chat_id: str,
    event: Any,
    *,
    flyer_generation_enabled: bool,
    flyer_workflow_enabled: bool,
) -> Optional[dict]:
    """Resolve a pending quote-echo NEW/APPROVE disambiguation.

    NEW replays the remembered echoed brief through the normal fresh-project
    path (`_try_flyer_primary_intercept(force_new=True)` — same reuse as the
    revenue-route clarification's flyer branch). APPROVE clears the pending
    state and returns None so the existing approval routing (including quoted
    binding) finalizes the existing project. Anything else leaves the pending
    state for its TTL and routes normally.
    """
    try:
        # M2 (PR #558 review): a live source-vs-new choice OUTRANKS the quote-
        # echo disambiguation — "NEW" must answer the question the customer
        # was asked most recently (the source/new prompt), not spawn a fresh
        # project from a stale echoed brief.
        if actions.has_awaiting_source_vs_new_choice(chat_id):
            return None
        pending = actions.get_flyer_quote_echo_pending(chat_id)
    except Exception as exc:  # noqa: BLE001 - pending state must not preempt core routing
        try:
            actions.audit_intercepted(
                reason="error",
                chat_id=chat_id,
                detail=f"flyer_quote_echo_pending_lookup_failed: {type(exc).__name__}: {exc}",
            )
        except Exception:
            pass
        return None
    if not pending:
        return None
    choice = actions.classify_flyer_quote_echo_choice(text)
    if choice is None:
        return None
    if choice == "approve":
        actions.pop_flyer_quote_echo_pending(chat_id)
        # M1 (PR #558 review): the disambiguation described the ECHO project's
        # status — the customer's APPROVE means THAT project, not whatever
        # newest-updated resolves to (they can differ with two open projects).
        # Stash the pending project id; resolve_flyer_binding_project prefers
        # it over the heuristic for this one inbound.
        pid = str(pending.get("project_id") or "").strip()
        if pid:
            try:
                actions.set_flyer_echo_approve_bind_hint(chat_id, pid)
            except Exception:  # noqa: BLE001 - hint is best-effort; fallback = old behavior
                pass
        return None
    pending = actions.pop_flyer_quote_echo_pending(chat_id) or pending
    original_text = str(pending.get("original_text") or "").strip()
    if not original_text:
        # grad9 LOW: the customer confirmed NEW but the popped echo row carried no
        # usable original brief (empty/whitespace) — audit the skip so it is not a
        # silent drop, then fall through to core routing.
        try:
            actions.audit_intercepted(
                reason="flyer_quote_echo_suppressed",
                chat_id=chat_id,
                detail=(
                    f"new_confirmed_empty_original; prior_project_id={pending.get('project_id') or ''}"
                ),
            )
        except Exception:  # noqa: BLE001 - audit is best-effort, must not preempt routing
            pass
        return None
    original_message_id = str(pending.get("message_id") or "").strip()
    if not original_message_id:
        original_message_id = _extract_message_id(event, chat_id, original_text)
    actions.audit_intercepted(
        reason="flyer_quote_echo_new_confirmed",
        chat_id=chat_id,
        detail=(
            f"prior_project_id={pending.get('project_id') or ''}; "
            f"original_message_id={original_message_id}"
        ),
    )
    original_event = _clarified_text_event(
        text=original_text,
        chat_id=chat_id,
        message_id=original_message_id,
    )
    if flyer_generation_enabled:
        return _try_flyer_primary_intercept(original_text, chat_id, original_event, force_new=True)
    if flyer_workflow_enabled:
        actions.spawn_bare_flyer_render_and_send(
            chat_id, original_text, message_id=original_message_id,
        )
        return {"action": "skip", "reason": "cf-router bare flyer dispatched"}
    return None


def _try_flyer_campaign_cta_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    """Route campaign button replies before project/revision intake."""
    source = actions.flyer_campaign_source(text)
    if not source:
        return None
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        return None
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
    if customer and customer.get("status") in {"active", "trial"}:
        if source == "start_trial":
            return _send_flyer_active_customer_trial_link_recovery(chat_id, customer, role=role)
        return _send_flyer_active_customer_ready(chat_id, customer, role=role)
    if customer:
        reply = actions.flyer_customer_not_active_reply(customer)
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, reply,
            action_context=build_action_context(
                action_id="flyer.account.customer_not_active",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_customer_not_active",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"customer_id={customer.get('customer_id') or ''}; status={customer.get('status') or ''}; "
                f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer customer not active"}
    return _start_flyer_intake(text, chat_id, event, source=source, original_text=text)


def _start_flyer_intake(
    text: str,
    chat_id: str,
    event: Any,
    *,
    source: str,
    original_text: str = "",
    media_path: Optional[str] = None,
) -> Optional[dict]:
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        return None

    ok, detail, result = actions.trigger_flyer_intake(
        chat_id=chat_id,
        sender_phone=phone,
        message_id=message_id,
        text=text,
        media_path=media_path or "",
        start_source=source,
        original_text=original_text or text,
    )
    if not ok or not result:
        actions.audit_intercepted(
            reason="flyer_intake_failed", chat_id=chat_id,
            subprocess_rc=2, detail=detail[:500],
        )
        actions.send_flyer_text(
            chat_id,
            "Flyer Studio\n------------\nI could not start the flyer setup cleanly. Please reply START FREE TRIAL again, or send your flyer request here.",
            action_context=build_action_context(
                action_id="flyer.project.intake_failed_fallback",
                is_regulated_action=False,
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer intake failed"}
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, result.get("reply_text") or "",
        action_context=build_action_context_for_command(
            PROJECT_ACTIONS, "intake.acknowledged",
        ),
    )
    actions.audit_intercepted(
        reason="flyer_intake_started",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"message_id={message_id}; source={source}; action={result.get('action') or ''}; "
            f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    return {"action": "skip", "reason": f"cf-router flyer intake started: {source}"}


def _try_flyer_intake_intercept(
    text: str,
    chat_id: str,
    event: Any,
    media_path: Optional[str] = None,
) -> Optional[dict]:
    """Advance language/mode/guided intake before normal Flyer routing."""
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        return None
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
    intake_session = actions.find_flyer_intake_session_by_sender(phone, chat_id)
    protected_statuses = {
        "choosing_sample_idea",
        "text_awaiting_brief",
        "guided_collecting_goal",
        "guided_collecting_schedule",
        "guided_collecting_items",
        "guided_collecting_location",
        "guided_collecting_assets",
        "brief_pending_approval",
    }
    # 2026-05-28 — intake bypass via the named helper (Commit 3 wiring).
    # Replaces the inline conditional that was here. Helper composes the
    # same 3 deployed classifiers (classify_flyer_intent,
    # is_exact_reference_edit_request, should_start_new_flyer_over_active)
    # plus the account-lifecycle precondition + protected-status guard.
    # Returns the bypass_reason Literal value (or None for "no bypass").
    bypass_reason = actions.should_bypass_intake_for_clear_intent(
        text=text,
        customer=customer,
        intake_session=intake_session,
        has_media=bool(media_path),
    )
    if bypass_reason is not None:
        inbound_script = actions._detect_inbound_script(text)
        customer_state = str((customer or {}).get("status") or "")
        intake_status = str((intake_session or {}).get("status") or "")
        # Decision-time structured audit row.
        actions.audit_flyer_intake_bypassed(
            chat_id=chat_id,
            bypass_reason=bypass_reason,
            has_media=bool(media_path),
            customer_state=customer_state,
            intake_session_status=intake_status,
            inbound_script=inbound_script,
        )
        # Existing-style cf_router_intercepted audit row for grepability.
        try:
            actions.audit_intercepted(
                reason="flyer_intake_bypassed",
                chat_id=chat_id,
                subprocess_rc=0,
                detail=(
                    f"bypass_reason={bypass_reason}; has_media={'1' if media_path else '0'}; "
                    f"customer_state={customer_state}; intake_status={intake_status}; "
                    f"inbound_script={inbound_script}; sender_role={role}"
                )[:500],
            )
        except Exception:
            # Audit emit must not block the bypass path.
            pass
        # Open the bypass shadow — dispatch wrapper's finally emits the
        # outcome row + resets the token.
        actions.note_flyer_intake_bypass_active(
            chat_id=chat_id,
            message_id=message_id,
            bypass_reason=bypass_reason,
            has_media=bool(media_path),
            customer_state=customer_state,
            intake_session_status=intake_status,
            inbound_script=inbound_script,
        )
        # Close-on-handoff (P0-2b): the bypass hands this customer to the
        # project-create flow, so a lingering intake session MUST be discarded
        # here — otherwise a later revision reply is hijacked back into "choose a
        # creation mode" (the 2026-06-02 stale-intake-session hijack). Only
        # NON-protected statuses (choosing_language/choosing_mode) reach this
        # branch; protected in-progress statuses block the bypass upstream
        # (precondition 1 of should_bypass_intake_for_clear_intent).
        if intake_session and actions.discard_flyer_intake_session_by_sender(phone, chat_id):
            actions.audit_intercepted(
                reason="flyer_intake_session_closed_on_handoff",
                chat_id=chat_id,
                subprocess_rc=0,
                detail=(
                    f"message_id={message_id}; bypass_reason={bypass_reason}; "
                    f"intake_status={intake_status}; sender_role={role}"
                )[:500],
            )
        return None
    if not intake_session:
        return None
    ok, detail, result = actions.trigger_flyer_intake(
        chat_id=chat_id,
        sender_phone=phone,
        message_id=message_id,
        text=text,
        media_path=media_path or "",
    )
    if not ok or not result:
        actions.audit_intercepted(
            reason="flyer_intake_failed", chat_id=chat_id,
            subprocess_rc=2, detail=detail[:500],
        )
        actions.send_flyer_text(
            chat_id,
            "Flyer Studio\n------------\nI could not continue the flyer setup cleanly. Please reply START FREE TRIAL again, or send your flyer request here.",
            action_context=build_action_context(
                action_id="flyer.project.intake_failed_fallback",
                is_regulated_action=False,
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer intake failed"}
    action = str(result.get("action") or "")
    if action == "create_project":
        raw_request = str(result.get("raw_request") or "").strip()
        reference_media_path = str(result.get("reference_media_path") or "").strip()
        if raw_request:
            actions.audit_intercepted(
                reason="flyer_brief_approved",
                chat_id=chat_id,
                subprocess_rc=0,
                detail=(
                    f"message_id={message_id}; source={result.get('brief_source') or ''}; "
                    f"approved_message_id={result.get('brief_approved_message_id') or ''}; "
                    f"approved_at={result.get('brief_approved_at') or ''}; sender_role={role}"
                ),
            )
            project_result = None
            if not actions.is_flyer_enabled() and not (reference_media_path or media_path):
                spawn_ok = actions.spawn_bare_flyer_render_and_send(
                    chat_id, raw_request, message_id=message_id,
                )
                actions.audit_intercepted(
                    reason=(
                        "flyer_bare_brief_generation_dispatched"
                        if spawn_ok
                        else "flyer_bare_brief_generation_failed"
                    ),
                    chat_id=chat_id,
                    subprocess_rc=0 if spawn_ok else 3,
                    detail=(
                        f"message_id={message_id}; source={result.get('brief_source') or ''}; "
                        f"approved_message_id={result.get('brief_approved_message_id') or ''}; "
                        f"approved_at={result.get('brief_approved_at') or ''}; sender_role={role}"
                    ),
                )
                if spawn_ok:
                    if not actions.discard_flyer_intake_session_by_sender(phone, chat_id):
                        actions.audit_intercepted(
                            reason="flyer_intake_cleanup_failed",
                            chat_id=chat_id,
                            subprocess_rc=3,
                            detail=(
                                f"message_id={message_id}; source={result.get('brief_source') or ''}; "
                                f"approved_message_id={result.get('brief_approved_message_id') or ''}; sender_role={role}"
                            ),
                        )
                    return {"action": "skip", "reason": "cf-router bare flyer dispatched from approved brief"}
            else:
                project_result = _try_flyer_primary_intercept(
                    raw_request,
                    chat_id,
                    event,
                    force_new=True,
                    media_path=reference_media_path or media_path,
                    brief_audit_detail=(
                        f"brief_source={result.get('brief_source') or ''}; "
                        f"brief_approved_message_id={result.get('brief_approved_message_id') or ''}; "
                        f"brief_approved_at={result.get('brief_approved_at') or ''}"
                    ),
                )
            if project_result is not None:
                if not actions.discard_flyer_intake_session_by_sender(phone, chat_id):
                    actions.audit_intercepted(
                        reason="flyer_intake_cleanup_failed",
                        chat_id=chat_id,
                        subprocess_rc=3,
                        detail=(
                            f"message_id={message_id}; source={result.get('brief_source') or ''}; "
                            f"approved_message_id={result.get('brief_approved_message_id') or ''}; sender_role={role}"
                        ),
                    )
                return project_result
            reply = (
                "Flyer Studio\n"
                "------------\n"
                "I could not start generation cleanly, but your flyer brief is still saved. "
                "Reply APPROVE to try again, or send changes to update it."
            )
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id, reply,
                action_context=build_action_context(
                    action_id="flyer.project.brief_saved_generation_failed_fallback",
                    is_regulated_action=False,
                ),
            )
            actions.audit_intercepted(
                reason="flyer_brief_project_create_failed",
                chat_id=chat_id,
                subprocess_rc=0 if ack_ok else 3,
                detail=(
                    f"message_id={message_id}; source={result.get('brief_source') or ''}; "
                    f"approved_message_id={result.get('brief_approved_message_id') or ''}; "
                    f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
                ),
            )
            return {"action": "skip", "reason": "cf-router flyer brief project creation failed"}
    if action == "start_guest_order":
        if not phone:
            reply = (
                "Flyer Studio\n"
                "------------\n"
                "I could not verify the WhatsApp phone number for this one-time flyer order.\n\n"
                "Please message from the phone number you want to use for the order, or tap Start Free Trial to set up your business account first."
            )
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id, reply,
                action_context=build_action_context(
                    action_id="flyer.guest_order.phone_required_fallback",
                    is_regulated_action=False,
                ),
            )
            actions.audit_intercepted(
                reason="flyer_guest_order_failed",
                chat_id=chat_id,
                subprocess_rc=0 if ack_ok else 3,
                detail=(
                    f"message_id={message_id}; detail=sender_phone_required; sender_role={role}; "
                    f"ack_message_id={mid}; ack_error={err[:300]}"
                ),
            )
            return {"action": "skip", "reason": "cf-router flyer intake: quick_flyer_phone_required"}
        ok_order, detail_order, order_result = actions.trigger_start_flyer_guest_order(
            sender_phone=phone,
            chat_id=chat_id,
            message_id=message_id,
        )
        if not ok_order or not order_result:
            actions.audit_intercepted(
                reason="flyer_guest_order_failed", chat_id=chat_id,
                subprocess_rc=2, detail=detail_order[:500],
            )
            return None
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, order_result.get("reply_text") or "",
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "guest_order.intake_acknowledged",
            ),
        )
        actions.audit_intercepted(
            reason="flyer_guest_order_started",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"message_id={message_id}; order_id={order_result.get('order_id') or ''}; "
                f"status={order_result.get('status') or ''}; sender_role={role}; "
                f"ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer intake: quick_flyer_payment"}

    reply = str(result.get("reply_text") or "")
    if not reply:
        return {"action": "skip", "reason": f"cf-router flyer intake: {action}"}
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply,
        action_context=build_action_context_for_command(
            PROJECT_ACTIONS, "intake.processing",
        ),
    )
    _release_starter_claim_on_hard_send_failure(
        reply,
        str(result.get("customer_id") or ""),
        ack_ok=ack_ok,
        outbound_message_id=mid,
    )
    actions.audit_intercepted(
        reason="flyer_intake",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"message_id={message_id}; action={action}; source={result.get('source') or ''}; "
            f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    return {"action": "skip", "reason": f"cf-router flyer intake: {action}"}


def _flyer_raw_request_with_reference(text: str, media_path: Optional[str]) -> str:
    body = " ".join((text or "").split())
    if not media_path:
        return body
    if actions.classify_flyer_intent(body)[0]:
        return f"{body}\nUploaded reference image/template is attached. Use it when designing this flyer."
    return f"Create flyer from uploaded template/reference. Customer requested: {body}"


def _flyer_customer_sender_phone(customer: Optional[dict]) -> str:
    if not customer:
        return ""
    for key in ("business_whatsapp_number", "onboarded_by_phone", "public_phone"):
        value = str(customer.get(key) or "").strip()
        if value:
            return value
    for value in customer.get("authorized_request_numbers") or []:
        value = str(value or "").strip()
        if value:
            return value
    return ""


def _send_flyer_active_customer_ready(chat_id: str, customer: dict, *, role: str = "") -> dict:
    business_name = str(customer.get("business_name") or "this business")
    reply = (
        "Flyer Studio\n"
        "------------\n"
        f"This number is already set up for {business_name}.\n\n"
        "Send your flyer request in one message, or attach an existing flyer, logo, menu, photos, or reference image."
    )
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply,
        action_context=build_action_context(
            action_id="flyer.account.active_customer_ready",
            is_regulated_action=False,
        ),
    )
    actions.audit_intercepted(
        reason="flyer_onboarding",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"status={customer.get('status') or ''}; customer_id={customer.get('customer_id') or ''}; "
            f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    return {"action": "skip", "reason": "cf-router flyer active customer ready"}


def _send_flyer_active_customer_trial_link_recovery(chat_id: str, customer: dict, *, role: str = "") -> dict:
    business_name = str(customer.get("business_name") or "this business")
    status = str(customer.get("status") or "trial").lower()
    plan_label = "Free plan" if status == "trial" else "active plan"
    reply = (
        "Flyer Studio\n"
        "------------\n"
        f"You're already on the {plan_label} for {business_name}.\n\n"
        "Create another flyer by sending the full request here, or reply UPGRADE PLAN to see paid plans."
    )
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply,
        action_context=build_action_context(
            action_id="flyer.account.trial_link_recovery",
            is_regulated_action=False,
        ),
    )
    actions.audit_intercepted(
        reason="flyer_trial_link_recovery",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"status={customer.get('status') or ''}; customer_id={customer.get('customer_id') or ''}; "
            f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    return {"action": "skip", "reason": "cf-router flyer active customer trial link recovery"}


def _try_flyer_onboarding_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    """Start or advance WhatsApp-native customer onboarding for new senders."""
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        return None
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
    if customer and customer.get("status") in {"active", "trial"}:
        return None

    ok, detail, result = actions.trigger_flyer_onboarding(
        chat_id=chat_id,
        sender_phone=phone,
        message_id=message_id,
        text=text,
    )
    if not ok or not result:
        actions.audit_intercepted(
            reason="flyer_onboarding_failed", chat_id=chat_id,
            subprocess_rc=2, detail=detail[:500],
        )
        actions.send_flyer_text(
            chat_id,
            "Flyer Studio\n------------\nI could not continue account setup cleanly. Please reply START FREE TRIAL again, or send your flyer request here.",
            action_context=build_action_context(
                action_id="flyer.account.onboarding_failed_fallback",
                is_regulated_action=False,
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer onboarding failed"}
    if not result.get("handled"):
        return None
    trailing_request = actions.extract_flyer_request_after_confirm(text)
    will_route_trailing = (
        result.get("next_status") in {"trial", "active"}
        and trailing_request
        and actions.should_start_new_flyer_over_active(trailing_request, has_media=False)
    )
    reply_text = result.get("reply_text") or ""
    if will_route_trailing:
        reply_text = _suppress_flyer_starter_brief(reply_text)
    # PR-ζ.1b §13.A — onboarding-progress reply via ACCOUNT_ACTIONS entry.
    # Account setup state lives in ACCOUNT_ACTIONS not a flat ad-hoc context.
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply_text,
        action_context=build_action_context_for_command(
            ACCOUNT_ACTIONS, "onboarding_progress",
        ),
    )
    _release_starter_claim_on_hard_send_failure(
        reply_text,
        str(result.get("customer_id") or ""),
        ack_ok=ack_ok,
        outbound_message_id=mid,
    )
    actions.audit_intercepted(
        reason="flyer_onboarding", chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"status={result.get('next_status')}; customer_id={result.get('customer_id') or ''}; "
            f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    if will_route_trailing:
        project_result = _try_flyer_primary_intercept(
            trailing_request, chat_id, event, force_new=True,
        )
        if project_result is not None:
            return project_result
    return {"action": "skip",
            "reason": f"cf-router flyer onboarding: {result.get('next_status')}"}


def _release_starter_claim_on_hard_send_failure(
    reply_text: str,
    customer_id: str,
    *,
    ack_ok: bool,
    outbound_message_id: str,
) -> None:
    if ack_ok or outbound_message_id or not customer_id:
        return
    if actions.flyer_starter_brief_marker() not in (reply_text or ""):
        return
    actions.release_flyer_starter_prompt_claim(customer_id)


def _suppress_flyer_starter_brief(reply_text: str) -> str:
    marker = actions.flyer_starter_brief_marker()
    if marker not in (reply_text or ""):
        return reply_text
    before, _sep, _after = reply_text.partition(marker)
    cleaned = before.rstrip()
    if not cleaned:
        cleaned = "Flyer Studio\n------------\nYour Flyer Studio account is ready."
    return f"{cleaned}\n\nI will create the flyer request you included now."


def _try_flyer_existing_onboarding_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    """Continue pending Flyer onboarding for arbitrary field replies."""
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        return None
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
    session = actions.find_flyer_onboarding_session_by_sender(phone, chat_id)
    if customer and customer.get("status") in {"active", "trial"}:
        if not session:
            return None
        if (
            actions.classify_flyer_intent(text)[0]
            or actions.should_start_new_flyer_over_active(text, has_media=False)
            or actions.is_flyer_campaign_cta(text)
        ):
            return None
        return _send_flyer_active_customer_ready(chat_id, customer, role=role)
    if not session:
        return None
    return _try_flyer_onboarding_intercept(text, chat_id, event)


# The deterministic sufficiency evidence: an explicit brand-artifact NOUN in the
# visible caption. Context-dependent words are NOT evidence on their own — the
# six-substring gate this replaces read "replace chicken with paneer", "sample
# menu" and "brand new menu" as brand assets — so `sample` / `reference` / `old`
# / `previous` qualify only when bound to a brand-artifact noun, and `brand`
# only in a brand-artifact compound ("brand kit", "brand colors"), never bare.
#
# Durable styling instructions are handled separately, by
# `_durable_style_donation` — see there for why they cannot live as another
# alternative in this regex.
_BRAND_ASSET_EVIDENCE = re.compile(
    r"\b(?:logos?|letterheads?|watermarks?|branding)\b"
    r"|\bbrand\s*(?:kit|mark|book|guide|guidelines|colou?rs?|fonts?|assets?|identity)\b"
    r"|\btemplates?\b"
    r"|\b(?:sample|reference|old|previous|existing|past)\s+(?:\w+\s+){0,2}"
    r"(?:flyer|flier|poster|banner|design|artwork|creative|template)s?\b",
    re.IGNORECASE,
)

# A durable styling instruction — what the production brand-asset store is
# mostly made of (B0004, B0006, B0008, the last being the only asset active on
# the box today). It names no artifact noun, so the rule above cannot see it.
#
# BOTH halves are required, and the second is the one that carries the weight.
# Durability alone says a preference is permanent; it does not say the attached
# image IS that preference. A donation POINTS AT the attachment — "use THIS
# theme", "the SAME theme", "follow theme LIKE THIS" — whereas a directive
# describes a property to apply ("always use a bigger font") and a complaint
# describes a defect ("our fonts always look bad"). Neither donates anything,
# and capturing them writes a durable template that `render.py` reads on every
# later render.
#
# The deictic must MODIFY the styling noun, not merely appear in the sentence:
# "in future the design should not be this dark" contains "this" as an
# intensifier and donates nothing, so a free-floating deictic would readmit it.
_DURABLE_SCOPE = re.compile(
    r"\b(?:going\s+forward|from\s+now\s+on|from\s+here\s+on|in\s+(?:the\s+)?future|always)\b",
    re.IGNORECASE,
)
_STYLE_NOUN = r"(?:theme|style|look|design|colou?rs?|fonts?|palette|branding)"
_STYLE_NOUN_RE = re.compile(r"\b" + _STYLE_NOUN + r"\b", re.IGNORECASE)
# Four words of slack before the noun: "the same warm earthy south indian
# palette" is ordinary phrasing, and B0004 survived the previous two-word
# window only by saying "the same theme" with nothing in between. Widening is
# safe in both directions — every known complaint in this class binds its
# deictic at zero words ("this design", "these colours"), so a wider window
# admits no complaint that a narrow one excluded.
_ATTACHED_STYLE_REFERENCE = re.compile(
    r"\b(?:this|these|the\s+same|attached|uploaded)\s+(?:\w+\s+){0,4}" + _STYLE_NOUN + r"\b"
    r"|\b" + _STYLE_NOUN + r"\b\s*(?:\w+\s+){0,4}"
    r"(?:like\s+(?:this|these)|of\s+this|from\s+this|in\s+this)\b",
    re.IGNORECASE,
)


def _durable_style_signal(text: str) -> bool:
    """The near-miss shape: a durable styling caption, before it has qualified.

    Used only to make the decline COUNTABLE. Without it this class declines as
    plain `no_brand_asset_evidence` — silent and uncountable, which is the
    property that made the first false-negative class expensive to find.
    """
    body = text or ""
    return bool(_DURABLE_SCOPE.search(body)) and bool(_STYLE_NOUN_RE.search(body))


def _durable_style_donation(text: str) -> bool:
    """Is this a durable styling instruction that DONATES the attached art?

    THREE conditions, and the third is the one that makes the class safe.

    This rule is the only place in the gate that ever authorized on the absence
    of a complaint rather than the presence of an offer, and that inversion is
    why the class regrew in a new shape on each of three review passes. A
    complaint points AT the attachment as readily as a donation does ("this
    design always comes out too dark"), so the deictic discriminates nothing on
    its own, and no defect-frame vocabulary is ever finished — "comes out",
    "ends up", "print badly", "renders poorly", "needs fixing" were all missed.
    Requiring an affirmative offer puts this class back on the closable side
    that the rest of the gate is built on.

    Kept out of `_BRAND_ASSET_EVIDENCE` deliberately: the temporal half used to
    be an alternative there while the same five phrases also sat in
    `_BRAND_ASSET_OFFER`, which made the pair mutually self-satisfying and the
    defect frame structurally unreachable for the class.
    """
    body = text or ""
    if not _DURABLE_SCOPE.search(body) or not _ATTACHED_STYLE_REFERENCE.search(body):
        return False
    if _BACKWARD_REFERENCE.search(body):
        return False
    return bool(_BRAND_ASSET_OFFER.search(body)) and not _BRAND_ASSET_NEGATED_OFFER.search(body)

# Genuine menu/price semantics — menu-ish media goes to normal routing, never to
# the brand store. Deliberately NOT gated on the classifier's role, for two
# reasons pulling in opposite directions:
#
#   * `classify_reference_role`'s `menu_reference` branch
#     (`reference_extract.py:135`) fires on the SINGULAR noun "flyer" with no
#     menu or price word anywhere, so "this is the flyer template we use"
#     classifies as `menu_reference`. Vetoing on that role ALONE dropped six
#     realistic template uploads.
#   * conversely the role misses menu media that names no deictic, so "our menu
#     template" and "price list template" classified as `inspiration` and rode
#     the bare `\btemplates?\b` alternative straight into the store.
#
# Keying the veto on the menu/price WORDS rather than on the role fixes both.
_MENU_PRICE_SEMANTICS = re.compile(
    r"\b(?:menus?|price\s*lists?|prices?|pricing|items?)\b",
    re.IGNORECASE,
)

# Naming a brand artifact is not offering one. "remove the watermark" and "the
# existing flyer has a typo" name one and complain about it; capturing those
# recreates the B0009 shape, and the result is not inert — `_brand_asset_kind`
# (`onboarding.py:1155`) files anything containing "flyer" as a `template`, and
# an active template steers every later render.
#
# Complaint phrasing is open-ended, so this is not an attempt to enumerate it.
# It only has to be good enough to catch captions that ALSO carry no evidence of
# offering — see `_BRAND_ASSET_OFFER`, which is the closable set and which wins
# whenever both match. That is what lets B0008's real caption ("change theme of
# my fliers going forward") keep its edit verb and still be captured.
#
# The last alternative covers absence rather than defect — "this flyer has no
# logo" reports that the artifact is MISSING from the attachment, which is the
# opposite of donating one.
_BRAND_ASSET_DEFECT_FRAME = re.compile(
    r"\b(?:remove|delete|erase|get\s+rid\s+of|take\s+(?:it\s+)?off|fix|correct|redo|"
    r"crop|resize|change|replace|update|edit|modify|revise)\b"
    r"|\b(?:is|are|was|were|looks?|looked|seems?|seemed|has|have|had|came\s+out)\b"
    r"[^.!?]{0,40}\b(?:too\s+\w+|wrong|bad|blurry|dark|light|small|tiny|big|huge|"
    r"typo|error|mistake|missing|cut\s+off|incorrect|outdated|ugly|misspel)\w*"
    r"|\b(?:no|without|missing|lacks?|lacking)\s+(?:\w+\s+){0,2}"
    r"(?:logos?|watermarks?|branding|templates?|flyers?|fliers?|posters?)\b",
    re.IGNORECASE,
)

# Affirmative evidence that the customer is HANDING OVER the attached media
# rather than talking about it. Small and closable by design, which is why the
# gate is built on this side rather than on the complaint side.
#
# The five temporal phrases that used to sit here are GONE. They made this
# regex and the durability rule mutually self-satisfying (see
# `_durable_style_donation`), and they rescued nothing: all three production
# captions carry a non-temporal signal anyway — B0004 and B0006 match `use`,
# B0008 matches `want you to`.
#
# The verb list is a NAMED TUPLE OF ITS OWN because two regexes need to agree on
# it, and they silently stopped agreeing: `follow` was added here and not to the
# negation side, so "do not follow this theme going forward" read as a donation.
# That is not the open-ended-language problem — offer verbs are a closed set, so
# a verb present on one side and missing on the other is a parity error between
# two enumerable lists. `_BRAND_ASSET_NEGATED_OFFER` is now BUILT from this
# tuple, which makes the drift impossible rather than merely detectable, and
# `test_every_offer_verb_has_a_negated_form` fails if a future edit hardcodes a
# verb into either regex instead of adding it here.
_OFFER_VERBS = ("use", "save", "keep", "store", "apply", "follow")

# Not offer cues on their own, but refusing them still says "do not put this on
# the flyer", so the negation side carries them too.
_NEGATION_ONLY_VERBS = ("add", "put", "include")

# Offer verbs that deliberately need no negated form. Empty on purpose — the
# parity test refuses to pass over an entry here without a stated reason.
_OFFER_VERBS_EXEMPT_FROM_NEGATION: tuple[str, ...] = ()

# The last alternative is the "supplied artifact" shape: in "replace this with
# our logo" the edit verb acts ON something else and the artifact is what is
# being HANDED OVER, so the caption is a donation despite reading as an edit.
#
# A possessive alone does NOT make it one — possessives appear in complaints
# just as freely ("the flyer with our logo is wrong", "the banner with our
# watermark came out blurry"), and reading those as offers skipped the defect
# gate entirely. So the alternative is bound to a DONATING CONTEXT: a
# substitution verb acting on a deictic object. "replace THIS with our logo"
# donates; "replace the date with our template date" substitutes one field for
# another and donates nothing.
_BRAND_ASSET_OFFER = re.compile(
    r"\b(?:here\s+is|here's|here\s+are|this\s+is|these\s+are|attached\s+is|attaching|"
    r"i(?:'m|\s+am)\s+(?:sending|attaching|uploading)|want\s+you\s+to|"
    r"(?:please\s+)?(?:" + "|".join(_OFFER_VERBS) + r"))\b"
    r"|\b(?:replace|swap|change|update)\s+(?:this|that|these|those|it)\b[^.!?]{0,20}"
    r"\b(?:with|to)\s+(?:our|my|the\s+attached)\s+(?:\w+\s+){0,2}"
    r"(?:logos?|watermarks?|branding|templates?|letterheads?|designs?|artworks?)\b",
    re.IGNORECASE,
)

# "do not use our logo on this" contains `use`, so the offer test matches a verb
# the sentence is actually refusing. Python has no variable-length lookbehind,
# so the negation is detected separately and both suppresses the offer and
# counts as a defect frame in its own right.
_BRAND_ASSET_NEGATED_OFFER = re.compile(
    r"\b(?:do\s+not|don'?t|never|no\s+need\s+to)\s+(?:\w+\s+){0,2}"
    r"(?:" + "|".join(_OFFER_VERBS + _NEGATION_ONLY_VERBS) + r")\b",
    re.IGNORECASE,
)

# "follow the same style AS BEFORE" points at prior work, not at the attachment.
# `follow` takes a backward-referring object far more naturally than the other
# offer verbs, so the verb this class leans on is also the one that can aim its
# deictic away from the media actually attached. Deliberately narrow: "like
# this" points AT the media and must keep working — it is how B0008 donates.
_BACKWARD_REFERENCE = re.compile(
    r"\b(?:as|like)\s+(?:before|last\s+time|previously|usual|always)\b"
    r"|\bsame\s+as\s+(?:before|last\s+time|always)\b",
    re.IGNORECASE,
)


def _classify_flyer_reference_role(text: str, media_path: str) -> str:
    """What does the deployed Flyer classifier say the attached media is?

    `reference_extract.classify_reference_role` is the repo's ONE answer to this
    question — `create-flyer-project` has asked it since the source-contract work
    — and until now it had no cf-router call site at all, which is how the
    brand-asset arm ended up with a parallel six-substring rule of its own.

    Pure and local: regex over the caption plus the media's MIME type. No
    provider call, no network, no model — so it is viable on the inbound
    `pre_gateway_dispatch` path, which a vision call would not be (the reference
    vision provider carries a 60s timeout and a per-call cost, and goes dark
    whenever OpenRouter does).

    The classifier reads exactly one attribute off the asset, so it is handed a
    MIME-only stand-in rather than a real `FlyerAsset`: building one would mean
    sha256-ing the media on the inbound path for a value nothing here reads.

    Raises on import or classification failure — the caller fails closed.
    """
    actions._ensure_src_path()  # type: ignore[attr-defined]
    try:
        from agents.flyer.reference_extract import classify_reference_role  # type: ignore
    except ImportError:  # pragma: no cover - deployed flat-module fallback
        from flyer_reference_extract import classify_reference_role  # type: ignore
    mime = mimetypes.guess_type(media_path or "")[0] or ""
    return classify_reference_role(text or "", SimpleNamespace(mime_type=mime))


def _flyer_brand_asset_authorized(text: str, media_path: str) -> tuple[bool, str]:
    """May this inbound MUTATE the customer's brand-asset store?

    Model/classifier judgment decides what the media represents; this function
    decides whether that judgment is sufficient to write. The fixed ruling it
    encodes: an active Flyer project is CONTEXT, never EVIDENCE — its existence
    alone must never authorize brand-asset persistence.

        receipt / menu candidate -> handled by the cessions above this
        explicit flyer edit      -> vetoed here; the edit path owns it
        menu/price media         -> vetoed here; the menu path owns it
        classifier says logo     -> authorized
        explicit brand evidence  -> authorized
        anything else            -> NOT authorized; normal routing
        classifier failure       -> NOT authorized (`classifier_unavailable`)

    `source_edit_template` is a HARD veto: the edit path owns those turns
    whatever the caption also says. The menu/price veto is keyed on the caption
    words, not on the classifier's role — see `_MENU_PRICE_SEMANTICS`.

    Evidence alone is not enough. A caption that NAMES a brand artifact while
    complaining about it ("remove the watermark", "the existing flyer has a
    typo") is not an upload, so a defect frame with no offer declines.

    Returns `(authorized, reason)`. Three reason classes matter to the caller:
    an ordinary decline (no audit row — declining is the common case), a
    fail-closed `classifier_unavailable`, and a `..._over_evidence` decline
    where brand-artifact evidence WAS present and something overrode it. That
    last one is the false-negative class; it is rare and it is the one an
    operator needs to be able to count, so the caller audits it.
    """
    body = text or ""
    negated = bool(_BRAND_ASSET_NEGATED_OFFER.search(body))
    evidence = bool(_BRAND_ASSET_EVIDENCE.search(body)) or _durable_style_donation(body)
    offer = bool(_BRAND_ASSET_OFFER.search(body)) and not negated
    defect = bool(_BRAND_ASSET_DEFECT_FRAME.search(body)) or negated
    try:
        role = _classify_flyer_reference_role(text, media_path)
    except Exception as e:  # noqa: BLE001 — a classifier failure must never write
        return False, f"classifier_unavailable: {type(e).__name__}: {e}"
    over = "_over_evidence" if evidence else ""
    if role == "source_edit_template":
        return False, "role_source_edit_template" + over
    # Ordered ABOVE the `logo` return on purpose. The classifier's logo branch
    # has no negation handling, so "this flyer has no logo", "do not use our
    # logo on this" and "the logo is missing from this poster" all resolve to
    # `logo` and would otherwise walk straight past the guard added for exactly
    # that shape — a guard inconsistent with itself.
    if evidence and defect and not offer:
        return False, "defect_frame_without_offer_over_evidence"
    if role == "logo":
        return True, "role_logo"
    if _MENU_PRICE_SEMANTICS.search(body):
        return False, "menu_price_semantics" + over
    # Deliberately NOT keyed on `old_flyer_reference`: that role fires on a bare
    # "sample" or "reference" anywhere in the caption, which is the standalone
    # evidence this fix exists to reject.
    if not evidence:
        if _durable_style_signal(body):
            # Countable, not silent. This is the shape that leaked in a new
            # phrasing on each of three passes, so a residual leak has to be
            # visible in production rather than found by a fourth review.
            return False, "durable_style_without_donation"
        return False, f"no_brand_asset_evidence (role={role})"
    return True, "brand_artifact_evidence"


def _decline_is_countable(decision: str) -> bool:
    """Does this decline belong in the audited false-negative class?

    Two shapes: a decline that overrode explicit brand evidence, and a durable
    styling caption that did not qualify as a donation. Ordinary no-evidence
    declines are the common case and stay silent, or the signal drowns in them.
    """
    return decision.endswith("_over_evidence") or decision == "durable_style_without_donation"


def _try_flyer_brand_asset_intercept(text: str, chat_id: str, event: Any, media_path: str,
                                     *, owner_receipt_candidate: bool = False,
                                     menu_caption_candidate: bool = False,
                                     identity: Optional[tuple] = None) -> Optional[dict]:
    """Capture logo/template uploads during onboarding or flyer requests.

    `owner_receipt_candidate` and `menu_caption_candidate` are the TURN-SCOPED
    snapshots resolved once in dispatch. This arm must not re-resolve either: a
    second identity read could disagree with the first and let a brand asset be
    written for a message the turn already classified as an owner receipt or a
    menu update.

    `identity` is that same turn's `(phone, role)` scalar. Passed in so the whole
    turn reads it once; resolved here only when a caller omits it.
    """
    # Yield the EXACT owner-receipt candidate the later receipt cession claims.
    # This arm is terminal and runs before that cession, so without this a
    # genuine owner receipt is stored as a brand asset and the cession is never
    # reached — the live 2026-08-10 (B0009) and 2026-08-12 (B0010) failures.
    #
    # The owner exemption below cannot cover it: it reads the LEGACY SCALAR,
    # which is deliberately frozen at `employee` for a principal holding both
    # memberships, so the exemption never fires for exactly the sender this
    # matters for.
    #
    # Scoped to the four-way receipt predicate on purpose — NOT to owner
    # membership alone. A dual-role owner acting as a Flyer Studio customer
    # must still be able to upload a logo, template or reference.
    #
    # No audit row here: `receipt_caption_ceded_to_dispatcher` belongs to the
    # receipt arm when it actually claims the turn.
    if owner_receipt_candidate:
        return None
    # Same class of defect, same fix, for the menu cession — which sits 50+ lines
    # further down and is therefore unreachable for any turn this terminal arm
    # claims. The breaking shape is a verified EMPLOYEE (or the dual-role
    # principal whose legacy scalar is frozen at `employee`) sending a menu photo
    # captioned "new menu with our logo": the owner exemption below does not
    # apply, `should_start_new_flyer_over_active` declines because
    # `_CURRENT_BRAND_UPLOAD` matches "logo", and "logo" is an explicit asset
    # word — so the menu is stored as a brand LOGO and attached to every later
    # flyer prompt, and the pricebook is never updated.
    #
    # Scoped to the menu candidacy predicate on purpose, NOT to the caption or
    # the role alone: a legitimate "here's our new logo" from the same sender
    # must still be captured.
    #
    # No audit row here: `menu_caption_ceded_to_dispatcher` belongs to the menu
    # arm when it actually claims the turn.
    if menu_caption_candidate:
        return None
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = (
        identity if identity is not None
        else actions.lid_to_phone_via_identify_sender(chat_id))
    if role == "owner":
        return None
    if not phone:
        return None

    if actions.should_start_new_flyer_over_active(text, has_media=True):
        return None
    # The authorization decision. `active_project` is read for the regeneration
    # branch below ONLY — it is deliberately not an input here: an open flyer
    # project is context, not evidence that the attached media is brand art.
    authorized, decision = _flyer_brand_asset_authorized(text, media_path)
    if not authorized:
        # Neither branch replies: the turn continues down dispatch, and an ack
        # from this arm would double up with whichever arm actually claims it.
        if decision.startswith("classifier_unavailable"):
            actions.audit_intercepted(
                reason="flyer_brand_asset_classifier_unavailable", chat_id=chat_id,
                subprocess_rc=2, detail=decision[:500],
            )
        elif _decline_is_countable(decision):
            # The false-negative class, made countable.
            actions.audit_intercepted(
                reason="flyer_brand_asset_declined_over_evidence", chat_id=chat_id,
                detail=f"decision={decision}; media_path={media_path}",
            )
        return None
    # Defense in depth for the one class whose safety rests on open-ended
    # language. Complaint phrasing has no closed vocabulary, so a fourth leak
    # shape is possible; marking the CAPTURES makes a residual leak greppable in
    # production within a day instead of waiting on a fifth review. Carried on
    # the existing audit rows rather than a new audit reason, so it adds nothing
    # to the widened-Literal rollback exposure.
    #
    # It must reach ALL THREE terminal capture paths, not just the two obvious
    # ones. The store call above has already succeeded by the time any of them
    # runs, so the manual-review branch — which returns early with only its own
    # `flyer_reference_manual_review_queued` row — captured the asset while
    # carrying no marker. A compensating control with a hole is worse than one
    # known to be absent, because it is trusted.
    durable_marker = (
        "; durable_style_donation=true" if decision == "brand_artifact_evidence"
        and _durable_style_donation(text or "") else "")
    active_project = actions.find_active_flyer_project_by_sender(phone, chat_id)

    ok, detail, result = actions.trigger_store_flyer_brand_asset(
        chat_id=chat_id,
        sender_phone=phone,
        message_id=message_id,
        media_path=media_path,
        text=text,
        sender_role=role,
    )
    if not ok or not result:
        actions.audit_intercepted(
            reason="flyer_brand_asset_failed", chat_id=chat_id,
            subprocess_rc=2, detail=detail[:500],
        )
        return None

    # Style-transfer derivation (Workstream A save-time trigger, 2026-07-11):
    # best-effort, fire-and-forget, gated on the feature flag so it is dormant
    # until armed. The derivation runs as a DETACHED subprocess — the customer's
    # brand-asset-saved ack (below) is never blocked or delayed, and any failure
    # fails open to no-derived-style (render falls back to the register voice).
    # The script targets active templates lacking a derived_style, so a logo
    # upload is a cheap no-op. `customer_id` is only present once the account
    # exists (pending onboarding assets are picked up by --backfill-all later).
    if os.environ.get("FLYER_BRAND_STYLE_TRANSFER") == "1" and result.get("customer_id"):
        try:
            actions.spawn_derive_flyer_brand_style(str(result.get("customer_id")))
        except Exception:  # noqa: BLE001 — derivation is best-effort; never touch the ack
            pass

    reply = result.get("reply_text") or "Flyer Studio\n------------\nBrand asset saved."
    if active_project is not None:
        project_id = str(active_project.get("project_id") or "")
        status = str(active_project.get("status") or "")
        if status in {"awaiting_concept_selection", "awaiting_final_approval", "revising_design"}:
            actions.invoke_update_flyer_project(
                project_id,
                "--revision-text", "Use the newly uploaded logo/template for this flyer.",
                "--message-id", message_id,
            )
            gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
            if gen_ok:
                preview_ok, preview_mid, preview_err = actions._dispatch_concept_preview_send(chat_id, project_id)
                actions.audit_intercepted(
                    reason="flyer_brand_asset_saved", chat_id=chat_id,
                    subprocess_rc=0 if preview_ok else 3,
                    detail=f"project_id={project_id}; regenerated=true; status={result.get('next_status')}; ack_message_id={preview_mid}; ack_error={preview_err[:300]}{durable_marker}",
                )
                if preview_ok:
                    return {"action": "skip", "reason": f"cf-router flyer brand asset saved and regenerated {project_id}"}
            if actions.flyer_generation_queued_manual_review(gen_detail):
                ack_ok, manual_mid, ack_err = actions.send_flyer_manual_review_ack(
                    chat_id,
                    project_id,
                    text,
                    reason=gen_detail,
                    action_context=build_action_context_for_command(
                        PROJECT_ACTIONS, "manual_review.queued",
                        is_regulated_action=False,
                    ),
                )
                actions.audit_intercepted(
                    reason="flyer_reference_manual_review_queued",
                    chat_id=chat_id,
                    subprocess_rc=0 if ack_ok else 3,
                    detail=(
                        f"project_id={project_id}; brand_asset_regeneration_manual=true; "
                        f"status={result.get('next_status')}; ack_message_id={manual_mid}; "
                        f"ack_error={ack_err[:300]}{durable_marker}"
                    ),
                )
                return {"action": "skip", "reason": f"cf-router flyer brand asset manual review queued {project_id}"}
            reply = f"{reply}\n\nSaved. I couldn't finish the flyer update automatically yet. I'll send an update here shortly."

    # PR-ζ.1b §13.B — brand-asset save via ACCOUNT_ACTIONS entry.
    # Durable local account asset write; same shape as update_phone etc.
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply,
        action_context=build_action_context_for_command(
            ACCOUNT_ACTIONS, "update_brand_asset",
        ),
    )
    actions.audit_intercepted(
        reason="flyer_brand_asset_saved", chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"status={result.get('next_status')}; customer_id={result.get('customer_id') or ''}; "
            f"sender_role={role}; media_path={media_path}; ack_message_id={mid}; "
            f"ack_error={err[:300]}{durable_marker}"
        ),
    )
    return {"action": "skip",
            "reason": f"cf-router flyer brand asset: {result.get('next_status')}"}


# AN-1 (E2E audit 2026-07-13): statuses where a preview does not yet exist, so an
# approval reply is premature. Used to send a progress reply instead of silently
# rewriting the approval as a revision edit.
_FLYER_PRE_PREVIEW_STATUSES = {"generating_concepts", "awaiting_concept_selection"}


def _flyer_early_approval_progress_reply(status: str) -> Optional[str]:
    """AN-1: progress copy for an approval that arrives BEFORE a preview exists, or
    None when the status is not pre-preview (approval routes normally)."""
    if status not in _FLYER_PRE_PREVIEW_STATUSES:
        return None
    if status == "awaiting_concept_selection":
        body = ("Your flyer options are ready! Reply 1, 2, or 3 to pick one, "
                "then you can approve the final.")
    else:
        body = ("Thanks! Your flyer is still being prepared — I'll send you the "
                "preview to approve shortly.")
    return f"Flyer Studio\n------------\n{body}"


def _try_amendment_conflict_intercept(
    text: str, chat_id: str, event: Any, media_path: Optional[str] = None,
) -> Optional[dict]:
    """PR-R2B-1 flyer/catering amendment-conflict gate (SINGLE hoisted chokepoint,
    runs ONCE before the flyer active-project arm commits).

    Fires the bounded Hermes discriminator ONLY in the exactly-one-eligible-lead
    conflict cell; otherwise returns None (byte-identical fall-through to the flyer
    arm). Deterministic gating BEFORE any model call:
      * flag off ....................... None (no lookup, no call)
      * no non-delivered flyer project . None (nothing would consume it → no conflict)
      * zero eligible catering leads ... None (pure flyer path unchanged)
      * sender not allowlisted ......... None (no call)
      * MULTIPLE eligible leads ........ clarify (NEVER silently choose; no call)
      * exactly ONE eligible lead ...... run the discriminator (AT MOST one call)
    Discriminator routing:
      * flyer_edit ..................... None (UNCHANGED flyer path)
      * catering_amendment ............. R2A capture (source=conflict_discriminator)
                                         BEFORE any reply; on capture failure → retry +
                                         TOTAL suppression (no flyer, no LLM, no lead)
      * clarify / any failure .......... deterministic clarification (creates nothing)
    Hermes NEVER selects a lead — association is deterministic and computed here."""
    if not actions.catering_amendment_discriminator_enabled():
        return None  # DORMANT: flag off ⇒ byte-identical to today (flyer wins)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    # Only messages the flyer active-project arm would CONSUME are in the conflict set:
    # the sender must have a non-delivered active flyer project (role is neither
    # authorization nor exclusion — association + lead state alone decide).
    if not actions.has_non_delivered_flyer_project_by_sender(phone, chat_id):
        return None
    # DETERMINISTIC association (before Hermes sees anything): the sender's eligible
    # (ACTIONABLE) catering leads. Zero ⇒ no conflict; pure flyer path unchanged.
    eligible = actions.find_all_eligible_catering_leads_by_sender(phone, chat_id)
    if not eligible:
        return None
    # Canonical-identity allowlist gate. Not armed for this sender ⇒ no model call,
    # byte-identical fall-through.
    if not actions.catering_amendment_discriminator_allowlisted(chat_id):
        return None

    project = actions.find_active_flyer_project_by_sender(phone, chat_id) or {}
    project_id = str(project.get("project_id") or "")

    if len(eligible) > 1:
        # Multiple eligible leads → NEVER silently choose → clarify (no model call).
        return _send_amendment_conflict_clarification(
            text=text, chat_id=chat_id, event=event, leads=eligible,
            project_id=project_id, cause="multi_lead", latency_ms=0,
            reason="catering_amendment_conflict_clarify")

    lead = eligible[0]
    lead_id = str(lead.get("lead_id") or "")
    result = actions.run_catering_amendment_discriminator(
        text=text, lead_id=lead_id, lead_status=str(lead.get("status") or ""))
    decision, cause, latency = result["decision"], result["cause"], result["latency_ms"]

    if cause != "ok":
        # Discriminator unavailable/failed (timeout/error/out_of_enum/budget/no_key) →
        # deterministic clarify with a DEDICATED reason (Hermes-failure telemetry).
        return _send_amendment_conflict_clarification(
            text=text, chat_id=chat_id, event=event, leads=eligible,
            project_id=project_id, cause=cause, latency_ms=latency,
            reason="catering_amendment_conflict_discriminator_failed")

    if decision == "flyer_edit":
        actions.audit_intercepted(
            reason="catering_amendment_conflict_flyer_edit", chat_id=chat_id,
            detail=(f"lead_id={lead_id}; project_id={project_id}; latency_ms={latency}; "
                    f"fell through to unchanged flyer path"))
        return None  # UNCHANGED flyer path — the arm below runs exactly as today

    if decision == "catering_amendment":
        capture = catering_amendments.capture_branch_b_amendment(
            lead=lead, text=text, chat_id=chat_id, phone=phone,
            message_id=_extract_native_message_id(event),
            source_transport=_event_transport(event),
            provider_timestamp=_event_provider_timestamp(event),
            source="conflict_discriminator")
        if capture.ok:
            if F7_PRIMARY_FOLLOWUP_REPLY:
                actions.send_canonical_followup_reply(chat_id, lead_id, _extract_native_message_id(event))
            actions.audit_intercepted(
                reason="catering_amendment_conflict_captured", chat_id=chat_id,
                detail=(f"lead_id={lead_id}; project_id={project_id}; "
                        f"amendment_id={capture.amendment_id}; "
                        f"{'replayed' if capture.idempotent else 'captured'}; "
                        f"latency_ms={latency}; flyer routing suppressed"))
            return {"action": "skip",
                    "reason": f"cf-router R2B-1: catering amendment captured for {lead_id} (flyer suppressed)"}
        # Capture failure → deterministic retry + TOTAL suppression (flyer AND generic
        # LLM AND lead creation all suppressed; NEVER fall back to the flyer arm).
        if F7_PRIMARY_FOLLOWUP_REPLY:
            _send_amendment_retry_reply(chat_id, lead_id)
        actions.audit_intercepted(
            reason="catering_amendment_conflict_capture_failed", chat_id=chat_id,
            detail=(f"lead_id={lead_id}; project_id={project_id}; "
                    f"capture_failed reason={capture.reason}; latency_ms={latency}; "
                    f"retry requested; flyer+LLM suppressed"))
        return {"action": "skip",
                "reason": f"cf-router R2B-1: amendment capture failed for {lead_id}, retry requested"}

    # decision == "clarify" (genuine model ambiguity) → deterministic clarification.
    return _send_amendment_conflict_clarification(
        text=text, chat_id=chat_id, event=event, leads=eligible,
        project_id=project_id, cause="discriminator_clarify", latency_ms=latency,
        reason="catering_amendment_conflict_clarify")


def _send_amendment_conflict_clarification(
    *, text: str, chat_id: str, event: Any, leads: list, project_id: str,
    cause: str, latency_ms: int, reason: str,
) -> dict:
    """Send the deterministic flyer-vs-catering clarification for the conflict gate and
    park a single-turn pending choice (kind=amendment_conflict). Creates NEITHER a
    flyer revision NOR a catering lead — only asks + stores. Metadata-only audit."""
    lead_ids = [str(l.get("lead_id") or "") for l in leads if l.get("lead_id")]
    message_id = _extract_message_id(event, chat_id, text)
    try:
        actions.save_revenue_route_clarification(
            chat_id=chat_id, original_text=text, message_id=message_id,
            sender_phone="", sender_role="",
            signals=[f"amendment_conflict:{cause}"],
            kind="amendment_conflict", lead_ids=lead_ids)
    except Exception:
        pass  # storing the pending is best-effort; the ask still goes out
    reply = actions.amendment_conflict_clarification_reply(lead_ids)
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply,
        action_context=build_action_context(
            action_id="catering.routing.amendment_conflict_clarification",
            is_regulated_action=False))
    actions.audit_intercepted(
        reason=reason, chat_id=chat_id, subprocess_rc=0 if ack_ok else 3,
        detail=(f"cause={cause}; lead_ids={','.join(lead_ids)}; project_id={project_id}; "
                f"latency_ms={latency_ms}; ack_message_id={mid}; ack_error={err[:200]}"))
    return {"action": "skip",
            "reason": "cf-router R2B-1: amendment/flyer clarification sent"}


# P1-1 escape-gate sentinel. `_try_flyer_catering_escape_gate` returns this
# (NOT None) for the fall-through case, so the caller can distinguish "not
# catering — run the flyer arms unchanged" from "escaped to F7 which itself
# declined (owner / lead-create failure) → return None and let the LLM handle,
# but do NOT let the flyer active-project arms capture the catering inbound".
_GATE_FALLTHROUGH = object()


def _make_classify_catering_memo() -> Callable[[str], tuple]:
    """Return a dispatch-scoped single-flight memo around `actions.classify_catering`.

    The underlying classifier runs AT MOST ONCE per inbound even on the failure
    path: the first call caches the result OR the exception, and later calls
    return the cached tuple or RE-RAISE the cached exception without invoking the
    classifier again. Shared by the line-456 send-now compound check and the P1-1
    escape gate so the send-now-compound route costs exactly one classifier call.
    """
    cache: dict = {}

    def _memo(text: str) -> tuple:
        if "value" in cache:
            return cache["value"]
        if "error" in cache:
            raise cache["error"]
        try:
            cache["value"] = actions.classify_catering(text)
        except Exception as exc:  # noqa: BLE001 — cache + re-raise (single-flight)
            cache["error"] = exc
            raise
        return cache["value"]

    return _memo


def _flyer_send_now_early_path_allowed(text: str, classify_memo: Callable[[str], tuple]) -> bool:
    """True only for a PURE "send now" (no fresh catering intent) so the line-456
    approval/finalization arm still fires byte-identically.

    A compound "send it now + <fresh catering>" — or ANY classifier error —
    returns False so the inbound falls through the normal dispatch ladder (R2B-1
    keeps precedence) to the escape gate, which raises ONE flyer-vs-catering
    clarification. The whole-message approval-text arm is UNCHANGED and never
    reaches this check (it short-circuits the `or` before us), so approval
    replies stay classifier-free."""
    if not actions.is_flyer_send_now_intent(text):
        return False
    try:
        is_catering, _signals = classify_memo(text)
    except Exception:  # noqa: BLE001 — classifier error ⇒ treat as compound ⇒ clarify downstream
        return False
    return not is_catering


def _is_menu_caption_candidate(
    text: str, *, media_path: Optional[str], role: str,
) -> bool:
    """Is this inbound the exact owner/employee menu-photo shape?

    THE single source of truth for menu candidacy. Two call sites consume it and
    neither re-derives the conditions: the brand-asset arm, which yields for this
    exact shape, and `_menu_caption_cedes_to_dispatcher`, which claims it. A
    yield-predicate that could drift from the cession-predicate would resurrect
    the defect it exists to close — the arm yields, the cession declines, and the
    turn falls through into Flyer routing anyway.

    Side-effect-free: no audit row, no state change, no send. Unlike its receipt
    sister it is also fully deterministic, because the caller supplies the
    already-resolved sender role rather than this function spawning
    identify-sender.

    All four conditions are required:

      * the message carries media — the SKILL extracts from an image or a PDF,
        and `_extract_media_path` surfaces both as `media_path`
      * the sender resolves to owner or verified employee — the SKILL's hard
        rule; `identify-sender` returning owner/employee IS that verification
      * the visible caption carries a documented SKILL trigger
        (`actions.is_menu_update_caption`)
      * no explicit flyer create/edit signal, which vetoes — the same
        deterministic exclusion the P1-1 escape gate uses, so "update menu
        prices on this flyer" stays on the flyer path

    Defensive: any error means NOT a candidate, so the flyer path stays
    byte-identical on failure.
    """
    if not media_path or role not in ("owner", "employee"):
        return False
    try:
        if not actions.is_menu_update_caption(text):
            return False
        if _flyer_edit_signal_present(text, has_media=True):
            return False
        return True
    except Exception:  # noqa: BLE001 — never claim an inbound on an error
        return False


def _menu_caption_cedes_to_dispatcher(
    text: str, chat_id: str, *, role: str, menu_caption_candidate: bool,
) -> bool:
    """Menu-caption routing precedence (2026-08-01 live F0226 defect).

    True when cf-router must claim an inbound as a menu update so the
    menu→pricebook pipeline runs, instead of letting a flyer arm take it first.
    The conditions live in `_is_menu_caption_candidate`; this arm consumes the
    turn-scoped answer the brand-asset decision already acted on and adds the
    audit row, which belongs here because only this arm actually claims the turn.

    Records the cession, then the caller runs the ingestion — creating neither a
    flyer project nor a catering lead. Defensive: any error means NO cession
    (the flyer path stays byte-identical).
    """
    # Consumes the turn-scoped snapshot — deliberately does NOT re-derive it.
    if not menu_caption_candidate:
        return False
    try:
        actions.audit_intercepted(
            reason="menu_caption_ceded_to_dispatcher",
            chat_id=chat_id,
            detail=(f"sender_role={role}; has_media=true; "
                    f"skill=update_catering_menu"),
        )
        return True
    except Exception:  # noqa: BLE001 — a cession decision must never claim the inbound
        return False


_MENU_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

# The SKILL's Step-3 card, rendered here instead of by the LLM. `preview_text`
# is passed through VERBATIM — the script rendered it deterministically (item
# list, exclusions, and the Pricebook section that tells the owner `yes` also
# changes what customers are charged), and the SKILL's hard rule is not to
# paraphrase it.
_MENU_PREVIEW_CARD = (
    "⚕ *Catering Agent*\n"
    "────────────\n"
    "*Menu Update {update_id}* — preview\n\n"
    "{preview_text}\n\n"
    "----\n"
    "*To apply this menu, reply:* `{code} yes`\n"
    "*To discard and try again, reply:* `{code} no`"
)

# The failure arm's copy. Deliberately says what did NOT happen: no proposal is
# staged, so there is no code to approve and nothing for the owner to act on
# except re-sending. Carries no completion verb, so it is clean under the
# regulated-send lint it also has to pass.
_MENU_INGESTION_FAILED_TEXT = (
    "⚕ *Catering Agent*\n"
    "────────────\n"
    "I could not read that menu, so there is nothing staged for your approval "
    "yet — your current menu and prices are untouched.\n\n"
    "Please re-send it, ideally a straight-on photo with the prices in focus."
)


def _menu_message_shape(media_path: Optional[str], text: str) -> str:
    """Map the inbound to the DispatcherRouted `message_shape` enum."""
    if not str(media_path or "").lower().endswith(_MENU_IMAGE_SUFFIXES):
        return "media_other"  # PDF / doc — the SKILL accepts these too
    return "image_with_caption" if str(text or "").strip() else "image_only"


def _send_menu_ingestion_failure(chat_id: str) -> tuple[bool, str, str]:
    """The failure arm's ONE reply. Its context reports non-completion, which is
    why it is deliverable: the success invariant gates claims, not honesty."""
    return actions.send_flyer_text(
        chat_id, _MENU_INGESTION_FAILED_TEXT,
        action_context=build_action_context(
            action_id="catering.menu.ingestion_failed",
            is_regulated_action=True,
            verified_action_result=False,
            claims_action_completed=False,
            mutation_class="local_reversible",
        ),
    )


def _run_owner_menu_ingestion(
    chat_id: str, event: Any, *, text: str, media_path: Optional[str],
    role: str, sender_phone: str,
) -> dict:
    """Terminal wrapper. This arm exists to take the action decision away from
    the LLM, so it must ALWAYS return a terminal result: the outer plugin
    try/except turns an exception into `None`, which would hand the turn straight
    back to the dispatcher — the exact failure being fixed. An unexpected error
    therefore degrades to the honest failure reply, never to an LLM turn."""
    try:
        return _owner_menu_ingestion_impl(
            chat_id, event, text=text, media_path=media_path,
            role=role, sender_phone=sender_phone,
        )
    except Exception as exc:  # noqa: BLE001 — see docstring: never yield the turn
        try:
            actions.audit_intercepted(
                reason="menu_ingestion_failed",
                chat_id=chat_id,
                detail=(f"sender_role={role}; unexpected_error="
                        f"{type(exc).__name__}: {str(exc)[:200]}"),
            )
            _send_menu_ingestion_failure(chat_id)
        except Exception:
            pass  # the terminal return below is the guarantee that matters
        return {"action": "skip",
                "reason": f"cf-router menu ingestion errored ({type(exc).__name__})"}


def _owner_menu_ingestion_impl(
    chat_id: str, event: Any, *, text: str, media_path: Optional[str],
    role: str, sender_phone: str,
) -> dict:
    """Run menu ingestion deterministically for an inbound the cession already
    proved is one: media present, a documented SKILL trigger in the caption, and
    an owner / verified-employee sender.

    Hermes still owns all the judgment — vision, extraction, normalization,
    ambiguity notes, schema validation, code minting, the pending write and the
    audit row all happen inside `parse-menu-photo`. What is removed is only the
    LLM's redundant decision about WHETHER to call it.

    Success is not the script's word for it. The script's stdout `update_id` is a
    claim; this re-reads the durable pending store and requires it to hold that
    same update_id before anything tells the owner a proposal is ready. Only that
    verified path builds a context claiming completion — every other outcome
    reports honestly that nothing was staged.
    """
    message_id = _extract_message_id(event, chat_id, text)
    # Written BEFORE invoking, per the dispatcher contract: an arm that bypasses
    # the LLM must be visible to routing-accuracy pairing even if it then fails.
    try:
        actions.audit_dispatcher_routed(
            message_id=message_id,
            chat_id=chat_id,
            routed_to_skill="update_catering_menu",
            message_shape=_menu_message_shape(media_path, text),
        )
    except Exception:
        pass  # telemetry only — never block the action

    rc, out, err = actions.invoke_parse_menu_photo(
        image_path=str(media_path or ""),
        source_image_id=message_id,
        sender_phone=sender_phone,
    )

    update_id = ""
    code = ""
    preview_text = ""
    if rc == 0:
        for line in reversed([ln for ln in str(out or "").splitlines() if ln.strip()]):
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(doc, dict) and doc.get("update_id"):
                update_id = str(doc.get("update_id") or "")
                code = str(doc.get("confirmation_code") or "")
                preview_text = str(doc.get("preview_text") or "")
            break

    # The durable half of the receipt. Absent / unreadable / different update_id
    # all mean the same thing: there is no proposal to point the owner at.
    pending = (actions.find_menu_pending_by_update_id(update_id)
               if update_id else None)
    verified = bool(pending) and bool(code) and bool(preview_text)

    if not verified:
        actions.audit_intercepted(
            reason="menu_ingestion_failed",
            chat_id=chat_id,
            subprocess_rc=rc,
            detail=(f"sender_role={role}; update_id={update_id or 'none'}; "
                    f"durable_pending={'yes' if pending else 'no'}; "
                    f"stderr={str(err)[:200]!r}"),
        )
        ok, mid, send_err = _send_menu_ingestion_failure(chat_id)
        return {"action": "skip",
                "reason": (f"cf-router menu ingestion failed (rc={rc}); "
                           f"owner told (ok={ok} mid={mid} err={send_err[:80]})")}

    actions.audit_intercepted(
        reason="menu_ingestion_staged",
        chat_id=chat_id,
        code=code,
        subprocess_rc=0,
        detail=(f"sender_role={role}; update_id={update_id}; "
                f"item_count={len(pending.get('extracted_items') or [])}"),
    )
    ok, mid, send_err = actions.send_flyer_text(
        chat_id,
        _MENU_PREVIEW_CARD.format(
            update_id=update_id, preview_text=preview_text, code=code),
        action_context=build_action_context(
            # No money marker in the id on purpose: staging a proposal is
            # local_reversible and nothing is charged or activated until the
            # owner replies `{code} yes`, which apply-menu-update handles.
            action_id=f"catering.menu.update_proposed:{update_id}",
            is_regulated_action=True,
            # Both flags earned: parse-menu-photo exited 0 AND the durable
            # pending store was re-read and holds this update_id. audit_row_id
            # stays unset — the script emits `menu_update_proposed` but returns
            # no row identifier, and inventing one would be a fabricated receipt.
            verified_action_result=True,
            claims_action_completed=True,
            mutation_class="local_reversible",
        ),
    )
    return {"action": "skip",
            "reason": (f"cf-router menu ingestion staged {update_id} "
                       f"(ok={ok} mid={mid} err={send_err[:80]})")}


def _is_owner_receipt_candidate(
    text: str, chat_id: str, *, media_path: Optional[str],
) -> bool:
    """Is this inbound the exact owner-expense-receipt shape?

    Side-effect-free — no audit row, no state change, no send — but NOT
    deterministic: `has_owner_capability` shells out to identify-sender with a
    timeout, so two calls in one turn can disagree.

    Therefore call this EXACTLY ONCE per inbound, in `_pre_gateway_dispatch_impl`,
    and pass the resulting boolean to both consumers. Re-resolving it at the
    receipt cession would let a transient identity failure contradict the answer
    the brand-asset arm already acted on, and the turn would fall through into
    the active-project Flyer intercept — the same misclassification class this
    predicate exists to prevent.

    Emitting an audit row here would also be wrong: it would log a cession that
    may never happen.

    Four conditions, deliberately ALL of them:

      * media present — an expense needs the image;
      * OWNER MEMBERSHIP — `has_owner_capability`, not the legacy scalar;
      * an explicit documented receipt caption;
      * no explicit Flyer edit signal, which vetoes.

    Narrowness is the point. `has_owner_capability` ALONE would be wrong: the
    same dual-role principal is intentionally allowed to act as a Flyer Studio
    customer, so "owner membership ⇒ never capture a brand asset" would stop
    them uploading a logo or template. Only this exact four-way conjunction
    yields the Flyer arm.

    Defensive: any error means NOT a candidate, so the flyer path stays
    byte-identical on failure.
    """
    if not media_path:
        return False
    try:
        if not actions.has_owner_capability(chat_id):
            return False
        if not actions.is_receipt_caption(text):
            return False
        if _flyer_edit_signal_present(text, has_media=True):
            return False
        return True
    except Exception:  # noqa: BLE001 — never claim an inbound on an error
        return False


def _receipt_caption_cedes_to_dispatcher(
    text: str, chat_id: str, *, media_path: Optional[str], role: str,
    owner_receipt_candidate: bool,
) -> bool:
    """True when cf-router must claim an inbound as an owner expense receipt.

    Mirrors `_menu_caption_cedes_to_dispatcher` with two deliberate differences:

      * **owner capability required.** The menu gate admits a verified employee;
        an expense is a money record, so a principal WITHOUT owner membership
        must NOT enter it. Employee membership alone never satisfies this.
      * the caption trigger is `actions.is_receipt_caption`.

    Both remaining conditions are unchanged — media must be present, and an
    explicit flyer edit signal vetoes the cession. Defensive: any error means NO
    cession, so the flyer path stays byte-identical.

    Authorization has EXACTLY ONE path: owner membership. The caller-supplied
    scalar `role` is NOT accepted as proof and is retained only for the audit
    row. This is a money record, so the gate resolves authorization itself
    rather than trusting a label it was handed — a caller passing a stale or
    wrong role must not be able to open it.

    Membership is what makes the dual-role case work: a principal who is both
    owner and an active roster employee resolves scalar `employee` by LID, and
    the previous `role != "owner"` check refused a genuine owner.

    The business rule is unchanged and NOT weakened — employee membership alone
    never satisfies this gate, so employee-only, customer and guest senders
    cannot enter a money record.
    """
    # Consumes the turn-scoped snapshot — deliberately does NOT re-resolve.
    if not owner_receipt_candidate:
        return False
    try:
        actions.audit_intercepted(
            reason="receipt_caption_ceded_to_dispatcher",
            chat_id=chat_id,
            detail=(f"sender_role={role}; owner_capability=true; "
                    f"has_media=true; skill=parse_receipt_photo"),
        )
        return True
    except Exception:  # noqa: BLE001 — a cession decision must never claim the inbound
        return False


# DRAFT-tier card copy. States the boundary explicitly and carries no approval
# code, no approve/reject instruction and no push language, because none of
# those actions exist while RealQBOClient is unimplemented.
_RECEIPT_INGESTION_FAILED_TEXT = (
    "⚕ *Expense Bookkeeper*\n"
    "────────────\n"
    "I could not read that receipt, so nothing was recorded.\n\n"
    "Please re-send it, ideally a straight-on photo with the total in focus."
)


def _expense_leads_path():
    """Expense store, derived from the state dir this plugin is configured for
    (tests patch `actions.LEADS_PATH`), so verification reads the SAME file the
    extractor wrote."""
    return actions.LEADS_PATH.parent / "expense-bookkeeper" / "leads.json"


def _owner_receipt_ingestion_impl(
    chat_id: str, event: Any, *, text: str, media_path: Optional[str],
    role: str, sender_phone: str,
) -> dict:
    """Run receipt ingestion deterministically for an inbound already proven to
    be one: media present, a documented receipt caption, and an owner sender.

    Hermes still owns all the cognition — vision, extraction, classification,
    dedup and schema validation all happen inside `extract-receipt`. What is
    removed is only the LLM's redundant decision about WHETHER to call it, which
    is unreachable anyway while the `skills` toolset is disabled.

    DRAFT tier: `--review-only`. No approval code, no approval request, no QBO
    client. Success is not the script's word for it — its stdout `expense_id` is
    a claim, so the durable store is re-read and must hold that id at status
    DRAFTED before the owner is told anything was recorded.
    """
    message_id = _extract_message_id(event, chat_id, text)
    try:
        actions.audit_dispatcher_routed(
            message_id=message_id,
            chat_id=chat_id,
            routed_to_skill="parse_receipt_photo",
            message_shape=_menu_message_shape(media_path, text),
        )
    except Exception:
        pass  # telemetry only — never block the action

    sender_lid = chat_id if str(chat_id).endswith("@lid") else ""
    rc, out, err = actions.invoke_extract_receipt(
        image_path=str(media_path or ""),
        source_image_id=message_id,
        sender_phone=sender_phone,
        sender_lid=sender_lid,
    )

    expense_id, card_text = "", ""
    try:
        doc = json.loads(out or "{}")
        expense_id = str(doc.get("expense_id") or "")
        card_text = str(doc.get("approval_card_text") or "")
    except (json.JSONDecodeError, TypeError):
        pass

    # EXIT_IDEMPOTENCY (9) means this exact message already produced a draft;
    # the original card was already sent, so re-sending would double-report.
    if rc == 9 and expense_id:
        return {"action": "skip",
                "reason": f"cf-router receipt ingestion idempotent {expense_id}"}

    verified = False
    if rc in (0, 7) and expense_id:
        try:
            doc = json.loads(_expense_leads_path().read_text(encoding="utf-8"))
            verified = any(
                l.get("expense_id") == expense_id and l.get("status") == "DRAFTED"
                and not l.get("owner_approval_code")
                for l in (doc.get("leads") or [])
            )
        except (OSError, json.JSONDecodeError, AttributeError):
            verified = False

    if not verified or not card_text:
        actions.audit_intercepted(
            reason="receipt_ingestion_failed", chat_id=chat_id, subprocess_rc=rc,
            detail=f"sender_role={role}; expense_id={expense_id or '-'}; "
                   f"verified={verified}; err={err[:120]}",
        )
        actions.send_flyer_text(
            chat_id, _RECEIPT_INGESTION_FAILED_TEXT,
            action_context=build_action_context(
                action_id="expense.receipt.ingestion_failed",
                is_regulated_action=False,
                verified_action_result=True,
                claims_action_completed=False,
                mutation_class="none",
            ),
        )
        return {"action": "skip",
                "reason": f"cf-router receipt ingestion failed (rc={rc})"}

    actions.audit_intercepted(
        reason="receipt_drafted", chat_id=chat_id, subprocess_rc=0,
        detail=f"sender_role={role}; expense_id={expense_id}; tier=DRAFT",
    )
    ok, mid, send_err = actions.send_flyer_text(
        chat_id, card_text,
        action_context=build_action_context(
            action_id=f"expense.receipt.drafted:{expense_id}",
            # Not regulated: nothing is approved, charged, pushed or activated.
            is_regulated_action=False,
            # Both earned: the script exited cleanly AND the durable store was
            # re-read and holds this expense_id at DRAFTED with no approval code.
            verified_action_result=True,
            claims_action_completed=True,
            mutation_class="local_reversible",
        ),
    )
    return {"action": "skip",
            "reason": (f"cf-router receipt drafted {expense_id} "
                       f"(ok={ok} mid={mid} err={send_err[:80]})")}


def _run_owner_receipt_ingestion(
    chat_id: str, event: Any, *, text: str, media_path: Optional[str],
    role: str, sender_phone: str,
) -> dict:
    """Error boundary: ingestion must never raise into dispatch."""
    try:
        return _owner_receipt_ingestion_impl(
            chat_id, event, text=text, media_path=media_path,
            role=role, sender_phone=sender_phone,
        )
    except Exception as exc:  # noqa: BLE001
        return {"action": "skip",
                "reason": f"cf-router receipt ingestion errored ({type(exc).__name__})"}


def _flyer_edit_signal_present(text: str, *, has_media: bool) -> bool:
    """True when the inbound carries an EXPLICIT flyer edit/approval signal.

    Reuses the deployed deterministic flyer classifiers — no new NLP. Kept
    narrow on purpose: a genuine catering amendment ("add 20 veg meals for the
    party") must NOT read as a flyer signal, so `is_flyer_revision_intent`
    (which matches such follow-ups) is deliberately excluded. Only an explicit
    flyer/poster/banner mention, a pure approval/send-now token, or a
    source-preserving edit on attached artwork counts."""
    return bool(
        actions.classify_flyer_intent(text)[0]
        or actions.is_flyer_approval_text(text)
        or actions.is_flyer_send_now_intent(text)
        or actions.is_exact_reference_edit_request(text, has_media=has_media)
    )


def _send_flyer_catering_intent_clarification(
    *, text: str, chat_id: str, event: Any, project_id: str, status: str,
    role: str, cause: str,
) -> dict:
    """P1-1 ambiguous / gate-error outcome: send ONE flyer-vs-catering question
    and park a single pending choice so the customer's "flyer"/"catering" reply
    routes through the EXISTING revenue-route resolver (`_try_revenue_route_
    clarification_choice`). Creates NEITHER a flyer revision NOR a catering lead
    — only asks + stores. Metadata-only audit (no raw text / phone)."""
    message_id = _extract_message_id(event, chat_id, text)
    try:
        actions.save_revenue_route_clarification(
            chat_id=chat_id, original_text=text, message_id=message_id,
            sender_phone="", sender_role=role,
            signals=[f"flyer_catering_intent:{cause}"],
        )
    except Exception:
        pass  # storing the pending is best-effort; the ask still goes out
    reply = actions.revenue_route_clarification_reply()
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply,
        action_context=build_action_context(
            action_id="flyer.routing.flyer_catering_intent_clarification",
            is_regulated_action=False,
        ),
    )
    actions.audit_intercepted(
        reason="flyer_catering_intent_clarification",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(f"cause={cause}; project_id={project_id}; status={status}; "
                f"sender_role={role}; ack_message_id={mid}; ack_error={err[:200]}"),
    )
    return {"action": "skip",
            "reason": "cf-router flyer/catering intent clarification sent"}


def _send_customer_complaint_escalation(
    *, text: str, chat_id: str, event: Any, role: str,
    project_id: str, lead_id: str,
) -> dict:
    """P1-1 complaint outcome: a customer venting at the agent while a catering
    lead or flyer project is live gets ONE escalation acknowledgement instead of
    being captured as a flyer queued-edit. Creates NEITHER a flyer revision NOR a
    catering lead — only sends + audits. Metadata-only audit (no raw text/phone)."""
    reply = actions.customer_complaint_escalation_reply()
    ack_ok, mid, err = actions.send_flyer_text(
        chat_id, reply,
        action_context=build_action_context(
            action_id="flyer.routing.customer_complaint_escalation",
            is_regulated_action=False,
        ),
    )
    actions.audit_intercepted(
        reason="customer_complaint_escalation",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(f"project_id={project_id}; lead_id={lead_id}; sender_role={role}; "
                f"ack_message_id={mid}; ack_error={err[:200]}"),
    )
    return {"action": "skip",
            "reason": "cf-router customer complaint escalation sent"}


def _try_flyer_catering_escape_gate(
    text: str, chat_id: str, event: Any, media_path: Optional[str] = None,
    *, classify_fn: Optional[Callable[[str], tuple]] = None,
) -> Any:
    """P1-1 fresh-intent catering escape gate (single shared site).

    A customer with a LIVE flyer project sent a fresh catering inquiry
    (F0224 incident: a "wedding for 120 guests, send me two sample menus"
    message was captured by the flyer active-project intercept as
    `flyer_reference_exact_edit_queued` and catering never saw it). This gate
    runs AFTER the R2B-1 amendment-conflict gate (its precedence is preserved)
    and BEFORE `_try_flyer_active_project_intercept`, so no terminal flyer arm
    can claim the message first.

    `classify_fn` is the dispatch-scoped single-flight `classify_catering` memo
    (defaults to `actions.classify_catering` so every direct-call test keeps
    working unchanged). Passing the memo lets the send-now compound check at the
    line-456 approval arm and this gate SHARE a single underlying classifier call
    per inbound (the memo re-raises a cached exception, which this gate's
    try/except turns into a clarification — never a guessed route).

    Scoped to an EXISTING active flyer project so a catering inquiry from a
    sender with no project flows the normal F7 path unchanged (and the escape
    audit row stays truthful). `classify_catering` is invoked AT MOST ONCE.

    Decision table:
      complaint + (open lead OR project) → one escalation ack (customer_complaint_escalation)
      catering=False, open lead + proposal/menu ask, not a flyer edit
                                         → delegate to the F7 catering path (open-lead escape)
      catering=False, otherwise          → _GATE_FALLTHROUGH (flyer arms run unchanged)
      catering=True, no flyer signal     → delegate to the F7 new-catering path
      catering=True, flyer signal (ambiguous) → one flyer-vs-catering clarification
      any exception inside the gate      → clarification (never a guessed route)

    Returns:
      _GATE_FALLTHROUGH — caller runs the flyer active-project intercept unchanged.
      dict              — handled (F7 skip result, or clarification sent).
      None              — escape delegated to F7 which declined (owner / lead-create
                          failure); caller returns None so the LLM handles it and the
                          flyer active-project arms are NOT run.
    """
    def _classify() -> tuple:
        """Decide what the gate should DO. Sends nothing — see the note below."""
        phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
        active_project = actions.find_active_flyer_project_by_sender(phone, chat_id)
        open_lead = actions.find_active_catering_lead_by_sender(phone, chat_id)
        # Complaint / frustration guard (highest precedence inside the gate): a
        # customer venting at the agent while a catering lead OR flyer project is
        # live must never be captured as a flyer queued-edit. One escalation ack;
        # no classifier call, no lead, no revision.
        if (active_project is not None or open_lead is not None) and actions.is_customer_complaint(text):
            return ("complaint", role,
                    str((active_project or {}).get("project_id") or ""),
                    str((open_lead or {}).get("lead_id") or ""))
        if active_project is None:
            return ("fallthrough",)
        project_id = str(active_project.get("project_id") or "")
        status = str(active_project.get("status") or "")
        is_catering, signals = (classify_fn or actions.classify_catering)(text)
        if not is_catering:
            # Open-lead proposal/menu escape (P1-1 durable root fix): a customer
            # mid-catering-conversation (OPEN lead) asking to see/select menus must
            # reach catering even when classify_catering is weak — UNLESS the
            # message is an explicit flyer create/edit. Placed BEFORE the flyer
            # active-project intercept so no terminal flyer arm can claim it. Only
            # clear proposal/menu language with an open lead escapes; the same
            # deterministic flyer-signal exclusion the ambiguous arm uses keeps a
            # genuine flyer edit on the flyer path. Reuses F7's canonical open-lead
            # lookup (find_active_catering_lead_by_sender) so the gate and F7 agree
            # on what an open lead is; classify_catering is NOT re-invoked (signals
            # from the single memo call above are forwarded).
            if (
                open_lead is not None
                and (actions.is_proposal_request_escape(text)
                     or actions.is_mix_and_match_request(text))
                and not _flyer_edit_signal_present(text, has_media=bool(media_path))
            ):
                actions.audit_intercepted(
                    reason="flyer_active_project_open_lead_catering_escape",
                    chat_id=chat_id,
                    detail=(f"project_id={project_id}; status={status}; "
                            f"sender_role={role}; open_lead={open_lead.get('lead_id')}"),
                )
                return ("f7", signals)
            return ("fallthrough",)
        if _flyer_edit_signal_present(text, has_media=bool(media_path)):
            return ("clarify_ambiguous", project_id, status, role)
        actions.audit_intercepted(
            reason="flyer_active_project_catering_intent_escape",
            chat_id=chat_id,
            detail=(f"project_id={project_id}; status={status}; sender_role={role}; "
                    f"signals={','.join(signals)[:200]}"),
        )
        return ("f7", signals)

    # CLASSIFY (may fail -> clarification, since nothing has been sent yet).
    try:
        plan = _classify()
    except Exception as exc:  # noqa: BLE001 — a gate error must never guess a route
        return _send_flyer_catering_intent_clarification(
            text=text, chat_id=chat_id, event=event,
            project_id="", status="", role="",
            cause=f"gate_error:{type(exc).__name__}",
        )

    # ACT. Outside the try on purpose: everything below can send to the
    # customer, and a failure here must NOT be answered with a clarification —
    # a message may already have gone out, and the pair contradicts itself.
    if plan[0] == "fallthrough":
        return _GATE_FALLTHROUGH
    try:
        if plan[0] == "complaint":
            _kind, role, project_id, lead_id = plan
            return _send_customer_complaint_escalation(
                text=text, chat_id=chat_id, event=event, role=role,
                project_id=project_id, lead_id=lead_id,
            )
        if plan[0] == "clarify_ambiguous":
            _kind, project_id, status, role = plan
            return _send_flyer_catering_intent_clarification(
                text=text, chat_id=chat_id, event=event,
                project_id=project_id, status=status, role=role,
                cause="ambiguous_flyer_and_catering",
            )
        return _try_f7_primary_intercept(
            text, chat_id, event, signals=plan[1], allow_new_lead=True,
        )
    except Exception as exc:  # noqa: BLE001
        # We cannot know whether a customer message already went out — F7 and
        # both senders above send internally. A second message risks the
        # contradictory pair this fix exists to remove, so the turn ends here:
        # quiet for the CUSTOMER, audited for the operator. Returning a terminal
        # result rather than re-raising also keeps the turn from reaching the
        # LLM, which would answer on top of whatever was already delivered
        # (pre_gateway_dispatch re-raises, so an escaping exception's handling
        # is Hermes' to decide — not something to depend on here).
        try:
            actions.audit_intercepted(
                reason="flyer_primary_failed",
                chat_id=chat_id,
                subprocess_rc=2,
                detail=(f"catering_escape_gate_action_failed=true; plan={plan[0]}; "
                        f"error={type(exc).__name__}: {str(exc)[:200]}"),
            )
        except Exception:
            # The audit write must never void the terminal skip: with a customer
            # message possibly already delivered, an escaping exception here
            # hands the turn back to Hermes and re-opens the double-reply
            # window this handler exists to close.
            pass
        return {"action": "skip",
                "reason": f"cf-router catering escape gate: {plan[0]} failed after dispatch"}


def _try_flyer_active_project_intercept(text: str, chat_id: str, event: Any, media_path: Optional[str] = None) -> Optional[dict]:
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
    if not phone:
        phone = _flyer_customer_sender_phone(customer)
    if not phone:
        return None
    active_project = actions.find_active_flyer_project_by_sender(phone, chat_id)
    # Quoted-APPROVE binding (2026-07-05): a swipe-reply's quotedMessageId,
    # when it matches one of a project's known outbound mids (preview media /
    # APPROVE CTA / finals), identifies the project the customer means —
    # override the newest-updated pick. Fail-open: any quote-metadata parsing
    # or lookup issue keeps the newest-updated result untouched. `text` lets
    # the binder refuse approval binds onto already-delivered rows (F0213
    # incident — stale-quote APPROVE must not strand the pending approval).
    active_project, binding_source = actions.resolve_flyer_binding_project(
        active_project, phone, chat_id, event, text,
    )
    if active_project is None:
        # No active row, but a status check ("any update?" / "F0058 status")
        # must still resolve when the only relevant project is closed_no_send
        # or explicitly named. Without this branch the inbound falls through
        # to LLM dispatch and the customer never learns their project was
        # closed.
        body_no_active = " ".join(actions.flyer_visible_message_text(text).split())
        if not actions.is_flyer_project_status_request(body_no_active):
            return None
        mentioned_id = actions.extract_flyer_project_id_mention(body_no_active)
        status_project = None
        if mentioned_id:
            status_project = actions.find_flyer_project_by_id_for_sender(phone, chat_id, mentioned_id)
        if status_project is None:
            status_project = actions.find_latest_flyer_project_for_status_by_sender(phone, chat_id)
        if status_project is None:
            return None
        sp_id = str(status_project.get("project_id") or "")
        sp_status = str(status_project.get("status") or "")
        reply, _is_source_edit_manual_status = _select_flyer_status_reply(status_project)
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, reply,
            action_context=build_action_context(
                action_id="flyer.project.status_surfaced",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason=("flyer_project_status" if ack_ok else "flyer_primary_failed"),
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"project_id={sp_id}; status_check=true; status={sp_status}; "
                f"no_active_project=true; sender_role={role}; "
                f"id_mentioned={'1' if mentioned_id else '0'}; "
                f"ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {"action": "skip", "reason": f"cf-router flyer status for {sp_id}"}

    project_id = str(active_project.get("project_id") or "")
    if customer and customer.get("status") not in {"trial", "active"}:
        if not actions.find_paid_flyer_guest_order(phone, chat_id) and not actions.find_reserved_flyer_guest_order(phone, chat_id, project_id):
            reply = actions.flyer_customer_not_active_reply(customer)
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id, reply,
                action_context=build_action_context(
                    action_id="flyer.account.customer_not_active",
                    is_regulated_action=False,
                ),
            )
            actions.audit_intercepted(
                reason="flyer_customer_not_active",
                chat_id=chat_id,
                subprocess_rc=0 if ack_ok else 3,
                detail=(
                    f"project_id={project_id}; customer_id={customer.get('customer_id') or ''}; "
                    f"status={customer.get('status') or ''}; sender_role={role}; "
                    f"ack_message_id={mid}; ack_error={err[:300]}"
                ),
            )
            return {"action": "skip", "reason": "cf-router flyer customer not active"}
    status = str(active_project.get("status") or "")
    body = " ".join(actions.flyer_visible_message_text(text).split())
    lower = body.lower()
    scope_block = actions.flyer_business_scope_block_message(customer or {}, body)
    if scope_block:
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, scope_block,
            action_context=build_action_context(
                action_id="flyer.scope.business_scope_blocked",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_business_scope_blocked",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"project_id={project_id}; status={status}; sender_role={role}; "
                f"ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {"action": "skip", "reason": "cf-router flyer business scope blocked"}
    requested_business_scope = actions.flyer_requested_business_scope(body)
    # Dedicated starter-idea and legacy trial-link flows live later in
    # pre_gateway_dispatch ordering. Active-project intercept must not swallow
    # generic vague/account intents just because a stale active row exists.
    # Keep named-business requests here: cross-business names need the scope
    # guard above, and same-business wording such as "create a new flyer for
    # <current business> with the address smaller" is an active-project edit.
    if actions.is_vague_flyer_start(body, has_media=bool(media_path)) and not requested_business_scope:
        return None
    if customer and customer.get("status") in {"trial", "active"} and actions.is_flyer_legacy_trial_link_followup(body):
        return None
    active_project_fresh_bypass = actions.should_bypass_active_flyer_project_for_fresh_request(
        body,
        active_project,
        has_media=bool(media_path),
    )
    revision_on_delivered = (
        status == "delivered"
        and actions.is_flyer_revision_intent(body)
        and not active_project_fresh_bypass
    )
    if (
        not revision_on_delivered
        and active_project_fresh_bypass
    ):
        actions.audit_intercepted(
            reason="flyer_active_project_bypassed",
            chat_id=chat_id,
            subprocess_rc=0,
            detail=(
                f"project_id={project_id}; message_id={message_id}; fresh_flyer_intent=true; status={status}; "
                f"sender_role={role}; has_media={'1' if media_path else '0'}"
            ),
        )
        return None
    # P0-1 stale-project guard: when the active project has been idle past
    # its per-status threshold (actions._FLYER_STALE_HOURS) AND the inbound
    # is a CLEAR new-flyer request (`should_start_new_flyer_over_active`),
    # bail so the new-project path takes ownership. F0036/F0043/F0045-style
    # ~19h-old projects on prod were the empirical motivation.
    #
    # Design note (S3 review fix): the guard uses POSITIVE evidence
    # ("this is a clear new request") instead of negative evidence
    # ("not a status/revision"). Negative evidence drops concept
    # selections ("1"/"C1"), approvals ("approve"/"yes"/"ok"), and
    # non-English replies that the English-regex revision/status helpers
    # don't classify — all of which should continue to attach so the
    # downstream handlers (selection_map, approval flow, manual-review
    # forwarding) run normally.
    concept_selection_for_stale_guard = (
        _resolve_flyer_concept_selection(body, active_project)
        if status == "awaiting_concept_selection"
        else ""
    )
    if (
        not revision_on_delivered
        and not concept_selection_for_stale_guard
        and actions.is_stale_for_new_request(active_project)
        and actions.should_start_new_flyer_over_active(body, has_media=bool(media_path))
    ):
        actions.audit_intercepted(
            reason="flyer_active_project_bypassed",
            chat_id=chat_id,
            subprocess_rc=0,
            detail=(
                f"project_id={project_id}; message_id={message_id}; fresh_flyer_intent=true; stale_for_new_request=true; "
                f"status={status}; sender_role={role}; has_media={'1' if media_path else '0'}"
            ),
        )
        return None
    if actions.is_flyer_project_status_request(body) and status not in {"completed"}:
        # Project resolution for status replies is DISTINCT from active-project
        # routing. The active picker excludes closed_no_send (so closures
        # don't swallow new-request flow); for status checks we must surface
        # the project the customer is actually asking about, which is the one
        # they last engaged with (or named explicitly).
        #   1. Exact F#### id mention in the body wins.
        #   2. Otherwise, latest-for-status selector (includes closed_no_send
        #      and delivered) wins when it's strictly newer than the active
        #      picker's result. We refuse to downgrade the customer to a
        #      staler row.
        status_project, mentioned_id = _resolve_status_project_for_reply(
            active_project=active_project,
            body=body,
            phone=phone,
            chat_id=chat_id,
        )
        status_project_id = str(status_project.get("project_id") or "")
        status_project_status = str(status_project.get("status") or "")
        # P0-6: manual_edit_required projects pick the source-edit-specific
        # reply ONLY when the reason_code is source_edit_provider_unavailable.
        # All other reason codes (missing_required_facts, reference_unsupported,
        # visual_qa_failed, etc.) flow through the general status reply, which
        # now consults MANUAL_REVIEW_REASON_LINES to deliver reason-specific
        # copy instead of the generic "source-preserving edit queue" text.
        reply, is_source_edit_manual_status = _select_flyer_status_reply(status_project)
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id, reply,
            action_context=build_action_context(
                action_id="flyer.project.status_surfaced",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason=("flyer_reference_exact_edit_status" if is_source_edit_manual_status and ack_ok else ("flyer_project_status" if ack_ok else "flyer_primary_failed")),
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"project_id={status_project_id}; status_check=true; status={status_project_status}; sender_role={role}; "
                f"id_mentioned={'1' if mentioned_id else '0'}; "
                f"ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {
            "action": "skip",
            "reason": (
                f"cf-router flyer exact edit status for {status_project_id}"
                if is_source_edit_manual_status else
                f"cf-router flyer status for {status_project_id}"
            ),
        }
    if (
        status in {"intake_started", "collecting_required_info", "awaiting_assets"}
        and actions.flyer_project_has_required_fields(active_project)
        and not active_project.get("concepts")
    ):
        access, quota_result = _reserve_flyer_access_or_reply(
            chat_id, phone, project_id, message_id,
            consume_quota=not bool(active_project.get("revisions")),
        )
        if quota_result is not None:
            return quota_result
        proc_ok, proc_mid, proc_err = actions.send_flyer_processing_ack(
            chat_id, project_id,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "intake.processing",
                is_regulated_action=False,
            ),
        )
        gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
        if gen_ok:
            if not active_project.get("revisions"):
                ack_ok, outbound_message_id, ack_err = _send_preview_then_finalize_access(
                    access, chat_id, phone, project_id, message_id,
                    proc_ok=proc_ok, proc_mid=proc_mid, proc_err=proc_err,
                )
            else:
                ack_ok, outbound_message_id, ack_err = actions._dispatch_concept_preview_send(chat_id, project_id)
                outbound_message_id = ",".join(x for x in [proc_mid, outbound_message_id] if x)
                if not proc_ok:
                    ack_err = f"processing_ack_failed: {proc_err}; ack_error={ack_err}"
        else:
            if not active_project.get("revisions"):
                _release_flyer_access(access, chat_id, phone, project_id, message_id)
            ack_ok, outbound_message_id, ack_err = _send_generation_failure_customer_update(
                chat_id,
                project_id,
                body,
                gen_detail,
                proc_ok=proc_ok,
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "generation.failed_ack",
                    is_regulated_action=False,
                ),
            )
            outbound_message_id = ",".join(x for x in [proc_mid, outbound_message_id] if x)
            ack_err = f"concept_generation_failed: {gen_detail}; ack_error={ack_err}"
        actions.audit_intercepted(
            reason="flyer_primary_project_created" if gen_ok and ack_ok else "flyer_primary_failed",
            chat_id=chat_id,
            subprocess_rc=0 if gen_ok and ack_ok else (2 if not gen_ok else 3),
            detail=(
                f"project_id={project_id}; intake_ready=true; sender_role={role}; "
                f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
            ),
        )
        return {"action": "skip",
                "reason": f"cf-router flyer active: generated {project_id}"}

    concept_selection = _resolve_flyer_concept_selection(body, active_project)
    if status == "awaiting_final_approval" and concept_selection:
        if concept_selection == str(active_project.get("selected_concept_id") or "") or len(active_project.get("concepts") or []) == 1:
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id,
                f"{concept_selection} is selected. Reply APPROVE to receive final files, or reply with changes.",
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "concept_preview.cta_text",
                ),
            )
            actions.audit_intercepted(
                reason="flyer_primary_project_created" if ack_ok else "flyer_primary_failed",
                chat_id=chat_id,
                subprocess_rc=0 if ack_ok else 3,
                detail=f"project_id={project_id}; selected_reminder={concept_selection}; sender_role={role}; ack_message_id={mid}; ack_error={err}",
            )
            return {"action": "skip",
                    "reason": f"cf-router flyer active: selected concept reminder for {project_id}"}
    if status == "awaiting_concept_selection" and concept_selection:
        concept_id = concept_selection
        ok, detail = actions.invoke_update_flyer_project(project_id, "--select-concept", concept_id)
        if ok:
            ok, detail2 = actions.invoke_update_flyer_project(project_id, "--status", "awaiting_final_approval")
            detail = f"{detail}; {detail2}"
        if ok:
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id,
                f"Selected {concept_id}. Reply with revision notes, or reply APPROVE to receive final files.",
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "concept_preview.cta_text",
                ),
            )
            actions.audit_intercepted(
                reason="flyer_primary_project_created", chat_id=chat_id,
                subprocess_rc=0 if ack_ok else 3,
                detail=f"project_id={project_id}; selected={concept_id}; sender_role={role}; ack_message_id={mid}; ack_error={err}",
            )
            return {"action": "skip",
                    "reason": f"cf-router flyer active: selected {concept_id} for {project_id}"}
        actions.audit_intercepted(
            reason="flyer_primary_failed", chat_id=chat_id,
            subprocess_rc=2, detail=f"project_id={project_id}; select_failed={detail[:400]}",
        )
        return None

    if status == "delivered" and not actions.is_flyer_revision_intent(body):
        return None

    if status == "manual_edit_required":
        if actions.is_flyer_project_status_request(body):
            # Status-reply project resolution: prefer an explicit FXXXX
            # mention, else any closed_no_send/delivered/etc. row that's
            # strictly newer than the active picker's choice. Same rule as
            # the active-project status branch — keep the two sites
            # consistent so customers get the same answer regardless of
            # which intercept path runs first.
            status_project, mentioned_id = _resolve_status_project_for_reply(
                active_project=active_project,
                body=body,
                phone=phone,
                chat_id=chat_id,
            )
            status_project_id = str(status_project.get("project_id") or "")
            status_project_status = str(status_project.get("status") or "")
            # P0-6: same reason_code routing as the first status-check handler
            # — source-edit-specific reply for source_edit_provider_unavailable
            # only; everything else flows through the reason-code-aware general
            # reply.
            manual_block = status_project.get("manual_review") or {}
            manual_reason_code = actions.normalize_manual_reason_code(manual_block.get("reason_code"))
            reply, is_source_edit_manual_status = _select_flyer_status_reply(status_project)
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id, reply,
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "manual_review.status_replied",
                    is_regulated_action=False,
                ),
            )
            # P0-6: audit reason must match the routing branch. Pre-S7 this
            # was hardcoded to flyer_reference_exact_edit_status regardless
            # of reason_code — operator dashboards filtering by audit reason
            # would have overcounted source-edit traffic vs the general
            # manual-queue status checks (visual_qa_failed, etc.).
            if ack_ok:
                audit_reason = (
                    "flyer_reference_exact_edit_status"
                    if is_source_edit_manual_status
                    else "flyer_project_status"
                )
            else:
                audit_reason = "flyer_primary_failed"
            actions.audit_intercepted(
                reason=audit_reason,
                chat_id=chat_id,
                subprocess_rc=0 if ack_ok else 3,
                detail=(
                    f"project_id={status_project_id}; queued_status_check=true; "
                    f"status={status_project_status}; reason_code={manual_reason_code}; sender_role={role}; "
                    f"id_mentioned={'1' if mentioned_id else '0'}; "
                    f"ack_message_id={mid}; ack_error={err[:300]}"
                ),
            )
            return {"action": "skip",
                    "reason": (
                        f"cf-router flyer exact edit status for {status_project_id}"
                        if is_source_edit_manual_status else
                        f"cf-router flyer status for {status_project_id}"
                    )}
        # AN-1: an approval that arrives BEFORE a preview exists must not be silently
        # rewritten as a revision edit. This is the last stop before the revision
        # fallback — every finalize / concept-select / status handler above has
        # already returned — so only a premature approval remains here. Send a clear
        # progress reply instead of a confusing "what would you like to change?".
        _early_reply = _flyer_early_approval_progress_reply(status)
        if _early_reply is not None and actions.is_flyer_approval_text(body):
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id, _early_reply,
                action_context=build_action_context(
                    action_id="flyer.project.early_approval_progress",
                    is_regulated_action=False,
                ),
            )
            actions.audit_intercepted(
                reason="flyer_early_approval_progress" if ack_ok else "flyer_primary_failed",
                chat_id=chat_id,
                subprocess_rc=0 if ack_ok else 3,
                detail=(
                    f"project_id={project_id}; early_approval=true; status={status}; "
                    f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
                ),
            )
            return {"action": "skip",
                    "reason": f"cf-router flyer early approval progress for {project_id}"}
        ok, detail = actions.invoke_update_flyer_project(
            project_id,
            "--revision-text", body,
            "--message-id", message_id,
        )
        revision_requires_clarification = False
        clarification_reason = ""
        pending_confirmation_message = ""
        try:
            import json
            update_doc = json.loads(detail)
            patch = update_doc.get("revision_patch") or {}
            revision_requires_clarification = bool(update_doc.get("revision_requires_clarification"))
            clarification_reason = str(patch.get("unresolved_reason") or "I could not match that change to the queued edit.")
            pending_confirmation_message = str(patch.get("pending_confirmation_message") or "")
        except Exception:
            revision_requires_clarification = not ok
            clarification_reason = detail[:180] or "I could not save that correction."
        if revision_requires_clarification:
            if pending_confirmation_message.strip():
                reply = pending_confirmation_message.strip()
            else:
                # Outcome-only customer copy (mirrors send_flyer_manual_edit_ack
                # tone landed in PR #140). Project ID stays in the audit row's
                # `detail` field below; the customer reply asks the specific
                # clarification question without leaking the internal project
                # identifier. clarification_reason is preserved because it IS
                # the useful customer-facing signal.
                excerpt = _flyer_request_excerpt_for_reply(body)
                seen_line = f"\n\nI saw: {excerpt}" if excerpt else ""
                reply = (
                    "Flyer Studio\n"
                    "------------\n"
                    f"I need one clarification before adding that: {clarification_reason}\n\n"
                    "Please send the exact text, item, price, date, or area of the flyer to change."
                    f"{seen_line}"
                )
        else:
            # Outcome-only success copy for the queued-followup path. Mirrors
            # send_flyer_manual_edit_ack from PR #140: confirms receipt of the
            # additional correction and promises delivery, without leaking
            # "Project {project_id} ... queued for a source-preserving edit"
            # workflow internals. Audit row below still captures project_id
            # + queued_followup=true for operator/Cockpit triage.
            reply = (
                "Flyer Studio\n"
                "------------\n"
                "Got it. I've added this to the careful flyer edit. "
                "I'll send the updated flyer here once it's ready."
            )
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id,
            reply,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "manual_edit.acknowledged",
            ),
        )
        actions.audit_intercepted(
            reason="flyer_reference_exact_edit_queued" if ok and ack_ok else "flyer_primary_failed",
            chat_id=chat_id,
            subprocess_rc=0 if ok and ack_ok else 3,
            detail=(
                f"project_id={project_id}; queued_followup=true; "
                f"revision_requires_clarification={revision_requires_clarification}; sender_role={role}; "
                f"update={detail[:250]}; ack_message_id={mid}; ack_error={err[:300]}"
            ),
        )
        return {"action": "skip",
                "reason": f"cf-router flyer exact edit already queued for {project_id}"}

    # PR-β.1 2026-05-26 — "send now" treated as approval-equivalent for the
    # pending-revision-confirmation reminder: same intent ("commit / send the
    # current design"), so the same reminder fires when a revision proposal
    # is pending.
    if actions.is_flyer_approval_text(body) or actions.is_flyer_send_now_intent(body):
        pending = active_project.get("pending_revision_confirmation") or {}
        pending_revision_id = str(pending.get("revision_id") or "")
        if pending_revision_id:
            reminder = (
                "Flyer Studio\n"
                "------------\n"
                "You have a pending change proposal.\n\n"
                f"Reply APPLY {pending_revision_id} to regenerate a new preview, then reply APPROVE for final files."
            )
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id, reminder,
                action_context=build_action_context(
                    action_id="flyer.project.pending_revision_confirmation_reminder",
                    is_regulated_action=False,
                ),
            )
            actions.audit_intercepted(
                reason="flyer_pending_revision_confirmation_reminder" if ack_ok else "flyer_primary_failed",
                chat_id=chat_id,
                subprocess_rc=0 if ack_ok else 3,
                detail=f"project_id={project_id}; pending_revision_confirmation=true; sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}",
            )
            return {"action": "skip", "reason": f"cf-router flyer active: pending revision confirmation for {project_id}"}
    # PR-β.1 2026-05-26 — "send now" / "please send my flyer now" routed
    # through the SAME approval/finalization safe path as bare "approve"
    # when the active project is in a finalizable state. Same status gates,
    # same concept-regeneration branch, same finalize_and_send_flyer call,
    # same audit. Customer's intent matches: "go ahead and send."
    # P0 #2 — `delivered_with_warning` added to the approval-route allowlist.
    # Design §2 Q1 resolution: warn-tier "OK"/"approve"/"send now" routes via
    # awaiting_final_approval (NOT directly to delivered) — preserves the
    # finalize-flyer-assets contract. The FLYER_TRANSITIONS edge
    # delivered_with_warning → awaiting_final_approval (Commit 1) is what
    # makes the transition at line ~3354 below succeed.
    final_delivery_retry_intent = (
        actions.is_flyer_approval_text(body)
        or actions.is_flyer_send_now_intent(body)
        or actions.is_flyer_delivery_state_intent(body)
    )
    if final_delivery_retry_intent and status == "finalizing_assets":
        ok, detail = actions.retry_send_flyer_package(chat_id, project_id, message_id)
        actions.audit_intercepted(
            reason="flyer_primary_project_created" if ok else "flyer_primary_failed",
            chat_id=chat_id,
            subprocess_rc=0 if ok else 2,
            detail=(
                f"project_id={project_id}; retry_finalizing_assets=true; "
                # Stable marker so recovery.classify_decision ingests this
                # regardless of what the underlying script's prose says. The
                # project stays in finalizing_assets, so without it a failure
                # that never mentions `exit=` ages there unobserved.
                f"{'' if ok else 'delivery_send_failed=true; '}"
                f"sender_role={role}; message_id={message_id}; {detail[:500]}"
            ),
            binding_source=binding_source,
        )
        if ok:
            return {"action": "skip",
                    "reason": f"cf-router flyer active: retried final delivery for {project_id}"}
        fail_ack_ok, fail_mid, fail_err = _send_flyer_final_delivery_failed_ack(
            chat_id, project_id,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "finalization.failed_ack",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_primary_failed",
            chat_id=chat_id,
            subprocess_rc=0 if fail_ack_ok else 3,
            detail=(
                f"project_id={project_id}; retry_finalizing_assets_ack=true; "
                f"sender_role={role}; retry_detail={detail[:300]}; "
                f"ack_message_id={fail_mid}; ack_error={fail_err[:300]}"
            ),
        )
        return {"action": "skip",
                "reason": f"cf-router flyer active: final delivery retry failed for {project_id}"}

    if (actions.is_flyer_approval_text(body) or actions.is_flyer_send_now_intent(body)) and status in {"revising_design", "awaiting_final_approval", "delivered_with_warning"}:
        if status == "revising_design" and not active_project.get("concepts"):
            gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
            if gen_ok:
                ack_ok, outbound_message_id, ack_err = actions._dispatch_concept_preview_send(chat_id, project_id)
                actions.audit_intercepted(
                    reason="flyer_primary_project_created", chat_id=chat_id,
                    subprocess_rc=0 if ack_ok else 3,
                    detail=f"project_id={project_id}; approve_regenerated=true; sender_role={role}; ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}",
                )
                return {"action": "skip",
                        "reason": f"cf-router flyer active: regenerated revised design for {project_id}"}
            actions.audit_intercepted(
                reason="flyer_primary_failed", chat_id=chat_id,
                subprocess_rc=2, detail=f"project_id={project_id}; revision_regeneration_failed={gen_detail[:400]}",
            )
            if actions.flyer_generation_queued_manual_review(gen_detail):
                fail_ack_ok, fail_mid, fail_err = actions.send_flyer_manual_review_ack(
                    chat_id,
                    project_id,
                    body,
                    reason=gen_detail,
                    action_context=build_action_context_for_command(
                        PROJECT_ACTIONS, "manual_review.queued",
                        is_regulated_action=False,
                    ),
                )
            else:
                fail_ack_ok, fail_mid, fail_err = _send_flyer_regeneration_failed_ack(
                    chat_id, project_id,
                    action_context=build_action_context_for_command(
                        PROJECT_ACTIONS, "generation.failed_ack",
                        is_regulated_action=False,
                    ),
                )
            actions.audit_intercepted(
                reason="flyer_reference_exact_edit_queued" if fail_ack_ok else "flyer_primary_failed",
                chat_id=chat_id,
                subprocess_rc=0 if fail_ack_ok else 3,
                detail=(
                    f"project_id={project_id}; approve_regeneration_failed=true; "
                    f"sender_role={role}; gen_detail={gen_detail[:300]}; "
                    f"ack_message_id={fail_mid}; ack_error={fail_err[:300]}"
                ),
            )
            return {"action": "skip",
                    "reason": f"cf-router flyer active: regeneration failed for {project_id}"}
        # P0 #2 — `delivered_with_warning` joins the source-state transition
        # path for "OK"/"approve": same invoke_update_flyer_project call to
        # transition to awaiting_final_approval, then the shared finalize
        # path below picks up. FLYER_TRANSITIONS edge from Commit 1 makes
        # this allowed; without that edge the update-state call would fail.
        if status in {"revising_design", "delivered_with_warning"}:
            ok_status, status_detail = actions.invoke_update_flyer_project(project_id, "--status", "awaiting_final_approval")
            if not ok_status:
                actions.audit_intercepted(
                    reason="flyer_primary_failed", chat_id=chat_id,
                    subprocess_rc=2, detail=f"project_id={project_id}; approve_status_failed={status_detail[:400]}",
                )
                return None
        manual_completed = (active_project.get("manual_review") or {}).get("status") == "completed"
        manual_access = "none"
        if manual_completed:
            manual_access, quota_result = _reserve_flyer_access_or_reply(
                chat_id, phone, project_id, message_id,
                consume_quota=True,
            )
            if quota_result is not None:
                return quota_result
        ok, detail = actions.finalize_and_send_flyer(chat_id, project_id, message_id)
        if ok and manual_completed:
            access_ok, access_detail = _finalize_flyer_access_checked(manual_access, chat_id, phone, project_id, message_id)
            if not access_ok:
                detail = f"{detail}; manual_access_finalize_failed={access_detail[:250]}"
        elif manual_completed:
            release_ok, release_detail = _release_flyer_access(manual_access, chat_id, phone, project_id, message_id)
            if not release_ok:
                detail = f"{detail}; manual_access_release_failed={release_detail[:250]}"
        actions.audit_intercepted(
            reason="flyer_primary_project_created" if ok else "flyer_primary_failed",
            chat_id=chat_id, subprocess_rc=0 if ok else 2,
            # `finalize_failed=true` is the stable marker recovery.classify_decision
            # keys on; see the delivery-retry site above for why prose alone was
            # not enough.
            detail=(
                f"project_id={project_id}; approve=true; "
                f"{'' if ok else 'finalize_failed=true; '}"
                f"binding_source={binding_source}; sender_role={role}; {detail[:500]}"
            ),
            binding_source=binding_source,
        )
        if ok:
            return {"action": "skip",
                    "reason": f"cf-router flyer active: finalized {project_id}"}
        fail_ack_ok, fail_mid, fail_err = _send_flyer_finalization_failed_ack(
            chat_id, project_id,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "finalization.failed_ack",
                is_regulated_action=False,
            ),
        )
        actions.audit_intercepted(
            reason="flyer_primary_failed",
            chat_id=chat_id,
            subprocess_rc=0 if fail_ack_ok else 3,
            detail=(
                f"project_id={project_id}; approve_finalization_failed=true; "
                f"sender_role={role}; finalize_detail={detail[:300]}; "
                f"ack_message_id={fail_mid}; ack_error={fail_err[:300]}"
            ),
        )
        return {"action": "skip",
                "reason": f"cf-router flyer active: finalization failed for {project_id}"}

    # PR-β.1 2026-05-26 — exclude "send now" from the revision-text fallback.
    # Without this exclusion, "send now" + status="delivered" would be
    # mis-classified as a revision instruction (status="delivered" reaches
    # this branch because the finalization gate above only matches
    # revising_design / awaiting_final_approval). With the exclusion,
    # "send now" + delivered falls through active-project intercept and
    # is caught by PR-β's _try_flyer_delivery_state_guard which surfaces
    # the existing flyer_project_status_reply via
    # find_latest_flyer_project_for_status_by_sender.
    # P0 #2 — `delivered_with_warning` joins the revision-text fallback.
    # Customer replies that aren't approval/send-now on a warn-tier project
    # are revision instructions; route via invoke_update_flyer_project to
    # capture them. FLYER_TRANSITIONS edge delivered_with_warning →
    # revising_design (Commit 1) takes effect downstream of this capture.
    if (
        (
            status in {"revising_design", "awaiting_final_approval", "delivered", "delivered_with_warning"}
            or (status == "awaiting_concept_selection" and _looks_like_active_flyer_revision(body))
        )
        and body
        and not actions.is_flyer_send_now_intent(body)
    ):
        ok, detail = actions.invoke_update_flyer_project(
            project_id,
            "--revision-text", body,
            "--message-id", message_id,
        )
        revision_requires_clarification = False
        clarification_reason = ""
        pending_confirmation_message = ""
        try:
            import json
            update_doc = json.loads(detail)
            patch = update_doc.get("revision_patch") or {}
            revision_requires_clarification = bool(update_doc.get("revision_requires_clarification"))
            clarification_reason = str(patch.get("unresolved_reason") or "I could not match that change to the current flyer details.")
            pending_confirmation_message = str(patch.get("pending_confirmation_message") or "")
        except Exception:
            revision_requires_clarification = not ok
            clarification_reason = detail[:180] or "I could not apply that revision."
        # Reload the project the revision was actually applied to. When the
        # newest-updated picker resolves a DIFFERENT row (quoted binding chose
        # an older project, or a newer project appeared concurrently), the
        # regen decision must read the bound project's row by id instead.
        active_after = actions.find_active_flyer_project_by_sender(phone, chat_id) or {}
        if str(active_after.get("project_id") or "") != project_id:
            active_after = actions._load_flyer_project_dict(project_id) or {}
        needs_regen = not active_after.get("concepts")
        # PR-ζ.1b §13.C — split into 3 distinct sends, each with its own
        # action_context. Operator decision: clarification, regeneration-
        # started, and revision-noted are different customer intents and
        # should not share one generic revision action_id.
        if revision_requires_clarification:
            if pending_confirmation_message.strip():
                ack_message = pending_confirmation_message.strip()
            else:
                excerpt = _flyer_request_excerpt_for_reply(body)
                seen_line = f"\n\nI saw: {excerpt}" if excerpt else ""
                ack_message = (
                    f"I need one clarification before regenerating: {clarification_reason}. "
                    f"Please send the exact item or text to change.{seen_line}"
                )
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id, ack_message,
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "clarification.request",
                    is_regulated_action=False,
                ),
            )
        elif ok and needs_regen:
            ack_message = "Revision applied to the flyer details. I am regenerating the design now."
            # PR-ζ.1b PR #282 review fix — sub-branch (b) of §13.C 3-way split:
            # the "Revision applied" claim is verified by `ok` being True from
            # actions.invoke_update_flyer_project (subprocess that performed the
            # local-state mutation). verified_action_result=True is the
            # purpose-built chokepoint escape for completion verbs backed by a
            # verified action result. Regression test:
            # tests/test_revision_ack_chokepoint_lint.py.
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id, ack_message,
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "edit.processing",
                    verified_action_result=True,
                ),
            )
        else:
            ack_message = "Revision noted. I will keep it with this flyer project. Reply APPROVE when ready for final files."
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id, ack_message,
                action_context=build_action_context_for_command(
                    PROJECT_ACTIONS, "project.reply",
                ),
            )
        regeneration_failed = False
        if ok and needs_regen and not revision_requires_clarification:
            gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
            if gen_ok:
                preview_ok, preview_mid, preview_err = actions._dispatch_concept_preview_send(chat_id, project_id)
                mid = ",".join(x for x in [mid, preview_mid] if x)
                ack_ok = ack_ok and preview_ok
                if preview_err:
                    err = f"{err}; preview_error={preview_err}"
            else:
                regeneration_failed = True
                if actions.flyer_generation_queued_manual_review(gen_detail):
                    fail_ack_ok, fail_mid, fail_err = actions.send_flyer_manual_review_ack(
                        chat_id,
                        project_id,
                        body,
                        reason=gen_detail,
                        action_context=build_action_context_for_command(
                            PROJECT_ACTIONS, "manual_review.queued",
                            is_regulated_action=False,
                        ),
                    )
                else:
                    fail_ack_ok, fail_mid, fail_err = _send_flyer_regeneration_failed_ack(
                        chat_id, project_id,
                        action_context=build_action_context_for_command(
                            PROJECT_ACTIONS, "generation.failed_ack",
                            is_regulated_action=False,
                        ),
                    )
                mid = ",".join(x for x in [mid, fail_mid] if x)
                ack_ok = False
                err = f"{err}; regeneration_failed={gen_detail[:300]}"
                if fail_err:
                    err = f"{err}; failure_ack_error={fail_err[:300]}"
                if fail_ack_ok:
                    err = f"{err}; failure_ack_sent=true"
        actions.audit_intercepted(
            reason=(
                "flyer_reference_exact_edit_queued"
                if regeneration_failed else ("flyer_primary_project_created" if ok else "flyer_primary_failed")
            ),
            chat_id=chat_id, subprocess_rc=0 if ok and ack_ok else 2,
            detail=f"project_id={project_id}; revision=true; binding_source={binding_source}; revision_requires_clarification={revision_requires_clarification}; update={detail[:250]}; ack_message_id={mid}; ack_error={err}",
            binding_source=binding_source,
        )
        return {"action": "skip",
                "reason": f"cf-router flyer active: revision captured for {project_id}"}

    if status in {"intake_started", "collecting_required_info", "awaiting_assets"} and body:
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id,
            (
                "Flyer Studio\n"
                "------------\n"
                "I have your flyer request open. Please send the full flyer request in one message "
                "or send any logo/photos you want included. If this is a new flyer, start with "
                "\"Create flyer\" and the offer or event details."
            ),
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "intake.processing",
            ),
        )
        actions.audit_intercepted(
            reason="flyer_primary_project_created" if ack_ok else "flyer_primary_failed",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=f"project_id={project_id}; intake_reply=true; sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}",
        )
        return {"action": "skip",
                "reason": f"cf-router flyer active: intake reply captured for {project_id}"}

    return None


def _parse_headcount_from_signals(signals: list[str]) -> Optional[int]:
    """Extract the int from a `headcount:N` signal emitted by classify_catering.

    PR-CF1d Commit 4 2026-05-12. classify_catering's regex set already finds
    headcount and emits it as a signal (e.g. "headcount:80"); this helper
    parses that out so F7 primary can forward it into the lead's
    extracted_fields. Returns None if no headcount signal is present or
    parsing fails. Defensive against malformed signals.
    """
    for sig in signals or []:
        if isinstance(sig, str) and sig.startswith("headcount:"):
            try:
                return int(sig.split(":", 1)[1])
            except (ValueError, IndexError):
                return None
    return None


def _parse_month_day_event_date(text: str) -> Optional[str]:
    """Month-NAME event date -> ISO. Delegates to the shared platform grammar.

    DELIBERATELY the NARROW parser: this is what `_inbound_event_identity` feeds
    the fresh-vs-stale discriminator, whose rules are ruled binding. The widened
    grammar (numeric "6/15" dates) lives in catering_extraction.parse_event_date
    and is reached only through the extractor, so which follow-ups count as
    contradicting a different event is byte-identical to the deployed behavior.
    """
    return catering_extraction.parse_month_day_event_date(text)


def _extract_catering_fields_from_text(text: str, signals: list[str]) -> Optional[dict]:
    """Deterministic extracted-fields dict for an inbound. Delegates to the shared
    platform module so `amend-catering-lead` (a script, which cannot import this
    hyphenated plugin package) parses amendments with the EXACT same grammar the
    lead was created with — two copies would drift, and the amendment path exists
    to keep the lead in agreement with what the customer said."""
    return catering_extraction.extract_catering_fields(text, signals or [])


def _lead_id_from_create_detail(detail: str) -> str:
    try:
        doc = json.loads(detail or "{}")
    except json.JSONDecodeError:
        match = re.search(r'"lead_id"\s*:\s*"([^"]+)"', detail or "")
        return match.group(1) if match else ""
    if isinstance(doc, dict):
        return str(doc.get("lead_id") or "")
    return ""


def _maybe_generate_catering_proposals_for_new_lead(
    *, text: str, chat_id: str, message_id: str, detail: str,
) -> None:
    if not actions.is_proposal_request(text):
        return
    lead_id = _lead_id_from_create_detail(detail)
    if not lead_id:
        actions.audit_intercepted(
            reason="error",
            chat_id=chat_id,
            detail="f7_new_inquiry_proposal_requested_but_lead_id_missing",
        )
        return
    rc = actions.invoke_create_catering_proposals(lead_id, chat_id, message_id, text)
    actions.audit_intercepted(
        reason="f7_proposal_request",
        chat_id=chat_id,
        subprocess_rc=rc,
        detail=f"new {lead_id}; proposal request handled by cf-router",
    )


def _sender_has_qualifying_lead(chat_id: str) -> bool:
    """True when this sender has an OPEN lead parked mid-intake (QUALIFYING).

    M1 admission widening. The F7 gate admits on catering signals, but a slot-filling
    ANSWER carries none — "Grand Ballroom" or "about 80" classifies as nothing, so
    without this the reply to our own question would go to the LLM and the intake
    loop would silently break at question one. Only consulted when the cheap signal
    checks have already failed. Never raises (a lookup failure falls back to the
    pre-M1 admission set).

    Ordered cheap-first: identity resolution spawns `identify-sender`, so a store
    with no QUALIFYING lead at all short-circuits on one file read rather than adding
    a subprocess to every non-catering inbound.
    """
    try:
        if not actions.any_qualifying_lead_exists():
            return False
        phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
        if role == "owner":
            return False
        lead = actions.find_active_catering_lead_by_sender(phone, chat_id)
        return bool(lead) and lead.get("status") == "QUALIFYING"
    except Exception:  # noqa: BLE001 — admission widening must never break routing
        return False


def _has_f7_followup_signal(signals: list[str]) -> bool:
    """Return True when weak catering signals are enough for Branch B only.

    New-inquiry lead creation remains gated by `classify_catering=True`.
    Once an active lead exists for the sender, menu/event/food/headcount
    references should reach Branch B even if they are too weak to create a
    brand-new lead.
    """
    for sig in signals or []:
        if sig in {"food_keyword", "event_keyword", "delivery_keyword"}:
            return True
        if isinstance(sig, str) and (
            sig.startswith("headcount:") or sig.startswith("primary:catering")
        ):
            return True
    return False


def _should_start_new_lead_over_active(active_lead: dict, signals: list[str]) -> bool:
    """Return True when a strong new inquiry should not attach to old state."""
    if active_lead.get("status") not in {"CUSTOMER_FINALIZED", "OWNER_APPROVED"}:
        return False
    has_primary = any(sig.startswith("primary:catering") for sig in signals or [])
    has_headcount = any(sig.startswith("headcount:") for sig in signals or [])
    has_event = "event_keyword" in (signals or [])
    has_delivery = "delivery_keyword" in (signals or [])
    return has_primary and (has_headcount or has_event or has_delivery)


def _create_catering_lead_from_inbound(
    *, text: str, chat_id: str, message_id: str, signals: list[str],
    phone: Optional[str],
) -> Optional[dict]:
    """Invoke create-catering-lead for the deterministic F7 Branch A path."""
    if phone:
        customer_phone_arg = phone
    elif chat_id.endswith("@lid"):
        customer_phone_arg = "+" + chat_id[: -len("@lid")]
    else:
        return None

    extracted = _extract_catering_fields_from_text(text, signals or [])

    # Turn-arbitration 2026-07-26: when this new inquiry also asks for proposals we
    # immediately generate the tiered options below, so suppress create-catering-lead's
    # F14 sample menu — the generated set is the customer's SINGLE bounded response.
    # A new inquiry WITHOUT a proposal ask keeps the F14 menu as its one response.
    will_generate_proposals = actions.is_proposal_request(text)

    # M1 qualification gate. Gated ONLY when we are not about to generate proposals:
    # turn arbitration allows the customer exactly ONE bounded response per inbound,
    # and a proposal ask already claims it. A gated create sends the question batch
    # instead of the F14 sample menu — still one send.
    ok, detail = actions.trigger_create_catering_lead(
        customer_phone=customer_phone_arg,
        customer_name="",
        raw_inquiry=text,
        message_id=message_id,
        extracted_fields=extracted,
        suppress_customer_ack=will_generate_proposals,
        qualification_gate=f7_qualification_gate_enabled() and not will_generate_proposals,
    )
    actions.audit_intercepted(
        reason="f7_primary_new_inquiry", chat_id=chat_id,
        subprocess_rc=0 if ok else 2, detail=detail[:500],
    )
    if not ok:
        return None
    _maybe_generate_catering_proposals_for_new_lead(
        text=text,
        chat_id=chat_id,
        message_id=message_id,
        detail=detail,
    )
    return {"action": "skip",
            "reason": "cf-router F7 primary: catering inquiry routed deterministically"}


# ── PR-A fresh-vs-stale discriminator (deterministic identity comparison) ─────
# A fresh inquiry that contradicts the open lead on event date or headcount is a
# DIFFERENT event, not a follow-up. Reuses the deployed deterministic extractors
# (_parse_month_day_event_date + classify_catering's headcount signal). Venue is
# intentionally omitted — no deployed venue pattern exists, and a fragile venue
# regex would do more harm than good — so the comparison is date + headcount only.
_HEADCOUNT_CONTRADICTION_TOLERANCE = 5


def _inbound_event_identity(text: str, signals: list[str]) -> tuple[Optional[str], Optional[int]]:
    """Deterministically extract (event_date ISO, headcount) from the inbound."""
    return (
        _parse_month_day_event_date(text),
        _parse_headcount_from_signals(signals or []),
    )


def _material_contradiction(inbound_date: Optional[str], inbound_headcount: Optional[int],
                            lead: dict) -> bool:
    """True when the inbound MATERIALLY contradicts the open lead on an identity
    field the lead actually has set: a different event date, or a headcount
    differing beyond a small tolerance. A field absent on either side is never a
    contradiction (absence is not disagreement)."""
    extracted = (lead or {}).get("extracted") or {}
    lead_date = extracted.get("event_date")
    lead_headcount = extracted.get("headcount")
    if inbound_date and lead_date and inbound_date != lead_date:
        return True
    if (inbound_headcount is not None and lead_headcount is not None
            and abs(inbound_headcount - lead_headcount) > _HEADCOUNT_CONTRADICTION_TOLERANCE):
        return True
    return False


def _send_fresh_inquiry_clarification(chat_id: str, lead_id: str) -> bool:
    """One clarification for an inquiry-shaped follow-up that neither clearly
    contradicts nor amends the open lead (PR-A ambiguous branch). Hard-coded one-
    liner via the existing send-catering-ack subprocess — no new template system,
    no LLM, no lead mutation. Mirrors send_canonical_followup_reply's shape."""
    import subprocess

    template = (
        f"Just to confirm — is this about your existing inquiry {lead_id}, or a "
        f"new event? Reply and I'll route it to the right one."
    )
    try:
        result = subprocess.run(
            [
                str(actions.SEND_CATERING_ACK_BIN),
                "--customer-jid", chat_id,
                "--message-text", template,
                "--lead-id", lead_id,
            ],
            capture_output=True, text=True,
            timeout=actions.SUBPROCESS_TIMEOUT_SEC,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _open_fresh_lead_over_stale(*, text: str, chat_id: str, message_id: str,
                                signals: list[str], phone: Optional[str],
                                prior_lead_id: str,
                                suppress_customer_ack: bool = False) -> Optional[str]:
    """Open a NEW catering lead for a fresh inquiry whose event identity is
    DETERMINISTICALLY DISTINCT from an older open lead (date/headcount contradiction),
    via the existing create-catering-lead path (idempotent on (customer_phone,
    message_id) inside that script). Returns the new lead_id on success, else None so the
    caller falls through to the durable R2A capture (the message is never lost).
    Emits `f7_fresh_inquiry_new_lead_over_stale`.

    Turn-arbitration 2026-07-26: a distinct event is created WITHOUT a "is this a separate
    event?" question — the deterministic date/headcount contradiction already establishes a
    different event, so no clarification is warranted (the clarification is reserved for the
    genuinely AMBIGUOUS branch, which creates NO lead). And when the same inbound also asks
    for proposals, `suppress_customer_ack` skips create-catering-lead's F14 sample menu so
    the immediately-generated tiered options are the customer's SINGLE bounded response —
    not an F14 menu AND a cross-reference AND a tiered set as three independent sends."""
    if phone:
        customer_phone_arg = phone
    elif chat_id.endswith("@lid"):
        customer_phone_arg = "+" + chat_id[: -len("@lid")]
    else:
        return None

    extracted = _extract_catering_fields_from_text(text, signals or [])
    ok, detail = actions.trigger_create_catering_lead(
        customer_phone=customer_phone_arg,
        customer_name="",
        raw_inquiry=text,
        message_id=message_id,
        extracted_fields=extracted,
        suppress_customer_ack=suppress_customer_ack,
        # Same one-bounded-response rule as Branch A: when a proposal set is about
        # to be generated for this new lead it IS the turn's response, so the
        # qualification questions must not also fire.
        qualification_gate=f7_qualification_gate_enabled() and not suppress_customer_ack,
    )
    new_lead_id = _lead_id_from_create_detail(detail) if ok else ""
    if not ok or not new_lead_id:
        actions.audit_intercepted(
            reason="f7_fresh_inquiry_new_lead_over_stale", chat_id=chat_id,
            subprocess_rc=0 if ok else 2,
            detail=(f"create-over-stale {prior_lead_id} incomplete "
                    f"(ok={ok}); {detail[:400]}"),
        )
        return None
    actions.audit_intercepted(
        reason="f7_fresh_inquiry_new_lead_over_stale", chat_id=chat_id,
        subprocess_rc=0,
        detail=(f"new {new_lead_id} over stale {prior_lead_id}; fresh inquiry "
                f"contradicts open lead identity; distinct event, no clarification; "
                f"LLM bypassed"),
    )
    return new_lead_id


def _generate_proposals_deterministically(
    *, lead_id: str, chat_id: str, message_id: str, text: str,
    approval_code: str, detail: str,
) -> Optional[dict]:
    """Generate proposal options for an active lead via the DETERMINISTIC
    menu-grounded script (create-catering-proposal-options --auto-generate-from-menu)
    and return the F7 skip result.

    PR-B2 2026-07-21 — this restores the pre-PR-A F7_PROPOSAL_BRANCH generation
    path (which had live evidence of working) after PR-A moved active-lead
    generation onto the Hermes creative path (which spiraled to 28 sends and
    returned "unable to process" on the real gpt-4o-mini gateway). NO LLM composes
    the menu: the script builds it from catering-menu.json. Same shape as the
    pre-PR-A branch — a handled rc ({0,2,4,6,11}: ok / invalid-input / not-found /
    bridge-down / truth-guard, all cases the script has already owned incl. owner
    notification) skips the LLM; any other rc returns None so the LLM can surface
    the failure. Mirrors invoke_select_catering_proposal's rc guard."""
    rc = actions.invoke_create_catering_proposals(lead_id, chat_id, message_id, text)
    actions.audit_intercepted(
        reason="f7_proposal_request_deterministic_generation",
        chat_id=chat_id, code=approval_code, subprocess_rc=rc,
        detail=detail,
    )
    if rc in {0, 2, 4, 6, 11}:
        return {"action": "skip",
                "reason": f"cf-router F7 primary: proposals generated deterministically for {lead_id}"}
    return None


# F7 ACCEPTANCE ARM (M3). A customer whose quote has been SENT replies "we accept"
# — and before M3 nothing anywhere handled it. SENT_TO_CUSTOMER is deliberately NOT
# in ACTIONABLE_LEAD_STATUSES, so `find_active_catering_lead_by_sender` returns
# None, F7 Branch B never runs, and the message either created a SPURIOUS SECOND
# LEAD (if it happened to classify as catering) or went to the LLM. Neither books
# anything, so the booking existed nowhere.
#
# This arm needs its OWN admission because the F7 gate above admits on catering
# signals and "we accept" carries none — the same reason M1 had to add the
# QUALIFYING lead lookup for slot-filling answers.
#
# Cost discipline on the hot path, cheapest first: a pure-regex detector that
# returns None for virtually every inbound, THEN one tolerant file read, and only
# then the `identify-sender` subprocess. A bare "ok" is common, so the file-read
# gate matters.
#
# Armed by CATERING_ACCEPTANCE_ARM=1 — DEFAULT OFF (see f7_acceptance_arm_enabled).


def _try_catering_acceptance_intercept(
    text: str, chat_id: str, event: Any, *, flyer_generation_enabled: bool = False,
) -> Optional[dict]:
    """Record a customer's explicit accept/decline of a quote already sent to them.

    Returns a skip dict when the acceptance writer handled the turn, else None so
    the inbound takes its existing route completely unchanged.

    Precedence deliberately CEDED, in order:
      * amendment-phrased text ("actually we accept but move the date") keeps the
        R2A durable-capture path — the change matters more than the yes, and the
        capture path is where changes are preserved;
      * an owner inbound never books on a customer's behalf (that is
        `mark-catering-lead-outcome`, an owner-identified audit path);
      * a sender with a live flyer project is left to the existing flyer/catering
        routing rather than claimed here — mirrors the same guard the F7 catering
        block applies, so this arm cannot re-open the conflict class R2B-1 closed.
    """
    if not f7_acceptance_arm_enabled():
        return None
    detected = actions.detect_quote_acceptance(text)
    if detected is None:
        return None
    if actions.is_amendment_phrased(text):
        return None
    if not actions.any_post_quote_lead_exists():
        return None

    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        return None
    if flyer_generation_enabled and actions.has_non_delivered_flyer_project_by_sender(
        phone, chat_id
    ):
        return None

    lead = actions.find_post_quote_catering_lead_by_sender(phone, chat_id)
    if not lead:
        return None
    lead_id = lead.get("lead_id", "?")
    message_id = _extract_native_message_id(event) or _extract_message_id(event, chat_id, text)

    rc = actions.invoke_record_catering_acceptance(
        lead_id, chat_id, message_id, text, sender_role=role or "customer",
    )
    actions.audit_intercepted(
        reason="f7_quote_acceptance", chat_id=chat_id,
        code=lead.get("owner_approval_code") or "", subprocess_rc=rc,
        detail=(f"post-quote {lead_id}; detector={detected['outcome']}; "
                f"recorded deterministically by cf-router; LLM bypassed"),
    )
    # 0 = recorded / clarified / replay, 6 = recorded but the reply POST failed,
    # 14 = held (nothing sent, deliberately). All three mutated or deliberately
    # declined to act on state the script owns, so re-running the inbound through
    # the LLM would double-handle it. Anything else (no signal server-side,
    # not-found, schema, illegal transition) falls through untouched.
    if rc in {0, 6, 14}:
        return {"action": "skip",
                "reason": f"cf-router F7: quote acceptance recorded for {lead_id}"}
    return None


def _try_f7_primary_intercept(
    text: str, chat_id: str, event: Any,
    signals: Optional[list[str]] = None,
    allow_new_lead: bool = True,
) -> Optional[dict]:
    """F7 PRIMARY-MODE intercept (PR-CF1d 2026-05-12).

    Caller has already confirmed either `classify_catering(text)` is True
    or weak follow-up signals exist for Branch B. `allow_new_lead` is True
    only for full new-inquiry classification; weak follow-up signals may
    suppress against an existing active lead but must not create one.

    Returns:
      {"action": "skip", "reason": ...} — plugin handled, LLM bypassed
      None                              — sender is owner (F8 territory),
                                          or create-catering-lead
                                          returned non-zero (let LLM see the
                                          inbound for recovery)
    """
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        # Owner catering keywords are F8 territory. Employee-origin catering
        # can still be a private/family customer-side inquiry.
        return None

    active_lead = actions.find_active_catering_lead_by_sender(phone, chat_id)

    if active_lead is None:
        if not allow_new_lead:
            return None
        # Branch A — new inquiry → create lead deterministically, skip LLM.
        # `customer_name=""` kills the L0004-L0007 "Anjali Iyer" hallucination
        # class (regex-based extractor can't invent names from training data).
        return _create_catering_lead_from_inbound(
            text=text, chat_id=chat_id, message_id=message_id,
            signals=signals or [], phone=phone,
        )

    # Branch B — active lead exists → suppress follow-up, optionally reply.
    #
    # KNOWN GAP (documented at PR-CF1d review, 2026-05-12): follow-up content
    # is dropped silently. If a customer sends an amendment (e.g. "actually
    # 280 people not 235", "switch to vegetarian only", "move the date to
    # July 19th"), the canonical reply is sent but the amendment text is
    # not parsed or attached to the lead. Owner then approves against the
    # ORIGINAL lead state. The customer sees "Your inquiry is with the
    # owner" and reasonably assumes their correction was received. The
    # owner approves 235 guests when the customer expected 280. This is a
    # data-integrity gap, not just a UX issue.
    #
    # Cheapest fix is a second regex pass on `text` here for amendment
    # signals (headcount/date/dietary), invoking a future `amend-catering-
    # lead` script that merges the new extracted fields into the existing
    # lead and re-issues the owner card. Track via amendment-drop
    # complaints from operators; cf. tasks/cf-router-f7-primary-mode-
    # plan.md §"Risks + rollback" and the PR-CF1d reviewer feedback.
    lead_id = active_lead.get("lead_id", "?")
    approval_code = active_lead.get("owner_approval_code") or ""

    if allow_new_lead and _should_start_new_lead_over_active(active_lead, signals or []):
        result = _create_catering_lead_from_inbound(
            text=text, chat_id=chat_id, message_id=message_id,
            signals=signals or [], phone=phone,
        )
        if result is not None:
            return result

    # Compound-intent-aware SELECTION — turn-arbitration 2026-07-26. Runs BEFORE the
    # proposal-request-regeneration path. When a SENT selectable set already exists for
    # the lead, a message that NAMES a specific option is a SELECTION, not a request to
    # regenerate menus. This catches the compound "I like Option 2. Can you send me quote
    # and prices." that is_proposal_selection misses ("like" is not an action verb) and
    # is_proposal_request then mis-reads as a regeneration ask. Recording the selection
    # ONCE (via select-catering-proposal) acknowledges it and advances to the
    # deterministic owner-approval/quote path — "send me quote/prices" is a pricing-
    # advance signal, not a new-proposal request; the menus are NOT re-sent. An explicit
    # action-verb selection still wins; the weaker NAMES signal excludes amendment-phrased
    # text ("actually make it premium instead" keeps the R2A capture path) and mix-and-
    # match recompose (which reaches the escape branch below).
    if F7_PROPOSAL_BRANCH_ENABLED:
        selectable = actions.find_selectable_proposal_set(lead_id)
        names_option = (
            not actions.is_amendment_phrased(text)
            and actions.names_proposal_option(text)
        )
        if (selectable and not actions.is_mix_and_match_request(text)
                and (actions.is_proposal_selection(text) or names_option)):
            rc = actions.invoke_select_catering_proposal(
                lead_id, chat_id, message_id, text,
            )
            actions.audit_intercepted(
                reason="f7_proposal_selection", chat_id=chat_id,
                code=approval_code, subprocess_rc=rc,
                detail=f"active {lead_id}; selection handled by cf-router",
            )
            if rc in {0, 2, 4, 6, 11}:
                return {"action": "skip",
                        "reason": f"cf-router F7 proposal selection for {lead_id}"}
            return None

    # PR-A 2026-07-21 + PR-B2 2026-07-21 — fresh-vs-stale discriminator +
    # DETERMINISTIC proposal generation, BEFORE the durable follow-up capture. This
    # is the L0017 13:59 fix: a fresh inquiry + proposal request against a stale
    # AWAITING_OWNER_APPROVAL lead was captured as a follow-up
    # (`f7_primary_followup_suppressed`) and never generated proposals. PR-A first
    # routed generation to the Hermes creative path; PR-B2 reversed that (no live
    # evidence it worked — the real gpt-4o-mini gateway spiraled to 28 sends) and
    # took PLAIN generation back onto the deterministic --auto-generate-from-menu
    # script. Gated by F7_PROPOSAL_BRANCH_ENABLED so flag-off is a clean rollback to
    # pre-PR-A suppression. Amendment-phrased follow-ups (update/change/revise/
    # instead/actually/make it) are EXCLUDED here and keep the R2A capture path.
    #
    #   contradiction (fresh inquiry vs open lead date/headcount) → open a NEW lead
    #     (f7_fresh_inquiry_new_lead_over_stale). The contradiction already makes the
    #     event distinct, so NO "is this a separate event?" question is sent (turn-
    #     arbitration 2026-07-26). If the SAME message is also a proposal request,
    #     GENERATE proposals deterministically against the NEW lead — with the create's
    #     F14 sample menu SUPPRESSED so the tiered set is the ONE bounded response
    #     (f7_proposal_request_deterministic_generation) — no fall-through to Hermes.
    #   PLAIN proposal request (no contradiction, no mix-and-match) → GENERATE
    #     proposals deterministically against the active lead
    #     (f7_proposal_request_deterministic_generation); the LLM is bypassed.
    #   MIX-AND-MATCH recompose request (combine sections of already-SENT options)
    #     → fall through (return None) to the Hermes catering_dispatcher SKILL, which
    #     routes it to create-catering-proposal-options --recompose-from-sent
    #     (f7_proposal_request_escaped_to_dispatcher). Recompose is a different,
    #     already-deterministic script mode; only plain generation is taken back.
    #   inquiry-shaped but neither contradiction nor proposal request (ambiguous) →
    #     ONE clarification (f7_fresh_inquiry_ambiguous_clarification); no lead, no
    #     capture.
    #   none of the above → fall through to the unchanged R2A durable capture.
    if F7_PROPOSAL_BRANCH_ENABLED and not actions.is_amendment_phrased(text):
        is_inquiry, disc_signals = actions.classify_catering(text)
        proposal_escape = actions.is_proposal_request_escape(text)
        inbound_date, inbound_headcount = _inbound_event_identity(text, disc_signals)
        if is_inquiry and _material_contradiction(inbound_date, inbound_headcount, active_lead):
            # Suppress create-catering-lead's F14 sample menu when we will immediately
            # generate the tiered options — the generated set is the ONE bounded response
            # (turn-arbitration 2026-07-26). No cross-reference question is sent: the
            # contradiction already establishes a distinct event.
            new_lead_id = _open_fresh_lead_over_stale(
                text=text, chat_id=chat_id, message_id=message_id,
                signals=disc_signals, phone=phone, prior_lead_id=lead_id,
                suppress_customer_ack=proposal_escape,
            )
            if new_lead_id is not None:
                if proposal_escape:
                    # A fresh contradicting inquiry has no SENT options to recompose,
                    # so a proposal ask here is always plain generation against the
                    # NEW lead. (rc-unhandled → helper returns None → LLM surfaces it.)
                    return _generate_proposals_deterministically(
                        lead_id=new_lead_id, chat_id=chat_id, message_id=message_id,
                        text=text, approval_code=approval_code,
                        detail=(f"new {new_lead_id} over stale {lead_id}; proposal "
                                f"request generated deterministically against the new "
                                f"lead; LLM bypassed"),
                    )
                return {"action": "skip",
                        "reason": (f"cf-router F7 primary: fresh inquiry {new_lead_id} "
                                   f"opened over stale {lead_id}")}
            # Creation could not complete — fall through to the durable capture below
            # so the inbound is never lost.
        elif (f7_qualification_gate_enabled()
                and active_lead.get("status") == "QUALIFYING"):
            # M1 slot-filling answer. Placed AFTER the contradiction check so the
            # ruled fresh-vs-stale discriminator still owns a message that
            # contradicts a field the lead already has (that is a different event,
            # not an answer) — this arm only ever fills nulls. Placed BEFORE the
            # proposal / ambiguous arms and NOT gated on `is_inquiry`, because a
            # bare answer ("Grand Ballroom") carries no catering signal at all.
            rc = actions.invoke_amend_catering_lead(
                lead_id, mode="answer", answer_text=text,
                message_id=_extract_native_message_id(event),
            )
            actions.audit_intercepted(
                reason="f7_qualification_answer", chat_id=chat_id,
                code=approval_code, subprocess_rc=rc,
                detail=(f"active {lead_id} status=QUALIFYING; slot-filling answer "
                        f"applied by cf-router; LLM bypassed"),
            )
            if rc in {0, 6}:
                # 0 = applied and answered; 6 = applied but a notification send
                # failed. Both mutated state, so re-running the inbound through the
                # LLM would double-handle it. Anything else (not-found, schema,
                # illegal transition) falls through to the durable R2A capture, so
                # the answer is never lost.
                return {"action": "skip",
                        "reason": f"cf-router F7 primary: qualification answer applied to {lead_id}"}
        elif proposal_escape:
            if actions.is_mix_and_match_request(text):
                # Mix-and-match recompose stays with Hermes: the catering_dispatcher
                # SKILL routes it to create-catering-proposal-options
                # --recompose-from-sent, which pulls the named sections VERBATIM from
                # the SENT options. Only PLAIN menu generation is taken back here.
                actions.audit_intercepted(
                    reason="f7_proposal_request_escaped_to_dispatcher",
                    chat_id=chat_id, code=approval_code,
                    detail=(f"active {lead_id}; mix-and-match recompose escaped to "
                            f"Hermes dispatcher; LLM routes to recompose script"),
                )
                return None
            return _generate_proposals_deterministically(
                lead_id=lead_id, chat_id=chat_id, message_id=message_id,
                text=text, approval_code=approval_code,
                detail=(f"active {lead_id}; proposal request generated "
                        f"deterministically from menu; LLM bypassed"),
            )
        elif is_inquiry:
            if F7_PRIMARY_FOLLOWUP_REPLY:
                _send_fresh_inquiry_clarification(chat_id, lead_id)
            actions.audit_intercepted(
                reason="f7_fresh_inquiry_ambiguous_clarification",
                chat_id=chat_id, code=approval_code,
                detail=(f"active {lead_id} status={active_lead.get('status')}; "
                        f"inquiry-shaped follow-up with no contradicting identity "
                        f"fields; one clarification sent; LLM bypassed"),
            )
            return {"action": "skip",
                    "reason": (f"cf-router F7 primary: ambiguous fresh inquiry vs "
                               f"{lead_id} clarified")}

    # PR-R2A 2026-07-19 — durable amendment capture BEFORE the canonical reply.
    # The KNOWN GAP documented above dropped the follow-up TEXT silently. Now the
    # inbound is persisted to the sidecar amendment store FIRST, and the customer
    # only sees the canonical "with the owner" reply once capture has succeeded.
    #
    # Four outcomes, all returning skip (the amendment path NEVER falls through to
    # generic LLM routing or new-lead creation — that would re-expose the
    # fabrication class F7 primary-mode exists to prevent and double-handle the
    # inbound):
    #   captured  → ok, new record  → send UNCHANGED canonical reply
    #   replay    → ok, idempotent  → send UNCHANGED canonical reply, no new record
    #   captured/replay both audit `f7_primary_followup_suppressed`.
    #   capture_failed → not-ok → send a deterministic RETRY reply (never the
    #     canonical reply — that would falsely imply the update was recorded) and
    #     audit `f7_primary_amendment_capture_failed`.
    # The capture repository holds ONLY the sidecar lock and releases it before
    # returning, so every customer send below happens with no lock held.
    capture = catering_amendments.capture_branch_b_amendment(
        lead=active_lead,
        text=text,
        chat_id=chat_id,
        phone=phone,
        message_id=_extract_native_message_id(event),
        source_transport=_event_transport(event),
        provider_timestamp=_event_provider_timestamp(event),
    )
    if capture.ok:
        # M1 — APPLY what R2A captured. Until this call the sidecar was write-only:
        # the correction was durably stored and the owner still approved the
        # pre-amendment numbers (the data-integrity gap documented above). Runs only
        # on a NEW capture (a replay has nothing new to apply) and only after the
        # capture returned ok, so the sidecar lock is already released. Guarded and
        # fail-safe exactly like the other subprocess invokes on this path: any
        # non-zero rc is audited and the canonical reply still goes out — the
        # amendment stays unapplied in the sidecar and the next application run
        # picks it up, rather than the customer being told nothing was recorded.
        if f7_qualification_gate_enabled() and not capture.idempotent:
            apply_rc = actions.invoke_amend_catering_lead(
                lead_id, mode="amendment",
                message_id=_extract_native_message_id(event),
            )
            actions.audit_intercepted(
                reason="f7_primary_amendment_applied", chat_id=chat_id,
                code=approval_code, subprocess_rc=apply_rc,
                detail=(f"active {lead_id}; amendment {capture.amendment_id} "
                        f"application rc={apply_rc}"),
            )
        # captured OR replay — identical canonical reply, gated by the existing
        # UX flag so silent-suppression mode is preserved exactly as before R2A.
        if F7_PRIMARY_FOLLOWUP_REPLY:
            actions.send_canonical_followup_reply(chat_id, lead_id, _extract_native_message_id(event))
        actions.audit_intercepted(
            reason="f7_primary_followup_suppressed", chat_id=chat_id,
            code=approval_code,
            detail=(f"active {lead_id} status={active_lead.get('status')}; "
                    f"amendment {'replayed' if capture.idempotent else 'captured'} "
                    f"{capture.amendment_id}; LLM bypassed"),
        )
        return {"action": "skip",
                "reason": f"cf-router F7 primary: follow-up to active {lead_id} suppressed"}

    # capture_failed — DO NOT send the canonical reply (it implies the correction
    # was recorded). Send the deterministic retry ask (gated by the same UX flag)
    # and still suppress the LLM. The capture_failed audit row is emitted by the
    # repository AND recorded here, so the loss is never silent even in flag-off
    # silent-suppression mode.
    if F7_PRIMARY_FOLLOWUP_REPLY:
        _send_amendment_retry_reply(chat_id, lead_id)
    actions.audit_intercepted(
        reason="f7_primary_amendment_capture_failed", chat_id=chat_id,
        code=approval_code,
        detail=(f"active {lead_id} status={active_lead.get('status')}; "
                f"capture_failed reason={capture.reason}; retry requested; LLM bypassed"),
    )
    return {"action": "skip",
            "reason": (f"cf-router F7 primary: amendment capture failed for "
                       f"{lead_id}, retry requested")}


def _build_skip_or_passthrough(*, rc: int, chat_id: str, code: str,
                                reason: str, detail: str,
                                action_label: str) -> Optional[dict]:
    """Audit the outcome, then return skip iff apply-script succeeded.

    On non-zero rc, return None so the LLM runs and can surface the
    failure to the owner (e.g. "I tried to approve but the apply script
    returned exit 9 — the lead may already be in a terminal state").
    Audit is best-effort: if it raises, we still return the right value
    (audit_intercepted swallows its own exceptions).
    """
    actions.audit_intercepted(
        reason=reason, chat_id=chat_id, code=code,
        subprocess_rc=rc, detail=detail,
    )
    if rc == 0:
        return {"action": "skip",
                "reason": f"cf-router F8: invoked {action_label} (rc=0)"}
    # Non-zero exit → let LLM handle; owner gets diagnostic feedback
    return None


def _handle_shift_coverage_code(
    *, row: dict, code: str, chat_id: str, text: str, message_id: str,
    has_edit: bool,
) -> Optional[dict]:
    """Apply an owner answer to a Shift coverage proposal, deterministically.

    Closes the RETURN half of the coverage loop. The intake half is wired — a
    verified employee's sick call reaches `handle-shift-sick-call` through F9 —
    and it ends by sending the owner a card carrying a `#XXXXX` code. Until now
    that code resolved to POOL_SHIFT, hit a bare `return None`, and fell through
    to the SKILL dispatcher, which needs the `skills` toolset that is disabled
    on the box. The owner's answer reached nothing at all.

    Ordering is the handle_owner_command SKILL contract and is not negotiable:
    mark the proposal `approved` FIRST, and send only if that transition
    succeeded. Both steps run in Shift-owned scripts — this function never
    writes pending.json, never resolves a phone number, and never composes the
    outbound body.

    Fail-closed in five places: an unaddressable row, a message that is not
    unqualified consent, a proposal that is not `awaiting_owner_approval`, and
    a transition the kernel refuses. None of them send anything.

    A sixth used to live here — a local count refusing when a code named more
    than one proposal. `approval_code_pools.resolve_code` now refuses that case
    itself, for every pool, and does it BEFORE this function is reached: the
    `isinstance(resolved, CollisionResult)` arm in `_try_f8_intercept` catches
    the intra-pool sentinel and also pages the owner, which the local count
    never did. Keeping a second implementation of the pool's document shape
    here is what the registry change exists to end.
    """
    if has_edit:
        # Nothing here is editable — the candidate and the body were fixed when
        # the proposal was rendered. Let the LLM say so rather than approving.
        return None
    if _SHIFT_VERB_RETRY.search(text):
        # RETRY resumes a send_failed proposal (SKILL §3). Not implemented here,
        # and deliberately NOT collapsed into approve: that would re-send a
        # coverage ask the outbound gate already refused once.
        return None

    proposal_id = row.get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id:
        # The pool matched a row that cannot be addressed. Never guess an id.
        return None

    # Refusal verbs are checked BEFORE consent, so "DENY #ABCDE" can never be
    # read as the bare code it contains.
    if _SHIFT_VERB_CANCEL.search(text):
        target, cause = "cancelled", "owner_cancel"
    elif _SHIFT_VERB_DENY.search(text):
        target, cause = "denied_by_owner", "owner_deny"
    elif _is_unqualified_shift_consent(text, code):
        target, cause = "approved", "owner_code_match"
    else:
        # A code wrapped in words that are neither consent nor refusal. The card
        # asks for a bare code or DENY; anything else is ambiguous, and an
        # ambiguous owner message must not put a shift ask in front of staff.
        return None

    if row.get("status") != _SHIFT_APPROVABLE_STATUS:
        # Stale, already-answered, mid-send or terminal. Refuse the whole
        # message: no transition, no outbound. This is also the first of the two
        # guards that make a double tap safe — the second is the kernel refusing
        # the illegal transition under the pending lock, which covers the race
        # this check cannot see.
        return None

    actions.audit_dispatcher_routed(
        message_id=message_id,
        chat_id=chat_id,
        routed_to_skill="handle_owner_command",
        message_shape="approval_code",
    )

    rc = actions.invoke_update_proposal_status(
        proposal_id, target, cause=cause, actor="owner", owner_input=text,
    )
    if rc != 0:
        # The kernel refused (illegal transition, unknown proposal, lock
        # timeout). Nothing was sent. Fall through so the owner hears about it.
        return None
    if target != "approved":
        # A recorded refusal is a complete outcome: no coverage ask is owed.
        return {"action": "skip",
                "reason": (f"cf-router F8: invoked update-proposal-status "
                           f"{target} for {proposal_id} (rc=0)")}

    send_rc = actions.invoke_send_coverage_message(proposal_id)
    if send_rc == 0:
        return {"action": "skip",
                "reason": (f"cf-router F8: invoked send-coverage-message "
                           f"for {proposal_id} (rc=0)")}
    # Approved but not sent. send-coverage-message owns the recovery — it has
    # already moved the proposal to send_failed and fired the Pushover alert —
    # so fall through and let the owner be told, exactly as the catering arm
    # does on a non-zero apply.
    return None


def _try_f9_alert(text: str, chat_id: str) -> None:
    """Sick-call pattern detected from employee. Fire a Pushover P2 alert
    to the owner so they can verify the dispatcher route within 60s.

    Throttled per chat_id to avoid alert storm on retries (action.py
    holds the throttle state).
    """
    if actions.was_recently_alerted(chat_id, kind="sick_call"):
        return
    actions.fire_pushover_alert(
        title=f"Possible sick-call from {chat_id}",
        body=(f"cf-router detected a sick-call pattern from {chat_id}. "
              f"Verify dispatcher route (handle_sick_call) fires within 60s. "
              f"Text: {text[:200]}"),
    )
    actions.mark_alerted(chat_id, kind="sick_call")
    actions.audit_intercepted(
        reason="f9_sick_call_alert", chat_id=chat_id,
        detail=f"text snippet: {text[:200]}",
    )


def _extract_text(event: Any) -> Optional[str]:
    """Defensive extraction — event shape may vary across Hermes versions.

    Common attributes: event.text (str), event.body (str), event.message (str)
    """
    for attr in ("text", "body", "message", "content"):
        val = getattr(event, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _extract_chat_id(event: Any) -> Optional[str]:
    """Defensive chat_id extraction. Hermes MessageEvent has a `source`
    nested attribute with chat_id; some adapters expose chat_id directly.
    """
    # Direct attribute first
    direct = getattr(event, "chat_id", None)
    if isinstance(direct, str) and direct:
        return direct
    # Nested via source
    source = getattr(event, "source", None)
    if source is not None:
        nested = getattr(source, "chat_id", None)
        if isinstance(nested, str) and nested:
            return nested
    return None


def _extract_media_path(event: Any) -> Optional[str]:
    """Return the first local Hermes media path, if present."""
    for obj in (event, getattr(event, "source", None)):
        if obj is None:
            continue
        for attr in ("image_path", "media_path", "document_path"):
            val = getattr(obj, attr, None)
            if isinstance(val, str) and val:
                return val
        for attr in ("mediaUrls", "media_urls", "media_paths"):
            val = getattr(obj, attr, None)
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, str) and item:
                        return item
    return None


def _extract_from_me(event: Any) -> bool:
    """Return True only from adapter metadata, never from message text."""
    for obj in (event, getattr(event, "source", None)):
        if obj is None:
            continue
        for attr in ("fromMe", "from_me", "is_from_me"):
            val = getattr(obj, attr, None)
            if val is True:
                return True
            if isinstance(val, str) and val.strip().lower() == "true":
                return True
    return False


def _extract_message_id(event: Any, chat_id: str, text: str = "") -> str:
    """Defensive message_id extraction with deterministic fallback.

    Hermes MessageEvent shape varies across adapters; not all expose a
    native message_id. The CateringDispatcherWatchdog* audit variants
    require min_length=1, so we ALWAYS produce a non-empty string.

    The fallback uses a content hash (chat_id + text) rather than a
    timestamp. Multiple gateway instances processing the same inbound
    will derive the SAME fallback — so the `(customer_phone, message_id)`
    idempotency in `create-catering-lead` correctly deduplicates. Per
    PR-CF7 reviewer M8: a timestamp-based fallback would produce
    different message_ids per instance and break idempotency in the
    multi-instance deploy edge case.
    """
    native = _extract_native_message_id(event)
    if native:
        return native
    # Hash-based fallback (chat_id + text) — deterministic across instances
    import hashlib
    digest = hashlib.sha1(f"{chat_id}|{text}".encode("utf-8")).hexdigest()[:12]
    return f"cf_router_f7_{chat_id}_{digest}"


def _extract_native_message_id(event: Any) -> str:
    for attr in ("message_id", "id", "msg_id"):
        val = getattr(event, attr, None)
        if isinstance(val, str) and val:
            return val
        if isinstance(event, dict):
            val = event.get(attr)
            if isinstance(val, str) and val:
                return val
    # Nested via source (same shape as _extract_chat_id)
    source = getattr(event, "source", None)
    if source is None and isinstance(event, dict):
        source = event.get("source")
    if source is not None:
        for attr in ("message_id", "id", "msg_id"):
            val = getattr(source, attr, None)
            if isinstance(val, str) and val:
                return val
            if isinstance(source, dict):
                val = source.get(attr)
                if isinstance(val, str) and val:
                    return val
    return ""


def _event_transport(event: Any) -> str:
    """Best-effort inbound-transport label for PR-R2A amendment idempotency keying.

    Same defensive top-or-nested lookup shape as _extract_native_message_id.
    Defaults to "whatsapp" — the only transport F7 primary-mode routes today —
    so the primary idempotency tuple stays stable when the adapter omits it.
    """
    for attr in ("transport", "platform", "channel"):
        val = getattr(event, attr, None)
        if isinstance(val, str) and val:
            return val
        if isinstance(event, dict):
            val = event.get(attr)
            if isinstance(val, str) and val:
                return val
    source = getattr(event, "source", None)
    if source is None and isinstance(event, dict):
        source = event.get("source")
    if source is not None:
        for attr in ("transport", "platform", "channel"):
            val = getattr(source, attr, None)
            if isinstance(val, str) and val:
                return val
            if isinstance(source, dict):
                val = source.get(attr)
                if isinstance(val, str) and val:
                    return val
    return "whatsapp"


def _event_provider_timestamp(event: Any) -> str:
    """Best-effort provider-side envelope timestamp for PR-R2A.

    Used ONLY to strengthen the secondary idempotency fingerprint. Returns "" when
    the adapter exposes none — the amendment store then treats the fingerprint as
    underivable and relies on the primary (native-id) + text-window tiers. bool is
    excluded (it subclasses int but is never a timestamp).
    """
    def _coerce(val: Any) -> str:
        if isinstance(val, bool):
            return ""
        if isinstance(val, str) and val:
            return val
        if isinstance(val, int):
            return str(val)
        return ""

    for attr in ("timestamp", "provider_timestamp", "ts", "t", "messageTimestamp"):
        got = _coerce(getattr(event, attr, None))
        if got:
            return got
        if isinstance(event, dict):
            got = _coerce(event.get(attr))
            if got:
                return got
    source = getattr(event, "source", None)
    if source is None and isinstance(event, dict):
        source = event.get("source")
    if source is not None:
        for attr in ("timestamp", "provider_timestamp", "ts", "t", "messageTimestamp"):
            got = _coerce(getattr(source, attr, None))
            if got:
                return got
            if isinstance(source, dict):
                got = _coerce(source.get(attr))
                if got:
                    return got
    return ""


def _send_amendment_retry_reply(chat_id: str, lead_id: str) -> bool:
    """Deterministic retry ask sent when durable amendment capture FAILED (PR-R2A).

    Distinct from actions.send_canonical_followup_reply: it must NOT imply the
    update was recorded (capture failed) — only that the customer should resend.
    Hard-coded template (HARD RULES: no LLM, no prices, no menu items, no
    promises). Reuses the send-catering-ack subprocess (canonical bridge prefix +
    @s.whatsapp.net/@lid JID handling), mirroring send_canonical_followup_reply.
    actions.py is not modified for R2A, so this small twin lives here and reuses
    actions.SEND_CATERING_ACK_BIN + actions.SUBPROCESS_TIMEOUT_SEC. Failures are
    non-fatal: the caller still writes the capture-failed audit row and skips.
    """
    import subprocess

    template = (
        f"Sorry — we couldn't record your update to inquiry {lead_id} just now. "
        f"Please resend it in a few minutes so we can add it for the owner."
    )
    try:
        result = subprocess.run(
            [
                str(actions.SEND_CATERING_ACK_BIN),
                "--customer-jid", chat_id,
                "--message-text", template,
                "--lead-id", lead_id,
            ],
            capture_output=True, text=True,
            timeout=actions.SUBPROCESS_TIMEOUT_SEC,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _schedule_f7_rescue(text: str, chat_id: str, message_id: str,
                         signals: list[str]) -> None:
    """Schedule the 30s delayed rescue check via threading.Timer.

    Daemon-style thread (no asyncio coupling). The Timer dies if the
    gateway process restarts during the 30s window — acceptable per
    PR-CF7 plan §"Risks" #1; gateway restart implies new inbounds will
    be handled fresh anyway.
    """
    ts_at_schedule = time.time()
    timer = threading.Timer(
        F7_WATCHDOG_TIMEOUT_SEC,
        actions.f7_rescue_check,
        args=(text, chat_id, message_id, signals, ts_at_schedule),
    )
    # Mark as daemon so the gateway process can exit cleanly without
    # waiting for pending Timers (gracefully cancels in-flight rescues
    # on shutdown rather than hanging the process).
    timer.daemon = True
    timer.start()
