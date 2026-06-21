"""Creative-Director callable tool — builds ONE validated `FlyerBrief`.

Slice-1 of the Flyer Marketing Agent (design 2026-06-05, §3 + §5). This is the
plugin-backed seam the production path will call (in a LATER PR): it runs the
``flyer_generation`` Creative-Director skill via the Hermes gateway / OpenRouter
seam with structured JSON output, parses the result into a ``FlyerBrief``, and
hands it to the deterministic firewall (``flyer_brief_validator``) which validates
+ materializes customer-text spans. It returns the validated brief, or ``None``
on any failure so the caller fails safe.

The SKILL.md body IS the brain (Codex MAJOR #5): at call time this module READS
``skills/flyer_generation/SKILL.md`` and sends its body as the governing SYSTEM
instruction. Python only assembles the USER message — the raw request, the list
of available locked fact IDs, and a short profile summary. No creative
instructions are hardcoded in Python; ``flyer_generation/SKILL.md`` is the single
source of creative judgment (design §3: the skill is the implementation).

DORMANCY GUARANTEE: if ``FLYER_CREATIVE_DIRECTOR_ENABLED != "1"`` this returns
``None`` immediately — the caller falls back to the current Python prompt path,
and nothing here touches the network. Flag-OFF = byte-identical current behavior.

The OpenRouter seam is REUSED, not reinvented: ``OPENROUTER_URL`` +
``_openrouter_key`` come from the deployed ``flyer_semantic_brief`` module (flat
on the VPS), exactly as ``creative_planner.py`` imports them (creative_planner.py:28-31).
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Optional, Sequence

from pydantic import ValidationError

from schemas import FlyerLockedFact

try:  # reuse the deployed OpenRouter seam (flat layout on the VPS)
    from flyer_semantic_brief import OPENROUTER_URL, _openrouter_key  # type: ignore
except ImportError:  # pragma: no cover - import-path shim
    from agents.flyer.semantic_brief import OPENROUTER_URL, _openrouter_key

try:  # sibling FlyerBrief / validator — flat on the VPS, package-style in repo
    from flyer_brief import FactRef, FlyerBrief, MarketingHook, VisualDirection  # type: ignore
    import flyer_brief_validator as _validator  # type: ignore
    from flyer_brief_validator import (  # type: ignore
        _norm_ws,
        scrub_ungrounded_commercial_taste,
    )
except ImportError:  # pragma: no cover - import-path shim
    from agents.flyer.flyer_brief import FactRef, FlyerBrief, MarketingHook, VisualDirection
    from agents.flyer import flyer_brief_validator as _validator
    from agents.flyer.flyer_brief_validator import (
        _norm_ws,
        scrub_ungrounded_commercial_taste,
    )


CREATIVE_DIRECTOR_ENABLED_ENV = "FLYER_CREATIVE_DIRECTOR_ENABLED"

# Same model-resolution shape as the sibling LLM seams (creative_planner /
# semantic_brief): explicit override → Hermes default → cheap default.
CREATIVE_DIRECTOR_MODEL = (
    os.environ.get("FLYER_CREATIVE_DIRECTOR_MODEL")
    or os.environ.get("HERMES_DEFAULT_MODEL")
    or "openai/gpt-4o-mini"
)
# Creative direction wants a little temperature (the grounded extractor runs 0.0).
CREATIVE_DIRECTOR_TEMPERATURE = 0.4
CREATIVE_DIRECTOR_TIMEOUT_SEC = 30

# Bounded retry for a FAST transient gateway blip (root cause 2026-06-05: a single
# transient outage fail-closed the whole request; the identical call succeeded
# ~4s later). 3 total attempts; back off between attempts (one entry per gap, so
# len == attempts - 1). Retries ONLY *fast* transients — HTTP 5xx + connection-level
# errors (DNS / connection reset) that fail in milliseconds, so the retry budget
# stays bounded. A TIMEOUT is terminal (NOT retried): a stuck call already burned
# the full timeout, so retrying it stacks the tail (3 × 30s ≈ 91s) — exactly the
# long-tail latency we must avoid. A 4xx or a 200-but-unparseable response is
# deterministic and is also NOT retried (retrying cannot fix it; wastes a call + money).
CREATIVE_DIRECTOR_RETRY_BACKOFFS_SEC = (0.4, 1.0)

# The Creative-Director SKILL.md body is the governing system instruction (#5).
SKILL_MD_PATH = Path(__file__).resolve().parent / "skills" / "flyer_generation" / "SKILL.md"


# A four-state outcome (Codex P1 — typed status). The old contract returned a bare
# Optional[FlyerBrief], conflating "flag disabled" with "validation rejected": a PR3
# caller doing ``if brief is None: <old Python path>`` would BYPASS this firewall
# exactly when it REJECTS an unsafe brief. The status disambiguates the four cases
# so the caller can apply the fallback rule below — and ONLY that rule.
#
#   THE FALLBACK RULE: only ``status == "disabled"`` may fall back to the legacy
#   Python creative path. Every other status came from an ARMED firewall and must
#   be honored:
#     - "disabled"    → flag off: byte-identical legacy path (the ONLY fall-back).
#     - "unavailable" → the firewall is armed but the brain could not be reached
#                       (missing/placeholder gateway key, the call threw, or the
#                       response was empty/unparseable). The caller must fail-safe /
#                       retry — NEVER silently use the old creative path (that would
#                       route around the armed firewall on a transient outage).
#     - "invalid"     → the deterministic validator REJECTED the brief (``errors``
#                       populated, ``brief`` None). The caller MUST block / clarify /
#                       manual-route — NEVER the old path (this is the firewall doing
#                       its job; falling back here defeats it).
#     - "ok"          → validated ``brief`` + materialized spans; render it.
BriefStatus = Literal["disabled", "unavailable", "invalid", "ok"]


@dataclass
class BriefResult:
    """Typed outcome of ``build_flyer_brief`` (Codex P1).

    ``brief`` is set only when ``status == "ok"``; ``errors`` is populated only when
    ``status == "invalid"``. See ``BriefStatus`` for the per-status fallback rule —
    in short, only ``status == "disabled"`` permits the legacy Python path.
    """

    status: BriefStatus
    brief: Optional[FlyerBrief] = None
    errors: list[str] = field(default_factory=list)
    # Observability (2026-06-06): a SHORT classified reason for ``status=="unavailable"``
    # so the caller's audit row can say WHY the brain was unreachable (missing_key |
    # timeout | http_4xx | transient_exhausted:* | parse_failure | skill_body_unreadable
    # | brief_unparseable). Empty for "ok"/"invalid"/"disabled" (for "invalid" the
    # detail lives in ``errors``). LOG-ONLY — never changes the four-state contract.
    reason: str = ""


def _is_enabled() -> bool:
    return os.environ.get(CREATIVE_DIRECTOR_ENABLED_ENV) == "1"


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML ``---`` frontmatter block; keep the markdown body
    (the creative instructions). The frontmatter is skill-registry metadata, not
    instruction, so it shouldn't govern the model."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            return text[nl + 1:].lstrip("\n") if nl != -1 else ""
    return text


def _skill_body() -> str:
    """Read the flyer_generation SKILL.md body — the creative brain. Returns "" if
    the file is unreadable (then ``build_flyer_brief`` fails safe → None)."""
    try:
        return _strip_frontmatter(SKILL_MD_PATH.read_text(encoding="utf-8")).strip()
    except OSError:
        return ""


def _profile_summary(business_profile: Mapping[str, Any] | object | None) -> dict[str, Any]:
    """A SHORT, fact-free profile view for the user message — identity + language
    only. No creative direction here (that's the SKILL body's job)."""
    if business_profile is None:
        return {}
    if isinstance(business_profile, Mapping):
        return {
            k: business_profile[k]
            for k in ("business_name", "languages", "preferred_language")
            if k in business_profile
        }
    return {
        k: getattr(business_profile, k)
        for k in ("business_name", "languages", "preferred_language")
        if hasattr(business_profile, k)
    }


def _build_user_message(
    raw_request: str,
    locked_facts: Sequence[FlyerLockedFact],
    business_profile: Mapping[str, Any] | object | None,
    source_summary: Optional[str],
    project_context: Optional[str],
) -> str:
    """The USER message: just the DATA the SKILL body operates on — the raw
    request, the available locked-fact IDs (so the model references facts by ID,
    never by value), a short profile summary, and any source/project context.
    Carries NO creative instructions — those live in the SKILL.md system prompt.

    Plus a SHORT additive note that the brief ALWAYS includes the REQUIRED
    ``campaign_narrative`` (the message-first poster renders it as the dominant
    headline, so it must never be empty) and MAY include the CD v2 OPTIONAL
    TOP-LEVEL fields (``hero_ref`` / ``supporting_refs`` / ``marketing_hook`` /
    ``offer_priority``, and ``visual_direction.mood``).
    These are enhancement-only: the emphasis refs (``hero_ref`` / ``supporting_refs``
    / ``marketing_hook.text_ref``) point at a fact by a LOCKED ``fact_id`` only —
    NOT a ``raw_span`` (the resolver silently drops a raw_span on these) — never an
    inline value, and OMITTING them is always fine — they default and never block.
    The SKILL.md output schema is the
    authority for their exact shape (and the parser reads them at TOP LEVEL, so the
    note keeps them top-level, not nested)."""
    fact_catalog = [
        {"fact_id": f.fact_id, "label": f.label, "source": f.source}
        for f in locked_facts or []
    ]
    return json.dumps(
        {
            "customer_request": raw_request,
            "available_fact_ids": fact_catalog,
            "business_profile": _profile_summary(business_profile),
            "source_summary": source_summary or "",
            "project_context": project_context or "",
            # CD v2 — OPTIONAL TOP-LEVEL enhancement fields; see the SKILL schema for shape.
            "optional_creative_fields_note": (
                "Always include the top-level campaign_narrative (a short grounded "
                "marketing message; restate the campaign occasion if you cannot craft a "
                "distinct one) — it is required and the message-first poster renders it as "
                "the dominant headline. The brief MAY also include the OPTIONAL top-level "
                "fields hero_ref, supporting_refs, marketing_hook, offer_priority, and "
                "visual_direction.mood (per the SKILL output schema). The emphasis refs "
                "hero_ref, supporting_refs, and marketing_hook.text_ref each point at a "
                "fact by a locked fact_id only (never an inline value); omit any of these "
                "you are unsure of — they default and never block."
            ),
        },
        ensure_ascii=False,
    )


# Single-slot mailbox for the most recent gateway-failure CLASS, written by
# ``_attempt_gateway`` / ``_call_gateway`` and read by ``build_flyer_brief`` right after
# a ``None`` return (observability 2026-06-06). Safe WITHOUT locking: each inbound flyer
# runs in its OWN cf-router Popen subprocess (one per message) — no in-process concurrency.
# ``build_flyer_brief`` RESETS it before the call, so a monkeypatched ``_call_gateway``
# (which won't write it) yields "" → the caller falls back to "gateway_unreachable", never
# a stale value. LOG-ONLY: it never influences the return value or the four-state contract.
_GATEWAY_FAILURE: dict[str, str] = {"reason": ""}


def _set_gateway_reason(reason: str) -> None:
    _GATEWAY_FAILURE["reason"] = reason


class _TransientGatewayError(Exception):
    """Internal marker: a FAST transient gateway failure worth retrying — HTTP 5xx
    or a connection-level error (DNS / connection reset) that fails in milliseconds.
    Failures that must NOT be retried do NOT raise this and return ``None`` from
    ``_attempt_gateway``: a TIMEOUT (terminal — retrying stacks the 30s tail), an
    HTTP 4xx (deterministic client error), and a 200-but-unparseable response."""


def _attempt_gateway(req: urllib.request.Request) -> Optional[Mapping[str, Any]]:
    """ONE gateway attempt. Returns the parsed JSON object, or ``None`` for a
    failure that must NOT be retried — a TIMEOUT (terminal: a stuck call already
    burned the full timeout, so retrying it stacks the long tail), an HTTP 4xx
    (deterministic client error), or a 200 whose body is unparseable / the wrong
    shape. Raises ``_TransientGatewayError`` only for a FAST transient failure
    (HTTP 5xx or a connection-level error) so the caller retries it within a
    bounded budget."""
    try:
        with urllib.request.urlopen(req, timeout=CREATIVE_DIRECTOR_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        # HTTPError is a subclass of URLError — branch on the status code BEFORE the
        # URLError clause: 5xx is a fast, transient server-side blip (retry); 4xx is
        # a deterministic client error (do NOT retry — the same request will 4xx).
        if e.code >= 500:
            raise _TransientGatewayError(f"HTTP {e.code}") from e
        _set_gateway_reason(f"http_{e.code}")
        return None
    except (socket.timeout, TimeoutError):
        # A TIMEOUT is terminal: the call already burned the full 30s timeout, so
        # retrying it would stack the tail (3 × 30s ≈ 91s). socket.timeout/TimeoutError
        # are OSError subclasses, so this MUST precede the OSError clause below.
        _set_gateway_reason("timeout")
        return None
    except urllib.error.URLError as e:
        # A timeout can also surface wrapped as URLError(reason=timeout) — also
        # terminal. Any OTHER URLError (DNS / connection reset) fails fast → retry.
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            _set_gateway_reason("timeout")
            return None
        raise _TransientGatewayError(str(e)) from e
    except OSError as e:
        # Other connection-level socket errors (e.g. ECONNRESET) fail fast → retry.
        raise _TransientGatewayError(str(e)) from e
    # 200 OK: a parse/shape failure here is DETERMINISTIC (a garbled or off-schema
    # body won't fix itself on a retry) → return None, never retry.
    try:
        doc = json.loads(body)
        content = doc["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        _set_gateway_reason("parse_failure")
        return None
    if not isinstance(parsed, Mapping):
        _set_gateway_reason("parse_failure")
        return None
    return parsed


def _call_gateway(system_prompt: str, user_message: str) -> Optional[Mapping[str, Any]]:
    """Run the structured-LLM call through the deployed OpenRouter seam, with a
    bounded retry on FAST transient failures only.

    ``system_prompt`` is the SKILL.md body (the creative brain); ``user_message``
    is the request data Python assembled. Returns the parsed JSON object (the
    model's FlyerBrief candidate) or ``None`` on failure. External contract is
    unchanged from the pre-retry version — still ``Optional[Mapping]``, still
    ``None`` after exhausting retries (→ ``build_flyer_brief`` status stays
    "unavailable"). Tests monkeypatch THIS function (the four-state contract) or
    ``urllib.request.urlopen`` (the retry loop) — no real network in tests.

    Retry policy: up to ``len(CREATIVE_DIRECTOR_RETRY_BACKOFFS_SEC) + 1`` total
    attempts, retrying ONLY *fast* transients — HTTP 5xx + connection-level errors
    (DNS / connection reset) that fail in milliseconds — with a short backoff
    between attempts, so the retry budget stays bounded. Failures that return
    ``None`` immediately (NO retry): a TIMEOUT (terminal — the call already burned
    the full timeout, so retrying stacks the 30s tail; ~one timeout is the bounded
    worst case), an HTTP 4xx (deterministic client error), and a successful-200-
    but-unparseable/garbled response (retrying cannot fix it; wastes a call + money).
    """
    key = _openrouter_key()
    if not key or "PLACEHOLDER" in key:
        _set_gateway_reason("missing_key")
        return None
    payload = {
        "model": CREATIVE_DIRECTOR_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "response_format": {"type": "json_object"},
        "temperature": CREATIVE_DIRECTOR_TEMPERATURE,
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    # backoffs has one entry per inter-attempt gap, so total attempts = len + 1.
    for backoff in (*CREATIVE_DIRECTOR_RETRY_BACKOFFS_SEC, None):
        try:
            return _attempt_gateway(req)
        except _TransientGatewayError as exc:
            if backoff is None:  # transient failure on the final attempt → give up
                _set_gateway_reason(f"transient_exhausted:{str(exc)[:40]}")
                return None
            time.sleep(backoff)
    return None  # pragma: no cover - loop always returns on the final iteration


# CD v2 (Slice A) — the new OPTIONAL creative fields the brain may PROPOSE. They are
# ENHANCEMENTS, never requirements: a model that omits OR malforms any of them must
# never raise, never flip the brief to "invalid", and never disturb the rest of the
# brief. They are PRE-SANITIZED in the raw dict BEFORE a single ``FlyerBrief.
# model_validate`` runs (Codex MAJOR fix): each CD v2 field is reduced to a form that
# is GUARANTEED to satisfy the FlyerBrief schema constraints (or removed so it
# defaults), so the one strict ``model_validate`` ENFORCES every constraint uniformly
# — ``supporting_refs`` max_length=40, ``mood`` max_length=120 — instead of the old
# pop-then-reassign path, which set the validated fields by attribute and thereby
# bypassed those length constraints, letting over-length malformed values through.
_CDV2_TOP_LEVEL_FIELDS = ("hero_ref", "supporting_refs", "marketing_hook", "offer_priority")

# Schema caps mirrored from flyer_brief.py so the pre-sanitize CAPS the offending
# field to a value the single ``model_validate`` accepts (a bare ``model_validate``
# would RAISE on an over-length list/str, not truncate). Kept in sync with
# FlyerBrief.supporting_refs (max_length=40) / VisualDirection.mood (max_length=120)
# / FlyerBrief.campaign_narrative (max_length=200).
_SUPPORTING_REFS_MAX = 40
_MOOD_MAX_LEN = 120
_CAMPAIGN_NARRATIVE_MAX_LEN = 200


def _sanitize_cdv2_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return a COPY of the raw model dict with the CD v2 enhancement fields reduced
    to forms that are GUARANTEED to satisfy the ``FlyerBrief`` schema — so a SINGLE
    ``FlyerBrief.model_validate`` on the result enforces every constraint and never
    raises on these fields (a malformed value is REMOVED so it defaults, an
    over-length value is CAPPED). Pure: ``raw`` is not mutated.

      - ``hero_ref``: kept only if it constructs as a valid ``FactRef``; else removed
        (→ default None).
      - ``supporting_refs``: keep only entries that construct as a valid ``FactRef``,
        then CAP to ``_SUPPORTING_REFS_MAX`` (so the list-length constraint holds).
      - ``marketing_hook``: kept only if it constructs as a valid ``MarketingHook``;
        else removed (→ default None).
      - ``offer_priority``: kept only if ∈ {high, medium, low}; else removed
        (→ default "medium").
      - ``visual_direction.mood``: dropped if not a ``str``; otherwise TRUNCATED to
        ``_MOOD_MAX_LEN`` (so the str-length constraint holds).
      - ``campaign_narrative``: dropped if not a ``str`` (→ default ""); otherwise
        TRUNCATED to ``_CAMPAIGN_NARRATIVE_MAX_LEN`` (so the str-length constraint
        holds). Mirrors the ``mood`` handling exactly."""
    out: dict[str, Any] = dict(raw)

    # hero_ref — keep only a constructible FactRef; otherwise remove → default None.
    if "hero_ref" in out:
        hero = out["hero_ref"]
        if hero is None or not _constructs(FactRef, hero):
            out.pop("hero_ref", None)

    # supporting_refs — keep only constructible entries, then CAP to the schema max.
    if "supporting_refs" in out:
        supporting = out["supporting_refs"]
        if isinstance(supporting, list):
            valid = [e for e in supporting if _constructs(FactRef, e)]
            out["supporting_refs"] = valid[:_SUPPORTING_REFS_MAX]
        else:
            out.pop("supporting_refs", None)  # wrong type → default []

    # marketing_hook — keep only a constructible MarketingHook; else remove → None.
    if "marketing_hook" in out:
        hook = out["marketing_hook"]
        if hook is None or not _constructs(MarketingHook, hook):
            out.pop("marketing_hook", None)

    # offer_priority — keep only an in-enum value; else remove → default "medium".
    if "offer_priority" in out:
        if out["offer_priority"] not in ("high", "medium", "low"):
            out.pop("offer_priority", None)

    # visual_direction.mood — drop a non-str; truncate an over-length str to the cap.
    vd = out.get("visual_direction")
    if isinstance(vd, Mapping) and "mood" in vd:
        vd = dict(vd)
        mood = vd.get("mood")
        if not isinstance(mood, str):
            vd.pop("mood", None)  # wrong type → default ""
        elif len(mood) > _MOOD_MAX_LEN:
            vd["mood"] = mood[:_MOOD_MAX_LEN]
        out["visual_direction"] = vd

    # campaign_narrative — drop a non-str (→ default ""); truncate an over-length str
    # to the cap. Mirrors the mood handling exactly (a top-level free-text field).
    if "campaign_narrative" in out:
        narrative = out["campaign_narrative"]
        if not isinstance(narrative, str):
            out.pop("campaign_narrative", None)  # wrong type → default ""
        elif len(narrative) > _CAMPAIGN_NARRATIVE_MAX_LEN:
            out["campaign_narrative"] = narrative[:_CAMPAIGN_NARRATIVE_MAX_LEN]

    return out


def _constructs(model: type, value: Any) -> bool:
    """True iff ``value`` constructs into ``model`` via ``model_validate`` without
    raising. Used to pre-screen CD v2 sub-objects so the single ``FlyerBrief.
    model_validate`` never raises on a malformed enhancement field."""
    try:
        model.model_validate(value)
        return True
    except (ValidationError, TypeError, ValueError):
        return False


# A gateway callable: (system_prompt, user_message) -> parsed-JSON Mapping | None.
# Defaults to the module ``_call_gateway`` (the deployed OpenRouter seam + retry).
# Injectable so callers/tests can supply an offline fake without monkeypatching the
# module attribute. ``build_flyer_brief`` keeps its historic monkeypatch contract
# by NOT injecting (it calls the module ``_call_gateway`` so existing tests that
# ``monkeypatch.setattr(fcb, "_call_gateway", ...)`` still intercept it).
GatewayCallable = Callable[[str, str], Optional[Mapping[str, Any]]]


def _propose_and_parse_brief(
    raw_request: str,
    locked_facts: Sequence[FlyerLockedFact],
    business_profile: Mapping[str, Any] | object | None,
    source_summary: Optional[str],
    project_context: Optional[str],
    *,
    gateway: Optional[GatewayCallable],
) -> Optional[FlyerBrief]:
    """Run the SHARED propose+parse path: assemble the SKILL.md system prompt + the
    USER data message, call the gateway, PRE-SANITIZE the CD v2 enhancement fields,
    then run the SINGLE strict ``FlyerBrief.model_validate``. Returns the parsed
    ``FlyerBrief`` on success, or ``None`` on ANY failure (skill body unreadable,
    gateway unreachable/None, or an unparseable/off-schema response). Never raises.

    This is the ONE place the propose+parse internals live — both ``build_flyer_brief``
    (which layers the strict anti-fabrication ``validate`` + ``materialize_spans`` on
    top) and the V2 ``propose_creative_brief_v2`` (which does NOT) call it, so the
    prompt assembly + ``_call_gateway`` + ``_sanitize_cdv2_fields`` + ``model_validate``
    are not duplicated. The CD v2 / campaign_narrative instructions in the USER
    message (B0.2) and the firewalled sanitize are therefore identical on both paths.

    ``gateway`` defaults to the module ``_call_gateway`` when None, so ``build_flyer_brief``
    keeps its historic monkeypatch contract (tests patch the module attribute); the
    V2 path injects a fake gateway directly for tests (no network).
    """
    call_gateway = gateway or _call_gateway

    # The SKILL.md body is the governing system instruction (the brain). Unreadable
    # ⇒ the brain is unreachable ⇒ None (fail safe), never Python-authored creativity.
    system_prompt = _skill_body()
    if not system_prompt:
        _set_gateway_reason("skill_body_unreadable")
        return None

    user_message = _build_user_message(
        raw_request, locked_facts, business_profile, source_summary, project_context
    )
    # Reset the reason mailbox first so we read THIS call's classification (or "" →
    # the caller's default when a fake/monkeypatched gateway writes nothing).
    _set_gateway_reason("")
    raw = call_gateway(system_prompt, user_message)
    if not raw:
        return None

    # CD v2: the new OPTIONAL creative fields must NEVER fail the parse OR bypass the
    # FlyerBrief schema constraints. So PRE-SANITIZE them in the raw dict (malformed →
    # removed so it defaults; over-length → capped) and then run a SINGLE strict
    # ``FlyerBrief.model_validate`` over the WHOLE sanitized dict — that one validate
    # enforces every constraint uniformly (``supporting_refs`` max_length=40, ``mood``
    # max_length=120) instead of a pop-then-attribute-assign path that would bypass
    # those length constraints (Codex MAJOR).
    sanitized = _sanitize_cdv2_fields(raw)
    try:
        return FlyerBrief.model_validate(sanitized)
    except (ValidationError, TypeError, ValueError):
        # A response that does not shape into a FlyerBrief is an unreachable/garbled
        # brain → None (fail safe), exactly as a gateway failure.
        _set_gateway_reason("brief_unparseable")
        return None


def build_flyer_brief(
    raw_request: str,
    locked_facts: Sequence[FlyerLockedFact],
    business_profile: Mapping[str, Any] | object | None,
    source_summary: Optional[str] = None,
    project_context: Optional[str] = None,
) -> BriefResult:
    """Build ONE validated ``FlyerBrief`` for the request, as a typed ``BriefResult``.

    The status disambiguates the four outcomes so a caller never confuses "flag off"
    with "firewall rejected" (Codex P1). ONLY ``status == "disabled"`` permits the
    legacy Python creative path — see ``BriefStatus`` for the full rule:

      - ``"disabled"``    — flag unset (DORMANCY GUARANTEE: no network, no behavior
                            change). The ONLY status on which the caller may fall back
                            to the current Python prompt path.
      - ``"unavailable"`` — the firewall is armed but the brain could not be reached:
                            the SKILL.md body is unreadable, the gateway key is
                            missing/placeholder, the call threw, or the response was
                            empty / unparseable into a ``FlyerBrief``. The caller must
                            fail-safe / retry — NEVER silently use the old path.
      - ``"invalid"``     — the deterministic validator rejected the brief; ``errors``
                            is populated and ``brief`` is None. The caller MUST
                            block / clarify / manual-route — NEVER the old path.
      - ``"ok"``          — ``brief`` is the validated brief; its customer-text spans
                            have been materialized into the caller's locked-fact set
                            by ``materialize_spans`` (appended in place, so the overlay
                            later renders ``required_fact_ids ∩ locked_facts``).
    """
    if not _is_enabled():
        return BriefResult(status="disabled")

    # Reuse the SHARED propose+parse path (skill body → user message → gateway →
    # sanitize → model_validate). It returns None for ALL "brain unreachable" cases
    # (skill body unreadable, missing/placeholder key, the call threw, an empty /
    # unparseable JSON, or an off-schema body) → unavailable. ``_GATEWAY_FAILURE``
    # carries the classified reason (set by ``_attempt_gateway`` / the helper); when a
    # monkeypatched ``_call_gateway`` writes nothing it stays "" → "gateway_unreachable".
    brief = _propose_and_parse_brief(
        raw_request,
        locked_facts,
        business_profile,
        source_summary,
        project_context,
        gateway=None,  # use the module _call_gateway so existing monkeypatch tests intercept
    )
    if brief is None:
        return BriefResult(
            status="unavailable", reason=_GATEWAY_FAILURE["reason"] or "gateway_unreachable"
        )

    result = _validator.validate(brief, locked_facts, raw_request)
    if not result.ok:
        # The firewall REJECTED the brief. Surface the errors so the caller can
        # block / clarify / manual-route. This must NEVER fall back to the old path.
        return BriefResult(status="invalid", errors=list(result.errors))

    # Materialize validated customer-text spans into real locked facts so the
    # overlay can render required_fact_ids ∩ locked_facts. Append in place so the
    # caller's fact list (and the project) carry them forward.
    materialized = _validator.materialize_spans(brief, raw_request)
    if materialized and isinstance(locked_facts, list):
        locked_facts.extend(materialized)

    return BriefResult(status="ok", brief=brief)


def propose_creative_brief_v2(
    raw_request: str,
    locked_facts: Sequence[FlyerLockedFact],
    business_profile: Mapping[str, Any] | object | None = None,
    *,
    gateway: Optional[GatewayCallable] = None,
) -> Optional[FlyerBrief]:
    """CD v2 PROPOSE: the Hermes brain proposes the creative fields, parsed
    DEFENSIVELY into a ``FlyerBrief`` — WITHOUT the strict anti-fabrication
    ``validate`` and WITHOUT ``materialize_spans`` (so it NEVER mutates the passed
    ``locked_facts``).

    This is the V2 render path's brief source (B2.3). It REUSES the SHARED
    propose+parse internals (``_propose_and_parse_brief``): the SAME prompt assembly
    incl. the CD v2 / campaign_narrative instructions (B0.2), the SAME ``_call_gateway``
    seam, and the SAME ``_sanitize_cdv2_fields`` + single ``FlyerBrief.model_validate``
    parse — no prompt text or parse logic is duplicated. It deliberately departs from
    ``build_flyer_brief`` in two ways:

      - It does NOT run the strict ``flyer_brief_validator.validate`` (required-fact
        enforcement / fail-closed rejection). The deterministic resolver + the CD v2
        scrubs are the V2 firewall for every field V2 renders, so a brief that the
        strict validator would reject is still safely RESOLVED downstream (it only
        selects grounded locked-fact values, never invents).
      - It does NOT call ``materialize_spans`` — so it NEVER appends to / mutates the
        caller's ``locked_facts`` (a HARD V2 boundary). The list is read-only here.

    Returns the parsed ``FlyerBrief`` on success, or ``None`` on ANY gateway/parse
    failure (skill body unreadable, gateway unreachable, unparseable/off-schema
    response). NEVER raises. ``gateway`` is injectable so tests supply an offline fake
    (no network); when None it uses the module ``_call_gateway``.

    NOTE: unlike ``build_flyer_brief`` this is NOT gated by
    ``FLYER_CREATIVE_DIRECTOR_ENABLED`` — the V2 caller gates on the separate
    ``FLYER_CREATIVE_DIRECTOR_V2`` flag + allowlist (``_creative_director_v2_enabled``)
    BEFORE calling, so flag-off this function is never reached and the network is
    never touched.
    """
    return _propose_and_parse_brief(
        raw_request,
        locked_facts,
        business_profile,
        None,  # source_summary — not used on the V2 render path yet
        None,  # project_context — not used on the V2 render path yet
        gateway=gateway,
    )


def advise_scene_direction(
    raw_request: str,
    locked_facts: Sequence[FlyerLockedFact],
    business_profile: Mapping[str, Any] | object | None,
    source_summary: Optional[str] = None,
    project_context: Optional[str] = None,
) -> Optional[VisualDirection]:
    """ADVISORY art-direction for the INTEGRATED renderer — NOT the CD+overlay/firewall path.

    Reuses the ``flyer_generation`` SKILL via the gateway to infer the occasion/season/culture
    *visual direction* (theme/palette/motifs/subjects), but deliberately departs from
    ``build_flyer_brief``'s contract:

      - It is **NOT** gated by ``FLYER_CREATIVE_DIRECTOR_ENABLED`` (the caller gates on the separate
        ``FLYER_SKILL_DRIVEN_SCENE`` flag + allowlist).
      - It **never fail-closes**: on ANY problem (skill body unreadable, gateway disabled/missing
        key/error/timeout, unparseable response, missing/empty ``visual_direction``) it returns
        ``None`` so the caller silently falls back to today's Python integrated scene.
      - It reads **only** ``visual_direction`` (ignores ``background_brief``, ``offer_groups``,
        ``fact_refs`` — facts stay Python-injected by reference; this carries NO commercial values).

    The skill is an advisory art director here, never a new reason a render fails.
    """
    try:
        system_prompt = _skill_body()
        if not system_prompt:
            return None
        user_message = _build_user_message(
            raw_request, locked_facts, business_profile, source_summary, project_context
        )
        raw = _call_gateway(system_prompt, user_message)
        if not raw:
            return None
        vd_raw = dict(raw).get("visual_direction")
        if not isinstance(vd_raw, Mapping):
            return None
        vd = VisualDirection.model_validate(dict(vd_raw))
        # Scrub ungrounded COMMERCIAL values from the model-authored theme_family /
        # mood AT THE SOURCE (the brain) — these taste strings reach the image prompt,
        # so a model could otherwise smuggle a fabricated commercial claim (e.g.
        # theme_family="$5 off") into the scene. This closes the SAME class the CD v2
        # resolver's _resolve_theme_mood closes, via the SHARED scanner (no parallel
        # regex). Ground against the locked-fact values exactly as the resolver does,
        # so a legitimately grounded number is not over-stripped; a scene theme carries
        # no grounded numbers in practice, so any commercial value is stripped. Guarded
        # so a scrub error never breaks the advisory path (it falls back to the raw vd).
        try:
            allowed_values = [
                _norm_ws(getattr(f, "value", "") or "")
                for f in locked_facts or ()
                if (getattr(f, "value", "") or "").strip()
            ]
            scrubbed_theme, scrubbed_mood = scrub_ungrounded_commercial_taste(
                vd.theme_family, vd.mood, allowed_values
            )
            if scrubbed_theme != vd.theme_family or scrubbed_mood != vd.mood:
                vd = vd.model_copy(
                    update={"theme_family": scrubbed_theme, "mood": scrubbed_mood}
                )
        except Exception:  # noqa: BLE001 — advisory only; a scrub error keeps the raw vd
            pass
        # Require a SUBSTANTIVE direction — a theme AND at least one NON-EMPTY concrete subject/motif.
        # Check CLEANED values (render strips whitespace-only entries), so a partial like
        # ``{"theme_family": "x", "visual_subjects": [" "]}`` falls back to the richer Python scene
        # instead of rendering a weak theme-only block (Codex). palette alone is not enough taste.
        has_subject = any(str(s).strip() for s in (vd.visual_subjects or []))
        has_motif = any(str(m).strip() for m in (vd.motifs or []))
        if not vd.theme_family.strip() or not (has_subject or has_motif):
            return None
        return vd
    except Exception:  # noqa: BLE001 — advisory only; ANY failure -> None -> Python scene fallback
        return None
