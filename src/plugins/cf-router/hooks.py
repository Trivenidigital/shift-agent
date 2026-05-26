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

import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

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

# 30s rescue window — matches the deployed F7 daemon's WATCHDOG_TIMEOUT_SECS.
# PRESERVED (not removed) for backwards-compat with TestF7DispatcherWatchdog,
# but no longer wired into pre_gateway_dispatch. f7_rescue_check() +
# _schedule_f7_rescue() remain callable directly. Cleanup deferred to a
# follow-up PR after F7 primary-mode soaks in production.
F7_WATCHDOG_TIMEOUT_SEC = 30

# Owner-approval code regex — same alphabet as the deployed dispatcher
# (`#[A-HJ-NP-Z2-9]{5}`, 28.6M-entry alphabet excluding I/O/0/1/L).
# No IGNORECASE: codes are emitted uppercase by generate_unique_code; the
# dispatcher rejects lowercase, so matching lowercase here just adds surface.
_CODE_PATTERN = re.compile(r"#([A-HJ-NP-Z2-9]{5})")
_SAMPLE_PROMPT_REQUEST = re.compile(
    r"\b(?:sample|example|starter)\s+(?:prompt|prompts|idea|ideas)\b"
    r"|\b(?:sample|example|starter|prompt|idea|ideas|inspiration)\b.{0,40}\b(?:for|of)\b.{0,20}\b(?:flyer|flier|poster|marketing)\b"
    r"|\b(?:give|send|show|share|suggest|provide)\b.{0,50}\b(?:flyer|flier|poster|marketing)\b.{0,30}\b(?:idea|ideas|prompt|prompts|examples|inspiration)\b"
    r"|\b(?:give|send|show|share|suggest|provide)\b.{0,60}"
    r"\b(?:sample|example|starter|inspiration)?\s*(?:prompt|prompts|idea|ideas|examples|inspiration)\b.{0,60}"
    r"\b(?:flyer|flier|poster|marketing)\b"
    r"|\b(?:give|send|show|share|suggest|provide|need)\b.{0,70}"
    r"\b(?:ad|ads|promo|promotional|campaign|marketing|creative)\b.{0,40}"
    r"\b(?:idea|ideas|suggestion|suggestions|concept|concepts|example|examples|prompt|prompts|inspiration)\b.{0,40}"
    r"\b(?:business|shop|store|brand|service|offer)\b",
    re.IGNORECASE,
)


def _flyer_request_excerpt_for_reply(text: str, *, limit: int = 140) -> str:
    excerpt = " ".join(actions.flyer_visible_message_text(text).split())
    if len(excerpt) > limit:
        excerpt = excerpt[: limit - 3].rstrip() + "..."
    return excerpt

# Verb classifier — mirrors the F8 watchdog's accepted verb set so plugin
# coverage matches the watchdog it replaces. Past-tense forms ("approved",
# "rejected") are common in owner replies and were handled by the watchdog.
_VERB_APPROVE = re.compile(r"\b(approve|approved|yes|send|ok|go|send it)\b", re.IGNORECASE)
_VERB_REJECT = re.compile(r"\b(reject|rejected|no|decline|pass|cancel)\b", re.IGNORECASE)
_VERB_EDIT = re.compile(r"\b(edit|change|modify)\b", re.IGNORECASE)

# Sick-call regex set (employee path — F9 replacement). Mirrors the six
# patterns from src/agents/shift/scripts/shift-missed-dispatch-notifier
# so plugin coverage matches the watchdog it replaces. Conservative bias
# toward false-positives is acceptable because the action is alert-only.
_SICK_CALL_PATTERNS = [
    re.compile(r"\b(?:sick|fever|cough|cold|stomach|headache|vomit|migraine|flu|food\s*poisoning)\b", re.IGNORECASE),
    re.compile(r"\b(?:can'?t|cannot|won'?t|unable\s+to)\s+(?:come|make\s+it|work|attend)\b", re.IGNORECASE),
    re.compile(r"\b(?:not\s+feeling|feeling\s+(?:unwell|bad|ill|under\s+the\s+weather))\b", re.IGNORECASE),
    re.compile(r"\b(?:family\s+emergency|personal\s+emergency|hospital|doctor|emergency\s+room|er\b)\b", re.IGNORECASE),
    re.compile(r"\b(?:miss(?:ing)?|skip(?:ping)?|cover|coverage)\s+(?:my\s+)?(?:shift|today|tomorrow|tonight|evening|morning)\b", re.IGNORECASE),
    re.compile(r"\b(?:boss|sir|madam),?\s+(?:i'?m|i\s+am|today)\b", re.IGNORECASE),
]


def _is_sick_call(text: str) -> bool:
    if not text or len(text) < 4:
        return False
    return any(p.search(text) for p in _SICK_CALL_PATTERNS)


def pre_gateway_dispatch(event: Any, gateway: Any = None, session_store: Any = None,
                         **_kwargs: Any) -> Optional[dict]:
    """Wrapper that owns Flyer intent shadow context for this inbound."""
    token = None
    result: Optional[dict] = None
    error: Exception | None = None
    try:
        text = _extract_text(event) or ""
        media_path = _extract_media_path(event)
        chat_id = _extract_chat_id(event)
        if chat_id and (text or media_path) and actions.is_flyer_enabled():
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
        finally:
            actions.reset_flyer_intent_shadow(token)


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

        # F8 path — owner self-chat + #XXXXX code → bypass LLM
        if actions.is_owner_chat(chat_id):
            f8_result = _try_f8_intercept(text, chat_id)
            if f8_result is not None:
                return f8_result

        # F9 path — employee sender + sick-call regex → alert only
        if _is_sick_call(text) and actions.is_employee_chat(chat_id):
            _try_f9_alert(text, chat_id)
            # Always allow LLM to handle normally — F9 is alert-only
            return None

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
        if actions.is_flyer_enabled():
            campaign_cta_text = actions.flyer_campaign_cta_text(text)
            if campaign_cta_text:
                cta_result = _try_flyer_campaign_cta_intercept(campaign_cta_text, chat_id, event)
                if cta_result is not None:
                    return cta_result
                return None
            account_result = _try_flyer_account_intercept(text, chat_id, event)
            if account_result is not None:
                return account_result
            regulated_account_result = _try_flyer_regulated_account_guard(text, chat_id, event)
            if regulated_account_result is not None:
                return regulated_account_result
            sample_prompt_result = _try_flyer_sample_prompt_request_intercept(text, chat_id, event, media_path)
            if sample_prompt_result is not None:
                return sample_prompt_result
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
                brand_result = _try_flyer_brand_asset_intercept(text, chat_id, event, media_path)
                if brand_result is not None:
                    return brand_result
            onboarding_reply_result = _try_flyer_existing_onboarding_intercept(text, chat_id, event)
            if onboarding_reply_result is not None:
                return onboarding_reply_result
            guest_phone, guest_role = actions.lid_to_phone_via_identify_sender(chat_id)
            if guest_role != "owner" and actions.find_paid_flyer_guest_order(guest_phone, chat_id):
                flyer_result = _try_flyer_primary_intercept(
                    text, chat_id, event, force_new=True, media_path=media_path,
                )
                if flyer_result is not None:
                    return flyer_result
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
                    and actions.is_registered_customer_contextual_flyer_brief(text)
                ):
                    flyer_result = _try_flyer_primary_intercept(
                        text, chat_id, event, force_new=True, media_path=media_path,
                    )
                    if flyer_result is not None:
                        return flyer_result
            if actions.should_start_new_flyer_over_active(text, has_media=bool(media_path)):
                flyer_result = _try_flyer_primary_intercept(
                    text, chat_id, event, force_new=True, media_path=media_path,
                )
                if flyer_result is not None:
                    return flyer_result
            if actions.is_vague_flyer_start(text, has_media=bool(media_path)):
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
                            ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
                        ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
                        ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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

        if F7_ENABLED:
            if actions.is_flyer_enabled():
                is_flyer, _flyer_signals = actions.classify_flyer_intent(text)
                if is_flyer:
                    flyer_result = _try_flyer_primary_intercept(text, chat_id, event)
                    if flyer_result is not None:
                        return flyer_result
                    return None

            is_catering, signals = actions.classify_catering(text)
            proposal_workflow = (
                actions.is_proposal_selection(text)
                or actions.is_proposal_request(text)
            )
            if is_catering or _has_f7_followup_signal(signals) or proposal_workflow:
                if actions.is_flyer_enabled():
                    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
                    if role != "owner" and actions.has_non_delivered_flyer_project_by_sender(phone, chat_id):
                        return None
                f7_result = _try_f7_primary_intercept(
                    text, chat_id, event, signals=signals,
                    allow_new_lead=is_catering,
                )
                if f7_result is not None:
                    return f7_result

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


def _try_f8_intercept(text: str, chat_id: str) -> Optional[dict]:
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

    # Try catering lead first (more common path)
    lead = actions.find_catering_lead_by_code(code)
    if lead is not None:
        lead_status = lead.get("status")
        # Edit needs LLM extraction — let LLM handle
        if has_edit:
            return None
        if has_approve:
            rc = actions.invoke_apply_owner_decision(code, "approve", lead=lead)
            return _build_skip_or_passthrough(
                rc=rc, chat_id=chat_id, code=code,
                reason="f8_owner_approve",
                detail=f"lead status was {lead_status}",
                action_label=f"apply-owner-decision approve for {code}",
            )
        if has_reject:
            rc = actions.invoke_apply_owner_decision(code, "reject")
            return _build_skip_or_passthrough(
                rc=rc, chat_id=chat_id, code=code,
                reason="f8_owner_reject",
                detail=f"lead status was {lead_status}",
                action_label=f"apply-owner-decision reject for {code}",
            )
        # Code matched but no clear verb — let LLM ask for clarification
        return None

    # Try menu-pending
    menu_pending = actions.find_menu_pending_by_code(code)
    if menu_pending is not None:
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

    # Code didn't match any open lead/pending — let LLM handle (might be
    # a stale reference; LLM can tell the owner)
    return None


def _try_flyer_sample_prompt_request_intercept(text: str, chat_id: str, event: Any, media_path: Optional[str]) -> Optional[dict]:
    if actions.is_flyer_starter_prompt_preference_command(text):
        return None
    body = " ".join(actions.flyer_visible_message_text(text).split())
    if not _SAMPLE_PROMPT_REQUEST.search(body):
        return None

    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
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
        ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
        ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
    ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
        ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
        ack_ok, mid, err = actions.send_flyer_text(chat_id, scope_block)
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
            proc_ok, proc_mid, proc_err = actions.send_flyer_processing_ack(chat_id, project_id)
            gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
            if gen_ok:
                if not active_project.get("revisions"):
                    ack_ok, outbound_message_id, ack_err = _send_preview_then_finalize_access(
                        access, chat_id, phone, project_id, message_id,
                        proc_ok=proc_ok, proc_mid=proc_mid, proc_err=proc_err,
                    )
                else:
                    ack_ok, outbound_message_id, ack_err = actions.send_flyer_concept_previews(chat_id, project_id)
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
                )
                outbound_message_id = ",".join(x for x in [proc_mid, outbound_message_id] if x)
                ack_err = f"concept_generation_failed: {gen_detail}; ack_error={ack_err}"
        else:
            ack_ok, outbound_message_id, ack_err = actions.send_flyer_text(
                chat_id,
                actions.flyer_project_missing_info_reply(active_project),
            )
        actions.audit_intercepted(
            reason="flyer_primary_project_created", chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
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
        ack_ok, mid, err = actions.send_flyer_text(chat_id, block_message)
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
            ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
        proc_ok, proc_mid, proc_err = actions.send_flyer_edit_processing_ack(chat_id, project_id)
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
                    "--manual-detail", gen_detail[:500],
                )
            ack_ok, manual_mid, ack_err = actions.send_flyer_manual_edit_ack(
                chat_id,
                project_id,
                text,
                reason=f"automatic edit generation failed: {gen_detail}",
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
        proc_ok, proc_mid, proc_err = actions.send_flyer_processing_ack(chat_id, project_id)
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
            )
            outbound_message_id = ",".join(x for x in [proc_mid, outbound_message_id] if x)
            ack_err = f"concept_generation_failed: {gen_detail}; ack_error={ack_err}"
    else:
        ack_ok, outbound_message_id, ack_err = actions.send_flyer_text(
            chat_id,
            actions.flyer_project_missing_info_reply(project or {}),
        )
    actions.audit_intercepted(
        reason="flyer_primary_project_created", chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
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
        ack_ok, mid, err = actions.send_flyer_text(chat_id, clarification)
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
        ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
        proc_ok, proc_mid, proc_err = actions.send_flyer_processing_ack(chat_id, project_id)
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
            )
            outbound_message_id = ",".join(x for x in [proc_mid, outbound_message_id] if x)
            ack_err = f"concept_generation_failed: {gen_detail}; ack_error={ack_err}"
    else:
        ack_ok, outbound_message_id, ack_err = actions.send_flyer_text(
            chat_id,
            actions.flyer_project_missing_info_reply(project or {}),
        )
    actions.audit_intercepted(
        reason="flyer_primary_project_created",
        chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
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
            ack_ok, mid, err = actions.send_flyer_text(chat_id, clarification)
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
        proc_ok, proc_mid, proc_err = actions.send_flyer_edit_processing_ack(chat_id, project_id)
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
                    "--manual-detail", gen_detail[:500],
                )
            ack_ok, manual_mid, ack_err = actions.send_flyer_manual_edit_ack(
                chat_id,
                project_id,
                new_raw_request,
                reason=f"automatic edit generation failed: {gen_detail}",
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
            )
        else:
            ack_ok, outbound_message_id, ack_err = actions.send_flyer_intake_ack(chat_id, project_id)
        actions.audit_intercepted(
            reason="flyer_primary_project_created",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
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
        proc_ok, proc_mid, proc_err = actions.send_flyer_edit_processing_ack(chat_id, project_id)
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

    ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
        reply or (
            "Flyer Studio\n------------\n"
            "Please complete payment first. Tap Create One Flyer - $4, pay, then send your flyer details here."
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
        return ok, detail
    if access == "guest":
        ok, detail, _result = actions.trigger_consume_flyer_guest_order(sender_phone=phone, chat_id=chat_id, project_id=project_id)
        return ok, detail
    return True, "no_access_to_finalize"


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
    preview_ok, preview_mid, preview_err = actions.send_flyer_concept_previews(chat_id, project_id)
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
        return False, outbound_message_id, f"{ack_err}; access_finalize_failed={access_detail[:250]}"
    return proc_ok and preview_ok and access_ok, outbound_message_id, ack_err


def _send_generation_failure_customer_update(
    chat_id: str,
    project_id: str,
    request_text: str,
    gen_detail: str,
    *,
    proc_ok: bool,
) -> tuple[bool, str, str]:
    if actions.flyer_generation_queued_manual_review(gen_detail):
        return actions.send_flyer_manual_review_ack(
            chat_id,
            project_id,
            request_text,
            reason=gen_detail,
        )
    if proc_ok:
        return True, "", ""
    return actions.send_flyer_intake_ack(chat_id, project_id)


def _preview_may_have_delivered(outbound_message_id: str, ack_err: str) -> bool:
    err = (ack_err or "").lower()
    return bool(outbound_message_id) or "partial_delivery" in err or "send_uncertain" in err


def _send_flyer_regeneration_failed_ack(chat_id: str, project_id: str) -> tuple[bool, str, str]:
    return actions.send_flyer_text(
        chat_id,
        (
            "Flyer Studio\n"
            "------------\n"
            "I could not finish the revised flyer automatically just now.\n\n"
            "I kept the edit request open instead of sending a mismatched flyer. "
            "Please check back here shortly, or send one exact correction if anything else must change."
        ),
    )


def _send_flyer_finalization_failed_ack(chat_id: str, project_id: str) -> tuple[bool, str, str]:
    return actions.send_flyer_text(
        chat_id,
        (
            "Flyer Studio\n"
            "------------\n"
            "I hit an issue preparing the final files. I'll review it and send an update here."
        ),
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
        )
        return {"action": "skip", "reason": "cf-router flyer account command failed"}
    # PR-ζ F8 2026-05-26: construct ActionExecutionContext for the change_plan
    # path so safe_io.bridge_post runs PR-γ lint on the customer-facing reply.
    # change_plan is the only `external_irreversible` action in the portfolio
    # (per PR-δ action_registry). Other branches keep action_context=None and
    # fall through the actions.py basename allowlist; PR-ζ.1 migrates the rest.
    #
    # ActionExecutionContext is imported at module top (above) so any import
    # quirk surfaces at plugin load, not at first regulated send.
    detail = result.get("detail") or ""
    is_change_plan = "plan_change_requested" in detail
    action_ctx = None
    if is_change_plan:
        action_ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            # plan_change is a payment REQUEST, not a completion. The
            # checkout URL emits the next step; verification arrives only
            # after the payment webhook (PR-ζ.1 + §11 wiring).
            verified_action_result=False,
            mutation_class="external_irreversible",
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
        fallback_ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change_fallback",
            is_regulated_action=False,
            verified_action_result=False,
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
        phone_check, _role_check = actions.lid_to_phone_via_identify_sender(chat_id)
        active_project = actions.find_active_flyer_project_by_sender(phone_check, chat_id)
        if active_project:
            return None
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
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
    ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
        manual_block = status_project.get("manual_review") or {}
        manual_reason_code = str(manual_block.get("reason_code") or "")
        if sp_status == "manual_edit_required" and manual_reason_code == "source_edit_provider_unavailable":
            status_reply = actions.flyer_manual_edit_status_reply(status_project)
        else:
            status_reply = actions.flyer_project_status_reply(status_project)
        ack_ok, mid, err = actions.send_flyer_text(chat_id, status_reply)
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
    ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
        ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
        )
        return {"action": "skip", "reason": "cf-router flyer intake failed"}
    ack_ok, mid, err = actions.send_flyer_text(chat_id, result.get("reply_text") or "")
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
    status = str((intake_session or {}).get("status") or "")
    if customer and customer.get("status") in {"active", "trial"} and status not in protected_statuses and (
        actions.classify_flyer_intent(text)[0]
        or actions.should_start_new_flyer_over_active(text, has_media=bool(media_path))
    ):
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
            ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
            ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
        ack_ok, mid, err = actions.send_flyer_text(chat_id, order_result.get("reply_text") or "")
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
    ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
    ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
    ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
    ack_ok, mid, err = actions.send_flyer_text(chat_id, reply_text)
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


def _try_flyer_brand_asset_intercept(text: str, chat_id: str, event: Any, media_path: str) -> Optional[dict]:
    """Capture logo/template uploads during onboarding or flyer requests."""
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        return None
    if not phone:
        return None

    lower = (text or "").lower()
    if actions.should_start_new_flyer_over_active(text, has_media=True):
        return None
    active_project = actions.find_active_flyer_project_by_sender(phone, chat_id)
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
    explicit_asset_words = any(word in lower for word in ("logo", "template", "sample", "reference", "brand", "replace"))
    is_brand_asset = active_project is not None or explicit_asset_words
    if not is_brand_asset:
        return None

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
                preview_ok, preview_mid, preview_err = actions.send_flyer_concept_previews(chat_id, project_id)
                actions.audit_intercepted(
                    reason="flyer_brand_asset_saved", chat_id=chat_id,
                    subprocess_rc=0 if preview_ok else 3,
                    detail=f"project_id={project_id}; regenerated=true; status={result.get('next_status')}; ack_message_id={preview_mid}; ack_error={preview_err[:300]}",
                )
                if preview_ok:
                    return {"action": "skip", "reason": f"cf-router flyer brand asset saved and regenerated {project_id}"}
            if actions.flyer_generation_queued_manual_review(gen_detail):
                ack_ok, manual_mid, ack_err = actions.send_flyer_manual_review_ack(
                    chat_id,
                    project_id,
                    text,
                    reason=gen_detail,
                )
                actions.audit_intercepted(
                    reason="flyer_reference_manual_review_queued",
                    chat_id=chat_id,
                    subprocess_rc=0 if ack_ok else 3,
                    detail=(
                        f"project_id={project_id}; brand_asset_regeneration_manual=true; "
                        f"status={result.get('next_status')}; ack_message_id={manual_mid}; ack_error={ack_err[:300]}"
                    ),
                )
                return {"action": "skip", "reason": f"cf-router flyer brand asset manual review queued {project_id}"}
            reply = f"{reply}\n\nSaved. I couldn't finish the flyer update automatically yet. I'll send an update here shortly."

    ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
    actions.audit_intercepted(
        reason="flyer_brand_asset_saved", chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"status={result.get('next_status')}; customer_id={result.get('customer_id') or ''}; "
            f"sender_role={role}; media_path={media_path}; ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    return {"action": "skip",
            "reason": f"cf-router flyer brand asset: {result.get('next_status')}"}


def _try_flyer_active_project_intercept(text: str, chat_id: str, event: Any, media_path: Optional[str] = None) -> Optional[dict]:
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
    if not phone:
        phone = _flyer_customer_sender_phone(customer)
    if not phone:
        return None
    active_project = actions.find_active_flyer_project_by_sender(phone, chat_id)
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
        manual_block = status_project.get("manual_review") or {}
        manual_reason_code = str(manual_block.get("reason_code") or "")
        if sp_status == "manual_edit_required" and manual_reason_code == "source_edit_provider_unavailable":
            reply = actions.flyer_manual_edit_status_reply(status_project)
        else:
            reply = actions.flyer_project_status_reply(status_project)
        ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
            ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
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
        ack_ok, mid, err = actions.send_flyer_text(chat_id, scope_block)
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
    if actions.should_bypass_active_flyer_project_for_fresh_request(
        body,
        active_project,
        has_media=bool(media_path),
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
    if (
        actions.is_stale_for_new_request(active_project)
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
        status_project = active_project
        mentioned_id = actions.extract_flyer_project_id_mention(body)
        if mentioned_id:
            named = actions.find_flyer_project_by_id_for_sender(phone, chat_id, mentioned_id)
            if named is not None:
                status_project = named
        else:
            latest = actions.find_latest_flyer_project_for_status_by_sender(phone, chat_id)
            if latest is not None and str(latest.get("updated_at") or "") > str(active_project.get("updated_at") or ""):
                status_project = latest
        status_project_id = str(status_project.get("project_id") or "")
        status_project_status = str(status_project.get("status") or "")
        # P0-6: manual_edit_required projects pick the source-edit-specific
        # reply ONLY when the reason_code is source_edit_provider_unavailable.
        # All other reason codes (missing_required_facts, reference_unsupported,
        # visual_qa_failed, etc.) flow through the general status reply, which
        # now consults MANUAL_REVIEW_REASON_LINES to deliver reason-specific
        # copy instead of the generic "source-preserving edit queue" text.
        manual_block = status_project.get("manual_review") or {}
        manual_reason_code = str(manual_block.get("reason_code") or "")
        if status_project_status == "manual_edit_required" and manual_reason_code == "source_edit_provider_unavailable":
            reply = actions.flyer_manual_edit_status_reply(status_project)
        else:
            reply = actions.flyer_project_status_reply(status_project)
        ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
        is_source_edit_manual_status = (
            status_project_status == "manual_edit_required"
            and manual_reason_code == "source_edit_provider_unavailable"
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
    selection_map = {
        "1": "C1", "option 1": "C1", "concept 1": "C1", "c1": "C1",
        "2": "C2", "option 2": "C2", "concept 2": "C2", "c2": "C2",
        "3": "C3", "option 3": "C3", "concept 3": "C3", "c3": "C3",
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
        proc_ok, proc_mid, proc_err = actions.send_flyer_processing_ack(chat_id, project_id)
        gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
        if gen_ok:
            if not active_project.get("revisions"):
                ack_ok, outbound_message_id, ack_err = _send_preview_then_finalize_access(
                    access, chat_id, phone, project_id, message_id,
                    proc_ok=proc_ok, proc_mid=proc_mid, proc_err=proc_err,
                )
            else:
                ack_ok, outbound_message_id, ack_err = actions.send_flyer_concept_previews(chat_id, project_id)
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
            )
            outbound_message_id = ",".join(x for x in [proc_mid, outbound_message_id] if x)
            ack_err = f"concept_generation_failed: {gen_detail}; ack_error={ack_err}"
        actions.audit_intercepted(
            reason="flyer_primary_project_created" if gen_ok and ack_ok else "flyer_primary_failed",
            chat_id=chat_id,
            subprocess_rc=0 if gen_ok and ack_ok else 2,
            detail=(
                f"project_id={project_id}; intake_ready=true; sender_role={role}; "
                f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
            ),
        )
        return {"action": "skip",
                "reason": f"cf-router flyer active: generated {project_id}"}

    if status == "awaiting_concept_selection" and lower in selection_map:
        concept_id = selection_map[lower]
        ok, detail = actions.invoke_update_flyer_project(project_id, "--select-concept", concept_id)
        if ok:
            ok, detail2 = actions.invoke_update_flyer_project(project_id, "--status", "awaiting_final_approval")
            detail = f"{detail}; {detail2}"
        if ok:
            ack_ok, mid, err = actions.send_flyer_text(
                chat_id,
                f"Selected {concept_id}. Reply with revision notes, or reply APPROVE to receive final files.",
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
            status_project = active_project
            mentioned_id = actions.extract_flyer_project_id_mention(body)
            if mentioned_id:
                named = actions.find_flyer_project_by_id_for_sender(phone, chat_id, mentioned_id)
                if named is not None:
                    status_project = named
            else:
                latest = actions.find_latest_flyer_project_for_status_by_sender(phone, chat_id)
                if latest is not None and str(latest.get("updated_at") or "") > str(active_project.get("updated_at") or ""):
                    status_project = latest
            status_project_id = str(status_project.get("project_id") or "")
            status_project_status = str(status_project.get("status") or "")
            # P0-6: same reason_code routing as the first status-check handler
            # — source-edit-specific reply for source_edit_provider_unavailable
            # only; everything else flows through the reason-code-aware general
            # reply.
            manual_block = status_project.get("manual_review") or {}
            manual_reason_code = str(manual_block.get("reason_code") or "")
            if status_project_status == "manual_edit_required" and manual_reason_code == "source_edit_provider_unavailable":
                reply = actions.flyer_manual_edit_status_reply(status_project)
            else:
                reply = actions.flyer_project_status_reply(status_project)
            ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
            # P0-6: audit reason must match the routing branch. Pre-S7 this
            # was hardcoded to flyer_reference_exact_edit_status regardless
            # of reason_code — operator dashboards filtering by audit reason
            # would have overcounted source-edit traffic vs the general
            # manual-queue status checks (visual_qa_failed, etc.).
            if ack_ok:
                audit_reason = (
                    "flyer_reference_exact_edit_status"
                    if status_project_status == "manual_edit_required" and manual_reason_code == "source_edit_provider_unavailable"
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
                    "reason": f"cf-router flyer status for {status_project_id}"}
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
            ack_ok, mid, err = actions.send_flyer_text(chat_id, reminder)
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
    if (actions.is_flyer_approval_text(body) or actions.is_flyer_send_now_intent(body)) and status in {"revising_design", "awaiting_final_approval"}:
        if status == "revising_design" and not active_project.get("concepts"):
            gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
            if gen_ok:
                ack_ok, outbound_message_id, ack_err = actions.send_flyer_concept_previews(chat_id, project_id)
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
                )
            else:
                fail_ack_ok, fail_mid, fail_err = _send_flyer_regeneration_failed_ack(chat_id, project_id)
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
        if status == "revising_design":
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
                ok = False
                detail = f"{detail}; manual_access_finalize_failed={access_detail[:250]}"
        elif manual_completed:
            release_ok, release_detail = _release_flyer_access(manual_access, chat_id, phone, project_id, message_id)
            if not release_ok:
                detail = f"{detail}; manual_access_release_failed={release_detail[:250]}"
        actions.audit_intercepted(
            reason="flyer_primary_project_created" if ok else "flyer_primary_failed",
            chat_id=chat_id, subprocess_rc=0 if ok else 2,
            detail=f"project_id={project_id}; approve=true; sender_role={role}; {detail[:500]}",
        )
        if ok:
            return {"action": "skip",
                    "reason": f"cf-router flyer active: finalized {project_id}"}
        fail_ack_ok, fail_mid, fail_err = _send_flyer_finalization_failed_ack(chat_id, project_id)
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
    if status in {"revising_design", "awaiting_final_approval", "delivered"} and body and not actions.is_flyer_send_now_intent(body):
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
        active_after = actions.find_active_flyer_project_by_sender(phone, chat_id) or {}
        needs_regen = not active_after.get("concepts")
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
        elif ok and needs_regen:
            ack_message = "Revision applied to the flyer details. I am regenerating the design now."
        else:
            ack_message = "Revision noted. I will keep it with this flyer project. Reply APPROVE when ready for final files."
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id,
            ack_message,
        )
        regeneration_failed = False
        if ok and needs_regen and not revision_requires_clarification:
            gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
            if gen_ok:
                preview_ok, preview_mid, preview_err = actions.send_flyer_concept_previews(chat_id, project_id)
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
                    )
                else:
                    fail_ack_ok, fail_mid, fail_err = _send_flyer_regeneration_failed_ack(chat_id, project_id)
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
            detail=f"project_id={project_id}; revision=true; revision_requires_clarification={revision_requires_clarification}; update={detail[:250]}; ack_message_id={mid}; ack_error={err}",
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

    extracted: Optional[dict] = None
    headcount = _parse_headcount_from_signals(signals or [])
    if headcount is not None:
        extracted = {"headcount": headcount}

    ok, detail = actions.trigger_create_catering_lead(
        customer_phone=customer_phone_arg,
        customer_name="",
        raw_inquiry=text,
        message_id=message_id,
        extracted_fields=extracted,
    )
    actions.audit_intercepted(
        reason="f7_primary_new_inquiry", chat_id=chat_id,
        subprocess_rc=0 if ok else 2, detail=detail[:500],
    )
    if not ok:
        return None
    return {"action": "skip",
            "reason": "cf-router F7 primary: catering inquiry routed deterministically"}


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

    if F7_PROPOSAL_BRANCH_ENABLED and actions.is_proposal_selection(text):
        if actions.find_selectable_proposal_set(lead_id):
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

    if F7_PROPOSAL_BRANCH_ENABLED and actions.is_proposal_request(text):
        rc = actions.invoke_create_catering_proposals(
            lead_id, chat_id, message_id, text,
        )
        actions.audit_intercepted(
            reason="f7_proposal_request", chat_id=chat_id,
            code=approval_code, subprocess_rc=rc,
            detail=f"active {lead_id}; proposal request handled by cf-router",
        )
        if rc in {0, 2, 4, 6, 11}:
            return {"action": "skip",
                    "reason": f"cf-router F7 proposal request for {lead_id}"}
        return None

    if F7_PRIMARY_FOLLOWUP_REPLY:
        actions.send_canonical_followup_reply(chat_id, lead_id)
    actions.audit_intercepted(
        reason="f7_primary_followup_suppressed", chat_id=chat_id,
        code=approval_code,
        detail=f"active {lead_id} status={active_lead.get('status')}; LLM bypassed",
    )
    return {"action": "skip",
            "reason": f"cf-router F7 primary: follow-up to active {lead_id} suppressed"}


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
