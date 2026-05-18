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
            intake_result = _try_flyer_intake_intercept(text, chat_id, event, media_path=media_path)
            if intake_result is not None:
                return intake_result
            account_result = _try_flyer_account_intercept(text, chat_id, event)
            if account_result is not None:
                return account_result
            scope_choice_result = _try_flyer_reference_scope_choice_intercept(text, chat_id, event)
            if scope_choice_result is not None:
                return scope_choice_result
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
            if actions.is_vague_flyer_start(text, has_media=bool(media_path)):
                phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
                if role != "owner":
                    customer = actions.find_flyer_customer_by_sender(phone, chat_id) if phone else None
                    source = "new_flyer" if customer else "start_trial"
                    started = _start_flyer_intake(
                        text, chat_id, event, source=source, original_text=text,
                        media_path=media_path,
                    )
                    if started is not None:
                        return started
            if actions.should_start_new_flyer_over_active(text, has_media=bool(media_path)):
                flyer_result = _try_flyer_primary_intercept(
                    text, chat_id, event, force_new=True, media_path=media_path,
                )
                if flyer_result is not None:
                    return flyer_result
            flyer_result = _try_flyer_active_project_intercept(text, chat_id, event)
            if flyer_result is not None:
                return flyer_result
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


def _try_flyer_primary_intercept(
    text: str,
    chat_id: str,
    event: Any,
    *,
    force_new: bool = False,
    media_path: Optional[str] = None,
) -> Optional[dict]:
    """Create a Flyer Studio project deterministically before LLM dispatch.

    This mirrors the Catering F7 primary-mode safety pattern: explicit flyer
    requests should not depend on the generic LLM dispatcher being able to
    call shell tools correctly.
    """
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if not phone:
        return None
    if actions.is_vague_flyer_start(text, has_media=bool(media_path)):
        return _start_flyer_intake(
            text, chat_id, event, source="new_flyer", original_text=text,
            media_path=media_path,
        )

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
                ack_ok, outbound_message_id, ack_err = actions.send_flyer_intake_ack(chat_id, project_id)
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
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
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
            actions.save_flyer_reference_scope_pending(
                chat_id=chat_id,
                sender_phone=phone,
                customer=customer,
                raw_request=raw_request,
                media_path=media_path,
                scope=scope or {},
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

    if exact_reference_edit:
        access, quota_result = _reserve_flyer_access_or_reply(chat_id, phone, project_id, message_id, consume_quota=True)
        if quota_result is not None:
            return quota_result
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
            _release_flyer_access(access, chat_id, phone, project_id, message_id)
            ack_ok, manual_mid, ack_err = actions.send_flyer_manual_edit_ack(chat_id, project_id, text)
            outbound_message_id = ",".join(x for x in [proc_mid, manual_mid] if x)
            ack_err = f"edit_generation_failed: {gen_detail}; ack_error={ack_err}"
            reason = f"cf-router flyer exact edit queued: project {project_id}"
        actions.audit_intercepted(
            reason="flyer_primary_project_created" if gen_ok else "flyer_reference_exact_edit_queued",
            chat_id=chat_id,
            subprocess_rc=0 if ack_ok else 3,
            detail=(
                f"project_id={project_id}; sender_role={role}; "
                f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
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
            ack_ok, outbound_message_id, ack_err = actions.send_flyer_intake_ack(chat_id, project_id)
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
            f"ack_message_id={outbound_message_id}; ack_error={ack_err[:300]}"
        ),
    )
    return {"action": "skip",
            "reason": f"cf-router flyer primary: project {project_id} created"}


def _try_flyer_reference_scope_choice_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if not phone:
        return None
    pending = actions.consume_flyer_reference_scope_choice(text, chat_id=chat_id, sender_phone=phone)
    if not pending:
        return None

    choice = str(pending.get("choice") or "")
    business_name = str(((pending.get("customer") or {}).get("business_name")) or "this business")
    source = str(pending.get("source_organization") or "the source flyer")

    if choice == "authorized":
        actions.save_flyer_reference_authorization_pending(pending)
        reply = (
            "Flyer Studio\n"
            "------------\n"
            f"Thanks. Please reply with how {source} is connected to {business_name}, "
            f"and send the {business_name} logo/details to use.\n\n"
            "Once I have that, I can continue with the flyer update."
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
            ack_ok, outbound_message_id, ack_err = actions.send_flyer_intake_ack(chat_id, project_id)
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
            _release_flyer_access(access, chat_id, phone, project_id, message_id)
            ack_ok, manual_mid, ack_err = actions.send_flyer_manual_edit_ack(chat_id, project_id, raw_request)
            outbound_message_id = ",".join(x for x in [proc_mid, manual_mid] if x)
            ack_err = f"edit_generation_failed: {gen_detail}; ack_error={ack_err}"
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


def _release_flyer_access(access: str, chat_id: str, phone: str, project_id: str, message_id: str) -> None:
    if access == "quota":
        actions.trigger_flyer_release_quota(customer_phone=phone, project_id=project_id, message_id=message_id)
    elif access == "guest":
        actions.trigger_release_flyer_guest_order(sender_phone=phone, chat_id=chat_id, project_id=project_id)


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
        if _preview_may_have_delivered(outbound_message_id, ack_err):
            access_ok, access_detail = _finalize_flyer_access_checked(access, chat_id, phone, project_id, message_id)
            if not access_ok:
                return False, outbound_message_id, f"{ack_err}; access_finalize_failed={access_detail[:250]}"
            return False, outbound_message_id, ack_err
        _release_flyer_access(access, chat_id, phone, project_id, message_id)
        return False, outbound_message_id, ack_err
    access_ok, access_detail = _finalize_flyer_access_checked(access, chat_id, phone, project_id, message_id)
    if not access_ok:
        _release_flyer_access(access, chat_id, phone, project_id, message_id)
        return False, outbound_message_id, f"{ack_err}; access_finalize_failed={access_detail[:250]}"
    return proc_ok and preview_ok and access_ok, outbound_message_id, ack_err


def _preview_may_have_delivered(outbound_message_id: str, ack_err: str) -> bool:
    err = (ack_err or "").lower()
    return bool(outbound_message_id) or "partial_delivery" in err or "send_uncertain" in err


def _try_flyer_account_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    if not actions.is_flyer_account_command(text):
        return None
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if not phone:
        return None
    customer = actions.find_flyer_customer_by_sender(phone, chat_id)
    if customer is None:
        return None
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
        return None
    if not result.get("handled"):
        return None
    ack_ok, mid, err = actions.send_flyer_text(chat_id, result.get("reply_text") or "")
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


def _try_flyer_campaign_cta_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    """Route campaign button replies before project/revision intake."""
    source = actions.flyer_campaign_source(text)
    if not source:
        return None
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
    if not actions.find_flyer_intake_session_by_sender(phone, chat_id):
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
            return _try_flyer_primary_intercept(
                raw_request,
                chat_id,
                event,
                force_new=True,
                media_path=reference_media_path or media_path,
            )
    if action == "start_guest_order":
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


def _try_flyer_onboarding_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    """Start or advance WhatsApp-native customer onboarding for new senders."""
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        return None
    customer = actions.find_flyer_customer_by_sender(phone, chat_id) if phone else None
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
    ack_ok, mid, err = actions.send_flyer_text(chat_id, result.get("reply_text") or "")
    actions.audit_intercepted(
        reason="flyer_onboarding", chat_id=chat_id,
        subprocess_rc=0 if ack_ok else 3,
        detail=(
            f"status={result.get('next_status')}; customer_id={result.get('customer_id') or ''}; "
            f"sender_role={role}; ack_message_id={mid}; ack_error={err[:300]}"
        ),
    )
    trailing_request = actions.extract_flyer_request_after_confirm(text)
    if (
        result.get("next_status") in {"trial", "active"}
        and trailing_request
        and actions.should_start_new_flyer_over_active(trailing_request, has_media=False)
    ):
        project_result = _try_flyer_primary_intercept(
            trailing_request, chat_id, event, force_new=True,
        )
        if project_result is not None:
            return project_result
    return {"action": "skip",
            "reason": f"cf-router flyer onboarding: {result.get('next_status')}"}


def _try_flyer_existing_onboarding_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    """Continue pending Flyer onboarding for arbitrary field replies."""
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if role == "owner":
        return None
    customer = actions.find_flyer_customer_by_sender(phone, chat_id) if phone else None
    if customer and customer.get("status") in {"active", "trial"}:
        return None
    if not actions.find_flyer_onboarding_session_by_sender(phone, chat_id):
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
            reply = f"{reply}\n\nSaved. I could not regenerate the flyer automatically yet: {gen_detail[:160] if 'gen_detail' in locals() else ''}"

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


def _try_flyer_active_project_intercept(text: str, chat_id: str, event: Any) -> Optional[dict]:
    message_id = _extract_message_id(event, chat_id, text)
    phone, role = actions.lid_to_phone_via_identify_sender(chat_id)
    if not phone:
        return None
    active_project = actions.find_active_flyer_project_by_sender(phone, chat_id)
    if active_project is None:
        return None

    project_id = str(active_project.get("project_id") or "")
    status = str(active_project.get("status") or "")
    body = " ".join(actions.flyer_visible_message_text(text).split())
    lower = body.lower()
    if actions.should_start_new_flyer_over_active(body, has_media=False):
        return None
    selection_map = {
        "1": "C1", "option 1": "C1", "concept 1": "C1", "c1": "C1",
        "2": "C2", "option 2": "C2", "concept 2": "C2", "c2": "C2",
        "3": "C3", "option 3": "C3", "concept 3": "C3", "c3": "C3",
    }

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
        ok, detail = actions.invoke_update_flyer_project(
            project_id,
            "--revision-text", body,
            "--message-id", message_id,
        )
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id,
            (
                "Flyer Studio\n"
                "------------\n"
                f"Project {project_id} is already queued for a source-preserving edit. "
                "I saved this additional correction with the edit request and will send the corrected flyer here when it is ready."
            ),
        )
        actions.audit_intercepted(
            reason="flyer_reference_exact_edit_queued" if ok and ack_ok else "flyer_primary_failed",
            chat_id=chat_id,
            subprocess_rc=0 if ok and ack_ok else 3,
            detail=f"project_id={project_id}; queued_followup=true; sender_role={role}; update={detail[:250]}; ack_message_id={mid}; ack_error={err[:300]}",
        )
        return {"action": "skip",
                "reason": f"cf-router flyer exact edit already queued for {project_id}"}

    if actions.is_flyer_approval_text(body) and status in {"revising_design", "awaiting_final_approval"}:
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
            return None
        if status == "revising_design":
            ok_status, status_detail = actions.invoke_update_flyer_project(project_id, "--status", "awaiting_final_approval")
            if not ok_status:
                actions.audit_intercepted(
                    reason="flyer_primary_failed", chat_id=chat_id,
                    subprocess_rc=2, detail=f"project_id={project_id}; approve_status_failed={status_detail[:400]}",
                )
                return None
        ok, detail = actions.finalize_and_send_flyer(chat_id, project_id, message_id)
        actions.audit_intercepted(
            reason="flyer_primary_project_created" if ok else "flyer_primary_failed",
            chat_id=chat_id, subprocess_rc=0 if ok else 2,
            detail=f"project_id={project_id}; approve=true; sender_role={role}; {detail[:500]}",
        )
        if ok:
            return {"action": "skip",
                    "reason": f"cf-router flyer active: finalized {project_id}"}
        return None

    if status in {"revising_design", "awaiting_final_approval", "delivered"} and body:
        ok, detail = actions.invoke_update_flyer_project(
            project_id,
            "--revision-text", body,
            "--message-id", message_id,
        )
        revision_requires_clarification = False
        clarification_reason = ""
        try:
            import json
            update_doc = json.loads(detail)
            patch = update_doc.get("revision_patch") or {}
            revision_requires_clarification = bool(update_doc.get("revision_requires_clarification"))
            clarification_reason = str(patch.get("unresolved_reason") or "I could not match that change to the current flyer details.")
        except Exception:
            revision_requires_clarification = not ok
            clarification_reason = detail[:180] or "I could not apply that revision."
        active_after = actions.find_active_flyer_project_by_sender(phone, chat_id) or {}
        needs_regen = not active_after.get("concepts")
        if revision_requires_clarification:
            ack_message = f"I need one clarification before regenerating: {clarification_reason}. Please send the exact item or text to change."
        elif ok and needs_regen:
            ack_message = "Revision applied to the flyer details. I am regenerating the design now."
        else:
            ack_message = "Revision noted. I will keep it with this flyer project. Reply APPROVE when ready for final files."
        ack_ok, mid, err = actions.send_flyer_text(
            chat_id,
            ack_message,
        )
        if ok and needs_regen and not revision_requires_clarification:
            gen_ok, gen_detail = actions.trigger_generate_flyer_concepts(project_id)
            if gen_ok:
                preview_ok, preview_mid, preview_err = actions.send_flyer_concept_previews(chat_id, project_id)
                mid = ",".join(x for x in [mid, preview_mid] if x)
                ack_ok = ack_ok and preview_ok
                if preview_err:
                    err = f"{err}; preview_error={preview_err}"
            else:
                ack_ok = False
                err = f"{err}; regeneration_failed={gen_detail[:300]}"
        actions.audit_intercepted(
            reason="flyer_primary_project_created" if ok else "flyer_primary_failed",
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
                f"I have project {project_id} open. Please send the full flyer request in one message "
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
    for attr in ("message_id", "id", "msg_id"):
        val = getattr(event, attr, None)
        if isinstance(val, str) and val:
            return val
    # Nested via source (same shape as _extract_chat_id)
    source = getattr(event, "source", None)
    if source is not None:
        for attr in ("message_id", "id", "msg_id"):
            val = getattr(source, attr, None)
            if isinstance(val, str) and val:
                return val
    # Hash-based fallback (chat_id + text) — deterministic across instances
    import hashlib
    digest = hashlib.sha1(f"{chat_id}|{text}".encode("utf-8")).hexdigest()[:12]
    return f"cf_router_f7_{chat_id}_{digest}"


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
