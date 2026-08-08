"""Agent #13 Wave-1 — add-compliance-item.py, the missing seed path.

The items file shipped documented as "operator-seeded" with no seeder in the
repo, so `compliance_owner_query` read an empty list on every deployment and
answered "nothing due" from no data at all. These tests pin the seeder and,
crucially, the round trip: seeded item -> check-compliance-deadlines.py actually
sees it. A seeder that writes a file nothing downstream reads is not a delivery.

Linux-only (fcntl), subprocess-invoked — mirrors test_agent_13_compliance_script.py.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="depends on safe_io which uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parent.parent
ADD_SCRIPT = REPO / "src" / "agents" / "compliance" / "scripts" / "add-compliance-item.py"
CHECK_SCRIPT = REPO / "src" / "agents" / "compliance" / "scripts" / "check-compliance-deadlines.py"
MARK_SCRIPT = REPO / "src" / "agents" / "compliance" / "scripts" / "mark-compliance-item-done.py"
PLATFORM_DIR = REPO / "src" / "platform"

sys.path.insert(0, str(PLATFORM_DIR))


@pytest.fixture
def env_dir(tmp_path):
    """A box with config + logs but NO compliance-items.json — the real
    first-seed situation on a freshly provisioned VPS."""
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    config = {
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_jax_01",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "test_k", "pushover_app_token": "test_t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "compliance": {"enabled": True,
                       "advance_warning_days": [30, 14, 7, 3, 1],
                       "max_deferral_days": 7},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (tmp_path / "logs" / "decisions.log").write_text("", encoding="utf-8")
    return tmp_path


def _env(env_dir: Path) -> dict:
    return {
        **os.environ,
        "SHIFT_AGENT_CONFIG_PATH": str(env_dir / "config.yaml"),
        "SHIFT_AGENT_COMPLIANCE_ITEMS_PATH": str(env_dir / "state" / "compliance-items.json"),
        "SHIFT_AGENT_COMPLIANCE_SENTINEL_PATH": str(env_dir / "state" / "compliance-last-sent.json"),
        "SHIFT_AGENT_COMPLIANCE_HEARTBEAT_PATH": str(env_dir / "state" / "compliance-last-cron-tick.json"),
        "SHIFT_AGENT_COMPLIANCE_LOCK_PATH": str(env_dir / "state" / "compliance-check.json.lock"),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(env_dir / "logs" / "decisions.log"),
        "PYTHONPATH": str(PLATFORM_DIR),
    }


def _add(env_dir, **kw):
    args = [sys.executable, str(ADD_SCRIPT)]
    flags = {
        "--id": kw.pop("item_id", "health_inspect_houston"),
        "--name": kw.pop("name", "Health Inspection Houston"),
        "--category": kw.pop("category", "inspection"),
        "--renewal-date": kw.pop("renewal_date", "2026-09-01"),
        "--recurrence-days": str(kw.pop("recurrence_days", 365)),
    }
    for k, v in flags.items():
        args += [k, v]
    for opt in ("agency", "notes", "location_id", "resource_url"):
        if opt in kw:
            args += [f"--{opt.replace('_', '-')}", str(kw.pop(opt))]
    if kw.pop("replace", False):
        args.append("--replace")
    if kw.pop("dry_run", False):
        args.append("--dry-run")
    assert not kw, f"unconsumed kwargs: {kw}"
    return subprocess.run(args, env=_env(env_dir), capture_output=True,
                          text=True, timeout=20)


STORE_NAME = "compliance-items.json"
AUDIT_TYPE = "compliance_item_upserted"
ID_KW = "item_id"


def _add_raw(env_dir, extra):
    """Invoke with valid required args PLUS `extra` (for rejected-flag tests)."""
    args = [sys.executable, str(ADD_SCRIPT),
            "--id", "health_inspect_houston", "--name", "Health Inspection Houston",
            "--category", "inspection", "--renewal-date", "2026-09-01",
            "--recurrence-days", "365"] + list(extra)
    return subprocess.run(args, env=_env(env_dir), capture_output=True,
                          text=True, timeout=20)


def _items(env_dir) -> dict:
    p = env_dir / "state" / "compliance-items.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _audit(env_dir) -> list[dict]:
    p = env_dir / "logs" / "decisions.log"
    out = []
    for line in p.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


# ── seeding ────────────────────────────────────────────────────────────────

def test_seeds_first_item_onto_a_box_with_no_items_file(env_dir):
    r = _add(env_dir)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["item_id"] == "health_inspect_houston"
    assert out["replaced"] is False
    assert out["n_items"] == 1
    assert _items(env_dir)["items"][0]["agency"] is None


def test_optional_fields_round_trip(env_dir):
    r = _add(env_dir, agency="HCPHTX", notes="bring prior report",
             location_id="loc_jax_01")
    assert r.returncode == 0, r.stderr
    item = _items(env_dir)["items"][0]
    assert item["agency"] == "HCPHTX"
    assert item["notes"] == "bring prior report"
    assert item["location_id"] == "loc_jax_01"


def test_second_distinct_item_appends(env_dir):
    assert _add(env_dir).returncode == 0
    r = _add(env_dir, item_id="tabc_permit", name="TABC Permit",
             category="license_renewal", renewal_date="2026-11-15")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["n_items"] == 2
    assert {i["id"] for i in _items(env_dir)["items"]} == {
        "health_inspect_houston", "tabc_permit"}


def test_writes_compliance_item_upserted_audit(env_dir):
    _add(env_dir)
    rows = [e for e in _audit(env_dir) if e.get("type") == "compliance_item_upserted"]
    assert len(rows) == 1
    assert rows[0]["item_id"] == "health_inspect_houston"
    assert rows[0]["replaced"] is False
    assert rows[0]["previous_renewal_date"] is None
    assert rows[0]["actor"] == "operator"


# ── overwrite protection (a seeded deadline is load-bearing) ───────────────

def test_duplicate_id_refused_without_replace(env_dir):
    assert _add(env_dir).returncode == 0
    r = _add(env_dir, renewal_date="2027-01-01")
    assert r.returncode == 1
    assert json.loads(r.stdout)["error"] == "item_exists"
    # The original date must survive a refused overwrite.
    assert _items(env_dir)["items"][0]["renewal_date"] == "2026-09-01"


def test_replace_overwrites_and_records_previous_date(env_dir):
    assert _add(env_dir).returncode == 0
    r = _add(env_dir, renewal_date="2027-01-01", replace=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["replaced"] is True
    assert out["previous_renewal_date"] == "2026-09-01"
    assert out["n_items"] == 1, "replace must not duplicate the row"
    assert _items(env_dir)["items"][0]["renewal_date"] == "2027-01-01"
    row = [e for e in _audit(env_dir) if e.get("type") == "compliance_item_upserted"][-1]
    assert row["replaced"] is True
    assert row["previous_renewal_date"] == "2026-09-01"


# ── input validation ───────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    {"item_id": "Health-Inspect"},          # uppercase + hyphen
    {"item_id": "health:inspect"},          # ':' corrupts sentinel keys
    {"renewal_date": "01-09-2026"},         # wrong date format
    {"recurrence_days": -1},                # negative
    {"recurrence_days": 4000},              # above the 3650 cap
])
def test_invalid_input_rejected_without_creating_state(env_dir, bad):
    r = _add(env_dir, **bad)
    assert r.returncode == 2, f"expected rejection, got rc={r.returncode} {r.stdout}"
    assert not (env_dir / "state" / "compliance-items.json").exists(), (
        "a rejected item must not leave a state file behind"
    )


def test_bad_category_rejected_by_argparse(env_dir):
    r = _add(env_dir, category="not_a_category")
    assert r.returncode == 2


# ── the round trip that makes this a delivery ──────────────────────────────

def test_seeded_item_is_visible_to_the_deadline_checker(env_dir):
    """THE point of the workflow: seed -> the reader actually reports it.

    Seeds a deadline 7 days out and runs check-compliance-deadlines.py at a
    fixed 'now'. The checker must pick the item up; before this seeder existed
    there was no way to get a row in front of it at all.
    """
    assert _add(env_dir, renewal_date="2026-09-08", recurrence_days=365).returncode == 0

    r = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "--dry-run"],
        env={**_env(env_dir), "SHIFT_AGENT_NOW_OVERRIDE": "2026-09-01T09:00:00-04:00"},
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    combined = r.stdout + json.dumps(_audit(env_dir))
    assert "health_inspect_houston" in combined, (
        "seeded item never reached check-compliance-deadlines.py\n"
        f"stdout={r.stdout}\naudit={_audit(env_dir)}"
    )


def test_seeded_item_can_then_be_marked_done(env_dir):
    """Seed -> mark-done advances the recurrence, closing the owner's loop."""
    assert _add(env_dir, renewal_date="2026-09-01", recurrence_days=365).returncode == 0

    r = subprocess.run(
        [sys.executable, str(MARK_SCRIPT), "--item-id", "health_inspect_houston",
         "--actor", "owner"],
        env={**_env(env_dir), "SHIFT_AGENT_NOW_OVERRIDE": "2026-09-02T09:00:00-04:00"},
        capture_output=True, text=True, timeout=20,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["completed"] == "2026-09-01"
    assert out["next"] == "2027-09-01"
    assert out["deleted"] is False


# ── dry-run contract (Wave-1 review 2026-08-08) ────────────────────────────
# The original dry-run test only asserted `items == []`, which PASSED while the
# script was creating the store file on a fresh box. Three explicit properties
# now, one per failure mode.


def _snapshot(p):
    return p.read_bytes() if p.exists() else None


def test_dry_run_leaves_absent_store_absent(env_dir):
    """THE bug: on a fresh box --dry-run used to materialize the empty store."""
    store = env_dir / "state" / STORE_NAME
    assert not store.exists(), "fixture precondition: store absent"
    r = _add(env_dir, dry_run=True)
    assert r.returncode == 0, r.stderr
    assert not store.exists(), (
        "--dry-run created the store file on a fresh box; dry-run must not "
        "mutate persistent state"
    )


def test_dry_run_leaves_existing_store_byte_identical(env_dir):
    assert _add(env_dir).returncode == 0
    store = env_dir / "state" / STORE_NAME
    before = _snapshot(store)
    r = _add(env_dir, **{ID_KW: "second_item"}, dry_run=True)
    assert r.returncode == 0, r.stderr
    assert _snapshot(store) == before, "--dry-run mutated an existing store"


def test_dry_run_leaves_decisions_log_byte_identical(env_dir):
    log = env_dir / "logs" / "decisions.log"
    before = _snapshot(log)
    assert _add(env_dir, dry_run=True).returncode == 0
    assert _snapshot(log) == before, "--dry-run wrote an audit row"


# ── audit actor provenance ─────────────────────────────────────────────────

def test_cli_cannot_fabricate_actor_owner(env_dir):
    """argv must not be able to assert owner provenance.

    No authenticated sender context exists for a shell caller, so an audit row
    claiming `actor=owner` would be unproven. The flag is gone; passing it is a
    hard argparse error, and the row that IS written says `operator`.
    """
    r = _add_raw(env_dir, ["--actor", "owner"])
    assert r.returncode == 2, (
        f"--actor should be rejected outright, got rc={r.returncode}"
    )
    assert "unrecognized arguments" in (r.stderr or "").lower()

    assert _add(env_dir).returncode == 0
    rows = [e for e in _audit(env_dir) if e.get("type") == AUDIT_TYPE]
    assert rows and all(row["actor"] == "operator" for row in rows), (
        f"seed CLI must record actor=operator, got {[r_['actor'] for r_ in rows]}"
    )


# ── config failure modes exit cleanly, never as a traceback ────────────────

@pytest.mark.parametrize("mangle,label", [
    (lambda p: p.unlink(), "missing"),
    (lambda p: p.write_text("customer: [unclosed\n", encoding="utf-8"), "malformed-yaml"),
    (lambda p: p.write_text("schema_version: 1\ncustomer: {}\n", encoding="utf-8"),
     "schema-invalid"),
])
def test_bad_config_exits_two_without_traceback(env_dir, mangle, label):
    mangle(env_dir / "config.yaml")
    r = _add(env_dir)
    assert r.returncode == 2, f"{label}: expected rc=2, got {r.returncode}"
    assert "Traceback" not in (r.stderr or ""), (
        f"{label}: config failure escaped as an uncaught traceback:\n{r.stderr}"
    )
    assert not (env_dir / "state" / STORE_NAME).exists()
