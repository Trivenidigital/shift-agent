"""WS1b — the extraction seam switch (flag-gated, fail-closed to legacy).

Drift-check tag: extends-Hermes.

Hermes-first analysis: wiring only — routes between the legacy regex extractor
and extraction_v2 (both existing); no substrate re-implemented.

FLYER_EXTRACTION_V2=1 routes brief extraction through extraction_v2 with the
source-parity guard; ANY v2 failure falls back to the legacy extractor and the
outcome is auditable per brief (FlyerExtractionV2Outcome). Flag off/unset =>
byte-identical legacy, zero audit rows.

Identity discipline carry-over: when the caller passes allow_text_identity=False
(a registered profile owns identity), v2's text-derived identity facts
(business_name / contact_phone / location) are SUPPRESSED — message content
never overrides registered identity (2026-05-15 lesson class).
"""
from __future__ import annotations

import os
import re

_IDENTITY_FACT_IDS = ("business_name", "contact_phone", "location")

# IN-4 (E2E audit 2026-07-13): bounded, deterministic festival -> occasion map for
# the LEGACY extraction path so festival flyers theme correctly before v2 graduates.
# Only the four FlyerOccasion enum values are producible, keyed off EXPLICIT festival
# names (never generic "special"/"celebration"). Deliberately narrow — occasion is
# LLM-classifiable and v2 owns the general case. NOTE: bare "independence day" is NOT
# mapped to july4 — for an Indian SMB that is Aug 15, not the US July 4.
_DETERMINISTIC_OCCASION_PATTERNS = (
    (re.compile(r"\b(?:diwali|deepavali)\b", re.IGNORECASE), "diwali"),
    (re.compile(r"\b(?:ramadan|ramzan|iftar|eid\s+al[\s-]?fitr|\beid\b)\b", re.IGNORECASE), "ramadan"),
    (re.compile(r"\bthanksgiving\b", re.IGNORECASE), "thanksgiving"),
    (re.compile(r"\b(?:july\s*4th?|4th\s+of\s+july|fourth\s+of\s+july)\b", re.IGNORECASE), "july4"),
)


def _derive_deterministic_occasion(raw_request: str) -> str:
    """Return a FlyerOccasion enum value for an explicit festival name, else 'none'."""
    text = raw_request or ""
    for pattern, occasion in _DETERMINISTIC_OCCASION_PATTERNS:
        if pattern.search(text):
            return occasion
    return "none"


def extraction_v2_enabled() -> bool:
    return os.environ.get("FLYER_EXTRACTION_V2") == "1"


def extract_text_facts_seam(fields, raw_request, *, message_id="", report_out=None,
                            profile_business_name="", allow_text_identity=True,
                            cfg=None, audit=None, seam="managed_create"):
    """Drop-in replacement for facts.extract_text_facts at both seams.
    ``audit`` is an optional callable(event:str, report_or_reason) the caller
    wires to its decisions.log chokepoint; never raises out of auditing."""
    try:  # flat (VPS) then package (tests)
        from flyer_facts import extract_text_facts  # type: ignore
    except ImportError:  # pragma: no cover
        from agents.flyer.facts import extract_text_facts

    if extraction_v2_enabled():
        try:
            try:  # flat then package
                from flyer_extraction_v2 import extract_text_facts_v2  # type: ignore
            except ImportError:  # pragma: no cover
                from agents.flyer.extraction_v2 import extract_text_facts_v2
            facts, report = extract_text_facts_v2(
                fields, raw_request, message_id=message_id,
                profile_business_name=profile_business_name)
            if not allow_text_identity:
                facts = [f for f in facts if f.fact_id not in _IDENTITY_FACT_IDS]
            if report_out is not None:
                try:
                    report_out["occasion"] = getattr(report, "occasion", "none")
                except Exception:  # noqa: BLE001
                    pass
            if audit is not None:
                try:
                    audit("extraction_v2_used", report)
                except Exception:  # noqa: BLE001 — observability never blocks
                    pass
            return facts
        except Exception as exc:  # noqa: BLE001 — FAIL-CLOSED: legacy fallback
            if audit is not None:
                try:
                    audit("extraction_v2_fallback", f"{type(exc).__name__}: {str(exc)[:100]}")
                except Exception:  # noqa: BLE001
                    pass
    # Legacy path: capture the Hermes semantic-brief provenance (used vs
    # fell-back) so the caller records whether the provider is contributing.
    brief_provenance: dict = {}
    facts = extract_text_facts(
        fields, raw_request, message_id=message_id,
        profile_business_name=profile_business_name,
        allow_text_identity=allow_text_identity, cfg=cfg,
        brief_provenance=brief_provenance)
    # Flag-scope (#569 regression fix): the row exists to answer "did the
    # legacy provider fire while v2 is ACTIVE" (fallback observability, §9c).
    # With the flag deliberately off, legacy-and-silent is the PINNED contract
    # (test_flag_off_is_byte_identical_legacy_and_silent) — emit nothing.
    if (audit is not None and brief_provenance
            and os.environ.get("FLYER_EXTRACTION_V2") == "1"):
        try:
            audit("semantic_brief_outcome", brief_provenance)
        except Exception:  # noqa: BLE001 — observability never blocks
            pass
    # IN-4: derive the occasion deterministically on the legacy path (v2 sets it via
    # the LLM above; when v2 is off/fell-back the sink would otherwise stay "none").
    # Writes only to the report sink — never facts, never audit — so the flag-off
    # "byte-identical + silent" contract holds.
    if report_out is not None and str(report_out.get("occasion") or "none") == "none":
        report_out["occasion"] = _derive_deterministic_occasion(raw_request)
    return facts
