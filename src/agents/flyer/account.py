"""Account lifecycle helpers for WhatsApp-native Flyer Studio."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import calendar
import json
import re
from typing import Optional

from schemas import (
    E164Phone,
    FLYER_AUTHORIZED_REQUESTER_LIMIT,
    FlyerAccountUpdated,
    FlyerCustomerActivated,
    FlyerCustomerProfile,
    FlyerCustomerStore,
    FlyerPaymentRecord,
    FlyerPlanTier,
    FlyerQuotaBlocked,
    FlyerUsageEvent,
    FlyerUsageRecorded,
)

try:
    from safe_io import atomic_write_text  # type: ignore
except ModuleNotFoundError:
    def atomic_write_text(path: Path, text: str) -> None:  # type: ignore[no-redef]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class AccountResult:
    ok: bool
    handled: bool
    reply_text: str
    customer_id: str = ""
    status: str = ""
    quota_allowed: bool = False
    detail: str = ""


ACCOUNT_COMMAND_RE = re.compile(
    r"^\s*(?:"
    r"status|plan status|help|"
    r"don'?t show sample prompts|do not show sample prompts|"
    r"stop sample prompts|hide sample prompts|turn off sample prompts|disable sample prompts|"
    r"stop showing examples|no sample prompts|no examples|"
    r"don'?t show examples|hide examples|stop examples|"
    r"show sample prompts again|enable sample prompts|turn on sample prompts|"
    r"bring back sample prompts|show examples again|bring back examples|"
    r"add (?:authorized )?(?:number|auth)|add authorized number|"
    r"remove authorized number|remove number|"
    r"update business name|change business name|set business name|"
    r"update phone|update business phone|"
    r"update whatsapp|update business whatsapp|"
    r"change plan|upgrade plan|show flyer studio plans|confirm update"
    r")\b",
    re.IGNORECASE,
)

STARTER_PROMPT_OFF_RE = re.compile(
    r"^(?:"
    r"don'?t show sample prompts|do not show sample prompts|"
    r"stop sample prompts|hide sample prompts|turn off sample prompts|disable sample prompts|"
    r"stop showing examples|no sample prompts|no examples|"
    r"don'?t show examples|hide examples|stop examples"
    r")\b",
    re.IGNORECASE,
)

STARTER_PROMPT_ON_RE = re.compile(
    r"^(?:"
    r"show sample prompts again|enable sample prompts|turn on sample prompts|"
    r"bring back sample prompts|show examples again|bring back examples"
    r")\b",
    re.IGNORECASE,
)


def load_customer_store(path: Path) -> FlyerCustomerStore:
    if not path.exists():
        return FlyerCustomerStore()
    return FlyerCustomerStore.model_validate(json.loads(path.read_text(encoding="utf-8")))


def write_customer_store(path: Path, store: FlyerCustomerStore) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, store.model_dump_json(indent=2))


def is_account_command(text: str) -> bool:
    return bool(ACCOUNT_COMMAND_RE.search(_visible_message_text(text)))


def handle_account_command(
    *,
    state_path: Path,
    sender_phone: Optional[str],
    sender_role: str,
    chat_id: str,
    text: str,
    plan_tiers: Optional[list[FlyerPlanTier]] = None,
    payment_provider: str = "manual",
    payment_checkout_url_template: str = "",
    now: Optional[datetime] = None,
    audit_log_path: Optional[Path] = None,
) -> AccountResult:
    now = now or datetime.now(timezone.utc)
    tiers = plan_tiers or FlyerPlanTier.default_tiers()
    store = load_customer_store(state_path)
    customer = store.find_customer_by_sender(sender_phone, chat_id)
    if customer is None:
        return AccountResult(False, False, "", detail="customer_not_found")
    body = " ".join(_visible_message_text(text).split())
    lower = body.lower()
    preference_mode: Optional[str] = None
    if STARTER_PROMPT_OFF_RE.search(lower):
        preference_mode = "off"
    elif STARTER_PROMPT_ON_RE.search(lower):
        preference_mode = "auto"
    if preference_mode:
        store.set_starter_prompt_mode(customer.customer_id, preference_mode)  # type: ignore[arg-type]
        customer = customer.model_copy(update={"updated_at": now})
        _replace_customer(store, customer)
        write_customer_store(state_path, store)
        _audit_account_update(
            audit_log_path,
            customer_id=customer.customer_id,
            command="starter_prompt_mode",
            actor_phone=sender_phone,
            actor_role=sender_role,
            allowed=True,
            reason=f"starter_prompt_{preference_mode}",
        )
        if preference_mode == "off":
            reply = (
                "Flyer Studio\n------------\n"
                "Sample prompts are off for this business account. "
                'Reply "show sample prompts again" to turn them back on.'
            )
        else:
            reply = (
                "Flyer Studio\n------------\n"
                "Sample prompts are on for this business account. "
                "I will show one helpful example when it can save time."
            )
        return AccountResult(True, True, reply, customer.customer_id, customer.status)
    if lower in {"status", "plan status"}:
        customer = _roll_period(customer, now)
        customer = customer.model_copy(update={"monthly_flyers_used": customer.usage_count_for_current_period()})
        _replace_customer(store, customer)
        write_customer_store(state_path, store)
        return AccountResult(True, True, _status_reply(customer, tiers), customer.customer_id, customer.status)
    if lower == "help":
        return AccountResult(True, True, _help_reply(customer), customer.customer_id, customer.status)
    if _is_plan_menu_request(lower):
        return AccountResult(
            True,
            True,
            _plan_menu_reply(customer, tiers, is_admin=customer.is_account_admin(sender_phone, chat_id, sender_role)),
            customer.customer_id,
            customer.status,
        )
    if lower == "confirm update":
        return _confirm_pending_update(
            store=store,
            customer=customer,
            state_path=state_path,
            sender_phone=sender_phone,
            sender_role=sender_role,
            chat_id=chat_id,
            tiers=tiers,
            payment_provider=payment_provider,
            payment_checkout_url_template=payment_checkout_url_template,
            now=now,
            audit_log_path=audit_log_path,
        )
    if customer.status not in {"active", "trial"}:
        return AccountResult(
            True,
            True,
            "Flyer Studio\n------------\nYour account is waiting for payment confirmation. I saved your account details, but flyer generation starts after activation.",
            customer.customer_id,
            customer.status,
        )
    command, value = _parse_mutating_command(body)
    if not command:
        return AccountResult(False, False, "", customer.customer_id, customer.status, detail="not_account_command")
    if not customer.is_account_admin(sender_phone, chat_id, sender_role):
        _audit_account_update(
            audit_log_path,
            customer_id=customer.customer_id,
            command=command,
            actor_phone=sender_phone,
            actor_role=sender_role,
            allowed=False,
            reason="admin_required",
        )
        if command == "change_plan":
            return AccountResult(
                True,
                True,
                _plan_change_admin_required_reply(customer),
                customer.customer_id,
                customer.status,
            )
        return AccountResult(
            True,
            True,
            "Flyer Studio\n------------\nOnly the business WhatsApp number or account owner can change account settings.",
            customer.customer_id,
            customer.status,
        )

    if command in {"remove_authorized", "update_whatsapp", "change_plan"}:
        requested_by = _phone_or_none(sender_phone)
        customer = customer.model_copy(update={
            "pending_account_command": command,
            "pending_account_value": value,
            "pending_account_requested_by": requested_by,
            "pending_account_requested_at": now,
            "updated_at": now,
        })
        _replace_customer(store, customer)
        write_customer_store(state_path, store)
        return AccountResult(
            True,
            True,
            "Flyer Studio\n------------\nPlease reply CONFIRM UPDATE to apply this account change.",
            customer.customer_id,
            customer.status,
        )

    try:
        updated, reply, reason = _apply_account_update(
            store,
            customer,
            command,
            value,
            tiers=tiers,
            payment_provider=payment_provider,
            payment_checkout_url_template=payment_checkout_url_template,
            now=now,
            chat_id=chat_id,
        )
    except ValueError as e:
        return AccountResult(
            True,
            True,
            f"Flyer Studio\n------------\nI could not apply that account update: {e}",
            customer.customer_id,
            customer.status,
            detail="account_update_invalid",
        )
    _replace_customer(store, updated)
    write_customer_store(state_path, store)
    _audit_account_update(
        audit_log_path,
        customer_id=customer.customer_id,
        command=command,
        actor_phone=sender_phone,
        actor_role=sender_role,
        allowed=True,
        reason=reason,
    )
    return AccountResult(True, True, reply, updated.customer_id, updated.status)


def claim_starter_prompt_send(*, state_path: Path, customer_id: str) -> AccountResult:
    store = load_customer_store(state_path)
    customer = store.find_customer_by_id(customer_id)
    if customer is None:
        return AccountResult(False, True, "", customer_id, detail="customer_not_found")
    claimed = store.claim_starter_prompt_send(customer_id)
    if claimed:
        write_customer_store(state_path, store)
    return AccountResult(
        True,
        True,
        "",
        customer_id,
        customer.status,
        quota_allowed=claimed,
        detail="claimed" if claimed else "not_claimed",
    )


def release_starter_prompt_claim(*, state_path: Path, customer_id: str) -> AccountResult:
    store = load_customer_store(state_path)
    customer = store.find_customer_by_id(customer_id)
    if customer is None:
        return AccountResult(False, True, "", customer_id, detail="customer_not_found")
    store.release_starter_prompt_claim(customer_id)
    write_customer_store(state_path, store)
    return AccountResult(True, True, "", customer_id, customer.status, detail="released")


def activate_customer(
    *,
    state_path: Path,
    customer_id: str,
    provider: str,
    payment_reference: str,
    expected_plan: str,
    amount_cents: Optional[int],
    currency: str,
    plan_tiers: Optional[list[FlyerPlanTier]] = None,
    now: Optional[datetime] = None,
    audit_log_path: Optional[Path] = None,
) -> AccountResult:
    now = now or datetime.now(timezone.utc)
    tiers = plan_tiers or FlyerPlanTier.default_tiers()
    if provider not in {"manual", "stripe", "razorpay", "other"}:
        return AccountResult(False, True, "", customer_id, detail="invalid_provider")
    currency = (currency or "USD").upper()
    if not payment_reference.strip():
        return AccountResult(False, True, "", customer_id, detail="payment_reference_required")
    if provider != "manual" and amount_cents is None:
        return AccountResult(False, True, "", customer_id, detail="amount_cents_required")
    store = load_customer_store(state_path)
    customer = store.find_customer_by_id(customer_id) if hasattr(store, "find_customer_by_id") else None
    if customer is None:
        customer = next((row for row in store.customers if row.customer_id == customer_id), None)
    if customer is None:
        return AccountResult(False, True, "", customer_id, detail="customer_not_found")

    for other in store.customers:
        records = list(other.payment_records)
        if other.payment_reference:
            records.append(FlyerPaymentRecord(
                provider=other.billing_provider,
                payment_reference=other.payment_reference,
                plan_id=other.plan_id,
                amount_cents=other.payment_amount_cents,
                currency=other.payment_currency,
                recorded_at=other.activated_at or other.updated_at,
            ))
        for record in records:
            if record.provider != provider or record.payment_reference != payment_reference:
                continue
            same = (
                other.customer_id == customer.customer_id
                and record.plan_id == expected_plan
                and record.amount_cents == amount_cents
                and record.currency == currency
            )
            if same:
                if other.status not in {"active", "trial"}:
                    return AccountResult(False, True, "", other.customer_id, other.status, detail="payment_reference_replay_not_active")
                _audit_activation(audit_log_path, other, provider, payment_reference, amount_cents, currency, idempotent=True)
                return AccountResult(True, True, _activation_reply(other, tiers), other.customer_id, other.status)
            if other.customer_id == customer.customer_id:
                return AccountResult(False, True, "", customer.customer_id, customer.status, detail="payment_reference_replay_mismatch")
            return AccountResult(False, True, "", customer.customer_id, customer.status, detail="payment_reference_already_used")

    target_plan = customer.plan_id
    clearing_pending = False
    if customer.status == "payment_pending":
        target_plan = customer.plan_id
        if expected_plan != target_plan:
            return AccountResult(False, True, "", customer.customer_id, customer.status, detail="expected_plan_mismatch")
    elif customer.status == "active" and customer.pending_plan_id:
        target_plan = customer.pending_plan_id
        clearing_pending = True
        if expected_plan != target_plan:
            return AccountResult(False, True, "", customer.customer_id, customer.status, detail="pending_plan_mismatch")
    else:
        return AccountResult(False, True, "", customer.customer_id, customer.status, detail="no_pending_activation")

    tier = _find_tier(target_plan, tiers)
    if tier is None:
        return AccountResult(False, True, "", customer.customer_id, customer.status, detail="unknown_plan")
    if currency != tier.currency:
        return AccountResult(False, True, "", customer.customer_id, customer.status, detail="currency_mismatch")
    expected_cents = tier.price_cents()
    if provider != "manual" and amount_cents != expected_cents:
        return AccountResult(False, True, "", customer.customer_id, customer.status, detail="amount_mismatch")
    period_start = now
    period_end = _add_one_month(period_start)
    update = {
        "status": "active",
        "plan_id": target_plan,
        "activated_at": customer.activated_at or now,
        "plan_started_at": now,
        "current_period_start": period_start,
        "current_period_end": period_end,
        "monthly_flyers_used": 0,
        "billing_provider": provider,
        "payment_reference": payment_reference,
        "payment_amount_cents": amount_cents,
        "payment_currency": currency,
        "payment_checkout_url": "",
        "payment_records": [
            *customer.payment_records,
            FlyerPaymentRecord(
                provider=provider,  # type: ignore[arg-type]
                payment_reference=payment_reference,
                plan_id=target_plan,
                amount_cents=amount_cents,
                currency=currency,
                recorded_at=now,
            ),
        ],
        "updated_at": now,
    }
    if clearing_pending:
        update.update({
            "pending_plan_id": "",
            "pending_plan_checkout_url": "",
            "pending_plan_requested_at": None,
        })
    customer = customer.model_copy(update=update)
    _replace_customer(store, customer)
    write_customer_store(state_path, store)
    _audit_activation(audit_log_path, customer, provider, payment_reference, amount_cents, currency, idempotent=False)
    return AccountResult(True, True, _activation_reply(customer, tiers), customer.customer_id, customer.status)


def reserve_quota(
    *,
    state_path: Path,
    customer_phone: str,
    project_id: str,
    message_id: str,
    plan_tiers: Optional[list[FlyerPlanTier]] = None,
    now: Optional[datetime] = None,
    audit_log_path: Optional[Path] = None,
) -> AccountResult:
    return _usage_event(
        state_path=state_path,
        customer_phone=customer_phone,
        project_id=project_id,
        message_id=message_id,
        kind="reserved",
        plan_tiers=plan_tiers,
        now=now,
        audit_log_path=audit_log_path,
    )


def finalize_usage(**kwargs: object) -> AccountResult:
    return _usage_event(kind="used", **kwargs)  # type: ignore[arg-type]


def release_quota(**kwargs: object) -> AccountResult:
    return _usage_event(kind="released", **kwargs)  # type: ignore[arg-type]


def _usage_event(
    *,
    state_path: Path,
    customer_phone: str,
    project_id: str,
    message_id: str,
    kind: str,
    plan_tiers: Optional[list[FlyerPlanTier]] = None,
    now: Optional[datetime] = None,
    audit_log_path: Optional[Path] = None,
) -> AccountResult:
    now = now or datetime.now(timezone.utc)
    tiers = plan_tiers or FlyerPlanTier.default_tiers()
    store = load_customer_store(state_path)
    customer = store.find_customer_by_phone(customer_phone)
    if customer is None or customer.status not in {"active", "trial"}:
        return AccountResult(False, True, "", detail="active_customer_not_found")
    customer = _roll_period(customer, now)
    reservation_id = f"{customer.customer_id}:{project_id}"
    latest = _latest_usage_by_reservation(customer).get(reservation_id)
    if kind == "reserved":
        if latest and latest.kind in {"reserved", "used"}:
            customer = customer.model_copy(update={"monthly_flyers_used": customer.usage_count_for_current_period(), "updated_at": now})
            _replace_customer(store, customer)
            write_customer_store(state_path, store)
            return AccountResult(True, True, "", customer.customer_id, customer.status, quota_allowed=True)
        if not customer.can_create_flyer(tiers):
            limit = customer.included_flyer_limit(tiers) or 0
            usage = customer.usage_count_for_current_period()
            _audit_quota_blocked(audit_log_path, customer, project_id, usage, limit)
            return AccountResult(
                True,
                True,
                _quota_blocked_reply(customer, tiers),
                customer.customer_id,
                customer.status,
                quota_allowed=False,
            )
    elif kind in {"used", "released"}:
        if latest is None:
            return AccountResult(False, True, "", customer.customer_id, customer.status, detail="reservation_not_found")
        if latest.kind == kind:
            return AccountResult(True, True, "", customer.customer_id, customer.status, quota_allowed=True)
        if latest.kind == "released" and kind == "used":
            return AccountResult(False, True, "", customer.customer_id, customer.status, detail="reservation_released")
        if latest.kind == "used" and kind == "released":
            return AccountResult(True, True, "", customer.customer_id, customer.status, quota_allowed=True)

    event = FlyerUsageEvent(
        reservation_id=reservation_id,
        project_id=project_id,
        customer_id=customer.customer_id,
        kind=kind,  # type: ignore[arg-type]
        count=1,
        recorded_at=now,
        message_id=message_id,
    )
    customer = customer.model_copy(update={
        "usage_events": [*customer.usage_events, event],
        "updated_at": now,
    })
    customer = customer.model_copy(update={"monthly_flyers_used": customer.usage_count_for_current_period()})
    _replace_customer(store, customer)
    write_customer_store(state_path, store)
    _audit_usage(audit_log_path, customer, event)
    return AccountResult(True, True, "", customer.customer_id, customer.status, quota_allowed=(kind != "released"))


def _parse_mutating_command(text: str) -> tuple[str, str]:
    lower = text.lower()
    for prefix in ("update business name", "change business name", "set business name"):
        if lower.startswith(prefix):
            return "update_business_name", _strip_account_value_prefix(text[len(prefix):].strip())
    for prefix in ("add authorized number", "add number", "add auth"):
        if lower.startswith(prefix):
            return "add_authorized", text[len(prefix):].strip()
    for prefix in ("remove authorized number", "remove number"):
        if lower.startswith(prefix):
            return "remove_authorized", text[len(prefix):].strip()
    for prefix in ("update business phone", "update phone"):
        if lower.startswith(prefix):
            return "update_phone", text[len(prefix):].strip()
    for prefix in ("update business whatsapp", "update whatsapp"):
        if lower.startswith(prefix):
            return "update_whatsapp", text[len(prefix):].strip()
    if lower.startswith("change plan"):
        return "change_plan", text[len("change plan"):].strip()
    return "", ""


def _is_plan_menu_request(lower: str) -> bool:
    cleaned = lower.strip(" .!,:;")
    return (
        cleaned == "upgrade plan"
        or cleaned.startswith("upgrade plan ")
        or cleaned.startswith("upgrade plan -")
        or cleaned == "show flyer studio plans"
    )


def _strip_account_value_prefix(value: str) -> str:
    return re.sub(r"^(?:to|as|is|:|-)\s+", "", value.strip(), flags=re.IGNORECASE).strip()


def _confirm_pending_update(**kwargs: object) -> AccountResult:
    store: FlyerCustomerStore = kwargs["store"]  # type: ignore[assignment]
    customer: FlyerCustomerProfile = kwargs["customer"]  # type: ignore[assignment]
    state_path: Path = kwargs["state_path"]  # type: ignore[assignment]
    sender_phone: Optional[str] = kwargs["sender_phone"]  # type: ignore[assignment]
    sender_role: str = kwargs["sender_role"]  # type: ignore[assignment]
    chat_id: str = kwargs["chat_id"]  # type: ignore[assignment]
    if not customer.pending_account_command:
        return AccountResult(True, True, "Flyer Studio\n------------\nNo pending account update.", customer.customer_id, customer.status)
    if not customer.is_account_admin(sender_phone, chat_id, sender_role):
        return AccountResult(True, True, "Flyer Studio\n------------\nOnly the business WhatsApp number or account owner can confirm this update.", customer.customer_id, customer.status)
    if customer.pending_account_requested_by and _phone_or_none(sender_phone) != customer.pending_account_requested_by:
        return AccountResult(True, True, "Flyer Studio\n------------\nPlease confirm from the same admin number that requested this update.", customer.customer_id, customer.status)
    try:
        updated, reply, reason = _apply_account_update(
            store,
            customer,
            customer.pending_account_command,
            customer.pending_account_value,
            tiers=kwargs["tiers"],  # type: ignore[arg-type]
            payment_provider=kwargs["payment_provider"],  # type: ignore[arg-type]
            payment_checkout_url_template=kwargs["payment_checkout_url_template"],  # type: ignore[arg-type]
            now=kwargs["now"],  # type: ignore[arg-type]
            chat_id=chat_id,
        )
    except ValueError as e:
        return AccountResult(
            True,
            True,
            f"Flyer Studio\n------------\nI could not apply that account update: {e}",
            customer.customer_id,
            customer.status,
            detail="account_update_invalid",
        )
    updated = updated.model_copy(update={
        "pending_account_command": "",
        "pending_account_value": "",
        "pending_account_requested_by": None,
        "pending_account_requested_at": None,
    })
    _replace_customer(store, updated)
    write_customer_store(state_path, store)
    _audit_account_update(
        kwargs.get("audit_log_path"),  # type: ignore[arg-type]
        customer_id=customer.customer_id,
        command=customer.pending_account_command,
        actor_phone=sender_phone,
        actor_role=sender_role,
        allowed=True,
        reason=reason,
    )
    return AccountResult(True, True, reply, updated.customer_id, updated.status)


def _apply_account_update(
    store: FlyerCustomerStore,
    customer: FlyerCustomerProfile,
    command: str,
    value: str,
    *,
    tiers: list[FlyerPlanTier],
    payment_provider: str,
    payment_checkout_url_template: str,
    now: datetime,
    chat_id: str,
) -> tuple[FlyerCustomerProfile, str, str]:
    if command == "add_authorized":
        phone = E164Phone.from_any(value, country_code="US")
        _ensure_phone_available(store, phone, customer.customer_id)
        numbers = list(customer.authorized_request_numbers)
        if phone not in numbers:
            if len(numbers) >= FLYER_AUTHORIZED_REQUESTER_LIMIT:
                return (
                    customer,
                    "Flyer Studio\n------------\n"
                    f"This account already has {FLYER_AUTHORIZED_REQUESTER_LIMIT} authorized requester numbers.\n\n"
                    "Remove one before adding another.",
                    "authorized_limit_reached",
                )
            numbers.append(phone)
        return customer.model_copy(update={"authorized_request_numbers": numbers, "updated_at": now}), "Flyer Studio\n------------\nAuthorized request number added.", "authorized_added"
    if command == "remove_authorized":
        phone = E164Phone.from_any(value, country_code="US")
        numbers = [n for n in customer.authorized_request_numbers if n != phone]
        if not numbers:
            raise ValueError("At least one authorized request number must remain.")
        return customer.model_copy(update={"authorized_request_numbers": numbers, "updated_at": now}), "Flyer Studio\n------------\nAuthorized request number removed.", "authorized_removed"
    if command == "update_phone":
        phone = E164Phone.from_any(value, country_code="US")
        _ensure_phone_available(store, phone, customer.customer_id)
        return customer.model_copy(update={"public_phone": phone, "updated_at": now}), "Flyer Studio\n------------\nPublic flyer phone updated.", "public_phone_updated"
    if command == "update_whatsapp":
        phone = E164Phone.from_any(value, country_code="US")
        _ensure_phone_available(store, phone, customer.customer_id)
        numbers = list(customer.authorized_request_numbers)
        if phone not in numbers and len(numbers) < FLYER_AUTHORIZED_REQUESTER_LIMIT:
            numbers.append(phone)
        return customer.model_copy(update={
            "business_whatsapp_number": phone,
            "authorized_request_numbers": numbers,
            "updated_at": now,
        }), "Flyer Studio\n------------\nBusiness WhatsApp number updated.", "business_whatsapp_updated"
    if command == "update_business_name":
        name = " ".join(value.split()).strip()
        if not name:
            raise ValueError("Please include the new business name.")
        return customer.model_copy(update={
            "business_name": name,
            "updated_at": now,
        }), "Flyer Studio\n------------\nBusiness name updated.", "business_name_updated"
    if command == "change_plan":
        plan_id = _parse_plan_choice(value, tiers)
        url = _checkout_url(payment_checkout_url_template, customer.customer_id, plan_id, chat_id)
        return customer.model_copy(update={
            "pending_plan_id": plan_id,
            "pending_plan_checkout_url": url,
            "pending_plan_requested_at": now,
            "updated_at": now,
        }), _pending_plan_reply(plan_id, url, payment_provider), "plan_change_requested"
    raise ValueError(f"unsupported account command: {command}")


def _status_reply(customer: FlyerCustomerProfile, tiers: list[FlyerPlanTier]) -> str:
    used = customer.usage_count_for_current_period()
    remaining = customer.quota_remaining(tiers)
    limit_text = "unlimited" if remaining is None else f"{remaining} remaining"
    pending = f"\nPending plan change: {customer.pending_plan_id}" if customer.pending_plan_id else ""
    trial_cta = "\nTrial CTA: upgrade to Starter, Growth, or Unlimited to keep creating flyers." if customer.status == "trial" else ""
    return (
        "Flyer Studio\n------------\n"
        f"Account: {customer.customer_id}\n"
        f"Status: {customer.status}\n"
        f"Plan: {customer.plan_id}\n"
        f"Usage this period: {used} used, {limit_text}.{pending}{trial_cta}"
    )


def _help_reply(customer: FlyerCustomerProfile) -> str:
    if customer.status == "payment_pending":
        return "Flyer Studio\n------------\nYour registration is saved and waiting for payment confirmation. You can send STATUS, HELP, or upload a logo/template."
    if customer.status == "trial":
        return (
            "Flyer Studio\n------------\n"
            "Your free trial includes 3 free sample flyers. Send a flyer request, STATUS, or CHANGE PLAN to upgrade. "
            f"Account admins can keep up to {FLYER_AUTHORIZED_REQUESTER_LIMIT} authorized requester numbers."
        )
    return (
        "Flyer Studio\n------------\n"
        "Send a flyer request, STATUS, or HELP. "
        f"Account admins can keep up to {FLYER_AUTHORIZED_REQUESTER_LIMIT} authorized requester numbers, "
        "update phone details, or request a plan change."
    )


def _plan_menu_reply(customer: FlyerCustomerProfile, tiers: list[FlyerPlanTier], *, is_admin: bool) -> str:
    paid_tiers = [tier for tier in tiers if tier.plan_id != "trial"]
    lines = []
    for tier in paid_tiers:
        price = f"{tier.monthly_price_usd:.2f}".rstrip("0").rstrip(".")
        quota = "unlimited flyers/month" if tier.included_flyers is None else f"{tier.included_flyers} flyers/month"
        lines.append(f"{tier.label} - ${price}/month - {quota}")
    message = (
        "Flyer Studio\n------------\n"
        f"Plans for {customer.business_name}:\n"
        + "\n".join(lines)
        + f"\n\nCurrent plan: {customer.plan_id}.\n"
    )
    if is_admin:
        commands = ", ".join(f"CHANGE PLAN {tier.plan_id.upper()}" for tier in paid_tiers)
        return message + f"Reply {commands}."
    return message + _plan_change_admin_required_reply(customer).split("------------\n", 1)[1]


def _plan_change_admin_required_reply(customer: FlyerCustomerProfile) -> str:
    return (
        "Flyer Studio\n------------\n"
        "Plan changes must be requested from the business WhatsApp number "
        f"{customer.business_whatsapp_number} or the account owner.\n\n"
        "This chat can still request flyers for the business."
    )


def _activation_reply(customer: FlyerCustomerProfile, tiers: list[FlyerPlanTier]) -> str:
    remaining = customer.quota_remaining(tiers)
    quota = "unlimited flyers/month" if remaining is None else f"{remaining} flyers remaining this month"
    return (
        "Flyer Studio\n------------\n"
        f"Your {customer.plan_id} plan is active. {quota}.\n"
        "Send your first flyer request now."
    )


def _quota_blocked_reply(customer: FlyerCustomerProfile, tiers: list[FlyerPlanTier]) -> str:
    used = customer.usage_count_for_current_period()
    limit = customer.included_flyer_limit(tiers) or used
    if customer.status == "trial":
        return (
            "Flyer Studio\n------------\n"
            f"Your free trial has used {used}/{limit} sample flyers. "
            "Upgrade now to keep creating professional flyers: reply CHANGE PLAN STARTER, CHANGE PLAN GROWTH, or CHANGE PLAN UNLIMITED."
        )
    return (
        "Flyer Studio\n------------\n"
        f"Your {customer.plan_id} plan has used {used}/{limit} flyers this month. "
        "Reply STATUS for details or CHANGE PLAN to upgrade."
    )


def _pending_plan_reply(plan_id: str, url: str, provider: str) -> str:
    del provider
    pay = f"Pay here: {url}" if url else "Payment link is not configured yet. I saved this plan request for checkout setup."
    return f"Flyer Studio\n------------\nPlan change requested: {plan_id}.\n{pay}\nYour current plan remains active until payment is confirmed."


def _roll_period(customer: FlyerCustomerProfile, now: datetime) -> FlyerCustomerProfile:
    if customer.current_period_start is None or customer.current_period_end is None:
        start = customer.plan_started_at or customer.activated_at or now
        end = _add_one_month(start)
        customer = customer.model_copy(update={"current_period_start": start, "current_period_end": end})
    while customer.current_period_end and now >= customer.current_period_end:
        start = customer.current_period_end
        customer = customer.model_copy(update={"current_period_start": start, "current_period_end": _add_one_month(start)})
    return customer.model_copy(update={"monthly_flyers_used": customer.usage_count_for_current_period()})


def _add_one_month(dt: datetime) -> datetime:
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _latest_usage_by_reservation(customer: FlyerCustomerProfile) -> dict[str, FlyerUsageEvent]:
    latest: dict[str, FlyerUsageEvent] = {}
    for event in customer.usage_events:
        previous = latest.get(event.reservation_id)
        if previous is None or event.recorded_at >= previous.recorded_at:
            latest[event.reservation_id] = event
    return latest


def _replace_customer(store: FlyerCustomerStore, customer: FlyerCustomerProfile) -> None:
    store.customers = [customer if row.customer_id == customer.customer_id else row for row in store.customers]


def _find_tier(plan_id: str, tiers: list[FlyerPlanTier]) -> Optional[FlyerPlanTier]:
    return next((tier for tier in tiers if tier.plan_id == plan_id), None)


def _ensure_phone_available(store: FlyerCustomerStore, phone: str, current_customer_id: str) -> None:
    conflicts = store.customer_ids_for_phone(phone, exclude_customer_id=current_customer_id)
    if conflicts:
        raise ValueError(f"phone number already belongs to customer {', '.join(sorted(conflicts))}")


def _parse_plan_choice(text: str, tiers: list[FlyerPlanTier]) -> str:
    cleaned = text.strip().lower()
    if cleaned.isdigit():
        idx = int(cleaned) - 1
        if 0 <= idx < len(tiers):
            return tiers[idx].plan_id
    for tier in tiers:
        if cleaned in {tier.plan_id.lower(), tier.label.lower()}:
            return tier.plan_id
    raise ValueError("unknown plan")


def _checkout_url(template: str, customer_id: str, plan_id: str, chat_id: str) -> str:
    if not template:
        return ""
    try:
        return template.format(customer_id=customer_id, plan_id=plan_id, chat_id=chat_id)
    except (KeyError, IndexError, ValueError):
        return ""


def _phone_or_none(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    try:
        return E164Phone.from_any(text, country_code="US")
    except ValueError:
        return None


def _visible_message_text(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if lines and lines[0].startswith("[shift-agent-sender "):
        return "\n".join(lines[1:]).strip()
    return (text or "").strip()


def _append_audit(path: Optional[Path], entry_json: str) -> None:
    if path is None:
        return
    try:
        from safe_io import ndjson_append  # type: ignore
        ndjson_append(path, entry_json)
    except Exception:
        pass


def _audit_activation(path: Optional[Path], customer: FlyerCustomerProfile, provider: str, ref: str, amount_cents: Optional[int], currency: str, *, idempotent: bool) -> None:
    _append_audit(path, FlyerCustomerActivated(
        type="flyer_customer_activated",
        ts=datetime.now(timezone.utc),
        customer_id=customer.customer_id,
        plan_id=customer.plan_id,
        provider=provider,  # type: ignore[arg-type]
        payment_reference=ref,
        payment_amount_cents=amount_cents,
        payment_currency=currency,
        idempotent_replay=idempotent,
    ).model_dump_json())


def _audit_account_update(path: Optional[Path], *, customer_id: str, command: str, actor_phone: Optional[str], actor_role: str, allowed: bool, reason: str) -> None:
    _append_audit(path, FlyerAccountUpdated(
        type="flyer_account_updated",
        ts=datetime.now(timezone.utc),
        customer_id=customer_id,
        command=command,
        actor_phone=_phone_or_none(actor_phone),
        actor_role=actor_role,
        allowed=allowed,
        reason=reason,
    ).model_dump_json())


def _audit_usage(path: Optional[Path], customer: FlyerCustomerProfile, event: FlyerUsageEvent) -> None:
    _append_audit(path, FlyerUsageRecorded(
        type="flyer_usage_recorded",
        ts=datetime.now(timezone.utc),
        customer_id=customer.customer_id,
        project_id=event.project_id,
        reservation_id=event.reservation_id,
        kind=event.kind,
        usage_count=customer.usage_count_for_current_period(),
    ).model_dump_json())


def _audit_quota_blocked(path: Optional[Path], customer: FlyerCustomerProfile, project_id: str, usage: int, limit: int) -> None:
    _append_audit(path, FlyerQuotaBlocked(
        type="flyer_quota_blocked",
        ts=datetime.now(timezone.utc),
        customer_id=customer.customer_id,
        project_id=project_id,
        plan_id=customer.plan_id,
        usage_count=usage,
        limit=limit,
    ).model_dump_json())
