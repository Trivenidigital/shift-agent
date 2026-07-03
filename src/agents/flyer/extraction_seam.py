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

_IDENTITY_FACT_IDS = ("business_name", "contact_phone", "location")


def extraction_v2_enabled() -> bool:
    return os.environ.get("FLYER_EXTRACTION_V2") == "1"


def extract_text_facts_seam(fields, raw_request, *, message_id="",
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
    return extract_text_facts(
        fields, raw_request, message_id=message_id,
        profile_business_name=profile_business_name,
        allow_text_identity=allow_text_identity, cfg=cfg)
