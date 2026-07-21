"""Tests for the TTL-0 observe-only stale flyer-project sweep.

Covers the reviewer-ruled closing gates for PR #633:
  A. Semantic pins — manual_review_required for missing/contradictory timestamps
     (A1); strict > TTL staleness (A2); observe_only mode + operator_decision
     on delivered + no auto-close language (A3); deterministic project_id order
     (A4); flag-off exits before any store load (A5); extended privacy scan (A6).
  B. A fail-closed AST no-write guard proving the module + CLI cannot mutate
     state, send, or write anywhere but the caller-supplied --digest-path.

The module is read-only and importable on Windows (no fcntl / no safe_io).
"""
from __future__ import annotations

import ast
import importlib.machinery
import json
from pathlib import Path

from agents.flyer.ttl_observe import (
    build_ttl0_digest,
    compute_last_activity,
    parse_utc,
    serialize_digest,
)

REPO = Path(__file__).resolve().parents[1]
MODULE_SRC = REPO / "src" / "agents" / "flyer" / "ttl_observe.py"
CLI = REPO / "src" / "agents" / "flyer" / "scripts" / "flyer-ttl0-observe"

AS_OF = "2026-07-20T22:00:00Z"


def _as_of():
    return parse_utc(AS_OF)


def _load_cli():
    loader = importlib.machinery.SourceFileLoader("flyer_ttl0_observe_cli", str(CLI))
    return loader.load_module()


# ── fixture builders ────────────────────────────────────────────────────────

def _asset(asset_id: str, project_id: str, delivered_at: str) -> dict:
    return {
        "asset_id": asset_id,
        "kind": "final_whatsapp_image",
        "source": "rendered",
        "path": f"/opt/shift-agent/state/flyer/projects/{project_id}/final.png",
        "mime_type": "image/png",
        "sha256": "a" * 64,
        "received_at": delivered_at,
        "delivery_status": "sent",
        "delivered_at": delivered_at,
    }


def _project(
    project_id: str,
    *,
    status: str,
    customer_id: str,
    updated_at: str | None = "2026-07-06T00:00:00Z",
    created_at: str | None = "2026-07-01T00:00:00Z",
    customer_phone: str = "+17329837841",
    assets: list[dict] | None = None,
    manual_review: dict | None = None,
    chat_id: str = "",
    locked_facts: list[dict] | None = None,
) -> dict:
    proj: dict = {
        "project_id": project_id,
        "status": status,
        "customer_phone": customer_phone,
        "customer_id": customer_id,
        "original_message_id": f"m-{project_id}",
        "raw_request": "weekend flyer",
        "assets": assets or [],
    }
    if updated_at is not None:
        proj["updated_at"] = updated_at
    if created_at is not None:
        proj["created_at"] = created_at
    if manual_review is not None:
        proj["manual_review"] = manual_review
    if chat_id:
        proj["chat_id"] = chat_id
    if locked_facts is not None:
        proj["locked_facts"] = locked_facts
    return proj


def _store(*projects: dict) -> dict:
    return {"schema_version": 1, "next_sequence": len(projects) + 1, "projects": list(projects)}


def _incident_store() -> dict:
    """Reproduce the 2026-07-20 CUST0001 shape.

    Non-delivered CUST0001 rows are kept at/under 2026-07-13T00:00Z so that,
    against as_of 2026-07-20T22:00Z, every awaiting_final_approval row clears the
    168h TTL (boundary 07-13T22:00Z) and every intake_started row clears the 72h
    TTL. F0224 (manual_edit_required) is an excluded status; F0217/F0222 belong
    to CUST0007 and legitimately appear as candidates under their own id.
    """
    projects: list[dict] = []

    delivered = {
        "F0201": "2026-07-03T00:00:00Z",
        "F0203": "2026-07-04T00:00:00Z",
        "F0209": "2026-07-05T00:00:00Z",
        "F0210": "2026-07-05T00:00:00Z",
        "F0211": "2026-07-06T00:00:00Z",
        "F0212": "2026-07-06T00:00:00Z",
        "F0213": "2026-07-04T00:00:00Z",
    }
    for pid, ts in delivered.items():
        projects.append(_project(
            pid, status="delivered", customer_id="CUST0001", updated_at=ts,
            assets=[_asset("A0001", pid, ts)],
        ))

    afa = {
        "F0214": "2026-07-06T00:00:00Z",
        "F0215": "2026-07-07T00:00:00Z",
        "F0216": "2026-07-08T00:00:00Z",
        "F0219": "2026-07-09T00:00:00Z",
        "F0221": "2026-07-10T00:00:00Z",
        "F0223": "2026-07-13T00:00:00Z",
    }
    for i, (pid, ts) in enumerate(afa.items()):
        projects.append(_project(
            pid, status="awaiting_final_approval", customer_id="CUST0001",
            updated_at=ts,
            # Seed one row with a chat_id + locked fact so the privacy scan has
            # phone/chat_id/fact strings present in the store to prove non-leak.
            chat_id="201975216009469@lid" if i == 0 else "",
            locked_facts=[{
                "fact_id": "phone1", "label": "Business phone",
                "value": "+17329837841", "source": "customer_text",
            }] if i == 0 else None,
        ))

    for pid in ("F0218", "F0220"):
        projects.append(_project(
            pid, status="intake_started", customer_id="CUST0001",
            updated_at="2026-07-12T00:00:00Z",
        ))

    projects.append(_project(
        "F0224", status="manual_edit_required", customer_id="CUST0001",
        updated_at="2026-07-14T00:00:00Z",
        manual_review={"status": "queued", "reason_code": "visual_qa_failed",
                       "queued_at": "2026-07-14T00:00:00Z"},
    ))

    projects.append(_project(
        "F0217", status="awaiting_final_approval", customer_id="CUST0007",
        customer_phone="+19045550104", updated_at="2026-07-08T00:00:00Z",
    ))
    projects.append(_project(
        "F0222", status="awaiting_final_approval", customer_id="CUST0007",
        customer_phone="+19045550104", updated_at="2026-07-10T00:00:00Z",
    ))

    return {"schema_version": 1, "next_sequence": len(projects) + 1, "projects": projects}


def _write_store(tmp_path: Path, store: dict) -> Path:
    path = tmp_path / "projects.json"
    path.write_text(json.dumps(store), encoding="utf-8")
    return path


def _ids(rows: list[dict]) -> list[str]:
    return [r["project_id"] for r in rows]


# ── A5: flag-off exits before any store load ────────────────────────────────

def test_flag_off_is_a_strict_no_op(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FLYER_TTL0_OBSERVE_ENABLED", raising=False)
    cli = _load_cli()
    store_path = _write_store(tmp_path, _incident_store())
    digest_path = tmp_path / "digest.json"
    rc = cli.main(["--state-path", str(store_path), "--digest-path", str(digest_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == {"enabled": False, "candidates_scanned": 0}
    assert not digest_path.exists()  # no write when disabled


def test_flag_off_needs_no_as_of(capsys, monkeypatch):
    monkeypatch.setenv("FLYER_TTL0_OBSERVE_ENABLED", "0")
    cli = _load_cli()
    rc = cli.main([])
    assert rc == 0
    assert json.loads(capsys.readouterr().out.strip()) == {"enabled": False, "candidates_scanned": 0}


def test_flag_off_returns_before_store_load_even_for_missing_file(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FLYER_TTL0_OBSERVE_ENABLED", raising=False)
    cli = _load_cli()
    missing = tmp_path / "does-not-exist.json"
    rc = cli.main(["--state-path", str(missing), "--as-of", AS_OF])
    assert rc == 0
    assert json.loads(capsys.readouterr().out.strip()) == {"enabled": False, "candidates_scanned": 0}


def test_flag_off_does_not_load_store_even_if_loader_would_raise(tmp_path, capsys, monkeypatch):
    # Structural proof the disabled path never reaches the loader: replace it with
    # a raiser; flag-off must still return the disabled JSON without invoking it.
    monkeypatch.delenv("FLYER_TTL0_OBSERVE_ENABLED", raising=False)
    cli = _load_cli()

    def _boom(*_a, **_k):
        raise AssertionError("load_store must not run when the flag is off")

    monkeypatch.setattr(cli, "load_store", _boom)
    rc = cli.main(["--state-path", str(tmp_path / "x.json"), "--as-of", AS_OF])
    assert rc == 0
    assert json.loads(capsys.readouterr().out.strip()) == {"enabled": False, "candidates_scanned": 0}


def test_flag_on_requires_as_of(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_TTL0_OBSERVE_ENABLED", "1")
    cli = _load_cli()
    try:
        cli.main(["--state-path", str(tmp_path / "missing.json")])
    except SystemExit as exc:
        assert "as-of" in str(exc).lower()
    else:  # pragma: no cover - guard
        raise AssertionError("expected SystemExit when --as-of is missing")


# ── incident fixture ────────────────────────────────────────────────────────

def test_incident_fixture_partitions_candidates_delivered_and_excluded():
    digest = build_ttl0_digest(_incident_store(), as_of=_as_of())

    assert digest["enabled"] is True
    assert digest["candidates_scanned"] == 18

    assert _ids(digest["candidates"]) == [
        "F0214", "F0215", "F0216", "F0217", "F0218",
        "F0219", "F0220", "F0221", "F0222", "F0223",
    ]
    assert _ids(digest["delivered_candidates"]) == [
        "F0201", "F0203", "F0209", "F0210", "F0211", "F0212", "F0213",
    ]
    assert digest["excluded_statuses"] == {"manual_edit_required": 1}
    assert digest["excluded"] == []
    assert "F0224" not in _ids(digest["candidates"])
    assert "F0224" not in _ids(digest["delivered_candidates"])

    by_id = {r["project_id"]: r for r in digest["candidates"]}
    assert by_id["F0217"]["customer_id"] == "CUST0007"
    assert by_id["F0222"]["customer_id"] == "CUST0007"
    assert by_id["F0214"]["customer_id"] == "CUST0001"


def test_incident_legal_transitions_are_correct():
    digest = build_ttl0_digest(_incident_store(), as_of=_as_of())
    for row in digest["candidates"]:
        if row["status"] == "intake_started":
            assert row["legal_transition"] == "closed_no_send"
            assert row["ttl_hours"] == 72
        elif row["status"] == "awaiting_final_approval":
            assert row["legal_transition"] == "closed_no_send"
            assert row["ttl_hours"] == 168
        else:  # pragma: no cover - guard
            raise AssertionError(f"unexpected candidate status {row['status']}")
    for row in digest["delivered_candidates"]:
        assert row["status"] == "delivered"
        assert row["legal_transition"] == "completed"
        assert row["ttl_hours"] == 168


# ── A3: observe_only mode, operator decision, no auto-close language ─────────

def test_digest_mode_and_delivered_operator_decision_required():
    digest = build_ttl0_digest(_incident_store(), as_of=_as_of())
    assert digest["mode"] == "observe_only"
    assert digest["delivered_candidates"]  # non-empty
    for row in digest["delivered_candidates"]:
        assert row["operator_decision_required"] is True
    # Non-delivered candidates deliberately do NOT carry the delivered-only flag.
    for row in digest["candidates"]:
        assert "operator_decision_required" not in row


def test_digest_has_no_auto_close_language():
    payload = serialize_digest(build_ttl0_digest(_incident_store(), as_of=_as_of()))
    for banned in ("auto_close", "auto-close", "safe_to_close"):
        assert banned not in payload, f"delivered must never read as {banned!r}"


# ── A4: deterministic ordering ──────────────────────────────────────────────

def test_candidate_ordering_is_deterministic_by_project_id():
    store = _incident_store()
    store["projects"].reverse()  # shuffle input order
    digest = build_ttl0_digest(store, as_of=_as_of())
    assert _ids(digest["candidates"]) == sorted(_ids(digest["candidates"]))
    assert _ids(digest["delivered_candidates"]) == sorted(_ids(digest["delivered_candidates"]))
    assert _ids(digest["candidates"]) == [
        "F0214", "F0215", "F0216", "F0217", "F0218",
        "F0219", "F0220", "F0221", "F0222", "F0223",
    ]


# ── idempotency ─────────────────────────────────────────────────────────────

def test_digest_is_byte_identical_across_runs():
    store = _incident_store()
    first = serialize_digest(build_ttl0_digest(store, as_of=_as_of()))
    second = serialize_digest(build_ttl0_digest(store, as_of=_as_of()))
    assert first == second


def test_cli_digest_path_write_is_byte_identical_across_runs(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FLYER_TTL0_OBSERVE_ENABLED", "1")
    cli = _load_cli()
    store_path = _write_store(tmp_path, _incident_store())
    out_path = tmp_path / "digest.json"
    cli.main(["--state-path", str(store_path), "--as-of", AS_OF, "--digest-path", str(out_path)])
    capsys.readouterr()
    first = out_path.read_bytes()
    cli.main(["--state-path", str(store_path), "--as-of", AS_OF, "--digest-path", str(out_path)])
    capsys.readouterr()
    second = out_path.read_bytes()
    assert first == second
    assert json.loads(first.decode("utf-8"))["candidates_scanned"] == 18


# ── activity-aware age ──────────────────────────────────────────────────────

def test_asset_delivered_at_dominates_older_updated_at():
    project = _project("F5001", status="delivered", customer_id="CUST0001",
                       updated_at="2026-07-01T00:00:00Z",
                       assets=[_asset("A0001", "F5001", "2026-07-19T00:00:00Z")])
    # last_activity uses the newer asset delivered_at, not the older updated_at.
    assert compute_last_activity(project).isoformat() == "2026-07-19T00:00:00+00:00"
    # Fresh (delivered_at only ~1.9 days before as_of) => within 168h => not stale.
    digest = build_ttl0_digest(_store(project), as_of=_as_of())
    assert digest["delivered_candidates"] == []
    assert digest["candidates"] == []


def test_within_ttl_project_is_not_listed():
    project = _project("F5002", status="awaiting_final_approval", customer_id="CUST0001",
                       updated_at="2026-07-19T22:00:00Z")
    digest = build_ttl0_digest(_store(project), as_of=_as_of())
    assert digest["candidates"] == []
    assert digest["excluded"] == []
    assert digest["excluded_statuses"] == {}


# ── A2: exactly-on-threshold is not stale ───────────────────────────────────

def test_exactly_at_ttl_is_not_a_candidate():
    # last_activity exactly 168h before as_of => age == ttl => NOT stale (strict >).
    at_ttl = _project("F5100", status="awaiting_final_approval", customer_id="CUST0001",
                      updated_at="2026-07-13T22:00:00Z")
    digest = build_ttl0_digest(_store(at_ttl), as_of=_as_of())
    assert digest["candidates"] == []
    assert digest["excluded"] == []
    # One second past the TTL boundary IS a candidate — brackets the boundary.
    past = _project("F5101", status="awaiting_final_approval", customer_id="CUST0001",
                    updated_at="2026-07-13T21:59:59Z")
    digest2 = build_ttl0_digest(_store(past), as_of=_as_of())
    assert _ids(digest2["candidates"]) == ["F5101"]


# ── claimed exclusion ───────────────────────────────────────────────────────

def test_claimed_stale_project_is_excluded_not_a_candidate():
    project = _project("F5003", status="awaiting_final_approval", customer_id="CUST0001",
                       updated_at="2026-07-06T00:00:00Z",
                       manual_review={"status": "in_progress", "claimed_by": "admin-a",
                                      "claimed_at": "2026-07-06T00:00:00Z"})
    digest = build_ttl0_digest(_store(project), as_of=_as_of())
    assert _ids(digest["candidates"]) == []
    assert _ids(digest["excluded"]) == ["F5003"]
    row = digest["excluded"][0]
    assert row["exclusion"] == "claimed"
    assert row["claimed"] is True


# ── excluded-status tally ───────────────────────────────────────────────────

def test_excluded_and_unmonitored_statuses_are_tallied_only():
    digest = build_ttl0_digest(_store(
        _project("F5010", status="generating_concepts", customer_id="CUST0001",
                 updated_at="2026-07-01T00:00:00Z"),
        _project("F5011", status="completed", customer_id="CUST0001",
                 updated_at="2026-07-01T00:00:00Z"),
        _project("F5012", status="closed_no_send", customer_id="CUST0001",
                 updated_at="2026-07-01T00:00:00Z"),
        # delivered_with_warning is unmonitored by TTL-0 => tally only, never a candidate.
        _project("F5013", status="delivered_with_warning", customer_id="CUST0001",
                 updated_at="2026-07-01T00:00:00Z"),
    ), as_of=_as_of())
    assert digest["candidates"] == []
    assert digest["delivered_candidates"] == []
    assert digest["excluded"] == []
    assert digest["excluded_statuses"] == {
        "closed_no_send": 1,
        "completed": 1,
        "delivered_with_warning": 1,
        "generating_concepts": 1,
    }


# ── legal-transition: defensive no-edge path ────────────────────────────────

def test_status_without_legal_terminal_edge_is_excluded_defensively():
    # awaiting_concept_selection has a 168h TTL but no legal terminal edge in the
    # deployed FLYER_TRANSITIONS table (only -> revising_design), so a stale one
    # is reported as an exclusion, not a candidate.
    project = _project("F5020", status="awaiting_concept_selection", customer_id="CUST0001",
                       updated_at="2026-07-06T00:00:00Z")
    digest = build_ttl0_digest(_store(project), as_of=_as_of())
    assert _ids(digest["candidates"]) == []
    assert _ids(digest["excluded"]) == ["F5020"]
    row = digest["excluded"][0]
    assert row["exclusion"] == "no_legal_terminal_edge"
    assert row["legal_transition"] is None


# ── A1: missing / contradictory timestamps => manual_review_required ─────────

def _only_excluded(project: dict) -> dict:
    digest = build_ttl0_digest(_store(project), as_of=_as_of())
    assert digest["candidates"] == []
    assert digest["delivered_candidates"] == []
    assert _ids(digest["excluded"]) == [project["project_id"]]
    return digest["excluded"][0]


def test_missing_updated_at_is_manual_review_required():
    project = _project("F6001", status="awaiting_final_approval", customer_id="CUST0001",
                       updated_at=None)  # no updated_at at all
    assert _only_excluded(project)["exclusion"] == "manual_review_required"


def test_unparseable_timestamp_is_manual_review_required():
    project = _project("F6002", status="awaiting_final_approval", customer_id="CUST0001",
                       updated_at="not-a-real-timestamp")
    assert _only_excluded(project)["exclusion"] == "manual_review_required"


def test_asset_delivered_before_created_is_manual_review_required():
    project = _project("F6003", status="delivered", customer_id="CUST0001",
                       created_at="2026-07-10T00:00:00Z",
                       updated_at="2026-07-15T00:00:00Z",
                       assets=[_asset("A0001", "F6003", "2026-07-05T00:00:00Z")])
    assert _only_excluded(project)["exclusion"] == "manual_review_required"


def test_last_activity_in_future_is_manual_review_required():
    # updated_at 2h after as_of, beyond the 1h clock-skew allowance.
    project = _project("F6004", status="awaiting_final_approval", customer_id="CUST0001",
                       updated_at="2026-07-21T00:00:00Z")
    assert _only_excluded(project)["exclusion"] == "manual_review_required"


def test_slight_future_within_skew_is_not_flagged():
    # 30 min after as_of => within CLOCK_SKEW_HOURS => treated as fresh, omitted.
    project = _project("F6005", status="awaiting_final_approval", customer_id="CUST0001",
                       updated_at="2026-07-20T22:30:00Z")
    digest = build_ttl0_digest(_store(project), as_of=_as_of())
    assert digest["candidates"] == []
    assert digest["excluded"] == []


# ── A6: privacy scan ────────────────────────────────────────────────────────

def test_digest_contains_no_phone_chatid_or_fact_strings():
    payload = serialize_digest(build_ttl0_digest(_incident_store(), as_of=_as_of()))
    for needle in ("+1", "@lid", "phone", "chat_id", "locked_facts", "body", "message", "caption"):
        assert needle not in payload, f"privacy leak: {needle!r} present in digest"


# ── B: fail-closed AST no-write guard ───────────────────────────────────────

_ALLOWED_WRITER = "_write_digest_file"
_FORBIDDEN_IMPORTS = {"subprocess", "socket", "urllib", "http", "requests", "safe_io"}
_FORBIDDEN_CALL_SUBSTRINGS = (
    "bridge", "send_", "notify", "update_flyer_project",
    "manual_queue", "ndjson_append", "log_decision",
)
# Path/file methods that mutate the filesystem, flagged on ANY receiver (datetime
# has none of these, so they are unambiguous — unlike bare "replace"/"rename").
_PATH_MUTATE_ATTRS = {"unlink", "chmod", "chown", "mkdir", "rmdir", "touch",
                      "write_text", "write_bytes"}
# os.<attr> mutators, flagged ONLY when the receiver is the `os` module (so a
# datetime.replace / str.replace is not mistaken for os.replace).
_OS_MUTATE_ATTRS = {"replace", "rename", "remove", "removedirs", "unlink",
                    "mkdir", "makedirs", "rmdir", "chmod", "chown", "write"}
_OS_PROC_ATTRS = {"system", "popen", "execv", "execve", "execvp", "execvpe",
                  "execl", "execle", "execlp", "spawnv", "spawnl", "fork"}
_SHUTIL_MUTATE_ATTRS = {"rmtree", "move", "copy", "copy2", "copyfile", "copytree"}
_WRITE_LITERAL_MARKERS = ("/opt/shift-agent", "decisions.log")


def _open_write_mode(call: ast.Call):
    """(is_write, classifiable) for a bare open()/`.open()` call.

    classifiable is False when a mode is supplied but is not a string literal —
    the fail-closed case (a computed mode we cannot reason about)."""
    mode = None
    mode_is_const = True
    if len(call.args) >= 2:
        arg = call.args[1]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            mode = arg.value
        else:
            mode_is_const = False
    for kw in call.keywords:
        if kw.arg == "mode":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                mode = kw.value.value
            else:
                mode_is_const = False
    if not mode_is_const:
        return False, False  # unclassifiable computed mode
    if mode is None:
        return False, True  # default "r" => read
    return (any(c in mode for c in "wax+"), True)


def _string_consts(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


class _NoWriteScanner(ast.NodeVisitor):
    """Fail-closed AST scan: any construct it cannot classify is an offense."""

    def __init__(self, allowed_writer: str):
        self.allowed_writer = allowed_writer
        self.func_stack: list[str] = []
        self.offenses: list[str] = []

    def visit_FunctionDef(self, node):
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Import(self, node):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in _FORBIDDEN_IMPORTS:
                self.offenses.append(f"forbidden import: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        top = (node.module or "").split(".")[0]
        if top in _FORBIDDEN_IMPORTS:
            self.offenses.append(f"forbidden import-from: {node.module}")
        self.generic_visit(node)

    def _receiver_is(self, func: ast.Attribute, name: str) -> bool:
        return isinstance(func.value, ast.Name) and func.value.id == name

    def visit_Call(self, node):
        func = node.func
        enclosing = self.func_stack[-1] if self.func_stack else "<module>"

        # Forbidden-name calls (bridge/send/notify/audit) anywhere.
        call_name = None
        if isinstance(func, ast.Name):
            call_name = func.id
        elif isinstance(func, ast.Attribute):
            call_name = func.attr
        if call_name and any(s in call_name for s in _FORBIDDEN_CALL_SUBSTRINGS):
            self.offenses.append(f"forbidden call {call_name!r} in {enclosing}")

        is_write_or_mutate = False
        if isinstance(func, ast.Name) and func.id == "open":
            is_write, classifiable = _open_write_mode(node)
            if not classifiable:
                self.offenses.append(f"unclassifiable open() mode in {enclosing} (fail-closed)")
            is_write_or_mutate = is_write
        elif isinstance(func, ast.Attribute):
            attr = func.attr
            if attr == "open":
                is_write, classifiable = _open_write_mode(node)
                if not classifiable:
                    self.offenses.append(f"unclassifiable .open() mode in {enclosing} (fail-closed)")
                is_write_or_mutate = is_write
            elif attr in _PATH_MUTATE_ATTRS:
                is_write_or_mutate = True
            elif attr in _OS_MUTATE_ATTRS and self._receiver_is(func, "os"):
                is_write_or_mutate = True
            elif attr in _SHUTIL_MUTATE_ATTRS and self._receiver_is(func, "shutil"):
                is_write_or_mutate = True
            elif attr in _OS_PROC_ATTRS and self._receiver_is(func, "os"):
                self.offenses.append(f"forbidden os.{attr} process/exec call in {enclosing}")
            elif attr.startswith("exec") and self._receiver_is(func, "os"):
                self.offenses.append(f"forbidden os.{attr} exec call in {enclosing}")

        if is_write_or_mutate:
            if enclosing != self.allowed_writer:
                self.offenses.append(
                    f"filesystem write/mutate ({call_name}) outside "
                    f"{self.allowed_writer} — found in {enclosing}"
                )
            for literal in _string_consts(node):
                if any(marker in literal for marker in _WRITE_LITERAL_MARKERS):
                    self.offenses.append(f"write targets forbidden path literal {literal!r}")

        self.generic_visit(node)


def _scan_source(path: Path) -> list[str]:
    # Forbidden path literals (decisions.log / /opt/shift-agent) are checked only
    # as WRITE TARGETS inside the scanner — a docstring or sys.path entry that
    # merely mentions such a path is not a write and must not be flagged.
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    scanner = _NoWriteScanner(_ALLOWED_WRITER)
    scanner.visit(tree)
    return list(scanner.offenses)


def test_no_write_ast_guard_module_and_cli_are_clean():
    for path in (MODULE_SRC, CLI):
        offenses = _scan_source(path)
        assert offenses == [], f"{path.name} no-write guard offenses: {offenses}"


def test_no_write_guard_detector_is_sound():
    """Self-test so the guard cannot silently pass vacuously: each synthetic
    violation MUST be flagged, and a clean observe-shaped module MUST NOT be."""
    import tempfile

    def _scan_text(src: str) -> list[str]:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
            fh.write(src)
            name = fh.name
        try:
            return _scan_source(Path(name))
        finally:
            Path(name).unlink()

    # Rogue store write outside the allowed writer.
    assert _scan_text(
        "def sweep(p, data):\n"
        "    with open('/opt/shift-agent/state/flyer/projects.json', 'w') as f:\n"
        "        f.write(data)\n"
    )
    # os.replace outside the writer.
    assert _scan_text("import os\ndef sweep(a, b):\n    os.replace(a, b)\n")
    # bridge/send/notify call.
    assert _scan_text("def sweep(c, t):\n    bridge_post(c, t)\n")
    # forbidden import.
    assert _scan_text("import subprocess\ndef sweep():\n    return 1\n")
    # unclassifiable open() mode (fail-closed).
    assert _scan_text("def sweep(p, m):\n    open(p, m)\n")
    # a WRITE that targets the decisions.log audit path (append mode).
    assert _scan_text("def sweep():\n    open('/opt/shift-agent/logs/decisions.log', 'a')\n")
    # a docstring/sys.path mention of such a path is NOT a write => not flagged.
    assert _scan_text(
        "'''mentions /opt/shift-agent/logs/decisions.log in prose'''\n"
        "import sys\n"
        "sys.path.insert(0, '/opt/shift-agent')\n"
        "STATE = '/opt/shift-agent/state/flyer/projects.json'\n"
    ) == []
    # A clean observe-shaped module with the sanctioned writer is NOT flagged;
    # a datetime-style `.replace` (receiver not `os`) must not be mistaken for a mutate.
    assert _scan_text(
        "import os\n"
        "def _write_digest_file(p, payload):\n"
        "    p.parent.mkdir(parents=True, exist_ok=True)\n"
        "    tmp = p.with_name(p.name + '.tmp')\n"
        "    tmp.write_text(payload, encoding='utf-8')\n"
        "    os.replace(tmp, p)\n"
        "def build(x):\n"
        "    return x.replace(1, 2)\n"
    ) == []
