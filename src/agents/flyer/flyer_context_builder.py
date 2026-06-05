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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

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

# The Creative-Director SKILL.md body is the governing system instruction (#5).
SKILL_MD_PATH = Path(__file__).resolve().parent / "skills" / "flyer_generation" / "SKILL.md"


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


def _call_gateway(system_prompt: str, user_message: str) -> Optional[Mapping[str, Any]]:
    """Run the structured-LLM call through the deployed OpenRouter seam.

    ``system_prompt`` is the SKILL.md body (the creative brain); ``user_message``
    is the request data Python assembled. Returns the parsed JSON object (the
    model's FlyerBrief candidate) or ``None`` on any failure. Tests monkeypatch
    THIS function — no real network in tests.
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
    try:
        with urllib.request.urlopen(req, timeout=CREATIVE_DIRECTOR_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
        doc = json.loads(body)
        content = doc["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return parsed if isinstance(parsed, Mapping) else None
    except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError,
            urllib.error.URLError, urllib.error.HTTPError):
        return None


def build_flyer_brief(
    raw_request: str,
    locked_facts: Sequence[FlyerLockedFact],
    business_profile: Mapping[str, Any] | object | None,
    source_summary: Optional[str] = None,
    project_context: Optional[str] = None,
) -> Optional[FlyerBrief]:
    """Build ONE validated ``FlyerBrief`` for the request, or ``None``.

    Returns ``None`` (caller falls back to the current Python prompt path) when:
      - the flag is unset (DORMANCY GUARANTEE — no network, no behavior change);
      - the SKILL.md body is unreadable (no brain → fail safe);
      - the gateway call fails / returns nothing;
      - the response does not parse into a ``FlyerBrief``;
      - the deterministic validator rejects the brief (caller fails safe).

    On success the returned brief has been validated and its customer-text spans
    materialized into the caller's locked-fact set by ``materialize_spans`` (the
    materialized facts are appended to the list passed in, so the overlay later
    renders ``required_fact_ids ∩ locked_facts``).
    """
    if not _is_enabled():
        return None

    # The SKILL.md body is the governing system instruction (the brain). If it is
    # unreadable, fail safe rather than fall back to Python-authored creativity.
    system_prompt = _skill_body()
    if not system_prompt:
        return None

    user_message = _build_user_message(
        raw_request, locked_facts, business_profile, source_summary, project_context
    )
    raw = _call_gateway(system_prompt, user_message)
    if not raw:
        return None

    try:
        brief = FlyerBrief.model_validate(dict(raw))
    except (ValidationError, TypeError, ValueError):
        return None

    result = _validator.validate(brief, locked_facts, raw_request)
    if not result.ok:
        return None

    # Materialize validated customer-text spans into real locked facts so the
    # overlay can render required_fact_ids ∩ locked_facts. Append in place so the
    # caller's fact list (and the project) carry them forward.
    materialized = _validator.materialize_spans(brief, raw_request)
    if materialized and isinstance(locked_facts, list):
        locked_facts.extend(materialized)

    return brief
