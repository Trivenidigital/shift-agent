"""Deterministic Flyer Studio action authority.

Hermes may classify messy customer language, but this registry is the product
contract: which action exists, who may perform it, whether confirmation/payment
is required, and how common semantic phrasings normalize into command text.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Optional

from schemas import ActionExecutionContext, FlyerPlanTier


FlyerActionDomain = Literal["account", "billing", "quota", "guest_order", "project", "preference"]
FlyerActionEffect = Literal["read", "write", "payment_request", "payment_activation"]

# PR-δ 2026-05-26 — mutation_class declares whether the action's side effects
# can be rolled back via local state restoration. Future audit-fail-closed
# wiring (deferred to PR-ζ + audit-write-fail-closed) reads this field to
# choose the right customer copy after an audit-write failure:
#
#   local_reversible:      mandatory rollback path, customer copy says
#                          "no change has been made"
#   external_irreversible: rollback impossible (external API committed),
#                          customer copy says "under operator review"
#
# PR-δ ships the FIELD only. Rollback handler wiring is later.
FlyerActionMutationClass = Literal["local_reversible", "external_irreversible"]


@dataclass(frozen=True)
class FlyerActionDefinition:
    action_id: str
    command: str
    domain: FlyerActionDomain
    effect: FlyerActionEffect
    # PR-ζ.1b 2026-05-26 — mutation_class is now Optional so that registry
    # entries which do not yet have a concrete rollback consumer (project
    # actions, system-meta replies) can omit it without making a downstream-
    # consumed semantic claim. Existing ACCOUNT_ACTIONS entries continue to
    # declare it explicitly. Defer per-entry rollback semantics to ζ.2 when
    # the consumer wiring lands.
    mutation_class: Optional[FlyerActionMutationClass] = None
    requires_admin: bool = False
    requires_confirmation: bool = False
    requires_payment: bool = False
    success_detail: str = ""


@dataclass(frozen=True)
class AccountCommandMatch:
    command_text: str
    command: str
    action_id: str
    confidence: float
    reason: str


ACCOUNT_ACTIONS: dict[str, FlyerActionDefinition] = {
    "status": FlyerActionDefinition(
        action_id="flyer.account.status",
        command="status",
        domain="account",
        effect="read",
        mutation_class="local_reversible",  # read-only — trivially reversible
    ),
    "help": FlyerActionDefinition(
        action_id="flyer.account.help",
        command="help",
        domain="account",
        effect="read",
        mutation_class="local_reversible",  # read-only
    ),
    "plan_menu": FlyerActionDefinition(
        action_id="flyer.billing.plan_menu",
        command="plan_menu",
        domain="billing",
        effect="read",
        mutation_class="local_reversible",  # read-only
    ),
    "starter_prompt_mode": FlyerActionDefinition(
        action_id="flyer.preference.starter_prompt_mode",
        command="starter_prompt_mode",
        domain="preference",
        effect="write",
        mutation_class="local_reversible",  # JSON state preference; reversible by re-set
        requires_admin=True,
        success_detail="starter_prompt_mode_updated",
    ),
    "update_business_name": FlyerActionDefinition(
        action_id="flyer.account.update_business_name",
        command="update_business_name",
        domain="account",
        effect="write",
        mutation_class="local_reversible",  # JSON state field; reversible by re-update
        requires_admin=True,
        success_detail="business_name_updated",
    ),
    "add_authorized": FlyerActionDefinition(
        action_id="flyer.account.add_authorized_requester",
        command="add_authorized",
        domain="account",
        effect="write",
        mutation_class="local_reversible",  # adds entry to list; removable
        requires_admin=True,
        success_detail="authorized_added",
    ),
    "remove_authorized": FlyerActionDefinition(
        action_id="flyer.account.remove_authorized_requester",
        command="remove_authorized",
        domain="account",
        effect="write",
        mutation_class="local_reversible",  # removes entry from list; re-addable
        requires_admin=True,
        requires_confirmation=True,
        success_detail="authorized_removed",
    ),
    "update_phone": FlyerActionDefinition(
        action_id="flyer.account.update_public_phone",
        command="update_phone",
        domain="account",
        effect="write",
        mutation_class="local_reversible",  # JSON state field; reversible
        requires_admin=True,
        success_detail="public_phone_updated",
    ),
    "update_whatsapp": FlyerActionDefinition(
        action_id="flyer.account.update_business_whatsapp",
        command="update_whatsapp",
        domain="account",
        effect="write",
        mutation_class="local_reversible",  # JSON state field; reversible
        requires_admin=True,
        requires_confirmation=True,
        success_detail="business_whatsapp_updated",
    ),
    "change_plan": FlyerActionDefinition(
        action_id="flyer.billing.request_plan_change",
        command="change_plan",
        domain="billing",
        effect="payment_request",
        # external_irreversible — once a Stripe/Razorpay/manual charge is
        # committed by the provider, local state rollback alone cannot undo
        # the customer's payment. PR-ζ + audit-fail-closed wiring routes
        # customer copy to "under operator review" instead of "no change has
        # been made" for this action when the audit write fails.
        mutation_class="external_irreversible",
        requires_admin=True,
        requires_confirmation=True,
        requires_payment=True,
        success_detail="plan_change_requested",
    ),
    # PR-ζ.1b 2026-05-26 — informational fallback when change_plan
    # request fails downstream. mutation_class omitted (default None) per
    # PR-ζ.1b §3.1 rationale — fallback reply commits no rollback-relevant
    # state mutation.
    "change_plan_fallback": FlyerActionDefinition(
        action_id="flyer.billing.request_plan_change_fallback",
        command="change_plan_fallback",
        domain="billing",
        effect="read",
    ),
    # PR-ζ.1b 2026-05-26 — informational reply for account commands
    # routed via trigger_flyer_account_command result-router (non-
    # change_plan branch). is_regulated_action=False at the build_action_
    # context_for_command call site because the dispatcher matrix already
    # enforced the regulated check; this is the reply-emission step.
    "command_reply": FlyerActionDefinition(
        action_id="flyer.account.command_reply",
        command="command_reply",
        domain="account",
        effect="read",
    ),
    # PR-ζ.1b 2026-05-26 (§13.A) — onboarding multi-step progress reply
    # emitted by trigger_flyer_onboarding when result.handled is True.
    # Customer-facing copy varies by next_status (intake-progress vs
    # state-transition completion). Operator decision: account setup state
    # belongs in ACCOUNT_ACTIONS, not in flat ad-hoc context.
    "onboarding_progress": FlyerActionDefinition(
        action_id="flyer.account.onboarding_progress",
        command="onboarding_progress",
        domain="account",
        effect="write",
        mutation_class="local_reversible",  # onboarding state can be re-walked
    ),
    # PR-ζ.1b 2026-05-26 (§13.B) — durable account asset write (logo/brand
    # template stored on customer account state). Same shape as other
    # account JSON-state updates (update_phone, update_business_name etc.):
    # reversible by re-upload.
    "update_brand_asset": FlyerActionDefinition(
        action_id="flyer.account.update_brand_asset",
        command="update_brand_asset",
        domain="account",
        effect="write",
        mutation_class="local_reversible",
    ),
}


# PR-ζ.1b 2026-05-26 — project-lifecycle actions. The 20 entries cover the
# command-keyed lookups invoked by build_action_context_for_command(...)
# from cf-router project paths. mutation_class omitted (default None per
# FlyerActionDefinition shape) because project actions manipulate
# state/flyer/projects/<id>.json only; no external API commitment is
# involved (Stripe etc. belong to billing-domain ACCOUNT_ACTIONS).
PROJECT_ACTIONS: dict[str, FlyerActionDefinition] = {
    "intake.acknowledged": FlyerActionDefinition(
        action_id="flyer.project.intake_acknowledged",
        command="intake.acknowledged",
        domain="project",
        effect="write",
    ),
    "intake.processing": FlyerActionDefinition(
        action_id="flyer.project.intake_processing",
        command="intake.processing",
        domain="project",
        effect="write",
    ),
    "clarification.request": FlyerActionDefinition(
        action_id="flyer.project.clarification_request",
        command="clarification.request",
        domain="project",
        effect="read",
    ),
    "manual_review.queued": FlyerActionDefinition(
        action_id="flyer.project.manual_review_queued",
        command="manual_review.queued",
        domain="project",
        effect="write",
    ),
    "manual_review.status_replied": FlyerActionDefinition(
        action_id="flyer.project.manual_review_status_replied",
        command="manual_review.status_replied",
        domain="project",
        effect="read",
    ),
    "manual_edit.queued": FlyerActionDefinition(
        action_id="flyer.project.manual_edit_queued",
        command="manual_edit.queued",
        domain="project",
        effect="write",
    ),
    "manual_edit.acknowledged": FlyerActionDefinition(
        action_id="flyer.project.manual_edit_acknowledged",
        command="manual_edit.acknowledged",
        domain="project",
        effect="write",
    ),
    "edit.processing": FlyerActionDefinition(
        action_id="flyer.project.edit_processing",
        command="edit.processing",
        domain="project",
        effect="write",
    ),
    "regeneration.acknowledged": FlyerActionDefinition(
        action_id="flyer.project.regeneration_acknowledged",
        command="regeneration.acknowledged",
        domain="project",
        effect="write",
    ),
    "regeneration.already_in_progress": FlyerActionDefinition(
        action_id="flyer.project.regeneration_already_in_progress",
        command="regeneration.already_in_progress",
        domain="project",
        effect="read",
    ),
    "regeneration.completed": FlyerActionDefinition(
        action_id="flyer.project.regeneration_completed",
        command="regeneration.completed",
        domain="project",
        effect="write",
    ),
    "finalization.completed": FlyerActionDefinition(
        action_id="flyer.project.finalization_completed",
        command="finalization.completed",
        domain="project",
        effect="write",
    ),
    "finalization.reset": FlyerActionDefinition(
        action_id="flyer.project.finalization_reset",
        command="finalization.reset",
        domain="project",
        effect="write",
    ),
    "concept_preview.media_send": FlyerActionDefinition(
        action_id="flyer.project.concept_preview_media",
        command="concept_preview.media_send",
        domain="project",
        effect="read",
    ),
    "concept_preview.cta_text": FlyerActionDefinition(
        action_id="flyer.project.concept_preview_cta",
        command="concept_preview.cta_text",
        domain="project",
        effect="read",
    ),
    "project.recall": FlyerActionDefinition(
        action_id="flyer.project.recall",
        command="project.recall",
        domain="project",
        effect="read",
    ),
    "project.reply": FlyerActionDefinition(
        action_id="flyer.project.generic_reply",
        command="project.reply",
        domain="project",
        effect="read",
    ),
    "guest_order.intake_acknowledged": FlyerActionDefinition(
        action_id="flyer.guest_order.intake_acknowledged",
        command="guest_order.intake_acknowledged",
        domain="guest_order",
        effect="write",
    ),
    "guest_order.reply": FlyerActionDefinition(
        action_id="flyer.guest_order.reply",
        command="guest_order.reply",
        domain="guest_order",
        effect="read",
    ),
    # PR-ζ.1b 2026-05-26 (§13.D) — generation-failure customer notification.
    # Repeated operational notification emitted whenever
    # trigger_generate_flyer_concepts returns gen_ok=False. Callers MUST
    # pass is_regulated_action=False via build_action_context_for_command(
    # ..., is_regulated_action=False) — the registry entry exists for audit-
    # row attribution, but the message is NOT a completion claim.
    # mutation_class="local_reversible" — failure ack mutates no state.
    "generation.failed_ack": FlyerActionDefinition(
        action_id="flyer.generation.failed_ack",
        command="generation.failed_ack",
        domain="project",
        effect="read",
        mutation_class="local_reversible",
    ),
    # PR-ζ.1b 2026-05-26 (§13.D) — finalization-failure customer
    # notification. Same shape as generation.failed_ack. Emitted by
    # _send_flyer_finalization_failed_ack wrapper.
    "finalization.failed_ack": FlyerActionDefinition(
        action_id="flyer.finalization.failed_ack",
        command="finalization.failed_ack",
        domain="project",
        effect="read",
        mutation_class="local_reversible",
    ),
}


def build_action_context(
    *,
    action_id: str,
    is_regulated_action: bool,
    verified_action_result: bool = False,
    mutation_class: Optional[FlyerActionMutationClass] = None,
    audit_row_id: Optional[str] = None,
) -> ActionExecutionContext:
    """Flat named-kwarg constructor for ActionExecutionContext.

    Use for sites that do NOT correspond to a registry entry (system-meta
    replies, ad-hoc fallbacks). verified_action_result defaults False — most
    outbound replies are not completion claims; the caller flips True only
    after a verified action outcome.
    """
    return ActionExecutionContext(
        action_id=action_id,
        is_regulated_action=is_regulated_action,
        verified_action_result=verified_action_result,
        mutation_class=mutation_class,
        audit_row_id=audit_row_id,
    )


def build_action_context_for_command(
    registry: dict[str, FlyerActionDefinition],
    command: str,
    *,
    is_regulated_action: bool = True,
    verified_action_result: bool = False,
    audit_row_id: Optional[str] = None,
) -> ActionExecutionContext:
    """Derive ActionExecutionContext from a registry entry (PROJECT_ACTIONS
    or ACCOUNT_ACTIONS).

    PR-ζ.1b 2026-05-26 (§13.G) — is_regulated_action is a parameter (default
    True) so queue-state / status / informational acks can opt out of the
    chokepoint's regulated-action lint while keeping the registry's action_id
    propagation for audit-row attribution. Default True preserves the
    regulated-action posture for account-command and concept-preview paths.

    mutation_class is pulled from the registry entry (may be None per ζ.1b
    Optional shape); action_id is fully-qualified by the registry.

    Raises KeyError on missing command — intentional: an unmapped command is
    a programmer error, not a runtime contingency. Catching here would hide
    registry drift.
    """
    definition = registry[command]
    return ActionExecutionContext(
        action_id=definition.action_id,
        is_regulated_action=is_regulated_action,
        verified_action_result=verified_action_result,
        mutation_class=definition.mutation_class,
        audit_row_id=audit_row_id,
    )


_PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
_PLAN_MENU_RE = re.compile(
    r"\b(?:show|see|view|list)\b.*\b(?:plans?|pricing|subscription|upgrade)\b|"
    r"\b(?:upgrade|pricing)\s+plans?\s*$|"
    r"\bshow\s+flyer\s+studio\s+plans?\b",
    re.IGNORECASE,
)
_PLAN_CHANGE_RE = re.compile(
    r"\b(?:upgrade|downgrade|switch|move|change|select|choose|start|activate|"
    r"i\s+(?:want|need|would\s+like)|put\s+me)\b.*\b(?:starter|growth|unlimited|"
    r"30\s+flyers?|60\s+flyers?|unlimited\s+flyers?|\$?\s*49(?:\.99)?|"
    r"\$?\s*69(?:\.99)?|\$?\s*199(?:\.00|\.99)?)\b",
    re.IGNORECASE,
)
_UPDATE_WHATSAPP_RE = re.compile(
    r"\b(?:change|update|set|replace)\b.*\b(?:business\s+)?(?:whatsapp|wa|request(?:er|ing)?\s+number|flyer\s+request\s+number)\b",
    re.IGNORECASE,
)
_UPDATE_PUBLIC_PHONE_RE = re.compile(
    r"\b(?:change|update|set|replace)\b.*\b(?:public|business|flyer|contact)\s+phone\b",
    re.IGNORECASE,
)
_UPDATE_BUSINESS_NAME_RE = re.compile(
    r"\b(?:change|update|set|replace)\b.*\b(?:business|account)\s+name\b",
    re.IGNORECASE,
)


def get_account_action_definition(command: str) -> Optional[FlyerActionDefinition]:
    return ACCOUNT_ACTIONS.get(command)


def action_requires_confirmation(command: str) -> bool:
    definition = get_account_action_definition(command)
    return bool(definition and definition.requires_confirmation)


def normalize_account_command_text(text: str, tiers: Optional[list[FlyerPlanTier]] = None) -> Optional[AccountCommandMatch]:
    body = _visible_message_text(text)
    compact = " ".join(body.split())
    if not compact:
        return None
    lower = compact.lower().strip(" .!,:;")

    if _PLAN_MENU_RE.search(compact):
        return AccountCommandMatch("UPGRADE PLAN", "plan_menu", ACCOUNT_ACTIONS["plan_menu"].action_id, 0.88, "semantic_plan_menu")

    plan_id = _semantic_plan_id(compact, tiers or FlyerPlanTier.default_tiers())
    if plan_id and _PLAN_CHANGE_RE.search(compact):
        plan_label = next((tier.label for tier in (tiers or FlyerPlanTier.default_tiers()) if tier.plan_id == plan_id), plan_id)
        return AccountCommandMatch(
            f"CHANGE PLAN {plan_label}",
            "change_plan",
            ACCOUNT_ACTIONS["change_plan"].action_id,
            0.9,
            "semantic_plan_change",
        )

    if lower in {"status", "plan status", "help", "confirm update"}:
        return None

    phone = _extract_phone(compact)
    if phone and _UPDATE_WHATSAPP_RE.search(compact):
        return AccountCommandMatch(
            f"UPDATE BUSINESS WHATSAPP {phone}",
            "update_whatsapp",
            ACCOUNT_ACTIONS["update_whatsapp"].action_id,
            0.86,
            "semantic_update_whatsapp",
        )
    if phone and _UPDATE_PUBLIC_PHONE_RE.search(compact):
        return AccountCommandMatch(
            f"UPDATE PHONE {phone}",
            "update_phone",
            ACCOUNT_ACTIONS["update_phone"].action_id,
            0.86,
            "semantic_update_public_phone",
        )
    if _UPDATE_BUSINESS_NAME_RE.search(compact):
        value = _extract_value_after_to(compact)
        if value:
            return AccountCommandMatch(
                f"UPDATE BUSINESS NAME {value}",
                "update_business_name",
                ACCOUNT_ACTIONS["update_business_name"].action_id,
                0.82,
                "semantic_update_business_name",
            )

    return None


def _semantic_plan_id(text: str, tiers: list[FlyerPlanTier]) -> str:
    lower = text.lower()
    for tier in tiers:
        labels = {tier.plan_id.lower(), tier.label.lower()}
        price = tier.price_cents()
        labels.add(f"{price // 100}")
        labels.add(f"{price / 100:.2f}")
        if tier.included_flyers is not None:
            labels.add(f"{tier.included_flyers} flyers")
            labels.add(f"{tier.included_flyers} flyers/month")
        else:
            labels.add("unlimited flyers")
            labels.add("unlimited flyers/month")
        if any(re.search(rf"(?<!\w){re.escape(label)}(?!\w)", lower) for label in labels if label):
            return tier.plan_id
    return ""


def _extract_phone(text: str) -> str:
    match = _PHONE_RE.search(text)
    return " ".join(match.group(1).split()) if match else ""


def _extract_value_after_to(text: str) -> str:
    match = re.search(r"\b(?:to|as|is)\b\s+(.+?)\s*$", text, flags=re.IGNORECASE)
    return " ".join(match.group(1).split()) if match else ""


def _visible_message_text(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if lines and lines[0].startswith("[shift-agent-sender "):
        return "\n".join(lines[1:]).strip()
    return (text or "").strip()
