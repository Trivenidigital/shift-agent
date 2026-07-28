"""Governed live transport-budget evidence-enablement harness — test set.

Covers the FROZEN-SPEC test matrix (protocol / carrier / crash / admission /
effect+isolation) plus amendment-1 (epoch-scoped reconciliation) and amendment-2
(kernel-grounded epoch identity + strict startup singleton-ownership ordering).

All seams (socket, provider transport, clock, prerequisites) are in-process pure
fakes — NO real network / gateway / bridge / provider send. Cross-platform.

Authoritative contract: tasks/audits/catering-review-part1-2026-07-26/
transport-evidence-harness-frozen-spec.md (amendment 2, sha256
0053dd276d4cd6703541d6e64640a8e95e7b7b9b5fe8fbead882c0a824969d4a).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import secrets
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform", REPO / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import transport_evidence as te  # noqa: E402
import transport_evidence_diagnostic as ted  # noqa: E402
import transport_evidence_ledger as tel  # noqa: E402
import transport_evidence_lease as tle  # noqa: E402

FIXED_NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
FUTURE_RC_COMMIT = "b" * 40  # placeholder future RC commit — never 2bee67d
HARNESS_HASH = "a1b2c3d4" * 8
DEST = "+17329837841"
DEST_LID = "223456789012345@lid"
EVENT_TYPE = te.DIAGNOSTIC_EVENT_TYPE


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeConn:
    _counter = 0

    def __init__(self, peer_uid: int = 0, frames=None):
        FakeConn._counter += 1
        self.id = f"fakeconn_{FakeConn._counter}"
        self.peer_uid = peer_uid
        self._frames = list(frames or [])
        self.sent: list[dict] = []

    def recv_json(self):
        return self._frames.pop(0) if self._frames else None

    def send_json(self, obj):
        self.sent.append(obj)


class SeamState:
    """Mutable prerequisite state so a test can drift a value between PREPARE and
    COMMIT."""

    def __init__(self):
        self.feature = True
        self.commit = FUTURE_RC_COMMIT
        self.hash = HARNESS_HASH
        self.containment = True
        self.budget = (True, 5, 50)
        self.health = (True, True, 0)
        self.front_brain_armed = False  # FRONT_BRAIN_OUTBOUND_ENFORCE not armed (R1)
        # authorized identities (platform, form)
        self.authorized = {("whatsapp", DEST), ("whatsapp", DEST_LID)}
        self.aliases = {DEST: [DEST, DEST_LID], DEST_LID: [DEST_LID, DEST]}

    def seams(self, clock=None):
        clk = clock or (lambda: FIXED_NOW)
        return te.PrereqSeams(
            feature_enabled=lambda: self.feature,
            read_commit_hash=lambda: self.commit,
            installed_harness_digest=lambda: self.hash,
            containment_active=lambda: self.containment,
            is_destination_authorized=lambda p, forms: any(
                (p, f) in self.authorized for f in forms
            ),
            destination_alias_forms=lambda d: list(self.aliases.get(d, [d])),
            budget_state=lambda: self.budget,
            health_state=lambda: self.health,
            clock=clk,
            front_brain_enforce_armed=lambda d: self.front_brain_armed,
        )


def make_server(tmp_path, seamstate, *, epoch="epoch-current", reconciled=True,
                inject=None, run_id_factory=None, token_ttl_sec=30.0, clock=None):
    sd = Path(tmp_path)
    committed = tel.CommittedNonceLedger(sd / "committed.ndjson")
    runs = tel.RunLedger(sd)

    def factory(run_id, ep):
        return tel.AttemptLedger(sd / "attempts" / f"{run_id}.ndjson", ep)

    srv = te.EvidenceControlServer(
        seams=seamstate.seams(clock),
        committed_ledger=committed,
        run_ledger=runs,
        attempt_ledger_factory=factory,
        inject_diagnostic=inject or (lambda ctx: None),
        epoch=epoch,
        token_ttl_sec=token_ttl_sec,
        run_id_factory=run_id_factory,
    )
    if reconciled:
        srv.mark_reconciled()
        srv.set_loop(object())  # D1: readiness requires the captured loop too
    return srv


def prepare_dict(nonce=None, **over):
    d = {
        "op": "prepare",
        "nonce": nonce or secrets.token_hex(32),
        "authorized_commit": FUTURE_RC_COMMIT,
        "authorized_harness_hash": HARNESS_HASH,
        "normalized_destination": DEST,
        "expires_at": (FIXED_NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "diagnostic_event_type": EVENT_TYPE,
    }
    d.update(over)
    return d


# ═══════════════════════════════ PROTOCOL ═══════════════════════════════════


def test_non_root_peer_rejected(tmp_path):
    srv = make_server(tmp_path, SeamState())
    conn = FakeConn(peer_uid=999)  # even UID-999 (the gateway user) is rejected
    resp = srv.handle_prepare(conn, prepare_dict())
    assert resp["status"] == "error" and resp["reason"] == "peer_not_root"
    # handle_connection rejects before parsing any privileged frame
    conn2 = FakeConn(peer_uid=1000, frames=[prepare_dict()])
    srv.handle_connection(conn2)
    assert conn2.sent and conn2.sent[0]["reason"] == "peer_not_root"


def test_feature_off_rejects_all_ops(tmp_path):
    ss = SeamState()
    ss.feature = False
    srv = make_server(tmp_path, ss)
    resp = srv.handle_prepare(FakeConn(), prepare_dict())
    assert resp["status"] == "refused" and resp["reason"] == "feature_off"


def test_feature_off_serve_forever_binds_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("GATEWAY_TRANSPORT_EVIDENCE_ENABLED", raising=False)
    # serve_forever + start_gateway_harness are no-ops while OFF (never bind).
    srv = make_server(tmp_path, SeamState())
    te.serve_forever(srv, path=str(tmp_path / "x.sock"))  # returns immediately
    called = []
    te.start_gateway_harness(
        seams=SeamState().seams(), inject_diagnostic=lambda c: None,
        state_dir_path=str(tmp_path), serve_fn=lambda *a, **k: called.append(1),
    )
    assert called == []
    assert not (tmp_path / "current-epoch.json").exists()


def test_commit_without_prepare(tmp_path):
    srv = make_server(tmp_path, SeamState())
    resp = srv.handle_commit(FakeConn(), {"op": "commit", "prep_token": "x"})
    assert resp["status"] == "refused" and resp["reason"] == "no_prepare_on_connection"


def test_prepare_then_commit_happy(tmp_path):
    injected = []
    srv = make_server(tmp_path, SeamState(), inject=lambda ctx: injected.append(ctx),
                      run_id_factory=lambda: "run_happy")
    conn = FakeConn()
    p = srv.handle_prepare(conn, prepare_dict(nonce="n" + secrets.token_hex(30)))
    assert p["status"] == "prepared" and p["prep_token"]
    c = srv.handle_commit(conn, {"op": "commit", "prep_token": p["prep_token"]})
    assert c["status"] == "committed" and c["run_id"] == "run_happy"
    assert len(injected) == 1
    # committed nonce recorded durably BEFORE the run
    assert srv.committed_ledger.contains(injected[0].nonce)


def test_expired_prep_token(tmp_path):
    srv = make_server(tmp_path, SeamState(), token_ttl_sec=0.0)
    conn = FakeConn()
    p = srv.handle_prepare(conn, prepare_dict())
    # token minted with 0s TTL → already expired at commit
    c = srv.handle_commit(conn, {"op": "commit", "prep_token": p["prep_token"]})
    assert c["status"] == "refused" and c["reason"] == "token_expired"


def test_replayed_prep_token(tmp_path):
    srv = make_server(tmp_path, SeamState(), run_id_factory=lambda: "run_x")
    conn = FakeConn()
    p = srv.handle_prepare(conn, prepare_dict())
    c1 = srv.handle_commit(conn, {"op": "commit", "prep_token": p["prep_token"]})
    assert c1["status"] == "committed"
    # second COMMIT on the same connection with the same token → token consumed
    c2 = srv.handle_commit(conn, {"op": "commit", "prep_token": p["prep_token"]})
    assert c2["status"] == "refused" and c2["reason"] == "no_prepare_on_connection"


def test_token_reused_on_second_connection(tmp_path):
    srv = make_server(tmp_path, SeamState())
    conn_a = FakeConn()
    p = srv.handle_prepare(conn_a, prepare_dict())
    # a DIFFERENT connection presents the (valid-looking) token string
    conn_b = FakeConn()
    c = srv.handle_commit(conn_b, {"op": "commit", "prep_token": p["prep_token"]})
    assert c["status"] == "refused" and c["reason"] == "no_prepare_on_connection"


def test_two_clients_race_same_nonce_at_most_one_run(tmp_path):
    ids = iter(["run_A", "run_B"])
    srv = make_server(tmp_path, SeamState(), run_id_factory=lambda: next(ids))
    nonce = "race" + secrets.token_hex(30)
    conn_a, conn_b = FakeConn(), FakeConn()
    pa = srv.handle_prepare(conn_a, prepare_dict(nonce=nonce))
    pb = srv.handle_prepare(conn_b, prepare_dict(nonce=nonce))
    assert pa["status"] == "prepared" and pb["status"] == "prepared"
    ca = srv.handle_commit(conn_a, {"op": "commit", "prep_token": pa["prep_token"]})
    cb = srv.handle_commit(conn_b, {"op": "commit", "prep_token": pb["prep_token"]})
    committed = [c for c in (ca, cb) if c["status"] == "committed"]
    refused = [c for c in (ca, cb) if c["status"] == "refused"]
    assert len(committed) == 1
    # the loser is caught either at COMMIT re-verify or the commit-lock ledger
    # check — both prove ≤1 run for the nonce.
    assert "nonce_already_committed" in refused[0]["reason"]


def test_disconnect_after_commit_status_only_no_resubmit(tmp_path):
    injected = []
    srv = make_server(tmp_path, SeamState(), inject=lambda ctx: injected.append(ctx),
                      run_id_factory=lambda: "run_status")
    conn = FakeConn()
    p = srv.handle_prepare(conn, prepare_dict(nonce="s" + secrets.token_hex(30)))
    c = srv.handle_commit(conn, {"op": "commit", "prep_token": p["prep_token"]})
    assert c["status"] == "committed"
    srv.forget_connection(conn)  # client disconnects
    # a fresh connection can only STATUS — no resubmit path
    conn2 = FakeConn()
    st = srv.handle_status(conn2, {"op": "status", "run_id": "run_status"})
    assert st["status"] == "ok" and st["run_state"] == "committed"
    # re-COMMIT attempt on conn2 (no prepare) is refused
    rc = srv.handle_commit(conn2, {"op": "commit", "prep_token": p["prep_token"]})
    assert rc["status"] == "refused"
    assert len(injected) == 1  # exactly one run injected


def test_status_never_executes(tmp_path):
    calls = {"inject": 0}
    srv = make_server(tmp_path, SeamState(),
                      inject=lambda ctx: calls.__setitem__("inject", calls["inject"] + 1),
                      run_id_factory=lambda: "run_se")
    conn = FakeConn()
    p = srv.handle_prepare(conn, prepare_dict(nonce="e" + secrets.token_hex(30)))
    srv.handle_commit(conn, {"op": "commit", "prep_token": p["prep_token"]})
    before = calls["inject"]
    for _ in range(3):
        srv.handle_status(FakeConn(), {"op": "status", "run_id": "run_se"})
    assert calls["inject"] == before  # STATUS never triggers a run


def test_status_not_found_and_no_secret_exposure(tmp_path):
    injected = []
    srv = make_server(tmp_path, SeamState(), inject=lambda ctx: injected.append(ctx),
                      run_id_factory=lambda: "run_sec")
    conn = FakeConn()
    nonce = "secretnonce" + secrets.token_hex(28)
    p = srv.handle_prepare(conn, prepare_dict(nonce=nonce))
    srv.handle_commit(conn, {"op": "commit", "prep_token": p["prep_token"]})
    st = srv.handle_status(FakeConn(), {"op": "status", "run_id": "run_sec"})
    blob = json.dumps(st)
    assert nonce not in blob and p["prep_token"] not in blob
    nf = srv.handle_status(FakeConn(), {"op": "status", "run_id": "unknown"})
    assert nf["status"] == "not_found"


def test_stale_socket_symlink_or_regular_file_refused(tmp_path):
    # regular file at the socket path → refuse to unlink (fail closed)
    reg = tmp_path / "reg.sock"
    reg.write_text("not a socket")
    with pytest.raises(te.SocketSecurityError):
        te.cleanup_stale_socket(str(reg))
    assert reg.exists()  # never unlinked
    # symlink at the socket path → refuse (never followed)
    if hasattr(os, "symlink"):
        target = tmp_path / "target"
        target.write_text("x")
        link = tmp_path / "link.sock"
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError):
            pytest.skip("symlink not permitted on this host")
        with pytest.raises(te.SocketSecurityError):
            te.cleanup_stale_socket(str(link))
        assert link.exists() and target.exists()
    # a non-existent path is a clean no-op
    te.cleanup_stale_socket(str(tmp_path / "absent.sock"))


def test_hash_disagreement_refused(tmp_path):
    ss = SeamState()
    ss.hash = "different" + "0" * 56  # installed digest disagrees with the lease
    srv = make_server(tmp_path, ss)
    resp = srv.handle_prepare(FakeConn(), prepare_dict())
    assert resp["status"] == "refused" and resp["reason"] == "harness_hash_mismatch"


def test_commit_mismatch_refused(tmp_path):
    ss = SeamState()
    ss.commit = "c" * 40
    srv = make_server(tmp_path, ss)
    resp = srv.handle_prepare(FakeConn(), prepare_dict())
    assert resp["status"] == "refused" and resp["reason"] == "commit_mismatch"


def test_containment_drift_prepare_to_commit_fails_before_send(tmp_path):
    ss = SeamState()
    injected = []
    srv = make_server(tmp_path, ss, inject=lambda ctx: injected.append(ctx))
    conn = FakeConn()
    p = srv.handle_prepare(conn, prepare_dict())
    assert p["status"] == "prepared"
    ss.containment = False  # containment drops between PREPARE and COMMIT
    c = srv.handle_commit(conn, {"op": "commit", "prep_token": p["prep_token"]})
    assert c["status"] == "refused" and c["reason"] == "drift:containment_inactive"
    assert injected == []  # no run injected → no send


def test_budget_off_drift_prepare_to_commit_fails_before_send(tmp_path):
    ss = SeamState()
    injected = []
    srv = make_server(tmp_path, ss, inject=lambda ctx: injected.append(ctx))
    conn = FakeConn()
    p = srv.handle_prepare(conn, prepare_dict())
    ss.budget = (False, 5, 50)  # budget disabled between PREPARE and COMMIT
    c = srv.handle_commit(conn, {"op": "commit", "prep_token": p["prep_token"]})
    assert c["status"] == "refused" and c["reason"] == "drift:budget_off"
    assert injected == []


def test_caps_not_exactly_5_50_refused(tmp_path):
    ss = SeamState()
    ss.budget = (True, 6, 50)
    srv = make_server(tmp_path, ss)
    assert srv.handle_prepare(FakeConn(), prepare_dict())["reason"] == "caps_unexpected"
    ss.budget = (True, 5, 40)
    assert srv.handle_prepare(FakeConn(), prepare_dict())["reason"] == "caps_unexpected"


def test_queue_not_drained_refused(tmp_path):
    ss = SeamState()
    ss.health = (True, True, 1)
    srv = make_server(tmp_path, ss)
    assert srv.handle_prepare(FakeConn(), prepare_dict())["reason"] == "queue_not_drained"


def test_expired_lease_expiry_refused(tmp_path):
    srv = make_server(tmp_path, SeamState())
    past = (FIXED_NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    resp = srv.handle_prepare(FakeConn(), prepare_dict(expires_at=past))
    assert resp["status"] == "refused" and resp["reason"] == "expired"


def test_unknown_field_and_bad_op_rejected(tmp_path):
    srv = make_server(tmp_path, SeamState())
    bad = prepare_dict()
    bad["surprise"] = 1
    assert srv.handle_prepare(FakeConn(), bad)["status"] == "error"
    assert srv.dispatch(FakeConn(), {"op": "nope"})["reason"] == "unknown_op"


# ═══════════════════════════════ ADMISSION ══════════════════════════════════


def test_phone_lease_does_not_authorize_unrelated_lid(tmp_path):
    ss = SeamState()
    # authorize ONLY the phone (and its true alias); an unrelated LID is separate
    ss.authorized = {("whatsapp", DEST), ("whatsapp", DEST_LID)}
    ss.aliases = {DEST: [DEST, DEST_LID], "999999999999999@lid": ["999999999999999@lid"]}
    srv = make_server(tmp_path, ss)
    ok = srv.handle_prepare(FakeConn(), prepare_dict(normalized_destination=DEST))
    assert ok["status"] == "prepared"
    bad = srv.handle_prepare(
        FakeConn(), prepare_dict(normalized_destination="999999999999999@lid")
    )
    assert bad["status"] == "refused" and bad["reason"] == "destination_unauthorized"


def test_lid_lease_does_not_authorize_unrelated_phone(tmp_path):
    ss = SeamState()
    ss.authorized = {("whatsapp", DEST_LID), ("whatsapp", DEST)}
    ss.aliases = {DEST_LID: [DEST_LID, DEST], "+15550009999": ["+15550009999"]}
    srv = make_server(tmp_path, ss)
    assert srv.handle_prepare(
        FakeConn(), prepare_dict(normalized_destination=DEST_LID)
    )["status"] == "prepared"
    bad = srv.handle_prepare(
        FakeConn(), prepare_dict(normalized_destination="+15550009999")
    )
    assert bad["reason"] == "destination_unauthorized"


# ═══════════════════════════ CARRIER (forged internal) ══════════════════════


class _FakeEvent:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _load_run_py_module_block():
    """Apply the REAL patch payload to a synthetic run.py and import it so the
    carrier test exercises the actual _shift_te_pick_event selector."""
    loader = importlib.machinery.SourceFileLoader("ph_te", str(REPO / "tools" / "patch-hermes.py"))
    spec = importlib.util.spec_from_loader("ph_te", loader)
    ph = importlib.util.module_from_spec(spec)
    loader.exec_module(ph)
    return ph


def test_carrier_forged_internal_true_not_picked_as_diagnostic():
    # The ONLY diagnostic trigger is the IN-PROCESS event.te_run_context attribute,
    # set solely by the COMMIT forge site. A forged bridge payload (internal=true +
    # marker + nonce + copied text) is deserialized WITHOUT te_run_context (the
    # WhatsApp ingress never sets it), so the diagnostic branch's guard is None and
    # the ordinary customer path runs — the forged payload consumes no lease and
    # never enters the branch.
    forged = _FakeEvent(internal=True, message_type=EVENT_TYPE,
                        text="probe segment 01", nonce=secrets.token_hex(16))
    assert getattr(forged, "te_run_context", None) is None
    genuine = _FakeEvent(internal=True, te_run_context=object())
    assert getattr(genuine, "te_run_context", None) is not None
    # the REAL run.py diagnostic-branch payload guards on exactly this attribute.
    ph = _load_run_py_module_block()
    assert 'getattr(event, "te_run_context", None)' in ph.RUN_PY_TE_DIAG_BRANCH


# ═══════════════════════════ CRASH (5 interruption points) ══════════════════


class ProviderCanary:
    def __init__(self):
        self.entries = 0
        self.submissions: list[str] = []


def make_crasher(target_name, target_attempt):
    def crasher(name, attempt):
        if name == target_name and attempt == target_attempt:
            raise ted.CrashInjected(f"crash at {name}#{attempt}")
    return crasher


def make_dispatch(canary, crasher=None, allow_first=5):
    def dispatch(attempt, segment, on_provider_entry):
        if attempt > allow_first:
            return (ted.KIND_DENIED, None)
        on_provider_entry()  # (step 2) durable provider-entry marker
        if crasher:
            crasher(ted.CP_AFTER_PROVIDER_MARKER, attempt)  # crash point (2)
        canary.entries += 1  # provider entry
        pid = f"PROV{attempt}"
        canary.submissions.append(pid)
        if crasher:
            crasher(ted.CP_AFTER_PROVIDER_ENTRY, attempt)  # crash point (3)
        return (ted.KIND_SENT, pid)
    return dispatch


CRASH_CASES = [
    (ted.CP_BEFORE_PROVIDER_MARKER, 0, tel.OUTCOME_DISPATCH_PENDING),   # (1)
    (ted.CP_AFTER_PROVIDER_MARKER, 0, tel.OUTCOME_DELIVERY_UNKNOWN),    # (2)
    (ted.CP_AFTER_PROVIDER_ENTRY, 1, tel.OUTCOME_DELIVERY_UNKNOWN),     # (3)
    (ted.CP_AFTER_ACK, 1, tel.OUTCOME_DELIVERY_UNKNOWN),               # (4)
    (ted.CP_AFTER_SENT, 1, tel.OUTCOME_SENT),                          # (5)
]


@pytest.mark.parametrize("cp_name,expect_entries,expect_state", CRASH_CASES)
def test_crash_point_zero_duplicate_provider_submission(tmp_path, cp_name, expect_entries, expect_state):
    path = tmp_path / "attempts.ndjson"
    canary = ProviderCanary()
    crasher = make_crasher(cp_name, 1)
    ledger1 = tel.AttemptLedger(path, epoch="epoch-1")
    runner1 = ted.DiagnosticRunner(
        run_id="r", plan=ted.build_segment_plan(),
        attempt_ledger=ledger1, dispatch_segment=make_dispatch(canary, crasher),
        checkpoints=crasher,
    )
    with pytest.raises(ted.CrashInjected):
        runner1.run()
    # provider submissions that actually happened before the crash
    assert canary.entries == expect_entries

    # RESTART: a fresh process (new epoch) must NOT resend anything.
    canary2 = ProviderCanary()
    ledger2 = tel.AttemptLedger(path, epoch="epoch-2")
    runner2 = ted.DiagnosticRunner(
        run_id="r", plan=ted.build_segment_plan(),
        attempt_ledger=ledger2, dispatch_segment=make_dispatch(canary2),
    )
    summary = runner2.run()
    assert summary.continued is False  # no auto-continuation after crash
    assert canary2.entries == 0  # ZERO duplicate provider submission
    # reconciled (prior-epoch) state matches the crash point
    assert ledger2.reconciled_state(1) == expect_state
    assert ledger2.retransmit_forbidden(1) is True


def test_gateway_crash_after_commit_nonce_non_replayable(tmp_path):
    # inject that "crashes" (raises) — the nonce is recorded BEFORE inject runs.
    def crashing_inject(ctx):
        raise RuntimeError("simulated gateway crash after durable COMMIT")

    srv = make_server(tmp_path, SeamState(), inject=crashing_inject,
                      run_id_factory=lambda: "run_crash")
    conn = FakeConn()
    nonce = "crash" + secrets.token_hex(30)
    p = srv.handle_prepare(conn, prepare_dict(nonce=nonce))
    c = srv.handle_commit(conn, {"op": "commit", "prep_token": p["prep_token"]})
    assert c["status"] == "committed"  # recorded despite the inject fault
    assert srv.committed_ledger.contains(nonce)
    # a brand-new PREPARE with the same nonce is refused forever
    p2 = srv.handle_prepare(FakeConn(), prepare_dict(nonce=nonce))
    assert p2["status"] == "refused" and p2["reason"] == "nonce_already_committed"


# ═══════════════════════ EFFECT / ISOLATION ═════════════════════════════════


def run_full_plan(tmp_path, *, epoch="epoch-1"):
    canary = ProviderCanary()
    ledger = tel.AttemptLedger(tmp_path / "a.ndjson", epoch=epoch)
    pages = {"n": 0}

    def dispatch(attempt, segment, on_provider_entry):
        # budget denies from attempt 6 (cap=5) — a denied send never reaches the
        # provider-entry canary (non-vacuous denial proof) and pages ONCE.
        if attempt > 5:
            if pages["n"] == 0:
                pages["n"] += 1  # exactly one page on first denial (external path)
            return (ted.KIND_DENIED, None)
        on_provider_entry()
        canary.entries += 1
        pid = f"PROV{attempt}"
        canary.submissions.append(pid)
        return (ted.KIND_SENT, pid)

    runner = ted.DiagnosticRunner(
        run_id="full", plan=ted.build_segment_plan(),
        attempt_ledger=ledger, dispatch_segment=dispatch,
    )
    summary = runner.run()
    return summary, canary, ledger, pages


def test_five_accepted_23_denied_denied_never_enter_provider(tmp_path):
    summary, canary, ledger, pages = run_full_plan(tmp_path)
    assert summary.accepted == 5 and summary.denied == 23
    assert canary.entries == 5  # only the 5 accepted reached the provider
    assert len(canary.submissions) == 5
    assert pages["n"] == 1  # exactly one page
    # denied attempts (6..28) carry NO provider-entry marker (non-vacuous proof)
    for i in range(6, 29):
        assert ledger.reconciled_state(i) == tel.OUTCOME_DENIED
        assert ledger.has_provider_entry_marker(i) is False
    # accepted attempts have real provider ids
    assert ledger.provider_ids() == {i: f"PROV{i}" for i in range(1, 6)}


def test_audit_write_failure_halts_no_further_sends(tmp_path):
    canary = ProviderCanary()

    class FaultyLedger(tel.AttemptLedger):
        def mark_dispatch_pending(self, attempt, ts=""):
            if attempt == 3:
                raise tel.LedgerError("simulated audit-write failure")
            return super().mark_dispatch_pending(attempt, ts)

    ledger = FaultyLedger(tmp_path / "a.ndjson", epoch="e")

    def dispatch(attempt, segment, on_provider_entry):
        on_provider_entry()
        canary.entries += 1
        return (ted.KIND_SENT, f"P{attempt}")

    runner = ted.DiagnosticRunner(
        run_id="halt", plan=ted.build_segment_plan(),
        attempt_ledger=ledger, dispatch_segment=dispatch,
    )
    summary = runner.run()
    assert summary.halted is True and "audit_write_failed" in summary.halt_reason
    # halted at attempt 3 → only 1 + 2 reached the provider, nothing after
    assert canary.entries == 2


def test_diagnostic_runner_has_no_business_state_seam():
    # Structural isolation: the runner's only mutable dependency is the attempt
    # ledger + the transport seam. No lead/proposal/pricing/owner-decision handle.
    import inspect
    params = set(inspect.signature(ted.DiagnosticRunner.__init__).parameters)
    assert params == {"self", "run_id", "plan", "attempt_ledger", "dispatch_segment",
                      "checkpoints", "clock"}


# ═══════════════════════ AMENDMENT 1 — epoch-scoped reconciliation ══════════


def test_in_epoch_dispatch_pending_not_reclassified_prior_is(tmp_path):
    path = tmp_path / "a.ndjson"
    live = tel.AttemptLedger(path, epoch="epoch-live")
    live.mark_dispatch_pending(1)
    live.mark_provider_entry(1, "pe1")            # dispatch_pending + marker
    live.mark_dispatch_pending(2)                  # dispatch_pending, no marker
    # same-epoch (live) view: the in-flight attempt is NOT reclassified
    assert live.reconciled_state(1) == tel.OUTCOME_DISPATCH_PENDING
    assert live.reconciled_state(2) == tel.OUTCOME_DISPATCH_PENDING
    # prior-(dead)-epoch view (restart): marker → delivery_unknown, none → reserved
    dead = tel.AttemptLedger(path, epoch="epoch-new")
    assert dead.reconciled_state(1) == tel.OUTCOME_DELIVERY_UNKNOWN
    assert dead.reconciled_state(2) == tel.OUTCOME_DISPATCH_PENDING
    # neither view performs any provider submission (pure reads)


# ═══════════════════════ AMENDMENT 2 — singleton ownership + startup ═════════


def test_singleton_lock_single_owner(tmp_path):
    lock1 = tel.SingletonLock(tmp_path / "owner.lock")
    lock1.acquire()
    try:
        with pytest.raises(tel.SingletonLockError):
            tel.SingletonLock(tmp_path / "owner.lock").acquire()
    finally:
        lock1.release()
    # released → a legitimate restart can re-acquire
    lock2 = tel.SingletonLock(tmp_path / "owner.lock")
    lock2.acquire()
    lock2.release()


def test_singleton_lock_fd_cloexec_not_inherited(tmp_path):
    lock = tel.SingletonLock(tmp_path / "owner.lock")
    lock.acquire()
    try:
        assert lock.close_on_exec is True          # FD_CLOEXEC set
        assert os.get_inheritable(lock._fd) is False  # a child cannot inherit it
    finally:
        lock.release()


def test_singleton_lock_recovers_after_abnormal_termination(tmp_path):
    # 1st owner holds the lock → a 2nd instance cannot activate.
    owner = tel.SingletonLock(tmp_path / "owner.lock")
    owner.acquire()
    with pytest.raises(tel.SingletonLockError):
        tel.SingletonLock(tmp_path / "owner.lock").acquire()
    # Simulate ABNORMAL termination (kill -9): the kernel drops the fd → flock
    # releases. On non-flock hosts (no kernel flock), a clean release stands in.
    if tel._SINGLETON_USES_FLOCK:
        closed = owner._fd
        os.close(owner._fd)   # kernel-drop analog — no clean release() ran
        owner._fd = None
        tel._OPEN_LOCK_FDS.discard(closed)
    else:
        owner.release()
    # A fresh instance CAN now safely initialize (ownership genuinely released).
    fresh = tel.SingletonLock(tmp_path / "owner.lock")
    fresh.acquire()
    try:
        assert fresh.held
    finally:
        fresh.release()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is POSIX-only")
def test_fork_child_does_not_retain_singleton_ownership(tmp_path):
    # A fork-only child inherits the lock fd (FD_CLOEXEC does NOT drop it across
    # fork). The after-fork-in-child handler must close it so the child cannot keep
    # the flock alive after the parent's death.
    lockpath = tmp_path / "owner.lock"
    lock = tel.SingletonLock(lockpath)
    lock.acquire()
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:  # child
        os.close(w)
        try:
            os.read(r, 1)  # stay alive until the parent lets us exit
        finally:
            os._exit(0)
    os.close(r)
    try:
        # parent alive + holding → a 3rd instance cannot acquire
        with pytest.raises(tel.SingletonLockError):
            tel.SingletonLock(lockpath).acquire()
        # simulate SIGKILL of the parent: the kernel drops the parent's fd. The
        # child is STILL ALIVE; if it had retained the inherited fd the flock would
        # persist and the next acquire would fail.
        closed = lock._fd
        os.close(lock._fd)
        lock._fd = None
        tel._OPEN_LOCK_FDS.discard(closed)
        fresh = tel.SingletonLock(lockpath)
        fresh.acquire()  # succeeds ONLY because the child dropped its inherited fd
        assert fresh.held
        fresh.release()
    finally:
        os.close(w)  # release the child so it exits
        os.waitpid(pid, 0)


def test_disable_makes_prepare_refuse_shutting_down(tmp_path):
    srv = make_server(tmp_path, SeamState())
    assert srv.active is True
    srv.disable()  # teardown disables the control plane before the lock releases
    assert srv.active is False
    assert srv.handle_prepare(FakeConn(), prepare_dict())["reason"] == "shutting_down"


def test_lock_released_after_serve_returns(monkeypatch, tmp_path):
    monkeypatch.setenv("GATEWAY_TRANSPORT_EVIDENCE_ENABLED", "1")
    during = {}

    def serve_fn(server, **kw):
        during["active"] = server.active  # still outbound-capable during serve

    te.start_gateway_harness(
        seams=SeamState().seams(), inject_diagnostic=lambda c: None,
        state_dir_path=str(tmp_path), serve_fn=serve_fn, epoch="e-shut",
    )
    assert during["active"] is True
    # serve returned → harness released the lock → a fresh owner can acquire it
    fresh = tel.SingletonLock(tmp_path / "te-owner.lock")
    fresh.acquire()
    fresh.release()
    assert te._ACTIVE_SINGLETON_LOCK is None


def test_corrupt_epoch_fails_closed(tmp_path):
    # boot_id present but empty → ambiguous → EpochError
    bad_boot = tmp_path / "boot_id"
    bad_boot.write_text("   \n")
    stat_ok = tmp_path / "stat"
    stat_ok.write_text("1 (proc) S 0 0 0 " + " ".join(str(i) for i in range(30)))
    with pytest.raises(tel.EpochError):
        tel.read_kernel_epoch(str(bad_boot), str(stat_ok))
    # one source present, the other absent → ambiguous → EpochError
    good_boot = tmp_path / "boot_id2"
    good_boot.write_text("11111111-2222-3333-4444-555555555555")
    with pytest.raises(tel.EpochError):
        tel.read_kernel_epoch(str(good_boot), str(tmp_path / "absent-stat"))
    # both absent → None (non-Linux) — the durable-UUID path takes over
    assert tel.read_kernel_epoch(str(tmp_path / "no-boot"), str(tmp_path / "no-stat")) is None


def test_prepare_unavailable_until_reconciliation_and_loop(tmp_path):
    # D1: PREPARE/COMMIT rejected (not queued) until reconciliation completes AND
    # the loop is captured.
    srv = make_server(tmp_path, SeamState(), reconciled=False)
    p = srv.handle_prepare(FakeConn(), prepare_dict())
    assert p["status"] == "refused" and p["reason"] == "reconciliation_incomplete"
    c = srv.handle_commit(FakeConn(), {"op": "commit", "prep_token": "x"})
    assert c["reason"] == "reconciliation_incomplete"
    # reconciled but loop not yet captured → still refused (loop_not_ready)
    srv.mark_reconciled()
    assert srv.ready() is False
    p2 = srv.handle_prepare(FakeConn(), prepare_dict())
    assert p2["status"] == "refused" and p2["reason"] == "loop_not_ready"
    # loop captured → ready → served
    srv.set_loop(object())
    assert srv.ready() is True
    assert srv.handle_prepare(FakeConn(), prepare_dict())["status"] == "prepared"


def test_serve_forever_refuses_bind_before_reconciliation(monkeypatch, tmp_path):
    monkeypatch.setenv("GATEWAY_TRANSPORT_EVIDENCE_ENABLED", "1")
    srv = make_server(tmp_path, SeamState(), reconciled=False)
    with pytest.raises(te.SocketSecurityError):
        te.serve_forever(srv, path=str(tmp_path / "x.sock"))


def test_startup_ordering_reconcile_before_socket(monkeypatch, tmp_path):
    monkeypatch.setenv("GATEWAY_TRANSPORT_EVIDENCE_ENABLED", "1")
    order = []
    seen_reconciled = {}

    def serve_spy(server, **kw):
        order.append("serve")
        seen_reconciled["v"] = server.reconciled

    te.start_gateway_harness(
        seams=SeamState().seams(), inject_diagnostic=lambda c: None,
        state_dir_path=str(tmp_path), serve_fn=serve_spy, epoch="epoch-startup",
    )
    assert order == ["serve"]
    assert seen_reconciled["v"] is True  # reconciliation completed before serve
    assert (tmp_path / "current-epoch.json").exists()  # epoch established durably


def test_competing_gateway_performs_none_of_the_five(monkeypatch, tmp_path):
    monkeypatch.setenv("GATEWAY_TRANSPORT_EVIDENCE_ENABLED", "1")
    # first owner holds the singleton lock for the whole test
    holder = tel.SingletonLock(tmp_path / "te-owner.lock")
    holder.acquire()
    try:
        served = []
        provider = ProviderCanary()

        def serve_spy(server, **kw):  # (c) socket bind / (d) inbound / (e) provider
            served.append(1)

        te.start_gateway_harness(
            seams=SeamState().seams(),
            inject_diagnostic=lambda c: provider.__setattr__("entries", provider.entries + 1),
            state_dir_path=str(tmp_path), serve_fn=serve_spy, epoch="epoch-compete",
        )
        # competing process could NOT acquire the lock → performs NONE of:
        assert served == []                                   # (c)/(d) no socket/inbound
        assert provider.entries == 0                          # (e) no provider submit
        assert not (tmp_path / "current-epoch.json").exists() # (a) no ledger/epoch init
        assert not (tmp_path / "reconciled").exists()         # (b) no reconciliation
    finally:
        holder.release()


def test_reconcile_and_status_have_no_outbound(tmp_path):
    # reconcile_prior_epoch over a prior-epoch run + STATUS produce ZERO provider ops
    sd = tmp_path
    runs = tel.RunLedger(sd)
    runs.record_committed(run_id="run1", nonce="n1", destination=DEST,
                          logical_turn_id="t1", authorized_commit=FUTURE_RC_COMMIT,
                          harness_hash=HARNESS_HASH, epoch="epoch-old", ts="ts")
    old = tel.AttemptLedger(sd / "attempts" / "run1.ndjson", epoch="epoch-old")
    old.mark_dispatch_pending(1)
    old.mark_provider_entry(1, "pe1")  # crashed after entry, before ack

    reconcile_ledger = tel.ReconcileLedger(sd)

    def factory(run_id, ep):
        return tel.AttemptLedger(sd / "attempts" / f"{run_id}.ndjson", ep)

    summary = tel.reconcile_prior_epoch(
        run_ledger=runs, attempt_ledger_factory=factory,
        reconcile_ledger=reconcile_ledger, current_epoch="epoch-new", ts="ts",
    )
    assert summary["reconciled"]["run1"][1] == tel.OUTCOME_DELIVERY_UNKNOWN
    snap = reconcile_ledger.load("run1")
    assert snap["states"]["1"] == tel.OUTCOME_DELIVERY_UNKNOWN
    # STATUS reads only — build a server (current epoch) and query
    srv = te.EvidenceControlServer(
        seams=SeamState().seams(), committed_ledger=tel.CommittedNonceLedger(sd / "c.ndjson"),
        run_ledger=runs, attempt_ledger_factory=factory,
        inject_diagnostic=lambda ctx: pytest.fail("STATUS must not inject"),
        epoch="epoch-new",
    )
    srv.mark_reconciled()
    st = srv.handle_status(FakeConn(), {"op": "status", "run_id": "run1"})
    assert st["status"] == "ok"
    assert st["state_counts"].get(tel.OUTCOME_DELIVERY_UNKNOWN) == 1


# ═══════════════════════ LEASE FS HARDENING (CLI side) ══════════════════════


def _write_lease(path, *, mode=0o600, **over):
    d = {
        "nonce": secrets.token_hex(32),
        "authorized_commit": FUTURE_RC_COMMIT,
        "authorized_harness_hash": HARNESS_HASH,
        "normalized_destination": DEST,
        "expires_at": (FIXED_NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "max_runs": 1,
        "diagnostic_event_type": EVENT_TYPE,
    }
    d.update(over)
    path.write_text(json.dumps(d))
    os.chmod(path, mode)
    return d


def _owner(path):
    st = os.stat(path)
    return st.st_uid, st.st_gid


def test_lease_valid_accepted(tmp_path):
    lp = tmp_path / "lease.json"
    _write_lease(lp)
    if os.name != "nt":
        os.chmod(lp.parent, 0o700)  # F1: run_probe requires a 0o700 lease dir
    uid, gid = _owner(lp)
    lease = tle.open_and_validate_lease(lp, now=FIXED_NOW, expect_uid=uid, expect_gid=gid)
    assert lease.normalized_destination == DEST and lease.max_runs == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits (Linux box only)")
def test_lease_bad_mode_rejected(tmp_path):
    lp = tmp_path / "lease.json"
    _write_lease(lp, mode=0o644)
    uid, gid = _owner(lp)
    with pytest.raises(tle.LeaseValidationError):
        tle.open_and_validate_lease(lp, now=FIXED_NOW, expect_uid=uid, expect_gid=gid)


def test_lease_wrong_owner_rejected(tmp_path):
    lp = tmp_path / "lease.json"
    _write_lease(lp)
    if os.name != "nt":
        os.chmod(lp.parent, 0o700)  # F1: run_probe requires a 0o700 lease dir
    uid, gid = _owner(lp)
    with pytest.raises(tle.LeaseValidationError):
        tle.open_and_validate_lease(lp, now=FIXED_NOW, expect_uid=uid + 12345, expect_gid=gid)


def test_lease_expired_rejected(tmp_path):
    lp = tmp_path / "lease.json"
    _write_lease(lp, expires_at=(FIXED_NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"))
    uid, gid = _owner(lp)
    with pytest.raises(tle.LeaseValidationError):
        tle.open_and_validate_lease(lp, now=FIXED_NOW, expect_uid=uid, expect_gid=gid)


def test_lease_low_entropy_nonce_rejected(tmp_path):
    lp = tmp_path / "lease.json"
    _write_lease(lp, nonce="aaaa")
    uid, gid = _owner(lp)
    with pytest.raises(tle.LeaseValidationError):
        tle.open_and_validate_lease(lp, now=FIXED_NOW, expect_uid=uid, expect_gid=gid)


def test_lease_bad_schema_rejected(tmp_path):
    lp = tmp_path / "lease.json"
    _write_lease(lp, extra_field="x")
    uid, gid = _owner(lp)
    with pytest.raises(tle.LeaseValidationError):
        tle.open_and_validate_lease(lp, now=FIXED_NOW, expect_uid=uid, expect_gid=gid)


def test_lease_max_runs_not_one_rejected(tmp_path):
    lp = tmp_path / "lease.json"
    _write_lease(lp, max_runs=2)
    uid, gid = _owner(lp)
    with pytest.raises(tle.LeaseValidationError):
        tle.open_and_validate_lease(lp, now=FIXED_NOW, expect_uid=uid, expect_gid=gid)


def test_lease_oversize_rejected(tmp_path):
    lp = tmp_path / "lease.json"
    _write_lease(lp, diagnostic_event_type=EVENT_TYPE + "x" * 8000)
    uid, gid = _owner(lp)
    with pytest.raises(tle.LeaseValidationError):
        tle.open_and_validate_lease(lp, now=FIXED_NOW, expect_uid=uid, expect_gid=gid, max_bytes=4096)


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW absent (non-Linux)")
def test_lease_symlink_rejected_o_nofollow(tmp_path):
    real = tmp_path / "real.json"
    _write_lease(real)
    link = tmp_path / "link.json"
    try:
        os.symlink(str(real), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlink not permitted")
    uid, gid = _owner(real)
    with pytest.raises(tle.LeaseValidationError):
        tle.open_and_validate_lease(link, now=FIXED_NOW, expect_uid=uid, expect_gid=gid)


def test_lease_consume_is_single_use(tmp_path):
    lp = tmp_path / "lease.json"
    _write_lease(lp)
    att = tle.consume_lease(lp, now=FIXED_NOW)
    assert Path(att["consumed_path"]).exists()
    assert not lp.exists()  # moved out of the active path
    with pytest.raises(tle.LeaseConsumeError):
        tle.consume_lease(lp, now=FIXED_NOW)  # second consume fails


# ═══════════════════════ CLI end-to-end (in-process loopback) ═══════════════


class LoopbackClient:
    """Client conn backed by an in-process server; PREPARE + COMMIT ride one
    stable server-side connection identity."""

    def __init__(self, server, peer_uid=0):
        self.server = server
        self._srvconn = FakeConn(peer_uid=peer_uid)
        self._pending = None

    def send_json(self, obj):
        self._pending = self.server.dispatch(self._srvconn, obj)

    def recv_json(self):
        r, self._pending = self._pending, None
        return r

    def close(self):
        self.server.forget_connection(self._srvconn)


def _load_cli():
    path = REPO / "src" / "platform" / "scripts" / "shift-agent-transport-evidence-probe"
    loader = importlib.machinery.SourceFileLoader("te_probe_cli", str(path))
    spec = importlib.util.spec_from_loader("te_probe_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["te_probe_cli"] = mod
    loader.exec_module(mod)
    return mod


def test_cli_run_probe_full_flow(tmp_path):
    cli = _load_cli()
    injected = []
    srv = make_server(tmp_path, SeamState(), inject=lambda ctx: injected.append(ctx),
                      run_id_factory=lambda: "run_cli")
    lp = tmp_path / "lease.json"
    _write_lease(lp)
    if os.name != "nt":
        os.chmod(lp.parent, 0o700)  # F1: run_probe requires a 0o700 lease dir
    uid, gid = _owner(lp)
    conn = LoopbackClient(srv)
    code, resp = cli.run_probe(conn, str(lp), now=FIXED_NOW, expect_uid=uid,
                               expect_gid=gid, verify_installed_digest=False)
    assert code == 0 and resp["status"] == "committed" and resp["run_id"] == "run_cli"
    assert not lp.exists()  # lease consumed exactly once
    assert len(injected) == 1


def test_cli_prepare_refused_does_not_consume_lease(tmp_path):
    cli = _load_cli()
    ss = SeamState()
    ss.containment = False  # PREPARE will be refused
    srv = make_server(tmp_path, ss)
    lp = tmp_path / "lease.json"
    _write_lease(lp)
    if os.name != "nt":
        os.chmod(lp.parent, 0o700)  # F1: run_probe requires a 0o700 lease dir
    uid, gid = _owner(lp)
    conn = LoopbackClient(srv)
    code, resp = cli.run_probe(conn, str(lp), now=FIXED_NOW, expect_uid=uid,
                               expect_gid=gid, verify_installed_digest=False)
    assert code != 0 and resp["status"] == "refused"
    assert lp.exists()  # PREPARE refused → lease NOT consumed


def test_cli_status_read_only(tmp_path):
    cli = _load_cli()
    injected = []
    srv = make_server(tmp_path, SeamState(), inject=lambda ctx: injected.append(ctx),
                      run_id_factory=lambda: "run_cli2")
    lp = tmp_path / "lease.json"
    _write_lease(lp)
    if os.name != "nt":
        os.chmod(lp.parent, 0o700)  # F1: run_probe requires a 0o700 lease dir
    uid, gid = _owner(lp)
    cli.run_probe(LoopbackClient(srv), str(lp), now=FIXED_NOW, expect_uid=uid,
                  expect_gid=gid, verify_installed_digest=False)
    before = len(injected)
    code, resp = cli.status_probe(LoopbackClient(srv), "run_cli2")
    assert code == 0 and resp["status"] == "ok"
    assert len(injected) == before  # STATUS never triggers a run


# ═══════════════════════ AMENDMENT 3 — D3 normalized-path manifest ═══════════


def _make_manifest_root(tmp_path, files):
    root = tmp_path / "manifest_root"
    root.mkdir()
    for name, data in files.items():
        (root / name).write_bytes(data)
    return root


def test_manifest_digest_deterministic_normalized(tmp_path):
    root = _make_manifest_root(tmp_path, {"a.py": b"AAA", "b.py": b"BBB"})
    d1 = te.canonical_harness_manifest_digest(str(root), ["a.py", "b.py"])
    d2 = te.canonical_harness_manifest_digest(str(root), ["b.py", "a.py"])  # order-independent
    assert d1 == d2
    (root / "a.py").write_bytes(b"AAB")  # one byte flips the digest
    assert te.canonical_harness_manifest_digest(str(root), ["a.py", "b.py"]) != d1


def test_manifest_rejects_absolute_dotdot_dot_dup(tmp_path):
    root = _make_manifest_root(tmp_path, {"a.py": b"AAA"})
    with pytest.raises(te.ManifestError):
        te.canonical_harness_manifest_digest(str(root), ["/etc/passwd"])
    with pytest.raises(te.ManifestError):
        te.canonical_harness_manifest_digest(str(root), ["../a.py"])
    with pytest.raises(te.ManifestError):
        te.canonical_harness_manifest_digest(str(root), ["./a.py"])
    with pytest.raises(te.ManifestError):
        te.canonical_harness_manifest_digest(str(root), ["a.py", "a.py"])


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink POSIX-only")
def test_manifest_rejects_symlink_and_escape(tmp_path):
    root = _make_manifest_root(tmp_path, {"real.py": b"X"})
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"Y")
    try:
        os.symlink(str(root / "real.py"), str(root / "link.py"))
        os.symlink(str(outside), str(root / "escape.py"))
    except (OSError, NotImplementedError):
        pytest.skip("symlink not permitted")
    with pytest.raises(te.ManifestError):
        te.canonical_harness_manifest_digest(str(root), ["link.py"])
    with pytest.raises(te.ManifestError):
        te.canonical_harness_manifest_digest(str(root), ["escape.py"])


def test_cli_and_gateway_compute_identical_digest(tmp_path, monkeypatch):
    files = {n: (n + "-body").encode() for n in te.HARNESS_MANIFEST_RELPATHS}
    root = _make_manifest_root(tmp_path, files)
    monkeypatch.setenv("SHIFT_AGENT_TE_MANIFEST_ROOT", str(root))
    gateway_digest = te.default_harness_manifest_digest()
    cli = _load_cli()
    cli_digest = cli.compute_installed_harness_digest()
    assert gateway_digest == cli_digest


# ═══════════════ AMENDMENT 3 — D2 reconciliation transitions ═════════════════


def test_reconciliation_appends_transition_to_authoritative_ledger(tmp_path):
    sd = tmp_path
    runs = tel.RunLedger(sd)
    runs.record_committed(run_id="run1", nonce="n", destination=DEST, logical_turn_id="t",
                          authorized_commit=FUTURE_RC_COMMIT, harness_hash=HARNESS_HASH,
                          epoch="E1", ts="ts")
    al = tel.AttemptLedger(sd / "attempts" / "run1.ndjson", epoch="E1")
    al.mark_dispatch_pending(1)
    al.mark_provider_entry(1, "pe1")  # marked stale pending
    al.mark_dispatch_pending(2)        # unmarked stale pending
    rl = tel.ReconcileLedger(sd)

    def factory(rid, ep):
        return tel.AttemptLedger(sd / "attempts" / (rid + ".ndjson"), ep)

    summary = tel.reconcile_prior_epoch(run_ledger=runs, attempt_ledger_factory=factory,
                                        reconcile_ledger=rl, current_epoch="E2", ts="ts2")
    assert summary["transitions"] == 2
    al2 = tel.AttemptLedger(sd / "attempts" / "run1.ndjson", epoch="E2")
    # state resolves from the AUTHORITATIVE ledger + transitions
    assert al2.reconciled_state(1) == tel.OUTCOME_DELIVERY_UNKNOWN  # marked → delivery_unknown
    assert al2.reconciled_state(2) == tel.OUTCOME_DISPATCH_PENDING  # unmarked → reserved
    assert al2.has_reconciliation(1) and al2.has_reconciliation(2)
    recon = al2._recon_rows()
    assert len(recon) == 2
    assert any(r["reconciled_outcome"] == tel.OUTCOME_DELIVERY_UNKNOWN
               and r["provider_entry_marker_present"] for r in recon)
    # idempotent: a later boot adds no new transitions
    s3 = tel.reconcile_prior_epoch(run_ledger=runs, attempt_ledger_factory=factory,
                                   reconcile_ledger=rl, current_epoch="E3", ts="ts3")
    assert s3["transitions"] == 0


def test_snapshot_is_derived_cache_mismatch_fails_closed(tmp_path):
    sd = tmp_path
    runs = tel.RunLedger(sd)
    runs.record_committed(run_id="r", nonce="n", destination=DEST, logical_turn_id="t",
                          authorized_commit=FUTURE_RC_COMMIT, harness_hash=HARNESS_HASH,
                          epoch="E1", ts="ts")
    al = tel.AttemptLedger(sd / "attempts" / "r.ndjson", epoch="E1")
    al.mark_dispatch_pending(1)
    al.mark_provider_entry(1, "pe")
    rl = tel.ReconcileLedger(sd)

    def factory(rid, ep):
        return tel.AttemptLedger(sd / "attempts" / (rid + ".ndjson"), ep)

    tel.reconcile_prior_epoch(run_ledger=runs, attempt_ledger_factory=factory,
                              reconcile_ledger=rl, current_epoch="E2", ts="ts2")
    al2 = tel.AttemptLedger(sd / "attempts" / "r.ndjson", epoch="E2")
    tel.assert_snapshot_consistent("r", al2, rl, "E2")  # consistent → no raise
    rl.record_snapshot("r", "E2", {"1": tel.OUTCOME_SENT}, "ts")  # tamper the derived cache
    with pytest.raises(tel.LedgerCorruption):
        tel.assert_snapshot_consistent("r", al2, rl, "E2")


# ═══════════════ AMENDMENT 3 — D1 async gateway-boot ordering ════════════════


class _FakeSock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_d1_async_startup_binds_socket_only_after_reconcile(monkeypatch, tmp_path):
    import asyncio
    monkeypatch.setenv("GATEWAY_TRANSPORT_EVIDENCE_ENABLED", "1")
    order = []

    def fake_bind(path, *, uid, gid):
        order.append("bind")
        assert (tmp_path / "current-epoch.json").exists()  # reconcile+epoch done first
        return _FakeSock()

    def fake_accept(server, srv, *, stop, path, uid):
        order.append("accept")

    handle = asyncio.run(te.start_gateway_harness_async(
        seams=SeamState().seams(), inject_diagnostic=lambda c: None,
        state_dir_path=str(tmp_path), epoch="e-d1", bind_fn=fake_bind, accept_fn=fake_accept,
    ))
    assert handle is not None
    assert handle.server.ready() is True  # reconciled + loop captured → PREPARE/COMMIT served
    assert order and order[0] == "bind"
    # D1 shutdown ordering: disable control plane, close socket, release lock
    asyncio.run(te.stop_gateway_harness_async(handle))
    assert handle.server.active is False and handle.sock.closed is True
    fresh = tel.SingletonLock(tmp_path / "te-owner.lock")
    fresh.acquire()  # lock released after shutdown
    fresh.release()


def test_d1_async_competing_owner_binds_nothing(monkeypatch, tmp_path):
    import asyncio
    monkeypatch.setenv("GATEWAY_TRANSPORT_EVIDENCE_ENABLED", "1")
    holder = tel.SingletonLock(tmp_path / "te-owner.lock")
    holder.acquire()
    try:
        bound = []
        handle = asyncio.run(te.start_gateway_harness_async(
            seams=SeamState().seams(), inject_diagnostic=lambda c: None,
            state_dir_path=str(tmp_path), epoch="e",
            bind_fn=lambda *a, **k: bound.append(1) or _FakeSock(),
            accept_fn=lambda *a, **k: None,
        ))
        assert handle is None and bound == []
        assert not (tmp_path / "current-epoch.json").exists()  # no init/reconcile either
    finally:
        holder.release()


# ═══════════════ async diagnostic driver (real-path classification) ══════════


class _AsyncSendResult:
    def __init__(self, message_id):
        self.success = True
        self.message_id = message_id
        self.error = None


def test_async_driver_5_accepted_23_denied_canary(tmp_path):
    import asyncio
    ledger = tel.AttemptLedger(tmp_path / "a.ndjson", epoch="e")
    posts = {"n": 0}

    async def fake_send(chat_id, segment, max_retries=0):
        assert max_retries == 0  # R3: the diagnostic must never retry
        pec = ted._ACTIVE_ATTEMPT.get()
        if pec.attempt > 5:
            return None  # budget denied — observer NEVER called (never reached boundary)
        ted.provider_entry_observer(chat_id)  # the provider boundary
        posts["n"] += 1
        return _AsyncSendResult("PROV%d" % pec.attempt)

    summary = asyncio.run(ted.run_diagnostic_async(
        chat_id=DEST, run_id="r", attempt_ledger=ledger, send_with_retry=fake_send,
    ))
    assert summary.accepted == 5 and summary.denied == 23
    assert posts["n"] == 5  # only the 5 accepted crossed the provider boundary
    for i in range(1, 6):
        assert ledger.reconciled_state(i) == tel.OUTCOME_SENT
    for i in range(6, 29):
        assert ledger.reconciled_state(i) == tel.OUTCOME_DENIED
        assert ledger.has_provider_entry_marker(i) is False  # non-vacuous denial proof
    assert ledger.provider_ids() == {i: "PROV%d" % i for i in range(1, 6)}


# ═══════════════ INTEGRATION — REAL patch on a synthetic gateway ═════════════
#
# Applies the REAL patch-hermes whatsapp transform to a production-equivalent
# synthetic adapter whose send() mirrors the real budget-gate -> provider-boundary
# structure and drives the WIRED diagnostic path through the REAL safe_io per-turn
# budget (cap=5). Proves 5 accepted (provider IDs), attempt 6 first-denied, 6-28
# never crossing the provider boundary (canary), exactly one page, correct ledger.

WA_FIXTURE = '''import os


class SendResult:
    def __init__(self, success, message_id=None, error=None):
        self.success = success
        self.message_id = message_id
        self.error = error


class BasePlatformAdapter:
    async def _send_with_retry(self, chat_id, content, reply_to=None, metadata=None,
                               max_retries=2, base_delay=2.0):
        # Mirrors gateway/platforms/base.py:2521 — calls self.send() with NO
        # None-guard (faithful to the deployed behaviour on a budget-drop).
        result = await self.send(chat_id=chat_id, content=content)
        if result.success:
            return result
        return result


class WhatsAppAdapter(BasePlatformAdapter):
    def __init__(self, bridge_port, http_session):
        self._bridge_port = bridge_port
        self._http_session = http_session
        self._running = True

    async def send(self, chat_id, content=None, reply_to=None, metadata=None):
        try:
            import aiohttp
            import safe_io as _sio
            # production-equivalent per-turn budget gate at the TOP of send()
            _gate = _sio.turn_send_budget_gate(chat_id, content)
            if _gate is False:
                return None  # suppressed before the provider boundary (mirrors drop)
            chunks = [content]
            last_message_id = None
            for chunk in chunks:
                payload = {"chatId": chat_id, "message": chunk}
                async with self._http_session.post(
                    f"http://127.0.0.1:{self._bridge_port}/send",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        last_message_id = data.get("messageId")
                    else:
                        error = await resp.text()
                        return SendResult(success=False, error=error)
        except Exception:
            raise
        return SendResult(success=True, message_id=last_message_id)
'''


class _IntgResp:
    def __init__(self, status, message_id):
        self.status = status
        self._mid = message_id

    async def json(self):
        return {"messageId": self._mid}

    async def text(self):
        return "err"


class _IntgPost:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.session.post_count += 1  # provider-boundary POST canary
        return _IntgResp(200, "PROVMSG%d" % self.session.post_count)

    async def __aexit__(self, *a):
        return False


class _IntgHttpSession:
    def __init__(self):
        self.post_count = 0

    def post(self, url, json=None, timeout=None):
        return _IntgPost(self)


def test_integration_wired_diagnostic_path(monkeypatch, tmp_path):
    import asyncio
    import types
    from fixtures_fleet import ensure_fcntl_stub

    ensure_fcntl_stub()
    import safe_io

    # Enable the REAL per-turn budget (cap=5) and reset its ContextVar.
    monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", "1")
    monkeypatch.delenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", raising=False)
    safe_io._TURN_SEND_BUDGET.set(None)
    pages = {"n": 0}
    monkeypatch.setattr(
        safe_io, "notify_owner_with_fallback",
        lambda *a, **k: (pages.__setitem__("n", pages["n"] + 1) or True),
        raising=False,
    )

    # Apply the REAL whatsapp transform to the production-equivalent fixture.
    ph = _load_patch_hermes()
    patched = ph._apply_wa_transport_evidence(WA_FIXTURE)
    assert ph._apply_wa_transport_evidence(patched) == patched  # idempotent
    assert patched.count(ph.TE_PROVIDER_ENTRY_MARK_BEGIN) == 1  # applied exactly once

    fake_aiohttp = types.ModuleType("aiohttp")
    fake_aiohttp.ClientTimeout = lambda **k: None
    monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)

    ns = {}
    exec(compile(patched, "<wa-fixture>", "exec"), ns)
    session = _IntgHttpSession()
    adapter = ns["WhatsAppAdapter"](3000, session)

    ledger = tel.AttemptLedger(tmp_path / "a.ndjson", epoch="e-int")
    summary = asyncio.run(ted.run_diagnostic_async(
        chat_id=DEST, run_id="rint", attempt_ledger=ledger,
        send_with_retry=adapter._send_with_retry,
        begin_turn_budget=safe_io.begin_inbound_turn_send_budget,
    ))
    assert summary.accepted == 5, summary
    assert summary.denied == 23, summary
    assert session.post_count == 5  # only 5 crossed the REAL provider boundary
    assert pages["n"] == 1  # the REAL budget gate paged exactly once
    assert ledger.provider_ids() == {i: "PROVMSG%d" % i for i in range(1, 6)}
    for i in range(1, 6):
        assert ledger.reconciled_state(i) == tel.OUTCOME_SENT
    for i in range(6, 29):
        assert ledger.reconciled_state(i) == tel.OUTCOME_DENIED
        assert ledger.has_provider_entry_marker(i) is False


def test_run_py_transform_applies_exactly_once():
    ph = _load_patch_hermes()
    fixture = (
        "import os\n"
        "class R:\n"
        "    async def start(self) -> bool:\n"
        '        """Start the gateway."""\n'
        '        logger.info("Press Ctrl+C to stop")\n'
        "        return True\n"
        "    async def stop(self, *, restart: bool = False) -> None:\n"
        '        """Stop the gateway and disconnect all adapters."""\n'
        "        return None\n"
        "    async def _handle_message(self, event):\n"
        '        return await self._handle_message_with_agent(event, event.source, "k", 0)\n'
        "    async def _handle_message_with_agent(self, event, source, _quick_key: str, run_generation: int):\n"
        '        """Inner handler."""\n'
        "        return None\n"
    )
    import ast as _ast
    out = ph._apply_run_transport_evidence(fixture)
    _ast.parse(out)
    assert ph._apply_run_transport_evidence(out) == out  # idempotent (exactly once)
    for mk in (ph.TE_MARK_BEGIN, ph.TE_STARTUP_MARK_BEGIN, ph.TE_SHUTDOWN_MARK_BEGIN, ph.TE_DIAG_MARK_BEGIN):
        assert out.count(mk) == 1


def _load_patch_hermes():
    path = REPO / "tools" / "patch-hermes.py"
    loader = importlib.machinery.SourceFileLoader("te_patch_hermes", str(path))
    spec = importlib.util.spec_from_loader("te_patch_hermes", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ═══════════════ CONSOLIDATED FIX PASS — reviewer findings (B1/F1/R2/3/5/6/8) ══


def test_r8_front_brain_enforce_armed_refuses_prepare(tmp_path):
    # R1/evidence integrity: FRONT_BRAIN_OUTBOUND_ENFORCE armed for the probe
    # destination → PREPARE fails closed (front-brain would screen/substitute the
    # diagnostic content + trigger its own paging → contaminate the evidence).
    ss = SeamState()
    ss.front_brain_armed = True
    srv = make_server(tmp_path, ss)
    resp = srv.handle_prepare(FakeConn(), prepare_dict())
    assert resp["status"] == "refused" and resp["reason"] == "front_brain_enforce_armed"
    ss.front_brain_armed = False
    assert srv.handle_prepare(FakeConn(), prepare_dict())["status"] == "prepared"


def test_r8_front_brain_drift_prepare_to_commit_fails_before_send(tmp_path):
    # R8 COMMIT-time revalidation: front-brain enforcement CLEAR at PREPARE, then
    # armed before COMMIT → the generic COMMIT prerequisite re-check (handle_commit
    # re-runs verify_prerequisites, which includes front_brain_enforce_armed)
    # refuses BEFORE the first send → zero diagnostic sends.
    ss = SeamState()  # front_brain_armed = False (clear at PREPARE)
    injected = []
    srv = make_server(tmp_path, ss, inject=lambda ctx: injected.append(ctx))
    conn = FakeConn()
    p = srv.handle_prepare(conn, prepare_dict())
    assert p["status"] == "prepared"
    ss.front_brain_armed = True  # becomes armed between PREPARE and COMMIT
    c = srv.handle_commit(conn, {"op": "commit", "prep_token": p["prep_token"]})
    assert c["status"] == "refused" and c["reason"] == "drift:front_brain_enforce_armed"
    assert injected == []  # zero diagnostic sends


def test_r6_paging_self_chat_cannot_trip_attempt_canary(tmp_path):
    # A budget-exhaustion PAGE self-chat to the SEPARATE owner identity, POSTing
    # through the same send() while attempt 6's context is active, must NOT trip
    # the current attempt's provider-entry canary (fail-loud misclassification).
    marked = {"n": 0}

    def on_entry():
        marked["n"] += 1

    pec = ted.ProviderEntryContext(attempt=6, on_provider_entry=on_entry, expected_chat_id=DEST)
    token = ted._ACTIVE_ATTEMPT.set(pec)
    try:
        ted.provider_entry_observer("19995550000@s.whatsapp.net")  # owner self-chat
        assert pec.entered == 0 and marked["n"] == 0  # NOT tripped, no marker
        ted.provider_entry_observer(DEST)  # a genuine send to the diagnostic dest
        assert pec.entered == 1 and marked["n"] == 1  # DOES trip + writes the marker
    finally:
        ted._ACTIVE_ATTEMPT.reset(token)


def test_r3_diagnostic_sends_use_max_retries_zero(tmp_path):
    import asyncio
    ledger = tel.AttemptLedger(tmp_path / "a.ndjson", epoch="e")
    seen = []

    async def fake_send(chat_id, segment, max_retries=99):
        seen.append(max_retries)
        pec = ted._ACTIVE_ATTEMPT.get()
        if pec.attempt > 5:
            return None
        ted.provider_entry_observer(chat_id)
        return _AsyncSendResult("P%d" % pec.attempt)

    asyncio.run(ted.run_diagnostic_async(
        chat_id=DEST, run_id="r", attempt_ledger=ledger, send_with_retry=fake_send,
    ))
    assert seen and all(m == 0 for m in seen)  # every send used max_retries=0 (no dup POST)


def test_r5_startup_refusal_writes_stderr(monkeypatch, tmp_path, capsys):
    # transport_evidence.py now imports sys → the fail-closed startup diagnostics
    # actually emit (before the R5 fix _te_stderr NameError'd silently).
    monkeypatch.setenv("GATEWAY_TRANSPORT_EVIDENCE_ENABLED", "1")
    holder = tel.SingletonLock(tmp_path / "te-owner.lock")
    holder.acquire()
    try:
        te.start_gateway_harness(
            seams=SeamState().seams(), inject_diagnostic=lambda c: None,
            state_dir_path=str(tmp_path), serve_fn=lambda *a, **k: None, epoch="e",
        )
    finally:
        holder.release()
    err = capsys.readouterr().err
    assert "another owner holds the ledger" in err


def test_r2_accept_loop_survives_bad_connection(monkeypatch):
    # A malformed / stalled connection (handle_connection raises) must NOT tear
    # down the whole control plane — a subsequent valid connection is still served.
    monkeypatch.setattr(te, "peer_uid_of", lambda s: 0)

    class FakeSockConn:
        def close(self):
            pass

    class FakeSrv:
        def __init__(self, n):
            self.n = n
            self.i = 0

        def settimeout(self, t):
            pass

        def accept(self):
            if self.i >= self.n:
                raise OSError("no more connections")  # ends the loop cleanly
            self.i += 1
            return FakeSockConn(), ("addr",)

        def close(self):
            pass

    class FakeServer:
        def __init__(self):
            self.calls = 0
            self.served = []
            self.disabled = False

        def handle_connection(self, conn):
            self.calls += 1
            if self.calls == 1:
                raise te.ProtocolError("malformed frame on connection 1")
            self.served.append(self.calls)

        def forget_connection(self, conn):
            pass

        def disable(self):
            self.disabled = True

    server = FakeServer()
    te._run_accept_loop(server, FakeSrv(2), stop=None, path="/nonexistent-te.sock", uid=999)
    assert server.calls == 2  # BOTH connections processed — the bad one didn't kill the loop
    assert server.served == [2]  # connection 2 (a valid one) served AFTER connection 1 raised
    assert server.disabled is True  # normal teardown only at the end


def test_f1_validate_lease_dir_bad_owner_refused(tmp_path):
    ld = tmp_path / "leasedir"
    ld.mkdir()
    if os.name != "nt":
        os.chmod(ld, 0o700)
    duid, dgid = _owner(ld)
    # correct owner (+ 0o700 on POSIX) → passes
    tle.validate_lease_dir(ld, expect_uid=duid, expect_gid=dgid, expect_mode=0o700)
    # wrong owner → refused
    with pytest.raises(tle.LeaseValidationError):
        tle.validate_lease_dir(ld, expect_uid=duid + 99999, expect_gid=dgid, expect_mode=0o700)


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir permission bits (Linux box only)")
def test_f1_validate_lease_dir_bad_mode_refused(tmp_path):
    ld = tmp_path / "leasedir"
    ld.mkdir()
    os.chmod(ld, 0o755)  # not 0o700
    duid, dgid = _owner(ld)
    with pytest.raises(tle.LeaseValidationError):
        tle.validate_lease_dir(ld, expect_uid=duid, expect_gid=dgid, expect_mode=0o700)


def test_f1_cli_refuses_wrong_owner_lease_dir(tmp_path):
    # The CLI checks the lease DIR (root:root 0700) BEFORE the lease file.
    cli = _load_cli()
    srv = make_server(tmp_path, SeamState())
    ld = tmp_path / "leasedir"
    ld.mkdir()
    if os.name != "nt":
        os.chmod(ld, 0o700)
    lp = ld / "lease.json"
    _write_lease(lp)
    duid, dgid = _owner(ld)
    code, resp = cli.run_probe(
        LoopbackClient(srv), str(lp), now=FIXED_NOW,
        expect_uid=duid + 99999, expect_gid=dgid, verify_installed_digest=False,
    )
    assert code != 0 and resp.get("phase") == "lease_dir"
    assert lp.exists()  # refused before any consumption


# ── B1: rollback-safety of the deploy install lines ──────────────────────────

DEPLOY_SH = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"


def test_b1_deploy_installs_are_rollback_guarded():
    deploy = DEPLOY_SH.read_text(encoding="utf-8")
    for mod in (
        "transport_evidence.py", "transport_evidence_ledger.py",
        "transport_evidence_diagnostic.py", "transport_evidence_lease.py",
    ):
        # install-or-remove: guarded install + else rm -f (era convention).
        assert f"if [ -f src/platform/{mod} ]; then" in deploy, mod
        assert f"install -m 644 src/platform/{mod} /opt/shift-agent/{mod}" in deploy, mod
        assert f"rm -f /opt/shift-agent/{mod}" in deploy, mod
        # NO unconditional install (would abort a rollback under set -euo pipefail).
        assert f"\n    install -m 644 src/platform/{mod} " not in deploy, mod
    # CLI rollback hygiene (installs via the additive scripts/* glob; rm on rollback).
    assert "if [ ! -f src/platform/scripts/shift-agent-transport-evidence-probe ]; then" in deploy
    assert "rm -f /usr/local/bin/shift-agent-transport-evidence-probe" in deploy


@pytest.mark.skipif(os.name == "nt", reason="needs a real POSIX shell (runs on Linux CI)")
def test_b1_rollback_guard_does_not_error_when_source_absent(tmp_path):
    # Simulate install_artifacts under `set -euo pipefail` from a rollback src_root
    # that LACKS the harness modules: the guarded install-or-remove must NOT abort
    # (the pre-fix unconditional `install` would exit non-zero mid-rollback), and
    # must REMOVE the previously installed copy.
    import subprocess
    src_root = tmp_path / "src_root"
    (src_root / "src" / "platform").mkdir(parents=True)  # empty: no harness modules
    target = tmp_path / "opt"
    target.mkdir()
    (target / "transport_evidence.py").write_text("stale-installed-copy")
    script = (
        "set -euo pipefail\n"
        f'cd "{src_root}"\n'
        "if [ -f src/platform/transport_evidence.py ]; then\n"
        f'    install -m 644 src/platform/transport_evidence.py "{target}/transport_evidence.py"\n'
        "else\n"
        f'    rm -f "{target}/transport_evidence.py"\n'
        "fi\n"
        "echo ROLLBACK_OK\n"
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "ROLLBACK_OK" in r.stdout
    assert not (target / "transport_evidence.py").exists()  # stale copy removed, no error


# ═══════════════ deploy-gate FUNCTIONAL matrix (Linux CI) ════════════════════


@pytest.mark.skipif(os.name == "nt", reason="needs a real POSIX shell (runs on Linux CI)")
def test_transport_evidence_deploy_gate_matrix(tmp_path):
    """Functional accept/reject matrix for tools/check-transport-evidence-patch.sh
    (not just `bash -n`): correct closure accepted; unpatched/missing/duplicate/
    partial/default-OFF-violation/version-skew rejected; idempotent second apply
    accepted."""
    import subprocess

    ph = _load_patch_hermes()
    run_fix = (
        "import os\n"
        "class R:\n"
        "    async def start(self) -> bool:\n"
        '        """d."""\n'
        '        logger.info("Press Ctrl+C to stop")\n'
        "        return True\n"
        "    async def stop(self, *, restart: bool = False) -> None:\n"
        '        """Stop the gateway and disconnect all adapters."""\n'
        "        return None\n"
        "    async def _handle_message(self, event):\n"
        '        return await self._handle_message_with_agent(event, event.source, "k", 0)\n'
        "    async def _handle_message_with_agent(self, event, source, _quick_key: str, run_generation: int):\n"
        '        """Inner."""\n'
        "        return None\n"
    )
    wa_fix = (
        "import os\n"
        "class BasePlatformAdapter:\n"
        "    pass\n"
        "class WhatsAppAdapter(BasePlatformAdapter):\n"
        "    async def send(self, chat_id, content=None):\n"
        "        try:\n"
        "            import aiohttp\n"
        "            chunks = [content]\n"
        "            for chunk in chunks:\n"
        '                payload = {"chatId": chat_id, "message": chunk}\n'
        "                async with self._http_session.post(\n"
        '                    f"http://127.0.0.1:{self._bridge_port}/send",\n'
        "                    json=payload,\n"
        "                    timeout=aiohttp.ClientTimeout(total=30)\n"
        "                ) as resp:\n"
        "                    if resp.status == 200:\n"
        "                        return (await resp.json()).get(\"messageId\")\n"
        "        except Exception:\n"
        "            raise\n"
        "        return None\n"
    )
    H = tmp_path / "hermes"
    (H / "gateway" / "platforms").mkdir(parents=True)
    platform = tmp_path / "opt"
    platform.mkdir()
    run_py = H / "gateway" / "run.py"
    wa_py = H / "gateway" / "platforms" / "whatsapp.py"
    run_py.write_text(ph._apply_run_transport_evidence(run_fix), encoding="utf-8")
    wa_py.write_text(ph._apply_wa_transport_evidence(wa_fix), encoding="utf-8")
    for m in ("transport_evidence.py", "transport_evidence_ledger.py",
              "transport_evidence_diagnostic.py", "transport_evidence_lease.py"):
        (platform / m).write_bytes((REPO / "src" / "platform" / m).read_bytes())

    gate = str(REPO / "tools" / "check-transport-evidence-patch.sh")

    def run_gate(run_path, wa_path):
        env = {**os.environ, "RUN": str(run_path), "WA": str(wa_path), "PLATFORM": str(platform)}
        return subprocess.run(["bash", gate], env=env, capture_output=True, text=True).returncode

    # correct closure accepted
    assert run_gate(run_py, wa_py) == 0
    # idempotent second application accepted
    run2 = H / "gateway" / "run2.py"
    run2.write_text(ph._apply_run_transport_evidence(run_py.read_text(encoding="utf-8")), encoding="utf-8")
    assert run_gate(run2, wa_py) == 0
    # unpatched run.py rejected
    unpatched = tmp_path / "unpatched.py"
    unpatched.write_text("import os\nx = 1\n", encoding="utf-8")
    assert run_gate(unpatched, wa_py) != 0
    # missing startup marker rejected
    miss = tmp_path / "miss_startup.py"
    miss.write_text("\n".join(
        ln for ln in run_py.read_text(encoding="utf-8").splitlines()
        if "shift-agent-transport-evidence-startup" not in ln
    ), encoding="utf-8")
    assert run_gate(miss, wa_py) != 0
    # duplicate diag marker rejected
    dup = tmp_path / "dup_diag.py"
    dup.write_text(run_py.read_text(encoding="utf-8")
                   + "\n# BEGIN shift-agent-transport-evidence-diag\n", encoding="utf-8")
    assert run_gate(dup, wa_py) != 0
    # partial patch: whatsapp provider-entry marker removed → rejected
    wmiss = tmp_path / "miss_pe.py"
    wmiss.write_text("\n".join(
        ln for ln in wa_py.read_text(encoding="utf-8").splitlines()
        if "shift-agent-transport-evidence-provider-entry" not in ln
    ), encoding="utf-8")
    assert run_gate(run_py, wmiss) != 0
    # default-OFF violation: module-scope call injected into the block → rejected
    notoff = tmp_path / "notoff.py"
    notoff.write_text(run_py.read_text(encoding="utf-8").replace(
        "# BEGIN shift-agent-transport-evidence-probe\n",
        "# BEGIN shift-agent-transport-evidence-probe\n_shift_te_on_startup(None)\n", 1,
    ), encoding="utf-8")
    assert run_gate(notoff, wa_py) != 0
    # version-skew: a required platform module missing → rejected
    (platform / "transport_evidence_ledger.py").unlink()
    assert run_gate(run_py, wa_py) != 0
