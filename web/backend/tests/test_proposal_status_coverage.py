"""Mirror schemas.ProposalStatus with the frontend STATUS_BADGE map.

If schemas.py adds a status, this test fails — forcing the frontend's
proposalStatus.ts to update or the backend to translate.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Add /opt/shift-agent so we can import the live schemas
_AGENT_ROOT = Path("/opt/shift-agent")
if _AGENT_ROOT.exists():
    sys.path.insert(0, str(_AGENT_ROOT))


def _frontend_statuses() -> set[str]:
    """Parse PROPOSAL_STATUSES from the frontend TS file."""
    fe = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "proposalStatus.ts"
    text = fe.read_text()
    match = re.search(r"PROPOSAL_STATUSES\s*=\s*\[([^\]]+)\]\s*as const", text)
    assert match, "PROPOSAL_STATUSES not found in frontend"
    return {s.strip().strip('"').strip("'") for s in match.group(1).split(",") if s.strip()}


def test_frontend_proposal_statuses_match_backend():
    try:
        from schemas import ProposalStatus  # type: ignore  # noqa
    except ImportError:
        # /opt/shift-agent isn't on path in CI; skip
        import pytest
        pytest.skip("/opt/shift-agent not available")

    # Pydantic Literal type can be enumerated via __args__ for typing.Literal
    import typing

    backend = set(typing.get_args(ProposalStatus))
    frontend = _frontend_statuses()
    missing_in_fe = backend - frontend
    extra_in_fe = frontend - backend
    assert not missing_in_fe, f"Frontend missing statuses: {missing_in_fe}"
    assert not extra_in_fe, f"Frontend has extra/stale statuses: {extra_in_fe}"
