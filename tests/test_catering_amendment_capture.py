"""PR-R2A — durable Branch-B amendment capture (sidecar store) enforcing tests.

Closes the F7-primary-mode KNOWN GAP: a customer amendment against an active lead
was suppressed WITHOUT persisting the text. This suite pins the four outcomes the
reviewer taxonomy names, plus every load-bearing gate:

  captured        one new record durably committed → canonical reply
  replay          existing record found, none written → UNCHANGED canonical reply
  capture_failed  deterministic retry response, no routing continuation, store preserved
  not_applicable  anything outside the exact Branch-B amendment arm → behavior unchanged

Gates pinned here: single critical section (load+3 idempotency tiers+append+write under
ONE lock); lock released before any send (hooks-level); replay ≠ capture_failed; explicit
half-open 24h boundary (24h-1s dedup / 24h exactly new); corrupt/unsafe stores untouched
(NOT quarantine-renamed); semantic preservation across append (deep-equality); atomic
persistence never silently changes owner/mode (post-write assert-and-fail); privacy (no
raw text / full-text hash / phone / transport payload in general logs).

Windows-runnable cells use the fcntl no-op stub (single-process, advisory locking is
irrelevant to correctness). POSIX-only cells (real multi-process contention, symlink /
ownership / mode enforcement) are skipif win32.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import importlib.machinery
import importlib.util
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()  # before any safe_io / schemas / catering_amendments import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLUGIN_DIR = SRC / "plugins" / "cf-router"
for _p in (SRC, SRC / "platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import catering_amendments as ca  # noqa: E402
from catering_amendments import CaptureResult  # noqa: E402

FIXED_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)


def _ids():
    """Expected owner/group for filesystem checks. On POSIX the pytest tmp dir +
    the atomically-written file are both created by THIS process, so the running
    uid/gid match both the parent and the new file. On Windows the permission-bit
    checks are skipped, so the value is irrelevant."""
    if os.name == "posix":
        import grp
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
    return ("shift-agent", "shift-agent")


OWNER, GROUP = _ids()


def _cap(data_path, *, lead=None, text="please make it 280 guests not 235",
         chat_id="cust@lid", phone="+15551230000", message_id="wamid.DEFAULT",
         source_transport="whatsapp", provider_timestamp="1721390400", now=None,
         expected_owner=OWNER, expected_group=GROUP, **kw):
    if lead is None:
        lead = {"lead_id": "L0001", "extracted": {"guests": 235}}
    return ca.capture_branch_b_amendment(
        lead=lead, text=text, chat_id=chat_id, phone=phone, message_id=message_id,
        source_transport=source_transport, provider_timestamp=provider_timestamp,
        now=now or FIXED_NOW, data_path=Path(data_path),
        expected_owner=expected_owner, expected_group=expected_group, **kw)


def _seed_store(path, content):
    """Write a raw store fixture, then on POSIX chmod it to 0640.

    The filesystem contract (correctly) rejects any store whose mode is not in
    {640,660} BEFORE it reads content — and `write_text` under the default umask
    produces 0644. In production the file is only ever written by
    `atomic_write_json(mode=0o640)`, so 0644 never occurs; a fixture that seeds
    0644 is testing an unreachable state. chmod'ing to 0640 makes these cells
    exercise the LOAD path (corrupt / unexpected-shape / empty / seq-derivation)
    they intend to, on POSIX CI as well as Windows (where mode is inert). The
    dedicated mode-tamper cell seeds 0644 explicitly to assert rejection."""
    p = Path(path)
    if not isinstance(content, str):
        content = json.dumps(content)
    p.write_text(content, encoding="utf-8")
    if os.name == "posix":
        os.chmod(p, 0o640)
    return p


def _store(data_path):
    return json.loads(Path(data_path).read_text(encoding="utf-8"))


def _audit_rows(tmp_path):
    log = tmp_path / "audit" / "decisions.log"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ════════════════════════════════════════════════════════════════════════════
# OUTCOME: captured
# ════════════════════════════════════════════════════════════════════════════
def test_captured_writes_one_record_and_audits(tmp_path):
    data = tmp_path / "catering-amendments.json"
    r = _cap(data, message_id="wamid.A")
    assert r == CaptureResult(ok=True, amendment_id="A0001", reason=None, idempotent=False)
    store = _store(data)
    assert store["schema_version"] == 1 and store["next_seq"] == 2
    assert [rec["amendment_id"] for rec in store["records"]] == ["A0001"]
    rec = store["records"][0]
    assert rec["lead_id"] == "L0001" and rec["source"] == "f7_branch_b"
    assert rec["status"] == "captured" and rec["source_transport"] == "whatsapp"
    assert rec["message_id"] == "wamid.A" and rec["raw_text"] == "please make it 280 guests not 235"
    assert rec["raw_text_truncated"] is False
    assert rec["raw_text_original_length"] == len("please make it 280 guests not 235")
    assert len(rec["raw_text_sha256"]) == 64 and len(rec["base_extracted_sha256"]) == 64
    rows = _audit_rows(tmp_path)
    assert [x["type"] for x in rows] == ["catering_amendment_captured"]
    assert rows[0]["amendment_id"] == "A0001" and rows[0]["text_len"] == rec["raw_text_original_length"]


def test_base_extracted_sha_tracks_lead_state(tmp_path):
    a = _cap(tmp_path / "a.json", lead={"lead_id": "L1", "extracted": {"g": 100}})
    b = _cap(tmp_path / "b.json", lead={"lead_id": "L1", "extracted": {"g": 200}})
    assert a.ok and b.ok
    sa = _store(tmp_path / "a.json")["records"][0]["base_extracted_sha256"]
    sb = _store(tmp_path / "b.json")["records"][0]["base_extracted_sha256"]
    assert sa != sb, "base_extracted_sha256 must reflect the lead's extracted state"


# ════════════════════════════════════════════════════════════════════════════
# OUTCOME: replay (idempotent; NO new record; distinct from capture_failed)
# ════════════════════════════════════════════════════════════════════════════
def test_replay_primary_native_id_any_status(tmp_path):
    data = tmp_path / "catering-amendments.json"
    first = _cap(data, message_id="wamid.SAME")
    # Same (source_transport, lead_id, native_message_id) — even different text.
    again = _cap(data, message_id="wamid.SAME", text="totally different words")
    assert first.ok and not first.idempotent
    assert again == CaptureResult(ok=True, amendment_id="A0001", reason=None, idempotent=True)
    assert len(_store(data)["records"]) == 1, "replay must NOT append a second record"
    # replay emits NO amendment_captured audit row (nothing new was captured)
    assert [x["type"] for x in _audit_rows(tmp_path)] == ["catering_amendment_captured"]


def test_replay_envelope_fingerprint_when_native_id_absent(tmp_path):
    data = tmp_path / "catering-amendments.json"
    first = _cap(data, message_id="", provider_timestamp="1721390400", text="switch to veg")
    again = _cap(data, message_id="", provider_timestamp="1721390400", text="switch to veg")
    assert first.ok and not first.idempotent
    assert again.ok and again.idempotent and again.amendment_id == "A0001"
    assert len(_store(data)["records"]) == 1


def test_replay_text_window_fallback_across_statuses(tmp_path):
    data = tmp_path / "catering-amendments.json"
    # First capture, then mutate the stored record's status to a terminal-ish value:
    # the tier-3 fallback must still dedup identical text within 24h REGARDLESS of status.
    _cap(data, message_id="", provider_timestamp="", text="move date to July 19")
    doc = _store(data)
    doc["records"][0]["status"] = "some_future_terminal_status"
    _seed_store(data, doc)
    again = _cap(data, message_id="", provider_timestamp="",
                 text="move date to July 19", now=FIXED_NOW + timedelta(hours=5))
    assert again.ok and again.idempotent and again.amendment_id == "A0001"
    assert len(_store(data)["records"]) == 1


# ════════════════════════════════════════════════════════════════════════════
# 24h boundary — EXPLICIT half-open window (documented in-module)
# ════════════════════════════════════════════════════════════════════════════
def test_boundary_24h_minus_1s_dedups(tmp_path):
    data = tmp_path / "catering-amendments.json"
    t0 = FIXED_NOW
    _cap(data, message_id="", provider_timestamp="", text="same text", now=t0)
    later = _cap(data, message_id="", provider_timestamp="", text="same text",
                 now=t0 + timedelta(hours=24) - timedelta(seconds=1))
    assert later.ok and later.idempotent, "24h-1s is INSIDE the window → replay"
    assert len(_store(data)["records"]) == 1


def test_boundary_24h_exactly_is_new_record(tmp_path):
    data = tmp_path / "catering-amendments.json"
    t0 = FIXED_NOW
    _cap(data, message_id="", provider_timestamp="", text="same text", now=t0)
    later = _cap(data, message_id="", provider_timestamp="", text="same text",
                 now=t0 + timedelta(hours=24))
    assert later.ok and not later.idempotent, "exactly 24h is OUTSIDE the window → new record"
    assert [r["amendment_id"] for r in _store(data)["records"]] == ["A0001", "A0002"]


# ════════════════════════════════════════════════════════════════════════════
# No FALSE dedup across leads / senders
# ════════════════════════════════════════════════════════════════════════════
def test_identical_text_different_leads_no_false_dedup(tmp_path):
    data = tmp_path / "catering-amendments.json"
    _cap(data, message_id="", provider_timestamp="", text="need 300 plates",
         lead={"lead_id": "L1", "extracted": {}})
    r2 = _cap(data, message_id="", provider_timestamp="", text="need 300 plates",
              lead={"lead_id": "L2", "extracted": {}})
    assert r2.ok and not r2.idempotent
    assert len(_store(data)["records"]) == 2


def test_identical_text_different_senders_no_false_dedup(tmp_path):
    data = tmp_path / "catering-amendments.json"
    # Distinct senders = distinct chat_id/phone (the canonical sender ref keys on
    # the conversation identity, so same-chat + different-phone is the SAME sender).
    _cap(data, message_id="", provider_timestamp="", text="need 300 plates",
         chat_id="alice@lid", phone="+1111")
    r2 = _cap(data, message_id="", provider_timestamp="", text="need 300 plates",
              chat_id="bob@lid", phone="+2222")
    assert r2.ok and not r2.idempotent
    assert len(_store(data)["records"]) == 2


# ════════════════════════════════════════════════════════════════════════════
# Raw text: full-text hash BEFORE truncation, bounded stored prefix
# ════════════════════════════════════════════════════════════════════════════
def test_truncation_hashes_complete_text_stores_bounded_prefix(tmp_path):
    import hashlib
    data = tmp_path / "catering-amendments.json"
    big = "x" * (ca.RAW_TEXT_MAX + 500)
    r = _cap(data, text=big, message_id="wamid.big")
    assert r.ok
    rec = _store(data)["records"][0]
    assert rec["raw_text_truncated"] is True
    assert rec["raw_text_original_length"] == ca.RAW_TEXT_MAX + 500
    assert len(rec["raw_text"]) == ca.RAW_TEXT_MAX
    # sha is over the COMPLETE text, not the stored prefix
    assert rec["raw_text_sha256"] == hashlib.sha256(big.encode("utf-8")).hexdigest()
    assert rec["raw_text_sha256"] != hashlib.sha256(rec["raw_text"].encode("utf-8")).hexdigest()


# ════════════════════════════════════════════════════════════════════════════
# Semantic preservation across append (forward-compat) — deep equality
# ════════════════════════════════════════════════════════════════════════════
def test_forward_compat_preserves_unknown_fields_and_records(tmp_path):
    data = tmp_path / "catering-amendments.json"
    seed = {
        "schema_version": 1,
        "next_seq": 5,
        "future_top_level_key": {"nested": [1, 2, 3]},
        "records": [{
            "amendment_id": "A0004",
            "lead_id": "L9",
            "sender_ref": "+1999",
            "raw_text_sha256": "d" * 64,
            "captured_at": "2026-07-18T00:00:00+00:00",
            "status": "r2b_future_disposition",
            "r2b_only_field": {"deep": {"list": [{"k": "v"}]}},
            "message_id": "wamid.OLD",
            "envelope_fingerprint": "fp-old",
        }],
    }
    _seed_store(data, seed)
    import copy
    pre_record = copy.deepcopy(seed["records"][0])
    pre_top = copy.deepcopy(seed["future_top_level_key"])

    r = _cap(data, lead={"lead_id": "L10", "extracted": {}}, message_id="wamid.NEW", text="brand new")
    assert r.ok and r.amendment_id == "A0005"
    out = _store(data)
    # unknown top-level key survives byte-for-byte (semantically)
    assert out["future_top_level_key"] == pre_top
    # the pre-existing record survives untouched (no strict-schema normalization)
    assert out["records"][0] == pre_record
    assert out["next_seq"] == 6
    assert [r["amendment_id"] for r in out["records"]] == ["A0004", "A0005"]


def test_next_seq_derived_when_absent(tmp_path):
    data = tmp_path / "catering-amendments.json"
    _seed_store(data, {"records": [
        {"amendment_id": "A0007", "lead_id": "L1", "sender_ref": "s",
         "raw_text_sha256": "a" * 64, "captured_at": "2026-07-18T00:00:00+00:00"},
    ]})
    r = _cap(data, lead={"lead_id": "L2", "extracted": {}}, message_id="wamid.X", text="hi")
    assert r.ok and r.amendment_id == "A0008", "next_seq must derive from max existing seq"


# ════════════════════════════════════════════════════════════════════════════
# Restart persistence
# ════════════════════════════════════════════════════════════════════════════
def test_restart_persistence_replays_after_reload(tmp_path):
    data = tmp_path / "catering-amendments.json"
    first = _cap(data, message_id="wamid.persist")
    # Simulate a process restart: nothing in-memory, reload purely from disk.
    again = _cap(data, message_id="wamid.persist")
    assert first.ok and again.idempotent and again.amendment_id == first.amendment_id
    assert len(_store(data)["records"]) == 1


# ════════════════════════════════════════════════════════════════════════════
# OUTCOME: capture_failed — store PRESERVED (never renamed/repaired), retry signalled
# ════════════════════════════════════════════════════════════════════════════
def test_corrupt_store_preserved_not_quarantined(tmp_path):
    data = tmp_path / "catering-amendments.json"
    _seed_store(data, "{ this is not valid json")
    r = _cap(data, message_id="wamid.x")
    assert not r.ok and r.reason == "corrupt_json"
    # file content UNCHANGED, and NO .corrupt-* sibling created (unlike safe_load_json)
    assert Path(data).read_text(encoding="utf-8") == "{ this is not valid json"
    assert list(tmp_path.glob("catering-amendments.json.corrupt-*")) == []
    assert [x["type"] for x in _audit_rows(tmp_path)] == ["catering_amendment_capture_failed"]


def test_unexpected_shape_preserved(tmp_path):
    data = tmp_path / "catering-amendments.json"
    _seed_store(data, {"records": "not a list"})
    r = _cap(data, message_id="wamid.x")
    assert not r.ok and r.reason == "unexpected_shape"
    assert _store(data) == {"records": "not a list"}


def test_empty_store_treated_as_fresh(tmp_path):
    data = tmp_path / "catering-amendments.json"
    _seed_store(data, "   \n")
    r = _cap(data, message_id="wamid.x")
    assert r.ok and r.amendment_id == "A0001"


def test_missing_lead_id_fails_closed(tmp_path):
    data = tmp_path / "catering-amendments.json"
    r = _cap(data, lead={"extracted": {}}, message_id="wamid.x")
    assert not r.ok and r.reason == "no_lead"
    assert not Path(data).exists(), "a no-lead failure must not create the store"


def test_write_failure_preserves_prior_store(tmp_path, monkeypatch):
    data = tmp_path / "catering-amendments.json"
    _cap(data, message_id="wamid.first")  # one good record on disk
    before = _store(data)

    def _boom(path, store):
        raise OSError("disk full")
    monkeypatch.setattr(ca, "_atomic_write", _boom)
    r = _cap(data, message_id="wamid.second", text="new one")
    assert not r.ok and r.reason == "write_failed"
    assert _store(data) == before, "a failed write must leave the prior store intact"


def test_capture_failed_audit_carries_reason_only(tmp_path):
    data = tmp_path / "catering-amendments.json"
    _seed_store(data, "{bad")
    _cap(data, message_id="wamid.x")
    row = [x for x in _audit_rows(tmp_path) if x["type"] == "catering_amendment_capture_failed"][0]
    assert set(row) <= {"type", "ts", "lead_id", "reason"}
    assert row["reason"] == "corrupt_json"


# ════════════════════════════════════════════════════════════════════════════
# Privacy — general logs carry ids/reason/text_len ONLY (no raw text/hash/phone)
# ════════════════════════════════════════════════════════════════════════════
def test_audit_rows_leak_no_raw_text_hash_or_phone(tmp_path):
    data = tmp_path / "catering-amendments.json"
    secret = "SECRET amendment 280 guests vegetarian"
    r = _cap(data, text=secret, phone="+15559998888", message_id="wamid.p")
    assert r.ok
    rec = _store(data)["records"][0]
    blob = "\n".join(json.dumps(x) for x in _audit_rows(tmp_path))
    assert secret not in blob, "raw amendment text must never enter a general log"
    assert "+15559998888" not in blob, "phone must never enter a general log"
    assert rec["raw_text_sha256"] not in blob, "full-text hash must never enter a general log"
    captured = [x for x in _audit_rows(tmp_path) if x["type"] == "catering_amendment_captured"][0]
    assert set(captured) <= {"type", "ts", "lead_id", "amendment_id", "message_id", "source", "text_len"}


# ════════════════════════════════════════════════════════════════════════════
# OUTCOME routing (hooks Branch-B arm): captured/replay/capture_failed/not_applicable
# ════════════════════════════════════════════════════════════════════════════
def _load_plugin():
    pkg = "cf_router_r2a_pkg"
    for m in list(sys.modules):
        if m == pkg or m.startswith(pkg + "."):
            del sys.modules[m]
    spec = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
    spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg] = importlib.util.module_from_spec(spec)

    def _load(name):
        full = f"{pkg}.{name}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{name}.py"))
        sp = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(sp)
        sys.modules[full] = mod
        loader.exec_module(mod)
        return mod

    actions_mod = _load("actions")
    hooks_mod = _load("hooks")
    return hooks_mod, actions_mod


class _Spies:
    def __init__(self):
        self.canonical = []
        self.retry = []
        self.audits = []


def _wire_branch_b(hooks_mod, actions_mod, monkeypatch, capture_result):
    spies = _Spies()
    lead = {"lead_id": "L0001", "status": "AWAITING_OWNER_APPROVAL",
            "owner_approval_code": "#ABCDE", "extracted": {}}
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender",
                        lambda chat_id: ("+15551230000", "customer"))
    monkeypatch.setattr(actions_mod, "find_active_catering_lead_by_sender",
                        lambda phone, chat_id: lead)
    monkeypatch.setattr(actions_mod, "is_proposal_selection", lambda text: False)
    monkeypatch.setattr(actions_mod, "is_proposal_request", lambda text: False)
    monkeypatch.setattr(hooks_mod, "_should_start_new_lead_over_active",
                        lambda active, signals: False)
    monkeypatch.setattr(hooks_mod.catering_amendments, "capture_branch_b_amendment",
                        lambda **kw: capture_result)
    monkeypatch.setattr(actions_mod, "send_canonical_followup_reply",
                        lambda chat_id, lead_id: spies.canonical.append((chat_id, lead_id)))
    monkeypatch.setattr(hooks_mod, "_send_amendment_retry_reply",
                        lambda chat_id, lead_id: spies.retry.append((chat_id, lead_id)))
    monkeypatch.setattr(actions_mod, "audit_intercepted",
                        lambda **kw: spies.audits.append(kw))
    return spies


def _event():
    return SimpleNamespace(message_id="wamid.evt", timestamp="1721390400", transport="whatsapp")


def test_hooks_captured_sends_canonical_reply_and_skips(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    spies = _wire_branch_b(hooks_mod, actions_mod, monkeypatch,
                           CaptureResult(ok=True, amendment_id="A0001", idempotent=False))
    out = hooks_mod._try_f7_primary_intercept("make it 280", "cust@lid", _event(),
                                              signals=[], allow_new_lead=True)
    assert out["action"] == "skip" and "suppressed" in out["reason"]
    assert spies.canonical == [("cust@lid", "L0001")], "captured → UNCHANGED canonical reply"
    assert spies.retry == [], "captured must NOT send the retry reply"
    assert spies.audits[-1]["reason"] == "f7_primary_followup_suppressed"


def test_hooks_replay_sends_same_canonical_reply_and_skips(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    spies = _wire_branch_b(hooks_mod, actions_mod, monkeypatch,
                           CaptureResult(ok=True, amendment_id="A0001", idempotent=True))
    out = hooks_mod._try_f7_primary_intercept("make it 280", "cust@lid", _event(),
                                              signals=[], allow_new_lead=True)
    assert out["action"] == "skip"
    assert spies.canonical == [("cust@lid", "L0001")], "replay → SAME canonical reply, not retry"
    assert spies.retry == []
    assert spies.audits[-1]["reason"] == "f7_primary_followup_suppressed"


def test_hooks_capture_failed_sends_retry_not_canonical_and_skips(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    spies = _wire_branch_b(hooks_mod, actions_mod, monkeypatch,
                           CaptureResult(ok=False, reason="lock_unavailable"))
    out = hooks_mod._try_f7_primary_intercept("make it 280", "cust@lid", _event(),
                                              signals=[], allow_new_lead=True)
    assert out["action"] == "skip", "capture_failed still suppresses the LLM (no routing continuation)"
    assert "capture failed" in out["reason"]
    assert spies.retry == [("cust@lid", "L0001")], "capture_failed → deterministic retry reply"
    assert spies.canonical == [], "capture_failed must NOT send the canonical (implies-recorded) reply"
    assert spies.audits[-1]["reason"] == "f7_primary_amendment_capture_failed"


def test_hooks_not_applicable_owner_never_captures(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    called = []
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender",
                        lambda chat_id: ("+15550000000", "owner"))
    monkeypatch.setattr(hooks_mod.catering_amendments, "capture_branch_b_amendment",
                        lambda **kw: called.append(kw) or CaptureResult(ok=True, amendment_id="X"))
    out = hooks_mod._try_f7_primary_intercept("#ABCDE approve", "owner@c.us", _event(),
                                              signals=[], allow_new_lead=True)
    assert out is None, "owner path is F8 territory → LLM/F8, not amendment capture"
    assert called == [], "amendment capture must NOT run for the owner path"


def test_hooks_not_applicable_new_lead_branch_never_captures(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    called = []
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender",
                        lambda chat_id: ("+15551230000", "customer"))
    monkeypatch.setattr(actions_mod, "find_active_catering_lead_by_sender",
                        lambda phone, chat_id: None)  # Branch A — no active lead
    monkeypatch.setattr(hooks_mod, "_create_catering_lead_from_inbound",
                        lambda **kw: {"action": "skip", "reason": "new lead created"})
    monkeypatch.setattr(hooks_mod.catering_amendments, "capture_branch_b_amendment",
                        lambda **kw: called.append(kw) or CaptureResult(ok=True, amendment_id="X"))
    out = hooks_mod._try_f7_primary_intercept("we want catering for 200", "cust@lid", _event(),
                                              signals=[], allow_new_lead=True)
    assert out == {"action": "skip", "reason": "new lead created"}
    assert called == [], "Branch A (new lead) must NOT run amendment capture"


# ════════════════════════════════════════════════════════════════════════════
# POSIX-only — real multi-process contention + filesystem/ownership/mode contract
# ════════════════════════════════════════════════════════════════════════════
_MP_WORKER = '''
import sys, os
from pathlib import Path
from datetime import datetime, timezone
platform_dir, data_path, logp = sys.argv[1:4]
os.environ["SHIFT_AGENT_DECISIONS_LOG_PATH"] = logp
sys.path.insert(0, platform_dir)
import catering_amendments as ca
import pwd, grp
owner = pwd.getpwuid(os.getuid()).pw_name
group = grp.getgrgid(os.getgid()).gr_name
r = ca.capture_branch_b_amendment(
    lead={"lead_id": "Lmp", "extracted": {}}, text="concurrent identical amendment",
    chat_id="c@lid", phone="+1999", message_id="wamid.SAME", source_transport="whatsapp",
    provider_timestamp="1721390400",
    now=datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc), data_path=Path(data_path),
    expected_owner=owner, expected_group=group, lock_attempts=60, lock_sleep_sec=0.2)
print(f"{r.ok}|{r.amendment_id}|{r.idempotent}")
'''


@pytest.mark.skipif(sys.platform == "win32",
                    reason="multi-process lock contention needs real fcntl.flock (stubbed on Windows)")
def test_concurrent_identical_capture_yields_one_record(tmp_path):
    import subprocess
    (tmp_path / "state").mkdir()
    data = tmp_path / "state" / "catering-amendments.json"
    logp = str(tmp_path / "audit" / "decisions.log")
    worker = tmp_path / "mp_worker.py"
    worker.write_text(_MP_WORKER, encoding="utf-8")
    platform_dir = str(SRC / "platform")
    procs = [subprocess.Popen([sys.executable, str(worker), platform_dir, str(data), logp],
                              stdout=subprocess.PIPE, text=True) for _ in range(2)]
    outs = [p.communicate()[0].strip() for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    assert all(o.startswith("True|A0001|") for o in outs), outs
    # exactly one durable record; the loser deduped (idempotent True)
    store = json.loads(data.read_text(encoding="utf-8"))
    assert len(store["records"]) == 1, f"concurrency produced {len(store['records'])} records: {outs}"
    assert sorted(o.split("|")[2] for o in outs) == ["False", "True"], outs


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink/ownership/mode contract")
def test_symlink_data_path_rejected_and_preserved(tmp_path):
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps({"schema_version": 1, "next_seq": 1, "records": []}), encoding="utf-8")
    data = tmp_path / "catering-amendments.json"
    os.symlink(real, data)
    r = _cap(data, message_id="wamid.x")
    assert not r.ok and r.reason == "fs_path_symlink"
    assert os.path.islink(data), "the symlink must be left in place, never replaced"
    assert real.read_text(encoding="utf-8") == json.dumps(
        {"schema_version": 1, "next_seq": 1, "records": []})


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink contract")
def test_parent_symlink_rejected(tmp_path):
    realdir = tmp_path / "realdir"
    realdir.mkdir()
    linkdir = tmp_path / "linkdir"
    os.symlink(realdir, linkdir)
    r = _cap(linkdir / "catering-amendments.json", message_id="wamid.x")
    assert not r.ok and r.reason == "fs_parent_symlink"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX regular-file contract")
def test_non_regular_path_rejected(tmp_path):
    data = tmp_path / "catering-amendments.json"
    data.mkdir()  # a directory where the store file should be
    r = _cap(data, message_id="wamid.x")
    assert not r.ok and r.reason == "fs_path_not_regular"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode enforcement")
def test_written_file_mode_is_0640(tmp_path):
    data = tmp_path / "catering-amendments.json"
    r = _cap(data, message_id="wamid.x")
    assert r.ok
    assert ("%o" % (os.stat(data).st_mode & 0o777)) == "640"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode enforcement")
def test_out_of_band_mode_tamper_rejected_and_preserved(tmp_path):
    data = tmp_path / "catering-amendments.json"
    _cap(data, message_id="wamid.first")
    before = _store(data)
    os.chmod(data, 0o644)  # world-readable — outside {640,660}
    r = _cap(data, message_id="wamid.second", text="new")
    assert not r.ok and r.reason == "fs_path_bad_mode"
    assert _store(data) == before, "a mode-tamper rejection must leave the store intact"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership enforcement")
def test_wrong_expected_owner_rejected(tmp_path):
    data = tmp_path / "catering-amendments.json"
    _cap(data, message_id="wamid.first")  # valid store owned by the runner
    before = _store(data)
    r = _cap(data, message_id="wamid.second", text="new",
             expected_owner="definitely-not-a-real-user-xyz")
    assert not r.ok and "bad_owner" in r.reason
    assert _store(data) == before


# ════════════════════════════════════════════════════════════════════════════
# Static invariant — cf_router_intercepted reason-enum coverage (bug-CLASS guard)
#
# `actions.audit_intercepted()` ALWAYS builds a CfRouterIntercepted(reason=reason)
# and swallows exceptions (best-effort). So any `reason=` literal it is called with
# that is NOT a member of CfRouterIntercepted.reason raises a pydantic
# ValidationError that is silently eaten → the LLM-suppressing intercept vanishes
# from routing / dispatcher-accuracy telemetry. That is exactly the gap PR-R2A's
# `f7_primary_amendment_capture_failed` reason hit (CI round 3). This static source
# scan fails CI on any future emitter/enum drift instead. Pure ast parse — no plugin
# import — so it runs on every platform (incl. Windows). Per the standing rule:
# every documented invariant gets a test that fails if the invariant is violated.
# ════════════════════════════════════════════════════════════════════════════
def _cf_router_reason_enum() -> set:
    import schemas  # fcntl already stubbed at module import
    return set(get_args(schemas.CfRouterIntercepted.model_fields["reason"].annotation))


def _audit_intercepted_reason_literals(path: Path) -> set:
    """Every string literal that can be passed as the `reason` of an
    `audit_intercepted(...)` call in `path`. Reads the `reason=` keyword (or the
    first positional arg, matching the signature) and recurses into the expression
    so a conditional `"a" if cond else "b"` contributes BOTH "a" and "b". Bare
    variable reasons (no string constant anywhere) are not statically resolvable
    and are skipped — the four-outcome routing tests cover the R2A reasons at
    runtime. Comments are ignored inherently (ast parse, not text grep)."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    literals: set = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        fname = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else None)
        if fname != "audit_intercepted":
            continue
        reason = next((kw.value for kw in node.keywords if kw.arg == "reason"), None)
        if reason is None and node.args:
            reason = node.args[0]  # audit_intercepted(reason, chat_id, ...)
        if reason is None:
            continue
        literals.update(d.value for d in ast.walk(reason)
                        if isinstance(d, ast.Constant) and isinstance(d.value, str))
    return literals


def test_audit_intercepted_literal_reasons_are_all_enum_members():
    """Drift guard (bug-CLASS): every literal reason emitted to audit_intercepted()
    across the cf-router plugin must be a CfRouterIntercepted.reason member, or the
    best-effort audit swallows it into invisible telemetry."""
    allowed = _cf_router_reason_enum()
    emitted: set = set()
    for name in ("hooks.py", "actions.py"):
        emitted |= _audit_intercepted_reason_literals(PLUGIN_DIR / name)
    assert emitted, "scan found zero literal reasons — the extractor regressed"
    orphan = sorted(emitted - allowed)
    assert not orphan, (
        "audit_intercepted() emits reason literal(s) absent from "
        "CfRouterIntercepted.reason — they would be swallowed into invisible "
        f"routing telemetry: {orphan}"
    )


def test_r2a_capture_failed_reason_is_enum_member():
    """Pin the specific PR-R2A reason whose omission caused the CI-round-3 gap."""
    assert "f7_primary_amendment_capture_failed" in _cf_router_reason_enum()
