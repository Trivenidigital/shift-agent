"""Agent #41 Owner Wellbeing v0.1 — quiet-hours guard tests.

Covers: cross-midnight + same-day windows, boundary semantics
(now==start → suppressed, now==end → not suppressed), priority bypass
(>= critical_priority_threshold always sends), weekday filter,
disabled short-circuit, schema-level validation (empty quiet_days,
zero-width window).

Plan: tasks/agent-41-owner-wellbeing-plan.md
Design: tasks/agent-41-owner-wellbeing-design.md

Pattern: importlib.SourceFileLoader for hyphen-named script (mirrors
tests/_b1_helpers.run_apply) + threaded HTTP stub for Pushover +
frozen-time injection via mod.customer_now monkey-patch.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from zoneinfo import ZoneInfo

import pytest
import yaml
from pydantic import ValidationError

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="shift-agent-notify-owner depends on safe_io which uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
NOTIFY_SCRIPT = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-notify-owner"

EXIT_OK = 0


# ─────────────────────────────────────────────────────────────────
# Pushover HTTP stub
# ─────────────────────────────────────────────────────────────────


class _PushoverStub(BaseHTTPRequestHandler):
    """Captures POST bodies into class-level requests list."""
    requests: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        self.__class__.requests.append({"path": self.path, "body": body})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":1}')

    def log_message(self, *args, **kwargs):  # silence
        return


@pytest.fixture
def pushover_stub():
    _PushoverStub.requests = []
    server = HTTPServer(("127.0.0.1", 0), _PushoverStub)
    port = server.server_port
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield port, _PushoverStub
    finally:
        server.shutdown()


# ─────────────────────────────────────────────────────────────────
# env_dir + config builder
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def env_dir(tmp_path):
    """Per-test config + state + logs dirs. Mirrors _b1_helpers.make_env_dir."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    cfg = {
        "schema_version": 1,
        "customer": {
            "name": "Test", "location_id": "loc_t",
            "timezone": "America/New_York",
        },
        "owner": {
            "name": "Owner", "phone": "+19045550100",
            "self_chat_jid": "19045550100@s.whatsapp.net",
        },
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "owner_wellbeing": {
            "enabled": True,
            "quiet_start": "22:00",
            "quiet_end": "06:00",
            "quiet_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "critical_priority_threshold": 1,
        },
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path


def _override_owner_wellbeing(env_dir: Path, **kwargs) -> None:
    """Mutate the config.yaml's owner_wellbeing block in place."""
    cfg_path = env_dir / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["owner_wellbeing"].update(kwargs)
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _run_notify(
    env_dir: Path, pushover_port: int, *,
    title: str = "Test", priority: int = 0, message: str = "test message",
    frozen_now: datetime | None = None,
) -> subprocess.CompletedProcess:
    """Subprocess-invoke shift-agent-notify-owner via importlib SourceFileLoader.

    Overrides CONFIG_PATH + DECISIONS_LOG_PATH + PUSHOVER_URL post-exec_module.
    Optionally monkey-patches customer_now to return frozen_now.
    """
    frozen_iso = frozen_now.isoformat() if frozen_now else None
    use_frozen = frozen_now is not None
    wrapper = f"""
import sys, pathlib
import importlib.machinery, importlib.util

sys.argv = [
    "shift-agent-notify-owner",
    {message!r},
    "--title", {title!r},
    "--priority", {str(priority)!r},
]
sys.path.insert(0, {str(PLATFORM_DIR)!r})

# Pre-load schemas + safe_io + exit_codes from the test PLATFORM_DIR into
# sys.modules BEFORE exec_module on the script. The script does
# `sys.path.insert(0, "/opt/shift-agent")` at module load time, which would
# otherwise win the resolution race for `from schemas import ...` and pull
# the deployed (older) schemas.py — missing new classes added in this PR.
# Caching in sys.modules makes the script's `from X import Y` hit the
# already-loaded test version instead of resolving via sys.path.
for _modname in ("schemas", "safe_io", "exit_codes"):
    _path = pathlib.Path({str(PLATFORM_DIR)!r}) / f"{{_modname}}.py"
    _loader = importlib.machinery.SourceFileLoader(_modname, str(_path))
    _spec = importlib.util.spec_from_file_location(_modname, str(_path), loader=_loader)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_modname] = _mod
    _spec.loader.exec_module(_mod)

loader = importlib.machinery.SourceFileLoader("notify", {str(NOTIFY_SCRIPT)!r})
spec = importlib.util.spec_from_file_location("notify", {str(NOTIFY_SCRIPT)!r}, loader=loader)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Override module-level paths + URLs after exec_module
mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.DECISIONS_LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.STATE_DIR = pathlib.Path({str(env_dir / 'state')!r})
mod.NOTIFY_FAILED_LOG = pathlib.Path({str(env_dir / 'state' / 'notify-failed.log')!r})
mod.PUSHOVER_URL = "http://127.0.0.1:{pushover_port}/messages"
mod.WHATSAPP_BRIDGE_URL = "http://127.0.0.1:{pushover_port}/bridge"  # also stub bridge

if {use_frozen!r}:
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    _frozen = _dt.fromisoformat({frozen_iso!r})
    def _patched(tz_name):
        return _frozen.astimezone(_ZI(tz_name))
    mod.customer_now = _patched

sys.exit(mod.main())
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, timeout=15,
    )


def _read_audit(env_dir: Path) -> list[dict]:
    log = env_dir / "logs" / "decisions.log"
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


# ─────────────────────────────────────────────────────────────────
# 1. Cross-midnight (placed first per R1-NIT-2 — trickiest case visible on open)
# ─────────────────────────────────────────────────────────────────


def test_cross_midnight_window_suppresses(env_dir, pushover_stub):
    """Quiet 22:00-06:00, now=02:00 Tuesday local → suppressed.

    Cross-midnight branch: now_time >= start OR now_time < end.
    "02:00" >= "22:00" is False; "02:00" < "06:00" is True → suppressed.
    """
    port, stub = pushover_stub
    # 2026-05-12 (Tuesday) 02:00 EDT
    frozen = datetime(2026, 5, 12, 2, 0, 0, tzinfo=ZoneInfo("America/New_York"))

    r = _run_notify(env_dir, port, priority=0, frozen_now=frozen)

    assert r.returncode == EXIT_OK, f"stderr={r.stderr}"
    assert len(stub.requests) == 0, "Pushover unexpectedly called during quiet hours"

    audit = _read_audit(env_dir)
    suppressed = [e for e in audit if e["type"] == "owner_notification_suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0]["priority"] == 0
    assert suppressed[0]["quiet_start"] == "22:00"
    assert suppressed[0]["quiet_end"] == "06:00"
    assert suppressed[0]["suppressed_at_local"] == "02:00:00"


# ─────────────────────────────────────────────────────────────────
# 2-4. Priority threshold semantics
# ─────────────────────────────────────────────────────────────────


def test_priority_below_threshold_in_window_suppressed(env_dir, pushover_stub):
    """priority=0, threshold=1, in window → suppressed."""
    port, stub = pushover_stub
    frozen = datetime(2026, 5, 12, 2, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    r = _run_notify(env_dir, port, priority=0, frozen_now=frozen)
    assert r.returncode == EXIT_OK
    assert len(stub.requests) == 0


def test_priority_at_threshold_in_window_sends(env_dir, pushover_stub):
    """priority=1 == threshold=1 → NOT suppressed (>= threshold).
    Pins the comparison operator (R2-M3)."""
    port, stub = pushover_stub
    frozen = datetime(2026, 5, 12, 2, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    r = _run_notify(env_dir, port, priority=1, frozen_now=frozen)
    assert r.returncode == EXIT_OK
    assert len(stub.requests) == 1, "priority==threshold should send"
    audit = _read_audit(env_dir)
    assert not any(e["type"] == "owner_notification_suppressed" for e in audit)


def test_priority_above_threshold_in_window_sends(env_dir, pushover_stub):
    """priority=2 > threshold=1 → emergency, always sends."""
    port, stub = pushover_stub
    frozen = datetime(2026, 5, 12, 2, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    r = _run_notify(env_dir, port, priority=2, frozen_now=frozen)
    assert r.returncode == EXIT_OK
    assert len(stub.requests) == 1


# ─────────────────────────────────────────────────────────────────
# 5. Outside window (cross-midnight branch, time NOT in quiet)
# ─────────────────────────────────────────────────────────────────


def test_outside_window_sends(env_dir, pushover_stub):
    """Quiet 22:00-06:00, now=12:00 → outside window → Pushover called."""
    port, stub = pushover_stub
    frozen = datetime(2026, 5, 12, 12, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    r = _run_notify(env_dir, port, priority=0, frozen_now=frozen)
    assert r.returncode == EXIT_OK
    assert len(stub.requests) == 1


# ─────────────────────────────────────────────────────────────────
# 6. Same-day window (separate code path: start < end)
# ─────────────────────────────────────────────────────────────────


def test_same_day_window_suppresses(env_dir, pushover_stub):
    """Quiet 13:00-15:00 (same day, NOT cross-midnight), now=14:00 → suppressed.
    R2-M2: pins the start < end branch."""
    port, stub = pushover_stub
    _override_owner_wellbeing(env_dir, quiet_start="13:00", quiet_end="15:00")
    frozen = datetime(2026, 5, 12, 14, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    r = _run_notify(env_dir, port, priority=0, frozen_now=frozen)
    assert r.returncode == EXIT_OK
    assert len(stub.requests) == 0
    audit = _read_audit(env_dir)
    suppressed = [e for e in audit if e["type"] == "owner_notification_suppressed"]
    assert len(suppressed) == 1


# ─────────────────────────────────────────────────────────────────
# 7. Boundary semantics — now == quiet_start (R2-M2)
# ─────────────────────────────────────────────────────────────────


def test_boundary_at_quiet_start_suppressed(env_dir, pushover_stub):
    """Quiet 22:00-06:00, now=22:00 sharp → suppressed (>= start)."""
    port, stub = pushover_stub
    frozen = datetime(2026, 5, 12, 22, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    r = _run_notify(env_dir, port, priority=0, frozen_now=frozen)
    assert r.returncode == EXIT_OK
    assert len(stub.requests) == 0


# ─────────────────────────────────────────────────────────────────
# 8. Weekday filter
# ─────────────────────────────────────────────────────────────────


def test_weekday_filter_excludes_weekend(env_dir, pushover_stub):
    """quiet_days=[mon-fri], now=Saturday inside time-window → Pushover called
    (Saturday not in quiet_days)."""
    port, stub = pushover_stub
    _override_owner_wellbeing(env_dir, quiet_days=["mon", "tue", "wed", "thu", "fri"])
    # 2026-05-09 is a Saturday
    frozen = datetime(2026, 5, 9, 2, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    r = _run_notify(env_dir, port, priority=0, frozen_now=frozen)
    assert r.returncode == EXIT_OK
    assert len(stub.requests) == 1


# ─────────────────────────────────────────────────────────────────
# 9. Disabled short-circuits everything
# ─────────────────────────────────────────────────────────────────


def test_disabled_short_circuits(env_dir, pushover_stub):
    """enabled=False → no time-window evaluation, Pushover called normally
    even at 02:00 Tuesday with priority=0."""
    port, stub = pushover_stub
    _override_owner_wellbeing(env_dir, enabled=False)
    frozen = datetime(2026, 5, 12, 2, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    r = _run_notify(env_dir, port, priority=0, frozen_now=frozen)
    assert r.returncode == EXIT_OK
    assert len(stub.requests) == 1
    audit = _read_audit(env_dir)
    assert not any(e["type"] == "owner_notification_suppressed" for e in audit)


# ─────────────────────────────────────────────────────────────────
# 10-11. Schema-level validation (in-process, no subprocess)
# ─────────────────────────────────────────────────────────────────


def test_validation_rejects_empty_quiet_days():
    """R2-MEDIUM-2: Field(min_length=1) on quiet_days must reject empty list.
    Operator footgun: typo'd YAML with `quiet_days: []` would silently turn
    the guard into 'always allow' — must fail loud at config load."""
    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import OwnerWellbeingConfig  # noqa: E402
    with pytest.raises(ValidationError):
        OwnerWellbeingConfig(enabled=True, quiet_days=[])


def test_validation_rejects_zero_width_window():
    """R1-M2: zero-width window (start == end) silently never fires —
    @model_validator must reject when enabled=True."""
    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import OwnerWellbeingConfig  # noqa: E402
    with pytest.raises(ValidationError):
        OwnerWellbeingConfig(
            enabled=True, quiet_start="22:00", quiet_end="22:00",
        )


def test_validation_allows_zero_width_when_disabled():
    """When enabled=False the zero-width validator must NOT fire — operator
    can set both fields equal and disable the agent without an error."""
    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import OwnerWellbeingConfig  # noqa: E402
    cfg = OwnerWellbeingConfig(
        enabled=False, quiet_start="22:00", quiet_end="22:00",
    )
    assert cfg.enabled is False
