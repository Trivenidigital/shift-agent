"""PR-B — retained immutable catering quote-version ledger enforcing tests.

Pins the two BINDING reviewer criteria plus every load-bearing gate:

  crit 1  the version-bearing owner approval card carries "Quote version N"
          (+ a short diff line vs N-1 when N>1) — tested against the finalize
          owner-card renderer AND the shipped template placeholder.
  crit 2  version immutability is enforced BY TEST — an attempted mutation of a
          committed version (a duplicate (lead_id, version) append) fails loudly
          and leaves the store byte-unchanged; the module exposes NO update API.

Also pinned: monotonic per-lead versioning; per-lead independence; deterministic
pure diff (added/removed/total incl. zero-diff + first-version); render-from-
committed reads ONLY ledger+lead (never a transcript); tolerant/preservation-safe
load (corrupt/unexpected/empty untouched, unknown fields round-trip); best-effort
failure (write failure preserves prior store); privacy (no raw quote text in
general logs). POSIX-only cells (mode/symlink/ownership/multi-process contention)
are skipif win32; everything else runs on the Windows dev box via the fcntl stub.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()  # before any safe_io / schemas / catering_quote_ledger import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLATFORM_DIR = SRC / "platform"
for _p in (SRC, PLATFORM_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import catering_quote_ledger as ql  # noqa: E402
from catering_quote_ledger import LedgerResult, QuoteDiff  # noqa: E402

FIXED_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)


def _ids():
    if os.name == "posix":
        import grp
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
    return ("shift-agent", "shift-agent")


OWNER, GROUP = _ids()


def _items(*specs):
    """(name, qty, price) tuples → selected_items dicts."""
    return [{"name": n, "qty": q, "price_usd": p} for (n, q, p) in specs]


def _append(data_path, *, lead_id="L0001", quote_text="quote for 50 guests",
            quote_total_usd=100, selected_items=None, source="customer_finalize",
            source_message_id=None, approval_code=None, now=None,
            expected_owner=OWNER, expected_group=GROUP, **kw):
    if selected_items is None:
        selected_items = _items(("Aloo", 1, 100))
    return ql.append_version(
        lead_id=lead_id, quote_text=quote_text, quote_total_usd=quote_total_usd,
        selected_items=selected_items, source=source,
        source_message_id=source_message_id, approval_code=approval_code,
        created_at=now or FIXED_NOW, data_path=Path(data_path),
        expected_owner=expected_owner, expected_group=expected_group, **kw)


def _store(data_path):
    return json.loads(Path(data_path).read_text(encoding="utf-8"))


def _seed_store(path, content):
    p = Path(path)
    if not isinstance(content, str):
        content = json.dumps(content)
    p.write_text(content, encoding="utf-8")
    if os.name == "posix":
        os.chmod(p, 0o640)
    return p


def _audit_rows(tmp_path):
    log = tmp_path / "audit" / "decisions.log"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ════════════════════════════════════════════════════════════════════════════
# Append + monotonic per-lead versioning
# ════════════════════════════════════════════════════════════════════════════
def test_append_writes_one_record_and_audits(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    r = _append(data)
    assert r == LedgerResult(ok=True, ledger_entry_id="Q0001", version=1, reason=None)
    store = _store(data)
    assert store["schema_version"] == 1 and store["next_seq"] == 2
    rec = store["records"][0]
    assert rec["ledger_entry_id"] == "Q0001" and rec["version"] == 1
    assert rec["lead_id"] == "L0001" and rec["source"] == "customer_finalize"
    assert rec["quote_total_usd"] == 100
    assert [it["name"] for it in rec["selected_items"]] == ["Aloo"]
    rows = _audit_rows(tmp_path)
    assert [x["type"] for x in rows] == ["catering_quote_version_committed"]
    assert rows[0]["version"] == 1 and rows[0]["item_count"] == 1
    assert rows[0]["quote_total_usd"] == 100 and rows[0]["ledger_entry_id"] == "Q0001"


def test_monotonic_versioning_per_lead(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    v1 = _append(data, quote_text="v1")
    v2 = _append(data, quote_text="v2", source="owner_edit")
    v3 = _append(data, quote_text="v3", source="customer_finalize")
    assert (v1.version, v2.version, v3.version) == (1, 2, 3)
    assert [r["ledger_entry_id"] for r in _store(data)["records"]] == ["Q0001", "Q0002", "Q0003"]
    assert [r["version"] for r in _store(data)["records"]] == [1, 2, 3]


def test_versions_are_per_lead_independent(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    _append(data, lead_id="L1")
    _append(data, lead_id="L1", source="owner_edit")
    r = _append(data, lead_id="L2")
    assert r.version == 1, "a second lead starts its own version sequence at 1"
    assert [x["version"] for x in ql.history("L1", data_path=data)] == [1, 2]
    assert [x["version"] for x in ql.history("L2", data_path=data)] == [1]


def test_earlier_versions_unchanged_after_later_append(tmp_path):
    """IMMUTABILITY by construction: appending a later version never rewrites an
    earlier committed one — the v1 record is byte-identical after v2 lands."""
    data = tmp_path / "catering-quote-ledger.json"
    _append(data, quote_text="v1", selected_items=_items(("A", 1, 10)))
    import copy
    v1_before = copy.deepcopy(_store(data)["records"][0])
    _append(data, quote_text="v2", selected_items=_items(("A", 1, 10), ("B", 2, 20)))
    assert _store(data)["records"][0] == v1_before


# ════════════════════════════════════════════════════════════════════════════
# IMMUTABILITY enforcement (reviewer criterion 2)
# ════════════════════════════════════════════════════════════════════════════
def test_duplicate_version_append_fails_loud_and_preserves_file(tmp_path, monkeypatch):
    """A crafted duplicate (lead_id, version) append is REFUSED loudly and the
    store is left byte-unchanged. Forcing the version computer to return an
    already-committed version is the only way to reach a collision — proving the
    scan-and-commit refuses to rewrite a committed version."""
    data = tmp_path / "catering-quote-ledger.json"
    _append(data, quote_text="v1")            # version 1 committed
    before = _store(data)
    monkeypatch.setattr(ql, "_next_version_for_lead", lambda records, lead_id: 1)
    r = _append(data, quote_text="attempted rewrite of v1")
    assert not r.ok and r.reason == "duplicate_version"
    assert _store(data) == before, "a duplicate-version rejection must not mutate the store"
    assert any(x["type"] == "catering_quote_ledger_append_failed"
               and x["reason"] == "duplicate_version" for x in _audit_rows(tmp_path))


def test_module_exposes_no_mutation_api():
    """The public surface is append + reads ONLY — no update/delete/set/rewrite.
    An immutable ledger has no way to change a committed version."""
    public = {n for n in dir(ql) if not n.startswith("_")}
    forbidden = {"update", "delete", "remove", "set", "mutate", "rewrite", "overwrite", "edit"}
    offenders = {n for n in public if any(f in n.lower() for f in forbidden)}
    assert not offenders, f"ledger must expose no mutation API; found {offenders}"
    assert "append_version" in public and "history" in public and "diff" in public


# ════════════════════════════════════════════════════════════════════════════
# Deterministic pure diff
# ════════════════════════════════════════════════════════════════════════════
def _rec(version, total, text, *item_specs):
    return {"version": version, "quote_total_usd": total, "quote_text": text,
            "selected_items": _items(*item_specs)}


def test_diff_added_removed_and_total_delta():
    older = _rec(1, 100, "a", ("A", 1, 100))
    newer = _rec(2, 220, "b", ("A", 1, 100), ("B", 1, 120))
    d = ql.diff_versions(older, newer)
    assert d.from_version == 1 and d.to_version == 2
    assert d.items_added == ("B",) and d.items_removed == ()
    assert d.total_delta_usd == 120 and d.quote_text_changed is True
    assert d.summary_line() == "+1 item, total +$120"


def test_diff_removed_item_and_negative_total():
    older = _rec(2, 300, "same", ("A", 1, 100), ("B", 1, 200))
    newer = _rec(3, 100, "same", ("A", 1, 100))
    d = ql.diff_versions(older, newer)
    assert d.items_removed == ("B",) and d.items_added == ()
    assert d.total_delta_usd == -200 and d.quote_text_changed is False
    assert d.summary_line() == "-1 item, total -$200"


def test_diff_added_and_removed_plural():
    older = _rec(1, 100, "a", ("A", 1, 50), ("B", 1, 50))
    newer = _rec(2, 220, "a", ("C", 1, 100), ("D", 1, 120))
    d = ql.diff_versions(older, newer)
    assert set(d.items_added) == {"C", "D"} and set(d.items_removed) == {"A", "B"}
    assert d.summary_line() == "+2 items, -2 items, total +$120"


def test_diff_zero_diff():
    older = _rec(1, 100, "same", ("A", 1, 100))
    newer = _rec(2, 100, "same", ("A", 1, 100))
    d = ql.diff_versions(older, newer)
    assert d.items_added == () and d.items_removed == () and d.total_delta_usd == 0
    assert d.summary_line() == "total +$0"


def test_diff_first_version_has_no_predecessor_line():
    first = _rec(1, 100, "a", ("A", 1, 100))
    d = ql.diff_versions(None, first)
    assert d.from_version is None and d.to_version == 1
    assert d.summary_line() == "", "first version has no N-1 diff line"


def test_diff_read_path_loads_committed_versions(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    _append(data, quote_text="v1", quote_total_usd=100, selected_items=_items(("A", 1, 100)))
    _append(data, quote_text="v2", quote_total_usd=220, source="owner_edit",
            selected_items=_items(("A", 1, 100), ("B", 1, 120)))
    d = ql.diff("L0001", 1, 2, data_path=data)
    assert d.summary_line() == "+1 item, total +$120"
    # first version via read path: n1=None
    d0 = ql.diff("L0001", None, 1, data_path=data)
    assert d0.summary_line() == ""
    assert ql.diff("L0001", 1, 99, data_path=data) is None, "absent target version → None"


# ════════════════════════════════════════════════════════════════════════════
# Render-from-committed (never reconstructed from transcript)
# ════════════════════════════════════════════════════════════════════════════
def test_render_latest_shows_version_fields(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    _append(data, quote_text="v1", selected_items=_items(("Aloo", 2, 4)))
    _append(data, quote_text="v2", source="owner_edit",
            selected_items=_items(("Aloo", 2, 4), ("Gulab", 5, 3)), quote_total_usd=23)
    lead = {"lead_id": "L0001", "status": "CUSTOMER_FINALIZED",
            "extracted": {"event_date": "2026-06-15", "headcount": 50}}
    out = ql.render_latest_committed(lead=lead, data_path=data)
    assert "version 2" in out and "CUSTOMER_FINALIZED" in out
    assert "2026-06-15" in out and "Headcount: 50" in out
    assert "Aloo x2 @ $4" in out and "Gulab x5 @ $3" in out
    assert "Total: $23" in out


def test_render_none_when_no_committed_version(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    lead = {"lead_id": "L9", "status": "AWAITING_OWNER_APPROVAL", "extracted": {}}
    assert ql.render_latest_committed(lead=lead, data_path=data) is None


def test_render_takes_only_ledger_and_lead_inputs():
    """The renderer signature admits ONLY the lead + the ledger path — there is no
    transcript/message parameter, so it cannot reconstruct from conversation."""
    import inspect
    params = set(inspect.signature(ql.render_latest_committed).parameters)
    assert params <= {"lead", "data_path"}, f"unexpected inputs: {params}"


# ════════════════════════════════════════════════════════════════════════════
# Tolerant load / preservation / best-effort failure
# ════════════════════════════════════════════════════════════════════════════
def test_forward_compat_preserves_unknown_fields_and_records(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    seed = {
        "schema_version": 1, "next_seq": 5,
        "future_top_level_key": {"nested": [1, 2, 3]},
        "records": [{
            "ledger_entry_id": "Q0004", "lead_id": "L9", "version": 1,
            "quote_total_usd": 500, "quote_text": "old", "selected_items": [],
            "source": "customer_finalize", "created_at": "2026-07-18T00:00:00+00:00",
            "future_field": {"deep": [{"k": "v"}]},
        }],
    }
    _seed_store(data, seed)
    import copy
    pre_record = copy.deepcopy(seed["records"][0])
    pre_top = copy.deepcopy(seed["future_top_level_key"])
    r = _append(data, lead_id="L10", quote_text="brand new")
    assert r.ok and r.ledger_entry_id == "Q0005"
    out = _store(data)
    assert out["future_top_level_key"] == pre_top
    assert out["records"][0] == pre_record, "pre-existing record survives untouched"
    assert out["next_seq"] == 6
    assert [r["ledger_entry_id"] for r in out["records"]] == ["Q0004", "Q0005"]


def test_next_seq_derived_when_absent(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    _seed_store(data, {"records": [
        {"ledger_entry_id": "Q0007", "lead_id": "L1", "version": 1,
         "quote_total_usd": 1, "source": "customer_finalize",
         "created_at": "2026-07-18T00:00:00+00:00"},
    ]})
    r = _append(data, lead_id="L2", quote_text="hi")
    assert r.ok and r.ledger_entry_id == "Q0008", "next_seq derives from max existing seq"


def test_expected_owner_group_resolve_from_env(tmp_path, monkeypatch):
    """CROSS-PLATFORM wiring guard for the fs-owner contract. `_validate_fs`'s
    owner/group enforcement is POSIX-only (a no-op off POSIX), so a Windows-only
    dev loop can't observe it — which is exactly how a subprocess script that used
    the default shift-agent owner passed locally but failed on the runner-owned CI
    tmp dir. This pins the env-override WIRING deterministically on every platform:
    the SHIFT_AGENT_CATERING_QUOTE_LEDGER_OWNER/_GROUP env values must reach
    _validate_fs, and an explicit argument must still win over the env."""
    seen: dict = {}

    def _spy(path, owner, group):
        seen["owner"], seen["group"] = owner, group
        return "path_symlink"  # short-circuit before any write; deterministic fail
    monkeypatch.setattr(ql, "_validate_fs", _spy)
    monkeypatch.setenv("SHIFT_AGENT_CATERING_QUOTE_LEDGER_OWNER", "ci-runner")
    monkeypatch.setenv("SHIFT_AGENT_CATERING_QUOTE_LEDGER_GROUP", "ci-group")
    data = tmp_path / "catering-quote-ledger.json"
    r = ql.append_version(lead_id="L1", quote_text="q", quote_total_usd=1,
                          selected_items=[], source="owner_edit", data_path=data)
    assert not r.ok and r.reason == "fs_path_symlink"
    assert seen == {"owner": "ci-runner", "group": "ci-group"}, "env owner/group must reach _validate_fs"

    seen.clear()
    ql.append_version(lead_id="L1", quote_text="q", quote_total_usd=1, selected_items=[],
                      source="owner_edit", data_path=data,
                      expected_owner="explicit-o", expected_group="explicit-g")
    assert seen == {"owner": "explicit-o", "group": "explicit-g"}, "explicit arg wins over env"


def test_corrupt_store_preserved_not_quarantined(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    _seed_store(data, "{ this is not valid json")
    r = _append(data)
    assert not r.ok and r.reason == "corrupt_json"
    assert Path(data).read_text(encoding="utf-8") == "{ this is not valid json"
    assert list(tmp_path.glob("catering-quote-ledger.json.corrupt-*")) == []
    assert [x["type"] for x in _audit_rows(tmp_path)] == ["catering_quote_ledger_append_failed"]


def test_unexpected_shape_preserved(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    _seed_store(data, {"records": "not a list"})
    r = _append(data)
    assert not r.ok and r.reason == "unexpected_shape"
    assert _store(data) == {"records": "not a list"}


def test_empty_store_treated_as_fresh(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    _seed_store(data, "   \n")
    r = _append(data)
    assert r.ok and r.ledger_entry_id == "Q0001" and r.version == 1


def test_missing_lead_id_fails_closed(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    r = _append(data, lead_id="")
    assert not r.ok and r.reason == "no_lead"
    assert not Path(data).exists(), "a no-lead failure must not create the store"


def test_invalid_source_rejected(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    r = _append(data, source="not_a_real_source")
    assert not r.ok and r.reason == "invalid_source"
    assert not Path(data).exists()


def test_write_failure_preserves_prior_store(tmp_path, monkeypatch):
    data = tmp_path / "catering-quote-ledger.json"
    _append(data, quote_text="first")
    before = _store(data)

    def _boom(path, store):
        raise OSError("disk full")
    monkeypatch.setattr(ql, "_atomic_write", _boom)
    r = _append(data, quote_text="second")
    assert not r.ok and r.reason == "write_failed"
    assert _store(data) == before, "a failed write must leave the prior store intact"


def test_record_validation_failure_preserves_store(tmp_path):
    """A record that violates the strict schema (bad approval_code) fails the
    append without mutating the store."""
    data = tmp_path / "catering-quote-ledger.json"
    _append(data, quote_text="first")
    before = _store(data)
    r = _append(data, quote_text="bad", approval_code="not-a-code")
    assert not r.ok and r.reason == "record_validation_failed"
    assert _store(data) == before


# ════════════════════════════════════════════════════════════════════════════
# Privacy — general logs carry ids/version/source/total/count ONLY (no quote text)
# ════════════════════════════════════════════════════════════════════════════
def test_audit_rows_leak_no_quote_text(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    secret = "SECRET custom buffet menu for the Reddy wedding"
    r = _append(data, quote_text=secret)
    assert r.ok
    blob = "\n".join(json.dumps(x) for x in _audit_rows(tmp_path))
    assert secret not in blob, "raw quote text must never enter a general log"
    committed = [x for x in _audit_rows(tmp_path)
                 if x["type"] == "catering_quote_version_committed"][0]
    assert set(committed) <= {"type", "ts", "lead_id", "ledger_entry_id", "version",
                              "source", "quote_total_usd", "item_count"}


# ════════════════════════════════════════════════════════════════════════════
# Restart persistence
# ════════════════════════════════════════════════════════════════════════════
def test_restart_persistence_reappends_from_disk(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    _append(data, quote_text="v1")
    # Simulate a restart: nothing in-memory, next append reloads purely from disk.
    r = _append(data, quote_text="v2", source="owner_edit")
    assert r.version == 2 and len(_store(data)["records"]) == 2


# ════════════════════════════════════════════════════════════════════════════
# Version-bearing owner card (reviewer criterion 1)
# ════════════════════════════════════════════════════════════════════════════
def test_finalize_template_carries_version_placeholder():
    """Static: the shipped finalize owner-card template exposes the version line
    placeholder so the production (template) render path carries the version."""
    tmpl = (REPO / "src" / "agents" / "catering" / "templates"
            / "catering_finalized_menu_to_owner.txt").read_text(encoding="utf-8")
    assert "{quote_version_line}" in tmpl


@pytest.mark.skipif(platform.system() == "Windows",
                    reason="finalize script imports safe_io (fcntl) — Linux-only")
def test_owner_card_renders_version_and_diff_line(tmp_path, monkeypatch):
    """The finalize owner-card renderer carries 'Quote version N' and, for N>1, a
    'Changes since v{N-1}' diff line — reviewer criterion 1. N=1 card omits the
    diff line; no-version card omits the whole banner."""
    import importlib.machinery
    fin = importlib.machinery.SourceFileLoader(
        "finalize_card_test",
        str(REPO / "src" / "agents" / "catering" / "scripts" / "finalize-catering-menu"),
    ).load_module()
    # Exercise the production template path (not just the inline fallback).
    monkeypatch.setattr(fin, "TEMPLATE_DIR",
                        REPO / "src" / "agents" / "catering" / "templates")
    from schemas import CateringLead, CateringSelectedItem
    lead = CateringLead(
        lead_id="L0001", status="CUSTOMER_FINALIZED", customer_phone="+19045550199",
        customer_name="Priya", raw_inquiry="x", original_message_id="m",
        created_at=FIXED_NOW, updated_at=FIXED_NOW,
        quote_text="q", owner_approval_code="#ABCDE",
        selected_items=[CateringSelectedItem(name="Aloo", qty=2, price_usd=4)],
        quote_total_usd=8,
    )
    items = [("Aloo", 2, 4)]

    v2 = fin._render_owner_card(lead, items, 8, False, quote_version=2,
                                quote_diff_line="+1 item, total +$120")
    assert "Quote version 2" in v2
    assert "Changes since v1: +1 item, total +$120" in v2

    v1 = fin._render_owner_card(lead, items, 8, False, quote_version=1)
    assert "Quote version 1" in v1
    assert "Changes since" not in v1, "first version card has no diff line"

    none = fin._render_owner_card(lead, items, 8, False)
    assert "Quote version" not in none, "no committed version → no version banner"


# ════════════════════════════════════════════════════════════════════════════
# Write-site integration — owner-EDIT path appends exactly one version (subprocess)
# ════════════════════════════════════════════════════════════════════════════
def _write_config(path: Path):
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100", "self_chat_jid": ""},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _write_lead(path: Path, code="#ABCDE"):
    lead = {
        "lead_id": "L0007", "status": "AWAITING_OWNER_APPROVAL",
        "customer_phone": "+19045550199", "customer_name": "Priya",
        "raw_inquiry": "catering for 50", "original_message_id": "m1",
        "created_at": "2026-07-10T10:00:00-04:00", "updated_at": "2026-07-10T10:00:00-04:00",
        "extracted": {"headcount": 50, "event_date": "2026-08-15"},
        "quote_text": "proposal text Ref L0007", "quote_version": 0,
        "owner_approval_code": code, "selected_items": [], "quote_total_usd": None,
    }
    path.write_text(json.dumps({"leads": [lead]}), encoding="utf-8")


_APPLY_SCRIPT = REPO / "src" / "agents" / "catering" / "scripts" / "apply-catering-owner-decision"


def _apply_edit_env(tmp_path, ledger, decisions_log):
    """Subprocess env for the real apply-script. Threads the ledger PATH + the
    runner's OWNER/GROUP (the ledger's POSIX fs-owner check defaults to shift-agent,
    which the runner-owned tmp dir would fail) + a tmp decisions log so the ledger's
    committed/append_failed audit rows land assertably."""
    state = ledger.parent
    return {
        **os.environ,
        "SHIFT_AGENT_CONFIG_PATH": str(tmp_path / "config.yaml"),
        "SHIFT_AGENT_LEADS_PATH": str(state / "catering-leads.json"),
        "SHIFT_AGENT_LEADS_LOCK": str(state / "catering-leads.json.lock"),
        "SHIFT_AGENT_LOG_PATH": str(decisions_log),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(decisions_log),
        "SHIFT_AGENT_CATERING_QUOTE_LEDGER_PATH": str(ledger),
        "SHIFT_AGENT_CATERING_QUOTE_LEDGER_OWNER": OWNER,
        "SHIFT_AGENT_CATERING_QUOTE_LEDGER_GROUP": GROUP,
        "PYTHONPATH": f"{PLATFORM_DIR}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    }


def _run_apply_edit(env):
    return subprocess.run(
        [sys.executable, str(_APPLY_SCRIPT), "--code", "#ABCDE", "--decision", "edit",
         "--edit-text", "add appetizer platter; cap at $400", "--sender-role", "owner"],
        env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.skipif(platform.system() == "Windows",
                    reason="apply script imports safe_io (fcntl) — Linux-only")
def test_owner_edit_appends_exactly_one_ledger_version(tmp_path):
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    _write_config(tmp_path / "config.yaml")
    _write_lead(state / "catering-leads.json")
    ledger = state / "catering-quote-ledger.json"
    result = _run_apply_edit(_apply_edit_env(tmp_path, ledger, logs / "decisions.log"))
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert ledger.exists(), "owner edit must append a committed version to the ledger"
    records = json.loads(ledger.read_text(encoding="utf-8"))["records"]
    assert len(records) == 1, f"exactly one version expected, got {len(records)}"
    assert records[0]["version"] == 1 and records[0]["source"] == "owner_edit"
    assert records[0]["approval_code"] == "#ABCDE" and records[0]["lead_id"] == "L0007"


@pytest.mark.skipif(platform.system() == "Windows",
                    reason="apply script imports safe_io (fcntl) — Linux-only")
def test_owner_edit_ledger_failure_does_not_block_lead_write_and_alarms(tmp_path):
    """NEGATIVE / best-effort-with-alarm contract: when the ledger append CANNOT
    persist (here the ledger path is a directory → fs_path_not_regular), the owner
    edit STILL commits the lead (OWNER_EDITED) AND the failure is surfaced — a
    stderr WARN at the write site + a catering_quote_ledger_append_failed audit
    row. The append is never a silent hole."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    _write_config(tmp_path / "config.yaml")
    _write_lead(state / "catering-leads.json")
    ledger = state / "catering-quote-ledger.json"
    ledger.mkdir()  # a directory where the ledger file should be → append cannot write
    decisions_log = logs / "decisions.log"
    result = _run_apply_edit(_apply_edit_env(tmp_path, ledger, decisions_log))

    # Lead write succeeded — the best-effort append never fails the script.
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    lead = json.loads((state / "catering-leads.json").read_text(encoding="utf-8"))["leads"][0]
    assert lead["status"] == "OWNER_EDITED"
    # No committed version (the directory is untouched, no records file written).
    assert not (ledger / "records").exists()
    # Alarm fired both ways: stderr WARN at the write site + append_failed audit row.
    assert "quote-ledger append failed" in result.stderr
    rows = [json.loads(ln) for ln in decisions_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    failed = [r for r in rows if r.get("type") == "catering_quote_ledger_append_failed"]
    assert failed and failed[0]["source"] == "owner_edit"
    assert failed[0]["reason"] == "fs_path_not_regular"


# ════════════════════════════════════════════════════════════════════════════
# POSIX-only — filesystem/ownership/mode contract + multi-process contention
# ════════════════════════════════════════════════════════════════════════════
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode enforcement")
def test_written_file_mode_is_0640(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    assert _append(data).ok
    assert ("%o" % (os.stat(data).st_mode & 0o777)) == "640"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink/ownership/mode contract")
def test_symlink_data_path_rejected_and_preserved(tmp_path):
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps({"schema_version": 1, "next_seq": 1, "records": []}),
                    encoding="utf-8")
    data = tmp_path / "catering-quote-ledger.json"
    os.symlink(real, data)
    r = _append(data)
    assert not r.ok and r.reason == "fs_path_symlink"
    assert os.path.islink(data), "the symlink must be left in place, never replaced"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode enforcement")
def test_out_of_band_mode_tamper_rejected_and_preserved(tmp_path):
    data = tmp_path / "catering-quote-ledger.json"
    _append(data, quote_text="first")
    before = _store(data)
    os.chmod(data, 0o644)  # world-readable — outside {640,660}
    r = _append(data, quote_text="second")
    assert not r.ok and r.reason == "fs_path_bad_mode"
    assert _store(data) == before


_MP_WORKER = '''
import sys, os
from pathlib import Path
from datetime import datetime, timezone
platform_dir, data_path, logp = sys.argv[1:4]
os.environ["SHIFT_AGENT_DECISIONS_LOG_PATH"] = logp
sys.path.insert(0, platform_dir)
import catering_quote_ledger as ql
import pwd, grp
owner = pwd.getpwuid(os.getuid()).pw_name
group = grp.getgrgid(os.getgid()).gr_name
r = ql.append_version(
    lead_id="Lmp", quote_text="concurrent", quote_total_usd=100,
    selected_items=[{"name": "A", "qty": 1, "price_usd": 100}], source="customer_finalize",
    created_at=datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc), data_path=Path(data_path),
    expected_owner=owner, expected_group=group, lock_attempts=60, lock_sleep_sec=0.2)
print(f"{r.ok}|{r.version}")
'''


@pytest.mark.skipif(sys.platform == "win32",
                    reason="multi-process lock contention needs real fcntl.flock")
def test_concurrent_appends_serialize_to_distinct_versions(tmp_path):
    (tmp_path / "state").mkdir()
    data = tmp_path / "state" / "catering-quote-ledger.json"
    logp = str(tmp_path / "audit" / "decisions.log")
    worker = tmp_path / "mp_worker.py"
    worker.write_text(_MP_WORKER, encoding="utf-8")
    procs = [subprocess.Popen([sys.executable, str(worker), str(PLATFORM_DIR), str(data), logp],
                              stdout=subprocess.PIPE, text=True) for _ in range(2)]
    outs = [p.communicate()[0].strip() for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    assert all(o.startswith("True|") for o in outs), outs
    store = json.loads(data.read_text(encoding="utf-8"))
    assert len(store["records"]) == 2, f"concurrency produced {len(store['records'])} records"
    assert sorted(int(o.split("|")[1]) for o in outs) == [1, 2], outs
