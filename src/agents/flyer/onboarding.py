"""WhatsApp-native customer onboarding for Hermes Flyer Studio."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import calendar
from difflib import SequenceMatcher
import hashlib
import json
import mimetypes
import os
import re
from typing import Optional

from schemas import (
    E164Phone,
    FLYER_AUTHORIZED_REQUESTER_LIMIT,
    FlyerBrandAsset,
    FlyerCustomerProfile,
    FlyerCustomerStore,
    FlyerIntakeSession,
    FlyerOnboardingSession,
    FlyerPlanTier,
)

try:
    from agents.flyer.starter_briefs import starter_brief_message, starter_idea_choices_message  # type: ignore
except ModuleNotFoundError:
    from flyer_starter_briefs import starter_brief_message, starter_idea_choices_message  # type: ignore

try:
    from safe_io import atomic_write_text  # type: ignore
except ModuleNotFoundError:
    def atomic_write_text(path: Path, text: str) -> None:  # type: ignore[no-redef]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class OnboardingResult:
    handled: bool
    reply_text: str
    next_status: str
    customer_id: str = ""
    customer_created: bool = False


def load_customer_store(path: Path) -> FlyerCustomerStore:
    if not path.exists():
        return FlyerCustomerStore()
    return FlyerCustomerStore.model_validate(json.loads(path.read_text(encoding="utf-8")))


def write_customer_store(path: Path, store: FlyerCustomerStore) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, store.model_dump_json(indent=2))


def handle_onboarding_message(
    *,
    state_path: Path,
    chat_id: str,
    sender_phone: Optional[str],
    message_id: str,
    text: str,
    now: Optional[datetime] = None,
    plan_tiers: Optional[list[FlyerPlanTier]] = None,
    payment_provider: str = "manual",
    payment_checkout_url_template: str = "",
) -> OnboardingResult:
    """Advance onboarding for an unknown or in-progress Flyer customer.

    Returns handled=False only when the sender is already active and should
    proceed to normal flyer routing.
    """
    now = now or datetime.now(timezone.utc)
    tiers = plan_tiers or FlyerPlanTier.default_tiers()
    store = load_customer_store(state_path)
    session = store.find_session(chat_id, sender_phone)
    normalized_text = " ".join((text or "").split())
    customer = store.find_customer_by_phone(sender_phone)
    if customer and customer.status in {"active", "trial"}:
        if session:
            _discard_session(store, session)
            write_customer_store(state_path, store)
        if session and (_is_confirm_reply(normalized_text) or _is_onboarding_start(normalized_text)):
            return OnboardingResult(
                True,
                _existing_account_ready_reply(customer),
                customer.status,
                customer.customer_id,
            )
        return OnboardingResult(False, "", customer.status, customer.customer_id)
    if customer and customer.status == "payment_pending":
        if session:
            _discard_session(store, session)
            write_customer_store(state_path, store)
        return OnboardingResult(
            True,
            _payment_reply(customer.customer_id, customer.plan_id, customer.payment_checkout_url),
            "payment_pending",
            customer.customer_id,
        )

    if session and session.status == "payment_pending" and session.customer_id:
        existing = store.find_customer_by_id(session.customer_id)
        if existing:
            return OnboardingResult(
                True,
                _payment_reply(existing.customer_id, existing.plan_id, existing.payment_checkout_url),
                "payment_pending",
                existing.customer_id,
            )
    if session is None or session.status in {"payment_pending", "active"}:
        trial_requested = _is_trial_start(text)
        session = FlyerOnboardingSession(
            chat_id=chat_id,
            sender_phone=_phone_or_none(sender_phone),
            status="collecting_business_name",
            started_at=now,
            updated_at=now,
            last_message_id=message_id,
            plan_id="trial" if trial_requested else "",
        )
        store.onboarding_sessions = [
            s for s in store.onboarding_sessions
            if s.chat_id != chat_id and (not sender_phone or s.sender_phone != _phone_or_none(sender_phone))
        ]
        store.onboarding_sessions.append(session)
        write_customer_store(state_path, store)
        return OnboardingResult(True, _welcome_reply(tiers, trial_requested=trial_requested), session.status)

    if _is_onboarding_start(normalized_text):
        trial_requested = _is_trial_start(normalized_text)
        session = FlyerOnboardingSession(
            chat_id=session.chat_id,
            sender_phone=session.sender_phone or _phone_or_none(sender_phone),
            status="collecting_business_name",
            started_at=now,
            updated_at=now,
            last_message_id=message_id,
            plan_id="trial" if trial_requested else "",
            pending_brand_assets=session.pending_brand_assets,
        )
        _replace_session(store, session)
        write_customer_store(state_path, store)
        return OnboardingResult(True, _welcome_reply(tiers, trial_requested=trial_requested), session.status)

    special = _handle_session_control(session, text=normalized_text, now=now, tiers=tiers)
    if special is not None:
        session = special
    else:
        try:
            session = _advance_session(
                session=session,
                text=normalized_text,
                message_id=message_id,
                now=now,
                store=store,
                tiers=tiers,
                payment_provider=payment_provider,
                payment_checkout_url_template=payment_checkout_url_template,
            )
        except ValueError as e:
            session = session.model_copy(update={"last_message_id": message_id, "updated_at": now})
            _replace_session(store, session)
            write_customer_store(state_path, store)
            return OnboardingResult(
                True,
                f"Flyer Studio\n------------\n{e}\n\n{_welcome_or_next_prompt(session)}",
                session.status,
                session.customer_id,
            )
    _replace_session(store, session)
    customer_id = ""
    customer_created = False
    include_trial_starter_brief = True
    if session.status in {"payment_pending", "trial"}:
        try:
            customer = store.new_customer(
                business_name=session.business_name,
                business_address=session.business_address,
                public_phone=str(session.public_phone or ""),
                business_whatsapp_number=str(session.business_whatsapp_number or ""),
                authorized_request_number=str(session.authorized_request_number or ""),
                business_category=session.business_category,
                preferred_language=session.preferred_language,
                plan_id=session.plan_id,
                now=now,
                billing_provider=payment_provider,
                payment_checkout_url="",
                primary_chat_id=chat_id,
                onboarded_by_phone=sender_phone,
            )
        except ValueError:
            existing = _find_same_sender_duplicate_customer(store, session, sender_phone, chat_id)
            if existing is None:
                existing = _find_named_duplicate_customer(store, session)
            if existing and existing.status in {"active", "trial", "payment_pending"}:
                if existing.status in {"active", "trial"}:
                    _connect_recovered_sender(
                        store=store,
                        customer=existing,
                        session=session,
                        sender_phone=sender_phone,
                        now=now,
                    )
                    existing = store.find_customer_by_id(existing.customer_id) or existing
                _discard_session(store, session)
                write_customer_store(state_path, store)
                if existing.status == "payment_pending":
                    return OnboardingResult(
                        True,
                        _payment_reply(existing.customer_id, existing.plan_id, existing.payment_checkout_url),
                        existing.status,
                        existing.customer_id,
                    )
                return OnboardingResult(
                    True,
                    _existing_account_ready_reply(existing),
                    existing.status,
                    existing.customer_id,
                )
            session = session.model_copy(update={"status": "confirming_summary", "updated_at": now})
            _replace_session(store, session)
            write_customer_store(state_path, store)
            return OnboardingResult(
                True,
                "Flyer Studio\n------------\n"
                "That phone number belongs to another Flyer Studio account.\n\n"
                "Reply EDIT WHATSAPP or EDIT AUTHORIZED with a different number.",
                session.status,
            )
        customer = customer.model_copy(update={"brand_assets": session.pending_brand_assets})
        if session.plan_id == "trial":
            customer = customer.model_copy(update={
                "status": "trial",
                "activated_at": now,
                "plan_started_at": now,
                "current_period_start": now,
                "current_period_end": _add_one_month(now),
            })
        customer = customer.model_copy(update={
            "payment_checkout_url": _checkout_url(
                template=payment_checkout_url_template,
                customer_id=customer.customer_id,
                plan_id=customer.plan_id,
                chat_id=chat_id,
            )
        })
        store.customers.append(customer)
        customer_id = customer.customer_id
        customer_created = True
        session = session.model_copy(update={"status": customer.status, "updated_at": now, "customer_id": customer.customer_id})
        _replace_session(store, session)
        if customer.status == "trial" and session.creation_mode == "sample":
            include_trial_starter_brief = False
            store.claim_starter_prompt_send(customer.customer_id)
            store.replace_intake_session(FlyerIntakeSession(
                chat_id=chat_id,
                sender_phone=_phone_or_none(sender_phone),
                status="choosing_sample_idea",
                source="new_flyer",
                started_at=now,
                updated_at=now,
                last_message_id=message_id,
                preferred_language=session.preferred_language,
                creation_mode="sample",
                mode_prompt_version="brief_builder_v1",
            ))
        elif customer.status == "trial" and session.creation_mode == "guided":
            include_trial_starter_brief = False
            store.replace_intake_session(FlyerIntakeSession(
                chat_id=chat_id,
                sender_phone=_phone_or_none(sender_phone),
                status="guided_collecting_goal",
                source="new_flyer",
                started_at=now,
                updated_at=now,
                last_message_id=message_id,
                preferred_language=session.preferred_language,
                creation_mode="guided",
                mode_prompt_version="brief_builder_v1",
            ))
        elif customer.status == "trial":
            include_trial_starter_brief = (
                not _has_trailing_flyer_request_after_confirm(normalized_text)
                and store.claim_starter_prompt_send(customer.customer_id)
            )
    write_customer_store(state_path, store)
    return OnboardingResult(
        True,
        _reply_for_session(
            session,
            tiers=tiers,
            customer_id=customer_id,
            store=store,
            include_starter_brief=include_trial_starter_brief,
        ),
        session.status,
        customer_id,
        customer_created,
    )


def store_brand_asset(
    *,
    state_path: Path,
    chat_id: str,
    sender_phone: Optional[str],
    message_id: str,
    media_path: Path,
    text: str,
    sender_role: str = "",
    now: Optional[datetime] = None,
) -> OnboardingResult:
    """Store or replace a customer logo/template from WhatsApp media."""
    now = now or datetime.now(timezone.utc)
    store = load_customer_store(state_path)
    if not media_path.exists() or not media_path.is_file():
        raise ValueError(f"media file not found: {media_path}")
    kind = _brand_asset_kind(text, media_path)

    session = store.find_session(chat_id, sender_phone)
    customer = store.find_customer_by_phone(sender_phone)
    if customer is None and session and session.customer_id:
        customer = next((row for row in store.customers if row.customer_id == session.customer_id), None)
    if customer is not None:
        if not customer.is_account_admin(sender_phone, chat_id, sender_role):
            return OnboardingResult(
                True,
                "Flyer Studio\n------------\nOnly the business WhatsApp number or account owner can replace saved logos/templates for this account.",
                "brand_asset_admin_required",
                customer.customer_id,
            )
        asset = _copy_brand_asset(
            store=store,
            state_path=state_path,
            media_path=media_path,
            kind=kind,
            message_id=message_id,
            now=now,
            notes=text,
            owner_key=customer.customer_id,
        )
        updated = []
        for existing in customer.brand_assets:
            if existing.kind == kind and existing.active:
                updated.append(existing.model_copy(update={"active": False}))
            else:
                updated.append(existing)
        updated.append(asset)
        store.customers = [
            row.model_copy(update={"brand_assets": updated, "updated_at": now})
            if row.customer_id == customer.customer_id else row
            for row in store.customers
        ]
        write_customer_store(state_path, store)
        return OnboardingResult(
            True,
            f"Flyer Studio\n------------\n{kind.title()} saved and will be used for future flyers.",
            "brand_asset_saved",
            customer.customer_id,
        )

    if session is None:
        session = FlyerOnboardingSession(
            chat_id=chat_id,
            sender_phone=_phone_or_none(sender_phone),
            status="collecting_business_name",
            started_at=now,
            updated_at=now,
            last_message_id=message_id,
        )
    asset = _copy_brand_asset(
        store=store,
        state_path=state_path,
        media_path=media_path,
        kind=kind,
        message_id=message_id,
        now=now,
        notes=text,
        owner_key=_asset_owner_key(store, chat_id, sender_phone),
    )
    pending = []
    for existing in session.pending_brand_assets:
        if existing.kind == kind and existing.active:
            pending.append(existing.model_copy(update={"active": False}))
        else:
            pending.append(existing)
    pending.append(asset)
    session = session.model_copy(update={
        "pending_brand_assets": pending,
        "updated_at": now,
        "last_message_id": message_id,
    })
    _replace_session(store, session)
    write_customer_store(state_path, store)
    return OnboardingResult(
        True,
        f"Flyer Studio\n------------\n{kind.title()} saved. I will attach it to this account during onboarding.\n\n{_welcome_or_next_prompt(session)}",
        session.status,
    )


def _advance_session(
    *,
    session: FlyerOnboardingSession,
    text: str,
    message_id: str,
    now: datetime,
    store: FlyerCustomerStore,
    tiers: list[FlyerPlanTier],
    payment_provider: str,
    payment_checkout_url_template: str,
) -> FlyerOnboardingSession:
    del store, payment_provider, payment_checkout_url_template
    update: dict[str, object] = {"last_message_id": message_id, "updated_at": now}
    status = session.status
    if status == "collecting_business_name":
        update.update({"business_name": _require_text(text, "business name"), "status": "collecting_business_address"})
    elif status == "collecting_business_address":
        update.update({"business_address": _parse_business_address(text), "status": "collecting_public_phone"})
    elif status == "collecting_public_phone":
        update.update({"public_phone": _parse_phone(text), "status": "collecting_business_whatsapp"})
    elif status == "collecting_business_whatsapp":
        update.update({
            "business_whatsapp_number": _parse_optional_phone(text, fallback=session.public_phone),
            "status": "collecting_authorized_request_number",
        })
    elif status == "collecting_authorized_request_number":
        update.update({"authorized_request_number": _parse_phone(text), "status": "collecting_business_profile"})
    elif status == "collecting_business_profile":
        category, language = _parse_profile_text(text, default_language=session.preferred_language)
        next_status = "confirming_summary" if session.plan_id else "choosing_plan"
        update.update({"business_category": category, "preferred_language": language, "status": next_status})
    elif status == "choosing_plan":
        update.update({"plan_id": _parse_plan_choice(text, tiers), "status": "confirming_summary"})
    elif status == "confirming_summary":
        edit_update = _parse_confirmation_edit(text, tiers)
        if edit_update:
            update.update(edit_update)
        elif _is_confirm_reply(text):
            update.update({"status": "trial" if session.plan_id == "trial" else "payment_pending"})
        else:
            raise ValueError("Reply CONFIRM to finish registration, or send EDIT FIELD: value.")
    return session.model_copy(update=update)


def _handle_session_control(
    session: FlyerOnboardingSession,
    *,
    text: str,
    now: datetime,
    tiers: list[FlyerPlanTier],
) -> Optional[FlyerOnboardingSession]:
    body = text.strip()
    upper = body.upper()
    if upper == "HELP":
        return session.model_copy(update={"updated_at": now})
    if upper == "RESTART":
        return FlyerOnboardingSession(
            chat_id=session.chat_id,
            sender_phone=session.sender_phone,
            status="collecting_business_name",
            started_at=now,
            updated_at=now,
            last_message_id=session.last_message_id,
            pending_brand_assets=session.pending_brand_assets,
        )
    if upper != "BACK":
        return None
    # BUG-FLYER-QA-2026-05-19-001: trial sessions skip `choosing_plan`
    # entirely on the forward path (see `next_status` around the
    # collecting_business_profile branch). The BACK chain must mirror that
    # skip on the return trip — otherwise a trial user pressing BACK at
    # the summary screen lands in the paid plan chooser and loses
    # `plan_id="trial"`.
    back = {
        "collecting_business_address": ("collecting_business_name", {"business_name": ""}),
        "collecting_public_phone": ("collecting_business_address", {"business_address": ""}),
        "collecting_business_whatsapp": ("collecting_public_phone", {"public_phone": None}),
        "collecting_authorized_request_number": ("collecting_business_whatsapp", {"business_whatsapp_number": None}),
        "collecting_business_profile": ("collecting_authorized_request_number", {"authorized_request_number": None}),
        "choosing_plan": ("collecting_business_profile", {"business_category": "", "preferred_language": "en"}),
        "confirming_summary": (
            ("collecting_business_profile", {"business_category": "", "preferred_language": "en"})
            if session.plan_id == "trial"
            else ("choosing_plan", {"plan_id": ""})
        ),
    }
    target = back.get(session.status)
    if not target:
        return session
    status, clears = target
    del tiers
    return session.model_copy(update={"status": status, "updated_at": now, **clears})


def _replace_session(store: FlyerCustomerStore, session: FlyerOnboardingSession) -> None:
    store.onboarding_sessions = [
        s for s in store.onboarding_sessions
        if s.chat_id != session.chat_id and s.sender_phone != session.sender_phone
    ]
    store.onboarding_sessions.append(session)


def _discard_session(store: FlyerCustomerStore, session: FlyerOnboardingSession) -> None:
    store.onboarding_sessions = [
        s for s in store.onboarding_sessions
        if s.chat_id != session.chat_id and s.sender_phone != session.sender_phone
    ]


def _find_same_sender_duplicate_customer(
    store: FlyerCustomerStore,
    session: FlyerOnboardingSession,
    sender_phone: Optional[str],
    chat_id: str,
) -> Optional[FlyerCustomerProfile]:
    sender = _phone_or_none(sender_phone)
    session_phones = {
        str(phone)
        for phone in (
            session.public_phone,
            session.business_whatsapp_number,
            session.authorized_request_number,
            sender,
        )
        if phone
    }
    for customer in store.customers:
        owned = customer.owned_phone_numbers()
        same_sender = (sender and str(sender) in owned) or (
            customer.primary_chat_id and customer.primary_chat_id == chat_id
        )
        if same_sender and session_phones.intersection(owned):
            return customer
    return None


def _find_named_duplicate_customer(
    store: FlyerCustomerStore,
    session: FlyerOnboardingSession,
) -> Optional[FlyerCustomerProfile]:
    """Recover an already-onboarded business when a second sender retries setup.

    This is intentionally narrower than "any duplicate phone": all duplicate
    numbers in the pending session must point at one existing account, and the
    business names must be close enough to be a human typo/variant. Otherwise a
    stranger could type a real business phone and attach themselves.
    """
    session_phones = [
        str(phone)
        for phone in (
            session.public_phone,
            session.business_whatsapp_number,
            session.authorized_request_number,
        )
        if phone
    ]
    conflict_ids: set[str] = set()
    for phone in session_phones:
        conflict_ids.update(store.customer_ids_for_phone(phone))
    if len(conflict_ids) != 1:
        return None
    customer = store.find_customer_by_id(next(iter(conflict_ids)))
    if customer is None:
        return None
    if not _business_names_match(session.business_name, customer.business_name):
        return None
    return customer


def _business_names_match(left: str, right: str) -> bool:
    l_norm = re.sub(r"[^a-z0-9]+", "", (left or "").lower())
    r_norm = re.sub(r"[^a-z0-9]+", "", (right or "").lower())
    if not l_norm or not r_norm:
        return False
    if l_norm in r_norm or r_norm in l_norm:
        return True
    return SequenceMatcher(None, l_norm, r_norm).ratio() >= 0.86


def _connect_recovered_sender(
    *,
    store: FlyerCustomerStore,
    customer: FlyerCustomerProfile,
    session: FlyerOnboardingSession,
    sender_phone: Optional[str],
    now: datetime,
) -> None:
    updates: dict[str, object] = {"updated_at": now}
    canonical_sender = _phone_or_none(sender_phone)
    if canonical_sender and str(canonical_sender) not in customer.owned_phone_numbers():
        if len(customer.authorized_request_numbers) < FLYER_AUTHORIZED_REQUESTER_LIMIT:
            updates["authorized_request_numbers"] = [
                *customer.authorized_request_numbers,
                E164Phone.from_any(canonical_sender, country_code="US"),
            ]

    pending_assets = list(session.pending_brand_assets)
    if pending_assets:
        existing_assets = list(customer.brand_assets)
        existing_hashes = {asset.sha256 for asset in existing_assets}
        merged_assets = existing_assets
        for asset in pending_assets:
            if asset.sha256 in existing_hashes:
                continue
            if asset.active:
                merged_assets = [
                    old.model_copy(update={"active": False})
                    if old.kind == asset.kind and old.active else old
                    for old in merged_assets
                ]
            merged_assets.append(asset)
            existing_hashes.add(asset.sha256)
        updates["brand_assets"] = merged_assets

    if len(updates) == 1:
        return
    store.customers = [
        row.model_copy(update=updates) if row.customer_id == customer.customer_id else row
        for row in store.customers
    ]


def _reply_for_session(
    session: FlyerOnboardingSession,
    *,
    tiers: list[FlyerPlanTier],
    customer_id: str,
    store: FlyerCustomerStore,
    include_starter_brief: bool = True,
) -> str:
    if session.status == "collecting_business_address":
        return "Flyer Studio\n------------\nGreat. What is the business address?"
    if session.status == "collecting_public_phone":
        return "Flyer Studio\n------------\nWhat public business phone number should appear on flyers?"
    if session.status == "collecting_business_whatsapp":
        return "Flyer Studio\n------------\nWhat is the business WhatsApp number for this account?"
    if session.status == "collecting_authorized_request_number":
        return "Flyer Studio\n------------\nWhat is the authorized flyer request number?"
    if session.status == "collecting_business_profile":
        return (
            "Flyer Studio\n------------\n"
            "What business type and preferred flyer language should I use? "
            "Example: Indian restaurant, English and Telugu."
        )
    if session.status == "choosing_plan":
        return _plans_reply(tiers)
    if session.status == "confirming_summary":
        return _summary_reply(session, tiers)
    if session.status == "payment_pending":
        customer = next((c for c in store.customers if c.customer_id == customer_id), None)
        return _payment_reply(customer_id, session.plan_id, customer.payment_checkout_url if customer else "")
    if session.status == "trial":
        customer = next((c for c in store.customers if c.customer_id == customer_id), None)
        return _trial_active_reply(
            customer_id,
            creation_mode=session.creation_mode,
            language=session.preferred_language,
            customer=customer,
            include_starter_brief=include_starter_brief,
        )
    return _welcome_reply(tiers)


def _welcome_reply(tiers: list[FlyerPlanTier], *, trial_requested: bool = False) -> str:
    trial_line = (
        "Absolutely, let's create a beautiful flyer for your business. "
        "I will set up your free trial first so I can save your business details "
        "and send the finished files here on WhatsApp.\n\n"
        "Your free trial includes 3 free sample flyers.\n\n"
        if trial_requested else ""
    )
    return (
        "Flyer Studio\n------------\n"
        "Welcome. I can set up your flyer account here on WhatsApp.\n\n"
        f"{trial_line}"
        f"{_plan_lines(tiers)}\n\n"
        "First, what is your business name?"
    )


def _plans_reply(tiers: list[FlyerPlanTier]) -> str:
    choices = ", ".join(str(i) for i in range(1, len(tiers) + 1))
    return (
        "Flyer Studio\n------------\n"
        f"Choose a plan by replying {choices}:\n\n"
        f"{_plan_lines(tiers)}"
    )


def _payment_reply(customer_id: str, plan_id: str, checkout_url: str) -> str:
    payment_line = (
        f"Pay here: {checkout_url}"
        if checkout_url
        else "Payment link is not configured yet. Your registration is saved; payment can be confirmed once the checkout link is ready."
    )
    return (
        "Flyer Studio\n------------\n"
        f"Registration saved as {customer_id} on the {plan_id} plan.\n"
        f"{payment_line}\n\n"
        "After payment is confirmed, this WhatsApp number can request flyers."
    )


def _has_trailing_flyer_request_after_confirm(text: str) -> bool:
    trailing = _trailing_text_after_compound_confirm(text)
    return bool(re.search(r"\b(?:create|make|design|need|flyer|poster|banner)\b", trailing.lower()))


def _trailing_text_after_compound_confirm(text: str) -> str:
    body = " ".join((text or "").split())
    match = re.match(
        r"^\s*(?:confirm|ok|yes)\b(?:\s*[\.:,;!\-]\s*|\s+)(?P<trailing>.+)$",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return match.group("trailing").strip()


def _trial_active_reply(
    customer_id: str,
    *,
    creation_mode: str = "",
    language: str = "en",
    customer: Optional[FlyerCustomerProfile] = None,
    include_starter_brief: bool = True,
) -> str:
    if creation_mode == "guided":
        reply = (
            "Flyer Studio\n------------\n"
            f"Free trial active for {customer_id}. You have 3 free sample flyers.\n\n"
            "Guided Mode is ready.\n"
            "First, what are you promoting? Example: weekend sale, breakfast specials, grand opening, class, service offer."
        )
    elif creation_mode == "sample":
        idea_message = starter_idea_choices_message(
            customer.business_category if customer else "",
            business_name=customer.business_name if customer else "",
            language=language,
        ) if customer else "Flyer Studio\n------------\nPick a sample idea to start.\n\nReply 1 or 2. I will show the final brief before generating."
        _header, _sep, body = idea_message.partition("------------\n")
        body = body if body else idea_message
        reply = (
            "Flyer Studio\n------------\n"
            f"Free trial active for {customer_id}. You have 3 free sample flyers.\n\n"
            f"{body}"
        )
    elif creation_mode == "text":
        reply = (
            "Flyer Studio\n------------\n"
            f"Free trial active for {customer_id}. You have 3 free sample flyers.\n\n"
            "Text Mode is ready. Send your first flyer request in one message. "
            "You can also attach an existing flyer, logo, menu, photos, or reference image."
        )
    else:
        del language
        reply = (
            "Flyer Studio\n------------\n"
            f"Free trial active for {customer_id}. You have 3 free sample flyers.\n"
            "Send your first flyer request now. After each sample, I will show the paid onboarding link and plans."
        )
    if include_starter_brief and creation_mode not in {"guided", "sample"} and customer and customer.status in {"trial", "active"}:
        reply = f"{reply}\n\n{starter_brief_message(customer.business_category, business_name=customer.business_name, include_opt_out_hint=True)}"
    return reply


def _existing_account_ready_reply(customer: FlyerCustomerProfile) -> str:
    return (
        "Flyer Studio\n------------\n"
        f"This number is already set up for {customer.business_name}.\n\n"
        "You can start creating a flyer now. Send your flyer request, for example:\n"
        '"Create a breakfast menu flyer for tomorrow from 8 AM to 10 AM."'
    )


def _summary_reply(session: FlyerOnboardingSession, tiers: list[FlyerPlanTier]) -> str:
    plan = next((tier for tier in tiers if tier.plan_id == session.plan_id), None)
    plan_label = plan.label if plan else session.plan_id
    logo_count = len([asset for asset in session.pending_brand_assets if asset.active and asset.kind == "logo"])
    template_count = len([asset for asset in session.pending_brand_assets if asset.active and asset.kind == "template"])
    return (
        "Flyer Studio\n------------\n"
        "Please confirm your account details:\n\n"
        f"Business: {session.business_name}\n"
        f"Address: {session.business_address}\n"
        f"Flyer phone: {session.public_phone}\n"
        f"Business WhatsApp: {session.business_whatsapp_number}\n"
        f"Authorized requester: {session.authorized_request_number}\n"
        f"Profile: {session.business_category}, {session.preferred_language}\n"
        f"Plan: {plan_label}\n"
        f"Assets: {logo_count} logo, {template_count} template\n\n"
        "Reply CONFIRM to finish, or EDIT NAME/ADDRESS/PHONE/WHATSAPP/AUTHORIZED/PROFILE/PLAN: value."
    )


def _plan_lines(tiers: list[FlyerPlanTier]) -> str:
    lines = []
    for index, tier in enumerate(tiers, start=1):
        included = "unlimited flyers/month" if tier.included_flyers is None else f"{tier.included_flyers} flyers/month"
        line = f"{index}. ${tier.monthly_price_usd:.2f} - {included} ({tier.label})"
        if tier.included_flyers is None:
            line += " - includes designer-assisted manual edits for custom requests."
        lines.append(line)
    return "\n".join(lines)


def _parse_phone(text: str) -> str:
    try:
        return E164Phone.from_any(text, country_code="US")
    except ValueError as e:
        raise ValueError("Please send a valid phone number with country code, or a US 10-digit number.") from e


def _parse_optional_phone(text: str, *, fallback: Optional[str]) -> str:
    if _is_skip_optional_reply(text):
        if fallback:
            return str(fallback)
        raise ValueError("Please send a valid phone number, or type SKIP after the public phone is saved.")
    return _parse_phone(text)


def _phone_or_none(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    try:
        return E164Phone.from_any(text, country_code="US")
    except ValueError:
        return None


def _require_text(text: str, label: str) -> str:
    cleaned = text.strip()
    if len(cleaned) < 2:
        raise ValueError(f"Please send the {label}.")
    return cleaned[:300]


def _parse_business_address(text: str) -> str:
    cleaned = _require_text(text, "business address")
    lower = cleaned.lower()
    has_digit = bool(re.search(r"\d", cleaned))
    has_address_signal = bool(re.search(
        r"\b(st|street|rd|road|dr|drive|ave|avenue|blvd|boulevard|ln|lane|ct|court|pl|place|"
        r"pkwy|parkway|hwy|highway|suite|ste|unit|#|north|south|east|west|nc|sc|fl|tx|va|md|oh|ca|ny|nj)\b",
        lower,
    ))
    if not has_digit and not has_address_signal:
        raise ValueError("Please send the full business address, including street/city/state if available.")
    return cleaned[:300]


def _parse_profile_text(text: str, *, default_language: str = "en") -> tuple[str, str]:
    lower = text.lower()
    language = default_language if default_language in {"en", "te", "hi", "ml", "ta", "kn", "gu", "mr", "pa", "es", "mixed", "other"} else "en"
    explicit_languages = [
        name for name in (
            "english", "telugu", "hindi", "malayalam", "tamil", "kannada",
            "gujarati", "marathi", "punjabi", "spanish",
        )
        if name in lower
    ]
    if len(explicit_languages) > 1:
        language = "mixed"
    elif "english" in lower:
        language = "en"
    elif "telugu" in lower:
        language = "te"
    elif "hindi" in lower:
        language = "hi"
    elif "malayalam" in lower:
        language = "ml"
    elif "tamil" in lower:
        language = "ta"
    elif "kannada" in lower:
        language = "kn"
    elif "gujarati" in lower:
        language = "gu"
    elif "marathi" in lower:
        language = "mr"
    elif "punjabi" in lower:
        language = "pa"
    elif "spanish" in lower:
        language = "es"
    elif "mixed" in lower or "multi" in lower:
        language = "mixed"
    category = re.sub(
        r"\b(english|telugu|hindi|malayalam|tamil|kannada|gujarati|marathi|punjabi|spanish|mixed|language|languages|and)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    category = re.sub(r"[,;]+", " ", category)
    category = " ".join(category.split())
    if not category:
        raise ValueError(
            "Please include the business type, for example: Hair salon, English."
        )
    return category[:120], language


def _parse_plan_choice(text: str, tiers: list[FlyerPlanTier]) -> str:
    cleaned = text.strip().lower()
    if cleaned.isdigit():
        idx = int(cleaned) - 1
        if 0 <= idx < len(tiers):
            return tiers[idx].plan_id
    for tier in tiers:
        if cleaned in {tier.plan_id.lower(), tier.label.lower()}:
            return tier.plan_id
    choices = ", ".join(str(i) for i in range(1, len(tiers) + 1))
    raise ValueError(f"Please choose a plan by replying {choices}.")


def _parse_confirmation_edit(text: str, tiers: list[FlyerPlanTier]) -> dict[str, object]:
    match = re.match(r"^\s*EDIT\s+([A-Z ]+?)\s*:\s*(.+?)\s*$", text, flags=re.IGNORECASE)
    if not match:
        return {}
    field = " ".join(match.group(1).lower().split())
    value = match.group(2).strip()
    if field in {"name", "business", "business name"}:
        return {"business_name": _require_text(value, "business name")}
    if field in {"address", "business address"}:
        return {"business_address": _parse_business_address(value)}
    if field in {"phone", "public phone", "flyer phone"}:
        return {"public_phone": _parse_phone(value)}
    if field in {"whatsapp", "business whatsapp"}:
        return {"business_whatsapp_number": _parse_phone(value)}
    if field in {"authorized", "authorized number", "requester"}:
        return {"authorized_request_number": _parse_phone(value)}
    if field in {"profile", "business profile"}:
        category, language = _parse_profile_text(value)
        return {"business_category": category, "preferred_language": language}
    if field == "plan":
        return {"plan_id": _parse_plan_choice(value, tiers)}
    raise ValueError("Unknown edit field. Use EDIT NAME, ADDRESS, PHONE, WHATSAPP, AUTHORIZED, PROFILE, or PLAN.")


def _is_confirm_reply(text: str) -> bool:
    body = " ".join((text or "").strip().lower().split())
    if body in {"confirm", "ok", "okay", "ok proceed", "proceed", "yes", "yes proceed", "y", "go ahead", "looks good"}:
        return True
    if _has_trailing_flyer_request_after_confirm(text):
        return True
    return bool(re.match(r"^\s*CONFIRM\b(?:\s*[\.:,;!\-]\s*|\s*$|\s+.+$)", text or "", flags=re.IGNORECASE | re.DOTALL))


def _is_skip_optional_reply(text: str) -> bool:
    body = " ".join((text or "").strip().lower().split())
    return body in {
        "no", "none", "skip", "no business account", "no business whatsapp",
        "same", "same as public", "same as phone", "use same", "use public phone",
    }


def _checkout_url(*, template: str, customer_id: str, plan_id: str, chat_id: str) -> str:
    if not template:
        return ""
    try:
        return template.format(customer_id=customer_id, plan_id=plan_id, chat_id=chat_id)
    except (KeyError, IndexError, ValueError):
        return ""


def _is_trial_start(text: str) -> bool:
    return bool(re.search(
        r"\b(free\s+trial|start\s+trial|try\s+free|3\s+free|help\s+me\s+create\s+a\s+beautiful\s+flyer)\b",
        text or "",
        flags=re.IGNORECASE,
    ))


def _is_onboarding_start(text: str) -> bool:
    return bool(re.search(
        r"\b("
        r"free\s+trial|start\s+trial|try\s+free|3\s+free|"
        r"help\s+me\s+create\s+a\s+beautiful\s+flyer|"
        r"start\s+free\s+(?:trial|trail)|"
        r"act\s+now!?\s+save\s+time\s+and\s+money|"
        r"set\s+up\s+flyer\s+studio"
        r")\b",
        text or "",
        flags=re.IGNORECASE,
    ))


def _add_one_month(dt: datetime) -> datetime:
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _brand_asset_kind(text: str, media_path: Path) -> str:
    lower = f"{text} {media_path.name}".lower()
    if any(
        token in lower
        for token in (
            "template", "sample", "reference", "old flyer", "previous flyer",
            "flyer", "flier", "poster", "menu", "combo", "price", "special",
            "dosa", "idly", "breakfast", "lunch", "dinner",
        )
    ):
        return "template"
    return "logo"


def _asset_owner_key(store: FlyerCustomerStore, chat_id: str, sender_phone: Optional[str]) -> str:
    customer = store.find_customer_by_phone(sender_phone)
    if customer:
        return customer.customer_id
    canonical = _phone_or_none(sender_phone)
    if canonical:
        return re.sub(r"\D", "", canonical)
    return re.sub(r"[^A-Za-z0-9_-]", "_", chat_id)[:80] or "unknown"


def _copy_brand_asset(
    *,
    store: FlyerCustomerStore,
    state_path: Path,
    media_path: Path,
    kind: str,
    message_id: str,
    now: datetime,
    notes: str,
    owner_key: str,
) -> FlyerBrandAsset:
    raw = media_path.read_bytes()
    if not raw:
        raise ValueError("brand asset media file is empty")
    asset_id = f"B{store.next_brand_asset_sequence:04d}"
    store.next_brand_asset_sequence += 1
    suffix = media_path.suffix.lower() or ".bin"
    dest_dir = state_path.parent / "brand_assets" / owner_key
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{asset_id}-{kind}{suffix}"
    tmp = dest.with_name(f".{dest.name}.tmp.{os.getpid()}")
    with open(tmp, "xb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dest)
    mime, _ = mimetypes.guess_type(str(dest))
    return FlyerBrandAsset(
        asset_id=asset_id,
        kind=kind,  # type: ignore[arg-type]
        path=str(dest),
        mime_type=mime or "application/octet-stream",
        sha256=hashlib.sha256(raw).hexdigest(),
        original_message_id=message_id,
        received_at=now,
        active=True,
        notes=notes[:500],
    )


def _welcome_or_next_prompt(session: FlyerOnboardingSession) -> str:
    if session.status == "collecting_business_name":
        return "What is your business name?"
    if session.status == "collecting_business_address":
        return "What is the business address?"
    if session.status == "collecting_public_phone":
        return "What public business phone number should appear on flyers?"
    if session.status == "collecting_business_whatsapp":
        return "What is the business WhatsApp number for this account?"
    if session.status == "collecting_authorized_request_number":
        return "What is the authorized flyer request number?"
    if session.status == "collecting_business_profile":
        return "What business type and preferred flyer language should I use?"
    if session.status == "choosing_plan":
        return "Choose a plan by replying 1, 2, or 3."
    if session.status == "confirming_summary":
        return "Reply CONFIRM, or EDIT FIELD: value."
    return "You can continue here any time."
