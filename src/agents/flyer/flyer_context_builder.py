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
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Optional, Sequence

from pydantic import ValidationError

from schemas import FlyerLockedFact

try:  # reuse the deployed OpenRouter seam (flat layout on the VPS)
    from flyer_semantic_brief import OPENROUTER_URL, _openrouter_key  # type: ignore
except ImportError:  # pragma: no cover - import-path shim
    from agents.flyer.semantic_brief import OPENROUTER_URL, _openrouter_key

try:  # sibling FlyerBrief / validator — flat on the VPS, package-style in repo
    from flyer_brief import FlyerBrief  # type: ignore
    import flyer_brief_validator as _validator  # type: ignore
except ImportError:  # pragma: no cover - import-path shim
    from agents.flyer.flyer_brief import FlyerBrief
    from agents.flyer import flyer_brief_validator as _validator


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

# Bounded retry for a TRANSIENT gateway blip (root cause 2026-06-05: a single
# transient outage fail-closed the whole request; the identical call succeeded
# ~4s later). 3 total attempts; back off between attempts (one entry per gap, so
# len == attempts - 1). Retries ONLY transient failures (network/timeout/5xx);
# a 4xx or a 200-but-unparseable response is deterministic and is NOT retried
# (retrying wastes a call + money on a problem the retry cannot fix).
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
    Carries NO creative instructions — those live in the SKILL.md system prompt."""
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
        },
        ensure_ascii=False,
    )


class _TransientGatewayError(Exception):
    """Internal marker: a TRANSIENT gateway failure worth retrying (network
    error, timeout, or HTTP 5xx). Deterministic failures (HTTP 4xx, or a
    successful-200-but-unparseable response) do NOT raise this — they return
    ``None`` from ``_attempt_gateway`` and are never retried."""


def _attempt_gateway(req: urllib.request.Request) -> Optional[Mapping[str, Any]]:
    """ONE gateway attempt. Returns the parsed JSON object, or ``None`` for a
    DETERMINISTIC failure that retrying cannot fix (HTTP 4xx, or a 200 whose body
    is unparseable / the wrong shape). Raises ``_TransientGatewayError`` for a
    TRANSIENT failure (URLError/timeout/HTTP 5xx) so the caller retries it."""
    try:
        with urllib.request.urlopen(req, timeout=CREATIVE_DIRECTOR_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        # HTTPError is a subclass of URLError — branch on the status code BEFORE
        # the URLError clause: 5xx is a transient server-side blip (retry), 4xx is
        # a deterministic client error (do NOT retry — the same request will 4xx).
        if e.code >= 500:
            raise _TransientGatewayError(f"HTTP {e.code}") from e
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # Network unreachable / DNS / connection reset / socket timeout — transient.
        raise _TransientGatewayError(str(e)) from e
    # 200 OK: a parse/shape failure here is DETERMINISTIC (a garbled or off-schema
    # body won't fix itself on a retry) → return None, never retry.
    try:
        doc = json.loads(body)
        content = doc["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _call_gateway(system_prompt: str, user_message: str) -> Optional[Mapping[str, Any]]:
    """Run the structured-LLM call through the deployed OpenRouter seam, with a
    bounded retry on TRANSIENT failures only.

    ``system_prompt`` is the SKILL.md body (the creative brain); ``user_message``
    is the request data Python assembled. Returns the parsed JSON object (the
    model's FlyerBrief candidate) or ``None`` on failure. External contract is
    unchanged from the pre-retry version — still ``Optional[Mapping]``, still
    ``None`` after exhausting retries (→ ``build_flyer_brief`` status stays
    "unavailable"). Tests monkeypatch THIS function (the four-state contract) or
    ``urllib.request.urlopen`` (the retry loop) — no real network in tests.

    Retry policy: up to ``len(CREATIVE_DIRECTOR_RETRY_BACKOFFS_SEC) + 1`` total
    attempts, retrying ONLY transient failures (network error, timeout, HTTP 5xx)
    with a short backoff between attempts. A deterministic failure — HTTP 4xx, or a
    successful-200-but-unparseable/garbled response — returns ``None`` immediately
    (no retry: retrying cannot fix it and would waste a call + money).
    """
    key = _openrouter_key()
    if not key or "PLACEHOLDER" in key:
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
        except _TransientGatewayError:
            if backoff is None:  # transient failure on the final attempt → give up
                return None
            time.sleep(backoff)
    return None  # pragma: no cover - loop always returns on the final iteration


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

    # The SKILL.md body is the governing system instruction (the brain). If it is
    # unreadable the firewall is armed but the brain is unreachable → unavailable
    # (fail safe), NEVER a fall-back to Python-authored creativity.
    system_prompt = _skill_body()
    if not system_prompt:
        return BriefResult(status="unavailable")

    user_message = _build_user_message(
        raw_request, locked_facts, business_profile, source_summary, project_context
    )
    # _call_gateway returns None for ALL "brain unreachable" cases (missing/placeholder
    # key, the call threw, or the response was empty/unparseable JSON) → unavailable.
    raw = _call_gateway(system_prompt, user_message)
    if not raw:
        return BriefResult(status="unavailable")

    try:
        brief = FlyerBrief.model_validate(dict(raw))
    except (ValidationError, TypeError, ValueError):
        # A response that does not shape into a FlyerBrief is an unreachable/garbled
        # brain, not a firewall rejection → unavailable (fail safe), not invalid.
        return BriefResult(status="unavailable")

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
