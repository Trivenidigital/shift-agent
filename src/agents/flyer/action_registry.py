"""Deterministic Flyer Studio action authority.

Hermes may classify messy customer language, but this registry is the product
contract: which action exists, who may perform it, whether confirmation/payment
is required, and how common semantic phrasings normalize into command text.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Optional

from schemas import FlyerPlanTier


FlyerActionDomain = Literal["account", "billing", "quota", "guest_order", "project", "preference"]
FlyerActionEffect = Literal["read", "write", "payment_request", "payment_activation"]


@dataclass(frozen=True)
class FlyerActionDefinition:
    action_id: str
    command: str
    domain: FlyerActionDomain
    effect: FlyerActionEffect
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
    ),
    "help": FlyerActionDefinition(
        action_id="flyer.account.help",
        command="help",
        domain="account",
        effect="read",
    ),
    "plan_menu": FlyerActionDefinition(
        action_id="flyer.billing.plan_menu",
        command="plan_menu",
        domain="billing",
        effect="read",
    ),
    "starter_prompt_mode": FlyerActionDefinition(
        action_id="flyer.preference.starter_prompt_mode",
        command="starter_prompt_mode",
        domain="preference",
        effect="write",
        requires_admin=True,
        success_detail="starter_prompt_mode_updated",
    ),
    "update_business_name": FlyerActionDefinition(
        action_id="flyer.account.update_business_name",
        command="update_business_name",
        domain="account",
        effect="write",
        requires_admin=True,
        success_detail="business_name_updated",
    ),
    "add_authorized": FlyerActionDefinition(
        action_id="flyer.account.add_authorized_requester",
        command="add_authorized",
        domain="account",
        effect="write",
        requires_admin=True,
        success_detail="authorized_added",
    ),
    "remove_authorized": FlyerActionDefinition(
        action_id="flyer.account.remove_authorized_requester",
        command="remove_authorized",
        domain="account",
        effect="write",
        requires_admin=True,
        requires_confirmation=True,
        success_detail="authorized_removed",
    ),
    "update_phone": FlyerActionDefinition(
        action_id="flyer.account.update_public_phone",
        command="update_phone",
        domain="account",
        effect="write",
        requires_admin=True,
        success_detail="public_phone_updated",
    ),
    "update_whatsapp": FlyerActionDefinition(
        action_id="flyer.account.update_business_whatsapp",
        command="update_whatsapp",
        domain="account",
        effect="write",
        requires_admin=True,
        requires_confirmation=True,
        success_detail="business_whatsapp_updated",
    ),
    "change_plan": FlyerActionDefinition(
        action_id="flyer.billing.request_plan_change",
        command="change_plan",
        domain="billing",
        effect="payment_request",
        requires_admin=True,
        requires_confirmation=True,
        requires_payment=True,
        success_detail="plan_change_requested",
    ),
}


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
