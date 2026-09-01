"""Tests for shift-agent-lid-learn — applies lid-cache.json pairs to
roster.json + config.yaml. Linux-only (uses fcntl via safe_io)."""
from __future__ import annotations
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="lid-learn depends on safe_io which uses fcntl (Linux only)",
)

SCRIPT = Path(__file__).resolve().parent.parent / "src" / "agents" / "shift" / "scripts" / "shift-agent-lid-learn"


@pytest.fixture
def fixture_dir(tmp_path):
    """Build minimal valid roster, config, empty cache + log."""
    roster = {
        "location": {"id": "loc_test", "name": "Test", "timezone": "America/New_York"},
        "employees": [
            {"id": "e004", "name": "Anjali Iyer", "role": "cashier",
             "phone": "+15550100001", "languages": ["en"],
             "can_cover_roles": ["cashier"], "status": "active"},
            {"id": "e006", "name": "Lakshmi Rao", "role": "sweets",
             "phone": "+19802005022", "languages": ["en"],
             "can_cover_roles": ["cashier", "sweets"], "status": "active"},
        ],
        "schedule": {},
    }
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_test",
                     "timezone": "America/New_York", "languages": ["en"]},
        "owner": {"name": "Owner", "phone": "+15550100002",
                  "self_chat_jid": "15550100002@s.whatsapp.net"},
        "limits": {"max_outbound_per_day": 6, "max_outbound_per_minute": 30,
                   "pending_proposal_ttl_hours": 4,
                   "per_message_timeout_sec": 120, "send_failure_retry_count": 1},
        "alerting": {"pushover_user_key": "x", "pushover_app_token": "y"},
        "backup": {"gpg_recipient_email": "test@example.com", "retention_days": 30},
        "operations": {"business_hours_local": "08:00-22:00"},
    }
    import yaml
    (tmp_path / "roster.json").write_text(json.dumps(roster))
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    (tmp_path / "decisions.log").write_text("")
    return tmp_path


def _run(fixture_dir, cache):
    cache_path = fixture_dir / "lid-cache.json"
    cache_path.write_text(json.dumps(cache))
    env = {
        **os.environ,
        "SHIFT_AGENT_LID_CACHE_PATH": str(cache_path),
        "SHIFT_AGENT_ROSTER_PATH": str(fixture_dir / "roster.json"),
        "SHIFT_AGENT_CONFIG_PATH": str(fixture_dir / "config.yaml"),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(fixture_dir / "decisions.log"),
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, env=env,
    )


def _content_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_apply_lid_to_employee(fixture_dir):
    cache = {
        "schema_version": 1,
        "pairs": [{"phone": "+15550100001", "lid": "100000000000001@lid",
                   "learned_ts": "2026-04-28T00:00:00+00:00"}],
    }
    r = _run(fixture_dir, cache)
    assert r.returncode == 0, r.stderr

    roster = json.loads((fixture_dir / "roster.json").read_text())
    e004 = next(e for e in roster["employees"] if e["id"] == "e004")
    assert e004["lid"] == "100000000000001@lid"

    log = (fixture_dir / "decisions.log").read_text().strip().split("\n")
    assert len(log) == 1
    entry = json.loads(log[0])
    assert entry["type"] == "lid_learned"
    assert entry["target"] == "employee"
    assert entry["employee_id"] == "e004"
    assert entry["new_lid"] == "100000000000001@lid"
    assert entry["old_lid"] is None


def test_apply_lid_to_owner(fixture_dir):
    cache = {
        "schema_version": 1,
        "pairs": [{"phone": "+15550100002", "lid": "211390371475536@lid",
                   "learned_ts": "2026-04-28T00:00:00+00:00"}],
    }
    r = _run(fixture_dir, cache)
    assert r.returncode == 0, r.stderr

    import yaml
    cfg = yaml.safe_load((fixture_dir / "config.yaml").read_text())
    assert cfg["owner"]["lid"] == "211390371475536@lid"

    log = (fixture_dir / "decisions.log").read_text().strip().split("\n")
    assert len(log) == 1
    entry = json.loads(log[0])
    assert entry["target"] == "owner"


def test_idempotent_no_rewrite_when_already_set(fixture_dir):
    cache = {
        "schema_version": 1,
        "pairs": [{"phone": "+15550100001", "lid": "100000000000001@lid",
                   "learned_ts": "2026-04-28T00:00:00+00:00"}],
    }
    _run(fixture_dir, cache)  # first run: applies
    h1 = _content_hash(fixture_dir / "roster.json")
    log_size_1 = (fixture_dir / "decisions.log").stat().st_size

    _run(fixture_dir, cache)  # second run: should be no-op
    h2 = _content_hash(fixture_dir / "roster.json")
    log_size_2 = (fixture_dir / "decisions.log").stat().st_size

    # Roster content must be byte-identical (DH8 — content hash, not mtime)
    assert h1 == h2
    # Audit log must not have grown (no spam)
    assert log_size_1 == log_size_2


def test_conflict_detection_logged(fixture_dir):
    """Phone re-pair scenario: cache has new LID for same phone.
    LIDs must be digit-only per the schema regex `^\\d{6,20}@lid$`."""
    OLD_LID = "100000000000001@lid"
    NEW_LID = "200000000000002@lid"

    # First: set initial LID
    cache1 = {"schema_version": 1, "pairs": [
        {"phone": "+15550100001", "lid": OLD_LID,
         "learned_ts": "2026-04-28T00:00:00+00:00"}
    ]}
    _run(fixture_dir, cache1)

    # Then: re-pair with new LID
    cache2 = {"schema_version": 1, "pairs": [
        {"phone": "+15550100001", "lid": NEW_LID,
         "learned_ts": "2026-04-28T01:00:00+00:00"}
    ]}
    r = _run(fixture_dir, cache2)
    assert r.returncode == 0, r.stderr

    roster = json.loads((fixture_dir / "roster.json").read_text())
    e004 = next(e for e in roster["employees"] if e["id"] == "e004")
    assert e004["lid"] == NEW_LID

    log = [json.loads(l) for l in (fixture_dir / "decisions.log").read_text().strip().split("\n")]
    assert len(log) == 2
    assert log[1]["old_lid"] == OLD_LID
    assert log[1]["new_lid"] == NEW_LID


def test_unknown_phone_no_mutation(fixture_dir):
    cache = {"schema_version": 1, "pairs": [
        {"phone": "+15551234567", "lid": "999999999999@lid",
         "learned_ts": "2026-04-28T00:00:00+00:00"}
    ]}
    h_before = _content_hash(fixture_dir / "roster.json")
    r = _run(fixture_dir, cache)
    assert r.returncode == 0, r.stderr

    h_after = _content_hash(fixture_dir / "roster.json")
    assert h_before == h_after  # no mutation
    assert (fixture_dir / "decisions.log").read_text() == ""  # no audit entry


def test_schema_version_mismatch_exits_5(fixture_dir):
    cache = {"schema_version": 2, "pairs": []}
    r = _run(fixture_dir, cache)
    assert r.returncode == 5


def test_empty_cache_file_safe(fixture_dir):
    """Power-loss remnant: empty file. Must not crash, must not mutate."""
    cache_path = fixture_dir / "lid-cache.json"
    cache_path.write_text("")
    env = {
        **os.environ,
        "SHIFT_AGENT_LID_CACHE_PATH": str(cache_path),
        "SHIFT_AGENT_ROSTER_PATH": str(fixture_dir / "roster.json"),
        "SHIFT_AGENT_CONFIG_PATH": str(fixture_dir / "config.yaml"),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(fixture_dir / "decisions.log"),
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
    }
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr


def test_cache_trimmed_after_apply(fixture_dir):
    cache = {"schema_version": 1, "pairs": [
        {"phone": "+15550100001", "lid": "100000000000001@lid",
         "learned_ts": "2026-04-28T00:00:00+00:00"},
        {"phone": "+15551234567", "lid": "999999999999@lid",  # unknown
         "learned_ts": "2026-04-28T00:00:00+00:00"},
    ]}
    _run(fixture_dir, cache)
    # After apply: applied phones removed from cache; unknown phone retained
    final_cache = json.loads((fixture_dir / "lid-cache.json").read_text())
    phones = {p["phone"] for p in final_cache["pairs"]}
    assert "+15550100001" not in phones  # applied → trimmed
    assert "+15551234567" in phones  # unknown → kept for retry


# ── phone_history effective windows (2026-09-01) ─────────────────────────────
#
# lid-learn matched ANY phone_history entry regardless of its window, while
# Roster.find_by_phone and identify-sender both skip a closed assignment --
# and find_by_phone documents that window as precisely what stops a recycled
# number from inheriting an identity.
#
# Consequence: a number an employee gave up, now held by someone else, still
# matched that employee's row, so the new holder's LID was written onto it.
# Where the row also carries an owner-authorized phone, the stranger's LID
# then resolves to OWNER. Measured against the real resolver, not theorised.

def _with_history(fixture_dir, entries, *, emp_id="e004"):
    import json as _json
    roster = _json.loads((fixture_dir / "roster.json").read_text())
    emp = next(e for e in roster["employees"] if e["id"] == emp_id)
    emp["phone_history"] = entries
    (fixture_dir / "roster.json").write_text(_json.dumps(roster))
    return roster


RECYCLED = "+15550109999"


def test_expired_history_entry_does_not_capture_a_recycled_number(fixture_dir):
    """The defect. e004 gave this number up; someone else holds it now."""
    _with_history(fixture_dir, [{
        "phone": RECYCLED,
        "effective_from": "2024-01-01T00:00:00+00:00",
        "effective_to": "2024-06-01T00:00:00+00:00",   # CLOSED
    }])
    before = _content_hash(fixture_dir / "roster.json")

    r = _run(fixture_dir, {"schema_version": 1, "pairs": [
        {"phone": RECYCLED, "lid": "999000000000001@lid",
         "learned_ts": "2026-04-28T00:00:00+00:00"}]})

    assert r.returncode == 0, r.stderr
    roster = json.loads((fixture_dir / "roster.json").read_text())
    e004 = next(e for e in roster["employees"] if e["id"] == "e004")
    assert e004.get("lid") is None, "a stranger's LID was written onto e004"
    # WITNESS: nothing was written at all, and no audit row claims otherwise.
    assert _content_hash(fixture_dir / "roster.json") == before
    assert (fixture_dir / "decisions.log").read_text().strip() == ""


def test_an_open_history_entry_still_captures(fixture_dir):
    """The paired control.

    Without this, a fix that simply ignored phone_history entirely would pass
    the test above. An OPEN window is a live assignment and must still match.
    """
    _with_history(fixture_dir, [{
        "phone": RECYCLED,
        "effective_from": "2024-01-01T00:00:00+00:00",   # no effective_to
    }])

    r = _run(fixture_dir, {"schema_version": 1, "pairs": [
        {"phone": RECYCLED, "lid": "999000000000002@lid",
         "learned_ts": "2026-04-28T00:00:00+00:00"}]})

    assert r.returncode == 0, r.stderr
    roster = json.loads((fixture_dir / "roster.json").read_text())
    e004 = next(e for e in roster["employees"] if e["id"] == "e004")
    assert e004["lid"] == "999000000000002@lid"


def test_a_primary_phone_match_is_unaffected_by_history_handling(fixture_dir):
    """Second control: the ordinary path must not regress."""
    _with_history(fixture_dir, [{
        "phone": RECYCLED,
        "effective_from": "2024-01-01T00:00:00+00:00",
        "effective_to": "2024-06-01T00:00:00+00:00",
    }])
    r = _run(fixture_dir, {"schema_version": 1, "pairs": [
        {"phone": "+15550100001", "lid": "100000000000009@lid",
         "learned_ts": "2026-04-28T00:00:00+00:00"}]})
    assert r.returncode == 0, r.stderr
    roster = json.loads((fixture_dir / "roster.json").read_text())
    e004 = next(e for e in roster["employees"] if e["id"] == "e004")
    assert e004["lid"] == "100000000000009@lid"


# ── config validation before write (2026-09-01) ──────────────────────────────

def test_a_malformed_owner_lid_is_refused_by_the_audit_model(fixture_dir):
    r"""A malformed LID never reaches either store -- but NOT for the reason
    I first assumed, and the difference is worth recording.

    I added `Config.model_validate` to the config write path because the
    roster path was validated and the config path was a bare yaml dump, so
    `OwnerConfig.lid`'s ^\d{6,20}@lid$ pattern appeared never to run for the
    OWNER. A mutant run disproved that: removing the new validation left this
    test still passing.

    The actual refusal comes from `LidLearned.new_lid`, which carries the same
    pattern and is constructed while the pair is processed -- before any write
    is attempted. So the config path was already covered for this input.

    The added `Config.model_validate` is therefore defence in depth with no
    currently-reachable trigger, not a fix for a live defect. It is kept for
    symmetry with the roster write, and this test asserts the mechanism that
    ACTUALLY refuses, with a witness, so the next reader is not misled.
    """
    before = _content_hash(fixture_dir / "config.yaml")

    r = _run(fixture_dir, {"schema_version": 1, "pairs": [
        {"phone": "+15550100002", "lid": "not-a-valid-lid",
         "learned_ts": "2026-04-28T00:00:00+00:00"}]})

    assert r.returncode != 0, "a malformed owner lid was accepted"
    # WITNESS: names the model that refused, so an unrelated crash cannot pass.
    assert "LidLearned" in r.stderr, r.stderr[-400:]
    assert "new_lid" in r.stderr
    # WITNESS: refused BEFORE persistence, and nothing was audited either.
    assert _content_hash(fixture_dir / "config.yaml") == before
    assert (fixture_dir / "decisions.log").read_text().strip() == ""


def test_a_wellformed_owner_lid_is_still_applied(fixture_dir):
    """The control. Validation must not block the legitimate case."""
    import yaml
    r = _run(fixture_dir, {"schema_version": 1, "pairs": [
        {"phone": "+15550100002", "lid": "200000000000001@lid",
         "learned_ts": "2026-04-28T00:00:00+00:00"}]})
    assert r.returncode == 0, r.stderr
    cfg = yaml.safe_load((fixture_dir / "config.yaml").read_text())
    assert cfg["owner"]["lid"] == "200000000000001@lid"
