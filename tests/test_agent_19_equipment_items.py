"""Agent #19 Wave-1 — seed path + dispatcher reachability.

Agent #19 shipped as a self-declining stub: one SKILL.md, no store, and no
dispatcher routing row. It was in tools/skills-manifest.txt (so it deployed) and
structurally unreachable (so it could never run). These tests pin both halves of
the fix: the store can be seeded, and the dispatcher actually has a row.

Store tests are Linux-only (fcntl via safe_io) and subprocess-invoked, mirroring
tests/test_agent_13_compliance_script.py. The regex tests are pure-Python and
run everywhere.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ADD_SCRIPT = REPO / "src" / "agents" / "equipment_maintenance" / "scripts" / "add-equipment-item.py"
PLATFORM_DIR = REPO / "src" / "platform"
DISPATCHER_SKILL = (
    REPO / "src" / "agents" / "shift" / "skills" / "dispatch_shift_agent" / "SKILL.md"
)
EQUIP_SKILL = (
    REPO / "src" / "agents" / "equipment_maintenance" / "skills"
    / "equipment_maintenance_dispatcher" / "SKILL.md"
)

sys.path.insert(0, str(PLATFORM_DIR))

linux_only = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="depends on safe_io which uses fcntl (Linux only)",
)


# ── dispatcher reachability (cross-platform) ───────────────────────────────

def test_dispatcher_has_an_equipment_routing_row():
    """THE structural fix. Without a row the SKILL can never be invoked."""
    text = DISPATCHER_SKILL.read_text(encoding="utf-8")
    assert "equipment_maintenance_dispatcher" in text, (
        "dispatch_shift_agent has no row routing to equipment_maintenance_dispatcher "
        "— the agent deploys but is unreachable"
    )


def test_equipment_row_is_gated_on_owner_and_config():
    text = DISPATCHER_SKILL.read_text(encoding="utf-8")
    row = next(
        ln for ln in text.splitlines()
        if "**equipment_maintenance_dispatcher**" in ln
    )
    assert "owner" in row, "equipment row must be owner-gated"
    assert "cfg.equipment_maintenance.enabled" in row, "row must respect the config gate"


def test_equipment_row_sits_after_compliance_row():
    """Ordering is load-bearing: 'fire suppression inspection' is a compliance
    calendar question first. The matrix is priority-ordered, so compliance must
    appear above equipment."""
    lines = DISPATCHER_SKILL.read_text(encoding="utf-8").splitlines()
    compliance_at = next(
        i for i, ln in enumerate(lines) if "**compliance_owner_query**" in ln
    )
    equipment_at = next(
        i for i, ln in enumerate(lines) if "**equipment_maintenance_dispatcher**" in ln
    )
    assert compliance_at < equipment_at


def test_skill_frontmatter_describes_a_read_workflow():
    text = EQUIP_SKILL.read_text(encoding="utf-8")
    assert text.startswith("---"), "SKILL.md needs YAML frontmatter"
    fm = yaml.safe_load(text.split("---")[1])
    assert fm["name"] == "equipment_maintenance_dispatcher"
    assert "Phase 0" not in text, "SKILL is still the self-declining stub"
    # The empty-file trap is the one that produces a false operational claim.
    assert "Empty or missing file" in text


# ── the routing regex (pure-Python; kept in sync with the SKILL) ───────────

_EQUIPMENT_REGEX = re.compile(
    r"\b(maintenance|servicing|preventive|pm\s+due)\b"
    r"|\b(service|serviced|repair|repaired|filter\s+change|calibration)\b.{0,40}"
    r"\b(walk[\s-]?in|cooler|freezer|fridge|refrigerat\w*|oven|fryer|grill|hood|pos|"
    r"terminal|register|van|truck|vehicle|a/?c|hvac|fire\s+suppression|equipment)\b"
    r"|\b(walk[\s-]?in|cooler|freezer|oven|fryer|hood|fire\s+suppression)\b.{0,30}"
    r"\b(due|overdue|schedule[d]?|next\s+service|serviced|service|repair(?:ed)?)\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize("text", [
    "what maintenance is due?",
    "any preventive maintenance coming up",
    "when was the walk-in serviced?",
    "need a repair on the fryer",
    "filter change for the hood?",
    "is the freezer overdue",
    "when is the next service on the oven",
    "calibration for the POS terminal",
])
def test_equipment_regex_matches_real_questions(text):
    assert _EQUIPMENT_REGEX.search(text), f"should match: {text!r}"


@pytest.mark.parametrize("text", [
    "service was slow today",              # 'service' with no equipment noun
    "can you repair the wording on that flyer",   # repair, wrong domain
    "the cooler drinks are selling well",  # equipment noun, no service intent
    "I can't come in, I'm sick",           # shift
    "need catering for 200 guests",        # catering
    "make me a poster for Diwali",         # flyer
])
def test_equipment_regex_ignores_unrelated_traffic(text):
    assert not _EQUIPMENT_REGEX.search(text), f"should NOT match: {text!r}"


# ── seed CLI ───────────────────────────────────────────────────────────────

@pytest.fixture
def env_dir(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    config = {
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_jax_01",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "equipment_maintenance": {"enabled": True},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (tmp_path / "logs" / "decisions.log").write_text("", encoding="utf-8")
    return tmp_path


def _add(env_dir, **kw):
    args = [sys.executable, str(ADD_SCRIPT),
            "--id", kw.pop("equipment_id", "walkin_cooler_main"),
            "--name", kw.pop("name", "Walk-in Cooler (main)"),
            "--category", kw.pop("category", "refrigeration"),
            "--next-service-date", kw.pop("next_service_date", "2026-09-01"),
            "--interval-days", str(kw.pop("interval_days", 90))]
    for opt in ("vendor_name", "vendor_phone", "location_id", "serial", "notes"):
        if opt in kw:
            args += [f"--{opt.replace('_', '-')}", str(kw.pop(opt))]
    if kw.pop("replace", False):
        args.append("--replace")
    if kw.pop("dry_run", False):
        args.append("--dry-run")
    assert not kw, f"unconsumed kwargs: {kw}"
    env = {
        **os.environ,
        "SHIFT_AGENT_CONFIG_PATH": str(env_dir / "config.yaml"),
        "SHIFT_AGENT_EQUIPMENT_ITEMS_PATH": str(env_dir / "state" / "equipment-items.json"),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(env_dir / "logs" / "decisions.log"),
        "PYTHONPATH": str(PLATFORM_DIR),
    }
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=20)


STORE_NAME = "equipment-items.json"
AUDIT_TYPE = "equipment_item_upserted"
ID_KW = "equipment_id"


def _env(env_dir):
    return {
        **os.environ,
        "SHIFT_AGENT_CONFIG_PATH": str(env_dir / "config.yaml"),
        "SHIFT_AGENT_EQUIPMENT_ITEMS_PATH": str(env_dir / "state" / "equipment-items.json"),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(env_dir / "logs" / "decisions.log"),
        "PYTHONPATH": str(PLATFORM_DIR),
    }


def _add_raw(env_dir, extra):
    """Invoke with valid required args PLUS `extra` (for rejected-flag tests)."""
    args = [sys.executable, str(ADD_SCRIPT),
            "--id", "walkin_cooler_main", "--name", "Walk-in Cooler (main)",
            "--category", "refrigeration", "--next-service-date", "2026-09-01",
            "--interval-days", "90"] + list(extra)
    return subprocess.run(args, env=_env(env_dir), capture_output=True,
                          text=True, timeout=20)


def _items(env_dir):
    p = env_dir / "state" / "equipment-items.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _audit(env_dir):
    out = []
    for line in (env_dir / "logs" / "decisions.log").read_text(encoding="utf-8").split("\n"):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


@linux_only
def test_seeds_first_asset_on_a_box_with_no_store(env_dir):
    r = _add(env_dir)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["equipment_id"] == "walkin_cooler_main"
    assert out["replaced"] is False and out["n_items"] == 1


@linux_only
def test_vendor_details_round_trip(env_dir):
    r = _add(env_dir, vendor_name="Hobart Service", vendor_phone="+18005551234",
             serial="WC-99120")
    assert r.returncode == 0, r.stderr
    item = _items(env_dir)["items"][0]
    assert item["vendor_name"] == "Hobart Service"
    assert item["vendor_phone"] == "+18005551234"
    assert item["serial"] == "WC-99120"


@linux_only
def test_writes_equipment_item_upserted_audit(env_dir):
    _add(env_dir)
    rows = [e for e in _audit(env_dir) if e.get("type") == "equipment_item_upserted"]
    assert len(rows) == 1
    assert rows[0]["equipment_id"] == "walkin_cooler_main"
    assert rows[0]["replaced"] is False
    assert rows[0]["previous_next_service_date"] is None


@linux_only
def test_duplicate_id_refused_without_replace(env_dir):
    assert _add(env_dir).returncode == 0
    r = _add(env_dir, next_service_date="2027-01-01")
    assert r.returncode == 1
    assert json.loads(r.stdout)["error"] == "item_exists"
    assert _items(env_dir)["items"][0]["next_service_date"] == "2026-09-01"


@linux_only
def test_replace_overwrites_and_records_previous_date(env_dir):
    assert _add(env_dir).returncode == 0
    r = _add(env_dir, next_service_date="2027-01-01", replace=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["replaced"] is True
    assert out["previous_next_service_date"] == "2026-09-01"
    assert out["n_items"] == 1, "replace must not duplicate the asset"


@linux_only
@pytest.mark.parametrize("bad", [
    {"equipment_id": "Walkin-Cooler"},      # uppercase + hyphen
    {"equipment_id": "walkin:cooler"},      # ':' reserved for v0.2 sentinel keys
    {"next_service_date": "01-09-2026"},    # wrong format
    {"interval_days": -1},
    {"interval_days": 4000},
])
def test_invalid_input_rejected_without_creating_state(env_dir, bad):
    r = _add(env_dir, **bad)
    assert r.returncode == 2, f"rc={r.returncode} stdout={r.stdout}"
    assert not (env_dir / "state" / "equipment-items.json").exists()


@linux_only
@linux_only
def test_multiple_assets_sort_by_service_date_for_the_read_path(env_dir):
    """The SKILL sorts by days_until; this pins that the data supports it."""
    assert _add(env_dir, equipment_id="hood_suppression", name="Hood Suppression",
                category="fire_safety", next_service_date="2026-08-20").returncode == 0
    assert _add(env_dir, equipment_id="walkin_cooler_main",
                next_service_date="2026-12-01").returncode == 0
    dates = sorted(i["next_service_date"] for i in _items(env_dir)["items"])
    assert dates == ["2026-08-20", "2026-12-01"]


# ── dry-run contract (Wave-1 review 2026-08-08) ────────────────────────────
# The original dry-run test only asserted `items == []`, which PASSED while the
# script was creating the store file on a fresh box. Three explicit properties
# now, one per failure mode.


def _snapshot(p):
    return p.read_bytes() if p.exists() else None


@linux_only
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


@linux_only
def test_dry_run_leaves_existing_store_byte_identical(env_dir):
    assert _add(env_dir).returncode == 0
    store = env_dir / "state" / STORE_NAME
    before = _snapshot(store)
    r = _add(env_dir, **{ID_KW: "second_item"}, dry_run=True)
    assert r.returncode == 0, r.stderr
    assert _snapshot(store) == before, "--dry-run mutated an existing store"


@linux_only
def test_dry_run_leaves_decisions_log_byte_identical(env_dir):
    log = env_dir / "logs" / "decisions.log"
    before = _snapshot(log)
    assert _add(env_dir, dry_run=True).returncode == 0
    assert _snapshot(log) == before, "--dry-run wrote an audit row"


# ── audit actor provenance ─────────────────────────────────────────────────

@linux_only
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

@linux_only
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
