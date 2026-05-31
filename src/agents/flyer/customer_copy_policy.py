"""Deterministic Flyer customer-copy policy checks.

This module is intentionally offline and side-effect free. It centralizes the
customer-facing copy terms that must stay in tests and self-evaluation reports
without changing runtime WhatsApp wording.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any


BANNED_CUSTOMER_COPY_TERMS = (
    "queued project",
    "created flyer project",
    "Request processing",
    "Project F",
    "Requested edit:",
    "Original customer request",
    "Authorized relationship",
    "source-preserving workflow",
    "source-preserving edit",
    "operator",
    "manual_edit_required",
    "provider",
    "reason_code",
)

STATIC_CUSTOMER_COPY_FUNCTIONS = (
    "send_flyer_manual_edit_ack",
    "send_flyer_edit_processing_ack",
    "send_flyer_processing_ack",
    "send_flyer_intake_ack",
    "send_flyer_manual_review_ack",
    "flyer_manual_edit_status_reply",
    "flyer_project_status_reply",
)

OUTBOUND_TEXT_FIELDS = (
    "outbound_text",
    "customer_text",
    "message_text",
    "sent_text",
    "reply_text",
)

DUPLICATE_INITIAL_ACK_MARKERS = {
    "processing": ("creating your flyer now", "request processing"),
    "intake": ("have your flyer request", "created flyer project"),
}

PROJECT_ID_RE = re.compile(r"\b(?:project\s+)?F[-\s]?\d{4,}\b", re.IGNORECASE)
PROJECT_PLACEHOLDER_RE = re.compile(
    r"\bproject\s+\{[^}]+\}|\bF[-\s]?\{[^}]+\}",
    re.IGNORECASE,
)
CUSTOMER_COPY_FORBIDDEN_RE = re.compile(
    r"\b(?:F[-\s]?\d{4,}|project\s+F[-\s]?\d{4,}|project\s+\{[^}]+\}|F[-\s]?\{[^}]+\})\b"
    r"|created flyer project|queued project|operator|provider|reason_code|source-preserving",
    re.IGNORECASE,
)

# PR-γ 2026-05-26 — forbidden completion verbs lint (measure/test mode only).
# These verbs make a completion claim about a regulated action (billing,
# payment, account, schedule, delivery). They MUST NOT appear in customer-
# visible copy unless the system has a verified action result (e.g., payment
# webhook received, deterministic-handler success audit row written).
#
# This list is the basis for future PR-ζ chokepoint enforcement at
# safe_io.bridge_post. PR-γ ships the constants + the peer lint function
# (`lint_no_unverified_completion`) for static analysis and tests. NO
# chokepoint hookup, NO ActionExecutionContext, NO send blocking in this PR.
# Existing `scan_customer_text` is intentionally NOT modified — many replay
# tests assert `not scan(text).hits` for legitimate Flyer copy that contains
# words like "sent" / "scheduled" / "applied", and changing the existing scan
# semantics would break them. The new lint function is a peer, not a wrapper.
FORBIDDEN_COMPLETION_VERBS: tuple[str, ...] = (
    "processed",
    "completed",
    "upgraded",
    "downgraded",
    "changed",
    "confirmed",
    "sent",
    "approved",
    "paid",
    "posted",
    "pushed",
    "applied",
    "scheduled",
    "booked",
    "cancelled",
    "canceled",
    "refunded",
)

FORBIDDEN_COMPLETION_VERB_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in FORBIDDEN_COMPLETION_VERBS) + r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CustomerCopyHit:
    category: str
    value: str


@dataclass(frozen=True)
class CustomerCopyScan:
    text: str
    hits: tuple[CustomerCopyHit, ...]

    @property
    def matched_values(self) -> tuple[str, ...]:
        return tuple(hit.value for hit in self.hits)


def normalize_for_copy_policy(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def scan_customer_text(text: str, *, raw_request: str = "") -> CustomerCopyScan:
    body = str(text or "")
    lowered = body.casefold()
    hits: list[CustomerCopyHit] = []
    seen: set[tuple[str, str]] = set()

    def add(category: str, value: str) -> None:
        key = (category, value.casefold())
        if key not in seen:
            seen.add(key)
            hits.append(CustomerCopyHit(category=category, value=value))

    for term in BANNED_CUSTOMER_COPY_TERMS:
        if term.casefold() in lowered:
            add("internal_term", term)

    for match in PROJECT_ID_RE.finditer(body):
        add("project_id", match.group(0))
    for match in PROJECT_PLACEHOLDER_RE.finditer(body):
        add("project_id", match.group(0))

    normalized_raw = normalize_for_copy_policy(raw_request)
    normalized_body = normalize_for_copy_policy(body)
    if len(normalized_raw) >= 8 and normalized_raw in normalized_body:
        add("raw_request_echo", raw_request)

    return CustomerCopyScan(text=body, hits=tuple(hits))


def outbound_text_from_entry(entry: dict[str, Any]) -> str:
    for field in OUTBOUND_TEXT_FIELDS:
        value = entry.get(field)
        if value:
            return str(value)
    return ""


def scan_outbound_entry(entry: dict[str, Any]) -> CustomerCopyScan:
    return scan_customer_text(
        outbound_text_from_entry(entry),
        raw_request=str(entry.get("raw_request") or entry.get("request_text") or ""),
    )


def lint_no_unverified_completion(
    text: str,
    *,
    has_verified_action_result: bool = False,
) -> CustomerCopyScan:
    """Return CustomerCopyScan with `unverified_completion_verb` hits for
    forbidden completion verbs in customer-visible copy.

    PR-γ 2026-05-26 — measure/test mode only. Returns hits but does NOT block
    any send. Future PR-ζ will wire this into safe_io.bridge_post chokepoint
    so unverified completion claims are refused at runtime.

    Semantics:
    - If `has_verified_action_result=True`, returns an empty scan even if the
      text contains forbidden verbs — caller has confirmed evidence of a real
      action result (payment webhook, deterministic-handler success, etc.).
    - If `has_verified_action_result=False` (the default), returns hits for
      every distinct forbidden verb found (case-insensitive, word-boundary
      anchored, deduplicated per verb).

    This is a PEER to `scan_customer_text`, not a wrapper. `scan_customer_text`
    remains unchanged so existing replay tests (which assert `not hits` for
    legitimate Flyer copy containing words like "sent" / "scheduled" /
    "applied") continue to pass.
    """
    body = str(text or "")
    if has_verified_action_result:
        return CustomerCopyScan(text=body, hits=())
    hits: list[CustomerCopyHit] = []
    seen: set[str] = set()
    for match in FORBIDDEN_COMPLETION_VERB_RE.finditer(body):
        verb = match.group(0).lower()
        if verb in seen:
            continue
        seen.add(verb)
        hits.append(CustomerCopyHit(category="unverified_completion_verb", value=verb))
    return CustomerCopyScan(text=body, hits=tuple(hits))


def classify_initial_ack(text: str) -> set[str]:
    lowered = str(text or "").casefold()
    found: set[str] = set()
    for label, markers in DUPLICATE_INITIAL_ACK_MARKERS.items():
        if any(marker in lowered for marker in markers):
            found.add(label)
    return found


def extract_function_block(source: str, function_name: str) -> str:
    lines = source.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if re.match(rf"^def {re.escape(function_name)}\b", line):
            start = index
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("def ") or line.startswith("class "):
            end = index
            break
    return "\n".join(lines[start:end])


def literal_text(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(literal_text(value) for value in node.values)
    if isinstance(node, ast.FormattedValue):
        if isinstance(node.value, ast.Name):
            return "{" + node.value.id + "}"
        if isinstance(node.value, ast.Attribute):
            return "{" + node.value.attr + "}"
        return "{value}"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return literal_text(node.left) + literal_text(node.right)
    return ""


def extract_customer_copy_literals(function_block: str) -> str:
    if not function_block.strip():
        return ""
    try:
        tree = ast.parse(function_block)
    except SyntaxError:
        return function_block
    snippets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            target_names = {
                target.id for target in node.targets
                if isinstance(target, ast.Name)
            }
            if target_names & {"message", "body", "reply", "text"}:
                snippets.append(literal_text(node.value))
        elif isinstance(node, ast.Call):
            func_name = call_name(node)
            if func_name in {"bridge_post", "send_flyer_text"}:
                snippets.extend(literal_text(arg) for arg in node.args)
    return "\n".join(part for part in snippets if part)


def call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def extract_send_call_literals(source: str, function_names: tuple[str, ...] | None = None) -> str:
    blocks: list[str]
    if function_names:
        blocks = [extract_function_block(source, name) for name in function_names]
    else:
        blocks = [source]
    snippets: list[str] = []
    for block in blocks:
        if not block.strip():
            continue
        try:
            tree = ast.parse(block)
        except SyntaxError:
            snippets.append(block)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if call_name(node) not in {"bridge_post", "bridge_send_media", "send_flyer_text"}:
                continue
            snippets.extend(literal_text(arg) for arg in node.args)
            for keyword in node.keywords:
                if keyword.arg in {"caption", "message", "text"}:
                    snippets.append(literal_text(keyword.value))
    return "\n".join(part for part in snippets if part)


# ─────────────────────────────────────────────────────────────────
# P0 #2 — warn-tier customer copy (Commit 2)
# Pure-function formatters: blockers + project → string.
# No workflow side effects (Hermes-as-brain compliance — see plan §6).
# All output verified against scan_customer_text + lint_no_unverified_completion.
# ─────────────────────────────────────────────────────────────────


WARN_TIER_DRAFT_HEADER = "Here's your flyer draft."

# Translation table: blocker-string prefix → (full sentence template, short clause).
# Templates may interpolate {brand} (resolved from project.business_name).
# Order = severity within warn-tier — brand-identity first (most user-visible),
# then event-essential (location/schedule/promotion_end), then core-promise
# (item names), then contact_info. Mirrors the classifier's escalation rules.
_WARN_BLOCKER_TRANSLATIONS: tuple[tuple[str, str, str], ...] = (
    ("visible wrong business/brand:",
     "the spelling of {brand} near the bottom",
     "the spelling near the bottom"),
    ("missing required visible fact: location",
     "the location address isn't showing",
     "the missing location"),
    ("missing required visible fact: schedule",
     "the event time isn't showing",
     "the missing event time"),
    ("missing required visible fact: promotion_end",
     "the promotion end date isn't showing",
     "the missing end date"),
    ("missing required visible fact: item:",
     "one menu item name didn't come through correctly",
     "the menu item issue"),
    ("missing required visible fact: contact_info",
     "the contact info isn't showing",
     "the missing contact"),
)

_WARN_TIER_TOP_N = 2  # Clamp summaries to top-2 most-severe translations.


def _resolve_brand(project: Any) -> str:
    """Read business_name from project.locked_facts.

    Accepts either a Pydantic FlyerProject or a plain dict (matches the
    runtime call shape from cf-router's _dispatch_concept_preview_send,
    which loads projects.json into dicts). Returns empty string when the
    business_name fact is absent — caller's template should degrade
    gracefully (e.g. brand-typo summary uses 'the business name'
    placeholder)."""
    if isinstance(project, dict):
        facts = project.get("locked_facts") or []
        for fact in facts:
            if isinstance(fact, dict) and fact.get("fact_id") == "business_name":
                return str(fact.get("value") or "")
        return ""
    facts = getattr(project, "locked_facts", None) or []
    for fact in facts:
        if getattr(fact, "fact_id", None) == "business_name":
            return str(getattr(fact, "value", "") or "")
    return ""


_PREVIEW_CHECKLIST_MAX_ITEMS = 4
_PREVIEW_CHECKLIST_MAX_CHARS = 700
_PREVIEW_CHECKLIST_VALUE_MAX_CHARS = 120


def _project_facts(project: Any) -> list[Any]:
    if isinstance(project, dict):
        facts = project.get("locked_facts") or []
        return list(facts) if isinstance(facts, list) else []
    facts = getattr(project, "locked_facts", None) or []
    return list(facts)


def _project_fields(project: Any) -> Any:
    if isinstance(project, dict):
        return project.get("fields") or {}
    return getattr(project, "fields", None)


def _fact_id(fact: Any) -> str:
    if isinstance(fact, dict):
        return str(fact.get("fact_id") or "")
    return str(getattr(fact, "fact_id", "") or "")


def _fact_value_text(fact: Any) -> str:
    if isinstance(fact, dict):
        return " ".join(str(fact.get("value") or "").split())
    return " ".join(str(getattr(fact, "value", "") or "").split())


def _field_value(project: Any, name: str) -> str:
    fields = _project_fields(project)
    if isinstance(fields, dict):
        return " ".join(str(fields.get(name) or "").split())
    return " ".join(str(getattr(fields, name, "") or "").split())


def _first_fact_value(project: Any, *fact_ids: str, fallback_field: str = "") -> str:
    wanted = set(fact_ids)
    for fact in _project_facts(project):
        if _fact_id(fact) in wanted:
            value = _fact_value_text(fact)
            if value:
                return value
    if fallback_field:
        return _field_value(project, fallback_field)
    return ""


def _offer_fact_values(project: Any) -> list[str]:
    out: list[str] = []
    for fact in _project_facts(project):
        fact_id = _fact_id(fact)
        if fact_id.startswith("offer:") or fact_id in {"pricing_structure", "offer_price"}:
            value = _fact_value_text(fact)
            if value and value not in out:
                out.append(value)
    return out[:2]


def _item_fact_values(project: Any) -> list[str]:
    names: dict[int, str] = {}
    prices: dict[int, str] = {}
    for fact in _project_facts(project):
        match = re.fullmatch(r"item:(\d+):(name|price)", _fact_id(fact))
        if not match:
            continue
        value = _fact_value_text(fact)
        if not value:
            continue
        index = int(match.group(1))
        if match.group(2) == "name":
            names[index] = value
        else:
            prices[index] = value
    items = []
    for index in sorted(names):
        name = names[index]
        price = prices.get(index, "")
        items.append(f"{name} - {price}" if price else name)
    for fact in _project_facts(project):
        fact_id = _fact_id(fact)
        if not re.fullmatch(r"detail_\d+", fact_id):
            continue
        value = _fact_value_text(fact)
        if value and value not in items:
            items.append(value)
    return items


def _preview_line(label: str, value: str) -> str:
    clean = " ".join(str(value or "").split())
    return f"{label}: {clean}" if clean else ""


def _shorten_preview_value(value: str, limit: int = _PREVIEW_CHECKLIST_VALUE_MAX_CHARS) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip(" ;,.") + "..."


def build_preview_approval_checklist(project: Any) -> str:
    """Build the compact fact checklist shown before preview approval.

    Hermes/Flyer already decided and validated the project facts; this helper
    only formats those facts so the customer can check what they are approving.
    It accepts dict-shaped projects from cf-router and Pydantic projects from
    tests/scripts, and intentionally omits project ids and operational state.
    """
    lines: list[str] = []
    business = _shorten_preview_value(_first_fact_value(project, "business_name", fallback_field="business_name"))
    title = _shorten_preview_value(
        _first_fact_value(
            project,
            "campaign_title",
            "headline",
            fallback_field="event_or_business_name",
        )
    )
    offer_text = _shorten_preview_value("; ".join(_offer_fact_values(project)))
    items = _item_fact_values(project)
    item_text = ""
    if items:
        visible_items = [_shorten_preview_value(item, 72) for item in items[:_PREVIEW_CHECKLIST_MAX_ITEMS]]
        remaining = len(items) - len(visible_items)
        item_text = "; ".join(visible_items)
        if remaining > 0:
            item_text = f"{item_text}; +{remaining} more"
    schedule = _first_fact_value(project, "schedule", fallback_field="event_date")
    time_value = _field_value(project, "event_time")
    if time_value and time_value not in schedule:
        schedule = f"{schedule}; {time_value}" if schedule else time_value
    schedule = _shorten_preview_value(schedule)
    location = _first_fact_value(project, "location", fallback_field="venue_or_location")
    contact = _first_fact_value(project, "contact_phone", fallback_field="contact_info")
    contact = _shorten_preview_value("; ".join(part for part in (location, contact) if part))
    promotion_end = _shorten_preview_value(_first_fact_value(project, "promotion_end"))

    for line in (
        _preview_line("Business", business),
        _preview_line("Title", title),
        _preview_line("Offer", offer_text),
        _preview_line("Items", item_text),
        _preview_line("Schedule", schedule),
        _preview_line("Contact", contact),
        _preview_line("Ends", promotion_end),
    ):
        if line:
            lines.append(line)

    if not lines:
        return ""
    prefix = "Please check these details before approving:"
    required_suffixes = [
        line for line in lines
        if line.startswith(("Items:", "Ends:"))
    ]
    selected: list[str] = []

    def candidate_length(next_lines: list[str]) -> int:
        return len(prefix + "\n" + "\n".join(next_lines))

    for line in required_suffixes:
        if line not in selected:
            selected.append(line)
    for line in lines:
        if line in selected:
            continue
        if candidate_length([*selected, line]) <= _PREVIEW_CHECKLIST_MAX_CHARS:
            selected.append(line)
    # Present in the normal human scan order after budget selection.
    ordered = [line for line in lines if line in selected]
    text = prefix + "\n" + "\n".join(ordered)
    if len(text) <= _PREVIEW_CHECKLIST_MAX_CHARS:
        return text
    # Hard fallback: preserve customer-checkable required facts before optional
    # context. Values are already shortened, so this should only trigger for
    # pathological data.
    text = prefix + "\n" + "\n".join(required_suffixes)
    return text if len(text) <= _PREVIEW_CHECKLIST_MAX_CHARS else prefix


def _translate_warn_blockers(
    blockers: list[str],
    *,
    brand: str,
) -> list[tuple[str, str]]:
    """Match blocker strings against _WARN_BLOCKER_TRANSLATIONS in severity
    order. Returns list of (full_sentence, short_clause) tuples, deduped by
    short_clause (a customer doesn't need "the missing location" listed twice
    even if two blockers happen to map to the same clause)."""
    brand_display = f'"{brand}"' if brand else "the business name"
    seen_short: set[str] = set()
    out: list[tuple[str, str]] = []
    # Iterate the table in severity order; within each table entry, every
    # matching blocker counts once. This keeps brand-identity first even if
    # the input list orders blockers differently.
    for prefix, full_template, short_clause in _WARN_BLOCKER_TRANSLATIONS:
        if short_clause in seen_short:
            continue
        for blocker in blockers:
            if blocker.startswith(prefix):
                full = full_template.format(brand=brand_display)
                out.append((full, short_clause))
                seen_short.add(short_clause)
                break
    return out


def format_warn_tier_correction_summary(
    blockers: list[str],
    project: Any,
) -> tuple[str, str]:
    """Translate warn-tier blockers into customer-language sentences.

    Returns (full_summary, short_summary):
    - full_summary: comma-joined full sentences for the body
      (e.g., "the spelling of 'Lakshmi's Kitchen' near the bottom").
    - short_summary: comma-joined short clauses for the OK-confirm sentence
      (e.g., "the spelling near the bottom").

    Both clamped to top-2 most-severe per plan §6 (warn-tier cohort can
    only have 1-2 warns; 3+ trips the classifier's count cap to block)."""
    brand = _resolve_brand(project)
    translations = _translate_warn_blockers(blockers, brand=brand)[:_WARN_TIER_TOP_N]
    full = ", and ".join(t[0] for t in translations) if translations else ""
    short = " and ".join(t[1] for t in translations) if translations else ""
    return full, short


def build_warn_tier_customer_text(
    blockers: list[str],
    project: Any,
) -> str:
    """Compose the full warn-tier customer message: header + correction
    summary + OK-confirm sentence with short-clause echo (reviewer 2 #3
    refinement — forces conscious confirmation rather than passive 'OK')."""
    full, short = format_warn_tier_correction_summary(blockers, project)
    if not full:
        # Degenerate path — caller passed blockers that didn't match any
        # known translation. Should not occur in practice (Commit 3 only
        # calls this on severity=warn output), but stay safe.
        return f"{WARN_TIER_DRAFT_HEADER}\n\nReply with any changes you'd like."
    return (
        f"{WARN_TIER_DRAFT_HEADER}\n\n"
        f"We noticed a small detail you may want to fix:\n"
        f"{full}.\n\n"
        f"Reply with the correction and we'll redo the design.\n"
        f"Reply OK if you've checked {short} and it's acceptable as drawn."
    )


def format_warn_recovery_revision_ack(
    blockers: list[str],
    project: Any,
) -> str:
    """Acknowledgment for the customer reply-with-fix flow on a warn-tier
    project (reviewer 2 #7 — existing 'revising_design' ack assumes the
    prior draft was clean; warn-recovery context is different and needs
    its own tone).

    The blockers parameter is the prior warn payload's blocker list,
    available for future personalization. Today the ack stays generic to
    avoid presupposing which fix the customer actually sent — they may
    have addressed a different blocker than the classifier flagged.

    Verbs verified against FORBIDDEN_COMPLETION_VERBS: 'Got', 'update',
    'redrawing', 'fix', 'share', 'draft', 'here' — none forbidden."""
    _ = blockers, project  # reserved for future personalization
    return (
        "Got your update — I'm redrawing the flyer with this fix "
        "and will share the new draft here."
    )
