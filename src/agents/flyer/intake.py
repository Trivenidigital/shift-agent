"""Adaptive language and guided-intake flow for Flyer Studio."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import re
from typing import Optional

from schemas import (
    E164Phone,
    FlyerCustomerProfile,
    FlyerCustomerStore,
    FlyerIntakeSession,
    FlyerIntakeSource,
)

try:
    from agents.flyer.starter_briefs import starter_brief_message, starter_idea_choices, starter_idea_choices_message  # type: ignore
except ModuleNotFoundError:
    from flyer_starter_briefs import starter_brief_message, starter_idea_choices, starter_idea_choices_message  # type: ignore

try:
    from safe_io import atomic_write_text  # type: ignore
except ModuleNotFoundError:
    def atomic_write_text(path: Path, text: str) -> None:  # type: ignore[no-redef]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


LANGUAGES: list[tuple[str, str, tuple[str, ...]]] = [
    ("en", "English", ("1", "english", "en")),
    ("te", "Telugu", ("2", "telugu", "te")),
    ("hi", "Hindi", ("3", "hindi", "hi")),
    ("ml", "Malayalam", ("4", "malayalam", "ml")),
    ("ta", "Tamil", ("5", "tamil", "ta")),
    ("kn", "Kannada", ("6", "kannada", "kn")),
    ("gu", "Gujarati", ("7", "gujarati", "gu")),
    ("mr", "Marathi", ("8", "marathi", "mr")),
    ("pa", "Punjabi", ("9", "punjabi", "pa")),
    ("es", "Spanish", ("10", "spanish", "es")),
    ("mixed", "Mixed / Other", ("11", "mixed", "other", "mix")),
]


@dataclass(frozen=True)
class IntakeResult:
    handled: bool
    reply_text: str
    action: str = ""
    raw_request: str = ""
    source: str = ""
    preferred_language: str = "en"
    creation_mode: str = ""
    customer_id: str = ""
    reference_media_path: str = ""
    brief_source: str = ""
    brief_approved_at: str = ""
    brief_approved_message_id: str = ""


def load_customer_store(path: Path) -> FlyerCustomerStore:
    if not path.exists():
        return FlyerCustomerStore()
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return FlyerCustomerStore()
    return FlyerCustomerStore.model_validate(json.loads(text))


def write_customer_store(path: Path, store: FlyerCustomerStore) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, store.model_dump_json(indent=2))


def handle_intake_message(
    *,
    state_path: Path,
    chat_id: str,
    sender_phone: Optional[str],
    message_id: str,
    text: str,
    media_path: str = "",
    start_source: Optional[str] = None,
    original_text: str = "",
    now: Optional[datetime] = None,
) -> IntakeResult:
    """Start or advance adaptive Flyer Studio intake.

    This flow is deliberately narrow: it chooses language, chooses guided vs
    text mode, and, for guided mode, collects enough facts to synthesize the
    same raw request accepted by the existing project-creation pipeline.
    """
    now = now or datetime.now(timezone.utc)
    store = load_customer_store(state_path)
    normalized_text = " ".join((text or "").split())
    session = store.find_intake_session(chat_id, sender_phone)
    customer = store.find_customer_by_sender(sender_phone, chat_id)

    if start_source:
        source = _normalize_source(start_source)
        if start_source in {"sample_idea", "starter_idea"} and customer and customer.status in {"trial", "active"}:
            session = FlyerIntakeSession(
                chat_id=chat_id,
                sender_phone=_phone_or_none(sender_phone),
                status="choosing_sample_idea",
                source=source,
                started_at=now,
                updated_at=now,
                last_message_id=message_id,
                original_text=original_text or normalized_text,
                preferred_language=customer.preferred_language,
                creation_mode="sample",
                mode_prompt_version="brief_builder_v1",
                reference_media_path=media_path or "",
                reference_media_message_id=message_id if media_path else "",
            )
            store.replace_intake_session(session)
            write_customer_store(state_path, store)
            return IntakeResult(
                True,
                starter_idea_choices_message(
                    customer.business_category,
                    business_name=customer.business_name,
                    language=customer.preferred_language,
                ),
                "choose_sample_idea",
                source=source,
                preferred_language=customer.preferred_language,
                creation_mode="sample",
                customer_id=customer.customer_id,
            )
        session = FlyerIntakeSession(
            chat_id=chat_id,
            sender_phone=_phone_or_none(sender_phone),
            status="choosing_language",
            source=source,
            started_at=now,
            updated_at=now,
            last_message_id=message_id,
            original_text=original_text or normalized_text,
            preferred_language=(customer.preferred_language if customer else "en"),
            reference_media_path=media_path or "",
            reference_media_message_id=message_id if media_path else "",
        )
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(True, _language_prompt(), "choose_language", source=source)

    if session is None:
        return IntakeResult(False, "")

    media_update = _reference_media_update(media_path, message_id)

    if session.status == "choosing_language":
        language = parse_language_choice(normalized_text)
        if not language:
            session = session.model_copy(update={"last_message_id": message_id, "updated_at": now, **media_update})
            store.replace_intake_session(session)
            write_customer_store(state_path, store)
            return IntakeResult(True, _language_prompt(prefix="Please choose one of these languages."), "choose_language")
        session = session.model_copy(update={
            "preferred_language": language,
            "status": "choosing_mode",
            "mode_prompt_version": "brief_builder_v1",
            "last_message_id": message_id,
            "updated_at": now,
            **media_update,
        })
        _update_customer_language(store, customer, language, now)
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(
            True,
            _mode_prompt(language),
            "choose_mode",
            source=session.source,
            preferred_language=language,
        )

    if session.status == "choosing_mode":
        mode = parse_mode_choice(normalized_text, prompt_version=session.mode_prompt_version)
        if not mode:
            session = session.model_copy(update={"last_message_id": message_id, "updated_at": now, **media_update})
            store.replace_intake_session(session)
            write_customer_store(state_path, store)
            return IntakeResult(True, _mode_prompt(session.preferred_language, prefix="Please choose a creation mode."), "choose_mode")
        if _needs_onboarding(customer, session.source):
            _start_onboarding_from_intake(store, session, mode=mode, message_id=message_id, now=now)
            store.discard_intake_session(session)
            write_customer_store(state_path, store)
            return IntakeResult(
                True,
                _onboarding_handoff_reply(session.source, mode),
                "onboarding_started",
                source=session.source,
                preferred_language=session.preferred_language,
                creation_mode=mode,
                customer_id=customer.customer_id if customer else "",
            )
        if session.source == "quick_flyer":
            store.discard_intake_session(session)
            write_customer_store(state_path, store)
            return IntakeResult(
                True,
                "",
                "start_guest_order",
                source=session.source,
                preferred_language=session.preferred_language,
                creation_mode=mode,
            )
        if mode == "sample":
            session = session.model_copy(update={
                "creation_mode": "sample",
                "status": "choosing_sample_idea",
                "last_message_id": message_id,
                "updated_at": now,
                **media_update,
            })
            store.replace_intake_session(session)
            write_customer_store(state_path, store)
            return IntakeResult(
                True,
                starter_idea_choices_message(
                    customer.business_category if customer else "",
                    business_name=customer.business_name if customer else "",
                    language=session.preferred_language,
                ),
                "choose_sample_idea",
                source=session.source,
                preferred_language=session.preferred_language,
                creation_mode=mode,
                customer_id=customer.customer_id if customer else "",
            )
        if mode == "text":
            include_starter = bool(
                customer
                and customer.status in {"trial", "active"}
                and store.claim_starter_prompt_send(customer.customer_id)
            )
            session = session.model_copy(update={
                "creation_mode": "text",
                "status": "text_awaiting_brief",
                "last_message_id": message_id,
                "updated_at": now,
                **media_update,
            })
            store.replace_intake_session(session)
            write_customer_store(state_path, store)
            return IntakeResult(
                True,
                _text_mode_ready_reply(
                    session.preferred_language,
                    customer=customer,
                    include_starter_brief=include_starter,
                ),
                "text_ready",
                source=session.source,
                preferred_language=session.preferred_language,
                creation_mode=mode,
            )
        session = session.model_copy(update={
            "creation_mode": "guided",
            "status": "guided_collecting_goal",
            "last_message_id": message_id,
            "updated_at": now,
            **media_update,
        })
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(
            True,
            _guided_goal_prompt(session.preferred_language),
            "guided_question",
            source=session.source,
            preferred_language=session.preferred_language,
            creation_mode=mode,
        )

    if session.status == "text_awaiting_brief":
        if _is_cancel_reply(text):
            store.discard_intake_session(session)
            write_customer_store(state_path, store)
            return IntakeResult(True, _brief_cancelled_reply(), "brief_cancelled")
        raw_request = _build_pending_brief_request(session, customer, text, source="text")
        session = session.model_copy(update={
            "brief_raw_request": raw_request,
            "brief_display_request": _visible_reply_text(text),
            "brief_source": "text",
            "status": "brief_pending_approval",
            "last_message_id": message_id,
            "updated_at": now,
            **media_update,
        })
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(
            True,
            _brief_preview_reply(session, customer),
            "brief_preview",
            source=session.source,
            preferred_language=session.preferred_language,
            creation_mode="text",
            customer_id=customer.customer_id if customer else "",
        )

    if session.status == "choosing_sample_idea":
        choice = _parse_sample_choice(text)
        if choice is None:
            session = session.model_copy(update={"last_message_id": message_id, "updated_at": now, **media_update})
            store.replace_intake_session(session)
            write_customer_store(state_path, store)
            return IntakeResult(
                True,
                starter_idea_choices_message(
                    customer.business_category if customer else "",
                    business_name=customer.business_name if customer else "",
                    language=session.preferred_language,
                ),
                "choose_sample_idea",
                source=session.source,
                preferred_language=session.preferred_language,
                creation_mode="sample",
                customer_id=customer.customer_id if customer else "",
            )
        ideas = starter_idea_choices(
            customer.business_category if customer else "",
            business_name=customer.business_name if customer else "",
            language=session.preferred_language,
        )
        selected = ideas[choice]
        raw_request = _build_pending_brief_request(session, customer, selected, source="sample")
        session = session.model_copy(update={
            "brief_raw_request": raw_request,
            "brief_display_request": selected,
            "brief_source": "sample",
            "status": "brief_pending_approval",
            "last_message_id": message_id,
            "updated_at": now,
            **media_update,
        })
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(
            True,
            _brief_preview_reply(session, customer),
            "brief_preview",
            source=session.source,
            preferred_language=session.preferred_language,
            creation_mode="sample",
            customer_id=customer.customer_id if customer else "",
        )

    if session.status == "guided_collecting_goal":
        session = session.model_copy(update={
            "goal": _required_or_original(normalized_text, "promotion"),
            "status": "guided_collecting_schedule",
            "last_message_id": message_id,
            "updated_at": now,
            **media_update,
        })
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(True, _guided_schedule_prompt(), "guided_question")

    if session.status == "guided_collecting_schedule":
        session = session.model_copy(update={
            "schedule": normalized_text or "Not specified",
            "status": "guided_collecting_items",
            "last_message_id": message_id,
            "updated_at": now,
            **media_update,
        })
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(True, _guided_items_prompt(), "guided_question")

    if session.status == "guided_collecting_items":
        session = session.model_copy(update={
            "items": normalized_text or "Use a strong general marketing message",
            "status": "guided_collecting_location",
            "last_message_id": message_id,
            "updated_at": now,
            **media_update,
        })
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(True, _guided_location_prompt(customer), "guided_question")

    if session.status == "guided_collecting_location":
        session = session.model_copy(update={
            "location_contact": normalized_text or _customer_location_contact(customer),
            "status": "guided_collecting_assets",
            "last_message_id": message_id,
            "updated_at": now,
            **media_update,
        })
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(True, _guided_assets_prompt(), "guided_question")

    if session.status == "guided_collecting_assets":
        session = session.model_copy(update={
            "style_assets": normalized_text or "Use saved logo/assets if available",
            "last_message_id": message_id,
            "updated_at": now,
            **media_update,
        })
        raw_request = _synthesize_request(session, customer)
        session = session.model_copy(update={
            "brief_raw_request": raw_request,
            "brief_display_request": _guided_display_request(session),
            "brief_source": "guided",
            "status": "brief_pending_approval",
        })
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(
            True,
            _brief_preview_reply(session, customer),
            "brief_preview",
            source=session.source,
            preferred_language=session.preferred_language,
            creation_mode="guided",
            customer_id=customer.customer_id if customer else "",
            reference_media_path=session.reference_media_path,
        )

    if session.status == "brief_pending_approval":
        if _is_cancel_reply(text):
            store.discard_intake_session(session)
            write_customer_store(state_path, store)
            return IntakeResult(True, _brief_cancelled_reply(), "brief_cancelled")
        if _is_approve_reply(text):
            approved_at = now.isoformat()
            session = session.model_copy(update={
                "brief_approved_at": now,
                "brief_approved_message_id": message_id,
                "last_message_id": message_id,
                "updated_at": now,
            })
            store.replace_intake_session(session)
            write_customer_store(state_path, store)
            return IntakeResult(
                True,
                "",
                "create_project",
                raw_request=session.brief_raw_request,
                source=session.source,
                preferred_language=session.preferred_language,
                creation_mode=session.creation_mode,
                customer_id=customer.customer_id if customer else "",
                reference_media_path=session.reference_media_path,
                brief_source=session.brief_source,
                brief_approved_at=approved_at,
                brief_approved_message_id=message_id,
            )
        session = session.model_copy(update={
            "brief_raw_request": _apply_brief_edit(session.brief_raw_request, text),
            "brief_display_request": _apply_brief_edit(session.brief_display_request, text),
            "last_message_id": message_id,
            "updated_at": now,
            **media_update,
        })
        store.replace_intake_session(session)
        write_customer_store(state_path, store)
        return IntakeResult(
            True,
            _brief_preview_reply(session, customer),
            "brief_preview",
            source=session.source,
            preferred_language=session.preferred_language,
            creation_mode=session.creation_mode,
            customer_id=customer.customer_id if customer else "",
            reference_media_path=session.reference_media_path,
            brief_source=session.brief_source,
        )

    return IntakeResult(False, "")


def parse_language_choice(text: str) -> str:
    choice = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    for code, _label, aliases in LANGUAGES:
        if choice in aliases:
            return code
    return ""


def parse_mode_choice(text: str, *, prompt_version: str = "brief_builder_v1") -> str:
    choice = " ".join((text or "").lower().split())
    if prompt_version != "brief_builder_v1":
        if choice in {"1", "guide", "guided", "guide me", "agent", "agent mode", "guided mode", "step by step", "self guided", "self-guided"}:
            return "guided"
        if choice in {"2", "text", "text mode", "type", "i'll type", "ill type", "i will type", "manual"}:
            return "text"
        return ""
    if choice in {"1", "sample", "samples", "idea", "ideas", "pick an idea", "starter", "starter idea"}:
        return "sample"
    if choice in {"2", "guide", "guided", "guide me", "agent", "agent mode", "guided mode", "step by step", "self guided", "self-guided"}:
        return "guided"
    if choice in {"3", "text", "text mode", "type", "i'll type", "ill type", "i will type", "manual"}:
        return "text"
    return ""


def language_label(code: str) -> str:
    for language_code, label, _aliases in LANGUAGES:
        if language_code == code:
            return label
    return "English"


def _reference_media_update(media_path: str, message_id: str) -> dict[str, str]:
    media_path = (media_path or "").strip()
    if not media_path:
        return {}
    return {
        "reference_media_path": media_path,
        "reference_media_message_id": message_id,
    }


def _normalize_source(source: str) -> FlyerIntakeSource:
    if source in {"start_trial", "act_now", "quick_flyer", "new_flyer"}:
        return source  # type: ignore[return-value]
    return "new_flyer"


def _phone_or_none(phone: Optional[str]) -> Optional[E164Phone]:
    if not phone:
        return None
    try:
        return E164Phone.from_any(phone, country_code="US")
    except ValueError:
        return None


def _language_prompt(*, prefix: str = "") -> str:
    lines = [
        "Flyer Studio",
        "------------",
    ]
    if prefix:
        lines.extend([prefix, ""])
    lines.extend([
        "Choose your preferred flyer language:",
        "",
        "1. English",
        "2. Telugu",
        "3. Hindi",
        "4. Malayalam",
        "5. Tamil",
        "6. Kannada",
        "7. Gujarati",
        "8. Marathi",
        "9. Punjabi",
        "10. Spanish",
        "11. Mixed / Other",
        "",
        "Reply with the number or language name.",
    ])
    return "\n".join(lines)


def _mode_prompt(language: str, *, prefix: str = "") -> str:
    lines = ["Flyer Studio", "------------"]
    if prefix:
        lines.extend([prefix, ""])
    lines.extend([
        f"Great. I will use {language_label(language)}.",
        "",
        "How would you like to create your flyer?",
        "",
        "1. Pick an idea",
        "2. Guide me step by step",
        "3. I'll type my request",
        "",
        "Reply 1, 2, or 3.",
    ])
    return "\n".join(lines)


def _text_mode_ready_reply(
    language: str,
    *,
    customer: Optional[FlyerCustomerProfile] = None,
    include_starter_brief: bool = True,
) -> str:
    reply = (
        "Flyer Studio\n"
        "------------\n"
        f"Text Mode is ready in {language_label(language)}.\n\n"
        "Send your flyer request in one message. You can also attach an existing flyer, logo, menu, photos, or reference image."
    )
    if include_starter_brief and customer and customer.status in {"trial", "active"}:
        reply = f"{reply}\n\n{starter_brief_message(customer.business_category, business_name=customer.business_name, include_opt_out_hint=True)}"
    elif customer and customer.status in {"trial", "active"}:
        reply = f"{reply}\n\nReply with your edited version, or send your own flyer request."
    return reply


def _guided_goal_prompt(language: str) -> str:
    return (
        "Flyer Studio\n"
        "------------\n"
        f"Guided Mode is ready in {language_label(language)}.\n\n"
        "First, what are you promoting? Example: weekend sale, breakfast specials, grand opening, class, service offer."
    )


def _guided_schedule_prompt() -> str:
    return "What date, time, or schedule should appear on the flyer? You can reply SKIP if not needed."


def _guided_items_prompt() -> str:
    return "What items, offers, prices, or key message should appear?"


def _guided_location_prompt(customer: Optional[FlyerCustomerProfile]) -> str:
    suffix = ""
    if customer:
        suffix = f"\n\nSaved location/contact: {customer.business_address}, {customer.public_phone}. Reply USE SAVED to use that."
    return f"What location and contact number should appear on the flyer?{suffix}"


def _guided_assets_prompt() -> str:
    return "Any style preference, logo/photo/reference note, or special instruction? Reply SKIP if none."


def _onboarding_handoff_reply(source: str, mode: str) -> str:
    if source == "start_trial":
        lead = "I will set up your free trial first so I can save your business details."
    else:
        lead = "I will set up your Flyer Studio account first so I can save your business details."
    if mode == "guided":
        mode_line = "After setup, I will guide you step by step."
    elif mode == "sample":
        mode_line = "After setup, I will show sample ideas you can pick from."
    else:
        mode_line = "After setup, you can type your flyer request in one message."
    return (
        "Flyer Studio\n"
        "------------\n"
        f"{lead} {mode_line}\n\n"
        "First, what is your business name?"
    )


def _required_or_original(text: str, label: str) -> str:
    cleaned = text.strip()
    return cleaned if cleaned else f"Not specified {label}"


def _needs_onboarding(customer: Optional[FlyerCustomerProfile], source: str) -> bool:
    if source not in {"start_trial", "act_now"}:
        return False
    return customer is None or customer.status not in {"trial", "active"}


def _replace_onboarding_session(store: FlyerCustomerStore, session) -> None:
    store.onboarding_sessions = [
        s for s in store.onboarding_sessions
        if s.chat_id != session.chat_id and s.sender_phone != session.sender_phone
    ]
    store.onboarding_sessions.append(session)


def _start_onboarding_from_intake(
    store: FlyerCustomerStore,
    session: FlyerIntakeSession,
    *,
    mode: str,
    message_id: str,
    now: datetime,
) -> None:
    from schemas import FlyerOnboardingSession

    onboarding = FlyerOnboardingSession(
        chat_id=session.chat_id,
        sender_phone=session.sender_phone,
        status="collecting_business_name",
        started_at=now,
        updated_at=now,
        last_message_id=message_id,
        preferred_language=session.preferred_language,
        creation_mode=mode,
        plan_id="trial" if session.source == "start_trial" else "",
    )
    _replace_onboarding_session(store, onboarding)


def _update_customer_language(
    store: FlyerCustomerStore,
    customer: Optional[FlyerCustomerProfile],
    language: str,
    now: datetime,
) -> None:
    if customer is None or customer.preferred_language == language:
        return
    store.customers = [
        row.model_copy(update={"preferred_language": language, "updated_at": now})
        if row.customer_id == customer.customer_id else row
        for row in store.customers
    ]


def _customer_location_contact(customer: Optional[FlyerCustomerProfile]) -> str:
    if not customer:
        return "Use the business location and contact number"
    return f"{customer.business_address}. Contact: {customer.public_phone}"


def _synthesize_request(session: FlyerIntakeSession, customer: Optional[FlyerCustomerProfile]) -> str:
    assets = session.style_assets
    if assets.strip().lower() in {"skip", "none", "no"}:
        assets = "Use saved logo/assets if available"
    location = session.location_contact
    if location.strip().lower() in {"use saved", "saved"}:
        location = "Use the saved business address and public phone"
    schedule = session.schedule
    if schedule.strip().lower() in {"skip", "none", "no"}:
        schedule = "No specific date or time"
    reference_note = ""
    if session.reference_media_path:
        reference_note = " Attached reference/sample flyer is available; extract any requested visible items, prices, and layout cues from it."
    profile_note = _profile_instruction(customer)
    return (
        f"Create a professional flyer for {_business_name(customer)}. Promotion: {session.goal}. "
        f"Schedule: {schedule}. Items/offers/prices/key message: {session.items}. "
        f"Location/contact: {location}. Style/assets: {assets}.{reference_note} "
        f"{profile_note} "
        f"Preferred flyer language: {language_label(session.preferred_language)}. "
        "Make the flyer polished, customer-attracting, and ready for WhatsApp and social media."
    )


def _visible_reply_text(text: str) -> str:
    lines = [
        line for line in (text or "").splitlines()
        if not line.strip().startswith("[shift-agent-sender")
    ]
    return " ".join(" ".join(lines).split())


def _is_approve_reply(text: str) -> bool:
    visible = re.sub(r"[^a-z0-9 ]+", " ", _visible_reply_text(text).lower())
    visible = " ".join(visible.split())
    return visible in {
        "approve",
        "approved",
        "yes",
        "yes create it",
        "yes start",
        "go ahead",
        "looks good",
        "ok",
        "okay",
        "start",
        "create it",
    }


def _is_cancel_reply(text: str) -> bool:
    visible = re.sub(r"[^a-z0-9 ]+", " ", _visible_reply_text(text).lower())
    visible = " ".join(visible.split())
    return visible in {"cancel", "stop", "never mind", "nevermind"}


def _parse_sample_choice(text: str) -> Optional[int]:
    visible = _visible_reply_text(text).lower()
    match = re.search(r"\b(?:option\s*)?([12])\b", visible)
    if not match:
        return None
    return int(match.group(1)) - 1


def _build_pending_brief_request(
    session: FlyerIntakeSession,
    customer: Optional[FlyerCustomerProfile],
    request_text: str,
    *,
    source: str,
) -> str:
    request = _required_or_original(_visible_reply_text(request_text), "flyer request")
    reference_note = ""
    if session.reference_media_path:
        reference_note = " Uploaded reference image/template is attached. Use it when designing this flyer."
    return (
        f"Create a professional flyer for {_business_name(customer)}. "
        f"Customer request: {request}. "
        f"{_profile_instruction(customer)} "
        f"Preferred flyer language: {language_label(session.preferred_language)}. "
        f"Brief source: {source}.{reference_note}"
    )


def _brief_preview_reply(
    session: FlyerIntakeSession,
    customer: Optional[FlyerCustomerProfile],
) -> str:
    request = (session.brief_display_request or _customer_visible_request_from_raw(session.brief_raw_request)).strip()
    if len(request) > 900:
        request = f"{request[:897].rstrip()}..."
    return (
        "Flyer Studio\n"
        "------------\n"
        "I will create this flyer:\n\n"
        f"Business: {_business_name(customer)}\n"
        f"Request: {request}\n"
        f"Language: {language_label(session.preferred_language)}\n\n"
        "Reply APPROVE to start, or tell me what to change."
    )


def _brief_cancelled_reply() -> str:
    return (
        "Flyer Studio\n"
        "------------\n"
        "No problem. I stopped this flyer request. Send a new flyer request whenever you are ready."
    )


def _apply_brief_edit(raw_request: str, edit_text: str) -> str:
    edit = _visible_reply_text(edit_text).strip()
    if not edit:
        return raw_request
    return f"{raw_request} Customer update before generation: {edit}."


def _customer_visible_request_from_raw(raw_request: str) -> str:
    text = " ".join((raw_request or "").split())
    match = re.search(r"Customer request:\s*(.+?)(?:\.\s+(?:Use saved|Preferred flyer language|Brief source)|$)", text)
    if match:
        return match.group(1).strip()
    text = re.sub(r"\bPreferred flyer language:\s*[^.]+\.?", "", text)
    text = re.sub(r"\bBrief source:\s*[^.]+\.?", "", text)
    text = re.sub(r"\bUse saved business name, address, phone, and logo\.?", "", text)
    return " ".join(text.split()).strip()


def _guided_display_request(session: FlyerIntakeSession) -> str:
    parts = [
        f"Promotion: {session.goal}",
        f"Schedule: {session.schedule}",
        f"Items/offers: {session.items}",
        f"Location/contact: {session.location_contact}",
        f"Style/assets: {session.style_assets}",
    ]
    return ". ".join(part for part in parts if part.strip())


def _business_name(customer: Optional[FlyerCustomerProfile]) -> str:
    if customer and customer.business_name:
        return customer.business_name
    return "this business"


def _profile_instruction(customer: Optional[FlyerCustomerProfile]) -> str:
    if customer:
        return "Use saved business name, address, phone, and logo."
    return "Use the business details provided by the customer."
