"""PR-R1 routing-invariants — the enforcing tests for invariants I1-I5.

Covers (reviewer's verbatim list):
  * Identity convergence (I1, I5): phone/LID converge; empty/stale/malformed/
    unknown cache fail-safe to today's 3-priority behavior; most-recent-wins
    among multiple non-terminal leads (characterized, not necessarily permanent);
    repeated lookup idempotent.
  * Pool invariants (I2, I3, I4): no pool / exactly one pool / two pools (fail-
    closed CollisionResult from resolve AND the F8 path); canonical order from
    one source; F8 consumes the registry; every generator excludes cross-pool
    codes; collision never falls through to first canonical match; owner alert
    goes through shift-agent-notify-owner; repeated collision -> one notification.
  * Concurrency: the reservation lock — sequential proof + a genuinely concurrent
    two-thread test (skipped where FileLock needs real fcntl, i.e. Windows).
  * Compatibility: valid codes per pool resolve as before; the four intentionally
    -changed paths each explicitly exercised.

Runs on Windows via the fcntl stub. ALL writes are tmp-path; the SKILL.md-order
+ cross-pool-source text assertions live in test_approval_code_pool_invariants.py.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub, load_script, read_log_rows

ensure_fcntl_stub()  # before any safe_io / schemas / approval_code_pools import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLUGIN_DIR = SRC / "plugins" / "cf-router"
for _p in (SRC, SRC / "platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import approval_code_pools as pools  # noqa: E402
import safe_io  # noqa: E402


# ── state-dir + pool-file helpers (all tmp) ──────────────────────────────────
def _write_pools(state_dir: Path, *, menu=None, catering=None, expense=None, shift=None):
    """Write pool state files under `state_dir`. catering/expense are lists of
    lead dicts; shift is a dict {proposal_id: proposal_dict}; menu is a dict."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    if menu is not None:
        (state_dir / "catering-menu-pending.json").write_text(json.dumps(menu), encoding="utf-8")
    if catering is not None:
        (state_dir / "catering-leads.json").write_text(
            json.dumps({"leads": catering}), encoding="utf-8")
    if expense is not None:
        (state_dir / "expense-bookkeeper").mkdir(parents=True, exist_ok=True)
        (state_dir / "expense-bookkeeper" / "leads.json").write_text(
            json.dumps({"leads": expense}), encoding="utf-8")
    if shift is not None:
        (state_dir / "pending.json").write_text(
            json.dumps({"proposals": shift}), encoding="utf-8")


def _catering_lead(code, status="AWAITING_OWNER_APPROVAL"):
    return {"lead_id": "L0001", "owner_approval_code": code, "status": status}


def _expense_lead(code, status="AWAITING_OWNER_APPROVAL"):
    return {"expense_id": "E0001", "owner_approval_code": code, "status": status}


def _shift_prop(code, status="sent"):
    return {"P1": {"proposal_id": "P1", "code": code, "status": status}}


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir()
    monkeypatch.setenv("SHIFT_AGENT_STATE_DIR", str(d))
    return d


# ════════════════════════════════════════════════════════════════════════════
# Pool invariants — I2 / I3 / I4
# ════════════════════════════════════════════════════════════════════════════
def test_resolve_no_pool_has_code(state_dir):
    _write_pools(state_dir, catering=[_catering_lead("#CAT01")])
    assert pools.resolve_code("#ZZZZZ") is None


@pytest.mark.parametrize("which,code", [
    ("menu", "#MEN01"), ("catering", "#CAT01"),
    ("expense", "#EXP01"), ("shift", "#SHF01"),
])
def test_resolve_exactly_one_pool(state_dir, which, code):
    kwargs = {
        "menu": {"confirmation_code": code} if which == "menu" else None,
        "catering": [_catering_lead(code)] if which == "catering" else None,
        "expense": [_expense_lead(code)] if which == "expense" else None,
        "shift": _shift_prop(code) if which == "shift" else None,
    }
    _write_pools(state_dir, **kwargs)
    res = pools.resolve_code(code)
    expected_pool = {
        "menu": pools.POOL_MENU_PENDING, "catering": pools.POOL_CATERING_LEADS,
        "expense": pools.POOL_EXPENSE, "shift": pools.POOL_SHIFT,
    }[which]
    assert not isinstance(res, pools.CollisionResult)
    assert res is not None and res[0] == expected_pool


@pytest.mark.parametrize("a,b,write", [
    ("menu-pending", "catering-leads",
     lambda s, c: _write_pools(s, menu={"confirmation_code": c}, catering=[_catering_lead(c)])),
    ("menu-pending", "shift",
     lambda s, c: _write_pools(s, menu={"confirmation_code": c}, shift=_shift_prop(c))),
    ("catering-leads", "expense",
     lambda s, c: _write_pools(s, catering=[_catering_lead(c)], expense=[_expense_lead(c)])),
])
def test_resolve_two_pools_fail_closed(state_dir, a, b, write):
    write(state_dir, "#DUP01")
    res = pools.resolve_code("#DUP01")
    assert isinstance(res, pools.CollisionResult), "collision MUST NOT resolve to a single match"
    assert res.pools == (a, b), f"pools must be canonical-ordered: {res.pools}"
    assert res.code == "#DUP01"


def test_collision_cannot_fall_through_to_first_canonical(state_dir):
    """A code in menu (first canonical) AND catering must NOT resolve to menu —
    it must fail closed."""
    _write_pools(state_dir, menu={"confirmation_code": "#DUP01"},
                 catering=[_catering_lead("#DUP01")])
    res = pools.resolve_code("#DUP01")
    # MUST be the fail-closed sentinel, NOT a (pool, row) first-match tuple.
    assert isinstance(res, pools.CollisionResult)
    assert not isinstance(res, tuple)


def test_canonical_order_exported_from_one_source():
    assert pools.CODE_POOL_CANONICAL_ORDER == (
        "menu-pending", "catering-leads", "expense", "shift")


def test_all_live_codes_union_excludes_terminal(state_dir):
    _write_pools(
        state_dir,
        menu={"confirmation_code": "#MEN01"},
        catering=[_catering_lead("#CAT01"), _catering_lead("#CATx", status="CLOSED")],
        expense=[_expense_lead("#EXP01"), _expense_lead("#EXPx", status="PUSHED")],
        shift=_shift_prop("#SHF01"),
    )
    assert pools.all_live_codes() == {"#MEN01", "#CAT01", "#EXP01", "#SHF01"}


@pytest.mark.parametrize("pool,terminal", [
    ("catering", "CLOSED"), ("catering", "OWNER_REJECTED"), ("catering", "STALE"),
    ("expense", "PUSHED"), ("expense", "REVERSED"), ("expense", "REJECTED"), ("expense", "EXPIRED"),
])
def test_terminal_status_excluded_from_resolve(state_dir, pool, terminal):
    if pool == "catering":
        _write_pools(state_dir, catering=[_catering_lead("#TRM01", status=terminal)])
    else:
        _write_pools(state_dir, expense=[_expense_lead("#TRM01", status=terminal)])
    assert pools.resolve_code("#TRM01") is None


# ════════════════════════════════════════════════════════════════════════════
# Collision event — audit every time, notify once, privacy, chokepoint
# ════════════════════════════════════════════════════════════════════════════
def _collision():
    return pools.CollisionResult(code="#DUP01", pools=("menu-pending", "catering-leads"))


def _patch_notify(monkeypatch, fn):
    """Patch the owner-alert chokepoint at the LIVE ``sys.modules['safe_io']`` —
    the exact object the kernel resolves.

    THE REAL SEAM is ``safe_io.notify_owner_with_fallback`` (approval_code_pools.
    record_collision_event does a lazy ``from safe_io import
    notify_owner_with_fallback``, resolved via sys.modules at CALL time). Patching
    a module-level ``import safe_io`` reference bound at test-load time is NOT
    robust: test_cf_router_plugin.py:76-77 pops ``safe_io`` from sys.modules, and
    the plugin load (hooks.py -> actions._ensure_platform_path, actions.py:83-89)
    inserts ``/opt/shift-agent`` onto sys.path; on a box with a deployed
    /opt/shift-agent/safe_io.py the re-import rebinds sys.modules['safe_io'] to the
    /opt copy. The stale ref then misses the kernel's call, the REAL chokepoint
    runs (subprocess to an absent /usr/local/bin/shift-agent-notify-owner ->
    returns False), and zero calls are captured (Linux-CI-only symptom). Resolving
    the module fresh here always targets what the kernel imports."""
    import importlib
    sio = importlib.import_module("safe_io")  # == the live sys.modules['safe_io']
    monkeypatch.setattr(sio, "notify_owner_with_fallback", fn)


def test_collision_audit_emitted_every_call(state_dir, monkeypatch):
    log = Path(sys.modules["safe_io"]._decisions_log_path())  # conftest-isolated tmp path
    _patch_notify(monkeypatch, lambda *a, **k: True)
    pools.record_collision_event(_collision(), detected_by="unit")
    pools.record_collision_event(_collision(), detected_by="unit")
    rows = [r for r in read_log_rows(log) if r["type"] == "approval_code_collision_detected"]
    assert len(rows) == 2


def test_collision_audit_privacy(state_dir, monkeypatch):
    log = Path(sys.modules["safe_io"]._decisions_log_path())
    _patch_notify(monkeypatch, lambda *a, **k: True)
    pools.record_collision_event(_collision(), detected_by="f8_intercept")
    row = [r for r in read_log_rows(log) if r["type"] == "approval_code_collision_detected"][-1]
    assert row["code"] == "#DUP01"
    assert row["pools"] == ["menu-pending", "catering-leads"]
    assert row["detected_by"] == "f8_intercept"
    # Privacy: ONLY code + pool names + detected_by (+ type/ts). No phone/text/chat.
    assert set(row) == {"type", "ts", "code", "pools", "detected_by"}


def test_collision_notifies_owner_once_via_chokepoint(state_dir, monkeypatch):
    calls = []
    _patch_notify(monkeypatch,
                  lambda title, message, **k: calls.append((title, message, k)) or True)
    pools.record_collision_event(_collision(), detected_by="unit")
    pools.record_collision_event(_collision(), detected_by="unit")
    pools.record_collision_event(_collision(), detected_by="unit")
    assert len(calls) == 1, "sentinel must suppress repeat notifications for the same code"
    title, message, _ = calls[0]
    # goes through the notify-owner chokepoint (monkeypatched here) — never a bridge send.
    assert "#DUP01" in message and "menu-pending" in message and "catering-leads" in message
    # privacy on the alert body too
    assert "phone" not in message.lower() and "chat" not in message.lower()


def test_collision_distinct_codes_each_notify(state_dir, monkeypatch):
    calls = []
    _patch_notify(monkeypatch, lambda t, m, **k: calls.append(m) or True)
    pools.record_collision_event(
        pools.CollisionResult(code="#AAAAA", pools=("menu-pending", "shift")), detected_by="u")
    pools.record_collision_event(
        pools.CollisionResult(code="#BBBBB", pools=("menu-pending", "shift")), detected_by="u")
    assert len(calls) == 2


# ════════════════════════════════════════════════════════════════════════════
# Concurrency — the reservation lock
# ════════════════════════════════════════════════════════════════════════════
def _write_fn_factory(state_dir):
    """Return a write_fn(code) that appends the code to the catering pool so a
    later all_live_codes() sees it (mirrors a generator's own-store write)."""
    path = state_dir / "catering-leads.json"

    def write_fn(code):
        doc = json.loads(path.read_text()) if path.exists() else {"leads": []}
        doc["leads"].append(_catering_lead(code))
        path.write_text(json.dumps(doc), encoding="utf-8")
        return code
    return write_fn


def _seq_candidate(first, fallback):
    calls = {"n": 0}

    def cand():
        calls["n"] += 1
        return first if calls["n"] == 1 else fallback
    return cand


def test_atomic_scan_and_commit_sequential_regenerates(state_dir):
    """Windows-runnable sequential proof: the second scan-and-commit sees the
    first's committed code and regenerates away from the forced-identical
    candidate."""
    write_fn = _write_fn_factory(state_dir)
    code1, _ = pools.atomic_scan_and_commit(
        write_fn, candidate_fn=_seq_candidate("#AAAAA", "#AAAAA"), state_dir=state_dir)
    code2, _ = pools.atomic_scan_and_commit(
        write_fn, candidate_fn=_seq_candidate("#AAAAA", "#CCCCC"), state_dir=state_dir)
    assert code1 == "#AAAAA"
    assert code2 == "#CCCCC"
    assert code1 != code2


def test_pool_write_failure_no_code_issued(state_dir):
    """A pool-store write failure propagates; NO code is reported as issued and
    nothing is committed."""
    def bad_write(code):
        raise RuntimeError("simulated disk-full on commit")
    with pytest.raises(RuntimeError, match="disk-full"):
        pools.atomic_scan_and_commit(
            bad_write, candidate_fn=lambda: "#AAAAA", state_dir=state_dir)
    assert pools.all_live_codes() == set()  # nothing committed


@pytest.mark.skipif(sys.platform == "win32",
                    reason="bounded-lock timeout needs real fcntl.flock (stubbed on Windows)")
def test_lock_timeout_fails_closed(state_dir, monkeypatch):
    """When the shared lock is already held, a bounded acquisition times out and
    FAILS CLOSED (raises before the body runs; no code issued)."""
    from safe_io import FileLock, LockUnavailable
    monkeypatch.setenv("SHIFT_AGENT_CODE_POOL_LOCK_ATTEMPTS", "1")
    monkeypatch.setenv("SHIFT_AGENT_CODE_POOL_LOCK_SLEEP_SEC", "0")
    lock_path = pools._resolve_lock_path(state_dir)
    committed = []
    with FileLock(lock_path):  # hold the lock
        with pytest.raises(LockUnavailable):
            pools.atomic_scan_and_commit(
                lambda code: committed.append(code),
                candidate_fn=lambda: "#AAAAA", state_dir=state_dir,
            )
    assert committed == []  # write_fn never ran -> no code issued


_MP_WORKER = r'''
import json, os, sys, time
from pathlib import Path
state_dir, first, fallback = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, sys.argv[4])
os.environ["SHIFT_AGENT_STATE_DIR"] = state_dir
import approval_code_pools as p
path = Path(state_dir) / "catering-leads.json"
n = {"i": 0}
def cand():
    n["i"] += 1
    return first if n["i"] == 1 else fallback
def write_fn(code):
    doc = json.loads(path.read_text()) if path.exists() else {"leads": []}
    doc["leads"].append({"lead_id": "L", "owner_approval_code": code,
                         "status": "AWAITING_OWNER_APPROVAL"})
    time.sleep(0.4)  # widen the critical section so the peers genuinely contend
    path.write_text(json.dumps(doc))
    return code
code, _ = p.atomic_scan_and_commit(write_fn, candidate_fn=cand, state_dir=state_dir)
print(code)
'''


@pytest.mark.skipif(sys.platform == "win32",
                    reason="multi-process lock contention needs real fcntl.flock (stubbed on Windows)")
def test_atomic_scan_and_commit_multiprocess_no_duplicate(state_dir, tmp_path):
    """Genuinely concurrent MULTI-PROCESS issuers with forced-identical first
    candidate -> the shared lock serializes scan+commit, so no duplicate code is
    issued (the loser observes the winner's code and regenerates)."""
    import subprocess
    worker = tmp_path / "mp_worker.py"
    worker.write_text(_MP_WORKER, encoding="utf-8")
    platform_dir = str(SRC / "platform")
    procs = [
        subprocess.Popen([sys.executable, str(worker), str(state_dir), "#AAAAA", fb, platform_dir],
                         stdout=subprocess.PIPE, text=True)
        for fb in ("#DDDDD", "#EEEEE")
    ]
    outs = [p.communicate()[0].strip() for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    assert len(set(outs)) == 2, f"duplicate code issued under concurrency: {outs}"
    assert "#AAAAA" in set(outs), outs


# ════════════════════════════════════════════════════════════════════════════
# F8 path — consumes the registry, dispatches by pool, fail-closed on collision
# ════════════════════════════════════════════════════════════════════════════
def _load_plugin(state_dir):
    """Load cf-router actions + hooks as submodules of a synthetic package (the
    dir name has a hyphen). fcntl already stubbed at module import."""
    pkg = "cf_router_r1_pkg"
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
    actions_mod.LEADS_PATH = state_dir / "catering-leads.json"
    actions_mod.MENU_PENDING_PATH = state_dir / "catering-menu-pending.json"
    actions_mod.PENDING_PATH = state_dir / "pending.json"
    return hooks_mod, actions_mod


def test_f8_reflects_registry_menu_result(state_dir, monkeypatch):
    hooks_mod, actions_mod = _load_plugin(state_dir)
    calls = []
    monkeypatch.setattr(actions_mod, "invoke_apply_menu_update",
                        lambda code, verb: calls.append(("menu", code, verb)) or 0)
    monkeypatch.setattr(hooks_mod.approval_code_pools, "resolve_code",
                        lambda code, **k: (pools.POOL_MENU_PENDING, {"confirmation_code": code}))
    out = hooks_mod._try_f8_intercept("#ABCDE yes", "owner@c.us")
    assert calls == [("menu", "#ABCDE", "yes")]
    assert out["action"] == "skip" and "apply-menu-update" in out["reason"]


def test_f8_reflects_registry_catering_result(state_dir, monkeypatch):
    hooks_mod, actions_mod = _load_plugin(state_dir)
    calls = []
    monkeypatch.setattr(actions_mod, "invoke_apply_owner_decision",
                        lambda code, decision, lead=None: calls.append((code, decision, lead)) or 0)
    lead = _catering_lead("#ABCDE")
    monkeypatch.setattr(hooks_mod.approval_code_pools, "resolve_code",
                        lambda code, **k: (pools.POOL_CATERING_LEADS, lead))
    out = hooks_mod._try_f8_intercept("#ABCDE approve", "owner@c.us")
    assert calls == [("#ABCDE", "approve", lead)]
    assert out["action"] == "skip"


def test_f8_collision_refuses_and_records(state_dir, monkeypatch):
    hooks_mod, actions_mod = _load_plugin(state_dir)
    owner = []
    menu = []
    monkeypatch.setattr(actions_mod, "invoke_apply_owner_decision",
                        lambda *a, **k: owner.append(a) or 0)
    monkeypatch.setattr(actions_mod, "invoke_apply_menu_update",
                        lambda *a, **k: menu.append(a) or 0)
    recorded = []
    monkeypatch.setattr(hooks_mod.approval_code_pools, "record_collision_event",
                        lambda coll, detected_by=None: recorded.append((coll, detected_by)))
    monkeypatch.setattr(hooks_mod.approval_code_pools, "resolve_code",
                        lambda code, **k: pools.CollisionResult(code=code, pools=("menu-pending", "catering-leads")))
    out = hooks_mod._try_f8_intercept("#ABCDE approve", "owner@c.us")
    assert out is None, "F8 must refuse (fall through) on collision"
    assert owner == [] and menu == [], "no approval may be applied on collision"
    assert len(recorded) == 1 and recorded[0][1] == "f8_intercept"


def test_f8_real_registry_collision_end_to_end(state_dir, monkeypatch):
    """Real registry (no resolve monkeypatch): a code in menu AND catering ->
    F8 refuses, writes the audit row, applies nothing."""
    hooks_mod, actions_mod = _load_plugin(state_dir)
    _write_pools(state_dir, menu={"confirmation_code": "#ABCDE"},
                 catering=[_catering_lead("#ABCDE")])
    _patch_notify(monkeypatch, lambda *a, **k: True)  # live sys.modules['safe_io'] seam
    owner = []
    monkeypatch.setattr(actions_mod, "invoke_apply_owner_decision", lambda *a, **k: owner.append(a) or 0)
    monkeypatch.setattr(actions_mod, "invoke_apply_menu_update", lambda *a, **k: owner.append(a) or 0)
    out = hooks_mod._try_f8_intercept("#ABCDE approve", "owner@c.us")
    assert out is None
    assert owner == []
    log = Path(sys.modules["safe_io"]._decisions_log_path())
    rows = [r for r in read_log_rows(log) if r["type"] == "approval_code_collision_detected"]
    assert len(rows) == 1 and rows[0]["code"] == "#ABCDE"


def test_f8_real_registry_menu_before_catering_no_collision(state_dir, monkeypatch):
    """Intentionally-changed path (d): when only catering has the code, F8 still
    resolves it (canonical order does not break single-pool lookups)."""
    hooks_mod, actions_mod = _load_plugin(state_dir)
    _write_pools(state_dir, catering=[_catering_lead("#ABCDE")])
    calls = []
    monkeypatch.setattr(actions_mod, "invoke_apply_owner_decision",
                        lambda code, decision, lead=None: calls.append((code, decision)) or 0)
    out = hooks_mod._try_f8_intercept("#ABCDE approve", "owner@c.us")
    assert calls == [("#ABCDE", "approve")]
    assert out["action"] == "skip"


# ════════════════════════════════════════════════════════════════════════════
# Catering identity convergence — I1 / I5 (find_active_catering_lead_by_sender)
# ════════════════════════════════════════════════════════════════════════════
LID = "200000000000001@lid"
LID_DIGITS = "200000000000001"
PHONE = "+17329837841"


@pytest.fixture
def actions_id(tmp_path, monkeypatch):
    """cf-router actions loaded with LEADS_PATH + lid-cache pointed at tmp."""
    _, actions_mod = _load_plugin(tmp_path / "state")
    (tmp_path / "state").mkdir(exist_ok=True)
    actions_mod.LEADS_PATH = tmp_path / "state" / "catering-leads.json"
    cache = tmp_path / "lid-cache.json"
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(cache))
    return actions_mod, tmp_path, cache


def _seed_leads(actions_mod, leads):
    actions_mod.LEADS_PATH.write_text(json.dumps({"leads": leads}), encoding="utf-8")


def _lead(lead_id, *, phone=None, lid=None, status="AWAITING_OWNER_APPROVAL", created="2026-07-01T00:00:00+00:00"):
    return {
        "lead_id": lead_id, "owner_approval_code": f"#{lead_id[-5:]}",
        "status": status, "customer_phone": phone, "customer_lid": lid,
        "created_at": created, "updated_at": created,
    }


def _paired_cache(cache: Path):
    cache.write_text(json.dumps({"schema_version": 1,
                                 "pairs": [{"phone": PHONE, "lid": LID}]}), encoding="utf-8")


def test_identity_phone_and_lid_converge_paired_cache(actions_id):
    actions_mod, _, cache = actions_id
    _paired_cache(cache)
    _seed_leads(actions_mod, [_lead("L0001", phone=PHONE, lid=None)])
    # phone arrival
    via_phone = actions_mod.find_active_catering_lead_by_sender(PHONE, f"{PHONE}@s.whatsapp.net")
    # LID arrival (no phone) — converges via canonical key (priority 4)
    via_lid = actions_mod.find_active_catering_lead_by_sender(None, LID)
    assert via_phone is not None and via_lid is not None
    assert via_phone["lead_id"] == via_lid["lead_id"] == "L0001"


def test_identity_empty_cache_byte_identical(actions_id):
    """No lid-cache -> LID sender does NOT converge onto a phone-stored lead
    (byte-identical to pre-PR-R1 3-priority behavior; census: cache empty)."""
    actions_mod, _, cache = actions_id  # cache file not created -> missing
    _seed_leads(actions_mod, [_lead("L0001", phone=PHONE, lid=None)])
    assert actions_mod.find_active_catering_lead_by_sender(None, LID) is None
    # a phone sender still matches (priority 1, unchanged)
    assert actions_mod.find_active_catering_lead_by_sender(PHONE, None)["lead_id"] == "L0001"


def test_identity_stale_cache_no_false_match(actions_id):
    actions_mod, _, cache = actions_id
    cache.write_text(json.dumps({"schema_version": 1,
                                 "pairs": [{"phone": "+19999999999", "lid": LID}]}), encoding="utf-8")
    _seed_leads(actions_mod, [_lead("L0001", phone=PHONE, lid=None)])  # different phone
    assert actions_mod.find_active_catering_lead_by_sender(None, LID) is None


def test_identity_malformed_cache_fails_safe(actions_id):
    actions_mod, _, cache = actions_id
    cache.write_text("{{{ not json", encoding="utf-8")
    _seed_leads(actions_mod, [_lead("L0001", phone=PHONE, lid=None)])
    # must not raise; degrades to 3-priority (no convergence)
    assert actions_mod.find_active_catering_lead_by_sender(None, LID) is None


def test_identity_unknown_lid_todays_behavior(actions_id):
    actions_mod, _, cache = actions_id
    _paired_cache(cache)  # pairs a DIFFERENT lid
    _seed_leads(actions_mod, [_lead("L0001", phone=PHONE, lid=None)])
    other_lid = "200000000000999@lid"
    assert actions_mod.find_active_catering_lead_by_sender(None, other_lid) is None


def test_identity_multiple_non_terminal_most_recent_wins(actions_id):
    """I5 characterization (L0016/L0017 shape): most-recent-wins among multiple
    ACTIONABLE leads on one identity. Characterized as KNOWN CURRENT behavior —
    not necessarily permanent policy. Synthetic fixture, never live data."""
    actions_mod, _, cache = actions_id
    _seed_leads(actions_mod, [
        _lead("L0016", phone=PHONE, status="CUSTOMER_FINALIZED", created="2026-07-01T00:00:00+00:00"),
        _lead("L0017", phone=PHONE, status="AWAITING_OWNER_APPROVAL", created="2026-07-05T00:00:00+00:00"),
    ])
    got = actions_mod.find_active_catering_lead_by_sender(PHONE, None)
    assert got["lead_id"] == "L0017"  # newest created_at


def test_identity_legacy_L0005_L0006_shape_unchanged(actions_id):
    """I5 characterization: the L0005/L0006 legacy cluster shape (SENT_TO_CUSTOMER,
    non-ACTIONABLE) resolves unchanged — skipped by the ACTIONABLE filter, so
    priority-4 never sees it. Synthetic fixture, never live data."""
    actions_mod, _, cache = actions_id
    _paired_cache(cache)
    _seed_leads(actions_mod, [
        _lead("L0005", phone="+201975216009469", status="SENT_TO_CUSTOMER"),
        _lead("L0006", phone="+201975216009469", status="SENT_TO_CUSTOMER"),
    ])
    assert actions_mod.find_active_catering_lead_by_sender("+201975216009469", None) is None


def test_identity_repeated_lookup_idempotent(actions_id):
    actions_mod, tmp_path, cache = actions_id
    _paired_cache(cache)
    _seed_leads(actions_mod, [_lead("L0001", phone=PHONE, lid=None)])
    before_leads = actions_mod.LEADS_PATH.read_text()
    before_cache = cache.read_text()
    r1 = actions_mod.find_active_catering_lead_by_sender(None, LID)
    r2 = actions_mod.find_active_catering_lead_by_sender(None, LID)
    assert r1 == r2 and r1["lead_id"] == "L0001"
    # never writes to cache or leads on the hot path
    assert actions_mod.LEADS_PATH.read_text() == before_leads
    assert cache.read_text() == before_cache


# ════════════════════════════════════════════════════════════════════════════
# Per-generator cross-pool exclusion — I2 (forced-RNG planted collision)
# ════════════════════════════════════════════════════════════════════════════
def _force_choice(monkeypatch, mod, planted_body):
    """Make secrets.choice yield planted_body's chars first (building the planted
    code), then fall back to real randomness so the generator regenerates."""
    import secrets as _s
    real = _s.choice
    seq = iter(list(planted_body))

    def fake(alpha):
        try:
            return next(seq)
        except StopIteration:
            return real(alpha)
    monkeypatch.setattr(mod.secrets, "choice", fake)


def _empty_expense_store(mod):
    return mod.ExpenseLeadStore()


@pytest.mark.parametrize("script_rel,modname,call", [
    ("agents/shift/scripts/create-proposal", "cp_gen",
     lambda m: m.generate_unique_code()),
    ("agents/catering/scripts/create-catering-lead", "ccl_gen",
     lambda m: m._generate_unique_code()),
    ("agents/catering/scripts/parse-menu-photo", "pmp_gen",
     lambda m: m._generate_unique_code()),
    ("agents/expense_bookkeeper/scripts/extract-receipt", "er_gen",
     lambda m: m._generate_unique_code(_empty_expense_store(m))),
])
def test_generator_excludes_planted_cross_pool_code(state_dir, monkeypatch, script_rel, modname, call):
    # Plant a live code in a SIBLING pool (shift), reachable only via all_live_codes.
    _write_pools(state_dir, shift=_shift_prop("#AB2CD"))
    mod = load_script(modname, SRC / script_rel)
    _force_choice(monkeypatch, mod, "AB2CD")  # first candidate == planted -> collision
    code = call(mod)
    assert code != "#AB2CD", "generator must regenerate away from a live cross-pool code"
    assert code.startswith("#") and len(code) == 6


# ════════════════════════════════════════════════════════════════════════════
# Compatibility — valid codes per pool resolve as before
# ════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("which,code,status", [
    ("catering", "#CAT01", "AWAITING_OWNER_APPROVAL"),
    ("catering", "#CAT02", "CUSTOMER_FINALIZED"),  # second routing-eligible status
    ("expense", "#EXP01", "AWAITING_OWNER_APPROVAL"),
    ("shift", "#SHF01", "sent"),
    ("menu", "#MEN01", None),
])
def test_valid_codes_resolve_per_pool(state_dir, which, code, status):
    if which == "menu":
        _write_pools(state_dir, menu={"confirmation_code": code})
        expected = pools.POOL_MENU_PENDING
    elif which == "catering":
        _write_pools(state_dir, catering=[_catering_lead(code, status=status)])
        expected = pools.POOL_CATERING_LEADS
    elif which == "expense":
        _write_pools(state_dir, expense=[_expense_lead(code, status=status)])
        expected = pools.POOL_EXPENSE
    else:
        _write_pools(state_dir, shift=_shift_prop(code, status=status))
        expected = pools.POOL_SHIFT
    res = pools.resolve_code(code)
    assert not isinstance(res, pools.CollisionResult)
    assert res is not None and res[0] == expected


# ════════════════════════════════════════════════════════════════════════════
# No silent fallback — every generator imports approval_code_pools UNCONDITIONALLY
# ════════════════════════════════════════════════════════════════════════════
import re as _re

_GENERATORS = [
    SRC / "agents" / "shift" / "scripts" / "create-proposal",
    SRC / "agents" / "catering" / "scripts" / "create-catering-lead",
    SRC / "agents" / "catering" / "scripts" / "parse-menu-photo",
    SRC / "agents" / "expense_bookkeeper" / "scripts" / "extract-receipt",
]


@pytest.mark.parametrize("gen", _GENERATORS, ids=lambda p: p.name)
def test_generator_imports_pools_unconditionally(gen):
    """Every generator must `import approval_code_pools` at module top-level with
    NO try/except soft-fallback — a missing module on a box must hard-fail
    (ImportError), never silently degrade to unlocked/inline scanning."""
    text = gen.read_text(encoding="utf-8")
    # top-level (column 0) unconditional import present
    assert _re.search(r"^import approval_code_pools\b", text, _re.M), (
        f"{gen.name} lacks a top-level `import approval_code_pools`")
    # not guarded by a try: (which would allow a silent except-fallback)
    assert not _re.search(r"try:\s*(?:#[^\n]*)?\n\s+import approval_code_pools\b", text), (
        f"{gen.name} guards its approval_code_pools import in a try/except — "
        f"that permits a silent fallback to unlocked scanning")


# ════════════════════════════════════════════════════════════════════════════
# extract-receipt issuance boundary — code NOT exposed before the durable commit
# ════════════════════════════════════════════════════════════════════════════
@pytest.mark.skipif(sys.platform == "win32",
                    reason="ExpenseLead.image_path validator hardcodes '/' separators "
                           "(whole extract-receipt suite is Linux-only); the shared-lock "
                           "boundary is proven Windows-runnable by the cross-store sequential test")
def test_extract_receipt_no_code_exposed_before_commit(tmp_path, monkeypatch):
    """Load extract-receipt in-process (fcntl-stubbed), mock the network + image
    + card, run main(), and assert the leads write CARRYING THE CODE precedes the
    code-referencing audit AND the stdout that exposes the code — i.e. the code
    is issued only via atomic_scan_and_commit's durable commit."""
    import io
    import contextlib
    import yaml
    er = load_script("er_boundary_test", SRC / "agents" / "expense_bookkeeper" / "scripts" / "extract-receipt")
    state = tmp_path / "state" / "expense-bookkeeper"
    (state / "receipts").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    img = tmp_path / "r.jpg"
    img.write_bytes(b"x")
    cfg = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "l", "timezone": "America/New_York"},
        "owner": {"name": "O", "phone": "+19045550100", "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {}, "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "expense_bookkeeper": {"enabled": True, "qbo_client_mode": "mock"},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    er.CONFIG_PATH = tmp_path / "config.yaml"
    er.LEADS_PATH = state / "leads.json"
    er.LOG_PATH = tmp_path / "logs" / "decisions.log"
    er.RECEIPTS_DIR = state / "receipts"
    # ExpenseLead.image_path (schemas.py:3141) requires image_path to be under
    # EXPENSE_RECEIPTS_DIR (default /opt/...); point it at the real tmp receipts
    # dir (posix, trailing slash) so the fully-valid ExpenseLead the commit builds
    # passes the Linux `/`-separator validator (no schema weakening).
    monkeypatch.setenv("EXPENSE_RECEIPTS_DIR", er.RECEIPTS_DIR.as_posix() + "/")
    # hex hashes (image_byte_hash = sha256 hex, image_phash = 16 hex) so the real
    # ExpenseLead validates on Linux.
    monkeypatch.setattr(er, "_atomic_copy_image", lambda s, d: (b"img", "0" * 64, "0" * 16))
    monkeypatch.setattr(er, "_call_vision",
                        lambda *a, **k: {"total_cents": 1000, "line_items": [], "extraction_confidence": 0.9})
    monkeypatch.setattr(er, "_classify_text",
                        lambda *a, **k: {"is_business": True, "confidence": 0.9,
                                         "rationale": "t", "qbo_account": "Office Supplies"})
    monkeypatch.setattr(er, "_build_approval_card", lambda *a, **k: ("tmpl", "card text"))

    events: list[str] = []
    real_write = er.atomic_write_json

    def tracked_write(path, obj, **k):
        if str(path).endswith("leads.json"):
            leads = obj.get("leads") if isinstance(obj, dict) else []
            events.append("commit_with_code" if any(l.get("owner_approval_code") for l in leads)
                          else "write_no_code")
        return real_write(path, obj, **k)
    monkeypatch.setattr(er, "atomic_write_json", tracked_write)

    real_log = er._log

    def tracked_log(entry):
        if getattr(entry, "type", "") == "expense_owner_approval_requested":
            events.append("code_audit")
        return real_log(entry)
    monkeypatch.setattr(er, "_log", tracked_log)

    monkeypatch.setattr(sys, "argv", [
        "extract-receipt", "--image-path", str(img),
        "--source-image-id", "wa1", "--owner-phone", "+19045550100",
    ])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = er.main()
    assert rc == er.EXIT_OK, buf.getvalue()
    code = json.loads(buf.getvalue())["approval_code"]
    assert code.startswith("#") and len(code) == 6
    # the ONLY leads write carrying a code is the atomic commit, and it precedes
    # the code-referencing audit (nothing exposes the code before the commit).
    assert "commit_with_code" in events
    assert events.index("commit_with_code") < events.index("code_audit"), events
    stored = json.loads((state / "leads.json").read_text())
    assert stored["leads"][-1]["owner_approval_code"] == code


# ════════════════════════════════════════════════════════════════════════════
# Cross-generator contention — extract-receipt vs create-catering-lead boundary
# ════════════════════════════════════════════════════════════════════════════
def _xstore_writers(state_dir):
    exp = state_dir / "expense-bookkeeper" / "leads.json"
    exp.parent.mkdir(parents=True, exist_ok=True)
    cat = state_dir / "catering-leads.json"

    def _append(path, code):
        doc = json.loads(path.read_text()) if path.exists() else {"leads": []}
        doc["leads"].append({"lead_id": "L", "owner_approval_code": code,
                             "status": "AWAITING_OWNER_APPROVAL"})
        path.write_text(json.dumps(doc), encoding="utf-8")
        return code
    return exp, cat, (lambda c: _append(exp, c)), (lambda c: _append(cat, c))


def test_cross_store_contention_sequential(state_dir):
    """Windows-runnable proof of the shared boundary: an expense-pool commit and
    a catering-pool commit forced onto the SAME first candidate cannot both
    issue it — the second observes the first (all_live_codes spans both stores)
    and regenerates. No duplicate code across the two stores."""
    exp, cat, write_exp, write_cat = _xstore_writers(state_dir)
    c_cat, _ = pools.atomic_scan_and_commit(
        write_cat, candidate_fn=_seq_candidate("#AAAAA", "#AAAAA"), state_dir=state_dir)
    c_exp, _ = pools.atomic_scan_and_commit(
        write_exp, candidate_fn=_seq_candidate("#AAAAA", "#CCCCC"), state_dir=state_dir)
    assert c_cat == "#AAAAA" and c_exp == "#CCCCC" and c_cat != c_exp
    all_codes = ({l["owner_approval_code"] for l in json.loads(cat.read_text())["leads"]}
                 | {l["owner_approval_code"] for l in json.loads(exp.read_text())["leads"]})
    assert len(all_codes) == 2  # no duplicate across the two stores


_MP_XSTORE_WORKER = r'''
import json, os, sys, time
from pathlib import Path
state_dir, first, fallback, pool = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, sys.argv[5])
os.environ["SHIFT_AGENT_STATE_DIR"] = state_dir
import approval_code_pools as p
if pool == "expense":
    path = Path(state_dir) / "expense-bookkeeper" / "leads.json"
    path.parent.mkdir(parents=True, exist_ok=True)
else:
    path = Path(state_dir) / "catering-leads.json"
n = {"i": 0}
def cand():
    n["i"] += 1
    return first if n["i"] == 1 else fallback
def write_fn(code):
    doc = json.loads(path.read_text()) if path.exists() else {"leads": []}
    doc["leads"].append({"lead_id": "L", "owner_approval_code": code,
                         "status": "AWAITING_OWNER_APPROVAL"})
    time.sleep(0.4)  # widen the critical section so the peers genuinely contend
    path.write_text(json.dumps(doc))
    return code
code, _ = p.atomic_scan_and_commit(write_fn, candidate_fn=cand, state_dir=state_dir)
print(code)
'''


@pytest.mark.skipif(sys.platform == "win32",
                    reason="multi-process lock contention needs real fcntl.flock (stubbed on Windows)")
def test_cross_store_contention_multiprocess(state_dir, tmp_path):
    """Genuinely concurrent MULTI-PROCESS issuers — one on the expense store
    (extract-receipt's boundary), one on the catering store (create-catering-lead
    's) — forced onto the SAME first candidate. Exactly one durable issuance wins
    it; the loser regenerates. No duplicate code across the two stores."""
    import subprocess
    worker = tmp_path / "mp_xstore.py"
    worker.write_text(_MP_XSTORE_WORKER, encoding="utf-8")
    platform_dir = str(SRC / "platform")
    procs = [
        subprocess.Popen([sys.executable, str(worker), str(state_dir), "#AAAAA", fb, pool, platform_dir],
                         stdout=subprocess.PIPE, text=True)
        for pool, fb in (("expense", "#DDDDD"), ("catering", "#EEEEE"))
    ]
    outs = [pr.communicate()[0].strip() for pr in procs]
    assert all(pr.returncode == 0 for pr in procs), outs
    assert len(set(outs)) == 2, f"duplicate code issued across stores under concurrency: {outs}"
    assert "#AAAAA" in set(outs), outs
    exp = json.loads((state_dir / "expense-bookkeeper" / "leads.json").read_text())
    cat = json.loads((state_dir / "catering-leads.json").read_text())
    all_codes = ({l["owner_approval_code"] for l in exp["leads"]}
                 | {l["owner_approval_code"] for l in cat["leads"]})
    assert len(all_codes) == 2  # no cross-store duplicate durably committed


# ════════════════════════════════════════════════════════════════════════════
# Adapter drift — RESOLVE filters mirror the deployed authoritative helpers
# ════════════════════════════════════════════════════════════════════════════
_ALL_CATERING_STATUSES = [
    "NEW", "EXTRACTING", "NOT_CATERING", "AWAITING_OWNER_APPROVAL",
    "CUSTOMER_FINALIZED", "OWNER_APPROVED", "OWNER_EDITED", "OWNER_REJECTED",
    "SENT_TO_CUSTOMER", "CLOSED", "STALE",
]


def _reg_is_pool(res, pool):
    return (not isinstance(res, pools.CollisionResult)) and res is not None and res[0] == pool


def test_catering_resolve_parity_with_find_catering_lead_by_code(state_dir):
    """PARITY: the registry catering RESOLVE filter agrees with the deployed
    authoritative helper (actions.find_catering_lead_by_code) for EVERY catering
    status. Pins the duplicated filter against drift."""
    _, actions_mod = _load_plugin(state_dir)
    actions_mod.LEADS_PATH = state_dir / "catering-leads.json"
    leads, codes = [], []
    for i, s in enumerate(_ALL_CATERING_STATUSES):
        code = "#" + "ABCDEFGHJKM"[i] + "2222"
        codes.append((code, s))
        leads.append({"lead_id": f"L{i:04d}", "owner_approval_code": code, "status": s,
                      "customer_phone": None, "customer_lid": None,
                      "created_at": "2026-07-01T00:00:00+00:00"})
    _write_pools(state_dir, catering=leads)
    for code, s in codes:
        helper_found = actions_mod.find_catering_lead_by_code(code) is not None
        reg_found = _reg_is_pool(pools.resolve_code(code), pools.POOL_CATERING_LEADS)
        assert helper_found == reg_found, f"catering resolve parity drift at status {s}"


def test_menu_resolve_parity_with_find_menu_pending_by_code(state_dir):
    """PARITY: registry menu RESOLVE agrees with actions.find_menu_pending_by_code."""
    _, actions_mod = _load_plugin(state_dir)
    actions_mod.MENU_PENDING_PATH = state_dir / "catering-menu-pending.json"
    _write_pools(state_dir, menu={"confirmation_code": "#MEN99"})
    for code in ("#MEN99", "#OTHER"):
        helper_found = actions_mod.find_menu_pending_by_code(code) is not None
        reg_found = _reg_is_pool(pools.resolve_code(code), pools.POOL_MENU_PENDING)
        assert helper_found == reg_found, f"menu resolve parity drift for {code}"


def test_catering_resolve_excludes_but_enumerate_includes_sent(state_dir):
    """Two-filter design: a SENT_TO_CUSTOMER lead is NOT routing-eligible (resolve
    -> None, mirroring find_catering_lead_by_code's ACTIONABLE filter) but its
    code IS still in-play (enumerate includes it, so it is never re-minted)."""
    _write_pools(state_dir, catering=[_catering_lead("#SENT1", status="SENT_TO_CUSTOMER")])
    assert pools.resolve_code("#SENT1") is None
    assert "#SENT1" in pools.all_live_codes()


def test_expense_shift_filter_characterization(state_dir):
    """Characterization of the duplicated SKILL filters (no importable helper):
    expense excludes {PUSHED,REVERSED,REJECTED,EXPIRED}; shift matches any code."""
    _write_pools(
        state_dir,
        expense=[_expense_lead("#EXPa"), _expense_lead("#EXPz", status="PUSHED")],
        shift={"P1": {"proposal_id": "P1", "code": "#SHFa", "status": "sent"},
               "P2": {"proposal_id": "P2", "code": "#SHFz", "status": "expired"}},
    )
    assert _reg_is_pool(pools.resolve_code("#EXPa"), pools.POOL_EXPENSE)
    assert pools.resolve_code("#EXPz") is None  # PUSHED excluded
    assert _reg_is_pool(pools.resolve_code("#SHFa"), pools.POOL_SHIFT)
    assert _reg_is_pool(pools.resolve_code("#SHFz"), pools.POOL_SHIFT)  # no shift status filter


# ════════════════════════════════════════════════════════════════════════════
# Identity safety — convergence never writes; direct match always wins
# ════════════════════════════════════════════════════════════════════════════
def test_identity_convergence_never_writes_state(actions_id):
    actions_mod, _, cache = actions_id
    _paired_cache(cache)
    _seed_leads(actions_mod, [_lead("L0001", phone=PHONE, lid=None)])
    before_leads = actions_mod.LEADS_PATH.read_text()
    before_cache = cache.read_text()
    got = actions_mod.find_active_catering_lead_by_sender(None, LID)  # converges via canonical
    assert got is not None and got["lead_id"] == "L0001"
    assert actions_mod.LEADS_PATH.read_text() == before_leads
    assert cache.read_text() == before_cache


def test_identity_direct_match_wins_over_newer_canonical(actions_id):
    """A direct phone match and a canonical-ONLY match to DIFFERENT leads: the
    authoritative direct match wins even when the canonical candidate is newer.
    Canonical only ADDS candidates; on conflict the existing safe path wins."""
    actions_mod, _, cache = actions_id
    other_lid = "200000000000002@lid"
    cache.write_text(json.dumps({"schema_version": 1, "pairs": [
        {"phone": PHONE, "lid": LID}, {"phone": PHONE, "lid": other_lid},
    ]}), encoding="utf-8")
    _seed_leads(actions_mod, [
        _lead("LX", phone=PHONE, lid=None, created="2026-07-01T00:00:00+00:00"),   # direct
        _lead("LA", phone=None, lid=other_lid, created="2026-07-09T00:00:00+00:00"),  # canonical-only, newer
    ])
    got = actions_mod.find_active_catering_lead_by_sender(PHONE, LID)
    assert got["lead_id"] == "LX", "direct match must win over a newer canonical-only match"
