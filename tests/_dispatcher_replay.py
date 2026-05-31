"""Dispatcher-replay harness library (Layer C, v0.1).

Loads paired raw_inbound + expected-handler fixtures, replays each through a
caller-supplied LLM function, and reports routing decisions vs. baseline.

Design constraints (per drift rules + Hermes-first):
  - No hard dependency on any specific LLM provider. Caller injects the LLM
    function via dependency injection so tests can run with a mock and
    production validation runs with a real OpenRouter call.
  - Fixture format is JSONL (one JSON object per line) so fixtures can be
    appended cheaply as real production traffic accumulates.
  - The harness does NOT execute the dispatcher SKILL bash steps (no
    subprocess invocation of validate-sender-block, identify-sender, etc.).
    Those steps are deterministic and tested elsewhere. This harness tests
    the LLM's *routing decision* given the SKILL.md + the inbound + the
    deterministic-helper outputs (sender_block, identity, state_files).

Fixture format (one JSON object per line in dispatcher_traffic.jsonl):
  {
    "id": "...",                      # unique fixture id
    "category": "...",                # routing-matrix row category
    "description": "...",             # human-readable
    "source_row": "matrix row N",     # which matrix row this exercises
    "input": {
      "raw_text": "<full inbound including [shift-agent-sender v=1 ...] block>",
      "sender_block": {...},          # output of validate-sender-block
      "identity": {...},              # output of identify-sender
      "media_type": "image"|null,     # only set if image/document
      "config": {...},                # cfg.<feature>.enabled flags
      "state_files": {                # state-file contents at decision time
        "catering-menu-pending.json": [...],
        "catering-leads.json": [...],
        "pending.json": [...],
        "expense_leads.json": [...]
      }
    },
    "expected_handler": "<handler skill name>"
  }
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


# Sentinel returned by parse_handler_from_response when no handler name is
# mentioned in the LLM response. Distinguishable in assertion messages from
# "wrong handler" — see ReplayResult.parse_failed.
NO_HANDLER_FOUND = "<no-handler-found>"


# SHA-256 of dispatch_shift_agent/SKILL.md at the time these fixtures were
# authored. The drift gate (test_skill_md_hash_unchanged) fails LOUD when
# SKILL.md changes — forcing whoever changes it to also re-validate that
# the fixtures + priority mock still reflect the new matrix. This is the
# guardrail that prevents the priority mock from silently drifting against
# a changed SKILL while tests stay green.
#
# When SKILL.md legitimately changes:
#   1. Re-run the harness with mock_llm_priority_order
#   2. Update fixtures + mock to reflect any new priority rules
#   3. Update SKILL_MD_KNOWN_SHA256 below to the new hash
#   4. Document the change in the commit message
SKILL_MD_KNOWN_SHA256 = "a658a26c01c401463948b4ec0f719c76f16efcfe634f80aaf4b6066da0a8d3e7"


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "dispatcher_traffic.jsonl"
DISPATCHER_SKILL_PATH = (
    REPO_ROOT / "src" / "agents" / "shift" / "skills" / "dispatch_shift_agent" / "SKILL.md"
)


@dataclass
class Fixture:
    id: str
    category: str
    description: str
    source_row: str
    input_payload: dict
    expected_handler: str
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Fixture":
        return cls(
            id=d["id"],
            category=d["category"],
            description=d.get("description", ""),
            source_row=d.get("source_row", ""),
            input_payload=d["input"],
            expected_handler=d["expected_handler"],
            notes=d.get("notes", ""),
        )


@dataclass
class ReplayResult:
    fixture_id: str
    expected_handler: str
    actual_handler: str
    match: bool
    raw_response: str = ""
    parse_failed: bool = False  # True when actual_handler == NO_HANDLER_FOUND

    def __bool__(self) -> bool:
        return self.match

    def diagnostic(self) -> str:
        """Human-readable failure message that distinguishes 'parser couldn't
        find a handler' from 'LLM picked the wrong handler'."""
        if self.match:
            return f"OK: {self.fixture_id} → {self.actual_handler}"
        if self.parse_failed:
            return (
                f"PARSE_FAIL: {self.fixture_id} — LLM response contained no "
                f"recognizable handler name. expected={self.expected_handler!r}. "
                f"raw={self.raw_response!r}"
            )
        return (
            f"WRONG_HANDLER: {self.fixture_id} — expected={self.expected_handler!r} "
            f"actual={self.actual_handler!r} raw={self.raw_response!r}"
        )


# Caller-supplied signature: takes dispatcher SKILL.md + fixture input dict, returns
# (raw_llm_response, parsed_handler). Parsed handler is the harness's best guess
# at which downstream skill the LLM picked. The library provides a default parser;
# callers can override.
LLMCaller = Callable[[str, dict], tuple[str, str]]


def load_fixtures(path: Path = FIXTURE_PATH) -> list[Fixture]:
    """Load all fixtures from the JSONL file."""
    if not path.exists():
        raise FileNotFoundError(f"Fixture file not found: {path}")
    fixtures = []
    with path.open("r", encoding="utf-8") as fh:
        for line_num, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                d = json.loads(line)
                fixtures.append(Fixture.from_dict(d))
            except (json.JSONDecodeError, KeyError) as e:
                raise ValueError(f"Malformed fixture at line {line_num}: {e}") from e
    return fixtures


def load_dispatcher_skill(path: Path = DISPATCHER_SKILL_PATH) -> str:
    """Load the dispatcher SKILL.md content (the system prompt for the routing decision)."""
    if not path.exists():
        raise FileNotFoundError(f"Dispatcher SKILL not found: {path}")
    return path.read_text(encoding="utf-8")


def compute_skill_md_hash(path: Path = DISPATCHER_SKILL_PATH) -> str:
    """SHA-256 of the dispatcher SKILL.md, normalized to LF line endings.

    Normalization matters because Windows checkouts may CRLF the file via
    .gitattributes; we want the hash to be platform-independent.
    """
    content = load_dispatcher_skill(path)
    normalized = content.replace("\r\n", "\n").encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


# Known handler names — keep in sync with the routing matrix in dispatch_shift_agent/SKILL.md.
# Used by the default parser to extract a handler name from free-form LLM output.
KNOWN_HANDLERS = frozenset({
    "apply_catering_menu_decision",
    "handle_catering_owner_approval",
    "expense_bookkeeper_dispatcher",
    "handle_owner_command",
    "update_catering_menu",
    "flyer_dispatcher",
    "catering_dispatcher",
    "compliance_owner_query",
    "customer_location_query",
    "handle_candidate_response",
    "handle_sick_call",
    "unknown_sender_declined",
})


def parse_handler_from_response(response: str) -> str:
    """Default parser: scan the LLM response for the first known handler name.

    Production-faithful would be to actually invoke the SKILL chain, but for
    the harness we only need to know which handler the LLM chose. The
    dispatcher SKILL.md asks the LLM to produce a routing decision, so a
    handler name should appear verbatim in the response.

    Returns the first match, or NO_HANDLER_FOUND sentinel if no known handler
    is mentioned. Caller code checks `result == NO_HANDLER_FOUND` to
    distinguish "couldn't parse" from "wrong handler" — see
    ReplayResult.parse_failed.

    KNOWN-FRAGILE: substring scan can false-positive when an LLM mentions a
    handler in a "considered but rejected" sentence. Real-LLM mode (v0.2)
    should use a structured-output prompt (e.g., function-calling with a
    handler enum) rather than free-text scanning. Tracked in the v0.2
    follow-up section of tasks/todo.md P2.5.
    """
    text = response.lower()
    for handler in sorted(KNOWN_HANDLERS, key=len, reverse=True):  # longest match first
        if handler.lower() in text:
            return handler
    return NO_HANDLER_FOUND


def replay_one(
    fixture: Fixture,
    skill_md: str,
    llm_caller: LLMCaller,
) -> ReplayResult:
    """Replay a single fixture through the LLM caller and grade the result."""
    raw_response, actual_handler = llm_caller(skill_md, fixture.input_payload)
    parse_failed = (actual_handler == NO_HANDLER_FOUND)
    return ReplayResult(
        fixture_id=fixture.id,
        expected_handler=fixture.expected_handler,
        actual_handler=actual_handler,
        match=(not parse_failed) and (actual_handler == fixture.expected_handler),
        raw_response=raw_response,
        parse_failed=parse_failed,
    )


def replay_all(
    fixtures: list[Fixture],
    skill_md: str,
    llm_caller: LLMCaller,
) -> list[ReplayResult]:
    return [replay_one(f, skill_md, llm_caller) for f in fixtures]


# ──────────────────────────────────────────────────────────────────────────
# Built-in LLM callers
# ──────────────────────────────────────────────────────────────────────────


def mock_llm_returns_expected(skill_md: str, input_payload: dict) -> tuple[str, str]:
    """Test-only mock: returns whatever the fixture's `expected_handler` is.

    Used by the v0.1 self-test to validate the harness scaffolding works.
    NOT for production validation.
    """
    expected = input_payload.get("_expected_handler_for_mock", "handle_sick_call")
    return (f"Routing to: {expected}", expected)


def mock_llm_priority_order(skill_md: str, input_payload: dict) -> tuple[str, str]:
    """Mock that applies the priority-ordered routing matrix deterministically.

    **WHAT THIS IS:** A fixture-author sanity check. Walks the matrix top-to-bottom
    and returns the first matching handler so the test suite can detect when a
    fixture's `expected_handler` disagrees with the priority ordering as the
    fixture-author understood it.

    **WHAT THIS IS NOT:** A routing oracle. This mock encodes a Python re-implementation
    of `dispatch_shift_agent/SKILL.md`'s priority matrix. When SKILL.md changes,
    this mock will silently drift unless the SKILL.md hash gate
    (test_skill_md_hash_unchanged) catches it. Real-model routing validation
    requires the openrouter_llm_caller (v0.2 work).

    **The hash gate is the trust boundary.** If SKILL.md changes:
      1. The hash test fails LOUD
      2. Fixture authors must re-validate fixtures + this mock against the new matrix
      3. Update SKILL_MD_KNOWN_SHA256 in this file to acknowledge the new state

    Without the hash gate, this mock + the fixtures would happily agree with
    each other while production routing diverged. The gate prevents that.

    **Required fields in input_payload (raises ValueError if missing):**
      - sender_block.valid + .v
      - identity.role
      - raw_text

    Defaulting these is the silent-misclassification footgun the previous version
    had. Now: missing required field = noisy failure at the test level, not a
    silent route to `unknown_sender_declined`.
    """
    state = input_payload.get("state_files", {})
    sender_block = input_payload.get("sender_block", {})
    identity = input_payload.get("identity", {})
    config = input_payload.get("config", {})
    media_type = input_payload.get("media_type")
    raw_text = input_payload.get("raw_text")

    # Required-field validation: fail noisily, never silently misclassify.
    if raw_text is None:
        raise ValueError("input_payload missing required field 'raw_text'")
    if "role" not in identity:
        raise ValueError(
            "input_payload missing required field 'identity.role' — "
            "dispatcher contract requires this for routing"
        )
    if not sender_block:
        raise ValueError("input_payload missing required field 'sender_block'")

    # Extract message body (everything after the sender block on line 1).
    lines = raw_text.split("\n", 1)
    body = lines[1] if len(lines) > 1 else ""
    body_lower = body.lower()
    role = identity["role"]

    # Sender-block validity gate.
    if not sender_block.get("valid") or sender_block.get("v") != 1:
        return ("invalid sender block — fail closed", "unknown_sender_declined")

    # Extract code if present.
    import re
    code_match = re.search(r"#[A-HJKMNPQR-Z2-9]{5}", body)
    code = code_match.group(0) if code_match else None

    # Priority 1: code matches catering-menu-pending — owner-only since
    # commit 02afc22 (privilege-escalation guards). Pre-02afc22 this row
    # was role-any; non-owner senders with a forwarded code now hit
    # priority-9 catering-keyword fall-through (or an explicit deny).
    if (
        code
        and role == "owner"
        and any(
            r.get("approval_code") == code
            for r in state.get("catering-menu-pending.json", [])
        )
    ):
        return ("→ apply_catering_menu_decision", "apply_catering_menu_decision")

    # Priority 2: code matches non-terminal catering-leads + owner.
    if code and role == "owner" and any(
        r.get("approval_code") == code and r.get("status") not in {"COMPLETED", "REJECTED", "EXPIRED"}
        for r in state.get("catering-leads.json", [])
    ):
        return ("→ handle_catering_owner_approval", "handle_catering_owner_approval")

    # Priority 3: code matches expense-leads non-terminal + owner + enabled.
    if (
        code
        and role == "owner"
        and config.get("expense_bookkeeper.enabled")
        and any(
            r.get("approval_code") == code
            and r.get("status") not in {"COMPLETED", "REJECTED", "EXPIRED"}
            for r in state.get("expense_leads.json", [])
        )
    ):
        return ("→ expense_bookkeeper_dispatcher", "expense_bookkeeper_dispatcher")

    # Priority 4: undo E\d+ + owner + enabled.
    if role == "owner" and config.get("expense_bookkeeper.enabled") and re.match(
        r"^undo E\d{4,}( force)?$", body.strip(), re.IGNORECASE
    ):
        return ("→ expense_bookkeeper_dispatcher", "expense_bookkeeper_dispatcher")

    # Priority 5: code matches pending.json + owner.
    if code and role == "owner" and any(
        r.get("approval_code") == code for r in state.get("pending.json", [])
    ):
        return ("→ handle_owner_command", "handle_owner_command")

    def _has_active_flyer_project_for_sender() -> bool:
        phone = identity.get("phone_normalized") or sender_block.get("phone")
        chat_id = sender_block.get("chat_id")
        active_statuses = {
            "intake_started", "collecting_required_info", "awaiting_assets",
            "generating_concepts", "awaiting_concept_selection",
            "revising_design", "awaiting_final_approval", "finalizing_assets",
        }
        for project in state.get("flyer/projects.json", []):
            if project.get("status") not in active_statuses:
                continue
            if phone and project.get("customer_phone") == phone:
                return True
            if chat_id and project.get("customer_jid") == chat_id:
                return True
        return False

    if config.get("flyer.enabled") and _has_active_flyer_project_for_sender():
        return ("active flyer project -> flyer_dispatcher", "flyer_dispatcher")

    # Priority 6: image/doc + (owner OR employee) + caption mentions "menu".
    # Employee menu-update enabled 2026-05-05 (multi-role authorization for
    # delegated menu updates; expense remains owner-only at priority 7).
    if media_type in {"image", "document"} and role in {"owner", "employee"} and "menu" in body_lower:
        return ("→ update_catering_menu", "update_catering_menu")

    # Priority 7: image/doc + owner + caption mentions "expense"/"receipt" + enabled.
    if (
        media_type in {"image", "document"}
        and role == "owner"
        and ("expense" in body_lower or "receipt" in body_lower)
        and config.get("expense_bookkeeper.enabled")
    ):
        return ("→ expense_bookkeeper_dispatcher", "expense_bookkeeper_dispatcher")

    # Priority 8: image/doc + owner with no menu/expense keyword (assume menu).
    # Note: body may contain the Hermes "[The user sent an image but I couldn't
    # quite see it...]" marker even when there's no real caption — that's still
    # priority 8 (image_only shape), not "text only". Priorities 6 and 7
    # already returned if "menu" or "expense"/"receipt" was in the body, so by
    # the time we reach here, any media-bearing message from owner falls
    # through to assumed-menu intent.
    if media_type in {"image", "document"} and role == "owner":
        return ("→ update_catering_menu (assumed)", "update_catering_menu")

    flyer_keywords = (
        "flyer", "flier", "poster", "banner", "invite", "invitation",
        "social post", "instagram post", "instagram story", "ig post",
        "ig story", "graphic", "design flyer", "design poster",
        "make a flyer", "create a flyer",
    )
    if config.get("flyer.enabled") and any(kw in body_lower for kw in flyer_keywords):
        return ("-> flyer_dispatcher", "flyer_dispatcher")

    # Priority 9: catering keyword + catering enabled.
    catering_keywords = (
        "cater", "catering", "headcount", "guests", "event", "wedding",
        "reception", "banquet", "birthday", "anniversary", "party",
        "drop off", "pickup for event", "do you do catering",
    )
    if config.get("catering.enabled") and any(kw in body_lower for kw in catering_keywords):
        return ("→ catering_dispatcher", "catering_dispatcher")

    # PR-CF1 / PR-CF2 addendum: non-owner senders with an active catering
    # lead route finalize intent and proposal request/selection follow-ups
    # to the catering sub-dispatcher. Bare proposal words are intentionally
    # not part of the global catering keyword row.
    def _has_active_lead_for_sender() -> bool:
        phone = identity.get("phone_normalized") or sender_block.get("phone")
        chat_id = sender_block.get("chat_id")
        lid_digits = None
        if isinstance(chat_id, str) and chat_id.endswith("@lid"):
            maybe_digits = chat_id[:-len("@lid")]
            if maybe_digits.isdigit():
                lid_digits = maybe_digits
        actionable = {
            "AWAITING_OWNER_APPROVAL", "CUSTOMER_FINALIZED",
            "OWNER_EDITED", "OWNER_APPROVED",
        }
        for lead in state.get("catering-leads.json", []):
            if lead.get("status") not in actionable:
                continue
            if phone and lead.get("customer_phone") == phone:
                return True
            if chat_id and lead.get("customer_lid") == chat_id:
                return True
            if lid_digits and lead.get("customer_phone") == f"+{lid_digits}":
                return True
        return False

    finalize_terms = (
        "finalize", "send to owner", "confirm the menu", "confirm this menu",
        "lock it in", "proceed with this menu", "submit for approval",
        "ready to book",
    )
    proposal_passive_wait = re.compile(
        r"\b(?:will\s+wait|waiting|wait\s+for|want\s+to\s+wait|"
        r"no\s+need\s+to\s+send|not\s+yet)\b|^\s*any\s+update\??\s*$",
        re.IGNORECASE,
    )
    proposal_request_verb = re.compile(
        r"\b(?:send|share|show|give|create|make|prepare|draft|suggest|propose|"
        r"build|generate|want|wants|wanted|need|needs|needed|like|likes|"
        r"request|requests)\b",
        re.IGNORECASE,
    )
    proposal_request_object = re.compile(
        r"\b(?:proposal menus?|menu proposals?|proposal|proposals|"
        r"menu options?|options?)\b",
        re.IGNORECASE,
    )
    proposal_action_verb = (
        r"(?:choose|chose|select|selected|pick|picked|take|taking|use|"
        r"go\s+with|proceed\s+with|confirm|finalize)"
    )
    proposal_selection_numbered = re.compile(
        rf"\b{proposal_action_verb}\b.{{0,40}}\b(?:(?:option|proposal|menu)\s*)?#?\s*[1-3]\b",
        re.IGNORECASE | re.DOTALL,
    )
    proposal_selection_bare_numbered = re.compile(
        r"^\s*(?:(?:option|proposal|menu)\s*)?#?\s*[1-3]\s*$",
        re.IGNORECASE,
    )
    proposal_selection_named = re.compile(
        rf"\b{proposal_action_verb}\b.{{0,40}}\b(?:premium|balanced|classic)\b",
        re.IGNORECASE | re.DOTALL,
    )

    normalized_body = " ".join(body.split())
    proposal_request = False
    if normalized_body and not proposal_passive_wait.search(normalized_body):
        for obj in proposal_request_object.finditer(normalized_body):
            window = normalized_body[max(0, obj.start() - 80):obj.start()]
            if proposal_request_verb.search(window):
                proposal_request = True
                break
    proposal_selection = bool(
        proposal_selection_numbered.search(normalized_body)
        or proposal_selection_bare_numbered.search(normalized_body)
        or proposal_selection_named.search(normalized_body)
    )
    customer_finalize_intent = any(term in body_lower for term in finalize_terms)
    if (
        role != "owner"
        and _has_active_lead_for_sender()
        and (customer_finalize_intent or proposal_request or proposal_selection)
    ):
        return ("active lead follow-up -> catering_dispatcher", "catering_dispatcher")

    # Priority 10: compliance regex + owner + enabled.
    if role == "owner" and config.get("compliance.enabled"):
        compliance_re = re.compile(
            r"(?i)\b(compliance|deadline|inspection|license\s+renewal|tax\s+filing|servsafe)\b"
        )
        if compliance_re.search(body):
            return ("→ compliance_owner_query", "compliance_owner_query")

    # Priority 11: store-locator regex + multi_location.
    # NOTE: SKILL.md uses `(?i)` inline flags twice; Python 3.12+ rejects that as
    # "global flags not at the start of the expression". We split into two patterns
    # with re.IGNORECASE — semantically equivalent, just differently-spelled.
    if config.get("multi_location.locations"):
        locator_proximity = re.compile(
            r"\b(nearest|closest|near\s*(?:me|you|by))\b.{0,40}\b(store|location|branch|shop)\b",
            re.IGNORECASE,
        )
        locator_explicit = re.compile(
            r"\b(where\s+are\s+you\s+located|store\s+locator|find\s+(?:a\s+|the\s+)?store)\b",
            re.IGNORECASE,
        )
        if locator_proximity.search(body) or locator_explicit.search(body):
            return ("→ customer_location_query", "customer_location_query")

    # Priority 12: text-only owner + no code + no catering keyword → handle_owner_command.
    if role == "owner" and not code:
        return ("→ handle_owner_command (text only)", "handle_owner_command")

    # Priority 13: text-only employee → sick_call (or candidate_response if pending proposal).
    if role == "employee":
        # Subcase: pending proposal for this employee.
        emp_id = identity.get("employee_id")
        if emp_id and any(
            r.get("employee_id") == emp_id for r in state.get("pending.json", [])
        ):
            return ("→ handle_candidate_response", "handle_candidate_response")
        return ("→ handle_sick_call", "handle_sick_call")

    # Priority 14: anything from unknown → decline.
    return ("→ unknown_sender_declined (no other match)", "unknown_sender_declined")


# ──────────────────────────────────────────────────────────────────────────
# Real-LLM caller (v0.2 — function-calling structured output)
# ──────────────────────────────────────────────────────────────────────────


# Tracked per-call cost storage. Real-LLM tests aggregate this across runs
# so a CI/operator can see total $ spent. List of (model, fixture_id, cost_usd).
_REAL_LLM_COST_LOG: list[tuple[str, str, float]] = []


def get_real_llm_cost_log() -> list[tuple[str, str, float]]:
    """Return all per-call cost records from real-LLM caller invocations."""
    return list(_REAL_LLM_COST_LOG)


def reset_real_llm_cost_log() -> None:
    """Clear the cost log (use between test sessions)."""
    _REAL_LLM_COST_LOG.clear()


def _build_routing_tool() -> dict:
    """OpenAI function-calling schema for the routing decision.

    The model picks exactly one handler from KNOWN_HANDLERS via the
    `handler` enum. This replaces the v0.1 free-text substring scan
    (Reviewer-R2 documented-fragility — see parse_handler_from_response
    docstring) with a structured-output contract the model can't drift
    from.
    """
    return {
        "type": "function",
        "function": {
            "name": "route_to_handler",
            "description": (
                "Pick exactly one downstream handler skill based on the "
                "dispatch_shift_agent priority matrix. The matrix is in priority "
                "order — earlier rows fire first. Walk top-to-bottom and pick "
                "the first matching row's handler."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "handler": {
                        "type": "string",
                        "enum": sorted(KNOWN_HANDLERS),
                        "description": "The downstream handler skill to invoke.",
                    },
                    "matched_priority": {
                        "type": "integer",
                        "description": (
                            "1-based priority row number from the matrix that "
                            "matched. Use 14 for unknown-sender decline."
                        ),
                    },
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "One-sentence justification citing the matrix row "
                            "that matched."
                        ),
                    },
                },
                "required": ["handler", "matched_priority"],
            },
        },
    }


def openrouter_llm_caller(
    model_id: str,
    api_key: str,
    *,
    base_url: str = "https://openrouter.ai/api/v1",
    cheapest_provider: bool = True,
    timeout_s: float = 60.0,
) -> LLMCaller:
    """Build an LLMCaller that hits OpenRouter with the given model.

    Uses OpenAI function-calling for structured-output routing decisions.
    The model is forced to call `route_to_handler` with a `handler` enum,
    eliminating the free-text-scan fragility from v0.1.

    Costs are recorded in _REAL_LLM_COST_LOG via OpenRouter's
    `response.usage.cost` field (USD per call). Caller can access via
    `get_real_llm_cost_log()` or `reset_real_llm_cost_log()`.

    cheapest_provider=True passes `provider.sort: "price"` to honor the
    cost-optimization config from P2.5 B (also production default on
    srilu-vps). Set False for tests that want to compare specific
    providers.
    """
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError(
            "openrouter_llm_caller requires the `openai` Python package. "
            "Install with: pip install openai"
        ) from e

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s)
    tools = [_build_routing_tool()]
    extra_body: dict = {}
    if cheapest_provider:
        extra_body["provider"] = {"sort": "price"}

    def _caller(skill_md: str, input_payload: dict) -> tuple[str, str]:
        # Build the user message: serialize the routing inputs the LLM needs.
        # The dispatcher SKILL expects the v=1 sender block on line 1 + the
        # message body — but we ALSO surface the parsed sender_block, identity,
        # and state_files so the model doesn't have to re-parse + re-resolve
        # via subprocess. This is consistent with how the production gateway
        # invokes the SKILL: the deterministic helpers run first, then the
        # SKILL is asked to make the routing decision given those results.
        user_msg = (
            "Inbound message context (validate-sender-block + identify-sender "
            "outputs + state-file contents already provided):\n\n"
            f"{json.dumps(input_payload, indent=2, ensure_ascii=False)}\n\n"
            "Walk the routing matrix in priority order and call route_to_handler "
            "with the first matching row's handler. Use handler "
            f"'unknown_sender_declined' if no row matches. Available handlers: "
            f"{sorted(KNOWN_HANDLERS)}."
        )

        kwargs = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": skill_md},
                {"role": "user", "content": user_msg},
            ],
            "tools": tools,
            "tool_choice": {
                "type": "function",
                "function": {"name": "route_to_handler"},
            },
            "temperature": 0,  # deterministic routing decision
        }
        if extra_body:
            kwargs["extra_body"] = extra_body

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as e:
            return (f"ERROR: {type(e).__name__}: {e}", NO_HANDLER_FOUND)

        # Cost tracking — OpenRouter returns usage.cost in USD
        try:
            cost = float(getattr(response.usage, "cost", 0.0) or 0.0)
        except (AttributeError, TypeError, ValueError):
            cost = 0.0
        fixture_id = input_payload.get("_fixture_id_for_cost_tracking", "unknown")
        _REAL_LLM_COST_LOG.append((model_id, fixture_id, cost))

        # Parse the tool call
        try:
            tool_calls = response.choices[0].message.tool_calls or []
            if not tool_calls:
                # Model didn't call the tool — fall back to free-text scan
                content = response.choices[0].message.content or ""
                handler = parse_handler_from_response(content)
                return (f"NO_TOOL_CALL: {content!r}", handler)

            args_str = tool_calls[0].function.arguments
            args = json.loads(args_str)
            handler = args.get("handler", NO_HANDLER_FOUND)

            # Validate handler is in our enum (model could theoretically
            # hallucinate via JSON-mode-bypass; defensive check).
            if handler not in KNOWN_HANDLERS:
                return (
                    f"INVALID_HANDLER: model returned {handler!r} (not in enum). "
                    f"args={args!r}",
                    NO_HANDLER_FOUND,
                )

            reasoning = args.get("reasoning", "")
            priority = args.get("matched_priority", "?")
            raw = f"priority={priority} → {handler} ({reasoning!r})"
            return (raw, handler)
        except (json.JSONDecodeError, AttributeError, IndexError) as e:
            return (f"PARSE_ERROR: {type(e).__name__}: {e}", NO_HANDLER_FOUND)

    return _caller
