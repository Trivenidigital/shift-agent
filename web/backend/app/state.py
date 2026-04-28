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

from .config import get_settings  # noqa: E402

settings = get_settings()


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
        roster = safe_io.load_model(settings.roster_path, Roster)
        committer = RosterCommitter(roster)
        try:
            yield roster, committer
        except BaseException:
            # Don't write on any exception
            raise
        if committer.committed:
            # Re-validate after mutation — referential integrity etc.
            Roster.model_validate(roster.model_dump())
            safe_io.dump_model(settings.roster_path, roster)


class RosterCommitter:
    __slots__ = ("_roster", "committed")

    def __init__(self, roster: Roster) -> None:
        self._roster = roster
        self.committed = False

    def __call__(self) -> None:
        self.committed = True


def load_roster() -> Roster:
    return safe_io.load_model(settings.roster_path, Roster)


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
    return safe_io.load_model(settings.pending_path, PendingStore)


def load_send_counter() -> SendCounter | None:
    if not settings.send_counter_path.exists():
        return None
    try:
        return safe_io.load_model(settings.send_counter_path, SendCounter)
    except Exception:
        return None


def is_disabled() -> bool:
    return settings.disabled_flag.exists()
