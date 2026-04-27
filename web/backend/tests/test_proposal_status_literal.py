"""ProposalView.status is a Literal (BL-130) — narrowing requirement."""
from __future__ import annotations


def test_proposal_status_alias_has_11_variants():
    from app.models import ProposalStatus
    from typing import get_args

    variants = set(get_args(ProposalStatus))
    assert variants == {
        "awaiting_owner_approval",
        "approved",
        "reconciling",
        "sent",
        "send_failed",
        "accepted",
        "declined",
        "denied_by_owner",
        "expired",
        "cancelled",
        "no_response_timeout",
    }


def test_proposalview_uses_literal_status():
    """The OpenAPI schema for ProposalView must emit `enum:` for status,
    not a free-form string. Without this, openapi-typescript can't generate
    a discriminated union and the frontend loses narrowing."""
    from app.models import ProposalView

    schema = ProposalView.model_json_schema()
    status_def = schema["properties"]["status"]
    # Pydantic v2 emits 'enum' for Literal types
    assert "enum" in status_def or "$ref" in status_def
    if "enum" in status_def:
        assert len(status_def["enum"]) == 11
