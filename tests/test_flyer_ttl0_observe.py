"""Tests for the TTL-0 observe-only stale flyer-project sweep.

Covers: flag-off no-op; the 2026-07-20 CUST0001 incident fixture; idempotency
(byte-identical digests); activity-aware age (asset delivered_at dominating
updated_at); claimed exclusion; excluded-status tally; legal-transition
correctness (incl. the defensive no_legal_terminal_edge path); and a privacy
scan proving no phone / chat_id / fact strings leak into the digest.

The module is read-only and importable on Windows (no fcntl / no safe_io).
"""
from __future__ import annotations

import importlib.machinery
import json
from pathlib import Path

from agents.flyer.ttl_observe import (
    build_ttl0_digest,
    compute_last_activity,
    load_project_store,
    serialize_digest,
)

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "src" / "agents" / "flyer" / "scripts" / "flyer-ttl0-observe"

AS_OF = "2026-07-20T22:00:00Z"


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
    updated_at: str,
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
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": updated_at,
        "original_message_id": f"m-{project_id}",
        "raw_request": "weekend flyer",
        "assets": assets or [],
    }
    if manual_review is not None:
        proj["manual_review"] = manual_review
    if chat_id:
        proj["chat_id"] = chat_id
    if locked_facts is not None:
        proj["locked_facts"] = locked_facts
    return proj


def _incident_store() -> dict:
    """Reproduce the 2026-07-20 CUST0001 shape.

    Non-delivered CUST0001 rows are kept at/under 2026-07-13T00:00Z so that,
    against as_of 2026-07-20T22:00Z, every awaiting_final_approval row clears the
    168h TTL (boundary 07-13T22:00Z) and every intake_started row clears the 72h
    TTL. F0224 (manual_edit_required) is an excluded status; F0217/F0222 belong
    to CUST0007 and legitimately appear as candidates under their own id.
    """
    projects: list[dict] = []

    # 7 delivered CUST0001 rows (updated 07-03..07-06), each with a delivered asset.
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

    # 6 CUST0001 awaiting_final_approval rows (all >168h stale at as_of).
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

    # 2 CUST0001 intake_started rows (updated 07-12, >72h stale at as_of).
    for pid in ("F0218", "F0220"):
        projects.append(_project(
            pid, status="intake_started", customer_id="CUST0001",
            updated_at="2026-07-12T00:00:00Z",
        ))

    # F0224 manual_edit_required — excluded status (tally only).
    projects.append(_project(
        "F0224", status="manual_edit_required", customer_id="CUST0001",
        updated_at="2026-07-14T00:00:00Z",
        manual_review={"status": "queued", "reason_code": "visual_qa_failed",
                       "queued_at": "2026-07-14T00:00:00Z"},
    ))

    # CUST0007 awaiting_final_approval rows — also stale, own customer_id.
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


# ── flag-off no-op ──────────────────────────────────────────────────────────

def test_flag_off_is_a_strict_no_op(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FLYER_TTL0_OBSERVE_ENABLED", raising=False)
    cli = _load_cli()
    # A populated store + a digest-path exist; the disabled path must touch neither.
    store_path = _write_store(tmp_path, _incident_store())
    digest_path = tmp_path / "digest.json"
    rc = cli.main(["--state-path", str(store_path), "--digest-path", str(digest_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == {"enabled": False, "candidates_scanned": 0}
    assert not digest_path.exists()  # no write when disabled


def test_flag_off_needs_no_as_of(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FLYER_TTL0_OBSERVE_ENABLED", "0")
    cli = _load_cli()
    rc = cli.main([])
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

def test_incident_fixture_partitions_candidates_delivered_and_excluded(tmp_path):
    store = load_project_store(_write_store(tmp_path, _incident_store()))
    digest = build_ttl0_digest(store, as_of=_as_of())

    assert digest["enabled"] is True
    assert digest["candidates_scanned"] == 18

    # Exactly the 6 CUST0001 AFA + 2 intake_started + F0217/F0222 (CUST0007).
    assert _ids(digest["candidates"]) == [
        "F0214", "F0215", "F0216", "F0217", "F0218",
        "F0219", "F0220", "F0221", "F0222", "F0223",
    ]
    # Exactly the 7 delivered rows.
    assert _ids(digest["delivered_candidates"]) == [
        "F0201", "F0203", "F0209", "F0210", "F0211", "F0212", "F0213",
    ]
    # F0224 is excluded-by-status: tally only, never a candidate.
    assert digest["excluded_statuses"] == {"manual_edit_required": 1}
    assert digest["excluded"] == []
    assert "F0224" not in _ids(digest["candidates"])
    assert "F0224" not in _ids(digest["delivered_candidates"])

    # CUST0007 rows carry their own customer_id.
    by_id = {r["project_id"]: r for r in digest["candidates"]}
    assert by_id["F0217"]["customer_id"] == "CUST0007"
    assert by_id["F0222"]["customer_id"] == "CUST0007"
    assert by_id["F0214"]["customer_id"] == "CUST0001"


def test_incident_legal_transitions_are_correct(tmp_path):
    store = load_project_store(_write_store(tmp_path, _incident_store()))
    digest = build_ttl0_digest(store, as_of=_as_of())
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


# ── idempotency ─────────────────────────────────────────────────────────────

def test_digest_is_byte_identical_across_runs(tmp_path):
    store = load_project_store(_write_store(tmp_path, _incident_store()))
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
    # The file content matches what is printed to stdout.
    assert json.loads(first.decode("utf-8"))["candidates_scanned"] == 18


# ── activity-aware age ──────────────────────────────────────────────────────

def test_asset_delivered_at_dominates_older_updated_at(tmp_path):
    store = load_project_store(_write_store(tmp_path, {"projects": [
        _project("F5001", status="delivered", customer_id="CUST0001",
                 updated_at="2026-07-01T00:00:00Z",
                 assets=[_asset("A0001", "F5001", "2026-07-19T00:00:00Z")]),
    ]}))
    project = store.projects[0]
    # last_activity uses the newer asset delivered_at, not the older updated_at.
    assert compute_last_activity(project).isoformat() == "2026-07-19T00:00:00+00:00"
    # Fresh (delivered_at only 1.9 days before as_of) => within 168h => not stale.
    digest = build_ttl0_digest(store, as_of=_as_of())
    assert digest["delivered_candidates"] == []
    assert digest["candidates"] == []


def test_within_ttl_project_is_not_listed(tmp_path):
    # AFA updated only 24h before as_of => under the 168h TTL => omitted entirely.
    store = load_project_store(_write_store(tmp_path, {"projects": [
        _project("F5002", status="awaiting_final_approval", customer_id="CUST0001",
                 updated_at="2026-07-19T22:00:00Z"),
    ]}))
    digest = build_ttl0_digest(store, as_of=_as_of())
    assert digest["candidates"] == []
    assert digest["excluded"] == []
    assert digest["excluded_statuses"] == {}


# ── claimed exclusion ───────────────────────────────────────────────────────

def test_claimed_stale_project_is_excluded_not_a_candidate(tmp_path):
    store = load_project_store(_write_store(tmp_path, {"projects": [
        _project("F5003", status="awaiting_final_approval", customer_id="CUST0001",
                 updated_at="2026-07-06T00:00:00Z",
                 manual_review={"status": "in_progress", "claimed_by": "admin-a",
                                "claimed_at": "2026-07-06T00:00:00Z"}),
    ]}))
    digest = build_ttl0_digest(store, as_of=_as_of())
    assert _ids(digest["candidates"]) == []
    assert _ids(digest["excluded"]) == ["F5003"]
    row = digest["excluded"][0]
    assert row["exclusion"] == "claimed"
    assert row["claimed"] is True


# ── excluded-status tally ───────────────────────────────────────────────────

def test_excluded_and_unmonitored_statuses_are_tallied_only(tmp_path):
    store = load_project_store(_write_store(tmp_path, {"projects": [
        _project("F5010", status="generating_concepts", customer_id="CUST0001",
                 updated_at="2026-07-01T00:00:00Z"),
        _project("F5011", status="completed", customer_id="CUST0001",
                 updated_at="2026-07-01T00:00:00Z"),
        _project("F5012", status="closed_no_send", customer_id="CUST0001",
                 updated_at="2026-07-01T00:00:00Z"),
        # delivered_with_warning is unmonitored by TTL-0 => tally only, never a candidate.
        _project("F5013", status="delivered_with_warning", customer_id="CUST0001",
                 updated_at="2026-07-01T00:00:00Z"),
    ]}))
    digest = build_ttl0_digest(store, as_of=_as_of())
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

def test_status_without_legal_terminal_edge_is_excluded_defensively(tmp_path):
    # awaiting_concept_selection has a 168h TTL but no legal terminal edge in the
    # deployed FLYER_TRANSITIONS table (only -> revising_design), so a stale one
    # is reported as an exclusion, not a candidate.
    store = load_project_store(_write_store(tmp_path, {"projects": [
        _project("F5020", status="awaiting_concept_selection", customer_id="CUST0001",
                 updated_at="2026-07-06T00:00:00Z"),
    ]}))
    digest = build_ttl0_digest(store, as_of=_as_of())
    assert _ids(digest["candidates"]) == []
    assert _ids(digest["excluded"]) == ["F5020"]
    row = digest["excluded"][0]
    assert row["exclusion"] == "no_legal_terminal_edge"
    assert row["legal_transition"] is None


# ── privacy scan ────────────────────────────────────────────────────────────

def test_digest_contains_no_phone_chatid_or_fact_strings(tmp_path):
    store = load_project_store(_write_store(tmp_path, _incident_store()))
    payload = serialize_digest(build_ttl0_digest(store, as_of=_as_of()))
    for needle in ("+1", "@lid", "phone", "chat_id"):
        assert needle not in payload, f"privacy leak: {needle!r} present in digest"


# ── helpers ─────────────────────────────────────────────────────────────────

def _as_of():
    from agents.flyer.ttl_observe import parse_utc
    return parse_utc(AS_OF)
