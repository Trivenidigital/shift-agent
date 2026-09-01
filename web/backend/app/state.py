"""State-file coordinators using existing safe_io + schemas.

The cockpit shares /opt/shift-agent/{roster,config,state}.json with the agent.
All mutations go through these helpers to preserve flock invariants.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# PLATFORM-EXTRACTION TODO (Phase C, deferred until agent #2 ships):
# _AGENT_ROOT and the schemas import below are platform-shareable boundaries.
# When the second agent's cockpit needs land (e.g. Daily Brief sections),
# parameterize via env (AGENT_ROOT / AGENTS_ENABLED) and split shift-specific
# routers (pending, roster, schedule) from platform routers (audit, auth,
# config, health, disclosures, safety, whatsapp). The schemas import will
# then split into platform schemas + per-agent schemas. See
# web/frontend/src/components/layout/Layout.tsx:5-16 for the matching
# frontend NAV array that needs the same agent #2-driven refactor.
#
# Add /opt/shift-agent to sys.path so we can import schemas + safe_io
_AGENT_ROOT = Path("/opt/shift-agent")
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import safe_io  # noqa: E402
from schemas import Config, PendingStore, Roster, SendCounter  # noqa: E402
from privileged_identity import (  # noqa: E402
    check_privileged_identity_integrity,
)

from .config import get_settings  # noqa: E402

settings = get_settings()


def _unwrap_loaded(model):
    if isinstance(model, tuple):
        return model[0]
    return model


class PrivilegedIdentityViolation(Exception):
    """A roster mutation would stitch identifiers across owner principals.

    Carries the structured violations so the caller can name the row and the
    conflicting identifiers rather than reporting a generic refusal.
    """

    def __init__(self, violations):
        self.violations = [v.as_dict() for v in violations]
        super().__init__(
            "roster mutation would create %d privileged-identity violation(s): %s"
            % (len(violations), "; ".join(v.detail for v in violations))
        )


def _privileged_violations(roster: Roster) -> dict:
    """Violations keyed by (employee_id, reason), or {} if unevaluable.

    Returns {} rather than raising when the owner config cannot be read: this
    guard must never be the reason a roster edit becomes impossible. The write
    it protects is already gated by auth; a config read failure is an
    availability problem, not a licence to stitch identities, and the
    resolver-side refusals from #773 still stand underneath.
    """
    try:
        owner = load_config().owner
    except Exception:
        return {}
    try:
        found = check_privileged_identity_integrity(roster.model_dump(mode="json"), owner)
    except Exception:
        return {}
    return {(v.employee_id, v.reason): v for v in found}


@contextmanager
def roster_session() -> Iterator[tuple[Roster, "RosterCommitter"]]:
    """Load Roster under flock; caller mutates and explicitly commits.

    Usage:
        with roster_session() as (roster, commit):
            roster.employees.append(new_emp)
            commit()  # writes if not called, exit-without-commit = no write

    On exception: original file is preserved (no write).
    """
    with safe_io.flock(settings.roster_path):
        roster = _unwrap_loaded(safe_io.load_model(settings.roster_path, Roster))
        before_violations = _privileged_violations(roster)
        committer = RosterCommitter(roster)
        try:
            yield roster, committer
        except BaseException:
            # Don't write on any exception
            raise
        if committer.committed:
            # Re-validate after mutation — referential integrity etc.
            Roster.model_validate(roster.model_dump())
            # Cross-store privileged-identity guard. Compares BEFORE against
            # AFTER and refuses only violations this mutation INTRODUCES, so a
            # roster that already contains one stays editable -- a guard that
            # rejects any present violation would lock the operator out of
            # fixing exactly the row that needs fixing.
            introduced = [
                v for key, v in _privileged_violations(roster).items()
                if key not in before_violations
            ]
            if introduced:
                raise PrivilegedIdentityViolation(introduced)
            safe_io.dump_model(settings.roster_path, roster)


class RosterCommitter:
    __slots__ = ("_roster", "committed")

    def __init__(self, roster: Roster) -> None:
        self._roster = roster
        self.committed = False

    def __call__(self) -> None:
        self.committed = True


def load_roster() -> Roster:
    return _unwrap_loaded(safe_io.load_model(settings.roster_path, Roster))


def load_config() -> Config:
    """Load config.yaml as Config model."""
    import yaml

    raw = settings.config_path.read_text()
    data = yaml.safe_load(raw) or {}
    return Config.model_validate(data)


def save_config(cfg: Config) -> None:
    """Write config.yaml under flock — preserves comments-stripped YAML output."""
    import yaml

    with safe_io.flock(settings.config_path):
        rendered = yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False, default_flow_style=False)
        safe_io.atomic_write_text(settings.config_path, rendered)


def load_pending() -> PendingStore:
    if not settings.pending_path.exists():
        return PendingStore(proposals={})
    return _unwrap_loaded(safe_io.load_model(settings.pending_path, PendingStore))


def load_send_counter() -> SendCounter | None:
    if not settings.send_counter_path.exists():
        return None
    try:
        return _unwrap_loaded(safe_io.load_model(settings.send_counter_path, SendCounter))
    except Exception:
        return None


def is_disabled() -> bool:
    return settings.disabled_flag.exists()
