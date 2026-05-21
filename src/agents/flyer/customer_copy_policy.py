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
CUSTOMER_COPY_FORBIDDEN_RE = re.compile(
    r"\b(?:F[-\s]?\d{4,}|project\s+F[-\s]?\d{4,})\b"
    r"|created flyer project|queued project|operator|provider|reason_code|source-preserving",
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
