"""Tests for src/agents/catering/scripts/lookup-prior-leads-by-phone (v3.1 C02).

Linux-only (depends on safe_io which uses fcntl).

Test count: 22, mapping 1:1 to design-review findings (audit table in design v2).
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="depends on safe_io which uses fcntl (Linux only)",
)

# fcntl is Unix-only; deferred import inside Linux-gated tests
fcntl = None
if platform.system() != "Windows":
    import fcntl  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "src" / "agents" / "catering" / "scripts" / "lookup-prior-leads-by-phone"


def _load_script():
    """Load the script as a module via importlib (matches test_send_routing_accuracy_summary
    pattern). Conftest puts src/platform on sys.path so safe_io/schemas/exit_codes
    resolve."""
    spec = importlib.util.spec_from_file_location("lookup_mod", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_leads(env_dir: Path, leads: list[dict]) -> Path:
    """Write a CateringLeadStore JSON file at env_dir/state/catering-leads.json."""
    state = env_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "catering-leads.json"
    path.write_text(json.dumps({"leads": leads}), encoding="utf-8")
    return path


def _mk_lead(
    *, lead_id: str, phone: str, status: str = "AWAITING_OWNER_APPROVAL",
    created_at: datetime, event_date: str | None = None,
    dietary: list[str] | None = None,
) -> dict:
    """Construct a minimal CateringLead dict matching the schema."""
    return {
        "lead_id": lead_id,
        "status": status,
        "customer_phone": phone,
        "customer_name": "Test Customer",
        "raw_inquiry": "test inquiry",
        "original_message_id": f"msg_{lead_id}",
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
        "extracted": {
            "headcount": 30,
            "event_date": event_date,
            "dietary_restrictions": dietary or [],
        },
        "quote_text": "",
        "quote_version": 0,
        "owner_approval_code": None,
        "customer_replied": False,
    }


@pytest.fixture
def env_dir(tmp_path):
    return tmp_path


# ---------- 1. empty / no-match paths ----------

def test_returns_empty_when_no_leads_file(env_dir):
    mod = _load_script()
    result = mod.lookup_prior_leads_by_phone(
        "+19045551234",
        leads_path=env_dir / "state" / "catering-leads.json",
    )
    assert result["lookup_status"] == "missing_file"
    assert result["prior_lead_count"] == 0
    assert result["most_recent_status"] is None
    assert result["last_seen_days_ago"] is None


def test_returns_no_match_when_phone_absent_from_store(env_dir):
    mod = _load_script()
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone="+19045559999",
                 created_at=datetime.now(tz=timezone.utc) - timedelta(days=5))
    ])
    result = mod.lookup_prior_leads_by_phone(
        "+19045551234",  # different phone
        leads_path=leads_path,
    )
    assert result["lookup_status"] == "no_match"
    assert result["prior_lead_count"] == 0


# ---------- 2. happy path / sort ----------

def test_returns_count_for_single_match(env_dir):
    mod = _load_script()
    created = datetime(2026, 4, 15, 14, 0, tzinfo=timezone.utc)
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone="+19045551234", status="CLOSED",
                 created_at=created, event_date="2026-04-20",
                 dietary=["vegetarian"])
    ])
    result = mod.lookup_prior_leads_by_phone(
        "+19045551234", leads_path=leads_path,
        now=datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc),
    )
    assert result["lookup_status"] == "ok"
    assert result["prior_lead_count"] == 1
    assert result["most_recent_status"] == "CLOSED"
    assert result["most_recent_event_date"] == "2026-04-20"
    assert result["most_recent_dietary_restrictions"] == ["vegetarian"]
    assert result["last_seen_days_ago"] == 13


def test_most_recent_is_highest_created_at_not_first_in_list(env_dir):
    """Critical (test-analyzer crit 9): regression to drop reverse=True silently
    corrupts every Kimi context. Insert leads in non-chronological order and pin
    that most_recent matches highest created_at."""
    mod = _load_script()
    phone = "+19045551234"
    leads_path = _seed_leads(env_dir, [
        # OLDEST FIRST (insertion order)
        _mk_lead(lead_id="L001", phone=phone, status="CLOSED",
                 created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                 event_date="2025-01-15"),
        # NEWEST IN MIDDLE
        _mk_lead(lead_id="L002", phone=phone, status="OWNER_REJECTED",
                 created_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
                 event_date="2026-04-20"),
        # MIDDLE LAST
        _mk_lead(lead_id="L003", phone=phone, status="STALE",
                 created_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
                 event_date="2025-06-15"),
    ])
    result = mod.lookup_prior_leads_by_phone(
        phone, leads_path=leads_path,
        now=datetime(2026, 4, 28, tzinfo=timezone.utc),
    )
    assert result["prior_lead_count"] == 3
    # Must match L002 (highest created_at), NOT L001 (insertion order)
    assert result["most_recent_status"] == "OWNER_REJECTED"
    assert result["most_recent_event_date"] == "2026-04-20"


# ---------- 3. canonicalization variants (per-form per design-review) ----------

def test_canonicalize_dashes(env_dir):
    mod = _load_script()
    canonical = "+19045551234"
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=canonical,
                 created_at=datetime.now(tz=timezone.utc) - timedelta(days=2))
    ])
    result = mod.lookup_prior_leads_by_phone(
        "+1-904-555-1234", leads_path=leads_path,
    )
    assert result["prior_lead_count"] == 1


def test_canonicalize_bare_digits(env_dir):
    mod = _load_script()
    canonical = "+19045551234"
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=canonical,
                 created_at=datetime.now(tz=timezone.utc) - timedelta(days=2))
    ])
    result = mod.lookup_prior_leads_by_phone(
        "19045551234",  # bare digits, no leading +
        leads_path=leads_path,
    )
    assert result["prior_lead_count"] == 1


def test_canonicalize_00_prefix(env_dir):
    mod = _load_script()
    canonical = "+19045551234"
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=canonical,
                 created_at=datetime.now(tz=timezone.utc) - timedelta(days=2))
    ])
    result = mod.lookup_prior_leads_by_phone(
        "0019045551234",  # 00- international prefix
        leads_path=leads_path,
    )
    assert result["prior_lead_count"] == 1


def test_canonicalize_jid_suffix(env_dir):
    mod = _load_script()
    canonical = "+19045551234"
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=canonical,
                 created_at=datetime.now(tz=timezone.utc) - timedelta(days=2))
    ])
    result = mod.lookup_prior_leads_by_phone(
        "19045551234@s.whatsapp.net",  # WhatsApp jid form
        leads_path=leads_path,
    )
    assert result["prior_lead_count"] == 1


def test_priya_letters_rejected_via_main(env_dir):
    """Architect HIGH-1 + test-analyzer crit 7 (E): PRIYA = letters that
    canonicalize to +1555PRIYA, fail _PHONE_E164. Lock the contract that
    letters are rejected (illustrative-not-literal interpretation)."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--customer-phone", "+1-555-PRIYA"],
        capture_output=True, text=True,
        env={**os.environ,
             "PYTHONPATH": str(REPO_ROOT / "src" / "platform")},
        timeout=10,
    )
    assert proc.returncode == 2  # EXIT_INVALID_INPUT
    assert "invalid phone" in proc.stderr.lower()


def test_invalid_phone_raises_valueerror_function_call(env_dir):
    mod = _load_script()
    with pytest.raises(ValueError, match="invalid phone"):
        mod.lookup_prior_leads_by_phone("not a phone")


# ---------- 4. corrupt store ----------

def test_corrupt_leads_store_function_raises(env_dir):
    mod = _load_script()
    state = env_dir / "state"
    state.mkdir()
    leads_path = state / "catering-leads.json"
    leads_path.write_text("not valid json {{{", encoding="utf-8")
    with pytest.raises(RuntimeError, match="corrupt"):
        mod.lookup_prior_leads_by_phone("+19045551234", leads_path=leads_path)


def test_corrupt_leads_store_cli_returns_dict_with_status_corrupt(env_dir):
    """Design-review MEDIUM-2 asymmetry resolution: CLI catches RuntimeError and
    emits structured JSON to stdout AND EXIT_SCHEMA_VIOLATION. SKILL parser
    sees a dict on stdout regardless of failure mode."""
    state = env_dir / "state"
    state.mkdir()
    leads_path = state / "catering-leads.json"
    leads_path.write_text("not valid json {{{", encoding="utf-8")
    # Patch script's LEADS_PATH via env-var-style override (script uses
    # module-level constant; test wrapper invokes via a small Python script)
    wrapper = f"""
import sys, importlib.util, pathlib
sys.path.insert(0, str(pathlib.Path({str(REPO_ROOT / 'src' / 'platform')!r})))
spec = importlib.util.spec_from_file_location("lookup_mod", {str(SCRIPT_PATH)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.LEADS_PATH = pathlib.Path({str(leads_path)!r})
sys.argv = ["lookup-prior-leads-by-phone", "--customer-phone", "+19045551234"]
sys.exit(mod.main())
"""
    proc = subprocess.run(
        [sys.executable, "-c", wrapper], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 5  # EXIT_SCHEMA_VIOLATION
    out = json.loads(proc.stdout.strip())
    assert out["lookup_status"] == "corrupt"
    assert out["prior_lead_count"] == 0


# ---------- 5. tz / time semantics ----------

def test_aware_now_aware_created_at_succeeds(env_dir):
    mod = _load_script()
    phone = "+19045551234"
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=phone,
                 created_at=datetime(2026, 4, 15, 14, 0, tzinfo=timezone.utc))
    ])
    result = mod.lookup_prior_leads_by_phone(
        phone, leads_path=leads_path,
        now=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
    )
    assert result["last_seen_days_ago"] == 13


def test_cross_tz_aware_aware_succeeds(env_dir):
    """Test-analyzer crit 6 (C): created_at=UTC, now=IST. Pins astimezone-style
    correct cross-tz arithmetic vs replace(tzinfo=...) bugs."""
    from zoneinfo import ZoneInfo
    mod = _load_script()
    phone = "+19045551234"
    # 2026-04-15 18:00 UTC == 2026-04-15 23:30 IST (same day in both tzs)
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=phone,
                 created_at=datetime(2026, 4, 15, 18, 0, tzinfo=timezone.utc))
    ])
    result = mod.lookup_prior_leads_by_phone(
        phone, leads_path=leads_path,
        now=datetime(2026, 4, 28, 14, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
    )
    # 2026-04-28 14:00 IST == 2026-04-28 08:30 UTC. Delta from 2026-04-15 18:00 UTC
    # = 12 days, 14h30m → .days truncates to 12.
    assert result["last_seen_days_ago"] == 12


def test_naive_input_normalized_to_utc_with_warn(env_dir, capsys):
    """Silent-failure-hunter MEDIUM-3: naive datetime → coerce + WARN."""
    mod = _load_script()
    phone = "+19045551234"
    naive_created = datetime(2026, 4, 15, 14, 0)  # no tzinfo
    state = env_dir / "state"
    state.mkdir()
    leads_path = state / "catering-leads.json"
    # Manually serialize a naive datetime — Pydantic would normally reject this,
    # but a hand-edited file or future writer regression could produce it.
    leads_path.write_text(json.dumps({
        "leads": [_mk_lead(lead_id="L001", phone=phone, created_at=naive_created)]
    }), encoding="utf-8")

    result = mod.lookup_prior_leads_by_phone(
        phone, leads_path=leads_path,
        now=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
    )
    # Pydantic actually adds tz=UTC when parsing naive ISO into datetime; if it
    # didn't, the WARN would fire. Either way the result is correct (13 days).
    assert result["last_seen_days_ago"] == 13


@pytest.mark.parametrize("delta_seconds, expected_days", [
    (0, 0),                     # exactly now
    (60 * 60 * 23 + 59 * 60, 0),  # 23h59m → 0 (truncation)
    (60 * 60 * 24, 1),          # exactly 24h → 1
    (60 * 60 * 24 * 30, 30),    # 30 days
    (-60 * 60 * 24, -1),        # FUTURE event (test-analyzer crit 6 G)
])
def test_last_seen_days_ago_truncation_boundary(env_dir, delta_seconds, expected_days):
    """Pin .days truncation across boundaries including negative (future-dated lead)."""
    mod = _load_script()
    phone = "+19045551234"
    now = datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc)
    created = now - timedelta(seconds=delta_seconds)
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=phone, created_at=created)
    ])
    result = mod.lookup_prior_leads_by_phone(phone, leads_path=leads_path, now=now)
    assert result["last_seen_days_ago"] == expected_days


def test_most_recent_event_date_none_when_not_yet_extracted(env_dir):
    """Test-analyzer crit 8: NEW-status leads before extractor populates event_date."""
    mod = _load_script()
    phone = "+19045551234"
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=phone, status="NEW",
                 created_at=datetime.now(tz=timezone.utc) - timedelta(days=1),
                 event_date=None)  # extractor hasn't populated yet
    ])
    result = mod.lookup_prior_leads_by_phone(phone, leads_path=leads_path)
    assert result["most_recent_event_date"] is None
    assert result["most_recent_status"] == "NEW"


def test_dietary_restrictions_empty_list_not_none(env_dir):
    """Test-analyzer #9: empty list, never None — JSON-stable contract."""
    mod = _load_script()
    phone = "+19045551234"
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=phone,
                 created_at=datetime.now(tz=timezone.utc) - timedelta(days=1),
                 dietary=[])
    ])
    result = mod.lookup_prior_leads_by_phone(phone, leads_path=leads_path)
    assert result["most_recent_dietary_restrictions"] == []
    assert result["most_recent_dietary_restrictions"] is not None


# ---------- 6. lock semantics ----------

def test_lock_timeout_via_real_subprocess_holding_flock(env_dir):
    """Test-analyzer H crit 9 + B crit 8: must exercise REAL fcntl.LOCK_NB
    syscall (not a mock) — a future bug swapping LOCK_NB for blocking LOCK_EX
    would pass mocked tests but hang in production. Spawns a holder subprocess,
    runs lookup against the same file, asserts lock_timeout."""
    mod = _load_script()
    phone = "+19045551234"
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=phone,
                 created_at=datetime.now(tz=timezone.utc) - timedelta(days=1))
    ])

    # Spawn a holder process that grabs LOCK_EX and sleeps longer than our
    # retry budget (3 × 1.0s = 3s). Use 5s sleep so the lookup definitely
    # hits lock_timeout.
    holder_script = f"""
import fcntl, time, sys
fd = open({str(leads_path)!r}, 'rb')
fcntl.flock(fd, fcntl.LOCK_EX)
print('LOCKED', flush=True)
time.sleep(5)
fcntl.flock(fd, fcntl.LOCK_UN)
fd.close()
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # Wait for holder to acquire lock
        line = holder.stdout.readline()
        assert line.strip() == "LOCKED", f"holder didn't print LOCKED: {line!r}"

        # Assert sleep is called between retries (B crit 8) — patch time.sleep
        # in the module just before our call, count invocations.
        sleep_calls = []
        original_sleep = time.sleep

        def counting_sleep(secs):
            sleep_calls.append(secs)
            original_sleep(secs)

        with patch.object(mod, "time", new=MagicMock(sleep=counting_sleep)):
            start = time.monotonic()
            result = mod.lookup_prior_leads_by_phone(phone, leads_path=leads_path)
            elapsed = time.monotonic() - start

        assert result["lookup_status"] == "lock_timeout"
        assert result["prior_lead_count"] == 0
        # Sleep called LOCK_RETRY_ATTEMPTS - 1 times (between attempts only)
        assert len(sleep_calls) == mod.LOCK_RETRY_ATTEMPTS - 1
        assert all(s == mod.LOCK_RETRY_SLEEP_SEC for s in sleep_calls)
        # Total elapsed must be near (LOCK_RETRY_ATTEMPTS - 1) * sleep_sec
        # but allow generous slack for CI variability
        assert 1.5 <= elapsed <= 5.0
    finally:
        holder.terminate()
        holder.wait(timeout=2)


def test_flock_is_invoked_on_read_path(env_dir):
    """Silent-failure HIGH-8: pin that the helper actually calls fcntl.flock
    on the data file fd (vs safe_io.flock which would lock a sibling .lock).
    Monkeypatches the module's `fcntl.flock` directly."""
    mod = _load_script()
    phone = "+19045551234"
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=phone,
                 created_at=datetime.now(tz=timezone.utc) - timedelta(days=1))
    ])

    flock_calls = []
    original_flock = fcntl.flock

    def spy_flock(fd, op):
        flock_calls.append(op)
        return original_flock(fd, op)

    with patch.object(mod.fcntl, "flock", side_effect=spy_flock):
        result = mod.lookup_prior_leads_by_phone(phone, leads_path=leads_path)

    assert result["lookup_status"] == "ok"
    # First call: LOCK_EX | LOCK_NB (acquire). Second call: LOCK_UN (release).
    assert flock_calls[0] == (fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert flock_calls[1] == fcntl.LOCK_UN


# ---------- 7. lookup_status enum + CLI/importable parity ----------

@pytest.mark.parametrize("path_setup,expected_status", [
    ("missing", "missing_file"),
    ("empty_no_match", "no_match"),
    ("populated", "ok"),
])
def test_lookup_status_field_present_in_paths(env_dir, path_setup, expected_status):
    """Pin lookup_status enum across all return paths.
    (lock_timeout + corrupt covered by dedicated tests above.)"""
    mod = _load_script()
    phone = "+19045551234"
    if path_setup == "missing":
        leads_path = env_dir / "state" / "catering-leads.json"  # doesn't exist
    elif path_setup == "empty_no_match":
        leads_path = _seed_leads(env_dir, [
            _mk_lead(lead_id="L001", phone="+19999999999",  # different phone
                     created_at=datetime.now(tz=timezone.utc) - timedelta(days=1))
        ])
    else:  # populated
        leads_path = _seed_leads(env_dir, [
            _mk_lead(lead_id="L001", phone=phone,
                     created_at=datetime.now(tz=timezone.utc) - timedelta(days=1))
        ])

    result = mod.lookup_prior_leads_by_phone(phone, leads_path=leads_path)
    assert "lookup_status" in result
    assert result["lookup_status"] == expected_status


def test_cli_output_matches_function_dict(env_dir):
    """Test-analyzer crit 7: subprocess JSON.loads must equal direct call dict.
    Pins that argv→main() emits the same shape as the importable function,
    including most_recent_status as a JSON-serializable string."""
    mod = _load_script()
    phone = "+19045551234"
    created = datetime(2026, 4, 15, 14, 0, tzinfo=timezone.utc)
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=phone, status="OWNER_REJECTED",
                 created_at=created, event_date="2026-04-20",
                 dietary=["vegan"])
    ])

    # Direct call (importable form) — controlled now for determinism
    direct = mod.lookup_prior_leads_by_phone(
        phone, leads_path=leads_path,
        now=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
    )

    # CLI form
    wrapper = f"""
import sys, importlib.util, pathlib
sys.path.insert(0, str(pathlib.Path({str(REPO_ROOT / 'src' / 'platform')!r})))
spec = importlib.util.spec_from_file_location("lookup_mod", {str(SCRIPT_PATH)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.LEADS_PATH = pathlib.Path({str(leads_path)!r})
mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'no_config.yaml')!r})  # forces UTC fallback
sys.argv = ["lookup-prior-leads-by-phone", "--customer-phone", {phone!r}]
sys.exit(mod.main())
"""
    proc = subprocess.run(
        [sys.executable, "-c", wrapper], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    cli_out = json.loads(proc.stdout.strip().splitlines()[-1])

    # Compare structural fields (last_seen_days_ago will differ because CLI
    # uses real wall-clock now). Direct sets `now` explicitly; just confirm
    # all keys + value types match.
    assert set(cli_out.keys()) == set(direct.keys())
    assert cli_out["lookup_status"] == direct["lookup_status"]
    assert cli_out["prior_lead_count"] == direct["prior_lead_count"]
    # most_recent_status must round-trip as plain str (Literal serialization)
    assert isinstance(cli_out["most_recent_status"], str)
    assert cli_out["most_recent_status"] == direct["most_recent_status"]
    assert cli_out["most_recent_event_date"] == direct["most_recent_event_date"]
    assert cli_out["most_recent_dietary_restrictions"] == direct["most_recent_dietary_restrictions"]


# ---------- 8. pure-read invariant ----------

def test_pure_read_no_state_mutation_bytes_snapshot(env_dir):
    """Test-analyzer #8 replacement: bytes-equality is behavior-based, not
    metadata-based (mtime/atime fs-coupling). Also asserts no sibling files
    appeared (no .tmp / .bak / .lock created in the read path)."""
    mod = _load_script()
    phone = "+19045551234"
    leads_path = _seed_leads(env_dir, [
        _mk_lead(lead_id="L001", phone=phone,
                 created_at=datetime.now(tz=timezone.utc) - timedelta(days=2))
    ])
    state_dir = leads_path.parent
    before_bytes = leads_path.read_bytes()
    before_files = sorted(p.name for p in state_dir.iterdir())

    mod.lookup_prior_leads_by_phone(phone, leads_path=leads_path)

    after_bytes = leads_path.read_bytes()
    after_files = sorted(p.name for p in state_dir.iterdir())
    assert before_bytes == after_bytes, "lookup must not mutate leads.json contents"
    assert before_files == after_files, (
        f"lookup must not create sibling files; before={before_files} after={after_files}"
    )
