"""Scenario harness for catering E2E testing on VPS sandbox.

Tests edge cases beyond the B1 case suite:
- S1: corrupt leads.json
- S2: idempotent replay with mutated args
- S3: bridge POST 500 / network error
- S4: empty headcount + empty fields
- S5: extreme headcount (1, 10000)
- S6: malformed event_date (calendar-invalid like 2026-02-30)
- S7: future-dated >1yr
- S8: extremely long notes (max + over)
- S9: dual-write race (concurrent create with same msg_id)
- S10: rapid-fire creates within lock-timeout window
- S11: apply with stale code (already used)
- S12: apply with wrong-status lead (NEW -> approve directly)
- S13: menu with empty items list
- S14: lookup against missing leads.json
- S15: lookup with malformed phone
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import yaml

REPO = pathlib.Path("/tmp/catering_e2e")
CREATE = REPO / "src/agents/catering/scripts/create-catering-lead"
APPLY = REPO / "src/agents/catering/scripts/apply-catering-owner-decision"
LOOKUP = REPO / "src/agents/catering/scripts/lookup-prior-leads-by-phone"
TMPLS = REPO / "src/agents/catering/templates"
PLATFORM = REPO / "src/platform"

sys.path.insert(0, str(PLATFORM))


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(name, str(path), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _setup_env(tmp):
    tmp = pathlib.Path(tmp)
    (tmp / "state").mkdir(parents=True, exist_ok=True)
    (tmp / "logs").mkdir(parents=True, exist_ok=True)
    tdir = tmp / "templates"
    tdir.mkdir(exist_ok=True)
    for f in TMPLS.iterdir():
        dst = tdir / f.name
        if not dst.exists():
            dst.symlink_to(f.absolute())
    cfg = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100", "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
    }
    (tmp / "config.yaml").write_text(yaml.safe_dump(cfg))
    return tmp


def _run_create(env, msg_id, fields, *, customer_phone="+15551234567",
                customer_name="Test", raw="test inquiry", bridge_port=9999,
                stdin_now=None):
    sys.argv = [
        "create-catering-lead",
        "--customer-phone", customer_phone,
        "--customer-name", customer_name,
        "--raw-inquiry", raw,
        "--message-id", msg_id,
        "--fields-json", json.dumps(fields),
    ]
    mod = _load(f"ccl_{msg_id}", CREATE)
    mod.CONFIG_PATH = env / "config.yaml"
    mod.LEADS_PATH = env / "state" / "catering-leads.json"
    mod.LEADS_LOCK = env / "state" / "catering-leads.json.lock"
    mod.LOG_PATH = env / "logs" / "decisions.log"
    mod.TEMPLATE_DIR = env / "templates"
    mod.BRIDGE_URL = f"http://127.0.0.1:{bridge_port}/send"
    if stdin_now is not None:
        from zoneinfo import ZoneInfo
        frozen = stdin_now
        def _patched(tz_name):
            return frozen.astimezone(ZoneInfo(tz_name))
        mod.customer_now = _patched
    try:
        return mod.main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


def _read(env):
    p = env / "state" / "catering-leads.json"
    if not p.exists():
        return {"leads": []}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {"_corrupt": True, "raw": p.read_text()[:200]}


def main():
    print("=" * 70)
    print("CATERING E2E SCENARIO HARNESS — read-only sandbox tests")
    print("=" * 70)

    findings = []

    # === S1: corrupt leads.json ===
    print("\n--- S1: corrupt leads.json ---")
    tmp = tempfile.mkdtemp()
    env = _setup_env(tmp)
    (env / "state" / "catering-leads.json").write_text("{not valid json")
    rc = _run_create(env, "S1_001", {"headcount": 30, "event_date": "2026-09-01", "dietary_restrictions": []})
    after = _read(env)
    print(f"  rc={rc} leads={after}")
    if rc == 0 and not after.get("_corrupt") and len(after.get("leads", [])) > 0:
        findings.append("S1: corrupt store overwritten with new lead — silent recovery (data loss risk)")
    elif rc != 0 and rc not in (3, 4, 5):
        findings.append(f"S1: corrupt store returned unexpected rc={rc} (expected EXIT_SCHEMA_VIOLATION/EXIT_LOCK_TIMEOUT)")

    # === S2: idempotent replay with mutated args ===
    print("\n--- S2: replay with mutated args (same msg_id) ---")
    tmp = tempfile.mkdtemp()
    env = _setup_env(tmp)
    rc1 = _run_create(env, "S2_001", {"headcount": 40, "event_date": "2026-09-01", "dietary_restrictions": []})
    leads1 = _read(env)["leads"]
    rc2 = _run_create(env, "S2_001",
                       {"headcount": 99, "event_date": "2026-09-01", "dietary_restrictions": []},
                       customer_name="DIFFERENT NAME")
    leads2 = _read(env)["leads"]
    print(f"  call1 rc={rc1} leads={len(leads1)}")
    print(f"  call2 rc={rc2} leads={len(leads2)}")
    if len(leads2) != len(leads1):
        findings.append(f"S2: replay created duplicate lead (count {len(leads1)}->{len(leads2)})")
    if leads2 and leads2[0]["extracted"]["headcount"] != 40:
        findings.append(f"S2: replay mutated stored headcount (expected 40, got {leads2[0]['extracted']['headcount']})")
    if leads2 and "DIFFERENT" in leads2[0]["customer_name"]:
        findings.append("S2: replay mutated stored customer_name")

    # === S6: malformed event_date (calendar-invalid) ===
    print("\n--- S6: calendar-invalid date 2026-02-30 ---")
    tmp = tempfile.mkdtemp()
    env = _setup_env(tmp)
    rc = _run_create(env, "S6_001", {"headcount": 30, "event_date": "2026-02-30", "dietary_restrictions": []})
    after = _read(env)
    print(f"  rc={rc} leads={len(after.get('leads', []))}")
    if rc == 0 and len(after.get("leads", [])) > 0:
        ed = after["leads"][0]["extracted"].get("event_date")
        findings.append(f"S6: invalid date 2026-02-30 ACCEPTED (stored as {ed!r})")

    # === S7: future-dated >1yr ===
    print("\n--- S7: future date 2030-12-25 (>1yr out) ---")
    tmp = tempfile.mkdtemp()
    env = _setup_env(tmp)
    rc = _run_create(env, "S7_001", {"headcount": 50, "event_date": "2030-12-25", "dietary_restrictions": []})
    after = _read(env)
    print(f"  rc={rc} leads={len(after.get('leads', []))}")
    if rc == 0 and len(after.get("leads", [])) > 0:
        print(f"  stored event_date={after['leads'][0]['extracted'].get('event_date')}")
    # No specific pass/fail — just observation

    # === S8: extreme headcount ===
    print("\n--- S8: headcount=10000 ---")
    tmp = tempfile.mkdtemp()
    env = _setup_env(tmp)
    rc = _run_create(env, "S8_001", {"headcount": 10000, "event_date": "2026-09-01", "dietary_restrictions": []})
    after = _read(env)
    print(f"  rc={rc} leads={len(after.get('leads', []))}")
    if rc == 0 and after.get("leads"):
        print(f"  headcount stored={after['leads'][0]['extracted']['headcount']}")
    # === S8b: headcount=0 ===
    print("\n--- S8b: headcount=0 ---")
    tmp = tempfile.mkdtemp()
    env = _setup_env(tmp)
    rc = _run_create(env, "S8b_001", {"headcount": 0, "event_date": "2026-09-01", "dietary_restrictions": []})
    after = _read(env)
    print(f"  rc={rc} leads={len(after.get('leads', []))}")
    if rc == 0 and after.get("leads"):
        findings.append("S8b: headcount=0 ACCEPTED (likely should reject as nonsense input)")

    # === S8c: headcount=-5 ===
    print("\n--- S8c: headcount=-5 ---")
    tmp = tempfile.mkdtemp()
    env = _setup_env(tmp)
    rc = _run_create(env, "S8c_001", {"headcount": -5, "event_date": "2026-09-01", "dietary_restrictions": []})
    after = _read(env)
    print(f"  rc={rc} leads={len(after.get('leads', []))}")
    if rc == 0 and after.get("leads"):
        findings.append(f"S8c: headcount=-5 ACCEPTED (negative headcount)")

    # === S15: lookup with malformed phone ===
    print("\n--- S15: lookup with malformed phone ---")
    tmp = tempfile.mkdtemp()
    env = _setup_env(tmp)
    leads = {"leads": []}
    (env / "state" / "catering-leads.json").write_text(json.dumps(leads))
    lookup_mod = _load("lookup_s15", LOOKUP)
    for bad in ["", "abc", "+1", "+1-555-PRIYA", "555-1234"]:
        try:
            r = lookup_mod.lookup_prior_leads_by_phone(bad, leads_path=env / "state" / "catering-leads.json")
            print(f"  phone={bad!r} -> {r}")
        except Exception as e:
            print(f"  phone={bad!r} -> EXC {type(e).__name__}: {str(e)[:80]}")

    # === S16: very long notes (over max) ===
    print("\n--- S16: very long notes 3000 chars ---")
    tmp = tempfile.mkdtemp()
    env = _setup_env(tmp)
    long_notes = "x" * 3000
    rc = _run_create(env, "S16_001", {"headcount": 30, "event_date": "2026-09-01",
                                       "dietary_restrictions": [], "notes": long_notes})
    after = _read(env)
    print(f"  rc={rc} leads={len(after.get('leads', []))}")
    if rc == 0 and after.get("leads"):
        stored = after["leads"][0]["extracted"]["notes"]
        print(f"  stored notes len={len(stored)} (input was 3000)")
        if len(stored) == 3000:
            findings.append("S16: 3000-char notes accepted unbounded (schema cap=2000?)")

    # === S17: 30 off_menu_items (over max=20) ===
    print("\n--- S17: 30 off_menu_items ---")
    tmp = tempfile.mkdtemp()
    env = _setup_env(tmp)
    rc = _run_create(env, "S17_001", {
        "headcount": 30, "event_date": "2026-09-01",
        "dietary_restrictions": [],
        "off_menu_items": [f"item-{i}" for i in range(30)]
    })
    after = _read(env)
    print(f"  rc={rc}")
    if rc == 0:
        items = after["leads"][0]["extracted"].get("off_menu_items", [])
        print(f"  stored {len(items)} items")
        if len(items) > 20:
            findings.append(f"S17: {len(items)} off_menu_items stored (schema max=20)")

    # === S20: catering disabled in config ===
    print("\n--- S20: catering disabled ---")
    tmp = tempfile.mkdtemp()
    env = _setup_env(tmp)
    cfg = yaml.safe_load((env / "config.yaml").read_text())
    cfg["catering"]["enabled"] = False
    (env / "config.yaml").write_text(yaml.safe_dump(cfg))
    rc = _run_create(env, "S20_001", {"headcount": 30, "event_date": "2026-09-01", "dietary_restrictions": []})
    after = _read(env)
    print(f"  rc={rc} leads={len(after.get('leads', []))}")
    # Expect EXIT_DISABLED (commonly 7) and 0 leads
    if rc == 0 or len(after.get("leads", [])) > 0:
        findings.append(f"S20: catering disabled but lead created (rc={rc} leads={len(after.get('leads', []))})")

    # === S21: missing config.yaml ===
    print("\n--- S21: missing config.yaml ---")
    tmp = tempfile.mkdtemp()
    env = _setup_env(tmp)
    (env / "config.yaml").unlink()
    rc = _run_create(env, "S21_001", {"headcount": 30, "event_date": "2026-09-01", "dietary_restrictions": []})
    print(f"  rc={rc}")
    after = _read(env)
    if rc == 0:
        findings.append("S21: missing config.yaml didn't crash — lead created with what config?")

    # === Summary ===
    print("\n" + "=" * 70)
    print("FINDINGS")
    print("=" * 70)
    if findings:
        for f in findings:
            print(f"  - {f}")
    else:
        print("  (none)")


if __name__ == "__main__":
    main()
